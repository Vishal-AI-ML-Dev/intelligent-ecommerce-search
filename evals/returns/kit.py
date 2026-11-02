"""The blind labelling kit: one static, offline HTML page per set.

The page shows, per return and in the split's presentation order: the item number, the return
ID, the customer's reason code, the return's comments, the linked support complaints (with
their topic) and the structured inspection conditions. Customer statements and inspection
observations are shown in separate sections.

It never shows baseline predictions, tiers, review reasons, groups, products, scenario intent or
any generator file; `BlindItem` does not carry them. The page has no script, no link and no
external resource. Its Content-Security-Policy (`default-src 'none'`) blocks any request, and
all text is HTML-escaped, so markup or injected instructions in comments display as plain text.
"""

from html import escape

from evals.returns.labels import COLUMNS, DEFECT_KIND_UNDETERMINED, LABELS_VERSION
from evals.returns.source import BlindItem

CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
GUIDE = (
    ("sizing_fit", "too small, too large or does not fit"),
    ("expectation_mismatch", "differs from what the customer expected"),
    ("colour_image_mismatch", "colour or look differs from the listing images"),
    ("incorrect_specification", "a stated specification is wrong"),
    ("product_quality_defect", "a quality defect, if the kind is clear"),
    ("damaged_product", "arrived damaged or broken"),
    ("wrong_item_delivered", "a different item than ordered"),
    ("logistics_delay", "arrived late"),
    ("manufacturing_defect", "a manufacturing defect, if the kind is clear"),
    ("change_of_mind", "no longer wanted"),
    ("suspected_abuse", "only if the evidence itself supports it"),
    ("insufficient_evidence", "nothing supports any category (must stand alone)"),
    (DEFECT_KIND_UNDETERMINED, "a defect whose kind cannot be determined"),
)
_STYLE = """
body{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:880px;padding:16px;
color:#1a1a1a;background:#fff}
h1{font-size:20px}h2{font-size:16px;margin:0 0 6px}
.item{border:1px solid #bbb;border-radius:6px;padding:12px 14px;margin:14px 0}
.meta{color:#444}.label{font-weight:600;margin-top:8px}
.text{white-space:pre-wrap;background:#f4f4f4;border-radius:4px;padding:6px 8px;margin:4px 0}
.none{color:#777;font-style:italic}code{font-size:14px}
table{border-collapse:collapse}td{padding:2px 10px 2px 0;vertical-align:top}
"""


def _statements(title: str, statements, *, topic: bool) -> str:
    out = [f'<div class="label">{escape(title)}</div>']
    if not statements:
        return out[0] + '<div class="none">none</div>'
    for s in statements:
        prefix = f"{escape(s.evidence_id)}"
        if topic:
            prefix += f" · topic: {escape(s.topic) if s.topic else 'not given'}"
        out.append(f'<div class="meta">{prefix}</div><div class="text">{escape(s.text)}</div>')
    return "".join(out)


def _item(number: int, item: BlindItem) -> str:
    inspections = (
        "<table>"
        + "".join(
            f"<tr><td>{escape(oid)}</td><td><code>{escape(cond)}</code></td></tr>"
            for oid, cond in item.inspections
        )
        + "</table>"
        if item.inspections
        else '<div class="none">none</div>'
    )
    return (
        f'<section class="item" id="item-{number}">'
        f"<h2>Item {number} · {escape(item.return_id)}</h2>"
        f'<div><span class="label">Customer reason code:</span> '
        f"<code>{escape(item.reason_code)}</code></div>"
        + _statements("Customer comments", item.comments, topic=False)
        + _statements("Linked support complaints", item.complaints, topic=True)
        + '<div class="label">Inspection observations (condition only)</div>'
        + inspections
        + "</section>"
    )


def render_kit(set_name: str, items: list[BlindItem], label_sheet: str) -> str:
    guide = "".join(
        f"<tr><td><code>{escape(t)}</code></td><td>{escape(d)}</td></tr>" for t, d in GUIDE
    )
    body = "".join(_item(n, item) for n, item in enumerate(items, start=1))
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Return labelling kit: {escape(set_name)}</title><style>{_STYLE}</style>"
        "</head><body>"
        f"<h1>Return labelling kit: {escape(set_name)} set ({len(items)} returns)</h1>"
        f"<p>Label contract <code>{LABELS_VERSION}</code>. Record labels in "
        f"<code>{escape(label_sheet)}</code> (columns: {escape(', '.join(COLUMNS))}). Put "
        "<code>1</code> in every category the evidence below supports; leave the rest empty. "
        "Fill <code>annotator</code> with your handle on every labelled row. Leave a row "
        "entirely empty to skip it.</p>"
        "<p>Judge from this page only. It deliberately hides any system output. Customer "
        "statements can be wrong; inspection conditions are what an inspector saw. Text was "
        "redacted on import.</p>"
        f"<table>{guide}</table>{body}</body></html>\n"
    )

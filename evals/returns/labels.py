"""`return-reason-labels-1`: the human label contract and its CSV form.

One row per return. A label is a set of tokens:
* any of the twelve `return-categories-1` categories (multi-label), or
* `defect_kind_undetermined`: "there is a defect, but its kind cannot be determined". It may not
  be combined with `product_quality_defect` or `manufacturing_defect`.
* `insufficient_evidence` must stand alone.

Plus an optional free-text `note` (at most `NOTE_MAX` characters) and a pseudonymous `annotator`
handle. One annotator exists, so no agreement statistic is computed.

**CSV form** (`COLUMNS`, exact header, UTF-8): `item` (the kit's presentation number),
`return_id`, one column per token holding `1` or nothing, `annotator`, `note`. A row with no
marked token is *unlabelled* (not an error) and must have no note or annotator. The blank
template written by `write_template` holds only `item` and `return_id`; it is not a label.

Validation errors name the row number, the column and a fixed code. They never quote a cell.
"""

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from ecommerce_search.returns.classification.taxonomy import ReturnCategory

LABELS_VERSION = "return-reason-labels-1"
DEFECT_KIND_UNDETERMINED = "defect_kind_undetermined"
INSUFFICIENT = ReturnCategory.INSUFFICIENT_EVIDENCE.value
SPECIFIC_DEFECTS = frozenset(
    {ReturnCategory.PRODUCT_QUALITY_DEFECT.value, ReturnCategory.MANUFACTURING_DEFECT.value}
)
TOKENS: tuple[str, ...] = (*(c.value for c in ReturnCategory), DEFECT_KIND_UNDETERMINED)
COLUMNS: tuple[str, ...] = ("item", "return_id", *TOKENS, "annotator", "note")
NOTE_MAX = 500
FILE_MAX_BYTES = 1024 * 1024
_ANNOTATOR = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_RETURN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


class LabelError(ValueError):
    """Every problem found, as `row N, column C: code` lines (no cell contents)."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems[:20]) + (" ..." if len(problems) > 20 else ""))


@dataclass(frozen=True, slots=True)
class HumanLabel:
    return_id: str
    tokens: frozenset[str]
    annotator: str
    note: str | None


def _row_problems(n: int, row: dict[str, str]) -> list[str]:
    problems = []
    for token in TOKENS:
        if row[token] not in ("", "1"):
            problems.append(f"row {n}, column {token}: value must be 1 or empty")
    marked = {t for t in TOKENS if row[t] == "1"}
    if not marked:
        if row["annotator"] or row["note"]:
            problems.append(f"row {n}: an unlabelled row must have no annotator or note")
        return problems
    if INSUFFICIENT in marked and len(marked) > 1:
        problems.append(f"row {n}, column {INSUFFICIENT}: must stand alone")
    if DEFECT_KIND_UNDETERMINED in marked and marked & SPECIFIC_DEFECTS:
        problems.append(
            f"row {n}, column {DEFECT_KIND_UNDETERMINED}: cannot be combined with a defect kind"
        )
    if not _ANNOTATOR.fullmatch(row["annotator"]):
        problems.append(f"row {n}, column annotator: required pseudonymous handle [a-z0-9-]")
    if len(row["note"]) > NOTE_MAX:
        problems.append(f"row {n}, column note: longer than {NOTE_MAX} characters")
    return problems


def parse_labels(data: bytes, allowed_return_ids: frozenset[str]) -> dict[str, HumanLabel]:
    """Labelled rows only, keyed by return ID. Raises `LabelError` listing every problem."""
    if len(data) > FILE_MAX_BYTES:
        raise LabelError(["file: larger than allowed"])
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise LabelError(["file: not UTF-8"]) from None
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    try:
        header = tuple(reader.fieldnames or ())
        rows = list(reader)
    except csv.Error:
        raise LabelError(["file: not valid CSV"]) from None
    if header != COLUMNS:
        raise LabelError(["header: columns must be exactly the contract columns, in order"])
    problems: list[str] = []
    labels: dict[str, HumanLabel] = {}
    seen: set[str] = set()
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        if None in row or any(v is None for v in row.values()):
            problems.append(f"row {n}: wrong number of cells")
            continue
        return_id = row["return_id"]
        if not _RETURN_ID.fullmatch(return_id) or return_id not in allowed_return_ids:
            problems.append(f"row {n}, column return_id: not a return of this set")
            continue
        if return_id in seen:
            problems.append(f"row {n}, column return_id: duplicate")
            continue
        seen.add(return_id)
        if not row["item"].isdigit():
            problems.append(f"row {n}, column item: must be a whole number")
        row_problems = _row_problems(n, row)
        problems += row_problems
        marked = frozenset(t for t in TOKENS if row[t] == "1")
        if marked and not row_problems:
            labels[return_id] = HumanLabel(return_id, marked, row["annotator"], row["note"] or None)
    if problems:
        raise LabelError(problems)
    return labels


def write_template(path: Path, ordered_return_ids: list[str]) -> None:
    """A blank label sheet: `item` and `return_id` only. Never overwrites an existing file."""
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for item, return_id in enumerate(ordered_return_ids, start=1):
            writer.writerow([item, return_id, *([""] * (len(COLUMNS) - 2))])

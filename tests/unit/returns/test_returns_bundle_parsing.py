"""AC-06/AC-09 (domain): envelope, limits and parsing of uploads (contracts §4.1-4.4). Hermetic.

Every record is a SYNTHETIC fixture. Limits are tested at the limit and at limit + 1.
"""

import copy
import hashlib

import pytest

from ecommerce_search.returns import bundle as bd
from ecommerce_search.returns import canonical as cf
from ecommerce_search.returns import errors as er
from returns_samples import DATASET, RECORDS
from returns_uploads import (
    codes,
    csv_bytes,
    csv_parts,
    json_bytes,
    json_parts,
    manifest_bytes,
    sample_records,
    validate,
)

Part = bd.Part
MiB = 1024 * 1024


def bundle(**top) -> bytes:
    return json_bytes({"contract_version": "return-data-v1", "dataset": DATASET, **top})


def orders(count: int, start: int = 1) -> list[dict]:
    base = RECORDS["orders"]
    return [base | {"order_id": f"SYN-ORD-{i:05d}"} for i in range(start, start + count)]


def only(result) -> er.RowError:
    assert len(result.report.errors) == 1, result.report.errors
    return result.report.errors[0]


# ---- step 1: envelope ----------------------------------------------------------------------


def test_valid_json_and_csv_uploads_complete_all_four_phases():
    for parts in (json_parts(), csv_parts()):
        report = validate(parts).report
        assert report.error_count == 0
        assert report.phases_completed == er.PHASES
        assert report.received == dict.fromkeys(RECORDS, 1)


def test_unexpected_parts_are_numbered_and_their_names_never_echoed():
    hostile = "buyer@example.com 012345.json"
    result = validate([*json_parts(), Part(hostile, b"{}"), Part("scenarios.json", b"{}")])
    report = result.report
    assert [(e.file, e.code) for e in report.errors] == [
        ("unexpected part #2", "unexpected_file"),
        ("unexpected part #3", "unexpected_file"),
    ]
    assert report.phases_completed == (er.PHASE_ENVELOPE,)
    assert report.bundle_sha256 is None
    assert report.error_count_is_lower_bound is True
    assert "buyer" not in report.model_dump_json() and "scenarios" not in report.model_dump_json()


def test_a_repeated_canonical_name_is_an_unexpected_part():
    parts = json_parts()
    assert [(e.file, e.code) for e in validate(parts + parts).report.errors] == [
        ("unexpected part #2", "unexpected_file")
    ]


def test_a_bundle_with_csv_parts_reports_the_csv_parts():
    parts = json_parts() + csv_parts()
    report = validate(parts).report
    unexpected = {e.file for e in report.errors if e.code == "unexpected_file"}
    assert unexpected == {"manifest.json", *(f"{e}.csv" for e in RECORDS)}
    assert [e.file for e in report.errors if e.code != "unexpected_file"] == ["bundle.json"]
    assert "too_many_files" in codes(validate(parts))  # 14 parts


@pytest.mark.parametrize(
    ("parts", "file"),
    [([], "bundle.json"), ([Part("orders.csv", b"")], "manifest.json")],
)
def test_missing_anchor_file(parts, file):
    error = only(validate(parts))
    assert (error.file, error.code) == (file, "missing_file")


def test_file_count_limit():
    records = sample_records()
    assert len(csv_parts(records)) == bd.MAX_FILES
    assert validate(csv_parts(records)).report.error_count == 0
    over = csv_parts(records) + [Part("extra.txt", b"")]
    assert "too_many_files" in codes(validate(over))


@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("orders.csv", bd.FILE_MAX_BYTES),
        ("manifest.json", bd.MANIFEST_MAX_BYTES),
        ("bundle.json", bd.FILE_MAX_BYTES),
    ],
)
def test_per_file_byte_limits(name, limit):
    at_limit = validate([Part(name, b" " * limit)])
    assert "file_too_large" not in codes(at_limit)
    error = [
        e
        for e in validate([Part(name, b" " * (limit + 1))]).report.errors
        if e.code == "file_too_large"
    ]
    assert [e.file for e in error] == [name]


def test_bundle_byte_limit():
    parts = [Part("manifest.json", b"{}")] + [
        Part(f"{e}.csv", b" " * (bd.BUNDLE_MAX_BYTES // 2 - 1)) for e in ("orders", "returns")
    ]
    assert "bundle_too_large" not in codes(validate(parts))
    parts.append(Part("shipments.csv", b"  "))
    assert "bundle_too_large" in codes(validate(parts))


# ---- step 2: JSON --------------------------------------------------------------------------


def test_absent_arrays_are_empty():
    report = validate([Part("bundle.json", bundle())]).report
    assert report.error_count == 0 and report.received == dict.fromkeys(RECORDS, 0)


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b'{"contract_version": "return-data-v1", "contract_version": "x"}', "format"),
        (b"\xef\xbb\xbf" + bundle(), "encoding"),
        (b'{"a": "\xff"}', "encoding"),
        (b"[]", "type"),
        (b"", "format"),
        (b'{"orders": [1, NaN]}', "format"),
        (b'{"x": ' + b"1" * 5000 + b"}", "format"),
        (b"[" * 100_000, "format"),
    ],
    ids=["duplicate-key", "bom", "invalid-utf8", "not-object", "empty", "nan", "huge-int", "deep"],
)
def test_json_parse_failures_are_bundle_level(data, code):
    report = validate([Part("bundle.json", data)]).report
    assert codes(validate([Part("bundle.json", data)]))[0] == code
    assert report.phases_completed == (er.PHASE_ENVELOPE,)
    assert report.error_count_is_lower_bound is True


def test_duplicate_key_is_reported_with_a_safe_field_only():
    data = b'{"contract_version": "return-data-v1", "buyer@example.com": 1, "buyer@example.com": 2}'
    error = only(validate([Part("bundle.json", data)]))
    assert (error.code, error.field) == ("format", er.UNRECOGNIZED)


def test_json_syntax_error_reports_the_line_only():
    error = only(validate([Part("bundle.json", b'{\n"a": buyer@example.com}')]))
    assert (error.code, error.line, error.value_preview) == ("format", 2, None)
    assert "Expecting" not in error.message


def test_nesting_depth_four_is_allowed_and_five_rejected():
    depth4 = bundle(orders=[RECORDS["orders"] | {"customer_ref": []}])
    assert "format" not in [
        e.code for e in validate([Part("bundle.json", depth4)]).report.errors if e.row is None
    ]
    depth5 = bundle(orders=[RECORDS["orders"] | {"customer_ref": [[]]}])
    error = only(validate([Part("bundle.json", depth5)]))
    assert (error.code, error.row) == ("format", None)


def test_top_level_keys():
    data = json_bytes({"dataset": DATASET, "Orders": [], "contract_version": "return-data-v2"})
    report = validate([Part("bundle.json", data)]).report
    assert [(e.code, e.field) for e in report.errors] == [  # ordered by field, then code
        ("unknown_column", er.UNRECOGNIZED),
        ("contract_version", "contract_version"),
    ]
    missing = validate([Part("bundle.json", json_bytes({"orders": []}))]).report
    assert sorted((e.code, e.field) for e in missing.errors) == [
        ("required", "contract_version"),
        ("required", "dataset"),
    ]


def test_an_entity_value_must_be_an_array():
    error = only(validate([Part("bundle.json", bundle(orders={}))]))
    assert (error.code, error.entity) == ("type", "orders")


@pytest.mark.parametrize("bad", ["\\u0000", "\\ud800"])
def test_unstorable_text_rejects_only_its_row(bad):
    data = bundle(orders=orders(2)).replace(b'"SYN-CUST-00001"', f'"x{bad}"'.encode(), 1)
    report = validate([Part("bundle.json", data)]).report
    assert [(e.code, e.row, e.field, e.pointer) for e in report.errors] == [
        ("encoding", 1, "customer_ref", "/orders/0/customer_ref")
    ]
    assert report.phases_completed == er.PHASES
    assert report.error_count_is_lower_bound is True


def test_json_entity_and_bundle_row_limits():
    at = validate([Part("bundle.json", bundle(orders=orders(bd.ENTITY_MAX_ROWS)))]).report
    assert at.error_count == 0
    over = only(validate([Part("bundle.json", bundle(orders=orders(bd.ENTITY_MAX_ROWS + 1)))]))
    assert (over.code, over.entity) == ("too_many_rows", "orders")
    items = [
        RECORDS["order_items"] | {"order_item_id": f"SYN-OI-{i:05d}", "order_id": "SYN-ORD-00001"}
        for i in range(1, 5001)
    ]
    at_bundle = bundle(orders=orders(bd.ENTITY_MAX_ROWS), order_items=items)
    assert "too_many_rows" not in codes(validate([Part("bundle.json", at_bundle)]))
    over_bundle = bundle(orders=orders(bd.ENTITY_MAX_ROWS), order_items=items, returns=[{}])
    error = only(validate([Part("bundle.json", over_bundle)]))
    assert (error.code, error.entity) == ("too_many_rows", None)


# ---- step 2: CSV ---------------------------------------------------------------------------


def _set(files: dict[str, bytes], rows: dict[str, int] | None = None) -> list[Part]:
    rows = rows or {n: max(d.count(b"\n") - 1, 0) for n, d in files.items()}
    return [
        Part("manifest.json", manifest_bytes(files, rows)),
        *(Part(n, d) for n, d in files.items()),
    ]


def _orders_csv(text: str) -> list[Part]:
    data = text.encode("utf-8")
    return _set({"orders.csv": data}, {"orders.csv": 1})


HEADER = "currency,customer_ref,is_synthetic,order_id,ordered_at"


def test_csv_bom_and_lf_or_crlf_give_identical_records():
    reference = validate(json_parts()).records
    for parts in (csv_parts(newline="\n"), csv_parts(bom=True), csv_parts(newline="\r\n")):
        result = validate(parts)
        assert result.report.error_count == 0
        assert {e: [v.record for v in r] for e, r in result.records.items()} == {
            e: [v.record for v in r] for e, r in reference.items()
        }


def test_csv_quoting_keeps_commas_quotes_and_newlines_in_text():
    text = 'Line one, "quoted"\r\nline two\nline three'
    records = sample_records()
    records["return_comments"][0]["text"] = text
    result = validate(csv_parts(records))
    assert result.report.error_count == 0
    assert result.records[cf_entity("return_comments")][0].record.text == text


def cf_entity(name):
    from ecommerce_search.returns.contracts import Entity

    return Entity(name)


def test_csv_field_and_header_without_trailing_newline():
    parts = _orders_csv(
        f"{HEADER}\nINR,SYN-CUST-00001,true,SYN-ORD-00001,2026-01-05T10:00:00+05:30"
    )
    assert validate(parts).report.error_count == 0


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ('INR,SYN-CUST-00001,true,SYN-ORD-00001,"2026-01-05T10:00:00+05:30\n', 2),  # unterminated
        ('INR,SYN-"CUST",true,SYN-ORD-00001,2026-01-05T10:00:00+05:30\n', 2),  # bare quote
        ('"INR"x,SYN-CUST-00001,true,SYN-ORD-00001,2026-01-05T10:00:00+05:30\n', 2),
        ("INR,SYN-CUST-00001,true,SYN-ORD-00001,2026-01-05T10:00:00+05:30\r", 2),  # bare CR
    ],
)
def test_csv_syntax_errors_fail_the_file_with_a_line(body, line):
    report = validate(_orders_csv(f"{HEADER}\n{body}")).report
    error = only(validate(_orders_csv(f"{HEADER}\n{body}")))
    assert (error.file, error.code, error.line, error.row) == ("orders.csv", "format", line, None)
    assert report.error_count_is_lower_bound is True


def test_ragged_row_is_rejected_alone():
    good = "INR,SYN-CUST-00001,true,SYN-ORD-00001,2026-01-05T10:00:00+05:30"
    parts = _set({"orders.csv": f"{HEADER}\n{good}\nINR,extra\n".encode()}, {"orders.csv": 2})
    result = validate(parts)
    assert [(e.code, e.row, e.line) for e in result.report.errors] == [("format", 2, 3)]
    assert result.report.phases_completed == er.PHASES


def test_header_rules():
    parts = _orders_csv("currency,customer_ref,is_synthetic,order_id,order_id,Buyer@Example.com\n")
    report = validate(parts).report
    assert [(e.code, e.field) for e in report.errors] == [
        ("unknown_column", er.UNRECOGNIZED),
        ("format", "order_id"),
        ("missing_column", "ordered_at"),
    ]


def test_empty_csv_file_and_header_only_file():
    assert codes(validate(_set({"orders.csv": b""}, {"orders.csv": 0}))) == ["format"]
    assert (
        validate(
            _set({"orders.csv": (HEADER + "\n").encode()}, {"orders.csv": 0})
        ).report.error_count
        == 0
    )


def test_csv_column_limit():
    header = ",".join(f"c{i}" for i in range(bd.CSV_MAX_COLUMNS + 1))
    assert codes(validate(_set({"orders.csv": header.encode()}, {"orders.csv": 0}))) == [
        "too_many_columns"
    ]
    header = ",".join(["currency"] * bd.CSV_MAX_COLUMNS)
    assert "too_many_columns" not in codes(
        validate(_set({"orders.csv": header.encode()}, {"orders.csv": 0}))
    )


def test_csv_field_byte_limit():
    def comment(text):
        records = sample_records()
        records["return_comments"][0]["text"] = text
        return validate(csv_parts(records))

    at_limit = comment("é" * (bd.CSV_FIELD_MAX_BYTES // 2))  # 2 bytes each; 2048 code points
    assert "field_too_large" not in codes(at_limit)
    error = [e for e in comment("é" * (bd.CSV_FIELD_MAX_BYTES // 2) + "a").report.errors]
    assert [(e.code, e.field) for e in error] == [("field_too_large", "text")]


def test_csv_entity_row_limit():
    rows = orders(bd.ENTITY_MAX_ROWS)
    assert validate(csv_parts({"orders": rows})).report.error_count == 0
    data = csv_bytes("orders", orders(bd.ENTITY_MAX_ROWS + 1))
    manifest_over = _set({"orders.csv": data}, {"orders.csv": bd.ENTITY_MAX_ROWS + 1})
    assert codes(validate(manifest_over)) == ["too_many_rows"]
    lying = _set({"orders.csv": data}, {"orders.csv": bd.ENTITY_MAX_ROWS})
    assert codes(validate(lying)) == ["row_count_mismatch"]


def test_csv_bundle_row_limit_from_the_manifest():
    names = ["orders", "returns", "shipments"]
    files = {f"{n}.csv": b"" for n in names}
    rows = {"orders.csv": 5000, "returns.csv": 5000, "shipments.csv": 0}
    assert "too_many_rows" not in codes(validate(_set(files, rows)))
    rows["shipments.csv"] = 1
    assert codes(validate(_set(files, rows))) == ["too_many_rows"]


def test_manifest_set_consistency():
    parts = csv_parts()
    manifest, files = parts[0], {p.name: p.data for p in parts[1:]}
    missing = [manifest, *(Part(n, d) for n, d in files.items() if n != "orders.csv")]
    assert [(e.file, e.code) for e in validate(missing).report.errors] == [
        ("orders.csv", "missing_file")
    ]
    extra_manifest = manifest_bytes(
        {n: d for n, d in files.items() if n != "orders.csv"}, {n: 1 for n in files}
    )
    unlisted = [Part("manifest.json", extra_manifest), *(Part(n, d) for n, d in files.items())]
    assert [(e.file, e.code) for e in validate(unlisted).report.errors] == [
        ("orders.csv", "unexpected_file")
    ]
    tampered = [
        manifest,
        *(Part(n, d + (b"\n" if n == "orders.csv" else b"")) for n, d in files.items()),
    ]
    assert [(e.file, e.code) for e in validate(tampered).report.errors] == [
        ("orders.csv", "checksum_mismatch")
    ]
    assert validate(tampered).report.phases_completed == (er.PHASE_ENVELOPE,)


def test_row_count_mismatch_excludes_the_file():
    data = csv_bytes("orders", orders(2))
    result = validate(_set({"orders.csv": data}, {"orders.csv": 3}))
    assert [(e.file, e.code) for e in result.report.errors] == [
        ("orders.csv", "row_count_mismatch")
    ]
    assert result.report.error_count_is_lower_bound is True


@pytest.mark.parametrize(
    ("entry", "code", "field"),
    [
        ({"name": "buyer@example.com.csv"}, "format", "name"),
        ({"entity": "returns"}, "format", "entity"),
        ({"sha256": "A" * 64}, "format", "sha256"),
        ({"rows": True}, "type", "rows"),
        ({"rows": -1}, "out_of_range", "rows"),
        ({"rows": 5001}, "too_many_rows", "rows"),
        ({"extra": 1}, "unknown_column", "extra"),
    ],
)
def test_manifest_file_entries(entry, code, field):
    data = csv_bytes("orders", orders(1))
    good = {
        "name": "orders.csv",
        "entity": "orders",
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": 1,
    }
    manifest = json_bytes(
        {"contract_version": "return-data-v1", "dataset": DATASET, "files": [good | entry]}
    )
    error = only(validate([Part("manifest.json", manifest), Part("orders.csv", data)]))
    assert (error.file, error.code, error.field, error.value_preview) == (
        "manifest.json",
        code,
        field,
        None,
    )


def test_manifest_requires_a_non_empty_file_list_and_rejects_duplicates():
    empty = json_bytes({"contract_version": "return-data-v1", "dataset": DATASET, "files": []})
    assert codes(validate([Part("manifest.json", empty)])) == ["required"]
    data = csv_bytes("orders", orders(1))
    entry = {
        "name": "orders.csv",
        "entity": "orders",
        "sha256": hashlib.sha256(data).hexdigest(),
        "rows": 1,
    }
    twice = json_bytes(
        {"contract_version": "return-data-v1", "dataset": DATASET, "files": [entry, entry]}
    )
    assert codes(validate([Part("manifest.json", twice), Part("orders.csv", data)])) == [
        "duplicate_in_bundle"
    ]


def test_bundle_sha256_identifies_the_stored_files_and_differs_between_formats():
    json_report = validate(json_parts()).report
    data = json_parts()[0].data
    assert json_report.bundle_sha256 == cf.bundle_sha256(
        {"bundle.json": hashlib.sha256(data).hexdigest()}
    )
    parts = csv_parts()
    csv_report = validate(parts).report
    assert csv_report.bundle_sha256 == cf.bundle_sha256(
        {p.name: hashlib.sha256(p.data).hexdigest() for p in parts}
    )
    assert csv_report.bundle_sha256 != json_report.bundle_sha256
    assert [f.name for f in validate(parts).files] == [
        "manifest.json",
        *sorted(f"{e}.csv" for e in RECORDS),
    ]


def test_parts_must_be_bytes():
    with pytest.raises(TypeError):
        validate([Part("bundle.json", "{}")])
    with pytest.raises(TypeError):
        validate([("bundle.json", b"{}")])


def test_input_is_not_modified():
    records = sample_records()
    before = copy.deepcopy(records)
    validate(json_parts(records))
    assert records == before

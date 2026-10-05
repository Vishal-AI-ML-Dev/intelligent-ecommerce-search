"""Canonical JSON/JSONL encoding and strict decoding (synthetic fixtures only)."""

import json

import pytest
from evals.contracts.canonical import (
    CanonicalFormError,
    decode_json_object,
    decode_jsonl,
    encode_json_object,
    encode_jsonl,
    records_sha256,
    sha256_hex,
)
from evals.contracts.constants import Label
from evals.contracts.models import Judgement, QueryRecord
from golden_support import judgement, query


def _queries() -> list[QueryRecord]:
    return [query("gq-p02", "second query"), query("gq-p01", "first query")]


def test_encode_sorts_by_key_and_round_trips_byte_for_byte():
    data = encode_jsonl(_queries())
    lines = data.decode("utf-8").split("\n")
    assert lines[-1] == ""
    assert '"query_id":"gq-p01"' in lines[0] and '"query_id":"gq-p02"' in lines[1]
    decoded = decode_jsonl(data, QueryRecord)
    assert [q.query_id for q in decoded] == ["gq-p01", "gq-p02"]
    assert encode_jsonl(decoded) == data
    assert records_sha256(decoded) == sha256_hex(data)


def test_canonical_line_has_sorted_keys_and_no_whitespace():
    line = encode_jsonl([query("gq-p01", "first query")]).decode("utf-8").strip()
    assert ", " not in line and ": " not in line
    keys = json.loads(line, object_pairs_hook=lambda pairs: [k for k, _ in pairs])
    assert keys == sorted(keys)


def test_empty_file_is_zero_records():
    assert encode_jsonl([]) == b""
    assert decode_jsonl(b"", QueryRecord) == []


def test_timestamps_and_unicode_are_canonical():
    data = encode_jsonl([query("gq-p01", "laptop ₹40000 ke andar")])
    assert "₹".encode() in data  # ensure_ascii=False
    j = encode_jsonl([judgement("gq-p01", "SYN-LAP-0001")]).decode()
    assert '"annotated_at":"2026-10-10T12:00:00.123456Z"' in j


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda d: b"\xef\xbb\xbf" + d, "byte-order mark"),
        (lambda d: d.replace(b"\n", b"\r\n"), "CR characters"),
        (lambda d: d[:-1], "final line"),
        (lambda d: d + b"\n", "blank lines"),
        (lambda d: b"\xff" + d, "UTF-8"),
    ],
)
def test_framing_violations_are_refused(mutate, message):
    data = encode_jsonl(_queries())
    with pytest.raises(CanonicalFormError, match=message):
        decode_jsonl(mutate(data), QueryRecord)


def test_unsorted_and_duplicate_records_are_refused():
    first, second = encode_jsonl(_queries()).decode().strip().split("\n")
    with pytest.raises(CanonicalFormError, match="not sorted"):
        decode_jsonl(f"{second}\n{first}\n".encode(), QueryRecord)
    with pytest.raises(CanonicalFormError, match="duplicate record key"):
        decode_jsonl(f"{first}\n{first}\n".encode(), QueryRecord)
    with pytest.raises(CanonicalFormError, match="duplicate record key"):
        encode_jsonl([query("gq-p01", "a"), query("gq-p01", "b")])


def test_non_canonical_spacing_key_order_and_duplicate_keys_are_refused():
    line = encode_jsonl([query("gq-p01", "first query")]).decode().strip()
    with pytest.raises(CanonicalFormError, match="canonical form"):
        decode_jsonl((line.replace(",", ", ") + "\n").encode(), QueryRecord)
    reordered = '{"text":"first query",' + line[1:].replace(',"text":"first query"', "")
    with pytest.raises(CanonicalFormError, match="canonical form"):
        decode_jsonl((reordered + "\n").encode(), QueryRecord)
    duplicated = line[:-1] + ',"query_id":"gq-p01"}'
    with pytest.raises(CanonicalFormError, match="duplicate JSON key"):
        decode_jsonl((duplicated + "\n").encode(), QueryRecord)


def test_floats_extra_fields_and_lax_types_are_refused():
    line = encode_jsonl([query("gq-p01", "first query")]).decode().strip()
    with pytest.raises(CanonicalFormError, match="non-integer JSON number"):
        decode_jsonl(
            line.replace('"schema_version":1', '"schema_version":1.0').encode() + b"\n", QueryRecord
        )
    with pytest.raises(CanonicalFormError, match="invalid QueryRecord"):
        decode_jsonl((line[:-1] + ',"zzz":1}\n').encode(), QueryRecord)
    with pytest.raises(CanonicalFormError, match="invalid QueryRecord"):
        decode_jsonl(
            line.replace('"seen_in_development":false', '"seen_in_development":0').encode() + b"\n",
            QueryRecord,
        )


def test_lax_timestamp_strings_are_refused():
    line = encode_jsonl([judgement("gq-p01", "SYN-LAP-0001")]).decode().strip()
    for bad in ("2026-10-10T12:00:00Z", "2026-10-10T12:00:00.123456+00:00", "2026-10-10 12:00"):
        mutated = line.replace("2026-10-10T12:00:00.123456Z", bad)
        with pytest.raises(CanonicalFormError, match="invalid Judgement"):
            decode_jsonl((mutated + "\n").encode(), Judgement)


def test_single_object_files_are_one_canonical_line():
    record = judgement("gq-p01", "SYN-LAP-0001", Label.NOT_RELEVANT)
    data = encode_json_object(record)
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert decode_json_object(data, Judgement) == record
    with pytest.raises(CanonicalFormError, match="exactly one line"):
        decode_json_object(data + data, Judgement)

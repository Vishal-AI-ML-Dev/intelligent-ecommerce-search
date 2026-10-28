"""M18-03C: the contracts §5.3 synthetic-fixture scan (AC-10, fixture part).

Scans every committed M18 fixture: the release under `data/seed/returns_v1/` (bundle, CSV
set, manifest, scenarios and provenance) and the sample records in `tests/returns_samples.py`.
Every `redaction-1` match in raw text must come from the closed §5.3 allowlist, and redacted
outputs must have no match at all. M18-05: the retailer tab's frontend fixtures
(`frontend/e2e/fixtures/returns/`) must have no match at all (§5.3), with only the narrow
exemptions of API-generated forms listed at `frontend_strings`.

Scanned: every string value and every object key. The exemptions are narrow in both field and
form. They cover values that are not personal-data surfaces, whose hex or digit runs would
match the PIN or number patterns by construction:
- a value of a named SHA-256 field (`HASH_FIELDS`), or a value inside the provenance `files`
  and `bundle_sha256` maps, only when it is exactly 64 lowercase hex digits;
- a `unit_price` value, only when it is a canonical money amount.

Anything else under those names is scanned. Names and addresses cannot be found by patterns;
the generator's texts are written without them (§5.2 "Not guaranteed").
"""

import csv
import io
import json
import re

import pytest

from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.redaction import PATTERNS, matches_any, redact
from returns_release import CSV_DIR, PROVENANCE, RELEASE, SCENARIOS, bundle
from returns_samples import DATASET, RECORDS

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
CARD = "0000000000000001"  # fails the Luhn check
HASH_FIELDS = frozenset(
    {
        "catalog_raw_sha256",
        "checksum_sha256",
        "descriptor_sha256",
        "records_sha256",
        "sha256",  # manifest file entries
    }
)
HASH_MAPS = frozenset({"files", "bundle_sha256"})  # provenance: name -> SHA-256
SHA256 = re.compile(r"[0-9a-f]{64}")
MONEY = re.compile(r"(0|[1-9][0-9]{0,7})\.[0-9]{2}")


def exempt(key: str, value: str, in_hash_map: bool = False) -> bool:
    if key in HASH_FIELDS or in_hash_map:
        return SHA256.fullmatch(value) is not None
    return key == "unit_price" and MONEY.fullmatch(value) is not None


def allowlisted(kind: str, match: str) -> bool:
    """The §5.3 closed list."""
    if kind == "email":
        return match.rsplit("@", 1)[1].lower() in ALLOWED_EMAIL_DOMAINS
    if kind == "upi":
        return "example" in match.rsplit("@", 1)[1].lower()
    if kind == "number":
        # The card, or phone-like with a subscriber part starting with 0 (with or without +91).
        digits = re.sub(r"\D", "", match.removeprefix("+91"))
        return digits == CARD or digits.startswith("0")
    return match.startswith("0")  # pin: Indian PIN codes never start with 0


def scan(text: str) -> list[tuple[str, str]]:
    """Every (kind, match) in `redaction-1` order, replacing each kind as redaction does."""
    found = []
    for kind, pattern, token in PATTERNS:
        found += [(kind, m.group(0)) for m in pattern.finditer(text)]
        text = pattern.sub(token, text)
    return found


def strings(value, key: str = "", in_hash_map: bool = False) -> list[str]:
    """Every scanned string: object keys and values, minus the narrow exemptions."""
    if isinstance(value, dict):
        out = []
        for k, v in value.items():  # values of a HASH_MAPS object are its name -> hash entries
            out.append(k)
            out += strings(v, k, in_hash_map=key in HASH_MAPS)
        return out
    if isinstance(value, list):
        return [s for v in value for s in strings(v, key)]
    if isinstance(value, str) and not exempt(key, value, in_hash_map):
        return [value]
    return []


def csv_strings() -> list[str]:
    out = []
    for path in sorted(CSV_DIR.glob("*.csv")):
        for row in csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"))):
            out += strings(row)
    return out


def fixture_strings() -> dict[str, list[str]]:
    return {
        "bundle": strings(bundle()),
        "csv": csv_strings(),
        "manifest": strings(json.loads((CSV_DIR / "manifest.json").read_bytes())),
        "scenarios": strings(json.loads(SCENARIOS.read_bytes())),
        "provenance": strings(json.loads(PROVENANCE.read_bytes())),
        "samples": strings(DATASET) + strings(RECORDS),
    }


@pytest.mark.parametrize("source", sorted(fixture_strings()))
def test_every_raw_match_in_committed_fixtures_is_on_the_allowlist(source):
    offending = [
        (kind, match)
        for text in fixture_strings()[source]
        for kind, match in scan(text)
        if not allowlisted(kind, match)
    ]
    assert offending == []


def test_the_scan_sees_the_allowlisted_overlays():
    """Not vacuous: the release carries allowlisted personal-data-like text of every kind."""
    kinds = {kind for text in fixture_strings()["bundle"] for kind, _ in scan(text)}
    assert kinds == {"email", "upi", "number", "pin"}
    assert sum(1 for t in fixture_strings()["bundle"] if matches_any(t)) >= 10


@pytest.mark.parametrize(
    ("kind", "value"),
    [
        ("email", "someone@mail.invalid"),
        ("upi", "someone@okbank"),
        ("number", "+91 9000000000"),
        ("number", "9000000000"),
        ("number", "+91 9100000000"),
        ("pin", "999999"),
    ],
)
def test_the_allowlist_rejects_values_outside_it(kind, value):
    assert [k for k, m in scan(value) if not allowlisted(k, m)] == [kind]


def test_ids_actor_refs_carriers_and_dataset_text_have_no_match_at_all():
    """§5.1: these surfaces are rejected on any match, so no fixture value may match."""
    data = bundle()
    values = strings(data["dataset"])
    for entity in rc.Entity:
        model = rc.ENTITY_MODELS[entity]
        text_field = rc.FREE_TEXT_FIELDS.get(entity)
        for record in data[entity.value]:
            values += [
                v
                for k, v in record.items()
                if k in model.model_fields
                and k != text_field
                and isinstance(v, str)
                and not exempt(k, v)
            ]
    assert values and not any(matches_any(v) for v in values)


def test_the_exemptions_are_narrow_in_field_and_form():
    """Only well-formed hashes and amounts under the named fields are skipped. Personal-data-
    like text under those names, a hash or amount in another field, and keys are scanned."""
    sha = "9876543210" * 6 + "abcd"  # leading 9: not an allowlisted phone-like form
    assert scan(sha)  # why hashes need an exemption: their digit runs match by construction
    exempted = {
        "catalog_raw_sha256": sha,
        "unit_price": "149999.00",
        "files": {"bundle/returns_v1.json": sha},
        "bundle_sha256": {"json": sha},
    }
    assert strings(exempted) == [
        "catalog_raw_sha256",
        "unit_price",
        "files",
        "bundle/returns_v1.json",
        "bundle_sha256",
        "json",
    ]
    for value in (
        {"catalog_raw_sha256": "call 9000000000"},
        {"sha256": "someone@mail.invalid"},
        {"unit_price": "999999"},
        {"files": {"bundle.json": "PIN 999999"}},
        {"price": "149999.00"},  # money form, but not the money field
        {"text": sha},  # hash form, but not a hash field
        {"someone@mail.invalid": "x"},  # keys are scanned
    ):
        found = [m for s in strings(value) for k, m in scan(s) if not allowlisted(k, m)]
        assert found, value


def test_redacted_outputs_of_every_free_text_have_no_match():
    data = bundle()
    texts = [
        record[field]
        for entity, field in rc.FREE_TEXT_FIELDS.items()
        for record in data[entity.value]
        if record[field] is not None
    ]
    assert len(texts) > 300
    redacted = [redact(t).text for t in texts]
    assert not any(matches_any(t) for t in redacted)
    assert sum(t != r for t, r in zip(texts, redacted, strict=True)) >= 10


def test_the_release_directory_holds_only_the_contracted_files():
    names = sorted(p.relative_to(RELEASE).as_posix() for p in RELEASE.rglob("*") if p.is_file())
    assert names == sorted(
        [
            "bundle/returns_v1.json",
            "provenance.json",
            "scenarios.json",
            "csv/manifest.json",
            *(f"csv/{e.value}.csv" for e in rc.Entity),
        ]
    )


# ---- M18-05: frontend fixtures (zero matches, §5.3) -------------------------------------------

FRONTEND_FIXTURES = RELEASE.parents[2] / "frontend" / "e2e" / "fixtures" / "returns"
# API-generated forms whose digit runs match by construction: canonical UTC timestamps (six
# fraction digits), SHA-256 hashes, batch UUIDs and canonical money. Each is exempt only under
# its named fields and only in that exact form; everything else, including every key, is scanned.
CANONICAL_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
FRONTEND_EXEMPT = {
    **dict.fromkeys(
        (
            "checked_at",
            "requested_at",
            "received_at",
            "ordered_at",
            "submitted_at",
            "observed_at",
            "occurred_at",
            "shipped_at",
            "promised_delivery_by",
            "delivered_at",
            "opened_at",
            "imported_at",
            "ingestion_timestamp",
        ),
        CANONICAL_TIMESTAMP,
    ),
    **dict.fromkeys(("bundle_sha256", "raw_sha256"), SHA256),
    "batch_id": UUID,
    "unit_price": MONEY,
}


def frontend_strings(value, key: str = "") -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *frontend_strings(v, k)]]
    if isinstance(value, list):
        return [s for v in value for s in frontend_strings(v, key)]
    if isinstance(value, str):
        form = FRONTEND_EXEMPT.get(key)
        return [] if form is not None and form.fullmatch(value) else [value]
    return []


def frontend_fixtures() -> dict[str, list[str]]:
    return {
        p.name: frontend_strings(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(FRONTEND_FIXTURES.glob("*.json"))
    }


def test_frontend_fixtures_exist():
    assert len(frontend_fixtures()) >= 15


@pytest.mark.parametrize("name", sorted(frontend_fixtures()))
def test_frontend_fixtures_have_no_match_at_all(name):
    assert [(k, m) for text in frontend_fixtures()[name] for k, m in scan(text)] == []


def test_the_frontend_exemptions_are_narrow_in_field_and_form():
    exempted = {
        "requested_at": "2026-03-04T06:30:00.000000Z",
        "raw_sha256": "9876543210" * 6 + "abcd",
        "batch_id": "7d3f9c2e-4a1b-4e8d-9c5f-2b7e1a9d4c3f",
        "unit_price": "149999.00",
    }
    assert frontend_strings(exempted) == list(exempted)  # only the keys remain
    for value in (
        {"requested_at": "call 9000000000"},
        {"requested_at": "2026-03-04T06:30:00+05:30 PIN 999999"},
        {"text": "2026-03-04T06:30:00.000000Z"},  # timestamp form, but not a timestamp field
        {"batch_id": "999999"},
        {"raw_sha256": "someone@mail.invalid"},
        {"someone@mail.invalid": "x"},  # keys are scanned
    ):
        assert [m for s in frontend_strings(value) for _, m in scan(s)], value

"""M13-3 storage logic that needs no database: lock keys, records as stored, integrity checks
and the ORM's enumerated CHECK values. All listings are SYNTHETIC test fixtures."""

import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from ecommerce_search.models.listings import ListingCandidate, ListingFinding
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization import store
from ecommerce_search.normalization.intake import ListingIdentity, parse_raw_listing
from ecommerce_search.normalization.models import FindingKind, Outcome
from ecommerce_search.normalization.normalizer import normalize_listing
from ecommerce_search.normalization.rules import Basis, ValueType
from ecommerce_search.normalization.store import (
    IntegrityFailure,
    StoredCandidate,
    StoredFinding,
    StoredRun,
    StoredVersion,
    candidate_record,
    finding_record,
    listing_lock_key,
    verify_integrity,
)

ROOT = Path(__file__).resolve().parents[3]

# SYNTHETIC listings. Together they produce every finding kind, outcome, basis and value type.
SYNTHETIC = [
    {
        "seller_id": "SYN-TEST-SELLER",
        "seller_listing_ref": "SYN-COVER-1",
        "category": "laptop",
        "subcategory": "gaming-shoe",
        "brand": "Nike",
        "title": "Zénith 🔥 laptop 16GB RAM 512GB NVMe SSD up to 64GB RAM",
        "description": "Ships with 1TB HDD",
        "price": "₹40,000",
        "currency": "inr",
        "availability": "in_stock",
        "ram_gb": "8 gb",
        "storage_type": "NVMe SSD",
        "screen_size_inches": "999 inch",
        "weight_kg": "",
        "size": "9",
        "rating": 4.5,
        "review_count": 12,
        "color_code": "x",
    },
    {
        "seller_id": "SYN-TEST-SELLER",
        "seller_listing_ref": "SYN-COVER-2",
        "brand": "hp",
        "wireless": True,
        "connectivity": "wired",
        "storage_type": "SATA",
        "ram_gb": "16 GiB",
        "anc": None,
    },
]


def _raw(listing: dict) -> bytes:
    return json.dumps(listing, ensure_ascii=False).encode("utf-8")


def _stored(listing: dict) -> StoredVersion:
    raw = parse_raw_listing(_raw(listing))
    result = normalize_listing(raw)
    run = StoredRun(
        rules_version=result.rules_version,
        rules_digest=result.rules_digest,
        taxonomy_version=result.taxonomy_version,
        output_sha256=result.output_sha256(),
        candidate_count=len(result.candidates),
        finding_count=len(result.findings),
        created_at=None,
        candidates=tuple(candidate_record(c) for c in result.candidates),
        findings=tuple(finding_record(f) for f in result.findings),
        candidate_ordinals=tuple(range(len(result.candidates))),
        finding_ordinals=tuple(range(len(result.findings))),
    )
    return StoredVersion(
        seller_id=raw.identity.seller_id,
        seller_listing_ref=raw.identity.seller_listing_ref,
        version_number=1,
        version_count=1,
        raw_bytes=raw.raw_bytes,
        raw_sha256=raw.raw_sha256,
        submitted_at=None,
        runs=(run,),
    )


# ---- lock key ---------------------------------------------------------------------------


def test_lock_key_follows_its_specification():
    identity = ListingIdentity(seller_id="SYN-S", seller_listing_ref="SYN-R")
    digest = hashlib.sha256(b'listing-normalization:["SYN-S","SYN-R"]').digest()
    assert listing_lock_key(identity) == int.from_bytes(digest[:8], "big", signed=True)


def test_lock_key_is_exact_and_unambiguous():
    key = lambda s, r: listing_lock_key(ListingIdentity(seller_id=s, seller_listing_ref=r))  # noqa: E731
    assert key("ab", "c") != key("a", "bc")
    assert key("S", "r") != key("s", "r")  # identity is case-sensitive, never casefolded
    assert -(2**63) <= key("S", "r") < 2**63


def test_lock_key_is_stable_across_processes_with_different_hash_seeds():
    code = (
        "from ecommerce_search.normalization.intake import ListingIdentity\n"
        "from ecommerce_search.normalization.store import listing_lock_key\n"
        "print(listing_lock_key(ListingIdentity(seller_id='SYN-S', seller_listing_ref='SYN-R')))"
    )
    keys = set()
    for seed in ("1", "2", "random"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
            [sys.executable, "-c", code],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        keys.add(int(result.stdout))
    identity = ListingIdentity(seller_id="SYN-S", seller_listing_ref="SYN-R")
    assert keys == {listing_lock_key(identity)}


# ---- records as stored ------------------------------------------------------------------


def test_fixtures_cover_every_kind_outcome_basis_and_value_type():
    results = [normalize_listing(parse_raw_listing(_raw(listing))) for listing in SYNTHETIC]
    candidates = [c for r in results for c in r.candidates]
    assert {f.kind for r in results for f in r.findings} == set(FindingKind)
    assert {c.outcome for c in candidates} == set(Outcome)
    assert {c.basis for c in candidates} == set(Basis)
    assert {c.value_type for c in candidates if c.value_type} == set(ValueType)


@pytest.mark.parametrize("listing", SYNTHETIC, ids=lambda d: d["seller_listing_ref"])
def test_recorded_output_reproduces_the_contract_dump_and_hash(listing):
    raw = parse_raw_listing(_raw(listing))
    result = normalize_listing(raw)
    (run,) = _stored(listing).runs
    assert run.recorded_output() == result.model_dump(mode="json")
    assert run.recomputed_output_sha256() == result.output_sha256()
    verify_integrity(_stored(listing))


def test_record_fields_equal_the_orm_columns():
    candidate_columns = {c.name for c in ListingCandidate.__table__.columns}
    finding_columns = {c.name for c in ListingFinding.__table__.columns}
    assert set(StoredCandidate.__dataclass_fields__) == candidate_columns - {
        "id",
        "run_id",
        "ordinal",
    }
    assert set(StoredFinding.__dataclass_fields__) == finding_columns - {"id", "run_id", "ordinal"}


# ---- integrity --------------------------------------------------------------------------


def _first_run(stored: StoredVersion) -> StoredRun:
    return stored.runs[0]


def _with_run(stored: StoredVersion, **changes) -> StoredVersion:
    return replace(stored, runs=(replace(_first_run(stored), **changes),))


def _tampered_cases():
    good = _stored(SYNTHETIC[0])
    run = _first_run(good)
    other = _raw({**SYNTHETIC[0], "seller_listing_ref": "SYN-OTHER"})
    changed = replace(run.candidates[0], normalized_value="NotTheRecordedValue")
    return {
        "raw bytes": (replace(good, raw_bytes=good.raw_bytes + b" "), "raw_sha256"),
        "raw hash": (replace(good, raw_sha256="0" * 64), "raw_sha256"),
        "raw identity": (
            replace(good, raw_bytes=other, raw_sha256=hashlib.sha256(other).hexdigest()),
            "identity",
        ),
        "raw not intake": (
            replace(good, raw_bytes=b"[]", raw_sha256=hashlib.sha256(b"[]").hexdigest()),
            "intake",
        ),
        "candidate value": (
            _with_run(good, candidates=(changed, *run.candidates[1:])),
            "output_sha256",
        ),
        "finding dropped": (
            _with_run(good, findings=run.findings[1:], finding_ordinals=run.finding_ordinals[1:]),
            "finding rows",
        ),
        "ordinal gap": (
            _with_run(good, finding_ordinals=(*run.finding_ordinals[:-1], 99)),
            "ordinals",
        ),
        "output hash": (_with_run(good, output_sha256="f" * 64), "output_sha256"),
        "digest": (_with_run(good, rules_digest="e" * 64), "output_sha256"),
    }


@pytest.mark.parametrize("case", sorted(_tampered_cases()))
def test_integrity_failures_are_reported(case):
    stored, fragment = _tampered_cases()[case]
    with pytest.raises(IntegrityFailure) as excinfo:
        verify_integrity(stored)
    assert fragment in str(excinfo.value)
    assert "SYN-COVER-1" in str(excinfo.value)


def test_a_historical_run_is_read_as_recorded_without_current_rules(monkeypatch):
    # A run recorded under an older (synthetic) rules version, with a retired rule id and field
    # that today's contracts would reject. Integrity holds against its own stored hash; nothing
    # is re-normalized and the historical digest need not equal today's.
    def refuse(*_args, **_kwargs):
        raise AssertionError("reading history must not re-run normalization")

    monkeypatch.setattr(store, "normalize_listing", refuse)
    good = _stored(SYNTHETIC[1])
    candidate = StoredCandidate(
        field="retired_field",
        basis="seller_field",
        source_key="brand",
        raw_text="hp",
        span_start=None,
        span_end=None,
        outcome="normalized",
        normalized_value="hp-as-recorded-then",
        value_type="text",
        unit=None,
        rule_id="retired.rule",
    )
    finding = StoredFinding("value_conflict", "retired_field", None, None, "as recorded")
    historical = StoredRun(
        rules_version="listing-norm-0-synthetic",
        rules_digest="0" * 64,
        taxonomy_version="synthetic-taxonomy-0",
        output_sha256="",
        candidate_count=1,
        finding_count=1,
        created_at=None,
        candidates=(candidate,),
        findings=(finding,),
        candidate_ordinals=(0,),
        finding_ordinals=(0,),
    )
    historical = replace(historical, output_sha256=historical.recomputed_output_sha256())
    assert historical.rules_digest != nr.rules_digest()
    verify_integrity(replace(good, runs=(historical, *good.runs)))


# ---- ORM enumerated values --------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "name", "enum_cls"),
    [
        (ListingCandidate, "basis_valid", Basis),
        (ListingCandidate, "outcome_valid", Outcome),
        (ListingCandidate, "value_type_valid", ValueType),
        (ListingFinding, "kind_valid", FindingKind),
    ],
)
def test_check_constraints_use_the_contract_values(model, name, enum_cls):
    check = next(
        c for c in model.__table__.constraints if str(getattr(c, "name", "")).endswith(name)
    )
    assert set(re.findall(r"'([^']+)'", str(check.sqltext))) == {m.value for m in enum_cls}

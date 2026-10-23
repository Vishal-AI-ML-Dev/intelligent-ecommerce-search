"""Milestone 17: post-hoc unsupported-claims extraction (`claims.py`) over saved responses from
mocked runs: binding, atomic boundaries, duplicate rule, mechanical verdicts, seller text,
omission findings, adjudication union and refusals. No provider is called; no verdict is made.
"""

import hashlib
import json
from pathlib import Path

import pytest
from evals.assistant import __main__ as cli
from evals.assistant.claims import (
    ADJUDICATION_VERSION,
    _clause_check,
    extract,
    load_adjudications,
    summarize,
)
from evals.assistant.contracts import file_sha256, load_cases

FIXTURES = Path(__file__).parents[3] / "evals/assistant/fixtures/synthetic-v1/cases.jsonl"
CLEAN = {"code_commit": "c" * 40, "tracked_modified": [], "untracked_code": []}
DELL = "grounded-dell-under-budget"
SPEC = {
    "cases": [
        {
            "case_id": DELL,
            "requirements": {"category": "laptop", "brand": "Dell", "max_price": "50000",
                             "gender": ["men"], "ram_gb": 16},
            "not_assessed": ["coding suitability"],
        }
    ]
}  # fmt: skip


def run_dir(tmp_path, *ids):
    only = tmp_path / "only.txt"
    only.write_text("".join(f"{i}\n" for i in ids), "utf-8")
    out = tmp_path / "run"
    argv = ["mocked", "--only", str(only), "--out", str(out), "--run-id", "claims-test"]
    cli.main(argv, provenance_check=lambda: CLEAN)
    return out


def cases():
    return {c.case_id: c for c in load_cases(FIXTURES)}


def rebind(out):
    report = json.loads((out / "report.json").read_text("utf-8"))
    report["responses_sha256"] = file_sha256(out / "responses.jsonl")
    (out / "report.json").write_text(json.dumps(report), "utf-8")


def test_claims_are_bound_atomic_and_counted_once(tmp_path):
    out = run_dir(tmp_path, DELL)
    claims, _, binding = extract(out, cases(), SPEC)
    line = (out / "responses.jsonl").read_text("utf-8").splitlines()[0]
    assert binding["run_id"] == "claims-test" and binding["responses"] == 1
    assert len({c["claim_id"] for c in claims}) == len(claims)
    for c in claims:
        assert c["run_id"] == "claims-test" and c["case_id"] == DELL
        assert c["responses_sha256"] == file_sha256(out / "responses.jsonl")
        assert c["response_sha256"] == hashlib.sha256(line.encode()).hexdigest()
        assert c["json_path"].startswith("$.response.") and c["text"]
    summary = [c for c in claims if c["json_path"].startswith("$.response.summary")]
    assert [c["claim_type"] for c in summary] == ["C3_scope", "C6_disclaimer", "C6_disclaimer"]
    assert all(c["primary_verdict"] == "supported" for c in summary)
    facts = [c for c in claims if c["claim_type"] == "C1_fact"]
    assert facts and all(c["span"] is not None for c in facts)  # rendered in the explanation
    # the explanation's "Cited catalog facts" is not counted a second time
    assert not [c for c in claims if c["json_path"].endswith("explanation") and
                c["claim_type"] == "C1_fact"]  # fmt: skip
    requirements = [c for c in claims if c["claim_type"] == "C2_requirement"]
    assert requirements and all(c["primary_verdict"] == "supported" for c in requirements)
    assert all(c["misleading"] is None for c in claims)


def test_tampered_displays_are_contradicted_mechanically(tmp_path):
    out = run_dir(tmp_path, DELL)
    record = json.loads((out / "responses.jsonl").read_text("utf-8"))
    record["response"]["summary"] = record["response"]["summary"].replace(
        "selected 1", "selected 2"
    )
    record["response"]["recommendations"][0]["facts"][0]["value"] = "1.00"
    (out / "responses.jsonl").write_text(json.dumps(record) + "\n", "utf-8", newline="\n")
    with pytest.raises(ValueError, match="responses_sha256"):
        extract(out, cases(), SPEC)
    rebind(out)
    claims, _, _ = extract(out, cases(), SPEC)
    contradicted = {c["claim_type"] for c in claims if c["primary_verdict"] == "contradicted"}
    assert contradicted == {"C3_scope", "C1_fact"}
    assert any(c.get("catalog_consistency") == "inconsistent" for c in claims)


def test_status_and_interpretation_clauses_need_a_human():
    response = {"recommendations": [], "evidence": [], "generation": {"status": "not_needed",
                "calls": 0}}  # fmt: skip
    kind, verdict = _clause_check("No catalog products matched this request", response)
    assert verdict["verdict_source"] == "needs_human" and verdict["primary_verdict"] is None
    kind, verdict = _clause_check("so no answer was generated.", response)
    assert verdict["primary_verdict"] == "supported"


def test_seller_text_is_not_truth_and_injected_text_is_flagged(tmp_path):
    out = run_dir(tmp_path, "listing-clean-counterpart", "listing-injection-ignored-stays-visible")
    lines = (out / "responses.jsonl").read_text("utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    injected = next(r for r in records if r["case_id"] == "listing-injection-ignored-stays-visible")
    product = next(p for p in injected["response"]["evidence"] if p["product_id"] == "SYN-LAP-012")
    rec = injected["response"]["recommendations"][0]
    rec["product_id"] = "SYN-LAP-012"
    rec["facts"] = [{"field": "title", "value": product["title"]}]
    rec["constraint_checks"] = []
    (out / "responses.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), "utf-8", newline="\n"
    )
    rebind(out)
    claims, _, _ = extract(out, cases(), {"cases": []})
    seller = [c for c in claims if c["claim_type"] == "C7_seller_text"]
    assert len(seller) == 1 and seller[0]["catalog_consistency"] == "consistent"
    assert seller[0]["verdict_source"] == "needs_human" and seller[0]["untrusted_instruction"]
    assert seller[0]["real_world_truth"].startswith("not assessed")


def test_missing_requested_attributes_are_omissions_not_false_claims(tmp_path):
    out = run_dir(tmp_path, DELL)
    claims, omissions, _ = extract(out, cases(), SPEC)
    kinds = {(o["kind"], o["requirement"]) for o in omissions}
    assert ("stated requirement not server-checked", "gender") in kinds
    assert ("stated requirement not extracted by the parser", "ram_gb") in kinds
    assert ("not assessed (owner-reviewed spec)", "coding suitability") in kinds
    assert not [c for c in claims if c["primary_verdict"] == "contradicted"]


def adjudication(claims_sha256, claim_id, primary=None, misleading=None):
    return {
        "format": ADJUDICATION_VERSION, "claims_sha256": claims_sha256, "claim_id": claim_id,
        "primary_verdict": primary, "misleading": misleading, "reviewer_handle": "ann-1",
        "provenance": "test", "notes": "",
    }  # fmt: skip


def test_union_is_counted_once_and_adjudications_are_bound(tmp_path):
    out = run_dir(tmp_path, DELL)
    claims, omissions, binding = extract(out, cases(), SPEC)
    a, b = claims[0]["claim_id"], claims[1]["claim_id"]
    rows = [
        adjudication("f" * 64, a, "contradicted", "yes"),
        adjudication("f" * 64, b, None, "yes"),
    ]
    path = tmp_path / "adj.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), "utf-8")
    with pytest.raises(ValueError, match="not bound"):
        load_adjudications(path, "0" * 64)
    summary = summarize(claims, omissions, load_adjudications(path, "f" * 64), binding)
    assert summary["unsupported_or_misleading_union"]["numerator"] == 2  # a counted once
    assert summary["unsupported_rate"]["numerator"] == 1
    assert summary["verdict_sources"]["human"] == 2
    assert summary["post_hoc"].startswith("defined after the runs")
    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps(adjudication("f" * 64, a, "false")) + "\n", "utf-8")
    with pytest.raises(ValueError, match="unknown primary verdict"):
        load_adjudications(bad, "f" * 64)


def test_a_run_without_saved_responses_is_unavailable(tmp_path, capsys):
    run = tmp_path / "run-001-like"
    run.mkdir()
    (run / "report.json").write_text("{}", "utf-8")
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"cases": []}), "utf-8")
    argv = ["claims", "--run-dir", str(run), "--cases", str(FIXTURES), "--spec", str(spec),
            "--out", str(tmp_path / "out")]  # fmt: skip
    assert cli.main(argv) == 2
    assert "measurement unavailable" in capsys.readouterr().err

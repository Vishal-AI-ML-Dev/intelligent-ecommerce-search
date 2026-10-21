"""M14-4 offline evaluator end to end: binding checks, split policy, disclosure, deterministic
report bytes, the CLI and offline imports."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from evals.listing_decisions.cli import main
from evals.listing_decisions.contracts import EvalSetError, Split, load_set
from evals.listing_decisions.evaluate import DISCLOSURE, evaluate, render
from listing_eval_support import (
    ITEMS,
    LAPTOP,
    PHONE,
    SHOE_CASUAL,
    default_outcomes,
    label,
    outcome,
    raw_bytes,
    write_fixture,
    write_outcomes,
)

from ecommerce_search.decision.listing_models import ListingDecisionOutcome
from ecommerce_search.normalization.intake import raw_sha256


def _evaluate(fixture_set, **kwargs):
    set_path, raw_dir, outcomes = fixture_set
    return evaluate(load_set(set_path), raw_dir, outcomes, **kwargs)


def test_the_report_discloses_label_provenance_rights_and_scope(fixture_set) -> None:
    report, failed = _evaluate(fixture_set)
    assert failed == []
    assert report["schema_version"] == "le-report-1"
    assert report["disclosure"] == DISCLOSURE
    assert "not human-reviewed gold" in DISCLOSURE and "Development-scoped" in DISCLOSURE
    assert report["synthetic"] is True and report["set"]["synthetic_fixture"] is True
    assert all(s["rights_caveat"] and s["licence_declared_by"] for s in report["sources"])
    versions = report["versions"]
    assert (versions["question_set"], versions["routing_policy"]) == ("jl-1", "lr-1")
    assert versions["normalization_rules"] == "listing-norm-1"
    assert report["label_metrics"]["category"]["jev"]["label_basis"].endswith(
        "not human-reviewed gold"
    )


def test_reports_are_byte_identical_for_the_same_inputs(fixture_set, tmp_path) -> None:
    first = render(_evaluate(fixture_set)[0])
    assert render(_evaluate(fixture_set)[0]) == first
    again = tmp_path / "again"
    set_path, raw_dir = write_fixture(again)
    outcomes = write_outcomes(again / "o.jsonl", set_path, default_outcomes())
    assert render(evaluate(load_set(set_path), raw_dir, outcomes)[0]) == first
    assert b"timestamp" not in first


def test_dev_is_the_default_and_the_test_split_needs_an_explicit_flag(fixture_set) -> None:
    report, _ = _evaluate(fixture_set)
    assert report["split"] == {"evaluated": "dev", "test_split_explicitly_allowed": False}
    assert report["items"]["outside_split"] == 1  # lp-2
    with pytest.raises(EvalSetError, match="explicit allow_test_split"):
        _evaluate(fixture_set, split=Split.TEST)
    report, _ = _evaluate(fixture_set, split=Split.TEST, allow_test_split=True)
    assert report["split"] == {"evaluated": "test", "test_split_explicitly_allowed": True}
    assert report["items"]["in_split"] == 1
    assert report["label_metrics"]["category"]["jev"]["pooled"]["not_attempted"] == 1


def test_a_raw_file_that_does_not_match_its_hash_is_refused(fixture_set) -> None:
    set_path, raw_dir, outcomes = fixture_set
    target = raw_dir / f"{raw_sha256(raw_bytes(SHOE_CASUAL))}.json"
    target.write_bytes(raw_bytes(SHOE_CASUAL | {"color": "Tan"}))
    with pytest.raises(EvalSetError, match="SHA-256 mismatch"):
        evaluate(load_set(set_path), raw_dir, outcomes)


def test_a_missing_raw_file_is_refused(fixture_set) -> None:
    set_path, raw_dir, outcomes = fixture_set
    (raw_dir / f"{raw_sha256(raw_bytes(PHONE))}.json").unlink()
    with pytest.raises(EvalSetError, match="unreadable"):
        evaluate(load_set(set_path), raw_dir, outcomes)


def test_the_seller_category_must_equal_its_label(tmp_path) -> None:
    items = dict(ITEMS)
    fields, source, group, split, _, twin, corrupt = items["lp-1"]
    items["lp-1"] = (fields, source, group, split, [label("category", "phone")], twin, corrupt)
    set_path, raw_dir = write_fixture(tmp_path, items)
    with pytest.raises(EvalSetError, match="seller category must equal its label"):
        evaluate(load_set(set_path), raw_dir)


def _outcomes(fixture_set, tmp_path, outcomes, **header) -> Path:
    set_path, _, _ = fixture_set
    path = write_outcomes(tmp_path / "bad.jsonl", set_path, outcomes)
    if header:
        lines = path.read_text(encoding="utf-8").splitlines()
        lines[0] = json.dumps(json.loads(lines[0]) | header)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _drop_first_claim(value: ListingDecisionOutcome) -> ListingDecisionOutcome:
    data = value.model_dump(mode="json")
    data["attribute_support"] = data["attribute_support"][1:]
    return ListingDecisionOutcome.model_validate(data, strict=False)


@pytest.mark.parametrize(
    ("change", "header", "message"),
    [
        ({"lp-1": outcome(PHONE, ("phone",), ("smartphone",))}, {}, "does not answer this state"),
        ({"shoe-1": _drop_first_claim(outcome(SHOE_CASUAL))}, {}, "does not answer this state"),
        ({}, {"set_sha256": "0" * 64}, "another set"),
        ({}, {"question_set_digest": "0" * 64}, "another jl-1 question set"),
    ],
    ids=["another-listings-state", "missing-asked-claim", "another-set", "another-question-set"],
)
def test_recorded_outcomes_must_match_the_recomputed_state(
    fixture_set, tmp_path, change, header, message
) -> None:
    set_path, raw_dir, _ = fixture_set
    path = _outcomes(fixture_set, tmp_path, default_outcomes() | change, **header)
    with pytest.raises(EvalSetError, match=message):
        evaluate(load_set(set_path), raw_dir, path)


def test_outcome_files_with_unknown_duplicate_or_non_jev_records_are_refused(
    fixture_set, tmp_path
) -> None:
    set_path, raw_dir, good = fixture_set
    lines = good.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[1])
    deterministic = {
        "item_id": first["item_id"],
        "outcome": {"provider": "deterministic", "state_sha256": first["outcome"]["state_sha256"]},
    }
    cases = {
        "unknown item": [*lines, json.dumps(first | {"item_id": "nope"})],
        "more than one outcome": [*lines, lines[1]],
        "must come from jev": [lines[0], json.dumps(deterministic)],
    }
    for message, body in cases.items():
        path = tmp_path / "case.jsonl"
        path.write_text("\n".join(body) + "\n", encoding="utf-8")
        with pytest.raises(EvalSetError, match=message):
            evaluate(load_set(set_path), raw_dir, path)


def test_no_auto_accept_and_no_rejection_appear_anywhere(fixture_set) -> None:
    text = render(_evaluate(fixture_set)[0]).decode("utf-8")
    assert '"auto_accept"' not in text and "reject" not in text


# --- CLI ----------------------------------------------------------------------------------------


def _argv(fixture_set, out: Path, *extra: str) -> list[str]:
    set_path, raw_dir, outcomes = fixture_set
    return [
        "evaluate",
        "--set",
        str(set_path),
        "--raw-dir",
        str(raw_dir),
        "--outcomes",
        str(outcomes),
        "--out",
        str(out),
        *extra,
    ]


def test_cli_writes_the_report_once_and_refuses_to_overwrite(fixture_set, tmp_path) -> None:
    out = tmp_path / "report.json"
    assert main(_argv(fixture_set, out)) == 0
    assert out.read_bytes() == render(_evaluate(fixture_set)[0])
    assert main(_argv(fixture_set, out)) == 2


def test_cli_refuses_the_test_split_without_the_flag(fixture_set, tmp_path) -> None:
    out = tmp_path / "test.json"
    assert main(_argv(fixture_set, out, "--split", "test")) == 2 and not out.exists()
    assert main(_argv(fixture_set, out, "--split", "test", "--allow-test-split")) == 0


def test_cli_exits_one_on_a_failed_check_and_still_writes_the_report(tmp_path) -> None:
    from listing_eval_support import CORRUPT_INJECTION

    set_path, raw_dir = write_fixture(tmp_path)
    outcomes = default_outcomes() | {
        "shoe-1": outcome(SHOE_CASUAL, support={"color": ("contradicted",)}),
        "shoe-1-c2": outcome(ITEMS["shoe-1-c2"][0], support={"color": ("contradicted",)}),
        "shoe-1-c3": outcome(CORRUPT_INJECTION),
    }
    path = write_outcomes(tmp_path / "o.jsonl", set_path, outcomes)
    out = tmp_path / "report.json"
    argv = ["evaluate", "--set", str(set_path), "--raw-dir", str(raw_dir)]
    assert main([*argv, "--outcomes", str(path), "--out", str(out)]) == 1
    assert json.loads(out.read_bytes())["failed_checks"] == [
        "instruction_injection.jev.non_degradation"
    ]


def test_cli_rejects_invalid_input_without_writing(tmp_path) -> None:
    set_path, raw_dir = write_fixture(tmp_path)
    (raw_dir / f"{raw_sha256(raw_bytes(LAPTOP))}.json").unlink()
    out = tmp_path / "report.json"
    argv = ["evaluate", "--set", str(set_path), "--raw-dir", str(raw_dir), "--out", str(out)]
    assert main(argv) == 2 and not out.exists()


def test_the_evaluator_imports_no_sdk_settings_database_or_network_module() -> None:
    root = Path(__file__).resolve().parents[3]
    child = (
        "import json, sys; sys.path[:0] = [sys.argv[1], sys.argv[2]];"
        "import evals.listing_decisions.cli;"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] in "
        "{'typesafe_sdk','httpx','httpx2','sqlalchemy','psycopg','fastapi','dotenv'} "
        "or m.startswith(('ecommerce_search.config','ecommerce_search.db')))))"
    )
    env = {k: v for k, v in os.environ.items() if k in ("SYSTEMROOT", "PATH")}
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-I", "-c", child, str(root / "src"), str(root)],
        capture_output=True,
        text=True,
        check=True,
        env=env,
        timeout=60,
    )
    assert json.loads(result.stdout) == []

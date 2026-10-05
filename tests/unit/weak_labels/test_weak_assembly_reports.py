"""Weak-set assembly, the audit report and the provisional metrics on a SYNTHETIC temporary
generation. The 100 audit decisions are SYNTHETIC TEST INPUTS typed by this test (every third
pair overridden), never human decisions; nothing real is written."""

import json
from datetime import timedelta

import pytest
from evals.contracts.canonical import ContractError
from evals.contracts.constants import Label
from evals.runners.golden_io import write_exclusive
from evals.weak_labels import reports
from evals.weak_labels.assembly import (
    FINAL_FILE,
    SEALED_FILE,
    assemble,
    verify_sealed,
)
from evals.weak_labels.audit import AuditError, AuditStore, audit
from evals.weak_labels.contracts import BANNER, LabelSource
from weak_support import AUDIT_T0, Clock, audit_context, generate, scripted, weak_fixture

LATER = AUDIT_T0 + timedelta(days=365)


def synthetic_answers(context) -> list[str]:
    answers = []
    for item in context.queue:
        proposal = context.proposals[(item.query_id, item.product_id)]
        if item.audit_position % 3:
            answers.append("a")
        elif proposal.proposed_label is Label.NOT_RELEVANT:
            answers.append("b2l")
        else:
            answers.append("n2h")
    return answers


def audited(tmp_path, catalog, *, complete=True):
    fx = weak_fixture(tmp_path, catalog)
    generate(fx)
    context = audit_context(fx)
    store = AuditStore(context, fx.audit_root)
    answers = synthetic_answers(context)
    if not complete:
        answers = answers[:40] + ["q"]
    audit(
        context,
        store,
        annotator="ann-1",
        ask=scripted(answers),
        say=lambda _text: None,
        now=Clock(),
    )
    return fx, context, store


@pytest.fixture(scope="module")
def sealed(tmp_path_factory, catalog):
    fx, context, store = audited(tmp_path_factory.mktemp("weakseal"), catalog)
    record = assemble(
        context,
        store,
        annotator="ann-1",
        now=LATER,
        weak_dir=fx.weak_dir,
        allowed_root=fx.weak_root,
    )
    weak = verify_sealed(context, fx.weak_dir, allowed_root=fx.weak_root)
    return fx, context, record, weak


def test_assembly_refuses_before_100_decisions(tmp_path, catalog):
    fx, context, store = audited(tmp_path, catalog, complete=False)
    with pytest.raises(AuditError, match="all 100 audit decisions"):
        assemble(
            context,
            store,
            annotator="ann-1",
            now=LATER,
            weak_dir=fx.weak_dir,
            allowed_root=fx.weak_root,
        )
    assert not (fx.weak_dir / SEALED_FILE).exists()


def test_assembly_is_human_only_and_refuses_while_locked(tmp_path, catalog):
    fx, context, store = audited(tmp_path, catalog)
    with pytest.raises(AuditError, match="only 'ann-1'"):
        assemble(
            context,
            store,
            annotator="weak-label-policy-1",
            now=LATER,
            weak_dir=fx.weak_dir,
            allowed_root=fx.weak_root,
        )
    store.lock_path.write_text("{}", encoding="utf-8")
    with pytest.raises(AuditError, match="lock"):
        assemble(
            context,
            store,
            annotator="ann-1",
            now=LATER,
            weak_dir=fx.weak_dir,
            allowed_root=fx.weak_root,
        )


def test_every_label_records_its_source(sealed):
    fx, context, record, weak = sealed
    assert len(weak.finals) == len(fx.facts.pool)
    counts = {s: sum(f.label_source is s for f in weak.finals) for s in LabelSource}
    assert counts[LabelSource.HUMAN_ACCEPTED_PROPOSAL] + counts[LabelSource.HUMAN_OVERRIDE] == 100
    assert counts[LabelSource.HUMAN_OVERRIDE] == 33
    assert counts[LabelSource.MACHINE_UNREVIEWED] == len(fx.facts.pool) - 100
    queued = {(q.query_id, q.product_id) for q in context.queue}
    for f in weak.finals:
        assert ((f.query_id, f.product_id) in queued) == (
            f.label_source is not LabelSource.MACHINE_UNREVIEWED
        )
        if f.label_source is LabelSource.MACHINE_UNREVIEWED:
            assert f.decided_by is None and f.label is f.proposed_label
    assert record.status == "weak_dev_sealed" and record.banner == BANNER
    assert record.machine_unreviewed_count == counts[LabelSource.MACHINE_UNREVIEWED]


def test_sealed_set_is_never_overwritten_and_tampering_fails(sealed, tmp_path, catalog):
    fx, context, _, _ = sealed
    store = AuditStore(context, fx.audit_root)
    with pytest.raises(ContractError, match="refusing to overwrite"):
        assemble(
            context,
            store,
            annotator="ann-1",
            now=LATER,
            weak_dir=fx.weak_dir,
            allowed_root=fx.weak_root,
        )
    other, other_context, other_store = audited(tmp_path, catalog)
    assemble(
        other_context,
        other_store,
        annotator="ann-1",
        now=LATER,
        weak_dir=other.weak_dir,
        allowed_root=other.weak_root,
    )
    path = other.weak_dir / FINAL_FILE
    path.write_bytes(path.read_bytes().replace(b"machine_unreviewed", b"human_override", 1))
    with pytest.raises(AuditError, match="does not match"):
        verify_sealed(other_context, other.weak_dir, allowed_root=other.weak_root)


def test_audit_report(sealed):
    _, context, _, weak = sealed
    data = reports.audit_report(context, weak)
    assert data["banner"] == BANNER
    assert data["audited_count"] == 100 and data["override_count"] == 33
    assert data["accept_rate"] == "0.6700000000" and data["override_rate"] == "0.3300000000"
    assert data["machine_only_label_count"] == len(weak.finals) - 100
    confusion = data["confusion_matrix_proposed_by_final"]
    assert sum(sum(row.values()) for row in confusion.values()) == 100
    assert sum(q["audited"] for q in data["per_query"]) == 100
    assert all(q["audited"] == 5 for q in data["per_query"])
    by_label = data["agreement_by_proposed_label"]
    assert sum(row["audited"] for row in by_label.values()) == 100
    by_confidence = data["agreement_by_proposal_confidence"]
    assert sum(row["audited"] for row in by_confidence.values()) == 100
    markdown = reports.audit_report_markdown(data)
    assert f"**{BANNER}**" in markdown
    assert "non-blind" in markdown and "no accuracy is claimed" in markdown


def test_provisional_metrics_carry_the_banner_and_cover_every_system(sealed):
    _, context, _, weak = sealed
    data = reports.provisional_metrics(context, weak)
    assert data["banner"] == BANNER and data["evaluator_version"] == "eval-1"
    assert {s["system"] for s in data["systems"]} == {
        "v0_lexical",
        "dense_only",
        "v1_hybrid",
        "v2_filtered",
        "v3_reranked_d10",
        "v3_reranked_d20",
    }
    for system in data["systems"]:
        assert len(system["per_query"]) == 20
        keys = {(m["metric"], m["k"], m["definition"]) for m in system["macro"]}
        for wanted in reports.REPORTED_METRICS:
            assert wanted in keys
    markdown = reports.metrics_markdown(data)
    assert f"**{BANNER}**" in markdown and "precision@10 (strict)" in markdown
    assert data["label_sources"]["machine_unreviewed"] == len(weak.finals) - 100


def test_reports_go_to_the_weak_directory_and_are_never_overwritten(sealed):
    fx, context, _, weak = sealed
    data = reports.audit_report(context, weak)
    files = reports.report_files("audit_report", data, reports.audit_report_markdown(data))
    write_exclusive(fx.weak_dir, files)
    stored = json.loads((fx.weak_dir / "reports" / "audit_report.json").read_text("utf-8"))
    assert stored["banner"] == BANNER
    with pytest.raises(ContractError, match="refusing to overwrite"):
        write_exclusive(fx.weak_dir, files)

"""ann-1's targeted adjudication runner (review-2) on a temporary golden directory sealed
through `second_review_sealed` from SYNTHETIC judgements. Every primary, second-review and
adjudication record here is a SYNTHETIC TEST FIXTURE typed by scripted fake input, never a
human label. The real golden directory and the real work directories are never read or written.
"""

import dataclasses
import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest
from evals.annotation import adjudication as adj
from evals.annotation import reviewer_tool
from evals.annotation.session import WorkStore, load_pool
from evals.contracts.canonical import ContractError, decode_jsonl, sha256_hex
from evals.contracts.models import (
    Confidence,
    Judgement,
    Label,
    LabelChange,
    OverallStatus,
    ReviewReason,
    Round,
)
from evals.contracts.review_sets import REVIEW_2_OPTIONAL_REASONS, build_adjudication_queue
from evals.contracts.validation import validate_adjudication_round
from evals.runners import build_reviewer_bundle, seal_primary, seal_second_review
from pool_support import LABEL_T0, frozen_pool, synthetic_primaries

PRIMARY_SEALED_AT = LABEL_T0 + timedelta(days=2)
REVIEW_T0 = PRIMARY_SEALED_AT + timedelta(hours=1)
SECOND_SEALED_AT = PRIMARY_SEALED_AT + timedelta(hours=3)
ADJ_T0 = SECOND_SEALED_AT + timedelta(hours=1)
RUBRIC = b"SYNTHETIC TEST RUBRIC (not rubric-1 text)\n"


def _flagged_primaries(items):
    """SYNTHETIC primaries with some ann-1 flags and low confidence, so that the targeted
    queue holds mandatory items next to optional (borderline / ambiguous-only) candidates."""
    out = []
    for index, j in enumerate(synthetic_primaries(items)):
        update = {}
        if index % 5 == 0:
            update["needs_adjudication"] = True
        if index % 11 == 4:
            update["confidence"] = Confidence.LOW
            update["rationale"] = j.rationale or "Synthetic low-confidence fixture."
        if index % 13 == 6:
            update["catalog_issue"] = True
        out.append(j.model_copy(update=update) if update else j)
    return out


def _second_review_answers(count: int) -> list[str]:
    """SYNTHETIC compact decisions with some disagreements and some ann-2 flags."""
    answers = []
    for i in range(count):
        if i % 7 == 3:
            answers.append("n2hny!")  # catalog issue flag
        elif i % 2 == 0:
            answers.append("n2hnn!")
        else:
            answers.append("b2mnn! | Synthetic second-review fixture.")
    return answers


@pytest.fixture(scope="module")
def sealed_template(tmp_path_factory, catalog):
    """A golden directory frozen through `second_review_sealed` from SYNTHETIC records."""
    tmp = tmp_path_factory.mktemp("adjudication")
    root, target, _, work = frozen_pool(tmp, catalog)
    context = load_pool(target, allowed_root=root, catalog=catalog)
    store = WorkStore(context, work)
    store.initialize(now=LABEL_T0)
    store.save(_flagged_primaries(context.items), now=PRIMARY_SEALED_AT)
    seal_primary.seal(
        target,
        annotator="ann-1",
        confirm_human_review=True,
        judgements_sha256=sha256_hex(store.path.read_bytes()),
        work_root=work,
        allowed_root=root,
        catalog=catalog,
        now=lambda: PRIMARY_SEALED_AT,
    )
    rubric = tmp / "rubric.md"
    rubric.write_bytes(RUBRIC)
    bundle = tmp / "bundle"
    build_reviewer_bundle.write_bundle(
        target, rubric, bundle, allowed_root=root, catalog=catalog, repository_root=tmp / "repo"
    )
    workspace = reviewer_tool.Workspace(bundle)
    ticks = iter(REVIEW_T0 + timedelta(seconds=i) for i in range(10_000))
    reviewer_tool.annotate(
        workspace,
        annotator="ann-2",
        ask=_scripted(_second_review_answers(len(workspace.items))),
        say=lambda _: None,
        now=lambda: next(ticks),
        compact=True,
    )
    seal_second_review.seal(
        target,
        bundle,
        annotator="ann-2",
        confirm_human_review=True,
        judgements_sha256=sha256_hex((bundle / reviewer_tool.JUDGEMENTS_FILE).read_bytes()),
        allowed_root=root,
        catalog=catalog,
        now=lambda: SECOND_SEALED_AT,
    )
    return tmp


@pytest.fixture
def sealed(sealed_template, tmp_path):
    shutil.copytree(sealed_template / "goldens", tmp_path / "goldens")
    root = tmp_path / "goldens"
    return root, root / "internal-search" / "v1"


@pytest.fixture
def context(sealed, catalog):
    root, target = sealed
    return adj.load_targeted(target, allowed_root=root, catalog=catalog)


@pytest.fixture
def store(context, tmp_path):
    return adj.AdjudicationStore(context, tmp_path / "work")


def _scripted(answers):
    queue = list(answers)

    def ask(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def _clock(start=ADJ_T0):
    ticks = iter(start + timedelta(seconds=i) for i in range(10_000))
    return lambda: next(ticks)


def _letter(label: Label) -> str:
    return {Label.RELEVANT: "r", Label.BORDERLINE: "b", Label.NOT_RELEVANT: "n"}[label]


def _retain(context, item) -> list[str]:
    """SYNTHETIC answers that choose the primary label again (relation 1, medium)."""
    label = context.primaries[(item.query_id, item.product_id)].label
    rationale = "Synthetic retained fixture." if label is Label.BORDERLINE else ""
    return [_letter(label), "1", "m", rationale, "", "n", "y"]


def _correct(context, item) -> list[str]:
    """SYNTHETIC answers that change a never-relevant primary label."""
    label = context.primaries[(item.query_id, item.product_id)].label
    if label is Label.BORDERLINE:
        return ["n", "2", "h", "", "Synthetic correction fixture.", "n", "y"]
    return ["b", "2", "m", "Synthetic rationale fixture.", "Synthetic correction.", "n", "y"]


def _tree(directory: Path) -> dict[str, str]:
    return {
        p.relative_to(directory).as_posix(): sha256_hex(p.read_bytes())
        for p in directory.rglob("*")
        if p.is_file()
    }


# ---- scope and binding ----------------------------------------------------------------------


def test_scope_is_the_targeted_queue_minus_the_joint_resolution_queue(context):
    sealed = context.sealed
    queue = build_adjudication_queue(
        list(sealed.primaries), list(sealed.queries.values()), list(sealed.eligibility.values())
    )
    assert list(context.queue) == queue
    queued = {(i.query_id, i.product_id) for i in queue}
    overlap = queued & set(context.joint)
    assert overlap, "the synthetic fixture must exercise a pair in both queues"
    assert set(context.pairs) == queued - set(context.joint)
    assert not set(context.pairs) & set(context.joint)
    assert len(context.items) == len(queue) - len(overlap)


def test_targeted_only_pairs_split_into_mandatory_and_optional(context):
    pairs = lambda items: {(i.query_id, i.product_id) for i in items}  # noqa: E731
    mandatory, optional = pairs(context.mandatory), pairs(context.optional)
    assert mandatory and optional, "the synthetic fixture must exercise both sets"
    assert not mandatory & optional
    assert mandatory | optional == set(context.pairs)
    assert len(context.mandatory) + len(context.optional) == len(context.items)
    for item in context.optional:
        assert set(item.review_reasons) <= REVIEW_2_OPTIONAL_REASONS
    for item in context.mandatory:
        assert not set(item.review_reasons) <= REVIEW_2_OPTIONAL_REASONS
    # a mandatory reason wins over an optional one on the same pair
    mixed = [
        i
        for i in context.mandatory
        if ReviewReason.BORDERLINE in i.review_reasons
        and ReviewReason.NEEDS_ADJUDICATION in i.review_reasons
    ]
    assert mixed
    # a pair in both queues is in neither set: its joint record settles it
    assert not (mandatory | optional) & set(context.joint)


def test_a_joint_resolution_pair_is_refused(context, store):
    joint_pair = next(
        p for p in context.joint if p in {(i.query_id, i.product_id) for i in context.queue}
    )
    queued = next(i for i in context.queue if (i.query_id, i.product_id) == joint_pair)
    primary = context.primaries[joint_pair]
    store.initialize(now=ADJ_T0)
    record = Judgement(
        query_id=joint_pair[0],
        product_id=joint_pair[1],
        round=Round.ADJUDICATION,
        label=primary.label,
        relation=primary.relation,
        confidence=primary.confidence,
        rationale="Synthetic fixture.",
        annotator_handle="ann-1",
        annotated_at=ADJ_T0,
        rubric_version=primary.rubric_version,
        review_reasons=queued.review_reasons,
        changes_label=False,
    )
    with pytest.raises(ContractError, match="joint-resolution queue"):
        store.save([record], now=ADJ_T0 + timedelta(minutes=1))
    assert not store.path.exists()


def test_a_later_manifest_or_a_changed_sealed_file_is_refused(sealed, catalog):
    root, target = sealed
    (target / "manifests" / "5-labels_frozen.json").write_bytes(b"{}\n")
    with pytest.raises(ContractError, match="no later stage"):
        adj.load_targeted(target, allowed_root=root, catalog=catalog)
    (target / "manifests" / "5-labels_frozen.json").unlink()
    path = target / "joint_resolution_queue.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ContractError):
        adj.load_targeted(target, allowed_root=root, catalog=catalog)


def test_the_binding_is_tied_to_the_sealed_stage(context, store):
    binding = store.initialize(now=ADJ_T0)
    assert binding.second_review_manifest_sha256 == context.manifest.manifest_sha256
    assert binding.joint_resolution_queue_sha256 == (
        context.manifest.entry(adj.Role.JOINT_RESOLUTION_QUEUE).file_sha256
    )
    assert store.initialize(now=ADJ_T0 + timedelta(days=1)) == binding  # written once
    tampered = json.loads(store.binding_path.read_bytes())
    tampered["adjudication_queue_sha256"] = "0" * 64
    store.binding_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ContractError):
        store.load(now=ADJ_T0)


def test_work_cannot_start_before_the_second_review_seal(store):
    with pytest.raises(ContractError, match="created_at"):
        store.initialize(now=SECOND_SEALED_AT - timedelta(seconds=1))
        store.load(now=SECOND_SEALED_AT)


# ---- the session ------------------------------------------------------------------------------


def test_retain_and_correct_record_only_the_required_label_changes(context, store, sealed):
    before = _tree(sealed[1])
    first, second = context.mandatory[0], context.mandatory[1]
    said: list[str] = []
    saved = adj.annotate(
        context,
        store,
        annotator="ann-1",
        ask=_scripted(_retain(context, first) + _correct(context, second)),
        say=said.append,
        now=_clock(),
    )
    assert saved == 2
    judgements, changes = store.load(now=ADJ_T0 + timedelta(hours=1))
    by_pair = {(j.query_id, j.product_id): j for j in judgements}
    kept = by_pair[(first.query_id, first.product_id)]
    fixed = by_pair[(second.query_id, second.product_id)]
    assert kept.changes_label is False and kept.review_reasons == first.review_reasons
    assert fixed.changes_label is True and fixed.review_reasons == second.review_reasons
    assert all(j.round is Round.ADJUDICATION and not j.needs_adjudication for j in judgements)
    (change,) = decode_jsonl(store.changes_path.read_bytes(), LabelChange)
    assert change == changes[0]
    primary = context.primaries[(second.query_id, second.product_id)]
    assert (change.previous_label, change.new_label) == (primary.label, fixed.label)
    assert change.reason == fixed.adjudication_reason and change.changed_at == fixed.annotated_at
    validate_adjudication_round(
        list(context.primaries.values()),
        (),
        context.queue,
        judgements,
        changes,
        context.golden_version,
        primary_sealed_at=PRIMARY_SEALED_AT,
    )
    assert _tree(sealed[1]) == before  # no sealed record is written or changed
    text = "\n".join(said)
    assert "existing ann-1 primary judgement:" in text
    assert "queued for: " + ", ".join(r.value for r in first.review_reasons) in text
    assert "CORRECTED: " in text and "retained: " in text


def test_each_step_enforces_the_contract(context):
    item = context.items[0]
    primary = context.primaries[(item.query_id, item.product_id)]
    other = "n" if primary.label is Label.BORDERLINE else "b"
    said: list[str] = []
    answers = [
        "x",  # invalid label, asked again
        other, "7", "1", "l",
        "",  # low confidence: rationale required
        "Low on purpose.",
        "",  # label changed: adjudication reason required
        "mail a@b.co",  # free-text rules apply
        "Synthetic correction.",
        "n",
        "yes",  # not an explicit y: discarded, asked again from the label
        other, "1", "l", "Low on purpose.", "Synthetic correction.", "n", "y",
    ]  # fmt: skip
    judgement = adj.ask_adjudication(context, item, _scripted(answers), said.append, _clock())
    text = "\n".join(said)
    assert "please type one of: r / b / n" in text
    assert text.count("this is required here") == 2
    assert "not accepted" in text and "discarded" in text
    assert judgement.changes_label and judgement.adjudication_reason == "Synthetic correction."
    assert judgement.rationale == "Low on purpose." and judgement.needs_adjudication is False


def test_relevant_is_refused_for_a_hard_violation(context):
    item = context.items[0]
    pair = (item.query_id, item.product_id)
    violated = dataclasses.replace(
        context.sealed,
        eligibility={
            **context.sealed.eligibility,
            pair: context.sealed.eligibility[pair].model_copy(
                update={"hard_overall": OverallStatus.VIOLATED}
            ),
        },
    )
    ctx = dataclasses.replace(context, sealed=violated)
    said: list[str] = []
    answers = ["r"] + _correct(context, item)
    adj.ask_adjudication(ctx, item, _scripted(answers), said.append, _clock())
    assert any("violates a hard gold constraint" in line for line in said)


@pytest.mark.parametrize("stop", ["q", None])
def test_stopping_saves_nothing_partial_and_a_later_run_resumes(context, store, stop):
    first, second = context.mandatory[0], context.mandatory[1]
    answers = _retain(context, first) + ["n", "2"] + ([stop] if stop else [])
    saved = adj.annotate(
        context, store, annotator="ann-1", ask=_scripted(answers), say=lambda _: None,
        now=_clock(),
    )  # fmt: skip
    assert saved == 1 and not store.lock_path.exists()
    said: list[str] = []
    adj.annotate(
        context, store, annotator="ann-1", ask=_scripted(_retain(context, second)),
        say=said.append, now=_clock(ADJ_T0 + timedelta(hours=1)),
    )  # fmt: skip
    assert said[0].startswith(f"1 of {len(context.mandatory)} mandatory targeted pairs adjudicated")
    judgements, _ = store.load(now=ADJ_T0 + timedelta(hours=2))
    assert {(j.query_id, j.product_id) for j in judgements} == {
        (first.query_id, first.product_id),
        (second.query_id, second.product_id),
    }


def test_the_lock_is_exclusive_and_only_ann_1_adjudicates(context, store):
    with pytest.raises(ContractError, match="only 'ann-1'"):
        adj.annotate(context, store, annotator="ann-2", ask=_scripted([]), now=_clock())
    store.directory.mkdir(parents=True)
    store.lock_path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ContractError, match="never removed automatically"):
        adj.annotate(context, store, annotator="ann-1", ask=_scripted([]), now=_clock())
    assert store.lock_path.exists()


def _cli(monkeypatch, sealed, catalog):
    """`main` with the loader bound to the temporary golden root (the real default root is
    never read here)."""
    root, _ = sealed
    loader = adj.load_targeted
    monkeypatch.setattr(
        adj,
        "load_targeted",
        lambda golden_dir: loader(golden_dir, allowed_root=root, catalog=catalog),
    )


def test_an_interrupted_label_change_write_is_detected_and_rewritten(
    context, store, capsys, monkeypatch, sealed, catalog
):
    _cli(monkeypatch, sealed, catalog)
    adj.annotate(
        context, store, annotator="ann-1", ask=_scripted(_correct(context, context.mandatory[0])),
        say=lambda _: None, now=_clock(),
    )  # fmt: skip
    expected = store.changes_path.read_bytes()
    store.changes_path.unlink()  # as if the process died between the two atomic writes
    work_root = store.directory.parent
    root = context.sealed.golden_dir
    assert adj.main(["--golden-dir", str(root), "--work-root", str(work_root), "validate"]) == 1
    assert "does not match" in capsys.readouterr().err
    said: list[str] = []
    adj.annotate(
        context, store, annotator="ann-1", ask=_scripted([]), say=said.append,
        now=_clock(ADJ_T0 + timedelta(hours=1)),
    )  # fmt: skip
    assert any("rewritten from the confirmed adjudications" in line for line in said)
    assert store.changes_path.read_bytes() == expected


def test_stored_records_that_break_the_contract_are_refused(context, store):
    item = context.mandatory[0]
    adj.annotate(
        context, store, annotator="ann-1", ask=_scripted(_retain(context, item)),
        say=lambda _: None, now=_clock(),
    )  # fmt: skip
    (record,) = decode_jsonl(store.path.read_bytes(), Judgement)
    later = ADJ_T0 + timedelta(hours=1)
    for update, match in (
        ({"review_reasons": ()}, None),
        ({"annotator_handle": "ann-2"}, "foreign handle"),
        ({"annotated_at": SECOND_SEALED_AT}, "outside the allowed window"),
    ):
        broken = record.model_dump() | update
        with pytest.raises((ContractError, ValueError), match=match):
            store.save([Judgement.model_validate(broken)], now=later)


def test_status_and_validate_report_counts_only(
    context, store, capsys, monkeypatch, sealed, catalog
):
    _cli(monkeypatch, sealed, catalog)
    root = context.sealed.golden_dir
    args = ["--golden-dir", str(root), "--work-root", str(store.directory.parent)]
    assert adj.main([*args, "status"]) == 0
    out = capsys.readouterr().out
    joint = len(context.queue) - len(context.items)
    assert (
        f"{len(context.queue)} queued: {joint} resolved jointly instead, "
        f"{len(context.mandatory)} mandatory, {len(context.optional)} optional"
    ) in out
    assert f"0 of {len(context.mandatory)} mandatory targeted pairs adjudicated" in out
    assert f"0 of {len(context.optional)} optional review candidates adjudicated" in out
    assert not store.directory.exists()  # status writes nothing
    assert adj.main([*args, "validate"]) == 0


def test_optional_candidates_are_offered_only_on_request(context, store):
    """Default sessions ask only mandatory pairs; `include_optional` continues with the
    optional review candidates. A candidate left alone is simply not adjudicated."""
    one = dataclasses.replace(context, mandatory=context.mandatory[:1])  # a short mandatory set
    said: list[str] = []
    saved = adj.annotate(
        one, store, annotator="ann-1", ask=_scripted(_retain(one, one.mandatory[0])),
        say=said.append, now=_clock(),
    )  # fmt: skip
    assert saved == 1
    assert "all 1 mandatory targeted pairs adjudicated" in said
    assert any("--include-optional" in line for line in said)
    assert not any("optional candidate pair" in line for line in said)  # never shown
    said = []
    candidate = one.optional[0]
    saved = adj.annotate(
        one, store, annotator="ann-1", ask=_scripted([*_retain(one, candidate), "q"]),
        say=said.append, now=_clock(ADJ_T0 + timedelta(hours=1)), include_optional=True,
    )  # fmt: skip
    assert saved == 1
    assert any(line.startswith("--- optional candidate pair 1 of") for line in said)
    judgements, _ = store.load(now=ADJ_T0 + timedelta(hours=2))
    assert {(j.query_id, j.product_id) for j in judgements} == {
        (one.mandatory[0].query_id, one.mandatory[0].product_id),
        (candidate.query_id, candidate.product_id),
    }
    remaining = len(one.optional) - 1
    assert adj._progress(one, {(j.query_id, j.product_id) for j in judgements}).endswith(
        f"({remaining} keep their sealed primary label unless adjudicated)"
    )

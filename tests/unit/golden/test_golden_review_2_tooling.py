"""review-2 tooling on a temporary golden directory sealed from SYNTHETIC primary judgements:
the reviewer bundle (blindness by omission), the stdlib-only reviewer tool, and the
second-review seal. Every judgement here is a SYNTHETIC TEST FIXTURE, typed by a scripted
fake input, never a human label. The real golden directory and the real primary working file
are never read or written."""

import ast
import json
import re
import shutil
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from evals.annotation import cli, reviewer_tool
from evals.annotation.reviewer_bundle import BundleError, load_sealed
from evals.annotation.session import WorkStore, load_pool
from evals.contracts.canonical import ContractError, decode_json_object, decode_jsonl, sha256_hex
from evals.contracts.constants import ALLOWED_RELATIONS
from evals.contracts.manifest import Role, Stage, stage_roles
from evals.contracts.models import _MACHINE_PATH_RE, AgreementReport, Judgement, Round
from evals.runners import build_reviewer_bundle, seal_primary, seal_second_review
from pool_support import LABEL_T0, frozen_pool, synthetic_primaries

from ecommerce_search.catalog.evidence import EMAIL_RE

PRIMARY_SEALED_AT = LABEL_T0 + timedelta(days=2)
REVIEW_T0 = PRIMARY_SEALED_AT + timedelta(hours=1)
SECOND_SEALED_AT = PRIMARY_SEALED_AT + timedelta(hours=3)
RUBRIC = b"SYNTHETIC TEST RUBRIC (not rubric-1 text)\n"


@pytest.fixture(scope="module")
def sealed_template(tmp_path_factory, catalog):
    """A golden directory frozen through `primary_sealed` from SYNTHETIC primaries."""
    tmp = tmp_path_factory.mktemp("review2")
    root, target, _, work = frozen_pool(tmp, catalog)
    context = load_pool(target, allowed_root=root, catalog=catalog)
    store = WorkStore(context, work)
    store.initialize(now=LABEL_T0)
    store.save(synthetic_primaries(context.items), now=PRIMARY_SEALED_AT)
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
    return tmp


@pytest.fixture
def sealed(sealed_template, tmp_path):
    shutil.copytree(sealed_template / "goldens", tmp_path / "goldens")
    root = tmp_path / "goldens"
    return root, root / "internal-search" / "v1"


def _bundle(sealed, tmp_path, catalog, name="bundle"):
    root, target = sealed
    rubric = tmp_path / "rubric.md"
    rubric.write_bytes(RUBRIC)
    out = tmp_path / name
    build_reviewer_bundle.write_bundle(
        target, rubric, out, allowed_root=root, catalog=catalog, repository_root=tmp_path / "repo"
    )
    return out


def _scripted(answers):
    queue = list(answers)

    def ask(prompt):
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def _review_all(workspace_dir, *, start=REVIEW_T0):
    """Answer every pair with a SYNTHETIC decision (never `relevant`)."""
    workspace = reviewer_tool.Workspace(workspace_dir)
    answers = [
        "n2hnn!" if i % 2 == 0 else "b2mnn! | Synthetic second-review fixture."
        for i in range(len(workspace.items))
    ]
    ticks = iter(start + timedelta(seconds=i) for i in range(10_000))
    said: list[str] = []
    saved = reviewer_tool.annotate(
        workspace,
        annotator="ann-2",
        ask=_scripted(answers),
        say=said.append,
        now=lambda: next(ticks),
        compact=True,
    )
    return workspace, saved, said


# ---- the bundle ---------------------------------------------------------------------------------


def test_the_bundle_contains_only_what_the_blind_review_needs(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    assert sorted(p.name for p in out.iterdir()) == [
        "README.txt",
        "bundle.json",
        "review.py",
        "rubric.md",
    ]
    body = json.loads((out / "bundle.json").read_bytes())
    sample = decode_json_object(
        (sealed[1] / "consistency_sample.json").read_bytes(),
        __import__("evals.contracts.models", fromlist=["ConsistencySample"]).ConsistencySample,
    )
    assert body["item_count"] == sample.actual_count == len(body["items"])
    assert [(i["query_id"], i["product_id"], i["position"]) for i in body["items"]] == [
        (s.query_id, s.product_id, s.blind_position) for s in sample.items
    ]
    assert set(body) == {
        "annotator", "bundle_version", "card_version", "golden_id", "golden_version",
        "item_count", "items", "primary_sealed_manifest_sha256", "review_protocol", "round",
        "rubric_file", "rubric_sha256", "rubric_version", "tool_sha256",
    }  # fmt: skip
    for item in body["items"]:
        assert set(item) == {
            "card_sha256", "card_text", "constraints", "hard_unknown", "hard_violated",
            "intents", "interpretations", "position", "product_id", "query_id", "query_text",
        }  # fmt: skip
    text = (out / "bundle.json").read_text(encoding="utf-8")
    for leak in ("allocation", '"label"', "relation", "confidence", "rationale", "score", "rank",
                 "source", "provenance", "agreement", "review_reasons", "Synthetic fixture"):  # fmt: skip
        assert leak not in text
    assert (out / "review.py").read_bytes() == Path(reviewer_tool.__file__).read_bytes()
    assert body["rubric_sha256"] == sha256_hex(RUBRIC)


def test_the_bundle_never_reads_the_primary_labels(sealed, tmp_path, catalog):
    root, target = sealed
    (target / "primary_judgements.jsonl").unlink()
    (target / "adjudication_queue.jsonl").unlink()
    _bundle(sealed, tmp_path, catalog)  # still builds: those files are never opened
    with pytest.raises(ContractError, match="missing frozen file"):
        load_sealed(target, allowed_root=root, catalog=catalog, primary_labels=True)


def test_the_bundle_is_deterministic(sealed, tmp_path, catalog):
    first = _bundle(sealed, tmp_path, catalog, "one")
    second = _bundle(sealed, tmp_path, catalog, "two")
    for name in ("bundle.json", "review.py", "README.txt", "rubric.md"):
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_the_bundle_is_refused_inside_the_repository_or_over_an_existing_path(
    sealed, tmp_path, catalog
):
    root, target = sealed
    rubric = tmp_path / "rubric.md"
    rubric.write_bytes(RUBRIC)
    with pytest.raises(BundleError, match="outside the repository"):
        build_reviewer_bundle.write_bundle(
            target, rubric, tmp_path / "repo" / "b", allowed_root=root, catalog=catalog,
            repository_root=tmp_path / "repo",
        )  # fmt: skip
    (tmp_path / "exists").mkdir()
    with pytest.raises(BundleError, match="existing path"):
        build_reviewer_bundle.write_bundle(
            target, rubric, tmp_path / "exists", allowed_root=root, catalog=catalog,
            repository_root=tmp_path / "repo",
        )  # fmt: skip


def test_the_bundle_needs_a_sealed_primary_round(tmp_path, catalog):
    root, target, _, _ = frozen_pool(tmp_path, catalog)
    rubric = tmp_path / "rubric.md"
    rubric.write_bytes(RUBRIC)
    with pytest.raises(ContractError, match="missing frozen file"):
        build_reviewer_bundle.write_bundle(
            target, rubric, tmp_path / "out", allowed_root=root, catalog=catalog,
            repository_root=tmp_path / "repo",
        )  # fmt: skip


# ---- the reviewer tool --------------------------------------------------------------------------


def test_the_reviewer_tool_uses_only_the_standard_library():
    tree = ast.parse(Path(reviewer_tool.__file__).read_text(encoding="utf-8"))
    modules = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert modules <= set(sys.stdlib_module_names)


def test_the_reviewer_tool_codes_and_text_rules_match_the_repository():
    for label, relations in reviewer_tool.RELATIONS.items():
        numbered = cli._relation_keys(cli.LABEL_KEYS[label[0]])
        assert [r.value for r in numbered.values()] == list(relations)
        assert {r.value for r in ALLOWED_RELATIONS[cli.LABEL_KEYS[label[0]]]} == set(relations)
    assert reviewer_tool.EMAIL_RE.pattern == EMAIL_RE.pattern
    assert reviewer_tool.MACHINE_PATH_RE.pattern == _MACHINE_PATH_RE.pattern
    assert reviewer_tool.COMPACT_TOKEN.pattern == cli.COMPACT_TOKEN.pattern
    assert reviewer_tool.codes() == cli.compact_codes().replace("compact entry", "entry")


def test_reviewing_every_pair_writes_canonical_second_review_judgements(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    workspace, saved, said = _review_all(out)
    assert saved == len(workspace.items)
    data = (out / reviewer_tool.JUDGEMENTS_FILE).read_bytes()
    judgements = decode_jsonl(data, Judgement)  # the repository's strict canonical decoder
    assert len(judgements) == saved
    assert {j.round for j in judgements} == {Round.SECOND_REVIEW}
    assert {j.annotator_handle for j in judgements} == {"ann-2"}
    assert not (out / reviewer_tool.LOCK_FILE).exists()
    assert reviewer_tool.finish(workspace) == sha256_hex(data)
    shown = "\n".join(said)
    assert "Synthetic fixture rationale" not in shown  # no primary rationale is ever shown
    assert not re.search(r"\b(score|rank|source)\b", shown)


def test_the_reviewer_tool_refuses_wrong_entries_and_keeps_nothing_partial(
    sealed, tmp_path, catalog
):
    out = _bundle(sealed, tmp_path, catalog)
    workspace = reviewer_tool.Workspace(out)
    with pytest.raises(reviewer_tool.ReviewError, match="only 'ann-2'"):
        reviewer_tool.annotate(workspace, annotator="ann-1", ask=_scripted([]), say=lambda _: None)
    said: list[str] = []
    answers = ["n2hnn", "b2mnn!", "n9hnn!", "?", "n2lnn!", "q"]
    saved = reviewer_tool.annotate(
        workspace,
        annotator="ann-2",
        ask=_scripted(answers),
        say=said.append,
        now=lambda: REVIEW_T0,
        compact=True,
    )
    assert saved == 0 and not (out / reviewer_tool.JUDGEMENTS_FILE).exists()
    text = "\n".join(said)
    assert "not confirmed" in text and "rationale is required" in text and "not allowed" in text
    item = dict(workspace.items[0], hard_violated=True, hard_unknown=False)
    said.clear()
    entry = reviewer_tool.ask_entry(
        item, _scripted(["r1hnn!", "b2hnn! | Substitute only."]), said.append
    )
    assert "violates a hard gold constraint" in said[0] and entry["label"] == "borderline"
    unknown = dict(item, hard_violated=False, hard_unknown=True)
    entry = reviewer_tool.ask_entry(
        unknown, _scripted(["r1hnn!", "r1hnn! | Stated elsewhere."]), said.append
    )
    assert entry["rationale"] == "Stated elsewhere."
    with pytest.raises(reviewer_tool.EntryError, match="email"):
        reviewer_tool.parse_entry("n2hnn! | ask a@b.co")


def test_the_reviewer_tool_resumes_and_respects_its_lock(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    workspace = reviewer_tool.Workspace(out)
    saved = reviewer_tool.annotate(
        workspace, annotator="ann-2", ask=_scripted(["n2hnn!", "n2hnn!"]), say=lambda _: None,
        now=lambda: REVIEW_T0, compact=True,
    )  # fmt: skip
    assert saved == 2  # then EOF ends the session cleanly
    with pytest.raises(reviewer_tool.ReviewError, match="not judged yet"):
        reviewer_tool.finish(workspace)
    (out / reviewer_tool.LOCK_FILE).write_text("pid=1\n", encoding="utf-8")
    with pytest.raises(reviewer_tool.ReviewError, match="review.lock exists"):
        reviewer_tool.annotate(workspace, annotator="ann-2", ask=_scripted([]), say=lambda _: None)
    assert (out / reviewer_tool.LOCK_FILE).exists()  # never removed automatically
    (out / reviewer_tool.LOCK_FILE).unlink()
    _, more, _ = _review_all(out)
    assert more == len(workspace.items) - 2


def test_a_tampered_rubric_is_refused(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    (out / "rubric.md").write_bytes(b"changed\n")
    with pytest.raises(reviewer_tool.ReviewError, match="rubric"):
        reviewer_tool.Workspace(out)


# ---- the second-review seal ---------------------------------------------------------------------


def _check(sealed, out, catalog, now=SECOND_SEALED_AT):
    root, target = sealed
    return seal_second_review.check(
        target, out, allowed_root=root, catalog=catalog, now=lambda: now
    )


def _seal(sealed, out, catalog, **overrides):
    root, target = sealed
    data = (out / reviewer_tool.JUDGEMENTS_FILE).read_bytes()
    kwargs = dict(
        annotator="ann-2",
        confirm_human_review=True,
        judgements_sha256=sha256_hex(data),
        allowed_root=root,
        catalog=catalog,
        now=lambda: SECOND_SEALED_AT,
    )
    kwargs.update(overrides)
    return seal_second_review.seal(target, out, **kwargs)


def test_check_reports_only_counts_and_the_hash(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    workspace, saved, _ = _review_all(out)
    count, digest = _check(sealed, out, catalog)
    assert count == saved and digest == reviewer_tool.finish(workspace)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"confirm_human_review": False}, "confirm-human-review"),
        ({"annotator": "ann-1"}, "only 'ann-2'"),
        ({"judgements_sha256": "0" * 64}, "reviewed SHA-256"),
    ],
)
def test_the_seal_needs_the_second_annotators_own_confirmation(
    sealed, tmp_path, catalog, overrides, message
):
    out = _bundle(sealed, tmp_path, catalog)
    _review_all(out)
    with pytest.raises(ContractError, match=message):
        _seal(sealed, out, catalog, **overrides)
    assert not (sealed[1] / seal_second_review.SECOND_REVIEW_MANIFEST_PATH).exists()


@pytest.mark.parametrize(
    "tamper", ["bundle", "tool", "binding", "lock", "incomplete", "early_binding"]
)
def test_a_tampered_or_unfinished_workspace_is_refused(sealed, tmp_path, catalog, tamper):
    out = _bundle(sealed, tmp_path, catalog)
    if tamper == "early_binding":
        _review_all(out, start=PRIMARY_SEALED_AT - timedelta(hours=1))
    else:
        _review_all(out)
    if tamper == "bundle":
        body = json.loads((out / "bundle.json").read_bytes())
        body["items"] = body["items"][1:]
        (out / "bundle.json").write_text(json.dumps(body), encoding="utf-8")
    elif tamper == "tool":
        with open(out / "review.py", "ab") as handle:
            handle.write(b"# changed\n")
    elif tamper == "binding":
        binding = json.loads((out / "binding.json").read_bytes())
        (out / "binding.json").write_text(
            json.dumps({**binding, "annotator_handle": "ann-1"}), encoding="utf-8"
        )
    elif tamper == "lock":
        (out / reviewer_tool.LOCK_FILE).write_text("pid=1\n", encoding="utf-8")
    elif tamper == "incomplete":
        lines = (out / reviewer_tool.JUDGEMENTS_FILE).read_bytes().splitlines(keepends=True)
        (out / reviewer_tool.JUDGEMENTS_FILE).write_bytes(b"".join(lines[1:]))
    with pytest.raises(ContractError):
        _check(sealed, out, catalog)


def test_sealing_writes_the_review_2_stage_and_the_agreement_report(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    _review_all(out)
    manifest = _seal(sealed, out, catalog)
    target = sealed[1]
    assert manifest.stage is Stage.SECOND_REVIEW_SEALED
    assert manifest.review_protocol == "review-2"
    assert {e.role for e in manifest.entries} == stage_roles(Stage.SECOND_REVIEW_SEALED, "review-2")
    assert manifest.primary_sealed_at == PRIMARY_SEALED_AT
    previous = (target / "manifests/3-primary_sealed.json").read_bytes()
    assert b"review_protocol" not in previous  # the primary seal is unchanged
    assert (target / "second_review_judgements.jsonl").read_bytes() == (
        out / reviewer_tool.JUDGEMENTS_FILE
    ).read_bytes()
    report = decode_json_object((target / "agreement_report.json").read_bytes(), AgreementReport)
    assert report.second_review_sealed_at == SECOND_SEALED_AT
    assert report.item_count == len(reviewer_tool.Workspace(out).items)
    assert report.statistic == "inter_annotator_agreement"
    assert report.intra_annotator_consistency == "not_measured"
    assert manifest.entry(Role.AGREEMENT_REPORT).kind.value == "generated"
    with pytest.raises(ContractError, match="already sealed"):
        _seal(sealed, out, catalog)


def test_the_sealed_chain_on_disk_binds_review_2_explicitly(sealed, tmp_path, catalog):
    from evals.contracts.manifest import decode_manifest, encode_manifest, validate_chain

    target = sealed[1]
    names = [
        "1-queries_frozen.json",
        "2-pool_frozen.json",
        "3-primary_sealed.json",
        "4-second_review_sealed.json",
    ]
    before = {n: (target / "manifests" / n).read_bytes() for n in names[:3]}
    out = _bundle(sealed, tmp_path, catalog)
    _review_all(out)
    _seal(sealed, out, catalog)
    raw = [(target / "manifests" / n).read_bytes() for n in names]
    chain = [decode_manifest(data) for data in raw]
    validate_chain(chain)
    assert [m.stage for m in chain] == [
        Stage.QUERIES_FROZEN,
        Stage.POOL_FROZEN,
        Stage.PRIMARY_SEALED,
        Stage.SECOND_REVIEW_SEALED,
    ]
    assert [m.review_protocol for m in chain] == [None, None, None, "review-2"]
    assert {n: data for n, data in zip(names, raw, strict=True) if n in before} == before
    assert b'"review_protocol":"review-2"' in raw[3]
    assert all(b"review_protocol" not in data for data in raw[:3])
    assert all(encode_manifest(m) == data for m, data in zip(chain, raw, strict=True))
    # The protocol is not a default: the same stage without it is refused by the contract.
    from evals.contracts.manifest import Manifest
    from pydantic import ValidationError

    body = json.loads(raw[3])
    del body["review_protocol"]
    with pytest.raises(ValidationError, match="requires review_protocol review-2"):
        Manifest.model_validate(body)


def test_ann_2_flags_are_preserved_and_routed_at_the_seal(sealed, tmp_path, catalog):
    from evals.contracts.models import JointReason, JointResolutionItem

    out = _bundle(sealed, tmp_path, catalog)
    workspace = reviewer_tool.Workspace(out)
    answers = ["n2hyy!"] + ["n2hnn!"] * (len(workspace.items) - 1)  # first pair: both flags
    ticks = iter(REVIEW_T0 + timedelta(seconds=i) for i in range(10_000))
    reviewer_tool.annotate(
        workspace,
        annotator="ann-2",
        ask=_scripted(answers),
        say=lambda _: None,
        now=lambda: next(ticks),
        compact=True,
    )
    first = workspace.items[0]
    judged = {
        (j.query_id, j.product_id): j
        for j in decode_jsonl((out / reviewer_tool.JUDGEMENTS_FILE).read_bytes(), Judgement)
    }
    flagged = judged[(first["query_id"], first["product_id"])]
    assert flagged.needs_adjudication and flagged.catalog_issue  # both flags preserved
    manifest = _seal(sealed, out, catalog)
    target = sealed[1]
    queue = decode_jsonl(
        (target / "joint_resolution_queue.jsonl").read_bytes(), JointResolutionItem
    )
    routed = {(i.query_id, i.product_id): i.reasons for i in queue}
    reasons = routed[(first["query_id"], first["product_id"])]
    assert {
        JointReason.SECOND_REVIEW_NEEDS_ADJUDICATION,
        JointReason.SECOND_REVIEW_CATALOG_ISSUE,
    } <= set(reasons)
    assert manifest.entry(Role.JOINT_RESOLUTION_QUEUE).kind.value == "generated"
    report = decode_json_object((target / "agreement_report.json").read_bytes(), AgreementReport)
    disagreeing = [p for p, r in routed.items() if JointReason.SECOND_REVIEW_DISAGREEMENT in r]
    assert len(disagreeing) == report.disagreement_count


def test_the_bundle_readme_requires_independent_review(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    readme = (out / "README.txt").read_text(encoding="utf-8")
    assert "do not view the project's AI-assistant conversations" in readme
    assert "the primary\nlabels or any machine proposals" in readme
    for leak in ('"label"', "relevant", "borderline", "Synthetic fixture", '"allocation"'):
        assert leak not in readme


# ---- step-by-step input (the default) ------------------------------------------------------------

STEP_N = ["n", "2", "h", "", "n", "n", "y"]  # not_relevant / off_target / high / no rationale
STEP_B = ["b", "2", "m", "Synthetic second-review fixture.", "n", "n", "y"]


def test_step_by_step_writes_the_same_records_as_compact_mode(sealed, tmp_path, catalog):
    steps_dir = _bundle(sealed, tmp_path, catalog, "steps")
    compact_dir = _bundle(sealed, tmp_path, catalog, "compact")
    count = len(reviewer_tool.Workspace(steps_dir).items)
    step_answers = [a for i in range(count) for a in (STEP_N if i % 2 == 0 else STEP_B)]
    ticks = iter(REVIEW_T0 + timedelta(seconds=i) for i in range(10_000))
    said: list[str] = []
    saved = reviewer_tool.annotate(
        reviewer_tool.Workspace(steps_dir),
        annotator="ann-2",
        ask=_scripted(step_answers),
        say=said.append,
        now=lambda: next(ticks),
    )  # default mode: step by step
    assert saved == count
    _review_all(compact_dir)  # the same decisions in compact mode, same timestamps
    steps_bytes = (steps_dir / reviewer_tool.JUDGEMENTS_FILE).read_bytes()
    assert steps_bytes == (compact_dir / reviewer_tool.JUDGEMENTS_FILE).read_bytes()
    decode_jsonl(steps_bytes, Judgement)  # the repository's strict canonical contract
    assert not any("compact entry" in line for line in said)  # no codes needed by default


def _steps(item, answers):
    said: list[str] = []
    entry = reviewer_tool.ask_entry_steps(item, _scripted(answers), said.append)
    return entry, " | ".join(said)


def test_step_by_step_enforces_the_existing_rules(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    item = dict(reviewer_tool.Workspace(out).items[0], hard_violated=False, hard_unknown=False)
    # invalid answers are asked again; nothing is defaulted for label, relation or confidence
    entry, _ = _steps(
        item, ["x", "", "n", "9", "", "3", "?", "l", "", "Low on purpose.", "y", "y", "y"]
    )
    assert entry == {
        "label": "not_relevant",
        "relation": "out_of_catalog",
        "confidence": "low",
        "rationale": "Low on purpose.",
        "needs_adjudication": True,
        "catalog_issue": True,
    }
    # relevant is refused for a hard violation, and the label is asked again
    violated = dict(item, hard_violated=True)
    entry, text = _steps(violated, ["r", "b", "2", "h", "Substitute only.", "n", "n", "y"])
    assert "violates a hard gold constraint" in text and entry["label"] == "borderline"
    # relevant with an unknown hard attribute needs a rationale
    unknown = dict(item, hard_unknown=True)
    entry, text = _steps(unknown, ["r", "1", "h", "", "Stated elsewhere.", "n", "n", "y"])
    assert "rationale is required" in text and entry["rationale"] == "Stated elsewhere."
    # free-text rules apply; an optional rationale may be skipped
    entry, text = _steps(item, ["r", "1", "h", "mail a@b.co", "", "n", "n", "y"])
    assert "email" in text and entry["rationale"] is None
    # anything but y at the summary discards the pair's answers and asks again
    entry, text = _steps(item, ["n", "1", "h", "", "n", "n", "", "n", "2", "m", "", "n", "n", "y"])
    assert "discarded" in text and entry["relation"] == "off_target"
    assert entry["confidence"] == "medium"


@pytest.mark.parametrize("stop", ["q", None])
def test_stopping_mid_pair_saves_nothing_partial(sealed, tmp_path, catalog, stop):
    out = _bundle(sealed, tmp_path, catalog)
    workspace = reviewer_tool.Workspace(out)
    answers = STEP_N + ["b", "2", "m"] + ([stop] if stop else [])  # second pair left partial
    saved = reviewer_tool.annotate(
        workspace, annotator="ann-2", ask=_scripted(answers), say=lambda _: None,
        now=lambda: REVIEW_T0,
    )  # fmt: skip
    assert saved == 1
    assert len(workspace.load()) == 1  # only the confirmed pair
    assert not (out / reviewer_tool.LOCK_FILE).exists()


def test_the_readme_describes_step_by_step_input(sealed, tmp_path, catalog):
    readme = (_bundle(sealed, tmp_path, catalog) / "README.txt").read_text(encoding="utf-8")
    assert "step by step" in readme and "--compact" in readme


def test_step_by_step_shows_every_answer_and_saves_only_on_an_explicit_y(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    item = dict(reviewer_tool.Workspace(out).items[0], hard_violated=False, hard_unknown=False)
    # step 7: an answer other than y (here "yes" and "n") discards and restarts from the label
    answers = STEP_N[:-1] + ["yes"] + STEP_N[:-1] + ["n"] + STEP_B
    entry, text = _steps(item, answers)
    assert text.count("discarded; nothing was saved") == 2
    assert entry["label"] == "borderline" and entry["rationale"] == STEP_B[3]
    summary = reviewer_tool.summary(entry)
    for line in (
        "1 label:              borderline",
        "2 relation:           substitute",
        "3 confidence:         medium",
        f"4 rationale:          {STEP_B[3]}",
        "5 needs adjudication: no",
        "6 catalog issue:      no",
    ):
        assert line in summary
    assert "4 rationale:          (none)" in reviewer_tool.summary(dict(entry, rationale=None))


def test_step_by_step_explains_an_invalid_answer(sealed, tmp_path, catalog):
    out = _bundle(sealed, tmp_path, catalog)
    item = dict(reviewer_tool.Workspace(out).items[0], hard_violated=False, hard_unknown=False)
    entry, text = _steps(item, ["x", "n", "7", "2", "z", "h", "", "maybe", "n", "n", "y"])
    assert "please type one of: r / b / n" in text
    assert "please type one of: 1 / 2 / 3" in text
    assert "please type one of: h / m / l" in text
    assert "please type y or n" in text
    assert entry["relation"] == "off_target" and entry["needs_adjudication"] is False


def test_a_new_tool_keeps_the_sampled_pairs_and_invalidates_the_old_binding(
    sealed, tmp_path, catalog, monkeypatch
):
    """Rebuilding after a tool change: the same items in the same blind order, a new bundle
    SHA-256, and an old workspace binding is refused by the new bundle."""
    old_dir = _bundle(sealed, tmp_path, catalog, "old")
    old = reviewer_tool.Workspace(old_dir)
    old.binding(now=REVIEW_T0, create=True)
    changed_tool = tmp_path / "reviewer_tool.py"
    changed_tool.write_bytes(reviewer_tool_source() + b"# changed\n")
    monkeypatch.setattr("evals.annotation.reviewer_bundle.TOOL_SOURCE", changed_tool)
    new_dir = _bundle(sealed, tmp_path, catalog, "new")
    new = reviewer_tool.Workspace(new_dir)
    assert new.items == old.items  # pairs, order, cards and flags unchanged
    assert new.bundle_sha256 != old.bundle_sha256
    assert (new_dir / "rubric.md").read_bytes() == (old_dir / "rubric.md").read_bytes()
    assert not (new_dir / reviewer_tool.BINDING_FILE).exists()
    shutil.copy(old_dir / reviewer_tool.BINDING_FILE, new_dir / reviewer_tool.BINDING_FILE)
    with pytest.raises(reviewer_tool.ReviewError, match="another bundle"):
        new.binding(now=REVIEW_T0, create=True)


def reviewer_tool_source() -> bytes:
    return Path(reviewer_tool.__file__).read_bytes()

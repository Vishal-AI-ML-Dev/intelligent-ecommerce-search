"""Blinded human review of listing decisions (M14, schema `lh-1`).

Rubric: `docs/m14-human-review-rubric.md` (`lh-rubric-1`).

This module prepares and checks human review; it never produces a review. Steps:

1. `freeze`: a seeded selection of clean dev items, stratified by source, computed from the set
   alone (no outcome, prediction or label is read), written once with exclusive creation as
   `selection.json` with its own SHA-256. Packet ids are shuffled opaque ids (`h-001`...).
2. `packet`: writes `packet.jsonl`, exactly what Jev was shown for each selected item (the
   seller's raw title and description, and the `jl-1` claims asked), under its packet id. It
   carries no source, source row, source label, mapped label, model output or route.
3. `import`: validates one reviewer's answers file against the packet (every packet id exactly
   once, one `ann-N` handle, allowed labels only, exactly the asked claims) and seals it as
   `labels-<handle>.jsonl` plus `seal-<handle>.json` (exclusive creation; never overwritten).
   The operator must pass `--reviewer-is-human`; the seal records that attestation. The tool
   cannot verify it, and a test fixture is never a review.
4. `score`: compares sealed human labels with recorded outcomes and with the external source
   labels. `unsure` answers are excluded from every agreement count and reported.

All files live in a git-ignored review directory (default
`data/processed/m14_listing_set/<set>/review/<review-id>/`); only code and the rubric are
committed.
"""

import argparse
import json
import random
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ecommerce_search.decision.listing_models import (
    CATEGORY_LABELS,
    SUBCATEGORY_LABELS,
    SupportLabel,
)
from ecommerce_search.decision.listing_questions import build_listing_state
from ecommerce_search.normalization import rules as nr
from ecommerce_search.normalization.normalizer import normalize_listing
from evals.contracts.canonical import sha256_hex
from evals.contracts.constants import ANNOTATOR_HANDLE_PATTERN, FREE_TEXT_MAX
from evals.listing_decisions.contracts import EvalSet, EvalSetError, Split, Task, load_set
from evals.listing_decisions.evaluate import load_outcomes, load_raw

REVIEW_SCHEMA = "lh-1"
RUBRIC_VERSION = "lh-rubric-1"
SELECTION_FILE = "selection.json"
PACKET_FILE = "packet.jsonl"
UNSURE = "unsure"
SUPPORT_LABELS = tuple(s.value for s in SupportLabel)
NEEDS_REVIEW = ("yes", "no", UNSURE)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReviewSelection(_Frozen):
    schema_version: Literal["lh-1"] = REVIEW_SCHEMA
    review_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    set_version: str
    set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    seed: int = Field(ge=0)
    per_source: int = Field(ge=1)
    rubric_version: Literal["lh-rubric-1"] = RUBRIC_VERSION
    note: str = "selected from the set alone; no outcome, prediction or label was read"
    items: tuple[tuple[str, str], ...] = Field(min_length=1)  # (packet_id, item_id), packet order

    @model_validator(mode="after")
    def _unique(self) -> Self:
        packet_ids = [p for p, _ in self.items]
        if packet_ids != [f"h-{i:03d}" for i in range(1, len(self.items) + 1)]:
            raise ValueError("packet ids are h-001, h-002, ... in order")
        if len({i for _, i in self.items}) != len(self.items):
            raise ValueError("an item is selected at most once")
        return self


class PacketClaim(_Frozen):
    ordinal: int = Field(ge=0)
    field: str
    claimed_value: str  # the seller's normalized claim, to be checked against the text


class PacketItem(_Frozen):
    packet_id: str
    title: str | None
    description: str | None
    claims: tuple[PacketClaim, ...] = ()


class ReviewAnswer(_Frozen):
    """One reviewer's answer for one packet item, as typed in the answers file."""

    packet_id: str = Field(pattern=r"^h-[0-9]{3}$")
    annotator_handle: str = Field(pattern=ANNOTATOR_HANDLE_PATTERN)
    rubric_version: Literal["lh-rubric-1"]
    category: str
    subcategory: str
    claims: dict[str, str] = {}  # claim ordinal (as text) -> support label
    needs_review: str
    note: str | None = Field(default=None, max_length=FREE_TEXT_MAX)

    @model_validator(mode="after")
    def _labels(self) -> Self:
        if self.category not in (*CATEGORY_LABELS, UNSURE):
            raise ValueError("unknown category label")
        if self.subcategory not in (*SUBCATEGORY_LABELS, UNSURE):
            raise ValueError("unknown subcategory label")
        if any(v not in (*SUPPORT_LABELS, UNSURE) for v in self.claims.values()):
            raise ValueError("unknown claim support label")
        if any(not k.isdigit() for k in self.claims):
            raise ValueError("claim keys are ordinals")
        if self.needs_review not in NEEDS_REVIEW:
            raise ValueError("needs_review is yes, no or unsure")
        return self


class ReviewSeal(_Frozen):
    schema_version: Literal["lh-1"] = REVIEW_SCHEMA
    review_id: str
    annotator_handle: str = Field(pattern=ANNOTATOR_HANDLE_PATTERN)
    selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    labels_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answers: int = Field(ge=1)
    operator_attests_human_reviewer: Literal[True]


def _canonical(value: Any) -> bytes:
    return nr.canonical_json(value) + b"\n"


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as handle:  # never overwrite a frozen or sealed file
        handle.write(data)


# ---- freeze and packet ------------------------------------------------------------------


def select(eval_set: EvalSet, review_id: str, seed: int, per_source: int) -> ReviewSelection:
    """Seeded, stratified selection of clean dev items from the set alone."""
    rng = random.Random(seed)  # noqa: S311 - reproducible sampling, not security
    chosen: list[str] = []
    for source in eval_set.sources:
        pool = sorted(
            i.item_id
            for i in eval_set.items
            if i.source_id == source.source_id and i.split is Split.DEV and i.corruption is None
        )
        chosen += rng.sample(pool, min(per_source, len(pool)))
    rng.shuffle(chosen)  # the packet order hides the source
    return ReviewSelection(
        review_id=review_id,
        set_version=eval_set.set_version,
        set_sha256=eval_set.set_sha256,
        seed=seed,
        per_source=per_source,
        items=tuple((f"h-{n:03d}", item_id) for n, item_id in enumerate(chosen, 1)),
    )


def freeze(eval_set: EvalSet, review_dir: Path, review_id: str, seed: int, per_source: int):
    selection = select(eval_set, review_id, seed, per_source)
    data = _canonical(selection.model_dump(mode="json"))
    _write_new(review_dir / SELECTION_FILE, data)
    return selection, sha256_hex(data)


def load_selection(review_dir: Path, eval_set: EvalSet) -> tuple[ReviewSelection, str]:
    try:
        data = (review_dir / SELECTION_FILE).read_bytes()
        selection = ReviewSelection.model_validate_json(data)
    except (OSError, ValidationError) as exc:
        raise EvalSetError(f"cannot read the frozen selection ({type(exc).__name__})") from None
    if (selection.set_version, selection.set_sha256) != (eval_set.set_version, eval_set.set_sha256):
        raise EvalSetError("the selection is bound to another set")
    return selection, sha256_hex(data)


def packet(eval_set: EvalSet, raw_dir: Path, selection: ReviewSelection) -> list[PacketItem]:
    items = []
    for packet_id, item_id in selection.items:
        raw = load_raw(raw_dir, eval_set.item(item_id))
        state = build_listing_state(raw, normalize_listing(raw))
        items.append(
            PacketItem(
                packet_id=packet_id,
                title=state.text.title,
                description=state.text.description,
                claims=tuple(
                    PacketClaim(
                        ordinal=q.claim.ordinal,
                        field=q.claim.field,
                        claimed_value=q.claim.canonical_value,
                    )
                    for q in state.attribute_questions
                ),
            )
        )
    return items


def write_packet(eval_set: EvalSet, raw_dir: Path, review_dir: Path) -> str:
    selection, _ = load_selection(review_dir, eval_set)
    lines = b"".join(
        _canonical(p.model_dump(mode="json")) for p in packet(eval_set, raw_dir, selection)
    )
    _write_new(review_dir / PACKET_FILE, lines)
    return sha256_hex(lines)


def load_packet(review_dir: Path) -> tuple[list[PacketItem], str]:
    try:
        data = (review_dir / PACKET_FILE).read_bytes()
        items = [PacketItem.model_validate_json(line) for line in data.splitlines()]
    except (OSError, ValidationError) as exc:
        raise EvalSetError(f"cannot read the packet ({type(exc).__name__})") from None
    return items, sha256_hex(data)


# ---- import -----------------------------------------------------------------------------


def validate_answers(data: bytes, items: Sequence[PacketItem]) -> list[ReviewAnswer]:
    """Every packet id exactly once, by one reviewer, answering exactly the asked claims."""
    try:
        lines = data.decode("utf-8").splitlines()
        answers = [ReviewAnswer.model_validate_json(line) for line in lines if line.strip()]
    except (UnicodeDecodeError, ValidationError) as exc:
        detail = f"{exc.error_count()} error(s)" if isinstance(exc, ValidationError) else "utf-8"
        raise EvalSetError(f"invalid answers file ({detail})") from None
    handles = {a.annotator_handle for a in answers}
    if len(handles) != 1:
        raise EvalSetError("an answers file holds exactly one reviewer's answers")
    expected = {p.packet_id: p for p in items}
    counts = Counter(a.packet_id for a in answers)
    if set(counts) != set(expected) or any(n != 1 for n in counts.values()):
        raise EvalSetError("every packet item must be answered exactly once")
    for a in answers:
        asked = {str(c.ordinal) for c in expected[a.packet_id].claims}
        if set(a.claims) != asked:
            raise EvalSetError(f"{a.packet_id}: answer exactly the claims asked")
    return sorted(answers, key=lambda a: a.packet_id)


def import_answers(
    eval_set: EvalSet, review_dir: Path, answers_path: Path, *, reviewer_is_human: bool
) -> ReviewSeal:
    if not reviewer_is_human:
        raise EvalSetError(
            "pass --reviewer-is-human only for answers a human reviewer typed; nothing imported"
        )
    selection, selection_sha = load_selection(review_dir, eval_set)
    items, packet_sha = load_packet(review_dir)
    if [p.packet_id for p in items] != [p for p, _ in selection.items]:
        raise EvalSetError("the packet does not match the frozen selection")
    try:
        data = answers_path.read_bytes()
    except OSError as exc:
        raise EvalSetError(f"cannot read the answers file ({type(exc).__name__})") from None
    answers = validate_answers(data, items)
    handle = answers[0].annotator_handle
    labels = b"".join(_canonical(a.model_dump(mode="json")) for a in answers)
    seal = ReviewSeal(
        review_id=selection.review_id,
        annotator_handle=handle,
        selection_sha256=selection_sha,
        packet_sha256=packet_sha,
        labels_sha256=sha256_hex(labels),
        answers=len(answers),
        operator_attests_human_reviewer=True,
    )
    _write_new(review_dir / f"labels-{handle}.jsonl", labels)
    _write_new(review_dir / f"seal-{handle}.json", _canonical(seal.model_dump(mode="json")))
    return seal


def load_sealed(review_dir: Path, eval_set: EvalSet) -> dict[str, list[ReviewAnswer]]:
    """Every sealed reviewer's labels, each checked against its seal, selection and packet."""
    _, selection_sha = load_selection(review_dir, eval_set)
    _, packet_sha = load_packet(review_dir)
    sealed: dict[str, list[ReviewAnswer]] = {}
    for seal_path in sorted(review_dir.glob("seal-ann-*.json")):
        try:
            seal = ReviewSeal.model_validate_json(seal_path.read_bytes())
            labels = (review_dir / f"labels-{seal.annotator_handle}.jsonl").read_bytes()
        except (OSError, ValidationError) as exc:
            raise EvalSetError(f"cannot read {seal_path.name} ({type(exc).__name__})") from None
        if (seal.selection_sha256, seal.packet_sha256, seal.labels_sha256) != (
            selection_sha,
            packet_sha,
            sha256_hex(labels),
        ):
            raise EvalSetError(f"{seal_path.name} does not match its labels, selection or packet")
        sealed[seal.annotator_handle] = [
            ReviewAnswer.model_validate_json(line) for line in labels.splitlines()
        ]
    return sealed


# ---- score ------------------------------------------------------------------------------


def _agreement(pairs: list[tuple[str, str | None]]) -> dict[str, Any]:
    """Human label vs other label; `unsure` human answers and missing others are counted apart."""
    scored = [(h, o) for h, o in pairs if h != UNSURE and o is not None]
    agree = sum(1 for h, o in scored if h == o)
    return {
        "items": len(pairs),
        "human_unsure": sum(1 for h, _ in pairs if h == UNSURE),
        "other_missing": sum(1 for h, o in pairs if h != UNSURE and o is None),
        "scored": len(scored),
        "agree": agree,
        "agreement": None if not scored else round(agree / len(scored), 4),
    }


def _model_label(proposal: Any) -> str | None:
    """The model's unique label, `none`, `tie` (several top labels), or None when absent."""
    if proposal is None:
        return None
    if proposal.label is not None:
        return proposal.label
    return proposal.top_labels[0] if len(proposal.top_labels) == 1 else "tie"


def score(eval_set: EvalSet, review_dir: Path, outcomes_path: Path | None) -> dict[str, Any]:
    selection, _ = load_selection(review_dir, eval_set)
    sealed = load_sealed(review_dir, eval_set)
    if not sealed:
        raise EvalSetError("no sealed human labels; nothing to score")
    item_of = dict(selection.items)
    outcomes = {} if outcomes_path is None else load_outcomes(outcomes_path, eval_set)[1]
    report: dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA,
        "review_id": selection.review_id,
        "set_sha256": selection.set_sha256,
        "label_basis": "human review under the operator's attestation (see seals)",
        "reviewers": sorted(sealed),
        "by_reviewer": {},
    }
    for handle, answers in sealed.items():
        model_cat, model_sub, source_cat, source_sub, claims, review = [], [], [], [], [], []
        for a in answers:
            item = eval_set.item(item_of[a.packet_id])
            outcome = outcomes.get(item.item_id)
            ok = outcome is not None and outcome.fallback_reason is None
            cat = outcome.category if ok else None
            sub = outcome.subcategory if ok else None
            model_cat.append((a.category, _model_label(cat)))
            model_sub.append((a.subcategory, _model_label(sub)))
            src_cat, src_sub = item.label(Task.CATEGORY), item.label(Task.SUBCATEGORY)
            source_cat.append((a.category, src_cat.value if src_cat else None))
            source_sub.append((a.subcategory, src_sub.value if src_sub else None))
            support = {p.ordinal: p for p in (outcome.attribute_support if ok else ())}
            for ordinal, label in a.claims.items():
                p = support.get(int(ordinal))
                claims.append((label, _model_label(p)))
            if outcome is not None:
                review.append(a.needs_review)
        report["by_reviewer"][handle] = {
            "category_vs_model": _agreement(model_cat),
            "subcategory_vs_model": _agreement(model_sub),
            "claim_support_vs_model": _agreement(claims),
            "category_vs_source_label": _agreement(source_cat),
            "subcategory_vs_source_label": _agreement(source_sub),
            "needs_review_answers": dict(Counter(review)),
            "note": (
                "a model tie counts as `tie` (never a human label); fallbacks count as missing. "
                "Every recorded route is manual_review, so route agreement is the share of "
                "`yes` among needs_review answers"
            ),
        }
    return report


# ---- command line -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.listing_decisions.review")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("freeze", "packet", "import", "score"):
        p = sub.add_parser(name)
        p.add_argument("--set-dir", type=Path, required=True)
        p.add_argument("--review-id", required=True)
        if name == "freeze":
            p.add_argument("--seed", type=int, required=True)
            p.add_argument("--per-source", type=int, default=20)
        if name == "import":
            p.add_argument("--answers", type=Path, required=True)
            p.add_argument("--reviewer-is-human", action="store_true")
        if name == "score":
            p.add_argument("--outcomes", type=Path)
            p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    review_dir = args.set_dir / "review" / args.review_id
    try:
        eval_set = load_set(args.set_dir / "set.json")
        if args.command == "freeze":
            selection, digest = freeze(
                eval_set, review_dir, args.review_id, args.seed, args.per_source
            )
            result: dict[str, Any] = {"items": len(selection.items), "selection_sha256": digest}
        elif args.command == "packet":
            result = {"packet_sha256": write_packet(eval_set, args.set_dir / "raw", review_dir)}
        elif args.command == "import":
            seal = import_answers(
                eval_set, review_dir, args.answers, reviewer_is_human=args.reviewer_is_human
            )
            result = {"sealed": seal.annotator_handle, "answers": seal.answers}
        else:
            report = score(eval_set, review_dir, args.outcomes)
            _write_new(args.out, json.dumps(report, indent=2, sort_keys=True).encode() + b"\n")
            result = {"reviewers": report["reviewers"]}
    except (EvalSetError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

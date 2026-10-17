"""Interactive blind annotation of the frozen pool (primary round, annotator `ann-1` only).

Commands:

* `status`: how many pooled pairs are judged (counts only; no labels are shown).
* `annotate --annotator ann-1`: one query-product pair at a time, in blind order. Shows the
  query, its recorded interpretations, the annotator's own gold constraints and product intents,
  and the `card-1` product card. Never shows a source, rank, score, inclusion reason, parser
  output, filter, seller, availability, rating or review count. Nothing is prefilled or
  suggested. Every confirmed judgement is saved atomically before the next pair; `q` stops and a
  later run resumes at the first unjudged pair. End of input (EOF) at any prompt ends the
  session the same way (exit code 0) and never saves a partly answered pair. A session holds
  an exclusive lock on its work directory; a second session refuses to start, and a lock left
  by a hard kill must be removed by hand after checking that no session is running (there is
  no override).
* `annotate --annotator ann-1 --compact`: the same session, but each pair's whole decision is
  typed on one line as `<label><relation><confidence><adjudication><catalog_issue>!`, for
  example `b1mnn! | rationale`. The trailing `!` is the explicit confirmation; there is no
  separate save prompt. A malformed entry, a relation the label does not allow, a missing `!`
  or a missing required rationale saves nothing. Every other rule is the same as above.
* `validate`: re-check the working file against the frozen pool.

Usage:
  uv run python -m evals.annotation status
  uv run python -m evals.annotation annotate --annotator ann-1
  uv run python -m evals.annotation annotate --annotator ann-1 --compact
  uv run python -m evals.annotation validate
"""

import argparse
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evals.annotation.session import (
    WORK_ROOT,
    AnnotationError,
    PoolContext,
    WorkStore,
    annotation_lock,
    hard_unknown,
    hard_violation,
    load_pool,
    pending,
)
from evals.contracts.canonical import ContractError
from evals.contracts.cards import render_card
from evals.contracts.constants import (
    ALLOWED_RELATIONS,
    SINGLE_ANNOTATOR_HANDLE,
    Label,
    Relation,
)
from evals.contracts.models import (
    Confidence,
    Judgement,
    PoolItem,
    Round,
    canonical_gold_value,
)
from evals.runners import build_pool

Ask = Callable[[str], str]
Say = Callable[[str], None]
Now = Callable[[], datetime]

LABEL_KEYS = {"r": Label.RELEVANT, "b": Label.BORDERLINE, "n": Label.NOT_RELEVANT}
CONFIDENCE_KEYS = {"h": Confidence.HIGH, "m": Confidence.MEDIUM, "l": Confidence.LOW}
QUIT = "q"


class Quit(Exception):
    """The annotator stopped; everything confirmed so far is saved."""


class InputEnded(Quit):
    """Input ended (EOF) at a prompt: handled exactly like `q`, the pair is not saved."""


INPUT_ENDED_MESSAGE = "Input ended; progress saved. Resume by running the annotation command again."


def _eof_as_quit(ask: Ask) -> Ask:
    """Every prompt answers EOF by ending the session cleanly instead of crashing."""

    def wrapped(prompt: str) -> str:
        try:
            return ask(prompt)
        except EOFError:
            raise InputEnded from None

    return wrapped


def query_block(context: PoolContext, item: PoolItem) -> str:
    query = context.queries[item.query_id]
    lines = [f"query {query.query_id}: {query.text}"]
    for interpretation in query.interpretations:
        lines.append(f"  interpretation: {interpretation}")
    own = [c for c in context.constraints if c.query_id == item.query_id]
    for c in sorted(own, key=lambda c: c.sort_key()):
        lines.append(
            f"  your {c.hardness.value} constraint: {c.field.value} {c.operator.value} "
            f"{canonical_gold_value(c.value)}"
        )
    for intent in context.intents:
        if intent.query_id == item.query_id:
            lines.append(f"  your soft {intent.kind.value} intent: {intent.value}")
    if not own:
        lines.append("  (no gold constraints recorded for this query)")
    return "\n".join(lines)


def _choose[T](ask: Ask, prompt: str, keys: dict[str, T]) -> T:
    while True:
        answer = ask(prompt).strip().lower()
        if answer == QUIT:
            raise Quit
        if answer in keys:
            return keys[answer]


def _yes_no(ask: Ask, prompt: str) -> bool:
    while True:
        answer = ask(prompt).strip().lower()
        if answer == QUIT:
            raise Quit
        if answer in ("y", "n", ""):
            return answer == "y"


def _rationale(ask: Ask, say: Say, required: bool) -> str | None:
    while True:
        text = ask("rationale" + (" (required): " if required else " (optional, Enter to skip): "))
        text = text.strip()
        if text == QUIT:
            raise Quit
        if text or not required:
            return text or None
        say("a rationale is required here")


def _relation_keys(label: Label) -> dict[str, Relation]:
    """The label's allowed relations, numbered from 1 in name order (both entry modes)."""
    relations = sorted(ALLOWED_RELATIONS[label], key=lambda r: r.value)
    return {str(i): r for i, r in enumerate(relations, start=1)}


def _rationale_required(
    context: PoolContext, item: PoolItem, label: Label, confidence: Confidence
) -> bool:
    return (
        label is Label.BORDERLINE
        or confidence is Confidence.LOW
        or (label is Label.RELEVANT and hard_unknown(context, item))
    )


def ask_judgement(context: PoolContext, item: PoolItem, ask: Ask, say: Say, now: Now) -> Judgement:
    """Collect one judgement from the annotator. Every field starts unset."""
    while True:
        label = _choose(
            ask, "label [r]elevant / [b]orderline / [n]ot_relevant / [q]uit: ", LABEL_KEYS
        )
        if label is Label.RELEVANT and hard_violation(context, item):
            say(
                "relevant is not allowed: this product violates a hard gold constraint of the "
                "query (rubric rule). Choose borderline (substitute) or not_relevant."
            )
            continue
        relation_keys = _relation_keys(label)
        menu = " / ".join(f"[{k}] {r.value}" for k, r in relation_keys.items())
        relation: Relation = _choose(ask, f"relation {menu}: ", relation_keys)
        confidence = _choose(ask, "confidence [h]igh / [m]edium / [l]ow: ", CONFIDENCE_KEYS)
        required = _rationale_required(context, item, label, confidence)
        rationale = _rationale(ask, say, required)
        needs_adjudication = _yes_no(ask, "needs adjudication? [y/N]: ")
        catalog_issue = _yes_no(ask, "catalog issue (malformed record)? [y/N]: ")
        try:
            judgement = Judgement(
                query_id=item.query_id,
                product_id=item.product_id,
                round=Round.PRIMARY,
                label=label,
                relation=relation,
                confidence=confidence,
                rationale=rationale,
                needs_adjudication=needs_adjudication,
                catalog_issue=catalog_issue,
                annotator_handle=SINGLE_ANNOTATOR_HANDLE,
                annotated_at=now(),
                rubric_version=context.rubric_version,
            )
        except ValueError as exc:
            say(f"not saved: {exc}")
            continue
        summary = (
            f"{label.value} / {relation.value} / {confidence.value}"
            f"{' / needs adjudication' if needs_adjudication else ''}"
            f"{' / catalog issue' if catalog_issue else ''}"
        )
        if _yes_no(ask, f"save {summary}? [y/N]: "):
            return judgement
        say("discarded; judge this pair again")


COMPACT_TOKEN = re.compile(r"([rbn])([0-9])([hml])([yn])([yn])(!*)")
COMPACT_PROMPT = (
    "decision <label><relation><confidence><adjudication><catalog_issue>! [| rationale] "
    "(? codes, q quit): "
)
YES_NO_KEYS = {"y": True, "n": False}


class CompactEntryError(ValueError):
    """A compact entry that is not a complete, confirmed decision; nothing is saved."""


@dataclass(frozen=True)
class CompactEntry:
    label: Label
    relation: Relation
    confidence: Confidence
    needs_adjudication: bool
    catalog_issue: bool
    rationale: str | None


def compact_codes() -> str:
    lines = [
        "compact entry: <label><relation><confidence><adjudication><catalog_issue>! [| rationale]",
        "  label: r relevant / b borderline / n not_relevant",
    ]
    for key, label in LABEL_KEYS.items():
        codes = ", ".join(f"{k} {r.value}" for k, r in _relation_keys(label).items())
        lines.append(f"  relation after {key}: {codes}")
    lines += [
        "  confidence: h high / m medium / l low",
        "  adjudication: y / n; catalog issue (malformed record): y / n",
        "  the trailing ! confirms and saves; a rationale follows ' | ' on the same line",
        "  q quits; everything confirmed so far is already saved",
    ]
    return "\n".join(lines)


def parse_compact(text: str) -> CompactEntry:
    """Parse one compact decision. Every field must be typed; nothing has a default."""
    token, bar, rest = text.partition("|")
    token = token.strip().lower()
    rationale = rest.strip() or None
    if bar and rationale is None:
        raise CompactEntryError("nothing follows '|'; type the rationale or remove the '|'")
    match = COMPACT_TOKEN.fullmatch(token)
    if match is None:
        raise CompactEntryError(
            "malformed entry; type <label><relation><confidence><adjudication><catalog_issue>! "
            "(? shows the codes)"
        )
    label_key, relation_key, confidence_key, adjudication_key, issue_key, bang = match.groups()
    if bang != "!":
        raise CompactEntryError("not confirmed; end the entry with a single !")
    label = LABEL_KEYS[label_key]
    relation = _relation_keys(label).get(relation_key)
    if relation is None:
        raise CompactEntryError(f"relation {relation_key} is not allowed for {label.value}")
    return CompactEntry(
        label=label,
        relation=relation,
        confidence=CONFIDENCE_KEYS[confidence_key],
        needs_adjudication=YES_NO_KEYS[adjudication_key],
        catalog_issue=YES_NO_KEYS[issue_key],
        rationale=rationale,
    )


def ask_judgement_compact(
    context: PoolContext, item: PoolItem, ask: Ask, say: Say, now: Now
) -> Judgement:
    """Collect one judgement typed as a single confirmed compact entry."""
    while True:
        text = ask(COMPACT_PROMPT).strip()
        if text.lower() == QUIT:
            raise Quit
        if text == "?":
            say(compact_codes())
            continue
        try:
            entry = parse_compact(text)
        except CompactEntryError as exc:
            say(f"not saved: {exc}")
            continue
        if entry.label is Label.RELEVANT and hard_violation(context, item):
            say(
                "not saved: relevant is not allowed: this product violates a hard gold constraint "
                "of the query (rubric rule). Choose borderline (substitute) or not_relevant."
            )
            continue
        if entry.rationale is None and _rationale_required(
            context, item, entry.label, entry.confidence
        ):
            say("not saved: a rationale is required here; add it after ' | ' on the same line")
            continue
        try:
            return Judgement(
                query_id=item.query_id,
                product_id=item.product_id,
                round=Round.PRIMARY,
                label=entry.label,
                relation=entry.relation,
                confidence=entry.confidence,
                rationale=entry.rationale,
                needs_adjudication=entry.needs_adjudication,
                catalog_issue=entry.catalog_issue,
                annotator_handle=SINGLE_ANNOTATOR_HANDLE,
                annotated_at=now(),
                rubric_version=context.rubric_version,
            )
        except ValueError as exc:
            say(f"not saved: {exc}")


def _summary(judgement: Judgement) -> str:
    return (
        f"{judgement.label.value} / {judgement.relation.value} / {judgement.confidence.value}"
        f"{' / needs adjudication' if judgement.needs_adjudication else ''}"
        f"{' / catalog issue' if judgement.catalog_issue else ''}"
    )


def annotate(
    context: PoolContext,
    store: WorkStore,
    *,
    annotator: str,
    ask: Ask = input,
    say: Say = print,
    now: Now = lambda: datetime.now(UTC),
    compact: bool = False,
) -> int:
    """Annotate pending pairs until done or the annotator quits. Returns the number saved."""
    if annotator != SINGLE_ANNOTATOR_HANDLE:
        raise AnnotationError(f"refused: only {SINGLE_ANNOTATOR_HANDLE!r} annotates")
    # The lock is taken before any judgement is read; it is released on every exit path.
    with annotation_lock(store, annotator=annotator, now=now()):
        return _annotate_locked(context, store, ask=ask, say=say, now=now, compact=compact)


def _annotate_locked(
    context: PoolContext, store: WorkStore, *, ask: Ask, say: Say, now: Now, compact: bool
) -> int:
    ask = _eof_as_quit(ask)
    store.initialize(now=now())
    judgements = store.load(now=now())
    todo = pending(context, judgements)
    total = len(context.items)
    say(f"{total - len(todo)} of {total} pairs judged; {len(todo)} to go")
    if compact:
        say(compact_codes())
    collect = ask_judgement_compact if compact else ask_judgement
    saved = 0
    for item in todo:
        say("")
        say(f"--- pair {total - len(todo) + saved + 1} of {total} ---")
        say(query_block(context, item))
        say("")
        say(render_card(context.catalog.products[item.product_id]))
        try:
            judgement = collect(context, item, ask, say, now)
        except InputEnded:
            say(INPUT_ENDED_MESSAGE)
            return saved
        except Quit:
            say(f"stopped; {saved} judgement(s) saved this session")
            return saved
        judgements.append(judgement)
        store.save(judgements, now=now())
        saved += 1
        if compact:
            say(f"saved: {_summary(judgement)}")
    say(f"all {total} pairs judged. Next: `python -m evals.runners.seal_primary check`")
    return saved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.annotation")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--work-root", type=Path, default=WORK_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="progress counts only")
    run = commands.add_parser("annotate", help="interactive blind primary annotation")
    run.add_argument("--annotator", required=True)
    run.add_argument(
        "--compact",
        action="store_true",
        help="type each whole decision on one line, confirmed by a trailing !",
    )
    commands.add_parser("validate", help="re-check the working file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        context = load_pool(args.golden_dir)
        store = WorkStore(context, args.work_root)
        if args.command == "annotate":
            annotate(context, store, annotator=args.annotator, compact=args.compact)
            return 0
        judgements = store.load(now=datetime.now(UTC))
        if args.command == "status":
            print(f"pool_frozen {context.pool_manifest.manifest_sha256}")
            print(f"{len(judgements)} of {len(context.items)} pairs judged")
            if store.lock_path.exists():
                print("an annotation lock is present (a session is running or was hard-killed)")
        else:
            print(f"working file valid: {len(judgements)} primary judgement(s)")
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0

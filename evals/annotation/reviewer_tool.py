"""Blind second-review tool (review-2) for the independent second annotator `ann-2`.

This file is copied unchanged into the reviewer bundle as `review.py` and runs there with the
Python standard library only: the bundle holds no repository, history, primary label, sample
allocation, machine proposal, provenance, source, rank, score, agreement figure or adjudication
queue. It reads `bundle.json` next to itself and writes, in the same directory:

* `binding.json`: the bundle's SHA-256, the annotator and a UTC `created_at` (written once);
* `second_review_judgements.jsonl`: canonical JSONL judgements, atomically replaced after
  every confirmed decision (the repository's `Judgement` contract, round `second_review`);
* `review.lock` while a session runs (exclusive; never removed automatically after a hard
  kill: check that no session is running, then delete it by hand).

Commands (run inside the bundle directory):

  python review.py status
  python review.py annotate --annotator ann-2
  python review.py annotate --annotator ann-2 --compact
  python review.py finish

Each pair shows the query, its recorded interpretations, the frozen gold constraints and soft
intents, and the `card-1` product card, exactly one pair at a time in the sample's blind order.

By default the decision is asked step by step, exactly like the primary round's default mode:
label, then one of that label's allowed relations, confidence, the rationale (required for
borderline, low confidence, and relevant with an unknown hard attribute; otherwise optional),
needs adjudication [y/N], catalog issue [y/N], then a summary of all six answers with an
explicit save confirmation (only `y` saves; anything else, Enter included, discards the pair's
answers and asks again from the label). An invalid answer is explained and asked again. Label,
relation, confidence and the save confirmation have no default; nothing is suggested; relevant
is refused for a hard-constraint violation.

`--compact` keeps the one-line mode with the primary round's compact codes:
`<label><relation><confidence><adjudication><catalog_issue>! [| rationale]`; the trailing `!`
confirms, and a malformed or unconfirmed entry saves nothing.

In both modes the same rules, records and file are used. `q` (or end of input) stops at any
prompt; everything confirmed is kept, a partly answered pair is never saved, and a later run
resumes. `finish` checks that every pair is judged and prints the file's SHA-256 for the
annotator's own seal confirmation.

Blindness is procedural: the bundle simply does not contain the primary labels. It is not
cryptographic isolation.
"""

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

BUNDLE_FILE = "bundle.json"
BINDING_FILE = "binding.json"
JUDGEMENTS_FILE = "second_review_judgements.jsonl"
LOCK_FILE = "review.lock"
ANNOTATOR = "ann-2"
ROUND = "second_review"
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
FREE_TEXT_MAX = 1000
QUIT = "q"

LABELS = {"r": "relevant", "b": "borderline", "n": "not_relevant"}
# Allowed relations per label, numbered from 1 in name order (as in the primary round).
RELATIONS = {
    "relevant": ("exact_match", "satisfies_intent"),
    "borderline": ("partial", "substitute"),
    "not_relevant": ("complement", "off_target", "out_of_catalog"),
}
CONFIDENCES = {"h": "high", "m": "medium", "l": "low"}
YES_NO = {"y": True, "n": False}
COMPACT_TOKEN = re.compile(r"([rbn])([0-9])([hml])([yn])([yn])(!*)")
# The repository's free-text rules (no email address, no machine-specific path).
EMAIL_RE = re.compile(r"\S+@\S+\.\S+")
MACHINE_PATH_RE = re.compile(r"(?:\b[A-Za-z]:[\\/])|(?:\\\\)|(?:(?:^|\s)/(?:Users|home)/)")
PROMPT = (
    "decision <label><relation><confidence><adjudication><catalog_issue>! [| rationale] "
    "(? codes, q quit): "
)

Ask = Callable[[str], str]
Say = Callable[[str], None]
Now = Callable[[], datetime]


class ReviewError(Exception):
    """The workspace cannot be used as it is; nothing was saved."""


class EntryError(ValueError):
    """A typed entry that is not a complete, confirmed, allowed decision; nothing is saved."""


class Stop(Exception):
    """The annotator stopped (q or end of input); everything confirmed is already saved."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def codes() -> str:
    lines = [
        "entry: <label><relation><confidence><adjudication><catalog_issue>! [| rationale]",
        "  label: r relevant / b borderline / n not_relevant",
    ]
    for key, label in LABELS.items():
        numbered = ", ".join(f"{i} {r}" for i, r in enumerate(RELATIONS[label], start=1))
        lines.append(f"  relation after {key}: {numbered}")
    lines += [
        "  confidence: h high / m medium / l low",
        "  adjudication: y / n; catalog issue (malformed record): y / n",
        "  the trailing ! confirms and saves; a rationale follows ' | ' on the same line",
        "  q quits; everything confirmed so far is already saved",
    ]
    return "\n".join(lines)


def check_free_text(text: str) -> str:
    if not text or text != text.strip():
        raise EntryError("the rationale must be non-empty with no surrounding spaces")
    if len(text) > FREE_TEXT_MAX:
        raise EntryError(f"the rationale is longer than {FREE_TEXT_MAX} characters")
    if EMAIL_RE.search(text):
        raise EntryError("the rationale looks like it contains an email address")
    if MACHINE_PATH_RE.search(text):
        raise EntryError("the rationale must not contain a machine-specific path")
    return text


def parse_entry(text: str) -> dict:
    """One compact decision; every field must be typed and nothing has a default."""
    token, bar, rest = text.partition("|")
    token = token.strip().lower()
    rationale = rest.strip() or None
    if bar and rationale is None:
        raise EntryError("nothing follows '|'; type the rationale or remove the '|'")
    match = COMPACT_TOKEN.fullmatch(token)
    if match is None:
        raise EntryError("malformed entry (? shows the codes)")
    label_key, relation_key, confidence_key, adjudication_key, issue_key, bang = match.groups()
    if bang != "!":
        raise EntryError("not confirmed; end the entry with a single !")
    label = LABELS[label_key]
    relations = RELATIONS[label]
    number = int(relation_key)
    if not 1 <= number <= len(relations):
        raise EntryError(f"relation {relation_key} is not allowed for {label}")
    return {
        "label": label,
        "relation": relations[number - 1],
        "confidence": CONFIDENCES[confidence_key],
        "needs_adjudication": YES_NO[adjudication_key],
        "catalog_issue": YES_NO[issue_key],
        "rationale": None if rationale is None else check_free_text(rationale),
    }


def record_line(record: dict) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def judgement_record(item: dict, entry: dict, *, rubric: str, at: datetime) -> dict:
    """Exactly the fields of the repository's `Judgement` contract, in canonical JSON form."""
    return {
        "adjudication_reason": None,
        "annotated_at": timestamp(at),
        "annotator_handle": ANNOTATOR,
        "catalog_issue": entry["catalog_issue"],
        "changes_label": None,
        "confidence": entry["confidence"],
        "label": entry["label"],
        "needs_adjudication": entry["needs_adjudication"],
        "product_id": item["product_id"],
        "query_id": item["query_id"],
        "rationale": entry["rationale"],
        "relation": entry["relation"],
        "review_reasons": [],
        "round": ROUND,
        "rubric_version": rubric,
        "schema_version": 1,
    }


def encode_records(records: list[dict]) -> bytes:
    ordered = sorted(records, key=lambda r: (r["query_id"], r["product_id"]))
    return "".join(record_line(r) + "\n" for r in ordered).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with open(temporary, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class Workspace:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        raw = (directory / BUNDLE_FILE).read_bytes()
        self.bundle_sha256 = sha256_hex(raw)
        self.bundle = json.loads(raw)
        if self.bundle.get("review_protocol") != "review-2" or self.bundle.get("round") != ROUND:
            raise ReviewError("this is not a review-2 second-review bundle")
        rubric = directory / self.bundle["rubric_file"]
        if not rubric.is_file() or sha256_hex(rubric.read_bytes()) != self.bundle["rubric_sha256"]:
            raise ReviewError("the rubric file is missing or differs from the bundle")
        self.items: list[dict] = self.bundle["items"]
        self.pairs = {(i["query_id"], i["product_id"]): i for i in self.items}
        self.path = directory / JUDGEMENTS_FILE
        self.lock = directory / LOCK_FILE
        self.binding_path = directory / BINDING_FILE

    def binding(self, *, now: datetime, create: bool) -> dict | None:
        if self.binding_path.exists():
            binding = json.loads(self.binding_path.read_bytes())
            if (
                binding.get("bundle_sha256") != self.bundle_sha256
                or binding.get("annotator_handle") != ANNOTATOR
            ):
                raise ReviewError("binding.json belongs to another bundle or annotator")
            return binding
        if not create:
            return None
        binding = {
            "annotator_handle": ANNOTATOR,
            "bundle_sha256": self.bundle_sha256,
            "created_at": timestamp(now),
        }
        with open(self.binding_path, "xb") as handle:
            handle.write((record_line(binding) + "\n").encode("utf-8"))
        return binding

    def load(self) -> list[dict]:
        if not self.path.exists():
            return []
        data = self.path.read_bytes()
        records = [json.loads(line) for line in data.decode("utf-8").splitlines()]
        if encode_records(records) != data:
            raise ReviewError("the judgements file is not in canonical form; refusing")
        seen: set[tuple[str, str]] = set()
        for record in records:
            pair = (record["query_id"], record["product_id"])
            if pair not in self.pairs or pair in seen or record["annotator_handle"] != ANNOTATOR:
                raise ReviewError(f"{pair!r}: unknown, duplicate or foreign judgement")
            seen.add(pair)
        return records

    def save(self, records: list[dict]) -> None:
        _atomic_write(self.path, encode_records(records))


def render(item: dict, number: int, total: int) -> str:
    lines = [f"--- pair {number} of {total} (position {item['position']}) ---"]
    lines.append(f"query {item['query_id']}: {item['query_text']}")
    lines += [f"  interpretation: {text}" for text in item["interpretations"]]
    lines += [f"  {text}" for text in item["constraints"]]
    lines += [f"  {text}" for text in item["intents"]]
    if not item["constraints"]:
        lines.append("  (no gold constraints recorded for this query)")
    lines += ["", item["card_text"]]
    return "\n".join(lines)


def ask_entry(item: dict, ask: Ask, say: Say) -> dict:
    while True:
        try:
            text = ask(PROMPT).strip()
        except EOFError:
            raise Stop from None
        if text.lower() == QUIT:
            raise Stop
        if text == "?":
            say(codes())
            continue
        try:
            entry = parse_entry(text)
        except EntryError as exc:
            say(f"not saved: {exc}")
            continue
        if entry["label"] == "relevant" and item["hard_violated"]:
            say(f"not saved: {HARD_VIOLATION_MESSAGE}")
            continue
        required = (
            entry["label"] == "borderline"
            or entry["confidence"] == "low"
            or (entry["label"] == "relevant" and item["hard_unknown"])
        )
        if required and entry["rationale"] is None:
            say("not saved: a rationale is required here; add it after ' | ' on the same line")
            continue
        return entry


HARD_VIOLATION_MESSAGE = (
    "relevant is not allowed: this product violates a hard gold constraint of the query "
    "(rubric rule). Choose borderline (substitute) or not_relevant."
)


def _ask(ask: Ask, prompt: str) -> str:
    try:
        text = ask(prompt).strip()
    except EOFError:
        raise Stop from None
    if text.lower() == QUIT:
        raise Stop
    return text


def _choose(ask: Ask, say: Say, prompt: str, keys: dict[str, str]) -> str:
    while True:
        answer = _ask(ask, prompt).lower()
        if answer in keys:
            return keys[answer]
        say(f"please type one of: {' / '.join(keys)} (q quits)")


def _yes_no(ask: Ask, say: Say, prompt: str) -> bool:
    while True:
        answer = _ask(ask, prompt).lower()
        if answer in ("y", "n", ""):
            return answer == "y"
        say("please type y or n (Enter means n; q quits)")


def _rationale(ask: Ask, say: Say, required: bool) -> str | None:
    prompt = "4 rationale" + (" (required): " if required else " (optional, Enter to skip): ")
    while True:
        text = _ask(ask, prompt)
        if not text:
            if not required:
                return None
            say("a rationale is required here")
            continue
        try:
            return check_free_text(text)
        except EntryError as exc:
            say(f"not accepted: {exc}")


def summary(entry: dict) -> str:
    """Every answer of one pair, shown before the explicit save confirmation."""
    return "\n".join(
        [
            "summary of this pair (nothing is saved yet):",
            f"  1 label:              {entry['label']}",
            f"  2 relation:           {entry['relation']}",
            f"  3 confidence:         {entry['confidence']}",
            f"  4 rationale:          {entry['rationale'] or '(none)'}",
            f"  5 needs adjudication: {'yes' if entry['needs_adjudication'] else 'no'}",
            f"  6 catalog issue:      {'yes' if entry['catalog_issue'] else 'no'}",
        ]
    )


def ask_entry_steps(item: dict, ask: Ask, say: Say) -> dict:
    """Step-by-step decision, as in the primary round's default mode. Every field starts unset;
    nothing is saved unless the summary is explicitly confirmed with `y`."""
    while True:
        label = _choose(
            ask, say, "1 label [r]elevant / [b]orderline / [n]ot_relevant / [q]uit: ", LABELS
        )
        if label == "relevant" and item["hard_violated"]:
            say(HARD_VIOLATION_MESSAGE)
            continue
        relations = {str(i): r for i, r in enumerate(RELATIONS[label], start=1)}
        menu = " / ".join(f"[{k}] {r}" for k, r in relations.items())
        relation = _choose(ask, say, f"2 relation {menu}: ", relations)
        confidence = _choose(ask, say, "3 confidence [h]igh / [m]edium / [l]ow: ", CONFIDENCES)
        required = (
            label == "borderline"
            or confidence == "low"
            or (label == "relevant" and item["hard_unknown"])
        )
        entry = {
            "label": label,
            "relation": relation,
            "confidence": confidence,
            "rationale": _rationale(ask, say, required),
            "needs_adjudication": _yes_no(ask, say, "5 needs adjudication? [y/N]: "),
            "catalog_issue": _yes_no(ask, say, "6 catalog issue (malformed record)? [y/N]: "),
        }
        say(summary(entry))
        # only an explicit y saves; anything else (Enter included) discards this pair's answers
        if _ask(ask, "7 save this judgement? type y to save, anything else discards: ") in (
            "y",
            "Y",
        ):
            return entry
        say("discarded; nothing was saved for this pair. Judge it again from step 1")


def annotate(
    workspace: Workspace,
    *,
    annotator: str,
    ask: Ask = input,
    say: Say = print,
    now: Now = lambda: datetime.now(UTC),
    compact: bool = False,
) -> int:
    if annotator != ANNOTATOR:
        raise ReviewError(f"refused: only {ANNOTATOR!r} performs the second review")
    try:
        with open(workspace.lock, "x", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()} started_at={timestamp(now())}\n")
    except FileExistsError:
        raise ReviewError(
            "a review.lock exists: a session is running or was hard-killed. Check that no "
            "session is running, then delete review.lock by hand."
        ) from None
    try:
        workspace.binding(now=now(), create=True)
        records = workspace.load()
        done = {(r["query_id"], r["product_id"]) for r in records}
        todo = [i for i in workspace.items if (i["query_id"], i["product_id"]) not in done]
        total = len(workspace.items)
        say(f"{total - len(todo)} of {total} pairs judged; {len(todo)} to go")
        if compact:
            say(codes())
        collect = ask_entry if compact else ask_entry_steps
        saved = 0
        for item in todo:
            say("")
            say(render(item, total - len(todo) + saved + 1, total))
            try:
                entry = collect(item, ask, say)
            except Stop:
                say(f"stopped; {saved} judgement(s) saved this session")
                return saved
            record = judgement_record(
                item, entry, rubric=workspace.bundle["rubric_version"], at=now()
            )
            records.append(record)
            workspace.save(records)
            saved += 1
            say(f"saved: {entry['label']} / {entry['relation']} / {entry['confidence']}")
        say(f"all {total} pairs judged. Next: python review.py finish")
        return saved
    finally:
        workspace.lock.unlink(missing_ok=True)


def finish(workspace: Workspace) -> str:
    if workspace.lock.exists():
        raise ReviewError("a review.lock exists; finish only when no session is running")
    records = workspace.load()
    if len(records) != len(workspace.items):
        raise ReviewError(f"{len(workspace.items) - len(records)} pair(s) are not judged yet")
    return sha256_hex(workspace.path.read_bytes())


def main(argv: list[str] | None = None, directory: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python review.py")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="progress counts only")
    run = commands.add_parser("annotate", help="blind second review, one pair at a time")
    run.add_argument("--annotator", required=True)
    run.add_argument(
        "--compact",
        action="store_true",
        help="type each whole decision on one line, confirmed by a trailing !",
    )
    commands.add_parser("finish", help="check completeness and print the file's SHA-256")
    args = parser.parse_args(argv)
    try:
        workspace = Workspace(directory or Path(__file__).resolve().parent)
        if args.command == "annotate":
            annotate(workspace, annotator=args.annotator, compact=args.compact)
        elif args.command == "status":
            print(f"{len(workspace.load())} of {len(workspace.items)} pairs judged")
        else:
            print(f"complete second review: {len(workspace.items)} judgements")
            print(f"judgements_sha256 {finish(workspace)}")
    except (ReviewError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

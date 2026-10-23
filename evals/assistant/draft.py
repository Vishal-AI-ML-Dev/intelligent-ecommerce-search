"""Unsealed draft of an `eval` case set (Milestone 17, Step B).

A draft spec (`abe-draft-1`, hand-written, AI-authored) gives each case's message, stated
requirements and proposed outcome, written before any evidence is captured. The builder joins it
with one frozen V3 capture per clean message and writes `aq-2` cases, a provenance file and a
review packet for the owner. Nothing here calls a provider, writes to the database or reviews
anything: every case is `ai_authored` with no `case_review`, so `live` refuses the file.

Requirement fit is a catalog-field comparison (RAM and storage as exact values, like the M16
constraint check; prices inclusive). It proposes acceptable recommendations; it is not a human
judgement. The deterministic pre-retrieval decision of the code under test is recorded next to
the proposed outcome and never changes it: a mismatch is flagged for the owner.

Counterparts: the injection target is the best-ranked captured record that fails a stated
requirement (other than availability, when one exists), so forbidding it is consistent with the
clean expectation. A message injection keeps the clean evidence; a listing injection copies it
and changes one free-text field of the target.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ecommerce_search.assistant.clarify import decide_clarification
from ecommerce_search.assistant.models import AbstainReason, ClarifyReason, EvidenceProduct
from ecommerce_search.query_understanding.parser import parse_normalized_query
from ecommerce_search.search.query import normalize_query
from evals.assistant.contracts import (
    FREE_TEXT_FIELDS,
    SCHEMA_VERSION,
    Counterpart,
    EvalCase,
    EvidenceCapture,
    Expectation,
    Tag,
)

DRAFT_SPEC_VERSION = "abe-draft-1"
MAX_MESSAGE_LENGTH = 200  # Settings.search_max_query_length default, as in runner.py
Group = Literal[
    "grounded",
    "clarify",
    "unsupported",
    "false_refusal",
    "message_injection",
    "listing_injection",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Requirements(_Frozen):
    category: str | None = None
    brand: str | None = None
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    ram_gb: int | None = None
    storage_gb: int | None = None
    storage_type: str | None = None
    subcategory: str | None = None
    wireless: bool | None = None
    anc: bool | None = None
    gender: tuple[str, ...] | None = None
    color: str | None = None


class CounterpartSpec(_Frozen):
    clean: str
    site: Literal["message", "listing"]
    template: str


class DraftCaseSpec(_Frozen):
    case_id: str
    group: Group
    tags: tuple[Tag, ...] = Field(min_length=1)
    message: str | None = None
    proposed_outcome: Literal["answer", "clarify", "abstain"] | None = None
    clarify_reason: ClarifyReason | None = None
    abstain_reasons: tuple[AbstainReason, ...] = ()
    requirements: Requirements = Requirements()
    rationale: str
    counterpart: CounterpartSpec | None = None


class ListingTemplate(_Frozen):
    field: Literal["title", "subcategory", "description"]
    mode: Literal["replace", "append", "prepend"]
    text: str


class DraftSpec(_Frozen):
    spec_version: Literal["abe-draft-1"]
    set_name: str
    status: Literal["unsealed_draft"]
    author: str
    authorship_note: str
    revision_notes: tuple[str, ...] = ()
    message_templates: dict[str, str]
    listing_templates: dict[str, ListingTemplate]
    cases: tuple[DraftCaseSpec, ...]


@dataclass(frozen=True)
class Capture:
    """One V3 retrieval of a clean message, frozen."""

    search_version: str
    evidence: tuple[EvidenceProduct, ...]


@dataclass(frozen=True)
class CaptureRun:
    captured_at: datetime
    top_k: int
    captures: dict[str, Capture]  # clean case_id -> capture
    # product_id -> catalog spec values not carried by EvidenceProduct (wireless, anc, ...)
    extras: dict[str, dict[str, Any]]
    environment: dict[str, Any]


def unmet(product: EvidenceProduct, extra: dict[str, Any], req: Requirements) -> list[str]:
    """Stated requirements the catalog record does not satisfy (empty: it fits)."""
    reasons: list[str] = []

    def same(field: str, wanted: object, actual: object) -> None:
        if actual is None:
            reasons.append(f"{field} not given")
        elif str(actual).casefold() != str(wanted).casefold():
            reasons.append(f"{field} {actual} != {wanted}")

    if req.category is not None:
        same("category", req.category, product.category)
    if req.brand is not None:
        same("brand", req.brand, product.brand)
    if req.max_price is not None and product.price > req.max_price:
        reasons.append(f"price {product.price} > {req.max_price}")
    if req.min_price is not None and product.price < req.min_price:
        reasons.append(f"price {product.price} < {req.min_price}")
    for field in ("ram_gb", "storage_gb", "storage_type"):
        if getattr(req, field) is not None:
            same(field, getattr(req, field), getattr(product, field))
    if req.subcategory is not None:
        same("subcategory", req.subcategory, product.subcategory)
    for field in ("wireless", "anc", "color"):
        if getattr(req, field) is not None:
            same(field, getattr(req, field), extra.get(field))
    if req.gender is not None and extra.get("gender") not in req.gender:
        reasons.append(f"gender {extra.get('gender')} not in {'/'.join(req.gender)}")
    if product.availability != "in_stock":
        reasons.append(f"availability {product.availability}")
    return reasons


def pre_retrieval_decision(message: str) -> str:
    """The code under test's deterministic decision before retrieval (no provider)."""
    clarification = decide_clarification(
        parse_normalized_query(normalize_query(message, MAX_MESSAGE_LENGTH))
    )
    return "retrieve" if clarification is None else f"clarify:{clarification.reason}"


def choose_target(assessed: list[tuple[EvidenceProduct, list[str]]]) -> str | None:
    for product, reasons in assessed:
        if any(not r.startswith("availability") for r in reasons):
            return product.product_id
    return next((product.product_id for product, reasons in assessed if reasons), None)


def mutate(product: EvidenceProduct, template: ListingTemplate) -> EvidenceProduct:
    if template.field not in FREE_TEXT_FIELDS:
        raise ValueError(f"listing templates change free text only, not {template.field}")
    before = getattr(product, template.field) or ""
    text = template.text.replace("{pid}", product.product_id)
    after = {
        "replace": text,
        "append": before + text,
        "prepend": text + before,
    }[template.mode]
    return product.model_copy(update={template.field: after})


def build(spec: DraftSpec, run: CaptureRun) -> tuple[list[EvalCase], dict[str, Any]]:
    """`aq-2` cases (in spec order) and per-case provenance for the review packet."""
    capture = EvidenceCapture(
        search_version=next(iter(run.captures.values())).search_version,
        top_k=run.top_k,
        captured_at=run.captured_at,
    )
    cases: list[EvalCase] = []
    details: dict[str, Any] = {}
    clean_results: dict[str, tuple[EvalCase, dict[str, Any]]] = {}

    def assess(evidence: tuple[EvidenceProduct, ...], req: Requirements):
        return [(p, unmet(p, run.extras.get(p.product_id, {}), req)) for p in evidence]

    for item in spec.cases:
        if item.counterpart is not None:
            continue
        if item.message is None or item.proposed_outcome is None:
            raise ValueError(f"{item.case_id}: a clean case needs a message and an outcome")
        got = run.captures[item.case_id]
        if got.search_version != capture.search_version:
            raise ValueError(f"{item.case_id}: captures span search versions")
        assessed = assess(got.evidence, item.requirements)
        fitting = tuple(p.product_id for p, reasons in assessed if not reasons)
        flags: list[str] = []
        decision = pre_retrieval_decision(item.message)
        if item.proposed_outcome == "clarify":
            if decision != f"clarify:{item.clarify_reason}":
                flags.append(f"pre-retrieval decision is {decision}, proposed clarify")
        elif decision != "retrieve":
            flags.append(f"pre-retrieval decision is {decision}: evidence would not be used")
        if item.proposed_outcome == "answer" and not fitting:
            flags.append("no captured record satisfies the stated requirements")
        if item.proposed_outcome == "abstain" and fitting:
            flags.append("captured records satisfy the stated requirements")
        if not got.evidence:
            flags.append("V3 returned no records")
        clarify = item.proposed_outcome == "clarify"
        expect = Expectation(
            outcome=item.proposed_outcome,
            clarify_reason=item.clarify_reason,
            abstain_reasons=item.abstain_reasons,
            acceptable_recommendation_ids=None if clarify else fitting,
            max_generation_calls=0 if clarify else 2,
        )
        case = _case(spec, item, item.message, "captured_v3", capture, got.evidence, None, expect)
        info = {
            "decision": decision,
            "assessed": {p.product_id: reasons for p, reasons in assessed},
            "flags": flags,
            "target": choose_target(assessed),
        }
        clean_results[item.case_id] = (case, info)
        details[item.case_id] = info

    for item in spec.cases:
        if item.counterpart is None:
            cases.append(clean_results[item.case_id][0])
            continue
        pair = item.counterpart
        clean, info = clean_results[pair.clean]
        target = info["target"]
        if target is None:
            raise ValueError(f"{item.case_id}: clean case has no non-fitting record to target")
        acceptable = tuple(
            i for i in clean.expect.acceptable_recommendation_ids or () if i != target
        )
        expect = clean.expect.model_copy(
            update={
                "acceptable_recommendation_ids": acceptable,
                "forbidden_recommendation_ids": (target,),
                "required_evidence_ids": (target,),
            }
        )
        if pair.site == "message":
            message = spec.message_templates[pair.template].format(
                clean=clean.message, target=target
            )
            evidence = clean.evidence
            source = "captured_v3"
            mutation: dict[str, Any] = {
                "site": "message",
                "template": pair.template,
                "before": clean.message,
                "after": message,
            }
            injected: tuple[str, ...] = ()
        else:
            template = spec.listing_templates[pair.template]
            message = clean.message
            evidence = tuple(
                mutate(p, template) if p.product_id == target else p for p in clean.evidence
            )
            source = "captured_v3_injected"
            original = next(p for p in clean.evidence if p.product_id == target)
            mutation = {
                "site": "listing",
                "template": pair.template,
                "product_id": target,
                "field": template.field,
                "mode": template.mode,
                "before": getattr(original, template.field),
                "after": getattr(
                    next(p for p in evidence if p.product_id == target), template.field
                ),
            }
            injected = (target,)
        flags = list(info["flags"])
        decision = pre_retrieval_decision(message)
        if decision != info["decision"]:
            flags.append(f"pre-retrieval decision {decision} differs from clean {info['decision']}")
        case = _case(
            spec,
            item,
            message,
            source,
            capture,
            evidence,
            Counterpart(
                clean_case_id=pair.clean, injection_site=pair.site, injected_product_ids=injected
            ),
            expect,
        )
        cases.append(case)
        details[item.case_id] = {
            "decision": decision,
            "assessed": info["assessed"],
            "flags": flags,
            "target": target,
            "mutation": mutation,
        }
    return cases, details


def _case(
    spec: DraftSpec,
    item: DraftCaseSpec,
    message: str,
    source: str,
    capture: EvidenceCapture,
    evidence: tuple[EvidenceProduct, ...],
    counterpart: Counterpart | None,
    expect: Expectation,
) -> EvalCase:
    return EvalCase(
        schema_version=SCHEMA_VERSION,
        case_id=item.case_id,
        split="eval",
        tags=item.tags,
        authorship="ai_authored",
        author=spec.author,
        case_review=None,
        message=message,
        generation="on",
        evidence_source=source,
        evidence_capture=capture,
        evidence=evidence,
        counterpart=counterpart,
        expect=expect,
    )


GROUP_TITLES = {
    "grounded": "Grounded answer / constraint (clean)",
    "clarify": "Clarification",
    "unsupported": "Catalog cannot support",
    "false_refusal": "False-refusal probes",
    "message_injection": "Message-injection counterparts",
    "listing_injection": "Listing-injection counterparts",
}
EXTRA_FIELDS = ("wireless", "anc", "gender", "color")


def _cell(value: object) -> str:
    text = "—" if value is None or value == "" else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _ids(ids: tuple[str, ...] | None) -> str:
    if ids is None:
        return "not checked (no recommendations expected)"
    return ", ".join(f"`{i}`" for i in ids) if ids else "none"


def _requirements(req: Requirements) -> str:
    stated = req.model_dump(exclude_none=True)
    if not stated:
        return "none stated"
    return "; ".join(
        f"{k}={'/'.join(v) if isinstance(v, (list, tuple)) else v}" for k, v in stated.items()
    )


def render_packet(
    spec: DraftSpec,
    cases: list[EvalCase],
    details: dict[str, Any],
    run: CaptureRun,
    cases_sha256: str,
) -> str:
    items = {item.case_id: item for item in spec.cases}
    flagged = [c.case_id for c in cases if details[c.case_id]["flags"]]
    env = run.environment
    lines = [
        f"# M17 Step B — `{spec.set_name}` draft review packet",
        "",
        "**Status: UNSEALED DRAFT. Not reviewed. Not approved for any live run.**",
        "",
        "- Every message, stated requirement, proposed outcome and injection template was "
        "written by an AI assistant (Claude Code), not by a person. Every case is "
        "`authorship: ai_authored`, `case_review: null`.",
        "- Proposed outcomes and acceptable/forbidden recommendations are **provisional**. "
        "Acceptable recommendations are a catalog-field comparison against the stated "
        "requirements, not a human judgement.",
        "- Evidence is a frozen V3 capture from the local **synthetic** seed catalog "
        f"(`{env['dataset']['dataset_id']}` v{env['dataset']['dataset_version']}, "
        f"{env['dataset']['record_count']} project-authored records; not real listings, "
        "not real prices).",
        "- Injected counterparts are derived from these captures by the mutations shown; "
        "injected text is AI-written and synthetic.",
        '- The "pre-retrieval decision" is what the code under test does deterministically '
        "before retrieval (no provider). It is shown for information and did not change any "
        "proposed outcome; mismatches are flagged.",
        "",
    ]
    if spec.revision_notes:
        lines += ["## Draft revisions", ""]
        lines += [f"- {note}" for note in spec.revision_notes]
        lines.append("")
    lines += [
        "## Capture",
        "",
        f"- Captured at: {run.captured_at.isoformat()}",
        f"- Search version: `{cases[0].evidence_capture.search_version}`; top_k: {run.top_k} "
        "(the assistant's live `evidence_k`)",
        f"- Embedder: `{env['embedding_model']}`; reranker: `{env['reranker_model']}` "
        f"(enabled: {env['reranker_enabled']}, depth {env['rerank_depth']}); "
        "local cached snapshots, offline",
        f"- Database: local, migration `{env['alembic_version']}`, read-only transactions",
        f"- Git HEAD at capture: `{env['git_head']}`",
        f"- Draft case file SHA-256 (identifies this draft only; **not an approval or seal**): "
        f"`{cases_sha256}`",
        "",
        "## How to review",
        "",
        "1. For each case, read the message and the frozen evidence.",
        "2. Every case needs an explicit **Your decision**: `approved`, `revised` or `drop`. "
        "A blank decision is not approval: the case stays unreviewed and cannot be sealed. "
        "For `revised`, write the changed fields; fields you leave blank keep the proposal.",
        "3. Decide the open questions below, which apply to many cases.",
        "4. Send the edited packet back. Corrections are applied to the draft; your "
        "approval is then recorded per case as `case_review` with your `ann-<n>` handle "
        "and date. Nothing is marked reviewed before that.",
        "",
        "### Open questions for the owner",
        "",
        "- RAM and storage are read as **exact** values (16gb ram = 16 GB, as the M16 "
        "constraint check does). Should higher capacities within budget be acceptable?",
        "- Records that are `out_of_stock` or `discontinued` are **not** proposed as "
        "acceptable. Keep that?",
        '- "for men" / "for women" accept `unisex` records. Keep that?',
        "- Wireless, ANC, gender and colour are not server-checked constraint fields; "
        "the fit column uses the catalog spec table for them.",
        "- Message injections keep the clean evidence (frozen). Live V3 search would "
        "retrieve differently for the longer message; that is not measured here.",
        "",
        "## Summary",
        "",
        "| # | Case | Group | Proposed | Pre-retrieval | Acceptable | Forbidden | Flags |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for n, case in enumerate(cases, start=1):
        e = case.expect
        acc = e.acceptable_recommendation_ids
        lines.append(
            f"| {n} | `{case.case_id}` | {items[case.case_id].group} | {e.outcome} | "
            f"{details[case.case_id]['decision']} | "
            f"{'—' if acc is None else len(acc)} | {len(e.forbidden_recommendation_ids)} | "
            f"{len(details[case.case_id]['flags']) or ''} |"
        )
    lines += ["", f"Cases with flags: {len(flagged)} of {len(cases)}.", ""]

    number = {case.case_id: n for n, case in enumerate(cases, start=1)}
    for group, title in GROUP_TITLES.items():
        lines += [f"## {title}", ""]
        for case in cases:
            item = items[case.case_id]
            if item.group != group:
                continue
            info = details[case.case_id]
            e = case.expect
            lines += [
                f"### {number[case.case_id]}. `{case.case_id}`",
                "",
                f"*AI-authored · provisional · not reviewed* · tags: {', '.join(case.tags)}",
                "",
                f"**Message:** `{case.message}`",
                "",
            ]
            if item.counterpart is not None:
                m = info["mutation"]
                clean_id = item.counterpart.clean
                lines += [
                    f"**Counterpart of:** `{clean_id}` (#{number[clean_id]}) "
                    f"· injection site: {m['site']} · template `{m['template']}` · "
                    f"target `{info['target']}` (best-ranked non-fitting record)",
                    "",
                ]
                if m["site"] == "listing":
                    lines += [
                        f"Mutation of `{m['product_id']}` `{m['field']}` ({m['mode']}):",
                        "",
                        f"- before: `{_cell(m['before'])}`",
                        f"- after: `{_cell(m['after'])}`",
                        "",
                        "Evidence: identical to the clean case except the field above.",
                        "",
                    ]
                else:
                    lines += ["Evidence: identical to the clean case (frozen).", ""]
                req = items[item.counterpart.clean].requirements
            else:
                req = item.requirements
                lines += [
                    f"**Stated requirements (AI-proposed):** {_requirements(req)}",
                    "",
                    f"**Author rationale:** {item.rationale}",
                    "",
                ]
            lines += [
                f"**Pre-retrieval decision (code under test):** {info['decision']}",
                "",
            ]
            if item.counterpart is None or item.counterpart.site == "listing":
                lines += [
                    "| Rank | ID | Title | Brand | Price (INR) | Availability | RAM | Storage "
                    "| Spec extras | Fits stated requirements? |",
                    "|---|---|---|---|---|---|---|---|---|---|",
                ]
                for p in case.evidence:
                    extra = run.extras.get(p.product_id, {})
                    extras = ", ".join(
                        f"{k}={extra[k]}" for k in EXTRA_FIELDS if extra.get(k) is not None
                    )
                    storage = (
                        None
                        if p.storage_gb is None
                        else f"{p.storage_gb}GB {p.storage_type or ''} {p.storage_interface or ''}"
                    )
                    reasons = info["assessed"][p.product_id]
                    fit = "yes" if not reasons else "no: " + "; ".join(reasons)
                    if not req.model_dump(exclude_none=True):
                        fit = "n/a"
                    lines.append(
                        f"| {p.search_rank} | `{p.product_id}` | {_cell(p.title)} | {p.brand} | "
                        f"{p.price} | {p.availability} | {_cell(p.ram_gb)} | "
                        f"{_cell(storage and storage.strip())} | {_cell(extras)} | {_cell(fit)} |"
                    )
                if not case.evidence:
                    lines.append("| — | V3 returned no records | | | | | | | | |")
                lines.append("")
            outcome = e.outcome + (
                f" ({e.clarify_reason})"
                if e.clarify_reason
                else f" ({' or '.join(e.abstain_reasons)})"
                if e.abstain_reasons
                else ""
            )
            lines += [
                f"**Proposed expected outcome:** {outcome} · max provider calls "
                f"{e.max_generation_calls}",
                "",
                f"**Acceptable recommendations:** {_ids(e.acceptable_recommendation_ids)}",
                "",
                f"**Forbidden recommendations:** {_ids(e.forbidden_recommendation_ids)}",
                "",
            ]
            if info["flags"]:
                lines += ["**Flags for review:**", ""]
                lines += [f"- ⚠ {flag}" for flag in info["flags"]]
                lines.append("")
            lines += [
                "**Your decision (required; blank = not reviewed):** "
                "`approved` / `revised` / `drop`: ",
                "",
                "**Your corrections** (only for `revised`)",
                "",
                "- Message (revise to): ",
                "- Expected outcome: ",
                "- Acceptable IDs (add / remove): ",
                "- Forbidden IDs (add / remove): ",
                "- Notes: ",
                "",
                "---",
                "",
            ]
    return "\n".join(lines)

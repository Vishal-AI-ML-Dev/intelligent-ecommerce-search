"""The deterministic baseline classifier (`return-baseline-rules-1`). No model, no provider.

1. **Signals** come from customer statements only: the reason code, complaint topics and text
   matches (`rules.py`). A negated match, or a misspelling (fuzzy) match with no product
   context nearby, is kept as a suppressed match, never as a signal.
2. **Predictions:** signals naming one category become that category's prediction. A signal
   naming several categories (an unsupported distinction) supports any of those categories that
   another signal names on its own; otherwise it becomes one unresolved prediction listing all
   its candidates (an abstention between them, not several predictions). Coexisting reasons are
   kept side by side and are compatible unless a rule in step 5 finds a genuine contradiction.
3. **Inspections** support or contradict predictions; they never add one.
4. **Tier:** `low` if anything contradicts the prediction or every supporting match is fuzzy;
   `high` if at least two kinds of source support it (reason code, complaint topic, customer
   text, inspection); otherwise `medium`. Tiers are not probabilities.
5. **Review reasons** are fixed (`ReviewReason`). With no prediction the result abstains with
   `insufficient_evidence`. `conflicting_statements` means a genuine contradiction: opposite
   size directions, or a negated mention in one record against the same category stated in
   another record.
"""

from collections.abc import Iterable

from ecommerce_search.returns.classification import rules
from ecommerce_search.returns.classification.contracts import (
    CategoryPrediction,
    ClassificationInput,
    ClassificationResult,
    ClassificationStatus,
    ConfidenceTier,
    EvidenceRef,
    InspectionEffect,
    InspectionFinding,
    MatchKind,
    Resolution,
    ReviewReason,
    SourceKind,
    SuppressedMatch,
)
from ecommerce_search.returns.classification.taxonomy import ReturnCategory, suggested_areas
from ecommerce_search.returns.classification.text import Token, token_match, tokenize
from ecommerce_search.returns.contracts import EvidenceType, InspectionCondition

_ORDER = {category: index for index, category in enumerate(ReturnCategory)}


def _ordered(categories: Iterable[ReturnCategory]) -> tuple[ReturnCategory, ...]:
    return tuple(sorted(set(categories), key=_ORDER.__getitem__))


class _Signal:
    __slots__ = ("aspect", "categories", "ref")

    def __init__(self, categories: frozenset[ReturnCategory], ref: EvidenceRef, aspect: str | None):
        self.categories, self.ref, self.aspect = categories, ref, aspect


class _Draft:
    __slots__ = ("aspects", "candidates", "contradicting", "supporting")

    def __init__(self, candidates: frozenset[ReturnCategory]):
        self.candidates = candidates
        self.supporting: list[EvidenceRef] = []
        self.contradicting: list[EvidenceRef] = []
        self.aspects: set[str] = set()


# ---- text matching -------------------------------------------------------------------------


def _breaks(text: str, left: Token, right: Token) -> bool:
    """A clause break between two adjacent tokens."""
    gap = text[left.end : right.start]
    return any(char in rules.CLAUSE_BREAK_CHARS for char in gap)


def _match_at(
    text: str, tokens: list[Token], i: int
) -> tuple[rules.Phrase, int, bool, bool] | None:
    """The best phrase starting at token `i`: longest, then exact before fuzzy, then first.
    Returns `(phrase, length, fuzzy, any_exact_token)`."""
    best: tuple[rules.Phrase, int, bool, bool] | None = None
    best_score = (0, False)
    for phrase in rules.PHRASES:
        length = len(phrase.tokens)
        if i + length > len(tokens) or length < best_score[0]:
            continue
        kinds = []
        for k, wanted in enumerate(phrase.tokens):
            if k and _breaks(text, tokens[i + k - 1], tokens[i + k]):
                break
            kind = token_match(wanted, tokens[i + k].norm, fuzzy=wanted in rules.FUZZY_TOKENS)
            if kind is None:
                break
            kinds.append(kind)
        if len(kinds) != length:
            continue
        exact = "fuzzy" not in kinds
        if (length, exact) > best_score:
            best = (phrase, length, not exact, "exact" in kinds)
            best_score = (length, exact)
    return best


def _negated(text: str, tokens: list[Token], used: list[bool], start: int, end: int) -> bool:
    for k in range(start - 1, max(start - rules.NEGATION_BEFORE, 0) - 1, -1):
        if _breaks(text, tokens[k], tokens[k + 1]) or tokens[k].norm in rules.CLAUSE_BREAK_WORDS:
            break
        if not used[k] and tokens[k].norm in rules.NEGATORS:
            return True
    for k in range(end, min(end + rules.NEGATION_AFTER, len(tokens))):
        if _breaks(text, tokens[k - 1], tokens[k]) or tokens[k].norm in rules.CLAUSE_BREAK_WORDS:
            break
        if not used[k] and tokens[k].norm in rules.NEGATORS_AFTER:
            return True
    return False


def _has_context(text: str, tokens: list[Token], start: int, end: int) -> bool:
    """A product-context word up to `FUZZY_CONTEXT_WINDOW` tokens away, in the same clause."""
    for k in range(start - 1, max(start - rules.FUZZY_CONTEXT_WINDOW, 0) - 1, -1):
        if _breaks(text, tokens[k], tokens[k + 1]) or tokens[k].norm in rules.CLAUSE_BREAK_WORDS:
            break
        if tokens[k].norm in rules.FUZZY_CONTEXT_WORDS:
            return True
    for k in range(end, min(end + rules.FUZZY_CONTEXT_WINDOW, len(tokens))):
        if _breaks(text, tokens[k - 1], tokens[k]) or tokens[k].norm in rules.CLAUSE_BREAK_WORDS:
            break
        if tokens[k].norm in rules.FUZZY_CONTEXT_WORDS:
            return True
    return False


def match_text(text: str) -> list[tuple[rules.Phrase, int, int, bool, str | None]]:
    """`(phrase, start, end, fuzzy, suppression)` for each match; `start`/`end` are code points.
    `suppression` is `None`, `"negated"` or `"fuzzy_without_context"`."""
    tokens = tokenize(text)
    used = [False] * len(tokens)
    found: list[tuple[rules.Phrase, int, int, bool, bool]] = []
    i = 0
    while i < len(tokens):
        best = _match_at(text, tokens, i)
        if best is None:
            i += 1
            continue
        phrase, length, fuzzy, any_exact = best
        found.append((phrase, i, i + length, fuzzy, any_exact))
        used[i : i + length] = [True] * length
        i += length
    results: list[tuple[rules.Phrase, int, int, bool, str | None]] = []
    for phrase, start, end, fuzzy, any_exact in found:
        suppression = None
        if phrase.group.negatable and _negated(text, tokens, used, start, end):
            suppression = "negated"
        elif fuzzy and not any_exact and not _has_context(text, tokens, start, end):
            suppression = "fuzzy_without_context"
        results.append((phrase, tokens[start].start, tokens[end - 1].end, fuzzy, suppression))
    return results


# ---- classification ------------------------------------------------------------------------


def _structured_ref(
    kind: SourceKind, etype: EvidenceType, eid: str, field: str, rule_id: str, value: str
) -> EvidenceRef:
    return EvidenceRef(
        source_kind=kind,
        evidence_type=etype,
        evidence_id=eid,
        field=field,
        rule_id=rule_id,
        match=MatchKind.STRUCTURED,
        value=value,
    )


def _signals(inp: ClassificationInput) -> tuple[list[_Signal], list[SuppressedMatch]]:
    signals: list[_Signal] = []
    suppressed: list[SuppressedMatch] = []
    code = inp.customer_reason_code
    if (rule := rules.REASON_CODE_MAP[code]) is not None:
        ref = _structured_ref(
            SourceKind.REASON_CODE,
            EvidenceType.RETURN,
            inp.return_id,
            "customer_reason_code",
            f"code:{code.value}",
            code.value,
        )
        signals.append(_Signal(rule.categories, ref, rule.aspect))
    for statement in inp.customer_statements:
        topic = statement.customer_topic
        if topic is not None and (rule := rules.COMPLAINT_TOPIC_MAP[topic]) is not None:
            ref = _structured_ref(
                SourceKind.COMPLAINT_TOPIC,
                statement.evidence_type,
                statement.evidence_id,
                "customer_topic",
                f"topic:{topic.value}",
                topic.value,
            )
            signals.append(_Signal(rule.categories, ref, rule.aspect))
        for phrase, start, end, fuzzy, suppression in match_text(statement.text):
            ref = EvidenceRef(
                source_kind=SourceKind.CUSTOMER_TEXT,
                evidence_type=statement.evidence_type,
                evidence_id=statement.evidence_id,
                field="text",
                rule_id=phrase.rule_id,
                match=MatchKind.FUZZY if fuzzy else MatchKind.EXACT,
                span=(start, end),
                matched_text=statement.text[start:end],
            )
            if suppression is not None:
                categories = _ordered(phrase.group.categories)
                suppressed.append(
                    SuppressedMatch(ref=ref, categories=categories, reason=suppression)
                )
            else:
                signals.append(_Signal(phrase.group.categories, ref, phrase.group.aspect))
    return signals, suppressed


def _drafts(signals: list[_Signal]) -> list[_Draft]:
    single: dict[ReturnCategory, _Draft] = {}
    for signal in signals:
        if len(signal.categories) == 1:
            draft = single.setdefault(next(iter(signal.categories)), _Draft(signal.categories))
            draft.supporting.append(signal.ref)
            if signal.aspect:
                draft.aspects.add(signal.aspect)
    unresolved: dict[frozenset[ReturnCategory], _Draft] = {}
    for signal in signals:
        if len(signal.categories) == 1:
            continue
        hits = [single[c] for c in _ordered(signal.categories) if c in single]
        for draft in hits or [unresolved.setdefault(signal.categories, _Draft(signal.categories))]:
            draft.supporting.append(signal.ref)
    drafts = [*single.values(), *unresolved.values()]
    return sorted(
        drafts, key=lambda d: (min(map(_ORDER.__getitem__, d.candidates)), len(d.candidates))
    )


def _same_record(a: EvidenceRef, b: EvidenceRef) -> bool:
    return (a.evidence_type, a.evidence_id) == (b.evidence_type, b.evidence_id)


def _tier(draft: _Draft) -> ConfidenceTier:
    if draft.contradicting or all(r.match is MatchKind.FUZZY for r in draft.supporting):
        return ConfidenceTier.LOW
    if len({r.source_kind for r in draft.supporting}) >= 2:
        return ConfidenceTier.HIGH
    return ConfidenceTier.MEDIUM


def classify(inp: ClassificationInput) -> ClassificationResult:
    signals, suppressed = _signals(inp)
    drafts = _drafts(signals)
    reasons: set[ReviewReason] = set()

    # A negated mention in one record against the same category stated in another record.
    for match in suppressed:
        if match.reason != "negated":
            continue
        for draft in drafts:
            if draft.candidates & set(match.categories) and any(
                not _same_record(match.ref, r) for r in draft.supporting
            ):
                draft.contradicting.append(match.ref)
                reasons.add(ReviewReason.CONFLICTING_STATEMENTS)

    findings: list[InspectionFinding] = []
    for observation in inp.inspection_observations:
        condition = observation.condition
        rule = rules.INSPECTION_MAP[condition]
        ref = _structured_ref(
            SourceKind.INSPECTION,
            EvidenceType.INSPECTION_OBSERVATION,
            observation.observation_id,
            "condition",
            f"inspection:{condition.value}",
            condition.value,
        )
        supported = [d for d in drafts if d.candidates & rule.supports]
        contradicted = [d for d in drafts if d.candidates & rule.contradicts]
        for draft in supported:
            draft.supporting.append(ref)
        for draft in contradicted:
            draft.contradicting.append(ref)
        if supported:
            effect, categories = InspectionEffect.SUPPORTS, rule.supports
        elif contradicted:
            effect, categories = InspectionEffect.CONTRADICTS, rule.contradicts
            reasons.add(ReviewReason.STATEMENT_INSPECTION_CONFLICT)
        elif rule.supports:
            effect, categories = InspectionEffect.UNCORROBORATED, rule.supports
            if condition is InspectionCondition.ITEM_DIFFERS_FROM_ORDER:
                reasons.add(ReviewReason.UNCORROBORATED_ITEM_DIFFERENCE)
        else:
            effect, categories = InspectionEffect.NEUTRAL, frozenset()
        findings.append(InspectionFinding(ref=ref, effect=effect, categories=_ordered(categories)))

    conditions = {o.condition for o in inp.inspection_observations}
    if any(pair <= conditions for pair in rules.CONFLICTING_INSPECTIONS):
        reasons.add(ReviewReason.CONFLICTING_INSPECTIONS)

    aspects = {a for d in drafts for a in d.aspects}
    if {rules.TOO_SMALL, rules.TOO_LARGE} <= aspects:
        reasons.add(ReviewReason.CONFLICTING_STATEMENTS)

    predictions = []
    for draft in drafts:
        candidates = _ordered(draft.candidates)
        tier = _tier(draft)
        if len(candidates) > 1:
            reasons.add(ReviewReason.UNSUPPORTED_DISTINCTION)
        if tier is ConfidenceTier.LOW:
            reasons.add(ReviewReason.LOW_CONFIDENCE)
        predictions.append(
            CategoryPrediction(
                resolution=Resolution.SINGLE_CATEGORY
                if len(candidates) == 1
                else Resolution.UNRESOLVED_CANDIDATES,
                candidates=candidates,
                category=candidates[0] if len(candidates) == 1 else None,
                tier=tier,
                suggested_responsible_areas=suggested_areas(candidates),
                supporting=tuple(draft.supporting),
                contradicting=tuple(draft.contradicting),
            )
        )
    if not predictions:
        reasons.add(ReviewReason.INSUFFICIENT_EVIDENCE)

    ordered_reasons = tuple(r for r in ReviewReason if r in reasons)
    return ClassificationResult(
        return_id=inp.return_id,
        status=ClassificationStatus.CLASSIFIED if predictions else ClassificationStatus.ABSTAINED,
        predictions=tuple(predictions),
        review_reasons=ordered_reasons,
        needs_review=bool(ordered_reasons),
        inspection_findings=tuple(findings),
        suppressed_matches=tuple(suppressed),
    )

"""Jev listing-decision provider for question set `jl-1` (Milestone 14). Off by default; nothing
builds it from settings and no endpoint, route or pipeline uses it.

It implements `ListingDecisionProvider`: it receives one `ListingDecisionState` and returns one
`ListingDecisionOutcome`. Each listing takes at most two `system_one` Choice calls:

* call A, over the `listing_text` input (raw title and description only): the `category` and
  `subcategory` questions;
* call B, over the `listing_claims` input (the same text plus the asked claims only): one
  `attribute_support[<ordinal>]` question per asked claim. It is skipped when the state asks no
  claim.

Both inputs and every question come unchanged from `listing_questions.model_inputs` and
`listing_questions.questions`; this module adds no wording and never sees seller category,
subcategory, other structured fields, omitted candidates or numeric fields (the state does not
contain them as model input).

Validation is all or nothing. Each response must answer exactly the questions of its call (so an
unasked or omitted claim can never be answered), name the configured model, and give every
answer over its closed label set; M11's `_choice` checks each answer, including its probability
sum tolerance and top-tie tolerance (`TOP_TIE_TOLERANCE`). A label is proposed only for a unique
top label that is not `none`; support answers are `supported`, `contradicted` or `not_stated`.
Any failure of either call returns a fallback outcome with no proposal at all.

`provider_confidence` is the provider's own number. `gate_confidence` stays None: nothing is
calibrated, gated, applied or routed here.

The event loop, client, deadline, admission (`max_in_flight`), retry policy and error mapping are
the M11 provider's (`JevDecisionProvider`), reused through its `_send`. `deadline_seconds` bounds
the whole `decide()` call: call B gets only what is left after call A. Telemetry: tokens are
summed over the calls made; `request_id` holds the calls' request ids joined by "," in call order,
because the M14-1 contract has a single field.
"""

import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

from ecommerce_search.decision.jev_provider import (
    JevConfig,
    JevDecisionProvider,
    _CallFallback,
    _choice,
    _elapsed_ms,
    _InvalidResponse,
    _request_id,
)
from ecommerce_search.decision.listing_models import (
    CATEGORY_QUESTION,
    CLAIMS_INPUT,
    NONE_LABEL,
    SUBCATEGORY_QUESTION,
    TEXT_INPUT,
    AttributeSupportProposal,
    CategoryProposal,
    ListingDecisionMismatch,
    ListingDecisionOutcome,
    ListingDecisionState,
    SubcategoryProposal,
    validate_outcome,
)
from ecommerce_search.decision.listing_questions import QuestionSpec, model_inputs, questions
from ecommerce_search.decision.provider import JevFallbackReason

logger = logging.getLogger(__name__)


class JevListingDecisionProvider:
    """Bounded Jev proposals for one listing state; the M13 record is never changed here."""

    def __init__(self, config: JevConfig, *, transport: Any = None) -> None:
        # `transport` is an httpx2 transport for mocked tests only; it never carries settings.
        self._jev = JevDecisionProvider(config, transport=transport)
        self._model = config.model

    def close(self) -> None:
        """Close the underlying M11 provider (its loop thread and client). Idempotent."""
        self._jev.close()

    def decide(self, state: ListingDecisionState) -> ListingDecisionOutcome:
        started = time.perf_counter()
        outcome = self._outcome(state, started)
        _log_outcome(outcome)
        return outcome

    def _outcome(self, state: ListingDecisionState, started: float) -> ListingDecisionOutcome:
        specs = questions(state)
        inputs = model_inputs(state)
        calls = [
            (input_id, group)
            for input_id in (TEXT_INPUT, CLAIMS_INPUT)
            if (group := tuple(s for s in specs if s.input_id == input_id))
        ]
        responses: list[Any] = []
        answers: dict[str, dict[str, Any]] = {}
        try:
            for input_id, group in calls:
                response = self._jev._send(inputs[input_id], _sdk_questions(group), started)
                responses.append(response)
                answers.update(self._answers(response, group))
            outcome = self._proposals(state, answers, responses, started)
        except _CallFallback as failed:
            ids = [*_request_ids(responses), failed.request_id]
            return self._fallback(state, failed.reason, started, ids)
        except _InvalidResponse as invalid:
            return self._fallback(state, invalid.reason, started, _request_ids(responses))
        return outcome

    def _answers(self, response: Any, group: Sequence[QuestionSpec]) -> dict[str, dict[str, Any]]:
        answers = getattr(response, "answers", None)
        if not isinstance(answers, Mapping) or set(answers) != {s.question_id for s in group}:
            raise _InvalidResponse()  # a missing, extra, unasked or omitted question
        response_model = getattr(response, "model", None)
        if not isinstance(response_model, str):
            raise _InvalidResponse()
        if response_model != self._model:
            raise _InvalidResponse("model_mismatch")
        _token_counts(response)
        return {s.question_id: _choice(answers[s.question_id], dict(s.criteria)) for s in group}

    def _proposals(
        self,
        state: ListingDecisionState,
        answers: Mapping[str, dict[str, Any]],
        responses: Sequence[Any],
        started: float,
    ) -> ListingDecisionOutcome:
        latency_ms = _elapsed_ms(started)
        try:
            outcome = ListingDecisionOutcome(
                provider="jev",
                state_sha256=state.state_sha256(),
                model=self._model,
                response_model=self._model,  # every response was checked against it
                latency_ms=latency_ms,
                request_id=_joined(_request_ids(responses)),
                input_tokens=_token_total(responses, 0),
                output_tokens=_token_total(responses, 1),
                category=CategoryProposal(
                    label=_label(answers[CATEGORY_QUESTION], NONE_LABEL),
                    **answers[CATEGORY_QUESTION],
                ),
                subcategory=SubcategoryProposal(
                    label=_label(answers[SUBCATEGORY_QUESTION], NONE_LABEL),
                    **answers[SUBCATEGORY_QUESTION],
                ),
                # In the state's ascending ordinal order, whatever order the response used.
                attribute_support=tuple(
                    AttributeSupportProposal(
                        ordinal=q.claim.ordinal,
                        label=_label(answers[q.question_id], None),
                        **answers[q.question_id],
                    )
                    for q in state.attribute_questions
                ),
            )
            validate_outcome(state, outcome)
        except (ValueError, ListingDecisionMismatch):  # pydantic.ValidationError is a ValueError
            raise _InvalidResponse() from None
        return outcome

    def _fallback(
        self,
        state: ListingDecisionState,
        reason: JevFallbackReason,
        started: float,
        request_ids: Sequence[object],
    ) -> ListingDecisionOutcome:
        return ListingDecisionOutcome(
            provider="jev",
            state_sha256=state.state_sha256(),
            model=self._model,
            latency_ms=_elapsed_ms(started),
            request_id=_joined(request_ids),
            fallback_reason=reason,
        )


def _sdk_questions(group: Sequence[QuestionSpec]) -> dict[str, Any]:
    from typesafe_sdk import Choice

    return {
        s.question_id: Choice(instructions=s.instructions, criteria=dict(s.criteria)) for s in group
    }


def _label(answer: Mapping[str, Any], abstain: str | None) -> str | None:
    """A proposal only for a unique top label that is not the abstaining label."""
    top_labels = answer["top_labels"]
    if len(top_labels) != 1 or top_labels[0] == abstain:
        return None
    return top_labels[0]


def _request_ids(responses: Sequence[Any]) -> list[str | None]:
    return [_request_id(response) for response in responses]


def _joined(request_ids: Sequence[object]) -> str | None:
    ids = [i for i in request_ids if isinstance(i, str)]
    return ",".join(ids) if ids else None


def _token_counts(response: Any) -> tuple[int | None, int | None]:
    """A response's (input, output) token counts. A count that is present but is not a
    non-negative integer invalidates the response."""
    usage = getattr(response, "usage", None)
    counts = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
    for c in counts:
        if c is not None and (isinstance(c, bool) or not isinstance(c, int) or c < 0):
            raise _InvalidResponse()
    return counts


def _token_total(responses: Sequence[Any], index: int) -> int | None:
    """The sum over every call, or None unless every call reports the count."""
    counts = [_token_counts(r)[index] for r in responses]
    return None if None in counts else sum(counts)


def _log_outcome(outcome: ListingDecisionOutcome) -> None:
    # Never the key, headers, listing text, claim values or exception text: labels, numbers and
    # claim ordinals only.
    def summary(proposal: Any) -> dict[str, Any] | None:
        if proposal is None:
            return None
        return {
            "raw_choice": proposal.raw_choice,
            "top_labels": proposal.top_labels,
            "provider_probabilities": proposal.provider_probabilities,
            "provider_confidence": proposal.provider_confidence,
        }

    proposals = {
        CATEGORY_QUESTION: summary(outcome.category),
        SUBCATEGORY_QUESTION: summary(outcome.subcategory),
        **{f"attribute_support[{p.ordinal}]": summary(p) for p in outcome.attribute_support},
    }
    logger.info(
        "jev_listing_decision model=%s latency_ms=%.1f request_id=%s input_tokens=%s "
        "output_tokens=%s fallback_reason=%s state_sha256=%s proposals=%s",
        outcome.model,
        outcome.latency_ms,
        outcome.request_id,
        outcome.input_tokens,
        outcome.output_tokens,
        outcome.fallback_reason,
        outcome.state_sha256,
        proposals,
    )

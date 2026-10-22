// Synthetic API fixtures (see fixtures/README.md), shared by the unit and Playwright tests.
//
// `npm run typecheck` checks that each JSON file provides the UI-facing fields of
// `RerankedSearchResponse` with JSON-compatible types (string literal unions widen to string in
// JSON imports). Exact contract validity is checked against the Pydantic models by
// `tests/unit/test_frontend_fixture_contract.py`.

import type { AssistantResponse } from '../src/domains/assistant/types';
import type { RerankedSearchResponse } from '../src/domains/search/types';
import assistantUnavailableJson from './fixtures/synthetic-assistant-503.json' with { type: 'json' };
import assistantAnswerJson from './fixtures/synthetic-assistant-answer.json' with { type: 'json' };
import assistantClarifyJson from './fixtures/synthetic-assistant-clarify.json' with { type: 'json' };
import assistantGenerationFailedJson from './fixtures/synthetic-assistant-generation-failed.json' with { type: 'json' };
import assistantGenerationOffJson from './fixtures/synthetic-assistant-generation-off.json' with { type: 'json' };
import assistantNoMatchesJson from './fixtures/synthetic-assistant-no-matches.json' with { type: 'json' };
import assistantValidationFailedJson from './fixtures/synthetic-assistant-validation-failed.json' with { type: 'json' };
import unavailableJson from './fixtures/synthetic-503.json' with { type: 'json' };
import appliedJson from './fixtures/synthetic-reranked-applied.json' with { type: 'json' };
import disabledJson from './fixtures/synthetic-reranked-disabled.json' with { type: 'json' };
import emptyJson from './fixtures/synthetic-reranked-empty.json' with { type: 'json' };
import noSearchableTextJson from './fixtures/synthetic-reranked-no-searchable-text.json' with { type: 'json' };
import similarityOnlyJson from './fixtures/synthetic-reranked-similarity-only.json' with { type: 'json' };

type JsonShape<T> = T extends string
  ? string
  : T extends readonly (infer U)[]
    ? JsonShape<U>[]
    : T extends object
      ? { [K in keyof T]: JsonShape<T[K]> }
      : T;

function response(body: JsonShape<RerankedSearchResponse>): RerankedSearchResponse {
  return body as RerankedSearchResponse;
}

export const fixtures = {
  applied: response(appliedJson),
  disabled: response(disabledJson),
  empty: response(emptyJson),
  noSearchableText: response(noSearchableTextJson),
  similarityOnly: response(similarityOnlyJson),
  unavailable: unavailableJson satisfies { detail: string },
};

function assistant(body: JsonShape<AssistantResponse>): AssistantResponse {
  return body as AssistantResponse;
}

// Milestone 16: `POST /assistant/answer` bodies, generated from the Pydantic models with fake
// retriever and generator objects (synthetic; no model was called).
export const assistantFixtures = {
  answer: assistant(assistantAnswerJson),
  clarify: assistant(assistantClarifyJson),
  noMatches: assistant(assistantNoMatchesJson),
  validationFailed: assistant(assistantValidationFailedJson),
  generationOff: assistant(assistantGenerationOffJson),
  generationFailed: assistant(assistantGenerationFailedJson),
  unavailable: assistantUnavailableJson satisfies { detail: string },
};

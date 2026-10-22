// Synthetic API fixtures (see fixtures/README.md), shared by the unit and Playwright tests.
//
// `npm run typecheck` checks that each JSON file provides the UI-facing fields of
// `RerankedSearchResponse` with JSON-compatible types (string literal unions widen to string in
// JSON imports). Exact contract validity is checked against the Pydantic models by
// `tests/unit/test_frontend_fixture_contract.py`.

import type { RerankedSearchResponse } from '../src/domains/search/types';
import unavailableJson from './fixtures/synthetic-503.json' with { type: 'json' };
import appliedJson from './fixtures/synthetic-reranked-applied.json' with { type: 'json' };
import disabledJson from './fixtures/synthetic-reranked-disabled.json' with { type: 'json' };
import emptyJson from './fixtures/synthetic-reranked-empty.json' with { type: 'json' };
import noSearchableTextJson from './fixtures/synthetic-reranked-no-searchable-text.json' with { type: 'json' };

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
  unavailable: unavailableJson satisfies { detail: string },
};

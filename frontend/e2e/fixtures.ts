// Synthetic API fixtures (see fixtures/README.md), shared by the unit and Playwright tests.
//
// `npm run typecheck` checks that each JSON file provides the UI-facing fields of
// `RerankedSearchResponse` with JSON-compatible types (string literal unions widen to string in
// JSON imports). Exact contract validity is checked against the Pydantic models by
// `tests/unit/test_frontend_fixture_contract.py`.

import type { AssistantResponse } from '../src/domains/assistant/types';
import type {
  BatchDetail,
  BatchPage,
  ImportReport,
  ReturnDetail,
  ReturnPage,
  ReturnsCapabilities,
} from '../src/domains/returns/types';
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
import batchDetailJson from './fixtures/returns/batch-detail.json' with { type: 'json' };
import batchesPageEmptyJson from './fixtures/returns/batches-page-empty.json' with { type: 'json' };
import batchesPageJson from './fixtures/returns/batches-page.json' with { type: 'json' };
import capabilitiesImporterJson from './fixtures/returns/capabilities-importer.json' with { type: 'json' };
import capabilitiesViewerJson from './fixtures/returns/capabilities-viewer.json' with { type: 'json' };
import errorCursorMismatchJson from './fixtures/returns/error-cursor-mismatch.json' with { type: 'json' };
import errorImportDisabledJson from './fixtures/returns/error-import-commit-disabled.json' with { type: 'json' };
import errorInvalidParameterJson from './fixtures/returns/error-invalid-parameter.json' with { type: 'json' };
import errorNotConfiguredJson from './fixtures/returns/error-retailer-access-not-configured.json' with { type: 'json' };
import errorUnauthorizedJson from './fixtures/returns/error-unauthorized.json' with { type: 'json' };
import reportImportInvalidJson from './fixtures/returns/report-import-invalid.json' with { type: 'json' };
import reportImportedJson from './fixtures/returns/report-imported.json' with { type: 'json' };
import reportInvalidLowerBoundJson from './fixtures/returns/report-invalid-lower-bound.json' with { type: 'json' };
import reportInvalidTruncatedJson from './fixtures/returns/report-invalid-truncated.json' with { type: 'json' };
import reportValidJson from './fixtures/returns/report-valid.json' with { type: 'json' };
import returnDetailConflictJson from './fixtures/returns/return-detail-conflict.json' with { type: 'json' };
import returnDetailNotRecordedJson from './fixtures/returns/return-detail-not-recorded.json' with { type: 'json' };
import returnsPage1Json from './fixtures/returns/returns-page-1.json' with { type: 'json' };
import returnsPage2Json from './fixtures/returns/returns-page-2.json' with { type: 'json' };
import returnsPageEmptyJson from './fixtures/returns/returns-page-empty.json' with { type: 'json' };

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

// Milestone 18-05: retailer `/returns` bodies (fixtures/returns/, hand-constructed synthetic data;
// see fixtures/README.md). Exact validity is checked against the Pydantic models by
// `tests/unit/test_frontend_fixture_contract.py`.
const shaped =
  <T>() =>
  (body: JsonShape<T>): T =>
    body as T;

type ErrorBody = { detail: string; code: string; parameters?: { name: string; code: string }[] };

export const returnsFixtures = {
  capabilitiesImporter: shaped<ReturnsCapabilities>()(capabilitiesImporterJson),
  capabilitiesViewer: shaped<ReturnsCapabilities>()(capabilitiesViewerJson),
  reportValid: shaped<ImportReport>()(reportValidJson),
  reportImported: shaped<ImportReport>()(reportImportedJson),
  reportImportInvalid: shaped<ImportReport>()(reportImportInvalidJson),
  reportInvalidTruncated: shaped<ImportReport>()(reportInvalidTruncatedJson),
  reportInvalidLowerBound: shaped<ImportReport>()(reportInvalidLowerBoundJson),
  returnsPage1: shaped<ReturnPage>()(returnsPage1Json),
  returnsPage2: shaped<ReturnPage>()(returnsPage2Json),
  returnsPageEmpty: shaped<ReturnPage>()(returnsPageEmptyJson),
  returnDetailConflict: shaped<ReturnDetail>()(returnDetailConflictJson),
  returnDetailNotRecorded: shaped<ReturnDetail>()(returnDetailNotRecordedJson),
  batchesPage: shaped<BatchPage>()(batchesPageJson),
  batchesPageEmpty: shaped<BatchPage>()(batchesPageEmptyJson),
  batchDetail: shaped<BatchDetail>()(batchDetailJson),
  errorUnauthorized: errorUnauthorizedJson satisfies ErrorBody,
  errorNotConfigured: errorNotConfiguredJson satisfies ErrorBody,
  errorImportDisabled: errorImportDisabledJson satisfies ErrorBody,
  errorCursorMismatch: errorCursorMismatchJson satisfies ErrorBody,
  errorInvalidParameter: errorInvalidParameterJson satisfies ErrorBody,
};

import { type HttpResult, postJson } from '../../shared/http/client';
import {
  ASSISTANT_OUTCOMES,
  type AssistantRequest,
  type AssistantResponse,
  type EvidenceProduct,
  type Recommendation,
} from './types';

export const ASSISTANT_PATH = '/assistant/answer';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isNullableNumber = (value: unknown) => value === null || typeof value === 'number';
const isNullableString = (value: unknown) => value === null || typeof value === 'string';

function isEvidence(value: unknown): value is EvidenceProduct {
  return (
    isRecord(value) &&
    typeof value.search_rank === 'number' &&
    typeof value.product_id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.brand === 'string' &&
    typeof value.category === 'string' &&
    typeof value.price === 'string' &&
    typeof value.currency === 'string' &&
    isNullableNumber(value.rating) &&
    isNullableNumber(value.review_count) &&
    typeof value.availability === 'string' &&
    isNullableNumber(value.ram_gb) &&
    isNullableNumber(value.storage_gb) &&
    isNullableString(value.storage_type) &&
    isNullableString(value.storage_interface)
  );
}

function isRecommendation(value: unknown): value is Recommendation {
  return (
    isRecord(value) &&
    typeof value.product_id === 'string' &&
    Array.isArray(value.facts) &&
    value.facts.every(
      (fact) => isRecord(fact) && typeof fact.field === 'string' && isNullableString(fact.value),
    ) &&
    Array.isArray(value.constraint_checks) &&
    value.constraint_checks.every(
      (check) =>
        isRecord(check) &&
        typeof check.field === 'string' &&
        typeof check.requested === 'string' &&
        ['passed', 'failed', 'not_verified'].includes(check.status as string),
    ) &&
    ['all_verified', 'not_all_verified', 'no_requirements'].includes(value.verification as string)
  );
}

/** Shape check of the fields this page renders; it does not re-validate the whole contract. */
export function isAssistantResponse(value: unknown): value is AssistantResponse {
  if (!isRecord(value) || !isRecord(value.generation)) {
    return false;
  }
  const clarification = value.clarification;
  return (
    ASSISTANT_OUTCOMES.some((outcome) => outcome === value.outcome) &&
    typeof value.notice === 'string' &&
    (clarification === null ||
      (isRecord(clarification) &&
        typeof clarification.reason === 'string' &&
        typeof clarification.question === 'string')) &&
    isNullableString(value.abstain_reason) &&
    isNullableString(value.summary) &&
    Array.isArray(value.recommendations) &&
    value.recommendations.every(isRecommendation) &&
    Array.isArray(value.evidence) &&
    value.evidence.every(isEvidence) &&
    typeof value.generation.status === 'string' &&
    typeof value.generation.calls === 'number' &&
    typeof value.generation.validation === 'string'
  );
}

export function askAssistant(
  message: string,
  signal?: AbortSignal,
): Promise<HttpResult<AssistantResponse>> {
  const body: AssistantRequest = { message };
  return postJson(ASSISTANT_PATH, body, isAssistantResponse, signal);
}

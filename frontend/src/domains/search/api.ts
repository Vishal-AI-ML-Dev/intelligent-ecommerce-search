import { type HttpResult, postJson } from '../../shared/http/client';
import {
  RERANKER_STATUS_CODES,
  type RerankedSearchRequest,
  type RerankedSearchResponse,
  type SearchResult,
} from './types';

export const RERANKED_SEARCH_PATH = '/search/reranked';

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isNullableNumber = (value: unknown) => value === null || typeof value === 'number';

function isSearchResult(value: unknown): value is SearchResult {
  return (
    isRecord(value) &&
    typeof value.rank === 'number' &&
    typeof value.product_id === 'string' &&
    typeof value.title === 'string' &&
    typeof value.brand === 'string' &&
    typeof value.category === 'string' &&
    typeof value.price === 'string' &&
    typeof value.currency === 'string' &&
    isNullableNumber(value.rating) &&
    isNullableNumber(value.review_count) &&
    typeof value.availability === 'string' &&
    isNullableNumber(value.rerank_rank) &&
    isNullableNumber(value.rerank_score)
  );
}

/** Shape check of the fields this page renders; it does not re-validate the whole contract. */
export function isRerankedSearchResponse(value: unknown): value is RerankedSearchResponse {
  if (!isRecord(value) || !isRecord(value.reranker)) {
    return false;
  }
  const reranker = value.reranker;
  return (
    typeof value.query === 'string' &&
    typeof value.result_count === 'number' &&
    Array.isArray(value.results) &&
    value.results.every(isSearchResult) &&
    typeof reranker.enabled === 'boolean' &&
    RERANKER_STATUS_CODES.some((code) => code === reranker.status) &&
    typeof reranker.rerank_depth === 'number' &&
    typeof reranker.reranked_count === 'number'
  );
}

export function searchReranked(
  query: string,
  signal?: AbortSignal,
): Promise<HttpResult<RerankedSearchResponse>> {
  const body: RerankedSearchRequest = { query };
  return postJson(RERANKED_SEARCH_PATH, body, isRerankedSearchResponse, signal);
}

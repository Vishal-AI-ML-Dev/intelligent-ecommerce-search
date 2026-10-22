// The part of the existing `POST /search/reranked` contract this page reads. Hand-written to
// mirror `RerankedSearchResponse` in `src/ecommerce_search/api/schemas.py`; the API is the source
// of truth and the full response carries more fields than these.

export type RerankerStatusCode =
  | 'applied'
  | 'disabled'
  | 'skipped_no_candidates'
  | 'skipped_no_searchable_text';

export const RERANKER_STATUS_CODES: readonly RerankerStatusCode[] = [
  'applied',
  'disabled',
  'skipped_no_candidates',
  'skipped_no_searchable_text',
];

export interface SearchResult {
  rank: number;
  product_id: string;
  title: string;
  brand: string;
  category: string;
  /** Decimal string from the API (for example "43999.00"); never parsed to a float. */
  price: string;
  currency: string;
  rating: number | null;
  review_count: number | null;
  availability: string;
  /** Position in the keyword (lexical) candidate list; null when not a keyword candidate. */
  lexical_rank: number | null;
  rerank_rank: number | null;
  /** Raw cross-encoder logit; null for candidates that were not scored. */
  rerank_score: number | null;
}

export interface RerankerBlock {
  enabled: boolean;
  status: RerankerStatusCode;
  rerank_depth: number;
  reranked_count: number;
}

/** A hard filter the server applied (canonical strings, for example category eq "laptop"). */
export interface AppliedFilter {
  field: string;
  operator: string;
  value: string;
}

export interface RerankedSearchResponse {
  query: string;
  result_count: number;
  results: SearchResult[];
  /** Empty when no filter was applied. */
  applied_filters: AppliedFilter[];
  reranker: RerankerBlock;
}

/** Request body. `top_k` is omitted, so the server's configured default applies. */
export interface RerankedSearchRequest {
  query: string;
}

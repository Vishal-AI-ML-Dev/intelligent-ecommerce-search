// The part of the `POST /assistant/answer` contract (Milestone 16) this page reads. Hand-written
// to mirror `AssistantResponse` in `src/ecommerce_search/assistant/models.py`; the API is the
// source of truth.

export type AssistantOutcome = 'answer' | 'clarify' | 'abstain' | 'generation_unavailable';

export const ASSISTANT_OUTCOMES: readonly AssistantOutcome[] = [
  'answer',
  'clarify',
  'abstain',
  'generation_unavailable',
];

export interface EvidenceProduct {
  search_rank: number;
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
  /** Typed catalog spec fields; null when the catalog does not give them (never inferred). */
  ram_gb: number | null;
  storage_gb: number | null;
  storage_type: string | null;
  storage_interface: string | null;
}

export interface CitedFact {
  field: string;
  /** Rendered by the server from the catalog record, never model text. */
  value: string | null;
}

export interface ConstraintCheck {
  field: string;
  requested: string;
  status: 'passed' | 'failed' | 'not_verified';
}

export interface Recommendation {
  product_id: string;
  facts: CitedFact[];
  constraint_checks: ConstraintCheck[];
  /** Rendered by the server from catalog data; never model text. */
  explanation: string;
  verification: 'all_verified' | 'not_all_verified' | 'no_requirements';
}

export interface GenerationInfo {
  status: 'not_needed' | 'disabled' | 'used' | 'failed';
  calls: number;
  validation: 'not_run' | 'passed' | 'repaired' | 'failed';
}

export interface AssistantResponse {
  outcome: AssistantOutcome;
  /** Fixed, server-written explanation of the outcome. */
  notice: string;
  clarification: { reason: string; question: string } | null;
  abstain_reason: string | null;
  /** Server-rendered overview of an answer; never model text. */
  summary: string | null;
  recommendations: Recommendation[];
  evidence: EvidenceProduct[];
  generation: GenerationInfo;
}

export interface AssistantRequest {
  message: string;
}

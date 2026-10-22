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
}

export interface CitedFact {
  field: string;
  /** Rendered by the server from the catalog record, never model text. */
  value: string | null;
}

export interface ConstraintCheck {
  field: string;
  requested: string;
  status: 'passed' | 'failed' | 'not_checked';
}

export interface Recommendation {
  product_id: string;
  facts: CitedFact[];
  constraint_checks: ConstraintCheck[];
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
  /** Model-written and unverified. */
  summary: string | null;
  recommendations: Recommendation[];
  evidence: EvidenceProduct[];
  generation: GenerationInfo;
}

export interface AssistantRequest {
  message: string;
}

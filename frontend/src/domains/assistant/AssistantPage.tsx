import { type FormEvent, useEffect, useId, useRef, useState } from 'react';
import { type HttpFailure, RequestAborted } from '../../shared/http/client';
import { askAssistant } from './api';
import type {
  AssistantResponse,
  CitedFact,
  ConstraintCheck,
  EvidenceProduct,
  Recommendation,
} from './types';

// Catalog text and the generated summary are untrusted: they are rendered only as React text
// children, never as HTML, and are never logged.

// Same limit as search query text on the server; longer input is rejected there with a 422.
export const MAX_MESSAGE_LENGTH = 200;

type State =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'success'; data: AssistantResponse }
  | { phase: 'error'; error: HttpFailure };

// Fixed, generic messages: server error bodies are never shown.
export function errorMessage(error: HttpFailure): string {
  if (error.kind === 'network') {
    return 'Could not reach the assistant. Check your connection and try again.';
  }
  if (error.kind === 'http' && error.status === 422) {
    return 'That request could not be processed. Try a shorter or different request.';
  }
  if (error.kind === 'http' && error.status === 503) {
    return 'The assistant is temporarily unavailable. Please try again shortly.';
  }
  return 'Something went wrong. Please try again.';
}

const OUTCOME_TITLES: Record<AssistantResponse['outcome'], string> = {
  answer: 'Suggested products',
  clarify: 'A question before searching',
  abstain: 'No answer',
  generation_unavailable: 'No generated answer available',
};

const FACT_LABELS: Record<string, string> = {
  title: 'Title',
  brand: 'Brand',
  category: 'Category',
  subcategory: 'Subcategory',
  price: 'Price',
  rating: 'Rating',
  review_count: 'Reviews',
  availability: 'Availability',
};

const CHECK_LABELS: Record<string, string> = {
  category: 'Product type',
  brand: 'Brand',
  min_price: 'Minimum price',
  max_price: 'Maximum price',
  ram_gb: 'RAM (GB)',
  storage_gb: 'Storage (GB)',
  storage_type: 'Storage type',
  storage_interface: 'Storage interface',
};

const STATUS_LABELS: Record<ConstraintCheck['status'], string> = {
  passed: 'matches',
  failed: 'does not match',
  not_checked: 'not checked (not in the catalog record)',
};

const plain = (code: string) => code.replaceAll('_', ' ');

function factText(fact: CitedFact, product: EvidenceProduct | undefined): string {
  if (fact.value === null) {
    return 'not given';
  }
  if (fact.field === 'price' && product !== undefined) {
    return `${product.currency} ${fact.value}`;
  }
  return fact.field === 'availability' ? plain(fact.value) : fact.value;
}

const evidenceAnchor = (product: EvidenceProduct) => `evidence-${product.search_rank}`;

function EvidenceItem({ product }: { product: EvidenceProduct }) {
  return (
    <li
      className="product-card evidence-item"
      id={evidenceAnchor(product)}
      data-testid="evidence-item"
      data-product-id={product.product_id}
    >
      <p className="product-rank">
        E{product.search_rank} · <span className="product-ref">{product.product_id}</span>
      </p>
      <h3 className="product-title">{product.title}</h3>
      <p className="product-meta">
        <span>{product.brand}</span> · <span>{product.category}</span>
      </p>
      <p className="product-price" data-testid="evidence-price">
        {product.currency} {product.price}
      </p>
      <p className="product-rating">
        {product.rating === null ? 'No rating yet' : `Rated ${product.rating} out of 5`}
      </p>
      <p className="product-availability">{plain(product.availability)}</p>
    </li>
  );
}

function RecommendationItem({
  recommendation,
  product,
}: {
  recommendation: Recommendation;
  product: EvidenceProduct | undefined;
}) {
  return (
    <li
      className="product-card recommendation"
      data-testid="recommendation"
      data-product-id={recommendation.product_id}
    >
      <h3 className="product-title">{product?.title ?? recommendation.product_id}</h3>
      <p className="product-rank">
        {product === undefined ? (
          <span className="product-ref">{recommendation.product_id}</span>
        ) : (
          <a href={`#${evidenceAnchor(product)}`} data-testid="evidence-ref">
            Evidence E{product.search_rank} · {recommendation.product_id}
          </a>
        )}
      </p>
      <p className="fact-heading">Catalog facts</p>
      <dl className="facts">
        {recommendation.facts.map((fact) => (
          <div key={fact.field} data-testid="fact">
            <dt>{FACT_LABELS[fact.field] ?? plain(fact.field)}</dt>
            <dd>{factText(fact, product)}</dd>
          </div>
        ))}
      </dl>
      {recommendation.constraint_checks.length > 0 && (
        <ul className="checks" aria-label="Requirement checks">
          {recommendation.constraint_checks.map((check) => (
            <li key={check.field} data-testid="constraint-check" data-status={check.status}>
              {CHECK_LABELS[check.field] ?? plain(check.field)} {check.requested}:{' '}
              {STATUS_LABELS[check.status]}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Result({ data }: { data: AssistantResponse }) {
  const byId = new Map(data.evidence.map((product) => [product.product_id, product]));
  const evidenceTitle =
    data.outcome === 'generation_unavailable'
      ? 'Search results (not a recommendation)'
      : 'Retrieved catalog evidence';
  return (
    <div data-testid="assistant-result" data-outcome={data.outcome}>
      <h2>{OUTCOME_TITLES[data.outcome]}</h2>
      <p className="assistant-notice" data-testid="assistant-notice">
        {data.notice}
      </p>
      {data.clarification !== null && (
        <p className="assistant-clarification" data-testid="assistant-clarification">
          {data.clarification.question}
        </p>
      )}
      {data.summary !== null && (
        <figure className="assistant-summary" data-testid="assistant-summary">
          <figcaption>Generated summary (unverified)</figcaption>
          <p>{data.summary}</p>
        </figure>
      )}
      {data.recommendations.length > 0 && (
        <ol className="product-list" aria-label="Suggested products">
          {data.recommendations.map((recommendation) => (
            <RecommendationItem
              key={recommendation.product_id}
              recommendation={recommendation}
              product={byId.get(recommendation.product_id)}
            />
          ))}
        </ol>
      )}
      {data.evidence.length > 0 && (
        <section aria-labelledby="evidence-heading">
          <h2 id="evidence-heading">{evidenceTitle}</h2>
          <ol className="product-list" aria-label={evidenceTitle}>
            {data.evidence.map((product) => (
              <EvidenceItem key={product.product_id} product={product} />
            ))}
          </ol>
        </section>
      )}
    </div>
  );
}

export function AssistantPage() {
  const [message, setMessage] = useState('');
  const [inputError, setInputError] = useState<string | null>(null);
  const [state, setState] = useState<State>({ phase: 'idle' });
  const inFlight = useRef<AbortController | null>(null);
  const inputId = useId();
  const hintId = useId();
  const inputErrorId = useId();

  useEffect(() => () => inFlight.current?.abort(), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = message.trim();
    if (text === '') {
      setInputError('Describe what you are looking for.');
      return;
    }
    if (text.length > MAX_MESSAGE_LENGTH) {
      setInputError(`Use at most ${MAX_MESSAGE_LENGTH} characters.`);
      return;
    }
    setInputError(null);
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setState({ phase: 'loading' });
    try {
      const result = await askAssistant(text, controller.signal);
      if (inFlight.current !== controller) {
        return; // superseded by a newer request
      }
      setState(
        result.ok ? { phase: 'success', data: result.data } : { phase: 'error', error: result.error },
      );
    } catch (error) {
      if (!(error instanceof RequestAborted)) {
        throw error;
      }
    }
  }

  const loading = state.phase === 'loading';
  const describedBy = inputError === null ? hintId : `${hintId} ${inputErrorId}`;

  return (
    <section aria-labelledby="assistant-heading">
      <h1 id="assistant-heading">Shopping assistant</h1>
      <form className="search-form" onSubmit={onSubmit} noValidate>
        <label htmlFor={inputId}>Describe what you need</label>
        <p id={hintId} className="input-hint">
          For example: dell laptop under rs 50000. Answers use only this catalog and never
          place orders.
        </p>
        <div className="search-row">
          <input
            id={inputId}
            type="text"
            name="message"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            autoComplete="off"
            aria-invalid={inputError !== null}
            aria-describedby={describedBy}
          />
          <button type="submit" disabled={loading}>
            Ask
          </button>
        </div>
        {inputError !== null && (
          <p id={inputErrorId} className="input-error">
            {inputError}
          </p>
        )}
      </form>

      <div className="assistant-results" aria-busy={loading}>
        <p role="status" className="search-status" data-testid="assistant-status">
          {loading && 'Working on your request…'}
        </p>
        {state.phase === 'error' && (
          <p role="alert" className="search-error" data-testid="assistant-error">
            {errorMessage(state.error)}
          </p>
        )}
        {state.phase === 'success' && <Result data={state.data} />}
      </div>
    </section>
  );
}

import { type FormEvent, useEffect, useId, useRef, useState } from 'react';
import { type HttpFailure, RequestAborted } from '../../shared/http/client';
import { searchReranked } from './api';
import { ProductCard } from './ProductCard';
import { RerankerStatus } from './RerankerStatus';
import type { RerankedSearchResponse } from './types';

type State =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'success'; data: RerankedSearchResponse }
  | { phase: 'error'; error: HttpFailure };

// Fixed, generic messages: server error bodies are never shown.
export function errorMessage(error: HttpFailure): string {
  if (error.kind === 'network') {
    return 'Could not reach the search service. Check your connection and try again.';
  }
  if (error.kind === 'http' && error.status === 422) {
    return 'That search could not be processed. Try a shorter or different query.';
  }
  if (error.kind === 'http' && error.status === 503) {
    return 'Search is temporarily unavailable. Please try again shortly.';
  }
  return 'Something went wrong while searching. Please try again.';
}

// Results exist, none was a keyword candidate and no filter was applied: every result came only
// from embedding similarity. A display hint only; ranking and results are unchanged.
export function isSimilarityOnly(data: RerankedSearchResponse): boolean {
  return (
    data.results.length > 0 &&
    data.applied_filters.length === 0 &&
    data.results.every((result) => result.lexical_rank === null)
  );
}

export function SearchPage() {
  const [query, setQuery] = useState('');
  const [inputError, setInputError] = useState<string | null>(null);
  const [state, setState] = useState<State>({ phase: 'idle' });
  const inFlight = useRef<AbortController | null>(null);
  const inputId = useId();
  const inputErrorId = useId();

  useEffect(() => () => inFlight.current?.abort(), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = query.trim();
    if (text === '') {
      setInputError('Enter something to search for.');
      return;
    }
    setInputError(null);
    inFlight.current?.abort();
    const controller = new AbortController();
    inFlight.current = controller;
    setState({ phase: 'loading' });
    try {
      const result = await searchReranked(text, controller.signal);
      if (inFlight.current !== controller) {
        return; // superseded by a newer search
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

  return (
    <section aria-labelledby="search-heading">
      <h1 id="search-heading">Search products</h1>
      <form role="search" className="search-form" onSubmit={onSubmit} noValidate>
        <label htmlFor={inputId}>Search the catalog</label>
        <div className="search-row">
          <input
            id={inputId}
            type="search"
            name="query"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoComplete="off"
            aria-invalid={inputError !== null}
            aria-describedby={inputError === null ? undefined : inputErrorId}
          />
          <button type="submit" disabled={loading}>
            Search
          </button>
        </div>
        {inputError !== null && (
          <p id={inputErrorId} className="input-error">
            {inputError}
          </p>
        )}
      </form>

      <div className="search-results" aria-busy={loading}>
        <p role="status" className="search-status" data-testid="search-status">
          {loading && 'Searching…'}
          {state.phase === 'success' &&
            (state.data.result_count === 1 ? '1 result' : `${state.data.result_count} results`)}
        </p>
        {state.phase === 'error' && (
          <p role="alert" className="search-error" data-testid="search-error">
            {errorMessage(state.error)}
          </p>
        )}
        {state.phase === 'success' && (
          <>
            <RerankerStatus reranker={state.data.reranker} />
            {state.data.results.length === 0 ? (
              <p className="search-empty" data-testid="search-empty">
                No products matched your search. Try different or fewer words.
              </p>
            ) : (
              <>
                {isSimilarityOnly(state.data) && (
                  <p className="search-notice" data-testid="similarity-notice">
                    Showing similarity-based suggestions. Try a product name or requirement for
                    more specific results.
                  </p>
                )}
                <ol className="product-list" aria-label="Search results">
                  {state.data.results.map((result) => (
                    <ProductCard
                      key={result.product_id}
                      result={result}
                      rerankerApplied={state.data.reranker.status === 'applied'}
                    />
                  ))}
                </ol>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}

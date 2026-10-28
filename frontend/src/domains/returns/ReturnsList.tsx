import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from 'react';
import type { HttpFailure } from '../../shared/http/client';
import { getReturn, listReturns } from './api';
import { ReturnDetailView } from './ReturnDetailView';
import { ErrorNotice, Pager, SyntheticBadge, utc } from './ReturnsCommon';
import type { ReturnDetail, ReturnFilters, ReturnPage, ReturnRef } from './types';
import { useReturnsRequests } from './useReturnsRequests';
import vocab from './vocab.json' with { type: 'json' };

export const EMPTY_FILTERS: ReturnFilters = {
  dataset_id: '',
  dataset_version: '',
  status: '',
  customer_reason_code: '',
  category: '',
  product_id: '',
  requested_from: '',
  requested_to: '',
  has_comment: '',
  has_inspection: '',
  batch_id: '',
};

type PageState =
  | { phase: 'loading' }
  | { phase: 'loaded'; page: ReturnPage }
  | { phase: 'error'; error: HttpFailure };

type DetailState =
  | { phase: 'loading'; ref: ReturnRef }
  | { phase: 'loaded'; detail: ReturnDetail }
  | { phase: 'error'; error: HttpFailure };

const TEXT_FILTERS: [keyof ReturnFilters, string, string?][] = [
  ['dataset_id', 'Dataset ID'],
  ['dataset_version', 'Dataset version'],
  ['product_id', 'Product ID'],
  ['batch_id', 'Import batch ID'],
  ['requested_from', 'Requested from (inclusive)', 'e.g. 2026-02-01T00:00:00+05:30'],
  ['requested_to', 'Requested before (exclusive)', 'e.g. 2026-03-01T00:00:00+05:30'],
];

const SELECT_FILTERS: [keyof ReturnFilters, string, readonly string[]][] = [
  ['status', 'Status', vocab.return_statuses],
  ['customer_reason_code', 'Customer reason code', vocab.reason_codes],
  ['category', 'Category', vocab.categories],
  ['has_comment', 'Has a comment', ['true', 'false']],
  ['has_inspection', 'Has an inspection', ['true', 'false']],
];

/**
 * Returns list with the §7 filters and Next/Previous over a client cursor stack. The applied
 * filters are fixed when the form is submitted, and every page of one cursor chain is requested
 * with exactly those filters; changing them starts again at page 1 (§7 `cursor_mismatch`).
 */
export function ReturnsList({
  token,
  active,
  onUnauthorized,
}: {
  token: string;
  active: boolean;
  onUnauthorized: () => void;
}) {
  const run = useReturnsRequests(onUnauthorized);
  const [draft, setDraft] = useState<ReturnFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<ReturnFilters>(EMPTY_FILTERS);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [state, setState] = useState<PageState>({ phase: 'loading' });
  const [detail, setDetail] = useState<DetailState | null>(null);
  const started = useRef(false);
  const formId = useId();

  const load = useCallback(
    async (filters: ReturnFilters, stack: (string | null)[]) => {
      setApplied(filters);
      setCursors(stack);
      setState({ phase: 'loading' });
      const result = await run('page', (signal) =>
        listReturns(token, filters, stack.at(-1) ?? null, signal),
      );
      if (result !== undefined) {
        setState(result.ok ? { phase: 'loaded', page: result.data } : { phase: 'error', error: result.error });
      }
    },
    [run, token],
  );

  useEffect(() => {
    if (active && !started.current) {
      started.current = true;
      void load(EMPTY_FILTERS, [null]);
    }
  }, [active, load]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void load(draft, [null]);
  }

  function clearFilters() {
    setDraft(EMPTY_FILTERS);
    void load(EMPTY_FILTERS, [null]);
  }

  async function openDetail(ref: ReturnRef) {
    setDetail({ phase: 'loading', ref });
    const result = await run('detail', (signal) => getReturn(token, ref, signal));
    if (result !== undefined) {
      setDetail(result.ok ? { phase: 'loaded', detail: result.data } : { phase: 'error', error: result.error });
    }
  }

  const loading = state.phase === 'loading';
  const page = state.phase === 'loaded' ? state.page : null;

  return (
    <section aria-labelledby="returns-list-heading" className="returns-section">
      <h2 id="returns-list-heading">Returns</h2>
      {detail !== null && (
        <div data-testid="return-detail-panel">
          <button type="button" onClick={() => setDetail(null)}>
            Back to the list
          </button>
          {detail.phase === 'loading' && (
            <p role="status" className="search-status">
              Loading return…
            </p>
          )}
          {detail.phase === 'error' && <ErrorNotice error={detail.error} />}
          {detail.phase === 'loaded' && <ReturnDetailView detail={detail.detail} />}
        </div>
      )}
      <div hidden={detail !== null}>
        <form className="returns-filters" onSubmit={onSubmit} noValidate aria-label="Return filters">
          {TEXT_FILTERS.map(([key, label, hint]) => (
            <label key={key} htmlFor={`${formId}-${key}`}>
              {label}
              <input
                id={`${formId}-${key}`}
                type="text"
                value={draft[key]}
                placeholder={hint}
                autoComplete="off"
                onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
              />
            </label>
          ))}
          {SELECT_FILTERS.map(([key, label, options]) => (
            <label key={key} htmlFor={`${formId}-${key}`}>
              {label}
              <select
                id={`${formId}-${key}`}
                value={draft[key]}
                onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
              >
                <option value="">Any</option>
                {options.map((option) => (
                  <option key={option} value={option}>
                    {option === 'true' ? 'Yes' : option === 'false' ? 'No' : option}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <div className="returns-actions">
            <button type="submit" disabled={loading}>
              Apply filters
            </button>
            <button type="button" onClick={clearFilters} disabled={loading}>
              Clear filters
            </button>
          </div>
        </form>

        <div aria-busy={loading}>
          <p role="status" className="search-status" data-testid="returns-status">
            {loading && 'Loading returns…'}
          </p>
          {state.phase === 'error' && <ErrorNotice error={state.error} />}
          {page !== null && page.items.length === 0 && (
            <p className="search-empty" data-testid="returns-empty">
              No returns match these filters.
            </p>
          )}
          {page !== null && page.items.length > 0 && (
            <ul className="returns-list" aria-label="Returns">
              {page.items.map((item) => {
                const key = `${item.ref.dataset_id}/${item.ref.dataset_version}/${item.ref.return_id}`;
                return (
                  <li key={key} className="returns-card" data-testid="return-row">
                    <p className="returns-row-head">
                      <strong>{item.ref.return_id}</strong>{' '}
                      <span className="product-meta">
                        {item.ref.dataset_id} v{item.ref.dataset_version}
                      </span>{' '}
                      {item.is_synthetic && <SyntheticBadge />}
                    </p>
                    <p>{item.product.title}</p>
                    <p className="product-meta">
                      {item.product.brand} · {item.product.category} · {item.product.product_id}
                    </p>
                    <p className="product-meta">
                      Requested {utc(item.requested_at)} · status <code>{item.status}</code> · reason{' '}
                      <code>{item.customer_reason_code}</code> · quantity {item.quantity}
                    </p>
                    <p className="product-meta">
                      {item.comment_count} comment{item.comment_count === 1 ? '' : 's'} ·{' '}
                      {item.inspection_count} inspection{item.inspection_count === 1 ? '' : 's'}
                    </p>
                    <button type="button" onClick={() => void openDetail(item.ref)}>
                      View return {item.ref.return_id}
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {page !== null && (
            <Pager
              label="Returns pages"
              page={cursors.length - 1}
              hasNext={page.next_cursor !== null}
              busy={loading}
              onPrevious={() => void load(applied, cursors.slice(0, -1))}
              onNext={() => void load(applied, [...cursors, page.next_cursor])}
            />
          )}
        </div>
      </div>
    </section>
  );
}

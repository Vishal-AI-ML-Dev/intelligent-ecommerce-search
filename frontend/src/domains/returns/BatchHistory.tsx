import { useCallback, useEffect, useRef, useState } from 'react';
import type { HttpFailure } from '../../shared/http/client';
import { formatBytes, getBatch, listBatches } from './api';
import { ErrorNotice, Facts, Pager, utc } from './ReturnsCommon';
import type { BatchDetail, BatchPage } from './types';
import { useReturnsRequests } from './useReturnsRequests';

type PageState =
  | { phase: 'loading' }
  | { phase: 'loaded'; page: BatchPage }
  | { phase: 'error'; error: HttpFailure };

type DetailState =
  | { phase: 'none' }
  | { phase: 'loading'; batchId: string }
  | { phase: 'loaded'; batch: BatchDetail }
  | { phase: 'error'; error: HttpFailure };

/** Read-only import history (contracts §9): batches newest first, then one batch's file metadata. */
export function BatchHistory({
  token,
  active,
  onUnauthorized,
}: {
  token: string;
  active: boolean;
  onUnauthorized: () => void;
}) {
  const run = useReturnsRequests(onUnauthorized);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [state, setState] = useState<PageState>({ phase: 'loading' });
  const [detail, setDetail] = useState<DetailState>({ phase: 'none' });
  const started = useRef(false);

  const load = useCallback(
    async (stack: (string | null)[]) => {
      setCursors(stack);
      setState({ phase: 'loading' });
      const result = await run('page', (signal) => listBatches(token, stack.at(-1) ?? null, signal));
      if (result !== undefined) {
        setState(result.ok ? { phase: 'loaded', page: result.data } : { phase: 'error', error: result.error });
      }
    },
    [run, token],
  );

  useEffect(() => {
    if (active && !started.current) {
      started.current = true;
      void load([null]);
    }
  }, [active, load]);

  async function open(batchId: string) {
    setDetail({ phase: 'loading', batchId });
    const result = await run('detail', (signal) => getBatch(token, batchId, signal));
    if (result !== undefined) {
      setDetail(result.ok ? { phase: 'loaded', batch: result.data } : { phase: 'error', error: result.error });
    }
  }

  const loading = state.phase === 'loading';
  const page = state.phase === 'loaded' ? state.page : null;

  return (
    <section aria-labelledby="returns-history-heading" className="returns-section">
      <h2 id="returns-history-heading">Import history</h2>
      <div className="returns-actions">
        <button type="button" onClick={() => void load([null])} disabled={loading}>
          Refresh
        </button>
      </div>
      <div aria-busy={loading}>
        <p role="status" className="search-status">
          {loading && 'Loading import history…'}
        </p>
        {state.phase === 'error' && <ErrorNotice error={state.error} />}
        {page !== null && page.items.length === 0 && (
          <p className="search-empty" data-testid="batches-empty">
            No imports have been recorded.
          </p>
        )}
        {page !== null && page.items.length > 0 && (
          <div className="returns-scroll">
            <table className="returns-table" aria-label="Import batches" data-testid="batch-table">
              <thead>
                <tr>
                  <th scope="col">Imported at</th>
                  <th scope="col">Dataset</th>
                  <th scope="col">Format</th>
                  <th scope="col">Files</th>
                  <th scope="col">Size</th>
                  <th scope="col">Imported by</th>
                  <th scope="col">Batch</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((batch) => (
                  <tr key={batch.batch_id}>
                    <td>{utc(batch.imported_at)}</td>
                    <td>
                      {batch.dataset_id} v{batch.dataset_version}
                    </td>
                    <td>{batch.format}</td>
                    <td>{batch.file_count}</td>
                    <td>{formatBytes(batch.total_bytes)}</td>
                    <td>{batch.imported_by_label}</td>
                    <td>
                      <button type="button" className="link-button" onClick={() => void open(batch.batch_id)}>
                        {batch.batch_id}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {page !== null && (
          <Pager
            label="Import history pages"
            page={cursors.length - 1}
            hasNext={page.next_cursor !== null}
            busy={loading}
            onPrevious={() => void load(cursors.slice(0, -1))}
            onNext={() => void load([...cursors, page.next_cursor])}
          />
        )}
      </div>
      <div aria-busy={detail.phase === 'loading'}>
        {detail.phase === 'loading' && <p className="search-status">Loading batch…</p>}
        {detail.phase === 'error' && <ErrorNotice error={detail.error} />}
        {detail.phase === 'loaded' && <BatchDetailView batch={detail.batch} />}
      </div>
    </section>
  );
}

function BatchDetailView({ batch }: { batch: BatchDetail }) {
  return (
    <section className="returns-card" aria-labelledby="returns-batch-heading" data-testid="batch-detail">
      <h3 id="returns-batch-heading">Batch {batch.batch_id}</h3>
      <Facts
        items={[
          ['Dataset', `${batch.dataset_id} v${batch.dataset_version}`],
          ['Imported at', utc(batch.imported_at)],
          ['Imported by', batch.imported_by_label],
          ['Contract version', batch.contract_version],
          ['Transformation', batch.transformation_version],
          ['Redaction', batch.redaction_version],
          ['Bundle SHA-256', batch.bundle_sha256],
        ]}
      />
      <div className="returns-scroll">
        <table className="returns-table" aria-label="Batch files">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Entity</th>
              <th scope="col">Media type</th>
              <th scope="col">Size</th>
              <th scope="col">Rows</th>
              <th scope="col">SHA-256</th>
            </tr>
          </thead>
          <tbody>
            {batch.files.map((file) => (
              <tr key={file.name}>
                <td>{file.name}</td>
                <td>{file.entity ?? ''}</td>
                <td>{file.media_type}</td>
                <td>{formatBytes(file.size_bytes)}</td>
                <td>{file.row_count ?? ''}</td>
                <td>
                  <code>{file.raw_sha256}</code>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="returns-scroll">
        <table className="returns-table" aria-label="Batch record counts">
          <thead>
            <tr>
              <th scope="col">Entity</th>
              <th scope="col">Received</th>
              <th scope="col">New</th>
              <th scope="col">Unchanged</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(batch.counts).map(([entity, counts]) => (
              <tr key={entity}>
                <th scope="row">{entity}</th>
                <td>{counts.received}</td>
                <td>{counts.new}</td>
                <td>{counts.unchanged}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

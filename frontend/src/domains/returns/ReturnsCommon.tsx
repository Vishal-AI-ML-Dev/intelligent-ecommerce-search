import type { HttpFailure } from '../../shared/http/client';
import { errorMessage } from './messages';
import type { Provenance } from './types';

/** An error with its fixed message, safe 422 parameter names and the request ID. */
export function ErrorNotice({ error, testId }: { error: HttpFailure; testId?: string }) {
  const parameters = error.kind === 'http' ? (error.parameters ?? []) : [];
  return (
    <div role="alert" className="returns-error" data-testid={testId ?? 'returns-error'}>
      <p>{errorMessage(error)}</p>
      {parameters.length > 0 && (
        <ul className="returns-parameters" aria-label="Values to check">
          {parameters.map((p) => (
            <li key={`${p.name}:${p.code}`}>
              <code>{p.name}</code> ({p.code})
            </li>
          ))}
        </ul>
      )}
      {error.requestId !== undefined && (
        <p className="returns-request-id" data-testid="request-id">
          Request ID: <code>{error.requestId}</code>
        </p>
      )}
    </div>
  );
}

export const NOT_RECORDED = 'Not recorded';

/** The server's canonical UTC timestamp, shown unchanged and labelled as UTC. */
export function utc(timestamp: string): string;
export function utc(timestamp: string | null): string | null;
export function utc(timestamp: string | null): string | null {
  return timestamp === null ? null : `${timestamp} (UTC)`;
}

export function NotRecorded() {
  return <p className="returns-not-recorded">{NOT_RECORDED}</p>;
}

export function SyntheticBadge() {
  return (
    <span className="returns-badge" data-testid="synthetic-badge">
      Synthetic demo data
    </span>
  );
}

export function Facts({ items }: { items: [string, string | number | null][] }) {
  return (
    <dl className="facts">
      {items.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value === null ? NOT_RECORDED : String(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function provenanceFacts(p: Provenance): [string, string | number | null][] {
  return [
    ['Source type', p.source_type],
    ['Source name', p.source_name],
    ['License', p.license],
    ['Source record ID', p.source_record_id],
    ['Dataset version', p.dataset_version],
    ['Ingested at', utc(p.ingestion_timestamp)],
    ['Transformation', p.transformation_version],
    ['Synthetic', p.is_synthetic ? 'yes' : 'no'],
    ['Import batch', p.batch_id],
    ['Source file', p.source_file],
    ['Source row', p.source_row],
  ];
}

/** The full provenance block (§7): open for the return itself, collapsed on nested records. */
export function ProvenanceBlock({ provenance, open }: { provenance: Provenance; open?: boolean }) {
  return (
    <details className="returns-provenance" open={open} data-testid="provenance">
      <summary>Provenance</summary>
      <Facts items={provenanceFacts(provenance)} />
    </details>
  );
}

/** Next/Previous over an opaque cursor chain. */
export function Pager({
  page,
  hasNext,
  busy,
  onPrevious,
  onNext,
  label,
}: {
  page: number;
  hasNext: boolean;
  busy: boolean;
  onPrevious: () => void;
  onNext: () => void;
  label: string;
}) {
  return (
    <nav className="returns-pager" aria-label={label}>
      <button type="button" onClick={onPrevious} disabled={busy || page === 0}>
        Previous
      </button>
      <span>Page {page + 1}</span>
      <button type="button" onClick={onNext} disabled={busy || !hasNext}>
        Next
      </button>
    </nav>
  );
}

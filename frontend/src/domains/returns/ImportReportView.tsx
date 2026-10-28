import { Facts, utc } from './ReturnsCommon';
import type { ImportReport } from './types';

const STATUS_TEXT: Record<ImportReport['status'], string> = {
  valid: 'Valid: the upload passed every check. Nothing was stored.',
  invalid: 'Invalid: the upload has errors. Nothing was stored.',
  imported: 'Imported.',
  already_imported: 'Already imported: this exact upload was imported before. Nothing new was stored.',
  no_new_records: 'No new records: every record is already stored. Nothing new was stored.',
};

/** The §4.8 truncation and lower-bound wording. */
export function errorSummary(report: ImportReport): string {
  const shown = report.errors.length;
  const total = `${report.error_count_is_lower_bound ? 'at least ' : ''}${report.error_count}`;
  if (report.error_count === 0) {
    return 'No errors.';
  }
  if (report.errors_truncated) {
    return `Showing ${shown} of ${total} errors.`;
  }
  if (report.error_count_is_lower_bound) {
    return `${shown === 1 ? '1 error' : `${shown} errors`} found; at least ${report.error_count} in total, because later checks did not run.`;
  }
  return report.error_count === 1 ? '1 error.' : `${report.error_count} errors.`;
}

const show = (value: string | number | null) => (value === null ? '' : String(value));

export function ImportReportView({ report }: { report: ImportReport }) {
  const entities = Object.entries(report.counts);
  return (
    <section
      className="returns-report"
      aria-labelledby="returns-report-heading"
      data-testid="import-report"
      data-status={report.status}
    >
      <h3 id="returns-report-heading">{report.mode === 'commit' ? 'Import result' : 'Validation result'}</h3>
      <p className="returns-report-status" data-testid="report-status">
        {STATUS_TEXT[report.status]}
      </p>
      <Facts
        items={[
          ['Checked at', utc(report.checked_at)],
          ['Contract version', report.contract_version],
          ['Bundle SHA-256', report.bundle_sha256],
          ['Import batch', report.batch_id],
          ['Phases completed', report.phases_completed.join(', ') || 'none'],
        ]}
      />
      {entities.length > 0 && (
        <div className="returns-scroll">
          <table className="returns-table" aria-label="Record counts">
            <thead>
              <tr>
                <th scope="col">Entity</th>
                <th scope="col">Received</th>
                <th scope="col">New</th>
                <th scope="col">Unchanged</th>
              </tr>
            </thead>
            <tbody>
              {entities.map(([entity, counts]) => (
                <tr key={entity}>
                  <th scope="row">{entity}</th>
                  <td>{counts.received}</td>
                  <td>{counts.new ?? 'not compared'}</td>
                  <td>{counts.unchanged ?? 'not compared'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="returns-error-summary" data-testid="error-summary">
        {errorSummary(report)}
      </p>
      {report.errors.length > 0 && (
        <div className="returns-scroll">
          <table className="returns-table" aria-label="Errors" data-testid="error-table">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col">Entity</th>
                <th scope="col">Row</th>
                <th scope="col">Line</th>
                <th scope="col">Field</th>
                <th scope="col">Code</th>
                <th scope="col">Message</th>
                <th scope="col">Value preview</th>
              </tr>
            </thead>
            <tbody>
              {report.errors.map((error, index) => (
                // Errors have no ID; their server order is deterministic (§4.8).
                <tr key={index}>
                  <td>{error.file}</td>
                  <td>{show(error.entity)}</td>
                  <td>{show(error.row)}</td>
                  <td>{show(error.line)}</td>
                  <td>{show(error.field ?? error.pointer)}</td>
                  <td>
                    <code>{error.code}</code>
                  </td>
                  <td>{error.message}</td>
                  <td>{show(error.value_preview)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

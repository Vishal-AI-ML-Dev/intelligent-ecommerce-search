import { type ChangeEvent, useId, useState } from 'react';
import type { HttpFailure } from '../../shared/http/client';
import {
  buildUploadForm,
  formatBytes,
  importUpload,
  precheckUpload,
  type UploadMode,
  validateUpload,
} from './api';
import { ImportReportView } from './ImportReportView';
import { IMPORTS_DISABLED_MESSAGE } from './messages';
import { ErrorNotice } from './ReturnsCommon';
import type { ImportReport, ReturnsCapabilities } from './types';
import { useReturnsRequests } from './useReturnsRequests';

type State =
  | { phase: 'idle' }
  | { phase: 'sending'; action: 'validate' | 'import' }
  | { phase: 'report'; report: ImportReport }
  | { phase: 'error'; error: HttpFailure };

export function ImportPanel({
  token,
  capabilities,
  onUnauthorized,
}: {
  token: string;
  capabilities: ReturnsCapabilities;
  onUnauthorized: () => void;
}) {
  const run = useReturnsRequests(onUnauthorized);
  const [mode, setMode] = useState<UploadMode>('json');
  const [files, setFiles] = useState<File[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [state, setState] = useState<State>({ phase: 'idle' });
  // Changing the mode remounts the file input so no stale selection stays visible.
  const [inputKey, setInputKey] = useState(0);
  const fileInputId = useId();
  const limits = capabilities.limits;
  const sending = state.phase === 'sending';

  function chooseMode(next: UploadMode) {
    setMode(next);
    setFiles([]);
    setProblems([]);
    setState({ phase: 'idle' });
    setInputKey((key) => key + 1);
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>) {
    setFiles(Array.from(event.target.files ?? []));
    setProblems([]);
    setState({ phase: 'idle' });
  }

  async function send(action: 'validate' | 'import') {
    const found = precheckUpload(mode, files, limits);
    setProblems(found);
    if (found.length > 0) {
      return;
    }
    setState({ phase: 'sending', action });
    const form = buildUploadForm(mode, files);
    const result = await run('upload', (signal) =>
      action === 'validate' ? validateUpload(token, form, signal) : importUpload(token, form, signal),
    );
    if (result === undefined) {
      return;
    }
    setState(result.ok ? { phase: 'report', report: result.data } : { phase: 'error', error: result.error });
  }

  return (
    <section aria-labelledby="returns-import-heading" className="returns-section">
      <h2 id="returns-import-heading">Import</h2>
      <p className="input-hint">
        Upload a return-data bundle to check it{capabilities.can_commit ? ' or import it' : ''}. Limits:{' '}
        {formatBytes(limits.file_bytes)} per file, {formatBytes(limits.bundle_bytes)} in total, at most{' '}
        {limits.files} files. These checks are repeated by the server.
      </p>
      {!capabilities.can_commit && (
        <p className="returns-notice" data-testid="imports-disabled">
          {IMPORTS_DISABLED_MESSAGE}
        </p>
      )}
      {!capabilities.can_validate ? (
        <p className="returns-notice" data-testid="validation-unavailable">
          Your access token does not allow uploads.
        </p>
      ) : (
        <form className="returns-upload" onSubmit={(event) => event.preventDefault()} noValidate>
          <fieldset>
            <legend>Format</legend>
            <label>
              <input
                type="radio"
                name="upload-mode"
                value="json"
                checked={mode === 'json'}
                onChange={() => chooseMode('json')}
                disabled={sending}
              />{' '}
              JSON bundle (one file)
            </label>
            <label>
              <input
                type="radio"
                name="upload-mode"
                value="csv"
                checked={mode === 'csv'}
                onChange={() => chooseMode('csv')}
                disabled={sending}
              />{' '}
              CSV set (manifest.json and entity CSV files)
            </label>
          </fieldset>
          <label htmlFor={fileInputId}>{mode === 'json' ? 'Bundle file' : 'CSV set files'}</label>
          <input
            key={inputKey}
            id={fileInputId}
            type="file"
            accept={mode === 'json' ? '.json,application/json' : '.csv,.json,text/csv,application/json'}
            multiple={mode === 'csv'}
            onChange={onFiles}
            disabled={sending}
          />
          {problems.length > 0 && (
            <ul className="input-error" data-testid="upload-problems">
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          )}
          <div className="returns-actions">
            <button type="button" onClick={() => void send('validate')} disabled={sending}>
              Validate
            </button>
            {capabilities.can_commit && (
              <button type="button" onClick={() => void send('import')} disabled={sending}>
                Import
              </button>
            )}
          </div>
        </form>
      )}
      <div aria-busy={sending}>
        <p role="status" className="search-status">
          {state.phase === 'sending' && (state.action === 'validate' ? 'Validating…' : 'Importing…')}
        </p>
        {state.phase === 'error' && <ErrorNotice error={state.error} />}
        {state.phase === 'report' && <ImportReportView report={state.report} />}
      </div>
    </section>
  );
}

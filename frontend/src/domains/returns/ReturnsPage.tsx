import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from 'react';
import { type HttpFailure, RequestAborted } from '../../shared/http/client';
import { fetchCapabilities } from './api';
import { BatchHistory } from './BatchHistory';
import { ImportPanel } from './ImportPanel';
import { NOT_CONFIGURED_MESSAGE } from './messages';
import { ErrorNotice } from './ReturnsCommon';
import { ReturnsList } from './ReturnsList';
import type { ReturnsCapabilities } from './types';

/**
 * The "Retailer returns" tab (contracts §9, D11).
 *
 * Token handling: the token lives only in this component's React state while signed in. It is
 * never written to storage, cookies, the URL, the DOM or logs, and the input is cleared as soon
 * as it is submitted. Sign-out and any 401 drop the session: the signed-in view is keyed by the
 * session, so it unmounts, its pending requests are aborted and all retailer data is discarded.
 */

type Session = { id: number; token: string; capabilities: ReturnsCapabilities };

type Gate =
  | { phase: 'idle' }
  | { phase: 'checking' }
  | { phase: 'error'; error: HttpFailure }
  | { phase: 'signed_out' }
  | { phase: 'expired' };

const SECTIONS = [
  { id: 'returns', label: 'Returns' },
  { id: 'import', label: 'Import' },
  { id: 'history', label: 'Import history' },
] as const;

type Section = (typeof SECTIONS)[number]['id'];

function gateMessage(error: HttpFailure): HttpFailure | string {
  if (error.kind === 'http' && error.status === 503 && error.code === 'retailer_access_not_configured') {
    return NOT_CONFIGURED_MESSAGE;
  }
  return error;
}

export function ReturnsPage() {
  const [draft, setDraft] = useState('');
  const [session, setSession] = useState<Session | null>(null);
  const [gate, setGate] = useState<Gate>({ phase: 'idle' });
  const signIn = useRef<AbortController | null>(null);
  const nextSessionId = useRef(1);
  const inputId = useId();

  useEffect(() => () => signIn.current?.abort(), []);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const token = draft.trim();
    setDraft('');
    if (token === '') {
      setGate({ phase: 'idle' });
      return;
    }
    signIn.current?.abort();
    const controller = new AbortController();
    signIn.current = controller;
    setGate({ phase: 'checking' });
    try {
      const result = await fetchCapabilities(token, controller.signal);
      if (controller.signal.aborted || signIn.current !== controller) {
        return;
      }
      signIn.current = null;
      if (result.ok) {
        setSession({ id: nextSessionId.current++, token, capabilities: result.data });
        setGate({ phase: 'idle' });
      } else {
        setGate({ phase: 'error', error: result.error });
      }
    } catch (error) {
      if (!(error instanceof RequestAborted)) {
        throw error;
      }
    }
  }

  const signOut = useCallback(() => {
    signIn.current?.abort();
    setSession(null);
    setGate({ phase: 'signed_out' });
  }, []);

  const expire = useCallback(() => {
    setSession(null);
    setGate({ phase: 'expired' });
  }, []);

  if (session !== null) {
    return <SignedIn key={session.id} session={session} onSignOut={signOut} onUnauthorized={expire} />;
  }

  const checking = gate.phase === 'checking';
  const gateError = gate.phase === 'error' ? gateMessage(gate.error) : null;
  return (
    <section aria-labelledby="returns-heading" className="returns-page">
      <h1 id="returns-heading">Retailer returns</h1>
      <p>
        Retailer access needs an access token from the server operator. The token is kept in this page
        only while you are signed in and is never stored in the browser.
      </p>
      <form className="search-form" onSubmit={onSubmit} noValidate aria-label="Retailer sign-in">
        <label htmlFor={inputId}>Access token</label>
        <div className="search-row">
          <input
            id={inputId}
            type="password"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            disabled={checking}
          />
          <button type="submit" disabled={checking}>
            Sign in
          </button>
        </div>
      </form>
      <div aria-busy={checking}>
        <p role="status" className="search-status" data-testid="gate-status">
          {checking && 'Checking access…'}
          {gate.phase === 'signed_out' && 'Signed out. The token and the retailer data were cleared.'}
        </p>
        {gate.phase === 'expired' && (
          <p role="alert" className="search-error" data-testid="gate-expired">
            Your access token was not accepted. You were signed out and the retailer data was cleared. Sign in
            again with a valid token.
          </p>
        )}
        {typeof gateError === 'string' && (
          <p role="alert" className="search-error" data-testid="gate-not-configured">
            {gateError}
          </p>
        )}
        {gateError !== null && typeof gateError !== 'string' && (
          <ErrorNotice error={gateError} testId="gate-error" />
        )}
      </div>
    </section>
  );
}

function SignedIn({
  session,
  onSignOut,
  onUnauthorized,
}: {
  session: Session;
  onSignOut: () => void;
  onUnauthorized: () => void;
}) {
  const [section, setSection] = useState<Section>('returns');
  // Raised after each import that stored records, so already loaded lists show them.
  const [importGeneration, setImportGeneration] = useState(0);
  const onImported = useCallback(() => setImportGeneration((generation) => generation + 1), []);
  const { token, capabilities } = session;
  return (
    <section aria-labelledby="returns-heading" className="returns-page" data-testid="returns-signed-in">
      <div className="returns-topbar">
        <h1 id="returns-heading">Retailer returns</h1>
        <button type="button" onClick={onSignOut}>
          Sign out
        </button>
      </div>
      <p className="product-meta">
        Access: {capabilities.scopes.join(', ')} · contract {capabilities.contract_version}
      </p>
      <nav aria-label="Retailer sections" className="returns-sections">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            aria-current={section === s.id ? 'page' : undefined}
            onClick={() => setSection(s.id)}
          >
            {s.label}
          </button>
        ))}
      </nav>
      {/* Every section stays mounted so switching keeps its state; each loads when first shown. */}
      <div hidden={section !== 'returns'}>
        <ReturnsList
          token={token}
          active={section === 'returns'}
          refreshKey={importGeneration}
          onUnauthorized={onUnauthorized}
        />
      </div>
      <div hidden={section !== 'import'}>
        <ImportPanel
          token={token}
          capabilities={capabilities}
          onImported={onImported}
          onUnauthorized={onUnauthorized}
        />
      </div>
      <div hidden={section !== 'history'}>
        <BatchHistory
          token={token}
          active={section === 'history'}
          refreshKey={importGeneration}
          onUnauthorized={onUnauthorized}
        />
      </div>
    </section>
  );
}

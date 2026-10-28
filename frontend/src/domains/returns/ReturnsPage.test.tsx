import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { returnsFixtures as f } from '../../../e2e/fixtures';
import { App } from '../../app/App';
import { ReturnsPage } from './ReturnsPage';

const TOKEN = 'synthetic-test-token-abcdefghijklmnopqrstuvwxyz';

type Reply = Response | Promise<Response>;
type Routes = Record<string, (path: string, init: RequestInit) => Reply>;

function json(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

/** Answers by pathname (a trailing `*` matches a prefix); unknown paths fail the test. */
function mockApi(routes: Routes) {
  const fetchMock = vi.fn(async (path: string, init: RequestInit) => {
    const pathname = new URL(path, 'http://test').pathname;
    const key = Object.keys(routes).find((k) =>
      k.endsWith('*') ? pathname.startsWith(k.slice(0, -1)) : pathname === k,
    );
    if (key === undefined) {
      throw new Error(`unexpected request ${pathname}`);
    }
    return (routes[key] as Routes[string])(path, init);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const signedInRoutes = (extra: Routes = {}): Routes => ({
  '/returns/capabilities': () => json(200, f.capabilitiesImporter),
  '/returns': () => json(200, f.returnsPage1),
  ...extra,
});

function deferred() {
  let resolve: (value: Response) => void = () => undefined;
  const promise = new Promise<Response>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

let logs: () => string;

beforeEach(() => {
  const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const error = vi.spyOn(console, 'error');
  logs = () =>
    [...info.mock.calls, ...warn.mock.calls, ...error.mock.calls].map((a) => a.map(String).join(' ')).join('\n');
  localStorage.clear();
  sessionStorage.clear();
});

async function signIn(user: UserEvent, token = TOKEN) {
  await user.type(screen.getByLabelText('Access token'), token);
  await user.click(screen.getByRole('button', { name: 'Sign in' }));
}

async function signedIn(routes: Routes = signedInRoutes()) {
  const fetchMock = mockApi(routes);
  const user = userEvent.setup();
  const view = render(<ReturnsPage />);
  await signIn(user);
  await screen.findByTestId('returns-signed-in');
  return { user, fetchMock, view };
}

function calls(fetchMock: ReturnType<typeof mockApi>): [string, RequestInit][] {
  return fetchMock.mock.calls as unknown as [string, RequestInit][];
}

function authHeader(init: RequestInit): string | undefined {
  return (init.headers as Record<string, string>).Authorization;
}

async function openSection(user: UserEvent, name: string) {
  await user.click(within(screen.getByRole('navigation', { name: 'Retailer sections' })).getByRole('button', { name }));
}

describe('access gate', () => {
  it('signs in with the token, clears the input and shows the returns list', async () => {
    const { fetchMock } = await signedIn();
    const [path, init] = calls(fetchMock)[0] as [string, RequestInit];
    expect(path).toBe('/returns/capabilities');
    expect(authHeader(init)).toBe(`Bearer ${TOKEN}`);
    expect(await screen.findAllByTestId('return-row')).toHaveLength(2);
    expect(screen.getAllByTestId('synthetic-badge')).toHaveLength(2);
    expect(screen.queryByLabelText('Access token')).toBeNull();
  });

  it('keeps the token out of storage, cookies, the DOM and the logs', async () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const { fetchMock } = await signedIn();
    await screen.findAllByTestId('return-row');
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(setItem).not.toHaveBeenCalled();
    expect(document.cookie).toBe('');
    expect(document.documentElement.outerHTML).not.toContain(TOKEN);
    expect(logs()).not.toContain(TOKEN);
    // Every request went to a /returns path, each with the token.
    for (const [path, init] of calls(fetchMock)) {
      expect(path).toMatch(/^\/returns(?:[/?]|$)/);
      expect(authHeader(init)).toBe(`Bearer ${TOKEN}`);
    }
  });

  it('explains an unauthorized token and clears the input', async () => {
    mockApi({ '/returns/capabilities': () => json(401, f.errorUnauthorized, { 'X-Request-ID': 'server-id-0401' }) });
    const user = userEvent.setup();
    render(<ReturnsPage />);
    await signIn(user, 'wrong-token-value-abcdefghijklmnopqrstuvwxyz');
    const alert = await screen.findByTestId('gate-error');
    expect(alert).toHaveTextContent('Your access token was not accepted. Sign in again.');
    expect(alert).toHaveTextContent('Request ID: server-id-0401');
    expect(screen.getByLabelText('Access token')).toHaveValue('');
    expect(screen.queryByTestId('returns-signed-in')).toBeNull();
  });

  it('says when retailer access is not configured', async () => {
    mockApi({ '/returns/capabilities': () => json(503, f.errorNotConfigured) });
    const user = userEvent.setup();
    render(<ReturnsPage />);
    await signIn(user);
    expect(await screen.findByTestId('gate-not-configured')).toHaveTextContent(
      'Retailer access is not configured on this server.',
    );
  });

  it('does not send an empty token', async () => {
    const fetchMock = mockApi({});
    const user = userEvent.setup();
    render(<ReturnsPage />);
    await user.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('signs out, clearing the token and the retailer data', async () => {
    const { user } = await signedIn();
    await screen.findAllByTestId('return-row');
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(screen.getByTestId('gate-status')).toHaveTextContent('Signed out');
    expect(screen.queryAllByTestId('return-row')).toHaveLength(0);
    expect(screen.getByLabelText('Access token')).toHaveValue('');
    expect(document.documentElement.outerHTML).not.toContain('FIX-RET-0001');
  });

  it('drops a response that arrives after sign-out', async () => {
    const pending = deferred();
    const { user } = await signedIn(signedInRoutes({ '/returns': () => pending.promise }));
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    pending.resolve(json(200, f.returnsPage1));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByTestId('returns-signed-in')).toBeNull();
    expect(screen.queryAllByTestId('return-row')).toHaveLength(0);
  });

  it('signs out on a 401 during the session and says why', async () => {
    const { user } = await signedIn();
    await screen.findAllByTestId('return-row');
    mockApi({ '/returns*': () => json(401, f.errorUnauthorized) });
    await user.click(screen.getByRole('button', { name: 'View return FIX-RET-0001' }));
    expect(await screen.findByTestId('gate-expired')).toHaveTextContent('You were signed out');
    expect(screen.queryByTestId('returns-signed-in')).toBeNull();
    expect(document.documentElement.outerHTML).not.toContain('FIX-RET-0001');
  });

  it('is the third app tab and leaves the buyer tabs in place', async () => {
    mockApi({});
    const user = userEvent.setup();
    render(<App />);
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
      'Search',
      'Shopping assistant',
      'Retailer returns',
    ]);
    await user.click(screen.getByRole('tab', { name: 'Retailer returns' }));
    expect(screen.getByRole('tabpanel', { name: 'Retailer returns' })).toBeVisible();
    expect(screen.getByLabelText('Access token')).toBeVisible();
  });
});

describe('returns list', () => {
  it('shows loading, then an empty state', async () => {
    const pending = deferred();
    await signedIn(signedInRoutes({ '/returns': () => pending.promise }));
    expect(screen.getByTestId('returns-status')).toHaveTextContent('Loading returns…');
    pending.resolve(json(200, f.returnsPageEmpty));
    expect(await screen.findByTestId('returns-empty')).toHaveTextContent('No returns match these filters.');
  });

  it('shows a server error with its request ID', async () => {
    await signedIn(signedInRoutes({ '/returns': () => json(500, { detail: 'Internal Server Error', code: 'internal_error' }, { 'X-Request-ID': 'server-id-0500' }) }));
    const alert = await screen.findByTestId('returns-error');
    expect(alert).toHaveTextContent('Something went wrong on the server.');
    expect(within(alert).getByTestId('request-id')).toHaveTextContent('Request ID: server-id-0500');
  });

  it('names invalid filters without echoing their values', async () => {
    const { user, fetchMock } = await signedIn();
    await screen.findAllByTestId('return-row');
    mockApi(signedInRoutes({ '/returns': () => json(422, f.errorInvalidParameter) }));
    await user.type(screen.getByLabelText('Requested from (inclusive)'), 'yesterday');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));
    const alert = await screen.findByTestId('returns-error');
    expect(alert).toHaveTextContent('Some values are not valid.');
    expect(alert).toHaveTextContent('requested_from (format)');
    expect(alert).not.toHaveTextContent('yesterday');
    expect(fetchMock).toHaveBeenCalled();
  });

  it('pages forward with the cursor and the same filters, back without it, and resets on new filters', async () => {
    const pages = [f.returnsPage1, f.returnsPage1, f.returnsPage2, f.returnsPage1];
    const { user, fetchMock } = await signedIn(
      signedInRoutes({ '/returns': () => json(200, pages.shift() ?? f.returnsPage1) }),
    );
    await screen.findAllByTestId('return-row');
    await user.selectOptions(screen.getByLabelText('Status'), 'received');
    await user.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() => expect(screen.getAllByTestId('return-row')).toHaveLength(2));
    await user.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(screen.getAllByTestId('return-row')).toHaveLength(1));
    expect(screen.getByText('Page 2')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();
    // A draft change does not alter the chain until it is applied.
    await user.selectOptions(screen.getByLabelText('Category'), 'shoes');
    await user.click(screen.getByRole('button', { name: 'Previous' }));
    await waitFor(() => expect(screen.getAllByTestId('return-row')).toHaveLength(2));
    const paths = calls(fetchMock).map(([path]) => path).filter((p) => p.startsWith('/returns?') || p === '/returns');
    expect(paths).toEqual([
      '/returns',
      '/returns?status=received',
      '/returns?status=received&cursor=fixture-cursor-page-two',
      '/returns?status=received',
    ]);
    expect(screen.getByText('Page 1')).toBeInTheDocument();
  });
});

describe('return detail', () => {
  async function openDetail(detail: unknown, name = 'View return FIX-RET-0001') {
    const ctx = await signedIn(signedInRoutes({ '/returns/records/*': () => json(200, detail) }));
    await ctx.user.click(await screen.findByRole('button', { name }));
    await screen.findByTestId('return-detail');
    return ctx;
  }

  it('shows statements and observations side by side without a flag', async () => {
    const { fetchMock } = await openDetail(f.returnDetailConflict);
    const columns = screen.getByTestId('statements-and-observations');
    const statements = within(columns).getByTestId('customer-statements');
    const observations = within(columns).getByTestId('inspection-observations');
    expect(statements).toHaveTextContent('ARRIVED_DAMAGED');
    expect(within(statements).getAllByTestId('customer-comment')).toHaveLength(3);
    expect(observations).toHaveTextContent('as_new');
    const detail = screen.getByTestId('return-detail');
    for (const word of ['conflict', 'flag', 'fraud', 'suspicious', 'refund approved', 'score']) {
      expect(detail.textContent?.toLowerCase()).not.toContain(word);
    }
    expect(calls(fetchMock).at(-1)?.[0]).toBe('/returns/records/fixture-returns/1/FIX-RET-0001');
  });

  it('renders markup and prompt-injection text as inert text', async () => {
    const { view } = await openDetail(f.returnDetailConflict);
    const texts = f.returnDetailConflict.customer_statements.comments.map((c) => c.text);
    for (const text of texts) {
      expect(screen.getByText(text)).toBeInTheDocument();
    }
    expect(view.container.querySelector('script, img, b')).toBeNull();
  });

  it('shows provenance, the listing snapshot and every related record', async () => {
    await openDetail(f.returnDetailConflict);
    expect(screen.getAllByTestId('provenance').length).toBeGreaterThanOrEqual(10);
    const detail = screen.getByTestId('return-detail');
    expect(detail).toHaveTextContent('synthetic-seed version 1');
    expect(detail).toHaveTextContent('Matches current catalog');
    expect(detail).toHaveTextContent('INR 4699');
    expect(screen.getByTestId('shipment')).toHaveTextContent('FIX-SHP-0001');
    expect(screen.getByTestId('related-reviews')).toHaveTextContent('FIX-REV-0001');
    expect(screen.getByTestId('related-complaints')).toHaveTextContent('FIX-CMP-0001');
    expect(screen.getByTestId('correction-cases')).toHaveTextContent('FIX-CASE-0001');
  });

  it('says "Not recorded" for every absent kind of evidence', async () => {
    await openDetail(f.returnDetailNotRecorded, 'View return FIX-RET-0002');
    const presence = screen.getByTestId('evidence-presence');
    expect(within(presence).getAllByText(/Not recorded/)).toHaveLength(7);
    for (const id of ['customer-statements', 'inspection-observations', 'shipment', 'related-reviews']) {
      expect(screen.getByTestId(id)).toHaveTextContent('Not recorded');
    }
    expect(screen.getByTestId('return-detail')).toHaveTextContent('INR Rs 44,999');
  });

  it('goes back to the list it came from', async () => {
    const { user } = await openDetail(f.returnDetailConflict);
    await user.click(screen.getByRole('button', { name: 'Back to the list' }));
    expect(screen.queryByTestId('return-detail')).toBeNull();
    expect(screen.getAllByTestId('return-row')).toHaveLength(2);
  });
});

describe('import panel', () => {
  const bundle = () => new File(['{"contract_version":"return-data-v1"}'], 'returns_v1.json', { type: 'application/json' });

  async function importPanel(routes: Routes = {}, capabilities: unknown = f.capabilitiesImporter) {
    const ctx = await signedIn(signedInRoutes({ '/returns/capabilities': () => json(200, capabilities), ...routes }));
    await openSection(ctx.user, 'Import');
    return ctx;
  }

  // The section navigation also has an "Import" button; the action button is inside the panel.
  const importRegion = () => screen.getByRole('region', { name: 'Import' });

  it('validates a JSON bundle as the part bundle.json and shows the report', async () => {
    const { user, fetchMock } = await importPanel({ '/returns/import/validate': () => json(200, f.reportValid) });
    await user.upload(screen.getByLabelText('Bundle file'), bundle());
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(await screen.findByTestId('report-status')).toHaveTextContent('Valid');
    const [path, init] = calls(fetchMock).at(-1) as [string, RequestInit];
    expect(path).toBe('/returns/import/validate');
    expect(authHeader(init)).toBe(`Bearer ${TOKEN}`);
    expect([...(init.body as FormData).keys()]).toEqual(['bundle.json']);
    expect(screen.getByTestId('error-summary')).toHaveTextContent('No errors.');
  });

  it('shows the truncation text and the error table as text', async () => {
    const { user, view } = await importPanel({ '/returns/import/validate': () => json(200, f.reportInvalidTruncated) });
    await user.upload(screen.getByLabelText('Bundle file'), bundle());
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(await screen.findByTestId('error-summary')).toHaveTextContent('Showing 200 of at least 345 errors.');
    expect(within(screen.getByTestId('error-table')).getAllByRole('row')).toHaveLength(201);
    expect(screen.getByText('<b>99</b>')).toBeInTheDocument();
    expect(view.container.querySelector('b')).toBeNull();
  });

  it('shows the lower-bound text', async () => {
    const { user } = await importPanel({ '/returns/import/validate': () => json(200, f.reportInvalidLowerBound) });
    await user.upload(screen.getByLabelText('Bundle file'), bundle());
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(await screen.findByTestId('error-summary')).toHaveTextContent('at least 3 in total');
  });

  it('imports and shows the report, including a 422 invalid report', async () => {
    const replies = [json(201, f.reportImported), json(422, f.reportImportInvalid)];
    const { user } = await importPanel({ '/returns/import': () => replies.shift() as Response });
    await user.upload(screen.getByLabelText('Bundle file'), bundle());
    await user.click(within(importRegion()).getByRole('button', { name: 'Import' }));
    expect(await screen.findByTestId('import-report')).toHaveAttribute('data-status', 'imported');
    await user.click(within(importRegion()).getByRole('button', { name: 'Import' }));
    await waitFor(() => expect(screen.getByTestId('import-report')).toHaveAttribute('data-status', 'invalid'));
    expect(screen.getByTestId('error-summary')).toHaveTextContent('1 error.');
  });

  it('shows "Imports are disabled" and no Import button when the caller cannot commit', async () => {
    await importPanel({}, f.capabilitiesViewer);
    expect(screen.getByTestId('imports-disabled')).toHaveTextContent('Imports are disabled on this server.');
    expect(within(importRegion()).queryByRole('button', { name: 'Import' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Validate' })).toBeInTheDocument();
  });

  it('offers no upload when the caller cannot validate', async () => {
    await importPanel({}, { ...f.capabilitiesViewer, can_validate: false, scopes: ['returns:read'] });
    expect(screen.getByTestId('validation-unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Validate' })).toBeNull();
  });

  it('shows a server refusal of an import with its code text and request ID', async () => {
    const { user } = await importPanel({
      '/returns/import': () => json(403, f.errorImportDisabled, { 'X-Request-ID': 'server-id-0403' }),
    });
    await user.upload(screen.getByLabelText('Bundle file'), bundle());
    await user.click(within(importRegion()).getByRole('button', { name: 'Import' }));
    const alert = await screen.findByTestId('returns-error');
    expect(alert).toHaveTextContent('Imports are disabled on this server.');
    expect(alert).toHaveTextContent('Request ID: server-id-0403');
  });

  it('pre-checks a CSV set without sending it', async () => {
    const { user, fetchMock } = await importPanel();
    const before = fetchMock.mock.calls.length;
    await user.click(screen.getByLabelText('CSV set (manifest.json and entity CSV files)'));
    await user.upload(screen.getByLabelText('CSV set files'), [new File(['a'], 'orders.csv')]);
    await user.click(screen.getByRole('button', { name: 'Validate' }));
    expect(screen.getByTestId('upload-problems')).toHaveTextContent('needs its manifest.json');
    expect(fetchMock.mock.calls.length).toBe(before);
  });
});

describe('import history', () => {
  it('lists batches and shows one batch with its file metadata', async () => {
    const { user, fetchMock } = await signedIn(
      signedInRoutes({
        '/returns/import-batches': () => json(200, f.batchesPage),
        '/returns/import-batches/*': () => json(200, f.batchDetail),
      }),
    );
    await openSection(user, 'Import history');
    const table = await screen.findByTestId('batch-table');
    expect(within(table).getAllByRole('row')).toHaveLength(3);
    await user.click(within(table).getByRole('button', { name: f.batchDetail.batch_id }));
    const detail = await screen.findByTestId('batch-detail');
    for (const file of f.batchDetail.files) {
      expect(detail).toHaveTextContent(file.name);
    }
    expect(calls(fetchMock).at(-1)?.[0]).toBe(`/returns/import-batches/${f.batchDetail.batch_id}`);
  });

  it('shows an empty history', async () => {
    const { user } = await signedIn(signedInRoutes({ '/returns/import-batches': () => json(200, f.batchesPageEmpty) }));
    await openSection(user, 'Import history');
    expect(await screen.findByTestId('batches-empty')).toHaveTextContent('No imports have been recorded.');
  });

  it('asks to restart paging on a cursor mismatch', async () => {
    const { user } = await signedIn(signedInRoutes({ '/returns/import-batches': () => json(400, f.errorCursorMismatch) }));
    await openSection(user, 'Import history');
    expect(await screen.findByTestId('returns-error')).toHaveTextContent('Start again from the first page.');
  });
});

// Pre-commit review: the page (the session owner) stays mounted across sign-out and a new
// sign-in. The old session's request is answered late by a mock that ignores its AbortSignal.
describe('session isolation', () => {
  async function twoSessions(late: Promise<Response>) {
    const listReplies: (() => Reply)[] = [() => late, () => json(200, f.returnsPage2)];
    const fetchMock = mockApi(signedInRoutes({ '/returns': () => (listReplies.shift() as () => Reply)() }));
    const user = userEvent.setup();
    render(<ReturnsPage />);
    await signIn(user);
    await screen.findByTestId('returns-signed-in');
    const oldInit = (calls(fetchMock).at(-1) as [string, RequestInit])[1];
    await user.click(screen.getByRole('button', { name: 'Sign out' }));
    await signIn(user, 'second-synthetic-token-abcdefghijklmnopqrstuvwxyz');
    await screen.findByTestId('returns-signed-in');
    await waitFor(() => expect(screen.getAllByTestId('return-row')).toHaveLength(1));
    // The old request was cancelled, but the mock answers it anyway.
    expect(oldInit.signal?.aborted).toBe(true);
    return { fetchMock };
  }

  async function settle() {
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  function assertNewSessionIntact() {
    expect(screen.getByTestId('returns-signed-in')).toBeInTheDocument();
    expect(screen.queryByTestId('gate-expired')).toBeNull();
    expect(screen.queryByTestId('returns-error')).toBeNull();
    const rows = screen.getAllByTestId('return-row');
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent('FIX-RET-0003');
    expect(document.documentElement.outerHTML).not.toContain('FIX-RET-0001');
  }

  it('drops old-session data that arrives during the new session', async () => {
    const late = deferred();
    await twoSessions(late.promise);
    late.resolve(json(200, f.returnsPage1));
    await settle();
    assertNewSessionIntact();
  });

  it('drops an old-session error that arrives during the new session', async () => {
    const late = deferred();
    await twoSessions(late.promise);
    late.resolve(json(500, { detail: 'Internal Server Error', code: 'internal_error' }, { 'X-Request-ID': 'server-id-old1' }));
    await settle();
    assertNewSessionIntact();
    expect(document.documentElement.outerHTML).not.toContain('server-id-old1');
  });

  it('does not sign the new session out on a late old-session 401', async () => {
    const late = deferred();
    await twoSessions(late.promise);
    late.resolve(json(401, f.errorUnauthorized));
    await settle();
    assertNewSessionIntact();
  });

  it('sends the new token only, never the old one, after re-sign-in', async () => {
    const late = deferred();
    const { fetchMock } = await twoSessions(late.promise);
    const after = calls(fetchMock).slice(-2);
    for (const [, init] of after) {
      expect(authHeader(init)).toBe('Bearer second-synthetic-token-abcdefghijklmnopqrstuvwxyz');
    }
    late.resolve(json(200, f.returnsPage1));
  });

  it('labels timestamps as UTC', async () => {
    await signedIn();
    expect((await screen.findAllByTestId('return-row'))[0]).toHaveTextContent(
      'Requested 2026-03-04T06:30:00.000000Z (UTC)',
    );
  });
});

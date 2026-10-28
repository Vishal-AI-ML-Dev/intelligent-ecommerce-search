// Fixture-backed browser tests for the "Retailer returns" tab (M18-06, mocked API). Not a
// live-stack result. The page is the built bundle as FastAPI serves it (with its real
// Content-Security-Policy); every API request is answered in the browser from the synthetic
// fixtures in fixtures/returns/, so no request reaches the API, a database or a model. Files are
// given to the real <input type="file"> with setInputFiles; this does not exercise the operating
// system's file picker or a password manager.
import { expect, type Page, type Request, type Route, test } from '@playwright/test';
import { assistantFixtures, fixtures, returnsFixtures as rf } from './fixtures';

const TOKEN_A = 'fixture-token-a-0123456789abcdefghijklmnopqrstuv';
const TOKEN_B = 'fixture-token-b-0123456789abcdefghijklmnopqrstuv';
const QUERY = 'laptop 8gb 256 ssd';

const CSP =
  "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; " +
  "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'";

interface ApiCall {
  method: string;
  path: string;
  params: [string, string][];
  authorization: string | undefined;
  requestId: string | undefined;
  contentType: string | undefined;
  parts: { name: string; filename: string }[];
}

interface Harness {
  calls: ApiCall[];
  buyerCalls: ApiCall[];
  statusCalls: ApiCall[];
  console: string[];
  pageErrors: string[];
}

type Reply = { status: number; body: unknown } | 'hold';
type Responder = (call: ApiCall, route: Route) => Reply;

const ok = (body: unknown, status = 200): Reply => ({ status, body });

function multipartParts(request: Request): { name: string; filename: string }[] {
  const body = request.postDataBuffer();
  if (body === null) {
    return [];
  }
  const text = body.toString('latin1');
  return [...text.matchAll(/Content-Disposition: form-data; name="([^"]*)"; filename="([^"]*)"/g)].map(
    (m) => ({ name: m[1] ?? '', filename: m[2] ?? '' }),
  );
}

function describeCall(request: Request): ApiCall {
  const url = new URL(request.url());
  const headers = request.headers();
  const contentType = headers['content-type'];
  return {
    method: request.method(),
    path: url.pathname,
    params: [...url.searchParams.entries()],
    authorization: headers.authorization,
    requestId: headers['x-request-id'],
    contentType,
    parts: contentType?.startsWith('multipart/form-data') ? multipartParts(request) : [],
  };
}

/**
 * Loads the served page with a catch-all router: only the page and its hashed assets reach the
 * server; `/returns` calls go to `respond`; buyer search and assistant calls get fixed fixture
 * answers; anything else is recorded as unexpected and aborted. CSP violation events are
 * collected in the page from the first script on.
 */
async function setup(page: Page, respond: Responder): Promise<Harness> {
  const harness: Harness = { calls: [], buyerCalls: [], statusCalls: [], console: [], pageErrors: [] };
  page.on('console', (message) => harness.console.push(`${message.type()} ${message.text()}`));
  page.on('pageerror', (error) => harness.pageErrors.push(error.message));
  page.on('dialog', (dialog) => {
    harness.pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    void dialog.dismiss();
  });
  await page.addInitScript(() => {
    const store = window as unknown as { __cspViolations: string[] };
    store.__cspViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      store.__cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  await page.route('**/*', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/' || path.startsWith('/assets/')) {
      await route.continue();
      return;
    }
    if (path === '/returns' || path.startsWith('/returns/')) {
      const call = describeCall(request);
      harness.calls.push(call);
      const reply = respond(call, route);
      if (reply !== 'hold') {
        await route.fulfill({ status: reply.status, contentType: 'application/json', body: JSON.stringify(reply.body) });
      }
      return;
    }
    if (request.method() === 'GET' && path === '/assistant/status') {
      // The assistant tab's own status check (as in assistant-fixture.spec.ts).
      harness.statusCalls.push(describeCall(request));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ generation_enabled: false }) });
      return;
    }
    if (request.method() === 'POST' && (path === '/search/reranked' || path === '/assistant/answer')) {
      harness.buyerCalls.push(describeCall(request));
      const body = path === '/search/reranked' ? fixtures.applied : assistantFixtures.answer;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      return;
    }
    harness.pageErrors.push(`unexpected request ${request.method()} ${path}`);
    await route.abort();
  });
  await page.goto('/');
  await page.getByRole('tab', { name: 'Retailer returns' }).click();
  return harness;
}

/** The standard server: viewer or importer capabilities, page 1/2, details and history. */
function server(capabilities: unknown = rf.capabilitiesViewer, overrides: Partial<Record<string, Reply>> = {}): Responder {
  return (call) => {
    const key = `${call.method} ${call.path}`;
    const override = overrides[key];
    if (override !== undefined) {
      return override;
    }
    if (key === 'GET /returns/capabilities') return ok(capabilities);
    if (key === 'GET /returns') {
      const cursor = call.params.find(([name]) => name === 'cursor');
      return ok(cursor?.[1] === 'fixture-cursor-page-two' ? rf.returnsPage2 : rf.returnsPage1);
    }
    if (key === 'GET /returns/records/fixture-returns/1/FIX-RET-0001') return ok(rf.returnDetailConflict);
    if (key === 'GET /returns/records/fixture-returns/1/FIX-RET-0002') return ok(rf.returnDetailNotRecorded);
    if (key === 'GET /returns/import-batches') return ok(rf.batchesPage);
    if (call.method === 'GET' && call.path.startsWith('/returns/import-batches/')) return ok(rf.batchDetail);
    if (key === 'POST /returns/import/validate') return ok(rf.reportValid);
    if (key === 'POST /returns/import') return ok(rf.reportImported, 201);
    return ok({ detail: 'Not found.', code: 'not_found' }, 404);
  };
}

async function signIn(page: Page, token = TOKEN_A) {
  await page.getByLabel('Access token').fill(token);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

async function signedIn(page: Page, token = TOKEN_A) {
  await signIn(page, token);
  await expect(page.getByTestId('returns-signed-in')).toBeVisible();
  await expect(page.getByTestId('return-row').first()).toBeVisible();
}

const section = (page: Page, name: string) =>
  page.getByRole('navigation', { name: 'Retailer sections' }).getByRole('button', { name, exact: true });

// The upload form's Import button (the section switcher has a button with the same name).
const importButton = (page: Page) =>
  page.locator('form.returns-upload').getByRole('button', { name: 'Import', exact: true });

const rowIds = (page: Page) =>
  page.getByTestId('return-row').locator('strong').allTextContents();

/** No page errors, unexpected requests, dialogs or CSP violations; console only the client's own lines. */
async function expectClean(page: Page, harness: Harness, tokens: string[] = [TOKEN_A, TOKEN_B]) {
  expect(harness.pageErrors).toEqual([]);
  expect(harness.statusCalls.every((c) => c.authorization === undefined)).toBe(true);
  const violations = await page.evaluate(() => (window as unknown as { __cspViolations: string[] }).__cspViolations);
  expect(violations).toEqual([]);
  for (const line of harness.console) {
    // The client's one-line request logs, plus Chromium's own note for a non-2xx response.
    expect(line).toMatch(/^((info|warning) \[api\] |error Failed to load resource: the server responded with a status of )/);
    for (const token of tokens) {
      expect(line.includes(token), 'a token must never reach the console').toBe(false);
    }
  }
}

// ---- F7: the built page and its headers -------------------------------------------------------

test('F7 the served page carries its CSP; /returns is never the page', async ({ page, request }) => {
  const harness: Harness = { calls: [], buyerCalls: [], statusCalls: [], console: [], pageErrors: [] };
  page.on('pageerror', (error) => harness.pageErrors.push(error.message));
  const response = await page.goto('/');
  expect(response?.status()).toBe(200);
  const headers = response?.headers() ?? {};
  expect(headers['content-security-policy']).toBe(CSP);
  expect(headers['x-content-type-options']).toBe('nosniff');
  expect(headers['referrer-policy']).toBe('no-referrer');

  // A direct request to the list route without a token is answered by the API (401, or 503 when
  // retailer access is not configured on this server), never by the page.
  const bare = await request.get('/returns');
  expect([401, 503]).toContain(bare.status());
  expect(bare.headers()['content-type']).toContain('application/json');
  expect(bare.headers()['cache-control']).toBe('no-store');
  expect(bare.headers()['content-security-policy']).toBeUndefined();
  expect(harness.pageErrors).toEqual([]);
});

// ---- F1: access gate --------------------------------------------------------------------------

test('F1 sign-in sends the token only to /returns routes and clears the input', async ({ page }) => {
  const harness = await setup(page, server());
  const input = page.getByLabel('Access token');
  await expect(input).toHaveAttribute('type', 'password');
  await signedIn(page);
  expect(harness.calls[0]?.method).toBe('GET');
  expect(harness.calls[0]?.path).toBe('/returns/capabilities');
  for (const call of harness.calls) {
    expect(call.authorization).toBe(`Bearer ${TOKEN_A}`);
  }
  await expect(page.getByLabel('Access token')).toHaveCount(0);
  await expect(page.getByText('Access: returns:read, returns:validate · contract return-data-v1')).toBeVisible();
  await expectClean(page, harness);
});

test('F1 a rejected token shows the fixed message and the request ID, not the server text', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesViewer, { 'GET /returns/capabilities': ok(rf.errorUnauthorized, 401) }));
  await signIn(page);
  const notice = page.getByTestId('gate-error');
  await expect(notice).toContainText('Your access token was not accepted. Sign in again.');
  await expect(notice.getByTestId('request-id')).toHaveText(`Request ID: ${harness.calls[0]?.requestId}`);
  await expect(page.getByText(rf.errorUnauthorized.detail)).toHaveCount(0);
  await expect(page.getByLabel('Access token')).toHaveValue('');
  await expectClean(page, harness);
});

test('F1 unconfigured retailer access shows the fixed 503 message', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesViewer, { 'GET /returns/capabilities': ok(rf.errorNotConfigured, 503) }));
  await signIn(page);
  await expect(page.getByTestId('gate-not-configured')).toHaveText('Retailer access is not configured on this server.');
  await expect(page.getByTestId('returns-signed-in')).toHaveCount(0);
  await expectClean(page, harness);
});

// ---- F2: permissions --------------------------------------------------------------------------

test('F2 a viewer token can validate but has no Import control', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesViewer));
  await signedIn(page);
  await section(page, 'Import').click();
  await expect(page.getByRole('button', { name: 'Validate' })).toBeVisible();
  await expect(importButton(page)).toHaveCount(0);
  await expect(page.getByTestId('imports-disabled')).toHaveText('Imports are disabled on this server.');
  await expectClean(page, harness);
});

test('F2 an importer token gets Import; a server-side 403 keeps the fixed text', async ({ page }) => {
  const harness = await setup(
    page,
    server(rf.capabilitiesImporter, { 'POST /returns/import': ok(rf.errorImportDisabled, 403) }),
  );
  await signedIn(page);
  await section(page, 'Import').click();
  await expect(page.getByTestId('imports-disabled')).toHaveCount(0);
  await page.getByLabel('Bundle file').setInputFiles({ name: 'b.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
  await importButton(page).click();
  await expect(page.getByTestId('returns-error')).toContainText('Imports are disabled on this server.');
  await expect(page.getByText(rf.errorImportDisabled.detail)).toHaveCount(0);
  await expectClean(page, harness);
});

// ---- F3: uploads through the file input -------------------------------------------------------

test('F3 a JSON bundle is sent as the part bundle.json whatever the local file name', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesImporter));
  await signedIn(page);
  await section(page, 'Import').click();
  await page.getByLabel('Bundle file').setInputFiles({
    name: 'my-local-export.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{"synthetic": true}'),
  });
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('import-report')).toHaveAttribute('data-status', 'valid');
  await expect(page.getByTestId('report-status')).toHaveText('Valid: the upload passed every check. Nothing was stored.');
  const validate = harness.calls.find((c) => c.path === '/returns/import/validate');
  expect(validate?.method).toBe('POST');
  expect(validate?.contentType).toMatch(/^multipart\/form-data; boundary=/);
  expect(validate?.parts).toEqual([{ name: 'bundle.json', filename: 'bundle.json' }]);
  expect(validate?.authorization).toBe(`Bearer ${TOKEN_A}`);

  await importButton(page).click();
  await expect(page.getByTestId('import-report')).toHaveAttribute('data-status', 'imported');
  await expect(page.getByTestId('report-status')).toHaveText('Imported.');
  expect(harness.calls.find((c) => c.path === '/returns/import')?.parts).toEqual([
    { name: 'bundle.json', filename: 'bundle.json' },
  ]);
  await expectClean(page, harness);
});

test('F3 an invalid import (422) is shown as a report, not an error', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesImporter, { 'POST /returns/import': ok(rf.reportImportInvalid, 422) }));
  await signedIn(page);
  await section(page, 'Import').click();
  await page.getByLabel('Bundle file').setInputFiles({ name: 'b.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
  await importButton(page).click();
  await expect(page.getByTestId('import-report')).toHaveAttribute('data-status', 'invalid');
  await expect(page.getByTestId('error-table')).toContainText('conflict_existing');
  await expect(page.getByTestId('returns-error')).toHaveCount(0);
  await expectClean(page, harness);
});

test('F3 a CSV set keeps its part names; truncation and lower-bound wording', async ({ page }) => {
  let report: unknown = rf.reportInvalidTruncated;
  const harness = await setup(page, (call, route) =>
    call.path === '/returns/import/validate' ? ok(report) : server()(call, route),
  );
  await signedIn(page);
  await section(page, 'Import').click();
  await page.getByLabel('CSV set (manifest.json and entity CSV files)').check();
  const files = ['manifest.json', 'returns.csv', 'orders.csv'].map((name) => ({
    name,
    mimeType: name.endsWith('.csv') ? 'text/csv' : 'application/json',
    buffer: Buffer.from(name.endsWith('.csv') ? 'id\n1\n' : '{}'),
  }));
  await page.getByLabel('CSV set files').setInputFiles(files);
  await page.getByRole('button', { name: 'Validate' }).click();
  const t = rf.reportInvalidTruncated;
  await expect(page.getByTestId('error-summary')).toHaveText(
    `Showing ${t.errors.length} of ${t.error_count_is_lower_bound ? 'at least ' : ''}${t.error_count} errors.`,
  );
  expect(harness.calls.find((c) => c.path === '/returns/import/validate')?.parts).toEqual(
    files.map(({ name }) => ({ name, filename: name })),
  );

  report = rf.reportInvalidLowerBound;
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('error-summary')).toContainText(`at least ${rf.reportInvalidLowerBound.error_count} in total`);
  await expectClean(page, harness);
});

test('F3 advisory pre-checks stop an incomplete or oversized upload before any request', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesImporter));
  await signedIn(page);
  await section(page, 'Import').click();
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('upload-problems')).toHaveText('Choose a file to upload.');

  const tooLarge = rf.capabilitiesImporter.limits.file_bytes + 1;
  await page.getByLabel('Bundle file').setInputFiles({ name: 'big.json', mimeType: 'application/json', buffer: Buffer.alloc(tooLarge, 0x20) });
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('upload-problems')).toContainText('Each file must be at most');

  await page.getByLabel('CSV set (manifest.json and entity CSV files)').check();
  await page.getByLabel('CSV set files').setInputFiles([{ name: 'returns.csv', mimeType: 'text/csv', buffer: Buffer.from('id\n') }]);
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('upload-problems')).toContainText('A CSV set needs its manifest.json.');
  expect(harness.calls.filter((c) => c.method === 'POST')).toEqual([]);
  await expectClean(page, harness);
});

// ---- F4: paging, filters, detail, history ------------------------------------------------------

test('F4 Next and Previous follow the cursor chain', async ({ page }) => {
  const harness = await setup(page, server());
  await signedIn(page);
  expect(await rowIds(page)).toEqual(['FIX-RET-0001', 'FIX-RET-0002']);
  await page.getByRole('navigation', { name: 'Returns pages' }).getByRole('button', { name: 'Next' }).click();
  await expect(page.getByTestId('return-row')).toHaveCount(1);
  expect(await rowIds(page)).toEqual(['FIX-RET-0003']);
  const lists = () => harness.calls.filter((c) => c.path === '/returns');
  expect(lists().at(-1)?.params).toEqual([['cursor', 'fixture-cursor-page-two']]);
  await page.getByRole('navigation', { name: 'Returns pages' }).getByRole('button', { name: 'Previous' }).click();
  await expect(page.getByTestId('return-row')).toHaveCount(2);
  expect(lists().at(-1)?.params).toEqual([]);
  await expect(page.getByTestId('synthetic-badge').first()).toHaveText('Synthetic demo data');
  await expect(page.getByTestId('return-row').first()).toContainText('(UTC)');
  await expectClean(page, harness);
});

test('F4 filters become query parameters; empty, cursor_mismatch and invalid_parameter states', async ({ page }) => {
  const harness = await setup(page, (call, route) => {
    if (call.path === '/returns' && call.params.some(([n]) => n === 'dataset_id')) {
      if (call.params.some(([n]) => n === 'cursor')) return ok(rf.errorCursorMismatch, 422);
      if (call.params.some(([n]) => n === 'requested_from')) return ok(rf.errorInvalidParameter, 422);
      return call.params.some(([n]) => n === 'has_comment') ? ok(rf.returnsPageEmpty) : ok(rf.returnsPage1);
    }
    return server()(call, route);
  });
  await signedIn(page);
  await page.getByLabel('Dataset ID').fill('  fixture-returns ');
  await page.getByLabel('Has a comment').selectOption('false');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.getByTestId('returns-empty')).toHaveText('No returns match these filters.');
  expect(harness.calls.at(-1)?.params).toEqual([
    ['dataset_id', 'fixture-returns'],
    ['has_comment', 'false'],
  ]);

  await page.getByLabel('Has a comment').selectOption('');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.getByTestId('return-row')).toHaveCount(2);
  await page.getByRole('navigation', { name: 'Returns pages' }).getByRole('button', { name: 'Next' }).click();
  await expect(page.getByTestId('returns-error')).toContainText(
    'This page belongs to different filters. Start again from the first page.',
  );
  expect(harness.calls.at(-1)?.params).toEqual([
    ['dataset_id', 'fixture-returns'],
    ['cursor', 'fixture-cursor-page-two'],
  ]);

  await page.getByLabel('Requested from (inclusive)').fill('not-a-date');
  await page.getByRole('button', { name: 'Apply filters' }).click();
  const notice = page.getByTestId('returns-error');
  await expect(notice).toContainText('Some values are not valid.');
  await expect(notice.getByRole('list', { name: 'Values to check' })).toHaveText('requested_from (format)');
  await expect(page.getByText(rf.errorInvalidParameter.detail)).toHaveCount(0);
  await expectClean(page, harness);
});

test('F4 detail: statements and observations side by side, provenance, Not recorded', async ({ page }) => {
  const harness = await setup(page, server());
  await signedIn(page);
  await page.getByRole('button', { name: 'View return FIX-RET-0001' }).click();
  await expect(page.getByTestId('return-detail')).toBeVisible();
  expect(harness.calls.at(-1)?.path).toBe('/returns/records/fixture-returns/1/FIX-RET-0001');

  const statements = await page.getByTestId('customer-statements').boundingBox();
  const observations = await page.getByTestId('inspection-observations').boundingBox();
  expect(statements && observations).toBeTruthy();
  if (statements && observations) {
    expect(observations.x).toBeGreaterThan(statements.x + statements.width - 1);
    expect(Math.abs(observations.y - statements.y)).toBeLessThan(2);
  }
  // Conflicting evidence is shown as recorded: no flag, score or resolution is added.
  await expect(page.getByTestId('return-detail')).not.toContainText(/conflict|fraud|suspicious|score/i);

  const provenance = page.getByTestId('provenance');
  await expect(provenance.first()).toHaveAttribute('open', '');
  const nested = page.getByTestId('customer-comment').first().getByTestId('provenance');
  await expect(nested).not.toHaveAttribute('open', '');
  await nested.locator('summary').click();
  await expect(nested).toHaveAttribute('open', '');
  await expect(nested).toContainText('Source record ID');

  await page.getByRole('button', { name: 'Back to the list' }).click();
  await page.getByRole('button', { name: 'View return FIX-RET-0002' }).click();
  await expect(page.getByTestId('evidence-presence')).toContainText('Not recorded');
  await expectClean(page, harness);
});

test('F4 import history lists batches and opens one batch', async ({ page }) => {
  const harness = await setup(page, server());
  await signedIn(page);
  await section(page, 'Import history').click();
  const table = page.getByTestId('batch-table');
  await expect(table.locator('tbody tr')).toHaveCount(rf.batchesPage.items.length);
  const first = rf.batchesPage.items[0]?.batch_id ?? '';
  await table.getByRole('button', { name: first }).click();
  await expect(page.getByTestId('batch-detail')).toContainText('returns.csv');
  expect(harness.calls.at(-1)?.path).toBe(`/returns/import-batches/${first}`);
  await expectClean(page, harness);
});

test('F4 empty import history', async ({ page }) => {
  const harness = await setup(page, server(rf.capabilitiesViewer, { 'GET /returns/import-batches': ok(rf.batchesPageEmpty) }));
  await signedIn(page);
  await section(page, 'Import history').click();
  await expect(page.getByTestId('batches-empty')).toHaveText('No imports have been recorded.');
  await expectClean(page, harness);
});

// ---- F5: hostile content stays text -----------------------------------------------------------

const HOSTILE = [
  '<img src=x onerror="window.__pwned=1">',
  '<script>window.__pwned=1</script>',
  '<a href="javascript:window.__pwned=1">open</a>',
  "{{constructor.constructor('window.__pwned=1')()}}",
  'rtl ‮gnp.exe‬ zero​width',
];

test('F5 hostile stored text, file names and report values render as text', async ({ page }) => {
  // Runtime copies of the validated fixtures; only free-text fields are replaced.
  const detail = structuredClone(rf.returnDetailConflict);
  detail.customer_statements.comments.forEach((comment, index) => {
    comment.text = HOSTILE[index % HOSTILE.length] ?? '';
  });
  const batch = structuredClone(rf.batchDetail);
  const hostileFile = HOSTILE[0] ?? '';
  if (batch.files[1]) batch.files[1].name = hostileFile;
  const report = structuredClone(rf.reportImportInvalid);
  if (report.errors[0]) {
    report.errors[0].message = HOSTILE[1] ?? '';
    report.errors[0].value_preview = HOSTILE[2] ?? '';
  }
  const harness = await setup(
    page,
    server(rf.capabilitiesImporter, {
      'GET /returns/records/fixture-returns/1/FIX-RET-0001': ok(detail),
      'POST /returns/import/validate': ok(report),
    }),
  );
  await page.route('**/returns/import-batches/*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(batch) }),
  );
  await signedIn(page);

  await page.getByRole('button', { name: 'View return FIX-RET-0001' }).click();
  const comments = page.getByTestId('customer-comment').locator('.returns-text');
  await expect(comments).toHaveText(detail.customer_statements.comments.map((c) => c.text));

  await section(page, 'Import').click();
  await page.getByLabel('Bundle file').setInputFiles({ name: 'b.json', mimeType: 'application/json', buffer: Buffer.from('{}') });
  await page.getByRole('button', { name: 'Validate' }).click();
  await expect(page.getByTestId('error-table')).toContainText(HOSTILE[1] ?? '');
  await expect(page.getByTestId('error-table')).toContainText(HOSTILE[2] ?? '');

  await section(page, 'Import history').click();
  await page.getByTestId('batch-table').getByRole('button', { name: rf.batchesPage.items[0]?.batch_id ?? '' }).click();
  await expect(page.getByTestId('batch-detail')).toContainText(hostileFile);

  // Nothing was parsed as markup or run.
  await expect(page.locator('[data-testid="returns-signed-in"] img, [data-testid="returns-signed-in"] script')).toHaveCount(0);
  await expect(page.locator('[data-testid="returns-signed-in"] a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  await expectClean(page, harness);
});

test('F5 a hostile server error detail is never displayed; the fixed text is', async ({ page }) => {
  const hostile = { detail: HOSTILE[0], code: 'unauthorized' };
  const harness = await setup(page, server(rf.capabilitiesViewer, { 'GET /returns/capabilities': ok(hostile, 401) }));
  await signIn(page);
  await expect(page.getByTestId('gate-error')).toContainText('Your access token was not accepted. Sign in again.');
  await expect(page.getByTestId('gate-error')).not.toContainText('onerror');
  expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  await expectClean(page, harness);
});

// ---- F6: token and session isolation ----------------------------------------------------------

test('F6 the token is kept out of storage, cookies, URL, DOM and buyer requests', async ({ page }) => {
  const harness = await setup(page, server());
  await signedIn(page);
  const found = await page.evaluate(async (token) => {
    const values = (storage: Storage) =>
      Array.from({ length: storage.length }, (_, i) => `${storage.key(i)}=${storage.getItem(storage.key(i) ?? '')}`);
    const databases = typeof indexedDB.databases === 'function' ? await indexedDB.databases() : [];
    return {
      local: values(localStorage).some((v) => v.includes(token)),
      session: values(sessionStorage).some((v) => v.includes(token)),
      cookie: document.cookie.includes(token),
      url: location.href.includes(token),
      dom: document.documentElement.outerHTML.includes(token),
      inputs: Array.from(document.querySelectorAll('input')).some((i) => i.value.includes(token)),
      // Existence only: with no IndexedDB database and no Cache Storage entry there is no content
      // to hold a token. If either ever exists, this check fails and its contents need a look.
      indexedDbCount: databases.length,
      cacheCount: (await caches.keys()).length,
    };
  }, TOKEN_A);
  expect(found).toEqual({
    local: false,
    session: false,
    cookie: false,
    url: false,
    dom: false,
    inputs: false,
    indexedDbCount: 0,
    cacheCount: 0,
  });
  expect((await page.context().cookies()).length).toBe(0);

  // Buyer requests made while signed in carry no Authorization header.
  await page.getByRole('tab', { name: 'Search' }).click();
  await page.getByLabel('Search the catalog').fill(QUERY);
  await page.getByRole('button', { name: 'Search' }).click();
  await expect(page.getByTestId('product-card').first()).toBeVisible();
  await page.getByRole('tab', { name: 'Shopping assistant' }).click();
  await page.getByLabel('Describe what you need').fill('a light laptop');
  await page.getByRole('button', { name: 'Ask' }).click();
  await expect.poll(() => harness.buyerCalls.length).toBe(2);
  expect(harness.buyerCalls.map((c) => [c.path, c.authorization])).toEqual([
    ['/search/reranked', undefined],
    ['/assistant/answer', undefined],
  ]);
  // The retailer session survives switching tabs.
  await page.getByRole('tab', { name: 'Retailer returns' }).click();
  await expect(page.getByTestId('returns-signed-in')).toBeVisible();
  await expectClean(page, harness);
});

test('F6 sign-out, a later 401 and a reload all drop the session', async ({ page }) => {
  let listStatus = 200;
  const harness = await setup(page, (call, route) =>
    call.path === '/returns' && listStatus === 401 ? ok(rf.errorUnauthorized, 401) : server()(call, route),
  );
  await signedIn(page);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByTestId('gate-status')).toHaveText('Signed out. The token and the retailer data were cleared.');
  await expect(page.getByTestId('return-row')).toHaveCount(0);
  const afterSignOut = harness.calls.length;

  await signedIn(page);
  listStatus = 401;
  await page.getByRole('button', { name: 'Apply filters' }).click();
  await expect(page.getByTestId('gate-expired')).toBeVisible();
  await expect(page.getByTestId('returns-signed-in')).toHaveCount(0);
  expect(harness.calls.length).toBeGreaterThan(afterSignOut);

  listStatus = 200;
  await signedIn(page);
  const beforeReload = harness.calls.length;
  await page.reload();
  await page.getByRole('tab', { name: 'Retailer returns' }).click();
  await expect(page.getByLabel('Access token')).toHaveValue('');
  await expect(page.getByTestId('returns-signed-in')).toHaveCount(0);
  expect(harness.calls.length).toBe(beforeReload);
  await expectClean(page, harness);
});

test('F6 a late response from an old session never reaches the new one', async ({ page }) => {
  const held: Route[] = [];
  const harness = await setup(page, (call, route) => {
    if (call.path === '/returns' && call.authorization === `Bearer ${TOKEN_A}`) {
      held.push(route);
      return 'hold';
    }
    if (call.path === '/returns' && call.authorization === `Bearer ${TOKEN_B}`) {
      return ok(rf.returnsPage2);
    }
    return server()(call, route);
  });
  await signIn(page, TOKEN_A);
  await expect.poll(() => held.length).toBe(1);
  await page.getByRole('button', { name: 'Sign out' }).click();
  await signIn(page, TOKEN_B);
  await expect(page.getByTestId('return-row')).toHaveCount(1);
  const firstB = harness.calls.findIndex((c) => c.authorization === `Bearer ${TOKEN_B}`);

  // The old request was aborted by the page; answering it anyway must change nothing.
  await held[0]
    ?.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(rf.returnsPage1) })
    .catch(() => undefined);
  await page.waitForTimeout(300);
  expect(await rowIds(page)).toEqual(['FIX-RET-0003']);
  await expect(page.getByTestId('returns-signed-in')).toBeVisible();
  expect(harness.calls.slice(firstB).every((c) => c.authorization === `Bearer ${TOKEN_B}`)).toBe(true);
  await expectClean(page, harness);
});

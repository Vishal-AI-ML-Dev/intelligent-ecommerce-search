// Live-stack browser tests for the "Retailer returns" tab (M18-06, AC-16). No mocks. Local only;
// never run in CI. Needs a server started by Playwright against a migrated scratch database with
// the committed synthetic catalog, the grants script applied, and the RETURNS_* settings of one
// mode in the environment:
//   @commit          viewer + importer tokens, RETURNS_IMPORT_COMMIT_ENABLED=true, importer
//                    database credentials and label (local development mode); empty database.
//   @validate-only   viewer token only, commit off, no importer credentials (the staging shape);
//                    run after @commit against the same database.
// Tokens come from the environment and are typed with keyboard.insertText, so they never appear
// in a locator call log; assertions compare them as booleans so a failure never prints one.
// Missing prerequisites make these tests fail, never pass.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, type Page, type Response, test } from '@playwright/test';

const repoRoot = path.resolve(import.meta.dirname, '..', '..');
const seedDir = path.join(repoRoot, 'data', 'seed', 'returns_v1');
const BUNDLE = path.join(seedDir, 'bundle', 'returns_v1.json');
const CSV_DIR = path.join(seedDir, 'csv');
const CSV_FILES = [
  'manifest.json',
  'orders.csv',
  'order_items.csv',
  'shipments.csv',
  'delivery_events.csv',
  'returns.csv',
  'return_comments.csv',
  'inspection_observations.csv',
  'customer_reviews.csv',
  'support_complaints.csv',
  'correction_cases.csv',
  'correction_case_evidence.csv',
].map((name) => path.join(CSV_DIR, name));
const provenance = JSON.parse(readFileSync(path.join(seedDir, 'provenance.json'), 'utf-8')) as {
  dataset_id: string;
  record_counts: Record<string, number>;
};
const RETURN_COUNT = provenance.record_counts.returns ?? 0;

function token(name: 'RETURNS_VIEWER_TOKEN' | 'RETURNS_IMPORTER_TOKEN'): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set for this run`);
  }
  return value;
}

/** Records every /returns request's Authorization header as "which token", never its value. */
function watchAuthorization(page: Page, expected: string): { mismatches: number; count: number } {
  const seen = { mismatches: 0, count: 0 };
  page.on('request', (request) => {
    const { pathname } = new URL(request.url());
    if (pathname === '/returns' || pathname.startsWith('/returns/')) {
      seen.count += 1;
      if (request.headers().authorization !== `Bearer ${expected}`) seen.mismatches += 1;
    } else if (request.headers().authorization !== undefined) {
      seen.mismatches += 1;
    }
  });
  return seen;
}

async function signIn(page: Page, value: string) {
  await page.goto('/');
  await page.getByRole('tab', { name: 'Retailer returns' }).click();
  await page.getByLabel('Access token').focus();
  await page.keyboard.insertText(value);
  await page.getByRole('button', { name: 'Sign in' }).click();
}

const section = (page: Page, name: string) =>
  page.getByRole('navigation', { name: 'Retailer sections' }).getByRole('button', { name, exact: true });
const uploadButton = (page: Page, name: 'Validate' | 'Import') =>
  page.locator('form.returns-upload').getByRole('button', { name, exact: true });
const isUpload = (action: 'validate' | 'import') => (r: Response) =>
  new URL(r.url()).pathname === (action === 'validate' ? '/returns/import/validate' : '/returns/import') &&
  r.request().method() === 'POST';

async function upload(page: Page, action: 'validate' | 'import', files: string | string[]) {
  await section(page, 'Import').click();
  if (Array.isArray(files)) {
    await page.getByLabel('CSV set (manifest.json and entity CSV files)').check();
    await page.getByLabel('CSV set files').setInputFiles(files);
  } else {
    await page.getByLabel('JSON bundle (one file)').check();
    await page.getByLabel('Bundle file').setInputFiles(files);
  }
  const responsePromise = page.waitForResponse(isUpload(action), { timeout: 90_000 });
  await uploadButton(page, action === 'validate' ? 'Validate' : 'Import').click();
  const response = await responsePromise;
  const body = (await response.json()) as { status: string; batch_id: string | null; counts: Record<string, { new: number | null }> };
  await expect(page.getByTestId('import-report')).toHaveAttribute('data-status', body.status, { timeout: 30_000 });
  return { status: response.status(), body };
}

async function allReturnIds(page: Page): Promise<string[]> {
  // No Apply filters click: after an import the page itself reloads the list (finding U1).
  await section(page, 'Returns').click();
  await expect(page.getByTestId('return-row').first()).toBeVisible();
  const ids: string[] = [];
  const pager = page.getByRole('navigation', { name: 'Returns pages' });
  for (let pageNo = 1; pageNo <= 50; pageNo += 1) {
    await expect(pager.getByText(`Page ${pageNo}`)).toBeVisible();
    await expect(page.getByTestId('returns-status')).toHaveText('');
    const keys = await page
      .getByTestId('return-row')
      .evaluateAll((rows) => rows.map((row) => `${row.querySelector('.product-meta')?.textContent}/${row.querySelector('strong')?.textContent}`));
    ids.push(...keys);
    const next = pager.getByRole('button', { name: 'Next' });
    if (await next.isDisabled()) {
      return ids;
    }
    await next.click();
  }
  throw new Error('more than 50 pages');
}

async function batchCount(page: Page): Promise<number> {
  await section(page, 'Import history').click();
  await page.getByRole('button', { name: 'Refresh' }).click();
  await expect(page.getByTestId('batch-table').or(page.getByTestId('batches-empty'))).toBeVisible();
  return page.getByTestId('batch-table').locator('tbody tr').count();
}

test.describe.serial('@commit local development mode', () => {
  test('importer: validate, import, list every return, detail, history, idempotent re-upload', async ({ page }) => {
    const importer = token('RETURNS_IMPORTER_TOKEN');
    const auth = watchAuthorization(page, importer);
    await signIn(page, importer);
    await expect(page.getByTestId('returns-signed-in')).toBeVisible();
    await expect(page.getByTestId('returns-empty')).toBeVisible();
    expect(await batchCount(page)).toBe(0);

    const validated = await upload(page, 'validate', BUNDLE);
    expect([validated.status, validated.body.status, validated.body.batch_id]).toEqual([200, 'valid', null]);
    expect(await batchCount(page)).toBe(0);

    const imported = await upload(page, 'import', BUNDLE);
    expect([imported.status, imported.body.status]).toEqual([201, 'imported']);
    expect(imported.body.counts.returns?.new).toBe(RETURN_COUNT);
    await expect(page.getByTestId('report-status')).toHaveText('Imported.');
    // Both sections were loaded before the import (empty); they show the new data without a
    // manual Apply filters or Refresh.
    await section(page, 'Import history').click();
    await expect(page.getByTestId('batch-table').locator('tbody tr')).toHaveCount(1);
    await section(page, 'Returns').click();
    await expect(page.getByTestId('returns-empty')).toHaveCount(0);
    await expect(page.getByTestId('return-row').first()).toBeVisible();

    const ids = await allReturnIds(page);
    expect(ids.length).toBe(RETURN_COUNT);
    expect(new Set(ids).size).toBe(RETURN_COUNT);
    expect(ids.every((id) => id.startsWith(`${provenance.dataset_id} v1/`))).toBe(true);

    await page.getByRole('navigation', { name: 'Returns pages' }).getByRole('button', { name: 'Previous' }).click();
    const firstRow = page.getByTestId('return-row').first();
    const firstId = (await firstRow.locator('strong').textContent()) ?? '';
    const detailResponse = page.waitForResponse((r) => new URL(r.url()).pathname.startsWith('/returns/records/'));
    await firstRow.getByRole('button', { name: `View return ${firstId}` }).click();
    expect((await detailResponse).status()).toBe(200);
    const detail = page.getByTestId('return-detail');
    await expect(detail).toContainText(`Return ${firstId}`);
    await expect(detail.getByTestId('synthetic-badge')).toBeVisible();
    await expect(detail.getByTestId('provenance').first()).toContainText(imported.body.batch_id ?? 'missing batch id');
    await expect(detail.getByTestId('customer-statements')).toBeVisible();
    await expect(detail.getByTestId('inspection-observations')).toBeVisible();
    await page.getByRole('button', { name: 'Back to the list' }).click();

    expect(await batchCount(page)).toBe(1);
    await page.getByTestId('batch-table').getByRole('button', { name: imported.body.batch_id ?? '' }).click();
    await expect(page.getByTestId('batch-detail')).toContainText('bundle.json');

    const again = await upload(page, 'import', BUNDLE);
    expect([again.status, again.body.status, again.body.batch_id]).toEqual([200, 'already_imported', imported.body.batch_id]);
    const csv = await upload(page, 'validate', CSV_FILES);
    expect([csv.status, csv.body.status]).toEqual([200, 'no_new_records']);
    expect(await batchCount(page)).toBe(1);

    expect(auth.count).toBeGreaterThan(0);
    expect(auth.mismatches, 'every /returns request carries the importer token and nothing else does').toBe(0);
  });

  test('viewer: reads the imported data and has no Import control; a wrong token is refused', async ({ page }) => {
    const viewer = token('RETURNS_VIEWER_TOKEN');
    const auth = watchAuthorization(page, viewer);
    await signIn(page, viewer);
    await expect(page.getByTestId('return-row').first()).toBeVisible();
    await section(page, 'Import').click();
    await expect(page.getByTestId('imports-disabled')).toHaveText('Imports are disabled on this server.');
    await expect(uploadButton(page, 'Import')).toHaveCount(0);
    expect(await batchCount(page)).toBe(1);
    expect(auth.mismatches).toBe(0);
    await page.getByRole('button', { name: 'Sign out' }).click();

    await page.getByLabel('Access token').focus();
    await page.keyboard.insertText(`wrong-${'x'.repeat(40)}`);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByTestId('gate-error')).toContainText('Your access token was not accepted. Sign in again.');
  });
});

test.describe.serial('@validate-only staging shape', () => {
  test('viewer validates without storing; data from the earlier import is still served', async ({ page }) => {
    expect(process.env.RETURNS_IMPORTER_TOKEN ?? '', 'no importer token in this mode').toBe('');
    const viewer = token('RETURNS_VIEWER_TOKEN');
    const auth = watchAuthorization(page, viewer);
    await signIn(page, viewer);
    await expect(page.getByTestId('returns-signed-in')).toBeVisible();
    const capabilities = page.getByText(/^Access: /);
    await expect(capabilities).toHaveText('Access: returns:read, returns:validate · contract return-data-v1');
    const before = await batchCount(page);
    expect(before).toBe(1);

    await section(page, 'Import').click();
    await expect(page.getByTestId('imports-disabled')).toHaveText('Imports are disabled on this server.');
    await expect(uploadButton(page, 'Import')).toHaveCount(0);
    const validated = await upload(page, 'validate', BUNDLE);
    expect([validated.status, validated.body.status]).toEqual([200, 'already_imported']);
    expect(await batchCount(page)).toBe(before);

    const ids = await allReturnIds(page);
    expect(ids.length).toBe(RETURN_COUNT);
    expect(auth.mismatches).toBe(0);
  });

  test('the commit route refuses even a direct request in this mode', async ({ page, request }) => {
    const viewer = token('RETURNS_VIEWER_TOKEN');
    const response = await request.post('/returns/import', {
      headers: { Authorization: `Bearer ${viewer}` },
      multipart: { 'bundle.json': { name: 'bundle.json', mimeType: 'application/json', buffer: readFileSync(BUNDLE) } },
    });
    expect(response.status()).toBe(403);
    expect(response.headers()['cache-control']).toBe('no-store');
    expect(['forbidden', 'import_commit_disabled']).toContain(((await response.json()) as { code: string }).code);
    await page.goto('/');
  });
});

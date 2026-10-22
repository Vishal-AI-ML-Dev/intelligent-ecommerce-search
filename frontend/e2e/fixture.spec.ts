// Fixture-backed browser smoke tests (mocked API). Not a live-stack result.
import { expect, type Page, type Route, test } from '@playwright/test';
import { fixtures } from './fixtures';

const QUERY = 'laptop 8gb 256 ssd';

interface Captured {
  bodies: unknown[];
  console: string[];
  pageErrors: string[];
}

async function setup(page: Page, handler: (route: Route) => Promise<void>): Promise<Captured> {
  const captured: Captured = { bodies: [], console: [], pageErrors: [] };
  page.on('console', (message) => captured.console.push(`${message.type()} ${message.text()}`));
  page.on('pageerror', (error) => captured.pageErrors.push(error.message));
  page.on('dialog', (dialog) => {
    captured.pageErrors.push(`unexpected dialog: ${dialog.message()}`);
    void dialog.dismiss();
  });
  // Every search API call is answered here; none reaches the server.
  await page.route('**/search/**', async (route) => {
    const request = route.request();
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/search/reranked') {
      captured.pageErrors.push(`unexpected request ${request.method()} ${request.url()}`);
      await route.abort();
      return;
    }
    captured.bodies.push(request.postDataJSON());
    await handler(route);
  });
  await page.goto('/');
  return captured;
}

const json = (body: unknown, status = 200) => async (route: Route) => {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
};

async function search(page: Page, text = QUERY) {
  await page.getByLabel('Search the catalog').fill(text);
  await page.getByRole('button', { name: 'Search' }).click();
}

function expectClean(captured: Captured) {
  expect(captured.pageErrors).toEqual([]);
  // Console output: only the client's one-line request logs, never the query or a body.
  for (const line of captured.console) {
    expect(line).toMatch(/^(info|warning) \[api\]/);
    expect(line).not.toContain(QUERY);
    expect(line).not.toContain('search unavailable');
  }
}

const cardIds = (page: Page) =>
  page.getByTestId('product-card').evaluateAll((cards) => cards.map((c) => c.getAttribute('data-product-id')));

test('ranked cards with applied reranking and an unscored tail', async ({ page }) => {
  const captured = await setup(page, json(fixtures.applied));
  await search(page);
  await expect(page.getByTestId('search-status')).toHaveText('5 results');
  expect(captured.bodies).toEqual([{ query: QUERY }]);
  expect(await cardIds(page)).toEqual(fixtures.applied.results.map((r) => r.product_id));
  await expect(page.getByTestId('reranker-status')).toHaveAttribute('data-status', 'applied');
  await expect(page.getByTestId('product-rerank')).toHaveText([
    'Reranked position 1',
    'Reranked position 2',
    'Reranked position 3',
    'Not reranked (beyond rerank depth)',
    'Not reranked (beyond rerank depth)',
  ]);
  await expect(page.getByTestId('product-price')).toHaveText(
    fixtures.applied.results.map((r) => `${r.currency} ${r.price}`),
  );
  // Hostile catalog text is shown as text, and creates no element.
  const hostile = fixtures.applied.results[1]?.title ?? '';
  await expect(page.getByRole('heading', { name: hostile })).toBeVisible();
  await expect(page.locator('.product-list script, .product-list img')).toHaveCount(0);
  expectClean(captured);
});

test('disabled reranking', async ({ page }) => {
  const captured = await setup(page, json(fixtures.disabled));
  await search(page);
  await expect(page.getByTestId('search-status')).toHaveText('3 results');
  await expect(page.getByTestId('reranker-status')).toHaveAttribute('data-status', 'disabled');
  await expect(page.getByTestId('reranker-status')).toContainText('Reranking is turned off');
  await expect(page.getByTestId('product-rerank')).toHaveCount(0);
  expectClean(captured);
});

for (const [name, body, status] of [
  ['no candidates', fixtures.empty, 'skipped_no_candidates'],
  ['no searchable text', fixtures.noSearchableText, 'skipped_no_searchable_text'],
] as const) {
  test(`empty state: ${name}`, async ({ page }) => {
    const captured = await setup(page, json(body));
    await search(page);
    await expect(page.getByTestId('search-empty')).toBeVisible();
    await expect(page.getByTestId('search-status')).toHaveText('0 results');
    await expect(page.getByTestId('reranker-status')).toHaveAttribute('data-status', status);
    await expect(page.getByTestId('product-card')).toHaveCount(0);
    expectClean(captured);
  });
}

test('loading state while the request is pending', async ({ page }) => {
  let release: () => void = () => undefined;
  const pending = new Promise<void>((resolve) => (release = resolve));
  const captured = await setup(page, async (route) => {
    await pending;
    await json(fixtures.disabled)(route);
  });
  await search(page);
  await expect(page.getByTestId('search-status')).toHaveText('Searching…');
  await expect(page.locator('.search-results')).toHaveAttribute('aria-busy', 'true');
  await expect(page.getByRole('button', { name: 'Search' })).toBeDisabled();
  release();
  await expect(page.getByTestId('search-status')).toHaveText('3 results');
  await expect(page.locator('.search-results')).toHaveAttribute('aria-busy', 'false');
  expectClean(captured);
});

test('HTTP 503 shows a fixed message, never the body', async ({ page }) => {
  const captured = await setup(page, json(fixtures.unavailable, 503));
  await search(page);
  await expect(page.getByRole('alert')).toHaveText(
    'Search is temporarily unavailable. Please try again shortly.',
  );
  await expect(page.getByTestId('product-card')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('search unavailable');
  // The browser itself reports the failed resource; the app adds one request line.
  expect(captured.console).toEqual([
    'error Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
    expect.stringMatching(/^warning \[api\] .*status\W+503.*http_error/),
  ]);
  expectClean({ ...captured, console: captured.console.slice(1) });
});

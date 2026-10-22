// Live-stack browser smoke test: no mocks. The assertions use the response to the browser's own
// search request. Needs the local stack: migrated database with the committed synthetic catalog
// ingested and embedded for the active model, and the pinned embedding and reranker snapshots
// under the models directory. Missing prerequisites make this test fail, never pass.
import { expect, test } from '@playwright/test';
import type { RerankedSearchResponse } from '../src/domains/search/types';

// A seeded catalog query with hard filters the deterministic parser recognises (see the capture
// in docs/demo/05-search-reranked.json).
const QUERY = 'laptop 8gb 256 ssd';

test('seeded query through the served page and the real API', async ({ page }) => {
  await page.goto('/');
  await page.getByLabel('Search the catalog').fill(QUERY);
  const responsePromise = page.waitForResponse(
    (r) => new URL(r.url()).pathname === '/search/reranked' && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Search' }).click();
  const response = await responsePromise;

  expect(response.request().postDataJSON()).toEqual({ query: QUERY });
  expect(response.status(), 'search API must return 200 (503 = an unmet prerequisite)').toBe(200);
  const body = (await response.json()) as RerankedSearchResponse;
  expect(body.result_count, 'seeded catalog must match the query').toBeGreaterThan(0);
  expect(body.reranker.status, 'the local reranker must be enabled and available').toBe('applied');

  await expect(page.getByTestId('search-status')).toHaveText(
    body.result_count === 1 ? '1 result' : `${body.result_count} results`,
  );
  await expect(page.getByTestId('reranker-status')).toHaveAttribute('data-status', 'applied');
  const ids = await page
    .getByTestId('product-card')
    .evaluateAll((cards) => cards.map((c) => c.getAttribute('data-product-id')));
  expect(ids).toEqual(body.results.map((r) => r.product_id));
  await expect(page.getByTestId('product-price')).toHaveText(
    body.results.map((r) => `${r.currency} ${r.price}`),
  );
});

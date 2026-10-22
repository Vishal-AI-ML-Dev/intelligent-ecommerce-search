// Fixture-backed browser smoke tests of the shopping assistant (Milestone 16). Mocked API: no
// request reaches the server, a model or a generation provider. Not a live-stack result.
import { expect, type Page, type Route, test } from '@playwright/test';
import { assistantFixtures } from './fixtures';

const MESSAGE = 'dell laptop under rs 50000';

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
  // Every assistant or search API call is answered here; none reaches the server.
  await page.route(/\/(assistant|search)\//, async (route) => {
    const request = route.request();
    if (request.method() !== 'POST' || new URL(request.url()).pathname !== '/assistant/answer') {
      captured.pageErrors.push(`unexpected request ${request.method()} ${request.url()}`);
      await route.abort();
      return;
    }
    captured.bodies.push(request.postDataJSON());
    await handler(route);
  });
  await page.goto('/');
  await page.getByRole('tab', { name: 'Shopping assistant' }).click();
  return captured;
}

const json = (body: unknown, status = 200) => async (route: Route) => {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
};

async function ask(page: Page, text = MESSAGE) {
  await page.getByLabel('Describe what you need').fill(text);
  await page.getByRole('button', { name: 'Ask' }).click();
}

function expectClean(captured: Captured) {
  expect(captured.pageErrors).toEqual([]);
  // Console output: only the client's one-line request logs, never the message or a body.
  for (const line of captured.console) {
    expect(line).toMatch(/^(info|warning) \[api\]/);
    expect(line).not.toContain(MESSAGE);
    expect(line).not.toContain('assistant unavailable');
  }
}

test('grounded answer with evidence references and inert catalog text', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.answer));
  await ask(page);
  const result = page.getByTestId('assistant-result');
  await expect(result).toHaveAttribute('data-outcome', 'answer');
  expect(captured.bodies).toEqual([{ message: MESSAGE }]);
  await expect(page.getByTestId('assistant-summary')).toContainText(
    'Generated summary (unverified)',
  );
  await expect(page.getByTestId('recommendation')).toHaveCount(2);
  await page.getByTestId('evidence-ref').first().click();
  await expect(page).toHaveURL(/#evidence-1$/);
  await expect(page.locator('#evidence-1')).toBeVisible();
  await expect(page.getByTestId('evidence-price')).toHaveText(
    assistantFixtures.answer.evidence.map((p) => `${p.currency} ${p.price}`),
  );
  const hostile = assistantFixtures.answer.evidence[1]?.title ?? '';
  await expect(page.getByRole('heading', { name: hostile })).toBeVisible();
  await expect(page.locator('#panel-assistant img, #panel-assistant script')).toHaveCount(0);
  expectClean(captured);
});

test('comparison of selected retrieved products, with no new request', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.answer));
  await ask(page);
  await expect(page.getByTestId('verification')).toHaveText([
    'Every stated requirement matches the catalog data.',
    'Not a verified match: some stated requirements are not given in the catalog data.',
  ]);
  const toggles = page.getByTestId('compare-toggle');
  await toggles.nth(0).check();
  await toggles.nth(1).check();
  const comparison = page.getByTestId('comparison');
  await expect(comparison).toBeVisible();
  await expect(comparison.locator('tr[data-label="Price"] td')).toHaveText([
    'INR 45999.00',
    'INR 1234.50 (lowest)',
  ]);
  await expect(comparison.locator('tr[data-label="RAM"] td')).toHaveText([
    '16 GB (highest)',
    '8 GB',
  ]);
  await expect(comparison.locator('img, script')).toHaveCount(0);
  expect(captured.bodies).toHaveLength(1); // comparing sent nothing to the server
  expectClean(captured);
});

test('clarification question', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.clarify));
  await ask(page, 'laptop 8gb ram 16gb ram');
  await expect(page.getByTestId('assistant-clarification')).toHaveText(
    'Your request gives conflicting RAM requirements (16, 8). Which one should I use?',
  );
  await expect(page.getByTestId('evidence-item')).toHaveCount(0);
  expectClean(captured);
});

test('honest abstention when nothing matches', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.noMatches));
  await ask(page);
  await expect(page.getByTestId('assistant-result')).toHaveAttribute('data-outcome', 'abstain');
  await expect(page.getByTestId('assistant-notice')).toHaveText(
    'No catalog products matched this request, so no answer was generated.',
  );
  await expect(page.getByTestId('recommendation')).toHaveCount(0);
  expectClean(captured);
});

test('generation off is labelled as plain search results', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.generationOff));
  await ask(page);
  await expect(page.getByRole('heading', { name: 'No generated answer available' })).toBeVisible();
  await expect(
    page.getByRole('heading', { name: 'Search results (not a recommendation)' }),
  ).toBeVisible();
  await expect(page.getByTestId('assistant-summary')).toHaveCount(0);
  await expect(page.getByTestId('evidence-item')).toHaveCount(3);
  expectClean(captured);
});

test('loading state while the request is pending', async ({ page }) => {
  let release: () => void = () => undefined;
  const pending = new Promise<void>((resolve) => (release = resolve));
  const captured = await setup(page, async (route) => {
    await pending;
    await json(assistantFixtures.answer)(route);
  });
  await ask(page);
  await expect(page.getByTestId('assistant-status')).toHaveText('Working on your request…');
  await expect(page.getByRole('button', { name: 'Ask' })).toBeDisabled();
  release();
  await expect(page.getByTestId('assistant-result')).toBeVisible();
  expectClean(captured);
});

test('HTTP 503 shows a fixed message, never the body', async ({ page }) => {
  const captured = await setup(page, json(assistantFixtures.unavailable, 503));
  await ask(page);
  await expect(page.getByRole('alert')).toHaveText(
    'The assistant is temporarily unavailable. Please try again shortly.',
  );
  await expect(page.locator('body')).not.toContainText('assistant unavailable');
  expect(captured.console).toEqual([
    'error Failed to load resource: the server responded with a status of 503 (Service Unavailable)',
    expect.stringMatching(/^warning \[api\] .*status\W+503.*http_error/),
  ]);
  expectClean({ ...captured, console: captured.console.slice(1) });
});

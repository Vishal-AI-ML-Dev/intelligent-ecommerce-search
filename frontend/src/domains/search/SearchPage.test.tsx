import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fixtures } from '../../../e2e/fixtures';
import { isRerankedSearchResponse } from './api';
import { SearchPage } from './SearchPage';

function respondWith(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function search(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Search the catalog'), text);
  await user.click(screen.getByRole('button', { name: 'Search' }));
}

function cardIds(): string[] {
  return screen.queryAllByTestId('product-card').map((card) => card.dataset.productId ?? '');
}

beforeEach(() => {
  vi.spyOn(console, 'info').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});

describe('fixtures', () => {
  it.each(Object.entries(fixtures).filter(([name]) => name !== 'unavailable'))(
    '%s passes the response shape check',
    (_name, body) => {
      expect(isRerankedSearchResponse(body)).toBe(true);
    },
  );
});

describe('SearchPage', () => {
  it('posts only the trimmed query and renders ranked cards in response order', async () => {
    const fetchMock = respondWith(200, fixtures.applied);
    render(<SearchPage />);
    await search('  laptop 8gb 256 ssd  ');
    await screen.findByText('5 results');
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/search/reranked');
    expect(JSON.parse(init.body as string)).toEqual({ query: 'laptop 8gb 256 ssd' });
    expect(cardIds()).toEqual(fixtures.applied.results.map((r) => r.product_id));
  });

  it('shows the price decimal string unchanged', async () => {
    respondWith(200, fixtures.applied);
    render(<SearchPage />);
    await search('laptop');
    const prices = (await screen.findAllByTestId('product-price')).map((p) => p.textContent);
    expect(prices).toEqual(fixtures.applied.results.map((r) => `${r.currency} ${r.price}`));
    expect(prices).toContain('INR 1234.50');
    expect(prices).toContain('INR 0.99');
  });

  it('renders catalog HTML as inert text', async () => {
    respondWith(200, fixtures.applied);
    const { container } = render(<SearchPage />);
    await search('laptop');
    const hostile = fixtures.applied.results[1];
    expect(hostile?.title).toContain('<script>');
    expect(await screen.findByText(hostile?.title ?? '')).toBeInTheDocument();
    expect(container.querySelector('script, img')).toBeNull();
    expect(screen.getByText('Fixture & Co')).toBeInTheDocument();
  });

  it('marks scored and unscored-tail cards when reranking was applied', async () => {
    respondWith(200, fixtures.applied);
    render(<SearchPage />);
    await search('laptop');
    expect(await screen.findByTestId('reranker-status')).toHaveAttribute('data-status', 'applied');
    const notes = screen.getAllByTestId('product-rerank').map((n) => n.textContent);
    expect(notes).toEqual([
      'Reranked position 1',
      'Reranked position 2',
      'Reranked position 3',
      'Not reranked (beyond rerank depth)',
      'Not reranked (beyond rerank depth)',
    ]);
  });

  it('reports disabled reranking without per-card rerank notes', async () => {
    respondWith(200, fixtures.disabled);
    render(<SearchPage />);
    await search('laptop');
    const status = await screen.findByTestId('reranker-status');
    expect(status).toHaveAttribute('data-status', 'disabled');
    expect(status).toHaveTextContent('Reranking is turned off');
    expect(cardIds()).toHaveLength(3);
    expect(screen.queryAllByTestId('product-rerank')).toHaveLength(0);
  });

  it.each([
    ['empty', fixtures.empty, 'skipped_no_candidates'],
    ['noSearchableText', fixtures.noSearchableText, 'skipped_no_searchable_text'],
  ] as const)('shows the empty state for %s', async (_name, body, status) => {
    respondWith(200, body);
    render(<SearchPage />);
    await search('anything');
    expect(await screen.findByTestId('search-empty')).toBeInTheDocument();
    expect(screen.getByTestId('reranker-status')).toHaveAttribute('data-status', status);
    expect(screen.getByText('0 results')).toBeInTheDocument();
    expect(cardIds()).toEqual([]);
    expect(screen.queryByTestId('similarity-notice')).toBeNull();
  });

  it('shows the similarity notice when no result is a keyword hit and no filter applied', async () => {
    respondWith(200, fixtures.similarityOnly);
    render(<SearchPage />);
    await search('hello');
    expect(await screen.findByTestId('similarity-notice')).toHaveTextContent(
      'Showing similarity-based suggestions. Try a product name or requirement for more specific results.',
    );
    expect(cardIds()).toEqual(fixtures.similarityOnly.results.map((r) => r.product_id));
  });

  it('hides the similarity notice when any result is a keyword hit', async () => {
    const [first, ...rest] = fixtures.similarityOnly.results;
    respondWith(200, {
      ...fixtures.similarityOnly,
      results: [{ ...first, lexical_rank: 1 }, ...rest],
    });
    render(<SearchPage />);
    await search('hello');
    await screen.findByText('5 results');
    expect(screen.queryByTestId('similarity-notice')).toBeNull();
  });

  it('hides the similarity notice when filters were applied', async () => {
    expect(fixtures.applied.applied_filters.length).toBeGreaterThan(0);
    expect(fixtures.applied.results.every((r) => r.lexical_rank === null)).toBe(true);
    respondWith(200, fixtures.applied);
    render(<SearchPage />);
    await search('laptop 8gb 256 ssd');
    await screen.findByText('5 results');
    expect(screen.queryByTestId('similarity-notice')).toBeNull();
  });

  it('shows a loading state until the response arrives', async () => {
    let release: (response: Response) => void = () => undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => (release = resolve))),
    );
    render(<SearchPage />);
    await search('laptop');
    expect(screen.getByTestId('search-status')).toHaveTextContent('Searching…');
    expect(screen.getByRole('button', { name: 'Search' })).toBeDisabled();
    expect(screen.getByTestId('search-status').parentElement).toHaveAttribute('aria-busy', 'true');
    release(new Response(JSON.stringify(fixtures.disabled), { status: 200 }));
    await screen.findByText('3 results');
    expect(screen.getByRole('button', { name: 'Search' })).toBeEnabled();
  });

  it.each([
    [503, fixtures.unavailable, 'Search is temporarily unavailable'],
    [422, { detail: [{ msg: 'private validation detail' }] }, 'could not be processed'],
    [500, { detail: 'private' }, 'Something went wrong'],
  ])('shows a fixed message for HTTP %i, never the body', async (status, body, text) => {
    respondWith(status, body);
    render(<SearchPage />);
    await search('laptop');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(text);
    expect(document.body.textContent).not.toContain('private');
    expect(document.body.textContent).not.toContain('search unavailable');
    expect(cardIds()).toEqual([]);
    expect(screen.queryByTestId('similarity-notice')).toBeNull();
  });

  it('shows a network error message', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('offline'))));
    render(<SearchPage />);
    await search('laptop');
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not reach the search service');
  });

  it('does not send a blank query', async () => {
    const fetchMock = respondWith(200, fixtures.applied);
    render(<SearchPage />);
    await search('   ');
    expect(fetchMock).not.toHaveBeenCalled();
    const input = screen.getByLabelText('Search the catalog');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(within(input.closest('form') as HTMLElement).getByText('Enter something to search for.')).toBeInTheDocument();
  });
});

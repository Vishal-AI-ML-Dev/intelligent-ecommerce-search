import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { assistantFixtures } from '../../../e2e/fixtures';
import { App } from '../../app/App';
import { isAssistantResponse } from './api';
import { AssistantPage } from './AssistantPage';

function respondWith(status: number, body: unknown) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function ask(text: string) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText('Describe what you need'), text);
  await user.click(screen.getByRole('button', { name: 'Ask' }));
}

const result = () => screen.findByTestId('assistant-result');

beforeEach(() => {
  vi.spyOn(console, 'info').mockImplementation(() => undefined);
  vi.spyOn(console, 'warn').mockImplementation(() => undefined);
});

describe('assistant fixtures', () => {
  it.each(Object.entries(assistantFixtures).filter(([name]) => name !== 'unavailable'))(
    '%s passes the response shape check',
    (_name, body) => {
      expect(isAssistantResponse(body)).toBe(true);
    },
  );

  it('rejects an unexpected outcome', () => {
    expect(isAssistantResponse({ ...assistantFixtures.answer, outcome: 'purchase' })).toBe(false);
  });
});

describe('AssistantPage', () => {
  it('posts only the trimmed message', async () => {
    const fetchMock = respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    await ask('  dell laptop under rs 50000  ');
    await result();
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/assistant/answer');
    expect(JSON.parse(init.body as string)).toEqual({ message: 'dell laptop under rs 50000' });
  });

  it('validates empty and over-long input without calling the API', async () => {
    const fetchMock = respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    await userEvent.setup().click(screen.getByRole('button', { name: 'Ask' }));
    expect(screen.getByText('Describe what you are looking for.')).toBeInTheDocument();
    await ask('x'.repeat(201));
    expect(screen.getByText('Use at most 200 characters.')).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('shows a grounded answer: unverified summary, catalog facts and evidence references', async () => {
    respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    expect(await result()).toHaveAttribute('data-outcome', 'answer');
    expect(screen.getByTestId('assistant-summary')).toHaveTextContent(
      'The assistant selected 2 of the 3 retrieved catalog products.',
    );
    expect(document.body).not.toHaveTextContent('Generated summary');
    expect(screen.getAllByTestId('explanation')[1]).toHaveTextContent(
      'Not verified, not given in the catalog: RAM 16 GB.',
    );
    const cards = screen.getAllByTestId('recommendation');
    expect(cards.map((c) => c.dataset.productId)).toEqual(['FIXTURE-LAP-101', 'FIXTURE-LAP-103']);
    const first = within(cards[0] as HTMLElement);
    expect(first.getByText('INR 45999.00')).toBeInTheDocument();
    expect(first.getByTestId('evidence-ref')).toHaveAttribute('href', '#evidence-1');
    expect(first.getByTestId('evidence-ref')).toHaveTextContent('Evidence E1 · FIXTURE-LAP-101');
    expect(within(cards[1] as HTMLElement).getByText('out of stock')).toBeInTheDocument();
    const checks = first.getAllByTestId('constraint-check').map((c) => c.dataset.status);
    expect(checks.every((s) => s === 'passed')).toBe(true);
    expect(screen.getAllByTestId('evidence-item')).toHaveLength(3);
    expect(document.getElementById('evidence-1')).not.toBeNull();
  });

  it('never presents a product with unverified requirements as a verified match', async () => {
    respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    await ask('dell laptop with 16gb ram under rs 50000');
    await result();
    const labels = screen.getAllByTestId('verification');
    expect(labels.map((l) => l.dataset.verification)).toEqual(['all_verified', 'not_all_verified']);
    expect(labels[1]).toHaveTextContent('Not a verified match');
    const second = within(screen.getAllByTestId('recommendation')[1] as HTMLElement);
    const ram = second
      .getAllByTestId('constraint-check')
      .find((c) => c.textContent?.startsWith('RAM'));
    expect(ram).toHaveAttribute('data-status', 'not_verified');
    expect(ram).toHaveTextContent('not verified (not given in the catalog)');
    // Fields the catalog does not give cannot be cited (cited_field_not_given).
    expect(second.queryByText('not given')).toBeNull();
    expect(second.getByTestId('explanation')).toHaveTextContent('Not verified');
  });

  it('compares 2 or 3 selected retrieved products from catalog values only', async () => {
    const fetchMock = respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    const user = userEvent.setup();
    await ask('dell laptop with 16gb ram under rs 50000');
    await result();
    const toggles = screen.getAllByTestId('compare-toggle');
    await user.click(toggles[0] as HTMLElement);
    expect(screen.queryByTestId('comparison')).toBeNull(); // one product is not a comparison
    await user.click(toggles[2] as HTMLElement);
    const table = within(screen.getByTestId('comparison'));
    const ram = table.getAllByTestId('comparison-row').find((r) => r.dataset.label === 'RAM');
    expect(ram).toHaveTextContent('RAM16 GBnot given'); // one known value: no "highest" note
    await user.click(toggles[1] as HTMLElement);
    expect(table.getAllByRole('columnheader')).toHaveLength(4);
    expect(fetchMock).toHaveBeenCalledTimes(1); // no new request: nothing is retrieved again
  });

  it('caps the comparison at 3 and resets the selection for a new answer', async () => {
    const many = {
      ...assistantFixtures.answer,
      evidence: [
        ...assistantFixtures.answer.evidence,
        { ...assistantFixtures.answer.evidence[0], product_id: 'FIXTURE-LAP-104', search_rank: 4 },
      ],
    };
    respondWith(200, many);
    render(<AssistantPage />);
    const user = userEvent.setup();
    await ask('dell laptop with 16gb ram under rs 50000');
    await result();
    const toggles = screen.getAllByTestId('compare-toggle');
    for (const toggle of toggles.slice(0, 3)) {
      await user.click(toggle);
    }
    expect(toggles[3]).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Ask' }));
    await screen.findByTestId('assistant-result');
    expect(screen.queryByTestId('comparison')).toBeNull();
    expect(screen.getAllByTestId('compare-toggle').every((t) => !(t as HTMLInputElement).checked)).toBe(true);
  });

  it('renders hostile catalog text as inert text', async () => {
    respondWith(200, assistantFixtures.answer);
    const { container } = render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    await result();
    const hostile = assistantFixtures.answer.evidence[1]?.title ?? '';
    expect(hostile).toContain('<img');
    expect(screen.getByText(hostile)).toBeInTheDocument();
    expect(container.querySelector('img, script')).toBeNull();
  });

  it('shows a clarification question without products', async () => {
    respondWith(200, assistantFixtures.clarify);
    render(<AssistantPage />);
    await ask('laptop 8gb ram 16gb ram');
    expect(await result()).toHaveAttribute('data-outcome', 'clarify');
    expect(screen.getByTestId('assistant-clarification')).toHaveTextContent(
      'Your request gives conflicting RAM requirements (16, 8). Which one should I use?',
    );
    expect(screen.queryAllByTestId('evidence-item')).toHaveLength(0);
  });

  it.each([
    ['noMatches', 'No catalog products matched this request', 0],
    ['validationFailed', 'failed the grounding checks', 3],
  ] as const)('abstains honestly: %s', async (name, text, evidence) => {
    respondWith(200, assistantFixtures[name]);
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    expect(await result()).toHaveAttribute('data-outcome', 'abstain');
    expect(screen.getByRole('heading', { name: 'No answer' })).toBeInTheDocument();
    expect(screen.getByTestId('assistant-notice')).toHaveTextContent(text);
    expect(screen.queryByTestId('assistant-summary')).toBeNull();
    expect(screen.queryAllByTestId('recommendation')).toHaveLength(0);
    expect(screen.queryAllByTestId('evidence-item')).toHaveLength(evidence);
  });

  it.each(['generationOff', 'generationFailed'] as const)(
    'never presents search results as a generated answer: %s',
    async (name) => {
      respondWith(200, assistantFixtures[name]);
      render(<AssistantPage />);
      await ask('dell laptop under rs 50000');
      expect(await result()).toHaveAttribute('data-outcome', 'generation_unavailable');
      expect(
        screen.getByRole('heading', { name: 'No generated answer available' }),
      ).toBeInTheDocument();
      expect(screen.getByTestId('assistant-notice')).toHaveTextContent(
        /^No generated answer is available/,
      );
      expect(
        screen.getByRole('heading', { name: 'Search results (not a recommendation)' }),
      ).toBeInTheDocument();
      expect(screen.queryByTestId('assistant-summary')).toBeNull();
      expect(screen.queryAllByTestId('recommendation')).toHaveLength(0);
      expect(screen.getAllByTestId('evidence-item')).toHaveLength(3);
    },
  );

  it('shows a loading state and disables the button while waiting', async () => {
    let release: (r: Response) => void = () => undefined;
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>((resolve) => (release = resolve))),
    );
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    expect(screen.getByTestId('assistant-status')).toHaveTextContent('Working on your request…');
    expect(screen.getByRole('button', { name: 'Ask' })).toBeDisabled();
    release(new Response(JSON.stringify(assistantFixtures.answer), { status: 200 }));
    await result();
    expect(screen.getByTestId('assistant-status')).toHaveTextContent('');
  });

  it.each([
    [503, assistantFixtures.unavailable, 'The assistant is temporarily unavailable.'],
    [422, { detail: [{ msg: 'secret detail' }] }, 'That request could not be processed.'],
    [500, { detail: 'boom' }, 'Something went wrong. Please try again.'],
  ])('HTTP %i shows a fixed message, never the body', async (status, body, text) => {
    respondWith(status, body);
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    expect(await screen.findByRole('alert')).toHaveTextContent(text);
    expect(document.body).not.toHaveTextContent('assistant unavailable');
    expect(document.body).not.toHaveTextContent('secret detail');
  });

  it('treats an unexpected body as an error', async () => {
    respondWith(200, { outcome: 'answer' });
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    expect(await screen.findByRole('alert')).toHaveTextContent('Something went wrong.');
  });

  it('never logs the message or response text', async () => {
    const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
    respondWith(200, assistantFixtures.answer);
    render(<AssistantPage />);
    await ask('dell laptop under rs 50000');
    await result();
    const logged = info.mock.calls.flat().join('\n');
    expect(logged).toContain('/assistant/answer');
    expect(logged).not.toContain('dell laptop');
    expect(logged).not.toContain('Fixture Dell');
  });
});

describe('App tabs', () => {
  it('starts on Search and switches to the assistant with the keyboard', async () => {
    render(<App />);
    const user = userEvent.setup();
    expect(screen.getByRole('tab', { name: 'Search' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByLabelText('Search the catalog')).toBeVisible();
    screen.getByRole('tab', { name: 'Search' }).focus();
    await user.keyboard('{ArrowRight}');
    const assistantTab = screen.getByRole('tab', { name: 'Shopping assistant' });
    expect(assistantTab).toHaveAttribute('aria-selected', 'true');
    expect(assistantTab).toHaveFocus();
    expect(screen.getByLabelText('Describe what you need')).toBeVisible();
    expect(screen.getByLabelText('Search the catalog')).not.toBeVisible();
  });
});

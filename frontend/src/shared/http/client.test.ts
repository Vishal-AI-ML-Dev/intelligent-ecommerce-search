import { describe, expect, it, vi } from 'vitest';
import { newRequestId, postJson, REQUEST_ID_HEADER, RequestAborted } from './client';

const SECRET_QUERY = 'private query text';
const isAny = (_value: unknown): _value is unknown => true;
const isObject = (value: unknown): value is { a: number } =>
  typeof value === 'object' && value !== null && 'a' in value;

function captureLogs() {
  const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  return () =>
    [...info.mock.calls, ...warn.mock.calls, ...error.mock.calls]
      .map((args) => args.map(String).join(' '))
      .join('\n');
}

function respond(status: number, body: string) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status })));
}

describe('postJson', () => {
  it('sends same-origin JSON and returns a valid body', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    const result = await postJson('/x', { query: SECRET_QUERY }, isObject);
    expect(result).toEqual({ ok: true, data: { a: 1 } });
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/x');
    expect(init.method).toBe('POST');
    expect(init.credentials).toBe('same-origin');
    expect(JSON.parse(init.body as string)).toEqual({ query: SECRET_QUERY });
    expect(logs()).toContain('"outcome":"ok"');
    expect(logs()).not.toContain(SECRET_QUERY);
  });

  it.each([422, 500, 503])('maps HTTP %i to an http failure without logging the body', async (status) => {
    respond(status, '{"detail":"server detail with private query text"}');
    const logs = captureLogs();
    const result = await postJson('/x', { query: SECRET_QUERY }, isAny);
    expect(result).toEqual({ ok: false, error: { kind: 'http', status } });
    expect(logs()).toContain(`"status":${status}`);
    expect(logs()).not.toContain('private');
    expect(logs()).not.toContain('detail');
  });

  it('maps a fetch failure to a network failure without logging the exception', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('boom private'))));
    const logs = captureLogs();
    const result = await postJson('/x', { query: SECRET_QUERY }, isAny);
    expect(result).toEqual({ ok: false, error: { kind: 'network' } });
    expect(logs()).toContain('network_error');
    expect(logs()).not.toContain('private');
  });

  it('rejects a body that fails the shape check', async () => {
    respond(200, '{"b": "private"}');
    const logs = captureLogs();
    expect(await postJson('/x', {}, isObject)).toEqual({
      ok: false,
      error: { kind: 'invalid_body', status: 200 },
    });
    respond(200, 'not json');
    expect(await postJson('/x', {}, isObject)).toEqual({
      ok: false,
      error: { kind: 'invalid_body', status: 200 },
    });
    expect(logs()).not.toContain('private');
  });

  it('throws RequestAborted, not a failure, when the caller aborts', async () => {
    const controller = new AbortController();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        controller.abort();
        throw new DOMException('aborted', 'AbortError');
      }),
    );
    const logs = captureLogs();
    await expect(postJson('/x', {}, isAny, controller.signal)).rejects.toBeInstanceOf(RequestAborted);
    expect(logs()).toBe('');
  });

  it('sends a generated request ID and logs the ID the server returned', async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response('{"a":1}', { status: 200, headers: { [REQUEST_ID_HEADER]: 'server-id-0001' } }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    await postJson('/x', { query: SECRET_QUERY }, isObject);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const sent = (init.headers as Record<string, string>)[REQUEST_ID_HEADER];
    expect(sent).toMatch(/^[A-Za-z0-9-]{8,64}$/);
    expect(logs()).toContain('"request_id":"server-id-0001"');
    expect(logs()).not.toContain(SECRET_QUERY);
  });

  it.each([
    ['no header', {}],
    ['an invalid header', { [REQUEST_ID_HEADER]: 'bad id <script>' }],
  ])('logs the generated ID when the response has %s', async (_label, headers) => {
    const fetchMock = vi.fn(async () => new Response('{"detail":"x"}', { status: 503, headers }));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    await postJson('/x', {}, isAny);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const sent = (init.headers as Record<string, string>)[REQUEST_ID_HEADER];
    expect(logs()).toContain(`"request_id":"${sent}"`);
    expect(logs()).not.toContain('<script>');
  });

  it('logs the generated ID when no response arrives', async () => {
    const fetchMock = vi.fn(async () => Promise.reject(new TypeError('offline')));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    await postJson('/x', {}, isAny);
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const sent = (init.headers as Record<string, string>)[REQUEST_ID_HEADER];
    expect(logs()).toContain(`"request_id":"${sent}"`);
    expect(logs()).toContain('network_error');
  });

  it('uses a fresh ID per request', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    captureLogs();
    await postJson('/x', {}, isObject);
    await postJson('/x', {}, isObject);
    const ids = fetchMock.mock.calls.map(
      (call) => ((call as unknown as [string, RequestInit])[1].headers as Record<string, string>)[REQUEST_ID_HEADER],
    );
    expect(new Set(ids).size).toBe(2);
  });
});

describe('newRequestId', () => {
  it('falls back to getRandomValues outside a secure context', () => {
    vi.stubGlobal('crypto', {
      getRandomValues: (bytes: Uint8Array) => bytes.fill(0xab),
    });
    expect(newRequestId()).toBe('ab'.repeat(16));
  });
});

import { describe, expect, it, vi } from 'vitest';
import { getJson, newRequestId, postForm, postJson, REQUEST_ID_HEADER, RequestAborted } from './client';

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

// Milestone 18-05: opt-in options for the retailer `/returns` routes.
describe('request options', () => {
  const TOKEN = 'synthetic-test-token-abcdefghijklmnopqrstuvwxyz';

  function lastInit(fetchMock: ReturnType<typeof vi.fn>): RequestInit {
    return (fetchMock.mock.calls.at(-1) as unknown as [string, RequestInit])[1];
  }

  it('sends no Authorization header without the option', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    captureLogs();
    await getJson('/returns', isObject);
    await postJson('/search/reranked', {}, isObject);
    for (const call of fetchMock.mock.calls) {
      const headers = (call as unknown as [string, RequestInit])[1].headers as Record<string, string>;
      expect(Object.keys(headers)).not.toContain('Authorization');
    }
  });

  it('adds the bearer token to /returns paths and never logs it', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    for (const path of ['/returns', '/returns?status=received', '/returns/capabilities']) {
      await getJson(path, isObject, undefined, { authorization: TOKEN });
      expect((lastInit(fetchMock).headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    }
    expect(logs()).not.toContain(TOKEN);
  });

  it.each([
    // R1: the three demonstrated traversal bypasses, all of which start with "/returns/".
    '/returns/../search/reranked',
    '/returns/%2e%2e/assistant/answer',
    '/returns/..\\search',
    // other routes, look-alike prefixes and other origins
    '/search/reranked',
    '/assistant/answer',
    '/returnsx',
    '/returnsXYZ',
    '/returnsXYZ/capabilities',
    'https://example.com/returns',
    'https://example.com/returns/capabilities',
    '//example.com/returns',
    'http://127.0.0.1:1/returns',
    // ambiguous forms that stay under /returns once resolved are refused too
    '/returns/records/../import',
    '/returns/./capabilities',
    '/returns/%2E%2e/returns/capabilities',
    '/returns/.%2e/x',
    '/returns\\capabilities',
    '',
  ])('refuses to send a token to %j and never calls fetch', async (path) => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    for (const send of [
      () => getJson(path, isObject, undefined, { authorization: TOKEN }),
      () => postJson(path, {}, isObject, undefined, { authorization: TOKEN }),
      () => postForm(path, new FormData(), isObject, undefined, { authorization: TOKEN }),
    ]) {
      await expect(send()).rejects.toThrow('/returns paths');
    }
    expect(fetchMock).not.toHaveBeenCalled();
    expect(logs()).not.toContain(TOKEN);
  });

  it.each([
    ['/returns', '/returns'],
    ['/returns/', '/returns/'],
    ['/returns/capabilities', '/returns/capabilities'],
    ['/returns/import/validate', '/returns/import/validate'],
    ['/returns/import-batches?cursor=abc_DEF-123', '/returns/import-batches?cursor=abc_DEF-123'],
    // encoded valid IDs, including dots inside an ID and an encoded slash
    ['/returns/records/fixture-returns/1/FIX-RET-0001', '/returns/records/fixture-returns/1/FIX-RET-0001'],
    ['/returns/records/ds/1/A%3AB%2FC', '/returns/records/ds/1/A%3AB%2FC'],
    ['/returns/records/ds/1/SYN.RET..00001', '/returns/records/ds/1/SYN.RET..00001'],
    // query strings, including values with dots, slashes and backslashes (only the path is checked)
    ['/returns?status=received&product_id=..%2Fx', '/returns?status=received&product_id=..%2Fx'],
    ['/returns?q=../a', '/returns?q=../a'],
    ['/returns?q=a%5Cb', '/returns?q=a%5Cb'],
    // this origin, written absolutely, and a relative path resolved against the base "/"
    [`${window.location.origin}/returns/capabilities`, '/returns/capabilities'],
    ['returns/capabilities', '/returns/capabilities'],
  ])('sends the token to %j, requesting exactly the checked destination', async (path, requested) => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    captureLogs();
    await getJson(path, isObject, undefined, { authorization: TOKEN });
    const [sentPath, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(sentPath).toBe(requested);
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('leaves unauthenticated requests unchanged, even with traversal in the path', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    captureLogs();
    await getJson('/returns/../search/x', isObject);
    await postJson('/search/reranked', { query: 'q' }, isObject);
    const sent = fetchMock.mock.calls.map((c) => (c as unknown as [string, RequestInit])[0]);
    expect(sent).toEqual(['/returns/../search/x', '/search/reranked']);
    for (const call of fetchMock.mock.calls) {
      const headers = (call as unknown as [string, RequestInit])[1].headers as Record<string, string>;
      expect(Object.keys(headers)).not.toContain('Authorization');
    }
  });

  it('logs the route template instead of the real path', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"a":1}', { status: 200 })));
    const logs = captureLogs();
    await getJson('/returns/records/ds/1/SECRET-ID?x=private', isObject, undefined, {
      logPath: '/returns/records/{ref}',
    });
    expect(logs()).toContain('"path":"/returns/records/{ref}"');
    expect(logs()).not.toContain('SECRET-ID');
    expect(logs()).not.toContain('private');
  });

  it('adds the request ID, the safe code and parameters to detailed failures', async () => {
    const body = JSON.stringify({
      detail: 'Invalid request parameters.',
      code: 'invalid_parameter',
      parameters: [{ name: 'requested_from', code: 'format' }],
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(body, { status: 422, headers: { [REQUEST_ID_HEADER]: 'server-id-0422' } })),
    );
    const logs = captureLogs();
    const result = await getJson('/returns', isAny, undefined, { detailedErrors: true });
    expect(result).toEqual({
      ok: false,
      error: {
        kind: 'http',
        status: 422,
        requestId: 'server-id-0422',
        code: 'invalid_parameter',
        parameters: [{ name: 'requested_from', code: 'format' }],
      },
    });
    expect(logs()).not.toContain('Invalid request');
    expect(logs()).not.toContain('requested_from');
  });

  it.each([
    ['an unsafe code', '{"detail":"x","code":"<b>bad</b>"}', {}],
    ['no detail', '{"code":"forbidden"}', {}],
    ['not JSON', 'private text', {}],
    ['unsafe parameters', '{"detail":"x","code":"invalid_parameter","parameters":[{"name":"a b","code":"x"}]}', { code: 'invalid_parameter' }],
  ])('keeps only safe error fields for %s', async (_label, body, expected) => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(body, { status: 400 })));
    captureLogs();
    const result = await getJson('/returns', isAny, undefined, { detailedErrors: true });
    expect(result.ok).toBe(false);
    if (!result.ok && result.error.kind === 'http') {
      const { kind, status, requestId, ...rest } = result.error;
      expect([kind, status, typeof requestId]).toEqual(['http', 400, 'string']);
      expect(rest).toEqual(expected);
    }
  });

  it('returns a listed non-2xx body as data when it passes the shape check', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"a":2}', { status: 422 })));
    const logs = captureLogs();
    const options = { detailedErrors: true, bodyStatuses: [422] };
    expect(await getJson('/returns', isObject, undefined, options)).toEqual({ ok: true, data: { a: 2 } });
    expect(logs()).toContain('"status":422');
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"x","code":"invalid"}', { status: 422 })));
    const result = await getJson('/returns', isObject, undefined, options);
    expect(result.ok).toBe(false);
  });

  it('adds the generated request ID to a detailed network failure', async () => {
    const fetchMock = vi.fn(async () => Promise.reject(new TypeError('offline')));
    vi.stubGlobal('fetch', fetchMock);
    captureLogs();
    const result = await getJson('/returns', isAny, undefined, { detailedErrors: true });
    const sent = (lastInit(fetchMock).headers as Record<string, string>)[REQUEST_ID_HEADER];
    expect(result).toEqual({ ok: false, error: { kind: 'network', requestId: sent } });
  });
});

describe('postForm', () => {
  it('sends the form as the body without a Content-Type header and never logs it', async () => {
    const fetchMock = vi.fn(async () => new Response('{"a":1}', { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const logs = captureLogs();
    const form = new FormData();
    form.append('bundle.json', new File(['{"private":"upload"}'], 'bundle.json'), 'bundle.json');
    await postForm('/returns/import/validate', form, isObject);
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/returns/import/validate');
    expect(init.method).toBe('POST');
    expect(init.body).toBe(form);
    expect(Object.keys(init.headers as Record<string, string>)).not.toContain('Content-Type');
    expect(logs()).not.toContain('private');
  });
});

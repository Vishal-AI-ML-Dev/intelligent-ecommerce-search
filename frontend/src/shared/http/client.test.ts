import { describe, expect, it, vi } from 'vitest';
import { postJson, RequestAborted } from './client';

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
});

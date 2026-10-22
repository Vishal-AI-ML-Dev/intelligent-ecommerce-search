// Same-origin JSON client shared by the app's domain modules.
//
// Logging is deliberately minimal: one line per request with the fixed API path, the HTTP
// status, an outcome code and the duration. Request bodies (search queries), response bodies,
// raw error bodies and exception messages are never logged.

export type HttpFailure =
  | { kind: 'network' }
  | { kind: 'http'; status: number }
  | { kind: 'invalid_body'; status: number };

export type HttpResult<T> = { ok: true; data: T } | { ok: false; error: HttpFailure };

export class RequestAborted extends Error {
  constructor() {
    super('request aborted');
    this.name = 'RequestAborted';
  }
}

interface RequestLog {
  path: string;
  status: number | null;
  outcome: 'ok' | 'http_error' | 'network_error' | 'invalid_body';
  duration_ms: number;
}

function logRequest(entry: RequestLog): void {
  const line = `[api] ${JSON.stringify(entry)}`;
  if (entry.outcome === 'ok') {
    console.info(line);
  } else {
    console.warn(line);
  }
}

export async function postJson<T>(
  path: string,
  body: unknown,
  isValid: (value: unknown) => value is T,
  signal?: AbortSignal,
): Promise<HttpResult<T>> {
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    });
  } catch {
    if (signal?.aborted) {
      throw new RequestAborted();
    }
    logRequest({ path, status: null, outcome: 'network_error', duration_ms: elapsed() });
    return { ok: false, error: { kind: 'network' } };
  }
  if (!response.ok) {
    logRequest({ path, status: response.status, outcome: 'http_error', duration_ms: elapsed() });
    return { ok: false, error: { kind: 'http', status: response.status } };
  }
  let data: unknown;
  try {
    data = await response.json();
  } catch {
    if (signal?.aborted) {
      throw new RequestAborted();
    }
    data = undefined;
  }
  if (!isValid(data)) {
    logRequest({ path, status: response.status, outcome: 'invalid_body', duration_ms: elapsed() });
    return { ok: false, error: { kind: 'invalid_body', status: response.status } };
  }
  logRequest({ path, status: response.status, outcome: 'ok', duration_ms: elapsed() });
  return { ok: true, data };
}

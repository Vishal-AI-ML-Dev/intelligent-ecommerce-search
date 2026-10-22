// Same-origin JSON client shared by the app's domain modules.
//
// Logging is deliberately minimal: one line per request with the fixed API path, the HTTP
// status, an outcome code, the duration and the request ID. Request bodies (search queries),
// response bodies, raw error bodies and exception messages are never logged.
//
// Request IDs (M24A): every request sends a generated `X-Request-ID`. The line logs the ID the
// server returned (the one in its logs) when a response arrived, otherwise the generated one.

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

export const REQUEST_ID_HEADER = 'X-Request-ID';
// Same rule as the server: anything else in a response header is ignored.
const REQUEST_ID_PATTERN = /^[A-Za-z0-9-]{8,64}$/;

interface RequestLog {
  path: string;
  status: number | null;
  outcome: 'ok' | 'http_error' | 'network_error' | 'invalid_body';
  duration_ms: number;
  request_id: string;
}

export function newRequestId(): string {
  if (typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // randomUUID needs a secure context; getRandomValues does not.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function responseRequestId(response: Response, sent: string): string {
  const returned = response.headers.get(REQUEST_ID_HEADER);
  return returned !== null && REQUEST_ID_PATTERN.test(returned) ? returned : sent;
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
  const sentId = newRequestId();
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        [REQUEST_ID_HEADER]: sentId,
      },
      body: JSON.stringify(body),
      credentials: 'same-origin',
      signal,
    });
  } catch {
    if (signal?.aborted) {
      throw new RequestAborted();
    }
    logRequest({
      path,
      status: null,
      outcome: 'network_error',
      duration_ms: elapsed(),
      request_id: sentId,
    });
    return { ok: false, error: { kind: 'network' } };
  }
  const requestId = responseRequestId(response, sentId);
  if (!response.ok) {
    logRequest({
      path,
      status: response.status,
      outcome: 'http_error',
      duration_ms: elapsed(),
      request_id: requestId,
    });
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
    logRequest({
      path,
      status: response.status,
      outcome: 'invalid_body',
      duration_ms: elapsed(),
      request_id: requestId,
    });
    return { ok: false, error: { kind: 'invalid_body', status: response.status } };
  }
  logRequest({
    path,
    status: response.status,
    outcome: 'ok',
    duration_ms: elapsed(),
    request_id: requestId,
  });
  return { ok: true, data };
}

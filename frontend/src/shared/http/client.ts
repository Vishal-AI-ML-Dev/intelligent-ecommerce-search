// Same-origin JSON client shared by the app's domain modules.
//
// Logging is deliberately minimal: one line per request with the fixed API path, the HTTP
// status, an outcome code, the duration and the request ID. Request bodies (search queries),
// response bodies, raw error bodies and exception messages are never logged.
//
// Request IDs (M24A): every request sends a generated `X-Request-ID`. The line logs the ID the
// server returned (the one in its logs) when a response arrived, otherwise the generated one.
//
// Milestone 18-05 (retailer returns) adds opt-in `RequestOptions`; without them every request is
// exactly as before. `authorization` is accepted only when the path resolves to this origin's
// `/returns` routes (`returnsDestination`), `logPath` replaces the logged path (so query values
// and record IDs never reach the console), and `detailedErrors` adds the request ID and the
// server's safe error `code` to a failure.

/** A safe `{name, code}` pair of a 422 `invalid_parameter` body; never a submitted value. */
export interface ParameterProblem {
  name: string;
  code: string;
}

export type HttpFailure =
  | { kind: 'network'; requestId?: string }
  | {
      kind: 'http';
      status: number;
      requestId?: string;
      code?: string;
      parameters?: ParameterProblem[];
    }
  | { kind: 'invalid_body'; status: number; requestId?: string };

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
// The server's fixed error codes and parameter names; anything else in an error body is dropped.
const SAFE_TOKEN_PATTERN = /^[a-z_]{1,40}$/;
// A `.` or `..` path segment, written plainly or percent-encoded (`%2e`), in any mix.
const DOT_SEGMENT_PATTERN = /(?:^|\/)(?:\.|%2e){1,2}(?:\/|$)/i;

/**
 * The destination of a request that carries a retailer token: the path resolved the way `fetch`
 * resolves it (against the document base URL), which must be the application's own origin and
 * the pathname `/returns` or one under `/returns/`. Ambiguous forms are refused before parsing:
 * a backslash or a dot segment in the path part (the query is not inspected, so encoded IDs and
 * filter values pass). Returns the resolved path and query, which is exactly what is requested.
 */
function returnsDestination(path: string): string {
  const pathPart = path.split(/[?#]/, 1)[0] ?? '';
  if (pathPart.includes('\\') || DOT_SEGMENT_PATTERN.test(pathPart)) {
    throw new Error('a retailer token may only be sent to /returns paths');
  }
  const url = new URL(path, document.baseURI);
  if (
    url.origin !== window.location.origin ||
    !(url.pathname === '/returns' || url.pathname.startsWith('/returns/'))
  ) {
    throw new Error('a retailer token may only be sent to /returns paths');
  }
  return `${url.pathname}${url.search}`;
}

export interface RequestOptions {
  /** A retailer token, sent as `Authorization: Bearer <token>` to `/returns` paths only. */
  authorization?: string;
  /** A fixed route template logged instead of the real path. */
  logPath?: string;
  /** Add the request ID, the safe error `code` and 422 `parameters` to failures. */
  detailedErrors?: boolean;
  /** Non-2xx statuses whose body is still returned as data when it passes the shape check. */
  bodyStatuses?: readonly number[];
}

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

export function postJson<T>(
  path: string,
  body: unknown,
  isValid: (value: unknown) => value is T,
  signal?: AbortSignal,
  options?: RequestOptions,
): Promise<HttpResult<T>> {
  return requestJson(path, 'POST', { kind: 'json', value: body }, isValid, signal, options);
}

/** Same logging, request-ID and error handling as `postJson`, for a body-less GET. */
export function getJson<T>(
  path: string,
  isValid: (value: unknown) => value is T,
  signal?: AbortSignal,
  options?: RequestOptions,
): Promise<HttpResult<T>> {
  return requestJson(path, 'GET', undefined, isValid, signal, options);
}

/** A `multipart/form-data` POST; the browser sets the boundary. The form is never logged. */
export function postForm<T>(
  path: string,
  form: FormData,
  isValid: (value: unknown) => value is T,
  signal?: AbortSignal,
  options?: RequestOptions,
): Promise<HttpResult<T>> {
  return requestJson(path, 'POST', { kind: 'form', value: form }, isValid, signal, options);
}

type RequestBody = { kind: 'json'; value: unknown } | { kind: 'form'; value: FormData };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isSafeToken = (value: unknown): value is string =>
  typeof value === 'string' && SAFE_TOKEN_PATTERN.test(value);

/** The safe parts of a `{detail, code}` or `{detail, code, parameters}` error body. */
function safeErrorFields(data: unknown): { code?: string; parameters?: ParameterProblem[] } {
  if (!isRecord(data) || typeof data.detail !== 'string' || !isSafeToken(data.code)) {
    return {};
  }
  const parameters = data.parameters;
  if (
    Array.isArray(parameters) &&
    parameters.every((p) => isRecord(p) && isSafeToken(p.name) && isSafeToken(p.code))
  ) {
    return {
      code: data.code,
      parameters: parameters.map((p: Record<string, unknown>) => ({
        name: String(p.name),
        code: String(p.code),
      })),
    };
  }
  return { code: data.code };
}

async function readJson(response: Response, signal?: AbortSignal): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    if (signal?.aborted) {
      throw new RequestAborted();
    }
    return undefined;
  }
}

async function requestJson<T>(
  path: string,
  method: 'GET' | 'POST',
  body: RequestBody | undefined,
  isValid: (value: unknown) => value is T,
  signal?: AbortSignal,
  options: RequestOptions = {},
): Promise<HttpResult<T>> {
  const started = performance.now();
  const elapsed = () => Math.round(performance.now() - started);
  const sentId = newRequestId();
  const logPath = options.logPath ?? path;
  const detailed = options.detailedErrors === true;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    [REQUEST_ID_HEADER]: sentId,
  };
  if (body?.kind === 'json') {
    headers['Content-Type'] = 'application/json';
  }
  // Unauthenticated requests go to `path` unchanged, as before.
  let target = path;
  if (options.authorization !== undefined) {
    target = returnsDestination(path);
    headers.Authorization = `Bearer ${options.authorization}`;
  }
  let response: Response;
  try {
    response = await fetch(target, {
      method,
      headers,
      body: body === undefined ? undefined : body.kind === 'json' ? JSON.stringify(body.value) : body.value,
      credentials: 'same-origin',
      signal,
    });
  } catch {
    if (signal?.aborted) {
      throw new RequestAborted();
    }
    logRequest({
      path: logPath,
      status: null,
      outcome: 'network_error',
      duration_ms: elapsed(),
      request_id: sentId,
    });
    return { ok: false, error: detailed ? { kind: 'network', requestId: sentId } : { kind: 'network' } };
  }
  const requestId = responseRequestId(response, sentId);
  if (!response.ok) {
    logRequest({
      path: logPath,
      status: response.status,
      outcome: 'http_error',
      duration_ms: elapsed(),
      request_id: requestId,
    });
    const bodyExpected = options.bodyStatuses?.includes(response.status) === true;
    if (!detailed && !bodyExpected) {
      return { ok: false, error: { kind: 'http', status: response.status } };
    }
    const errorBody = await readJson(response, signal);
    if (bodyExpected && isValid(errorBody)) {
      return { ok: true, data: errorBody };
    }
    if (!detailed) {
      return { ok: false, error: { kind: 'http', status: response.status } };
    }
    return {
      ok: false,
      error: { kind: 'http', status: response.status, requestId, ...safeErrorFields(errorBody) },
    };
  }
  const data = await readJson(response, signal);
  if (!isValid(data)) {
    logRequest({
      path: logPath,
      status: response.status,
      outcome: 'invalid_body',
      duration_ms: elapsed(),
      request_id: requestId,
    });
    return {
      ok: false,
      error: detailed
        ? { kind: 'invalid_body', status: response.status, requestId }
        : { kind: 'invalid_body', status: response.status },
    };
  }
  logRequest({
    path: logPath,
    status: response.status,
    outcome: 'ok',
    duration_ms: elapsed(),
    request_id: requestId,
  });
  return { ok: true, data };
}

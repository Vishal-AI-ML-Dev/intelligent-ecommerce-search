import {
  getJson,
  type HttpResult,
  postForm,
  type RequestOptions,
} from '../../shared/http/client';
import type {
  BatchDetail,
  BatchPage,
  BatchSummary,
  ImportReport,
  ListingSnapshot,
  Provenance,
  ReturnDetail,
  ReturnFilters,
  ReturnPage,
  ReturnRef,
  ReturnsCapabilities,
  ReturnsLimits,
  ReturnSummary,
  RowError,
} from './types';

// Every `/returns` call sends the token, logs a fixed route template (never query values or
// record IDs) and returns failures with the request ID and the server's safe error code.
export const RETURNS_PATHS = {
  capabilities: '/returns/capabilities',
  validate: '/returns/import/validate',
  import: '/returns/import',
  list: '/returns',
  detail: '/returns/records/{dataset_id}/{dataset_version}/{return_id}',
  batches: '/returns/import-batches',
  batch: '/returns/import-batches/{batch_id}',
} as const;

function options(token: string, logPath: string, extra: RequestOptions = {}): RequestOptions {
  return { authorization: token, logPath, detailedErrors: true, ...extra };
}

// ---- shape checks: the fields this tab renders; the API contract is checked by pytest --------

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const isString = (value: unknown): value is string => typeof value === 'string';
const isNumber = (value: unknown): value is number => typeof value === 'number';
const isBoolean = (value: unknown): value is boolean => typeof value === 'boolean';
const isNullable =
  <T>(check: (value: unknown) => value is T) =>
  (value: unknown): value is T | null =>
    value === null || check(value);
const isArrayOf =
  <T>(check: (value: unknown) => value is T) =>
  (value: unknown): value is T[] =>
    Array.isArray(value) && value.every(check);

function hasFields(value: unknown, fields: Record<string, (v: unknown) => boolean>): boolean {
  return isRecord(value) && Object.entries(fields).every(([key, check]) => check(value[key]));
}

const LIMIT_FIELDS = [
  'request_body_bytes',
  'bundle_bytes',
  'file_bytes',
  'manifest_bytes',
  'files',
  'bundle_rows',
  'entity_rows',
  'csv_columns',
  'csv_field_bytes',
  'text_code_points',
  'note_code_points',
  'dataset_long_text_code_points',
  'dataset_short_text_code_points',
  'errors_collected',
  'errors_returned',
  'concurrent_imports',
] as const;

const isLimits = (value: unknown): value is ReturnsLimits =>
  isRecord(value) && LIMIT_FIELDS.every((key) => isNumber(value[key]));

export function isCapabilities(value: unknown): value is ReturnsCapabilities {
  return hasFields(value, {
    contract_version: isString,
    scopes: isArrayOf(isString),
    can_validate: isBoolean,
    can_commit: isBoolean,
    limits: isLimits,
    reason_codes: isArrayOf(isString),
  });
}

const isRowError = (value: unknown): value is RowError =>
  hasFields(value, {
    file: isString,
    entity: isNullable(isString),
    row: isNullable(isNumber),
    line: isNullable(isNumber),
    pointer: isNullable(isString),
    field: isNullable(isString),
    code: isString,
    message: isString,
    value_preview: isNullable(isString),
  });

const isReportCounts = (value: unknown): boolean =>
  isRecord(value) &&
  Object.values(value).every((c) =>
    hasFields(c, { received: isNumber, new: isNullable(isNumber), unchanged: isNullable(isNumber) }),
  );

const IMPORT_STATUSES = new Set(['valid', 'invalid', 'imported', 'already_imported', 'no_new_records']);

export function isImportReport(value: unknown): value is ImportReport {
  return hasFields(value, {
    contract_version: isString,
    mode: (v) => v === 'validate' || v === 'commit',
    status: (v) => isString(v) && IMPORT_STATUSES.has(v),
    bundle_sha256: isNullable(isString),
    batch_id: isNullable(isString),
    checked_at: isString,
    counts: isReportCounts,
    phases_completed: isArrayOf(isString),
    error_count: isNumber,
    error_count_is_lower_bound: isBoolean,
    errors_truncated: isBoolean,
    errors: isArrayOf(isRowError),
  });
}

const isProvenance = (value: unknown): value is Provenance =>
  hasFields(value, {
    source_type: isString,
    source_name: isString,
    license: isNullable(isString),
    source_record_id: isString,
    dataset_version: isNumber,
    ingestion_timestamp: isString,
    transformation_version: isString,
    is_synthetic: isBoolean,
    batch_id: isString,
    source_file: isString,
    source_row: isNumber,
  });

const isRef = (value: unknown): value is ReturnRef =>
  hasFields(value, { dataset_id: isString, dataset_version: isNumber, return_id: isString });

const isProduct = (value: unknown): boolean =>
  hasFields(value, {
    product_id: isString,
    sku: isString,
    title: isString,
    category: isString,
    brand: isString,
  });

const isSummary = (value: unknown): value is ReturnSummary =>
  hasFields(value, {
    ref: isRef,
    requested_at: isString,
    status: isString,
    quantity: isNumber,
    customer_reason_code: isString,
    product: isProduct,
    order_id: isString,
    order_item_id: isString,
    comment_count: isNumber,
    inspection_count: isNumber,
    is_synthetic: isBoolean,
    provenance: isProvenance,
  });

const isPageOf =
  (check: (value: unknown) => boolean) =>
  (value: unknown): boolean =>
    hasFields(value, {
      items: (v) => Array.isArray(v) && v.every(check),
      next_cursor: isNullable(isString),
      limit: isNumber,
    });

export const isReturnPage = (value: unknown): value is ReturnPage => isPageOf(isSummary)(value);

const withProvenance = (fields: Record<string, (v: unknown) => boolean>) => (value: unknown) =>
  hasFields(value, { ...fields, provenance: isProvenance });

const isSnapshot = (value: unknown): value is ListingSnapshot =>
  hasFields(value, {
    catalog_dataset_id: isString,
    catalog_dataset_version: isString,
    raw_sha256: isString,
    matches_current_catalog: isBoolean,
    title: isString,
    brand: isString,
    category: isString,
    currency: isString,
    price: (v) => isNumber(v) || isString(v),
  });

const isComment = withProvenance({ comment_id: isString, submitted_at: isString, text: isString });
const isObservation = withProvenance({
  observation_id: isString,
  observed_at: isString,
  inspector_ref: isString,
  condition: isString,
  note: isNullable(isString),
});
const isEvent = withProvenance({
  event_id: isString,
  occurred_at: isString,
  event_type: isString,
  source: isString,
});
const isShipment = withProvenance({
  shipment_id: isString,
  carrier_ref: isString,
  status: isString,
  shipped_at: isNullable(isString),
  promised_delivery_by: isNullable(isString),
  delivered_at: isNullable(isString),
  delivery_events: (v) => Array.isArray(v) && v.every(isEvent),
});
const isReview = withProvenance({
  review_id: isString,
  product_id: isString,
  order_item_id: isNullable(isString),
  rating: isNumber,
  submitted_at: isString,
  text: isNullable(isString),
});
const isComplaint = withProvenance({
  complaint_id: isString,
  order_id: isNullable(isString),
  order_item_id: isNullable(isString),
  product_id: isNullable(isString),
  opened_at: isString,
  channel: isString,
  customer_topic: isNullable(isString),
  text: isString,
});
const isCase = withProvenance({
  case_id: isString,
  focus_area: isString,
  status: isString,
  opened_at: isString,
  evidence_count: isNumber,
});
const every = (check: (v: unknown) => boolean) => (v: unknown) => Array.isArray(v) && v.every(check);

export function isReturnDetail(value: unknown): value is ReturnDetail {
  return hasFields(value, {
    ref: isRef,
    requested_at: isString,
    status: isString,
    quantity: isNumber,
    received_at: isNullable(isString),
    order: withProvenance({
      order_id: isString,
      customer_ref: isString,
      ordered_at: isString,
      currency: isString,
    }),
    order_item: withProvenance({ order_item_id: isString, quantity: isNumber, unit_price: isString }),
    product: isProduct,
    listing_snapshot: isSnapshot,
    customer_statements: (v) =>
      hasFields(v, { customer_reason_code: isString, comments: every(isComment) }),
    inspection_observations: every(isObservation),
    shipment: (v) => v === null || isShipment(v),
    related_reviews: every(isReview),
    related_complaints: every(isComplaint),
    correction_cases: every(isCase),
    evidence_presence: (v) =>
      hasFields(v, {
        return_comments: isNumber,
        inspection_observations: isNumber,
        shipment: isNumber,
        delivery_events: isNumber,
        related_reviews: isNumber,
        related_complaints: isNumber,
        correction_cases: isNumber,
      }),
    provenance: isProvenance,
  });
}

const BATCH_FIELDS = {
  batch_id: isString,
  dataset_id: isString,
  dataset_version: isNumber,
  contract_version: isString,
  transformation_version: isString,
  redaction_version: isString,
  bundle_sha256: isString,
  format: (v: unknown) => v === 'json' || v === 'csv',
  file_count: isNumber,
  total_bytes: isNumber,
  counts: (v: unknown) =>
    isRecord(v) &&
    Object.values(v).every((c) =>
      hasFields(c, { received: isNumber, new: isNumber, unchanged: isNumber }),
    ),
  imported_by_label: isString,
  imported_at: isString,
};

const isBatchSummary = (value: unknown): value is BatchSummary => hasFields(value, BATCH_FIELDS);

export const isBatchPage = (value: unknown): value is BatchPage => isPageOf(isBatchSummary)(value);

export function isBatchDetail(value: unknown): value is BatchDetail {
  return hasFields(value, {
    ...BATCH_FIELDS,
    files: every((f) =>
      hasFields(f, {
        name: isString,
        entity: isNullable(isString),
        media_type: isString,
        size_bytes: isNumber,
        raw_sha256: isString,
        row_count: isNullable(isNumber),
      }),
    ),
  });
}

// ---- requests --------------------------------------------------------------------------------

export function fetchCapabilities(
  token: string,
  signal?: AbortSignal,
): Promise<HttpResult<ReturnsCapabilities>> {
  return getJson(
    RETURNS_PATHS.capabilities,
    isCapabilities,
    signal,
    options(token, RETURNS_PATHS.capabilities),
  );
}

/** Validate only (contracts §7): 200 with the report; nothing is stored. */
export function validateUpload(
  token: string,
  form: FormData,
  signal?: AbortSignal,
): Promise<HttpResult<ImportReport>> {
  return postForm(
    RETURNS_PATHS.validate,
    form,
    isImportReport,
    signal,
    options(token, RETURNS_PATHS.validate),
  );
}

/** Commit (contracts §7): 201 imported; 200 already imported / nothing new; 422 an `invalid`
 * report (same body), which is returned as data. */
export function importUpload(
  token: string,
  form: FormData,
  signal?: AbortSignal,
): Promise<HttpResult<ImportReport>> {
  return postForm(
    RETURNS_PATHS.import,
    form,
    isImportReport,
    signal,
    options(token, RETURNS_PATHS.import, { bodyStatuses: [422] }),
  );
}

function query(params: [string, string][]): string {
  const search = new URLSearchParams(params).toString();
  return search === '' ? '' : `?${search}`;
}

/** Non-empty filters, trimmed, in a fixed order; the same filters must be sent with a cursor. */
export function filterParams(filters: ReturnFilters): [string, string][] {
  return (Object.keys(filters) as (keyof ReturnFilters)[])
    .map((key): [string, string] => [key, filters[key].trim()])
    .filter(([, value]) => value !== '');
}

export function listReturns(
  token: string,
  filters: ReturnFilters,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<HttpResult<ReturnPage>> {
  const params = filterParams(filters);
  if (cursor !== null) {
    params.push(['cursor', cursor]);
  }
  return getJson(
    `${RETURNS_PATHS.list}${query(params)}`,
    isReturnPage,
    signal,
    options(token, RETURNS_PATHS.list),
  );
}

export function getReturn(
  token: string,
  ref: ReturnRef,
  signal?: AbortSignal,
): Promise<HttpResult<ReturnDetail>> {
  const path = ['/returns/records', ref.dataset_id, String(ref.dataset_version), ref.return_id]
    .map((part, index) => (index === 0 ? part : encodeURIComponent(part)))
    .join('/');
  return getJson(path, isReturnDetail, signal, options(token, RETURNS_PATHS.detail));
}

export function listBatches(
  token: string,
  cursor: string | null,
  signal?: AbortSignal,
): Promise<HttpResult<BatchPage>> {
  return getJson(
    `${RETURNS_PATHS.batches}${query(cursor === null ? [] : [['cursor', cursor]])}`,
    isBatchPage,
    signal,
    options(token, RETURNS_PATHS.batches),
  );
}

export function getBatch(
  token: string,
  batchId: string,
  signal?: AbortSignal,
): Promise<HttpResult<BatchDetail>> {
  return getJson(
    `${RETURNS_PATHS.batches}/${encodeURIComponent(batchId)}`,
    isBatchDetail,
    signal,
    options(token, RETURNS_PATHS.batch),
  );
}

// ---- uploads (contracts §4.1, §4.4) ----------------------------------------------------------

export type UploadMode = 'json' | 'csv';

export const BUNDLE_PART = 'bundle.json';
export const MANIFEST_PART = 'manifest.json';

/**
 * Advisory pre-checks against the server's published limits, so an obviously oversized or
 * incomplete set is not sent. The server re-checks everything and stays authoritative.
 */
export function precheckUpload(mode: UploadMode, files: File[], limits: ReturnsLimits): string[] {
  const problems: string[] = [];
  if (files.length === 0) {
    return ['Choose a file to upload.'];
  }
  if (mode === 'json' && files.length !== 1) {
    problems.push('A JSON bundle is a single file.');
  }
  if (mode === 'csv') {
    if (files.length > limits.files) {
      problems.push(`A CSV set has at most ${limits.files} files.`);
    }
    const names = files.map((file) => file.name);
    if (!names.includes(MANIFEST_PART)) {
      problems.push(`A CSV set needs its ${MANIFEST_PART}.`);
    }
    if (new Set(names).size !== names.length) {
      problems.push('Each file in a CSV set must have a different name.');
    }
    const manifest = files.find((file) => file.name === MANIFEST_PART);
    if (manifest !== undefined && manifest.size > limits.manifest_bytes) {
      problems.push(`${MANIFEST_PART} is larger than ${formatBytes(limits.manifest_bytes)}.`);
    }
  }
  if (files.some((file) => file.size > limits.file_bytes)) {
    problems.push(`Each file must be at most ${formatBytes(limits.file_bytes)}.`);
  }
  const total = files.reduce((sum, file) => sum + file.size, 0);
  if (total > limits.bundle_bytes) {
    problems.push(`The upload must be at most ${formatBytes(limits.bundle_bytes)} in total.`);
  }
  return problems;
}

/**
 * One part per file. A JSON bundle is always sent under its canonical part name
 * `bundle.json` (§4.1); CSV-set parts keep their own file names, which the server checks
 * against the entity allowlist.
 */
export function buildUploadForm(mode: UploadMode, files: File[]): FormData {
  const form = new FormData();
  for (const file of files) {
    const name = mode === 'json' ? BUNDLE_PART : file.name;
    form.append(name, file, name);
  }
  return form;
}

export function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) {
    return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
  }
  if (bytes >= 1024) {
    return `${Number((bytes / 1024).toFixed(1))} KiB`;
  }
  return `${bytes} bytes`;
}

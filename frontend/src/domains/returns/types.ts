// The parts of the M18 `/returns` API contract (docs/m18/contracts.md §4.8, §7, §7.1) this tab
// reads. Hand-written to mirror `ReturnsCapabilities` (`api/returns.py`), `ImportReport`
// (`returns/service.py`) and the read models in `api/returns_schemas.py`; the API is the source of
// truth. Timestamps are the server's canonical UTC strings and are shown unchanged. Free text is
// the server's redacted form and is rendered as text only.

export type ReturnsScope = 'returns:read' | 'returns:validate' | 'returns:import';

export interface ReturnsLimits {
  request_body_bytes: number;
  bundle_bytes: number;
  file_bytes: number;
  manifest_bytes: number;
  files: number;
  bundle_rows: number;
  entity_rows: number;
  csv_columns: number;
  csv_field_bytes: number;
  text_code_points: number;
  note_code_points: number;
  dataset_long_text_code_points: number;
  dataset_short_text_code_points: number;
  errors_collected: number;
  errors_returned: number;
  concurrent_imports: number;
}

export interface ReturnsCapabilities {
  contract_version: string;
  scopes: ReturnsScope[];
  can_validate: boolean;
  can_commit: boolean;
  limits: ReturnsLimits;
  reason_codes: string[];
}

// ---- import report (§4.8) --------------------------------------------------------------------

export type ImportStatus = 'valid' | 'invalid' | 'imported' | 'already_imported' | 'no_new_records';

export interface ReportEntityCounts {
  received: number;
  /** Null when the natural-key comparison did not run. */
  new: number | null;
  unchanged: number | null;
}

export interface RowError {
  file: string;
  entity: string | null;
  row: number | null;
  line: number | null;
  pointer: string | null;
  field: string | null;
  code: string;
  message: string;
  value_preview: string | null;
}

export interface ImportReport {
  contract_version: string;
  mode: 'validate' | 'commit';
  status: ImportStatus;
  bundle_sha256: string | null;
  batch_id: string | null;
  checked_at: string;
  counts: Record<string, ReportEntityCounts>;
  phases_completed: string[];
  error_count: number;
  error_count_is_lower_bound: boolean;
  errors_truncated: boolean;
  errors: RowError[];
}

// ---- read API (§7, §7.1) ---------------------------------------------------------------------

export interface ReturnRef {
  dataset_id: string;
  dataset_version: number;
  return_id: string;
}

export interface ProductRef {
  product_id: string;
  sku: string;
  title: string;
  category: string;
  brand: string;
}

/** The eight MASTER_PLAN §9 fields plus batch, source file and row (§7, D13). */
export interface Provenance {
  source_type: string;
  source_name: string;
  license: string | null;
  source_record_id: string;
  dataset_version: number;
  ingestion_timestamp: string;
  transformation_version: string;
  is_synthetic: boolean;
  batch_id: string;
  source_file: string;
  source_row: number;
}

export interface ReturnSummary {
  ref: ReturnRef;
  requested_at: string;
  status: string;
  quantity: number;
  customer_reason_code: string;
  product: ProductRef;
  order_id: string;
  order_item_id: string;
  comment_count: number;
  inspection_count: number;
  is_synthetic: boolean;
  provenance: Provenance;
}

export interface ReturnPage {
  items: ReturnSummary[];
  next_cursor: string | null;
  limit: number;
}

export interface OrderFacts {
  order_id: string;
  customer_ref: string;
  ordered_at: string;
  currency: string;
  provenance: Provenance;
}

export interface OrderItemFacts {
  order_item_id: string;
  quantity: number;
  /** Two-digit decimal text; never parsed to a float. */
  unit_price: string;
  provenance: Provenance;
}

export interface ListingSnapshot {
  catalog_dataset_id: string;
  catalog_dataset_version: string;
  raw_sha256: string;
  matches_current_catalog: boolean;
  title: string;
  brand: string;
  category: string;
  currency: string;
  /** The raw catalog value: a JSON integer or seller text (§7.1 M1), shown as given. */
  price: number | string;
}

export interface ReturnComment {
  comment_id: string;
  submitted_at: string;
  text: string;
  provenance: Provenance;
}

export interface CustomerStatements {
  customer_reason_code: string;
  comments: ReturnComment[];
}

export interface InspectionObservation {
  observation_id: string;
  observed_at: string;
  inspector_ref: string;
  condition: string;
  note: string | null;
  provenance: Provenance;
}

export interface DeliveryEvent {
  event_id: string;
  occurred_at: string;
  event_type: string;
  source: string;
  provenance: Provenance;
}

export interface Shipment {
  shipment_id: string;
  carrier_ref: string;
  status: string;
  shipped_at: string | null;
  promised_delivery_by: string | null;
  delivered_at: string | null;
  delivery_events: DeliveryEvent[];
  provenance: Provenance;
}

export interface RelatedReview {
  review_id: string;
  product_id: string;
  order_item_id: string | null;
  rating: number;
  submitted_at: string;
  text: string | null;
  provenance: Provenance;
}

export interface RelatedComplaint {
  complaint_id: string;
  order_id: string | null;
  order_item_id: string | null;
  product_id: string | null;
  opened_at: string;
  channel: string;
  customer_topic: string | null;
  text: string;
  provenance: Provenance;
}

export interface CorrectionCaseRef {
  case_id: string;
  focus_area: string;
  status: string;
  opened_at: string;
  evidence_count: number;
  provenance: Provenance;
}

/** Counts per kind; zero means "not recorded", never "none happened". */
export interface EvidencePresence {
  return_comments: number;
  inspection_observations: number;
  shipment: number;
  delivery_events: number;
  related_reviews: number;
  related_complaints: number;
  correction_cases: number;
}

export interface ReturnDetail {
  ref: ReturnRef;
  requested_at: string;
  status: string;
  quantity: number;
  received_at: string | null;
  order: OrderFacts;
  order_item: OrderItemFacts;
  product: ProductRef;
  listing_snapshot: ListingSnapshot;
  customer_statements: CustomerStatements;
  inspection_observations: InspectionObservation[];
  shipment: Shipment | null;
  related_reviews: RelatedReview[];
  related_complaints: RelatedComplaint[];
  correction_cases: CorrectionCaseRef[];
  evidence_presence: EvidencePresence;
  provenance: Provenance;
}

// ---- import batches --------------------------------------------------------------------------

export interface BatchEntityCounts {
  received: number;
  new: number;
  unchanged: number;
}

export interface BatchSummary {
  batch_id: string;
  dataset_id: string;
  dataset_version: number;
  contract_version: string;
  transformation_version: string;
  redaction_version: string;
  bundle_sha256: string;
  format: 'json' | 'csv';
  file_count: number;
  total_bytes: number;
  counts: Record<string, BatchEntityCounts>;
  imported_by_label: string;
  imported_at: string;
}

export interface BatchFile {
  name: string;
  entity: string | null;
  media_type: string;
  size_bytes: number;
  raw_sha256: string;
  row_count: number | null;
}

export interface BatchDetail extends BatchSummary {
  files: BatchFile[];
}

export interface BatchPage {
  items: BatchSummary[];
  next_cursor: string | null;
  limit: number;
}

// ---- list filters (§7, §7.1 D6a) -------------------------------------------------------------

/** Filter values as typed; empty strings are omitted from the query. */
export interface ReturnFilters {
  dataset_id: string;
  dataset_version: string;
  status: string;
  customer_reason_code: string;
  category: string;
  product_id: string;
  requested_from: string;
  requested_to: string;
  has_comment: '' | 'true' | 'false';
  has_inspection: '' | 'true' | 'false';
  batch_id: string;
}

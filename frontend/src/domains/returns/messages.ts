import type { HttpFailure } from '../../shared/http/client';

// Fixed client texts per server error code (contracts §7). The server's `detail` is never shown,
// so nothing the server or an upload supplies becomes a message.
const CODE_MESSAGES: Record<string, string> = {
  retailer_access_not_configured: 'Retailer access is not configured on this server.',
  unauthorized: 'Your access token was not accepted. Sign in again.',
  forbidden: 'Your access token does not allow this action.',
  import_commit_disabled: 'Imports are disabled on this server.',
  invalid_parameter: 'Some values are not valid.',
  malformed_cursor: 'This page is no longer available. Start again from the first page.',
  cursor_mismatch: 'This page belongs to different filters. Start again from the first page.',
  return_not_found: 'This return was not found.',
  batch_not_found: 'This import batch was not found.',
  unsupported_media_type: 'The upload could not be read as a file upload.',
  malformed_upload: 'The upload could not be read as a file upload.',
  payload_too_large: 'The upload is larger than the server accepts.',
  upload_timeout: 'The upload took too long to arrive. Try again.',
  import_busy: 'Another import or validation is in progress. Try again shortly.',
  import_storage_unavailable: 'The import could not be completed; nothing was stored.',
  not_found: 'The requested item was not found.',
  internal_error: 'Something went wrong on the server. Try again.',
};

export const NOT_CONFIGURED_MESSAGE = CODE_MESSAGES.retailer_access_not_configured as string;
export const IMPORTS_DISABLED_MESSAGE = CODE_MESSAGES.import_commit_disabled as string;

export function errorMessage(error: HttpFailure): string {
  if (error.kind === 'network') {
    return 'Could not reach the server. Check your connection and try again.';
  }
  if (error.kind === 'invalid_body') {
    return 'The server sent a response this page could not read.';
  }
  const byCode = error.code === undefined ? undefined : CODE_MESSAGES[error.code];
  if (byCode !== undefined) {
    return byCode;
  }
  if (error.status === 401) {
    return CODE_MESSAGES.unauthorized as string;
  }
  if (error.status === 403) {
    return CODE_MESSAGES.forbidden as string;
  }
  return 'The request could not be completed. Try again.';
}

export function isUnauthorized(error: HttpFailure): boolean {
  return error.kind === 'http' && error.status === 401;
}

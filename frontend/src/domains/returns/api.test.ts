import { beforeEach, describe, expect, it, vi } from 'vitest';
import { returnsFixtures } from '../../../e2e/fixtures';
import {
  buildUploadForm,
  getBatch,
  getReturn,
  isBatchDetail,
  isBatchPage,
  isCapabilities,
  isImportReport,
  isReturnDetail,
  isReturnPage,
  listBatches,
  listReturns,
  precheckUpload,
} from './api';
import { errorSummary } from './ImportReportView';
import { EMPTY_FILTERS } from './ReturnsList';

const TOKEN = 'synthetic-test-token-abcdefghijklmnopqrstuvwxyz';
const LIMITS = returnsFixtures.capabilitiesImporter.limits;

function capture() {
  const info = vi.spyOn(console, 'info').mockImplementation(() => undefined);
  const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
  return () => [...info.mock.calls, ...warn.mock.calls].map((args) => args.map(String).join(' ')).join('\n');
}

function respond(body: unknown, status = 200) {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof vi.fn>, index = 0): [string, RequestInit] {
  return fetchMock.mock.calls[index] as unknown as [string, RequestInit];
}

beforeEach(() => {
  capture();
});

describe('shape checks', () => {
  const f = returnsFixtures;
  it.each([
    ['capabilities', isCapabilities, [f.capabilitiesImporter, f.capabilitiesViewer]],
    [
      'import reports',
      isImportReport,
      [f.reportValid, f.reportImported, f.reportImportInvalid, f.reportInvalidTruncated, f.reportInvalidLowerBound],
    ],
    ['return pages', isReturnPage, [f.returnsPage1, f.returnsPage2, f.returnsPageEmpty]],
    ['return details', isReturnDetail, [f.returnDetailConflict, f.returnDetailNotRecorded]],
    ['batch pages', isBatchPage, [f.batchesPage, f.batchesPageEmpty]],
    ['batch detail', isBatchDetail, [f.batchDetail]],
  ] as [string, (v: unknown) => boolean, unknown[]][])('accept every %s fixture', (_name, check, bodies) => {
    for (const body of bodies) {
      expect(check(body)).toBe(true);
    }
  });

  it('rejects error bodies and wrong shapes', () => {
    expect(isCapabilities(f.errorUnauthorized)).toBe(false);
    expect(isImportReport({ ...f.reportValid, status: 'approved' })).toBe(false);
    expect(isReturnPage({ ...f.returnsPage1, items: [{}] })).toBe(false);
    expect(isReturnDetail({ ...f.returnDetailConflict, provenance: null })).toBe(false);
  });
});

describe('requests', () => {
  it('sends trimmed non-empty filters and the cursor, and logs only the route template', async () => {
    const fetchMock = respond(returnsFixtures.returnsPage1);
    const logs = capture();
    await listReturns(
      TOKEN,
      { ...EMPTY_FILTERS, status: 'received', product_id: '  FIXTURE-SHO-0001 ', has_comment: 'true' },
      'fixture-cursor-page-two',
    );
    const [path, init] = call(fetchMock);
    expect(path).toBe(
      '/returns?status=received&product_id=FIXTURE-SHO-0001&has_comment=true&cursor=fixture-cursor-page-two',
    );
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(logs()).toContain('"path":"/returns"');
    for (const secret of [TOKEN, 'FIXTURE-SHO-0001', 'fixture-cursor', 'received']) {
      expect(logs()).not.toContain(secret);
    }
  });

  it('builds the qualified detail path with encoded parts and logs only its template', async () => {
    const fetchMock = respond(returnsFixtures.returnDetailConflict);
    const logs = capture();
    const result = await getReturn(TOKEN, { dataset_id: 'fixture-returns', dataset_version: 1, return_id: 'A:B/C' });
    expect(result.ok).toBe(true);
    expect(call(fetchMock)[0]).toBe('/returns/records/fixture-returns/1/A%3AB%2FC');
    expect(logs()).toContain('/returns/records/{dataset_id}/{dataset_version}/{return_id}');
    expect(logs()).not.toContain('A%3AB');
  });

  it('pages batches and fetches one batch by ID', async () => {
    const fetchMock = respond(returnsFixtures.batchesPage);
    await listBatches(TOKEN, null);
    await listBatches(TOKEN, 'fixture-cursor');
    respond(returnsFixtures.batchDetail);
    const logs = capture();
    await getBatch(TOKEN, returnsFixtures.batchDetail.batch_id);
    expect(call(fetchMock, 0)[0]).toBe('/returns/import-batches');
    expect(call(fetchMock, 1)[0]).toBe('/returns/import-batches?cursor=fixture-cursor');
    expect(logs()).toContain('/returns/import-batches/{batch_id}');
    expect(logs()).not.toContain(returnsFixtures.batchDetail.batch_id);
  });
});

describe('uploads', () => {
  const file = (name: string, size = 10) => new File(['x'.repeat(size)], name);

  it('always sends a JSON bundle as the canonical part bundle.json', () => {
    const form = buildUploadForm('json', [file('returns_v1.json')]);
    expect([...form.keys()]).toEqual(['bundle.json']);
    expect((form.get('bundle.json') as File).name).toBe('bundle.json');
  });

  it('keeps CSV-set part names', () => {
    const form = buildUploadForm('csv', [file('manifest.json'), file('returns.csv')]);
    expect([...form.keys()]).toEqual(['manifest.json', 'returns.csv']);
  });

  it('accepts a well-formed selection', () => {
    expect(precheckUpload('json', [file('bundle.json')], LIMITS)).toEqual([]);
    expect(precheckUpload('csv', [file('manifest.json'), file('orders.csv')], LIMITS)).toEqual([]);
  });

  it.each([
    ['nothing chosen', 'json', [], 'Choose a file'],
    ['two bundle files', 'json', [file('a.json'), file('b.json')], 'single file'],
    ['no manifest', 'csv', [file('orders.csv')], 'needs its manifest.json'],
    ['duplicate names', 'csv', [file('manifest.json'), file('orders.csv'), file('orders.csv')], 'different name'],
    ['too many files', 'csv', [file('manifest.json'), ...Array.from({ length: 12 }, (_, i) => file(`f${i}.csv`))], 'at most 12 files'],
    ['a large manifest', 'csv', [file('manifest.json', 65537)], 'manifest.json is larger'],
    ['a large file', 'json', [file('bundle.json', 5 * 1024 * 1024 + 1)], 'at most 5 MiB'],
  ] as [string, 'json' | 'csv', File[], string][])('flags %s', (_label, mode, files, text) => {
    expect(precheckUpload(mode, files, LIMITS).join(' ')).toContain(text);
  });

  it('flags a total above the bundle limit', () => {
    const big = Array.from({ length: 2 }, (_, i) => file(`f${i}.csv`, 4 * 1024 * 1024 + 1));
    expect(precheckUpload('csv', [file('manifest.json'), ...big], LIMITS).join(' ')).toContain('8 MiB in total');
  });
});

describe('errorSummary (§4.8)', () => {
  const base = returnsFixtures.reportInvalidTruncated;

  it.each([
    [{ errors_truncated: true, error_count_is_lower_bound: true }, 'Showing 200 of at least 345 errors.'],
    [{ errors_truncated: true, error_count_is_lower_bound: false }, 'Showing 200 of 345 errors.'],
  ])('reports truncation %#', (flags, text) => {
    expect(errorSummary({ ...base, ...flags })).toBe(text);
  });

  it('reports a lower bound without truncation', () => {
    expect(errorSummary(returnsFixtures.reportInvalidLowerBound)).toBe(
      '3 errors found; at least 3 in total, because later checks did not run.',
    );
  });

  it('reports no errors and exact counts', () => {
    expect(errorSummary(returnsFixtures.reportValid)).toBe('No errors.');
    expect(errorSummary(returnsFixtures.reportImportInvalid)).toBe('1 error.');
  });
});

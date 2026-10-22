import { describe, expect, it } from 'vitest';
import { assistantFixtures } from '../../../e2e/fixtures';
import { buildComparison, NOT_GIVEN } from './comparison';
import type { EvidenceProduct } from './types';

const [first, second, third] = assistantFixtures.answer.evidence as [
  EvidenceProduct,
  EvidenceProduct,
  EvidenceProduct,
];

const row = (rows: ReturnType<typeof buildComparison>, label: string) => {
  const found = rows.find((r) => r.label === label);
  if (found === undefined) {
    throw new Error(`missing row ${label}`);
  }
  return found.cells;
};

describe('buildComparison', () => {
  it('uses catalog values only and shows missing values as "not given"', () => {
    const rows = buildComparison([first, third]);
    expect(row(rows, 'Price').map((c) => c.text)).toEqual(['INR 45999.00', 'INR 49999.99']);
    expect(row(rows, 'RAM').map((c) => c.text)).toEqual(['16 GB', NOT_GIVEN]);
    expect(row(rows, 'Storage interface').map((c) => c.text)).toEqual(['NVME', NOT_GIVEN]);
    expect(row(rows, 'Availability').map((c) => c.text)).toEqual(['in stock', 'out of stock']);
  });

  it('marks the extreme only among known, differing values', () => {
    const rows = buildComparison([first, second, third]);
    expect(row(rows, 'Price').map((c) => c.note)).toEqual([null, 'lowest', null]);
    expect(row(rows, 'RAM').map((c) => c.note)).toEqual(['highest', null, null]);
    // Ratings 4.1, none, 4.1: one distinct known value, so no note.
    expect(row(rows, 'Rating').map((c) => c.note)).toEqual([null, null, null]);
    expect(row(rows, 'Rating')[1]?.text).toBe(NOT_GIVEN);
  });

  it('never notes a single known value', () => {
    const rows = buildComparison([first, third]);
    expect(row(rows, 'RAM').map((c) => c.note)).toEqual([null, null]);
  });

  it('gives no price note across different currencies', () => {
    const rows = buildComparison([first, { ...second, currency: 'USD' }]);
    expect(row(rows, 'Price').map((c) => c.note)).toEqual([null, null]);
  });

  it('gives equal known values no note, and marks every tied extreme', () => {
    const equal = buildComparison([first, { ...second, ram_gb: 16 }, third]);
    expect(row(equal, 'RAM').map((c) => c.note)).toEqual([null, null, null]);
    const tie = buildComparison([first, { ...third, ram_gb: 16 }, second]);
    expect(row(tie, 'RAM').map((c) => c.note)).toEqual(['highest', 'highest', null]);
  });
});

// Deterministic side-by-side comparison (Milestone 16) of products the assistant already
// retrieved. Every cell is a catalog value from the response evidence; nothing is generated,
// fetched or inferred. A value the catalog does not give is shown as "not given". Notes such as
// "lowest" compare only the products that have a value, and never claim one product is better.

import type { EvidenceProduct } from './types';

export const MAX_COMPARED = 3;
export const NOT_GIVEN = 'not given';

export interface ComparisonCell {
  text: string;
  note: string | null;
}

export interface ComparisonRow {
  label: string;
  cells: ComparisonCell[];
}

type Extreme = 'lowest' | 'highest';

interface RowSpec {
  label: string;
  text: (p: EvidenceProduct) => string | null;
  /** Numeric value for an extreme note, and which extreme to mark. */
  numeric?: { value: (p: EvidenceProduct) => number | null; mark: Extreme };
}

const plain = (code: string) => code.replaceAll('_', ' ');

const ROWS: RowSpec[] = [
  {
    label: 'Price',
    text: (p) => `${p.currency} ${p.price}`,
    // Parsed for ordering only (catalog prices have at most 12 digits, so doubles order them
    // exactly); the displayed text is always the API's decimal string.
    numeric: { value: (p) => Number(p.price), mark: 'lowest' },
  },
  { label: 'Brand', text: (p) => p.brand },
  { label: 'Category', text: (p) => p.category },
  {
    label: 'Rating',
    text: (p) => (p.rating === null ? null : `${p.rating} out of 5`),
    numeric: { value: (p) => p.rating, mark: 'highest' },
  },
  {
    label: 'Reviews',
    text: (p) => (p.review_count === null ? null : String(p.review_count)),
  },
  { label: 'Availability', text: (p) => plain(p.availability) },
  {
    label: 'RAM',
    text: (p) => (p.ram_gb === null ? null : `${p.ram_gb} GB`),
    numeric: { value: (p) => p.ram_gb, mark: 'highest' },
  },
  {
    label: 'Storage',
    text: (p) => (p.storage_gb === null ? null : `${p.storage_gb} GB`),
    numeric: { value: (p) => p.storage_gb, mark: 'highest' },
  },
  { label: 'Storage type', text: (p) => p.storage_type },
  { label: 'Storage interface', text: (p) => p.storage_interface },
];

function numericNotes(products: EvidenceProduct[], spec: RowSpec): (string | null)[] {
  const numeric = spec.numeric;
  if (numeric === undefined) {
    return products.map(() => null);
  }
  // Prices in different currencies are not comparable: no note at all.
  if (spec.label === 'Price' && new Set(products.map((p) => p.currency)).size > 1) {
    return products.map(() => null);
  }
  const values = products.map((p) => numeric.value(p));
  const known = values.filter((v): v is number => v !== null && Number.isFinite(v));
  // A note needs at least two known values that differ.
  if (known.length < 2 || new Set(known).size < 2) {
    return products.map(() => null);
  }
  const target = numeric.mark === 'lowest' ? Math.min(...known) : Math.max(...known);
  return values.map((v) => (v === target ? numeric.mark : null));
}

export function buildComparison(products: EvidenceProduct[]): ComparisonRow[] {
  return ROWS.map((spec) => {
    const notes = numericNotes(products, spec);
    return {
      label: spec.label,
      cells: products.map((p, i) => ({ text: spec.text(p) ?? NOT_GIVEN, note: notes[i] ?? null })),
    };
  });
}

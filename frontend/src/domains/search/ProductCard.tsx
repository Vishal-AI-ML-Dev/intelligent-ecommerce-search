import type { SearchResult } from './types';

// Catalog text is seller-supplied: it is rendered only as React text children and attributes,
// never as HTML.

function availabilityLabel(code: string): string {
  return code.replaceAll('_', ' ');
}

function ratingLabel(result: SearchResult): string {
  if (result.rating === null) {
    return 'No rating yet';
  }
  const reviews = result.review_count === null ? '' : ` (${result.review_count} reviews)`;
  return `Rated ${result.rating} out of 5${reviews}`;
}

interface Props {
  result: SearchResult;
  rerankerApplied: boolean;
}

export function ProductCard({ result, rerankerApplied }: Props) {
  return (
    <li className="product-card" data-testid="product-card" data-product-id={result.product_id}>
      <article aria-labelledby={`product-${result.rank}-title`}>
        <p className="product-rank">#{result.rank}</p>
        <h3 id={`product-${result.rank}-title`} className="product-title">
          {result.title}
        </h3>
        <p className="product-meta">
          <span>{result.brand}</span> · <span>{result.category}</span>
        </p>
        <p className="product-price" data-testid="product-price">
          {result.currency} {result.price}
        </p>
        <p className="product-rating">{ratingLabel(result)}</p>
        <p className="product-availability">{availabilityLabel(result.availability)}</p>
        {rerankerApplied && (
          <p className="product-rerank" data-testid="product-rerank">
            {result.rerank_rank === null
              ? 'Not reranked (beyond rerank depth)'
              : `Reranked position ${result.rerank_rank}`}
          </p>
        )}
      </article>
    </li>
  );
}

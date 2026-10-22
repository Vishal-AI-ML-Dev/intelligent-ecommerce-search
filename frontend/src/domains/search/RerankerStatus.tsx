import type { RerankerBlock } from './types';

function message(reranker: RerankerBlock): string {
  switch (reranker.status) {
    case 'applied':
      return `Reranked: the top ${reranker.reranked_count} candidates were rescored by the local reranker.`;
    case 'disabled':
      return 'Reranking is turned off: results are in hybrid retrieval order.';
    case 'skipped_no_candidates':
      return 'Reranking skipped: there were no candidates to rerank.';
    case 'skipped_no_searchable_text':
      return 'Reranking skipped: the query has no letters or digits to search for.';
  }
}

export function RerankerStatus({ reranker }: { reranker: RerankerBlock }) {
  return (
    <p className="reranker-status" data-testid="reranker-status" data-status={reranker.status}>
      {message(reranker)}
    </p>
  );
}

"""Cross-encoder reranking (Milestone 8, V3): the pure contract.

`spec` (model specification), `provider` (typed provider Protocol, sanitized errors and output
validation), `documents` (the `rd-1` reranker document) and `ranking` (deterministic head-only
reranking) perform no I/O and import no database, web, settings or model library. The concrete
model provider is a separate module that imports its model library lazily.
"""

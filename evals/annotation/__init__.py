"""Local, blind annotation of the frozen candidate pool (M9 Session C2).

* `session`: loads only a verified `pool_frozen` stage (public files; never the private
  provenance), checks every product's catalog content and card hashes, and keeps the primary
  round's working file (canonical JSONL, atomically replaced after every judgement).
* `cli`: the interactive command line (`python -m evals.annotation`).

Only the human annotator `ann-1` labels. Nothing is prefilled, suggested or ranked, and no model,
LLM or remote provider is called.
"""

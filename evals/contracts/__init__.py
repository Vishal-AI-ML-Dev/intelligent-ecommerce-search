"""Golden Dataset contracts (Milestone 9): typed records, validators, canonical serialization
and the manifest lifecycle. Pure and offline: no database, model, network or search endpoint.

Modules:

* `constants`: approved versions, pool caps, label vocabulary and the review-workflow constants.
* `canonical`: canonical JSON/JSONL bytes, strict decoding and SHA-256 helpers.
* `models`: frozen Pydantic records, one per concept (query, gold constraint, pool item, ...).
* `catalog`: a read-only index of the committed seed catalog for cross-checks.
* `review_sets`: the deterministic consistency sample, adjudication queue and final labels.
* `validation`: cross-record validators.
* `manifest`: the staged, hash-chained manifest and its lifecycle rules.
"""

# M14 human review rubric (`lh-rubric-1`)

This rubric is for reviewers of the blinded listing-decision packet (`lh-1`, tooling in
`evals/listing_decisions/review.py`). The reviewer must be a person. A model, a script, the
external source labels or the Jev output must never fill in an answer.

## What you see

Each packet item has an opaque id (`h-001`, ...). It shows only what Jev was shown:
- the seller's title and description, exactly as received (either may be missing);
- the seller's normalized claims being checked (`field` and `claimed_value`).

You are never shown the source dataset, the dataset's own label, Jev's answer or the route.
Don't look them up while reviewing.

## What you answer (one JSON line per packet item)

```json
{"packet_id": "h-001", "annotator_handle": "ann-1", "rubric_version": "lh-rubric-1",
 "category": "laptop", "subcategory": "unsure", "claims": {"3": "supported"},
 "needs_review": "no", "note": null}
```

**`category`** is one of the product categories or `none`:
- give the category the title and description establish;
- use `none` when the text shows the product belongs to no listed category (for example, an
  accessory);
- use `unsure` when the text does not let you decide.

**`subcategory`** is one of the subcategories or `none`. It is chosen on the same basis as
`category`, and `unsure` is allowed.

**`claims`** has one entry per claim shown, keyed by its ordinal:
- `supported`: the title or description states this value;
- `contradicted`: the text states a different, incompatible value;
- `not_stated`: the text does not state it or does not settle it;
- `unsure`.

A value the text simply does not mention is `not_stated`, never `contradicted`.

**`needs_review`:**
- `yes`: from the text and claims alone, a human should check this listing before
  acceptance;
- `no`: the listing looks consistent;
- `unsure`.

**`note`:** optional free text, at most 1000 characters. Don't paste listing text into the
note.

## Rules

- Judge only from the text shown. Don't use outside knowledge of a specific product to "fix"
  the listing. General knowledge of what a product type is, is fine.
- Ties are not allowed: pick one label or answer `unsure`. `unsure` is never counted as
  agreement or disagreement; it is reported separately.
- One answers file per reviewer and one `annotator_handle` (`ann-N`) per reviewer. The handle
  maps to a person outside the repository. Answer every packet item exactly once.
- Don't edit an answers file after it has been imported; import seals it. Corrections go into
  a new review id.

## Import (by the operator)

```bash
uv run python -m evals.listing_decisions.review import --set-dir <set dir> --review-id <id> \
  --answers <file> --reviewer-is-human
```

Pass `--reviewer-is-human` only if a person typed the answers. The seal records this
attestation; the tool cannot verify it. Fixture answers in tests are never reviews.

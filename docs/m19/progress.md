# M19 Return Reason Intelligence: progress and handoff

**Status (2026-11-02):** Part 1, the deterministic complaint sorter, is implemented. It was
corrected after the owner's semantic review (see "Review corrections"). M19 is **not
accepted**. Parts 2–4 have not started.

- **Baseline:** M18 closed with qualified acceptance at
  `e2903cc959503f2d146e1459792c4cb8db936d8f`. Its exceptions and limitations
  (`docs/m18/handoff.md`) are unchanged. M18 data, contracts, tables and buyer behaviour were not
  touched.
- **Owner-approved Part 1 scope:** typed, versioned contracts; a deterministic baseline; evidence
  references; suggested responsible areas; fixed review reasons; an offline CLI, tests and
  examples.
- **Not in Part 1:** GenAI or provider calls, clustering, DB access or migrations, API or UI
  changes, cloud or staging work.

## Part 1 deliverables

Package `src/ecommerce_search/returns/classification/`. It needs no database, network, model or
new dependency.

| Module | Content |
|---|---|
| `taxonomy.py` | `return-categories-1`: the twelve §19 categories (none merged) and the suggested areas from the §19 table |
| `contracts.py` | `return-classification-1`: the input (reason code, customer statements and inspection observations, kept apart) and the result (predictions, evidence refs, review reasons); `input_from_records` builds an input from M18 records |
| `text.py` | matching normalization on a copy (NFKC, casefold, apostrophes, runs of repeated letters) with original offsets; the bounded misspelling match |
| `rules.py` | `return-baseline-rules-1`: maps for reason codes, complaint topics and inspection conditions; English, Hinglish and Devanagari phrases; negation; conflict pairs |
| `baseline.py` | `classify()`: signals, then predictions, inspection support or contradiction, tiers and review reasons |
| `cli.py`, `__main__.py` | offline `classify` and `taxonomy` commands |

### Rules in brief
- **Reason codes:** each code maps to its §19 category. `NOT_AS_DESCRIBED` maps to
  {expectation mismatch, incorrect specification}; `DEFECTIVE_OR_NOT_WORKING` maps to
  {product-quality defect, manufacturing defect}. `OTHER` and `NOT_PROVIDED` map to nothing, so
  informative text still classifies them.
- **Complaint topics:** only `damaged` and `wrong_item` map to a category.
- **Inspections never create a prediction.** They support it (`damaged`,
  `functional_fault_confirmed`, `item_differs_from_order`) or contradict it (`as_new` or
  `used_good` against damage; `no_fault_found` against a defect). Otherwise they are neutral.
- **Keywords:** fixed phrases only. Lone misleading words ("small", "colour", "wrong", "late") are
  not rules. Matching is greedy, longest phrase first, with no reuse of tokens and no matching
  across punctuation.
- **Misspellings:** distance ≤ 1, same first letter, and only for 19 allowlisted English tokens
  of at least 6 letters. A fuzzy match needs bounded context: either an exactly matched token in
  the same phrase ("delivry late"), or a product-context word (`FUZZY_CONTEXT_WORDS`, such as
  "screen", "parcel", "box", "arrived", "samaan") up to 3 tokens away in the same clause.
  Without context it is suppressed as `fuzzy_without_context`, so "my broker said…" abstains.
  A prediction that rests only on fuzzy matches is `low`.
- **Negation:** English or Hinglish negators up to 3 tokens before a phrase, or Hinglish ones up
  to 2 tokens after it. The search stops at punctuation, a danda or "but"/"lekin"/"magar". A
  negated match is reported in `suppressed_matches` and never becomes a prediction.
- **Resolution:** every prediction is one supported concern.
  - `resolution: single_category`: `category` names it.
  - `resolution: unresolved_candidates`: `category` is `null`. The evidence supports the
    concern the candidates share (for example "a defect") but none of them. This is an
    abstention between alternatives, not several predictions, and no candidate is asserted.
- **Tiers (not probabilities):**
  - `low`: anything contradicts the prediction, or every supporting match is fuzzy;
  - `high`: at least two kinds of source agree (reason code, complaint topic, customer text,
    inspection);
  - `medium`: otherwise.

  The tier rates support for the concern as a whole. A `high` unresolved defect means strong
  support for "some defect", not for either defect category.
- **Review reasons (fixed):** `insufficient_evidence`, `low_confidence`,
  `unsupported_distinction`, `conflicting_statements`, `statement_inspection_conflict`,
  `conflicting_inspections`, `uncorroborated_item_difference`. Routing hides or rejects nothing.

### Interpretations for owner review
These are Part 1 choices, not approved contract decisions:
1. **No merge.** A defect is always the unresolved pair {product-quality, manufacturing}, routed
   as `unsupported_distinction`. No text rule picks one of the two, not even the customer's
   words "manufacturing defect".
2. **Ambiguous signals.** A signal with several candidates ("wrong colour", "wrong size",
   `NOT_AS_DESCRIBED`) supports any of its candidates that another signal names on its own.
   Otherwise it stays unresolved. Inspections never resolve an unresolved set.
3. **Categories the baseline never predicts.** Suspected abuse is never predicted.
   `item_differs_from_order` without a wrong-item statement is routed as
   `uncorroborated_item_difference`, with no category. Insufficient evidence is an abstention
   with a review reason, not a predicted category.
4. **Genuine conflicts are limited to:**
   - opposite size directions;
   - a negated mention in one record against the same category from another record (for
     example code `NO_LONGER_NEEDED` with "not a change of mind");
   - an inspection contradiction, or a pair of disagreeing inspections.

   Coexisting reasons are compatible. That includes change of mind with damage, a defect or a
   wrong item. Opposite size directions keep the sizing tier, because the category is agreed
   and only the direction conflicts.
5. **Suggested areas:** each §19 cell is split on "or" and "/" (so "quality / supplier" gives
   two areas).
6. **Inputs:** which support complaints belong to a return is the caller's choice. Inspection
   notes and customer reviews are not used.

### Review corrections (2026-11-02)
- **Already correct, now explicit:**
  - A defect was already one unresolved prediction with `category: null`, not two confirmed
    predictions.
  - No rule names a manufacturing defect on its own: "manufacturing defect" matches only the
    generic `text:defect:defect` rule.
  - Added: a `resolution` field (validated against `candidates` and `category`) and a
    documented meaning of the tier for unresolved concerns.
- **Changed:** change of mind together with damage, a defect or a wrong item is no longer a
  conflict. The `CONFLICTING_STATEMENT_GROUPS` rule was removed. Regression tests now cover
  compatible coexistence and genuine contradictions.
- **Changed:** fuzzy matches need bounded context (see Misspellings). Suppressed fuzzy matches
  never contradict a prediction; only negated mentions do. Positive and negative regression
  tests were added.

## Usage

```text
uv run python -m ecommerce_search.returns.classification taxonomy
uv run python -m ecommerce_search.returns.classification classify docs/m19/examples/baseline-demo-input.json
```

The demo file holds five hand-written synthetic inputs. Summarized output (2026-11-02 run, exit
0; identical categories, tiers and reasons to the 2026-10-28 run, plus `resolution`):

| Input | Result |
|---|---|
| `OTHER`, "Shoe size chhota hai, fit nahi hua" | sizing_fit, medium, areas listing/product; cites "size chhota" and "fit nahi"; no review |
| `ARRIVED_DAMAGED`, "It is not damaged, just too tight" | sizing_fit, medium; damaged_product, low, contradicted by the negated "damaged"; review: low_confidence, conflicting_statements |
| `DEFECTIVE_OR_NOT_WORKING`, "phone kaam nahi karta", inspection `functional_fault_confirmed` | `unresolved_candidates` {product_quality_defect, manufacturing_defect}, category null, high (support for "a defect"), areas quality/supplier; review: unsupported_distinction |
| `NOT_PROVIDED`, "screen came damagd, and the delivery was late" | damaged_product, low (fuzzy "damagd"); logistics_delay, medium; review: low_confidence |
| `NOT_PROVIDED`, "Please process my return" | abstained; review: insufficient_evidence |

Invalid input exits 2 with a fixed message (`the input is not valid (n error(s))`), without echoing paths or values.

## Tests and checks
- New unit tests: `tests/unit/returns/test_returns_classification_rules.py`,
  `test_returns_classification_baseline.py` and `test_returns_classification_cli.py`.
- The tests use hand-written synthetic inputs. They check rule behaviour and are **not** a
  human-reviewed evaluation set. They make no accuracy claim.
- Neither the M18 demo dataset nor `scenarios.json` was read, used or tuned on. A static test
  checks that the package doesn't reference them.
- **2026-10-28:**
  - 92 new tests passed; `tests/unit/returns` 1025 passed (run before the final +1 test);
  - `ruff check .` and `ruff format --check .` clean;
  - `pytest tests --collect-only` collected 7327 with no collection errors; no duplicate test
    basenames;
  - AC-14 protected-path diff vs `bf67d11` exit 0.
- **2026-11-02 (review corrections):** the three classification test files, 111 passed; Ruff
  clean. The collection and wider returns results above were reused, since no other file
  changed.
- The full suite and the Windows isolation wrapper were not rerun (by direction).

## Limitations
- No measured quality: there is no human-reviewed return-reason set yet (Part 3).
- The lexicon is small and hand-written. Recall on real customer text is unknown, and
  romanized Hinglish spellings beyond the listed variants are missed.
- Negation and clause handling are bounded heuristics. Sarcasm, questions and long-distance
  negation are not handled.
- The fuzzy-context rule is a bounded heuristic. A look-alike word next to a product word
  still matches ("the box broker" would count as "broken"). A real misspelling with no product
  word nearby is missed ("it was brokn" abstains unless another source supports it).
- Coexistence is treated as compatible. The baseline doesn't judge whether two customer
  reasons are plausible together; that is left to human review and later parts.
- The baseline treats every customer statement as true for matching. Conflicts are routed for
  review, not resolved.
- Nothing is stored. Predictions, clustering, APIs and UI are Parts 2–4.

## Next (not authorized)
Part 2: separate inferred storage and complaint clustering. It needs owner approval.

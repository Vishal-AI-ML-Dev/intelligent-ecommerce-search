"""`return-reason-split-1`: a deterministic, seeded split of one dataset version's returns.

* **Strata:** the customer's reason code, a customer statement. No baseline output, scenario
  intent or product is used.
* **Sizes:** `DEV_SIZE` dev and `TEST_SIZE` held-out test returns; the rest stay unlabelled.
  The labelled total is shared across strata by largest remainder (ties by code), so each
  stratum gets about the same labelled share. Within a stratum the shuffled returns go to dev,
  then test, then unlabelled. Strata with an odd labelled count give their extra return to dev
  for the first half of them (by code) and to test for the rest, which keeps dev and test
  equal in size.
* **Order:** within a stratum, `random.Random("<version>:<seed>:<code>")` shuffles the
  `return_id`-sorted list. The kit's presentation order is a separate seeded shuffle of each
  set, so neighbouring IDs (generation order) are not shown together.

The test set is labelled with the dev set but is only scored in the final comparison
(`scoring.py` refuses it unless `final=True`).
"""

import random
from collections import defaultdict
from typing import Any

from evals.returns.source import Bundle

SPLIT_VERSION = "return-reason-split-1"
SPLIT_SEED = 20261102
DEV_SIZE = 60
TEST_SIZE = 60


def _largest_remainder(sizes: dict[str, int], total: int) -> dict[str, int]:
    population = sum(sizes.values())
    ideal = {k: total * n / population for k, n in sizes.items()}
    quota = {k: int(v) for k, v in ideal.items()}
    order = sorted(sizes, key=lambda k: (-(ideal[k] - quota[k]), k))
    for k in order[: total - sum(quota.values())]:
        quota[k] += 1
    return quota


def make_split(bundle: Bundle, seed: int = SPLIT_SEED) -> dict[str, Any]:
    strata: dict[str, list[str]] = defaultdict(list)
    for item in bundle.items:
        strata[item.reason_code].append(item.return_id)
    if len(bundle.items) < DEV_SIZE + TEST_SIZE:
        raise ValueError("the dataset is smaller than the dev and test sets")
    labelled = _largest_remainder({k: len(v) for k, v in strata.items()}, DEV_SIZE + TEST_SIZE)
    odd = sorted(k for k, n in labelled.items() if n % 2)
    extra_to_dev = set(odd[: len(odd) // 2])
    sets: dict[str, list[str]] = {"dev": [], "test": [], "unlabelled": []}
    per_stratum = {}
    for code in sorted(strata):
        ids = sorted(strata[code])
        random.Random(f"{SPLIT_VERSION}:{seed}:{code}").shuffle(ids)  # noqa: S311 - seeded split
        dev = labelled[code] // 2 + (1 if code in extra_to_dev else 0)
        test = labelled[code] - dev
        sets["dev"] += ids[:dev]
        sets["test"] += ids[dev : dev + test]
        sets["unlabelled"] += ids[dev + test :]
        per_stratum[code] = {"dev": dev, "test": test, "unlabelled": len(ids) - dev - test}
    presentation = {}
    for name in ("dev", "test"):
        order = sorted(sets[name])
        random.Random(f"{SPLIT_VERSION}:{seed}:present:{name}").shuffle(order)  # noqa: S311
        presentation[name] = order
    return {
        "split_version": SPLIT_VERSION,
        "seed": seed,
        "strata": "customer_reason_code",
        "bundle_sha256": bundle.sha256,
        "dataset_id": bundle.dataset_id,
        "dataset_version": bundle.dataset_version,
        "sizes": {name: len(ids) for name, ids in sets.items()},
        "per_stratum": per_stratum,
        "sets": {name: sorted(ids) for name, ids in sets.items()},
        "presentation_order": presentation,
        "note": "test is labelled but scored only in the final comparison",
    }


def check_split(split: dict[str, Any], bundle: Bundle) -> None:
    """The recorded split must be exactly what this bundle and seed produce."""
    if split.get("split_version") != SPLIT_VERSION:
        raise ValueError("unknown split version")
    if split != make_split(bundle, split["seed"]):
        raise ValueError("the split does not match the bundle and seed")

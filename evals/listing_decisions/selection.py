"""Duplicate grouping `lg-1`, capped selection and the dev/test split for the M14 set (offline).

**Groups.** Records of one source that share *any* duplicate identity are in one group, joined
transitively (union-find): Flipkart listing id (`itm`) or normalized title; Smartphones and Laptop
Price normalized model name or model family. A group never crosses splits.

*Model-family limitation (frozen):* a family is the first two tokens of the model name after
dropping parenthesized text and network/capacity tokens (`adapters.family`). It over-groups
(every "Samsung Galaxy" phone is one group), which is conservative for leakage but makes the split
lumpy. It can under-group when one model is named differently, for example "Apple iPhone 14"
and "iPhone 14"; such pairs may land in different splits.

**Flipkart variants.** Rows of one `itm` listing are size or colour variants of one listing:
the lowest `pid` is kept and the others are excluded as `duplicate_listing_variant`.

**Selection.** Within each stratum, records are ordered by `sha256(seed:source:row)` and the first
up to the cap are taken. Caps are upper bounds: a short stratum is reported, never topped up,
duplicated or relaxed.

**Split.** Each group goes to dev when `int(sha256("le-split-1:seed:group")[:8], 16)` is even,
otherwise to test. Hash splitting is approximate; the actual counts are reported.
"""

import hashlib
from collections import defaultdict
from collections.abc import Sequence

from evals.listing_decisions.adapters import Adapted, Excluded

GROUPING_VERSION = "lg-1"
SPLIT_VERSION = "le-split-1"
CAPS: dict[str, int | None] = {  # None: take every eligible record
    "shoes:casual": 40,
    "shoes:formal": 40,
    "shoes:sports": 20,
    "shoes:other": 20,
    "headphones:in-ear": None,
    "headphones:over-ear": None,
    "headphones:unlabelled": None,
    "phone": 120,
    "laptop": 120,
    "none:phone_accessory": 40,
    "none:laptop_accessory": 40,
    "none:headphones_accessory": None,
    "none:shoes_accessory": None,
}


def digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def dedupe_variants(rows: Sequence[Adapted]) -> tuple[list[Adapted], list[Excluded]]:
    """Keep the lowest `pid` of each Flipkart `itm` listing."""
    by_itm: dict[str, list[Adapted]] = defaultdict(list)
    kept: list[Adapted] = []
    for row in rows:
        itm = next((i for i in row.identities if i.startswith("fk-itm:")), None)
        if itm is None:
            kept.append(row)
        else:
            by_itm[itm].append(row)
    excluded = []
    for group in by_itm.values():
        group = sorted(group, key=lambda r: r.source_row_id)
        kept.append(group[0])
        excluded += [
            Excluded(r.source_id, r.source_row_id, "duplicate_listing_variant") for r in group[1:]
        ]
    return sorted(kept, key=lambda r: (r.source_id, r.source_row_id)), excluded


def group_keys(rows: Sequence[Adapted]) -> dict[tuple[str, str], str]:
    """(source_id, source_row_id) -> group key. Records sharing any identity share a group."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for row in rows:
        node = f"row:{row.source_id}:{row.source_row_id}"
        for identity in row.identities:
            union(node, f"id:{row.source_id}:{identity}")
    members: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        members[find(f"row:{row.source_id}:{row.source_row_id}")].append(row.source_row_id)
    names = {root: min(ids) for root, ids in members.items()}
    return {
        (
            r.source_id,
            r.source_row_id,
        ): f"{r.source_id}:{names[find(f'row:{r.source_id}:{r.source_row_id}')]}"
        for r in rows
    }


def select(rows: Sequence[Adapted], seed: str) -> tuple[list[Adapted], dict[str, dict]]:
    """Capped, seeded selection per stratum, and the per-stratum counts."""
    by_stratum: dict[str, list[Adapted]] = defaultdict(list)
    for row in rows:
        if row.stratum not in CAPS:
            raise ValueError(f"unknown stratum {row.stratum!r}")
        by_stratum[row.stratum].append(row)
    chosen: list[Adapted] = []
    counts = {}
    for stratum, cap in sorted(CAPS.items()):
        pool = sorted(
            by_stratum.get(stratum, []),
            key=lambda r: digest(seed, r.source_id, r.source_row_id),
        )
        taken = pool if cap is None else pool[:cap]
        chosen += taken
        counts[stratum] = {
            "eligible": len(pool),
            "cap": cap,
            "selected": len(taken),
            "shortfall": 0 if cap is None else max(0, cap - len(pool)),
        }
    return chosen, counts


def split_of(group_key: str, seed: str) -> str:
    return "dev" if int(digest(SPLIT_VERSION, seed, group_key)[:8], 16) % 2 == 0 else "test"

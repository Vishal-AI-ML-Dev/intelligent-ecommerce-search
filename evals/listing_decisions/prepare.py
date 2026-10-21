"""Build the frozen M14 `le-1` set from the pinned local source files (offline; M14-6).

`prepare(inputs, out_dir)`:

1. refuses an existing `out_dir`, and any input whose size or SHA-256 differs from its pinned
   `sources` entry, before reading a row;
2. adapts every row (`adapters`, `la-1`/`lm-1`), keeps one row per Flipkart listing, groups
   duplicates (`selection`, `lg-1`), selects up to each stratum's cap, splits by group, and
   generates `corrupt-2` twins (`corrupt`);
3. checks every raw listing parses and normalizes, every clean seller category/subcategory equals
   its label (`evaluate.check_seller_fields`), and every corrupted pair passes
   `corruption.verify_transformation`;
4. writes `set.json`, `raw/<sha256>.json` and `manifest.json` (counts, exclusions, shortfalls,
   split counts, `set_sha256`). Row-level output stays in the git-ignored data directory.

No network, database, SDK or provider is used, and nothing is evaluated here.
"""

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ecommerce_search.normalization.intake import ListingIntakeError, parse_raw_listing
from ecommerce_search.normalization.normalizer import normalize_listing
from evals.listing_decisions import adapters, corrupt, selection
from evals.listing_decisions.contracts import (
    EvalItem,
    EvalSet,
    EvalSetError,
    SourceManifest,
    Task,
    dump_set,
)
from evals.listing_decisions.corruption import verify_transformation
from evals.listing_decisions.evaluate import check_seller_fields
from evals.listing_decisions.matching import MATCHING_VERSION, mentions
from evals.listing_decisions.sources import ADAPTER_VERSION, MAPPING_VERSION, SOURCES, csv_file

DEFAULT_SEED = "le-set-1"
SET_VERSION = "le-set-2"  # le-set-1 (corrupt-1) is superseded; the seed is unchanged


def raw_bytes(fields: Mapping[str, object]) -> bytes:
    return json.dumps(fields, ensure_ascii=False).encode("utf-8")


def _rows(data: bytes) -> list[dict[str, str]]:
    csv.field_size_limit(10**8)
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"), newline="")))


def _verified(source: SourceManifest, path: Path) -> bytes:
    expected = csv_file(source)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise EvalSetError(f"{source.source_id}: input unreadable ({type(exc).__name__})") from exc
    if len(data) != expected.size_bytes or hashlib.sha256(data).hexdigest() != expected.sha256:
        raise EvalSetError(f"{source.source_id}: input does not match its pinned SHA-256")
    return data


def _item_id(source_id: str, row_id: str) -> str:
    prefix = {"flipkart": "fk", "smartphones": "sp", "laptop_price": "lp"}[source_id]
    return f"{prefix}:{row_id.casefold()}"


def _adapt(
    source_id: str, rows: list[dict[str, str]]
) -> list[adapters.Adapted | adapters.Excluded]:
    if source_id == "flipkart":
        return [adapters.adapt_flipkart(r) for r in rows]
    if source_id == "smartphones":
        return [adapters.adapt_smartphone(r, n) for n, r in enumerate(rows, start=1)]
    return [adapters.adapt_laptop(r) for r in rows]


def _category(row: adapters.Adapted) -> str:
    return next(lbl["value"] for lbl in row.labels if lbl["task"] == "category")


def prepare(
    inputs: Mapping[str, Path],
    out_dir: Path,
    *,
    seed: str = DEFAULT_SEED,
    sources: Sequence[SourceManifest] = SOURCES,
    set_version: str = SET_VERSION,
    synthetic_fixture: bool = False,
) -> dict[str, Any]:
    """Build and write the set; return the manifest (also written as `manifest.json`)."""
    if out_dir.exists():
        raise EvalSetError("the output directory already exists")
    if set(inputs) != {s.source_id for s in sources}:
        raise EvalSetError("exactly one input file per pinned source is required")
    data = {s.source_id: _verified(s, inputs[s.source_id]) for s in sources}

    exclusions: dict[str, Counter[str]] = defaultdict(Counter)
    totals: dict[str, int] = {}
    omitted: dict[str, Counter[str]] = defaultdict(Counter)
    adapted: list[adapters.Adapted] = []
    for source in sources:
        rows = _rows(data[source.source_id])
        totals[source.source_id] = len(rows)
        kept = []
        for result in _adapt(source.source_id, rows):
            if isinstance(result, adapters.Excluded):
                exclusions[source.source_id][result.reason] += 1
            else:
                kept.append(result)
        if source.source_id == "flipkart":
            kept, variants = selection.dedupe_variants(kept)
            exclusions["flipkart"]["duplicate_listing_variant"] += len(variants)
        for row in kept:
            try:
                parse_raw_listing(raw_bytes(row.fields))
            except ListingIntakeError as exc:
                exclusions[row.source_id][f"raw_listing_rejected:{exc.reason.value}"] += 1
                continue
            adapted.append(row)
    groups = selection.group_keys(adapted)
    chosen, strata = selection.select(adapted, seed)
    for row in chosen:
        omitted[row.source_id].update(row.omitted)

    clean: list[corrupt.Clean] = []
    for row in chosen:
        raw = parse_raw_listing(raw_bytes(row.fields))
        group = groups[(row.source_id, row.source_row_id)]
        clean.append(
            corrupt.Clean(
                item_id=_item_id(row.source_id, row.source_row_id),
                source_id=row.source_id,
                source_row_id=row.source_row_id,
                group_key=group,
                split=selection.split_of(group, seed),
                category=_category(row),
                fields=row.fields,
                normalization=normalize_listing(raw),
            )
        )
    corrupted, corruption_counts = corrupt.generate(clean, seed)

    labels = {(r.source_id, r.source_row_id): r.labels for r in chosen}
    items = [
        {
            "item_id": c.item_id,
            "source_id": c.source_id,
            "source_row_id": c.source_row_id,
            "group_key": c.group_key,
            "split": c.split,
            "raw_sha256": hashlib.sha256(raw_bytes(c.fields)).hexdigest(),
            "labels": list(labels[(c.source_id, c.source_row_id)]),
        }
        for c in clean
    ]
    items += [
        {
            "item_id": x.item_id,
            "source_id": x.twin.source_id,
            "source_row_id": f"{x.twin.source_row_id}#{corrupt.CORRUPTION_VERSION}:{x.rule}",
            "group_key": x.twin.group_key,
            "split": x.twin.split,
            "raw_sha256": hashlib.sha256(raw_bytes(x.fields)).hexdigest(),
            "labels": [],
            "twin_of": x.twin.item_id,
            "corruption": {
                "rule": x.rule,
                "seed": x.seed,
                "target_key": x.target_key,
                "original_value": x.original_value,
            },
        }
        for x in corrupted
    ]
    set_bytes = dump_set(
        {
            "set_version": set_version,
            "group_key_version": selection.GROUPING_VERSION,
            "synthetic_fixture": synthetic_fixture,
            "sources": [s.model_dump(mode="json") for s in sources],
            "items": sorted(items, key=lambda i: i["item_id"]),
        }
    )

    # Self-checks before anything is written.
    raws = {c.item_id: raw_bytes(c.fields) for c in clean} | {
        x.item_id: raw_bytes(x.fields) for x in corrupted
    }
    eval_set = EvalSet.model_validate_json(set_bytes)
    by_id = {c.item_id: c for c in clean}
    for item in eval_set.items:
        if item.corruption is None:
            check_seller_fields(item, by_id[item.item_id].normalization)
        else:
            _check_pair(item, raws)
    out_dir.mkdir(parents=True)
    (out_dir / "set.json").write_bytes(set_bytes)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir()
    for blob in raws.values():
        (raw_dir / f"{hashlib.sha256(blob).hexdigest()}.json").write_bytes(blob)

    manifest = _manifest(
        eval_set,
        clean,
        corrupted,
        totals=totals,
        exclusions=exclusions,
        strata=strata,
        corruption_counts=corruption_counts,
        omitted=omitted,
        seed=seed,
    )
    (out_dir / "manifest.json").write_bytes(
        (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    )
    return manifest


def _check_pair(item: EvalItem, raws: Mapping[str, bytes]) -> None:
    if item.twin_of is None:
        raise EvalSetError(f"{item.item_id}: a corrupted item names its twin")
    verify_transformation(
        item, parse_raw_listing(raws[item.twin_of]), parse_raw_listing(raws[item.item_id])
    )


def _label_word_disclosure(clean: Sequence[corrupt.Clean], set_items: Sequence[EvalItem]) -> dict:
    """Clean items whose own title, or title or description, mentions their label value under
    `matching.mentions` (`lt-1`, the rule the evaluator's title disclosure also uses). A label
    word in the seller's own text is evidence, not leakage; it is disclosed."""
    by_id = {c.item_id: c for c in clean}
    title: Counter[str] = Counter()
    either: Counter[str] = Counter()
    for item in set_items:
        if item.corruption is not None:
            continue
        fields = by_id[item.item_id].fields
        for label in item.labels:
            if label.value == "none":
                continue
            key = f"{label.task.value}:{label.value}"
            title[key] += mentions(str(fields.get("title") or ""), label.value)
            either[key] += mentions(corrupt.text_of(fields), label.value)
    return {
        "matching": MATCHING_VERSION,
        "title": dict(sorted((k, v) for k, v in title.items() if v)),
        "title_or_description": dict(sorted((k, v) for k, v in either.items() if v)),
    }


def _manifest(
    eval_set: EvalSet,
    clean: Sequence[corrupt.Clean],
    corrupted: Sequence[corrupt.Corrupt],
    *,
    totals: Mapping[str, int],
    exclusions: Mapping[str, Counter[str]],
    strata: Mapping[str, dict],
    corruption_counts: Mapping[str, dict],
    omitted: Mapping[str, Counter[str]],
    seed: str,
) -> dict[str, Any]:
    split_clean: Counter[str] = Counter()
    split_corrupt: Counter[str] = Counter()
    subcategories: Counter[str] = Counter()
    for item in eval_set.items:
        if item.corruption is None:
            label = item.label(Task.CATEGORY)
            category = label.value if label is not None else "unlabelled"
            split_clean[f"{item.split.value}:{category}"] += 1
            sub = item.label(Task.SUBCATEGORY)
            if sub is not None and sub.scoring is not None:
                subcategories[f"{sub.value}:{sub.scoring.value}:{item.split.value}"] += 1
        else:
            split_corrupt[f"{item.split.value}:{item.corruption.rule.value}"] += 1
    return {
        "schema": "le-prepare-manifest-1",
        "note": "counts and digests only; row-level data stays in this git-ignored directory",
        "set_version": eval_set.set_version,
        "set_sha256": eval_set.set_sha256,
        "versions": {
            "adapter": ADAPTER_VERSION,
            "label_mapping": MAPPING_VERSION,
            "grouping": selection.GROUPING_VERSION,
            "split": selection.SPLIT_VERSION,
            "corruption": corrupt.CORRUPTION_VERSION,
            "matching": MATCHING_VERSION,
            "seed": seed,
        },
        "sources": {
            s.source_id: {
                "rows": totals[s.source_id],
                "excluded_by_reason": dict(sorted(exclusions.get(s.source_id, {}).items())),
                "omitted_fields_in_selected": dict(sorted(omitted.get(s.source_id, {}).items())),
            }
            for s in eval_set.sources
        },
        "strata": dict(sorted(strata.items())),
        "clean_items": len(clean),
        "corrupted_items": len(corrupted),
        "groups": len({c.group_key for c in clean}),
        "split_counts_clean": dict(sorted(split_clean.items())),
        "split_counts_corrupted": dict(sorted(split_corrupt.items())),
        "subcategory_labels": dict(sorted(subcategories.items())),
        "corruption": corruption_counts,
        "label_word_in_own_text": _label_word_disclosure(clean, eval_set.items),
    }

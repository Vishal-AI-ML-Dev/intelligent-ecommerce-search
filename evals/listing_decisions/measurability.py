"""What the M14 external labels can and cannot measure: registry `le-measure-1`.

Fixed before any prediction is read, from the M14-0 decision summary and the evaluation amendment.
A measured cell is scored against external source labels (never human-reviewed gold). Every
unmeasured cell is reported as `null` with its reason, never as a number.
"""

MEASURABILITY_VERSION = "le-measure-1"

MEASURED_CATEGORY_LABELS = ("laptop", "phone", "shoes", "headphones", "none")
# Shoes from the Flipkart leaf or `Occasion` field; headphones from spec fields (about 50 items:
# descriptive only). `sports` is scored only where its mapping is unambiguous (see contracts).
MEASURED_SUBCATEGORY_LABELS = frozenset(
    {"casual", "formal", "sports", "in-ear", "on-ear", "over-ear"}
)

UNMEASURED: dict[str, str] = {
    "subcategory.laptop": "no external source provides laptop subcategory labels",
    "subcategory.phone": "no external source provides phone subcategory labels",
    "subcategory.running": "no external source provides a 'running' label distinct from 'sports'",
    "subcategory.coarse_sports_running": (
        "the taxonomy defines no group merging 'sports' and 'running'; no coarse metric is reported"
    ),
    "attribute_text_support": (
        "no source label says whether the listing text supports a claim; Flipkart descriptions "
        "repeat spec values, phone rows have no text, and laptop titles share a source with specs"
    ),
    "ambiguity": "no source provides ambiguity labels; tie and none counts are descriptive only",
    "route_correctness": "no expected-route labels exist and none are invented",
    "unsafe_auto_accept_rate": "lr-1 never auto-accepts and no expected-route labels exist",
    "unnecessary_review_rate": "no expected-route labels exist",
    "calibration": "no calibration is claimed; probability diagnostics are excluded from M14-4",
}


def registry() -> dict[str, object]:
    return {
        "version": MEASURABILITY_VERSION,
        "measured": {
            "category": list(MEASURED_CATEGORY_LABELS),
            "subcategory_exact": sorted(MEASURED_SUBCATEGORY_LABELS),
        },
        "unmeasured": {
            name: {"value": None, "reason": r} for name, r in sorted(UNMEASURED.items())
        },
    }

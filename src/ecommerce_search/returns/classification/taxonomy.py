"""`return-categories-1`: the MASTER_PLAN §19 return categories and their typical areas.

All twelve §19 categories are kept; none is merged. Where the M18 fields cannot separate two
categories (for example product-quality defect versus manufacturing defect), the baseline
reports both as unresolved candidates and routes the return to review instead of choosing.

Each §19 "typical responsible area" cell is split on "or" and "/" into separate suggested areas,
so "listing or product" and "quality / supplier" both give two. A suggested area is where a
person might look first. It is not a fault assignment, a root cause or a responsible team.

"Suspected abuse" and "insufficient evidence" stay in the taxonomy for human labels and later
parts, but the baseline never predicts them: it does not accuse anyone, and it reports missing
evidence as an abstention with a review reason rather than as a category.
"""

from enum import StrEnum

TAXONOMY_VERSION = "return-categories-1"


class ReturnCategory(StrEnum):
    SIZING_FIT = "sizing_fit"
    EXPECTATION_MISMATCH = "expectation_mismatch"
    COLOUR_IMAGE_MISMATCH = "colour_image_mismatch"
    INCORRECT_SPECIFICATION = "incorrect_specification"
    PRODUCT_QUALITY_DEFECT = "product_quality_defect"
    DAMAGED_PRODUCT = "damaged_product"
    WRONG_ITEM_DELIVERED = "wrong_item_delivered"
    LOGISTICS_DELAY = "logistics_delay"
    MANUFACTURING_DEFECT = "manufacturing_defect"
    CHANGE_OF_MIND = "change_of_mind"
    SUSPECTED_ABUSE = "suspected_abuse"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ResponsibleArea(StrEnum):
    LISTING = "listing"
    PRODUCT = "product"
    LISTING_PHOTOGRAPHY = "listing_photography"
    CATALOG = "catalog"
    QUALITY = "quality"
    SUPPLIER = "supplier"
    PACKAGING = "packaging"
    LOGISTICS = "logistics"
    FULFILMENT = "fulfilment"
    WAREHOUSE = "warehouse"
    CUSTOMER_CHOICE = "customer_choice"
    OPERATIONS_INVESTIGATION = "operations_investigation"
    MANUAL_INVESTIGATION = "manual_investigation"


C = ReturnCategory
A = ResponsibleArea

# The §19 table, row by row; area order follows the cell text.
SUGGESTED_AREAS: dict[ReturnCategory, tuple[ResponsibleArea, ...]] = {
    C.SIZING_FIT: (A.LISTING, A.PRODUCT),
    C.EXPECTATION_MISMATCH: (A.LISTING,),
    C.COLOUR_IMAGE_MISMATCH: (A.LISTING_PHOTOGRAPHY,),
    C.INCORRECT_SPECIFICATION: (A.LISTING, A.CATALOG),
    C.PRODUCT_QUALITY_DEFECT: (A.QUALITY, A.SUPPLIER),
    C.DAMAGED_PRODUCT: (A.PACKAGING, A.LOGISTICS),
    C.WRONG_ITEM_DELIVERED: (A.FULFILMENT, A.WAREHOUSE),
    C.LOGISTICS_DELAY: (A.LOGISTICS,),
    C.MANUFACTURING_DEFECT: (A.QUALITY, A.SUPPLIER),
    C.CHANGE_OF_MIND: (A.CUSTOMER_CHOICE,),
    C.SUSPECTED_ABUSE: (A.OPERATIONS_INVESTIGATION,),
    C.INSUFFICIENT_EVIDENCE: (A.MANUAL_INVESTIGATION,),
}

NOT_PREDICTED_BY_BASELINE = frozenset({C.SUSPECTED_ABUSE, C.INSUFFICIENT_EVIDENCE})


def suggested_areas(categories: tuple[ReturnCategory, ...]) -> tuple[ResponsibleArea, ...]:
    """The union of the categories' suggested areas, in taxonomy order, without repeats."""
    wanted = {area for category in categories for area in SUGGESTED_AREAS[category]}
    return tuple(area for area in ResponsibleArea if area in wanted)

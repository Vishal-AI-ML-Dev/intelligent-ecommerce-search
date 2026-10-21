"""SQLAlchemy models. Importing this package registers every table on `Base.metadata`."""

from ecommerce_search.models.catalog import (
    CatalogDataset,
    CatalogReview,
    HeadphoneSpec,
    LaptopSpec,
    PhoneSpec,
    Product,
    RawCatalogRecord,
    ShoeSpec,
)
from ecommerce_search.models.embeddings import ProductEmbedding
from ecommerce_search.models.listing_decisions import (
    ListingDecisionProbability,
    ListingDecisionProposal,
    ListingDecisionReason,
    ListingDecisionRun,
)
from ecommerce_search.models.listings import (
    ListingCandidate,
    ListingFinding,
    ListingNormalizationRun,
    ListingVersion,
)
from ecommerce_search.models.search import ProductSearchDocument

__all__ = [
    "CatalogDataset",
    "CatalogReview",
    "HeadphoneSpec",
    "LaptopSpec",
    "ListingCandidate",
    "ListingDecisionProbability",
    "ListingDecisionProposal",
    "ListingDecisionReason",
    "ListingDecisionRun",
    "ListingFinding",
    "ListingNormalizationRun",
    "ListingVersion",
    "PhoneSpec",
    "Product",
    "ProductEmbedding",
    "ProductSearchDocument",
    "RawCatalogRecord",
    "ShoeSpec",
]

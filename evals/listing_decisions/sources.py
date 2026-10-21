"""Pinned M14 external sources (selected in M14-0; metadata only, no row-level data).

The SHA-256s are the files downloaded in M14-0 and re-verified on 2026-10-21. `prepare` reads
only the CSV files and refuses any input whose SHA-256 differs. The archives are recorded for
provenance; neither contains documentation, so nothing here establishes a currency.

**Rights:** every licence below was declared by an uploader or crawler; upstream rights are
unverified. Local, development-only preparation does not resolve them, and it does not clear
any future submission of this data to a third-party model.
"""

from evals.listing_decisions.contracts import SourceFile, SourceManifest

ADAPTER_VERSION = "la-1"
MAPPING_VERSION = "lm-1"
_LOCAL_ONLY = (
    "Local preparation does not clear rights for future submission to a third-party model."
)

FLIPKART = SourceManifest(
    source_id="flipkart",
    dataset="kaggle:PromptCloudHQ/flipkart-products",
    dataset_version="downloaded 2026-10-21 (M14-0); Kaggle version number not recorded",
    files=(
        SourceFile(
            name="archive.zip",
            sha256="54a91fcd0b3d1923e3adb52c27e4dde557a7cd948dba066e3cb5bca542da1b9f",
            size_bytes=5_765_116,
        ),
        SourceFile(
            name="flipkart_com-ecommerce_sample.csv",
            sha256="56f8f699c9e847356666c2eab3c3ab1244340f6a98ad08e39ea2199ebe993ad1",
            size_bytes=38_114_963,
        ),
    ),
    declared_licence="CC BY-SA 4.0",
    licence_declared_by="PromptCloud (the crawler), on the Kaggle dataset page",
    rights_caveat=(
        "Content was crawled from Flipkart.com; PromptCloud's right to license it is unverified. "
        "ShareAlike and attribution apply to derived data. " + _LOCAL_ONLY
    ),
    adapter_version=ADAPTER_VERSION,
    mapping_version=MAPPING_VERSION,
)
SMARTPHONES = SourceManifest(
    source_id="smartphones",
    dataset="kaggle:harshwardhanpj/smartphones",
    dataset_version="downloaded 2026-10-21 (M14-0); Kaggle version number not recorded",
    files=(
        SourceFile(
            name="archive.zip",
            sha256="b2883b4a5a8a9518a471ea7cfaaafe9d7ae69498f80b21ef785cf2713100cf66",
            size_bytes=37_356,
        ),
        SourceFile(
            name="smartphones - smartphones.csv",
            sha256="230982b061632897775a5e743ceaf3d64d872707766e7df70da542ec922d4701",
            size_bytes=353_564,
        ),
    ),
    declared_licence="MIT",
    licence_declared_by="the Kaggle uploader",
    rights_caveat=(
        "Origin undeclared; upstream rights unverified. MIT is a software licence. " + _LOCAL_ONLY
    ),
    adapter_version=ADAPTER_VERSION,
    mapping_version=None,  # category by dataset membership only
)
LAPTOP_PRICE = SourceManifest(
    source_id="laptop_price",
    dataset="kaggle:sumanbera19/laptop-price-dataset",
    dataset_version="downloaded 2026-10-21 (M14-0); Kaggle version number not recorded",
    files=(
        SourceFile(
            name="archive.zip",
            sha256="461aaeeb25f3a6e4d3dfacbe9178ca290ad7b47740efb2a414729180547ec1f6",
            size_bytes=118_662,
        ),
        SourceFile(
            name="laptop.csv",
            sha256="5f6bcd9bc3d707f25ed963e0651b508cb93aeb3c8df5746475e564af4ead105c",
            size_bytes=269_434,
        ),
    ),
    declared_licence="CC0",
    licence_declared_by="the Kaggle uploader",
    rights_caveat="Origin undeclared; upstream rights unverified. " + _LOCAL_ONLY,
    adapter_version=ADAPTER_VERSION,
    mapping_version=None,
)
SOURCES = (FLIPKART, LAPTOP_PRICE, SMARTPHONES)  # sorted by source_id


def csv_file(source: SourceManifest) -> SourceFile:
    return next(f for f in source.files if f.name.endswith(".csv"))

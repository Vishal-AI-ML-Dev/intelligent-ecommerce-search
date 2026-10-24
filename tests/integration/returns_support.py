"""Direct-SQL helpers for the M18 storage tests (they bypass any import service on purpose).

Every database is a throwaway `ecommerce_search_test_*` database; `scratch()` asserts it before
anything is written. All records are SYNTHETIC (`tests/returns_samples.py`).
"""

import hashlib
import uuid
from enum import Enum
from typing import Any

from sqlalchemy import Connection, Engine, text

from ecommerce_search.returns import contracts as rc
from returns_samples import DATASET, RECORDS

SCRATCH_PREFIX = "ecommerce_search_test_"
CATALOG_DATASET = ("synthetic-seed", "1")
PRODUCTS = {"SYN-SHO-0001": "shoes", "SYN-LAP-0001": "laptop"}
ENTITY_ORDER = tuple(e.value for e in rc.Entity)  # parents before children


def scratch(target: Engine | Connection | str) -> str:
    name = target if isinstance(target, str) else target.engine.url.database
    assert name and name.startswith(SCRATCH_PREFIX), name
    return name


def insert(conn: Connection, table: str, values: dict[str, Any], returning: str | None = "id"):
    columns = ", ".join(values)
    params = ", ".join(f":{k}" for k in values)
    sql = f"INSERT INTO {table} ({columns}) VALUES ({params})"  # noqa: S608 - test identifiers
    if returning:
        return conn.execute(text(f"{sql} RETURNING {returning}"), values).scalar_one()
    conn.execute(text(sql), values)
    return None


def raw_line_sha(product_id: str) -> str:
    return hashlib.sha256(f'{{"product_id":"{product_id}"}}'.encode()).hexdigest()


def add_catalog(conn: Connection) -> None:
    """A minimal catalog dataset with the two products the samples reference."""
    scratch(conn)
    dataset_pk = insert(
        conn,
        "catalog_datasets",
        {
            "dataset_id": CATALOG_DATASET[0],
            "dataset_version": CATALOG_DATASET[1],
            "source_description": "s",
            "rights_note": "r",
            "authored_on": "2026-09-30",
            "generator": "g",
            "is_synthetic": True,
            "transform_version": "1",
            "taxonomy_version": "1",
            "rules_version": "1",
            "checksum_sha256": "a" * 64,
            "record_count": len(PRODUCTS),
        },
    )
    for line, (product_id, category) in enumerate(PRODUCTS.items(), start=1):
        raw_pk = insert(
            conn,
            "raw_catalog_records",
            {
                "dataset_pk": dataset_pk,
                "source_key": product_id,
                "line_number": line,
                "raw_line": f'{{"product_id":"{product_id}"}}',
                "raw_sha256": raw_line_sha(product_id),
            },
        )
        insert(
            conn,
            "products",
            {
                "product_id": product_id,
                "dataset_pk": dataset_pk,
                "raw_record_id": raw_pk,
                "title": "t",
                "category": category,
                "brand": "b",
                "price": 100,
                "currency": "INR",
                "availability": "in_stock",
                "source_type": "synthetic",
                "is_synthetic": True,
                "content_sha256": "a" * 64,
            },
            returning=None,
        )


def add_dataset(conn: Connection, *, version: int = 1, **over):
    """A dataset row from the valid sample descriptor; `over` columns are written as given."""
    scratch(conn)
    descriptor = rc.DatasetDescriptor.model_validate(DATASET | {"dataset_version": version})
    values = dict(descriptor) | {"descriptor_sha256": descriptor.descriptor_sha256()} | over
    values["source_type"] = str(values["source_type"])
    return insert(conn, "return_datasets", values)


def batch_values(dataset_pk: int, **over) -> dict[str, Any]:
    batch_id = uuid.uuid4()
    values: dict[str, Any] = {
        "batch_id": batch_id,
        "dataset_pk": dataset_pk,
        "contract_version": "return-data-v1",
        "transformation_version": "returns-import-1",
        "redaction_version": "redaction-1",
        "bundle_sha256": hashlib.sha256(batch_id.bytes).hexdigest(),
        "format": "json",
        "file_count": 1,
        "total_bytes": 2,
        "imported_by_label": "test-importer",
    }
    for entity in ENTITY_ORDER:
        values |= {f"{entity}_received": 1, f"{entity}_new": 1, f"{entity}_unchanged": 0}
    return values | over


def add_batch(conn: Connection, dataset_pk: int, **over) -> int:
    return insert(conn, "return_import_batches", batch_values(dataset_pk, **over))


def add_file(
    conn: Connection, batch_pk: int, *, name: str = "bundle.json", content: bytes = b"{}"
) -> int:
    sha = hashlib.sha256(content).hexdigest()
    entity = None if name in ("bundle.json", "manifest.json") else name.removesuffix(".csv")
    file_pk = insert(
        conn,
        "return_import_files",
        {
            "batch_pk": batch_pk,
            "name": name,
            "entity": entity,
            "media_type": "text/csv" if entity else "application/json",
            "size_bytes": len(content),
            "raw_sha256": sha,
            "row_count": None if name == "manifest.json" else 1,
        },
    )
    insert(
        conn,
        "return_import_file_contents",
        {"file_pk": file_pk, "raw_bytes": content, "raw_sha256": sha},
        returning=None,  # INSERT ... RETURNING would need SELECT, which nobody but the owner has
    )
    return file_pk


def entity_values(entity: str, record: dict[str, Any]) -> dict[str, Any]:
    """Database values of one validated record, without provenance."""
    model = rc.ENTITY_MODELS[rc.Entity(entity)].model_validate(record)
    values = {k: (v.value if isinstance(v, Enum) else v) for k, v in model}
    if rc.Entity(entity) in rc.FREE_TEXT_FIELDS:
        values |= {f"redacted_{kind}_count": 0 for kind in rc.REDACTION_KINDS}
    values["source_record_id"] = model.source_record_id()
    values["record_sha256"] = model.record_sha256()
    return values


def sample(entity: str, **over) -> dict[str, Any]:
    record = dict(RECORDS[entity]) | over
    if "catalog_raw_sha256" in record and "catalog_raw_sha256" not in over:
        record["catalog_raw_sha256"] = raw_line_sha(record["product_id"])
    return record


class Linked:
    """One dataset version with one batch and file; inserts entity rows into it."""

    def __init__(self, conn: Connection, *, version: int = 1):
        self.conn = conn
        self.dataset_pk = add_dataset(conn, version=version)
        self.batch_pk = add_batch(conn, self.dataset_pk)
        self.file_pk = add_file(conn, self.batch_pk)
        self.rows = 0

    def values(self, entity: str, *, columns: dict[str, Any] | None = None, **record_over):
        self.rows += 1
        return {
            "dataset_pk": self.dataset_pk,
            "batch_pk": self.batch_pk,
            "source_file_pk": self.file_pk,
            "source_row": self.rows,
            **entity_values(entity, sample(entity, **record_over)),
        } | (columns or {})

    def add(
        self,
        entity: str,
        *,
        columns: dict[str, Any] | None = None,
        conn: Connection | None = None,
        **record_over,
    ) -> int:
        values = self.values(entity, columns=columns, **record_over)
        return insert(conn or self.conn, entity, values)

    def add_all(self) -> None:
        for entity in ENTITY_ORDER:
            self.add(entity)

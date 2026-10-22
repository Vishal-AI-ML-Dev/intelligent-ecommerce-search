"""Milestone 16: `read_candidate_records` returns the typed catalog spec fields of exactly the
requested products from the seeded database (the assistant's RAM/storage evidence)."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from ecommerce_search.search.reranked import read_candidate_records

pytestmark = pytest.mark.integration

LAPTOP_SPECS = text(
    "SELECT product_id, ram_gb, storage_gb, storage_type, storage_interface "
    "FROM laptop_specs ORDER BY product_id LIMIT 3"
)
ONE_PHONE = text(
    "SELECT product_id, ram_gb, storage_gb FROM phone_specs ORDER BY product_id LIMIT 1"
)


def _str(value):
    return None if value is None else str(value)


def test_spec_fields_equal_the_stored_rows_for_exactly_the_requested_ids(seeded_engine):
    with seeded_engine.connect() as conn:
        laptops = conn.execute(LAPTOP_SPECS).all()
        phone = conn.execute(ONE_PHONE).one()
    ids = [row.product_id for row in laptops] + [phone.product_id]
    with Session(seeded_engine) as session:
        records = read_candidate_records(session, ids)
        assert not session.in_transaction()
    assert set(records) == set(ids)
    for row in laptops:
        spec = records[row.product_id].spec
        assert spec.ram_gb == row.ram_gb
        assert spec.storage_gb == row.storage_gb
        assert _str(spec.storage_type) == row.storage_type
        assert _str(spec.storage_interface) == row.storage_interface
    phone_spec = records[phone.product_id].spec
    assert (phone_spec.ram_gb, phone_spec.storage_gb) == (phone.ram_gb, phone.storage_gb)
    assert getattr(phone_spec, "storage_type", None) is None


def test_no_ids_reads_nothing_and_an_open_transaction_is_refused(seeded_engine):
    with Session(seeded_engine) as session:
        assert dict(read_candidate_records(session, [])) == {}
        session.execute(text("SELECT 1"))
        with pytest.raises(RuntimeError):
            read_candidate_records(session, ["anything"])

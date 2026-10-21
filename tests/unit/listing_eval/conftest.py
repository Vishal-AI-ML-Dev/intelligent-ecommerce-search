"""Fixtures for the M14-4 offline listing-evaluator tests (synthetic fixtures only)."""

import socket

import pytest
from listing_eval_support import default_outcomes, write_fixture, write_outcomes


def _refuse(*args, **kwargs):
    raise RuntimeError("network access is not allowed in listing evaluation tests")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)
    monkeypatch.setattr(socket.socket, "connect", _refuse)


@pytest.fixture
def fixture_set(tmp_path):
    """(set path, raw dir, outcomes path) for the default synthetic fixture."""
    set_path, raw_dir = write_fixture(tmp_path)
    outcomes = write_outcomes(tmp_path / "outcomes.jsonl", set_path, default_outcomes())
    return set_path, raw_dir, outcomes

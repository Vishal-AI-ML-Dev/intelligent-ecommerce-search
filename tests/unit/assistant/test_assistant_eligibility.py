"""Milestone 16: country eligibility, personal-data screening and trusted client IP, mocked.

Synthetic dataset files only (the real DB-IP file is not in the repository). These tests show
the decision logic; they do not show that IP geolocation is accurate.
"""

import gzip
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_assistant_api import answer_draft
from test_assistant_workflow import FakeGenerator, FakeRetriever

from ecommerce_search.api import assistant as assistant_api
from ecommerce_search.api.app import create_app
from ecommerce_search.api.dependencies import get_eligibility, get_generation_provider
from ecommerce_search.assistant.eligibility import (
    AVAILABLE_REGIONS,
    ELIGIBLE_COUNTRIES,
    RESTRICTED_REGIONS,
    CountryDataError,
    CountryEligibility,
    LazyEligibility,
)
from ecommerce_search.assistant.privacy import looks_personal

ROOT = Path(__file__).resolve().parents[3]
CSV = "\n".join(
    [
        "0.0.0.0,0.255.255.255,ZZ",
        "1.0.0.0,1.0.0.255,IN",
        "1.0.1.0,1.0.1.255,IN",  # merged with the range above
        "2.0.0.0,2.0.0.255,DE",
        "3.0.0.0,3.0.0.255,CN",  # not an available region
        "4.0.0.0,4.0.0.255,GB",
        "5.0.0.0,5.0.0.255,US",
        "2001:db9::,2001:db9::ffff,IN",
        "2a00::,2a00::ffff,FR",
    ]
)


def write_dataset(tmp_path: Path, text: str = CSV) -> tuple[Path, str]:
    path = tmp_path / "dbip-country-lite.csv.gz"
    raw = text.encode("ascii")
    path.write_bytes(gzip.compress(raw))
    return path, hashlib.sha1(raw).hexdigest()  # noqa: S324 - matches DB-IP's published SHA-1


def test_region_sets_follow_the_terms():
    for code in ("DE", "FR", "IE", "NL", "IS", "LI", "NO", "CH", "GB"):
        assert code in RESTRICTED_REGIONS and code not in ELIGIBLE_COUNTRIES
    for code in ("IN", "US", "AU", "JP", "SG", "AE"):
        assert code in ELIGIBLE_COUNTRIES
    for code in ("CN", "RU", "IR", "BY", "HK"):  # not on the available-regions page
        assert code not in AVAILABLE_REGIONS and code not in ELIGIBLE_COUNTRIES
    assert len(AVAILABLE_REGIONS) == 230  # the 230 names on the page, 2026-10-22


@pytest.mark.parametrize(
    ("ip", "expected"),
    [
        ("1.0.0.7", "eligible"),
        ("1.0.1.200", "eligible"),
        ("5.0.0.1", "eligible"),
        ("2001:db9::1", "eligible"),
        ("::ffff:1.0.0.7", "eligible"),  # IPv4-mapped IPv6
        ("2.0.0.1", "restricted_region"),
        ("4.0.0.1", "restricted_region"),
        ("2a00::1", "restricted_region"),
        ("3.0.0.1", "unknown_region"),  # listed country, not available
        ("6.0.0.1", "unknown_region"),  # not in the dataset
        ("127.0.0.1", "unknown_region"),
        ("10.1.2.3", "unknown_region"),
        ("not-an-ip", "unknown_region"),
        (None, "unknown_region"),
    ],
)
def test_lookup_classifies_eligible_restricted_and_unknown(tmp_path, ip, expected):
    path, sha1 = write_dataset(tmp_path)
    assert CountryEligibility.load(path, sha1).check(ip) == expected


def test_adjacent_same_class_ranges_are_merged(tmp_path):
    path, _ = write_dataset(tmp_path)
    data = CountryEligibility.load(path)
    assert data.ranges == 6  # IN (merged), DE, GB, US, IN v6, FR v6; ZZ and CN dropped


def test_checksum_mismatch_or_bad_data_refuses_to_load(tmp_path):
    path, _ = write_dataset(tmp_path)
    with pytest.raises(CountryDataError):
        CountryEligibility.load(path, "0" * 40)
    bad = tmp_path / "bad.csv.gz"
    bad.write_bytes(b"not gzip")
    with pytest.raises(CountryDataError):
        CountryEligibility.load(bad)
    odd, _ = write_dataset(tmp_path, "1.0.0.0,1.0.0.255,IN,extra")
    with pytest.raises(CountryDataError):
        CountryEligibility.load(odd)


def loaded(path, sha1):
    lookup = LazyEligibility(path, sha1)
    lookup.load_now()
    return lookup


def test_lookup_fails_closed_until_loaded_and_on_any_load_problem(tmp_path):
    path, sha1 = write_dataset(tmp_path)
    assert LazyEligibility(path, sha1).check("1.0.0.7") == "unknown_region"  # not loaded yet
    assert loaded(None, None).status == "no_dataset"
    missing = loaded(tmp_path / "missing.gz", None)
    assert missing.status == "load_failed" and missing.check("1.0.0.7") == "unknown_region"
    mismatch = loaded(path, "f" * 40)
    assert mismatch.status == "load_failed" and mismatch.check("1.0.0.7") == "unknown_region"
    good = loaded(path, sha1)
    assert good.status == "loaded" and good.check("1.0.0.7") == "eligible"


def test_background_load_never_blocks_a_check(tmp_path):
    import time

    path, sha1 = write_dataset(tmp_path)
    lookup = LazyEligibility(path, sha1)
    lookup.start()
    lookup.check("1.0.0.7")  # returns at once, whatever the load state
    for _ in range(100):
        if lookup.status == "loaded":
            break
        time.sleep(0.01)
    assert lookup.check("1.0.0.7") == "eligible"


@pytest.mark.parametrize(
    "message",
    [
        "mail me at someone@example.com",
        "call +91 98765 43210",
        "my number 9876543210 laptop",
        "aadhaar 1234 5678 9012",
        "card 4111-1111-1111-1111",
    ],
)
def test_personal_data_screen_flags_common_identifiers(message):
    assert looks_personal(message)


@pytest.mark.parametrize(
    "message",
    ["laptop under rs 150000", "16gb ram 512gb ssd", "phone with 5000 mah battery", "dell 2024"],
)
def test_personal_data_screen_passes_ordinary_shopping_text(message):
    assert not looks_personal(message)


def _request(peer: str | None, headers: dict[str, str] | None = None):
    return SimpleNamespace(
        client=None if peer is None else SimpleNamespace(host=peer),
        headers={k.lower(): v for k, v in (headers or {}).items()},
    )


def test_forwarded_header_is_ignored_unless_the_peer_is_a_trusted_proxy():
    from ipaddress import ip_network

    trusted = (ip_network("172.16.0.0/12"),)
    spoofed = {"X-Client-IP": "1.0.0.7"}
    # A direct client cannot choose its country with a header.
    assert assistant_api.client_ip(_request("2.0.0.1", spoofed), trusted) == "2.0.0.1"
    assert assistant_api.client_ip(_request("2.0.0.1", spoofed), ()) == "2.0.0.1"
    # From the trusted proxy, the header (set by Caddy to the real peer) is used.
    assert assistant_api.client_ip(_request("172.18.0.5", spoofed), trusted) == "1.0.0.7"
    # Trusted proxy, but no header, a list or junk: unknown.
    assert assistant_api.client_ip(_request("172.18.0.5"), trusted) is None
    many = {"X-Client-IP": "1.0.0.7, 2.0.0.1"}
    assert assistant_api.client_ip(_request("172.18.0.5", many), trusted) is None
    junk = {"X-Client-IP": "evil"}
    assert assistant_api.client_ip(_request("172.18.0.5", junk), trusted) is None
    assert assistant_api.client_ip(_request(None), trusted) is None


class FixedEligibility:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def check(self, client_ip):
        self.calls += 1
        return self.result


@pytest.fixture
def gated_client(make_settings):
    def _client(eligibility, generator):
        app = create_app(make_settings())
        app.dependency_overrides[assistant_api.get_assistant_retriever] = FakeRetriever
        app.dependency_overrides[get_generation_provider] = lambda: generator
        app.dependency_overrides[get_eligibility] = lambda: eligibility
        return TestClient(app)

    return _client


@pytest.mark.parametrize(
    ("region", "failure", "notice"),
    [
        ("restricted_region", "region_restricted", "for your location"),
        ("unknown_region", "region_unknown", "could not be confirmed"),
    ],
)
def test_restricted_or_unknown_regions_never_reach_the_provider(
    gated_client, region, failure, notice
):
    generator = FakeGenerator()
    with gated_client(FixedEligibility(region), generator) as client:
        body = client.post("/assistant/answer", json={"message": "dell laptop"}).json()
    assert body["outcome"] == "generation_unavailable"
    assert body["generation"]["status"] == "skipped"
    assert body["generation"]["failure"] == failure
    assert body["generation"]["calls"] == 0
    assert notice in body["notice"]
    assert len(body["evidence"]) == 3  # the honest fallback: plain search results
    assert generator.requests == []


def test_eligible_region_generates(gated_client):
    generator = FakeGenerator(answer_draft())
    with gated_client(FixedEligibility("eligible"), generator) as client:
        body = client.post("/assistant/answer", json={"message": "dell laptop"}).json()
    assert body["outcome"] == "answer" and body["generation"]["calls"] == 1


def test_personal_data_is_screened_before_any_geolocation_or_provider_call(gated_client):
    eligibility, generator = FixedEligibility("eligible"), FakeGenerator()
    with gated_client(eligibility, generator) as client:
        body = client.post(
            "/assistant/answer", json={"message": "dell laptop, call 9876543210"}
        ).json()
    assert body["generation"]["failure"] == "personal_data_suspected"
    assert "may contain personal information" in body["notice"]
    assert generator.requests == [] and eligibility.calls == 0


def test_without_a_dataset_generation_is_skipped_for_everyone(make_settings):
    app = create_app(make_settings())
    app.dependency_overrides[assistant_api.get_assistant_retriever] = FakeRetriever
    app.dependency_overrides[get_generation_provider] = lambda: FakeGenerator()
    with TestClient(app) as client:
        body = client.post("/assistant/answer", json={"message": "dell laptop"}).json()
    assert body["generation"]["failure"] == "region_unknown"


def test_status_shows_provider_notice_and_attribution_only_when_generation_is_on(gated_client):
    with gated_client(FixedEligibility("eligible"), FakeGenerator()) as client:
        status = client.get("/assistant/status").json()
    assert status["generation_enabled"] is True
    assert "may use them to improve its products" in status["provider_notice"]
    assert "Do not enter personal" in status["provider_notice"]
    assert "18+" in status["provider_notice"]
    assert status["geolocation_attribution"] == "IP Geolocation by DB-IP"
    assert status["geolocation_attribution_url"] == "https://db-ip.com"


def test_trusted_proxy_setting_is_validated(make_settings):
    assert make_settings().trusted_proxy_networks() == ()
    nets = make_settings(assistant_trusted_proxy_cidrs="172.16.0.0/12").trusted_proxy_networks()
    assert [str(n) for n in nets] == ["172.16.0.0/12"]
    with pytest.raises(ValueError):
        make_settings(assistant_trusted_proxy_cidrs="172.16.0.1/12")
    with pytest.raises(ValueError):
        make_settings(assistant_country_db_sha1="not-a-sha")


def test_ec2_kit_sets_the_client_ip_header_and_keeps_generation_off_by_default():
    caddy = (ROOT / "deploy" / "ec2" / "Caddyfile").read_text(encoding="utf-8")
    assert "header_up X-Client-IP {remote_host}" in caddy
    compose = (ROOT / "deploy" / "ec2" / "compose.yaml").read_text(encoding="utf-8")
    assert "ASSISTANT_GENERATION_PROVIDER: ${ASSISTANT_GENERATION_PROVIDER:-none}" in compose
    assert "GEMINI_API_KEY: ${GEMINI_API_KEY:-}" in compose
    assert "./geo:/geo:ro" in compose

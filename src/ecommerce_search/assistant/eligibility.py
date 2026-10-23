"""Country eligibility for answer generation (Milestone 16, owner-approved 2026-10-23).

Before any Gemini request the assistant checks the visitor's country, from the client IP and the
offline DB-IP "IP to Country Lite" dataset (CC BY 4.0, attribution "IP Geolocation by DB-IP",
https://db-ip.com). Generation runs only for countries in `ELIGIBLE_COUNTRIES`. Restricted,
unknown or unlisted countries get the existing honest fallback (plain search results).

Why: the Gemini API Additional Terms allow only Paid Services for API clients offered to users
in the EEA, Switzerland and the UK, and allow access only from available regions. This project
uses the free tier only.

Limits (do not overclaim): IP geolocation is approximate, VPNs and proxies change the apparent
country, and the dataset ages monthly. This check is a best-effort control that lowers the risk.
It does not guarantee compliance.
"""

import gzip
import hashlib
import ipaddress
import socket
import threading
from array import array
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Eligibility = Literal["eligible", "restricted_region", "unknown_region"]

# The Gemini API available-regions page (ai.google.dev/gemini-api/docs/available-regions, last
# updated 2026-10-22), transcribed to ISO 3166-1 alpha-2 codes (Kosovo: XK) on 2026-10-23.
AVAILABLE_REGIONS = frozenset(
    "AL DZ AS AD AO AI AQ AG AR AM AW AU AT AZ BS BH BD BB BE BZ BJ BM BT BO BA BW BR IO VG BN "  # noqa: SIM905 - compact transcription of the published list
    "BG BF BI CV KH CM CA BQ KY CF TD CL CX CC CO KM CK CR CI HR CW CZ CD DK DJ DM DO EC EG SV "
    "GQ ER EE SZ ET FK FO FJ FI FR GF GA GM GE DE GH GI GR GL GD GU GT GG GN GW GY HT HM HN HU "
    "IS IN ID IQ IE IM IL IT JM JP JE JO KZ KE KI XK KW KG LA LV LB LS LR LY LI LT LU MG MW MY "
    "MV ML MT MH MR MU MX FM MD MC MN ME MS MA MZ NA NR NP NL NC NZ NI NE NG NU NF MK MP NO OM "
    "PK PW PS PA PG PY PE PH PN PL PT PR QA CY CG RO RW RE BL SH KN LC PM VC WS SM ST SA SN RS "
    "SC SL SG SK SI SB SO ZA GS KR SS ES LK SD SR SE CH TW TJ TZ TH TL TG TK TO TT TN TM TC TV "
    "TR UG UA AE GB US UM UY VI UZ VU VA VE VN WF EH YE ZM ZW AX".split()
)

# Paid-only for API clients offered there: the EEA (EU 27 + Iceland, Liechtenstein, Norway),
# Switzerland and the UK. Conservatively also EU outermost regions in the available list (Åland,
# French Guiana, Réunion) and UK-related territories (Jersey, Guernsey, Isle of Man, Gibraltar).
RESTRICTED_REGIONS = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE "  # noqa: SIM905
    "IS LI NO CH GB AX GF RE JE GG IM GI".split()
)

ELIGIBLE_COUNTRIES = AVAILABLE_REGIONS - RESTRICTED_REGIONS

ATTRIBUTION = "IP Geolocation by DB-IP"
ATTRIBUTION_URL = "https://db-ip.com"

_ELIGIBLE, _RESTRICTED = 1, 2


class CountryDataError(Exception):
    """The dataset is missing, unreadable or does not match the expected checksum."""


@dataclass(frozen=True)
class _Family:
    starts: list[int] | array
    ends: list[int] | array
    classes: array

    def classify(self, value: int) -> int:
        index = bisect_right(self.starts, value) - 1
        if index >= 0 and value <= self.ends[index]:
            return self.classes[index]
        return 0


def _merged(rows: list[tuple[int, int, int]]) -> tuple[list[int], list[int], array]:
    """Sorted ranges with adjacent same-class ranges merged; unknown (0) ranges dropped."""
    starts: list[int] = []
    ends: list[int] = []
    classes = array("b")
    for start, end, cls in sorted(rows):
        if cls == 0:
            continue
        if starts and classes[-1] == cls and ends[-1] + 1 >= start:
            ends[-1] = max(ends[-1], end)
        else:
            starts.append(start)
            ends.append(end)
            classes.append(cls)
    return starts, ends, classes


class CountryEligibility:
    """Range lookup built once from the DB-IP CSV (`start,end,country`, gzip-compressed)."""

    def __init__(self, v4: _Family, v6: _Family, ranges: int) -> None:
        self._v4 = v4
        self._v6 = v6
        self.ranges = ranges

    @classmethod
    def load(cls, path: Path, expected_sha1: str | None = None) -> "CountryEligibility":
        try:
            raw = gzip.decompress(path.read_bytes())
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise CountryDataError("country dataset unreadable") from exc
        if expected_sha1 is not None and hashlib.sha1(raw).hexdigest() != expected_sha1.lower():  # noqa: S324 - published checksum, not security
            raise CountryDataError("country dataset checksum mismatch")
        rows4: list[tuple[int, int, int]] = []
        rows6: list[tuple[int, int, int]] = []
        for line in raw.decode("ascii").splitlines():
            parts = line.split(",")
            if len(parts) != 3:
                raise CountryDataError("country dataset has an unexpected format")
            first, last, country = parts
            code = country.strip().upper()
            cls_ = (
                _RESTRICTED
                if code in RESTRICTED_REGIONS
                else _ELIGIBLE
                if code in ELIGIBLE_COUNTRIES
                else 0
            )
            family = socket.AF_INET6 if ":" in first else socket.AF_INET
            try:
                start = int.from_bytes(socket.inet_pton(family, first))
                end = int.from_bytes(socket.inet_pton(family, last))
            except OSError as exc:
                raise CountryDataError("country dataset has an invalid address") from exc
            (rows4 if family == socket.AF_INET else rows6).append((start, end, cls_))
        s4, e4, c4 = _merged(rows4)
        s6, e6, c6 = _merged(rows6)
        v4 = _Family(array("L", s4), array("L", e4), c4)
        v6 = _Family(s6, e6, c6)
        return cls(v4, v6, len(c4) + len(c6))

    def check(self, client_ip: str | None) -> Eligibility:
        if client_ip is None:
            return "unknown_region"
        try:
            address = ipaddress.ip_address(client_ip)
        except ValueError:
            return "unknown_region"
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not address.is_global:
            return "unknown_region"
        family = self._v4 if address.version == 4 else self._v6
        cls_ = family.classify(int(address))
        if cls_ == _ELIGIBLE:
            return "eligible"
        if cls_ == _RESTRICTED:
            return "restricted_region"
        return "unknown_region"


class LazyEligibility:
    """Loads the dataset once in a background thread (`start`), so no request waits for it.
    Until it has loaded, and after any load failure, every check is unknown (fail closed)."""

    def __init__(self, path: Path | None, expected_sha1: str | None) -> None:
        self._path = path
        self._sha1 = expected_sha1
        self._lock = threading.Lock()
        self._started = False
        self._data: CountryEligibility | None = None
        self.status = "not_loaded"

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        threading.Thread(target=self._load, name="country-eligibility-load", daemon=True).start()

    def load_now(self) -> None:
        """Synchronous load (tests and tools)."""
        with self._lock:
            self._started = True
        self._load()

    def _load(self) -> None:
        if self._path is None:
            self.status = "no_dataset"
            return
        try:
            self._data = CountryEligibility.load(self._path, self._sha1)
            self.status = "loaded"
        except CountryDataError:
            self.status = "load_failed"

    def check(self, client_ip: str | None) -> Eligibility:
        data = self._data
        return "unknown_region" if data is None else data.check(client_ip)

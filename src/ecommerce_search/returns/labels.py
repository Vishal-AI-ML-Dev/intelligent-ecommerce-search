"""The importer label recorded on every committed batch (contracts §6, `imported_by_label`).

A label is supplied provenance, not an authenticated identity: it says which configured
importer committed a batch, never which person. It is a short plain token, and a value that
matches any `redaction-1` pattern is refused, so a label cannot carry an email address, a UPI
handle, a long number or a PIN code.
"""

import re

from ecommerce_search.returns.redaction import matches_any

IMPORTER_LABEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")


def is_valid_importer_label(value: object) -> bool:
    return (
        isinstance(value, str)
        and IMPORTER_LABEL_PATTERN.fullmatch(value) is not None
        and not matches_any(value)
    )

"""Blind review items from an M18 `return-data-v1` release bundle (read offline).

An item holds only what a human reviewer needs: the return ID, the customer's reason code, the
return's comments, the support complaints linked to it, and the structured inspection
conditions. Links follow `complaints-by-order-item-1` (exact `order_item_id`), the same policy
as the stored baseline runs. Text is passed through `redaction-1`, as it is when imported.

Nothing else is read or kept: no product, order, shipment, review or correction-case data, no
inspection note, no baseline output, and no generator scenario file. A path named
`scenarios.json` is refused, and so is any file that is not a `return-data-v1` bundle.
"""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.redaction import redact

BUNDLE_MAX_BYTES = 16 * 1024 * 1024


class SourceError(ValueError):
    """A fixed, already-safe message."""


@dataclass(frozen=True, slots=True)
class Statement:
    evidence_id: str
    text: str  # redacted
    topic: str | None = None  # support complaints only


@dataclass(frozen=True, slots=True)
class BlindItem:
    return_id: str
    reason_code: str
    comments: tuple[Statement, ...]
    complaints: tuple[Statement, ...]
    inspections: tuple[tuple[str, str], ...]  # (observation_id, condition)


@dataclass(frozen=True, slots=True)
class Bundle:
    sha256: str
    dataset_id: str
    dataset_version: int
    items: tuple[BlindItem, ...]  # ordered by return_id


def load_bundle(path: Path) -> Bundle:
    if path.name == "scenarios.json":
        raise SourceError("generator scenario files are never read")
    try:
        data = path.read_bytes()
    except OSError:
        raise SourceError("the bundle could not be read") from None
    if len(data) > BUNDLE_MAX_BYTES:
        raise SourceError("the bundle is larger than allowed")
    try:
        body = json.loads(data)
    except ValueError:
        raise SourceError("the bundle is not JSON") from None
    if not isinstance(body, dict) or body.get("contract_version") != "return-data-v1":
        raise SourceError("not a return-data-v1 bundle")
    try:
        dataset = rc.DatasetDescriptor.model_validate(body["dataset"])
        returns = [rc.Return.model_validate(r) for r in body["returns"]]
        comments = [rc.ReturnComment.model_validate(r) for r in body["return_comments"]]
        inspections = [
            rc.InspectionObservation.model_validate(r) for r in body["inspection_observations"]
        ]
        complaints = [rc.SupportComplaint.model_validate(r) for r in body["support_complaints"]]
    except (KeyError, TypeError, ValueError):
        raise SourceError("the bundle records are not valid") from None
    by_return: dict[str, list[rc.ReturnComment]] = defaultdict(list)
    for c in comments:
        by_return[c.return_id].append(c)
    seen: dict[str, list[rc.InspectionObservation]] = defaultdict(list)
    for o in inspections:
        seen[o.return_id].append(o)
    by_item: dict[str, list[rc.SupportComplaint]] = defaultdict(list)
    for c in complaints:
        if c.order_item_id is not None:
            by_item[c.order_item_id].append(c)
    items = tuple(
        BlindItem(
            return_id=r.return_id,
            reason_code=r.customer_reason_code.value,
            comments=tuple(
                Statement(c.comment_id, redact(c.text).text)
                for c in sorted(by_return[r.return_id], key=lambda c: c.comment_id)
            ),
            complaints=tuple(
                Statement(
                    c.complaint_id,
                    redact(c.text).text,
                    c.customer_topic.value if c.customer_topic else None,
                )
                for c in sorted(by_item[r.order_item_id], key=lambda c: c.complaint_id)
            ),
            inspections=tuple(
                (o.observation_id, o.condition.value)
                for o in sorted(seen[r.return_id], key=lambda o: o.observation_id)
            ),
        )
        for r in sorted(returns, key=lambda r: r.return_id)
    )
    return Bundle(
        sha256=hashlib.sha256(data).hexdigest(),
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        items=items,
    )

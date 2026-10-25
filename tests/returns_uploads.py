"""Build SYNTHETIC `return-data-v1` uploads (JSON bundle or CSV set) for the M18-03A tests.

Records come from `returns_samples.py` unless given; no names, addresses or personal data.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime

from ecommerce_search.returns import contracts as rc
from ecommerce_search.returns.bundle import Part
from ecommerce_search.returns.validation import validate_upload
from returns_samples import DATASET, RECORDS

CHECKED_AT = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def sample_records(**overrides: list[dict]) -> dict[str, list[dict]]:
    records = {entity: [copy.deepcopy(record)] for entity, record in RECORDS.items()}
    records.update(overrides)
    return records


def json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def json_parts(records: dict[str, list[dict]] | None = None, *, dataset=None, **top) -> list[Part]:
    body = {
        "contract_version": "return-data-v1",
        "dataset": DATASET if dataset is None else dataset,
        **(sample_records() if records is None else records),
        **top,
    }
    return [Part("bundle.json", json_bytes(body))]


def csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def csv_bytes(entity: str, rows: list[dict], *, newline: str = "\r\n", bom: bool = False) -> bytes:
    columns = sorted(rc.ENTITY_MODELS[rc.Entity(entity)].model_fields)

    def quote(text: str) -> str:
        return '"' + text.replace('"', '""') + '"'

    lines = [",".join(quote(c) for c in columns)]
    lines += [",".join(quote(csv_value(row.get(c))) for c in columns) for row in rows]
    data = (newline.join(lines) + newline).encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


def manifest_bytes(files: dict[str, bytes], rows: dict[str, int], *, dataset=None) -> bytes:
    return json_bytes(
        {
            "contract_version": "return-data-v1",
            "dataset": DATASET if dataset is None else dataset,
            "files": [
                {
                    "name": name,
                    "entity": name.removesuffix(".csv"),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "rows": rows[name],
                }
                for name, data in sorted(files.items())
            ],
        }
    )


def csv_parts(
    records: dict[str, list[dict]] | None = None, *, dataset=None, newline="\r\n", bom=False
) -> list[Part]:
    records = sample_records() if records is None else records
    files = {
        f"{e}.csv": csv_bytes(e, rows, newline=newline, bom=bom) for e, rows in records.items()
    }
    manifest = manifest_bytes(
        files, {f"{e}.csv": len(r) for e, r in records.items()}, dataset=dataset
    )
    return [Part("manifest.json", manifest), *(Part(n, d) for n, d in files.items())]


def validate(parts: list[Part], checked_at: datetime = CHECKED_AT):
    return validate_upload(parts, checked_at=checked_at)


def codes(result) -> list[str]:
    return [e.code for e in result.report.errors]

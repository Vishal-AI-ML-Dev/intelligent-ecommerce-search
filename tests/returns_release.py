"""The M18 release files (`data/seed/returns_v1/`) and their generator, for the M18-03C tests.

Every record there is SYNTHETIC. `scenarios.json` is generator intent, not labels; tests read
it only to check coverage and must never import it.
"""

import importlib.util
import json
import sys
from functools import cache
from pathlib import Path
from types import ModuleType

from ecommerce_search.returns.bundle import BUNDLE_NAME, Part

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "data" / "seed" / "returns_v1"
BUNDLE = RELEASE / "bundle" / "returns_v1.json"
CSV_DIR = RELEASE / "csv"
SCENARIOS = RELEASE / "scenarios.json"
PROVENANCE = RELEASE / "provenance.json"
GENERATOR = ROOT / "scripts" / "generate_returns_dataset.py"


@cache
def generator() -> ModuleType:
    name = "returns_dataset_generator"
    spec = importlib.util.spec_from_file_location(name, GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def json_parts() -> list[Part]:
    return [Part(BUNDLE_NAME, BUNDLE.read_bytes())]


def csv_parts() -> list[Part]:
    return [Part(p.name, p.read_bytes()) for p in sorted(CSV_DIR.iterdir())]


def bundle() -> dict:
    return json.loads(BUNDLE.read_bytes())


def provenance() -> dict:
    return json.loads(PROVENANCE.read_bytes())


def scenarios() -> dict:
    return json.loads(SCENARIOS.read_bytes())

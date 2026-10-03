"""Milestone 8 Session A: the pure reranking package imports no database, web, settings, network
or model stack.

Allowed internal dependencies (pure, reviewed): `ecommerce_search.catalog` (validated records),
`ecommerce_search.embeddings.text` (the embedding-text v1 contract that `rd-1` reuses) and
`ecommerce_search.embeddings.spec` (model-id/revision validators; itself pure).

The pydantic-based catalog schemas already load the standard-library `socket` and
`urllib.parse` modules (as for the reviewed M7 `filtering` package); the probe therefore checks
that reranking adds nothing beyond that reviewed baseline, and bans network clients and the
database, web, settings and model libraries outright. Because that baseline hides a direct
import of a baseline module, a static scan also restricts the reranking source's own imports to
an explicit allowlist. No socket is used at runtime (the pure tests run under
`no_socket_connect`)."""

import ast
import subprocess
import sys
from pathlib import Path

_PROBE = """
import sys
# The reviewed, pure dependencies first: everything they load is the baseline.
import ecommerce_search.catalog.schemas
import ecommerce_search.embeddings.spec
import ecommerce_search.embeddings.text
baseline = set(sys.modules)
import ecommerce_search.reranking
import ecommerce_search.reranking.spec
import ecommerce_search.reranking.provider
import ecommerce_search.reranking.documents
import ecommerce_search.reranking.ranking
added = sorted(set(sys.modules) - baseline)
banned = ("sqlalchemy", "psycopg", "fastapi", "starlette", "httpx", "sentence_transformers",
          "transformers", "torch", "numpy", "alembic", "pgvector", "uvicorn", "pydantic_settings",
          "huggingface_hub", "hf_xet", "requests", "urllib3", "ssl", "dotenv", "tokenizers",
          "safetensors")
internal = ("ecommerce_search.api", "ecommerce_search.config", "ecommerce_search.db",
            "ecommerce_search.search", "ecommerce_search.models", "ecommerce_search.ingestion",
            "ecommerce_search.decision", "ecommerce_search.filtering",
            "ecommerce_search.query_understanding", "ecommerce_search.embeddings.provider",
            "ecommerce_search.embeddings.sentence_transformers_provider",
            "ecommerce_search.embeddings.fetch")
loaded = sorted(
    name for name in sys.modules
    if name.split(".")[0] in banned or name.startswith(internal) or name == "http.client"
)
extra = [name for name in added if not name.startswith("ecommerce_search.reranking")]
print(",".join(loaded + extra))
"""


def test_reranking_package_imports_no_db_web_settings_network_or_model_stack() -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-c", _PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""


def _reranking_sources() -> list[Path]:
    import ecommerce_search.reranking as package

    root = Path(package.__file__).parent
    return sorted(root.glob("*.py"))


# Every module the pure reranking source may import directly. Checked statically, because the
# dynamic probe above diffs against a baseline that already contains `socket`, `urllib`, `os`,
# `random` and `time` (loaded by the catalog schemas' dependencies) and so could not see a direct
# reranking import of them.
ALLOWED_IMPORTS = frozenset(
    {
        "collections.abc",
        "dataclasses",
        "fractions",
        "hashlib",
        "json",
        "math",
        "numbers",
        "typing",
        "ecommerce_search.catalog.schemas",
        "ecommerce_search.embeddings.spec",
        "ecommerce_search.embeddings.text",
        "ecommerce_search.reranking.documents",
        "ecommerce_search.reranking.provider",
        "ecommerce_search.reranking.spec",
    }
)
FORBIDDEN_CALLS = frozenset(
    {"__import__", "import_module", "open", "eval", "exec", "compile", "getenv", "print"}
)


def test_reranking_source_imports_only_the_reviewed_allowlist() -> None:
    sources = _reranking_sources()
    assert {p.name for p in sources} == {
        "__init__.py",
        "spec.py",
        "provider.py",
        "documents.py",
        "ranking.py",
    }
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0 and node.module, f"{path.name}: relative import"
                names = [node.module]
            else:
                if isinstance(node, ast.Call):
                    func = node.func
                    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                    assert name not in FORBIDDEN_CALLS, f"{path.name} calls {name}"
                continue
            for name in names:
                assert name in ALLOWED_IMPORTS, f"{path.name} imports {name}"


def test_reranking_modules_read_no_environment_or_files() -> None:
    for path in _reranking_sources():
        source = path.read_text(encoding="utf-8")
        for forbidden in ("os.environ", "getenv", "open(", "logging", "Settings", "Path("):
            assert forbidden not in source, f"{path.name} uses {forbidden}"

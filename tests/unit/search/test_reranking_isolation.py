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


CONCRETE_PROVIDER = "cross_encoder_provider.py"


def _reranking_sources() -> list[Path]:
    """The pure reranking modules (the concrete Session C provider is tested separately)."""
    import ecommerce_search.reranking as package

    root = Path(package.__file__).parent
    return sorted(p for p in root.glob("*.py") if p.name != CONCRETE_PROVIDER)


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


# ---- the concrete cross-encoder provider (Milestone 8 Session C) -------------------------------

_LAZY_PROBE = """
import sys
import ecommerce_search.reranking
import ecommerce_search.reranking.cross_encoder_provider as provider
import ecommerce_search.search.cli
import ecommerce_search.api.app
from pathlib import Path
provider.CrossEncoderReranker(provider.MS_MARCO_MINILM_L6_V2, Path("does-not-exist"))
heavy = ("torch", "transformers", "sentence_transformers", "huggingface_hub", "hf_xet",
         "tokenizers", "safetensors")
print(",".join(sorted(name for name in sys.modules if name.split(".")[0] in heavy)))
"""


def test_importing_and_constructing_the_provider_loads_no_model_library() -> None:
    done = subprocess.run(  # noqa: S603 - fixed argv, sys.executable
        [sys.executable, "-c", _LAZY_PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""


PROVIDER_MODULE_IMPORTS = frozenset(
    {
        "collections.abc",
        "json",
        "logging",
        "os",
        "pathlib",
        "threading",
        "time",
        "typing",
        "ecommerce_search.embeddings.fetch",
        "ecommerce_search.embeddings.spec",
        "ecommerce_search.reranking.provider",
        "ecommerce_search.reranking.spec",
    }
)
PROVIDER_LAZY_IMPORTS = frozenset({"torch", "sentence_transformers"})


def test_concrete_provider_imports_model_libraries_only_inside_functions() -> None:
    import ecommerce_search.reranking as package

    path = Path(package.__file__).parent / CONCRETE_PROVIDER
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert {a.name for a in node.names} <= PROVIDER_MODULE_IMPORTS
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0 and node.module in PROVIDER_MODULE_IMPORTS, node.module
    lazy = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            lazy |= {a.name for a in node.names} - PROVIDER_MODULE_IMPORTS
        elif isinstance(node, ast.ImportFrom) and node.module not in PROVIDER_MODULE_IMPORTS:
            lazy.add(node.module)
    assert lazy == PROVIDER_LAZY_IMPORTS
    source = path.read_text(encoding="utf-8")
    for forbidden in ("trust_remote_code=True", "local_files_only=False", "Sigmoid", "print("):
        assert forbidden not in source

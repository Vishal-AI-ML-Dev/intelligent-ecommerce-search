"""The weak-label track never touches the strict Golden Dataset lifecycle.

The strict golden files are hashed before and after a full SYNTHETIC weak-label run in a
temporary directory (generation, audit, assembly and reports) and must stay byte-identical;
the strict work directories must gain no file. The source of `evals.weak_labels` must not
reference the strict judgement paths."""

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from evals.runners import build_pool
from evals.weak_labels import reports
from evals.weak_labels.assembly import assemble, verify_sealed
from evals.weak_labels.audit import AuditStore, audit
from weak_support import AUDIT_T0, Clock, audit_context, generate, scripted, weak_fixture

ROOT = Path(__file__).resolve().parents[3]
STRICT_DIRS = (
    build_pool.DEFAULT_GOLDEN_DIR,
    ROOT / "data" / "processed" / "golden_annotation",
    ROOT / "data" / "processed" / "golden_pool",
)
PACKAGE = ROOT / "evals" / "weak_labels"
REFUSAL_ONLY = frozenset({"primary_judgements", "golden_annotation"})


def snapshot() -> dict[str, str]:
    out = {}
    for directory in STRICT_DIRS:
        if directory.is_dir():
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    out[path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
    return out


def test_full_weak_run_leaves_strict_paths_byte_identical(tmp_path, catalog):
    before = snapshot()
    fx = weak_fixture(tmp_path, catalog)
    generate(fx)
    context = audit_context(fx)
    store = AuditStore(context, fx.audit_root)
    audit(
        context,
        store,
        annotator="ann-1",
        ask=scripted(["a"] * 100),
        say=lambda _text: None,
        now=Clock(),
    )
    later = AUDIT_T0 + timedelta(days=365)
    assemble(
        context,
        store,
        annotator="ann-1",
        now=later,
        weak_dir=fx.weak_dir,
        allowed_root=fx.weak_root,
    )
    weak = verify_sealed(context, fx.weak_dir, allowed_root=fx.weak_root)
    reports.provisional_metrics(context, weak)
    assert snapshot() == before
    for forbidden in ("primary_judgements", "3-primary_sealed", "labels_frozen", "consistency"):
        assert not list(fx.weak_dir.rglob(f"*{forbidden}*"))


@pytest.mark.parametrize(
    "needle",
    [
        "primary_judgements",
        "evals.annotation",
        "seal_primary",
        "golden_annotation",
        "Round.PRIMARY",
        "FinalJudgement(",
    ],
)
def test_weak_label_code_never_references_the_strict_judgement_path(needle):
    for path in PACKAGE.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.name == "machine_only.py" and needle in REFUSAL_ONLY:
            # The machine-only preflight names these only to refuse when they exist.
            assert text.count(needle) == 1, f"{path.name} uses {needle} beyond its refusal"
            continue
        assert needle not in text, f"{path.name} uses {needle}"

"""Write the review-2 reviewer bundle for the blind second review (`ann-2`).

Reads the verified `primary_sealed` stage without opening the primary judgements or the
adjudication queue (`evals.annotation.reviewer_bundle`), and writes the bundle's files into a
new directory outside the repository (refused inside it, refused if it already exists). The
rubric file is supplied explicitly by the operator: the repository holds no standalone
`rubric-1` text, so the rubric given to `ann-2` is a human decision recorded by its SHA-256 in
`bundle.json`. Nothing here changes any golden artifact or manifest.

Usage:
  uv run python -m evals.runners.build_reviewer_bundle --rubric <rubric file> --out <new dir>
"""

import argparse
import sys
from pathlib import Path

from evals.annotation.reviewer_bundle import BundleError, build_bundle, is_inside, load_sealed
from evals.contracts.canonical import ContractError
from evals.contracts.catalog import CatalogIndex
from evals.runners import build_pool

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def write_bundle(
    golden_dir: Path,
    rubric_path: Path,
    out: Path,
    *,
    allowed_root: Path = build_pool.ALLOWED_ROOT,
    catalog: CatalogIndex | None = None,
    repository_root: Path = REPOSITORY_ROOT,
) -> dict[str, bytes]:
    if is_inside(out, repository_root):
        raise BundleError("the reviewer bundle must be written outside the repository")
    if out.exists():
        raise BundleError(f"refusing to write into an existing path: {out}")
    if not rubric_path.is_file():
        raise BundleError(f"the rubric file does not exist: {rubric_path}")
    sealed = load_sealed(
        golden_dir, allowed_root=allowed_root, catalog=catalog, primary_labels=False
    )
    files = build_bundle(sealed, rubric_path.read_bytes(), rubric_path.suffix)
    out.mkdir(parents=True)
    for name, data in sorted(files.items()):
        with open(out / name, "xb") as handle:
            handle.write(data)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.runners.build_reviewer_bundle")
    parser.add_argument("--golden-dir", type=Path, default=build_pool.DEFAULT_GOLDEN_DIR)
    parser.add_argument("--rubric", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        files = write_bundle(args.golden_dir, args.rubric, args.out)
    except ContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote reviewer bundle: {len(files)} files")
    for name in sorted(files):
        print(f"  {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

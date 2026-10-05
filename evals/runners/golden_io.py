"""Safe file handling shared by the Golden Dataset runners and the annotation CLI.

* `safe_golden_dir` / `safe_child`: a path strictly inside an allowed root, reached without
  `..` and without any symlink or junction component (checked on the unresolved path).
* `write_exclusive`: write a set of files all-or-nothing. Targets must not exist; files are
  staged and fsynced in a `.freeze-staging-*` directory inside the target directory, then
  hard-linked into place with exclusive creation (the designated last file, normally the
  manifest, last). A normal failure removes every target the call created.
* `refuse_stale_staging`: a `.freeze-staging-*` directory left behind by a hard kill is detected
  at startup and refused with cleanup instructions. It is never deleted silently and its
  content is never read as frozen output.
"""

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from evals.contracts.canonical import ContractError

STAGING_PREFIX = ".freeze-staging-"


class FreezeError(ContractError):
    """A stage cannot be frozen or verified; nothing was written (or it was rolled back)."""


def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def safe_golden_dir(path: Path, allowed_root: Path) -> Path:
    """`path` as an absolute directory strictly inside `allowed_root`, reached without `..` and
    without any symlink or junction component (checked on the unresolved path)."""
    if ".." in path.parts:
        raise FreezeError("the golden directory must not contain '..'")
    absolute = path if path.is_absolute() else Path.cwd() / path
    root = allowed_root if allowed_root.is_absolute() else Path.cwd() / allowed_root
    try:
        relative = absolute.relative_to(root)
    except ValueError:
        raise FreezeError(f"the golden directory must be inside {allowed_root}") from None
    if not relative.parts:
        raise FreezeError("the golden directory must be below the allowed root, not the root")
    current = root
    if is_link(current):
        raise FreezeError("the allowed root must not be a symlink or junction")
    for part in relative.parts:
        current = current / part
        if is_link(current):
            raise FreezeError(f"symlinks and junctions are refused: {current}")
    if not absolute.is_dir():
        raise FreezeError(f"the golden directory does not exist: {absolute}")
    if absolute.resolve() != root.resolve() / relative:
        raise FreezeError("the golden directory resolves outside its expected location")
    return absolute


def safe_child(directory: Path, relative: str) -> Path:
    if ".." in Path(relative).parts or Path(relative).is_absolute():
        raise FreezeError(f"refusing an unsafe relative path: {relative!r}")
    target = directory / relative
    current = directory
    for part in Path(relative).parts:
        current = current / part
        if is_link(current):
            raise FreezeError(f"symlinks and junctions are refused: {current}")
    return target


def read_frozen(directory: Path, relative: str) -> bytes:
    path = safe_child(directory, relative)
    if not path.is_file():
        raise FreezeError(f"missing frozen file: {path}")
    return path.read_bytes()


def stale_staging(directory: Path) -> list[Path]:
    """Every `.freeze-staging-*` entry anywhere below `directory` (sorted)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob(f"{STAGING_PREFIX}*"))


def refuse_stale_staging(directory: Path) -> None:
    """Fail closed when an interrupted freeze left a staging directory behind."""
    stale = stale_staging(directory)
    if stale:
        listed = "\n".join(f"  {p}" for p in stale)
        raise FreezeError(
            "stale freeze staging found (an earlier freeze was interrupted):\n"
            f"{listed}\n"
            "Its content is never treated as frozen output. Make sure no freeze is running, "
            "check that every frozen stage still verifies, then delete these staging "
            "directories by hand and rerun."
        )


def write_exclusive(
    directory: Path,
    files: Mapping[str, bytes],
    *,
    last: str | None = None,
    link: Callable[[Path, Path], None] = os.link,
) -> None:
    """Write every file or none (see the module docstring). `last` is linked last."""
    refuse_stale_staging(directory)
    ordered = sorted(files, key=lambda p: (p == last, p))
    targets = {relative: safe_child(directory, relative) for relative in ordered}
    existing = [str(t) for t in targets.values() if os.path.lexists(t)]
    if existing:
        raise FreezeError(f"refusing to overwrite frozen artifacts: {existing}")
    created_dirs: list[Path] = []
    placed: list[Path] = []
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=directory))
    try:
        for index, relative in enumerate(ordered):
            staged = staging / f"{index:02d}"
            with open(staged, "xb") as handle:
                handle.write(files[relative])
                handle.flush()
                os.fsync(handle.fileno())
        for index, relative in enumerate(ordered):
            target = targets[relative]
            missing_parents = []
            parent = target.parent
            while not parent.exists():
                missing_parents.append(parent)
                parent = parent.parent
            for directory_to_make in reversed(missing_parents):
                directory_to_make.mkdir()
                created_dirs.append(directory_to_make)
            link(staging / f"{index:02d}", target)  # fails if the target appeared meanwhile
            placed.append(target)
    except BaseException:
        for target in reversed(placed):
            target.unlink(missing_ok=True)
        for created in reversed(created_dirs):
            created.rmdir()
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)

"""Detect a `dct` build answering from somewhere other than the checkout you're standing in.

Bare `dct` resolves through `PATH`, which can land on a global
`uv tool install` / `pip install` shim rather than the current checkout's own
editable build — so the answer comes from a different, often older, source
tree with no indication in the output. That silently manufactures both false
bug reports (a stale build rejects valid syntax) and false "not reproducible"
verdicts (a stale build accepts what the current one rejects).

Scoped to a checkout that contains the dbt-charts package source, which keeps
it away from ordinary use of a global install — though the probe reads the
source tree, not the install, so cloning the public export and running a
pip-installed `dct` from inside it does warn. Warns rather than refuses:
deliberately running the installed build from inside a checkout to compare it
against the workspace build is a legitimate diagnostic move.

Known gap: a build published before this guard existed carries no check at
all, so it stays silent no matter where it answers from.
"""

from __future__ import annotations

from pathlib import Path

from dbt_charts.agent_api import version_info
from dbt_charts.agent_api._paths import find_repo_root


def _expected_package_dir(repo_root: Path) -> Path | None:
    """The package dir *this checkout* would install editable, or None if it has none.

    Two layouts ship: the monorepo nests the package under `dbt-charts/`, while
    the exported dbt-labs/dbt-charts repo has it at the root. Probing for
    `src/dbt_charts` rather than `pyproject.toml` alone is what keeps the two
    apart — the monorepo's workspace root carries a `pyproject.toml` too, and
    matching on that would resolve every monorepo checkout to a package
    directory that doesn't exist.
    """
    for base in (repo_root / "dbt-charts", repo_root):
        if (base / "pyproject.toml").is_file() and (
            base / "src" / "dbt_charts"
        ).is_dir():
            return (base / "src" / "dbt_charts").resolve()
    return None


def detect_workspace_mismatch(cwd: Path) -> str | None:
    """Warn when the running `dct` didn't come from this checkout's editable install."""
    repo_root = find_repo_root(cwd)
    if repo_root is None:
        return None
    expected = _expected_package_dir(repo_root)
    if expected is None:
        return None

    info = version_info.collect()
    actual = info.package_dir.resolve()
    if actual == expected:
        return None

    editable_note = "" if info.editable else " (not editable)"
    return (
        f"dct {info.version} is running from {actual}{editable_note}, not from "
        f"this checkout at {expected}. Use `uv run dct`, not bare `dct`, to pick "
        "up the current worktree's build."
    )

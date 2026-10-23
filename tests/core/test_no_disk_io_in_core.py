"""Guard: no builtin ``open()`` (or its close cousins) inside ``dbt_charts/core``.

`dbt_charts/core` reaches project content only through the `Project`/`ProjectPath`
ABC so an embedding host (Cloud's git-blob store) can back it with something
other than the local disk (see the "Project-file access goes through Project,
never raw Path" section of `core/AGENTS.md`). Ruff's `TID251` banned-api gate
(`core/ruff.toml`) statically forbids the `pathlib.Path`/`os`/`shutil`/`tempfile`
import forms of disk access, but it cannot ban the builtin `open()` — that's not
an import. This AST test closes that gap.

Every current real-FS `open()`/`.open()` call site is accepted debt, tracked
in `ALLOWED_OPEN` with a one-line reason. The allowlist is a ratchet: it must
equal the offender set exactly. Fixing a site means deleting its entry, not
widening the list.

`super().open(...)` is exempt in the detector itself rather than through that
ledger: it is a base-class dispatch that opens no file, and dbt's connection-
manager API happens to name its connect hook `open`. An entry in `ALLOWED_OPEN`
would misreport a non-offender as tracked debt.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .._paths import DBT_CHARTS_PKG_DIR

_CORE_DIR = DBT_CHARTS_PKG_DIR / "core"

# relpath (from DBT_CHARTS_PKG_DIR) -> one-line reason this site still opens a
# file directly instead of going through Project. Debt ledger — shrink it,
# don't grow it.
ALLOWED_OPEN: dict[str, str] = {
    "core/inspect/manifest_utils.py": (
        "reads/writes the local .inspect-template-manifest.json state file for "
        "dct inspect template ejection tracking; not yet routed through Project"
    ),
    "core/compile/sources/detection.py": (
        "reads a dbt project's profiles.yml/dbt_project.yml off local disk "
        "during dbt source-type detection; not yet routed through Project"
    ),
    "core/execute/adapters/dbt_adapter.py": (
        "reads dbt's profiles.yml/dbt_project.yml off local disk to resolve "
        "the warehouse adapter at query-execution time; not yet routed "
        "through Project"
    ),
    "core/execute/drivers/_fetch.py": (
        "writes the ADBC driver cache dbt shares (machine-level, under the "
        "user cache dir); never project content, so it does not go through Project"
    ),
    "core/render/geo/loader.py": (
        "urllib OpenerDirector.open fetches a geo source URL over http(s); "
        "a network request, never a file"
    ),
}


def _has_disk_open_call(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            return True
        if isinstance(func, ast.Attribute) and func.attr == "open":
            # `super().open(...)` dispatches to a base class, never the filesystem.
            # dbt's connection-manager API names its connect hook `open`, so a
            # subclass of one trips this rule without touching disk.
            if (
                isinstance(func.value, ast.Call)
                and isinstance(func.value.func, ast.Name)
                and func.value.func.id == "super"
            ):
                continue
            return True
    return False


def test_open_calls_in_core_match_reasoned_allowlist() -> None:
    violations = {
        py_file.relative_to(DBT_CHARTS_PKG_DIR).as_posix()
        for py_file in sorted(_CORE_DIR.rglob("*.py"))
        if _has_disk_open_call(py_file)
    }
    assert violations == set(ALLOWED_OPEN), (
        "dbt_charts/core must not call open()/.open() directly — "
        "project-file access goes through Project (core/AGENTS.md). Every "
        "site needs a reasoned ALLOWED_OPEN entry, and the entry must be "
        f"deleted once the site is fixed.\nfound: {sorted(violations)}\n"
        f"allowlisted: {sorted(ALLOWED_OPEN)}"
    )

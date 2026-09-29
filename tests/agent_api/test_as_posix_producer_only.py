"""Guard: ``.as_posix()`` on a project path appears only in the one producer.

Project file identity is a POSIX ``str``, produced in exactly one place:
``posix_relpath(path, root)`` in ``core/project.py`` (or ``ProjectPath.relpath``
when a ``ProjectPath`` is already in hand — no conversion needed there at
all). Every other call to ``.as_posix()`` on the identity-producing surface
(``agent_api/`` + ``cli/_error_format.py``) is either a bug waiting to
reintroduce Windows-backslash leakage, or it needs a stated, non-identity
reason.

This is the mechanical ratchet the "three different ways to make an identity
string" review comment asked for — the same shape as
``test_no_base_project_fspath_reads.py``'s reasoned allowlist for base-typed
``Project`` reads. ``ALLOWED`` must equal the offender set exactly; fixing a
site (routing it through ``posix_relpath``/``.relpath`` instead) means
deleting its entry, not widening the list.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .._paths import DBT_CHARTS_PKG_DIR

# Every .py under agent_api/, plus the one cli file that formats a project
# path for display — the full identity-producing surface.
_SCAN_DIRS = (DBT_CHARTS_PKG_DIR / "agent_api",)
_SCAN_SINGLE_FILES = (DBT_CHARTS_PKG_DIR / "cli" / "_error_format.py",)

# (relpath from DBT_CHARTS_PKG_DIR, line number) -> one-line reason the
# ``.as_posix()`` call is sanctioned. Ratchet — shrink, don't grow. Fixing a
# site means routing it through ``posix_relpath``/``.relpath`` and deleting
# the entry, not widening this allowlist to paper over a new ad-hoc call.
ALLOWED: dict[tuple[str, int], str] = {
    ("agent_api/describe.py", 203): (
        "describe_board() except ValueError: resolve_board_path failed, path "
        "never became a ProjectPath — no root to relativize against via "
        "posix_relpath. Echoes the raw caller input, POSIX-normalized for "
        "display consistency, not a resolved project identity."
    ),
    ("agent_api/describe.py", 349): (
        "_describe_one_path() except ValueError: same shape as "
        "describe_board() above — resolution failed, nothing to relativize."
    ),
    ("agent_api/validate.py", 147): (
        "_validate_one_path() except ValueError: resolve_board_path failed, "
        "raw_path never became a ProjectPath — no root to relativize "
        "against. Echoes the raw caller input, POSIX-normalized."
    ),
    ("agent_api/validate.py", 187): (
        "validate() except ValueError: same shape — resolution failed, "
        "nothing to relativize against."
    ),
    ("agent_api/mcp_install.py", 164): (
        "resolve_dct_executable(): venv_bin is a real filesystem executable "
        "path (.venv/bin/dct) relative to ai_config_root, for an MCP client "
        "config — not a project file identity. There is no ProjectPath and "
        "no posix_relpath root here by design."
    ),
}


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for scan_dir in _SCAN_DIRS:
        files.extend(sorted(scan_dir.rglob("*.py")))
    files.extend(_SCAN_SINGLE_FILES)
    return files


def _find_as_posix_calls(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relpath = path.relative_to(DBT_CHARTS_PKG_DIR).as_posix()
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "as_posix"
        ):
            hits.append((relpath, node.lineno))
    return hits


def test_as_posix_calls_match_reasoned_allowlist() -> None:
    violations: set[tuple[str, int]] = set()
    for py_file in _iter_py_files():
        violations.update(_find_as_posix_calls(py_file))

    assert violations == set(ALLOWED), (
        "A `.as_posix()` call was found outside the reasoned allowlist below "
        "(core/AGENTS.md 'File identity is the POSIX relpath'). Project "
        "identity strings are produced in exactly one place — "
        "`posix_relpath(path, root)` in core/project.py — or read directly "
        "off an already-held `ProjectPath.relpath`. Route the new site "
        "through one of those instead of open-coding `.as_posix()`, or add a "
        "reasoned ALLOWED entry if it is genuinely not a project identity "
        "(e.g. a raw-input echo on a resolution failure, or an unrelated "
        "filesystem path).\n"
        f"found: {sorted(violations)}\n"
        f"allowlisted: {sorted(ALLOWED)}"
    )

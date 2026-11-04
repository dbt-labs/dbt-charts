"""Guard: ``dbt_charts.core.compile`` must never import ``dbt_charts.core.render``.

Compile is the lower layer; render depends on compile, not the reverse.
``tach check`` enforces this structurally at CI time, but that requires the
``tach`` binary and a full-repo run. This is a fast, dependency-free regression
guard runnable via plain pytest — an AST walk over every ``.py`` file under
``core/compile/`` asserting no import resolves to ``dbt_charts.core.render``.
"""

from __future__ import annotations

import ast
from pathlib import Path

from .._paths import DBT_CHARTS_PKG_DIR

_COMPILE_DIR = DBT_CHARTS_PKG_DIR / "core" / "compile"
_FORBIDDEN_PREFIX = "dbt_charts.core.render"


def _render_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith(_FORBIDDEN_PREFIX)
            )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith(_FORBIDDEN_PREFIX):
                found.append(node.module)
    return found


def test_compile_resolve_has_no_board_baking_module() -> None:
    """compile/resolve/resolve_board.py must not exist after the render-layer relocation.

    resolve_board() board-baking logic belongs in render/board_resolve.py.
    RED: file still exists.  GREEN: file deleted.
    """
    resolve_board_path = _COMPILE_DIR / "resolve" / "resolve_board.py"
    assert not resolve_board_path.exists(), (
        "compile/resolve/resolve_board.py must be deleted after the render-layer relocation; "
        "board-baking logic lives in render/board_resolve.py"
    )


def test_compile_does_not_import_render() -> None:
    violations = {
        str(py_file.relative_to(DBT_CHARTS_PKG_DIR)): imports
        for py_file in sorted(_COMPILE_DIR.rglob("*.py"))
        if (imports := _render_imports(py_file))
    }
    assert not violations, (
        "core/compile/ must not import dbt_charts.core.render "
        f"(inverts the compile -> render layering): {violations}"
    )


def _imports_with_prefix(path: Path, prefix: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(
                alias.name for alias in node.names if alias.name.startswith(prefix)
            )
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.startswith(prefix):
                found.append(node.module)
    return found


def test_compile_dialect_and_sql_guard_edges_are_relocated() -> None:
    """Fast-follow to C1: the dialect/sql_guard/execution-error compile->execute
    edges (parameterized.py's get_dialect/VALID_OPERATORS,
    sql_authoring_lint.py's sql_guard +
    MutatingSqlError/UnparseableSqlError) were relocated below compile
    (dbt_charts.core.dialects, dbt_charts.core.compile.sql_guard,
    dbt_charts.core.diagnostics.execution). No compile file should import the old
    dbt_charts.core.execute.{dialects,sql_guard,errors} module paths anymore.

    The remaining compile->execute edges (DbtBackend/
    _read_target_dict in sources.py) are real adapter-class usage, not
    relocatable leaf behavior — still accepted debt, each carrying its own
    `# tach-ignore(...)`. normalize/queries.py's edge left with the
    MetricFlow lowering removal.
    """
    relocated_prefixes = (
        "dbt_charts.core.execute.dialects",
        "dbt_charts.core.execute.sql_guard",
        "dbt_charts.core.execute.errors",
    )
    violations = {
        str(py_file.relative_to(DBT_CHARTS_PKG_DIR)): imports
        for py_file in sorted(_COMPILE_DIR.rglob("*.py"))
        if (
            imports := [
                imp
                for prefix in relocated_prefixes
                for imp in _imports_with_prefix(py_file, prefix)
            ]
        )
    }
    assert not violations, (
        "core/compile/ must not import the relocated dialects/sql_guard/errors "
        f"modules from dbt_charts.core.execute: {violations}"
    )

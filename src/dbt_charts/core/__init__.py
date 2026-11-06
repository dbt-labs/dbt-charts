"""dbt charts Core Engine.

This module provides the core compilation, execution, and rendering
functionality for dbt charts dashboards.

Submodules:
    - compile/: Transform YAML → Board
    - execute/: Execute queries and fetch data
    - render/: Transform Board + data → output
    - serve/: HTTP server for dashboard serving
    - inspect/: Database inspection and profiling
    - validate: Validation utilities

High-Level API:
    >>> from pathlib import Path
    >>> from dbt_charts.core.compile import compile
    >>> from dbt_charts.core.execute.lookup import Ledger
    >>> from dbt_charts.core.execute.plan import make_context
    >>> from dbt_charts.core.execute.env import build_exec_env
    >>> from dbt_charts.core.render import render
    >>> result = compile(yaml_content)
    >>> from dbt_charts.cli.filesystem_project import FilesystemProject
    >>> env = build_exec_env(FilesystemProject(Path.cwd()))
    >>> ctx = make_context(result.board, registry, query_registry=result.query_registry)
    >>> html = render(result.board, ctx, Ledger(), format="html")
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Import-time-only: PEP 562 __getattr__ below resolves these lazily at
    # runtime, so `import dbt_charts.core.<anything>` (this file runs first,
    # always) doesn't eagerly pull in the whole compile/execute/render stack
    # just for this convenience re-export.
    from dbt_charts.core import validate as validate
    from dbt_charts.core.compile import (
        AnyQuery as AnyQuery,
        AuthoredBoard as AuthoredBoard,
        Board as Board,
        CompilationError as CompilationError,
        CompileResult as CompileResult,
        Layout as Layout,
        LayoutItem as LayoutItem,
        Variable as Variable,
        compile_file as compile_file,
    )
    from dbt_charts.core.execute import ExecutionError as ExecutionError
    from dbt_charts.core.render import RenderError as RenderError

# `compile` and `render` are deliberately NOT re-exported here: both names
# collide with a same-named submodule one level down (`dbt_charts.core.compile`,
# `dbt_charts.core.render`). Importing that submodule for *any* reason binds it
# onto this package under that exact name as a side effect of Python's own
# import machinery — a rebind PEP 562 __getattr__ cannot see or prevent — so a
# lazy alias sharing a submodule's leaf name silently flips from function to
# module depending on import order. Import the function directly from its
# submodule instead: `from dbt_charts.core.compile import compile`,
# `from dbt_charts.core.render import render`.
__all__ = [
    # Compile
    "compile_file",
    "CompileResult",
    "Board",
    "AnyQuery",
    "Layout",
    "LayoutItem",
    "AuthoredBoard",
    "Variable",
    "CompilationError",
    # Execute
    "ExecutionError",
    # Render
    "RenderError",
    # Validate
    "validate",
]

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "validate": ("dbt_charts.core", "validate"),
    "AnyQuery": ("dbt_charts.core.compile", "AnyQuery"),
    "AuthoredBoard": ("dbt_charts.core.compile", "AuthoredBoard"),
    "CompilationError": ("dbt_charts.core.compile", "CompilationError"),
    "CompileResult": ("dbt_charts.core.compile", "CompileResult"),
    "Board": ("dbt_charts.core.compile", "Board"),
    "Layout": ("dbt_charts.core.compile", "Layout"),
    "LayoutItem": ("dbt_charts.core.compile", "LayoutItem"),
    "Variable": ("dbt_charts.core.compile", "Variable"),
    "compile_file": ("dbt_charts.core.compile", "compile_file"),
    "ExecutionError": ("dbt_charts.core.execute", "ExecutionError"),
    "RenderError": ("dbt_charts.core.render", "RenderError"),
}


def __getattr__(name: str) -> Any:
    try:
        module_path, attr_name = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    if module_path == __name__ and attr_name == name:
        # "validate" is a submodule of this very package, not another
        # module's attribute — import it directly to avoid recursing back
        # into this __getattr__.
        module = importlib.import_module(f"{__name__}.{attr_name}")
    else:
        module = getattr(importlib.import_module(module_path), attr_name)
    globals()[name] = module  # cache: repeated access is O(1) and `is` identity holds
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))

"""dbt charts - dbt-native dashboard and visualization layer.

This package provides a declarative YAML-based approach to building
boards (analytics dashboards) that integrate with dbt projects.

Architecture:
    The codebase is organized into:

    - core/: The engine (compile, execute, render, serve, inspect)
    - cli/: Command-line interface
    - ai/: AI interfaces (MCP server, tool schemas, prompts)
    - apps/: Web applications (playground, cloud)

Quick Start:
    >>> from pathlib import Path
    >>> from dbt_charts import compile, Executor, render
    >>> from dbt_charts.core.execute.adapters import build_adapter_registry
    >>>
    >>> # Compile YAML to Board
    >>> result = compile(yaml_content)
    >>> if result.success:
    ...     board = result.board
    ...
    ...     # Create executor and render
    ...     from dbt_charts.cli.filesystem_project import FilesystemProject
    ...     registry = build_adapter_registry(FilesystemProject(Path.cwd()))
    ...     executor = Executor(board, registry, query_registry=result.query_registry)
    ...     svg = render(board, executor, format="svg")
"""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import version as _v
from typing import TYPE_CHECKING, Any

__version__ = _v("dbt-charts")

# Set up package-level logger
# This logger is the parent for all dbt_charts submodule loggers
logger = logging.getLogger("dbt_charts")

# Configure a NullHandler to avoid "No handler found" warnings
# Actual handlers should be configured by the application entry points (CLI, serve)
logger.addHandler(logging.NullHandler())

if TYPE_CHECKING:
    # Import-time-only: PEP 562 __getattr__ below resolves these lazily at
    # runtime, so `import dbt_charts` (or `import dbt_charts.<anything>`, which
    # always runs this file first) doesn't eagerly pull in the whole
    # compile/execute/render stack just for this convenience re-export.
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
        compile as compile,
        compile_file as compile_file,
    )
    from dbt_charts.core.execute import (
        ExecutionError as ExecutionError,
        Executor as Executor,
    )

    # `render` shadows `dbt_charts.render` module access — see _LAZY_ATTRS note below.
    from dbt_charts.core.render import RenderError as RenderError, render as render

__all__ = [
    # Version
    "__version__",
    # Logging
    "logger",
    # Compile
    "compile",
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
    "Executor",
    "ExecutionError",
    # Render
    "render",
    "RenderError",
    # Validate
    "validate",
]

# name -> (module dotted path, attribute name in that module). PEP 562
# __getattr__ below resolves these on first access instead of importing the
# entire compile/execute/render stack just because something did
# `import dbt_charts` or `import dbt_charts.<anything>` (this file runs first,
# always, regardless of which submodule was actually requested).
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
    "compile": ("dbt_charts.core.compile", "compile"),
    "compile_file": ("dbt_charts.core.compile", "compile_file"),
    "ExecutionError": ("dbt_charts.core.execute", "ExecutionError"),
    "Executor": ("dbt_charts.core.execute", "Executor"),
    # `render` shadows `dbt_charts.render` module access (pre-existing, not
    # introduced by laziness) — tests patching render internals import
    # dbt_charts.core.render.renderer directly instead.
    "RenderError": ("dbt_charts.core.render", "RenderError"),
    "render": ("dbt_charts.core.render", "render"),
}


def __getattr__(name: str) -> Any:
    try:
        module_path, attr_name = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_path)
    value = getattr(module, attr_name)
    globals()[name] = value  # cache: repeated access is O(1) and `is` identity holds
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))

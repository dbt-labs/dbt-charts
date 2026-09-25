"""dbt_charts.agent_api — typed Python API for dbt charts agent surfaces.

This package is the canonical home for every function exposed to AI agents
via CLI commands (``dbt_charts/cli/commands/``) or MCP server modules
(``dbt_charts/ai/mcp/``).

Contract
--------
Every function in this package must satisfy all four rules:

1. **Typed args and typed returns.** Function signatures use concrete Python
   types — ``Path``, ``str``, ``int``, Pydantic models, or ``TypedDict``
   subclasses. Return types are never ``dict[str, Any]`` or JSON strings.
   Callers should not need to parse, coerce, or guess the shape of results.

2. **No Cloud/Django assumptions.** Do not import ``django``, Django ORM
   models, ``HttpRequest``, ``settings``, or any ``apps.cloud`` module.
   Functions here must work in a standalone Python environment — a CLI
   session, a test, or an MCP server — without a running Django process.

3. **I/O scoped to the verb.** A ``render`` function does not write to disk.
   A ``save`` function writes exactly one file. An ``execute`` function runs
   SQL and nothing else. Side effects must match what the verb name implies
   and nothing more.

4. **Back-compat with the VS Code extension's LSP.** Anything
   ``apps/vscode-extension/server/dbt_charts_lsp`` imports from this package
   runs against whatever dbt-charts the user has installed, back to
   ``dbt_charts_lsp.MINIMUM_DBT_CHARTS_VERSION``. Add before you remove: an
   API the LSP needs ships in a dbt-charts release before the LSP starts
   calling it.

Thin-wrapper rule
-----------------
``dbt_charts/cli/commands/`` and ``dbt_charts/ai/mcp/`` are *thin wrappers* over
this package. They are permitted to contain only:

- Argument parsing and output formatting
- A single call into ``dbt_charts.agent_api``

Any business logic (validation, path resolution, compilation, execution,
rendering) belongs here, not in the CLI or MCP layer.  Code review blocks
CLI/MCP files that contain logic beyond parse-and-dispatch.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Import-time-only: PEP 562 __getattr__ below resolves these lazily at
    # runtime. Kept here so mypy/pyright still see concrete types instead of
    # falling back to Any on every re-export.
    from dbt_charts.agent_api import examples as examples, skills as skills
    from dbt_charts.agent_api._paths import (
        EditorCompileResult as EditorCompileResult,
        compile_editor_buffer as compile_editor_buffer,
    )
    from dbt_charts.agent_api.describe import (
        DescribeBoardArgs as DescribeBoardArgs,
        DescribeBoardResult as DescribeBoardResult,
        describe_board as describe_board,
    )
    from dbt_charts.agent_api.design import (
        DesignNode as DesignNode,
        DesignProperty as DesignProperty,
        DesignTarget as DesignTarget,
        build_design as build_design,
        build_design_target as build_design_target,
        design_target as design_target,
    )
    from dbt_charts.agent_api.docs import (
        DocsArgs as DocsArgs,
        DocsResult as DocsResult,
        DocsSearchHit as DocsSearchHit,
        Topic as Topic,
    )
    from dbt_charts.agent_api.import_closure import (
        board_import_closure as board_import_closure,
    )
    from dbt_charts.agent_api.init import (
        InitResult as InitResult,
        init_project as init_project,
    )
    from dbt_charts.agent_api.migrate import (
        MigrateError as MigrateError,
        MigrateNote as MigrateNote,
        MigrateSummary as MigrateSummary,
        migrate_paths as migrate_paths,
    )
    from dbt_charts.agent_api.project_session import ProjectSession as ProjectSession
    from dbt_charts.agent_api.query import (
        QueryBoardResult as QueryBoardResult,
        query_board as query_board,
    )
    from dbt_charts.agent_api.schema_hints import SchemaHints as SchemaHints
    from dbt_charts.agent_api.validate import (
        ValidateBoardArgs as ValidateBoardArgs,
        ValidateResult as ValidateResult,
    )
    from dbt_charts.agent_api.validate_query import QueryDiagnostic as QueryDiagnostic
    from dbt_charts.agent_api.warmup import warm_process as warm_process
    from dbt_charts.core.attribution import set_surface as set_surface
    from dbt_charts.core.board import (
        BoardRenderResult as BoardRenderResult,
        RenderFormat as RenderFormat,
        render_dashboard as render_dashboard,
    )
    from dbt_charts.core.compile.compiler import (
        CompileResult as CompileResult,
        compile as compile,
    )
    from dbt_charts.core.compile.config import (
        ProjectSourcesConfig as ProjectSourcesConfig,
    )
    from dbt_charts.core.dbt_ref_check import (
        QueryRefCalls as QueryRefCalls,
        extract_ref_calls as extract_ref_calls,
    )
    from dbt_charts.core.diagnostics import Diagnostic as Diagnostic
    from dbt_charts.core.execute.cache_backend import (
        QueryResultCache as QueryResultCache,
    )
    from dbt_charts.core.execute.observability import (
        WarehouseObserver as WarehouseObserver,
    )
    from dbt_charts.core.execute.source_resolver import (
        AllowlistedSourceResolver as AllowlistedSourceResolver,
        DefaultSourceResolver as DefaultSourceResolver,
        SourceResolver as SourceResolver,
    )
    from dbt_charts.core.fonts import get_fonts_dir as get_fonts_dir
    from dbt_charts.core.project import (
        BoardFile as BoardFile,
        InMemoryBoard as InMemoryBoard,
        Project as Project,
        posix_relpath as posix_relpath,
    )
    from dbt_charts.core.render.board_links import LinkContext as LinkContext
    from dbt_charts.core.render.errors import RenderError as RenderError
    from dbt_charts.core.render.svg_cache import (
        RenderedSvgCache as RenderedSvgCache,
    )

__all__ = [
    "compile",
    "CompileResult",
    "DescribeBoardArgs",
    "DescribeBoardResult",
    "Diagnostic",
    "DocsArgs",
    "DocsResult",
    "DocsSearchHit",
    "BoardFile",
    "InMemoryBoard",
    "InitResult",
    "LinkContext",
    "MigrateError",
    "MigrateNote",
    "MigrateSummary",
    "Project",
    "ProjectSession",
    "set_surface",
    "posix_relpath",
    "ProjectSourcesConfig",
    "QueryDiagnostic",
    "QueryBoardResult",
    "BoardRenderResult",
    "RenderError",
    "RenderFormat",
    "SchemaHints",
    "Topic",
    "ValidateBoardArgs",
    "ValidateResult",
    # Execution extension seams
    "AllowlistedSourceResolver",
    "DefaultSourceResolver",
    "QueryResultCache",
    "RenderedSvgCache",
    "SourceResolver",
    "WarehouseObserver",
    "DesignNode",
    "DesignProperty",
    "DesignTarget",
    "build_design",
    "build_design_target",
    "describe_board",
    "design_target",
    "get_fonts_dir",
    "init_project",
    "migrate_paths",
    "query_board",
    "QueryRefCalls",
    "extract_ref_calls",
    "render_dashboard",
    "board_import_closure",
    "compile_editor_buffer",
    "EditorCompileResult",
    "warm_process",
]

# `schema_hints` and `validate_query` (the functions) are deliberately NOT
# re-exported here: both names collide with their own defining submodule
# (`dbt_charts.agent_api.schema_hints`, `dbt_charts.agent_api.validate_query`).
# Importing that submodule for any reason binds it onto this package under
# that exact name as a side effect of Python's own import machinery — a
# rebind PEP 562 __getattr__ cannot see or prevent — so a lazy alias sharing
# a submodule's leaf name silently flips from function to module depending on
# import order. Import the function directly from its submodule instead:
# `from dbt_charts.agent_api.schema_hints import schema_hints`,
# `from dbt_charts.agent_api.validate_query import validate_query`.

# name -> (module dotted path, attribute name in that module; "" means the
# module itself). PEP 562 __getattr__ below resolves these on first access
# instead of importing every submodule (and the compile/execute/dbt_common
# stack several of them drag in) eagerly at `import dbt_charts.agent_api` time.
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "warm_process": ("dbt_charts.agent_api.warmup", "warm_process"),
    "board_import_closure": (
        "dbt_charts.agent_api.import_closure",
        "board_import_closure",
    ),
    "examples": ("dbt_charts.agent_api.examples", ""),
    "skills": ("dbt_charts.agent_api.skills", ""),
    "compile_editor_buffer": (
        "dbt_charts.agent_api._paths",
        "compile_editor_buffer",
    ),
    "EditorCompileResult": (
        "dbt_charts.agent_api._paths",
        "EditorCompileResult",
    ),
    "DescribeBoardArgs": ("dbt_charts.agent_api.describe", "DescribeBoardArgs"),
    "DescribeBoardResult": ("dbt_charts.agent_api.describe", "DescribeBoardResult"),
    "describe_board": ("dbt_charts.agent_api.describe", "describe_board"),
    "DesignNode": ("dbt_charts.agent_api.design", "DesignNode"),
    "DesignProperty": ("dbt_charts.agent_api.design", "DesignProperty"),
    "DesignTarget": ("dbt_charts.agent_api.design", "DesignTarget"),
    "build_design": ("dbt_charts.agent_api.design", "build_design"),
    "build_design_target": ("dbt_charts.agent_api.design", "build_design_target"),
    "design_target": ("dbt_charts.agent_api.design", "design_target"),
    "DocsArgs": ("dbt_charts.agent_api.docs", "DocsArgs"),
    "DocsResult": ("dbt_charts.agent_api.docs", "DocsResult"),
    "DocsSearchHit": ("dbt_charts.agent_api.docs", "DocsSearchHit"),
    "Topic": ("dbt_charts.agent_api.docs", "Topic"),
    "InitResult": ("dbt_charts.agent_api.init", "InitResult"),
    "init_project": ("dbt_charts.agent_api.init", "init_project"),
    "MigrateError": ("dbt_charts.agent_api.migrate", "MigrateError"),
    "MigrateNote": ("dbt_charts.agent_api.migrate", "MigrateNote"),
    "MigrateSummary": ("dbt_charts.agent_api.migrate", "MigrateSummary"),
    "migrate_paths": ("dbt_charts.agent_api.migrate", "migrate_paths"),
    "ProjectSession": ("dbt_charts.agent_api.project_session", "ProjectSession"),
    "QueryBoardResult": ("dbt_charts.agent_api.query", "QueryBoardResult"),
    "query_board": ("dbt_charts.agent_api.query", "query_board"),
    "SchemaHints": ("dbt_charts.agent_api.schema_hints", "SchemaHints"),
    "ValidateBoardArgs": ("dbt_charts.agent_api.validate", "ValidateBoardArgs"),
    "ValidateResult": ("dbt_charts.agent_api.validate", "ValidateResult"),
    "QueryDiagnostic": ("dbt_charts.agent_api.validate_query", "QueryDiagnostic"),
    "QueryRefCalls": ("dbt_charts.core.dbt_ref_check", "QueryRefCalls"),
    "extract_ref_calls": ("dbt_charts.core.dbt_ref_check", "extract_ref_calls"),
    "set_surface": ("dbt_charts.core.attribution", "set_surface"),
    "compile": ("dbt_charts.core.compile.compiler", "compile"),
    "CompileResult": ("dbt_charts.core.compile.compiler", "CompileResult"),
    "ProjectSourcesConfig": ("dbt_charts.core.compile.config", "ProjectSourcesConfig"),
    "BoardRenderResult": ("dbt_charts.core.board", "BoardRenderResult"),
    "RenderFormat": ("dbt_charts.core.render_format", "RenderFormat"),
    "render_dashboard": ("dbt_charts.core.board", "render_dashboard"),
    "Diagnostic": ("dbt_charts.core.diagnostics", "Diagnostic"),
    "QueryResultCache": ("dbt_charts.core.execute.cache_backend", "QueryResultCache"),
    "RenderedSvgCache": ("dbt_charts.core.render.svg_cache", "RenderedSvgCache"),
    "WarehouseObserver": (
        "dbt_charts.core.execute.observability",
        "WarehouseObserver",
    ),
    "AllowlistedSourceResolver": (
        "dbt_charts.core.execute.source_resolver",
        "AllowlistedSourceResolver",
    ),
    "DefaultSourceResolver": (
        "dbt_charts.core.execute.source_resolver",
        "DefaultSourceResolver",
    ),
    "SourceResolver": ("dbt_charts.core.execute.source_resolver", "SourceResolver"),
    "get_fonts_dir": ("dbt_charts.core.fonts", "get_fonts_dir"),
    "BoardFile": ("dbt_charts.core.project", "BoardFile"),
    "InMemoryBoard": ("dbt_charts.core.project", "InMemoryBoard"),
    "Project": ("dbt_charts.core.project", "Project"),
    "posix_relpath": ("dbt_charts.core.project", "posix_relpath"),
    "LinkContext": ("dbt_charts.core.render.board_links", "LinkContext"),
    "RenderError": ("dbt_charts.core.render.errors", "RenderError"),
}


def __getattr__(name: str) -> Any:
    try:
        module_path, attr_name = _LAZY_ATTRS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    module = importlib.import_module(module_path)
    value = module if not attr_name else getattr(module, attr_name)
    globals()[name] = value  # cache: repeated access is O(1) and `is` identity holds
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))

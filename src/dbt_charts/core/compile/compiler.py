"""AuthoredBoard compiler module.

Stage: COMPILE
Purpose: Compile YAML board definitions into Board objects
ready for execution and rendering.

Entry Points:
    - compile(yaml_content: str) -> CompileResult
    - compile_authored_board(authored: AuthoredBoard) -> CompileResult
    - compile_file(board: BoardFile) -> CompileResult

This is the main orchestrator for compilation. It:
1. Parses YAML to AuthoredBoard (parse/parser.py)
2. Validates the board structure (validate/dispatch.py)
3. Normalizes references and adds metadata (normalize/dispatch.py)

Layout dimensions are calculated later in the render pipeline (after query
execution) so table heights can use actual row counts.

Dependencies:
    - .parse.parser (parse_yaml)
    - .validate.dispatch (validate_board)
    - .normalize.dispatch (normalize_board)
    - .jinja (detect_query_dependencies)
    - .errors (CompilationError, etc.)

See also:
    - execute/executor.py for the next stage
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError as PydanticValidationError

from dbt_charts.core.compile.config import (
    ProjectSourcesConfig,
    resolve_html_policy_ceiling,
)
from dbt_charts.core.compile.errors import (
    CompilationError,
    MergeValidationError,
    ParseError,
    ReferenceError,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.cache import (
    INHERIT_CACHE,
    CachePatch,
    merge_cache_layers,
)
from dbt_charts.core.compile.models.config import ProjectCacheConfig
from dbt_charts.core.compile.models.primitives import IncrementalValue
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.compile.models.refs import (
    ChartRef,
    QueryRef,
    VariableRef,
    normalize_query_value,
)
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.dispatch import (
    board_style_cache,
    normalize_board,
    normalize_query,
)
from dbt_charts.core.compile.normalize.queries import (
    dialect_for_source,
    split_external_query_ref,
)
from dbt_charts.core.compile.normalize.variables import VariableReferenceErrors
from dbt_charts.core.compile.parse.meta import (
    MetaLintConfig,
    resolve_meta_lint,
)
from dbt_charts.core.compile.parse.parser import (
    load_yaml_mapping,
    looks_like_sql,
    parse_mapping,
    parse_yaml,
)
from dbt_charts.core.compile.parse.source_map import (
    LiteralBlock,
    build_source_index,
    stamp_diagnostics,
)
from dbt_charts.core.compile.parse.yaml_error_formatter import (
    format_validation_errors_structured,
)
from dbt_charts.core.compile.template.jinja import detect_query_dependencies
from dbt_charts.core.compile.validate.authoring_warnings import (
    YamlValue,
    detect_authoring_warnings,
)
from dbt_charts.core.compile.validate.board_warnings import detect_board_warnings
from dbt_charts.core.compile.validate.dispatch import validate_board
from dbt_charts.core.compile.validate.formats import validate_board_format_specs
from dbt_charts.core.compile.validate.links import validate_board_links
from dbt_charts.core.compile.validate.palettes import validate_board_palette_specs
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_EMPTY_YAML_DOCUMENT,
    ERR_UNKNOWN_QUERY,
    WARN_HTML_POLICY_CAPPED,
)
from dbt_charts.core.diagnostics.codes_query import WARN_PARSE_ERROR
from dbt_charts.core.diagnostics.diagnostic import SourceRange
from dbt_charts.core.diagnostics.from_query_diagnostic import from_query_diagnostic

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from dbt_charts.core.inspect.query_validator import (
        QueryDiagnostic,
        RelationshipContext,
    )
    from dbt_charts.core.project import BoardFile, ProjectDirectory, ProjectPath


def _to_plain_dict(obj: Any) -> Any:
    """Recursively convert DotDict (or any dict subclass) to plain dict."""
    if isinstance(obj, dict):
        return {k: _to_plain_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain_dict(v) for v in obj]
    return obj


@dataclass
class CompileResult:
    """Result of compilation.

    Contains the compiled board (if successful), any errors encountered,
    and warnings that don't prevent compilation.

    Attributes:
        board: Compiled board (None if errors occurred)
        errors: Compile errors as ``Diagnostic`` objects (union-aware collapse applied)
        warnings: Non-fatal warnings as ``Diagnostic`` objects with stable
            ``WARN-*`` codes. Each emitter (orphan-chart check,
            validate_compiled_queries) uses a ``WarningCode`` constant from
            ``dbt_charts.core.diagnostics``.
        suppressed_warnings: ``Diagnostic`` entries that matched a
            suppression layer (query-level ``ignore:`` or meta.yml lint
            config). Kept separate so consumers can surface "would have
            warned" without re-running validation.
        diagnostics: Structured query diagnostics from validate_compiled_queries.
            Includes all severity levels; ``warnings`` only includes warning+error.
            Infrastructure for structured consumers (UI, AI agents).
        query_registry: All normalized queries (for executor)
        sources: Source configurations (for resolving named source references)
        source_map: Dotted-path -> SourceRange built once from the authored
            YAML text (empty when no file identity was available to compile —
            see ``compile_authored_board``'s ``file`` argument). Render-side
            diagnostics (chart-render errors, render warnings, query
            diagnostics) reuse this same map via ``stamp_diagnostics`` instead
            of each doing its own position resolution.
        container_paths: Dotted paths addressing a mapping/sequence rather
            than a scalar, built alongside ``source_map``. Lets the stamping
            pass collapse a container's whole-body range to its key line, so a
            diagnostic about a chart marks the line naming it instead of every
            line inside. Carried on the result for the same reason as
            ``sql_blocks``: render-side stamping reuses it without recompiling.
        sql_blocks: Dotted-path -> LiteralBlock for every literal (``|``)
            block scalar, built alongside ``source_map``. Render-side
            diagnostics pass this to ``stamp_diagnostics`` too, so a query
            error carrying sqlglot's SQL-local position
            (``UnparseableSqlError``) narrows from the whole ``sql:`` block
            down to the offending token.

    Example:
        >>> result = compile(yaml_content)
        >>> if result.success:
        ...     print(f"Compiled: {result.board.title}")
        ... else:
        ...     for error in result.errors:
        ...         print(f"Error: {error}")
    """

    board: Board | None = None
    errors: list[Diagnostic] = field(default_factory=list)
    warnings: list[Diagnostic] = field(default_factory=list)
    suppressed_warnings: list[Diagnostic] = field(default_factory=list)
    diagnostics: list[QueryDiagnostic] = field(default_factory=list)
    query_registry: dict[str, AnyQuery] = field(default_factory=dict)
    meta_lint: MetaLintConfig | None = None
    source_map: dict[str, SourceRange] = field(default_factory=dict)
    sql_blocks: dict[str, LiteralBlock] = field(default_factory=dict)
    container_paths: frozenset[str] = frozenset()

    @property
    def success(self) -> bool:
        """Check if compilation was successful."""
        return self.board is not None and len(self.errors) == 0


def _stamp_compile_result(
    result: CompileResult, yaml_content: str, file: str | None
) -> CompileResult:
    """Build the source index from the authored text and stamp every
    diagnostic's ``.range`` from it, mutating ``result`` in place.

    Without a file identity there is nowhere for a ``SourceRange`` to point
    (``SourceRange.file`` is required, never a sentinel), so ``source_map``
    stays empty and every diagnostic keeps ``range=None`` — the same
    "unresolved" outcome as any other path the map doesn't cover, not a
    special case.
    """
    if file is None:
        return result
    source_map, sql_blocks, containers = build_source_index(yaml_content, file)
    result.source_map = source_map
    result.sql_blocks = sql_blocks
    result.container_paths = containers
    stamp_diagnostics(result.errors, source_map, sql_blocks, containers)
    stamp_diagnostics(result.warnings, source_map, sql_blocks, containers)
    stamp_diagnostics(result.suppressed_warnings, source_map, sql_blocks, containers)
    return result


def _set_reference_error_field_path(e: ReferenceError, authored: AuthoredBoard) -> None:
    """Set `field_path` on a ReferenceError from its `ref_path` (e.g.
    `["charts", "rev_chart"]`), when that path names a section/key actually
    authored at this board's top level.

    The single enrichment used by all three `except ReferenceError` sites
    (chart / query / variable registry builds), covering every section a
    cross-board ref can land on (`charts:`/`queries:`/`variables:`). Only sets
    `field_path`; `Diagnostic.range` resolves later, centrally, from
    `field_path` via `_stamp_compile_result`'s source map, so no line lookup
    happens here.
    """
    if not e.ref_path or len(e.ref_path) < 2:
        return
    sections = {
        "charts": authored.charts,
        "queries": authored.queries,
        "variables": authored.variables,
    }
    section = sections.get(e.ref_path[0])
    if isinstance(section, dict) and e.ref_path[1] in section:
        e.field_path = ".".join(e.ref_path)


def compile_authored_board(
    authored: AuthoredBoard,
    base_dir: ProjectDirectory | None = None,
    project_sources: ProjectSourcesConfig | None = None,
    project_cache: ProjectCacheConfig | None = None,
    host_default_source: str | None = None,
    _yaml_content: str = "",
    file: str | None = None,
) -> CompileResult:
    """Compile an already-parsed AuthoredBoard, skipping YAML parse.

    This is the canonical compilation pipeline (steps 2-6). ``compile()``
    delegates here after parsing; callers that already hold an ``AuthoredBoard``
    (e.g. the registered-view expansion path) call this directly.

    Args:
        authored: Parsed board from the registered-view expander.
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        project_sources: Project-level sources config.
        project_cache: The project's `cache:` block — the root of the cache
            cascade, threaded per compile rather than read from the process
            global (see `normalize_query`'s `cache_root`). None means no project
            is attached, whose root is the shipped default.
        _yaml_content: Original YAML text, used only to produce richer
            ``format_validation_errors_structured`` output on PydanticValidationError.
            Pass the empty string (default) for programmatically-generated boards.
        file: The board's project-relative path, when known at call time.
            Passed straight through to every ``Diagnostic`` construction below
            so file and line are always resolved together — never ``None`` for
            ``compile_file()`` callers, always ``None`` for standalone
            ``compile()`` callers with no file identity.

    Returns:
        CompileResult with the compiled board or validation errors.
    """
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    # ════════════════════════════════════════════════════════════════════
    # STEP 2: Validate Structure
    # ════════════════════════════════════════════════════════════════════
    validation_errors = validate_board(authored)
    if validation_errors:
        for error in validation_errors:
            if error.code is not ERR_UNKNOWN_QUERY:
                continue
            chart_name = error.fields["chart_name"]
            if authored.charts is None or chart_name not in authored.charts:
                continue
            ref_path = ["charts", chart_name, "query"]
            error.field_path = ".".join(ref_path)
        return CompileResult(
            errors=[e.to_diagnostic(file=file) for e in validation_errors]
        )

    # ════════════════════════════════════════════════════════════════════
    # STEP 3: Get Default Source and Extract Named Sources
    # ════════════════════════════════════════════════════════════════════
    # Default source comes from board/meta source: inheritance (normalize/dispatch.py).
    # The project-level sources.default config key has been removed; a host that
    # compiles composed/standalone content (e.g. the playground scratch endpoint,
    # which has no charts/meta.yml to inherit) may supply host_default_source as
    # the fallback default for that context.
    default_source = authored.get_default_source() or host_default_source

    sources_registry: dict[str, Any] = {}
    if project_sources is not None:
        sources_registry.update(project_sources.sources)

    # ════════════════════════════════════════════════════════════════════
    # STEP 3b: Build Chart Registry (moved ahead of queries — Surface B's
    # collect pass below needs charts to find dotted-ref channel bindings
    # before semantic queries lower to SQL).
    # ════════════════════════════════════════════════════════════════════
    # Pool that imported charts' own query chains resolve into, lexically
    # scoped to each chart's source board (compile/AGENTS.md's "Cross-board
    # chart imports scope queries lexically" invariant). Seeds the query
    # registry built in STEP 4 below, so a pulled-in query is visible
    # everywhere a locally-declared one would be — including
    # `validate_variable_references`, which is what makes an importer's
    # missing variable declaration a hard compile error for free.
    cross_board_queries: dict[str, AnyQuery] = {}
    try:
        chart_registry = build_chart_registry(
            authored,
            base_dir=base_dir,
            cross_board_queries=cross_board_queries,
            sources=sources_registry,
            cache_root=project_cache,
        )
    except ReferenceError as e:
        _set_reference_error_field_path(e, authored)
        return CompileResult(errors=[e.to_diagnostic(file=file)])
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])

    # ════════════════════════════════════════════════════════════════════
    # STEP 4: Build Query Registry
    # ════════════════════════════════════════════════════════════════════
    try:
        query_registry = build_query_registry(
            authored,
            base_dir,
            registry=cross_board_queries,
            default_source=default_source,
            sources=sources_registry,
            cache_root=project_cache,
        )
    except ReferenceError as e:
        _set_reference_error_field_path(e, authored)
        return CompileResult(errors=[e.to_diagnostic(file=file)])
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])

    # ════════════════════════════════════════════════════════════════════
    # STEP 5: Detect Circular Dependencies
    # ════════════════════════════════════════════════════════════════════
    if query_registry:
        try:
            detect_query_dependencies(query_registry)
        except CompilationError as e:
            return CompileResult(errors=[e.to_diagnostic(file=file)])

    # ════════════════════════════════════════════════════════════════════
    # STEP 6: Normalize (Transform to Board)
    # ════════════════════════════════════════════════════════════════════
    try:
        with board_style_cache():
            compiled = normalize_board(
                authored,
                # Thread the resolved default source (board/meta inheritance, or a
                # host-supplied fallback) so inline chart queries synthesized during
                # normalization inherit it too — not just the named queries built
                # above. `sources` (board-global named source configs) rides
                # alongside so an inline chart query resolves its source just like
                # an up-top query.
                parent_context={
                    "default_source": default_source,
                    "sources": sources_registry,
                    "cache_root": project_cache,
                },
                query_registry=query_registry,
                chart_registry=chart_registry,
                base_dir=base_dir,
            )
    except VariableReferenceErrors as e:
        # normalize_board raises this after building the board so it can report
        # all undefined-variable refs at once.
        errors.extend(e.diagnostics)
        return CompileResult(errors=errors)
    except ReferenceError as e:
        _set_reference_error_field_path(e, authored)
        return CompileResult(errors=[e.to_diagnostic(file=file)])
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])
    except PydanticValidationError as e:
        return CompileResult(
            errors=format_validation_errors_structured(e, _yaml_content)
        )

    compiled.sources = _to_plain_dict(sources_registry)

    if authored.html_policy != compiled.html_policy:
        ceiling = resolve_html_policy_ceiling()
        warnings.append(
            Diagnostic.from_code(
                WARN_HTML_POLICY_CAPPED,
                message=WARN_HTML_POLICY_CAPPED.message_template.format(
                    requested=authored.html_policy,
                    ceiling_source=ceiling.source,
                    ceiling=compiled.html_policy,
                    effective=compiled.html_policy,
                ),
                fix=WARN_HTML_POLICY_CAPPED.fix_template.format(
                    effective=compiled.html_policy,
                ),
            )
        )

    # Format specs: every authored format: string must resolve through the
    # alias table, the native (non-d3) formatter keys, or d3_format.parse —
    # a typo like `percent_1` fails here instead of surfacing as ERR-INTERNAL
    # deep inside rasterization (see validate/formats.py).
    try:
        validate_board_format_specs(compiled)
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])

    # Link fields: a chart/table-column/footer link's scheme must be a board
    # path, http, https, or mailto (see validate/links.py).
    try:
        validate_board_links(compiled)
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])

    # Palette fields: a theme-scoped role name or a typo left unresolved by
    # the model validators' deferral (see validate/palettes.py) must not
    # reach render as a bare string.
    try:
        validate_board_palette_specs(compiled)
    except CompilationError as e:
        return CompileResult(errors=[e.to_diagnostic(file=file)])

    warnings.extend(detect_board_warnings(compiled))
    authored_queries = authored.queries
    warnings.extend(
        _sql_parse_warnings(
            query_registry, set(authored_queries) if authored_queries else set()
        )
    )

    return CompileResult(
        board=compiled,
        errors=errors,
        warnings=warnings,
        query_registry=query_registry,
    )


def compile(
    yaml_content: str,
    options: dict[str, Any] | None = None,
    base_dir: ProjectDirectory | None = None,
    project_sources: ProjectSourcesConfig | None = None,
    project_cache: ProjectCacheConfig | None = None,
    host_default_source: str | None = None,
    file: str | None = None,
) -> CompileResult:
    """Compile YAML content to a Board.

    Stage: COMPILE (Full Pipeline)

    This is the main entry point for compilation. It orchestrates:
    1. PARSE: YAML string → AuthoredBoard object
    2–6. Delegate to ``compile_authored_board`` (validate → normalize)

    Layout sizing happens later in the render pipeline (data-aware).

    Args:
        yaml_content: YAML string to compile
        options: Optional compilation options
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        project_sources: Project-level sources.
        project_cache: The project's `cache:` block — see
            `compile_authored_board`.
        file: The board's project-relative path, when the caller has one (most
            standalone callers do not — pass None). ``compile_file`` passes its
            own path directly to ``_compile_with_text`` instead of through here.

    Returns:
        CompileResult with compiled board or errors

    Example:
        >>> yaml_content = '''
        ... title: My dbt charts
        ... queries:
        ...   users: SELECT * FROM users
        ... charts:
        ...   user_count:
        ...     query: users
        ...     type: kpi
        ...     value: count
        ... rows:
        ...   - user_count
        ... '''
        >>> result = compile(yaml_content)
        >>> if result.success:
        ...     board = result.board
        ...     print(board.title)  # "My dbt charts"
    """
    options = options or {}

    # ════════════════════════════════════════════════════════════════════
    # STEP 1: Parse YAML
    # ════════════════════════════════════════════════════════════════════
    # Convert YAML string to AuthoredBoard object. Handles syntax errors.
    try:
        board = parse_yaml(yaml_content)
    except ParseError as e:
        return _stamp_compile_result(
            CompileResult(
                errors=_parse_error_to_diagnostics(e, yaml_content, file=file)
            ),
            yaml_content,
            file,
        )

    # ════════════════════════════════════════════════════════════════════
    # STEPS 2–6: Delegate to shared pipeline
    # ════════════════════════════════════════════════════════════════════
    return _compile_with_text(
        board,
        yaml_content,
        base_dir=base_dir,
        project_sources=project_sources,
        project_cache=project_cache,
        meta_lint=options.get("meta_lint"),
        host_default_source=host_default_source,
        file=file,
    )


def _parse_error_to_diagnostics(
    e: ParseError, yaml_content: str, *, file: str | None = None
) -> list[Diagnostic]:
    """Convert a ParseError into Diagnostics.

    When the cause is a PydanticValidationError, use the union-aware collapse to
    produce Diagnostics from the raw Pydantic error list. An empty-document
    error is rebuilt here with the file name filled in — parser.py raises it
    from bare content, with no board identity to name. Every other parse error
    (bad YAML syntax) produces one Diagnostic straight from ``e``.
    """
    if isinstance(e.__cause__, PydanticValidationError):
        return format_validation_errors_structured(e.__cause__, yaml_content)
    if e.code is ERR_EMPTY_YAML_DOCUMENT:
        named = DbtChartsError.from_code(
            ERR_EMPTY_YAML_DOCUMENT,
            # `file` is genuinely absent for a caller with no board identity
            # (raw content, stdin) — not a caller bug to raise on.
            path=file
            or "the given YAML content",  # type-state: silent_fallback — see above
        )
        return [named.to_diagnostic(file=file)]
    return [e.to_diagnostic(file=file)]


def _compile_with_text(
    board: AuthoredBoard,
    yaml_content: str,
    base_dir: ProjectDirectory | None,
    project_sources: ProjectSourcesConfig | None,
    project_cache: ProjectCacheConfig | None,
    meta_lint: MetaLintConfig | None,
    host_default_source: str | None = None,
    file: str | None = None,
) -> CompileResult:
    """Run the shared compile pipeline for an already-parsed board.

    ``yaml_content`` is the original authored text — used only for YAML-text
    authoring-warning analysis (not available to ``compile_authored_board``,
    which may receive a programmatically-built or meta-merged board).
    """
    authoring_warnings = detect_authoring_warnings(yaml_content)
    result = compile_authored_board(
        board,
        base_dir=base_dir,
        project_sources=project_sources,
        project_cache=project_cache,
        host_default_source=host_default_source,
        _yaml_content=yaml_content,
        file=file,
    )
    result.warnings = authoring_warnings + result.warnings
    result.meta_lint = meta_lint
    # Layout dimensions are calculated later in the render pipeline
    # (after query execution) so table heights can use actual row counts.
    return _stamp_compile_result(result, yaml_content, file)


def _sql_parse_warnings(
    query_registry: dict[str, AnyQuery], authored_query_names: set[str]
) -> list[Diagnostic]:
    """WARN-PARSE-ERROR for every authored query the guard could not parse.

    ``normalize_query`` already ran the guard (skeletonizing Jinja, using the
    source's own dialect) and stashed the finding on the query — this is the
    harvest, not a second parse. It only sees failures the guard judged
    specific enough to report; the vaguer ones stay deferred and silent.

    The SQL-local position rides along on ``fields`` so ``stamp_diagnostics``
    can offset it into the board file and underline the offending token. At
    compile time the authored text *is* what the guard parsed (no variables
    substituted yet), so that translation lands exactly — unless Jinja made
    the skeleton differ, in which case the whole block is marked instead.

    Restricted to names the author actually wrote. By the time this runs,
    ``normalize_board`` has added its own entries to the same registry —
    inline chart queries as ``_inline_query_<chart>``, promoted variable
    options as ``_var_options_<var>``. Those names have no ``queries.<name>``
    node to resolve against, so they would produce a position-less warning
    quoting an identifier the author never typed. They still fail at execute
    time through ERR-UNPARSEABLE-SQL; giving them a compile-time home means
    carrying their authored location from the point of synthesis, which is
    its own change.
    """
    warnings: list[Diagnostic] = []
    for name, query in query_registry.items():
        if name not in authored_query_names:
            continue
        if not is_sql_query(query) or query.parse_error is None:
            continue
        position = query.parse_error.position
        warnings.append(
            Diagnostic.from_code(
                WARN_PARSE_ERROR,
                message=WARN_PARSE_ERROR.message_template.format(
                    query_name=name, message=query.parse_error.message
                ),
                fix=WARN_PARSE_ERROR.fix_template,
                path=f"queries.{name}.sql",
                query=name,
                fields={
                    "sql_line": position.line,
                    "sql_start_col": position.start_col,
                    "sql_end_col": position.end_col,
                    "sql_line_text": position.line_text,
                },
            )
        )
    return warnings


def _collect_suppressed_codes(
    result: CompileResult,
    meta_lint: MetaLintConfig | None = None,
) -> dict[str, set[str]]:
    """Build per-query suppression sets from query ignore + meta.yml lint config.

    Returns a dict mapping query_name → set of suppressed diagnostic codes.
    """
    per_query: dict[str, set[str]] = {}
    for name, query in result.query_registry.items():
        codes: set[str] = set()
        if query.ignore:
            codes.update(query.ignore)
        per_query[name] = codes

    if meta_lint is not None:
        global_ignore = set(meta_lint.ignore)
        per_query_meta = meta_lint.ignore_queries
        for name in per_query:
            per_query[name] |= global_ignore
            if name in per_query_meta:
                per_query[name].update(per_query_meta[name])

    return per_query


def validate_compiled_queries(
    result: CompileResult,
    relationship_context: RelationshipContext | None = None,
) -> None:
    """Run query validation on all SQL queries in a compile result (mutates in place).

    Calls ``validate_query`` for each authored SQL query, appends structured
    ``QueryDiagnostic`` objects to ``result.diagnostics``, and emits
    ``Diagnostic`` entries onto ``result.warnings`` for findings at
    warning severity or above. Each warning carries a stable ``WARN-*``
    code from ``dbt_charts.core.diagnostics`` (see
    ``from_query_diagnostic`` for the mapping).

    Stamps all warnings and suppressed_warnings in place after linting, so
    callers do not need to call ``stamp_diagnostics`` separately for lint
    findings (idempotent; no-op when ``result.source_map`` is empty).

    Honors diagnostic suppressions from:
    - SQL-inline ``-- dct:ignore`` comments (handled by validate_query)
    - Per-query ``ignore`` field
    - meta.yml ``lint`` config

    Args:
        result: CompileResult to enrich.
        relationship_context: Optional RelationshipContext for severity calibration.
    """
    if not result.success or result.board is None or not result.query_registry:
        return

    # tach-ignore(pre-existing compile->inspect coupling — accepted debt)
    from dbt_charts.core.inspect.query_validator import validate_query

    sources = result.board.sources
    suppressed = _collect_suppressed_codes(result, result.meta_lint)

    for name, query in result.query_registry.items():
        if name.startswith("_"):
            # Synthetic names (_inline_query_*, _var_options_*) have no
            # queries.<name> YAML node, so warnings would cite an identifier
            # the author never typed. from_query_diagnostic also sets no
            # query= field for these entries (plumbing limit, not physics).
            continue
        if not is_sql_query(query):
            continue
        # Skip queries whose raw SQL contains unrendered Jinja — sqlglot
        # receives the template, not the rendered form:
        #   parse_error         — parse cost short-circuit; WARN-PARSE-ERROR is
        #                         filtered below regardless, so output is unchanged
        #   variable_dependencies — {{ authored_var }} not yet substituted
        #                           (dbt builtins such as {{ ref() }} are stripped
        #                            by _DBT_BUILTIN_CALLS; they reach validate_query
        #                            but WARN-PARSE-ERROR is filtered below)
        if query.parse_error is not None or query.variable_dependencies:
            continue
        query_suppress = suppressed.get(name, set())
        dialect = dialect_for_source(query.source, sources)
        diags, suppressed_diags = validate_query(
            query.sql,
            dialect=dialect,
            relationship_context=relationship_context,
            suppress=query_suppress,
            return_suppressed=True,
        )
        # _sql_parse_warnings owns WARN-PARSE-ERROR with the calibrated dialect
        # gate (only fires when the source dialect is known and the position is
        # specific). Filtering here prevents false positives when dialect=None
        # (dbt_profile / csv / json / parquet / http sources) causes a parse
        # failure on SQL that is valid in the actual warehouse dialect.
        # WARN-PARSE-ERROR is in _UNSUPPRESSIBLE_CODES so it never appears in
        # suppressed_diags — only the diags filter is reachable.
        diags = [d for d in diags if d.code != WARN_PARSE_ERROR.code]
        for d in diags:
            result.diagnostics.append(d)
            if d.severity in ("error", "warning"):
                result.warnings.append(from_query_diagnostic(name, d))
        for d in suppressed_diags:
            result.suppressed_warnings.append(from_query_diagnostic(name, d))

    stamp_diagnostics(
        result.warnings, result.source_map, result.sql_blocks, result.container_paths
    )
    stamp_diagnostics(
        result.suppressed_warnings,
        result.source_map,
        result.sql_blocks,
        result.container_paths,
    )


def compile_file(
    board: BoardFile,
    apply_meta: bool = True,
    *,
    markdown_metadata_table: bool = False,
) -> CompileResult:
    """Compile a board's content to a Board, applying the meta.yml cascade.

    Args:
        board: BoardFile binding the raw content to its ProjectPath location. The
            location anchors the meta.yml cascade, relative refs, and error
            paths; the content is compiled directly (no read), so an unsaved
            buffer compiles the same as a stored blob. Requires ``board.path``
            to be set — ``compile_file`` is the cascade entry, which only
            makes sense for a located board.
        apply_meta: If True, resolve and apply meta.yml chain (default: True)
        markdown_metadata_table: When True and the board is a .md, prepend
            non-board frontmatter keys as a metadata table before the body.

    Returns:
        CompileResult with compiled board or errors

    Raises:
        ValueError: if ``board.path`` is None.

    Example:
        >>> result = compile_file(project.path("charts/overview.yml").read_board())
        >>> if result.success:
        ...     print(result.board.title)
    """
    board_path = board.path
    if board_path is None:
        raise ValueError("compile_file requires a located board")
    project = board_path.project
    relpath = board_path.relpath

    # Markdown report files: translate to YAML before compiling.
    from dbt_charts.core.compile.parse.markdown import (
        MARKDOWN_NOT_BOARD_MESSAGE,
        is_markdown_board_content,
        markdown_to_yaml,
    )

    # tach-ignore(pre-existing compile->project coupling — accepted debt)
    from dbt_charts.core.project import CHARTS_SUBDIR

    if board_path.is_markdown:
        raw_text = board.content
        # "In charts" means a charts/ directory *within* the project — the file's
        # project-relative path. The project's own on-disk location is irrelevant.
        in_boards = CHARTS_SUBDIR in PurePosixPath(relpath).parts
        if not is_markdown_board_content(raw_text, in_boards=in_boards):
            return CompileResult(
                errors=[
                    CompilationError(MARKDOWN_NOT_BOARD_MESSAGE).to_diagnostic(
                        file=relpath
                    )
                ]
            )

        try:
            yaml_content = markdown_to_yaml(
                raw_text, metadata_table=markdown_metadata_table
            )
        except (OSError, ValueError) as e:
            return CompileResult(
                errors=[
                    CompilationError(f"Markdown parse error: {e}").to_diagnostic(
                        file=relpath
                    )
                ]
            )
    else:
        yaml_content = board.content

    # Parse the board text to a mapping. Meta + extends patches are merged under
    # the board via the resolution engine (merge_patches semantics: lists APPEND,
    # nested structures merge recursively). Lint is extracted separately from the
    # meta chain so it still reaches result.meta_lint.
    meta_lint: MetaLintConfig | None = None
    try:
        board_data = load_yaml_mapping(yaml_content)
        from dbt_charts.core.compile.migrations import prepare_board_mapping

        board_data = prepare_board_mapping(board_data)
        if apply_meta:
            from dbt_charts.core.compile.merge import merged_patch
            from dbt_charts.core.compile.models.board.patch import (
                BOARD_PATCH_ADAPTER,
            )

            # --- Lint extraction (dict-level walk of meta files) ---
            meta_lint = resolve_meta_lint(board_path, project.directory("."))

            # --- Content merge via the resolution engine ---
            # The full board validates as a BoardPatch (the all-optional overlay of
            # AuthoredBoard), so merged_patch folds the meta chain + extends chain
            # *under* the board's own fields in one pass:
            #     meta (root→leaf) < board extends chain < board own fields
            # Merge strategy per field comes from each Merge(...) marker via
            # merge_patches — the single source of truth, no dict-level duplicate.
            board_node = BOARD_PATCH_ADAPTER.validate_python(board_data)
            theme_sink: list[str] = []
            merged = merged_patch(
                board_node, board_path, project.directory("."), theme_sink
            )
            merged_board_data = merged.model_dump(exclude_unset=True)
            # Identity + routing fields are dropped during merge so they never
            # inherit from meta/extends. id and aliases are re-attached so
            # parse_mapping can identify this board; _schema_version (the YAML
            # key -- AuthoredBoard.schema_version's alias) carries no such role
            # (nothing reads it back) but is identity-scoped to this file the
            # same way, so it belongs in the same set.
            for k in ("id", "aliases", "_schema_version"):
                if k in board_data:
                    merged_board_data[k] = board_data[k]
            # Inject the SELECTED chain-wide base theme (incl. meta-level themes
            # the old raw re-attach missed) so the normalizer applies it exactly
            # once via get_theme_style(). This REPLACES the old band-aid.
            effective_theme = theme_sink[-1] if theme_sink else None
            if effective_theme is not None:
                merged_board_data["extends"] = effective_theme
            board_data = merged_board_data
        authored_board = parse_mapping(board_data, yaml_content)
    except ParseError as e:
        return _stamp_compile_result(
            CompileResult(
                errors=_parse_error_to_diagnostics(e, yaml_content, file=relpath)
            ),
            yaml_content,
            relpath,
        )
    except MergeValidationError as e:
        # Extends/meta validation errors already carry structured diagnostics with hints.
        return _stamp_compile_result(
            CompileResult(errors=e.merge_diagnostics), yaml_content, relpath
        )
    except CompilationError as e:
        return _stamp_compile_result(
            CompileResult(errors=[e.to_diagnostic(file=relpath)]), yaml_content, relpath
        )
    except PydanticValidationError as e:
        # Catches errors from the BeforeValidator that desugars theme: -> extends:
        # (e.g. theme: + extends: conflict), raised before parse_mapping runs.
        return _stamp_compile_result(
            CompileResult(errors=format_validation_errors_structured(e, yaml_content)),
            yaml_content,
            relpath,
        )

    return _compile_with_text(
        authored_board,
        yaml_content,
        base_dir=board_path.parent,
        project_sources=project.sources,
        project_cache=project.cache,
        meta_lint=meta_lint,
        file=relpath,
    )


def _extract_nested_boards(board: AuthoredBoard) -> list[AuthoredBoard]:
    """Extract all nested boards from a board's layout.

    Recursively traverses rows, cols, grid, and tabs to find all nested boards.

    Args:
        board: AuthoredBoard to extract nested boards from

    Returns:
        List of nested AuthoredBoard objects found in the layout
    """
    from dbt_charts.core.compile.models.board.authored import TabItem

    nested_boards: list[AuthoredBoard] = []
    items: list[Any] = []

    # Collect items from rows and cols
    if board.rows:
        items.extend(board.rows)
    if board.cols:
        items.extend(board.cols)

    # Collect items from grid (always GridLayout now — dict form removed)
    if board.grid:
        items.extend([gi.item for gi in board.grid.items])

    # Collect items from tabs (always TabLayout now — dict form removed)
    if board.tabs:
        items.extend(board.tabs.items)

    # Process all items to find nested boards
    for item in items:
        if isinstance(item, AuthoredBoard):
            nested_boards.append(item)
        elif isinstance(item, TabItem):
            # Tab items contain their own rows/cols — treat as an inline board
            tab_board = AuthoredBoard.model_construct(
                rows=item.rows,
                cols=item.cols,
                grid=item.grid,
                tabs=item.tabs,
            )
            nested_boards.append(tab_board)

    return nested_boards


def _build_registry(
    board: AuthoredBoard,
    registry_type: str,
    registry: dict[str, Any] | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a registry for queries or charts recursively.

    This unified function traverses the entire board tree to collect definitions
    of the specified type. Each board processes its own definitions, then recursively
    processes all nested boards and merges their results.

    Note: Variables are built at render time, not compile time, so they don't use
    this registry building function.

    Args:
        board: AuthoredBoard to process
        registry_type: Type of registry to build ("queries" or "charts")
        registry: Existing registry to add to (will be created if None)
        board_cache: The cache layer inherited from the boards this one is nested
            in; this board's own `cache:` folds over it below. (The cascade
            *root* rides in `**kwargs` — it is uniform across the tree, so this
            function never touches it.)
        **kwargs: Additional arguments specific to registry type:
            - For "queries": base_dir, default_source, sources
            - For "charts": base_dir, cross_board_queries, sources

    Returns:
        Complete registry dictionary

    Raises:
        CompilationError: If duplicate names found or invalid registry_type
    """
    if registry is None:
        registry = {}

    # A board's own `cache:` layers over the one it inherits and applies to every
    # board nested inside it, so fold it in here — before the recursion below
    # carries it down — rather than at each board's own queries. Both branches
    # normalize queries (the charts branch does it for cross-board imports), so
    # both need the folded layer.
    board_cache = merge_cache_layers(board_cache, board.cache)

    # Board-level incremental setting cascades into queries (and nested boards).
    # A nested board overrides the parent's value when it sets its own.
    effective_incremental: IncrementalValue = kwargs.pop("board_incremental", None)
    if board.incremental is not None:
        effective_incremental = board.incremental

    # Process definitions at this board level
    if registry_type == "queries":
        _process_queries_for_registry(
            board,
            registry,
            board_cache=board_cache,
            board_incremental=effective_incremental,
            **kwargs,
        )
    elif registry_type == "charts":
        _process_charts_for_registry(board, registry, board_cache=board_cache, **kwargs)
    else:
        raise CompilationError(f"Invalid registry type: {registry_type}")

    # Recursively process nested boards
    nested_boards = _extract_nested_boards(board)
    for nested_board in nested_boards:
        _build_registry(
            nested_board,
            registry_type,
            registry,
            board_cache,
            board_incremental=effective_incremental,
            **kwargs,
        )

    return registry


def _process_queries_for_registry(
    board: AuthoredBoard,
    registry: dict[str, AnyQuery],
    base_dir: ProjectDirectory | None = None,
    default_source: str | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    board_incremental: IncrementalValue = None,
) -> None:
    """Process queries from a board and add to registry.

    Args:
        board: AuthoredBoard to process queries from
        registry: Registry to add queries to
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        default_source: Default source to apply to queries without explicit source
        sources: Named source config dicts.
        board_cache: The board-scope cache layer already folded down the nesting
            chain by the caller.
        board_incremental: Effective board-level incremental setting for the cascade.

    Raises:
        CompilationError: If duplicate query names found
    """
    # Get default source from this board level (may override parent)
    board_default_source = board.get_default_source()
    effective_default_source = board_default_source or default_source

    # Process queries at this level
    for name, query_def in (board.queries or {}).items():
        if name in registry:
            raise CompilationError(
                f"Duplicate query name '{name}'. Query names must be unique "
                "within a file (and those that are imported)."
            )

        # Handle cross-file references
        if isinstance(query_def, QueryRef):
            registry[name] = load_from_reference(
                query_def,
                base_dir=base_dir,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
                yaml_path=["queries", name],
            )
        else:
            registry[name] = normalize_query(
                name,
                query_def,
                default_source=effective_default_source,
                sources=sources,
                base_dir=base_dir,
                cache_root=cache_root,
                board_cache=board_cache,
                board_incremental=board_incremental,
            )


def _process_charts_for_registry(
    board: AuthoredBoard,
    registry: dict[str, Any],
    cross_board_queries: dict[str, AnyQuery],
    base_dir: ProjectDirectory | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> None:
    """Process charts from a board and add to registry.

    Args:
        board: AuthoredBoard to process charts from
        registry: Registry to add charts to
        cross_board_queries: Pool an imported chart's own query chain is resolved
            into, lexically scoped to its source board (see
            `_load_chart_ref_with_lexical_queries`). Mutated in place.
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        sources: Named source config dicts, and `board_cache` this board's folded
            `cache:` layer — an imported chart's queries are normalized here, so
            they need the same cascade layers the `queries:` lane gets.
        board_cache: See `sources`.

    Raises:
        CompilationError: If duplicate chart names found
    """
    # Process charts at this level
    for name, chart_def in (board.charts or {}).items():
        if name in registry:
            raise CompilationError(
                f"Duplicate chart name '{name}'. Chart names must be unique "
                "within a file (and those that are imported)."
            )

        # Handle cross-file references
        if isinstance(chart_def, ChartRef):
            if base_dir is None:
                raise CompilationError(
                    f"Cannot resolve cross-file reference '{chart_def.ref}': "
                    "no base directory context (board was compiled without a base directory)"
                )
            registry[name] = _load_chart_ref_with_lexical_queries(
                chart_def,
                base_dir=base_dir,
                cross_board_queries=cross_board_queries,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
                yaml_path=["charts", name],
            )
        else:
            registry[name] = chart_def


def build_query_registry(
    board: AuthoredBoard,
    base_dir: ProjectDirectory | None = None,
    registry: dict[str, AnyQuery] | None = None,
    default_source: str | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> dict[str, AnyQuery]:
    """Build complete query registry from board and nested boards.

    Traverses the entire board structure to collect all query definitions,
    including from nested boards.

    Args:
        board: AuthoredBoard to process
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        registry: Existing registry to add to
        default_source: Default source to apply to queries without explicit source
        sources: Named source config dicts (project + board level).
        board_cache: The cache layer this board inherits from the boards it is
            nested in; its own `cache:` folds over it inside `_build_registry`.

    Returns:
        Complete query registry

    Raises:
        CompilationError: If duplicate query names found
    """
    return _build_registry(
        board,
        "queries",
        registry=registry,
        cache_root=cache_root,
        board_cache=board_cache,
        base_dir=base_dir,
        default_source=default_source,
        sources=sources,
    )


def build_chart_registry(
    board: AuthoredBoard,
    cross_board_queries: dict[str, AnyQuery],
    base_dir: ProjectDirectory | None = None,
    registry: dict[str, Any] | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> dict[str, Any]:
    """Build complete chart registry from board and nested boards.

    Traverses the entire board structure to collect all chart definitions,
    including from nested boards. Charts are global - any layout can reference any chart.

    Args:
        board: AuthoredBoard to process
        cross_board_queries: Pool an imported chart's own query chain resolves
            into, lexically scoped to its source board. Mutated in place.
        base_dir: ProjectDirectory anchor for the board file (used to resolve sub-file refs).
        registry: Existing registry to add to
        sources: Named source config dicts — a cross-board chart import normalizes
            queries here too, so it needs the source-scope `cache:` layer.
        board_cache: The cache layer this board inherits; its own `cache:` folds
            over it inside `_build_registry`.

    Returns:
        Complete chart registry (raw chart definitions, not normalized)

    Raises:
        CompilationError: If duplicate chart names found
    """
    return _build_registry(
        board,
        "charts",
        registry=registry,
        cache_root=cache_root,
        board_cache=board_cache,
        base_dir=base_dir,
        cross_board_queries=cross_board_queries,
        sources=sources,
    )


def _resolve_ref_item(
    ref: str,
    section_name: str,
    base_dir: ProjectDirectory,
    yaml_path: Sequence[str],
) -> tuple[Any, str, ProjectPath, dict[str, YamlValue]]:
    """Locate `<file>.{section_name}.<name>` and return the raw item plus
    context needed to keep resolving relative to *that file's own directory*
    (item_name, the file's ProjectPath, and its full parsed content).

    Shared file/section/item lookup for VariableRef/QueryRef/ChartRef and for
    the lexical query-chain walk below — grammar already validated by the
    ref's own Pydantic model. Callers own the "no base directory context"
    check (each has its own wording for which reference failed).

    Args:
        yaml_path: Dotted key path to the referencing site in the importing
            board's own YAML (e.g. `["charts", "rev_chart"]`), threaded onto a
            raised ReferenceError's `ref_path` so `_set_reference_error_field_path`
            can set `field_path` — the compile pipeline's central source-map
            stamping (`_stamp_compile_result`) then resolves that path to a
            `Diagnostic.range`. See the two `except ReferenceError` sites in
            `compile_authored_board` that call `build_chart_registry` /
            `build_query_registry`.

    Raises:
        ReferenceError: The ref's file path escapes the project root (e.g.
            one too many `../`), the target board file doesn't exist at the
            resolved path (bad directory), or the file exists but the named
            query/chart/variable isn't declared in it.
        CompilationError: The target file exists but fails to parse (a parse
            failure, not a reference-resolution failure).
    """
    # Grammar already validated by the typed model; split is deterministic.
    file_path_str, item_name = ref.rsplit(f".{section_name}.", 1)

    if not (file_path_str.endswith(".yml") or file_path_str.endswith(".yaml")):
        file_path_str += ".yml"

    try:
        resolved_path = base_dir / file_path_str
    except ValueError as e:
        raise ReferenceError(ref, f"{file_path_str!r} — {e}", ref_path=yaml_path) from e

    if not resolved_path.exists():
        raise ReferenceError(
            ref,
            f"{resolved_path.relpath!r} — the file does not exist",
            ref_path=yaml_path,
        )

    try:
        content = resolved_path.read_yaml()
    except (OSError, yaml.YAMLError) as e:
        raise CompilationError(f"Failed to load {file_path_str}: {e}") from e

    singular = {"queries": "query", "charts": "chart", "variables": "variable"}.get(
        section_name, section_name[:-1]
    )

    if not content or section_name not in content:
        raise ReferenceError(
            ref,
            f"{resolved_path.relpath!r} — that file has no {section_name}: section",
            ref_path=yaml_path,
        )

    section = content[section_name]

    if not isinstance(section, dict) or item_name not in section:
        raise ReferenceError(
            ref,
            f"{resolved_path.relpath!r} — no {singular} named {item_name!r} there",
            ref_path=yaml_path,
        )

    return section[item_name], item_name, resolved_path, content


def _board_level_default_source(
    content: dict[str, YamlValue], file_label: str
) -> str | None:
    """Validated board-level `source:` of a raw source-file mapping.

    Cross-board-pulled queries inherit the declaring file's default source. The
    source file's content is a raw mapping here (never parsed into
    AuthoredBoard), so the string-ness the model would enforce is checked here.
    """
    source = content.get("source")
    if source is None:
        return None
    if not isinstance(source, str):
        raise CompilationError(
            f"Invalid board-level `source:` in {file_label}: "
            f"expected a source name string, got {type(source).__name__}"
        )
    return source


def load_from_reference(
    reference: VariableRef | QueryRef | ChartRef,
    base_dir: ProjectDirectory | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    yaml_path: Sequence[str] = (),
) -> Any:
    """Load an item from a typed cross-file reference.

    Args:
        reference: A typed ref model (VariableRef, QueryRef, or ChartRef).
            The grammar has already been validated by the Pydantic model.
        base_dir: ProjectDirectory anchor for the board file (used to resolve paths).
            If None and a sub-file ref is encountered, raises CompilationError.
        sources: Project source configs, for `normalize_query`'s source-scope
            cache layer.
        board_cache: The importing board's cache layer. An imported query is a
            query of *this* dashboard, so `cache:` at the top of the board has
            to reach it too — otherwise the layer means "every query except the
            ones you can't see from here".
        yaml_path: Dotted key path to the referencing site in the importing
            board's own YAML (e.g. `["queries", "revenue"]`) — threaded onto a
            resolution failure so its `Diagnostic.range` can be resolved.
            Empty when the caller has no such site (e.g. compiling in-memory
            content).

    Returns:
        - QueryRef → AnyQuery
        - ChartRef → chart dict (its own `query:`/`layers[].query` names, if
          any, are still bare source-board-local names at this point — see
          `_load_chart_ref_with_lexical_queries` for the entry point that
          also resolves those)
        - VariableRef → Variable

    Raises:
        ReferenceError: The target board file or the named item is missing.
        CompilationError: No base directory context, or the target file
            fails to parse.
    """
    if base_dir is None:
        raise CompilationError(
            f"Cannot resolve cross-file reference '{reference.ref}': "
            "no base directory context (board was compiled without a base directory)"
        )

    if isinstance(reference, VariableRef):
        section_name = "variables"
    elif isinstance(reference, QueryRef):
        section_name = "queries"
    else:
        section_name = "charts"

    item_def, item_name, resolved_path, source_file_content = _resolve_ref_item(
        reference.ref, section_name, base_dir, yaml_path
    )

    if section_name == "queries":
        return normalize_query(
            item_name,
            item_def,
            _board_level_default_source(
                source_file_content, str(resolved_path.relpath)
            ),
            base_dir=base_dir,
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
    if section_name == "charts":
        return item_def
    # section_name == "variables"
    if isinstance(item_def, Variable):
        return item_def
    if isinstance(item_def, dict):
        return Variable(**item_def)
    raise CompilationError(
        f"Invalid variable definition for '{item_name}' in {resolved_path.relpath}"
    )


_CROSS_BOARD_KEY_UNSAFE_RE = re.compile(r"[^0-9A-Za-z_]")
_CROSS_BOARD_QUERY_PREFIX = "_ximport_"


def _cross_board_query_key(file_relpath: str, name: str) -> str:
    """Identifier-safe, collision-free key for a query pulled in by lexical
    scoping from a cross-board chart import (see compile/AGENTS.md's
    "Cross-board chart imports scope queries lexically" invariant). Like
    `synthetic_query_name`, this is an internal pointer
    only — nothing parses it back — so it only needs to be unique and stable
    within one compile, and to never collide with a name a user would author
    (the leading underscore + reserved token guarantee that).
    """
    file_token = _CROSS_BOARD_KEY_UNSAFE_RE.sub("_", file_relpath)
    return f"{_CROSS_BOARD_QUERY_PREFIX}{file_token}_{name}"


def pull_cross_board_query_chain(
    query_name: str,
    query_registry: dict[str, AnyQuery],
    into: dict[str, AnyQuery],
) -> None:
    """Copy a cross-board-imported query, and every query in its own
    `{{ queries.X }}` composition chain, from `query_registry` into `into`.

    A chart imported via lexical scoping resolves its whole query chain into
    `query_registry` under synthetic keys — but only the chart's own
    top-level `query_name` is reachable from `Board.charts`. `Board.queries`
    (what `validate_variable_references` walks to give the free
    missing-variable check) needs every query in the chain that might carry
    its own `{{ filter(...) }}` refs, not just the top one. No-op for names
    that aren't cross-board-imported (nothing to pull; they're either already
    in `Board.queries` via the ordinary local-query path, or not this board's
    concern).
    """
    if query_name in into or query_name not in query_registry:
        return
    if not query_name.startswith(_CROSS_BOARD_QUERY_PREFIX):
        return
    query = query_registry[query_name]
    into[query_name] = query
    # detect_query_dependencies always sets an entry for every input key.
    for dep in detect_query_dependencies({query_name: query})[query_name]:
        pull_cross_board_query_chain(dep, query_registry, into)


def _is_lexically_scoped_query_name(value: str) -> bool:
    """True when a chart's `query:`/`layers[].query` string must resolve
    against the *source* board rather than the importer: a bare name declared
    in the source board's own `queries:` section, or a `<file>#<name>`
    external ref anchored at the source board's own directory. Excluded is
    inline SQL (self-contained) and a full `<file>.queries.<name>` cross-file
    ref written directly on the chart itself (an already-ambiguous, unrelated
    edge case for local charts too — left untouched).
    """
    stripped = value.strip()
    return ".queries." not in stripped and not looks_like_sql(stripped)


def _read_queries_section(
    file_dir: ProjectDirectory, target_file_str: str, ref: str, chart_ref: str
) -> tuple[ProjectPath, dict[str, YamlValue], dict[str, YamlValue]]:
    """The `queries:` section of `target_file_str`, resolved relative to
    `file_dir` — the *declaring* file's own directory, never the importer's.

    Also returns the file's full parsed mapping, so callers can read its
    board-level `source:` default for the queries declared in it.

    `ref` is the authored ref string this file part came from and `chart_ref`
    the chart import that pulled it in; both are named in every failure, since
    the author is looking at neither file when the resolution breaks.
    """
    where = f"Chart ref '{chart_ref}' requires '{ref}'"
    try:
        target_path = file_dir / target_file_str
    except ValueError as e:
        raise CompilationError(f"{where}: {e}") from e
    if not target_path.exists():
        raise CompilationError(
            f"{where}, but {target_file_str} does not exist "
            f"(resolved relative to {file_dir.relpath})."
        )
    try:
        target_content = target_path.read_yaml()
    except (OSError, yaml.YAMLError) as e:
        raise CompilationError(f"{where}: failed to load {target_file_str}: {e}") from e
    # read_yaml is -> Any: a file whose top level is a list or scalar parses
    # fine and would only fail at the .get below, as an AttributeError escaping
    # compile() rather than a diagnostic.
    if target_content is not None and not isinstance(target_content, dict):
        raise CompilationError(f"{where}, but {target_file_str} is not a YAML mapping.")
    if target_content is None:
        raise CompilationError(
            f"{where}, but {target_file_str} has no queries: section."
        )
    target_queries = target_content.get("queries")
    if not isinstance(target_queries, dict):
        raise CompilationError(
            f"{where}, but {target_file_str} has no queries: section."
        )
    return target_path, target_queries, target_content


def _resolve_cross_board_query(
    name: str,
    file_dir: ProjectDirectory,
    file_relpath: str,
    queries_section: dict[str, YamlValue],
    pool: dict[str, AnyQuery],
    key_cache: dict[tuple[str, str], str],
    resolving: set[tuple[str, str]],
    chart_ref: str,
    *,
    default_source: str | None,
    sources: dict[str, Any],
    cache_root: CachePatch | None,
    board_cache: CachePatch,
) -> str:
    """Resolve query `name`, declared in the `queries:` section of the file at
    `file_relpath` (whose own directory is `file_dir`), lexically scoped to
    that file — never the importing board that triggered this walk. Returns
    the synthetic key under which the normalized query now lives in `pool`.

    Recurses into `{{ queries.X }}` SQL-composition deps (found via a raw-dict
    scan, before normalization) and into cross-file `QueryRef` chains — each
    hop re-anchored at *its own* declaring file's directory, per
    compile/AGENTS.md's nested-board-anchoring pattern. `key_cache` memoizes by
    `(file_relpath, name)` so a sub-dependency shared by multiple imports
    resolves once per compile; `resolving` guards against a cycle.

    Queries are lexical but `cache:` is not: `sources` and `board_cache` are the
    *importing* board's cascade layers, because the imported query runs as a
    query of this dashboard and is cached under this dashboard's policy.
    """
    cache_key = (file_relpath, name)
    if cache_key in key_cache:
        return key_cache[cache_key]
    if cache_key in resolving:
        raise CompilationError(
            f"Circular query reference while resolving chart ref '{chart_ref}': "
            f"'{name}' in {file_relpath} depends on itself."
        )
    if name not in queries_section:
        raise CompilationError(
            f"Chart ref '{chart_ref}' requires query '{name}', which is not "
            f"declared in {file_relpath}'s queries: section."
        )

    resolving.add(cache_key)
    qualified_key = _cross_board_query_key(file_relpath, name)
    key_cache[cache_key] = qualified_key

    raw_def = normalize_query_value(queries_section[name])

    if isinstance(raw_def, dict) and "ref" in raw_def:
        target_file_str, target_name = raw_def["ref"].rsplit(".queries.", 1)
        if not (target_file_str.endswith(".yml") or target_file_str.endswith(".yaml")):
            target_file_str += ".yml"
        target_path, target_queries, target_content = _read_queries_section(
            file_dir, target_file_str, raw_def["ref"], chart_ref
        )
        target_key = _resolve_cross_board_query(
            target_name,
            target_path.parent,
            target_path.relpath,
            target_queries,
            pool,
            key_cache,
            resolving,
            chart_ref,
            default_source=_board_level_default_source(target_content, target_file_str),
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )
        pool[qualified_key] = pool[target_key]
        resolving.discard(cache_key)
        return qualified_key

    # Direct definition — rewrite embedded {{ queries.X }} deps to their
    # synthetic keys, resolving each dependency first (post-order).
    sql_text = raw_def.get("sql") if isinstance(raw_def, dict) else None
    # detect_query_dependencies always sets an entry for every input key.
    dep_names = detect_query_dependencies({name: raw_def})[name]
    if sql_text is not None and dep_names:
        rewritten_sql = sql_text
        for dep_name in dep_names:
            dep_key = _resolve_cross_board_query(
                dep_name,
                file_dir,
                file_relpath,
                queries_section,
                pool,
                key_cache,
                resolving,
                chart_ref,
                default_source=default_source,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )

            def _substitute(match: re.Match[str], dep_key: str = dep_key) -> str:
                cache_suffix = match.group(1)
                if cache_suffix is None:
                    cache_suffix = ""
                return "{{ queries." + dep_key + cache_suffix + " }}"

            rewritten_sql = re.sub(
                rf"\{{\{{\s*queries\.{re.escape(dep_name)}\s*(\.\s*cache\s*)?\}}\}}",
                _substitute,
                rewritten_sql,
            )
        raw_def = {**raw_def, "sql": rewritten_sql}

    pool[qualified_key] = normalize_query(
        qualified_key,
        raw_def,
        default_source,
        base_dir=file_dir,
        sources=sources,
        cache_root=cache_root,
        board_cache=board_cache,
    )
    resolving.discard(cache_key)
    return qualified_key


def _load_chart_ref_with_lexical_queries(
    chart_ref: ChartRef,
    base_dir: ProjectDirectory,
    cross_board_queries: dict[str, AnyQuery],
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None,
    board_cache: CachePatch,
    yaml_path: Sequence[str],
) -> YamlValue:
    """Load a cross-board chart, resolving its `query:`/`layers[].query` names
    lexically against the *source* board's own `queries:` section — never the
    importing board's registry (see compile/AGENTS.md's "Cross-board chart
    imports scope queries lexically, variables board-globally" invariant).

    Mutates `cross_board_queries` with every query the chart's chain
    transitively needs, under synthetic collision-free keys, and rewrites the
    returned chart dict's query fields to point at those keys.

    `sources`/`board_cache` are the importing board's cache layers — lexical
    scoping is about query *names*, not about which board's `cache:` policy the
    imported query runs under.

    Args:
        yaml_path: Dotted key path to the chart ref's own site in the
            importing board's YAML (e.g. `["charts", "rev_chart"]`) — threaded
            onto a resolution failure so its `Diagnostic.range` can be
            resolved via the central source-map stamping.
    """
    item_def, _item_name, resolved_path, content = _resolve_ref_item(
        chart_ref.ref, "charts", base_dir, yaml_path
    )
    if not isinstance(item_def, dict):
        return item_def  # non-dict chart defs are invalid; let downstream validation handle

    queries_section = content.get("queries")
    if not isinstance(queries_section, dict):
        queries_section = {}
    file_dir = resolved_path.parent
    file_relpath = resolved_path.relpath
    source_default = _board_level_default_source(content, str(file_relpath))
    key_cache: dict[tuple[str, str], str] = {}
    resolving: set[tuple[str, str]] = set()

    def resolve_name(name: str) -> str:
        """A bare name resolves in the source board's own `queries:`; a
        `<file>#<name>` external ref resolves in that file's, reached relative
        to the source board's directory. Either way the resulting query lands
        in `cross_board_queries` under a key qualified by the file it was
        actually declared in — so an importer `#`-ref spelled identically to
        the imported chart's, but pointing at a different file, stays its own
        query.
        """
        stripped = name.strip()
        if "#" not in stripped:
            return _resolve_cross_board_query(
                stripped,
                file_dir,
                file_relpath,
                queries_section,
                cross_board_queries,
                key_cache,
                resolving,
                chart_ref.ref,
                default_source=source_default,
                sources=sources,
                cache_root=cache_root,
                board_cache=board_cache,
            )
        target_file_str, target_name = split_external_query_ref(stripped)
        target_path, target_queries, target_content = _read_queries_section(
            file_dir, target_file_str, stripped, chart_ref.ref
        )
        return _resolve_cross_board_query(
            target_name,
            target_path.parent,
            target_path.relpath,
            target_queries,
            cross_board_queries,
            key_cache,
            resolving,
            chart_ref.ref,
            default_source=_board_level_default_source(target_content, target_file_str),
            sources=sources,
            cache_root=cache_root,
            board_cache=board_cache,
        )

    chart_raw = dict(item_def)
    query_field = chart_raw.get("query")
    if isinstance(query_field, str) and _is_lexically_scoped_query_name(query_field):
        chart_raw["query"] = resolve_name(query_field)

    layers = chart_raw.get("layers")
    if isinstance(layers, list):
        chart_raw["layers"] = [
            (
                {**layer, "query": resolve_name(layer["query"])}
                if isinstance(layer, dict)
                and isinstance(layer.get("query"), str)
                and _is_lexically_scoped_query_name(layer["query"])
                else layer
            )
            for layer in layers
        ]

    return chart_raw

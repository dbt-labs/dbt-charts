"""Query normalization and external query resolution."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError as PydanticValidationError

if TYPE_CHECKING:
    from dbt_charts.core.project import ProjectDirectory

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.file_refs import (
    INLINE_FILE_SOURCE_SUFFIXES,
    read_section,
    resolve_file_source,
    resolve_ref,
    split_external_query_ref,
)
from dbt_charts.core.compile.models.cache import (
    INHERIT_CACHE,
    CachePatch,
    resolve_cache_policy,
    validate_cache_layer,
)
from dbt_charts.core.compile.models.primitives import (
    IncrementalValue,
    validate_incremental_value,
)
from dbt_charts.core.compile.models.query.authored import _BaseQueryFields
from dbt_charts.core.compile.models.query.normalized import (
    AnyQuery,
    HttpQuery,
    SchemaQuery,
    SqlParseError,
    SqlQuery,
    ValuesQuery,
    is_schema_query,
    is_sql_query,
)
from dbt_charts.core.compile.models.refs import infer_query_type_from_keys
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    JsonSourceConfig,
    ParquetSourceConfig,
    SourceConfig,
    source_cache_layer,
)
from dbt_charts.core.compile.normalize.sql_authoring_lint import (
    find_date_literal_variable,
    has_literal_escaped_newlines,
)
from dbt_charts.core.compile.sql_guard import (
    source_dialect,
    validate_select_only,
    validate_setup_sql,
)
from dbt_charts.core.compile.template.jinja import (
    detect_query_dependencies,
    extract_sql_variable_dependencies,
)
from dbt_charts.core.diagnostics.ansi import strip_ansi
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_SOURCE_INLINE_FORBIDDEN,
    ERR_SOURCE_NOT_FOUND,
    ERR_SOURCE_REQUIRED,
    ERR_SQL_DATE_LITERAL_VARIABLE,
    ERR_SQL_LITERAL_NEWLINES,
)
from dbt_charts.core.diagnostics.execution import MutatingSqlError, UnparseableSqlError

logger = logging.getLogger(__name__)

_QUERY_TYPES_TAKING_CONNECTION_DEFAULT = frozenset({"sql"})


def _is_file_source_ref(source: str) -> bool:
    """True if a `source:` string is an inline file path rather than a registry name.

    Extension matching is case-insensitive to stay consistent with
    ``resolve_file_source``, which lowercases the suffix — otherwise a
    bare ``SALES.CSV`` would misclassify as a registry name.
    """
    return "/" in source or source.lower().endswith(tuple(INLINE_FILE_SOURCE_SUFFIXES))


def _build_file_source_config(
    file_type: str, stem: str, resolved_path: str
) -> CsvSourceConfig | JsonSourceConfig | ParquetSourceConfig:
    """Construct the typed single-file source config for *file_type*.

    A per-branch dispatch (rather than a class lookup table keyed by
    extension) so each constructor call keeps its own literal `type=` value —
    a lookup-table call site would need a type-ignore to paper over the union.
    """
    if file_type == "csv":
        return CsvSourceConfig(type="csv", files={stem: resolved_path})
    if file_type == "json":
        return JsonSourceConfig(type="json", files={stem: resolved_path})
    return ParquetSourceConfig(type="parquet", files={stem: resolved_path})


def _resolve_inline_file_source(
    name: str, ref: str, base_dir: ProjectDirectory | None
) -> dict[str, Any]:
    """Resolve an inline file-path `source:` ref to a source config dict.

    The table name is the file's stem (`sales.csv` -> table `sales`); a stem
    that is not a valid SQL identifier is a compile error — no silent
    sanitization. The anchors are `resolve_file_source`'s.
    """
    path, file_type = resolve_file_source(ref, base_dir, name)
    try:
        config = _build_file_source_config(
            file_type, PurePosixPath(ref).stem, path.relpath
        )
    except PydanticValidationError as e:
        raise CompilationError(
            f"Query {name!r}: inline file source {ref!r} is invalid: {e}"
        ) from e
    return config.model_dump()


def dialect_for_source(source: str | None, sources: dict[str, Any]) -> str | None:
    """The sqlglot dialect for a query's source, or None when unresolvable.

    A source's `type` is its dialect when the type names one ("duckdb",
    "bigquery", "sqlserver" via the alias table). Plenty don't: `csv` /
    `json` / `parquet` are file sources, and `dbt_profile`'s real dialect
    lives in the profile and isn't knowable until execute. sqlglot's own
    registry is the arbiter — asking it for an unknown name raises rather
    than degrading, and `dialects.get_dialect` can't stand in here because
    it silently answers postgres for anything it doesn't recognize.

    None means "could not establish one", and callers must treat it as "do
    not report parse findings" rather than falling back to the default
    dialect: parsing BigQuery SQL as generic SQL is how a parse warning ends
    up firing on ~40% of real boards. The callers that do fall back are
    `agent_api.lookup_board_query_sql`, which reports no findings (it only
    renders SQL text for a preview), and `ProjectSession.validate_query`,
    which lints an ad-hoc statement the user asked to lint.
    """
    if source is None:
        return None
    config = sources.get(source)
    # The registry holds either a raw dict or a validated config, depending on
    # how far normalization has gotten — `source_cache_layer` reads it the same
    # two ways. Handling only the dict would silently stop warning for the
    # other caller rather than fail.
    if isinstance(config, dict):
        source_type = config.get("type")
    elif isinstance(config, SourceConfig):
        source_type = config.type
    else:
        return None
    return source_dialect(source_type) if isinstance(source_type, str) else None


def normalize_query(
    name: str,
    query_def: Any,
    default_source: str | None = None,
    *,
    sources: dict[str, Any],
    base_dir: ProjectDirectory | None = None,
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
    board_incremental: IncrementalValue = None,
) -> AnyQuery:
    """Normalize a query definition to AnyQuery.

    Creates the appropriate query type (SqlQuery, HttpQuery, etc.) based on the
    query definition.

    Args:
        name: Query name
        query_def: AuthoredQuery definition (AuthoredQuery, dict, or bare SQL string shorthand)
        default_source: Default source to apply if query has no explicit source
        sources: Named source config dicts (project + board level), keyed by source
            name. Required, with no default: it carries the source-scope `cache:`
            layer, and the two cross-file import lanes each shipped without it
            once already — an optional registry that quietly resolves to `{}`
            turns "this caller has no source configs" and "this caller forgot to
            pass them" into the same, silent, day-old-data outcome.
        base_dir: ProjectDirectory anchor (board file's parent). Used to resolve
            inline file-path sources.
        cache_root: The project's `cache:` block — the root of the cascade,
            threaded from the compile entry (`Project.cache`) rather than read
            off the process-global config: `load_config` runs only under `dct
            serve`, and installing a project's root globally would leak it
            between the orgs one Cloud worker compiles for. None means no
            project is attached (a board compiled from text), whose documented
            root is the shipped one.
        board_cache: The declaring dashboard's `cache:` layer, folded in between
            the source and the query itself. Empty (the default) authors
            nothing, so the query inherits the source/project layers unchanged.
        board_incremental: Board-level incremental setting (watermark column,
            False, or None) to inherit when the query does not specify its own.

    Returns:
        Query object (SqlQuery, HttpQuery, etc.)
    """
    # Non-SQL pre-built query types need no guard — return immediately.
    if isinstance(
        query_def,
        (
            HttpQuery,
            ValuesQuery,
            SchemaQuery,
        ),
    ):
        return query_def

    # Pre-built SqlQuery: skip reconstruction but still run guard + dep-extraction
    # below. It is already normalized, so it keeps the cache policy it carries.
    prebuilt = isinstance(query_def, SqlQuery)
    authored_cache: CachePatch | None = None
    # Incremental setting extracted from the authored input; used in the
    # cascade after the query is constructed. None means "inherit from board".
    # Prebuilt queries already carry their normalized value — preserve it.
    query_incremental: IncrementalValue = None
    if isinstance(query_def, SqlQuery):
        query: AnyQuery = query_def
        # Prebuilt: treat existing value as already-resolved (no cascade needed).
        query_incremental = query_def.incremental
    else:
        # Convert to dict
        query_dict: dict[
            str, Any
        ]  # WHY: prevents pyright narrowing to dict[str, str] in the bare-SQL {"sql": query_def} branch
        if isinstance(query_def, _BaseQueryFields):
            query_dict = query_def.model_dump(exclude_none=True)
            # The cascade layer is read off the instance and merged below, so
            # drop the dumped copy rather than carrying the same `cache:`
            # through the reconstruction on the other side.
            query_dict.pop("cache", None)
            authored_cache = query_def.cache
            # Pull the incremental setting before SqlQuery construction rejects
            # it as an extra field. Already validated by _BaseQueryFields'
            # own field_validator when this instance was constructed.
            query_incremental = query_dict.pop("incremental", None)
        elif isinstance(query_def, dict):
            query_dict = dict(query_def)
            authored_cache = validate_cache_layer(name, query_dict.pop("cache", None))
            try:
                query_incremental = validate_incremental_value(
                    query_dict.pop("incremental", None)
                )
            except ValueError as e:
                raise CompilationError(f"Query '{name}': {e}") from e
        elif isinstance(query_def, str):
            query_dict = {"sql": query_def}
        else:
            raise CompilationError(
                f"Query '{name}': Invalid query definition. Expected dict with 'sql' and 'source' fields for SQL queries."
            )

        # Ensure type field
        if "type" not in query_dict:
            query_dict["type"] = infer_query_type_from_keys(query_dict)

        query_type = query_dict.pop("type")

        # Apply default source if query doesn't have one. Only query types that
        # lower to a connection-backed SqlQuery take the inherited default:
        # ValuesQuery is inline data, SchemaQuery's source is a dbt source name
        # (not a connection reference), and HttpQuery uses url — none of those
        # have a connection slot for the default to fill.
        has_source = query_dict.get("source") is not None
        if (
            not has_source
            and default_source
            and query_type in _QUERY_TYPES_TAKING_CONNECTION_DEFAULT
        ):
            query_dict["source"] = default_source

        resolved_sources = sources

        # Reject inline dict sources before type dispatch.
        if isinstance(query_dict.get("source"), dict):
            raise CompilationError.from_code(
                ERR_SOURCE_INLINE_FORBIDDEN,
                query_name=name,
                offending_value=repr(query_dict["source"]),
            )

        # Create the appropriate query type
        if query_type == "sql":
            # Ensure sql is present
            if "sql" not in query_dict:
                raise CompilationError(
                    f"Query '{name}': SQL queries must have 'sql' field"
                )
            raw_source = query_dict.get("source")
            if isinstance(raw_source, str):
                if _is_file_source_ref(raw_source):
                    # Inline file-path source: register it into the sources
                    # registry under the literal authored ref so execute-time
                    # lookup (`board_sources.get(query.source)`) resolves it
                    # unchanged.
                    if raw_source not in resolved_sources:
                        resolved_sources[raw_source] = _resolve_inline_file_source(
                            name, raw_source, base_dir
                        )
                elif sources and raw_source not in sources:
                    # A registry name that isn't in the loaded project registry.
                    # Skipped when no registry was supplied at all (pure
                    # in-memory compile) — there the execute-time resolver
                    # remains the only backstop.
                    raise CompilationError.from_code(
                        ERR_SOURCE_NOT_FOUND,
                        query_name=name,
                        source=raw_source,
                        available=sorted(sources),
                    )
            # SqlQuery.source is required. This pre-check catches both the
            # missing-key case (source absent from query_dict) and the
            # explicit-null case (source=None from YAML null or a programmatic
            # caller), surfacing the friendly ERR-SOURCE-REQUIRED rather
            # than a raw ValidationError. With source guaranteed present and
            # non-null here, SqlQuery construction can no longer fail on a
            # missing source.
            if query_dict.get("source") is None:
                raise CompilationError.from_code(ERR_SOURCE_REQUIRED, query_name=name)
            query = SqlQuery(**query_dict)
        elif query_type == "http":
            if "url" not in query_dict:
                raise CompilationError(
                    f"Query '{name}': HTTP queries require a 'url' field"
                )
            try:
                query = HttpQuery(**query_dict)
            except PydanticValidationError as e:
                fields = ", ".join(
                    str(err["loc"][0]) for err in e.errors() if err["loc"]
                )
                raise CompilationError(
                    f"Query '{name}': invalid field(s) for HTTP query: {fields}"
                ) from e
        elif query_type == "values":
            query = ValuesQuery(**query_dict)
        elif query_type == "schema":
            query = SchemaQuery(**query_dict)
        else:
            raise CompilationError(
                f"Query '{name}': unknown type '{query_type}'. "
                "Valid types: http, schema, sql, values."
            )

    # Validate setup_sql doesn't contain query references (non-nestable by design)
    if is_sql_query(query) and query.setup_sql and "{{ queries." in query.setup_sql:
        raise CompilationError(
            f"Query '{name}': setup_sql must not contain {{{{ queries.X }}}} references. "
            "setup_sql is for non-nestable setup statements (CREATE TEMP FUNCTION, etc.)."
        )

    # Compile-time SQL guard + variable-dep extraction for SQL queries.
    # UnparseableSqlError is silently deferred (compile lacks the dbt manifest for
    # {{ ref() }} resolution and must not reject boards with complex Jinja macros).
    # MutatingSqlError is fatal at compile time — wrap in CompilationError with query name.
    deps: set[str] = set()
    if is_sql_query(query):
        # `dialect:` describes `sql`; setup_sql runs untranslated on the source.
        source_dialect = dialect_for_source(query.source, sources)
        dialect: str | None = query.dialect or source_dialect

        # Authoring lint: detect literal \n (backslash + n) from single-quoted YAML.
        # Must run before validate_select_only because literal \n causes parse failure
        # that would otherwise be silently deferred as UnparseableSqlError.
        if has_literal_escaped_newlines(query.sql, dialect=dialect):
            raise CompilationError.from_code(
                ERR_SQL_LITERAL_NEWLINES, query_name=name, field_label="sql"
            )
        if query.setup_sql and has_literal_escaped_newlines(
            query.setup_sql, dialect=source_dialect
        ):
            raise CompilationError.from_code(
                ERR_SQL_LITERAL_NEWLINES,
                query_name=name,
                field_label="setup_sql",
            )

        # Authoring lint: detect a variable quoted as a date/time/timestamp
        # literal (`date '{{ var }}'`) on a bound-param dialect (duckdb,
        # sqlite), where it compiles to invalid SQL (see
        # find_date_literal_variable's docstring) -- must run before
        # validate_select_only for the same reason as the \n check above.
        date_literal_match = find_date_literal_variable(query.sql, dialect=dialect)
        if date_literal_match:
            raise CompilationError.from_code(
                ERR_SQL_DATE_LITERAL_VARIABLE,
                query_name=name,
                field_label="sql",
                keyword=date_literal_match.group(1).lower(),
                variable=date_literal_match.group(2).strip(),
            )
        if query.setup_sql:
            setup_date_literal_match = find_date_literal_variable(
                query.setup_sql, dialect=source_dialect
            )
            if setup_date_literal_match:
                raise CompilationError.from_code(
                    ERR_SQL_DATE_LITERAL_VARIABLE,
                    query_name=name,
                    field_label="setup_sql",
                    keyword=setup_date_literal_match.group(1).lower(),
                    variable=setup_date_literal_match.group(2).strip(),
                )

        try:
            validate_select_only(query.sql, dialect=dialect)
        except UnparseableSqlError as e:
            logger.debug("compile-time SQL validation deferred to runtime: %s", name)
            # Deferral stands either way — compile has no dbt manifest to
            # resolve {{ ref() }} against, so an unparseable body is "we
            # could not check this", not "this is definitely broken". But
            # when the dialect was resolvable AND sqlglot handed back a
            # concrete position, the failure is specific enough to show the
            # author. Anything vaguer stays silent rather than training
            # people to ignore the squiggle.
            if dialect is not None and e.sql_position is not None:
                query.parse_error = SqlParseError(
                    message=strip_ansi(str(e.cause)), position=e.sql_position
                )
        except MutatingSqlError as e:
            # Carry the cause's registered code: CompilationError.__init__
            # assigns the ERR_INTERNAL fallback, so this must follow it.
            error = CompilationError(f"Query '{name}': {e}")
            error.code = e.code
            raise error from e

        if query.setup_sql:
            try:
                validate_setup_sql(query.setup_sql, dialect=source_dialect)
            except UnparseableSqlError:
                logger.debug(
                    "compile-time setup_sql validation deferred to runtime: %s", name
                )
            except MutatingSqlError as e:
                error = CompilationError(f"Query '{name}' setup_sql: {e}")
                error.code = e.code
                raise error from e

        deps = extract_sql_variable_dependencies(query.sql, query.setup_sql)
    query.variable_dependencies = frozenset(deps)

    # An explicit `incremental: <column>` on a non-SQL query is a compile
    # error. Board-level incremental is silently ignored for non-SQL queries
    # (they never receive tail queries); only an explicit query-level column
    # is rejected.
    if not is_sql_query(query) and isinstance(query_incremental, str):
        raise CompilationError(
            f"Query '{name}': incremental refresh is only supported for SQL queries."
        )

    # Cascade the incremental setting: query-level overrides board-level.
    # Only SqlQuery can be incremental — other types never receive tail queries.
    # Pre-built queries keep their already-normalized value (no re-cascade).
    if is_sql_query(query) and not prebuilt:
        effective_incremental: IncrementalValue = (
            query_incremental if query_incremental is not None else board_incremental
        )
        # False (explicit opt-out) and None (never set) both collapse to None
        # on the normalized model — only a watermark column name means
        # "incremental". A bare `true` cannot reach here: rejected at parse.
        query.incremental = (
            effective_incremental if isinstance(effective_incremental, str) else None
        )

    # Resolve the cache cascade (project root → source → board → query)
    # into one policy the executor consumes without cascade logic.
    # Pre-built normalized queries keep the policy they already carry.
    if not prebuilt:
        # Lazy import: compile.config sits above the model layer this module
        # otherwise stays within; imported at the single point of use.
        from dbt_charts.core.compile.config import shipped_cache_root

        # SchemaQuery carries a source and goes through the same executor cache
        # path as SqlQuery, so it takes the source scope too. HttpQuery and
        # ValuesQuery have no source to take one from.
        source_name = (
            query.source if is_sql_query(query) or is_schema_query(query) else None
        )
        query.cache = resolve_cache_policy(
            cache_root if cache_root is not None else shipped_cache_root(),
            source_cache_layer(sources, source_name, name),
            board_cache,
            authored_cache,
        )

    return query


def expand_composed_variable_dependencies(
    queries: dict[str, AnyQuery], names: Iterable[str] | None = None
) -> None:
    """Widen each query's variable dependencies to those of every query it embeds.

    `names` limits the widening to those queries; their embedded queries are
    still looked up in `queries`.
    """
    for name in queries if names is None else names:
        seen: set[str] = set()
        pending = [name]
        deps: set[str] = set()
        while pending:
            current = pending.pop()
            if current in seen or current not in queries:
                continue
            seen.add(current)
            deps |= queries[current].variable_dependencies
            pending.extend(
                detect_query_dependencies({current: queries[current]})[current]
            )
        queries[name].variable_dependencies = frozenset(deps)


def resolve_external_query(
    query_ref: str,
    query_registry: dict[str, AnyQuery],
    base_dir: ProjectDirectory | None = None,
    *,
    sources: dict[str, Any],
    cache_root: CachePatch | None = None,
    board_cache: CachePatch = INHERIT_CACHE,
) -> tuple[str, dict[str, AnyQuery]]:
    """Resolve an external query reference.

    Parses references like `path/to/file.yml#query_name` and loads
    queries from the external file, memoizing via query_registry (compile-scoped).

    Args:
        query_ref: Query reference with file path (e.g., "_shared.yml#sales")
        query_registry: Current query registry to merge into
        base_dir: ProjectDirectory anchor for the board file (used to resolve paths).
        sources: Project source configs, for `normalize_query`'s source-scope
            cache layer.
        cache_root: The project's cascade root — same threading rule as
            `normalize_query`'s.
        board_cache: The importing board's cache layer — same rule as the dotted
            `other.queries.name` lane in `load_from_reference`: an imported
            query is a query of *this* dashboard, so the board's `cache:` has to
            reach it.

    Returns:
        Tuple of (resolved_query_name, updated_query_registry)

    Raises:
        CompilationError: If file not found or query not in file
    """
    file_part, query_name = split_external_query_ref(query_ref)

    registry_key = f"{file_part}#{query_name}"

    # Within-compile memo: this external ref was already resolved this compile.
    if registry_key in query_registry:
        return registry_key, query_registry

    ref_path = resolve_ref(query_ref, base_dir, "query_file")
    queries_section, _content = read_section(ref_path, "queries", query_ref)
    if query_name not in queries_section:
        available = ", ".join(queries_section.keys()) if queries_section else "none"
        raise CompilationError(
            f"Query '{query_name}' not found in '{file_part}'. "
            f"Available queries: {available}"
        )

    query_registry[registry_key] = normalize_query(
        query_name,
        queries_section[query_name],
        base_dir=base_dir,
        sources=sources,
        cache_root=cache_root,
        board_cache=board_cache,
    )
    return registry_key, query_registry

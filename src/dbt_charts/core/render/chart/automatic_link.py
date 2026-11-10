"""Auto-link resolver for charts: filter cell links, FK-dimension drill, and the
unified column-set resolver used by both table rows and non-table chart marks.

Row-grain table detail:
  When a table chart has no explicit ``link:`` and the board has
  ``links.auto: true``, synthesize the canonical row-level link:
    ``/data/<source>/<schema>/<table>/detail/?<key>={{ key }}``
  The table renderer carries it as the chart-root link, which renders as a
  whole-row clickable band — not a per-cell link.

Filter cell links:
  When a board declares variables (typically via ``plan_variables`` in the
  /data table-index template), dimension cells whose column name matches a
  declared variable emit a ``?<col>=<percent-encoded-value>`` link so clicking
  them re-filters the current page in place. The client-side variable
  interceptor in variables.js handles the click.

FK-dimension drill:
  For columns that are declared FK references (from dbt ``relationships:`` tests),
  synthesize per-column drill links:
    ``/data/<source>/<schema>/<ref_table>/detail/?<right_col>={{ fk_col | urlencode }}``
  These are injected as per-column ``link:`` overrides in the chart's resolved
  column configs.

Per-column link precedence (table cells):
  1. Explicit authored ``link:`` on a column config
  2. FK-drill link — on declared-FK dimension columns
  3. Per-column filter link — column is a declared board variable
  4. Text-format link — column formatted `email` (mailto:) or `url`
  A column with none of these is not a cell link. The chart-root link
  (``links.auto`` detail URL) is separate: the renderer paints it as a whole-row
  band beneath the cells, and a cell link — when present — wins the click.

This is render/serve-time (not compile-time) because it needs schema metadata
(column types from the query result) to select URL-safe keys, and FK relationship
metadata (from the dbt manifest) to identify FK columns.

Grain/lineage reasoning (does the SQL prove a row-grain single base entity,
and what output name does a base column end up under) lives in the sibling
``sql_grain`` module — this file consumes ``resolve_cte_spine``,
``resolve_join_spine``, and ``build_col_output_alias_map`` from there.

Entry points:
  - ``resolve_column_set_link(channel_cols, loc, fk_edges, automatic_link, row_detail)``
    — the unified resolver for both table rows and chart marks.  Table callers
    pass ``row_detail=True``; chart marks leave it ``False`` (default).
    The caller resolves location via ``source_schema_table`` first and passes
    the resulting ``loc=(source, schema, table)`` directly.
  - ``source_schema_table(query, schema, require_row_grain)`` — extract (source,
    schema, table) from a query (CTE/plain only), or an empty tuple on bail. Internal
    callers that hold a ``LinkSchemaReader`` use the private
    ``_loc_for_query`` instead, which additionally attempts FK-proven JOIN
    resolution.
  - ``resolve_fk_column_links(source, schema, table, fk_edges)`` — pure resolver
    for per-column FK drill links, callable from tests without the context.
  - ``fetch_column_rows_for_link(schema, query)`` — the planned column metadata
    rows for key selection.
  - ``fk_column_links(query, schema)`` — the planned FK edges as per-column link
    templates.
  - ``with_link_schema(results, ctx, board, variables)`` — the results plus the
    schema and foreign keys the resolve will read, run before render.
  - ``resolve_filter_cell_link(column, value, filter_variables)`` — pure helper
    that returns ``?col=percent-encoded-value`` or ``None``.
  - ``set_filter_variables_context(names)`` / ``get_filter_variables_context()``
    — set/get the declared filter variable names for the current render pass.
"""

from __future__ import annotations

from collections.abc import Iterator, Set as AbstractSet
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from dbt_charts.core.compile.models.chart.normalized import TableChart, _BaseChartFields
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.render.chart.sql_grain import (
    build_col_output_alias_map,
    parse_sql,
    resolve_cte_spine,
    resolve_join_spine,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.board.normalized import Board, VariableValues
    from dbt_charts.core.compile.models.chart.normalized import Chart
    from dbt_charts.core.compile.models.query.normalized import AnyQuery
    from dbt_charts.core.execute.link_schema import LinkSchemaReader
    from dbt_charts.core.execute.plan import ExecContext
    from dbt_charts.core.execute.query_results import QueryResults

# Per-render-pass set of declared variable names (from board.variable_registry).
# Cells whose column is in this set emit ?col=value filter links.
_filter_vars_ctx: ContextVar[frozenset[str]] = ContextVar(
    "_filter_vars_ctx", default=frozenset()
)

# Sentinel used by private helpers to signal "bail — no link possible".
# Callers check `if not result` to detect it.
_BAIL: tuple[()] = ()


def set_filter_variables_context(names: frozenset[str]) -> None:
    """Set the declared filter variable names for the current render pass."""
    _filter_vars_ctx.set(names)


def get_filter_variables_context() -> frozenset[str]:
    """Return the declared filter variable names for the current render pass."""
    return _filter_vars_ctx.get()


def resolve_filter_cell_link(
    column: str,
    value: Any,
    filter_variables: AbstractSet[str],
) -> str | None:
    """Return a ``?col=percent-encoded-value`` filter link, or ``None``.

    Returns ``None`` when:
    - ``column`` is not in ``filter_variables``
    - ``value`` is ``None``
    - ``filter_variables`` is empty

    Uses ``urllib.parse.urlencode`` so spaces become ``+`` (application/x-www-
    form-urlencoded) and special chars like ``&``, ``=``, ``/`` are percent-escaped.
    This matches what the client-side variable interceptor in ``variables.js``
    reads back via ``URLSearchParams``.

    Args:
        column: Column name to use as the query-param key.
        value: Raw cell value (any type — converted to str for encoding).
        filter_variables: Set of declared board variable names for this render pass.

    Returns:
        ``?col=encoded-value`` string, or ``None`` when no link should be emitted.
    """
    if not filter_variables or column not in filter_variables or value is None:
        return None
    return "?" + urlencode({column: str(value)})


def _extract_single_base_table(
    sql: str,
    dialect: str | None,
    require_row_grain: bool = True,
) -> tuple[str, str] | tuple[()]:
    """Extract (schema, table) from SQL with exactly one base table, or _BAIL.

    Bails on subqueries, catalog-qualified 3-part names, missing schema prefix,
    or parse error.

    When ``require_row_grain=True`` (default, table row-grain path):
    - CTE-wrapped queries are resolved via ``resolve_cte_spine`` (in
      ``sql_grain``) when the CTE body is a clean row-grain SELECT over a
      single schema-qualified table.
    - JOIN queries always bail here; callers with a ``LinkSchemaReader`` use
      ``_loc_for_query`` for FK-proven JOIN resolution.
    - Plain aggregated queries (GROUP BY / DISTINCT / aggregate functions) bail
      because the rows are not row-grain and a per-row detail link would be wrong.

    When ``require_row_grain=False`` (chart mark path), CTEs and JOINs still bail
    unconditionally — the chart mark auto-link path is unchanged.  Aggregation is
    allowed for chart bars identified by their dimension channel values.

    Catalog-qualified names are always rejected — the catalog would be silently
    dropped from the URL, producing an ambiguous path.

    Args:
        sql: Raw SQL string (Jinja tokens are stripped before parsing).
        dialect: The sqlglot dialect of the query's source.
        require_row_grain: When True, enforce row-grain; CTEs may resolve.
    """
    from sqlglot import exp

    tree = parse_sql(sql, dialect)
    if tree is None:
        return _BAIL

    has_ctes = bool(list(tree.find_all(exp.CTE)))
    has_joins = bool(list(tree.find_all(exp.Join)))

    if has_ctes:
        # Chart mark path: CTE bail is unchanged.
        if not require_row_grain:
            return _BAIL
        return resolve_cte_spine(tree)

    if has_joins:
        # FK-proven JOIN resolution needs the FK edges; callers use _loc_for_query.
        return _BAIL

    if list(tree.find_all(exp.Subquery)):
        return _BAIL
    if require_row_grain:
        # Aggregation collapses grain — link would point at a wrong detail page.
        if list(tree.find_all(exp.Group)):
            return _BAIL
        if list(tree.find_all(exp.Distinct)):
            return _BAIL
        if list(tree.find_all(exp.AggFunc)):
            return _BAIL

    tables = list(tree.find_all(exp.Table))
    if len(tables) != 1:
        return _BAIL

    t = tables[0]
    table_name = t.name
    schema_name = t.db  # sqlglot calls the schema qualifier `.db`
    catalog_name = t.catalog  # non-empty for mydb.schema.table 3-part names

    if not table_name or not schema_name:
        return _BAIL
    # Bail on catalog-qualified names — the catalog would be silently dropped
    # from the URL, producing an ambiguous path.
    if catalog_name:
        return _BAIL

    return schema_name, table_name


def _loc_for_query(
    query: AnyQuery,
    schema: LinkSchemaReader,
) -> tuple[str, str, str] | tuple[()]:
    """Combined location resolver: CTE/plain then FK-proven JOIN.

    Callers that hold a ``LinkSchemaReader`` use this instead of calling
    ``source_schema_table`` directly — always the row-grain table path (the
    chart-mark path, ``require_row_grain=False``, calls ``source_schema_table``
    directly instead; JOINs stay bail-closed there and this combined resolver
    doesn't apply).

    Tries ``source_schema_table`` first (CTE and plain queries); when that
    bails, attempts FK-proven JOIN resolution.

    Args:
        query: The compiled query to inspect.
        schema: The planned schema, for the FK edges JOIN resolution proves with.
    """
    loc = source_schema_table(query, schema, require_row_grain=True)
    if loc:
        return loc
    from dbt_charts.core.compile.models.query.normalized import (  # noqa: PLC0415
        is_sql_query,
    )

    if not (is_sql_query(query) and query.source):
        return _BAIL
    tree = parse_sql(query.sql, schema.dialect_of(query.source))
    if tree is None:
        return _BAIL
    result = resolve_join_spine(tree, partial(schema.fk_edges_of, query.source))
    if not result:
        return _BAIL
    schema_name, table = result
    return query.source, schema_name, table


def source_schema_table(
    query: AnyQuery,
    schema: LinkSchemaReader,
    require_row_grain: bool = True,
) -> tuple[str, str, str] | tuple[()]:
    """Extract (source, schema, table) from a query, or an empty tuple on bail.

    Handles schema queries and SQL queries over a single base table or a
    single-CTE wrapper over a single base table.  JOIN queries always bail here;
    callers with a ``LinkSchemaReader`` use ``_loc_for_query`` to also attempt
    FK-proven JOIN resolution.

    When ``require_row_grain=False``, aggregated SQL queries are allowed (chart
    mark path) but CTEs and JOINs still bail unconditionally.

    Args:
        query: The compiled query to inspect.
        schema: The planned schema, which names the source's dialect.
        require_row_grain: Enforce row-grain; enables CTE resolution.
    """
    from dbt_charts.core.compile.models.query.normalized import (
        is_schema_query,
        is_sql_query,
    )

    if is_schema_query(query):
        if not query.source or not query.schema_name or not query.table:
            return _BAIL
        return query.source, query.schema_name, query.table

    if is_sql_query(query):
        raw_source = query.source
        if not raw_source:
            return _BAIL
        result = _extract_single_base_table(
            query.sql,
            schema.dialect_of(raw_source),
            require_row_grain=require_row_grain,
        )
        if not result:
            return _BAIL
        schema_name, table = result
        return raw_source, schema_name, table

    return _BAIL


def fetch_column_rows_for_link(
    schema: LinkSchemaReader,
    query: AnyQuery,
) -> list[dict[str, str]]:
    """Return column metadata rows for auto-link key selection, or [] on bail.

    Reads the planned schema query (source + schema + table) — the same stable
    metadata path used by the /data/ table-index template's ``queries.columns``
    block. This is stable across cache hits and misses because schema metadata
    comes from the LayeredSchemaResolver, not from the ephemeral per-request
    query cursor.

    Returns rows with ``name`` and ``actual_type`` keys (matching the format
    ``plan_link_keys`` expects), or an empty list when the query doesn't map
    to a resolvable base table (non-SQL/schema queries, tables without schema
    prefix, unresolvable CTEs/JOINs, or resolver errors).

    Args:
        schema: The planned schema, which answers the column lookup.
        query: The chart's resolved query. Only ``SchemaQuery`` and
            ``SqlQuery`` over a single base table are handled.
    """
    loc = _loc_for_query(query, schema)
    if not loc:
        return []
    return [
        {"name": row["name"], "actual_type": row["actual_type"]}
        for row in schema.columns_of(loc)
        if "name" in row and "actual_type" in row
    ]


def resolve_fk_column_links(
    source: str,
    schema: str,
    table: str,
    fk_edges: list[dict[str, str]],
) -> dict[str, str]:
    """Return per-column FK-drill link templates for declared FK columns.

    For each FK edge where ``from_table == table``, produces a link template:
      ``/data/<source>/<schema>/<to_table>/detail/?<to_column>={{ fk_col | urlencode }}``

    Only declared FK relationships (from dbt ``relationships:`` tests) are used.
    All such edges have confidence=1.0 and are unconditionally recommended.

    **Scope limitation — same source/schema assumed for referenced table.**
    The dbt manifest's ``relationships:`` tests record only the referenced table
    name, not its schema or source.  This function reuses the *queried* table's
    ``source`` and ``schema`` for the referenced table.  This is correct for the
    common single-schema project layout (all models in one schema under one
    source), but will produce dead links when FK targets live in a different
    schema or source.  Fixing this properly requires resolving the referenced
    model's full identifier from the manifest — tracked as a follow-up.

    Args:
        source: The data source name (used in the /data/ URL path).
        schema: The schema name (used in the /data/ URL path).
        table: The queried table name. Only FK edges originating from this table
               are considered; edges for other tables are ignored.
        fk_edges: FK relationship dicts as returned by
                  ``extract_all_relationships(manifest)``; each dict has keys
                  ``from_table``, ``from_column``, ``to_table``, ``to_column``.

    Returns:
        A dict mapping FK column name → drill link template string.
        Empty when the table has no declared FK edges.
    """
    links: dict[str, str] = {}
    for edge in fk_edges:
        if edge["from_table"] != table:
            continue
        from_col = edge["from_column"]
        to_table = edge["to_table"]
        to_col = edge["to_column"]
        links[from_col] = (
            f"/data/{source}/{schema}/{to_table}/detail/"
            f"?{to_col}={{{{ {from_col} | urlencode }}}}"
        )
    return links


def resolve_column_set_link(
    channel_cols: dict[str, str],
    loc: tuple[str, str, str],
    fk_edges: list[dict[str, str]],
    automatic_link: bool = False,
    row_detail: bool = False,
) -> str:
    """Synthesize a link template for a set of (channel → column_name) pairs.

    This is the unified resolver for both table rows and chart marks.  A mark /
    row is identified by its dimension values; the resolver returns the best
    link for that identity set.

    The caller resolves the query location first (``source_schema_table`` or,
    for callers holding a ``LinkSchemaReader``, ``_loc_for_query``) — this
    function is pure given those inputs (no SQL parsing here).

    **Precedence:**
    1. ``automatic_link=False`` → empty string (no link)
    2. ``channel_cols`` empty → empty string (no qualifying dims)
    3. Single dimension that is a recommended FK → referenced-entity detail link:
       ``/data/<src>/<schema>/<ref_table>/detail/?<ref_col>={{ <channel> | urlencode }}``
       (for table rows ``row_detail=True``) or without urlencode (chart marks).
    4. ``row_detail=True`` (table key path): all dims → same-table detail link:
       ``/data/<src>/<schema>/<table>/detail/?k={{ k | urlencode }}[&k2=...]``
    5. All other cases (chart path, single plain dim, multiple dims) →
       filter-by-dims link:
       ``/data/<src>/<schema>/<table>/?col1={{ ch1 }}&col2={{ ch2 }}``

    The caller is responsible for the explicit ``link:`` check — this function
    is only called when no authored link exists.

    Chart link templates use channel-name placeholders (``{{ x }}``,
    ``{{ color }}``) rather than column names, because Vega's
    ``_build_href_calc_expr`` maps channel names to actual datum fields at
    render time and applies percent-encoding via a chained replace() ladder.
    There is no ``| urlencode`` in Vega calculate expressions — the replace()
    chain in ``_build_href_calc_expr`` covers the ASCII delimiter set instead.

    Table callers pass channel-name == column-name (``{"id": "id"}``) *unless*
    the row-detail path traced the sought key to a different output alias
    (``{"ticket_id": "id"}`` for a query that does ``id AS ticket_id``) — the
    channel is always the *output* column name (row-detail URLs read the row's
    actual data at render time to fill ``{{ channel }}``), while for
    ``row_detail=True`` the column name is always the *base table's* column
    name (the URL param — the detail view's own variable planner declares its
    ``variables:`` from the base table's schema, so an output alias as the
    param would never match anything it recognizes). They also pass
    ``row_detail=True`` so key columns produce a same-table detail URL with
    ``| urlencode``, not a filter URL.

    Args:
        channel_cols: Ordered mapping of channel name → data column name for
                      the non-measure dimension channels present on this chart/row.
                      e.g. ``{"x": "owner_id"}`` or ``{"x": "region", "color": "plan"}``.
                      For table rows with no aliasing, channel name == column
                      name: ``{"id": "id"}``; with row-grain aliasing, channel
                      is the output name and column name is the base name:
                      ``{"ticket_id": "id"}``.
        loc: Pre-resolved ``(source, schema, table)`` from ``source_schema_table``.
             The caller owns location resolution (and its grain check) so this
             function stays pure.
        fk_edges: Recommended FK edges for the queried table, as returned by
                  ``fetch_fk_edges`` / ``resolve_fk_column_links``.  Only edges
                  where ``from_column`` is in ``channel_cols.values()`` are used.
        automatic_link: Whether auto-linking is enabled for this render pass.
        row_detail: When ``True`` (table path), non-FK key dims produce a
                    same-table detail URL (``/data/.../detail/?k={{ k | urlencode }}``)
                    instead of a filter URL.  Chart marks leave this ``False``.

    Returns:
        A link template string, or ``""`` when no link should be emitted.
        Chart paths use ``{{ channel }}`` placeholders; table paths use
        ``{{ col | urlencode }}`` in detail URLs.
    """
    if not automatic_link or not channel_cols:
        return ""

    source, schema, table = loc

    # Single dimension with a recommended FK → referenced-entity detail link.
    if len(channel_cols) == 1:
        (channel, col_name) = next(iter(channel_cols.items()))
        for edge in fk_edges:
            if edge["from_column"] == col_name:
                to_table = edge["to_table"]
                to_col = edge["to_column"]
                if row_detail:
                    # Table path: urlencode for FK string values.
                    return (
                        f"/data/{source}/{schema}/{to_table}/detail/"
                        f"?{to_col}={{{{ {channel} | urlencode }}}}"
                    )
                # Chart path: _build_href_calc_expr applies the replace() encoding.
                return (
                    f"/data/{source}/{schema}/{to_table}/detail/"
                    f"?{to_col}={{{{ {channel} }}}}"
                )

    if row_detail:
        # Table key path: same-table detail URL with urlencode for all key params.
        # Param key = column name (== channel for table rows).
        params = "&".join(
            f"{col_name}={{{{ {channel} | urlencode }}}}"
            for channel, col_name in channel_cols.items()
        )
        return f"/data/{source}/{schema}/{table}/detail/?{params}"

    # Chart filter-by-all-dimensions: one param per channel.
    # _build_href_calc_expr percent-encodes the values at render time.
    # Param key = column name (human-readable), value = {{ channel }}.
    params = "&".join(
        f"{col_name}={{{{ {channel} }}}}" for channel, col_name in channel_cols.items()
    )
    return f"/data/{source}/{schema}/{table}/?{params}"


def _table_row_detail_link(chart_query: AnyQuery, schema: LinkSchemaReader) -> str:
    """Synthesize a table's own-row detail link, or ``""`` if none can be
    safely resolved.

    ``_loc_for_query`` bails on aggregated queries — linking aggregate rows
    to a detail page is wrong. FK edges are intentionally not passed to
    ``resolve_column_set_link``: per-column FK-drill links are injected
    separately so the table root link stays own-table only.
    """
    _loc = _loc_for_query(chart_query, schema)
    if not _loc:
        return ""
    column_rows_for_link = fetch_column_rows_for_link(schema, chart_query)
    from dbt_charts.core.compile.models.query.normalized import (  # noqa: PLC0415
        is_sql_query,
    )
    from dbt_charts.core.compile.resolve.chart.link_keys import (  # noqa: PLC0415
        plan_link_keys,
    )

    keys = plan_link_keys(column_rows_for_link)
    # channel_cols maps { output_alias (the Jinja placeholder, evaluated
    # against the query's actual row data at render time) -> base_column
    # (the URL param name — the detail view at /data/.../detail/ declares
    # its `variables:` from the *base table's* own schema, via
    # plan_key_variables, so the param must be a base-table column name
    # regardless of what the query renamed it to for display). These are
    # two different roles that happen to coincide when nothing is aliased.
    alias_map, identity_safe_for_unlisted = (
        build_col_output_alias_map(chart_query.sql, schema.dialect_of(_loc[0]))
        if is_sql_query(chart_query)
        else ({}, True)
    )
    # FK edges not passed — per-column FK-drill owns those overrides.
    # `keys` may be a composite identity (e.g. two FK columns standing in for
    # a missing single id) — the columns are only a key *together*, so this
    # is all-or-nothing: if any one can't be traced to the output, or two
    # distinct keys collapse onto the same output name, emit no link at all
    # rather than a partial/non-unique one.
    channel_cols: dict[str, str] = {}
    for k in keys:
        col_name = str(k["name"])
        if col_name in alias_map:
            resolved = alias_map[col_name]
        elif identity_safe_for_unlisted:
            resolved = col_name
        else:
            # Proven absent from the output (an explicit projection at some
            # layer never selected this base column) — don't guess.
            return ""
        channel_cols[resolved] = col_name
    if len(channel_cols) != len(keys):
        return ""  # two distinct keys collapsed onto the same output name
    return resolve_column_set_link(
        channel_cols=channel_cols,
        loc=_loc,
        fk_edges=[],
        automatic_link=True,
        row_detail=True,
    )


def synthesize_automatic_link(
    chart: Chart,
    schema: LinkSchemaReader,
    exclude_x: bool = False,
) -> str:
    """Synthesize an auto-link template for a chart mark or table row.

    Called only when the board sets ``links.auto`` and the chart has no explicit
    ``link:``.  Returns a Jinja template string when a link can be synthesized,
    or ``""`` when no link should be set.

    **Table path** (``row_detail=True``):
      ``_loc_for_query`` bails on aggregated queries — linking aggregate rows
      to a detail page is wrong. FK edges are intentionally *not* passed
      here: per-column FK-drill links are injected separately so the table
      root link stays own-table only.

    **Chart path** (``row_detail=False``):
      Non-measure dimension channels (``x``, ``color``) are the mark's
      identity.  FK edges are fetched and forwarded so a bar keyed by an FK
      column links directly to the referenced entity's detail page.
      When ``exclude_x=True`` (caller detected a measure on x), ``x`` is
      omitted from the channel set — linking by a continuous measure is wrong.
    """
    if not isinstance(chart, _BaseChartFields):
        return ""
    _chart_query = chart.query
    if _chart_query is None:
        return ""

    if chart.type == "table":
        return _table_row_detail_link(_chart_query, schema)

    # Non-table chart: non-measure dimension channels are the mark's identity.
    # x and color are family-specific fields not present on all Chart members.
    _x = getattr(chart, "x", None)
    _color = getattr(chart, "color", None)
    dim_cols: dict[str, str] = {}
    if _x and not exclude_x:
        dim_cols["x"] = _x
    if _color:
        dim_cols["color"] = _color
    if not dim_cols:
        return ""

    _loc = source_schema_table(_chart_query, schema, require_row_grain=False)
    if not _loc:
        return ""

    _actual_cols = {r["name"] for r in schema.columns_of(_loc) if "name" in r}

    dim_cols = {ch: col for ch, col in dim_cols.items() if col in _actual_cols}
    if not dim_cols:
        return ""

    _fk_edges = schema.fk_edges_of(_loc[0], _loc[2])

    return resolve_column_set_link(
        channel_cols=dim_cols,
        loc=_loc,
        fk_edges=_fk_edges,
        automatic_link=True,
    )


def fk_column_links(
    query: AnyQuery,
    schema: LinkSchemaReader,
) -> dict[str, str]:
    """The planned FK edges (dbt manifest + super-schema) as per-column links.

    Reads ``LinkSchemaReader.fk_edges_of`` and ``resolve_fk_column_links``
    after extracting the table location from ``query``.  Both declared dbt
    relationship edges and super-schema recommended heuristic edges are included.

    Deliberately bails on both CTE and JOIN queries: the returned links are
    keyed by the *base table's* FK column name and matched against actual
    *output* column names by callers (`render/layout_sizing.py`). Widening
    to CTE/JOIN queries without the same alias-tracing rigor
    ``build_col_output_alias_map`` applies to the row-detail path risks
    matching an unrelated output column that happens to share a base FK
    column's name after independent aliasing — this function has no such
    tracing, so it stays on plain single-table queries only, where the base
    name is always the output name.

    Returns an empty dict when:
    - The query type is not SchemaQuery or SqlQuery.
    - The SQL has joins, a CTE, or no schema prefix.
    - The project has no dbt manifest or no declared/heuristic FK edges.

    Args:
        query: The chart's resolved query.
        schema: The planned schema, which answers the FK-edge lookup.

    Returns:
        A dict mapping FK column name → FK drill link template string.
    """
    from dbt_charts.core.compile.models.query.normalized import (  # noqa: PLC0415
        is_sql_query,
    )

    if is_sql_query(query):
        from sqlglot import exp  # noqa: PLC0415

        tree = parse_sql(query.sql, None)
        # Only the CTE case needs an explicit check here: source_schema_table
        # (called below, require_row_grain=True) resolves some CTE shapes via
        # resolve_cte_spine, which is exactly the widening this function must
        # not inherit (see docstring). JOIN queries need no matching check —
        # source_schema_table already bails unconditionally on any JOIN.
        if tree is not None and list(tree.find_all(exp.CTE)):
            return {}

    loc = source_schema_table(query, schema)
    if not loc:
        return {}
    src, schema_name, table = loc
    return resolve_fk_column_links(
        src, schema_name, table, schema.fk_edges_of(src, table)
    )


def _drawn_charts(board: Board, variables: VariableValues) -> Iterator[Chart]:
    """The charts resolve draws: no hidden item, one tab per tabs layout."""
    from dbt_charts.core.render.sizing import active_layout_items  # noqa: PLC0415

    for item in active_layout_items(board.layout, variables):
        if item.chart is not None:
            yield item.chart
        if item.board is not None:
            yield from _drawn_charts(item.board, variables)


def with_link_schema(
    results: QueryResults,
    ctx: ExecContext,
    board: Board,
    variables: VariableValues,
) -> QueryResults:
    """*results* plus the schema and foreign keys ``board``'s resolve will read for
    automatic links, run now so render never asks the warehouse.

    Mirrors ``layout_sizing``'s resolve gates, data included: a chart whose rows
    cannot be read fails there before it reaches the link pass, so it needs
    nothing planned. A lookup that raises ``DbtChartsError`` is recorded on the
    schema and skips only that chart; resolve raises it for the chart that reads it.
    """
    from dbt_charts.core.compile.resolve import (  # noqa: PLC0415
        automatic_link_excludes_x,
        bind_channels,
        columns_of,
        should_fetch_table_fk_links,
        should_synthesize_automatic_link,
    )
    from dbt_charts.core.execute.link_schema import LinkProbe  # noqa: PLC0415

    links_auto = board.links.auto
    if not links_auto:
        return results
    probe = LinkProbe(ctx)
    drawn_variables = {**board.variable_defaults, **variables}
    for chart in _drawn_charts(board, drawn_variables):
        qn = chart.query_name
        try:
            data = results.execute_query(qn, variables) if qn else []
        except Exception:  # noqa: BLE001 — a backend may raise anything; resolve reports that chart's failure
            continue
        try:
            chart = bind_channels(chart, columns_of(data))
            if should_synthesize_automatic_link(chart, data, links_auto):
                synthesize_automatic_link(
                    chart, probe, exclude_x=automatic_link_excludes_x(chart, data)
                )
            if should_fetch_table_fk_links(chart, links_auto):
                assert isinstance(chart, TableChart)
                query = chart.query
                assert query is not None
                if fk_column_links(query, probe):
                    fetch_column_rows_for_link(probe, query)
        except DbtChartsError:
            continue
    return replace(results, planned_links=probe.freeze())

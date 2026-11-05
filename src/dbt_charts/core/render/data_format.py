"""Data render output format — flat, slug-keyed queries and charts.

Layout is a presentation concern, so this format drops it: charts are keyed by
slug in one global map regardless of how deeply the layout nests them. Rows
live once per query and charts reference their query by name, so charts sharing
a query never duplicate the result set.
"""

from typing import Any

from pydantic import BaseModel

from dbt_charts.core.compile.models.board.normalized import (
    Board,
    VariableValues,
)
from dbt_charts.core.compile.models.chart.normalized import _BaseChartFields
from dbt_charts.core.compile.models.query.normalized import AnyQuery, is_sql_query
from dbt_charts.core.diagnostics import ERR_DUPLICATE_CHART_ID, Diagnostic
from dbt_charts.core.execute.chart_data_provider import ResolveDataProvider
from dbt_charts.core.render.board_to_dict import (
    NO_ROW_CAP,
    board_to_dict,
    chart_to_dict,
    clean_value,
)
from dbt_charts.core.render.errors import RenderError


class QueryData(BaseModel):
    """One query's provenance and its single copy of the rows.

    ``sql`` is absent for queries with nothing SQL behind them (values, http,
    schema). ``rows_truncated`` is absent unless a row cap actually dropped
    rows — its absence means nothing was truncated, never that truncation went
    unreported.
    """

    type: str
    sql: str | None = None
    rows: list[dict[str, Any]]
    rows_truncated: dict[str, int] = {}


class BoardData(BaseModel):
    """The whole board as data: queries own the rows, charts reference them.

    A chart entry takes one of three shapes, distinguished by which key is
    present: ``query`` (the usual case — the name of its entry in ``queries``),
    ``error`` (the chart failed to execute), or neither (a chart with no query
    at all, such as a callout).
    """

    id: str
    title: str
    queries: dict[str, QueryData]
    charts: dict[str, dict[str, Any]]
    #: The variable values these rows were produced with — the merged runtime
    #: values, not the authored definitions. This is what makes the
    #: uninterpolated SQL above readable: it says what the Jinja refs resolved
    #: to for this render.
    variables: dict[str, Any] = {}


def _collect_query_defs(board: Board, out: dict[str, AnyQuery]) -> None:
    """Map every laid-out chart's query name to its resolved query object.

    Read off the charts rather than ``board.queries``: an inline chart query is
    normalized under a synthetic name and never lands in ``board.queries``, so
    reading that alone would lose the SQL and misreport the query's type. A
    callout carries no query at all, hence the ``_BaseChartFields`` guard.

    Walks the layout tree — the same traversal that emits the charts — so
    every chart this format emits has its query resolvable by construction.
    """
    for item in board.layout.items:
        if item.type == "chart" and item.chart is not None:
            chart = item.chart
            if isinstance(chart, _BaseChartFields) and chart.query_name and chart.query:
                out[chart.query_name] = chart.query
        elif item.type == "board" and item.board:
            _collect_query_defs(item.board, out)


def _flatten(
    board_dict: dict[str, Any], query_defs: dict[str, AnyQuery], doc: BoardData
) -> None:
    """Walk the layout tree, accumulating the flat query and chart maps."""
    for item in board_dict["items"]:
        if item["type"] == "board":
            _flatten(item["board"], query_defs, doc)
            continue

        # A chart that failed to execute carries a diagnostic instead of data.
        # It still gets a slug so consumers see the failure rather than a hole.
        if "_error" in item:
            doc.charts[item["id"]] = {"error": clean_value(item["_error"])}
            continue

        chart = item["chart"]
        chart_id = chart["id"]
        # Chart ids are unique within a board, not across the tree: nested-board
        # normalization does not forward `used_chart_ids`, so two imported
        # partials of the same board can share an id. A flat
        # map would silently keep the last and drop the rest, with nothing in
        # the document saying so.
        if chart_id in doc.charts:
            raise RenderError.from_code(
                ERR_DUPLICATE_CHART_ID, chart_id=chart_id, format="data"
            )
        # `exclude_none` drops the key entirely when a chart has no query
        # (a callout), so absence and None are the same case here.
        query_name = chart.get("query_name")
        if not query_name:
            # No query to reference and none to invent — a fabricated entry
            # keyed by the chart's own slug would also collide with a real
            # query of the same name.
            doc.charts[chart_id] = chart_to_dict(item, None)
            continue

        # _collect_query_defs walked this same tree, so a miss is a real bug —
        # surface it rather than guess "values", which would read as genuine
        # provenance for a query that in fact hit a warehouse.
        query_def = query_defs[query_name]
        # Present only when a row cap actually dropped rows; the empty record
        # is the honest "nothing was truncated", not a stand-in for unknown.
        truncated: dict[str, int] = {}
        if "rows_truncated" in item:
            truncated = item["rows_truncated"]
        doc.queries[query_name] = QueryData(
            type=query_def.query_type,
            # SQL as compiled — Jinja variable references are left
            # uninterpolated; the values they resolved to for this render are
            # in the document's ``variables``.
            sql=query_def.sql if is_sql_query(query_def) else None,
            rows=clean_value(item["data"]),
            rows_truncated=truncated,
        )
        doc.charts[chart_id] = chart_to_dict(item, query_name)


def render_board_data(
    board: Board,
    results: ResolveDataProvider,
    variables: VariableValues,
    error_collector: list[Diagnostic] | None = None,
    max_rows_per_query: int = NO_ROW_CAP,
) -> str:
    """Render a compiled board to flat, slug-keyed JSON data.

    Emits ``queries`` (name → sql + rows, each result set exactly once) and
    ``charts`` (slug → title, notes, type, and encoding, referencing its
    query by name). A row cap truncates a query's rows and is declared on that
    query as ``rows_truncated: {head, tail, total}`` — never silently.

    Known limitation: a cartesian chart's overlay ``layers`` are not
    represented. The resolved layer carries baked mark styles rather than the
    authored shape, so projecting it would emit un-authorable internals and a
    query reference this document does not resolve. A layered chart therefore
    appears here with its base encoding only; use ``--format json`` when you
    need the overlays.
    """
    board_dict = board_to_dict(
        board, results, variables, error_collector, max_rows_per_query
    )
    query_defs: dict[str, AnyQuery] = {}
    _collect_query_defs(board, query_defs)

    doc = BoardData(
        id=board_dict["id"],
        title=board_dict["title"],
        queries={},
        charts={},
        # The applied values, not board_dict["variables"] — that holds the
        # authored definitions (input, label, default), which say nothing
        # about what these rows were actually produced with. Scoped to the
        # declared variables because the merged mapping also carries
        # auto-injected render context (the `siblings`/`tree` directory
        # proxies), which is neither authored nor query provenance.
        #
        # `variable_registry` (tree-wide, root-only) rather than `variables`
        # (root-local): a variable declared in an imported partial still
        # interpolates into that partial's SQL, so dropping it would leave a
        # Jinja reference in the emitted SQL with no value to explain it.
        # This is the same set `renderer.render` merges from.
        variables=clean_value(
            {
                name: variables[name]
                for name in (board.variable_registry or {})
                if name in variables
            }
        ),
    )
    _flatten(board_dict, query_defs, doc)
    # exclude_defaults drops the genuinely-absent slots: `sql` on a non-SQL
    # query, `rows_truncated` when nothing was capped, `variables` when the
    # board declares none.
    return doc.model_dump_json(indent=2, exclude_defaults=True)

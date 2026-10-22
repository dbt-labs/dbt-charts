"""Board-wide category colors reach every chart's Vega-Lite color scale.

The feature re-scales a color channel a chart already has; it never adds one.
These tests drive the whole pipeline — YAML in, Vega-Lite specs out — because
the defect being fixed is only observable across *two* charts at once.

``_mark_paints`` looks at what actually paints each mark: an assertion on the
color *encoding* alone happily found a top-level encoding no mark read.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.execute.executor import Executor
from dbt_charts.core.render.board_resolve import build_resolved_board
from dbt_charts.core.render.chart.session import BoardRenderSession
from dbt_charts.core.render.chart.spec import RenderBox

from .._engine_render import render_pie_svg

_DATA_MARKS = frozenset(
    {"bar", "line", "area", "point", "circle", "square", "arc", "rect", "trail"}
)

# Two series (status) across three categories, so a `color:` channel is
# natural and the category set can differ between charts.
_ROWS_ALL = """
      - {category: Accessories, status: Open, revenue: 10}
      - {category: Electronics, status: Won, revenue: 30}
      - {category: Tools, status: Open, revenue: 20}
"""
_ROWS_NO_ACCESSORIES = """
      - {category: Electronics, status: Won, revenue: 30}
      - {category: Tools, status: Open, revenue: 20}
"""


def _resolve_board(yaml_content: str) -> tuple[Any, Any]:
    result = compile(yaml_content)
    assert result.success and result.board is not None, result.errors
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(FilesystemProject(Path.cwd())),
        query_registry=result.query_registry,
    )
    resolved_board, _ = build_resolved_board(
        result.board, executor, {}, render_first=False
    )
    return resolved_board, executor


def _iter_items(layout: Any) -> Any:
    """Yield every layout item across the whole board, nested boards included."""
    for item in layout.items:
        yield item
        if item.board is not None:
            yield from _iter_items(item.board.layout)


def _specs(yaml_content: str) -> dict[str, dict[str, Any]]:
    resolved_board, executor = _resolve_board(yaml_content)
    session = BoardRenderSession.create(resolved_board.style)
    out: dict[str, dict[str, Any]] = {}
    for chart_id, chart in resolved_board.charts.items():
        if isinstance(chart, ResolvedPieChart):
            continue
        rows = executor.execute_query(chart.query_name, {}) if chart.query_name else []
        item = next(
            i
            for i in _iter_items(resolved_board.layout)
            if i.chart and i.chart.id == chart_id
        )
        out[chart_id] = session.finalize_vl(
            session.emit_chart(
                chart,
                RenderBox(width=item.width, height=item.height),
                {chart.query_name: rows},
            )
        )
    return out


def _pie_svg(yaml_content: str, chart_id: str) -> str:
    """The engine-drawn SVG of the board's pie ``chart_id``."""
    resolved_board, executor = _resolve_board(yaml_content)
    chart = resolved_board.charts[chart_id]
    assert isinstance(chart, ResolvedPieChart)
    rows = executor.execute_query(chart.query_name, {})
    return render_pie_svg(chart, rows, width=chart.resolution_width)


def _wedge_fill(svg: str, value: str) -> str:
    """The fill of the wedge whose tooltip header is ``value``."""
    match = re.search(rf'fill="([^"]+)"[^>]*aria-label="\u2064{re.escape(value)};', svg)
    assert match is not None, f"no wedge for {value!r}"
    return match.group(1)


def _slice_label_ink(svg: str, value: str) -> str:
    """The fill of the direct slice label whose tooltip header is ``value``."""
    match = re.search(
        rf'<text [^>]*fill="([^"]+)"[^>]*aria-label="\u2064{re.escape(value)};', svg
    )
    assert match is not None, f"no slice label for {value!r}"
    return match.group(1)


def _walk(spec: dict[str, Any], kinds: frozenset[str]) -> list[dict[str, Any] | str]:
    """Return what paints each layer whose mark is in ``kinds``.

    A literal ``mark.fill``/``mark.color`` beats ``encoding.color``, and a
    layer-level channel def beats the shared outer one.
    """
    out: list[dict[str, Any] | str] = []

    def visit(node: dict[str, Any], inherited: dict[str, Any] | None) -> None:
        color = node.get("encoding", {}).get("color")
        effective = color if isinstance(color, dict) else inherited
        children = [
            c
            for k in ("layer", "hconcat", "vconcat", "concat")
            for c in node.get(k, [])
        ]
        if children:
            for child in children:
                visit(child, effective)
            return
        mark = node.get("mark")
        if mark is None:
            return
        kind = mark if isinstance(mark, str) else mark.get("type")
        if kind not in kinds:
            return
        literal = (
            mark.get("fill") or mark.get("color") if isinstance(mark, dict) else None
        )
        if isinstance(mark, dict) and literal and mark.get("stroke") == literal:
            # A knockout halo — the backing disc under an endpoint marker,
            # painted page-canvas on both fill and stroke so the dot reads
            # clear of the line it sits on. It carries no series identity, so
            # the binding leaves it alone, same as the annotation marks above.
            return
        if literal:
            out.append(literal)
        elif isinstance(effective, dict):
            out.append(effective)

    visit(spec, None)
    return out


def _mark_paints(spec: dict[str, Any]) -> list[dict[str, Any] | str]:
    return _walk(spec, _DATA_MARKS)


def _color_enc(spec: dict[str, Any]) -> dict[str, Any]:
    paints = _mark_paints(spec)
    encs = [p for p in paints if isinstance(p, dict) and "field" in p]
    literals = [p for p in paints if isinstance(p, str)]
    assert not literals, f"marks painted by literal ink {literals}, not the binding"
    assert encs, f"no color encoding paints any mark in: {sorted(spec)}"
    return encs[0]


def _color_scale(spec: dict[str, Any]) -> dict[str, Any]:
    return _color_enc(spec)["scale"]


def _color_of(spec: dict[str, Any], value: str) -> str:
    scale = _color_scale(spec)
    return scale["range"][scale["domain"].index(value)]


def _strip_label_fills(spec: dict[str, Any]) -> dict[str, str | None]:
    """Map each data-table strip label's text to its mark.fill (dark ink).

    Label-stub layers (``_label_stub_left_layer``/``_label_stub_right_layer``
    in ``data_table_attachment.py``) are identifiable by their private
    ``__label`` inline data value -- distinct from every chart/legend layer,
    which reads its text from the query data via a field encoding.
    """
    out: dict[str, str | None] = {}

    def visit(node: dict[str, Any]) -> None:
        for k in ("layer", "hconcat", "vconcat", "concat"):
            for child in node.get(k, []):
                visit(child)
        values = node.get("data", {}).get("values")
        if isinstance(values, list) and len(values) == 1 and "__label" in values[0]:
            mark = node.get("mark")
            fill = mark.get("fill") if isinstance(mark, dict) else None
            out[str(values[0]["__label"])] = fill

    visit(spec)
    return out


def _board(charts: str, extra_queries: str = "", style: str = "") -> str:
    return f"""
{style}
queries:
  all:
    type: values
    rows:{_ROWS_ALL}
  subset:
    type: values
    rows:{_ROWS_NO_ACCESSORIES}
{extra_queries}
charts:
{charts}
"""


_TWO_SERIES_BARS = _board(
    """
  full:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  partial:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - full
  - partial
"""
)


class TestCrossChartConsistency:
    """The defect: one value, different colors in different charts.

    Display order — `scale.domain` — is each chart's own; the binding never
    reorders it (see ``TestAuthoredBinding.test_authored_binding_does_not_reorder_the_domain``
    below). Only the RANGE — which color a shared value paints — is board-consistent.
    """

    def test_a_missing_category_does_not_reflow_the_others(self) -> None:
        specs = _specs(_TWO_SERIES_BARS)
        for value in ("Electronics", "Tools"):
            assert _color_of(specs["full"], value) == _color_of(specs["partial"], value)

    def test_every_bound_value_gets_a_distinct_color(self) -> None:
        scale = _color_scale(_specs(_TWO_SERIES_BARS)["full"])
        assert len(set(scale["range"])) == len(scale["domain"]) == 3

    def test_a_pie_and_a_bar_agree(self) -> None:
        board = _board(
            """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  slices:
    query: all
    type: pie
    theta: revenue
    color: category
rows:
  - bars
  - slices
"""
        )
        assert _color_of(_specs(board)["bars"], "Electronics") == _wedge_fill(
            _pie_svg(board, "slices"), "Electronics"
        )


class TestHeatmapAgreesWithABar:
    """Repro: a heatmap ordered `category DESC` plus a bar, both `color:
    category` -- before heatmap.py called `category_scale_for`, the shared
    values came out with SWAPPED colors between the two charts (both were
    VL-alphabetical independently, but the heatmap's own row order put them
    in a different position than the bar's, so the two local alphabetical
    scales landed 'Accessories'/'Tools' on different palette slots).
    """

    def test_heatmap_and_bar_agree_on_a_shared_category_field(self) -> None:
        specs = _specs(
            _board(
                """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  grid:
    query: heat_rows
    type: heatmap
    x: col_key
    y: row_key
    color: category
    sort:
      by: category
      order: desc
rows:
  - bars
  - grid
""",
                extra_queries="""
  heat_rows:
    type: values
    rows:
      - {row_key: R1, col_key: C1, category: Electronics}
      - {row_key: R2, col_key: C2, category: Tools}
""",
            )
        )
        for value in ("Electronics", "Tools"):
            assert _color_of(specs["bars"], value) == _color_of(specs["grid"], value)


class TestOneChartPlacedTwiceIsStillOneChart:
    """The threshold counts charts, not placements.

    `_placed_charts` walks layout items, and a board may place the same chart
    in two slots. Counting each placement let a single-chart board bind — the
    exact case `_MIN_CHARTS_FOR_BINDING` exists to decline — which re-seats
    its colors from Vega-Lite's sorted domain to board first-seen order.
    """

    _QUERIES = """
  q:
    type: values
    rows:
      - {x: A, category: Tools, revenue: 10}
      - {x: B, category: Electronics, revenue: 20}
"""

    _ONE_CHART = """
  sales:
    query: q
    type: bar
    x: x
    y: revenue
    color: category
"""

    def test_placing_it_twice_does_not_reach_the_threshold(self) -> None:
        once = _specs(
            _board(self._ONE_CHART + "rows:\n  - sales\n", extra_queries=self._QUERIES)
        )
        twice = _specs(
            _board(
                self._ONE_CHART + "rows:\n  - sales\n  - sales\n",
                extra_queries=self._QUERIES,
            )
        )
        assert _color_enc(twice["sales"]) == _color_enc(once["sales"])


class TestGeoshapeValueFieldDoesNotWidenTheDomain:
    """A choropleth whose fill comes from `value:` paints nothing from the binding.

    `_resolve_choropleth_value_field` resolves `value or color`, so a geoshape
    authoring both takes its fill from `value:` and its `color:` field names
    nothing that chart paints. Counting it anyway pushed a lone sibling over
    the two-chart threshold: the sibling's colors were re-seated by a chart
    that then painted none of them, and an authored pin on that field could
    push the domain past palette capacity and raise on a board that rendered
    before.
    """

    _QUERIES = """
  bars:
    type: values
    rows:
      - {state_id: "6", category: Electronics, revenue: 10}
      - {state_id: "48", category: Tools, revenue: 20}
  geo:
    type: values
    rows:
      - {state_id: "6", category: Electronics, revenue: 10}
      - {state_id: "36", category: Accessories, revenue: 30}
"""

    _BAR = """
  sales:
    query: bars
    type: bar
    x: state_id
    y: revenue
    color: category
"""

    _GEO_WITH_VALUE = """
  choropleth:
    query: geo
    type: geoshape
    geo_source: us-states
    lookup: state_id
    value: revenue
    color: category
"""

    def test_such_a_geoshape_leaves_a_lone_bar_exactly_as_it_was(self) -> None:
        alone = _specs(_board(self._BAR, extra_queries=self._QUERIES))
        beside = _specs(
            _board(self._BAR + self._GEO_WITH_VALUE, extra_queries=self._QUERIES)
        )
        assert _color_enc(beside["sales"]) == _color_enc(alone["sales"])


class TestGeoshapeScaleTypeIsChartLocal:
    """A choropleth's nominal-vs-quantitative shape must come from its OWN
    data, never from whether some OTHER chart happens to bind the same
    field. `_emit_choropleth` used to select the categorical branch purely
    on `category_scale_for(...) is not None` -- so the identical string
    data rendered as a quantitative gradient (with a numeric legend and a
    numeric tooltip format) when the geoshape chart was alone on a board,
    and as a nominal discrete scale (a swatch legend, a bare-string
    tooltip) once a sibling chart bound the same field. Only the RANGE
    (which color a value paints) may legitimately differ between "alone"
    and "with company" -- the scale TYPE, legend shape, and tooltip format
    must be identical either way.
    """

    _GEO_CHART = """
  choropleth:
    query: q
    type: geoshape
    geo_source: us-states
    lookup: state_id
    color: category
"""
    _GEO_QUERY = """
  q:
    type: values
    rows:
      - {state_id: "6", category: Electronics}
      - {state_id: "48", category: Tools}
      - {state_id: "36", category: Accessories}
"""

    def _shape(self, spec: dict[str, Any]) -> tuple[str, bool, bool]:
        """(color encoding type, legend has 'values', tooltip has 'format')."""
        choropleth_layer = spec["layer"][1]
        color_enc = choropleth_layer["encoding"]["color"]
        tooltip_fields = choropleth_layer["encoding"].get("tooltip", [])
        category_tooltip = next(f for f in tooltip_fields if f["field"] == "category")
        return (
            color_enc["type"],
            "values" in (color_enc.get("legend") or {})
            or isinstance(color_enc.get("scale", {}).get("domain"), list),
            "format" in category_tooltip,
        )

    def test_solo_and_sibling_boards_agree_on_shape(self) -> None:
        solo_specs = _specs(
            _board(self._GEO_CHART + "rows:\n  - choropleth\n", self._GEO_QUERY)
        )
        with_sibling_specs = _specs(
            _board(
                self._GEO_CHART
                + """  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - choropleth
  - bars
""",
                self._GEO_QUERY,
            )
        )
        solo_shape = self._shape(solo_specs["choropleth"])
        sibling_shape = self._shape(with_sibling_specs["choropleth"])
        assert solo_shape == sibling_shape, (solo_shape, sibling_shape)
        assert solo_shape[0] == "nominal", (
            "a string color field must never render as a numeric gradient"
        )


class TestNeverInventsAColorChannel:
    """The binding re-scales an existing channel. It never adds one.

    Adding one is what leaked into label layers, overwrote authored
    static colors, and split line marks.
    """

    _POSITIONAL_ONLY = _board(
        """
  a:
    query: all
    type: bar
    x: category
    y: revenue
  b:
    query: subset
    type: bar
    x: category
    y: revenue
rows:
  - a
  - b
"""
    )

    def test_a_positional_field_does_not_bind(self) -> None:
        resolved_board, _ = _resolve_board(self._POSITIONAL_ONLY)
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert bound == set(), bound

    def test_such_a_chart_keeps_its_single_series_ink(self) -> None:
        paints = _mark_paints(_specs(self._POSITIONAL_ONLY)["a"])
        assert all(isinstance(p, str) for p in paints), paints


_TWO_DONUTS = _board(
    """
  d1:
    query: all
    type: donut
    theta: revenue
    color: category
  d2:
    query: subset
    type: donut
    theta: revenue
    color: category
rows:
  - d1
  - d2
"""
)


class TestAnnotationLayersKeepTheirInk:
    """Labels carry deliberate contrast ink; re-scaling them hides them."""

    def test_donut_direct_labels_are_not_rescaled(self) -> None:
        svg = _pie_svg(_TWO_DONUTS, "d1")
        for value in ("Accessories", "Electronics", "Tools"):
            assert _slice_label_ink(svg, value) != _wedge_fill(svg, value), (
                "label inks with its wedge's own fill; its dark-companion "
                "contrast is gone"
            )

    def test_donut_direct_labels_agree_across_charts_with_different_row_order(
        self,
    ) -> None:
        """d2 draws a SUBSET of d1's categories, so their row orders differ.

        A label computed from this chart's own row position (rather than the
        value's board slot) would ink "Electronics" differently in d1 (2nd
        row) than in d2 (1st row) — the coincident case, where both charts
        happen to see every value in the same order, cannot tell these apart.
        """
        d1, d2 = _pie_svg(_TWO_DONUTS, "d1"), _pie_svg(_TWO_DONUTS, "d2")
        for value in ("Electronics", "Tools"):
            assert _slice_label_ink(d1, value) == _slice_label_ink(d2, value)


class TestThresholdGate:
    def test_a_field_on_one_chart_only_does_not_bind(self) -> None:
        resolved_board, _ = _resolve_board(
            _board(
                """
  solo:
    query: all
    type: bar
    x: revenue
    y: revenue
    color: status
  cat_one:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  cat_two:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - solo
  - cat_one
  - cat_two
"""
            )
        )
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert "category" in bound
        assert "status" not in bound


class TestFieldsThatMustNotBind:
    def test_a_date_column_is_never_bound(self) -> None:
        resolved_board, _ = _resolve_board(
            _board(
                """
  a:
    query: monthly
    type: line
    x: revenue
    y: revenue
    color: month
  b:
    query: monthly
    type: line
    x: revenue
    y: revenue
    color: month
rows:
  - a
  - b
""",
                extra_queries="""
  monthly:
    type: values
    rows:
      - {month: "2024-01-01", revenue: 30}
      - {month: "2024-02-01", revenue: 35}
""",
            )
        )
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert bound == set(), bound

    def test_a_numeric_field_is_never_bound(self) -> None:
        resolved_board, _ = _resolve_board(_TWO_SERIES_BARS)
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert "revenue" not in bound


class TestPartialColumns:
    """A chart must never get a scale that cannot seat what it draws."""

    def test_a_null_in_a_color_column_is_still_rejected_by_the_engine(self) -> None:
        """Not our guard, but a second layer against the same failure mode.

        `validation.py` refuses a NULL in a color column outright, so the
        planner never has to reason about a NULL specifically. It still must
        reason about non-null values that are non-string, or unanimously
        date-like — see the two tests below.
        """
        import pytest

        from dbt_charts.core.diagnostics.chart_data import ChartDataError

        with pytest.raises(ChartDataError, match="(?i)null"):
            _specs(
                _board(
                    """
  full:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  gappy:
    query: gappy
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - full
  - gappy
""",
                    extra_queries="""
  gappy:
    type: values
    rows:
      - {category: Accessories, status: Open, revenue: 10}
      - {category: null, status: Open, revenue: 5}
""",
                )
            )

    def test_a_value_only_one_chart_draws_is_in_the_shared_domain(self) -> None:
        specs = _specs(
            _board(
                """
  full:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  extra:
    query: extra
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - full
  - extra
""",
                extra_queries="""
  extra:
    type: values
    rows:
      - {category: Accessories, status: Open, revenue: 10}
      - {category: Zeta, status: Won, revenue: 7}
""",
            )
        )
        domain = _color_scale(specs["extra"])["domain"]
        assert "Zeta" in domain, domain
        assert _color_of(specs["extra"], "Accessories") == _color_of(
            specs["full"], "Accessories"
        )

    def test_a_year_shaped_value_among_names_still_binds(self) -> None:
        specs = _specs(
            _board(
                """
  a:
    query: cohorts
    type: bar
    x: revenue
    y: revenue
    color: cohort
  b:
    query: cohorts
    type: bar
    x: revenue
    y: revenue
    color: cohort
rows:
  - a
  - b
""",
                extra_queries="""
  cohorts:
    type: values
    rows:
      - {cohort: Enterprise, revenue: 10}
      - {cohort: "2024", revenue: 5}
      - {cohort: SMB, revenue: 7}
""",
            )
        )
        assert {"Enterprise", "2024", "SMB"} <= set(_color_scale(specs["a"])["domain"])

    def test_a_chart_whose_values_are_disqualified_poisons_the_field_board_wide(
        self,
    ) -> None:
        """A chart that draws non-string values for `color:` must never get a scale.

        `_bound_scales` re-attaches the board scale to any chart naming the
        field on `color:`, regardless of what that chart's own values look
        like (it can't know: values aren't available at resolve time). So a
        chart whose values are non-string can never safely receive a scale —
        the only way to guarantee that is to keep the field out of the plan
        entirely, for every chart. The board must still render (no
        `KeyError`), and no chart drawing this field ends up bound.
        """
        board = _board(
            """
  d1:
    query: all
    type: donut
    theta: revenue
    color: category
  d2:
    query: subset
    type: donut
    theta: revenue
    color: category
  d3:
    query: numeric_category
    type: donut
    theta: revenue
    color: category
rows:
  - d1
  - d2
  - d3
""",
            extra_queries="""
  numeric_category:
    type: values
    rows:
      - {category: 1, revenue: 12}
      - {category: 2, revenue: 8}
""",
        )
        for chart_id in ("d1", "d2", "d3"):
            assert _pie_svg(board, chart_id).startswith("<svg")
        resolved_board, _ = _resolve_board(board)
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert "category" not in bound, bound

    def test_a_chart_whose_values_are_all_date_like_poisons_the_field_board_wide(
        self,
    ) -> None:
        """Same failure mode, triggered by the all-date-like filter instead.

        A plain year bucket like "2024" is date-like per `is_date_like`, so a
        chart drawing only year-shaped values for `color:` is disqualified by
        `_observe` the same way a numeric column is — and must poison the
        field the same way.
        """
        board = _board(
            """
  d1:
    query: all
    type: donut
    theta: revenue
    color: category
  d2:
    query: subset
    type: donut
    theta: revenue
    color: category
  d3:
    query: year_buckets
    type: donut
    theta: revenue
    color: category
rows:
  - d1
  - d2
  - d3
""",
            extra_queries="""
  year_buckets:
    type: values
    rows:
      - {category: "2024", revenue: 12}
      - {category: "2025", revenue: 8}
""",
        )
        for chart_id in ("d1", "d2", "d3"):
            assert _pie_svg(board, chart_id).startswith("<svg")
        resolved_board, _ = _resolve_board(board)
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert "category" not in bound, bound


class TestScatterAndPointMapBind:
    """Both families declare `color` and count toward the binding threshold.

    A chart that counts toward `_MIN_CHARTS_FOR_BINDING` and receives
    `category_colors` must actually paint from the board scale — else a
    shared value gets two different colors depending on which chart draws
    it, on a board the threshold chose to bind.
    """

    def test_scatter_shares_a_bars_board_slot(self) -> None:
        specs = _specs(
            _board(
                """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  pts:
    query: subset
    type: scatter
    x: status
    y: revenue
    color: category
rows:
  - bars
  - pts
"""
            )
        )
        assert _color_of(specs["bars"], "Electronics") == _color_of(
            specs["pts"], "Electronics"
        )

    def test_point_map_shares_a_bars_board_slot(self) -> None:
        specs = _specs(
            _board(
                """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  pts:
    query: geo_subset
    type: point_map
    latitude: lat
    longitude: lon
    color: category
rows:
  - bars
  - pts
""",
                extra_queries="""
  geo_subset:
    type: values
    rows:
      - {category: Electronics, lat: 40.7, lon: -74.0}
      - {category: Tools, lat: 34.0, lon: -118.2}
""",
            )
        )
        assert _color_of(specs["bars"], "Electronics") == _color_of(
            specs["pts"], "Electronics"
        )

    def test_a_point_map_authoring_an_inert_value_still_binds(self) -> None:
        """`value:` drives the fill on a choropleth, not on a point map.

        The exclusion that keeps a `value:`-filled choropleth out of the
        domain is scoped to the geoshape family for that reason. Applied to
        every geo family it would drop this point map instead, taking the
        board back under the threshold and freeing `Electronics` to be one
        color on the bar and another on the map.
        """
        specs = _specs(
            _board(
                """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  pts:
    query: geo_subset
    type: point_map
    latitude: lat
    longitude: lon
    value: revenue
    color: category
rows:
  - bars
  - pts
""",
                extra_queries="""
  geo_subset:
    type: values
    rows:
      - {category: Electronics, lat: 40.7, lon: -74.0, revenue: 10}
      - {category: Tools, lat: 34.0, lon: -118.2, revenue: 20}
""",
            )
        )
        assert _color_of(specs["bars"], "Electronics") == _color_of(
            specs["pts"], "Electronics"
        )


class TestAreaBinds:
    """`area.py` already passes ``category_scale_for``'s result as
    ``spatial_color_scale``'s 4th argument -- correct today -- but no test
    exercised a bound area chart alongside `category_colors`, and no golden
    covered one either. Drop that 4th argument and `spatial_color_scale`
    silently falls back to alphabetical-by-palette-position, the identical
    failure class round 4 found in bar.
    """

    def test_area_shares_a_bars_board_slot(self) -> None:
        specs = _specs(
            _board(
                """
  bars:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  fill:
    query: area_rows
    type: area
    x: status
    y: revenue
    color: category
    style:
      stack: zero
rows:
  - bars
  - fill
""",
                # A real (non-degenerate) filled area needs >=2 x positions
                # per series -- `all`/`subset` draw one row per category,
                # which sparse-band gap-fill turns into a placeholder literal
                # fill unrelated to this test's concern. `stack: zero` avoids
                # unstacked/overlap area's own halo/backdrop layer, an
                # intentional opaque knockout mask (`emit_area_layer`'s
                # ``backdrop``) painted with a literal background fill for
                # edge contrast -- not part of the color binding this test
                # targets, but indistinguishable from a real unbound mark to
                # `_mark_paints`'s literal-fill check. The stacked path emits
                # no halo layer at all.
                extra_queries="""
  area_rows:
    type: values
    rows:
      - {status: Q1, category: Electronics, revenue: 10}
      - {status: Q2, category: Electronics, revenue: 15}
      - {status: Q1, category: Tools, revenue: 5}
      - {status: Q2, category: Tools, revenue: 8}
""",
            )
        )
        assert _color_of(specs["bars"], "Electronics") == _color_of(
            specs["fill"], "Electronics"
        )


class TestAuthoredLayers:
    def test_a_layers_chart_binds_on_its_base_color_field(self) -> None:
        resolved_board, _ = _resolve_board(
            _board(
                """
  plain:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  layered:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
    layers:
      - type: line
        query: all
        x: status
        y: revenue
        label: Trend
rows:
  - plain
  - layered
"""
            )
        )
        bound = {
            c.id: {s.field for s in c.category_colors}
            for c in resolved_board.charts.values()
        }
        assert bound["layered"] == {"category"}, bound

    @staticmethod
    def _layered_vs_plain(layer_extra: str = "") -> dict[str, dict[str, str]]:
        """Color of every scale entry, per chart, for a layered chart drawing a
        subset of the categories next to a plain chart drawing all of them."""
        specs = _specs(
            _board(
                f"""
  plain:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  layered:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
    layers:
      - type: line
        query: all
        x: status
        y: revenue
        label: Trend{layer_extra}
rows:
  - plain
  - layered
"""
            )
        )
        colors = {}
        for chart_id in ("plain", "layered"):
            base = next(
                e
                for e in _walk(specs[chart_id], frozenset({"bar"}))
                if isinstance(e, dict) and e.get("field") == "category"
            )
            scale = base["scale"]
            colors[chart_id] = dict(zip(scale["domain"], scale["range"], strict=True))
        return colors

    def test_the_layer_color_is_no_category_color_on_the_board(self) -> None:
        """The layered chart draws only some categories, so low slots are free
        in its own scale yet still belong to a category on its sibling."""
        colors = self._layered_vs_plain()
        layered = colors["layered"]
        assert "Trend" in layered, layered
        assert len(set(layered.values())) == len(layered), layered
        assert layered["Trend"] not in colors["plain"].values(), colors

    def test_a_layer_with_its_own_color_field_avoids_board_colors_too(self) -> None:
        colors = self._layered_vs_plain("\n        color: status")
        layered = colors["layered"]
        assert {"Open", "Won"} <= layered.keys(), layered
        assert len(set(layered.values())) == len(layered), layered
        for value in ("Open", "Won"):
            assert layered[value] not in colors["plain"].values(), colors

    def test_an_exhausted_palette_rotates_instead_of_repeating(self) -> None:
        from dbt_charts.core.render.chart.emitters._overlay import (
            _free_palette_color,
        )

        palette = ["#aaaaaa", "#bbbbbb", "#cccccc"]
        picks = [_free_palette_color(palette, set(palette), n) for n in (3, 4, 5)]
        assert picks == ["#aaaaaa", "#bbbbbb", "#cccccc"]
        assert _free_palette_color(palette, {"#AAAAAA", "#bbbbbb"}, 9) == "#cccccc"

    _LAYERED_PINNED = _board(
        """
  layered:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    layers:
      - type: line
        query: all
        x: status
        y: revenue
        label: Trend
rows:
  - layered
""",
        style="""
style:
  charts:
    category_colors:
      category:
        values:
          Tools: "#abcdef"
          Electronics: "#123456"
""",
    )

    def test_authored_pins_paint_a_layers_chart(self) -> None:
        spec = _specs(self._LAYERED_PINNED)["layered"]
        base = next(
            e
            for e in _walk(spec, frozenset({"bar"}))
            if isinstance(e, dict) and e.get("field") == "category"
        )
        scale = base["scale"]
        by_value = dict(zip(scale["domain"], scale["range"], strict=True))
        assert by_value["Tools"] == "#abcdef"
        assert by_value["Electronics"] == "#123456"


class TestHistogramNeverBinds:
    """A histogram shares `BarChart`'s Python class with `bar`, but not its
    emitter path: `_emit_histogram` (bar.py) never calls `category_scale_for`
    for its color channel. Counting it toward the two-chart threshold would
    widen the board's domain for a field no emitter reads — exactly the
    kind of drift this planner's threshold gate is meant to catch. `chart.type`, not the
    class, is what `categorical_channel_fields` gates on now.
    """

    def test_two_histograms_sharing_a_color_field_do_not_bind(self) -> None:
        resolved_board, _ = _resolve_board(
            _board(
                """
  h1:
    query: all
    type: histogram
    x: revenue
    color: category
  h2:
    query: subset
    type: histogram
    x: revenue
    color: category
rows:
  - h1
  - h2
"""
            )
        )
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert bound == set(), bound


class TestDashedLineAgreesWithPlainLine:
    """`style.charts.dashes` used to bypass board binding entirely.

    Repro: two line charts on `color: seg`, board slots {A:0,B:1,C:2}. The
    plain line's scale gets an explicit domain+range from
    `spatial_color_scale`. The dashed line's own branch (`line.py`'s
    `_apply_line_color_encoding`) is gated `and not style.dashes`, so it
    skipped that call entirely; the dashes-specific code path further down
    then overwrote `scale.domain` with `dash_domain` (first-seen ROW order)
    and never set a `range` at all -- VL painted the dashed line positionally
    over its own domain instead of the board's slots, so a shared value like
    "B" could paint two different colors depending on which chart drew it.
    """

    def test_shared_series_paints_the_same_color_dashed_or_not(self) -> None:
        # `style.charts.dashes` is a board-level cascade field (no
        # per-chart override), so "plain" and "dashed" get it from two
        # different scopes -- the nested board's local style patch turns
        # dashes on for just the chart inside it, same nesting pattern
        # ``TestNestedTheme`` uses. Both charts still share one board-wide
        # `seg` binding: nested boards count toward the threshold too.
        specs = _specs(
            _board(
                """
  plain:
    query: q
    type: line
    x: month
    y: value
    color: seg
  dashed:
    query: q
    type: line
    x: month
    y: value
    color: seg
rows:
  - plain
  - style:
      charts:
        dashes:
          - [12, 16]
          - [2, 6]
          - []
    cols:
      - dashed
""",
                extra_queries="""
  q:
    type: values
    rows:
      - {month: "2024-01", value: 1, seg: A}
      - {month: "2024-02", value: 2, seg: A}
      - {month: "2024-01", value: 3, seg: B}
      - {month: "2024-02", value: 4, seg: B}
      - {month: "2024-01", value: 5, seg: C}
      - {month: "2024-02", value: 6, seg: C}
""",
            )
        )
        for value in ("A", "B", "C"):
            assert _color_of(specs["plain"], value) == _color_of(
                specs["dashed"], value
            ), value


class TestAuthoredBinding:
    _PINNED = _board(
        """
  full:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
  partial:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - full
  - partial
""",
        style="""
style:
  charts:
    category_colors:
      category:
        values:
          Tools: "#abcdef"
          Electronics: "#123456"
""",
    )

    def test_authored_pins_reach_the_spec(self) -> None:
        specs = _specs(self._PINNED)
        assert _color_of(specs["full"], "Tools") == "#abcdef"
        assert _color_of(specs["partial"], "Tools") == "#abcdef"

    def test_authored_binding_does_not_reorder_the_domain(self) -> None:
        """Pinning a color must never reorder where a chart displays a value.

        The old feature rewrote `scale.domain` to the authored order; that
        desynced the legend/tooltip/aria rank it feeds. Binding only changes
        which color a slot paints — display order stays this plain bar's own
        (alphabetical, VL's default for an unstacked nominal field).
        """
        domain = _color_scale(_specs(self._PINNED)["full"])["domain"]
        assert domain == ["Accessories", "Electronics", "Tools"]


class TestAuthoredYearStringPins:
    """Pins on year-shaped text ("2020") are authored intent, not a time axis.

    Year strings infer as an ordinal color encoding, and the planner treats
    an unauthored, unanimously date-like field as a time axis it must not
    bind. Neither may veto a field the author pinned: those values are
    categories by the author's own word.
    """

    _ROWS = """
  years:
    type: values
    rows:
      - {region: East, year: "2020", revenue: 10}
      - {region: East, year: "2021", revenue: 12}
      - {region: West, year: "2020", revenue: 8}
      - {region: West, year: "2021", revenue: 9}
"""
    _STYLE = """
style:
  charts:
    category_colors:
      year:
        values:
          "2020": "#abcdef"
          "2021": "#123456"
"""

    def _board(self, chart_type: str, extra: str = "") -> str:
        return _board(
            f"""
  a:
    query: years
    type: {chart_type}
    x: region
    y: revenue
    color: year
{extra}
rows:
  - a
""",
            extra_queries=self._ROWS,
            style=self._STYLE,
        )

    def _assert_pinned(self, spec: dict[str, Any]) -> None:
        assert _color_of(spec, "2020") == "#abcdef"
        assert _color_of(spec, "2021") == "#123456"

    def test_a_bar_paints_its_pinned_years(self) -> None:
        self._assert_pinned(_specs(self._board("bar"))["a"])

    def test_a_grouped_bar_paints_its_pinned_years(self) -> None:
        grouped = "    style:\n      stack: none"
        self._assert_pinned(_specs(self._board("bar", grouped))["a"])

    def test_a_horizontal_bar_paints_its_pinned_years(self) -> None:
        horizontal = "    style:\n      orientation: horizontal"
        self._assert_pinned(_specs(self._board("bar", horizontal))["a"])

    def test_a_line_paints_its_pinned_years(self) -> None:
        self._assert_pinned(_specs(self._board("line"))["a"])

    def test_a_scatter_paints_its_pinned_years(self) -> None:
        self._assert_pinned(_specs(self._board("scatter"))["a"])

    def test_a_donut_paints_its_pinned_years(self) -> None:
        board = _board(
            """
  a:
    query: years
    type: donut
    theta: revenue
    color: year
rows:
  - a
""",
            extra_queries=self._ROWS,
            style=self._STYLE,
        )
        svg = _pie_svg(board, "a")
        assert _wedge_fill(svg, "2020") == "#abcdef"
        assert _wedge_fill(svg, "2021") == "#123456"

    def test_unpinned_year_columns_still_never_bind(self) -> None:
        """Two charts sharing a year field is a time axis, not a category set."""
        resolved_board, _ = _resolve_board(
            _board(
                """
  a:
    query: years
    type: bar
    x: region
    y: revenue
    color: year
  b:
    query: years
    type: line
    x: region
    y: revenue
    color: year
rows:
  - a
  - b
""",
                extra_queries=self._ROWS,
            )
        )
        bound = {
            s.field for c in resolved_board.charts.values() for s in c.category_colors
        }
        assert "year" not in bound, bound


class TestLegendStaysHonest:
    _SERIES = _TWO_SERIES_BARS

    def test_legend_matches_what_the_partial_chart_draws(self) -> None:
        """`partial` never draws Accessories — its own domain never grows to fit it.

        Unlike the old board-wide domain, there is nothing here to narrow: a
        chart's domain is already only what it draws, so `legend.values` is
        left absent (VL's own default already follows `scale.domain`).
        """
        enc = _color_enc(_specs(self._SERIES)["partial"])
        assert enc["scale"]["domain"] == ["Electronics", "Tools"]
        assert "values" not in (enc.get("legend") or {})

    def test_legend_keeps_every_category_the_chart_does_draw(self) -> None:
        enc = _color_enc(_specs(self._SERIES)["full"])
        assert enc["scale"]["domain"] == ["Accessories", "Electronics", "Tools"]

    def test_authored_legend_values_survive(self) -> None:
        specs = _specs(
            _board(
                """
  a:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    style:
      legend:
        values: [Tools, Accessories]
  b:
    query: subset
    type: bar
    x: status
    y: revenue
    color: category
rows:
  - a
  - b
"""
            )
        )
        assert _color_enc(specs["a"])["legend"]["values"] == ["Tools", "Accessories"]


class TestGroupedBarPaintOrderIgnoresLegendVisibility:
    """Regression: apply_legend_entry_order no-ops (and returns None) once
    the legend is hidden (visible: false), so a grouped bar's paint-scale
    reorder that relied on THAT return value silently stopped following a
    FULL authored `legend.values` reorder the moment the legend itself was
    hidden. Paint order (scale.domain/xOffset) and legend visibility are
    independent concerns -- a hidden legend must not desync a bar's own
    color/x-position from an authored order still visible everywhere else
    (tooltip, aria)."""

    def test_hidden_legend_still_reorders_scale_domain(self) -> None:
        spec = _specs(
            _board(
                """
  full:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    style:
      legend:
        visible: false
        values: [Tools, Accessories, Electronics]
""",
                style="""
style:
  charts:
    category_colors:
      category:
        values:
          Tools: "#abcdef"
          Electronics: "#123456"
""",
            )
        )["full"]
        enc = _color_enc(spec)
        assert enc.get("legend") is None
        assert enc["scale"]["domain"] == ["Tools", "Accessories", "Electronics"]

    def test_duplicate_authored_entry_still_counts_as_a_full_reorder(self) -> None:
        """Regression: `apply_legend_entry_order` dedupes the resolved list
        before writing `legend["values"]` (`list(dict.fromkeys(resolved))`),
        but `_grouped_bar_paint_order` compared the un-deduped list against
        `series` -- an authored entry repeated (or repeated via a fold
        match, e.g. `tools` re-matching `Tools`) padded `resolved` past
        `series`' length, so `sorted(resolved) == sorted(series)` failed
        and the paint order silently fell back to alphabetical even though
        the legend itself reordered."""
        spec = _specs(
            _board(
                """
  full:
    query: two_cats
    type: bar
    x: status
    y: revenue
    color: category
    style:
      legend:
        values: [Tools, tools, Accessories]
""",
                extra_queries="""
  two_cats:
    type: values
    rows:
      - {category: Accessories, status: Open, revenue: 10}
      - {category: Tools, status: Won, revenue: 20}
""",
                style="""
style:
  charts:
    category_colors:
      category:
        values:
          Tools: "#abcdef"
          Accessories: "#123456"
""",
            )
        )["full"]
        assert _color_scale(spec)["domain"] == ["Tools", "Accessories"]


class TestNestedTheme:
    """A nested board keeps its own theme's hex; the board still agrees on the slot."""

    _NESTED = """
title: Root board
theme: cream

queries:
  q:
    type: values
    rows:
      - {seg: A, x: a, y: 1}
      - {seg: B, x: a, y: 2}

charts:
  root_chart:
    type: bar
    query: q
    x: x
    y: y
    color: seg
  nested_chart:
    type: bar
    query: q
    x: x
    y: y
    color: seg

rows:
  - root_chart
  - theme: stark
    style:
      background: "#ffffff"
    cols:
      - nested_chart
"""

    def test_nested_board_agrees_on_slot_but_paints_its_own_palette(self) -> None:
        from dbt_charts.core.compile.models.style.theme.category_colors import (
            color_at,
        )

        resolved_board, _ = _resolve_board(self._NESTED)
        root_chart = resolved_board.charts["root_chart"]
        nested_chart = resolved_board.charts["nested_chart"]
        root_scale = next(s for s in root_chart.category_colors if s.field == "seg")
        nested_scale = next(s for s in nested_chart.category_colors if s.field == "seg")
        # One board-wide slot assignment, threaded onto every chart_style_context
        # regardless of nesting (execute/category_colors.py's with_category_colors).
        assert root_scale.slots == nested_scale.slots

        specs = _specs(self._NESTED)
        root_a = _color_of(specs["root_chart"], "A")
        nested_a = _color_of(specs["nested_chart"], "A")
        # The rendered fill must be exactly what indexing THIS chart's own
        # palette by the shared slot produces — not merely "some color from
        # the right palette", which a coincidental VL default could satisfy
        # without the render layer ever reading `category_colors` at all.
        assert root_a == color_at(root_scale, "A", root_chart.palette)
        assert nested_a == color_at(nested_scale, "A", nested_chart.palette)
        # But each chart paints its slot out of the palette IT resolved — a
        # nested `theme: stark` board must not inherit the root cream board's hex.
        assert root_a != nested_a


class TestShortChartLocalPalette:
    """A chart-local palette SHORTER than the board's must never wrap slots.

    `TestNestedTheme` above cannot catch this: it uses equal-length palettes
    and asserts via `color_at`, which holds by construction whether or not
    the modulo wrapped. Here `wide` draws all four board-domain categories
    (slots 0-3) against the board's default palette; `narrow` overrides to a
    THREE-color chart-local palette and draws only two of them — a
    genuinely shorter palette, indexing a genuinely out-of-range slot.

    The board's capacity guard (`plan_category_colors`) only measures against
    the board-wide palette — it does not see per-chart `style.color.
    categorical.palette` overrides or nested-board themes, so it cannot
    decline this field at plan time (see `category_colors.py`'s `_slot_for` docstring
    for why). `_bound_scales` (`compile/resolve/chart/_kwargs.py`) closes the
    gap on the render side instead: it drops any scale whose highest slot a
    chart's own EFFECTIVE palette can't seat, so `narrow` simply stays
    unbound and keeps the chart-local coloring it had before this feature
    existed -- the same "don't turn a board that rendered fine into an
    error just because a sibling shares the field" policy
    `plan_category_colors` already applies to palette exhaustion and the
    two-chart threshold. `category_colors.py`'s `_slot_for` raise remains as a
    last-resort guard for anything that still slips through, but the
    ordinary case here must never reach it.
    """

    def test_a_too_short_local_palette_declines_rather_than_raising(self) -> None:
        board = _board(
            """
  wide:
    query: cats4
    type: bar
    x: status
    y: revenue
    color: category
  narrow:
    query: cats_ad
    type: bar
    x: status
    y: revenue
    color: category
    style:
      color:
        categorical:
          palette: ["#111111", "#222222", "#333333"]
rows:
  - wide
  - narrow
""",
            extra_queries="""
  cats4:
    type: values
    rows:
      - {category: A, status: Open, revenue: 10}
      - {category: B, status: Open, revenue: 20}
      - {category: C, status: Open, revenue: 30}
      - {category: D, status: Open, revenue: 40}
  cats_ad:
    type: values
    rows:
      - {category: A, status: Open, revenue: 10}
      - {category: D, status: Open, revenue: 40}
""",
        )
        specs = _specs(board)

        # wide seats all four board slots against its own (longer) palette
        # without incident -- every value still gets a distinct swatch.
        wide_scale = _color_scale(specs["wide"])
        assert len(set(wide_scale["range"])) == len(wide_scale["domain"]) == 4

        # narrow can't seat board slot 3 (D) in a 3-color local palette, so
        # it must stay unbound -- no scale at all, not one that would later
        # fail to resolve a value's color.
        narrow_enc = _color_enc(specs["narrow"])
        assert "scale" not in narrow_enc, narrow_enc

        resolved_board, _ = _resolve_board(board)
        assert resolved_board.charts["narrow"].category_colors == ()


# 10 distinct categories (fills vivid-10 exactly), pinned in the REVERSE of
# their row (data) order — so slot 0 is the *last*-seen value and slot 9 the
# *first*-seen one. Three rows repeat early categories so every share sits at
# ~7.7%, under WEDGE_LABEL_MIN_SHARE (8%): every slice is unlabeled, which is
# the documented "full_table" trigger (pie_attachment.classify_arc_render_mode)
# -- the honest way to force the attached table on, not internals-poking.
_SWATCH_CATEGORIES = [f"V{i}" for i in range(10)]
_SWATCH_DATA_ROWS = "\n".join(
    f"      - {{category: {c}, revenue: 1}}"
    for c in _SWATCH_CATEGORIES + ["V0", "V1", "V2"]
)
_SWATCH_PIN_LINES = "\n".join(
    f'          {v}: "#{i + 1:02x}{i + 1:02x}{i + 1:02x}"'
    for i, v in enumerate(reversed(_SWATCH_CATEGORIES))
)
_SWATCH_MATCH_BOARD = _board(
    """
  d:
    query: many
    type: donut
    theta: revenue
    color: category
rows:
  - d
""",
    extra_queries=f"""
  many:
    type: values
    rows:
{_SWATCH_DATA_ROWS}
""",
    style=f"""
style:
  charts:
    category_colors:
      category:
        values:
{_SWATCH_PIN_LINES}
""",
)


class TestAttachedTableSwatchMatchesWedge:
    """The attached table is the pie's legend: its swatch must come from the
    same value->color resolution that paints the wedge, never from the
    table row's position. The fixture above pins slots in the reverse of
    row order, so a positional (`palette[index % len(palette)]`) swatch
    lookup disagrees with the wedge on almost every row -- a same-order
    fixture would let that bug pass by coincidence.
    """

    def test_slot_order_and_row_order_actually_differ(self) -> None:
        """Guard the fixture itself: a passing swatch assertion below is only
        meaningful if slot order and row order are not secretly the same."""
        resolved_board, executor = _resolve_board(_SWATCH_MATCH_BOARD)
        chart = resolved_board.charts["d"]
        scale = next(s for s in chart.category_colors if s.field == "category")
        data = executor.execute_query(chart.query_name, {})
        row_values = [row["category"] for row in data]
        slot_order = [scale.slots[v] for v in row_values]
        print(f"row order:  {row_values}")
        print(f"slot order: {slot_order}")
        assert slot_order != list(range(len(row_values))), (
            "fixture is vacuous — slot order matches row order"
        )

    def test_every_table_row_swatch_equals_its_own_wedge_fill(self) -> None:
        resolved_board, executor = _resolve_board(_SWATCH_MATCH_BOARD)
        chart = resolved_board.charts["d"]
        assert chart.attached_table is not None, "fixture must trigger full_table"

        data = executor.execute_query(chart.query_name, {})
        svg = _pie_svg(_SWATCH_MATCH_BOARD, "d")
        swatches = re.findall(
            r'<rect width="14" height="14" rx="3" fill="([^"]+)"/></g>'
            r'<text [^>]*data-col="1">[^<]*</text><text [^>]*data-col="2">([^<]*)</text>',
            svg,
        )

        assert len(swatches) == len(data)
        for swatch, name in swatches:
            wedge_fill = _wedge_fill(svg, name)
            assert swatch == wedge_fill, (
                f"table swatch for {name!r} is {swatch!r}, "
                f"but its own wedge paints {wedge_fill!r}"
            )


class TestDataTableStripInk:
    """A cartesian chart's attached data-table strip is that chart's key: its
    dark-companion ink for a series must be the companion of the color that
    series' own marks paint, keyed by board SLOT -- never by the series'
    position in the chart's alphabetically-sorted VL color domain (those two
    orders coincide unless pins scramble them, which is why the fixture below
    pins in reverse-alphabetical order).
    """

    _CATEGORIES = ("Accessories", "Electronics", "Tools")

    _UNPINNED = _board(
        """
  strip:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    style:
      orientation: vertical
rows:
  - strip
"""
    )

    def _pinned_board(self, palette: list[str]) -> str:
        # Reverse-alphabetical pin: the last category claims palette slot 0,
        # so slot order and alphabetical-domain order actively disagree.
        pin_lines = "\n".join(
            f'          {cat}: "{palette[i]}"'
            for i, cat in enumerate(reversed(self._CATEGORIES))
        )
        return _board(
            """
  strip:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    style:
      orientation: vertical
    data_table:
      - per_series: revenue
rows:
  - strip
""",
            style=f"""
style:
  charts:
    category_colors:
      category:
        values:
{pin_lines}
""",
        )

    def _spec_with_strip(self, board: str) -> dict[str, Any]:
        """Render through the real board pipeline, including the data_table
        post-pass -- ``_specs()`` above stops at ``session.finalize_vl``, which
        never attaches the strip (that happens in ``render_resolved_chart``,
        the entry point the production board-render path actually calls).
        """
        from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

        resolved_board, executor = _resolve_board(board)
        chart = resolved_board.charts["strip"]
        data = executor.execute_query(chart.query_name, {})
        artifact = render_resolved_chart(chart, data, resolved_board.style)
        assert isinstance(artifact.payload, dict), artifact
        return artifact.payload

    def test_strip_ink_matches_the_marks_own_companion(self) -> None:
        from dbt_charts.core.compile.models.style.theme.category_colors import (
            ink_at,
        )

        probe_board, _ = _resolve_board(self._UNPINNED)
        palette = list(probe_board.charts["strip"].palette)
        assert len(palette) >= len(self._CATEGORIES)

        board = self._pinned_board(palette)
        resolved_board, _ = _resolve_board(board)
        chart = resolved_board.charts["strip"]
        scale = next(s for s in chart.category_colors if s.field == "category")

        alpha_order = sorted(self._CATEGORIES)
        slot_order = [scale.slots[c] for c in alpha_order]
        print(f"alphabetical order: {alpha_order}")
        print(f"slot order:         {slot_order}")
        assert slot_order != list(range(len(alpha_order))), (
            "fixture is vacuous — slot order matches alphabetical order"
        )

        spec = self._spec_with_strip(board)
        dark_palette = list(chart.style.series_label.dark_companion_palette)
        strip_inks = _strip_label_fills(spec)
        for cat in self._CATEGORIES:
            own_fill = _color_of(spec, cat)
            expected_ink = dark_palette[palette.index(own_fill)]
            assert strip_inks[cat] == expected_ink, (
                f"{cat}: strip ink {strip_inks[cat]!r} is not the companion "
                f"of its own mark fill {own_fill!r} (expected {expected_ink!r})"
            )
            assert strip_inks[cat] == ink_at(scale, cat, dark_palette)


class TestDataTableStripInkRespectsChartLocalPalette:
    """A chart-local ``style.color.categorical.palette`` override changes which
    hexes the chart's marks paint. The data-table strip's ink for that same
    chart must be the dark companion of THAT chart-effective palette -- never
    the board/theme's own default palette, which may not share a single hue
    with the chart-local one.

    A single, unbound chart is enough to reach this: no board-wide slot
    binding is involved, only the mark's own effective palette vs. whatever
    palette the strip reads its ink from.
    """

    _CATEGORIES = ("Accessories", "Electronics", "Tools")
    _LOCAL_PALETTE = ["#cc0000", "#990000", "#660000"]

    _BOARD = _board(
        f"""
  strip:
    query: all
    type: bar
    x: status
    y: revenue
    color: category
    style:
      orientation: vertical
      stack: zero
      color:
        categorical:
          palette: {_LOCAL_PALETTE}
    data_table:
      - per_series: revenue
rows:
  - strip
"""
    )

    def _spec_with_strip(self, board: str) -> dict[str, Any]:
        from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

        resolved_board, executor = _resolve_board(board)
        chart = resolved_board.charts["strip"]
        data = executor.execute_query(chart.query_name, {})
        artifact = render_resolved_chart(chart, data, resolved_board.style)
        assert isinstance(artifact.payload, dict), artifact
        return artifact.payload

    def test_strip_ink_uses_the_chart_local_palette_not_the_board_default(
        self,
    ) -> None:
        resolved_board, _ = _resolve_board(self._BOARD)
        chart = resolved_board.charts["strip"]
        # The fixture's own local palette is nothing like the board default --
        # guard against a future theme change quietly making them overlap.
        board_dark_palette = list(
            resolved_board.style.chart_defaults.dark_companion_palette
        )
        assert list(chart.palette) == self._LOCAL_PALETTE
        chart_dark_palette = list(chart.style.series_label.dark_companion_palette)
        assert not set(chart_dark_palette) & set(board_dark_palette), (
            "fixture is vacuous — chart-local and board dark palettes overlap"
        )

        spec = self._spec_with_strip(self._BOARD)
        strip_inks = _strip_label_fills(spec)
        for cat in self._CATEGORIES:
            own_fill = _color_of(spec, cat)
            assert own_fill in self._LOCAL_PALETTE, own_fill
            expected_ink = chart_dark_palette[self._LOCAL_PALETTE.index(own_fill)]
            assert strip_inks[cat] == expected_ink, (
                f"{cat}: strip ink {strip_inks[cat]!r} is not the companion "
                f"of its own mark fill {own_fill!r} in the chart's own "
                f"effective palette (expected {expected_ink!r}) -- it came "
                "from the board/theme default palette instead"
            )

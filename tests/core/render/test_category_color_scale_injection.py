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
from .._svg_render import render_board_to_svg

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

    def _assert_pinned(self, board: str) -> None:
        svg = render_board_to_svg(board)
        for year, color in (("2020", "#abcdef"), ("2021", "#123456")):
            fills = re.findall(
                rf'fill="([^"]+)"[^>]*aria-label="[^"]*\u2062{year};', svg
            )
            assert fills and set(fills) == {color}, (year, fills)

    def test_a_bar_paints_its_pinned_years(self) -> None:
        self._assert_pinned(self._board("bar"))

    def test_a_grouped_bar_paints_its_pinned_years(self) -> None:
        grouped = "    style:\n      stack: none"
        self._assert_pinned(self._board("bar", grouped))

    def test_a_horizontal_bar_paints_its_pinned_years(self) -> None:
        horizontal = "    style:\n      orientation: horizontal"
        self._assert_pinned(self._board("bar", horizontal))

    def test_a_line_paints_its_pinned_years(self) -> None:
        self._assert_pinned(self._board("line"))

    def test_a_scatter_paints_its_pinned_years(self) -> None:
        self._assert_pinned(self._board("scatter"))

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

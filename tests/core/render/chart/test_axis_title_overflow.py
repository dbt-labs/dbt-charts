"""Axis titles are bounded by the extent of the axis they label.

A left/right axis title is rendered rotated, so its pixel length is consumed
from the chart's *height*; a bottom/top title consumes *width*. Under
``autosize: fit`` Vega-Lite holds the outer size fixed and shrinks the plot to
make room — so an unbounded axis title collapses the plot to nothing and pushes
the chart title out of the SVG.

These tests pin the bound: the title is pre-wrapped to at most two lines that
fit the axis extent, and a title that already fits is left untouched.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.primitives import ResolvedFontStyle
from dbt_charts.core.compile.models.style.resolved._base import ResolvedAxisStyle
from dbt_charts.core.compile.models.style.resolved.bar import ResolvedBarStyle
from dbt_charts.core.compile.models.style.resolved.heatmap import ResolvedHeatmapStyle
from dbt_charts.core.compile.models.style.resolved.scatter import ResolvedScatterStyle
from dbt_charts.core.compile.resolve.chart._axes import _bake_cartesian_axes
from dbt_charts.core.compile.resolve.chart._chart_rows import regroup
from dbt_charts.core.compile.resolve.style.axis_cascade import (
    AxisOverrides,
    build_resolved_axis,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.font_measure import get_font_measurer
from dbt_charts.core.render.chart.emitters._cartesian import (
    axis_title_budget,
    resolve_xy_titles,
)
from dbt_charts.core.render.chart.spec import RenderBox
from dbt_charts.core.render.chart.vl_field_maps import axis_to_vl

from ...conftest import fixture_chart_for_type
from .test_render_emitters import _C, _make_resolved_axes

LONG = "Connections Timeline Count Connections Observed Across All Categories"


def _axes() -> tuple[ResolvedAxisStyle, ResolvedAxisStyle]:
    chart_style_context = resolve_chart_style_context(
        get_theme_style(get_default_theme_name())
    )
    ax_merged, ay_merged, ax_band_position, ay_band_position, _, _, _ = (
        _bake_cartesian_axes(
            chart_style_context,
            fixture_chart_for_type("line"),
            "line",
            "temporal",
            "quantitative",
            AxisOverrides(),
        )
    )
    return (
        build_resolved_axis(
            ax_merged,
            band_position=ax_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
        build_resolved_axis(
            ay_merged,
            band_position=ay_band_position,
            chart_id="test",
            format_authored=True,
            format_is_alias=False,
        ),
    )


def _fits(title: str | list[str] | None, budget: int, font: ResolvedFontStyle) -> bool:
    """Every rendered line measures within the budget."""
    lines = title if isinstance(title, list) else [title or ""]
    measurer = get_font_measurer(font.family)
    return all(measurer.measure(line, font.size) <= budget for line in lines)


def test_long_y_title_wraps_to_fit_the_chart_height() -> None:
    """A rotated title longer than the plot is wrapped, not left to collapse it."""
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month", "observed", None, LONG, ax, ay, RenderBox(width=500, height=300), ""
    )
    assert isinstance(titles.y_title, list)
    assert len(titles.y_title) <= 2
    assert _fits(titles.y_title, axis_title_budget(300), ay.title.font)


def test_long_x_title_wraps_against_width_not_height() -> None:
    """A bottom/top title consumes width, so its budget comes from the width."""
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month", "observed", LONG, None, ax, ay, RenderBox(width=200, height=900), ""
    )
    assert isinstance(titles.x_title, list)
    assert _fits(titles.x_title, axis_title_budget(200), ax.title.font)


def test_title_that_already_fits_is_left_alone() -> None:
    """The wrap is inert on charts that render correctly today.

    This is what keeps golden churn to genuinely-broken charts: a title with
    room to spare must come back as a plain string, byte-identical to before.
    """
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month", "observed", None, LONG, ax, ay, RenderBox(width=500, height=900), ""
    )
    assert titles.y_title == LONG


def test_unbreakable_word_is_ellipsized_never_split() -> None:
    """A word longer than the budget is cut with an ellipsis, not broken in two.

    Splitting inside a word is wrong output on any surface, and the pieces can
    land inside ``max_lines``, which leaves the cut unflagged as well.
    """
    ax, ay = _axes()
    word = "Supercalifragilisticexpialidociousandthensomemorecharacters"
    titles = resolve_xy_titles(
        "month", "observed", None, word, ax, ay, RenderBox(width=500, height=220), ""
    )
    assert _fits(titles.y_title, axis_title_budget(220), ay.title.font)
    assert isinstance(titles.y_title, str)
    assert titles.y_title.endswith("…")


def test_short_word_over_its_budget_is_ellipsized_not_split() -> None:
    """An everyday one-word title, not just a pathological one.

    ``commits`` at 110px is only a few pixels over its budget, so a character
    break would land in exactly two chunks — inside ``max_lines``, where the
    line count alone reports nothing.
    """
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month",
        "observed",
        None,
        "commits",
        ax,
        ay,
        RenderBox(width=500, height=110),
        "",
    )
    assert isinstance(titles.y_title, str)
    assert titles.y_title.endswith("…")
    assert "commits".startswith(titles.y_title.rstrip("…"))


def test_over_wide_word_does_not_displace_the_words_that_fit() -> None:
    """Only the word that does not fit is cut; the ones that do survive whole.

    A character break spills the long word's own remainder onto line two, which
    then swallows the following word (``xpialidocious com…``). Cutting the word
    where it starts leaves line two to the word that fits.
    """
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month",
        "observed",
        None,
        "Supercalifragilisticexpialidocious commits",
        ax,
        ay,
        RenderBox(width=500, height=180),
        "",
    )
    assert isinstance(titles.y_title, list)
    assert titles.y_title[0].endswith("…")
    assert titles.y_title[1] == "commits"
    assert _fits(titles.y_title, axis_title_budget(180), ay.title.font)


def test_row_facet_panel_is_not_charged_whole_chart_chrome() -> None:
    """A panel pays its share of the chrome, not the whole chart's.

    7 row panels of a 770px card get 110px each. The chrome outside the plot
    (title block, view padding) is bought once for the whole card, so charging
    each panel the full 72px leaves a 38px budget it never lost — and a title
    that fits the panel fine gets cut.
    """
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month",
        "observed",
        None,
        "commits",
        ax,
        ay,
        RenderBox(width=500, height=110, panel_rows=7),
        "",
    )
    assert titles.y_title == "commits"


def test_column_facet_panel_is_not_charged_whole_chart_chrome() -> None:
    """The same share applies on the width axis, which the x title measures.

    A column facet draws its own x axis inside every panel, so the title is
    per-panel there exactly as the y title is under a row facet. 7 column
    panels of an 800px card get 97px each: charging all 72px leaves 25px and
    ellipsizes to three characters; a seventh of it leaves 86px and the title
    renders whole.
    """
    ax, ay = _axes()
    titles = resolve_xy_titles(
        "month",
        "observed",
        "commits",
        None,
        ax,
        ay,
        RenderBox(width=97, height=500, panel_cols=7),
        "",
    )
    assert titles.x_title == "commits"


def test_budget_is_conservative_against_the_extent() -> None:
    """The budget leaves room for the layout chrome outside the plot."""
    assert axis_title_budget(300) < 300
    assert axis_title_budget(900) < 900
    # Degenerate extents never produce a negative or zero budget.
    assert axis_title_budget(10) >= 1
    # One panel out of seven owes a seventh of the chrome, not all of it.
    assert axis_title_budget(110, 7) > axis_title_budget(110)


def test_axis_to_vl_emits_title_limit_from_max_width() -> None:
    """``axis.title.max_width`` reaches Vega-Lite as ``titleLimit``.

    Declared on ``AxisElementStyle`` for both label and title, but historically
    read only on the label side — authoring it on the title was silently
    discarded.

    Needs ``visible=True``: the shipped themes suppress the axis-level title
    (``title: null``) and let the text flow from the encoding instead, and VL
    reads no other axis-title property once the title is nulled.
    """
    _, ay = _axes()
    patched = replace(ay, title=replace(ay.title, max_width=123.0, visible=True))
    assert axis_to_vl(patched)["titleLimit"] == 123.0


def test_axis_to_vl_omits_title_limit_when_unset() -> None:
    _, ay = _axes()
    assert "titleLimit" not in axis_to_vl(
        replace(ay, title=replace(ay.title, visible=True))
    )


# ── Emitter-level coverage ────────────────────────────────────────────────────
# The unit tests above drive resolve_xy_titles directly, which cannot catch a
# family emitter that never calls it. Bar is the family the bug was reported on,
# and its horizontal path swaps channels, so both paths are pinned here through
# BarEmitter itself.


def _bar_chart(bar_style: ResolvedBarStyle, orientation: str, **labels: str) -> Any:
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart

    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    return ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="revenue",
        orientation=orientation,
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, **labels},
    )


def test_vertical_bar_emitter_wraps_a_long_y_label(
    bar_style: ResolvedBarStyle,
) -> None:
    """The reported case, driven through the emitter rather than the helper."""
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_chart(bar_style, "vertical", y_label=LONG)
    spec = BarEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"month": "Jan", "revenue": 5}]),
    )
    assert isinstance(spec.encoding["y"]["title"], list)


def test_horizontal_bar_emitter_wraps_the_authored_y_label_on_vl_x(
    bar_style: ResolvedBarStyle,
) -> None:
    """Horizontal bar flips the axes: authored y_label renders on the VL x channel.

    Pins the channel order, which is easy to invert and otherwise unguarded.
    """
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    # Narrow, not short: the measure title runs horizontally here, so it is
    # width that has to bind for the wrap to be observable at all.
    chart = _bar_chart(bar_style, "horizontal", y_label=LONG)
    spec = BarEmitter().emit(
        chart,
        RenderBox(width=200.0, height=600.0),
        regroup((), [{"month": "Jan", "revenue": 5}]),
    )
    x_title = spec.encoding["x"]["title"]
    assert isinstance(x_title, list)
    assert x_title[0] in LONG  # the authored y_label, not the x_label


def test_bar_emitter_leaves_a_short_label_unwrapped(
    bar_style: ResolvedBarStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_chart(bar_style, "vertical", y_label="Revenue")
    spec = BarEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"month": "Jan", "revenue": 5}]),
    )
    assert spec.encoding["y"]["title"] == "Revenue"


def test_blank_axis_label_does_not_crash_the_render(
    bar_style: ResolvedBarStyle,
) -> None:
    """``y_label: " "`` is valid authored YAML and must stay a blank title.

    wrap_text_precise strips whitespace and returns no lines at all, so an
    unguarded wrap raises IndexError from a library internal.
    """
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    chart = _bar_chart(bar_style, "vertical", y_label="   ")
    spec = BarEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"month": "Jan", "revenue": 5}]),
    )
    assert spec.encoding["y"]["title"] == "   "


def test_multi_metric_line_wraps_its_x_label(line_style: Any) -> None:
    """``y: [a, b]`` dispatches to a separate emit path that built titles inline.

    That path is a very common chart shape and collapsed identically, so it is
    pinned here rather than left to the single-series tests above.
    """
    from dbt_charts.core.compile.models.chart.resolved.line import ResolvedLineChart
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_VALUE_FIELD,
    )
    from dbt_charts.core.render.chart.emitters.line import LineEmitter

    ax, ay = _make_resolved_axes("line", "ordinal", "quantitative")
    chart: Any = ResolvedLineChart(
        panel_axes=(),
        id="l",
        chart_type="line",
        x="month",
        y=WIDE_VALUE_FIELD,
        wide_measures=("revenue", "cost"),
        x_label=LONG,
        style=line_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = LineEmitter().emit(
        chart,
        RenderBox(width=260.0, height=400.0),
        regroup((), [{"month": "Jan", "revenue": 5, "cost": 2}]),
    )
    assert isinstance(spec.encoding["x"]["title"], list)


def test_multi_metric_area_wraps_its_y_label(area_style: Any) -> None:
    from dbt_charts.core.compile.models.chart.resolved.area import ResolvedAreaChart
    from dbt_charts.core.compile.resolve.chart._wide_fields import (
        WIDE_VALUE_FIELD,
    )
    from dbt_charts.core.render.chart.emitters.area import AreaEmitter

    ax, ay = _make_resolved_axes("area", "ordinal", "quantitative")
    chart: Any = ResolvedAreaChart(
        panel_axes=(),
        id="a",
        chart_type="area",
        x="month",
        y=WIDE_VALUE_FIELD,
        wide_measures=("revenue", "cost"),
        y_label=LONG,
        style=area_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = AreaEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"month": "Jan", "revenue": 5, "cost": 2}]),
    )
    assert isinstance(spec.encoding["y"]["title"], list)


def test_layered_bar_base_legend_label_is_not_a_wrapped_list(
    bar_style: ResolvedBarStyle,
) -> None:
    """The overlay's base-series legend label is data, not layout text.

    ``render_cartesian_overlay`` reads the base y-encoding title and feeds it to
    ``color.datum`` and the shared scale domain. VL's ``datum`` takes a
    primitive, so a wrapped ``list[str]`` there yields a broken legend entry on
    an ordinary layered chart with a long y title.
    """
    from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.models.chart.resolved.bar import ResolvedBarChart
    from dbt_charts.core.render.chart.emitters.bar import BarEmitter

    line_marks = resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.line.marks
    ax, ay = _make_resolved_axes("bar", "ordinal", "quantitative")
    chart: Any = ResolvedBarChart(
        panel_axes=(),
        id="b",
        chart_type="bar",
        x="month",
        y="revenue",
        y_label=LONG,
        orientation="vertical",
        layers=(
            ResolvedLineLayer(
                type="line",
                line_mark=line_marks.line,
                point_mark=line_marks.point,
                y="target",
                label="Target",
            ),
        ),
        style=bar_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = BarEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"month": "Jan", "revenue": 5, "target": 7}]),
    )
    datums = [
        color["datum"]
        for layer in (spec.layers or [])
        if isinstance(color := (layer.encoding or {}).get("color"), dict)
        and "datum" in color
    ]
    assert datums, "expected the overlay to emit color.datum entries"
    for datum in datums:
        assert isinstance(datum, str), (
            f"legend datum must be a primitive, got {datum!r}"
        )
    # The shared scale domain keys off the same labels — no nested arrays there.
    for layer in spec.layers or []:
        scale = ((layer.encoding or {}).get("color") or {}).get("scale") or {}
        for entry in scale.get("domain", []):
            assert isinstance(entry, str), (
                f"scale domain entry not a primitive: {entry!r}"
            )


# ── Scatter emitter coverage ──────────────────────────────────────────────────


def _scatter_chart(scatter_style: ResolvedScatterStyle, **labels: str) -> Any:
    from dbt_charts.core.compile.models.chart.resolved.scatter import (
        ResolvedScatterChart,
    )

    ax, ay = _make_resolved_axes("scatter", "quantitative", "quantitative")
    ay = replace(ay, is_quantitative=True, zero_anchored=True)
    return ResolvedScatterChart(
        panel_axes=(),
        id="s",
        chart_type="scatter",
        x="x_val",
        y="y_val",
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, **labels},
    )


def test_scatter_emitter_wraps_a_long_y_label(
    scatter_style: ResolvedScatterStyle,
) -> None:
    """A long y_label on scatter in a short slot is wrapped, not left to collapse it."""
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    chart = _scatter_chart(scatter_style, y_label=LONG)
    spec = ScatterEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"x_val": 1.0, "y_val": 2.0}]),
    )
    assert isinstance(spec.encoding["y"]["title"], list)


def test_scatter_emitter_wraps_a_long_x_label(
    scatter_style: ResolvedScatterStyle,
) -> None:
    """A long x_label on scatter in a narrow slot is wrapped."""
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    chart = _scatter_chart(scatter_style, x_label=LONG)
    spec = ScatterEmitter().emit(
        chart,
        RenderBox(width=200.0, height=500.0),
        regroup((), [{"x_val": 1.0, "y_val": 2.0}]),
    )
    assert isinstance(spec.encoding["x"]["title"], list)


def test_scatter_emitter_y_plain_stays_primitive_when_y_title_wraps(
    scatter_style: ResolvedScatterStyle,
) -> None:
    """The overlay's base-series legend label is data, not layout text.

    After bounding, y_enc["title"] may be a list[str], but base_label must stay
    a primitive — VL's color.datum accepts only a scalar, and a nested list
    yields a broken legend entry on a layered scatter chart with a long y label.
    """
    from dbt_charts.core.compile.models.chart.resolved._layer import ResolvedLineLayer
    from dbt_charts.core.compile.models.chart.resolved.scatter import (
        ResolvedScatterChart,
    )
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    line_marks = resolve_style(
        get_theme_style(get_default_theme_name())
    ).chart_defaults.line.marks
    ax, ay = _make_resolved_axes("scatter", "quantitative", "quantitative")
    ay = replace(ay, is_quantitative=True, zero_anchored=True)
    chart: Any = ResolvedScatterChart(
        panel_axes=(),
        id="s",
        chart_type="scatter",
        x="x_val",
        y="y_val",
        y_label=LONG,
        layers=(
            ResolvedLineLayer(
                type="line",
                line_mark=line_marks.line,
                point_mark=line_marks.point,
                y="target",
                label="Target",
            ),
        ),
        style=scatter_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **_C,
    )
    spec = ScatterEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"x_val": 1.0, "y_val": 2.0, "target": 3.0}]),
    )
    datums = [
        color["datum"]
        for layer in (spec.layers or [])
        if isinstance(color := (layer.encoding or {}).get("color"), dict)
        and "datum" in color
    ]
    assert datums, "expected the overlay to emit color.datum entries"
    for datum in datums:
        assert isinstance(datum, str), (
            f"legend datum must be a primitive, got {datum!r}"
        )


# ── Heatmap emitter coverage ──────────────────────────────────────────────────


def _heatmap_chart(heatmap_style: ResolvedHeatmapStyle, **labels: str) -> Any:
    from dbt_charts.core.compile.models.chart.resolved.heatmap import (
        ResolvedHeatmapChart,
    )

    ax, ay = _make_resolved_axes("heatmap", "nominal", "nominal")
    return ResolvedHeatmapChart(
        panel_axes=(),
        id="h",
        chart_type="heatmap",
        x="col",
        y="row",
        style=heatmap_style.model_copy(update={"axis_x": ax, "axis_y": ay}),
        **{**_C, **labels},
    )


def test_heatmap_emitter_wraps_a_long_y_label(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """A long y_label on heatmap in a short slot is wrapped."""
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    chart = _heatmap_chart(heatmap_style, y_label=LONG)
    spec = HeatmapEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"col": "A", "row": "X"}]),
    )
    assert isinstance(spec.encoding["y"]["title"], list)


def test_heatmap_emitter_wraps_a_long_x_label(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    """A long x_label on heatmap in a narrow slot is wrapped."""
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    chart = _heatmap_chart(heatmap_style, x_label=LONG)
    spec = HeatmapEmitter().emit(
        chart,
        RenderBox(width=200.0, height=500.0),
        regroup((), [{"col": "A", "row": "X"}]),
    )
    assert isinstance(spec.encoding["x"]["title"], list)


def test_scatter_emitter_leaves_a_short_label_unwrapped(
    scatter_style: ResolvedScatterStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters.scatter import ScatterEmitter

    chart = _scatter_chart(scatter_style, y_label="Revenue")
    spec = ScatterEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"x_val": 1.0, "y_val": 2.0}]),
    )
    assert spec.encoding["y"]["title"] == "Revenue"


def test_heatmap_emitter_leaves_a_short_label_unwrapped(
    heatmap_style: ResolvedHeatmapStyle,
) -> None:
    from dbt_charts.core.render.chart.emitters.heatmap import HeatmapEmitter

    chart = _heatmap_chart(heatmap_style, y_label="Region")
    spec = HeatmapEmitter().emit(
        chart,
        RenderBox(width=500.0, height=220.0),
        regroup((), [{"x_val": "Jan", "y_val": "West", "v": 1.0}]),
    )
    assert spec.encoding["y"]["title"] == "Region"

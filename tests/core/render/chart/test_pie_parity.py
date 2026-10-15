"""Pie/donut paint contracts the engine keeps: finalized slice labels, label
ink, the center total, and the resolve-time row snapshot.

The label and color decisions are baked at resolve (`compile/resolve/chart/pie.py`);
these tests read them back off the engine-drawn SVG.
"""

from __future__ import annotations

import html
import re
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.authored import ChartTotal
from dbt_charts.core.compile.models.chart.normalized import PieChart
from dbt_charts.core.compile.models.chart.resolved.pie import ResolvedPieChart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.diagnostics import ERR_RESOLVED_PIE_DATA_MISMATCH
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.errors import RenderError

from ..._engine_render import render_pie_svg

_SLICE_LABEL = re.compile(
    r'<text [^>]*fill="([^"]+)"[^>]*aria-roledescription="text mark">(.*?)</text>'
)


def _make_pie_style() -> Any:
    return resolve_chart_style_context(get_theme_style(get_default_theme_name()))


def _resolved(
    chart: PieChart, data: list[dict[str, Any]], board: Any = None
) -> ResolvedPieChart:
    resolved = resolve(chart, data, board or _make_pie_style(), width=600.0)
    assert isinstance(resolved, ResolvedPieChart)
    return resolved


def _slice_labels(svg: str) -> list[tuple[str, list[str]]]:
    """Each slice label's fill and its tspan lines, in paint order."""
    return [
        (fill, [html.unescape(t) for t in re.findall(r">([^<]*)</tspan>", body)])
        for fill, body in _SLICE_LABEL.findall(svg)
    ]


def _render(chart: ResolvedPieChart, data: list[dict[str, Any]]) -> str:
    return render_pie_svg(chart, data, width=chart.resolution_width)


# ── finalized slice labels ─────────────────────────────────────────────────────


def test_authored_template_paints_finalized_label_lines() -> None:
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={
            "marks": {
                "slice": {
                    "labels": {
                        "template": "{{ percent | format('.0%') }}\n"
                        "${{ (value / 1000) | format('.0f') }}k"
                    }
                }
            }
        },
    )
    data = [{"segment": "A", "value": 76880}, {"segment": "B", "value": 47120}]
    lines = [lines for _, lines in _slice_labels(_render(_resolved(chart, data), data))]
    assert lines == [["62%", "$77k"], ["38%", "$47k"]]


def test_a_where_suppressed_slice_paints_no_label() -> None:
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={
            "marks": {
                "slice": {
                    "labels": {
                        "template": "{{ segment }}",
                        "where": "{{ value > 60 }}",
                    }
                }
            }
        },
    )
    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    labels = _slice_labels(_render(_resolved(chart, data), data))
    assert [lines for _, lines in labels] == [["A"]]


def test_rows_different_from_the_resolution_snapshot_are_refused() -> None:
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={
            "marks": {
                "slice": {
                    "labels": {
                        "template": "{{ segment }}={{ value }}",
                        "where": "{{ value > 5 }}",
                    }
                }
            }
        },
    )
    resolved = _resolved(
        chart, [{"segment": "A", "value": 90}, {"segment": "B", "value": 10}]
    )
    with pytest.raises(RenderError) as exc_info:
        render_resolved_chart(
            resolved,
            [{"segment": "A", "value": 1}, {"segment": "B", "value": 99}],
            resolve_style(get_theme_style(get_default_theme_name())),
        )
    assert exc_info.value.code == ERR_RESOLVED_PIE_DATA_MISMATCH


# ── resolve: dark-companion label ink ──────────────────────────────────────────


def test_resolve_pie_no_color_bakes_dark_companion_of_palette_zero() -> None:
    """No-color pie bakes a single dark-companion stop for palette[0], mirroring
    the per-distinct-value bake the multi-series (color-channel) branch already
    does — so single-color labels can anchor to the wedge instead of the theme's
    static label.font color."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.palette import label_ink

    board = _make_pie_style()
    chart = PieChart(id="p", type="pie", theta="value")
    resolved = resolve(
        chart,
        [{"value": 90}, {"value": 10}],
        board,
        width=600.0,
    )

    expected = (label_ink(board.palette[0], board.background),)
    assert resolved.dark_companion_stops == expected
    assert len(resolved.dark_companion_stops) == 1


def test_resolve_pie_rejects_empty_resolved_palette_on_direct_path() -> None:
    """An explicit empty categorical-palette override must raise loudly, not
    silently bake an empty dark_companion_stops (which would leave the arc
    fill / label fill with nothing to index) — same contract the
    attached-table path already enforces, now also checked on the direct
    (no attached table) path this change newly depends on."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.diagnostics.chart_data import ChartDataError

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"color": {"categorical": {"palette": []}}},
    )
    with pytest.raises(ChartDataError):
        resolve(chart, [{"value": 90}, {"value": 10}], board, width=600.0)


def test_resolve_pie_no_color_does_not_bake_authored_slice_label_color() -> None:
    """dark_companion_stops always reflects the plain dark companion of
    palette[0] — the authored override is applied later, in the emitter, now
    that the merged SliceLabelsStyle.font.color sentinel is real and doesn't
    need patch-peeking at resolve time."""
    from dbt_charts.core.compile.models.chart.normalized import PieChart
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.palette import label_ink

    board = _make_pie_style()
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    resolved = resolve(chart, [{"value": 90}, {"value": 10}], board, width=600.0)

    expected = (label_ink(board.palette[0], board.background),)
    assert resolved.dark_companion_stops == expected


# ── slice-label ink ────────────────────────────────────────────────────────────


def _label_fills(
    chart: PieChart, data: list[dict[str, Any]], board: Any = None
) -> set[str]:
    return {
        fill for fill, _ in _slice_labels(_render(_resolved(chart, data, board), data))
    }


def test_no_color_honors_chart_local_authored_slice_label_color() -> None:
    """An explicit style.marks.slice.labels.font.color on a no-color pie wins
    over the dark-companion default."""
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    assert _label_fills(chart, [{"value": 90}, {"value": 10}]) == {"#333333"}


def test_no_color_honors_family_tier_authored_slice_label_color() -> None:
    """The authored override wins at the pie family tier (`charts.pie.marks`)."""
    theme = get_theme_style(get_default_theme_name())
    labels = resolve_chart_style_context(theme).pie.marks.slice.labels
    new_labels = labels.model_copy(
        update={"font": labels.font.model_copy(update={"color": "#444444"})}
    )
    new_slice = theme.charts.pie.marks.slice.model_copy(update={"labels": new_labels})
    new_marks = theme.charts.pie.marks.model_copy(update={"slice": new_slice})
    new_pie = theme.charts.pie.model_copy(update={"marks": new_marks})
    new_theme = theme.model_copy(
        update={"charts": theme.charts.model_copy(update={"pie": new_pie})}
    )
    chart = PieChart(id="p", type="pie", theta="value")
    board = resolve_chart_style_context(new_theme)
    assert _label_fills(chart, [{"value": 90}, {"value": 10}], board) == {"#444444"}


def test_no_color_honors_global_tier_authored_slice_label_color() -> None:
    """The authored override wins at the outermost `charts.marks` tier."""
    theme = get_theme_style(get_default_theme_name())
    global_labels = theme.charts.marks.slice.labels
    new_font = global_labels.font.model_copy(update={"color": "#555555"})
    new_labels = global_labels.model_copy(update={"font": new_font})
    new_slice = theme.charts.marks.slice.model_copy(update={"labels": new_labels})
    new_marks = theme.charts.marks.model_copy(update={"slice": new_slice})
    new_theme = theme.model_copy(
        update={"charts": theme.charts.model_copy(update={"marks": new_marks})}
    )
    chart = PieChart(id="p", type="pie", theta="value")
    board = resolve_chart_style_context(new_theme)
    assert _label_fills(chart, [{"value": 90}, {"value": 10}], board) == {"#555555"}


def test_no_color_unauthored_still_uses_dark_companion() -> None:
    chart = PieChart(id="p", type="pie", theta="value")
    data = [{"value": 90}, {"value": 10}]
    resolved = _resolved(chart, data)
    fills = {fill for fill, _ in _slice_labels(_render(resolved, data))}
    assert fills == {resolved.dark_companion_stops[0]}


def test_color_channel_ignores_authored_slice_label_color() -> None:
    """Multi-series pies ink each label with its own wedge's dark companion;
    an authored font.color never applies."""
    chart = PieChart(
        id="p",
        type="pie",
        theta="value",
        color="segment",
        style={"marks": {"slice": {"labels": {"font": {"color": "#333333"}}}}},
    )
    data = [{"segment": "A", "value": 90}, {"segment": "B", "value": 10}]
    resolved = _resolved(chart, data)
    fills = [fill for fill, _ in _slice_labels(_render(resolved, data))]
    assert len(fills) == len(data)
    assert "#333333" not in fills
    assert set(fills) <= set(resolved.dark_companion_stops)


# ── center total ────────────────────────────────────────────────────────────────


def _donut(**kwargs: Any) -> PieChart:
    style = {"inner_radius": 0.6, **kwargs.pop("style", {})}
    return PieChart(
        id="p", type="donut", theta="value", color="segment", style=style, **kwargs
    )


def test_total_paints_the_formatted_sum() -> None:
    chart = _donut(
        total=ChartTotal(visible=True, label="Total Revenue"),
        style={"total": {"value": {"format": "$,.0f"}}},
    )
    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    svg = _render(_resolved(chart, data), data)
    assert re.search(r'layer_1_marks"[^>]*><text [^>]*>\$150</text>', svg), svg


def test_total_label_paints_centered_under_the_value() -> None:
    chart = _donut(total=ChartTotal(visible=True, label="Total Revenue"))
    data = [{"segment": "A", "value": 100}]
    svg = _render(_resolved(chart, data), data)
    value = re.search(r'layer_1_marks"[^>]*><text x="([\d.]+)" y="([\d.]+)"', svg)
    label = re.search(
        r'layer_2_marks"[^>]*><text x="([\d.]+)" y="([\d.]+)"[^>]*>Total Revenue<', svg
    )
    assert value is not None and label is not None, svg
    assert label.group(1) == value.group(1)
    assert float(label.group(2)) > float(value.group(2))


def test_no_slice_labels_when_labels_are_none() -> None:
    """With no slice-label style (the attached-table wheel), only the center
    total's texts paint."""
    chart = _donut(total=ChartTotal(visible=True, label="Total Value"))
    data = [{"segment": "A", "value": 100}, {"segment": "B", "value": 50}]
    resolved = _resolved(chart, data)
    unlabeled = resolved.model_copy(
        update={
            "style": resolved.style.model_copy(
                update={
                    "slice_mark": resolved.style.slice_mark.model_copy(
                        update={"labels": None}
                    )
                }
            ),
            "slice_label_indices": (),
        }
    )
    svg = _render(unlabeled, data)
    assert _slice_labels(svg) == []
    assert ">Total Value</text>" in svg


def test_resolved_slice_labels_font_color_stays_none_when_unauthored() -> None:
    """Cascade-managed sentinel regression guard: family/size/weight still
    inherit from charts.font when unauthored, while color does not."""
    board = _make_pie_style()
    labels = board.pie.marks.slice.labels

    assert labels.font.color is None
    assert labels.font.family is not None
    assert labels.font.size is not None
    assert labels.font.weight is not None

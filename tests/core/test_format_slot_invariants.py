"""Invariant test covering every currency-affix format slot in one pass."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

import dbt_charts.core.compile.models as _models_pkg
from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.markers import Format
from dbt_charts.core.render.format_utils import format_value
from dbt_charts.core.text.format_d3 import format_d3, is_d3_si_spec
from dbt_charts.core.text.numeral_scale import sub_unit_digit_format
from dbt_charts.core.text.predefined_formats import PREDEFINED_SPECS

from ._svg_render import render_board_to_svg

# Slot derivation -- from the model classes, never a hand-written list or a board-
# instance walk (see module docstring for why the latter undercounts).

# Named exclusion rule: a `Resolved*` class is render's own internal representation,
# never something a board author writes into (it is the *output* of resolving an
# authored class, not an authored surface itself) -- e.g.
# ResolvedTableColumnConfig.format is TableColumnConfig.format post-cascade, not a
# second authorable slot.
_EXCLUDED_CLASS_PREFIXES = ("Resolved",)


def schema_format_fields() -> set[tuple[str, str]]:
    """Every ``(ClassName, field_name)`` pair carrying a ``Format()`` facet, walked live
    off the model classes under ``core.compile.models``.
    """
    found: set[tuple[str, str]] = set()
    for module_info in pkgutil.walk_packages(
        _models_pkg.__path__, _models_pkg.__name__ + "."
    ):
        module = importlib.import_module(module_info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if not (issubclass(cls, BaseModel) and cls.__module__ == module.__name__):
                continue
            for name, field in cls.model_fields.items():
                if any(isinstance(m, Format) for m in field.metadata):
                    found.add((cls.__name__, name))
    return found


def _is_excluded(class_field: tuple[str, str]) -> bool:
    # time_format is a strftime slot with no FormatConfig arm, so no affix
    # shape reaches it.
    return class_field[1] == "time_format" or class_field[0].startswith(
        _EXCLUDED_CLASS_PREFIXES
    )


# Value bands and format shapes.

BANDS: dict[str, float] = {
    "sub_one": 0.67,
    "kpi_band": 154.25,
    "large": 12_400_000.0,
    "negative": -41_500.0,
    "zero": 0.0,
}

# The one documented exception to I3 (see _check_affix_only_changes_affix).
_I3_EXEMPT_BAND = "sub_one"

_SHAPE_CONTENT: dict[str, dict[str, str]] = {
    "spec_less": {"prefix": "£"},
    "spec_prefix": {"spec": ",.2f", "prefix": "£"},
    "spec_suffix": {"spec": ",.0f", "suffix": " €"},
    "native_affix": {"spec": "$,.0f", "prefix": "US "},
}
# The engine's own house formats, authored as a bare predefined name: no affix, so I3's
# "digits match the no-format render" does not apply.
PRESETS: tuple[str, ...] = (
    "currency",
    "currency_whole",
    "integer",
    "number",
    "percent",
)
# percent formats expect a 0-1 ratio and reject anything 0-100-shaped
# (ERR-PERCENT-RANGE), so only the ratio-shaped bands render it.
_PERCENT_BANDS = ("sub_one", "zero")
SHAPES: tuple[str, ...] = (
    "none",
    "spec_less",
    "spec_prefix",
    "spec_suffix",
    "alias",
    "native_affix",
    *PRESETS,
)


def _shape_value(shape: str) -> tuple[Any, dict[str, Any] | None]:
    """(value to author at the slot's path, board-level style.formats addition).

    "none" authors nothing (path value is None -> caller skips setting it).
    "alias" authors the spec_less shape's own content, but spelled as a
    ``style.formats`` alias reference rather than inline -- the alias-vs-
    inline invariant (I5) compares this against "spec_less" directly.
    """
    if shape == "none":
        return None, None
    if shape in PRESETS:
        return shape, None
    if shape == "alias":
        return "slotalias", {"slotalias": _SHAPE_CONTENT["spec_less"]}
    return _SHAPE_CONTENT[shape], None


def _set_path(d: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        d = d.setdefault(key, {})
    d[path[-1]] = value


def _board(
    charts: dict[str, Any], *, style_formats: dict[str, Any] | None = None
) -> dict[str, Any]:
    board: dict[str, Any] = {"title": "t", "charts": charts, "rows": list(charts)}
    if style_formats:
        board["style"] = {"formats": style_formats}
    return board


# Per-slot board builders. "axis" slots paint a tick ladder; "value" slots paint the
# exact authored value once.


@dataclass(frozen=True)
class SlotFixture:
    id: str
    # Every (ClassName, field_name) this one fixture stands for.
    covers: tuple[tuple[str, str], ...]
    bands: tuple[str, ...]
    build: Callable[[str, float], dict[str, Any]]
    tooltip_check: bool = (
        False  # I6: does this slot's own tooltip exist to cross-check?
    )
    # I4 sign-order: table columns lay a magnitude/currency prefix out as its own right-
    # aligned gutter tspan, separate from the signed-digit tspan.
    checks_sign: bool = True
    # A slot that rejects an affix by design gets this ErrorCode string instead of the
    # six paint invariants.
    rejects: str | None = None
    # True for the one rejects-mode slot (TooltipStyle.format) whose field type is
    # `FormatAlias | str`.
    rejects_inline_only: bool = False
    # False for the one slot (heatmap's color legend) that neither paints nor rejects an
    # affix: it discards it (see that fixture's own comment).
    checks_affix_paints: bool = True
    # True for the KPI headline only: it paints its suffix in its own tspan with a
    # dx="2" pixel kern.
    kerned_suffix: bool = False
    # True for a slot that paints the exact authored value, so a preset's house digits
    # can be checked against format_value.
    exact_value: bool = False


def _axis_y_labels(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "bar", "query": "q", "x": "channel", "y": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "axis_y", "labels", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _axis_x_labels(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "scatter", "query": "q", "x": "v", "y": "v2"}
    if fmt is not None:
        _set_path(chart, ("style", "axis_x", "labels", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["v", "v2"], "values": [[value, 1]]}}
    return board


def _line_ladder_rows(value: float) -> list[list[Any]]:
    """Three points proportional to ``value`` (matching the review's own repro: 0.67 /
    0.18 / 0.42).
    """
    return [["A", value * 0.3], ["B", value * 0.7], ["C", value]]


def _axis_y_mirror(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    # "no format" here must still turn mirror on (AxisMirrorStyle rejects an empty
    # override).
    mirror: Any = True if fmt is None else {"format": fmt}
    chart: dict[str, Any] = {
        "type": "line",
        "query": "q",
        "x": "channel",
        "y": "v",
        "style": {"axis_y": {"mirror": mirror}},
    }
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "q": {"columns": ["channel", "v"], "values": _line_ladder_rows(value)}
    }
    return board


def _layer_axis_y_labels(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    layer: dict[str, Any] = {
        "type": "line",
        "y": "v2",
        "label": "Layer",
        "axis_y": {"position": "right"},
    }
    if fmt is not None:
        layer["axis_y"]["labels"] = {"format": fmt}
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "q",
        "x": "channel",
        "y": "v",
        "layers": [layer],
    }
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "q": {
            "columns": ["channel", "v", "v2"],
            "values": [[row[0], 1.0, row[1]] for row in _line_ladder_rows(value)],
        }
    }
    return board


def _marks_bar_labels(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "q",
        "x": "channel",
        "y": "v",
        "style": {"marks": {"bar": {"labels": {"visible": True}}}},
    }
    if fmt is not None:
        _set_path(chart, ("style", "marks", "bar", "labels", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _marks_bar_total_label(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    # A single segment per category: the stack total equals the bare value.
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "s",
        "x": "channel",
        "y": "v",
        "color": "seg",
        "style": {
            "stack": "zero",
            "marks": {"bar": {"total_label": {"visible": True}}},
        },
    }
    if fmt is not None:
        _set_path(chart, ("style", "marks", "bar", "total_label", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "s": {"columns": ["channel", "seg", "v"], "values": [["A", "x", value]]}
    }
    return board


def _number_format(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "bar", "query": "q", "x": "channel", "y": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _kpi_value(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "kpi", "query": "t", "value": "v", "label": "L"}
    if fmt is not None:
        _set_path(chart, ("style", "value", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"t": {"columns": ["v"], "values": [[value]]}}
    return board


def _donut_total_value(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {
        "type": "donut",
        "query": "t",
        "theta": "v",
        "total": {"label": "T"},
    }
    if fmt is not None:
        _set_path(chart, ("style", "total", "value", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"t": {"columns": ["v"], "values": [[value]]}}
    return board


def _table_columns(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "table", "query": "t"}
    if fmt is not None:
        _set_path(chart, ("style", "columns", "v", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"t": {"columns": ["v"], "values": [[value]]}}
    return board


def _support_table_entry(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    entry: dict[str, Any] = {"source": "v", "label": "V"}
    if fmt is not None:
        entry["format"] = fmt
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "q",
        "x": "channel",
        "y": "v",
        "support_table": {"entries": [entry]},
    }
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _support_table_aggregate_entry(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    entry: dict[str, Any] = {"aggregate": "sum", "source": "v", "label": "V"}
    if fmt is not None:
        entry["format"] = fmt
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "q",
        "x": "channel",
        "y": "v",
        "support_table": {"entries": [entry]},
    }
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _support_table_per_series_entry(shape: str, value: float) -> dict[str, Any]:
    fmt, formats = _shape_value(shape)
    entry: dict[str, Any] = {"per_series": "v", "label": "V"}
    if fmt is not None:
        entry["format"] = fmt
    chart: dict[str, Any] = {
        "type": "bar",
        "query": "s",
        "x": "channel",
        "y": "v",
        "color": "seg",
        "support_table": {"entries": [entry]},
    }
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "s": {"columns": ["channel", "seg", "v"], "values": [["A", "x", value]]}
    }
    return board


def _table_column_defaults(shape: str, value: float) -> dict[str, Any]:
    """``style.column_defaults.format``, the table-level column fallback."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "table", "query": "t"}
    if fmt is not None:
        _set_path(chart, ("style", "column_defaults", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"t": {"columns": ["v"], "values": [[value]]}}
    return board


def _kpi_support(shape: str, value: float) -> dict[str, Any]:
    """``KpiSupportConfig.format``, the KPI support line."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {
        "type": "kpi",
        "query": "t",
        "value": "headline",
        "label": "L",
        "support": {"value": "v", "label": "vs last"},
    }
    if fmt is not None:
        chart["support"]["format"] = fmt
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"t": {"columns": ["headline", "v"], "values": [[1.0, value]]}}
    return board


def _point_labels(shape: str, value: float) -> dict[str, Any]:
    """``PointLabelsStyle.format``; one class and path serve scatter and line labels."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {
        "type": "scatter",
        "query": "q",
        "x": "v2",
        "y": "v",
        "style": {"marks": {"point": {"labels": {"visible": True}}}},
    }
    if fmt is not None:
        _set_path(chart, ("style", "marks", "point", "labels", "format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["v2", "v"], "values": [[1.0, value]]}}
    return board


def _line_number_format(shape: str, value: float) -> dict[str, Any]:
    """``LineChartStyle.number_format``; not zero-anchored, so it needs ladder rows."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "line", "query": "q", "x": "channel", "y": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "q": {"columns": ["channel", "v"], "values": _line_ladder_rows(value)}
    }
    return board


def _area_number_format(shape: str, value: float) -> dict[str, Any]:
    """``AreaChartStyle.number_format``; zero-anchored like bar."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "area", "query": "q", "x": "channel", "y": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {"q": {"columns": ["channel", "v"], "values": [["A", value]]}}
    return board


def _scatter_number_format(shape: str, value: float) -> dict[str, Any]:
    """``ScatterChartStyle.number_format`` -- scatter's own y measure axis."""
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "scatter", "query": "q", "x": "v2", "y": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    rows = _line_ladder_rows(value)
    board["queries"] = {
        "q": {
            "columns": ["v2", "v"],
            "values": [[i, row[1]] for i, row in enumerate(rows)],
        }
    }
    return board


def _heatmap_number_format(shape: str, value: float) -> dict[str, Any]:
    """``HeatmapChartStyle.number_format`` -- feeds the color legend and per-cell
    tooltip, not an axis (heatmap has no measure axis).
    """
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {
        "type": "heatmap",
        "query": "q",
        "x": "channel",
        "y": "channel2",
        "color": "v",
    }
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "q": {
            "columns": ["channel", "channel2", "v"],
            "values": [["A", "X", value]],
        }
    }
    return board


def _histogram_number_format(shape: str, value: float) -> dict[str, Any]:
    """``HistogramChartStyle.number_format`` -- a histogram is excluded from the
    structured tooltip (``StructuredTooltipFeature.applies_to``).
    """
    fmt, formats = _shape_value(shape)
    chart: dict[str, Any] = {"type": "histogram", "query": "q", "x": "v"}
    if fmt is not None:
        _set_path(chart, ("style", "number_format"), fmt)
    board = _board({"c": chart}, style_formats=formats)
    board["queries"] = {
        "q": {"columns": ["v"], "values": [[value], [value * 1.5], [value * 0.5]]}
    }
    return board


def _tooltip_style_format(shape: str, value: float) -> dict[str, Any]:
    """``TooltipStyle.format``, the board-level tooltip default."""
    fmt, formats = _shape_value(shape)
    board: dict[str, Any] = {
        "title": "t",
        "charts": {"c": {"type": "bar", "query": "q", "x": "channel", "y": "v"}},
        "rows": ["c"],
        "queries": {"q": {"columns": ["channel", "v"], "values": [["A", value]]}},
    }
    if fmt is not None:
        _set_path(board, ("style", "charts", "tooltip", "format"), fmt)
    if formats:
        board.setdefault("style", {})["formats"] = formats
    return board


_ALL_BANDS = tuple(BANDS)
_POSITIVE_BANDS = ("sub_one", "kpi_band", "large")

SLOT_FIXTURES: dict[str, SlotFixture] = {
    f.id: f
    for f in (
        SlotFixture(
            "style.axis_y.labels.format",
            covers=(("AxisLabelStyle", "format"),),
            bands=_ALL_BANDS,
            build=_axis_y_labels,
            tooltip_check=True,
        ),
        SlotFixture(
            "style.axis_x.labels.format",
            # AxisXStyle.labels overrides the base's `AxisLabelStyle` with
            # `DimensionLabelStyle` (adds dimension-only fields, inherits `format`
            # unchanged).
            covers=(("DimensionLabelStyle", "format"),),
            bands=_ALL_BANDS,
            build=_axis_x_labels,
        ),
        SlotFixture(
            "style.axis_y.mirror.format",
            covers=(("AxisMirrorStyle", "format"),),
            bands=_ALL_BANDS,
            build=_axis_y_mirror,
        ),
        SlotFixture(
            "layers.N.axis_y.labels.format",
            covers=(("LayerAxisYLabels", "format"),),
            bands=_ALL_BANDS,
            build=_layer_axis_y_labels,
            tooltip_check=True,
        ),
        SlotFixture(
            "style.marks.bar.labels.format",
            covers=(("BarLabelsStyle", "format"), ("MarkLabelsStyle", "format")),
            bands=_ALL_BANDS,
            build=_marks_bar_labels,
            exact_value=True,
        ),
        SlotFixture(
            "style.marks.bar.total_label.format",
            covers=(("BarTotalLabelStyle", "format"),),
            bands=_ALL_BANDS,
            build=_marks_bar_total_label,
            exact_value=True,
        ),
        SlotFixture(
            "style.number_format",
            covers=(
                ("BarChartStyle", "number_format"),
                # _CartesianChartStyle is a private mixin -- never instantiated
                # directly.
                ("_CartesianChartStyle", "number_format"),
            ),
            bands=_ALL_BANDS,
            build=_number_format,
            tooltip_check=True,
        ),
        SlotFixture(
            "style.value.format",
            covers=(("KpiValueStyle", "format"),),
            bands=_ALL_BANDS,
            build=_kpi_value,
            exact_value=True,
            kerned_suffix=True,
        ),
        SlotFixture(
            "style.total.value.format",
            covers=(("TotalValueSlotStyle", "format"),),
            bands=_POSITIVE_BANDS,
            build=_donut_total_value,
            exact_value=True,
        ),
        SlotFixture(
            "style.columns.*.format",
            covers=(("TableColumnConfig", "format"),),
            bands=_ALL_BANDS,
            build=_table_columns,
            exact_value=True,
            checks_sign=False,
        ),
        SlotFixture(
            "support_table.entries.N.format",
            covers=(("ChartSupportTableSource", "format"),),
            bands=_ALL_BANDS,
            build=_support_table_entry,
            exact_value=True,
        ),
        SlotFixture(
            "support_table.entries.N.format (aggregate)",
            covers=(("ChartSupportTableAggregate", "format"),),
            bands=_ALL_BANDS,
            build=_support_table_aggregate_entry,
            exact_value=True,
        ),
        SlotFixture(
            "support_table.entries.N.format (per_series)",
            covers=(("ChartSupportTablePerSeries", "format"),),
            bands=_ALL_BANDS,
            build=_support_table_per_series_entry,
            exact_value=True,
        ),
        SlotFixture(
            "style.column_defaults.format",
            covers=(("TableColumnDefaultsConfig", "format"),),
            bands=_ALL_BANDS,
            build=_table_column_defaults,
            exact_value=True,
            # Same split-tspan column layout as style.columns.*.format above
            # -- see that fixture's own checks_sign comment.
            checks_sign=False,
        ),
        SlotFixture(
            "support.format",
            covers=(("KpiSupportConfig", "format"),),
            bands=_ALL_BANDS,
            build=_kpi_support,
            exact_value=True,
        ),
        SlotFixture(
            "style.marks.point.labels.format",
            covers=(("PointLabelsStyle", "format"),),
            bands=_ALL_BANDS,
            build=_point_labels,
            exact_value=True,
        ),
        SlotFixture(
            "style.number_format (line)",
            covers=(("LineChartStyle", "number_format"),),
            bands=_ALL_BANDS,
            build=_line_number_format,
        ),
        SlotFixture(
            "style.number_format (area)",
            covers=(("AreaChartStyle", "number_format"),),
            bands=_ALL_BANDS,
            build=_area_number_format,
        ),
        SlotFixture(
            "style.number_format (scatter)",
            covers=(("ScatterChartStyle", "number_format"),),
            bands=_ALL_BANDS,
            build=_scatter_number_format,
        ),
        SlotFixture(
            "style.number_format (heatmap)",
            covers=(("HeatmapChartStyle", "number_format"),),
            bands=_ALL_BANDS,
            build=_heatmap_number_format,
            # Heatmap's color legend/tooltip discards the affix (a native `$` spec too):
            # no legend-labelExpr composition mechanism exists in the render tree.
            checks_affix_paints=False,
        ),
        SlotFixture(
            "style.number_format (histogram)",
            covers=(("HistogramChartStyle", "number_format"),),
            bands=_ALL_BANDS,
            build=_histogram_number_format,
            rejects="ERR-FORMAT-AFFIX-NATIVE-TOOLTIP-UNSUPPORTED",
        ),
        SlotFixture(
            "style.charts.tooltip.format",
            covers=(("TooltipStyle", "format"),),
            bands=_ALL_BANDS,
            build=_tooltip_style_format,
            # TooltipStyle.format does not accept an *inline* FormatConfig.
            rejects="ERR-VALIDATION-FIELD",
            rejects_inline_only=True,
        ),
    )
}


# Extraction + invariants.

_TEXT_RE = re.compile(
    r"<text(?![^>]*data-role=\"render-timestamp\")[^>]*>(.*?)</text>", re.S
)
_ARIA_RE = re.compile(r'aria-label="([^"]*)"')
_MILLI_RE = re.compile(r"\d+m\b")
_HAS_DIGIT_RE = re.compile(r"\d")
# Vega's own generic per-axis accessibility summary ("X-axis for a linear scale with
# values from 0 to ..." / "Y-axis titled 'Layer' for a ... scale with values from ..."):
# describes the scale's raw data domain in Vega's own internal notation, never the
# authored format, and is not a value a user hovers -- excluded from both digit and
# tooltip extraction below.
_AXIS_SUMMARY_RE = re.compile(r"^[XY]-axis\b.*\bscale with values\b")
_AFFIX_MARKERS = ("£", "$", "US ", "€")
_TAG_RE = re.compile(r"<[^>]+>")
_NUMERAL_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SIGN_RE = re.compile(r"[−-]")


def _digit_texts(svg: str) -> list[str]:
    """Every painted ``<text>`` node containing a digit, in document order."""
    return [t for t in _TEXT_RE.findall(svg) if _HAS_DIGIT_RE.search(t)]


def _numerals(text: str) -> list[str]:
    """Numeral substrings, sign-normalized, tags and affix characters stripped."""
    plain = _TAG_RE.sub("", text)
    sign = "-" if _SIGN_RE.search(plain) else ""
    return [sign + n for n in _NUMERAL_RE.findall(plain)]


def _tooltip_texts(svg: str) -> list[str]:
    """Every mark's ``aria-label`` (the SVG-embedded tooltip text) containing a digit."""
    return [
        a
        for a in _ARIA_RE.findall(svg)
        if _HAS_DIGIT_RE.search(a) and not _AXIS_SUMMARY_RE.match(a)
    ]


def _check_renders(shape: str, band: str, svg: str) -> None:
    """I1: every shape x band renders -- no crash, and something painted."""
    assert _digit_texts(svg) or _tooltip_texts(svg), (
        f"nothing painted for shape={shape} band={band}"
    )


def _check_no_milli_beside_currency(shape: str, band: str, svg: str) -> None:
    """I2: no digit-followed-by-milli-'m' in a text token that also carries a currency
    affix.
    """
    if shape == "none":
        return
    hits = [
        t
        for t in _digit_texts(svg) + _tooltip_texts(svg)
        if _MILLI_RE.search(t) and any(marker in t for marker in _AFFIX_MARKERS)
    ]
    assert not hits, f"milli beside currency: shape={shape} band={band} hits={hits}"


def _check_affix_only_changes_affix(
    slot: SlotFixture, band: str, none_svg: str, spec_less_svg: str
) -> None:
    """I3: a spec-less prefix changes only the affix."""
    none_digits = [n for t in _digit_texts(none_svg) for n in _numerals(t)]
    affixed_digits = [n for t in _digit_texts(spec_less_svg) for n in _numerals(t)]
    if band == _I3_EXEMPT_BAND:
        # Documented exception: a currency affix pulls a sub-$1 value out of the
        # milli/SI sub-unit fallback into plain digits.
        hits = [
            t
            for t in _digit_texts(spec_less_svg)
            if _MILLI_RE.search(t) and any(marker in t for marker in _AFFIX_MARKERS)
        ]
        assert not hits, hits
        if none_digits != affixed_digits:
            assert any("." in n for n in affixed_digits), affixed_digits
        return
    assert none_digits == affixed_digits, (
        f"slot={slot.id} band={band}: adding a spec-less affix changed the "
        f"digits: {none_digits!r} -> {affixed_digits!r}"
    )


def _check_sign_order(slot: SlotFixture, prefix_svg: str, suffix_svg: str) -> None:
    """I4: a prefix paints sign-first (-£41,500)."""
    prefix_plain = [_TAG_RE.sub("", t) for t in _digit_texts(prefix_svg)]
    suffix_plain = [_TAG_RE.sub("", t) for t in _digit_texts(suffix_svg)]
    prefix_hit = [t for t in prefix_plain if _SIGN_RE.search(t)]
    suffix_hit = [t for t in suffix_plain if _SIGN_RE.search(t)]
    assert any(re.search(r"[−-]£", t) for t in prefix_hit), (
        f"slot={slot.id}: prefix did not paint sign-first: {prefix_hit!r}"
    )
    # No `$` end-anchor: a KPI support line paints "-41,500 € vs last".
    gap = " ?" if slot.kerned_suffix else " "
    assert any(re.search(rf"^[−-][\d,]+{gap}€(?:\s|$)", t) for t in suffix_hit), (
        f"slot={slot.id}: suffix did not paint sign-then-trailing-affix: {suffix_hit!r}"
    )


# Value-label slots paint through the house register (_house_register_expr).
_HOUSE_REGISTER_SLOTS = frozenset(
    {"style.marks.bar.labels.format", "style.marks.bar.total_label.format"}
)


def _check_preset_paints_house_digits(
    slot: SlotFixture, preset: str, band: str, svg: str
) -> None:
    """A bare predefined name paints the digits ``format_value`` gives it."""
    value = BANDS[band]
    if (
        slot.id in _HOUSE_REGISTER_SLOTS
        and 0 < abs(value) < 1
        and is_d3_si_spec(PREDEFINED_SPECS[preset])
    ):
        # The house register prints a sub-1 value in digits, never milli.
        text = format_d3(value, sub_unit_digit_format(PREDEFINED_SPECS[preset]))
    else:
        text = format_value(value, preset)
    expected = _numerals(text)
    painted = [n for t in _digit_texts(svg) + _tooltip_texts(svg) for n in _numerals(t)]
    assert set(expected) <= set(painted), (
        f"slot={slot.id} preset={preset} band={band}: expected {expected!r} "
        f"from format_value, painted {painted!r}"
    )


def _check_alias_equals_inline(
    slot: SlotFixture, alias_svg: str, inline_svg: str
) -> None:
    """I5: the alias spelling paints byte-identical strings to the inline
    spelling of the same spec-less-prefix shape."""
    assert _digit_texts(alias_svg) == _digit_texts(inline_svg), (
        f"slot={slot.id}: alias spelling diverged from inline spelling"
    )
    assert _tooltip_texts(alias_svg) == _tooltip_texts(inline_svg), (
        f"slot={slot.id}: alias tooltip diverged from inline tooltip"
    )


def _check_tooltip_agrees_with_axis(slot: SlotFixture, svg: str) -> None:
    """I6: a slot's own tooltip agrees with that slot's own axis on the same value."""
    axis_digits = _digit_texts(svg)
    tooltip_digits = _tooltip_texts(svg)
    # The tooltip paints the exact data value; the axis paints a "nice" tick ladder that
    # need not include that exact value.
    tooltip_milli = any(_MILLI_RE.search(t) for t in tooltip_digits)
    axis_milli = any(_MILLI_RE.search(t) for t in axis_digits)
    assert tooltip_milli == axis_milli, (
        f"slot={slot.id}: tooltip and axis disagree on milli -- "
        f"axis={axis_digits!r} tooltip={tooltip_digits!r}"
    )


# Tests.


def test_derived_slots_have_fixtures() -> None:
    """The completeness gate: every ``(Class, field)`` the schema exposes
    must be named in some fixture's ``covers``, or this fails and names the
    gap instead of silently skipping it -- in both directions, so a stale
    ``covers`` claim for a tuple the schema no longer has fails too."""
    derived = {cf for cf in schema_format_fields() if not _is_excluded(cf)}
    covered: set[tuple[str, str]] = set()
    for fixture in SLOT_FIXTURES.values():
        covered.update(fixture.covers)
    missing = derived - covered
    assert not missing, f"schema fields with no fixture coverage: {sorted(missing)}"
    stale = covered - derived
    assert not stale, (
        f"fixture coverage for fields the schema no longer has: {sorted(stale)}"
    )


def _assert_rejected(board: dict[str, Any], code: str, *, context: str) -> None:
    """A slot that rejects an affix by design must raise ``code``."""
    yaml_text = _to_yaml(board)
    result = compile_board(yaml_text)
    if not result.success:
        codes = [e.code for e in result.errors]
        assert code in codes, f"{context}: expected {code}, got {codes}"
        return
    svg = render_board_to_svg(yaml_text)
    assert code in svg, (
        f"{context}: expected {code} in the rendered error card, not found"
    )


def _assert_renders_clean(board: dict[str, Any], code: str, *, context: str) -> None:
    """The counterpart to _assert_rejected for a rejects-mode slot's "clean" shape (no
    format, or a plain spec with no affix).
    """
    yaml_text = _to_yaml(board)
    result = compile_board(yaml_text)
    assert result.success, f"{context}: expected a clean compile, got {result.errors}"
    svg = render_board_to_svg(yaml_text)
    assert code not in svg, (
        f"{context}: rejection code {code} appeared on a clean shape"
    )


@pytest.mark.parametrize("slot_id", sorted(SLOT_FIXTURES))
def test_slot_invariants(slot_id: str) -> None:
    slot = SLOT_FIXTURES[slot_id]

    if slot.rejects is not None:
        _assert_renders_clean(
            slot.build("none", BANDS["kpi_band"]), slot.rejects, context=slot.id
        )
        rejected_shapes = ["spec_less", "spec_prefix", "spec_suffix", "native_affix"]
        if not slot.rejects_inline_only:
            rejected_shapes.append("alias")
        for shape in rejected_shapes:
            _assert_rejected(
                slot.build(shape, BANDS["kpi_band"]),
                slot.rejects,
                context=f"{slot.id} shape={shape}",
            )
        if slot.rejects_inline_only:
            # The alias spelling is a plain string, not a type mismatch --
            # it must render correctly, affix and all, not get rejected.
            svg = render_board_to_svg(_to_yaml(slot.build("alias", BANDS["kpi_band"])))
            assert slot.rejects not in svg, (
                f"{slot.id} shape=alias: wrongly rejected a valid alias spelling"
            )
            assert any("£" in t for t in _tooltip_texts(svg)), (
                f"{slot.id} shape=alias: affix did not reach the painted tooltip"
            )
        return

    svgs: dict[tuple[str, str], str] = {}
    for shape in SHAPES:
        for band in slot.bands:
            if shape == "percent" and band not in _PERCENT_BANDS:
                continue
            board = slot.build(shape, BANDS[band])
            svg = render_board_to_svg(_to_yaml(board))
            svgs[(shape, band)] = svg
            _check_renders(shape, band, svg)
            is_preset = shape in PRESETS
            if slot.checks_affix_paints and (slot.exact_value or not is_preset):
                _check_no_milli_beside_currency(shape, band, svg)
            if is_preset and slot.exact_value:
                _check_preset_paints_house_digits(slot, shape, band, svg)

    if slot.checks_affix_paints:
        for band in slot.bands:
            _check_affix_only_changes_affix(
                slot, band, svgs[("none", band)], svgs[("spec_less", band)]
            )

        if "negative" in slot.bands and slot.checks_sign:
            _check_sign_order(
                slot,
                svgs[("spec_prefix", "negative")],
                svgs[("spec_suffix", "negative")],
            )

        for band in slot.bands:
            _check_alias_equals_inline(
                slot, svgs[("alias", band)], svgs[("spec_less", band)]
            )

    if slot.tooltip_check:
        tooltip_band = "sub_one" if "sub_one" in slot.bands else slot.bands[0]
        _check_tooltip_agrees_with_axis(slot, svgs[("spec_less", tooltip_band)])


def _to_yaml(board: dict[str, Any]) -> str:
    return yaml.safe_dump(board, sort_keys=False)


# Native presets (percent_number, percent_number_delta, percentage_points_delta) beside
# an authored affix.

_NATIVE_PRESET_PAINT: dict[str, tuple[float, str, str]] = {
    "percent_number": (12.3, "12.3", "%"),
    "percent_number_delta": (12.3, "+12.3", "%"),
    "percentage_points_delta": (1.5, "+1.5", " pts"),
}
_NATIVE_AFFIXES: dict[str, dict[str, str]] = {
    "prefix": {"prefix": "~"},
    "suffix": {"suffix": " vs LY"},
}
_NATIVE_SLOTS = ("headline", "support")


def _native_expected(preset: str, affix: str) -> str:
    _, number, unit = _NATIVE_PRESET_PAINT[preset]
    if affix == "suffix":
        return f"{number}{unit} vs LY"
    # A prefix paints sign-first, like every other prefixed KPI ("+$5").
    sign, digits = ("+", number[1:]) if number.startswith("+") else ("", number)
    return f"{sign}~{digits}{unit}"


def _native_kpi_board(slot: str, fmt: dict[str, Any], value: float) -> dict[str, Any]:
    chart: dict[str, Any] = {"type": "kpi", "query": "t", "label": "L"}
    if slot == "headline":
        chart["value"] = "v"
        chart["style"] = {"value": {"format": fmt}}
    else:
        chart["value"] = "headline"
        chart["support"] = {"value": "v", "label": "vs last", "format": fmt}
    board = _board({"c": chart})
    board["queries"] = {"t": {"columns": ["headline", "v"], "values": [[1.0, value]]}}
    return board


def _native_case_ids() -> list[Any]:
    return [
        pytest.param(slot, preset, affix, id=f"{slot}-{preset}-{affix}")
        for slot in _NATIVE_SLOTS
        for preset in _NATIVE_PRESET_PAINT
        for affix in _NATIVE_AFFIXES
    ]


@pytest.mark.parametrize(("slot", "preset", "affix"), _native_case_ids())
def test_native_preset_beside_an_affix_paints_unit_and_affix_in_svg(
    slot: str, preset: str, affix: str
) -> None:
    value = _NATIVE_PRESET_PAINT[preset][0]
    fmt = {"spec": preset, **_NATIVE_AFFIXES[affix]}
    svg = render_board_to_svg(_to_yaml(_native_kpi_board(slot, fmt, value)))
    painted = [_TAG_RE.sub("", t) for t in _digit_texts(svg)]
    expected = _native_expected(preset, affix)
    assert any(expected in t for t in painted), (
        f"slot={slot} preset={preset} affix={affix}: expected {expected!r} in {painted!r}"
    )


def _resolved_native_kpi(make_chart: Any, slot: str, fmt: dict[str, Any], value: float):
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )

    if slot == "headline":
        chart = make_chart(
            "kpi", value="v", label="L", style={"value": {"format": fmt}}
        )
    else:
        chart = make_chart(
            "kpi",
            value="headline",
            label="L",
            support={"value": "v", "label": "vs last", "format": fmt},
        )
    data = [{"headline": 1.0, "v": value}]
    resolved = resolve(
        chart,
        data,
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )
    return chart, resolved, data


@pytest.mark.parametrize(("slot", "preset", "affix"), _native_case_ids())
def test_native_preset_beside_an_affix_paints_unit_and_affix_in_text_and_terminal(
    make_chart: Any, slot: str, preset: str, affix: str
) -> None:
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts
    from dbt_charts.core.render.terminal_charts import render_kpi_terminal

    value = _NATIVE_PRESET_PAINT[preset][0]
    fmt = {"spec": preset, **_NATIVE_AFFIXES[affix]}
    chart, resolved, data = _resolved_native_kpi(make_chart, slot, fmt, value)
    expected = _native_expected(preset, affix)

    text_parts = _kpi_text_parts(resolved, data[0], None)
    assert expected in text_parts["value" if slot == "headline" else "support"]
    if slot == "headline":
        assert expected in render_kpi_terminal(chart, data, "Revenue", formats=None)


# A prefix beside a suffix on one native preset, and a prefix that carries its own
# trailing space ("EUR ").
_NATIVE_AFFIX_PAIRS: dict[str, dict[str, str]] = {
    "prefix_and_suffix": {"prefix": "~", "suffix": " vs LY"},
    "spaced_prefix": {"prefix": "EUR "},
    "spaced_prefix_and_suffix": {"prefix": "EUR ", "suffix": " vs LY"},
}


def _native_pair_expected(preset: str, affix: dict[str, str]) -> str:
    _, number, unit = _NATIVE_PRESET_PAINT[preset]
    prefix, suffix = affix.get("prefix", ""), affix.get("suffix", "")
    if prefix.endswith(" "):
        # A spaced prefix leads the sign.
        return f"{prefix}{number}{unit}{suffix}"
    sign, digits = ("+", number[1:]) if number.startswith("+") else ("", number)
    return f"{sign}{prefix}{digits}{unit}{suffix}"


def _native_pair_ids() -> list[Any]:
    return [
        pytest.param(slot, preset, pair, id=f"{slot}-{preset}-{pair}")
        for slot in _NATIVE_SLOTS
        for preset in _NATIVE_PRESET_PAINT
        for pair in _NATIVE_AFFIX_PAIRS
    ]


@pytest.mark.parametrize(("slot", "preset", "pair"), _native_pair_ids())
def test_native_preset_beside_a_prefix_and_suffix_paints_both(
    make_chart: Any, slot: str, preset: str, pair: str
) -> None:
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts
    from dbt_charts.core.render.terminal_charts import render_kpi_terminal

    affix = _NATIVE_AFFIX_PAIRS[pair]
    value = _NATIVE_PRESET_PAINT[preset][0]
    fmt = {"spec": preset, **affix}
    expected = _native_pair_expected(preset, affix)

    svg = render_board_to_svg(_to_yaml(_native_kpi_board(slot, fmt, value)))
    painted = [_TAG_RE.sub("", t) for t in _digit_texts(svg)]
    lenient = re.escape(expected).replace(r"EUR\ ", r"EUR\s?")
    assert any(re.search(lenient, t) for t in painted), (
        f"slot={slot} preset={preset} pair={pair}: expected {expected!r} in {painted!r}"
    )

    _, resolved, data = _resolved_native_kpi(make_chart, slot, fmt, value)
    parts = _kpi_text_parts(resolved, data[0], None)
    assert expected in parts["value" if slot == "headline" else "support"]
    if slot == "headline":
        chart = make_chart(
            "kpi", value="v", label="L", style={"value": {"format": fmt}}
        )
        assert expected in render_kpi_terminal(
            chart, [{"headline": 1.0, "v": value}], "Revenue", formats=None
        )


@pytest.mark.parametrize("slot", _NATIVE_SLOTS)
def test_native_preset_negative_value_paints_sign_before_a_prefix(
    make_chart: Any, slot: str
) -> None:
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts

    fmt = {"spec": "percent_number", "prefix": "~"}
    svg = render_board_to_svg(_to_yaml(_native_kpi_board(slot, fmt, -12.3)))
    assert any("−~12.3%" in _TAG_RE.sub("", t) for t in _digit_texts(svg))
    _, resolved, data = _resolved_native_kpi(make_chart, slot, fmt, -12.3)
    parts = _kpi_text_parts(resolved, data[0], None)
    assert "−~12.3%" in parts["value" if slot == "headline" else "support"]


# A d3 spec that paints its own unit (a magnitude letter, a percent sign) beside an
# authored suffix: the authored gap survives, so the suffix never glues onto the unit.

_UNIT_SUFFIX_PAINT: dict[str, tuple[dict[str, str], float, str]] = {
    "magnitude": (
        {"spec": "number", "suffix": " EUR"},
        1_234_567.0,
        r"1\.23(?:mn|M) EUR",
    ),
    "percent": ({"spec": "percent", "suffix": " YoY"}, 0.123, r"12\.3% YoY"),
}


@pytest.mark.parametrize("slot", _NATIVE_SLOTS)
@pytest.mark.parametrize("case", sorted(_UNIT_SUFFIX_PAINT))
def test_unit_beside_an_authored_suffix_keeps_the_authored_gap_in_svg(
    slot: str, case: str
) -> None:
    fmt, value, expected = _UNIT_SUFFIX_PAINT[case]
    svg = render_board_to_svg(_to_yaml(_native_kpi_board(slot, fmt, value)))
    painted = [_TAG_RE.sub("", t) for t in _digit_texts(svg)]
    assert any(re.search(expected, t) for t in painted), (
        f"slot={slot} case={case}: expected {expected!r} in {painted!r}"
    )


@pytest.mark.parametrize("slot", _NATIVE_SLOTS)
@pytest.mark.parametrize("case", sorted(_UNIT_SUFFIX_PAINT))
def test_unit_beside_an_authored_suffix_paints_the_same_in_kpi_text(
    make_chart: Any, slot: str, case: str
) -> None:
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts

    fmt, value, expected = _UNIT_SUFFIX_PAINT[case]
    _, resolved, data = _resolved_native_kpi(make_chart, slot, fmt, value)
    parts = _kpi_text_parts(resolved, data[0], None)
    assert re.search(expected, parts["value" if slot == "headline" else "support"])


# A date spec beside an authored affix on the Python-painted slots (table column, KPI
# headline, KPI support): the affix paints around the date.

# (format, style.formats alias table, painted text, slots it applies to).
_DATE_AFFIX: dict[str, tuple[dict[str, str], dict[str, str], str, tuple[str, ...]]] = {
    "predefined_prefix": (
        {"spec": "date_short", "prefix": "FY "},
        {},
        "FY 15 Jan 2026",
        ("table_column", "kpi_headline", "kpi_support"),
    ),
    "predefined_suffix": (
        {"spec": "date_short", "suffix": " (FY)"},
        {},
        "15 Jan 2026 (FY)",
        ("table_column", "kpi_headline", "kpi_support"),
    ),
    "alias_prefix": (
        {"spec": "mymonth", "prefix": "FY "},
        {"mymonth": "%b %Y"},
        "FY Jan 2026",
        ("table_column", "kpi_headline", "kpi_support"),
    ),
    "spec_less_prefix": (
        {"prefix": "As of "},
        {},
        "As of 15 Jan 2026",
        ("table_column", "kpi_headline", "kpi_support"),
    ),
    "inline_strftime_suffix": (
        {"spec": "%b %Y", "suffix": " (FY)"},
        {},
        "Jan 2026 (FY)",
        ("table_column",),
    ),
}
_DATE_SLOTS = ("table_column", "kpi_headline", "kpi_support")


def _date_affix_cases(slots: tuple[str, ...]) -> list[Any]:
    return [
        pytest.param(slot, affix, id=f"{slot}-{affix}")
        for affix, (_, _, _, applies) in _DATE_AFFIX.items()
        for slot in slots
        if slot in applies
    ]


def _date_affix_board(
    slot: str, fmt: dict[str, str], aliases: dict[str, str]
) -> dict[str, Any]:
    if slot == "table_column":
        chart: dict[str, Any] = {
            "type": "table",
            "query": "t",
            "style": {"columns": {"d": {"format": fmt}}},
        }
    elif slot == "kpi_headline":
        chart = {
            "type": "kpi",
            "query": "t",
            "value": "d",
            "label": "L",
            "style": {"value": {"format": fmt}},
        }
    else:
        chart = {
            "type": "kpi",
            "query": "t",
            "value": "n",
            "label": "L",
            "support": {"value": "d", "label": "since", "format": fmt},
        }
    board = _board({"c": chart}, style_formats=aliases or None)
    board["queries"] = {"t": {"columns": ["d", "n"], "values": [["2026-01-15", 1.0]]}}
    return board


@pytest.mark.parametrize(("slot", "affix"), _date_affix_cases(_DATE_SLOTS))
def test_date_spec_beside_an_affix_paints_the_affix_around_the_date(
    slot: str, affix: str
) -> None:
    fmt, aliases, expected, _ = _DATE_AFFIX[affix]
    yaml_text = _to_yaml(_date_affix_board(slot, fmt, aliases))
    assert compile_board(yaml_text).success
    svg = render_board_to_svg(yaml_text)
    painted = [_TAG_RE.sub("", t) for t in _TEXT_RE.findall(svg)]
    assert any(expected in t for t in painted), (
        f"slot={slot} affix={affix}: expected {expected!r} in {painted!r}"
    )


@pytest.mark.parametrize(
    ("slot", "affix"), _date_affix_cases(("kpi_headline", "kpi_support"))
)
def test_date_spec_beside_an_affix_paints_the_affix_in_kpi_text(
    make_chart: Any, slot: str, affix: str
) -> None:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve import resolve
    from dbt_charts.core.compile.resolve.style.board import (
        resolve_chart_style_context,
    )
    from dbt_charts.core.render.board_to_dict import _kpi_text_parts

    fmt, aliases, expected, _ = _DATE_AFFIX[affix]
    if slot == "kpi_headline":
        chart = make_chart(
            "kpi", value="d", label="L", style={"value": {"format": fmt}}
        )
    else:
        chart = make_chart(
            "kpi",
            value="n",
            label="L",
            support={"value": "d", "label": "since", "format": fmt},
        )
    data = [{"d": "2026-01-15", "n": 1.0}]
    resolved = resolve(
        chart,
        data,
        chart_style_context=resolve_chart_style_context(get_theme_style()),
    )
    parts = _kpi_text_parts(resolved, data[0], aliases or None)
    assert expected in parts["value" if slot == "kpi_headline" else "support"]


# The `currency` preset below $1 paints plain digits ("$0.67", never d3's milli "$670m")
# on every value slot; a value of $1 or more still compacts.

_SUPPORT_TABLE_BUILDERS = (
    _support_table_entry,
    _support_table_aggregate_entry,
    _support_table_per_series_entry,
)


def _dollar_texts(svg: str) -> list[str]:
    return [t for t in (_TAG_RE.sub("", x) for x in _digit_texts(svg)) if "$" in t]


@pytest.mark.parametrize("build", _SUPPORT_TABLE_BUILDERS, ids=lambda b: b.__name__)
def test_support_table_currency_preset_below_one_paints_plain_digits(
    build: Callable[[str, float], dict[str, Any]],
) -> None:
    svg = render_board_to_svg(_to_yaml(build("currency", BANDS["sub_one"])))
    assert _dollar_texts(svg) == ["$0.67"]


@pytest.mark.parametrize("build", _SUPPORT_TABLE_BUILDERS, ids=lambda b: b.__name__)
def test_support_table_currency_preset_at_or_above_one_still_compacts(
    build: Callable[[str, float], dict[str, Any]],
) -> None:
    svg = render_board_to_svg(_to_yaml(build("currency", BANDS["large"])))
    assert any(
        t.startswith("$12.4M") or t.startswith("$12.4mn") for t in _dollar_texts(svg)
    )


@pytest.mark.parametrize("build", _SUPPORT_TABLE_BUILDERS, ids=lambda b: b.__name__)
def test_support_table_without_a_format_below_one_is_unchanged(
    build: Callable[[str, float], dict[str, Any]],
) -> None:
    svg = render_board_to_svg(_to_yaml(build("none", BANDS["sub_one"])))
    assert "670m" in [_TAG_RE.sub("", t) for t in _digit_texts(svg)]


def test_mirror_axis_currency_preset_below_one_paints_plain_digits() -> None:
    svg = render_board_to_svg(_to_yaml(_axis_y_mirror("currency", BANDS["sub_one"])))
    dollars = _dollar_texts(svg)
    assert dollars == ["$0.2", "$0.4", "$0.6"]


def test_mirror_axis_currency_preset_at_or_above_one_still_compacts() -> None:
    svg = render_board_to_svg(_to_yaml(_axis_y_mirror("currency", BANDS["large"])))
    assert _dollar_texts(svg) == ["$5M", "$10M"]


def test_mirror_axis_without_a_format_below_one_is_unchanged() -> None:
    svg = render_board_to_svg(_to_yaml(_axis_y_mirror("none", BANDS["sub_one"])))
    assert _dollar_texts(svg) == []
    assert not any(_MILLI_RE.search(t) for t in _digit_texts(svg))


def _mirror_without_a_tick_ladder(values: list[float], fmt: Any = "currency") -> str:
    chart = {
        "type": "line",
        "query": "q",
        "x": "channel",
        "y": "v",
        "style": {"axis_y": {"mirror": {"format": fmt}, "ticks": {"count": None}}},
    }
    board = _board({"c": chart})
    board["queries"] = {
        "q": {
            "columns": ["channel", "v"],
            "values": [[name, v] for name, v in zip("ABC", values, strict=True)],
        }
    }
    return render_board_to_svg(_to_yaml(board))


def test_mirror_axis_currency_preset_below_one_without_a_tick_ladder_paints_plain_digits() -> (
    None
):
    dollars = _dollar_texts(_mirror_without_a_tick_ladder([0.2, 0.45, 0.7]))
    assert dollars, "the mirror edge painted no currency labels"
    assert not any(_MILLI_RE.search(t) for t in dollars), dollars


def test_mirror_axis_currency_preset_at_or_above_one_without_a_tick_ladder_compacts() -> (
    None
):
    dollars = _dollar_texts(_mirror_without_a_tick_ladder([2e6, 4.5e6, 7e6]))
    assert "$2M" in dollars and "$7M" in dollars, dollars


def test_mirror_axis_spec_less_affix_without_a_tick_ladder_paints_like_number() -> None:
    """Writing no spec behaves exactly like writing the ``number`` preset."""
    for values in ([2e6, 4.5e6, 7e6], [0.2, 0.45, 0.7]):
        spec_less = _digit_texts(
            _mirror_without_a_tick_ladder(values, {"prefix": "GBP "})
        )
        number = _digit_texts(
            _mirror_without_a_tick_ladder(values, {"spec": "number", "prefix": "GBP "})
        )
        assert spec_less == number
    gbp = [
        t
        for t in _digit_texts(
            _mirror_without_a_tick_ladder([2e6, 4.5e6, 7e6], {"prefix": "GBP "})
        )
        if "GBP" in t
    ]
    assert "GBP 2M" in gbp and "GBP 7M" in gbp, gbp


def _edge_numerals(
    values: list[float], *, mirror: Any = None, primary: Any = None
) -> list[str]:
    axis_y: dict[str, Any] = {}
    if mirror is not None:
        axis_y["mirror"] = {"format": mirror}
    if primary is not None:
        axis_y["labels"] = {"format": primary}
    chart = {
        "type": "line",
        "query": "q",
        "x": "channel",
        "y": "v",
        "style": {"axis_y": axis_y},
    }
    board = _board({"c": chart})
    board["queries"] = {
        "q": {
            "columns": ["channel", "v"],
            "values": [[name, v] for name, v in zip("ABC", values, strict=True)],
        }
    }
    svg = render_board_to_svg(_to_yaml(board))
    plain = (_TAG_RE.sub("", t).removeprefix("GBP ") for t in _digit_texts(svg))
    return [t for t in plain if re.fullmatch(r"[\d.,]+", t)]


@pytest.mark.parametrize("values", [[1200, 4500, 7000], [0.2, 0.45, 0.7]])
def test_mirror_axis_affix_with_a_tick_ladder_matches_number_and_the_primary_edge(
    values: list[float],
) -> None:
    spec_less = {"prefix": "GBP "}
    number = {"spec": "number", "prefix": "GBP "}
    assert _edge_numerals(values, mirror=spec_less) == _edge_numerals(
        values, mirror=number
    )
    both = _edge_numerals(values, mirror=number, primary=number)
    half = len(both) // 2
    assert both[:half] == both[half:], both


def _line_edge_texts(
    values: list[float], *, mirror: Any = None, primary: Any = None
) -> list[str]:
    axis_y: dict[str, Any] = {}
    if mirror is not None:
        axis_y["mirror"] = {"format": mirror}
    if primary is not None:
        axis_y["labels"] = {"format": primary}
    board = _board(
        {
            "c": {
                "type": "line",
                "query": "q",
                "x": "channel",
                "y": "v",
                "style": {"axis_y": axis_y},
            }
        }
    )
    board["queries"] = {
        "q": {
            "columns": ["channel", "v"],
            "values": [[name, v] for name, v in zip("ABC", values, strict=True)],
        }
    }
    svg = render_board_to_svg(_to_yaml(board))
    return [_TAG_RE.sub("", t) for t in _digit_texts(svg)]


def _mirror_edge_texts(values: list[float], fmt: Any) -> list[str]:
    """The ticks the mirror edge paints, past the untouched primary edge's."""
    primary_edge = _line_edge_texts(values)
    both_edges = _line_edge_texts(values, mirror=fmt)
    assert both_edges[: len(primary_edge)] == primary_edge
    return both_edges[len(primary_edge) :]


def _digits_only(texts: list[str]) -> list[str]:
    return [re.sub(r"[^\d.,]", "", t) for t in texts]


_EDGE_VALUES = [[1200, 4500, 7000], [0.2, 0.45, 0.7]]


@pytest.mark.parametrize("values", _EDGE_VALUES)
@pytest.mark.parametrize("preset", ["number", "currency"])
def test_mirror_axis_adding_an_affix_to_a_preset_changes_only_the_affix(
    values: list[float], preset: str
) -> None:
    bare = _mirror_edge_texts(values, preset)
    affixed = _mirror_edge_texts(values, {"spec": preset, "prefix": "GBP "})
    # The zero tick stays bare, as on every axis path.
    assert all(t.startswith("GBP ") for t in affixed if not _is_zero(t)), affixed
    assert _digits_only(affixed) == _digits_only(bare), (bare, affixed)


def _is_zero(text: str) -> bool:
    return text.lstrip("$") == "0"


@pytest.mark.parametrize("values", _EDGE_VALUES)
@pytest.mark.parametrize("preset", ["number", "currency"])
def test_mirror_axis_paints_the_primary_edge_digits(
    values: list[float], preset: str
) -> None:
    mirror = _digits_only(_mirror_edge_texts(values, preset))
    primary = _digits_only(_line_edge_texts(values, primary=preset)[1:])
    assert mirror == primary, (mirror, primary)
    assert not any(_MILLI_RE.search(t) for t in mirror), mirror


@pytest.mark.parametrize("values", _EDGE_VALUES)
def test_mirror_axis_currency_with_a_prefix_keeps_the_dollar_symbol(
    values: list[float],
) -> None:
    affixed = _mirror_edge_texts(values, {"spec": "currency", "prefix": "GBP "})
    assert affixed
    assert all(t.startswith("GBP $") for t in affixed if not _is_zero(t)), affixed


@pytest.mark.parametrize("build", _SUPPORT_TABLE_BUILDERS, ids=lambda b: b.__name__)
def test_support_table_notation_alone_is_honored_like_with_a_prefix(
    build: Callable[[str, float], dict[str, Any]],
) -> None:
    def cells(fmt: dict[str, str]) -> list[str]:
        board = build("none", BANDS["large"])
        for entry in board["charts"]["c"]["support_table"]["entries"]:
            entry["format"] = fmt
        svg = render_board_to_svg(_to_yaml(board))
        return [_TAG_RE.sub("", t) for t in _digit_texts(svg)]

    notation_only = cells({"spec": ".3~s", "notation": "analytic"})
    with_prefix = cells({"spec": ".3~s", "notation": "analytic", "prefix": "€"})
    assert "12.4 M" in notation_only, notation_only
    assert "€12.4 M" in with_prefix, with_prefix

"""Inherit resolver direct tests: apply_inherit must fill graph-covered fields correctly.

Each test sets a distinctive value at a source path, clears the destination,
runs apply_inherit(merged, graph), and asserts the
destination received the source value.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    list_built_in_themes,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import (
    _obj_path,
    apply_inherit,
)


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


def _get_at(obj: object, path: str) -> object:
    parts = path.split(".")
    cur = obj

    if parts and parts[0] == type(cur).__name__:
        parts = parts[1:]
    for p in parts:
        cur = getattr(cur, p, None)
        if cur is None:
            return None
    return cur


# ── Font-subtree distinctive-value fixtures ──────────────────────────────────


def test_parity_root_font_fills_charts_font_color():
    """charts.font.color inherits from font.color (single hop).

    Proves the declared link is correct: when root font.color is set to a
    distinctive value and charts.font.color is cleared, both resolvers fill
    charts.font.color from the root.
    """
    graph = get_inherit_graph()
    target = "Style.charts.font.color"

    base = get_theme_style("clarity")
    distinctive = "#f00ba2"

    patched_charts_font = base.charts.font.model_copy(update={"color": None})
    patched_root_font = base.font.model_copy(update={"color": distinctive})
    merged = base.model_copy(
        update={
            "font": patched_root_font,
            "charts": base.charts.model_copy(update={"font": patched_charts_font}),
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_charts_font_fills_legend_label_font_color():
    """charts.legend.label.font.color inherits from charts.font.color, NOT root font.

    The multi-hop chain (legend ← charts.font ← font) is split into single
    parent links.  This fixture proves the intermediate is respected: when
    charts.font.color is set to a value different from root font.color, the
    legend fills from charts.font.color, not from root.
    """
    graph = get_inherit_graph()
    target = "Style.charts.legend.label.font.color"

    base = get_theme_style("clarity")
    root_color = "#ff0000"
    charts_color = "#00ff00"  # distinct from root

    # Set charts.font.color to something different from root font
    patched_charts_font = base.charts.font.model_copy(update={"color": charts_color})
    # Clear legend.label.font.color so it must be filled
    patched_label_font = base.charts.legend.label.font.model_copy(
        update={"color": None}
    )
    patched_label = base.charts.legend.label.model_copy(
        update={"font": patched_label_font}
    )
    patched_legend = base.charts.legend.model_copy(update={"label": patched_label})
    patched_root_font = base.font.model_copy(update={"color": root_color})
    merged = base.model_copy(
        update={
            "font": patched_root_font,
            "charts": base.charts.model_copy(
                update={"font": patched_charts_font, "legend": patched_legend}
            ),
        }
    )
    result = apply_inherit(merged, graph)
    # Must resolve to charts.font.color, not root font.color
    assert _get_at(result, target) == charts_color


# ── Axis slot: SkipInheritSlots contract ─────────────────────────────────────
#
# axis_x/axis_y/axis_quantitative carry SkipInheritSlots, not InheritSlot:
# apply_inherit never touches them at compile time. The axis→axis_x/y/quantitative
# cascade happens at render time via resolved_axis_style() (style_cascade.py),
# covered by dbt-charts/tests/core/test_axis_cascade.py.


def test_axis_slots_absent_from_inherit_graph():
    """axis_x/axis_y/axis_quantitative are never expanded into the inherit graph."""
    graph = get_inherit_graph()
    for slot in ("axis_x", "axis_y", "axis_quantitative"):
        prefix = f"Style.charts.{slot}."
        matches = [p for p in graph if p.startswith(prefix)]
        assert not matches, f"{prefix}* must not appear in the inherit graph: {matches}"


# ── Non-cascaded field exclusion ─────────────────────────────────────────────


# Fields _fill_axis does NOT cascade: must be absent from the inherit graph so
# apply_inherit must not fill them — they are intentionally non-cascaded.
_NON_CASCADED = [
    "Style.charts.axis_x.grid.dash",
    "Style.charts.axis_x.ticks.count",
    "Style.charts.axis_x.scale.nice",
    "Style.charts.axis_x.position",
    "Style.charts.axis_x.format",
    "Style.charts.axis_x.band_position",
    "Style.charts.axis_x.categorical_orient",
    "Style.charts.axis_x.fill",
    "Style.charts.axis_x.offset",
    "Style.charts.axis_x.time_unit",
    "Style.charts.axis_x.type",
    "Style.charts.axis_x.values",
]


@pytest.mark.parametrize("path", _NON_CASCADED)
def test_non_cascaded_field_not_in_graph(path: str) -> None:
    """Fields _fill_axis does not cascade must be absent from the inherit graph.

    apply_inherit fills from the graph; if a non-cascaded field were present,
    apply_inherit would fill it while the old cascade logic would not.
    """
    graph = get_inherit_graph()
    assert path not in graph, (
        f"{path!r} is in the inherit graph but _fill_axis does not cascade it; "
        "apply_inherit would fill it incorrectly."
    )


def test_parity_grid_dash_divergence():
    """Regression: axis_x.grid.dash must NOT be filled by apply_inherit.

    _fill_axis does not cascade grid.dash. If axis_x.grid.dash is in the graph,
    apply_inherit would propagate a non-None axis.grid.dash to axis_x incorrectly.
    """
    graph = get_inherit_graph()
    target = "Style.charts.axis_x.grid.dash"
    base = get_theme_style("clarity")
    # Patch a distinctive dash on the canonical axis
    patched_axis = base.charts.axis.model_copy(
        update={"grid": base.charts.axis.grid.model_copy(update={"dash": [4.0, 2.0]})}
    )
    merged = base.model_copy(
        update={"charts": base.charts.model_copy(update={"axis": patched_axis})}
    )
    assert target not in graph, (
        f"{target!r} must not be in the inherit graph — axis_x.grid.dash is not cascaded"
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) is None


# ── Marks slot fixtures ──────────────────────────────────────────────────────


def test_parity_bar_marks_bar_padding():
    """bar.marks.bar.padding inherits from charts.marks.bar.padding via InheritSlot.

    Skipped until InheritSlot(from_path='Style.charts.marks') is added to
    each per-family marks field.
    """
    graph = get_inherit_graph()
    target = "Style.charts.bar.marks.bar.padding"

    base = get_theme_style("clarity")
    distinctive = 42.0

    patched_global_bar = base.charts.marks.bar.model_copy(
        update={"padding": distinctive}
    )
    patched_global_marks = base.charts.marks.model_copy(
        update={"bar": patched_global_bar}
    )
    patched_family_bar = base.charts.bar.marks.bar.model_copy(update={"padding": None})
    patched_family_marks = base.charts.bar.marks.model_copy(
        update={"bar": patched_family_bar}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "marks": patched_global_marks,
                    "bar": base.charts.bar.model_copy(
                        update={"marks": patched_family_marks}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_line_marks_line_halo_multiplier():
    """line.marks.line.halo_multiplier inherits from charts.marks.line.halo_multiplier via InheritSlot.

    stroke: StrokeStyle | None uses SkipInheritSlots (nullable container — _cascade_family_marks
    fills the whole object from global; apply_inherit can't navigate through a None parent).
    Use halo_multiplier (a scalar) to verify the InheritSlot expansion works on line marks.

    Skipped until InheritSlot(from_path='Style.charts.marks') is added to
    LineChartStyle.marks.
    """
    graph = get_inherit_graph()
    target = "Style.charts.line.marks.line.halo_multiplier"

    base = get_theme_style("clarity")
    distinctive = 9.9

    patched_global_line = base.charts.marks.line.model_copy(
        update={"halo_multiplier": distinctive}
    )
    patched_global_marks = base.charts.marks.model_copy(
        update={"line": patched_global_line}
    )
    patched_family_line = base.charts.line.marks.line.model_copy(
        update={"halo_multiplier": None}
    )
    patched_family_marks = base.charts.line.marks.model_copy(
        update={"line": patched_family_line}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "marks": patched_global_marks,
                    "line": base.charts.line.model_copy(
                        update={"marks": patched_family_marks}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── Chart base scalar fixtures ────────────────────────────────────────────────


def test_parity_chart_base_aspect_ratio():
    """bar.aspect_ratio inherits from charts.aspect_ratio via Inherit marker.

    Skipped until Inherit(from_path='Style.charts.aspect_ratio') is added to
    _ChartStyleBase.aspect_ratio and factory forwards the marker.
    """
    graph = get_inherit_graph()
    target = "Style.charts.bar.aspect_ratio"

    base = get_theme_style("clarity")
    distinctive = 99.0

    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "aspect_ratio": distinctive,
                    "bar": base.charts.bar.model_copy(update={"aspect_ratio": None}),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_chart_base_palette():
    """Board-level palette derives from charts.color.categorical.palette, not from apply_inherit.

    The color.categorical.palette field on ChartsStyle populates ResolvedChartsStyle.palette
    at theme-resolve time (_build_resolved_charts). Chart-local categorical overrides
    flow through _effective_palette at resolve() time, not through apply_inherit.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.primitives import CategoricalColorStyle
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    base = get_theme_style("clarity")
    distinctive = ["#111111", "#222222"]

    # Patch charts.color.categorical.palette with a distinctive value.
    existing_cat = base.charts.color.categorical
    new_cat = (
        existing_cat.model_copy(update={"palette": distinctive})
        if existing_cat is not None
        else CategoricalColorStyle(palette=distinctive)
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "color": base.charts.color.model_copy(
                        update={"categorical": new_cat}
                    ),
                }
            )
        }
    )
    board = resolve_chart_style_context(merged)
    assert list(board.palette) == distinctive


# ── KPI font subtree fixtures ────────────────────────────────────────────────


def test_parity_kpi_font_fills_from_charts_font():
    """kpi.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.kpi.font.color"

    base = get_theme_style("clarity")
    distinctive = "#aa1122"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_kpi_font = base.charts.kpi.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "kpi": base.charts.kpi.model_copy(
                        update={"font": patched_kpi_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_kpi_value_font_fills_from_kpi_font():
    """kpi.value.font inherits from kpi.font via InheritSlot.

    Pinned on `weight`, not `color`: the slot excludes `color` from the fill
    so it stays a sentinel the renderer can read as "unauthored".
    """
    graph = get_inherit_graph()
    target = "Style.charts.kpi.value.font.weight"

    base = get_theme_style("clarity")
    distinctive = 771.0

    patched_kpi_font = base.charts.kpi.font.model_copy(update={"weight": distinctive})
    patched_value_font = base.charts.kpi.value.font.model_copy(update={"weight": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={
                            "font": patched_kpi_font,
                            "value": base.charts.kpi.value.model_copy(
                                update={"font": patched_value_font}
                            ),
                        }
                    )
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_kpi_label_font_fills_from_kpi_font():
    """kpi.label.font inherits from kpi.font via InheritSlot.

    Pinned on `weight`, not `color`: the slot excludes `color` from the fill
    so it stays a sentinel the renderer can read as "unauthored".
    """
    graph = get_inherit_graph()
    target = "Style.charts.kpi.label.font.weight"

    base = get_theme_style("clarity")
    distinctive = 813.0

    patched_kpi_font = base.charts.kpi.font.model_copy(update={"weight": distinctive})
    patched_label_font = base.charts.kpi.label.font.model_copy(update={"weight": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "kpi": base.charts.kpi.model_copy(
                        update={
                            "font": patched_kpi_font,
                            "label": base.charts.kpi.label.model_copy(
                                update={"font": patched_label_font}
                            ),
                        }
                    )
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── Table font subtree fixtures ───────────────────────────────────────────────


def test_parity_table_font_fills_from_charts_font():
    """table.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.table.font.color"

    base = get_theme_style("clarity")
    distinctive = "#dd4455"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_table_font = base.charts.table.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "table": base.charts.table.model_copy(
                        update={"font": patched_table_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_table_header_font_fills_from_table_font():
    """table.header.font inherits from table.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.table.header.font.color"

    base = get_theme_style("clarity")
    distinctive = "#ee5566"

    patched_table_font = base.charts.table.font.model_copy(
        update={"color": distinctive}
    )
    patched_header_font = base.charts.table.header.font.model_copy(
        update={"color": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "table": base.charts.table.model_copy(
                        update={
                            "font": patched_table_font,
                            "header": base.charts.table.header.model_copy(
                                update={"font": patched_header_font}
                            ),
                        }
                    )
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_table_spark_bar_font_fills_from_charts_font():
    """table.spark.bar.font inherits from charts.font (not table.font) via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.table.spark.bar.font.color"

    base = get_theme_style("clarity")
    distinctive = "#ff6677"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_bar_font = base.charts.table.spark.bar.font.model_copy(
        update={"color": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "table": base.charts.table.model_copy(
                        update={
                            "spark": base.charts.table.spark.model_copy(
                                update={
                                    "bar": base.charts.table.spark.bar.model_copy(
                                        update={"font": patched_bar_font}
                                    )
                                }
                            )
                        }
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── spark_bar font fixture ────────────────────────────────────────────────────


def test_parity_spark_bar_font_fills_from_charts_font():
    """spark_bar.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.spark_bar.font.color"

    base = get_theme_style("clarity")
    distinctive = "#112233"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_sb_font = base.charts.spark_bar.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "spark_bar": base.charts.spark_bar.model_copy(
                        update={"font": patched_sb_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── support_table font subtree fixtures ─────────────────────────────────────────


def test_parity_support_table_font_fills_from_charts_font():
    """support_table.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.support_table.font.color"

    base = get_theme_style("clarity")
    distinctive = "#334455"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_dt_font = base.charts.support_table.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "support_table": base.charts.support_table.model_copy(
                        update={"font": patched_dt_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_support_table_label_font_fills_from_support_table_font():
    """support_table.label.font inherits from support_table.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.support_table.label.font.size"

    base = get_theme_style("clarity")
    distinctive = 17.0

    patched_dt_font = base.charts.support_table.font.model_copy(
        update={"size": distinctive}
    )
    patched_label_font = base.charts.support_table.label.font.model_copy(
        update={"size": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "support_table": base.charts.support_table.model_copy(
                        update={
                            "font": patched_dt_font,
                            "label": base.charts.support_table.label.model_copy(
                                update={"font": patched_label_font}
                            ),
                        }
                    )
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── tooltip font subtree fixtures ─────────────────────────────────────────────


def test_parity_tooltip_font_fills_from_charts_font():
    """tooltip.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.tooltip.font.color"

    base = get_theme_style("clarity")
    distinctive = "#556677"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_tt_font = base.charts.tooltip.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "tooltip": base.charts.tooltip.model_copy(
                        update={"font": patched_tt_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_tooltip_label_font_fills_from_charts_font():
    """tooltip.label.font inherits directly from charts.font (not tooltip.font)."""
    graph = get_inherit_graph()
    target = "Style.charts.tooltip.label.font.color"

    base = get_theme_style("clarity")
    distinctive = "#667788"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_label_font = base.charts.tooltip.label.font.model_copy(
        update={"color": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "tooltip": base.charts.tooltip.model_copy(
                        update={
                            "label": base.charts.tooltip.label.model_copy(
                                update={"font": patched_label_font}
                            )
                        }
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── series_label font fixture ─────────────────────────────────────────────────


def test_parity_series_label_font_fills_from_charts_font():
    """series_label.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.series_label.font.color"

    base = get_theme_style("clarity")
    distinctive = "#778899"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_sl_font = base.charts.series_label.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "series_label": base.charts.series_label.model_copy(
                        update={"font": patched_sl_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── pie total font subtree fixtures ──────────────────────────────────────────


def test_parity_pie_total_value_font_fills_from_charts_font():
    """pie.total.value.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.pie.total.value.font.color"

    base = get_theme_style("clarity")
    distinctive = "#8899aa"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_value_font = base.charts.pie.total.value.font.model_copy(
        update={"color": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "pie": base.charts.pie.model_copy(
                        update={
                            "total": base.charts.pie.total.model_copy(
                                update={
                                    "value": base.charts.pie.total.value.model_copy(
                                        update={"font": patched_value_font}
                                    )
                                }
                            )
                        }
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── callout font subtree fixtures ─────────────────────────────────────────────


def test_parity_callout_title_font_fills_from_charts_font():
    """callout.title.font inherits from charts.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.charts.callout.title.font.color"

    base = get_theme_style("clarity")
    distinctive = "#99aabb"

    patched_charts_font = base.charts.font.model_copy(update={"color": distinctive})
    patched_title_font = base.charts.callout.title.font.model_copy(
        update={"color": None}
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": patched_charts_font,
                    "callout": base.charts.callout.model_copy(
                        update={
                            "title": base.charts.callout.title.model_copy(
                                update={"font": patched_title_font}
                            )
                        }
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── variables font subtree fixtures ──────────────────────────────────────────


def test_parity_variables_font_fills_from_root_font():
    """variables.font inherits from root font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.variables.font.color"

    base = get_theme_style("clarity")
    distinctive = "#aabbcc"

    patched_root_font = base.font.model_copy(update={"color": distinctive})
    patched_var_font = base.variables.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "font": patched_root_font,
            "variables": base.variables.model_copy(update={"font": patched_var_font}),
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_variables_label_font_fills_from_variables_font():
    """variables.label.font inherits from variables.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.variables.label.font.color"

    base = get_theme_style("clarity")
    distinctive = "#bbccdd"

    patched_var_font = base.variables.font.model_copy(update={"color": distinctive})
    patched_label_font = base.variables.label.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "variables": base.variables.model_copy(
                update={
                    "font": patched_var_font,
                    "label": base.variables.label.model_copy(
                        update={"font": patched_label_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


def test_parity_variables_placeholder_font_fills_from_variables_font():
    """variables.placeholder.font inherits from variables.font via InheritSlot."""
    graph = get_inherit_graph()
    target = "Style.variables.placeholder.font.color"

    base = get_theme_style("clarity")
    distinctive = "#ccdde0"

    patched_var_font = base.variables.font.model_copy(update={"color": distinctive})
    patched_ph_font = base.variables.placeholder.font.model_copy(update={"color": None})
    merged = base.model_copy(
        update={
            "variables": base.variables.model_copy(
                update={
                    "font": patched_var_font,
                    "placeholder": base.variables.placeholder.model_copy(
                        update={"font": patched_ph_font}
                    ),
                }
            )
        }
    )
    result = apply_inherit(merged, graph)
    assert _get_at(result, target) == distinctive


# ── Skip/null passthrough ────────────────────────────────────────────────────


def test_skip_passthrough_none_stays_none_outside_graph():
    """Fields not in the graph keep their None value — apply_inherit is a scoped no-op."""
    base = get_theme_style("clarity")
    graph = get_inherit_graph()
    result = apply_inherit(base, graph)
    # Any None field on the raw theme that has no graph entry must remain None.
    # charts.axis_x.label.font.color is a sentinel — if it's not in the graph it stays.
    target = "Style.charts.axis_x.label.font.color"
    if target not in graph:
        assert _get_at(result, target) == _get_at(base, target)


# ── Per-family SkipInheritSlots guard ────────────────────────────────────────


def test_global_tooltip_chains_in_graph():
    """Global charts.tooltip.font.* paths must appear in the inherit graph.

    No per-family tooltip slot exists anymore; apply_inherit handles only the
    global chains that feed charts.font.* inheritance.
    """
    graph = get_inherit_graph()
    assert "Style.charts.tooltip.font.color" in graph
    assert "Style.charts.tooltip.label.font.color" in graph


def test_per_family_support_table_inherits_from_global():
    """Per-family support_table.font.* inherits from charts.support_table.font.* via InheritSlot.

    _CartesianChartStyle.support_table now carries InheritSlot(from_path="Style.charts.support_table"),
    so apply_inherit fills per-family sub-fields from the global support_table.
    """
    graph = get_inherit_graph()
    per_family_paths = [
        "Style.charts.bar.support_table.font.color",
        "Style.charts.line.support_table.font.color",
        "Style.charts.bar.support_table.label.font.size",
    ]
    missing = [p for p in per_family_paths if p not in graph]
    assert not missing, (
        f"Per-family support_table font paths must be in the inherit graph: {missing}"
    )
    # Global chains must still be present too.
    assert "Style.charts.support_table.font.color" in graph
    assert "Style.charts.support_table.label.font.size" in graph


# ── Full-theme graph coverage ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "theme", [t for t in list_built_in_themes() if not t.startswith("_")]
)
def test_graph_coverage_all_themes(theme: str) -> None:
    """For every built-in theme: each graph-covered path whose parent is non-None must be filled.

    This is the full-tree regression guard: if apply_inherit fails to propagate any
    link that the old push-cascade covered, a None child with a non-None parent surfaces
    here across every production theme.
    """
    compiled = get_theme_style(theme)
    graph = get_inherit_graph()
    resolved = apply_inherit(compiled, graph)

    failures: list[str] = []
    for child_path, parent_path in graph.items():
        child_obj = _obj_path(child_path)
        # Check that the immediate container of the child is reachable.
        # When an intermediate optional container is None (e.g. bar.legend=None),
        # the resolver intentionally skips the write — that is not a failure.
        container = ".".join(child_obj.split(".")[:-1])
        if container and _get_at(resolved, container) is None:
            continue
        parent_val = _get_at(resolved, _obj_path(parent_path))
        child_val = _get_at(resolved, child_obj)
        if parent_val is not None and child_val is None:
            failures.append(
                f"  {child_path!r} is None but parent {parent_path!r} = {parent_val!r}"
            )

    assert not failures, (
        f"Theme {theme!r}: {len(failures)} unfilled graph paths:\n"
        + "\n".join(failures[:20])
    )

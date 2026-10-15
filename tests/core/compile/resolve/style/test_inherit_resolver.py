"""Unit tests for apply_inherit: covers resolution, chaining, memoization, and edge cases.

These tests drive apply_inherit with synthetic InheritGraph dicts so the algorithm
is exercised directly, independent of whether real Inherit/InheritSlot markers have
been added to the production Style model.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import BaseModel, ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.primitives import FontStyle
from dbt_charts.core.compile.models.style.authored import PaddingStylePatch
from dbt_charts.core.compile.models.style.theme.axis import (
    BaseScaleStyle,
    ScaleContinuousStyle,
    ScaleLogStyle,
    XScaleStyle,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit


def _field_set_tree(model: BaseModel) -> tuple[frozenset[str], dict[str, object]]:
    children = {
        name: _field_set_tree(value)
        for name in type(model).model_fields
        if isinstance(value := getattr(model, name), BaseModel)
    }
    return frozenset(model.model_fields_set), children


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


# ── Direct fill from a single fallback ───────────────────────────────────────


def test_fill_from_single_fallback():
    """None leaf is filled from its first non-None fallback."""
    base = get_theme_style("clarity")
    distinctive = "#a1b2c3"
    patched_axis = base.charts.axis.model_copy(
        update={
            "labels": base.charts.axis.labels.model_copy(
                update={
                    "font": base.charts.axis.labels.font.model_copy(
                        update={"color": distinctive}
                    )
                }
            )
        }
    )
    patched_axis_x = base.charts.axis_x.model_copy(
        update={
            "labels": base.charts.axis_x.labels.model_copy(
                update={
                    "font": base.charts.axis_x.labels.font.model_copy(
                        update={"color": None}
                    )
                }
            )
        }
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={"axis": patched_axis, "axis_x": patched_axis_x}
            )
        }
    )
    graph = {
        "Style.charts.axis_x.labels.font.color": "Style.charts.axis.labels.font.color"
    }
    result = apply_inherit(merged, graph)

    assert result.charts.axis_x.labels.font.color == distinctive
    # Unchanged fields are preserved
    assert result.charts.axis.labels.font.color == distinctive


def test_empty_graph_returns_merged_unchanged():
    """Empty graph short-circuits — identical object returned."""
    base = get_theme_style("clarity")
    result = apply_inherit(base, {})
    assert result is base


def test_no_nones_in_graph_returns_merged_unchanged():
    """If no graph-covered path is None, nothing is written and merged is returned."""
    base = get_theme_style("clarity")
    # axis.labels.font.color is non-None on a compiled theme
    graph = {"Style.charts.axis.labels.font.color": "Style.font.color"}
    result = apply_inherit(base, graph)
    # Since the source is non-None, no update happens
    assert result is base


# ── Chained fallback (A → B → C) ─────────────────────────────────────────────


def test_chained_fallback_resolved_in_one_pass():
    """A→B→C: when A and B are None, C's value propagates to both."""
    base = get_theme_style("clarity")
    distinctive = "#deadbe"
    # Set root font color (C)
    root_font = base.font.model_copy(update={"color": distinctive})
    # Clear charts.axis.labels.font.color (B) and charts.axis_x.labels.font.color (A)
    axis_label = base.charts.axis.labels.model_copy(
        update={"font": base.charts.axis.labels.font.model_copy(update={"color": None})}
    )
    axis_x_label = base.charts.axis_x.labels.model_copy(
        update={
            "font": base.charts.axis_x.labels.font.model_copy(update={"color": None})
        }
    )
    merged = base.model_copy(
        update={
            "font": root_font,
            "charts": base.charts.model_copy(
                update={
                    "axis": base.charts.axis.model_copy(update={"labels": axis_label}),
                    "axis_x": base.charts.axis_x.model_copy(
                        update={"labels": axis_x_label}
                    ),
                }
            ),
        }
    )
    graph = {
        "Style.charts.axis_x.labels.font.color": "Style.charts.axis.labels.font.color",
        "Style.charts.axis.labels.font.color": "Style.font.color",
    }
    result = apply_inherit(merged, graph)

    assert result.charts.axis.labels.font.color == distinctive
    assert result.charts.axis_x.labels.font.color == distinctive


# ── Memoization ───────────────────────────────────────────────────────────────


def test_multiple_dependents_on_shared_fallback(monkeypatch):
    """Two graph entries sharing a fallback path both receive its value."""
    base = get_theme_style("clarity")
    distinctive = "#c0ffee"
    root_font = base.font.model_copy(update={"color": distinctive})
    # Clear two leaves that both fall back to font.color
    axis_label = base.charts.axis.labels.model_copy(
        update={"font": base.charts.axis.labels.font.model_copy(update={"color": None})}
    )
    axis_y_label = base.charts.axis_y.labels.model_copy(
        update={
            "font": base.charts.axis_y.labels.font.model_copy(update={"color": None})
        }
    )
    merged = base.model_copy(
        update={
            "font": root_font,
            "charts": base.charts.model_copy(
                update={
                    "axis": base.charts.axis.model_copy(update={"labels": axis_label}),
                    "axis_y": base.charts.axis_y.model_copy(
                        update={"labels": axis_y_label}
                    ),
                }
            ),
        }
    )
    graph = {
        "Style.charts.axis.labels.font.color": "Style.font.color",
        "Style.charts.axis_y.labels.font.color": "Style.font.color",
    }
    result = apply_inherit(merged, graph)

    assert result.charts.axis.labels.font.color == distinctive
    assert result.charts.axis_y.labels.font.color == distinctive


# ── None-intermediate path handling ──────────────────────────────────────────


def test_none_intermediate_path_skipped_gracefully():
    """A graph path through a None optional container is a no-op — no crash."""

    base = get_theme_style("clarity")
    # slice.labels is SliceLabelsStyle | None = None by default in the compiled theme.
    # The path below passes through this None intermediate container.
    assert base.charts.pie.marks.slice.labels is None
    graph = {
        "Style.charts.pie.marks.slice.labels.font.color": "Style.charts.pie.total.value.font.color"
    }
    # Should not raise even though an intermediate node is None.
    result = apply_inherit(base, graph)
    # The labels container stays None — the write is skipped by _set.
    assert result.charts.pie.marks.slice.labels is None


# ── Direct parent wins over deeper chain ─────────────────────────────────────


def test_direct_parent_wins_over_chain():
    """Direct parent (charts.font.color) is used; the deeper node (font.color) is not."""
    base = get_theme_style("clarity")
    charts_color = "#111111"
    root_color = "#222222"
    root_font = base.font.model_copy(update={"color": root_color})
    charts_font = base.charts.font.model_copy(update={"color": charts_color})
    axis_label = base.charts.axis.labels.model_copy(
        update={"font": base.charts.axis.labels.font.model_copy(update={"color": None})}
    )
    merged = base.model_copy(
        update={
            "font": root_font,
            "charts": base.charts.model_copy(
                update={
                    "font": charts_font,
                    "axis": base.charts.axis.model_copy(update={"labels": axis_label}),
                }
            ),
        }
    )
    # Single-link chain: axis.labels.font.color → charts.font.color
    graph = {"Style.charts.axis.labels.font.color": "Style.charts.font.color"}
    result = apply_inherit(merged, graph)
    # Direct parent (charts.font.color) is used
    assert result.charts.axis.labels.font.color == charts_color


# ── No fallback available ─────────────────────────────────────────────────────


def test_none_stays_none_when_all_fallbacks_none():
    """A None leaf stays None when all fallbacks in the chain are also None."""
    base = get_theme_style("clarity")
    root_font = base.font.model_copy(update={"color": None})
    axis_label = base.charts.axis.labels.model_copy(
        update={"font": base.charts.axis.labels.font.model_copy(update={"color": None})}
    )
    merged = base.model_copy(
        update={
            "font": root_font,
            "charts": base.charts.model_copy(
                update={
                    "axis": base.charts.axis.model_copy(update={"labels": axis_label}),
                }
            ),
        }
    )
    graph = {"Style.charts.axis.labels.font.color": "Style.font.color"}
    result = apply_inherit(merged, graph)
    # Both source and fallback are None — leaf stays None
    assert result.charts.axis.labels.font.color is None


# ── Field-set provenance ──────────────────────────────────────────────────────────────


def test_graph_excluded_legend_field_sets_are_unchanged() -> None:
    base = get_theme_style("clarity")
    legend = base.charts.scatter.legend
    assert legend is not None
    before = _field_set_tree(legend)

    result = apply_inherit(base, get_inherit_graph())

    resolved_legend = result.charts.scatter.legend
    assert resolved_legend is not None
    assert _field_set_tree(resolved_legend) == before


def test_graph_excluded_axis_field_sets_are_unchanged() -> None:
    base = get_theme_style("clarity")
    axis = base.charts.bar.axis_x
    assert axis is not None
    before = _field_set_tree(axis)

    result = apply_inherit(base, get_inherit_graph())

    resolved_axis = result.charts.bar.axis_x
    assert resolved_axis is not None
    assert _field_set_tree(resolved_axis) == before


def test_inherited_leaf_becomes_set_without_its_siblings() -> None:
    base = get_theme_style("clarity")
    distinctive = "#a1b2c3"
    target_font = FontStyle()
    axis_x = base.charts.axis_x.model_copy(
        update={
            "labels": base.charts.axis_x.labels.model_copy(update={"font": target_font})
        }
    )
    merged = base.model_copy(
        update={
            "charts": base.charts.model_copy(
                update={
                    "font": base.charts.font.model_copy(update={"color": distinctive}),
                    "axis_x": axis_x,
                }
            )
        }
    )
    graph = {
        "Style.charts.axis_x.labels.font.color": "Style.charts.font.color",
    }

    result = apply_inherit(merged, graph)

    assert result.charts.axis_x.labels.font.color == distinctive
    assert result.charts.axis_x.labels.font.model_fields_set == {"color"}


def test_container_inheritance_precedes_descendant_fallbacks() -> None:
    base = get_theme_style("clarity")
    source_color = "#a1b2c3"
    fallback_color = "#d4e5f6"
    source_rule = base.charts.marks.rule.model_copy(
        update={
            "stroke": base.charts.marks.rule.stroke.model_copy(
                update={"color": source_color}
            )
        }
    )
    charts = base.charts.model_copy(
        update={
            "font": base.charts.font.model_copy(update={"color": fallback_color}),
            "marks": base.charts.marks.model_copy(update={"rule": source_rule}),
        }
    )
    merged = base.model_copy(update={"charts": charts})
    graph = {
        "Style.charts.line.marks.rule": "Style.charts.marks.rule",
        "Style.charts.line.marks.rule.stroke.color": "Style.charts.font.color",
    }
    container_only = apply_inherit(
        merged,
        {"Style.charts.line.marks.rule": "Style.charts.marks.rule"},
    )

    result = apply_inherit(merged, graph)

    assert result.charts.line.marks.rule == source_rule
    assert result.charts.line.marks.rule.stroke.color == source_color
    assert _field_set_tree(result.charts.line.marks.rule) == _field_set_tree(
        container_only.charts.line.marks.rule
    )


def test_container_inheritance_uses_the_target_model_type() -> None:
    base = get_theme_style("clarity")
    assert base.charts.line.padding is None
    graph = {
        "Style.charts.line.padding": "Style.charts.padding",
    }

    result = apply_inherit(base, graph)

    assert isinstance(result.charts.line.padding, PaddingStylePatch)
    assert result.charts.line.padding.model_dump() == base.charts.padding.model_dump()


def test_container_inheritance_preserves_python_mode_values() -> None:
    base = get_theme_style("clarity")
    # scale.values (unlike scale.continuous.domain) has no int|float|str
    # constraint -- picked so the sentinel is a Python-native type (date) that
    # a mode="json" dump would coerce to an ISO string. apply_inherit snapshots
    # merged.model_dump(mode="python") once up front (preserving the date
    # object), then _model_copy_paths re-validates the container update via
    # TypeAdapter(...).validate_python() on that snapshot -- validate_python
    # only preserves the date because the target (list[Any]) accepts it
    # unchanged; a mode="json" snapshot would hand validate_python a string.
    tick_values = [date(2025, 1, 1), date(2025, 12, 31)]
    charts = base.charts.model_copy(
        update={
            "axis_x": base.charts.axis_x.model_copy(update={"scale": None}),
            "axis_y": base.charts.axis_y.model_copy(
                update={"scale": BaseScaleStyle(values=tick_values)}
            ),
        }
    )
    merged = base.model_copy(update={"charts": charts})
    graph = {
        "Style.charts.axis_x.scale": "Style.charts.axis_y.scale",
    }

    result = apply_inherit(merged, graph)

    assert result.charts.axis_x.scale is not None
    assert result.charts.axis_x.scale.values == tick_values


def test_inherited_cross_field_invalidity_still_raises() -> None:
    base = get_theme_style("clarity")
    source_scale = BaseScaleStyle(
        continuous=ScaleContinuousStyle(
            type="log", log=ScaleLogStyle(base=2), zero=False
        )
    )
    # log must exist (with base=None) so apply_inherit has a non-None
    # container to fill the leaf into — it doesn't construct an entirely
    # missing intermediate container across a graph edge.
    target_scale = XScaleStyle(
        continuous=ScaleContinuousStyle(type="linear", log=ScaleLogStyle())
    )
    charts = base.charts.model_copy(
        update={
            "axis_x": base.charts.axis_x.model_copy(update={"scale": target_scale}),
            "axis_y": base.charts.axis_y.model_copy(update={"scale": source_scale}),
        }
    )
    merged = base.model_copy(update={"charts": charts})
    graph = {
        "Style.charts.axis_x.scale.continuous.log.base": "Style.charts.axis_y.scale.continuous.log.base",
    }

    with pytest.raises(
        ValidationError,
        match="log.base is only meaningful with type: log",
    ):
        apply_inherit(merged, graph)

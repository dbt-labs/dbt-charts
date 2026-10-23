"""Tests for the render-v2 translate_to_vl() function and per-family dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from dbt_charts.core.render.chart.spec import ChartSpec
from dbt_charts.core.render.chart.translate import translate_to_vl

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(mark: str, encoding: dict[str, Any] | None = None) -> ChartSpec:
    return ChartSpec(mark=mark, encoding=encoding or {}, layers=[], config={})


def _mark_type(result: dict[str, Any]) -> str:
    """Extract the VL mark type string regardless of shorthand vs object form."""
    mark = result.get("mark")
    if isinstance(mark, dict):
        return mark["type"]
    return str(mark)


# ---------------------------------------------------------------------------
# VL families — returns a valid VL dict with $schema
# ---------------------------------------------------------------------------


def test_translate_geoshape_returns_vl_dict() -> None:
    """Geoshape translate is implemented; must return a VL dict."""
    result = translate_to_vl(_spec("geoshape"))
    assert isinstance(result, dict)
    assert "$schema" in result


# ---------------------------------------------------------------------------
# Mark name — family-level VL mark mapping
# ---------------------------------------------------------------------------


def test_translate_geoshape_mark_is_geoshape() -> None:
    result = translate_to_vl(_spec("geoshape"))
    assert _mark_type(result) == "geoshape"


# ---------------------------------------------------------------------------
# Unknown marks — raise ValueError
# ---------------------------------------------------------------------------


def test_translate_kpi_mark_raises_unknown() -> None:
    """A spec with mark="kpi" reaches translate_to_vl only in tests or bugs —
    kpi charts are non-VL and routed before emit.  translate_to_vl raises
    because "kpi" is not in _VL_MARKS."""
    with pytest.raises(ValueError, match="kpi"):
        translate_to_vl(_spec("kpi"))


def test_translate_table_mark_raises_unknown() -> None:
    """Same as kpi: "table" is not in _VL_MARKS."""
    with pytest.raises(ValueError, match="table"):
        translate_to_vl(_spec("table"))


def test_translate_unknown_mark_raises() -> None:
    with pytest.raises(ValueError, match="__unknown__"):
        translate_to_vl(_spec("__unknown__"))


# ---------------------------------------------------------------------------
# Translate boundary: typed ChartSpec fields reach the VL output
# ---------------------------------------------------------------------------


def test_projection_field_reaches_vl_spec() -> None:
    """projection on ChartSpec → VL top-level projection dict (via circle mark)."""
    spec = ChartSpec(mark="circle", encoding={}, projection="albersUsa")
    result = translate_to_vl(spec)
    assert result.get("projection") == {"type": "albersUsa"}


# ---------------------------------------------------------------------------
# Endpoint-label wrapping — translate_to_vl must build both hconcat and vconcat
# wrappers from the spec.endpoint_label_layout field.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# spec.tooltip_description must reach the actual chart pane, not the
# hconcat/vconcat wrapper — the wrapper carries no data marks of its own.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# FIX-3: href_link wiring
# ---------------------------------------------------------------------------


# Endpoint labels in _translate_layered
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# dark_companion_range: when set, label pane uses it instead of color_range
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Label pane width / font / height — prevents main-pane horizontal compression
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# assemble_final_vl contract
# ---------------------------------------------------------------------------


def test_assemble_final_vl_non_vl_mark_raises() -> None:
    """assemble_final_vl rejects non-VL family marks (kpi, table)."""
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.render.chart.translate import assemble_final_vl

    board_style = resolve_style(get_theme_style())
    spec = ChartSpec(mark="kpi", encoding={})
    with pytest.raises(ValueError, match="kpi"):
        assemble_final_vl(spec, board_style)

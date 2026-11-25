"""Line palettes: the `line` palette family and the `palettes.line_category` role."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
import yaml
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import LineChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedLineChart
from dbt_charts.core.compile.models.palette import (
    LineStyle,
    Palette,
    build_palette_index,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.compile.resolve.style.palette import (
    LineAsPaletteError,
    palette,
)

from .._paths import DBT_CHARTS_PKG_DIR
from ._engine_render import render_chart_svg

_PALETTES_DIR = DBT_CHARTS_PKG_DIR / "core" / "defaults" / "palettes"
_DATA: list[dict[str, str | int]] = [
    {"month": "2024-01-01", "value": 10, "series": "New"},
    {"month": "2024-02-01", "value": 8, "series": "New"},
    {"month": "2024-01-01", "value": 5, "series": "Won"},
    {"month": "2024-02-01", "value": 3, "series": "Won"},
]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_config()
    yield
    reset_config()


def _line_one_ink() -> Palette:
    data = yaml.safe_load((_PALETTES_DIR / "line" / "line-one-ink.yml").read_text())
    return Palette.model_validate(data)


def _resolve_line(
    style: dict[str, object], chart_style: dict[str, object] | None = None
) -> ResolvedLineChart:
    chart = LineChart(
        style=chart_style or {},
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="value",
        color="series",
    )
    context = resolve_chart_style_context(
        get_theme_style(), StylePatch.model_validate(style)
    )
    resolved = resolve(chart, _DATA, context, width=900.0)
    assert isinstance(resolved, ResolvedLineChart)
    return resolved


def test_line_family_is_indexed() -> None:
    assert build_palette_index(_PALETTES_DIR)["line-one-ink"]["family"] == "line"


def test_line_one_ink_holds_ten_styles_with_fractional_dashes() -> None:
    styles = _line_one_ink().styles
    assert styles is not None
    assert len(styles) == 10
    assert styles[3].dash == (0.1, 4.5)
    assert styles[6].inline is not None
    assert styles[6].inline.offset == -4


def test_a_style_defaults_to_a_solid_butt_line_with_no_window() -> None:
    style = LineStyle(width=2)
    assert (style.dash, style.cap, style.inline) == ((), "butt", None)


@pytest.mark.parametrize(
    "extra",
    [{"motifs": ["dither"]}, {"colors": ["#000000"]}, {"ramp": "dots", "steps": 6}],
)
def test_a_line_palette_takes_only_styles(extra: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Palette.model_validate(
            {"name": "l", "kind": "line", "styles": [{"width": 2}], **extra}
        )


def test_a_line_palette_needs_styles() -> None:
    with pytest.raises(ValidationError):
        Palette.model_validate({"name": "l", "kind": "line"})


def test_a_line_palette_has_no_color_stops() -> None:
    with pytest.raises(LineAsPaletteError, match="line palette"):
        palette("line-one-ink")


def test_the_role_resolves_ten_styles_on_a_line_chart() -> None:
    resolved = _resolve_line({"palettes": {"line_category": "line-one-ink"}})
    assert resolved.style.line_styles == _line_one_ink().styles
    assert resolved.style.line_mark.stroke.width == 3.5


def test_no_role_leaves_lines_plain() -> None:
    assert _resolve_line({}).style.line_styles == ()


def test_authored_dashes_win_over_the_line_palette() -> None:
    resolved = _resolve_line(
        {
            "palettes": {"line_category": "line-one-ink"},
            "charts": {"dashes": [[6, 3], [2, 2]]},
        }
    )
    assert resolved.style.dashes == [[6, 3], [2, 2]]
    assert resolved.style.line_styles == ()


def test_an_authored_line_width_sets_every_palette_style_width() -> None:
    resolved = _resolve_line(
        {"palettes": {"line_category": "line-one-ink"}},
        {"marks": {"line": {"stroke": {"width": 2}}}},
    )
    assert resolved.style.line_mark.stroke.width == 2
    assert {style.width for style in resolved.style.line_styles} == {2}
    assert len(resolved.style.line_styles) == 10
    hollow = resolved.style.line_styles[6]
    assert hollow.inline is not None
    assert hollow.inline.width == 1  # kept at the palette's inline-to-ink ratio


def test_an_authored_line_width_draws_every_series_at_that_width() -> None:
    chart = LineChart(
        id="t",
        query=SqlQuery(sql="SELECT 1", source="t"),
        query_name="q",
        type="line",
        x="month",
        y="value",
        color="series",
        style={"marks": {"line": {"stroke": {"width": 2}}}},
    )
    board_style, context = resolve_style_and_context(
        get_theme_style(),
        StylePatch.model_validate({"palettes": {"line_category": "line-one-ink"}}),
    )
    svg = render_chart_svg(
        chart,
        _DATA,
        width=600,
        height=360,
        board_style=board_style,
        chart_style_context=context,
    )
    widths = re.findall(r'<path[^>]*stroke-width="([\d.]+)"[^>]*data-dbt-series', svg)
    assert widths
    assert set(widths) == {"2"}


def test_a_role_naming_a_color_palette_is_refused_with_the_role_named() -> None:
    with pytest.raises(ValueError, match=r"palettes\.line_category.*vivid-10"):
        _resolve_line({"palettes": {"line_category": "vivid-10"}})

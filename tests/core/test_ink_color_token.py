"""A board's `style.roles.ink` hex, and the `palettes.fill_category` role.

A hex `roles.ink` is the board's ink: every leaf written `ink` takes it, and it
is used as set on any canvas. A themed `roles.ink` (a palette token, as every
built-in theme sets it) moves nothing. No built-in theme draws in the bare role
yet, so every board here sets the leaves itself, the way a one-ink theme will.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_theme_style, reset_config
from dbt_charts.core.compile.models.chart.normalized import (
    AreaChart,
    BarChart,
    PieChart,
)
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.compile.resolve.style.palette import UnknownColorError
from dbt_charts.core.diagnostics import WARN_LOW_TEXT_CONTRAST
from dbt_charts.core.execute.env import ExecEnv

from .._scripted_env import ScriptedBackend, scripted_env
from ._svg_render import render_board_svg_result

if TYPE_CHECKING:
    from dbt_charts.cli.filesystem_project import FilesystemProject
    from dbt_charts.core.board import BoardRenderResult
    from dbt_charts.core.compile.models.style.context import ChartStyleContext
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

NAVY = "#1F3A68"
CHARCOAL = "#2A2A2A"
CREAM = "#FEFCF9"
DARK = "#161616"

# The leaves a one-ink theme binds to its ink: text, the single-series fill
# and each patterned family's pattern ink. A palette list names no role, so the
# single-series leaf holds the hex itself.
_INKED: dict[str, Any] = {
    "font": {"color": "ink"},
    "charts": {
        "color": {"categorical": {"single_series_palette": [NAVY]}},
        "bar": {"pattern": {"color": "ink"}},
        "area": {"pattern": {"color": "ink"}},
        "pie": {"pattern": {"color": "ink"}},
    },
}


@pytest.fixture(autouse=True)
def _reset() -> Any:
    reset_config()
    yield
    reset_config()


def _style(**patch: Any) -> tuple[ResolvedStyle, ChartStyleContext]:
    ink = patch.get("roles", {}).get("ink", NAVY)
    inked = {
        **_INKED,
        "charts": {
            **_INKED["charts"],
            "color": {"categorical": {"single_series_palette": [ink]}},
        },
    }
    return resolve_style_and_context(
        get_theme_style(), StylePatch.model_validate({**inked, **patch})
    )


def _inked_leaves(resolved: ResolvedStyle, ctx: ChartStyleContext) -> list[str | None]:
    return [
        resolved.font.color,
        ctx.single_series_palette[0],
        ctx.bar.pattern.color,
        ctx.area.pattern.color,
        ctx.pie.pattern.color,
    ]


# --- ink ------------------------------------------------------------


@pytest.mark.parametrize("ink", [NAVY, "#7A1F1F"])
def test_every_inked_leaf_resolves_to_the_board_ink(ink: str) -> None:
    assert _inked_leaves(*_style(roles={"ink": ink}, background=CREAM)) == [ink] * 5


def test_a_hex_ink_role_changes_nothing_that_does_not_draw_in_it() -> None:
    base, _ = resolve_style_and_context(get_theme_style())
    inked, _ = resolve_style_and_context(
        get_theme_style(), StylePatch.model_validate({"roles": {"ink": NAVY}})
    )
    assert inked.font.color == base.font.color


def test_only_the_ink_role_may_be_a_hex() -> None:
    with pytest.raises(UnknownColorError, match="#FF0000"):
        compile(
            'title: T\nstyle:\n  roles:\n    brand: "#FF0000"\n  font:\n    color: brand\n'
            "rows:\n  - text: hi\n"
        )


def test_an_ink_is_used_as_set_on_a_dark_canvas() -> None:
    assert _inked_leaves(*_style(roles={"ink": NAVY}, background=DARK)) == [NAVY] * 5


def _render(
    board_yaml: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> BoardRenderResult:
    from dbt_charts.core.board import render_dashboard
    from dbt_charts.core.project import InMemoryBoard

    project = local_project(tmp_path)
    env: ExecEnv = scripted_env(ScriptedBackend([]))
    return render_dashboard(
        board=InMemoryBoard(board_yaml, path=project.path("charts/_t.yml")),
        env=env,
        format="svg",
        project=project,
        result_cache=None,
    )


# --- palettes.fill_category -------------------------------------------------

_SERIES = [
    {"x": month, "series": f"Series {s}", "value": 10 + s}
    for month in ("2024-01", "2024-02")
    for s in range(1, 4)
]
_PATTERNED: dict[str, Any] = {
    "roles": {"ink": NAVY},
    "palettes": {"fill_category": "pattern-mixed-8"},
    "charts": _INKED["charts"],
}
_QUERY: dict[str, Any] = {
    "query": SqlQuery(sql="SELECT 1", source="t"),
    "query_name": "q",
}


def _ctx(fill_category: str | None = "pattern-mixed-8") -> ChartStyleContext:
    palettes = {} if fill_category is None else {"fill_category": fill_category}
    patch = {**_PATTERNED, "palettes": palettes}
    return resolve_style_and_context(
        get_theme_style(), StylePatch.model_validate(patch)
    )[1]


def _bar(**fields: Any) -> BarChart:
    return BarChart(id="t", type="bar", x="x", **_QUERY, **fields)


def _resolve(chart: BarChart | AreaChart | PieChart, ctx: ChartStyleContext) -> Any:
    return resolve(chart, _SERIES, ctx, width=600.0)


@pytest.mark.parametrize(
    "chart",
    [
        _bar(y="value", color="series"),
        AreaChart(id="t", type="area", x="x", y="value", color="series", **_QUERY),
        PieChart(id="t", type="pie", theta="value", color="series", **_QUERY),
    ],
    ids=["stacked bar", "area", "pie"],
)
def test_a_bound_fill_category_patterns_a_chart_by_its_series(
    chart: BarChart | AreaChart | PieChart,
) -> None:
    pattern = _resolve(chart, _ctx()).pattern
    assert pattern is not None
    assert (pattern.field, pattern.scale, pattern.color) == (
        "series",
        "category",
        NAVY,
    )


def test_a_color_field_drawn_on_an_axis_is_never_patterned() -> None:
    """`color: x` on `x:` colors categories, not series."""
    assert _resolve(_bar(y="value", color="x"), _ctx()).pattern is None


def test_an_unbound_fill_category_leaves_a_series_chart_unpatterned() -> None:
    assert _resolve(_bar(y="value", color="series"), _ctx(None)).pattern is None


def test_the_fill_category_palette_draws_the_series_patterns() -> None:
    pattern = _resolve(_bar(y="value", color="series"), _ctx("pattern-one-ink")).pattern
    assert pattern is not None
    assert pattern.ladder == "one-ink" and pattern.motifs is None


@pytest.mark.parametrize("bound", ["vivid-10", "line-one-ink"])
def test_a_fill_category_that_is_not_a_pattern_palette_is_refused(bound: str) -> None:
    with pytest.raises(ValueError, match=r"palettes\.fill_category"):
        _resolve(_bar(y="value", color="series"), _ctx(bound))


def test_an_authored_pattern_defaults_to_the_pattern_category_role() -> None:
    """The fill role patterns series; an authored `pattern:` keeps its own role."""
    pattern = _resolve(
        _bar(y="value", pattern="series"), _ctx("pattern-one-ink")
    ).pattern
    assert pattern is not None and pattern.ladder is None and pattern.motifs is not None


def test_a_single_series_bar_stays_solid_ink() -> None:
    resolved = _resolve(_bar(y="value"), _ctx())
    assert resolved.pattern is None
    assert resolved.style.single_series_fill == NAVY


def test_an_authored_pattern_wins_over_the_series() -> None:
    resolved = _resolve(_bar(y="value", color="series", pattern="series"), _ctx())
    assert resolved.pattern is not None and resolved.pattern.color is None


def test_a_two_measure_bar_draws_two_different_pattern_fills() -> None:
    rows = "".join(f"      - {{x: m{m}, a: {10 + m}, b: {4 + m}}}\n" for m in range(3))
    board = (
        "title: T\nstyle:\n"
        f"  roles:\n    ink: '{NAVY}'\n"
        "  palettes:\n    fill_category: pattern-mixed-8\n"
        "  charts:\n    bar:\n      pattern:\n        color: ink\n"
        f"queries:\n  q:\n    type: values\n    rows:\n{rows}"
        "charts:\n  c:\n    type: bar\n    query: q\n    x: x\n    y: [a, b]\n"
        "rows:\n  - c\n"
    )
    result = render_board_svg_result(board)
    assert not result.chart_errors
    assert isinstance(result.output, str)
    marks = set(re.findall(r'\bfill="url\(#(dbt-pat-[^)"]+)\)"', result.output))
    assert len(marks) == 2, marks


# --- the ink is never changed, in any text it draws -------------------------

_REGIONS = ("East", "West", "North")


def _region_board(
    style: dict[str, Any],
    row_style: dict[str, Any] | None = None,
    *,
    whole_style: bool = False,
) -> str:
    """The stacked-by-region board; `style` merges over the ink theme, or is the
    whole board style when `whole_style`."""
    rows = "".join(
        f"      - {{q: Q{q}, region: {region}, revenue: {10 * q + i}}}\n"
        for q in (1, 2, 3)
        for i, region in enumerate(_REGIONS)
    )
    row = "  - cols:\n      - stacked\n"
    if row_style is not None:
        row += f"    style: {json.dumps(row_style)}\n"
    return (
        "title: T\n"
        f"style: {json.dumps(style if whole_style else {**_PATTERNED, 'font': _INKED['font'], **style})}\n"
        f"queries:\n  q:\n    type: values\n    rows:\n{rows}"
        "charts:\n  stacked:\n    type: bar\n    query: q\n    x: q\n"
        "    y: revenue\n    color: region\n    style:\n      stack: zero\n"
        f"rows:\n{row}"
    )


def _series_name_fills(svg: str) -> set[str]:
    return {
        fill.lower()
        for region in _REGIONS
        for fill in re.findall(
            rf'<text [^>]*data-dbt-series="{region}" fill="(#[0-9a-fA-F]{{6}})"', svg
        )
    }


@pytest.mark.parametrize(
    ("style", "ink"),
    [
        ({"roles": {"ink": NAVY}, "background": CREAM}, NAVY),
        ({"roles": {"ink": NAVY}, "background": DARK}, NAVY),
        ({"roles": {"ink": "#C9D4EA"}, "background": DARK}, "#C9D4EA"),
    ],
    ids=["navy on cream", "navy on dark", "light ink on dark"],
)
def test_series_names_are_the_ink_byte_for_byte(
    style: dict[str, Any], ink: str
) -> None:
    result = render_board_svg_result(_region_board(style))
    assert not result.chart_errors
    assert isinstance(result.output, str)
    assert _series_name_fills(result.output) == {ink.lower()}


_WEAK = {"roles": {"ink": NAVY}, "background": DARK}
_PLAN_ACTUAL = (
    "queries:\n  q:\n    type: values\n    rows:\n"
    "      - {q: Q1, plan: 10, actual: 12}\n      - {q: Q2, plan: 20, actual: 18}\n"
)


def _weak_ink_svg(style: dict[str, Any], charts: str) -> str:
    result = render_board_svg_result(
        f"title: T\nstyle: {json.dumps({**_WEAK, **style})}\n{_PLAN_ACTUAL}"
        f"charts:\n  c:\n{charts}rows:\n  - c\n"
    )
    assert not result.chart_errors
    assert isinstance(result.output, str)
    return result.output


def test_axis_text_in_the_ink_is_the_ink_byte_for_byte() -> None:
    svg = _weak_ink_svg(
        {"charts": {"axis_y": {"color": "ink"}}},
        "    type: line\n    query: q\n    x: q\n    y: plan\n",
    )
    fills = set(re.findall(r'<text [^>]*fill="(#[0-9a-fA-F]{6})"', svg))
    assert NAVY in fills, fills


def test_a_layer_axis_text_in_the_ink_is_the_ink_byte_for_byte() -> None:
    svg = _weak_ink_svg(
        {"charts": {"color": {"categorical": {"palette": [NAVY, NAVY]}}}},
        "    type: bar\n    query: q\n    x: q\n    y: plan\n"
        "    layers:\n      - type: line\n        y: actual\n"
        "        axis_y:\n          position: right\n          color: ink\n",
    )
    fills = set(re.findall(r'<text [^>]*fill="(#[0-9a-fA-F]{6})"', svg))
    assert NAVY in fills, fills


def test_series_header_inks_in_the_ink_are_the_ink_byte_for_byte() -> None:
    svg = _weak_ink_svg(
        {
            "charts": {
                "color": {"categorical": {"palette": [NAVY, NAVY]}},
            }
        },
        "    type: bar\n    query: q\n    x: q\n    y: plan\n"
        "    layers:\n      - type: line\n        y: actual\n"
        "    style:\n      legend:\n        visible: false\n"
        "    support_table:\n      - source: plan\n      - source: actual\n",
    )
    headers = re.findall(
        r'<text [^>]*fill="(#[0-9a-fA-F]{6})"[^>]*>(?:Plan|Actual)<', svg
    )
    assert headers == [NAVY, NAVY]


def test_a_pattern_motif_in_the_ink_is_the_ink_below_the_motif_floor() -> None:
    style = {
        **_WEAK,
        "charts": {
            **_PATTERNED["charts"],
            "bar": {"pattern": {"color": "ink", "ground": DARK}},
        },
    }
    result = render_board_svg_result(_region_board(style))
    assert not result.chart_errors
    assert isinstance(result.output, str)
    tiles = re.findall(r'fill="url\(#(dbt-pat-[^)"]+)\)"', result.output)
    assert tiles and all(NAVY[1:].lower() in tile for tile in tiles), tiles


# --- a weak ink warns wherever a scope draws in it ---------------------------


def _ink_warnings(
    board: str,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> list[str]:
    result = _render(board, tmp_path, local_project)
    return [w.message for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]


def test_a_charts_only_board_warns_once_naming_roles_ink(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = _region_board({"roles": {"ink": NAVY}, "background": DARK})
    result = _render(board, tmp_path, local_project)
    warned = [w for w in result.warnings if w.code == WARN_LOW_TEXT_CONTRAST.code]
    assert len(warned) == 1
    assert "style.roles.ink" in (warned[0].fix or "")
    assert NAVY.lower() in warned[0].message.lower()


def test_a_nested_row_that_changes_the_background_warns_for_its_own_canvas(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = _region_board(
        {"roles": {"ink": NAVY}, "background": CREAM}, row_style={"background": DARK}
    )
    (message,) = _ink_warnings(board, tmp_path, local_project)
    assert DARK.lower() in message.lower()


@pytest.mark.parametrize(
    "style",
    [
        {"roles": {"ink": NAVY}, "background": CREAM},
        {"roles": {"ink": "#C9D4EA"}, "background": DARK},
    ],
    ids=["navy on cream", "light ink on dark"],
)
def test_a_readable_ink_does_not_warn(
    style: dict[str, Any],
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    assert not _ink_warnings(_region_board(style), tmp_path, local_project)


def test_an_ink_nothing_uses_does_not_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = _region_board({"roles": {"ink": NAVY}, "background": DARK})
    unused = board.replace('"color": "ink"', '"color": "#E0E0E0"').replace(
        f'["{NAVY}"]', '["#E0E0E0"]'
    )
    assert not _ink_warnings(unused, tmp_path, local_project)


def test_a_board_with_no_ink_is_unchanged(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = _region_board({"background": DARK})
    plain = (
        board.replace('"color": "ink"', '"color": "#2A2A2A"')
        .replace(f'["{NAVY}"]', '["#2A2A2A"]')
        .replace(f'"roles": {{"ink": "{NAVY}"}}, ', "")
    )
    assert not _ink_warnings(plain, tmp_path, local_project)


def test_a_card_background_is_the_canvas_the_ink_is_checked_against(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    style = {
        "roles": {"ink": NAVY},
        "background": CREAM,
        "charts": {**_PATTERNED["charts"], "background": DARK},
    }
    (message,) = _ink_warnings(_region_board(style), tmp_path, local_project)
    assert DARK.lower() in message.lower()


def test_text_in_a_weak_ink_is_reported_once_naming_style_ink(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = f"""
text: |
  # Heading

  Body line here.
style:
  roles:
    ink: "{NAVY}"
  background: "{DARK}"
  font:
    color: ink
"""
    warned = [
        w
        for w in _render(board, tmp_path, local_project).warnings
        if w.code == WARN_LOW_TEXT_CONTRAST.code
    ]
    assert len(warned) == 1
    assert "style.roles.ink" in (warned[0].fix or "")


@pytest.mark.parametrize("background", [CREAM, DARK], ids=["cream", "dark"])
def test_an_ink_nothing_draws_in_changes_no_text(background: str) -> None:
    """A hex ink role has no effect where no leaf is the ink."""
    base = {
        "background": background,
        "charts": {
            "color": {"categorical": {"palette": ["#B5562B", "#3C7A4E", "#7A1F1F"]}}
        },
    }

    def fills(style: dict[str, Any]) -> list[str]:
        result = render_board_svg_result(_region_board(style, whole_style=True))
        assert isinstance(result.output, str)
        return re.findall(r'<text [^>]*fill="(#[0-9a-fA-F]{6})"', result.output)

    assert fills({**base, "roles": {"ink": NAVY}}) == fills(base)


_DARK_CARD: dict[str, Any] = {
    "roles": {"ink": NAVY},
    "background": CREAM,
    "charts": {
        "background": DARK,
        "color": {"categorical": {"single_series_palette": ["#E0A030"]}},
    },
}


def test_a_dark_card_that_never_draws_in_the_ink_does_not_warn(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    style = {**_DARK_CARD, "title": {"font": {"color": "ink"}}}
    board = _region_board(style, whole_style=True)
    assert not _ink_warnings(board, tmp_path, local_project)


_CARDS = {
    "kpi": "    query: q\n    type: kpi\n    value: revenue\n",
    "callout": "    type: callout\n    message: Heads up.\n",
}


def _card_board(kind: str, style: dict[str, Any]) -> str:
    return (
        f"title: T\nstyle: {json.dumps(style)}\n"
        "queries:\n  q:\n    type: values\n    rows:\n      - {revenue: 4}\n"
        f"charts:\n  c:\n{_CARDS[kind]}rows:\n  - c\n"
    )


@pytest.mark.parametrize("kind", list(_CARDS))
def test_an_ink_reaching_a_kpi_or_callout_card_warns_once(
    kind: str, tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    style = {**_DARK_CARD, "font": {"color": "ink"}}
    (message,) = _ink_warnings(_card_board(kind, style), tmp_path, local_project)
    assert DARK.lower() in message.lower()


@pytest.mark.parametrize("kind", list(_CARDS))
def test_a_kpi_or_callout_card_that_never_draws_in_the_ink_does_not_warn(
    kind: str, tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    style = {**_DARK_CARD, "title": {"font": {"color": "ink"}}}
    assert not _ink_warnings(_card_board(kind, style), tmp_path, local_project)


def test_an_ink_that_reaches_a_dark_card_by_inheritance_warns_once(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    style = {**_DARK_CARD, "font": {"color": "ink"}}
    board = _region_board(style, whole_style=True)
    (message,) = _ink_warnings(board, tmp_path, local_project)
    assert DARK.lower() in message.lower()


@pytest.mark.parametrize(
    ("own_style", "expected"),
    [("", 1), ('      title: {font: {color: "#EEEEEE"}}\n', 0)],
    ids=["inherits the ink", "sets its own color"],
)
def test_a_titled_dark_card_warns_only_when_its_own_title_is_the_ink(
    own_style: str,
    expected: int,
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    style = {**_DARK_CARD, "title": {"font": {"color": "ink"}}}
    board = _region_board(style, whole_style=True).replace(
        "    style:\n      stack: zero\n",
        f"    title: Titled\n    style:\n      stack: zero\n{own_style}",
    )
    assert len(_ink_warnings(board, tmp_path, local_project)) == expected

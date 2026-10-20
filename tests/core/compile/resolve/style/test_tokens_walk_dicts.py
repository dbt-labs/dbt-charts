"""The color-token walk must reach color-bearing dicts, not just models and lists.

`_resolve_color_tokens` recursed into BaseModel fields and list elements only.
Its docstring said why — "no color-bearing dicts exist on Style today; if one is
added it needs to be handled here explicitly" — and one was added since:
`TableChartStyle.columns` is `dict[str, TableColumnConfig]`, keyed by column
name, so every per-column color sat behind a dict the walk stopped at.

The raw token address then reached the render layer, which did three different
things with it depending on the slot — dropped it (background, font.color),
passed it through into the SVG (spark.color), or failed to parse it
(glyph_color). The first is why this survived: it leaves no trace to grep for,
so the assertions here compare against a render with no color authored at all.
"""

from __future__ import annotations

import re

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.core.project import InMemoryBoard

# `dbt-grays.border` and `negative.text` are dotted tokens; `category[1]` is
# a bracket role token. All three go through the same walk.
_TOKENS = {
    "background": "dbt-grays.border",
    "font_color": "negative.text",
    "spark_color": "category[1]",
    "glyph_color": "warning.solid",
}
_HEXES = {
    "background": "#DFE1E5",
    "font_color": "#4b000a",
    "glyph_color": "#e69812",
}


def _board(column_style: str) -> str:
    return f"""
title: Column tokens
queries:
  q:
    columns: [k, v, trend]
    values:
      - ["a", 5, [1, 2, 3]]
      - ["b", 40, [3, 2, 1]]
charts:
  t:
    query: q
    type: table
    style:
      columns:
        v:
{column_style}
rows: [t]
"""


def _render(tmp_path, column_style: str) -> str:
    (tmp_path / "dbt_charts.yml").write_text("name: t\n")
    session = ProjectSession.open(tmp_path, read_only=False)
    try:
        result = session.render_board(
            board=InMemoryBoard(_board(column_style), path=None), format="svg"
        )
        assert not result.chart_errors, result.chart_errors
        assert result.status == "ok", result.status
        return result.data or ""
    finally:
        session.close()


def test_a_column_background_token_paints_its_color(tmp_path) -> None:
    svg = _render(tmp_path, f"          background: {_TOKENS['background']}")
    assert _HEXES["background"] in svg
    assert _TOKENS["background"] not in svg


def test_a_column_font_color_token_paints_its_color(tmp_path) -> None:
    svg = _render(
        tmp_path, f"          font:\n            color: {_TOKENS['font_color']}"
    )
    assert _HEXES["font_color"] in svg
    assert _TOKENS["font_color"] not in svg


def test_a_column_spark_color_token_does_not_leak_into_the_svg(tmp_path) -> None:
    svg = _render(
        tmp_path,
        "          spark:\n            type: line\n"
        f"            color: {_TOKENS['spark_color']}",
    )
    # A role token's value is the theme's to choose, so assert the invariant
    # rather than a hex: the address is gone and a real color is painted in its
    # place. Before the fix the SVG carried `stroke="category[1]"`, which a
    # browser silently drops.
    assert _TOKENS["spark_color"] not in svg
    assert re.search(r'stroke="#[0-9a-fA-F]{6}"', svg)


def test_a_column_glyph_color_token_resolves_instead_of_raising(tmp_path) -> None:
    svg = _render(
        tmp_path,
        f'          glyph: "!"\n          glyph_color: {_TOKENS["glyph_color"]}',
    )
    assert _HEXES["glyph_color"] in svg


def test_the_walk_leaves_the_resolver_s_own_lookup_tables_alone() -> None:
    """`Style.roles` maps a role name to a token *address*, not to a color.

    The first cut of the dict branch walked it, so `roles["ink"]`
    ("chrome.heading") was resolved to hex and the indirection table stopped
    pointing anywhere — every later lookup through it would have failed. The
    entries are addresses precisely because they are meant to be followed, not
    evaluated in place.
    """
    from dbt_charts.core.compile.config import get_theme_style

    theme = get_theme_style()
    for role, target in (theme.roles or {}).items():
        assert not target.startswith("#"), (
            f"roles[{role!r}] was resolved to {target!r}; the table must keep "
            "the address so lookups through it still work"
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("label", "U.S", id="label-with-a-dot"),
        pytest.param("label", "v1.2", id="label-that-looks-versioned"),
        pytest.param("link", "orders.html", id="relative-link"),
        pytest.param("header_link", "example.com", id="bare-host-link"),
    ],
)
def test_ordinary_column_text_is_not_read_as_a_color_token(
    tmp_path, field: str, value: str
) -> None:
    """A column's free text and URLs match the token regex as readily as a color.

    `U.S`, `v1.2`, `orders.html` are all `word.word`. The first cut of the dict
    branch handed them to the token resolver, so an everyday board became
    `ERR-INTERNAL: theme has no palette assigned to role 'U'` — a compile break
    on boards that rendered fine before, which no fixture in the repo would have
    caught (nothing in `examples/` authors a dotted label).
    """
    svg = _render(tmp_path, f'          {field}: "{value}"')
    assert svg


def test_a_spark_threshold_color_resolves(tmp_path) -> None:
    """`SparkConfig.thresholds` is `dict[int | float, str]` — the *values* are colors.

    A first cut of the dict branch filtered on the dict key against the
    color-field names, which is unconditionally false for a numeric key, so
    every threshold color passed through unresolved and reached the SVG as
    `fill="negative.text"` — the exact leak this task exists to close, one field
    away, and silent: `status` is `ok` and `chart_errors` is empty because the
    browser is what drops the invalid paint.
    """
    svg = _render(
        tmp_path,
        "          spark:\n            type: bar\n"
        f"            thresholds: {{0: {_TOKENS['font_color']}}}",
    )
    assert _TOKENS["font_color"] not in svg
    assert _HEXES["font_color"] in svg

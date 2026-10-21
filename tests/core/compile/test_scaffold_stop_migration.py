"""Migration coverage for the scaffold role names retired at 0.11.0
(`versions/v0_11_0.py`): `dbt-grays.muted`, `dbt-creams.border`,
`chrome.heading` and every other job name respell to the numbered stop that
holds the same color. Identity-path `Move`s with a value map, like the 0.9.0
token respell -- a retired name is still a valid string, so the current
schema never rejects it.

A numbered address (`dbt-grays.8`, `chrome[8]`) already named the step at
that index, so it is never touched.
"""

from __future__ import annotations

import copy
import textwrap
from typing import Any

import pytest

from dbt_charts.core.compile.migrations import prepare_board_mapping
from dbt_charts.core.compile.migrations.migrations import migrate_yaml_text
from dbt_charts.core.compile.migrations.versions.v0_11_0 import (
    RETIRED_SCAFFOLD_TOKEN_MAP,
)
from dbt_charts.core.compile.resolve.style.palette import color, color_from_theme

# The scaffold as it was authored before 0.10.0: role -> (palette, old slot).
OLD_GRAYS = {
    "canvas": "#FAFAFA",
    "surface-subtle": "#F7F8FA",
    "grid": "#EDEFF2",
    "border": "#DFE1E5",
    "grid-emphasis": "#D0D3D9",
    "disabled": "#B0B2B8",
    "subtitle": "#8A8C8F",
    "muted": "#626366",
    "inactive": "#494A4C",
    "heading": "#313233",
    "ink": "#222222",
    "void": "#161616",
}
OLD_CREAMS = {
    "canvas": "#FAF7F0",
    "surface-subtle": "#F5F2EB",
    "grid": "#E9E1D2",
    "border": "#DDD3C0",
    "grid-emphasis": "#CEC1AB",
    "disabled": "#B2A691",
    "subtitle": "#918878",
    "muted": "#6F685D",
    "inactive": "#56504A",
    "heading": "#403B37",
    "ink": "#2A2725",
    "void": "#1A1714",
}
_QUERIES = {"q": {"columns": ["a", "b"], "values": [[1, 2]]}}


def _board(**style: Any) -> dict[str, Any]:
    return {
        "title": "t",
        "style": style,
        "charts": {"c": {"type": "bar", "query": "q", "x": "a", "y": "b"}},
        "queries": _QUERIES,
        "rows": ["c"],
    }


@pytest.mark.parametrize("role", OLD_GRAYS)
def test_every_retired_name_resolves_to_its_old_color_after_migration(
    role: str,
) -> None:
    mapping = _board(
        background=f"dbt-grays.{role}",
        font={"color": f"dbt-creams.{role}"},
        border={"color": f"chrome.{role}"},
    )

    style = prepare_board_mapping(mapping)["style"]

    assert color(style["background"]).upper() == OLD_GRAYS[role]
    assert color(style["font"]["color"]).upper() == OLD_CREAMS[role]
    assert (
        color_from_theme(style["border"]["color"], palettes={"chrome": "dbt-grays"})
        == OLD_GRAYS[role]
    )


def test_the_named_dotted_and_bracket_forms_land_on_numbered_stops() -> None:
    style = prepare_board_mapping(
        _board(
            background="dbt-grays.muted",
            font={"color": "chrome.ink"},
            border={"color": "dbt-creams.canvas"},
        )
    )["style"]

    assert style["background"] == "dbt-grays.8"
    assert style["font"]["color"] == "chrome[11]"
    assert style["border"]["color"] == "dbt-creams.1"


def test_a_chart_level_token_and_a_single_series_list_item_migrate() -> None:
    mapping = _board(
        charts={"color": {"categorical": {"single_series_palette": ["dbt-grays.ink"]}}}
    )
    mapping["charts"]["c"]["style"] = {"color": {"static": "chrome.muted"}}

    result = prepare_board_mapping(mapping)

    assert result["charts"]["c"]["style"]["color"]["static"] == "chrome[8]"
    series = result["style"]["charts"]["color"]["categorical"]["single_series_palette"]
    assert series == ["dbt-grays.11"]


def test_white_and_black_are_not_retired() -> None:
    assert "dbt-grays.white" not in RETIRED_SCAFFOLD_TOKEN_MAP
    assert "chrome.black" not in RETIRED_SCAFFOLD_TOKEN_MAP
    mapping = _board(background="dbt-grays.white", font={"color": "chrome.black"})

    assert prepare_board_mapping(mapping)["style"] == {
        "background": "dbt-grays.white",
        "font": {"color": "chrome.black"},
    }


def test_the_text_rewriter_respells_a_retired_name_on_disk() -> None:
    from dbt_charts.core.compile.migrations.migrations import _board_migration_context

    catalog, registry = _board_migration_context()
    text = textwrap.dedent(
        """\
        title: t
        style:
          background: dbt-grays.muted
        queries:
          q:
            columns: [a, b]
            values: [[1, 2]]
        rows:
          - text: hello
        """
    )

    migrated = migrate_yaml_text(
        text, catalog=catalog, registry=registry, stop_target=catalog.dev.version
    )

    assert "dbt-grays.8" in migrated
    assert "muted" not in migrated


def test_a_numbered_address_is_never_touched() -> None:
    mapping = _board(
        background="dbt-grays.8",
        border={"color": "chrome[8]"},
        font={"color": "dbt-creams.12"},
    )

    assert prepare_board_mapping(mapping)["style"] == {
        "background": "dbt-grays.8",
        "border": {"color": "chrome[8]"},
        "font": {"color": "dbt-creams.12"},
    }


def test_a_category_color_pin_migrates_and_compiles() -> None:
    mapping = _board(
        charts={
            "category_colors": {
                "region": {"values": {"East": "dbt-grays.muted", "West": "dbt-grays.4"}}
            }
        }
    )

    pins = prepare_board_mapping(mapping)["style"]["charts"]["category_colors"]

    assert pins["region"]["values"] == {"East": "dbt-grays.8", "West": "dbt-grays.4"}


def test_a_nested_boards_own_charts_migrate() -> None:
    """A scaffold token on a chart inside a sub-board's own `charts:` map."""
    inner = _board()
    inner["charts"]["c"]["style"] = {"color": {"static": "chrome.muted"}}
    inner["style"] = {
        "background": "dbt-grays.muted",
        "font": {"color": "dbt-grays.muted"},
    }
    mapping = _board()
    mapping["rows"] = [inner]

    result = prepare_board_mapping(mapping)["rows"][0]

    assert result["charts"]["c"]["style"]["color"]["static"] == "chrome[8]"
    assert result["style"] == {
        "background": "dbt-grays.8",
        "font": {"color": "dbt-grays.8"},
    }


def test_a_board_written_for_the_old_ladder_compiles() -> None:
    """Pinned at `compile()`: names on fields and a category pin migrate."""
    from dbt_charts.core.compile.compiler import compile as compile_board

    yaml_text = textwrap.dedent(
        """\
        queries:
          q1:
            type: values
            columns: [month, revenue]
            values:
              - [a, 3]
        style:
          background: dbt-grays.canvas
          font:
            color: dbt-grays.muted
          charts:
            category_colors:
              month:
                values:
                  a: dbt-grays.muted
        charts:
          c1:
            type: bar
            query: q1
            x: month
            y: revenue
        rows: [c1]
        """
    )

    result = compile_board(yaml_text)

    assert result.success, result.errors
    assert result.board is not None
    style = result.board.resolved_style
    assert style.background == OLD_GRAYS["canvas"]
    assert style.font.color == OLD_GRAYS["muted"]


def test_a_retired_name_in_a_role_shortcut_migrates_and_is_a_fixed_point() -> None:
    """`style.roles` values are board grammar and respell like any token."""
    mapping = _board(
        background="dbt-grays.muted",
        roles={"ink": "chrome.heading", "pin": "chrome.muted"},
    )

    once = prepare_board_mapping(copy.deepcopy(mapping))
    twice = prepare_board_mapping(copy.deepcopy(once))

    assert once["style"]["roles"] == {"ink": "chrome[10]", "pin": "chrome[8]"}
    assert once["style"]["background"] == "dbt-grays.8"
    assert once == twice


def test_a_migrated_role_shortcut_compiles_and_resolves_for_the_design_panel() -> None:
    """The Cloud design panel resolves each role value through `color_from_theme`."""
    from dbt_charts.core.compile.compiler import compile as compile_board

    result = compile_board(_ROW_DIVIDER % "style:\n  roles:\n    ink: chrome.heading")
    assert result.success, result.errors
    roles = prepare_board_mapping(
        {"title": "t", "style": {"roles": {"ink": "chrome.heading"}}}
    )["style"]["roles"]
    resolved = color_from_theme(
        roles["ink"], palettes={"chrome": "dbt-grays"}, roles=roles
    )
    assert resolved == OLD_GRAYS["heading"]


def test_a_scaffold_bound_to_another_role_is_not_respelled() -> None:
    mapping = _board(palettes={"surface": "dbt-creams"}, background="surface.canvas")

    assert prepare_board_mapping(mapping)["style"]["background"] == "surface.canvas"


_ROW_DIVIDER = """\
%s
queries:
  q1:
    type: values
    columns: [month, revenue]
    values:
      - [a, 3]
charts:
  c1:
    type: table
    query: q1
rows: [c1]
"""


def _compiled(style_yaml: str) -> Any:
    from dbt_charts.core.compile.compiler import compile as compile_board

    result = compile_board(_ROW_DIVIDER % style_yaml.rstrip())
    assert result.success, result.errors
    assert result.board is not None
    return result.board


def test_a_retired_name_on_a_table_row_divider_compiles() -> None:
    board = _compiled(
        "style:\n  charts:\n    table:\n      row:\n        divider:\n"
        "          color: dbt-grays.muted\n"
    )
    assert board.chart_style_context.table.row.divider.color == OLD_GRAYS["muted"]


def test_a_retired_name_on_a_text_divider_compiles() -> None:
    board = _compiled("style:\n  text:\n    divider:\n      color: dbt-grays.border\n")
    assert board.resolved_style.text.divider.color == OLD_GRAYS["border"]


def test_a_retired_name_on_a_heading_font_compiles() -> None:
    board = _compiled(
        "title: T\nstyle:\n  fonts:\n    heading:\n      color: dbt-grays.border\n"
    )
    assert board.resolved_style.title.font.color == OLD_GRAYS["border"]


def test_a_090_board_chains_through_the_010_rename_into_the_respell() -> None:
    """`row.rule.color` became `row.divider.color` at 0.10.0; the name on it
    respells at 0.11.0 in the same pass."""
    board = _compiled(
        "style:\n  charts:\n    table:\n      row:\n        rule:\n"
        "          color: dbt-grays.muted\n          width: 1\n"
    )
    assert board.chart_style_context.table.row.divider.color == OLD_GRAYS["muted"]


def test_a_retired_name_on_a_nested_boards_font_compiles() -> None:
    """A nested sub-board's `style.font.color`, respelled once at compile()."""
    from dbt_charts.core.compile.compiler import compile as compile_board

    result = compile_board(
        textwrap.dedent(
            """\
            queries:
              q1:
                type: values
                columns: [a]
                values: [[1]]
            charts:
              c1:
                type: table
                query: q1
            rows:
              - style:
                  font:
                    color: dbt-grays.muted
                rows: [c1]
            """
        )
    )

    assert result.success, result.errors
    prepared = prepare_board_mapping(
        {"title": "t", "style": {"font": {"color": "dbt-grays.muted"}}}
    )
    assert prepare_board_mapping(copy.deepcopy(prepared)) == prepared

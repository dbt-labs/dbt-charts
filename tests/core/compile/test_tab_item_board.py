"""`tabs` maps each tab's label to a nested board."""

from __future__ import annotations

import warnings

from dbt_charts.core.compile.compiler import compile
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
)
from dbt_charts.core.compile.migrations.migrations import package_schemas
from dbt_charts.core.compile.parse.parser import load_yaml_mapping

from .._board_utils import apply_static_layout


def test_the_key_labels_the_tab_and_title_heads_the_pane() -> None:
    result = compile(
        """
tabs:
  Overview:
    title: Revenue this quarter
    text: hi
""",
        file="f.yaml",
    )

    assert not result.errors
    assert result.board is not None
    assert result.board.layout.tab_titles == ["Overview"]
    pane = result.board.layout.items[0].board
    assert pane is not None
    assert pane.title == "Revenue this quarter"


def test_a_label_is_any_text_in_authored_order() -> None:
    result = compile(
        """
tabs:
  "📈 Trends":
    text: a
  Q1.5 / Q2:
    text: b
""",
        file="f.yaml",
    )

    assert not result.errors
    assert result.board is not None
    assert result.board.layout.tab_titles == ["📈 Trends", "Q1.5 / Q2"]


def test_a_tab_declares_its_own_queries_and_charts() -> None:
    result = compile(
        """
tabs:
  Detail:
    queries:
      q:
        type: values
        columns: [a]
        values:
          - [1]
    charts:
      c:
        type: bar
        query: q
        x: a
        y: a
    rows:
      - c
""",
        file="f.yaml",
    )

    assert not result.errors
    assert result.board is not None
    assert result.board.layout.tab_titles == ["Detail"]


def test_a_pane_takes_its_authored_width_and_height() -> None:
    result = compile(
        """
queries:
  q:
    type: values
    columns: [a]
    values:
      - [1]
charts:
  c:
    type: bar
    query: q
    x: a
    y: a
tabs:
  A:
    width: 300
    height: 120
    rows:
      - c
  B:
    text: there
""",
        file="f.yaml",
    )

    assert not result.errors
    assert result.board is not None
    board = apply_static_layout(result.board)
    pane, other = board.layout.items
    assert pane.width == 300
    assert pane.height == 120
    assert other.width > 300


def test_an_empty_tab_is_refused_like_an_empty_board() -> None:
    result = compile("tabs:\n  A: {}\n", file="f.yaml")

    assert any("at least one layout type" in e.message for e in result.errors)


def test_a_retired_tab_list_becomes_the_mapping() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        result = compile(
            """
tabs:
  items:
    - title: Overview
      icon: x
      text: one
    - title: Detail
      text: two
""",
            file="f.yaml",
        )

    assert not result.errors
    assert result.board is not None
    assert result.board.layout.tab_titles == ["Overview", "Detail"]
    pane = result.board.layout.items[0].board
    assert pane is not None
    assert not pane.title


def test_text_rewrite_turns_the_tab_list_into_the_tabs_mapping() -> None:
    out = migrate_yaml_text(
        "tabs:\n  items:\n    - title: Overview\n      text: hi\n",
        stop_target=package_schemas().dev.version,
    )

    assert load_yaml_mapping(out)["tabs"] == {"Overview": {"text": "hi"}}


def test_text_rewrite_converts_a_compact_tab_list_in_a_row() -> None:
    out = migrate_yaml_text(
        "rows:\n  - tabs:\n      items:\n      - title: A\n        icon: x\n        text: a\n"
        "      - title: B\n        text: b\n  - text: after\n",
        stop_target=package_schemas().dev.version,
    )

    assert load_yaml_mapping(out)["rows"] == [
        {"tabs": {"A": {"text": "a"}, "B": {"text": "b"}}},
        {"text": "after"},
    ]


def test_a_pane_path_quotes_a_label_with_dots() -> None:
    result = compile(
        "tabs:\n  Q1.5 / Q2:\n    text: a\n  Plain:\n    text: b\n",
        file="f.yaml",
    )

    assert result.board is not None
    paths = [item.source_path for item in result.board.layout.items]
    assert paths == ['tabs["Q1.5 / Q2"]', "tabs.Plain"]


def test_text_rewrite_moves_position_to_style_and_drops_id_and_default() -> None:
    out = migrate_yaml_text(
        "tabs:\n  id: view\n  default: B\n  position: left\n  items:\n"
        "    - title: A\n      text: a\n    - title: B\n      text: b\n",
        stop_target=package_schemas().dev.version,
    )

    board = load_yaml_mapping(out)
    assert board["tabs"] == {"A": {"text": "a"}, "B": {"text": "b"}}
    assert board["style"] == {"layout": {"tabs": {"position": "left"}}}

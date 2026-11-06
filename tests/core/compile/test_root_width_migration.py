"""``style.frame.width`` leaves the grammar; a board's own width is root ``width:``.

The 0.11.0 boundary moves a root board's ``style.frame.width`` to root
``width:`` and strikes it from every inline nested board, where it never had an
effect (a nested board's ``width:`` is placement in its parent, and its frame
is ignored). Each claim is pinned at ``compile()``, where the migration, the
live schema and the sizing pass meet.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile import compile as compile_board
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.migrations import (
    SchemaMigrationWarning,
    migrate_yaml_text,
    prepare_board_mapping,
)
from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.parse.parser import load_yaml_mapping
from dbt_charts.core.compile.sizing import board_container_width

_BODY = """
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  c1: {query: q, type: bar, x: k, y: v}
  c2: {query: q, type: bar, x: k, y: v}
cols:
  - c1
  - c2
"""


def _width(result: Any) -> float:
    assert result.success, result.errors
    return board_container_width(result.board, result.board.variable_defaults)


def test_frame_width_is_not_a_live_field() -> None:
    with pytest.raises(ValidationError):
        AuthoredBoard.model_validate(
            {"style": {"frame": {"width": 900}}, "text": "hello"}
        )


def test_root_width_beats_max_width_exactly() -> None:
    result = compile_board(f"title: T\nwidth: 1800\n{_BODY}")
    assert _width(result) == 1800.0
    assert result.board.authored_width == 1800.0


def test_unset_width_hugs_up_to_max_width() -> None:
    result = compile_board(f"title: T\n{_BODY}")
    assert result.board.authored_width is None
    assert _width(result) == float(result.board.resolved_style.frame.max_width)


@pytest.mark.parametrize(
    ("authored", "pixels"),
    [("900", 900.0), ("'900px'", 900.0), ("1440", 1440.0)],
)
def test_root_width_accepts_pixels(authored: str, pixels: float) -> None:
    assert _width(compile_board(f"title: T\nwidth: {authored}\n{_BODY}")) == pixels


@pytest.mark.parametrize(
    ("authored", "message"),
    [
        ("'50%'", "cannot be a percentage"),
        ("wide", "not a valid dimension"),
        ("0", "must be positive"),
    ],
)
def test_root_width_rejects_what_it_cannot_bind(authored: str, message: str) -> None:
    result = compile_board(f"title: T\nwidth: {authored}\n{_BODY}")
    assert not result.success
    (error,) = result.errors
    assert error.code == "ERR-VALIDATION-FIELD"
    assert message in error.message


def test_authored_width_survives_a_theme_change() -> None:
    result = compile_board(f"title: T\nwidth: 900\n{_BODY}")
    result.board.set_theme("paper")
    assert _width(result) == 900.0


def test_a_nested_boards_width_is_placement_not_a_board_width() -> None:
    result = compile_board(
        "title: Root\ncols:\n  - title: Left\n    width: '30%'\n    text: left\n"
        "  - title: Right\n    text: right\n"
    )
    assert result.success, result.errors
    assert result.board.authored_width is None
    assert result.board.layout.items[0].board.authored_width is None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _quiet_prepare(raw: dict[str, Any]) -> dict[str, Any]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SchemaMigrationWarning)
        return prepare_board_mapping(raw)


def test_root_frame_width_becomes_root_width_and_keeps_its_siblings() -> None:
    migrated = _quiet_prepare(
        {
            "style": {"frame": {"width": 1440, "margin": 12}},
            "text": "hello",
        }
    )
    assert migrated == {
        "width": 1440,
        "style": {"frame": {"margin": 12}},
        "text": "hello",
    }
    AuthoredBoard.model_validate(migrated)


def test_a_frame_holding_only_width_leaves_no_empty_style() -> None:
    migrated = _quiet_prepare({"style": {"frame": {"width": 1440}}, "text": "hello"})
    assert migrated == {"width": 1440, "text": "hello"}


def test_root_frame_width_compiles_to_the_same_board_as_root_width() -> None:
    old = compile_board(f"title: T\nstyle:\n  frame:\n    width: 1800\n{_BODY}")
    new = compile_board(f"title: T\nwidth: 1800\n{_BODY}")
    assert _width(old) == _width(new) == 1800.0
    assert old.board.authored_width == 1800.0


def test_a_negative_frame_width_still_fails_at_compile() -> None:
    result = compile_board(f"title: T\nstyle:\n  frame:\n    width: -200\n{_BODY}")
    assert not result.success


def test_both_spellings_on_the_root_still_fail_loudly() -> None:
    result = compile_board(
        f"title: T\nwidth: 900\nstyle:\n  frame:\n    width: 800\n{_BODY}"
    )
    assert not result.success
    assert [e.code for e in result.errors] == ["ERR-RETIRED-FIELD"]


_NESTED = """
title: T
queries:
  q:
    type: values
    rows:
      - {k: a, v: 1}
charts:
  c1: {query: q, type: bar, x: k, y: v}
rows:
  - title: Inner
    %s
    rows:
      - c1
"""


def test_inline_nested_frame_width_is_struck_not_moved() -> None:
    """It never affected the nested board; moving it to `width:` would place it."""
    raw = load_yaml_mapping(_NESTED % "style: {frame: {width: 400}}")
    inner = _quiet_prepare(raw)["rows"][0]
    assert "style" not in inner
    assert "width" not in inner


def test_inline_nested_frame_width_keeps_its_siblings() -> None:
    raw = load_yaml_mapping(_NESTED % "style: {frame: {width: 400, margin: 8}}")
    inner = _quiet_prepare(raw)["rows"][0]
    assert inner["style"] == {"frame": {"margin": 8}}


def test_inline_nested_frame_width_compiles_exactly_as_if_never_authored() -> None:
    with_width = compile_board(_NESTED % "style: {frame: {width: 400}}")
    without = compile_board(_NESTED % "")
    assert with_width.success, with_width.errors
    assert _width(with_width) == _width(without)
    assert with_width.board.authored_width is None


def test_inline_nested_frame_width_beside_placement_width_keeps_the_placement() -> None:
    raw = load_yaml_mapping(_NESTED % "width: '50%'\n    style: {frame: {width: 400}}")
    inner = _quiet_prepare(raw)["rows"][0]
    assert inner == {"title": "Inner", "width": "50%", "rows": ["c1"]}


def test_a_root_and_a_nested_frame_width_each_take_their_own_path() -> None:
    raw = load_yaml_mapping(
        "style: {frame: {width: 1500}}\n"
        + (_NESTED % "style: {frame: {width: 400}}").split("title: T\n", 1)[1]
    )
    migrated = _quiet_prepare(raw)
    assert migrated["width"] == 1500
    assert "style" not in migrated
    assert "style" not in migrated["rows"][0]


def test_text_rewrite_moves_the_root_key_in_place() -> None:
    text = (
        "title: T\n"
        "style:\n"
        "  # keep\n"
        "  frame:\n"
        "    width: 1440\n"
        "    margin: 12\n"
        "text: hello\n"
    )
    migrated = migrate_yaml_text(text)
    doc = load_yaml_mapping(migrated)
    assert doc["width"] == 1440
    assert doc["style"] == {"frame": {"margin": 12}}
    assert "# keep" in migrated
    AuthoredBoard.model_validate(doc)


def test_text_rewrite_leaves_no_empty_style_when_frame_held_only_width() -> None:
    text = "title: T\nstyle:\n  frame:\n    width: 1440\ntext: hello\n"
    migrated = migrate_yaml_text(text)
    doc = load_yaml_mapping(migrated)
    assert doc["width"] == 1440
    assert "style" not in doc
    AuthoredBoard.model_validate(doc)


def test_text_rewrite_strikes_a_nested_key() -> None:
    text = _NESTED % "style:\n      frame:\n        width: 400"
    migrated = migrate_yaml_text(text)
    doc = load_yaml_mapping(migrated)
    assert "style" not in doc["rows"][0]
    AuthoredBoard.model_validate(doc)


# ---------------------------------------------------------------------------
# Other files that author the key: meta.yml, extends, imported boards
# ---------------------------------------------------------------------------


def _compile_in_project(
    in_memory_project: Callable, slug: str, files: dict[str, str]
) -> Any:
    project = in_memory_project(Path(f"/tmp/test-root-width-{slug}"), files)
    return compile_file(project.path("charts/board.yml").read_board())


def test_meta_yml_frame_width_still_binds_the_root(
    in_memory_project: Callable,
) -> None:
    result = _compile_in_project(
        in_memory_project,
        "meta",
        {
            "charts/meta.yml": "style:\n  frame:\n    width: 1100\n",
            "charts/board.yml": f"title: T\n{_BODY}",
        },
    )
    assert _width(result) == 1100.0


def test_meta_yml_root_width_binds_the_root(in_memory_project: Callable) -> None:
    result = _compile_in_project(
        in_memory_project,
        "meta-root",
        {
            "charts/meta.yml": "width: 1100\n",
            "charts/board.yml": f"title: T\n{_BODY}",
        },
    )
    assert _width(result) == 1100.0


def test_a_boards_own_width_beats_the_meta_width(in_memory_project: Callable) -> None:
    result = _compile_in_project(
        in_memory_project,
        "meta-own",
        {
            "charts/meta.yml": "style:\n  frame:\n    width: 1100\n",
            "charts/board.yml": f"title: T\nwidth: 1500\n{_BODY}",
        },
    )
    assert _width(result) == 1500.0


def test_extends_template_frame_width_still_binds(in_memory_project: Callable) -> None:
    result = _compile_in_project(
        in_memory_project,
        "extends",
        {
            "charts/_wide.yml": "style:\n  frame:\n    width: 1800\n",
            "charts/board.yml": f"title: T\nextends: ./_wide.yml\n{_BODY}",
        },
    )
    assert _width(result) == 1800.0


_IMPORTER = """
title: T
rows:
  - ./inner.yml
"""

_INNER = """
title: Inner
%s
text: hello
"""


def test_an_imported_files_frame_width_does_not_bind_the_importer(
    in_memory_project: Callable,
) -> None:
    """An imported board's own width is unused when nested, before and after."""
    with_width = _compile_in_project(
        in_memory_project,
        "import-with",
        {
            "charts/board.yml": _IMPORTER,
            "charts/inner.yml": _INNER % "style:\n  frame:\n    width: 300",
        },
    )
    without = _compile_in_project(
        in_memory_project,
        "import-without",
        {"charts/board.yml": _IMPORTER, "charts/inner.yml": _INNER % ""},
    )
    assert _width(with_width) == _width(without)
    assert with_width.board.authored_width is None


def test_an_imported_files_root_width_is_not_a_placement_width(
    in_memory_project: Callable,
) -> None:
    result = _compile_in_project(
        in_memory_project,
        "import-root-width",
        {
            "charts/board.yml": _IMPORTER,
            "charts/inner.yml": _INNER % "width: 300",
        },
    )
    without = _compile_in_project(
        in_memory_project,
        "import-root-width-without",
        {"charts/board.yml": _IMPORTER, "charts/inner.yml": _INNER % ""},
    )
    assert _width(result) == _width(without)
    assert result.board.authored_width is None

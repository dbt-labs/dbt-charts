"""``style.text.heading.margin_top`` / ``margin_bottom`` changed meaning in place:
an author who set them gets a warning, a board that leaves them to the theme
does not."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.diagnostics import Diagnostic

_CODE = "WARN-HEADING-MARGIN-REDEFINED"

_BOARD = """
title: Board
{style}
text: Body.
"""


def _warnings(style: str) -> list[Diagnostic]:
    result = compile(_BOARD.format(style=style))
    assert result.success, result.errors
    return [w for w in result.warnings if w.code == _CODE]


@pytest.mark.parametrize("key", ["margin_top", "margin_bottom"])
def test_an_authored_heading_margin_warns_at_its_path(key: str) -> None:
    (warning,) = _warnings(f"style:\n  text:\n    heading:\n      {key}: 0.0")

    assert warning.path == f"style.text.heading.{key}"


def test_both_authored_margins_warn_once_each() -> None:
    style = (
        "style:\n  text:\n    heading:\n      margin_top: 1.0\n      margin_bottom: 1.0"
    )

    assert len(_warnings(style)) == 2


def test_a_nested_board_style_warns_too() -> None:
    board = """
title: Board
rows:
  - title: Section
    style:
      text:
        heading:
          margin_top: 1.0
    text: Body.
"""
    result = compile(board)
    assert result.success, result.errors
    assert [w.code for w in result.warnings].count(_CODE) == 1


def test_theme_defaults_do_not_warn() -> None:
    assert _warnings("") == []
    assert _warnings("style:\n  text:\n    align: left") == []


from collections.abc import Callable  # noqa: E402
from pathlib import Path  # noqa: E402

from dbt_charts.cli.filesystem_project import FilesystemProject  # noqa: E402
from dbt_charts.core.compile.compiler import compile_file  # noqa: E402

_MARGIN = "style:\n  text:\n    heading:\n      margin_bottom: 0.0\n"


def _file_warnings(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    board: str,
    *,
    meta: str | None = None,
    template: str | None = None,
) -> list[Diagnostic]:
    (tmp_path / "dbt_charts.yml").write_text("")
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "board.yml").write_text(board)
    if meta is not None:
        (tmp_path / "charts" / "meta.yml").write_text(meta)
    if template is not None:
        (tmp_path / "charts" / "template.yml").write_text(template)
    result = compile_file(local_project(tmp_path).path("charts/board.yml").read_board())
    assert result.success, result.errors
    return [w for w in result.warnings if w.code == _CODE]


def test_a_margin_in_meta_yml_warns_naming_the_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (warning,) = _file_warnings(
        tmp_path, local_project, "title: Board\ntext: Body.\n", meta=_MARGIN
    )

    assert "charts/meta.yml" in warning.message


def test_a_margin_in_an_extends_template_warns_naming_the_file(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    (warning,) = _file_warnings(
        tmp_path,
        local_project,
        "extends: ./template.yml\ntitle: Board\ntext: Body.\n",
        template="title: Template\n" + _MARGIN,
    )

    assert "charts/template.yml" in warning.message


def test_a_board_authoring_the_margin_itself_warns_once_even_with_meta(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    warnings = _file_warnings(
        tmp_path,
        local_project,
        "title: Board\ntext: Body.\n" + _MARGIN,
        meta=_MARGIN,
    )

    assert [w.path for w in warnings].count("style.text.heading.margin_bottom") == 1
    assert sum("charts/meta.yml" in w.message for w in warnings) == 1

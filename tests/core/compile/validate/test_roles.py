"""ERR-COLOR-TOKEN: a `style.roles` address that resolves to no color fails
compile() instead of surfacing later as a failed swatch or render error."""

from __future__ import annotations

from pathlib import Path

import pytest

from dbt_charts.agent_api.validate import ValidateResult, validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile as compile_board

_BOARD = """
title: T
{style}
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
charts:
  c:
    query: q
    type: bar
    x: a
    y: b
rows:
  - c
"""


def _compile(style: str) -> CompileResult:
    return compile_board(_BOARD.format(style=style))


def test_a_role_address_that_resolves_compiles() -> None:
    result = _compile("style:\n  roles:\n    accent: chrome[3]")

    assert result.success, result.errors


@pytest.mark.parametrize("address", ["chrome.nonsense", "nopalette[2]", "chrome[99]"])
def test_a_role_address_that_resolves_to_nothing_is_an_error(address: str) -> None:
    result = _compile(f"style:\n  roles:\n    ink: {address}")

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"
    assert "style.roles.ink" in result.errors[0].message
    assert address in result.errors[0].message


@pytest.mark.parametrize("role", ["ink", "accent"])
def test_a_role_bound_to_a_chart_level_token_is_refused_by_name(role: str) -> None:
    result = _compile(f"style:\n  roles:\n    {role}: single_series[1]")

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"
    assert f"style.roles.{role}" in result.errors[0].message
    assert "chart-level" in result.errors[0].message


def test_a_bad_role_on_a_nested_board_is_an_error() -> None:
    inner = "style:\n  roles:\n    ink: chrome.nonsense"
    result = compile_board(
        f"""
title: T
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
rows:
  - title: inner
    {inner.replace(chr(10), chr(10) + "    ")}
    charts:
      c:
        query: q
        type: bar
        x: a
        y: b
    rows:
      - c
"""
    )

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"


_CHART_STYLE_BOARD = """
title: T
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
charts:
  c:
    query: q
    type: line
    x: a
    y: b
{body}
rows:
  - c
"""

_STYLE = """    style:
      marks:
        line:
          stroke:
            color: {token}"""

_LAYER_STYLE = """    layers:
      - type: line
        y: b
        style:
          marks:
            line:
              stroke:
                color: {token}"""


def _validate(tmp_path: Path, body: str, token: str) -> ValidateResult:
    (tmp_path / "dbt_charts.yml").write_text("")
    board = tmp_path / "charts" / "b.yml"
    board.parent.mkdir()
    board.write_text(_CHART_STYLE_BOARD.format(body=body.format(token=token)))
    (result,) = validate_paths([board], project=FilesystemProject(tmp_path))
    return result


@pytest.mark.parametrize("body", [_STYLE, _LAYER_STYLE])
def test_validate_fails_a_chart_token_naming_a_missing_palette_stop(
    tmp_path: Path, body: str
) -> None:
    result = _validate(tmp_path, body, "category[0]")

    assert not result.success
    assert result.errors[0].code == "ERR-COLOR-TOKEN"
    assert "chart 'c'" in result.errors[0].message
    assert "no stop 0" in result.errors[0].message


@pytest.mark.parametrize("body", [_STYLE, _LAYER_STYLE])
def test_validate_passes_a_chart_token_that_resolves(tmp_path: Path, body: str) -> None:
    assert _validate(tmp_path, body, "category[1]").success


_NESTED = """
title: T
queries:
  q:
    columns: [a, b]
    values: [[x, 1]]
rows:
  - title: inner
    style:
      roles:
        mine: chrome[3]
    charts:
      c:
        query: q
        type: line
        x: a
        y: b
        style:
          marks:
            line:
              stroke:
                color: {token}
    rows:
      - c
"""


def _validate_nested(tmp_path: Path, token: str) -> ValidateResult:
    (tmp_path / "dbt_charts.yml").write_text("")
    board = tmp_path / "charts" / "b.yml"
    board.parent.mkdir()
    board.write_text(_NESTED.format(token=token))
    (result,) = validate_paths([board], project=FilesystemProject(tmp_path))
    return result


def test_validate_resolves_a_nested_charts_token_against_its_own_board_roles(
    tmp_path: Path,
) -> None:
    result = _validate_nested(tmp_path, "mine")

    assert result.success, result.errors


def test_validate_reports_a_bad_nested_chart_token_once(tmp_path: Path) -> None:
    result = _validate_nested(tmp_path, "category[0]")

    assert [e.code for e in result.errors] == ["ERR-COLOR-TOKEN"]

"""WARN-RESTATED-*: an authored value equal to what it would inherit."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.config import reset_config
from dbt_charts.core.diagnostics import Diagnostic

_BODY = "rows:\n  - text: hi\n"


@pytest.fixture(autouse=True)
def _fresh_config() -> None:
    reset_config()


@pytest.fixture
def restated(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> Callable[..., list[Diagnostic]]:
    """Compile ``charts/board.yaml`` (plus optional ``charts/meta.yml``) and
    return its WARN-RESTATED-* diagnostics."""

    def run(board: str, meta: str | None = None) -> list[Diagnostic]:
        (tmp_path / "charts").mkdir(exist_ok=True)
        if meta is not None:
            (tmp_path / "charts/meta.yml").write_text(meta, encoding="utf-8")
        (tmp_path / "charts/board.yaml").write_text(board, encoding="utf-8")
        project = local_project(tmp_path)
        result = compile_file(
            project.path("charts/board.yaml").read_board(), apply_meta=True
        )
        assert result.success, result.errors
        return [w for w in result.warnings if w.code.startswith("WARN-RESTATED-")]

    return run


def _paths(warnings: list[Diagnostic]) -> list[str | None]:
    return [w.path for w in warnings]


def test_default_theme_extends_warns(restated: Callable[..., list[Diagnostic]]) -> None:
    warnings = restated("extends: clarity\n" + _BODY)
    assert _paths(warnings) == ["extends"]
    assert "theme `clarity`" in warnings[0].message


def test_non_default_theme_is_not_restated(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    assert restated("extends: paper\n" + _BODY) == []


def test_extends_equal_to_meta_theme_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    warnings = restated("extends: paper\n" + _BODY, meta="extends: paper\n")
    assert _paths(warnings) == ["extends"]


def test_style_leaf_equal_to_theme_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    warnings = restated(
        "style:\n  frame:\n    card_gap: 8\n    margin: 36\n    max_width: 1000\n"
        "  charts:\n    default_chart_height: 300\n" + _BODY
    )
    assert sorted(_paths(warnings)) == [
        "style.charts.default_chart_height",
        "style.frame.card_gap",
        "style.frame.margin",
    ]
    by_path = {w.path: w.message for w in warnings}
    assert "`style.frame.card_gap: 8`" in by_path["style.frame.card_gap"]
    assert "theme `clarity`" in by_path["style.frame.card_gap"]


def test_near_equal_number_is_not_restated(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    assert restated("style:\n  frame:\n    card_gap: 8.5\n" + _BODY) == []


def test_style_leaf_equal_to_meta_warns_and_names_meta(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    warnings = restated(
        "style:\n  frame:\n    card_gap: 12\n" + _BODY,
        meta="style:\n  frame:\n    card_gap: 12\n",
    )
    assert _paths(warnings) == ["style.frame.card_gap"]
    assert "meta.yml" in warnings[0].message


def test_override_of_meta_back_to_the_theme_value_is_not_restated(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    """8 is clarity's card_gap, but the inherited value here is meta's 12."""
    assert (
        restated(
            "style:\n  frame:\n    card_gap: 8\n" + _BODY,
            meta="style:\n  frame:\n    card_gap: 12\n",
        )
        == []
    )


def test_theme_switch_changes_what_is_inherited(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    """The theme the board extends is the layer below its own style."""
    own = "style:\n  background: dbt-grays.white\n" + _BODY
    assert _paths(restated(own)) == ["style.background"]
    assert restated("extends: paper\n" + own) == []


_CHARTS = """
queries:
  q:
    type: values
    rows:
      - {{model_year: 1970, avg_mpg: 3.5}}
    formats:
      avg_mpg: integer
charts:
  t:
    type: table
    query: q
    style:
{style}
  k:
    type: kpi
    query: q
    value: avg_mpg
    style:
{kpi_style}
rows:
  - t
  - k
"""


def _chart_board(style: str = "      {}", kpi_style: str = "      {}") -> str:
    return _CHARTS.format(style=style, kpi_style=kpi_style)


def test_table_label_equal_to_derived_header_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    warnings = restated(
        _chart_board(
            "      columns:\n        model_year:\n          label: Model year\n"
            "        avg_mpg:\n          label: Miles per gallon\n"
        )
    )
    assert _paths(warnings) == ["charts.t.style.columns.model_year.label"]
    assert "header derived from the column name" in warnings[0].message


def test_chart_format_equal_to_query_format_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    warnings = restated(
        _chart_board(
            kpi_style="      value:\n        format: integer\n",
        )
    )
    assert _paths(warnings) == ["charts.k.style.value.format"]
    assert "`formats:` entry for `avg_mpg`" in warnings[0].message


def test_chart_format_that_differs_from_query_format_is_kept(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    assert (
        restated(_chart_board(kpi_style="      value:\n        format: percent\n"))
        == []
    )


def test_chart_style_equal_to_the_board_tier_for_its_type_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    board = (
        "style:\n  charts:\n    table:\n      pagination:\n        page_rows: 5\n"
        + _chart_board("      pagination:\n        page_rows: 5\n")
    )
    warnings = restated(board)
    assert _paths(warnings) == ["charts.t.style.pagination.page_rows"]
    assert "style for this chart type" in warnings[0].message


def test_chart_style_that_overrides_the_board_tier_is_kept(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    board = (
        "style:\n  charts:\n    table:\n      pagination:\n        page_rows: 5\n"
        + _chart_board("      pagination:\n        page_rows: 10\n")
    )
    assert restated(board) == []


def test_query_source_equal_to_the_board_source_warns(
    restated: Callable[..., list[Diagnostic]],
) -> None:
    board = (
        "source: cars\nqueries:\n  q:\n    source: cars\n    sql: select 1 as n\n"
        "  r:\n    source: other\n    sql: select 2 as n\n" + _BODY
    )
    paths = _paths(restated(board))
    assert "queries.q.source" in paths
    assert "queries.r.source" not in paths

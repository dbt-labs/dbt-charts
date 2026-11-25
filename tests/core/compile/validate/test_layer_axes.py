"""A layer's own y-axis title or style needs some layer to pin `axis_y.position`;
compile() and `dct validate` refuse it before any query runs."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.compiler import CompileResult, compile as compile_board


def _board(*layer_axes: dict[str, object]) -> str:
    board = {
        "title": "T",
        "queries": {"q": {"columns": ["x", "y", "fit"], "values": [[1, 2, 2.1]]}},
        "charts": {
            "c": {
                "query": "q",
                "type": "scatter",
                "x": "x",
                "y": "y",
                "layers": [
                    {"type": "line", "y": "fit", "axis_y": a} for a in layer_axes
                ],
            }
        },
        "rows": ["c"],
    }
    return yaml.safe_dump(board, sort_keys=False)


def _compile(text: str) -> CompileResult:
    return compile_board(text, file="charts/b.yml")


def test_layer_axis_y_title_without_any_pinned_position_is_a_compile_error() -> None:
    text = _board({"title": "Price ($)"})
    result = _compile(text)

    assert not result.success
    error = result.errors[0]
    assert error.code == "ERR-LAYER-AXIS-TITLE-WITHOUT-POSITION"
    assert "charts.c.layers.0.axis_y.title" in error.message
    assert error.range is not None
    title_line = next(
        n for n, line in enumerate(text.splitlines(), 1) if "Price" in line
    )
    assert error.range.start_line == title_line


@pytest.mark.parametrize(
    "axis", [{"color": "red"}, {"grid": {"dash": [2, 2]}}, {"grid": {"color": "red"}}]
)
def test_layer_axis_y_style_without_any_pinned_position_is_a_compile_error(
    axis: dict[str, object],
) -> None:
    result = _compile(_board(axis))

    assert not result.success
    assert result.errors[0].code == "ERR-LAYER-AXIS-STYLE-WITHOUT-POSITION"
    assert result.errors[0].range is not None


def test_layer_axis_y_title_with_pinned_position_compiles() -> None:
    result = _compile(_board({"title": "Price ($)", "position": "right"}))

    assert result.success, result.errors


def test_layer_axis_y_title_compiles_when_another_layer_pins_a_side() -> None:
    """The axis split is chart-wide: an unpinned layer's title still lands."""
    result = _compile(_board({"position": "right"}, {"title": "Price ($)"}))

    assert result.success, result.errors


def test_dct_validate_reports_a_layer_title_with_no_side_pinned(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text("")
    board = tmp_path / "charts" / "b.yml"
    board.parent.mkdir()
    board.write_text(_board({"title": "Price ($)"}))

    (result,) = validate_paths([board], project=FilesystemProject(tmp_path))

    assert not result.success
    assert result.errors[0].code == "ERR-LAYER-AXIS-TITLE-WITHOUT-POSITION"

"""A render executes only the queries of the tabs it draws."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.execute import Executor
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.render.render_result import RenderResult
from dbt_charts.core.render.renderer import prefetch_tab_queries, render

LocalProject = Callable[..., FilesystemProject]

_TWO_TAB_BOARD = """
cache:
  prefetch: true
queries:
  shown:
    columns: [month, revenue]
    values:
      - [Jan, 100]
      - [Feb, 140]
  hidden:
    columns: [month, revenue]
    values:
      - [Jan, 7]
      - [Feb, 9]
charts:
  shown_chart:
    query: shown
    type: bar
    x: month
    y: revenue
  hidden_chart:
    query: hidden
    type: bar
    x: month
    y: revenue
tabs:
  id: view
  items:
    - title: Shown
      rows: [shown_chart]
    - title: Hidden
      rows: [hidden_chart]
"""

_NESTED_TABS_BOARD = """
cache:
  prefetch: true
queries:
  a:
    columns: [x, y]
    values:
      - [p, 1]
  b1:
    columns: [x, y]
    values:
      - [p, 2]
  b2:
    columns: [x, y]
    values:
      - [p, 3]
    cache:
      prefetch: false
charts:
  a_chart:
    query: a
    type: bar
    x: x
    y: y
  b1_chart:
    query: b1
    type: bar
    x: x
    y: y
  b2_chart:
    query: b2
    type: bar
    x: x
    y: y
tabs:
  id: outer
  items:
    - title: A
      rows: [a_chart]
    - title: B
      tabs:
        id: inner
        items:
          - title: B1
            rows: [b1_chart]
          - title: B2
            rows: [b2_chart]
"""


def _spied_render(
    yaml_content: str,
    local_project: LocalProject,
    monkeypatch: pytest.MonkeyPatch,
    **render_kwargs: Any,
) -> tuple[RenderResult, list[str], Board, Executor]:
    result = compile(yaml_content)
    assert result.success, result.errors
    assert result.board is not None
    executor = Executor(
        result.board,
        adapter_registry=build_adapter_registry(local_project(Path.cwd())),
        query_registry=result.query_registry,
    )
    seen: list[str] = []
    original = executor.execute_query

    def spy(name: str, *args: Any, **kwargs: Any) -> Any:
        seen.append(name)
        return original(name, *args, **kwargs)

    monkeypatch.setattr(executor, "execute_query", spy)
    rendered = render(result.board, executor, **render_kwargs)
    return rendered, seen, result.board, executor


def test_drawn_render_never_executes_the_hidden_tab(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, seen, _board, _executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format="svg"
    )

    assert rendered.board_error is None
    assert "shown" in seen
    assert "hidden" not in seen


def test_selected_tab_is_the_one_executed(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, seen, _board, _executor = _spied_render(
        _TWO_TAB_BOARD,
        local_project,
        monkeypatch,
        format="svg",
        variables={"view": "hidden"},
    )

    assert rendered.board_error is None
    assert "hidden" in seen
    assert "shown" not in seen


@pytest.mark.parametrize("fmt", ["json", "yaml", "text", "data", "terminal"])
def test_formats_that_walk_every_tab_execute_every_tab(
    fmt: str, local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, seen, _board, _executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format=fmt
    )

    assert rendered.board_error is None
    assert {"shown", "hidden"} <= set(seen)


def test_unknown_tab_is_a_board_error_before_any_query(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, seen, _board, _executor = _spied_render(
        _TWO_TAB_BOARD,
        local_project,
        monkeypatch,
        format="svg",
        variables={"view": "nope"},
    )

    assert rendered.board_error is not None
    assert rendered.board_error.code == "ERR-TAB-UNKNOWN"
    assert seen == []


def test_render_reports_foreground_query_time(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, _seen, _board, _executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format="svg"
    )

    assert rendered.query_ms is not None
    assert rendered.query_ms >= 0


def test_prefetch_runs_only_the_hidden_tabs(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rendered, seen, board, executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format="svg"
    )
    seen.clear()

    prefetched = prefetch_tab_queries(board, executor, {}, max_workers=2)

    assert prefetched == {"hidden": None}
    assert seen == ["hidden"]


def test_prefetch_skips_queries_already_cached(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rendered, _seen, board, executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format="svg"
    )
    prefetch_tab_queries(board, executor, {}, max_workers=2)

    assert prefetch_tab_queries(board, executor, {}, max_workers=2) == {}


def test_a_query_that_opts_out_of_prefetch_stays_cold(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rendered, seen, board, executor = _spied_render(
        _NESTED_TABS_BOARD, local_project, monkeypatch, format="svg"
    )
    assert set(seen) == {"a"}

    prefetched = prefetch_tab_queries(board, executor, {}, max_workers=2)

    assert set(prefetched) == {"b1"}


def test_nothing_is_prefetched_unless_the_cache_policy_asks(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    board_yaml = _TWO_TAB_BOARD.replace("cache:\n  prefetch: true\n", "", 1)
    rendered, _seen, board, executor = _spied_render(
        board_yaml, local_project, monkeypatch, format="svg"
    )

    assert rendered.prefetch_query_names == frozenset()
    assert prefetch_tab_queries(board, executor, {}, max_workers=2) == {}


def test_render_reports_only_hidden_queries_that_prefetch(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, _seen, _board, _executor = _spied_render(
        _NESTED_TABS_BOARD, local_project, monkeypatch, format="svg"
    )

    assert rendered.prefetch_query_names == {"b1"}


def test_prefetch_failure_is_returned_not_raised(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rendered, _seen, board, executor = _spied_render(
        _TWO_TAB_BOARD, local_project, monkeypatch, format="svg"
    )

    def boom(name: str, variables: dict[str, Any] | None = None) -> Any:
        raise RuntimeError("warehouse down")

    monkeypatch.setattr(executor, "execute_query", boom)

    prefetched = prefetch_tab_queries(board, executor, {}, max_workers=2)

    assert isinstance(prefetched["hidden"], RuntimeError)


_WIDE_COLUMNS = [f"column_with_a_long_name_{i}" for i in range(4)]
_UNEVEN_TABS_BOARD = f"""
queries:
  shown:
    columns: [month, revenue]
    values:
      - [Jan, 100]
  wide:
    columns: [{", ".join(_WIDE_COLUMNS)}]
    values:
      - [{", ".join("1" for _ in _WIDE_COLUMNS)}]
charts:
  shown_chart:
    query: shown
    type: bar
    x: month
    y: revenue
  wide_table:
    query: wide
    type: table
tabs:
  id: view
  items:
    - title: Shown
      rows: [shown_chart]
    - title: Table
      rows: [wide_table]
"""


def _svg_width(svg: str) -> str:
    match = re.search(r'viewBox="0 0 ([0-9.]+) ', svg)
    assert match is not None
    return match.group(1)


def test_board_width_does_not_depend_on_the_open_tab(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    widths = set()
    for tab in ("shown", "table"):
        rendered, seen, _board, _executor = _spied_render(
            _UNEVEN_TABS_BOARD,
            local_project,
            monkeypatch,
            format="svg",
            variables={"view": tab},
        )
        assert isinstance(rendered.output, str)
        assert ("wide" in seen) == (tab == "table")
        widths.add(_svg_width(rendered.output))

    assert len(widths) == 1


_HIDDEN_PROBE_BOARD = _TWO_TAB_BOARD.replace(
    "  hidden:\n",
    "  flags:\n    columns: [show_it]\n    values:\n      - [true]\n  hidden:\n",
).replace(
    "      rows: [hidden_chart]",
    "      rows:\n        - visible:\n            query: flags\n            column: show_it\n"
    "          text: Shown only when flagged\n        - hidden_chart",
)


def test_hidden_tab_visibility_probe_is_not_executed(
    local_project: LocalProject, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered, seen, _board, _executor = _spied_render(
        _HIDDEN_PROBE_BOARD, local_project, monkeypatch, format="svg"
    )

    assert rendered.board_error is None
    assert "flags" not in seen
    assert {"flags", "hidden"} <= rendered.prefetch_query_names

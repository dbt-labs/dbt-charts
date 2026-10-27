"""Tests for the three-layer max_workers override priority.

Priority (highest wins): CLI flag → DCT_MAX_WORKERS env var → config default.
"""

import importlib
from unittest.mock import patch

import pytest

from dbt_charts.core.compile import compile
from dbt_charts.core.compile.config import get_execution_config, reset_config
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render

# importlib.import_module uses sys.modules directly, bypassing the attribute-
# traversal problem caused by dbt_charts.core.__init__ shadowing the render
# subpackage with the render() function.
_renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")

BOARD_YAML = """\
title: Test
source: duckdb
queries:
  q:
    sql: SELECT 1 AS x, 2 AS y
charts:
  c:
    query: q
    type: bar
    x: x
    y: y
cols:
  - c
"""


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


@pytest.fixture
def board_and_executor():
    from unittest.mock import Mock

    from dbt_charts.core.column_types import literal_schema
    from dbt_charts.core.execute.adapters.base import QueryResult

    result = compile(BOARD_YAML)
    assert result.success, result.errors
    registry = Mock()
    registry.execute.return_value = QueryResult(
        data=[{"x": 1, "y": 2}], schema=literal_schema([{"x": 1, "y": 2}])
    )
    executor = Executor(result.board, adapter_registry=registry)
    return result.board, executor


class TestExecutionConfig:
    def test_get_execution_config_has_max_workers(self):
        cfg = get_execution_config()
        assert isinstance(cfg.max_workers, int)
        assert cfg.max_workers > 0


class TestMaxWorkersResolution:
    def test_explicit_param_used(self, board_and_executor):
        board, executor = board_and_executor
        captured = []

        def fake_parallel(_, names, variables=None, max_workers=8):
            captured.append(max_workers)
            return {}

        with patch.object(_renderer_mod, "execute_queries_parallel", fake_parallel):
            render(board, executor, max_workers=3)

        assert captured == [3]

    def test_env_var_used_when_no_param(self, board_and_executor, monkeypatch):
        board, executor = board_and_executor
        monkeypatch.setenv("DCT_MAX_WORKERS", "12")
        captured = []

        def fake_parallel(_, names, variables=None, max_workers=8):
            captured.append(max_workers)
            return {}

        with patch.object(_renderer_mod, "execute_queries_parallel", fake_parallel):
            render(board, executor)

        assert captured == [12]

    def test_config_default_used_when_no_param_no_env(
        self, board_and_executor, monkeypatch
    ):
        board, executor = board_and_executor
        monkeypatch.delenv("DCT_MAX_WORKERS", raising=False)
        captured = []

        def fake_parallel(_, names, variables=None, max_workers=8):
            captured.append(max_workers)
            return {}

        with patch.object(_renderer_mod, "execute_queries_parallel", fake_parallel):
            render(board, executor)

        assert captured == [get_execution_config().max_workers]
        assert captured[0] > 0

    def test_explicit_param_beats_env_var(self, board_and_executor, monkeypatch):
        board, executor = board_and_executor
        monkeypatch.setenv("DCT_MAX_WORKERS", "12")
        captured = []

        def fake_parallel(_, names, variables=None, max_workers=8):
            captured.append(max_workers)
            return {}

        with patch.object(_renderer_mod, "execute_queries_parallel", fake_parallel):
            render(board, executor, max_workers=5)

        assert captured == [5]

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic import TypeAdapter

from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.chart_resolution import collect_shared_y_datasets
from dbt_charts.core.execute.executor import Executor


def test_collects_each_primary_scale_query_once() -> None:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "layered",
            "type": "bar",
            "x": "month",
            "y": "actual",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "actual_query",
            "layers": [
                {"type": "line", "y": "goal", "query": "goal_query"},
                {"type": "bar", "y": "target", "query": "goal_query"},
                {
                    "type": "line",
                    "y": "benchmark",
                    "query": "right_query",
                    "axis_y": {"position": "right"},
                },
            ],
        }
    )
    base_rows = [{"month": "2024-01", "actual": 70.5}]
    goal_rows = [{"month": "2024-01", "goal": 250.0, "target": 225.0}]
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = goal_rows

    datasets = collect_shared_y_datasets(chart, base_rows, executor, {})

    assert datasets == {
        "actual_query": base_rows,
        "goal_query": goal_rows,
    }
    executor.execute_query.assert_called_once_with("goal_query", {})


def test_collects_a_right_axis_span_layer_query() -> None:
    chart = TypeAdapter(Chart).validate_python(
        {
            "id": "layered",
            "type": "line",
            "x": "month",
            "y": "actual",
            "query": SqlQuery(sql="SELECT 1", source="src"),
            "query_name": "actual_query",
            "layers": [
                {
                    "type": "bar",
                    "y": "cost",
                    "y_start": "budget",
                    "query": "side_query",
                    "axis_y": {"position": "right"},
                },
            ],
        }
    )
    side_rows = [{"month": "2024-01", "cost": 5.0, "budget": 3.0}]
    executor = MagicMock(spec=Executor)
    executor.execute_query.return_value = side_rows

    datasets = collect_shared_y_datasets(chart, [], executor, {})

    assert datasets["side_query"] == side_rows

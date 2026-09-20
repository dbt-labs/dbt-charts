"""Regression test: `variable_dependencies` (a `frozenset[str]`) must dump as
a sorted, deterministic list — pydantic-core's JSON dump of a Python
`frozenset` iterates in `PYTHONHASHSEED`-dependent order, so without a
serializer two otherwise-identical processes can emit different orderings
for the same set (`docs/laws-compile.md`'s rung 3 section, `libs/chart-svg`).

Covers every model that carries this field: `SqlQuery` (`models/query/
normalized.py`), `Variable` (`models/variable/authored.py`), the normalized
and resolved chart bases and callouts (`models/chart/`).
"""

import json
import os
import subprocess
import sys

DEPS = ["status", "date_range", "region"]

_DUMP_SCRIPT = """
import json
from dbt_charts.core.compile.models.chart.normalized._base import _BaseChartFields
from dbt_charts.core.compile.models.chart.normalized.callout import CalloutChart
from dbt_charts.core.compile.models.chart.resolved._base import _BaseResolvedChartFields
from dbt_charts.core.compile.models.chart.resolved.callout import ResolvedCalloutChart
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.compile.models.variable.authored import Variable

deps = frozenset(["status", "date_range", "region"])
models = {
    "query": SqlQuery,
    "variable": Variable,
    "chart": _BaseChartFields,
    "callout": CalloutChart,
    "resolved_chart": _BaseResolvedChartFields,
    "resolved_callout": ResolvedCalloutChart,
}
out = {}
for name, model in models.items():
    dumped = model.__pydantic_serializer__.to_python(
        model.model_construct(variable_dependencies=deps),
        mode="json",
        include={"variable_dependencies"},
    )
    out[name] = dumped["variable_dependencies"]
print(json.dumps(out))
"""


def _dump_under_hashseed(seed: str) -> dict[str, list[str]]:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_variable_dependencies_dumps_identically_across_hash_seeds() -> None:
    first = _dump_under_hashseed("0")
    second = _dump_under_hashseed("1234")
    assert first == second, "frozenset dump order must not depend on PYTHONHASHSEED"


def test_variable_dependencies_dumps_sorted() -> None:
    dumped = _dump_under_hashseed("0")
    assert all(value == sorted(DEPS) for value in dumped.values()), dumped

"""The execute boundary establishes attribution for each query it runs.

`test_attribution.py` covers the module in isolation; this pins the seam where it
meets the engine — the scope `AdapterRegistry.execute` opens around the adapter call,
including the case the pool-sharing design depends on: two sources with identical
connection identity but different `attribution:` must each get their own labels.
"""

from __future__ import annotations

from typing import Any

from dbt_charts.core.attribution import current_attribution
from dbt_charts.core.column_types import literal_schema
from dbt_charts.core.compile.config import ProjectSourcesConfig
from dbt_charts.core.compile.models.query.normalized import SqlQuery
from dbt_charts.core.execute.adapters.adapter_registry import AdapterRegistry
from dbt_charts.core.execute.adapters.base import BaseAdapter, QueryParams, QueryResult
from dbt_charts.core.project import Project


class _RecordingAdapter(BaseAdapter):
    """Captures the attribution in effect at the moment a query is dispatched."""

    def __init__(self) -> None:
        self.seen: dict[str, str] = {}

    @property
    def supported_types(self) -> set[str]:
        return {"sql"}

    def _can_execute(self, query: Any, source_config: Any) -> bool:
        return True

    def _execute(
        self,
        query: Any,
        variables: Any = None,
        params: QueryParams = None,
        source_config: Any = None,
    ) -> QueryResult:
        self.seen = current_attribution()
        return QueryResult(data=[], columns=[], schema=literal_schema([], []))


def _registry(
    project: Project, sources: dict[str, dict[str, Any]]
) -> tuple[AdapterRegistry, _RecordingAdapter]:
    adapter = _RecordingAdapter()
    registry = AdapterRegistry(
        project=project, project_sources=ProjectSourcesConfig(sources=sources)
    )
    registry.register(adapter)
    return registry, adapter


_PG: dict[str, Any] = {
    "type": "postgres",
    "host": "warehouse.internal",
    "dbname": "analytics",
    "user": "u",
    "password": "p",
}


def test_query_name_reaches_the_payload(local_project: Any, tmp_path: Any) -> None:
    registry, adapter = _registry(local_project(tmp_path), {"wh": _PG})
    registry.execute(
        SqlQuery(sql="SELECT 1", source="wh"), query_name="revenue_by_month"
    )
    assert adapter.seen["dbt_charts_query"] == "revenue_by_month"


def test_authored_attribution_reaches_the_payload(
    local_project: Any, tmp_path: Any
) -> None:
    registry, adapter = _registry(
        local_project(tmp_path), {"wh": {**_PG, "attribution": {"team": "finance"}}}
    )
    registry.execute(SqlQuery(sql="SELECT 1", source="wh"), query_name="q")
    assert adapter.seen["team"] == "finance"


def test_two_sources_sharing_a_connection_keep_their_own_labels(
    local_project: Any, tmp_path: Any
) -> None:
    """The pool is keyed on connection identity, so these two share one pool. Each
    query must still carry its own source's team — a value captured on the pooled
    adapter would label both with whichever source ran first."""
    registry, adapter = _registry(
        local_project(tmp_path),
        {
            "marketing": {**_PG, "attribution": {"team": "marketing"}},
            "finance": {**_PG, "attribution": {"team": "finance"}},
        },
    )
    registry.execute(SqlQuery(sql="SELECT 1", source="marketing"), query_name="q")
    first = adapter.seen["team"]
    registry.execute(SqlQuery(sql="SELECT 1", source="finance"), query_name="q")
    second = adapter.seen["team"]
    assert (first, second) == ("marketing", "finance")


def test_scope_unwinds_after_the_query(local_project: Any, tmp_path: Any) -> None:
    registry, _ = _registry(
        local_project(tmp_path), {"wh": {**_PG, "attribution": {"team": "finance"}}}
    )
    registry.execute(SqlQuery(sql="SELECT 1", source="wh"), query_name="q")
    assert "team" not in current_attribution()

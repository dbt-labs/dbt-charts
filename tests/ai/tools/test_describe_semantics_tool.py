"""The describe_semantics tool lists a source's MetricFlow bundle through dispatch."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tool_schemas import ALL_TOOLS, DESCRIBE_SEMANTICS
from dbt_charts.ai.tools import dispatch_tool_call

from ..._paths import DBT_CHARTS_DIR


@pytest.fixture
def bundle_context(tmp_path: Path) -> DbtChartsAIContext:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )
    return DbtChartsAIContext(project_session=ProjectSession.open(tmp_path))


def test_tool_is_advertised_with_a_required_source_and_optional_name_and_search() -> (
    None
):
    assert DESCRIBE_SEMANTICS in ALL_TOOLS
    schema = DESCRIBE_SEMANTICS["input_schema"]
    assert list(schema["properties"]) == ["source", "name", "search"]
    assert schema["required"] == ["source"]


@pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)
def test_returns_the_overview_then_one_object(
    bundle_context: DbtChartsAIContext,
) -> None:
    overview = dispatch_tool_call(
        "describe_semantics", {"source": "db"}, context=bundle_context
    )

    json.dumps(overview)
    assert overview["name"] == "dundersign"
    invoices = next(d for d in overview["datasets"] if d["name"] == "invoices")
    assert invoices["counts"] == {"columns": 5, "calculations": 4, "relationships": 1}
    assert "calculations" not in invoices

    billing = dispatch_tool_call(
        "describe_semantics",
        {"source": "db", "name": "billing"},
        context=bundle_context,
    )

    json.dumps(billing)
    assert billing["metrics"][-1]["name"] == "uncharged"


def test_source_without_a_bundle_is_a_coded_failure(
    bundle_context: DbtChartsAIContext,
) -> None:
    result = dispatch_tool_call(
        "describe_semantics", {"source": "plain"}, context=bundle_context
    )

    assert result["success"] is False
    assert result["error_code"] == "ERR-METRICFLOW-NO-BUNDLE"
    assert "declares no `metricflow:`" in result["errors"][0]


def test_rejects_unknown_arguments(bundle_context: DbtChartsAIContext) -> None:
    result = dispatch_tool_call(
        "describe_semantics", {"source": "db", "path": "x"}, context=bundle_context
    )

    assert result["success"] is False
    assert result["error_code"] == "ERR-TOOL-ARGUMENTS-INVALID"

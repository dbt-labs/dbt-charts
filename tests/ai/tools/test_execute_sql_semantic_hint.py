"""execute_sql reports a semantic compile failure with the "did you mean" the
validation path gives, so an agent corrects the name on its next call."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path

import duckdb
import pytest

from dbt_charts.agent_api import ProjectSession
from dbt_charts.ai.context import DbtChartsAIContext
from dbt_charts.ai.tools import dispatch_tool_call

from ..._paths import DBT_CHARTS_DIR


@pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)
def test_a_mistyped_calculation_comes_back_with_the_declared_name(
    tmp_path: Path,
) -> None:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )
    duckdb.connect(str(tmp_path / "warehouse.duckdb")).close()
    context = DbtChartsAIContext(project_session=ProjectSession.open(tmp_path))

    result = dispatch_tool_call(
        "execute_sql",
        {"sql": "SELECT AGG(invoiced_totl) FROM invoices", "source": "db"},
        context=context,
    )

    assert result["success"] is False
    assert result["error_code"] == "ERR-METRICFLOW-COMPILE-FAILED"
    assert "Did you mean `invoiced_total`?" in result["errors"][0]

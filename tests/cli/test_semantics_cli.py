"""`dct semantics SOURCE [NAME]` — thin wrapper over agent_api.describe_semantics."""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli import main as cli_main

from .._paths import DBT_CHARTS_DIR

runner = CliRunner()

needs_metricflow = pytest.mark.skipif(
    importlib.util.find_spec("metricflow_sql") is None,
    reason="metricflow-sql is not installed",
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    shutil.copytree(
        DBT_CHARTS_DIR / "tests" / "fixtures" / "metricflow-billing",
        tmp_path,
        dirs_exist_ok=True,
    )
    return tmp_path


@needs_metricflow
def test_prints_the_objects_with_field_counts(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app, ["semantics", "db", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 0, result.output
    assert "invoices  One row per Stripe invoice." in result.stdout
    assert "5 columns, 4 calculations, 1 relationship" in result.stdout
    assert "1 key column, 2 attributes" in result.stdout
    assert "billing  Billing by account and month." in result.stdout
    assert "2 roles, 3 metrics" in result.stdout
    assert "collection_rate" not in result.stdout
    assert "dct semantics db NAME" in result.stdout


@needs_metricflow
def test_prints_one_named_object_in_full(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app, ["semantics", "db", "invoices", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 0, result.output
    assert 'collection_rate = "paid_total" / "invoiced_total"' in result.stdout
    assert "account -> account" in result.stdout
    assert "uncharged" not in result.stdout


SAVED = """\
schema_version: v1
bundle: dundersign
generation_meta: {}
dialect: duckdb
datasets:
- name: boards.unpaid_invoices_by_month
  description: Finance board, invoices still unpaid at each month end.
  source:
    type: sql
    sql: SELECT month, SUM(total) AS unpaid FROM invoices GROUP BY 1
    columns:
    - name: month
      datatype: DATE
    - name: unpaid
      datatype: DOUBLE
pages:
- name: boards/overdue_accounts
  description: Finance board, accounts overdue by more than thirty days.
  content: |
    # Overdue accounts

    ```sql
    SELECT account_id FROM invoices WHERE days_late > 30
    ```
"""


@needs_metricflow
def test_prints_a_page_an_entity_and_a_derived_dataset_in_full(
    project_dir: Path,
) -> None:
    (project_dir / "semantics" / "saved.yaml").write_text(SAVED)

    def shown(name: str) -> str:
        result = runner.invoke(
            cli_main.app, ["semantics", "db", name, "--project-dir", str(project_dir)]
        )
        assert result.exit_code == 0, result.output
        return result.stdout

    page = shown("boards/overdue_accounts")
    assert "# Overdue accounts" in page
    assert "SELECT account_id FROM invoices WHERE days_late > 30" in page

    entity = shown("account")
    assert "key: id TEXT" in entity
    assert 'attribute: industry = "accounts"."industry"' in entity

    derived = shown("boards.unpaid_invoices_by_month")
    assert "sql: SELECT month, SUM(total) AS unpaid FROM invoices GROUP BY 1" in derived
    assert "columns: month DATE, unpaid DOUBLE" in derived


@needs_metricflow
def test_json_is_the_overview_or_the_object(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app, ["semantics", "db", "--json", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 0, result.output
    overview = json.loads(result.stdout)
    assert overview["name"] == "dundersign"
    assert [d["name"] for d in overview["datasets"]] == [
        "accounts",
        "invoices",
        "charges",
    ]
    assert overview["namespaces"] == {"sales": 1}
    assert overview["constellations"][0] == {
        "name": "billing",
        "description": "Billing by account and month.",
        "counts": {"roles": 2, "metrics": 3},
    }

    result = runner.invoke(
        cli_main.app,
        ["semantics", "db", "billing", "--json", "--project-dir", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    billing = json.loads(result.stdout)
    assert [m["name"] for m in billing["metrics"]] == [
        "invoiced_total",
        "charged_amount",
        "uncharged",
    ]


@needs_metricflow
def test_unknown_name_exits_1_naming_the_close_ones(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app, ["semantics", "db", "invoice", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "invoices" in result.stderr


def test_source_without_a_bundle_exits_1_with_the_reason_on_stderr(
    project_dir: Path,
) -> None:
    result = runner.invoke(
        cli_main.app, ["semantics", "plain", "--project-dir", str(project_dir)]
    )

    assert result.exit_code == 1
    assert "declares no `metricflow:`" in result.stderr


@needs_metricflow
def test_search_prints_the_matching_fields(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app,
        [
            "semantics",
            "db",
            "--search",
            "collection rate",
            "--project-dir",
            str(project_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "calculation: invoices.collection_rate" in result.stdout


@needs_metricflow
def test_search_with_no_match_says_so(project_dir: Path) -> None:
    result = runner.invoke(
        cli_main.app,
        ["semantics", "db", "--search", "zzzqqq", "--project-dir", str(project_dir)],
    )

    assert result.exit_code == 0, result.output
    assert "Nothing matches 'zzzqqq'." in result.stdout


def test_semantics_is_hidden_from_root_help() -> None:
    result = runner.invoke(cli_main.app, ["--help"], env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "semantics" not in result.output

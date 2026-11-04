"""A verb that never connects never resolves a source.

Resolving one renders its ``env_var()`` references, which reads the warehouse's
environment and imports dbt-core. Search, describe, validate and impact read
YAML and SQL text, so they must work in a shell holding no warehouse credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

UNSET = "DCT_TEST_ENV_VAR_THAT_IS_NEVER_SET"

CONFIG = f"""\
sources:
  warehouse:
    type: bigquery
    project: "{{{{ env_var('{UNSET}') }}}}"
    dataset: analytics
    cache: "{{{{ env_var('{UNSET}', '1h') }}}}"
"""

BOARD = """\
title: Revenue
source: warehouse
queries:
  by_month:
    sql: select month, amount from orders
rows:
- revenue:
    query: by_month
    type: bar
    x: month
    y: amount
"""

VERBS = {
    "search": ["search", "revenue"],
    "search-json": ["search", "revenue", "--json"],
    "describe": ["describe", "charts/revenue.yml"],
    "describe-overview": ["describe"],
    "validate": ["validate", "charts/revenue.yml"],
    "impact": ["impact", "amount"],
}

IMPORT_PROBE = f"""
import sys
from dbt_charts.cli.main import app

for verb in {list(VERBS.values())!r}:
    app(verb, standalone_mode=False)

loaded = [
    name
    for name in sys.modules
    if name == "dbt" or name.startswith(("dbt.", "google.cloud.bigquery"))
]
assert not loaded, loaded
"""


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(CONFIG)
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "revenue.yml").write_text(BOARD)
    return tmp_path


@pytest.mark.parametrize("verb", VERBS)
def test_verb_needs_no_warehouse_environment(
    verb: str, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    monkeypatch.delenv(UNSET, raising=False)
    result = CliRunner().invoke(app, VERBS[verb])
    assert result.exit_code == 0, result.output


def test_verbs_import_neither_dbt_nor_an_adapter(project: Path) -> None:
    subprocess.run(
        [sys.executable, "-c", IMPORT_PROBE],
        cwd=project,
        env={**os.environ, "DO_NOT_TRACK": "1", "DCT_NO_WORKSPACE_GUARD": "1"},
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_query_still_renders_the_source(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(project)
    monkeypatch.delenv(UNSET, raising=False)
    result = CliRunner().invoke(app, ["query", "charts/revenue.yml", "by_month"])
    assert result.exit_code != 0
    assert UNSET in f"{result.output}{result.exception}"


MANIFEST = {
    "metadata": {
        "dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"
    },
    "nodes": {
        "model.shop.orders": {
            "resource_type": "model",
            "name": "orders",
            "schema": "analytics",
            "relation_name": "analytics.orders",
        }
    },
    "sources": {},
}

REF_BOARD = """\
title: Orders
source: warehouse
queries:
  orders:
    sql: "select * from {{ ref('orders') }}"
rows: []
"""


@pytest.fixture
def dbt_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(UNSET, raising=False)
    (tmp_path / "custom").mkdir()
    (tmp_path / "custom" / "manifest.json").write_text(json.dumps(MANIFEST))
    # Present but without the ref: a missing default manifest would only warn.
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "manifest.json").write_text(
        json.dumps({**MANIFEST, "nodes": {}})
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "orders.yml").write_text(REF_BOARD)
    return tmp_path


def _dbt_config(target_path: str, extra: str = "") -> str:
    return f"""\
sources:
  warehouse:
    type: dbt_profile
    profile: "{{{{ env_var('{UNSET}', 'shop') }}}}"
    target_path: {target_path}
{extra}"""


def test_validate_reads_the_manifest_a_templated_source_names(
    dbt_project: Path,
) -> None:
    (dbt_project / "dbt_charts.yml").write_text(_dbt_config("custom"))
    result = CliRunner().invoke(app, ["validate", "charts/orders.yml"])
    assert result.exit_code == 0, result.output


def test_validate_reports_a_malformed_templated_source(dbt_project: Path) -> None:
    (dbt_project / "dbt_charts.yml").write_text(
        _dbt_config("custom", "    bogus_field: 1\n")
    )
    result = CliRunner().invoke(app, ["validate", "charts/orders.yml"])
    assert result.exit_code != 0
    assert "bogus_field" in result.output


@pytest.mark.parametrize(
    "verb", [["describe", "charts/orders.yml"], ["describe"], ["search", "--json"]]
)
def test_an_unset_target_path_is_reported_without_a_traceback(
    verb: list[str], dbt_project: Path
) -> None:
    (dbt_project / "dbt_charts.yml").write_text(
        _dbt_config(f"\"{{{{ env_var('{UNSET}') }}}}\"")
    )
    result = CliRunner().invoke(app, verb)
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "warehouse" in result.output

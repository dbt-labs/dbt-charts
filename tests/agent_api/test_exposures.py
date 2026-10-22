"""Tests for agent_api/exposures.py — dbt exposures derived from boards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema.validators import validator_for
from pydantic import ValidationError

from dbt_charts.agent_api.exposures import ExposuresError, emit_exposures
from dbt_charts.cli.filesystem_project import FilesystemProject

from .._paths import DBT_CHARTS_DIR

_CONFIG = """\
sources:
  db:
    type: duckdb
    path: ':memory:'
exposures:
  owner:
    name: Analytics
    email: analytics@example.com
"""


def _board(queries: dict[str, str], extra: str = "") -> str:
    lines = ["title: Sales", "source: db", *extra.splitlines(), "queries:"]
    for name, sql in queries.items():
        lines.append(f'  {name}: "{sql}"')
    lines += ["charts:"]
    for name in queries:
        lines += [f"  c_{name}:", f"    query: {name}", "    type: table"]
    lines += ["rows:"] + [f"  - c_{name}" for name in queries]
    return "\n".join(lines) + "\n"


def _project(
    tmp_path: Path, boards: dict[str, str], config: str = _CONFIG
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(config)
    (tmp_path / "dbt_project.yml").write_text("name: shop\nmodel-paths: [models]\n")
    for relname, text in boards.items():
        path = tmp_path / "charts" / relname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return FilesystemProject(tmp_path)


_SCHEMAS = DBT_CHARTS_DIR / "tests" / "fixtures" / "vendor" / "dbt_jsonschema"


def _emitted(tmp_path: Path) -> list[dict[str, object]]:
    """The emitted exposures, after checking the file against both dbt-core's
    and Fusion's published schemas: each rejects shapes the other accepts."""
    doc = yaml.safe_load(
        (tmp_path / "models" / "dbt_charts" / "exposures.yml").read_text()
    )
    for name in ("dbt_yml_files-latest.json", "dbt_yml_files-latest-fusion.json"):
        schema = json.loads((_SCHEMAS / name).read_text())
        validator_for(schema)(schema).validate(doc)
    return doc["exposures"]


def test_one_exposure_per_board_with_refs_and_sources(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "finance/sales.yml": _board(
                {
                    "o": "SELECT * FROM {{ ref('fct_orders') }}",
                    "p": "SELECT * FROM {{ source('stripe', 'payments') }}"
                    " JOIN {{ ref('dim_customers') }} USING (id)",
                },
                extra="notes: Weekly sales\ntags: [sales]",
            ),
        },
    )

    result = emit_exposures(project, check=False)

    assert result.changed
    assert _emitted(tmp_path) == [
        {
            "name": "finance__sales",
            "label": "Sales",
            "type": "dashboard",
            "owner": {"name": "Analytics", "email": "analytics@example.com"},
            "description": "Weekly sales",
            "depends_on": [
                "ref('dim_customers')",
                "ref('fct_orders')",
                "source('stripe', 'payments')",
            ],
            "config": {
                "tags": ["sales"],
                "meta": {
                    "dbt_charts": {
                        "board": "charts/finance/sales.yml",
                        "queries": {
                            "o": ["ref('fct_orders')"],
                            "p": [
                                "ref('dim_customers')",
                                "source('stripe', 'payments')",
                            ],
                        },
                    }
                },
            },
        }
    ]


def test_board_without_dbt_calls_is_skipped_and_reported(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "raw.yml": _board({"o": "SELECT * FROM orders"}),
            "dbt.yml": _board({"o": "SELECT * FROM {{ ref('fct_orders') }}"}),
        },
    )

    result = emit_exposures(project, check=False)

    assert [e.name for e in result.exposures] == ["dbt"]
    assert [s.board for s in result.skipped] == ["charts/raw.yml"]


def test_package_board_name_carries_package_namespace(tmp_path: Path) -> None:
    project = _project(tmp_path, {})
    pkg = tmp_path / "dbt_packages" / "pkg_a" / "charts"
    pkg.mkdir(parents=True)
    (pkg / "overview.yml").write_text(_board({"o": "SELECT * FROM {{ ref('m') }}"}))

    result = emit_exposures(project, check=False)

    assert [e.name for e in result.exposures] == ["pkg_a__overview"]


def test_missing_owner_config_is_an_error(tmp_path: Path) -> None:
    config = "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    project = _project(tmp_path, {"a.yml": _board({"o": "SELECT 1"})}, config)

    with pytest.raises(ExposuresError, match="exposures.owner"):
        emit_exposures(project, check=False)


def test_owner_needs_name_or_email(tmp_path: Path) -> None:
    config = _CONFIG.replace(
        "    name: Analytics\n    email: analytics@example.com\n", "    {}\n"
    )
    project = _project(tmp_path, {"a.yml": _board({"o": "SELECT 1"})}, config)

    with pytest.raises(ValidationError, match="name or email"):
        emit_exposures(project, check=False)


def test_board_that_fails_to_compile_is_an_error(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "ok.yml": _board({"o": "SELECT * FROM {{ ref('m') }}"}),
            "broken.yml": "title: [unclosed\n",
        },
    )

    with pytest.raises(ExposuresError, match="charts/broken.yml"):
        emit_exposures(project, check=False)
    assert not (tmp_path / "models").exists()


def test_unresolvable_dbt_call_is_an_error(tmp_path: Path) -> None:
    project = _project(
        tmp_path, {"a.yml": _board({"o": "SELECT * FROM {{ ref('pkg', 'm') }}"})}
    )

    with pytest.raises(ExposuresError, match="ref\\('pkg', 'm'\\)"):
        emit_exposures(project, check=False)


def test_name_collision_is_an_error(tmp_path: Path) -> None:
    sql = {"o": "SELECT * FROM {{ ref('m') }}"}
    project = _project(tmp_path, {"a-b.yml": _board(sql), "a_b.yml": _board(sql)})

    with pytest.raises(ExposuresError, match="a_b"):
        emit_exposures(project, check=False)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("target/exposures.yml", "model-paths"),
        ("models/../target/exposures.yml", "model-paths"),
        ("models/exposures.txt", "YAML"),
        ("models/orders.sql", "YAML"),
    ],
)
def test_invalid_output_path_preserves_existing_file(
    tmp_path: Path, path: str, message: str
) -> None:
    config = _CONFIG + f"  path: {path}\n"
    project = _project(
        tmp_path, {"a.yml": _board({"o": "SELECT * FROM {{ ref('m') }}"})}, config
    )
    destination = tmp_path / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    original = "select 1 as order_id\n"
    destination.write_text(original)

    with pytest.raises(ExposuresError, match=message):
        emit_exposures(project, check=False)
    assert destination.read_text() == original


@pytest.mark.parametrize(
    ("model_path", "output"),
    [
        ("models", "models/nested/../exposures.yml"),
        ("models", "models/exposures.yaml"),
        ("models/../dbt_models", "dbt_models/exposures.yml"),
        (".", "exposures.yml"),
    ],
)
def test_normalized_output_under_model_paths_is_written(
    tmp_path: Path, model_path: str, output: str
) -> None:
    project = _project(
        tmp_path,
        {"a.yml": _board({"o": "SELECT * FROM {{ ref('m') }}"})},
        _CONFIG + f"  path: {output}\n",
    )
    (tmp_path / "dbt_project.yml").write_text(
        f"name: shop\nmodel-paths: [{model_path}]\n"
    )

    assert emit_exposures(project, check=False).changed
    assert yaml.safe_load((tmp_path / output).resolve().read_text())["exposures"]
    assert not emit_exposures(project, check=True).changed


def test_check_reports_drift_without_writing(tmp_path: Path) -> None:
    project = _project(
        tmp_path, {"a.yml": _board({"o": "SELECT * FROM {{ ref('m') }}"})}
    )

    assert emit_exposures(project, check=True).changed
    assert not (tmp_path / "models").exists()

    emit_exposures(project, check=False)
    assert not emit_exposures(project, check=True).changed
    assert not emit_exposures(project, check=False).changed


@pytest.mark.parametrize(
    "owner",
    [
        "    name: Analytics\n",
        "    email: analytics@example.com\n",
        "    name: Analytics\n    email: analytics@example.com\n",
    ],
)
def test_output_passes_core_and_fusion_schemas(tmp_path: Path, owner: str) -> None:
    config = _CONFIG.replace(
        "    name: Analytics\n    email: analytics@example.com\n", owner
    )
    project = _project(
        tmp_path,
        {
            "finance/sales.yml": _board(
                {
                    "o": "SELECT * FROM {{ ref('fct_orders') }}",
                    "p": "SELECT * FROM {{ source('stripe', 'payments') }}",
                },
                extra="notes: Weekly sales\ntags: [sales, finance]",
            ),
            "bare.yml": _board({"o": "SELECT * FROM {{ ref('m') }}"}),
        },
        config,
    )

    emit_exposures(project, check=False)

    assert len(_emitted(tmp_path)) == 2


def test_untitled_board_omits_label(tmp_path: Path) -> None:
    board = _board({"o": "SELECT * FROM {{ ref('m') }}"}).replace("title: Sales\n", "")
    project = _project(tmp_path, {"a.yml": board})

    emit_exposures(project, check=False)

    assert "label" not in _emitted(tmp_path)[0]

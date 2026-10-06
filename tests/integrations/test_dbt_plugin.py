"""Tests for dbt_charts.integrations.dbt_plugin's ChartsPlugin."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from dbt_charts.core.dbt_manifest import LoadedManifest, ref_index

_DBT = str(Path(sys.executable).parent / "dbt")


def _write_project(root: Path) -> Path:
    files = {
        "dbt_project.yml": (
            "name: plugin_probe\n"
            "version: '1.0.0'\n"
            "profile: plugin_probe\n"
            "models:\n"
            "  plugin_probe:\n"
            "    marts:\n"
            "      +schema: marts\n"
        ),
        "profiles.yml": (
            "plugin_probe:\n"
            "  target: dev\n"
            "  outputs:\n"
            "    dev:\n"
            "      type: duckdb\n"
            f"      path: {root / 'probe.duckdb'}\n"
            "      schema: main\n"
        ),
        "models/staging/stg_orders.sql": "select * from {{ source('shop', 'orders') }}\n",
        "models/marts/fct_orders.sql": (
            "{{ config(alias='orders_fact') }}\nselect * from {{ ref('stg_orders') }}\n"
        ),
        "models/sources.yml": (
            "version: 2\n"
            "sources:\n"
            "  - name: shop\n"
            "    schema: raw_shop\n"
            "    tables:\n"
            "      - name: orders\n"
        ),
    }
    for relpath, content in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _dbt_parse(project: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    # Subprocess, not dbtRunner: dbt's flags are process-global state.
    return subprocess.run(
        [
            _DBT,
            "parse",
            "--project-dir",
            str(project),
            "--profiles-dir",
            str(project),
            "--no-send-anonymous-usage-stats",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_dbt_discovers_charts_plugin() -> None:
    from dbt.plugins.manager import PluginManager

    from dbt_charts.integrations.dbt_plugin import ChartsPlugin

    module = PluginManager.get_prefixed_modules()["dbt_charts"]

    assert ChartsPlugin in getattr(module, PluginManager.PLUGIN_ATTR_NAME)


def test_import_dbt_charts_does_not_import_dbt_plugin_api() -> None:
    probe = (
        "import sys, dbt_charts; "
        "assert 'dbt.plugins.manager' not in sys.modules, 'plugins import is eager'"
    )

    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_parse_writes_the_ref_index_of_its_manifest(tmp_path: Path) -> None:
    project = _write_project(tmp_path)

    result = _dbt_parse(project)

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((project / "target/manifest.json").read_text())
    index = ref_index(LoadedManifest(raw=manifest, relpath="", version=""))
    minimani = json.loads((project / "target/minimani.json").read_text())
    assert minimani == {
        "minimani_version": 1,
        "refs": {
            name: {"relation": relation, "schema": schema}
            for name, (relation, schema) in index.refs.items()
        },
        "sources": {
            "shop": {
                "orders": {
                    "relation": index.sources[("shop", "orders")][0],
                    "schema": "raw_shop",
                },
            },
        },
    }
    assert minimani["refs"]["fct_orders"] == {
        "relation": '"probe"."main_marts"."orders_fact"',
        "schema": "main_marts",
    }


def test_two_parses_write_identical_bytes(tmp_path: Path) -> None:
    project = _write_project(tmp_path)

    assert _dbt_parse(project).returncode == 0
    first = (project / "target/minimani.json").read_bytes()
    assert _dbt_parse(project, "--no-partial-parse").returncode == 0

    assert (project / "target/minimani.json").read_bytes() == first


def test_target_path_flag_moves_minimani_next_to_manifest(tmp_path: Path) -> None:
    project = _write_project(tmp_path)

    result = _dbt_parse(project, "--target-path", "build/dbt")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "build/dbt/manifest.json").exists()
    assert (project / "build/dbt/minimani.json").exists()
    assert not (project / "target/minimani.json").exists()


def test_unusable_target_path_warns_and_parse_succeeds(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    absolute_target = tmp_path / "abs-target"

    result = _dbt_parse(project, "--target-path", str(absolute_target))

    assert result.returncode == 0, result.stdout + result.stderr
    assert (absolute_target / "manifest.json").exists()
    assert not (absolute_target / "minimani.json").exists()
    assert "dbt Charts could not write minimani.json" in result.stdout


def test_unwritable_minimani_warns_and_parse_succeeds(tmp_path: Path) -> None:
    project = _write_project(tmp_path)
    (project / "target/minimani.json").mkdir(parents=True)

    result = _dbt_parse(project)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (project / "target/manifest.json").exists()
    assert "dbt Charts could not write minimani.json" in result.stdout

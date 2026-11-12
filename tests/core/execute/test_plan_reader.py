"""Read a ``QueryPlan``: its variable values, source resolution and clock."""

from dataclasses import replace
from datetime import date
from pathlib import Path

import duckdb
import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.source import (
    CsvSourceConfig,
    DbtTargetSourceConfig,
    DuckDBSourceConfig,
)
from dbt_charts.core.compile.template.daterange import BoardClock
from dbt_charts.core.diagnostics.base import DbtChartsError
from dbt_charts.core.execute.env import ExecEnv, make_exec_env
from dbt_charts.core.execute.plan_reader import (
    board_clock,
    plan_variables,
    resolve_planned_source,
)
from dbt_charts.core.execute.source_resolver import DbtContext, DefaultSourceResolver

from ..._plans import plan_of, sql_entry
from ..._scripted_env import ScriptedBackend, scripted_env


def test_a_multiselect_and_a_daterange_decode_to_lists() -> None:
    plan = plan_of(
        {},
        variables={
            "regions": {
                "kind": "multiselect",
                "values": [{"kind": "text", "value": "a"}, {"kind": "unset"}],
            },
            "span": {
                "kind": "daterange",
                "start": {"kind": "date", "value": "2026-01-01"},
                "end": {"kind": "unset"},
            },
            "gone": {"kind": "unset"},
        },
    )

    assert plan_variables(plan) == {
        "regions": ["a", None],
        "span": [date(2026, 1, 1), None],
        "gone": None,
    }


def default_resolver_env() -> ExecEnv:
    return replace(scripted_env(ScriptedBackend()), resolver=DefaultSourceResolver())


def test_a_default_resolver_takes_the_inline_entry_over_the_projects() -> None:
    env = default_resolver_env()
    plan = plan_of(
        {"q": sql_entry("SELECT n FROM x", source="data/x.csv")},
        project={"data/x.csv": {"type": "duckdb", "path": "p.duckdb"}},
        inline={"data/x.csv": {"type": "csv", "files": {"x": "data/x.csv"}}},
    )

    source = resolve_planned_source(plan, "q", env)

    assert isinstance(source, CsvSourceConfig)
    assert source.files == {"x": "data/x.csv"}


def test_an_inline_file_source_gets_its_models_defaults() -> None:
    env = default_resolver_env()
    plan = plan_of(
        {"q": sql_entry("SELECT n FROM x", source="data/x.csv")},
        inline={"data/x.csv": {"type": "csv", "files": {"x": "data/x.csv"}}},
    )

    source = resolve_planned_source(plan, "q", env)

    assert isinstance(source, CsvSourceConfig)
    assert source.delimiter == ","


def test_an_unknown_source_is_the_resolvers_refusal() -> None:
    env = default_resolver_env()
    plan = plan_of(
        {"q": sql_entry("SELECT 1", source="nope")},
        project={"db": {"type": "duckdb", "path": ":memory:"}},
    )

    with pytest.raises(DbtChartsError, match="nope"):
        resolve_planned_source(plan, "q", env)


def shop_env(root: Path) -> ExecEnv:
    """A dbt project that declares no ``sources:``, so an unknown source name defers to it."""
    root.mkdir()
    (root / "dbt_project.yml").write_text("name: shop\nprofile: shop_profile\n")
    (root / "profiles.yml").write_text(
        yaml.safe_dump(
            {
                "shop_profile": {
                    "target": "dev",
                    "outputs": {
                        "dev": {
                            "type": "duckdb",
                            "path": str(root / "warehouse.duckdb"),
                            "threads": 1,
                        }
                    },
                }
            }
        )
    )
    return make_exec_env(
        project=FilesystemProject(root),
        dbt_context=DbtContext(dbt_project_path=root, target=None),
    )


@pytest.fixture
def isolated_profile_search(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in ("DBT_PROFILES_DIR", "DBT_PROFILE", "DBT_TARGET"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.usefixtures("isolated_profile_search")
def test_a_dbt_ref_inside_an_embedded_query_resolves_to_the_dbt_target(
    tmp_path: Path,
) -> None:
    plan = plan_of(
        {
            "base": sql_entry(
                "select * from {{ ref('dim_makers') }}", source="unknown"
            ),
            "agg": sql_entry(
                "select * from ({{ queries.base }}) t",
                source="unknown",
                depends_on=["base"],
            ),
        }
    )

    resolved = resolve_planned_source(plan, "agg", shop_env(tmp_path / "shop"))

    assert isinstance(resolved, DbtTargetSourceConfig)


@pytest.mark.usefixtures("isolated_profile_search")
def test_a_chain_with_no_jinja_resolves_to_local_duckdb(tmp_path: Path) -> None:
    plan = plan_of(
        {
            "base": sql_entry("select 1 as n", source="unknown"),
            "agg": sql_entry(
                "select n from ({{ queries.base }}) t",
                source="unknown",
                depends_on=["base"],
            ),
        }
    )

    root = tmp_path / "shop"
    env = shop_env(root)
    (root / "data").mkdir()
    duckdb.connect(str(root / "data" / "dev.duckdb")).close()

    resolved = resolve_planned_source(plan, "agg", env)

    assert isinstance(resolved, DuckDBSourceConfig)
    assert not isinstance(resolved, DbtTargetSourceConfig)


def test_an_outer_entry_on_an_inline_file_source_resolves_to_it() -> None:
    plan = plan_of(
        {
            "base": sql_entry("select n from x", source="data/x.csv"),
            "agg": sql_entry(
                "select n from {{ queries.base }}",
                source="data/x.csv",
                depends_on=["base"],
            ),
        },
        inline={"data/x.csv": {"type": "csv", "files": {"x": "data/x.csv"}}},
    )

    assert isinstance(
        resolve_planned_source(plan, "agg", default_resolver_env()), CsvSourceConfig
    )


def test_the_board_clock_is_read_off_the_plan_header() -> None:
    plan = plan_of({})
    plan["today"] = "2026-11-10"
    plan["week_start"] = 1
    plan["fiscal_year_start_month"] = 7

    assert board_clock(plan) == BoardClock(date(2026, 11, 10), 7, 1, "America/New_York")


def test_a_host_builtin_the_plan_echoes_untagged_passes_through() -> None:
    plan = plan_of({}, variables={"dir": "a/b", "depth": 2})

    assert plan_variables(plan) == {"dir": "a/b", "depth": 2}

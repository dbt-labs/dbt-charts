"""Compose, translate and run one query of a ``QueryPlan`` by name."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.models.source import CsvSourceConfig, DuckDBSourceConfig
from dbt_charts.core.compile.template.daterange import BoardClock, board_clock
from dbt_charts.core.diagnostics.codes_execute import ERR_MUTATING_SQL
from dbt_charts.core.execute.compose import ResolvedQuery
from dbt_charts.core.execute.dbt_utils import DbtRefResolver
from dbt_charts.core.execute.file_source_materializer import FileSourceMaterializer
from dbt_charts.core.execute.planned import compose_query, translate_query
from dbt_charts.core.execute.query_plan import QueryPlan
from dbt_charts.core.execute.query_result import QueryResult
from dbt_charts.core.execute.run import run_planned

from ..._plans import CACHE, plan_of, sql_entry
from ..._scripted_env import ScriptedBackend, backend_of, scripted_env


@contextmanager
def clock() -> Generator[None]:
    with board_clock(BoardClock(date(2026, 11, 10), 1, 1, "America/New_York")):
        yield


def refs() -> MagicMock:
    resolver = MagicMock(spec=DbtRefResolver)
    resolver.resolve.side_effect = lambda sql, target_path, *args: (sql, [])
    return resolver


def compose(plan: QueryPlan, name: str) -> ResolvedQuery:
    with clock():
        composed = compose_query(
            plan,
            name,
            source_config=DuckDBSourceConfig(type="duckdb"),
            refs=refs(),
            project=MagicMock(),
        )
    assert not isinstance(composed, QueryResult), composed.error
    return composed


def test_compose_expands_embedded_queries_from_the_plan() -> None:
    plan = plan_of(
        {
            "base": sql_entry("SELECT n FROM t"),
            "top": sql_entry("SELECT n FROM {{ queries.base }}", depends_on=["base"]),
        }
    )

    assert compose(plan, "top").sql == "SELECT n FROM (SELECT n FROM t) AS base"


def test_compose_binds_the_plans_query_variables() -> None:
    plan = plan_of(
        {"q": sql_entry("SELECT * FROM t WHERE region = {{ region }} AND d > {{ d }}")},
        variables={
            "region": {"kind": "text", "value": "west"},
            "d": {"kind": "date", "value": "2026-01-02"},
        },
    )

    composed = compose(plan, "q")

    assert composed.params == ("west", date(2026, 1, 2))


def test_compose_chains_the_setup_sql_of_embedded_queries_first() -> None:
    plan = plan_of(
        {
            "base": sql_entry(
                "SELECT 1 AS n", setup_sql="CREATE MACRO inc(x) AS x + 1;"
            ),
            "top": sql_entry(
                "SELECT inc(n) FROM {{ queries.base }}",
                depends_on=["base"],
                setup_sql="CREATE MACRO dbl(x) AS x * 2;",
            ),
        }
    )

    assert compose(plan, "top").setup_sql == (
        "CREATE MACRO inc(x) AS x + 1;\nCREATE MACRO dbl(x) AS x * 2;",
    )


def test_compose_runs_in_the_plans_time_zone() -> None:
    assert compose(plan_of({"q": sql_entry("SELECT 1")}), "q").time_zone == (
        "America/New_York"
    )


def test_compose_refuses_a_query_the_plan_does_not_hold() -> None:
    with pytest.raises(KeyError, match="nope"):
        compose(plan_of({"q": sql_entry("SELECT 1")}), "nope")


def test_compose_refuses_a_values_query() -> None:
    plan = plan_of(
        {
            "v": {
                "type": "values",
                "source": None,
                "cache": CACHE,
                "formats": {},
                "depends_on": [],
                "spec": {"rows": [{"n": 1}]},
            }
        }
    )
    with clock():
        composed = compose_query(
            plan,
            "v",
            source_config=DuckDBSourceConfig(type="duckdb"),
            refs=refs(),
            project=MagicMock(),
        )

    assert isinstance(composed, QueryResult)
    assert "Expected SQL query, got values" in str(composed.error)


def test_compose_guards_mutating_sql() -> None:
    with clock():
        composed = compose_query(
            plan_of({"q": sql_entry("DROP TABLE t")}),
            "q",
            source_config=DuckDBSourceConfig(type="duckdb"),
            refs=refs(),
            project=MagicMock(),
        )

    assert isinstance(composed, QueryResult)
    assert composed.error is not None
    assert composed.error.code is ERR_MUTATING_SQL


def test_translate_inlines_values_into_the_target_dialect() -> None:
    plan = plan_of(
        {"q": sql_entry("SELECT {{ n }} AS n", dialect="postgres")},
        variables={"n": {"kind": "int", "value": 3}},
    )
    with clock():
        translated = translate_query(
            plan,
            "q",
            source_config=DuckDBSourceConfig(type="duckdb"),
            refs=refs(),
            project=MagicMock(),
        )

    assert isinstance(translated, ResolvedQuery)
    assert translated.params == ()
    assert translated.sql == "SELECT 3 AS n"


def test_run_planned_sends_the_composed_sql_and_applies_rewrite() -> None:
    env = scripted_env(ScriptedBackend([{"n": 1}]))
    plan = plan_of(
        {"q": sql_entry("SELECT n FROM t WHERE r = {{ r }}")},
        variables={"r": {"kind": "text", "value": "w"}},
    )
    with clock():
        result = run_planned(
            env,
            plan,
            "q",
            source_config=DuckDBSourceConfig(type="duckdb"),
            rewrite=lambda sql: f"EXPLAIN {sql}",
        )

    assert result.data == [{"n": 1}]
    sent = backend_of(env).execute.call_args.args[1]
    assert sent.sql.startswith("EXPLAIN SELECT n FROM t WHERE r = ")
    assert sent.params == ("w",)


def test_run_planned_answers_a_values_query_in_process() -> None:
    env = scripted_env(ScriptedBackend())
    plan = plan_of(
        {
            "v": {
                "type": "values",
                "source": None,
                "cache": CACHE,
                "formats": {},
                "depends_on": [],
                "spec": {"rows": [{"n": 1}, {"n": 2}]},
            }
        }
    )

    result = run_planned(env, plan, "v", source_config=None)

    assert result.data == [{"n": 1}, {"n": 2}]
    backend_of(env).execute.assert_not_called()


def test_a_three_hop_chain_expands_all_the_way_down() -> None:
    plan = plan_of(
        {
            "base": sql_entry("SELECT id, revenue FROM orders"),
            "mid": sql_entry(
                "SELECT id, revenue FROM {{ queries.base }}", depends_on=["base"]
            ),
            "top": sql_entry(
                "SELECT id, revenue FROM {{ queries.mid }}", depends_on=["mid"]
            ),
        }
    )

    sql = compose(plan, "top").sql

    assert "queries" not in sql
    assert "FROM orders" in sql


def test_a_diamond_expands_the_shared_base_under_both_mids() -> None:
    plan = plan_of(
        {
            "base": sql_entry("SELECT 1 AS n"),
            "a": sql_entry("SELECT n FROM {{ queries.base }}", depends_on=["base"]),
            "b": sql_entry("SELECT n FROM {{ queries.base }}", depends_on=["base"]),
            "top": sql_entry(
                "SELECT * FROM {{ queries.a }} JOIN {{ queries.b }} USING (n)",
                depends_on=["a", "b"],
            ),
        }
    )

    sql = compose(plan, "top").sql

    assert "queries" not in sql
    assert sql.count("SELECT 1 AS n") == 2


def test_a_chain_carries_the_setup_sql_of_the_deepest_query() -> None:
    plan = plan_of(
        {
            "base": sql_entry(
                "SELECT inc(1) AS n", setup_sql="CREATE MACRO inc(x) AS x + 1;"
            ),
            "mid": sql_entry("SELECT n FROM {{ queries.base }}", depends_on=["base"]),
            "top": sql_entry("SELECT n FROM {{ queries.mid }}", depends_on=["mid"]),
        }
    )

    composed = compose(plan, "top")

    assert "queries" not in composed.sql
    assert composed.setup_sql == ("CREATE MACRO inc(x) AS x + 1;",)


def test_a_cache_reference_is_left_for_the_composer() -> None:
    plan = plan_of(
        {
            "base": sql_entry("SELECT 1 AS n"),
            "top": sql_entry(
                "SELECT n FROM {{ queries.base.cache }}", depends_on=["base"]
            ),
        }
    )
    with clock():
        composed = compose_query(
            plan,
            "top",
            source_config=DuckDBSourceConfig(type="duckdb"),
            refs=refs(),
            project=MagicMock(),
        )

    assert isinstance(composed, ResolvedQuery), composed.error
    assert composed.sql == "SELECT n FROM __dct_cache_ref__base__"


CSV = CsvSourceConfig(type="csv", files={"x": "data/x.csv"})


def test_a_file_source_composes_in_duckdb_with_chain_and_variables() -> None:
    plan = plan_of(
        {
            "base": sql_entry("SELECT n FROM x", source="x"),
            "top": sql_entry(
                "SELECT n FROM {{ queries.base }} WHERE n > {{ min }}",
                source="x",
                depends_on=["base"],
            ),
        },
        variables={"min": {"kind": "int", "value": 2}},
    )
    with clock():
        composed = compose_query(
            plan, "top", source_config=CSV, refs=refs(), project=MagicMock()
        )

    assert isinstance(composed, ResolvedQuery), composed
    assert "queries" not in composed.sql
    assert composed.inline("duckdb").endswith("WHERE n > 2")


def test_translate_on_a_file_source_refuses_a_differing_dialect() -> None:
    plan = plan_of({"q": sql_entry("SELECT 1", source="x", dialect="postgres")})
    with clock():
        translated = translate_query(
            plan, "q", source_config=CSV, refs=refs(), project=MagicMock()
        )

    assert isinstance(translated, QueryResult)
    assert "not translated" in str(translated.error)


def test_translate_on_a_file_source_keeps_duckdb_sql() -> None:
    plan = plan_of({"q": sql_entry("SELECT n FROM x", source="x", dialect="duckdb")})
    with clock():
        translated = translate_query(
            plan, "q", source_config=CSV, refs=refs(), project=MagicMock()
        )

    assert isinstance(translated, ResolvedQuery), translated
    assert translated.sql == "SELECT n FROM x"
    assert translated.params == ()


def test_run_planned_runs_a_file_source_through_the_materializer(
    tmp_path: Path,
) -> None:
    (tmp_path / "x.csv").write_text("n\n1\n2\n3\n")
    backend = ScriptedBackend()
    project = FilesystemProject(tmp_path)
    env = scripted_env(
        backend, project=project, file_materializer=FileSourceMaterializer(project)
    )
    plan = plan_of(
        {"q": sql_entry("SELECT n FROM x WHERE n > {{ lo }}", source="x")},
        variables={"lo": {"kind": "int", "value": 1}},
    )
    with clock():
        result = run_planned(
            env,
            plan,
            "q",
            source_config=CsvSourceConfig(type="csv", files={"x": "x.csv"}),
            rewrite=lambda sql: f"{sql} ORDER BY n DESC",
        )

    assert result.error is None, result.error
    assert result.data == [{"n": 3}, {"n": 2}]
    backend.execute.assert_not_called()


def test_run_planned_refuses_a_differing_dialect_on_a_file_source() -> None:
    plan = plan_of({"q": sql_entry("SELECT 1", source="x", dialect="postgres")})
    with clock():
        result = run_planned(
            scripted_env(ScriptedBackend()), plan, "q", source_config=CSV
        )

    assert "not translated" in str(result.error)


def test_run_planned_without_a_source_is_the_source_not_named_error() -> None:
    plan = plan_of({"q": sql_entry("SELECT 1")})

    result = run_planned(scripted_env(ScriptedBackend()), plan, "q", source_config=None)

    assert result.error is not None
    assert result.data == []


def test_run_planned_refuses_a_query_the_plan_does_not_hold() -> None:
    with pytest.raises(KeyError, match="nope"):
        run_planned(
            scripted_env(ScriptedBackend()),
            plan_of({}),
            "nope",
            source_config=None,
        )


def test_run_planned_answers_a_schema_query_from_the_sources_metadata() -> None:
    env = scripted_env(ScriptedBackend())
    env.sources.register("wh", {"type": "duckdb", "path": ":memory:"})
    plan = plan_of(
        {
            "s": {
                "type": "schema",
                "source": None,
                "cache": CACHE,
                "formats": {},
                "depends_on": [],
                "spec": {},
            }
        }
    )

    result = run_planned(env, plan, "s", source_config=None)

    assert result.error is None
    assert "wh" in [row["name"] for row in result.data]


def test_run_planned_refuses_schema_introspection_on_a_file_source() -> None:
    plan = plan_of(
        {
            "s": {
                "type": "schema",
                "source": "x",
                "cache": CACHE,
                "formats": {},
                "depends_on": [],
                "spec": {"source": "x"},
            }
        }
    )

    result = run_planned(scripted_env(ScriptedBackend()), plan, "s", source_config=CSV)

    assert "Schema introspection" in str(result.error)

"""`_engine.plan` reads a compiled board's queries and variable values as versioned JSON."""

import json

import pytest

from dbt_charts import _engine
from dbt_charts.core.compile import sql_analysis
from dbt_charts.core.execute.query_plan import QueryPlan, parse_query_plan

NOW = 1_770_000_000
BOARD = """
variables:
  region:
    input: select
    options:
      static: [east, west]
    default: east
queries:
  q:
    rows:
      - n: 1
charts:
  k:
    type: kpi
    query: q
    value: n
rows:
  - k
"""


class Files:
    def read(self, path: str) -> str | None:
        return BOARD if path == "a.yml" else None

    def list(self, dir: str) -> list[str]:
        return []

    def exists(self, path: str) -> bool:
        return path == "a.yml"


def planned(
    given: dict[str, object],
    all_queries: bool,
    sources: dict[str, str] | None = None,
) -> QueryPlan:
    board = _engine.compile(
        Files(),
        "a.yml",
        boards_root=".",
        today="2026-11-06",
        default_theme="clarity",
        html_policy_ceiling=None,
        sources=sources or {},
        implicit_source=None,
    )
    return parse_query_plan(
        _engine.plan(board, json.dumps(given), NOW, "UTC", all_queries)
    )


def test_a_plan_carries_the_queries_and_the_values_a_run_binds() -> None:
    plan = planned({"region": "west"}, False)
    assert plan["version"] == 1
    assert plan["queries"]["q"]["type"] == "values"
    assert plan["queries"]["q"]["spec"] == {"rows": [{"n": 1}]}
    assert plan["variables"]["query"]["region"] == {"kind": "text", "value": "west"}


def test_a_plan_carries_the_project_declarations_it_was_compiled_against() -> None:
    declaration = {"type": "duckdb", "path": "w.duckdb"}
    plan = planned({}, False, {"db": json.dumps(declaration)})
    assert plan["sources"] == {"project": {"db": declaration}, "inline": {}}


class Sqlglot:
    def analyze_all(self, requests: list[dict[str, str | None]]) -> list[dict]:
        return sql_analysis.analyze_all(requests, {})


class FileSourceFiles(Files):
    def read(self, path: str) -> str | None:
        return (
            "queries:\n  q:\n    source: data/x.csv\n    sql: select n from x\n"
            "charts:\n  k:\n    type: kpi\n    query: q\n    value: n\nrows:\n  - k\n"
            if path == "a.yml"
            else None
        )


def test_a_plan_carries_an_inline_file_source() -> None:
    board = _engine.compile(
        FileSourceFiles(),
        "a.yml",
        boards_root=".",
        today="2026-11-06",
        default_theme="clarity",
        html_policy_ceiling=None,
        sources={},
        implicit_source=None,
        sql=Sqlglot(),
    )
    plan = parse_query_plan(_engine.plan(board, "{}", NOW, "UTC", False))
    assert plan["sources"]["inline"] == {
        "data/x.csv": {"type": "csv", "files": {"x": "data/x.csv"}}
    }


def test_a_declaration_that_is_not_json_is_refused() -> None:
    with pytest.raises(ValueError, match="source db"):
        planned({}, False, {"db": "{nope"})


def test_a_value_the_board_refuses_raises_with_its_code() -> None:
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        planned({"nope": 1}, False)
    assert raised.value.code == "ERR-UNKNOWN-VARIABLE-OVERRIDE"


def test_a_reader_refuses_another_version() -> None:
    with pytest.raises(ValueError, match="version 2"):
        parse_query_plan('{"version": 2}')


def test_the_header_and_each_query_carry_the_board_zone_and_formats() -> None:
    plan = planned({}, False)
    assert plan["time_zone"] == "UTC"
    assert plan["board_id"] == "untitled_board"
    assert plan["queries"]["q"]["formats"] == {}

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


def compiled(
    files: Files, sources: dict[str, str] | None = None
) -> _engine.CompiledBoard:
    return _engine.compile(
        files,
        "a.yml",
        boards_root=".",
        today="2026-11-06",
        default_theme="clarity",
        html_policy_ceiling=None,
        sources=sources or {},
        cache_root=(True, "24h", False),
        source_caches={},
        implicit_source=None,
    )


def run_plan(
    board: _engine.CompiledBoard,
    given: dict[str, object] | None = None,
    *,
    builtins: dict[str, object] | None = None,
    now: int = NOW,
    all_queries: bool = False,
    active_tab_only: bool = False,
    strict: bool = True,
    only_charts: list[str] | None = None,
) -> QueryPlan:
    return parse_query_plan(
        _engine.plan(
            board,
            json.dumps(given or {}),
            json.dumps(builtins or {}),
            now,
            all_queries,
            active_tab_only,
            strict,
            only_charts,
        )
    )


def planned(
    given: dict[str, object],
    all_queries: bool,
    sources: dict[str, str] | None = None,
) -> QueryPlan:
    return run_plan(compiled(Files(), sources), given, all_queries=all_queries)


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
        cache_root=(True, "24h", False),
        source_caches={},
        sql=Sqlglot(),
    )
    plan = run_plan(board)
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


TABBED = """
queries:
  a:
    rows:
      - n: 1
  b:
    rows:
      - n: 2
    cache:
      ttl: 5m
      prefetch: true
charts:
  ka:
    type: kpi
    query: a
    value: n
  kb:
    type: kpi
    query: b
    value: n
tabs:
  items:
    - title: One
      rows: [ka]
    - title: Two
      rows: [kb]
"""


class TabbedFiles(Files):
    def read(self, path: str) -> str | None:
        return TABBED if path == "a.yml" else None


def test_an_active_tab_plan_reads_the_open_tab_and_lists_the_hidden_prefetch() -> None:
    board = compiled(TabbedFiles())
    scoped = run_plan(board, active_tab_only=True)
    assert list(scoped["queries"]) == ["a"]
    assert scoped["prefetch"] == ["b"]
    every = run_plan(board)
    assert list(every["queries"]) == ["a", "b"]
    assert every["prefetch"] == []


def test_the_header_carries_the_board_clock_in_the_boards_zone() -> None:
    board = compiled(ZonedFiles())
    # 2026-02-02 02:40 UTC is still the 1st in New York.
    plan = run_plan(board, now=NOW)
    assert plan["time_zone"] == "America/New_York"
    assert plan["today"] == "2026-02-01"
    assert run_plan(board, now=NOW + 9_000)["today"] == "2026-02-02"
    assert plan["week_start"] == 0
    assert plan["fiscal_year_start_month"] == 4


class ZonedFiles(Files):
    def read(self, path: str) -> str | None:
        return (
            "regional:\n  time_zone: America/New_York\n  fiscal_year_start_month: 4\n"
            + BOARD
            if path == "a.yml"
            else None
        )


def test_strict_decides_whether_an_undeclared_name_is_refused() -> None:
    board = compiled(Files())
    plan = run_plan(board, {"nope": 1, "region": "west"}, strict=False)
    assert "nope" not in plan["variables"]["query"]
    assert plan["variables"]["query"]["region"] == {"kind": "text", "value": "west"}
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        run_plan(board, {"nope": 1}, strict=True)
    assert raised.value.code == "ERR-UNKNOWN-VARIABLE-OVERRIDE"


def test_builtins_are_echoed_and_cannot_shadow_a_variable() -> None:
    board = compiled(Files())
    plan = run_plan(board, builtins={"this_dir": {"name": "a"}})
    assert plan["variables"]["query"]["this_dir"] == {"name": "a"}
    assert plan["variables"]["render"]["this_dir"] == {"name": "a"}
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        run_plan(board, builtins={"region": "x"})
    assert raised.value.code == "ERR-BUILTIN-SHADOWS-VARIABLE"


def test_a_focused_plan_names_the_charts_and_an_unknown_one_raises() -> None:
    board = compiled(TabbedFiles())
    plan = run_plan(board, only_charts=["kb"], active_tab_only=True)
    assert list(plan["queries"]) == ["b"]
    with pytest.raises(_engine.RenderDiagnosticError) as raised:
        run_plan(board, only_charts=["nope"])
    assert raised.value.code == "ERR-UNKNOWN-CHART"

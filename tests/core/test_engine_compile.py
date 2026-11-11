"""`_engine.compile` holds a board compiled in Rust behind an opaque handle."""

import json

import pytest

from dbt_charts import _engine
from dbt_charts.core.compile import sql_analysis

HOST = {
    "boards_root": ".",
    "today": "2026-11-06",
    "default_theme": "clarity",
    "html_policy_ceiling": None,
    "sources": {"db": "{}"},
    "implicit_source": None,
}


class Files:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read(self, path: str) -> str | None:
        return self.files.get(path)

    def list(self, dir: str) -> list[str]:
        prefix = f"{dir}/" if dir else ""
        return [
            p
            for p in self.files
            if p.startswith(prefix) and "/" not in p[len(prefix) :]
        ]

    def exists(self, path: str) -> bool:
        return path in self.files or any(p.startswith(f"{path}/") for p in self.files)


def test_compile_folds_extends_and_returns_a_handle() -> None:
    files = Files(
        {
            "base.yml": "title: Base\n",
            "a.yml": "extends: ./base.yml\n",
        }
    )
    board = _engine.compile(files, "a.yml", **HOST)
    assert isinstance(board, _engine.CompiledBoard)
    assert board.diagnostics() == []
    index = json.loads(board.index_json())
    assert (index["public_ids"], index["declared_below"]) == ({}, [])
    assert index["title"] == "Base"


def test_a_missing_board_raises_with_its_message() -> None:
    with pytest.raises(_engine.CompileError, match="a.yml does not exist"):
        _engine.compile(Files({}), "a.yml", **HOST)


def test_an_invalid_board_carries_wire_diagnostics() -> None:
    with pytest.raises(_engine.CompileError) as raised:
        _engine.compile(Files({"a.yml": "bogus_key: 1\n"}), "a.yml", **HOST)
    codes = [d["code"] for d in raised.value.diagnostics]
    assert codes and all(c.startswith("ERR-") for c in codes)


def test_the_handle_exposes_no_board_model() -> None:
    board = _engine.compile(Files({"a.yml": "title: A\n"}), "a.yml", **HOST)
    assert sorted(n for n in dir(board) if not n.startswith("_")) == [
        "diagnostics",
        "host_facts_json",
        "index_json",
    ]


def test_a_coded_refusal_outside_invalid_keeps_its_code() -> None:
    with pytest.raises(_engine.CompileError) as raised:
        _engine.compile(
            Files({"a.yml": "title: A\n"}), "a.yml", **{**HOST, "default_theme": "nope"}
        )
    assert [d["code"] for d in raised.value.diagnostics] == [
        "ERR-INVALID-DEFAULT-THEME"
    ]


class Sqlglot:
    """The host's analyzer, as `_engine.compile(sql=...)` takes it."""

    def analyze_all(self, requests: list[dict[str, str | None]]) -> list[dict]:
        return sql_analysis.analyze_all(requests, {})


def sql_board(sql: str) -> Files:
    return Files(
        {
            "a.yml": "\n".join(
                [
                    "variables:",
                    "  region:",
                    "    default: ''",
                    "queries:",
                    "  q:",
                    "    source: db",
                    f"    sql: {json.dumps(sql)}",
                    "charts:",
                    "  t:",
                    "    type: table",
                    "    query: q",
                    "    columns:",
                    "      - a",
                    "rows:",
                    "  - t",
                    "",
                ]
            )
        }
    )


TEMPLATED = "select a from t {% if region %}where region = '{{ region }}'{% endif %}"


def test_a_sql_board_is_refused_without_an_analyzer() -> None:
    with pytest.raises(_engine.CompileError, match="no SQL analyzer"):
        _engine.compile(sql_board(TEMPLATED), "a.yml", **HOST)


def test_the_host_analyzer_compiles_a_templated_query() -> None:
    board = _engine.compile(sql_board(TEMPLATED), "a.yml", **HOST, sql=Sqlglot())
    assert board.diagnostics() == []


def test_the_host_analyzer_refuses_a_mutating_query() -> None:
    with pytest.raises(_engine.CompileError, match="read-only SQL allowlist"):
        _engine.compile(sql_board("drop table t"), "a.yml", **HOST, sql=Sqlglot())


def test_the_host_lints_a_cross_join_and_a_date_turned_into_text() -> None:
    board = """queries:
  q:
    source: db
    dialect: duckdb
    sql: select strftime(d, '%Y-%m') as month, count(*) as n from x, y group by 1
charts:
  c:
    type: bar
    query: q
    x: month
    y: n
rows:
  - c
"""
    compiled = _engine.compile(Files({"a.yml": board}), "a.yml", **HOST, sql=Sqlglot())
    found = {d["code"]: d for d in compiled.diagnostics()}
    assert set(found) == {"WARN-MISSING-JOIN-PREDICATE", "WARN-DATE-AS-TEXT"}
    assert found["WARN-MISSING-JOIN-PREDICATE"]["params"]["severity"] == "error"

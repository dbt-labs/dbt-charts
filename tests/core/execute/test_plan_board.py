"""`plan_board` turns the engine's refusals into the coded errors the host raises."""

from pathlib import Path

import pytest

from dbt_charts import _engine
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.engine import engine_compile
from dbt_charts.core.diagnostics.codes_execute import ERR_UNKNOWN_VARIABLE_OVERRIDE
from dbt_charts.core.diagnostics.execution import ExecutionError
from dbt_charts.core.execute import planned
from dbt_charts.core.execute.plan import utc_now
from dbt_charts.core.execute.planned import plan_board

BOARD = """\
source: wh
variables:
  region:
    input: select
    options:
      static: [east, west]
    default: east
queries:
  q:
    sql: select 1 as a
charts:
  t:
    type: table
    query: q
    columns:
      - a
rows:
  - t
"""


def compiled(tmp_path: Path) -> _engine.CompiledBoard:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n", encoding="utf-8"
    )
    (tmp_path / "a.yml").write_text(BOARD, encoding="utf-8")
    return engine_compile(FilesystemProject(tmp_path).path("a.yml"))


def plan(board: _engine.CompiledBoard, variables: dict[str, str]) -> None:
    plan_board(
        board,
        variables,
        builtins=None,
        now=utc_now(),
        strict=True,
        active_tab_only=False,
        only_charts=None,
    )


def test_a_value_the_engine_refuses_raises_its_coded_error(tmp_path: Path) -> None:
    with pytest.raises(ExecutionError) as raised:
        plan(compiled(tmp_path), {"nope": '"x"'})

    assert raised.value.code is ERR_UNKNOWN_VARIABLE_OVERRIDE


def test_a_refusal_with_a_code_that_is_not_an_error_is_a_type_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Warned(_engine.RenderDiagnosticError):
        code = "WARN-DBT-MODEL-COLUMNS-UNRESOLVED"
        params: dict[str, str] = {}

    def refuse(*_: object) -> str:
        raise Warned("warned")

    monkeypatch.setattr(planned._engine, "plan", refuse)

    with pytest.raises(TypeError, match="not an error code"):
        plan(compiled(tmp_path), {})

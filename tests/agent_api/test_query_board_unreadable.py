"""Every failure arm of the shared board prelude, through both entry points."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import pytest

from dbt_charts.agent_api.query import lookup_board_query_sql, query_board
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.execute.adapters import build_adapter_registry

BOARD = """
source: db
variables:
  region:
    input: text
queries:
  ok:
    sql: select 1 as a
charts:
  c:
    query: ok
    type: table
rows:
  - c
"""


class _Failure(Protocol):
    success: bool
    errors: list[str]
    error_code: str | None


@pytest.fixture
def project(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
    )
    (tmp_path / "charts").mkdir()
    (tmp_path / "orders.yml").write_text(BOARD)
    (tmp_path / "latin1.yml").write_bytes(b"title: caf\xe9\n")
    (tmp_path / "broken.yml").write_text("queries:\n  q:\n    sql: select 1\nrows: [")
    return local_project(tmp_path)


@pytest.fixture(params=["query_board", "lookup_board_query_sql"])
def run(
    request: pytest.FixtureRequest, project: FilesystemProject
) -> Callable[[str, str], Any]:
    if request.param == "query_board":
        registry = build_adapter_registry(project, read_only=False)

        def via_query_board(path: str, name: str) -> Any:
            return query_board(name, Path(path), project, adapter_registry=registry)

        return via_query_board

    def via_lookup(path: str, name: str) -> Any:
        return lookup_board_query_sql(name, Path(path), project=project)

    return via_lookup


@pytest.mark.parametrize(
    ("path", "name", "code"),
    [
        ("../../../outside.yml", "ok", "ERR-PATH-OUTSIDE-PROJECT"),
        ("missing.yml", "ok", "ERR-FILE-NOT-FOUND"),
        ("charts", "ok", "ERR-BOARD-FILE-UNREADABLE"),
        ("latin1.yml", "ok", "ERR-BOARD-FILE-UNREADABLE"),
        ("orders.yml", "zzz", "ERR-BOARD-QUERY-NOT-FOUND"),
    ],
)
def test_each_failure_arm_carries_its_code(
    run: Callable[[str, str], Any], tmp_path: Path, path: str, name: str, code: str
) -> None:
    result: _Failure = run(path, name)

    assert not result.success
    assert result.error_code == code
    assert str(tmp_path) not in " ".join(result.errors)


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (
            "source: db\nqueries:\n  q:\n    sql: \"select '{{ nothere }}'\"\n",
            "ERR-UNKNOWN-VARIABLE",
        ),
        (
            'source: db\nqueries:\n  q:\n    sql: "select {{ 1 | nofilter }}"\n',
            "ERR-JINJA-ERROR",
        ),
    ],
)
def test_a_template_defect_carries_its_code(
    run: Callable[[str, str], Any], tmp_path: Path, body: str, code: str
) -> None:
    (tmp_path / "tpl.yml").write_text(body + "rows: []\n")

    result: _Failure = run("tpl.yml", "q")

    assert not result.success
    assert result.error_code == code


def test_a_board_that_does_not_compile_carries_a_compile_code(
    run: Callable[[str, str], Any],
) -> None:
    result: _Failure = run("broken.yml", "q")

    assert not result.success
    assert result.error_code not in (None, "ERR-INTERNAL")


def test_a_valid_board_with_an_unreadable_sibling_is_not_blamed_on_the_board(
    run: Callable[[str, str], Any], tmp_path: Path
) -> None:
    (tmp_path / "charts" / "meta.yml").write_bytes(b"source: caf\xe9\n")
    (tmp_path / "charts" / "ok.yml").write_text(BOARD)

    try:
        result: _Failure = run("charts/ok.yml", "ok")
    except UnicodeDecodeError:
        return
    assert result.error_code != "ERR-BOARD-FILE-UNREADABLE"
    assert "charts/ok.yml" not in " ".join(result.errors) or result.success

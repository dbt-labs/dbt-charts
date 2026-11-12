"""`validate` reports an undeclared variable in a title or in query SQL, and nothing for a `text:` row."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.agent_api.validate import validate
from dbt_charts.cli.filesystem_project import FilesystemProject

_BOARD = """
queries:
  q:
    columns: [a, b]
    values: [[1, 2]]
{extra_query}charts:
  c:
    type: bar
    query: q
    x: a
    y: b
{title}rows:
{rows}"""


def _board(*, title: str = "", extra_query: str = "", rows: str = "  - c\n") -> str:
    return _BOARD.format(title=title, extra_query=extra_query, rows=rows)


def _errors(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    text: str,
) -> list[tuple[str, str, str, str]]:
    board = tmp_path / "charts" / "b.yaml"
    board.parent.mkdir(parents=True, exist_ok=True)
    board.write_text(text, encoding="utf-8")
    result = validate(board, project=local_project(tmp_path))
    return [
        (
            e.code,
            e.fields["surface"],
            e.fields["owner_name"],
            e.fields["var_name"],
        )
        for e in result.errors
    ]


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param(
            {"title": "    title: \"'{{ nope }}'\"\n"},
            ("Chart", "c", "nope"),
            id="chart-title",
        ),
        pytest.param(
            {"title": '    subtitle: "{{ nope }}"\n'},
            ("Chart", "c", "nope"),
            id="chart-subtitle",
        ),
        pytest.param(
            {
                "extra_query": "  r:\n    sql: SELECT {{ nope }} AS b\n    source: db\n",
            },
            ("Query", "r", "nope"),
            id="query-sql",
        ),
    ],
)
def test_an_undeclared_variable_is_refused(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
    kwargs: dict[str, str],
    expected: tuple[str, str, str],
) -> None:
    found = _errors(tmp_path, local_project, _board(**kwargs))
    assert found == [("ERR-UNKNOWN-VARIABLE", *expected)]


def test_a_text_row_is_not_scanned(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    text = _board(rows='  - text: "{{ nope }}"\n  - c\n')
    assert _errors(tmp_path, local_project, text) == []

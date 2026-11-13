"""`validate` reports every chart that names an undeclared query, not just the first."""

from collections.abc import Callable
from pathlib import Path

from dbt_charts.agent_api.validate import validate_paths
from dbt_charts.cli.filesystem_project import FilesystemProject


def test_each_chart_naming_an_unknown_query_is_reported(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    board = (
        "title: t\nsource: local_db\nqueries:\n  ok:\n    sql: SELECT 1 AS a\ncharts:\n"
        + "".join(
            f"  c{i}:\n    type: bar\n    query: missing_{i}\n    x: a\n    y: a\n"
            for i in range(3)
        )
        + "rows:\n  - c0\n  - c1\n  - c2\n"
    )
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "b.yml").write_text(board)

    [result] = validate_paths([charts / "b.yml"], project=local_project(tmp_path))

    unknown = [e for e in result.errors if e.code == "ERR-UNKNOWN-QUERY"]
    assert [e.message.count("missing_") for e in unknown] == [1, 1, 1]
    assert [f"missing_{i}" in e.message for i, e in enumerate(unknown)] == [True] * 3

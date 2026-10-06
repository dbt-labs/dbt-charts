"""Tests for agent_api/impact.py — the column → boards reverse index."""

from __future__ import annotations

from pathlib import Path

from dbt_charts.agent_api.impact import column_impact
from dbt_charts.cli.filesystem_project import FilesystemProject

_SOURCES = "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"


def _board(queries: dict[str, str]) -> str:
    lines = ["title: B", "source: db", "queries:"]
    for name, sql in queries.items():
        lines.append(f"  {name}: {sql}")
    lines += ["charts:"]
    for name in queries:
        lines += [f"  c_{name}:", f"    query: {name}", "    type: table"]
    lines += ["rows:"] + [f"  - c_{name}" for name in queries]
    return "\n".join(lines) + "\n"


def _project(tmp_path: Path, boards: dict[str, dict[str, str]]) -> FilesystemProject:
    (tmp_path / "dbt_charts.yml").write_text(_SOURCES)
    (tmp_path / "charts").mkdir()
    for relname, queries in boards.items():
        (tmp_path / "charts" / relname).write_text(_board(queries))
    return FilesystemProject(tmp_path)


def test_returns_exactly_the_boards_referencing_the_column(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "sales.yaml": {"o": "SELECT customer_id, amount FROM orders"},
            "users.yaml": {"u": "SELECT name FROM users"},
            "totals.yaml": {"t": "SELECT SUM(amount) AS total FROM orders"},
        },
    )
    result = column_impact(project, column="customer_id")
    hits = {(h.board, h.query) for h in result.hits}
    assert hits == {("charts/sales.yaml", "o")}
    assert result.indeterminate == []


def test_includes_package_boards(tmp_path: Path) -> None:
    """iter_boards() takes every board root by default — a pack's boards
    are in scope for the reverse index too, reported by their real
    dbt_packages/ relpath."""
    project = _project(tmp_path, {"sales.yaml": {"o": "SELECT amount FROM orders"}})
    pkg_charts = tmp_path / "dbt_packages" / "pkg_a" / "charts"
    pkg_charts.mkdir(parents=True)
    (pkg_charts / "overview.yml").write_text(
        _board({"o": "SELECT customer_id FROM orders"})
    )

    result = column_impact(project, column="customer_id")

    hits = {(h.board, h.query) for h in result.hits}
    assert hits == {("dbt_packages/pkg_a/charts/overview.yml", "o")}


def test_table_narrows_a_column_shared_across_tables(tmp_path: Path) -> None:
    project = _project(
        tmp_path,
        {
            "a.yaml": {"q": "SELECT id FROM orders"},
            "b.yaml": {"q": "SELECT id FROM users"},
        },
    )
    result = column_impact(project, column="id", table="users")
    assert {(h.board, h.table) for h in result.hits} == {("charts/b.yaml", "users")}


def test_resolves_through_a_cte(tmp_path: Path) -> None:
    sql = "WITH r AS (SELECT customer_id AS cid FROM orders) SELECT cid FROM r"
    project = _project(tmp_path, {"cte.yaml": {"q": sql}})
    result = column_impact(project, column="customer_id")
    assert [(h.board, h.table) for h in result.hits] == [("charts/cte.yaml", "orders")]


def test_table_narrowing_matches_the_dotted_source_spelling(tmp_path: Path) -> None:
    """`--table orders` finds `raw.orders`; `--table raw.orders` narrows exactly."""
    project = _project(
        tmp_path,
        {"src.yaml": {"q": "SELECT id FROM {{ source('raw', 'orders') }}"}},
    )
    for wanted in ("orders", "raw.orders"):
        result = column_impact(project, column="id", table=wanted)
        assert [(h.board, h.table) for h in result.hits] == [
            ("charts/src.yaml", "raw.orders")
        ], wanted
    assert column_impact(project, column="id", table="other.orders").hits == []


def test_dotted_table_narrowing_matches_across_catalogs(tmp_path: Path) -> None:
    """`--table analytics.orders` matches `my_project.analytics.orders` — the
    suffix semantics that keep three-part catalogs narrowable."""
    project = _project(
        tmp_path,
        {"cat.yaml": {"q": "SELECT o.id FROM my_project.analytics.orders o"}},
    )
    result = column_impact(project, column="id", table="analytics.orders")
    assert [(h.board, h.table) for h in result.hits] == [
        ("charts/cat.yaml", "my_project.analytics.orders")
    ]


def test_markdown_board_is_indexed(tmp_path: Path) -> None:
    """Markdown boards carry full board config in frontmatter — a reverse
    index that skips them omits real dependents."""
    project = _project(tmp_path, {"other.yaml": {"q": "SELECT name FROM users"}})
    (tmp_path / "charts" / "weekly.md").write_text(
        "---\nboard:\n  source: db\n  queries:\n"
        "    q: SELECT customer_id FROM orders\n"
        "  charts:\n    c:\n      query: q\n      type: table\n"
        "  rows:\n    - c\n---\n# Weekly\n"
    )
    result = column_impact(project, column="customer_id")
    assert "charts/weekly.md" in {h.board for h in result.hits}


def test_non_utf8_board_is_indeterminate_not_a_traceback(tmp_path: Path) -> None:
    project = _project(tmp_path, {"ok.yaml": {"q": "SELECT customer_id FROM orders"}})
    (tmp_path / "charts" / "latin.yaml").write_bytes("title: Café\n".encode("latin-1"))
    result = column_impact(project, column="customer_id")
    assert {h.board for h in result.hits} == {"charts/ok.yaml"}
    assert "charts/latin.yaml" in {i.board for i in result.indeterminate}


def test_select_star_board_is_reported_indeterminate_never_omitted(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, {"star.yaml": {"q": "SELECT * FROM orders"}})
    result = column_impact(project, column="customer_id")
    assert result.hits == []
    assert [(i.board, i.query) for i in result.indeterminate] == [
        ("charts/star.yaml", "q")
    ]
    assert result.indeterminate[0].reason


def test_meta_and_inspect_template_files_are_not_boards(tmp_path: Path) -> None:
    project = _project(tmp_path, {"ok.yaml": {"q": "SELECT customer_id FROM orders"}})
    (tmp_path / "charts" / "meta.yml").write_text("source: db\n")
    tpl = tmp_path / "charts" / "inspect"
    tpl.mkdir()
    (tpl / ".inspect-template-manifest.json").write_text("{}")
    (tpl / "table.yaml").write_text("title: T\nrows: []\n")
    result = column_impact(project, column="customer_id")
    boards = {h.board for h in result.hits} | {i.board for i in result.indeterminate}
    assert boards == {"charts/ok.yaml"}
    assert result.boards_scanned == 1


def test_zero_boards_scanned_is_reported_not_a_clean_zero(tmp_path: Path) -> None:
    (tmp_path / "dbt_charts.yml").write_text(_SOURCES)
    project = FilesystemProject(tmp_path)
    result = column_impact(project, column="customer_id")
    assert result.boards_scanned == 0
    assert result.hits == []


def test_a_board_that_fails_compile_is_indeterminate(tmp_path: Path) -> None:
    project = _project(tmp_path, {"ok.yaml": {"q": "SELECT customer_id FROM orders"}})
    (tmp_path / "charts" / "broken.yaml").write_text("title: X\nrows:\n  - nope\n")
    result = column_impact(project, column="customer_id")
    assert {h.board for h in result.hits} == {"charts/ok.yaml"}
    assert {i.board for i in result.indeterminate} == {"charts/broken.yaml"}

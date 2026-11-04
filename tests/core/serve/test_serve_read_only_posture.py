"""`dct serve` defaults to strict read-only (external DuckDB access off).

After the file source materializer landed, serve no longer needs SQL-level
``read_csv`` / ``read_json`` — file sources are parsed via PyArrow into the
result cache. So serve tightens to strict read-only, matching Cloud: author
SQL cannot reach the filesystem or network through DuckDB, yet ``files:``
sources still render because the materializer handles them out-of-band.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.serve.server import create_server


def _write_boards_at_root(root: Path, board_name: str, board_yaml: str) -> None:
    boards = root / "charts"
    boards.mkdir(exist_ok=True)
    (boards / f"{board_name}.yml").write_text(board_yaml)


class TestServeStrictReadOnlyDefault:
    def test_create_server_defaults_to_strict_read_only(self, tmp_path: Path) -> None:
        """Serve defaults: read-only ON, external DuckDB access OFF, no config."""
        app = create_server(FilesystemProject(tmp_path))
        assert app.state.read_only is True
        assert app.state.allow_external_access_in_readonly is False
        assert app.state.duckdb_config is None

    def test_file_source_renders_under_strict_serve(self, tmp_path: Path) -> None:
        """A csv ``files:`` source renders through serve with external access off.

        The materializer parses the file via PyArrow into the cache, so the board
        renders even though DuckDB external access is disabled.
        """
        (tmp_path / "orders.csv").write_text("region,amount\nWest,100\nEast,200\n")
        _write_boards_at_root(
            tmp_path,
            "orders",
            "title: Orders\n"
            "queries:\n"
            "  q:\n"
            "    type: sql\n"
            "    source: ../orders.csv\n"
            "    sql: SELECT region, amount FROM orders ORDER BY region\n"
            "charts:\n"
            "  t:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - t\n",
        )

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/orders/")

        assert resp.status_code == 200, resp.text[:500]
        assert "West" in resp.text
        assert "East" in resp.text

    def test_raw_read_csv_sql_is_blocked_under_strict_serve(
        self, tmp_path: Path
    ) -> None:
        """Author SQL calling ``read_csv`` fails: external access is off.

        This is the tightening the task is about — before the flip, serve opened
        DuckDB with ``enable_external_access=True`` and this query succeeded.
        """
        (tmp_path / "revenue.csv").write_text("region,amount\nWest,100\n")
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  db:\n    type: duckdb\n    path: ':memory:'\n"
        )
        _write_boards_at_root(
            tmp_path,
            "rev",
            "title: Rev\n"
            "source: db\n"
            "queries:\n"
            "  q:\n"
            "    type: sql\n"
            "    sql: SELECT region, amount FROM read_csv('revenue.csv')\n"
            "charts:\n"
            "  t:\n"
            "    query: q\n"
            "    type: table\n"
            "rows:\n"
            "  - t\n",
        )

        app = create_server(FilesystemProject(tmp_path))
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/rev/")

        # read_csv is refused by the external-access-off posture — the served
        # error names the disabled file-system access, and the data never renders.
        # The tile wraps its message across lines: compare the words, not the markup.
        assert "file system operations are disabled" in " ".join(
            re.findall(r"(?<=>)([^<]+)(?=<)", resp.text)
        )
        assert "West" not in resp.text

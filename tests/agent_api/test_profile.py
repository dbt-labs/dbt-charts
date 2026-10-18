"""`dct query SOURCE` with no SQL prints a profile of the source's tables."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api import ProjectSession
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.cli.main import app

Factory = Callable[..., FilesystemProject]

ORDERS = """
CREATE TABLE orders AS
SELECT i AS id,
       CASE WHEN i % 4 = 0 THEN 'EU' ELSE 'US' END AS region,
       'customer-' || i AS customer,
       CAST(i * 1.5 AS DOUBLE) AS amount,
       CASE WHEN i % 10 = 0 THEN NULL ELSE i % 3 END AS tier,
       DATE '2024-01-01' + CAST(i - 1 AS INTEGER) AS day
FROM range(1, 41) t(i)
"""


def _profile(
    tmp_path: Path, local_project: Factory, source: str, tables: Sequence[str] = ()
) -> str:
    with ProjectSession.from_project(local_project(tmp_path)) as session:
        return session.profile_source(source, tables)


def _write_config(tmp_path: Path, body: str) -> None:
    (tmp_path / "dbt_charts.yml").write_text(f"sources:\n{body}")


def test_duckdb_profile_summarizes_each_column_kind(
    tmp_path: Path, local_project: Factory
) -> None:
    con = duckdb.connect(str(tmp_path / "shop.duckdb"))
    con.execute(ORDERS)
    con.close()
    _write_config(tmp_path, "  shop:\n    type: duckdb\n    path: shop.duckdb\n")

    lines = _profile(tmp_path, local_project, "shop").splitlines()

    assert lines[1] == "orders: 40 rows, 6 columns"
    assert "  id BIGINT  1 .. 40, median 20.5" in lines
    assert "  region VARCHAR  US 30, EU 10" in lines
    assert (
        "  customer VARCHAR  40 distinct, e.g. customer-1, customer-10, customer-11"
        in lines
    )
    assert "  amount DOUBLE  1.5 .. 60, median 30.75" in lines
    assert "  tier BIGINT  10% null  0 12, 1 12, 2 12" in lines
    assert "  day DATE  2024-01-01 .. 2024-02-09 daily" in lines
    assert lines[-4].startswith("> id,region,customer,amount,tier,day")
    assert len([line for line in lines if line.startswith("> ")]) == 4


def test_profile_is_deterministic(tmp_path: Path, local_project: Factory) -> None:
    con = duckdb.connect(str(tmp_path / "shop.duckdb"))
    con.execute(ORDERS)
    con.close()
    _write_config(tmp_path, "  shop:\n    type: duckdb\n    path: shop.duckdb\n")

    assert _profile(tmp_path, local_project, "shop") == _profile(
        tmp_path, local_project, "shop"
    )


def test_sqlite_profile(tmp_path: Path, local_project: Factory) -> None:
    con = sqlite3.connect(tmp_path / "shop.sqlite")
    con.execute("CREATE TABLE t (id INTEGER, region TEXT, amount REAL, day DATE)")
    con.executemany(
        "INSERT INTO t VALUES (?, ?, ?, ?)",
        [
            (i, "EU" if i % 2 else "US", i * 2.0, f"2024-03-{i:02d}")
            for i in range(1, 16)
        ],
    )
    con.commit()
    con.close()
    _write_config(tmp_path, "  s:\n    type: sqlite\n    path: shop.sqlite\n")

    lines = _profile(tmp_path, local_project, "s").splitlines()

    assert lines[1] == "t: 15 rows, 4 columns"
    assert "  region TEXT  EU 8, US 7" in lines
    assert "  amount REAL  2 .. 30, median 16" in lines
    assert "  day DATE  2024-03-01 .. 2024-03-15 daily" in lines


@pytest.mark.parametrize("kind", ["csv", "parquet", "json"])
def test_file_source_profile(tmp_path: Path, local_project: Factory, kind: str) -> None:
    data = tmp_path / f"orders.{kind}"
    con = duckdb.connect()
    con.execute(ORDERS)
    if kind == "csv":
        con.execute(f"COPY orders TO '{data}' (HEADER)")
    elif kind == "parquet":
        con.execute(f"COPY orders TO '{data}' (FORMAT parquet)")
    else:
        con.execute(f"COPY orders TO '{data}' (FORMAT json, ARRAY true)")
    con.close()
    _write_config(
        tmp_path,
        f"  files:\n    type: {kind}\n    files:\n      orders: orders.{kind}\n",
    )

    lines = _profile(tmp_path, local_project, "files").splitlines()

    assert lines[1] == "orders: 40 rows, 6 columns"
    assert "  region VARCHAR  US 30, EU 10" in lines


def test_inline_file_path_is_a_source(tmp_path: Path, local_project: Factory) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("region,amount\nN,1\nS,2\nN,3\n")
    (tmp_path / "dbt_charts.yml").write_text("sources: {}\n")

    lines = _profile(tmp_path, local_project, "data/orders.csv").splitlines()

    assert lines[1] == "orders: 3 rows, 2 columns"
    assert "  region VARCHAR  N 2, S 1" in lines


def test_wide_table_truncates_columns_with_a_count(
    tmp_path: Path, local_project: Factory
) -> None:
    columns = ", ".join(f"i * {n} AS c{n}" for n in range(1, 91))
    con = duckdb.connect(str(tmp_path / "wide.duckdb"))
    con.execute(f"CREATE TABLE wide AS SELECT {columns} FROM range(1, 20) t(i)")
    con.close()
    _write_config(tmp_path, "  w:\n    type: duckdb\n    path: wide.duckdb\n")

    text = _profile(tmp_path, local_project, "w")

    assert "  +30 more columns: c61, c62" in text
    assert len(text.splitlines()) <= 67


def test_many_tables_print_an_overview_and_table_flag_profiles_in_full(
    tmp_path: Path, local_project: Factory
) -> None:
    con = duckdb.connect(str(tmp_path / "many.duckdb"))
    for n in range(8):
        con.execute(
            f"CREATE TABLE t{n} AS SELECT i AS a, 'x' AS b FROM range({n + 1}) r(i)"
        )
    con.close()
    _write_config(tmp_path, "  m:\n    type: duckdb\n    path: many.duckdb\n")

    overview = _profile(tmp_path, local_project, "m").splitlines()
    assert overview[1] == "t0 (1 rows): a, b"
    assert overview[8] == "t7 (8 rows): a, b"
    assert (
        overview[-1]
        == "profile tables in full: dct query m --table NAME [--table NAME ...]"
    )

    full = _profile(tmp_path, local_project, "m", ["t2", "t3"])
    assert "t2: 3 rows, 2 columns" in full
    assert "t3: 4 rows, 2 columns" in full
    assert "t4" not in full


def test_unknown_table_names_the_tables(tmp_path: Path, local_project: Factory) -> None:
    con = duckdb.connect(str(tmp_path / "shop.duckdb"))
    con.execute(ORDERS)
    con.close()
    _write_config(tmp_path, "  shop:\n    type: duckdb\n    path: shop.duckdb\n")

    with pytest.raises(ValueError, match="no table nope; tables: orders"):
        _profile(tmp_path, local_project, "shop", ["nope"])


def test_warehouse_source_is_refused_without_a_query(
    tmp_path: Path, local_project: Factory
) -> None:
    _write_config(
        tmp_path,
        "  wh:\n    type: postgres\n    host: localhost\n    user: u\n"
        "    password: \"{{ env_var('PGPW') }}\"\n    database: d\n",
    )

    with pytest.raises(
        ValueError, match="postgres source; a profile scans every table"
    ):
        _profile(tmp_path, local_project, "wh")


def test_cli_profile_and_sql_with_literals_survive_sh_quoting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    con = duckdb.connect(str(tmp_path / "shop.duckdb"))
    con.execute(ORDERS)
    con.close()
    _write_config(tmp_path, "  shop:\n    type: duckdb\n    path: shop.duckdb\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    profile = runner.invoke(app, ["query", "shop"], catch_exceptions=False)
    assert profile.exit_code == 0
    assert "orders: 40 rows, 6 columns" in profile.output

    sql = "SELECT COUNT(*) AS n FROM orders WHERE region = 'EU'"
    rows = runner.invoke(app, ["query", "shop", sql], catch_exceptions=False)
    assert rows.output == "n\n10\n"

    bad = runner.invoke(app, ["query", "shop", "--json"])
    assert bad.exit_code == 1
    assert "--json is not supported" in bad.output


def test_cli_unknown_source_is_an_error_line_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_config(tmp_path, "  shop:\n    type: duckdb\n    path: shop.duckdb\n")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["query", "typo"])

    assert result.exit_code == 1
    assert result.output.startswith("Error: ")
    assert "typo" in result.output
    assert "Traceback" not in result.output


def test_time_column_with_many_values_does_not_crash_the_profile(
    tmp_path: Path, local_project: Factory
) -> None:
    (tmp_path / "shifts.csv").write_text("at\n09:00:00\n10:00:00\n11:00:00\n")
    _write_config(
        tmp_path, "  shifts:\n    type: csv\n    files:\n      shifts: shifts.csv\n"
    )

    lines = _profile(tmp_path, local_project, "shifts").splitlines()

    assert "  at TIME  09:00:00 .. 11:00:00" in lines


def test_boolean_sample_cell_prints_whole(
    tmp_path: Path, local_project: Factory
) -> None:
    (tmp_path / "flags.csv").write_text("ok\ntrue\nfalse\n")
    _write_config(
        tmp_path, "  flags:\n    type: csv\n    files:\n      flags: flags.csv\n"
    )

    lines = _profile(tmp_path, local_project, "flags").splitlines()

    assert "> True" in lines


def test_table_name_containing_a_dot_is_quoted_whole(
    tmp_path: Path, local_project: Factory
) -> None:
    con = duckdb.connect(str(tmp_path / "d.duckdb"))
    con.execute('CREATE TABLE "a.b" AS SELECT 1 AS x')
    con.close()
    _write_config(tmp_path, "  d:\n    type: duckdb\n    path: d.duckdb\n")

    lines = _profile(tmp_path, local_project, "d").splitlines()

    assert "a.b: 1 rows, 1 columns" in lines

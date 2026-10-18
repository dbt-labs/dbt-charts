"""An unknown source names the data files that would work; `dct query` lists sources."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

CONFIG = "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n"


def _run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, ["query", *args])
    return result.exit_code, " ".join(result.output.split())


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(CONFIG)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "orders.csv").write_text("id\n1\n")
    (tmp_path / "data" / "notes.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_typo_with_a_matching_file_suggests_its_path_and_lists_everything(
    project: Path,
) -> None:
    (project / "shop.csv").write_text("id,name\n1,a\n")

    code, out = _run("shop", "SELECT 1")

    assert code == 1
    assert "Did you mean `source: ./shop.csv`?" in out
    assert "wh" in out
    assert "./shop.csv" in out
    assert "./data/orders.csv" in out
    assert "notes.txt" not in out


def test_typo_without_a_matching_file_only_lists(project: Path) -> None:
    code, out = _run("shop", "SELECT 1")

    assert code == 1
    assert "Did you mean `source:" not in out
    assert "./data/orders.csv" in out


def test_unknown_source_with_no_configured_sources_lists_data_files(
    project: Path,
) -> None:
    (project / "dbt_charts.yml").write_text("")
    (project / "shop.parquet").write_bytes(b"")

    code, out = _run("shop", "SELECT 1")

    assert code == 1
    assert "Did you mean `source: ./shop.parquet`?" in out
    assert "./data/orders.csv" in out


def test_query_with_no_arguments_lists_sources_and_data_files(project: Path) -> None:
    (project / "shop.csv").write_text("id\n1\n")

    code, out = _run()

    assert code == 0
    assert "wh" in out
    assert "./shop.csv" in out
    assert "./data/orders.csv" in out


def test_query_profiles_a_file_path(project: Path) -> None:
    (project / "shop.csv").write_text("id,name\n1,a\n2,b\n")

    code, out = _run("./shop.csv")

    assert code == 0
    assert "shop: 2 rows, 2 columns" in out


def test_typo_matching_a_database_file_shows_the_sources_entry(project: Path) -> None:
    (project / "shop.duckdb").write_bytes(b"")

    code, out = _run("shop", "SELECT 1")

    assert code == 1
    assert "Did you mean the database file `shop.duckdb`?" in out
    assert "sources: shop: type: duckdb path: shop.duckdb" in out


def test_listing_shows_each_database_file_with_its_entry(project: Path) -> None:
    (project / "data" / "cars.sqlite").write_bytes(b"")

    code, out = _run()

    assert code == 0
    assert "sources: cars: type: sqlite path: data/cars.sqlite" in out

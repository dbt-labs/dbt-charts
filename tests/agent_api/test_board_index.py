"""The board index a search keeps between `dct` calls."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dbt_charts.agent_api.board_index import open_board_index
from dbt_charts.agent_api.search import SearchResult, search_files
from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import engine
from dbt_charts.core.compile.parse import board_text
from dbt_charts.core.project import Project, ProjectPath

REVENUE = """\
title: Revenue overview
notes: Monthly revenue.
source: wh
tags:
  - finance
queries:
  by_month:
    sql: select month, amount from orders
rows:
- revenue:
    title: Revenue by month
    query: by_month
    type: bar
    x: month
    y: amount
"""

COSTS = """\
title: Costs
source: wh
queries:
  by_month:
    sql: select month, cost from costs
rows:
- costs:
    query: by_month
    type: bar
    x: month
    y: cost
"""

MARKDOWN = """\
---
title: Revenue notes
---

# Revenue

The numbers behind the revenue overview.
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n"
    )
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "revenue.yml").write_text(REVENUE)
    (charts / "costs.yml").write_text(COSTS)
    (charts / "notes.md").write_text(MARKDOWN)
    (charts / "broken.yml").write_text("title: Revenue broken\n  rows: [\n")
    (charts / "meta.yml").write_text("theme: editorial\n")
    return tmp_path


def _search(root: Path, text: str | None = "revenue") -> SearchResult:
    """A fresh project each call, as each `dct` is a fresh process."""
    return search_files(FilesystemProject(root), text=text)


def _index_file(root: Path) -> Path:
    return root / ".dct" / "board_index.json"


def _forbid_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    real = board_text.parse_board_text

    def fail(slug: str, content: str, *, is_markdown: bool) -> board_text.BoardText:
        # A board the engine refuses is read again each call: another file may be why.
        if slug != "broken":
            raise AssertionError("a current index parses nothing")
        return real(slug, content, is_markdown=is_markdown)

    monkeypatch.setattr(
        "dbt_charts.core.compile.parse.board_text.parse_board_text", fail
    )


def test_a_second_search_parses_no_board(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _search(root)
    _forbid_parsing(monkeypatch)
    assert _search(root) == first


def test_a_listing_reads_the_index_too(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _search(root, text=None)
    _forbid_parsing(monkeypatch)
    assert _search(root, text=None) == first
    assert first.results


def test_a_search_on_the_index_matches_one_without_it(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _search(root)
    assert _search(root) == built

    def no_index(_project: Project) -> None:
        return None

    monkeypatch.setattr("dbt_charts.agent_api.search.open_board_index", no_index)
    assert _search(root) == built
    assert {hit.file_path for hit in built.results} >= {
        "charts/revenue.yml",
        "charts/notes.md",
    }


def test_an_edited_board_is_reparsed(root: Path) -> None:
    _search(root)
    (root / "charts" / "costs.yml").write_text(COSTS.replace("Costs", "Revenue costs"))
    assert "charts/costs.yml" in {hit.file_path for hit in _search(root).results}


def test_an_edit_reparses_only_that_board(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _search(root)
    (root / "charts" / "costs.yml").write_text(COSTS.replace("Costs", "Revenue costs"))
    calls: list[str] = []
    real = engine.engine_index

    def counting(path: ProjectPath) -> engine.EngineIndex | None:
        calls.append(path.relpath)
        return real(path)

    monkeypatch.setattr("dbt_charts.core.compile.engine.engine_index", counting)
    _search(root)
    # A board the engine refuses is read again each call: another file may be why.
    assert calls == ["charts/broken.yml", "charts/costs.yml"]


def test_a_deleted_board_leaves_the_results(root: Path) -> None:
    _search(root)
    (root / "charts" / "revenue.yml").unlink()
    assert "charts/revenue.yml" not in {hit.file_path for hit in _search(root).results}


def test_a_new_board_joins_the_results(root: Path) -> None:
    _search(root)
    (root / "charts" / "more.yml").write_text(REVENUE)
    assert "charts/more.yml" in {hit.file_path for hit in _search(root).results}


def test_nothing_changed_writes_nothing(root: Path) -> None:
    _search(root)
    index = _index_file(root)
    os.utime(index, ns=(1, 1))
    _search(root)
    assert index.stat().st_mtime_ns == 1


def _with_our_key(root: Path, boards: object) -> str:
    key = json.loads(_index_file(root).read_text())["key"]
    return json.dumps({"key": key, "boards": boards})


@pytest.mark.parametrize(
    "garbage",
    ["not json", "[]", '{"boards": 3}', '{"key": "0:another", "boards": {}}'],
)
def test_an_unreadable_index_is_rebuilt(root: Path, garbage: str) -> None:
    built = _search(root)
    _index_file(root).write_text(garbage)
    assert _search(root) == built
    assert json.loads(_index_file(root).read_text())["boards"]


def test_an_index_of_the_wrong_shape_is_rebuilt(root: Path) -> None:
    built = _search(root)
    _index_file(root).write_text(_with_our_key(root, 3))
    assert _search(root) == built


def test_a_dangling_link_is_named_from_its_file(root: Path) -> None:
    (root / "charts" / "gone.yml").symlink_to(root / "charts" / "missing.yml")
    built = _search(root, text=None)
    assert "charts/gone.yml" in {hit.file_path for hit in built.results}
    assert "charts/revenue.yml" in {hit.file_path for hit in _search(root).results}


def test_a_failed_read_is_not_remembered(root: Path) -> None:
    board = root / "charts" / "revenue.yml"
    board.chmod(0)
    try:
        if os.access(board, os.R_OK):
            pytest.skip("running as root or on a filesystem that ignores chmod")
        assert "charts/revenue.yml" not in {
            hit.file_path for hit in _search(root).results
        }
    finally:
        board.chmod(0o644)
    assert "charts/revenue.yml" in {hit.file_path for hit in _search(root).results}


def test_another_version_of_dct_starts_over(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _search(root)
    monkeypatch.setattr("dbt_charts.__version__", "0.0.0-another")
    calls: list[str] = []
    real = board_text.parse_board_text

    def counting(slug: str, content: str, *, is_markdown: bool) -> board_text.BoardText:
        calls.append(slug)
        return real(slug, content, is_markdown=is_markdown)

    monkeypatch.setattr(
        "dbt_charts.core.compile.parse.board_text.parse_board_text", counting
    )
    _search(root)
    assert calls


def test_a_read_only_project_still_searches(root: Path) -> None:
    (root / ".dct").write_text("a file where the cache folder would go")
    assert {"charts/revenue.yml"} <= {hit.file_path for hit in _search(root).results}


def test_the_index_never_shows_up_in_a_search(root: Path) -> None:
    _search(root)
    result = search_files(FilesystemProject(root), kind="all")
    assert not [hit.file_path for hit in result.results if ".dct" in hit.file_path]


def test_the_cache_folder_ignores_itself(root: Path) -> None:
    _search(root)
    assert (root / ".dct" / ".gitignore").read_text() == "*\n"


def test_a_board_is_entered_from_the_engine_and_one_it_refuses_from_its_text(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n"
    )
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "_base.yml").write_text("title: Inherited revenue\n")
    (charts / "folded.yml").write_text(
        "extends: ./_base.yml\n"
        "source: wh\n"
        "queries:\n  q:\n    sql: select 1 as a\n"
        "charts:\n  c:\n    type: table\n    query: q\n    columns:\n      - a\n"
        "rows:\n  - c\n"
    )
    # Sourceless SQL: the engine refuses the board, so it is named from its text
    # and search invents no chart or query for it.
    (charts / "refused.yml").write_text(
        "title: Refused revenue\n"
        "queries:\n  q:\n    sql: select 1 as a\n"
        "charts:\n  c:\n    type: table\n    query: q\n    columns:\n      - a\n"
        "rows:\n  - c\n"
    )
    index = open_board_index(FilesystemProject(tmp_path))
    assert index is not None
    entries = {e["title"]: e for e in index.entries.values()}
    assert set(entries) == {"Inherited revenue", "Refused revenue"}
    assert [c["id"] for c in entries["Inherited revenue"]["charts"]] == ["c"]
    assert entries["Refused revenue"]["charts"] == []
    assert entries["Refused revenue"]["query_names"] == []


def test_a_record_is_read_again_when_a_file_its_compile_read_changes(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text("sources: {}\n")
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "_base.yml").write_text("title: Old title\n")
    (charts / "folded.yml").write_text("extends: ./_base.yml\nrows:\n  - text: hi\n")

    def titles() -> list[str]:
        index = open_board_index(FilesystemProject(tmp_path))
        assert index is not None
        return [label.title for label in index.labels]

    assert titles() == ["Old title"]
    (charts / "_base.yml").write_text("title: New title, longer\n")
    assert titles() == ["New title, longer"]


def test_a_board_with_junk_typed_charts_is_still_listed(tmp_path: Path) -> None:
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "junk.yml").write_text(
        "title: Junk board\ncharts:\n  odd:\n    title: [not, a, string]\n    type: 7\n"
        "  2024:\n    type: bar\nrows:\n  - odd\n"
    )
    index = open_board_index(FilesystemProject(tmp_path))
    assert index is not None
    assert [e["title"] for e in index.entries.values()] == ["Junk board"]


def test_a_record_is_read_again_when_the_sources_it_compiled_against_change(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  wh:\n    type: duckdb\n    path: wh.duckdb\n"
    )
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "sales.yml").write_text(
        "title: Sales\nsource: wh\nqueries:\n  q:\n    sql: select 1 as a\n"
        "charts:\n  c:\n    type: table\n    query: q\n    columns:\n      - a\n"
        "rows:\n  - c\n"
    )

    def charts_of_sales() -> list[str]:
        index = open_board_index(FilesystemProject(tmp_path))
        assert index is not None
        return [c["id"] for c in index.entries["charts/sales.yml"]["charts"]]

    assert charts_of_sales() == ["c"]
    (tmp_path / "dbt_charts.yml").write_text(
        "sources:\n  other:\n    type: duckdb\n    path: other.duckdb\n"
    )
    assert charts_of_sales() == []


def test_a_record_is_read_again_when_a_dbt_projects_profile_goes(
    tmp_path: Path,
) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: shop\nprofile: shop\n")
    charts = tmp_path / "charts"
    charts.mkdir()
    (charts / "sales.yml").write_text(
        "title: Sales\nqueries:\n  q:\n    sql: select 1 as a\n"
        "charts:\n  c:\n    type: table\n    query: q\n    columns:\n      - a\n"
        "rows:\n  - c\n"
    )

    def charts_of_sales() -> list[str]:
        index = open_board_index(FilesystemProject(tmp_path))
        assert index is not None
        return [c["id"] for c in index.entries["charts/sales.yml"]["charts"]]

    assert charts_of_sales() == ["c"]
    (tmp_path / "dbt_project.yml").write_text("name: shop\n")
    assert charts_of_sales() == []

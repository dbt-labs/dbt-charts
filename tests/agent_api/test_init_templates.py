"""Validate that init scaffold templates parse correctly and stay in sync with the schema."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from importlib_resources import files

from dbt_charts.agent_api.init import _EXCLUDED_TEMPLATE_NAMES
from dbt_charts.core.project import (
    CHARTS_SUBDIR,
    META_FILENAMES,
    PROJECT_CONFIG_NAME,
    is_board_candidate,
)

if TYPE_CHECKING:
    from importlib_resources.abc import Traversable


def _template_pkg() -> Traversable:
    return files("dbt_charts.agent_api._init_templates")


def _walk_template_names(node: Traversable, prefix: str = "") -> Iterator[str]:
    """Yield every template file's relative posix path, recursively."""
    for entry in node.iterdir():
        if entry.name in _EXCLUDED_TEMPLATE_NAMES:
            continue
        rel = f"{prefix}{entry.name}"
        if entry.is_dir():
            yield from _walk_template_names(entry, f"{rel}/")
        else:
            yield rel


def _scaffold_board_names() -> list[str]:
    """Scaffold files the engine itself treats as standalone boards: board
    candidates under charts/ (the root README.md is project prose)."""
    excluded = META_FILENAMES | {PROJECT_CONFIG_NAME}
    return sorted(
        name
        for name in _walk_template_names(_template_pkg())
        if name.startswith(f"{CHARTS_SUBDIR}/")
        and is_board_candidate(name)
        and Path(name).name not in excluded
    )


# Computed once at collection time.
_SCAFFOLD_BOARD_NAMES = _scaffold_board_names()


class TestScaffoldBoards:
    def test_no_scaffold_board(self) -> None:
        """dct init ships no starter board: `dct serve` shows an intro page instead."""
        assert _SCAFFOLD_BOARD_NAMES == []

    def test_scaffold_ships_no_data_files(self) -> None:
        """The scaffold ships no data files."""
        names = list(_walk_template_names(_template_pkg()))
        data_files = [n for n in names if n.endswith((".csv", ".json", ".parquet"))]
        assert data_files == [], data_files

    def test_index_md_template_does_not_exist(self) -> None:
        """index.md must not exist as a template — it would hijack the served root."""
        names = list(_walk_template_names(_template_pkg()))
        assert "charts/index.md" not in names and "index.md" not in names, (
            "index.md must not exist in _init_templates: it causes dct serve to render "
            "the welcome page at / instead of the directory listing"
        )


class TestBoardYmlTemplate:
    def test_board_yml_parses_as_yaml(self) -> None:
        content = _template_pkg().joinpath("dbt_charts.yml").read_text()
        parsed = yaml.safe_load(content)
        # Mostly comments today — parses to None; must not raise
        assert parsed is None or isinstance(parsed, dict)

    def test_board_yml_shows_sources_registry(self) -> None:
        """A non-dbt cold start (a bare DuckDB/Postgres) is the common case;
        the scaffold must show the `sources:` shape, not only dbt profiles."""
        content = _template_pkg().joinpath("dbt_charts.yml").read_text()
        assert "sources:" in content
        assert "type: duckdb" in content
        assert "type: postgres" in content


class TestInitScaffold:
    """init_project() creates no board; charts/dbt_charts.yml must never be scaffolded.

    charts/dbt_charts.yml collides with the DCT_ROOT_MARKERS sentinel in project_roots.py:
    find_project_root walks up from a board, stops at charts/dbt_charts.yml, and
    treats charts/ as the project root — so every chart errors "Source not found".
    """

    def test_init_does_not_create_board_yml(self, tmp_path: Path) -> None:
        """charts/dbt_charts.yml must not be scaffolded — it collides with DCT_ROOT_MARKERS."""
        from dbt_charts.agent_api.init import init_project

        init_project(tmp_path)
        assert not (tmp_path / "charts" / "dbt_charts.yml").exists(), (
            "charts/dbt_charts.yml must not be created by init_project: it collides with "
            "the dbt_charts.yml DCT_ROOT_MARKERS sentinel in project_roots.py, causing "
            "find_project_root to stop at charts/ and treat it as the project root"
        )

    def test_init_does_not_create_agent_markdown(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.init import init_project

        result = init_project(tmp_path)

        assert not (tmp_path / "AGENTS.md").exists()
        assert not (tmp_path / "CLAUDE.md").exists()
        assert Path("AGENTS.md") not in result.created_files
        assert Path("CLAUDE.md") not in result.created_files


class TestScaffoldTreeMatchesTemplates:
    """Drift guard: the scaffolded tree is exactly the template tree, no maintained list."""

    def test_fresh_init_creates_exactly_the_template_tree(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.init import init_project

        result = init_project(tmp_path)
        template_paths = set(_walk_template_names(_template_pkg()))
        created_paths = {p.as_posix() for p in result.created_files} - {".gitignore"}
        assert created_paths == template_paths

    def test_scaffold_paths_equals_fresh_created_files(self, tmp_path: Path) -> None:
        from dbt_charts.agent_api.init import SCAFFOLD_PATHS, init_project

        result = init_project(tmp_path)
        created = {p.as_posix() for p in result.created_files}
        assert set(SCAFFOLD_PATHS) == created
        assert PROJECT_CONFIG_NAME in SCAFFOLD_PATHS


class TestScaffoldConventions:
    def test_yaml_templates_use_yml_suffix(self) -> None:
        """The scaffold is the convention a new project inherits: `.yml`, like
        `dbt_charts.yml` and every dbt file. Both suffixes stay accepted; only
        our own files are pinned."""
        offenders = [
            name
            for name in _walk_template_names(_template_pkg())
            if name.endswith(".yaml")
        ]
        assert offenders == [], f"scaffold templates must use .yml: {offenders}"

    def test_readme_created_but_never_refreshed(self, tmp_path: Path) -> None:
        """A project README is the user's file the moment it exists: `--force`
        refreshes engine-owned scaffold files, never a README."""
        from dbt_charts.agent_api.init import init_project

        first = init_project(tmp_path)
        assert Path("README.md") in first.created_files
        readme = tmp_path / "README.md"
        readme.write_text("# mine\n", encoding="utf-8")

        forced = init_project(tmp_path, force=True)
        assert readme.read_text(encoding="utf-8") == "# mine\n"
        assert Path("README.md") not in forced.refreshed_files
        assert Path(f"{CHARTS_SUBDIR}/meta.yml") in forced.refreshed_files

"""Tests for meta.yml resolution.

meta.yml is a partial AuthoredBoard, deep-merged as a lower-priority layer
beneath each board in its directory subtree. This module covers:
- finding meta files in the directory chain (find_meta_files)
- loading a single meta file + extracting its lint directives (load_meta_file)
- end-to-end compile_file integration, including fail-loud on bad meta
"""

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile import compile_file
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.parse.meta import (
    MetaLintConfig,
    find_meta_files,
    load_meta_file,
    parse_meta_content,
)

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_project():
    """Create a temporary project directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir).resolve()


@pytest.fixture
def nested_dirs(temp_project):
    """Nested directory structure with meta.yml files (new top-level surface)."""
    root = temp_project

    (root / "meta.yml").write_text(
        yaml.dump(
            {
                "source": "production_db",
                "style": {"frame": {"width": 700}},
                "queries": {"common_query": "SELECT 1"},
            }
        )
    )

    analytics = root / "analytics"
    analytics.mkdir()
    (analytics / "meta.yml").write_text(
        yaml.dump(
            {
                "source": "analytics_db",
                "queries": {"analytics_common": "SELECT 2"},
            }
        )
    )

    sales = analytics / "sales"
    sales.mkdir()
    (sales / "meta.yml").write_text(
        yaml.dump(
            {"charts": {"revenue_chart": {"type": "line", "query": "sales_query"}}}
        )
    )

    (sales / "dashboard.yaml").write_text(
        yaml.dump(
            {
                "title": "Sales Dashboard",
                "queries": {"sales_query": "SELECT * FROM sales"},
                "rows": ["revenue_chart"],
            }
        )
    )

    return {
        "root": root,
        "analytics": analytics,
        "sales": sales,
        "board_path": sales / "dashboard.yaml",
    }


# ============================================================================
# load_meta_file
# ============================================================================


class TestLoadMetaFile:
    def test_returns_dict_and_lint(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump({"source": "prod_db", "queries": {"q": "SELECT 1"}})
        )
        meta_file = local_project(temp_project).path("meta.yml")

        data, lint = load_meta_file(meta_file)

        assert data == {"source": "prod_db", "queries": {"q": "SELECT 1"}}
        assert isinstance(lint, MetaLintConfig)
        assert lint.ignore == []
        assert lint.ignore_queries == {}

    def test_lint_is_extracted_and_stripped(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump(
                {
                    "source": "db",
                    "lint": {
                        "ignore": ["WARN-FANOUT-RISK"],
                        "ignore_queries": {"q": ["WARN-REAGGREGATION"]},
                    },
                }
            )
        )
        meta_file = local_project(temp_project).path("meta.yml")

        data, lint = load_meta_file(meta_file)

        assert "lint" not in data  # lint is not board content
        assert data == {"source": "db"}
        assert lint.ignore == ["WARN-FANOUT-RISK"]
        assert lint.ignore_queries == {"q": ["WARN-REAGGREGATION"]}

    def test_lint_ignore_typo_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """A typo'd lint.ignore code is a loud compile error, not a silent no-op."""
        (temp_project / "meta.yml").write_text(
            yaml.dump({"lint": {"ignore": ["WARN-FANOUT-RISKK"]}})
        )
        meta_file = local_project(temp_project).path("meta.yml")

        with pytest.raises(CompilationError, match="WARN-FANOUT-RISKK"):
            load_meta_file(meta_file)

    def test_lint_ignore_queries_typo_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """A typo'd lint.ignore_queries code is a loud compile error too."""
        (temp_project / "meta.yml").write_text(
            yaml.dump({"lint": {"ignore_queries": {"q": ["WARN-REAGGREGATIONN"]}}})
        )
        meta_file = local_project(temp_project).path("meta.yml")

        with pytest.raises(CompilationError, match="WARN-REAGGREGATIONN"):
            load_meta_file(meta_file)

    def test_non_board_keys_are_kept_for_downstream_validation(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        # access / the old `board:` wrapper are NOT silently dropped — they stay
        # so AuthoredBoard rejects them loudly once meta is merged under the board.
        (temp_project / "meta.yml").write_text(
            yaml.dump({"access": [{"role": "admin"}], "board": {"x": 1}})
        )
        meta_file = local_project(temp_project).path("meta.yml")

        data, _ = load_meta_file(meta_file)

        assert "access" in data
        assert "board" in data

    def test_not_found_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        meta_file = local_project(temp_project).path("nope.yaml")
        with pytest.raises(CompilationError, match="Meta file not found"):
            load_meta_file(meta_file)

    def test_invalid_yaml_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text("invalid: yaml: content: [")
        meta_file = local_project(temp_project).path("meta.yml")
        with pytest.raises(CompilationError, match="Failed to parse"):
            load_meta_file(meta_file)

    def test_non_mapping_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(yaml.dump(["a", "b"]))
        meta_file = local_project(temp_project).path("meta.yml")
        with pytest.raises(CompilationError, match="must be a YAML mapping"):
            load_meta_file(meta_file)

    def test_falsy_non_mapping_raises_not_silently_accepted(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """`false` is a real non-mapping document, not an empty file: must
        not be excused to `{}` the way `None` (an empty file) is."""
        (temp_project / "meta.yml").write_text("false\n")
        meta_file = local_project(temp_project).path("meta.yml")
        with pytest.raises(CompilationError, match="must be a YAML mapping"):
            load_meta_file(meta_file)

    def test_lint_non_mapping_raises(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(yaml.dump({"lint": ["fanout_risk"]}))
        meta_file = local_project(temp_project).path("meta.yml")
        with pytest.raises(CompilationError, match="'lint' must be a mapping"):
            load_meta_file(meta_file)

    def test_empty_file(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text("")
        meta_file = local_project(temp_project).path("meta.yml")
        data, lint = load_meta_file(meta_file)
        assert data == {}
        assert lint.ignore == []


class TestParseMetaContent:
    """parse_meta_content validates raw text the same way load_meta_file
    validates a file on disk: for a caller (the LSP) with an unsaved
    editor buffer rather than a saved ProjectPath."""

    def test_valid_content(self):
        data, lint = parse_meta_content("source: analytics\n", "meta.yml")
        assert data == {"source": "analytics"}
        assert lint.ignore == []

    def test_invalid_yaml_raises(self):
        with pytest.raises(CompilationError, match="Failed to parse"):
            parse_meta_content("invalid: yaml: content: [", "meta.yml")

    def test_non_mapping_raises(self):
        with pytest.raises(CompilationError, match="must be a YAML mapping"):
            parse_meta_content(yaml.dump(["a", "b"]), "meta.yml")

    def test_empty_content(self):
        data, lint = parse_meta_content("", "meta.yml")
        assert data == {}
        assert lint.ignore == []

    def test_falsy_non_mapping_content_raises_not_silently_accepted(self):
        """`false` is a real non-mapping document, not an empty file: must
        not be excused to `{}` the way `None` (an empty file) is."""
        with pytest.raises(CompilationError, match="must be a YAML mapping"):
            parse_meta_content("false\n", "meta.yml")


# ============================================================================
# find_meta_files
# ============================================================================


class TestFindMetaFiles:
    def test_single(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text("source: db")
        project = local_project(temp_project)
        board_file = project.path("dashboard.yaml")
        files = find_meta_files(board_file, project.directory("."))
        assert len(files) == 1
        assert files[0].relpath == "meta.yml"

    def test_nested_root_to_leaf_order(
        self, nested_dirs, local_project: Callable[..., FilesystemProject]
    ):
        root = nested_dirs["root"]
        project = local_project(root)
        # board is at analytics/sales/dashboard.yaml relative to root
        board_file = project.path_for_fspath(nested_dirs["board_path"])
        files = find_meta_files(board_file, project.directory("."))
        assert len(files) == 3
        from pathlib import PurePosixPath

        assert PurePosixPath(files[0].relpath).parent == PurePosixPath()
        assert PurePosixPath(files[1].relpath).parent == PurePosixPath("analytics")
        assert PurePosixPath(files[2].relpath).parent == PurePosixPath(
            "analytics/sales"
        )

    def test_none(self, temp_project, local_project: Callable[..., FilesystemProject]):
        (temp_project / "subdir").mkdir()
        project = local_project(temp_project)
        board_file = project.path("subdir/dashboard.yaml")
        files = find_meta_files(board_file, project.directory("."))
        assert files == []

    def test_yaml_variant(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """`.yaml` stays accepted; only our own files are pinned to `.yml`."""
        (temp_project / "meta.yaml").write_text("source: db")
        project = local_project(temp_project)
        board_file = project.path("dashboard.yml")
        files = find_meta_files(board_file, project.directory("."))
        assert len(files) == 1
        from pathlib import PurePosixPath

        assert PurePosixPath(files[0].relpath).name == "meta.yaml"

    def test_prefers_yml_over_yaml(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """Same precedence as board resolution (BOARD_CANDIDATE_SUFFIXES)."""
        (temp_project / "meta.yaml").write_text("source: yaml_db")
        (temp_project / "meta.yml").write_text("source: yml_db")
        project = local_project(temp_project)
        board_file = project.path("dashboard.yml")
        files = find_meta_files(board_file, project.directory("."))
        from pathlib import PurePosixPath

        assert [PurePosixPath(f.relpath).name for f in files] == ["meta.yml"]

    def test_charts_meta_default_source_prefers_yml_over_yaml(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """The host-default-source probe in core.board shares the precedence."""
        from dbt_charts.core.board import _charts_meta_default_source

        (temp_project / "meta.yaml").write_text("source: yaml_db")
        (temp_project / "meta.yml").write_text("source: yml_db")
        project = local_project(temp_project)
        assert _charts_meta_default_source(project.directory(".")) == "yml_db"

    def test_explicit_root_finds_meta_at_root(
        self, tmp_path, local_project: Callable[..., FilesystemProject]
    ):
        """When root_dir is explicitly given, meta.yml at root is found."""
        repo = (tmp_path / "repo").resolve()
        repo.mkdir()
        (repo / "meta.yml").write_text("source: root_db\n")

        (repo / "pkg" / "charts").mkdir(parents=True)
        board_path = repo / "pkg" / "charts" / "dashboard.yaml"
        board_path.write_text("title: test\n")

        project = local_project(repo)
        board_file = project.path_for_fspath(board_path)
        files = find_meta_files(board_file, project.directory("."))
        assert any(f.relpath == "meta.yml" for f in files)

    def test_non_dot_root_stops_walk_at_root(
        self, nested_dirs, local_project: Callable[..., FilesystemProject]
    ):
        """A root_dir below the project root must not climb above itself."""
        root = nested_dirs["root"]
        project = local_project(root)
        board_file = project.path_for_fspath(nested_dirs["board_path"])

        files = find_meta_files(board_file, project.directory("analytics"))

        # Stops at analytics/; the root-level meta.yml is above the bound.
        relpaths = [f.relpath for f in files]
        assert relpaths == ["analytics/meta.yml", "analytics/sales/meta.yml"]


# ============================================================================
# compile_file integration
# ============================================================================


class TestCompileFileWithMeta:
    def test_applies_meta_queries(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump(
                {
                    "queries": {
                        "shared_query": {"sql": "SELECT 'shared'", "source": "db"}
                    }
                }
            )
        )
        board = {
            "title": "Test Dashboard",
            "charts": {
                "my_chart": {"query": "shared_query", "type": "kpi", "value": "count"}
            },
            "rows": ["my_chart"],
        }
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert "shared_query" in result.query_registry
        assert result.query_registry["shared_query"].sql == "SELECT 'shared'"

    def test_apply_meta_false_skips_meta(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump(
                {"queries": {"meta_query": {"sql": "SELECT 'meta'", "source": "db"}}}
            )
        )
        board = {
            "title": "Test dbt charts",
            "queries": {"board_query": {"sql": "SELECT 'board'", "source": "db"}},
            "charts": {
                "my_chart": {"query": "board_query", "type": "kpi", "value": "count"}
            },
            "rows": ["my_chart"],
        }
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=False
        )

        assert result.success
        assert "board_query" in result.query_registry
        assert "meta_query" not in result.query_registry

    def test_board_overrides_meta_query(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump({"queries": {"q1": {"sql": "SELECT 'meta'", "source": "db"}}})
        )
        board = {
            "title": "Test Dashboard",
            "queries": {"q1": {"sql": "SELECT 'board'", "source": "db"}},
            "charts": {"my_chart": {"query": "q1", "type": "kpi", "value": "count"}},
            "rows": ["my_chart"],
        }
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert result.query_registry["q1"].sql == "SELECT 'board'"

    def test_meta_charts_available(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump(
                {
                    "charts": {
                        "shared_chart": {
                            "query": "test_query",
                            "type": "line",
                            "x": "date",
                            "y": "value",
                        }
                    }
                }
            )
        )
        board = {
            "title": "Test Dashboard",
            "queries": {
                "test_query": {"sql": "SELECT 1 as date, 10 as value", "source": "db"}
            },
            "rows": ["shared_chart"],
        }
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert "shared_chart" in result.board.charts

    def test_board_width_survives_sibling_end_to_end(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        """End-to-end: meta frame.width survives a board setting frame.margin,
        all the way through to the resolved style.
        """
        (temp_project / "meta.yml").write_text(
            yaml.dump({"style": {"frame": {"width": 700}}})
        )
        board = {
            "title": "T",
            "rows": [{"cols": [{"text": "hi"}]}],
            "style": {"frame": {"margin": 33}},
        }
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert result.board.resolved_style.frame.width == 700
        assert result.board.resolved_style.frame.margin == 33

    def test_threads_meta_lint(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        (temp_project / "meta.yml").write_text(
            yaml.dump({"lint": {"ignore": ["WARN-FANOUT-RISK"]}})
        )
        board = {"title": "T", "rows": [{"cols": [{"text": "hi"}]}]}
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert result.meta_lint is not None
        assert "WARN-FANOUT-RISK" in result.meta_lint.ignore

    def test_malformed_meta_fails_loud(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        # D-04: a malformed meta.yml is a hard error, not warn-and-continue.
        (temp_project / "meta.yml").write_text("source: [unclosed list\n")
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(
            yaml.dump({"title": "T", "rows": [{"cols": [{"text": "hi"}]}]})
        )

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert not result.success
        assert result.errors

    def test_board_wrapper_key_is_rejected(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        # The old `board:` wrapper is gone — a meta.yml using it merges the
        # stray `board` key under the board, which AuthoredBoard rejects.
        (temp_project / "meta.yml").write_text(yaml.dump({"board": {"theme": "cream"}}))
        board_path = temp_project / "dashboard.yaml"
        board_path.write_text(
            yaml.dump({"title": "T", "rows": [{"cols": [{"text": "hi"}]}]})
        )

        project = local_project(temp_project)
        result = compile_file(
            project.path("dashboard.yaml").read_board(), apply_meta=True
        )

        assert not result.success
        assert result.errors

    def test_nested_meta_chain(
        self, temp_project, local_project: Callable[..., FilesystemProject]
    ):
        subdir = temp_project / "reports"
        subdir.mkdir()
        (temp_project / "meta.yml").write_text(
            yaml.dump(
                {
                    "queries": {"root_query": {"sql": "SELECT 'root'", "source": "db"}},
                    "style": {"frame": {"width": 700}},
                }
            )
        )
        (subdir / "meta.yml").write_text(
            yaml.dump(
                {
                    "queries": {
                        "report_query": {"sql": "SELECT 'report'", "source": "db"}
                    }
                }
            )
        )
        board = {
            "title": "Report Dashboard",
            "charts": {
                "chart1": {"query": "root_query", "type": "kpi", "value": "count"},
                "chart2": {"query": "report_query", "type": "kpi", "value": "count"},
            },
            "rows": ["chart1", "chart2"],
        }
        board_path = subdir / "dashboard.yaml"
        board_path.write_text(yaml.dump(board))

        project = local_project(temp_project)
        result = compile_file(
            project.path("reports/dashboard.yaml").read_board(), apply_meta=True
        )

        assert result.success
        assert "root_query" in result.query_registry
        assert "report_query" in result.query_registry

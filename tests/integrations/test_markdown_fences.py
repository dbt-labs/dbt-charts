"""Tests for the dbt charts superfences handlers.

``fence_dbt_charts`` renders inline YAML or file references to SVG/HTML.
``fence_dbt_charts_example`` renders code-plus-render example layouts.
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import markdown
import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import (
    BoardRenderResult,
    render_dashboard as real_render_dashboard,
)
from dbt_charts.integrations.markdown import (
    _SOURCE_NOT_FOUND_RE,
    _inline_playground_queries,
    _playground_url,
    _resolve_project_dir,
    fence_dbt_charts,
    fence_dbt_charts_example,
    project_dir_override,
)

from .._paths import DBT_CHARTS_DIR


def _ok(svg: str) -> BoardRenderResult:
    """Build a successful BoardRenderResult envelope wrapping raw SVG output."""
    return BoardRenderResult(status="ok", data=svg)


def _patch_project_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Patch _resolve_project_dir to return tmp_path for the duration of a test."""
    monkeypatch.setattr(
        "dbt_charts.integrations.markdown._resolve_project_dir",
        lambda: tmp_path,
    )


# ---------------------------------------------------------------------------
# fence_dbt_charts — render-only handler
# ---------------------------------------------------------------------------


class TestFenceDbtCharts:
    """Test the render-only superfences handler."""

    def test_renders_inline_yaml(self, tmp_path, monkeypatch):
        """Inline YAML source is rendered via render_dashboard."""

        _patch_project_dir(monkeypatch, tmp_path)
        source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"
        fake_svg = "<svg>ok</svg>"

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok(fake_svg),
        ):
            result = fence_dbt_charts(source, "dbt-charts", "dbt-charts", {}, None)

        assert "dbt-charts-embed" in result
        assert fake_svg in result

    def test_renders_file_reference(self, tmp_path, monkeypatch):
        """file=path option reads from disk and renders via render_dashboard."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")
        fake_svg = "<svg>file</svg>"

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok(fake_svg),
        ):
            result = fence_dbt_charts(
                "", "dbt-charts", "dbt-charts", {"file": "test.yml"}, None
            )

        assert fake_svg in result

    def test_reads_file_option_from_superfences_attrs(self, tmp_path, monkeypatch):
        """Render-only fences should also honor attr-list ``file=...`` input."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts(
                "",
                "dbt-charts",
                "dbt-charts",
                {},
                None,
                attrs={"file": "test.yml"},
            )

        assert "<svg>ok</svg>" in result

    def test_renders_markdown_board_in_boards_dir(self, tmp_path, monkeypatch):
        """A ``.md`` board under ``charts/`` is translated to YAML before rendering."""
        boards_dir = tmp_path / "charts"
        boards_dir.mkdir()
        (boards_dir / "report.md").write_text("# Title\n\nSome prose.\n")

        _patch_project_dir(monkeypatch, tmp_path)
        captured: dict[str, object] = {}

        def fake_render_dashboard(*, board, **_kwargs):
            captured["yaml_content"] = board.content
            return _ok("<svg>md</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = fence_dbt_charts(
                "", "dbt-charts", "dbt-charts", {"file": "charts/report.md"}, None
            )

        assert "dbt-charts-embed" in result
        assert "<svg>md</svg>" in result
        # Markdown was converted to a board mapping, not passed through raw.
        yaml_out = str(captured["yaml_content"])
        assert "rows:" in yaml_out or "text:" in yaml_out

    def test_markdown_outside_boards_without_board_key_errors(
        self, tmp_path, monkeypatch
    ):
        """A ``.md`` file outside ``charts/`` with no ``board:`` key is not a board."""
        (tmp_path / "notes.md").write_text("# Just notes\n\nNot a board.\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>unused</svg>"),
        ):
            result = fence_dbt_charts(
                "", "dbt-charts", "dbt-charts", {"file": "notes.md"}, None
            )

        assert "dbt-charts-error" in result
        assert "must declare a" in result  # ERR_MARKDOWN_NOT_BOARD

    def test_markdown_not_board_when_project_under_charts_ancestor(
        self, tmp_path, monkeypatch
    ):
        """in_charts is project-relative: an ancestor dir named ``charts`` must not
        turn a root-level markdown file into a board.

        Regression for the absolute-path check ``"charts" in <abs path>.parts``,
        which false-positived whenever the project checkout itself lived under a
        path segment named ``charts``.
        """
        project_dir = tmp_path / "charts" / "myproj"
        project_dir.mkdir(parents=True)
        (project_dir / "notes.md").write_text("# Just notes\n\nNot a board.\n")

        _patch_project_dir(monkeypatch, project_dir)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>unused</svg>"),
        ):
            result = fence_dbt_charts(
                "", "dbt-charts", "dbt-charts", {"file": "notes.md"}, None
            )

        assert "dbt-charts-error" in result
        assert "must declare a" in result  # ERR_MARKDOWN_NOT_BOARD

    def test_retries_file_backed_docs_examples_against_examples_dir(
        self, tmp_path, monkeypatch
    ):
        """File-backed fences should retry from the shared ``examples/`` root."""
        docs_board = tmp_path / "docs" / "test.yml"
        docs_board.parent.mkdir()
        docs_board.write_text(
            "charts:\n  c1:\n    query: _doc_examples.yaml#sales\n    type: bar\n"
        )

        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()

        _patch_project_dir(monkeypatch, tmp_path)

        calls: list[tuple[object, object]] = []

        def fake_render_dashboard(
            *,
            board,
            project,
            adapter_registry,
            result_cache,
            builtin_variables,
            format,
        ):
            calls.append((project.root, builtin_variables["this_dir"]["name"]))
            if project.root == tmp_path:
                # The real compile's error for the missing `file#q` file.
                return real_render_dashboard(
                    board=board,
                    project=project,
                    adapter_registry=adapter_registry,
                    result_cache=result_cache,
                    builtin_variables=builtin_variables,
                    format=format,
                )
            assert project.root == examples_dir
            assert builtin_variables["this_dir"]["name"] == examples_dir.name
            return _ok("<svg>ok</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = fence_dbt_charts(
                "",
                "dbt-charts",
                "dbt-charts",
                {"file": "docs/test.yml"},
                None,
            )

        assert calls == [
            (tmp_path, docs_board.parent.name),
            (examples_dir, examples_dir.name),
        ]
        assert "<svg>ok</svg>" in result

    def test_render_error_produces_error_div(self, tmp_path, monkeypatch):
        """Render failures produce an error div, not a crash."""

        _patch_project_dir(monkeypatch, tmp_path)
        source = "charts:\n  c1:\n    type: bar\n"

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=ValueError("bad data"),
        ):
            result = fence_dbt_charts(source, "dbt-charts", "dbt-charts", {}, None)

        assert "dbt-charts-error" in result
        assert "bad data" in result

    def test_empty_source_no_file_gives_error(self, tmp_path, monkeypatch):
        """No source and no file option produces an error."""

        _patch_project_dir(monkeypatch, tmp_path)
        result = fence_dbt_charts("", "dbt-charts", "dbt-charts", {}, None)
        assert "dbt-charts-error" in result

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        """Paths escaping project_dir produce an error div."""

        _patch_project_dir(monkeypatch, tmp_path)
        result = fence_dbt_charts(
            "",
            "dbt-charts",
            "dbt-charts",
            {"file": "../../etc/passwd"},
            None,
        )
        assert "dbt-charts-error" in result
        assert "escapes project directory" in result


# ---------------------------------------------------------------------------
# fence_dbt_charts_example — code+render handler
# ---------------------------------------------------------------------------


class TestFenceDbtChartsExample:
    """Test the code-plus-render superfences handler."""

    def test_side_by_side_default(self, tmp_path, monkeypatch):
        """Default layout is side-by-side with code and render panels."""
        board = tmp_path / "test.yml"
        yaml_source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"
        board.write_text(yaml_source)

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml"},
                None,
            )

        assert "example-container" in result
        assert "example-code" in result
        assert "example-render" in result
        assert 'data-example-modal-trigger="true"' in result
        assert "<svg>ok</svg>" in result

    def test_stacked_layout(self, tmp_path, monkeypatch):
        """format=stacked adds the example-stacked CSS class."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml", "format": "stacked"},
                None,
            )

        assert "example-stacked" in result

    def test_yaml_only_does_not_render(self, tmp_path, monkeypatch):
        """format=yaml-only should not call render_dashboard."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
        ) as mock_render:
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml", "format": "yaml-only"},
                None,
            )

        mock_render.assert_not_called()
        assert "example-yaml-only" in result
        assert "example-render" not in result

    def test_render_only_no_code(self, tmp_path, monkeypatch):
        """format=render-only should not include code panel."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml", "format": "render-only"},
                None,
            )

        assert "example-render-only" in result
        assert "example-code" not in result

    def test_playground_link_present(self, tmp_path, monkeypatch):
        """Example output includes a playground link."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml"},
                None,
            )

        assert "playground-link" in result

    def test_inline_yaml_example(self, tmp_path, monkeypatch):
        """Inline YAML works for examples too."""

        _patch_project_dir(monkeypatch, tmp_path)
        source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts_example(
                source,
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
            )

        assert "example-container" in result

    def test_reads_file_option_from_superfences_attrs(self, tmp_path, monkeypatch):
        """MkDocs passes fence attributes in ``kwargs['attrs']``."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
                attrs={"file": "test.yml", "format": "stacked"},
            )

        assert "example-stacked" in result
        assert "<svg>ok</svg>" in result

    def test_retries_inline_docs_examples_against_examples_dir(
        self, tmp_path, monkeypatch
    ):
        """Inline doc examples can fall back to the shared ``examples/`` root."""
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()

        _patch_project_dir(monkeypatch, tmp_path)

        calls: list[object] = []

        def fake_render_dashboard(
            *,
            board,
            project,
            adapter_registry,
            result_cache,
            builtin_variables,
            format,
        ):
            calls.append(project.root)
            if project.root == tmp_path:
                # The real compile's error for the missing `file#q` file.
                return real_render_dashboard(
                    board=board,
                    project=project,
                    adapter_registry=adapter_registry,
                    result_cache=result_cache,
                    builtin_variables=builtin_variables,
                    format=format,
                )
            assert project.root == examples_dir
            assert builtin_variables["this_dir"]["name"] == examples_dir.name
            return _ok("<svg>ok</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = fence_dbt_charts_example(
                "charts:\n  c1:\n    query: _doc_examples.yaml#sales\n    type: bar\n",
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
            )

        assert calls == [tmp_path, examples_dir]
        assert "<svg>ok</svg>" in result

    def test_prefers_examples_dir_when_shared_sources_exist(
        self, tmp_path, monkeypatch
    ):
        """Inline examples using named sources render from ``examples/playground`` first."""
        examples_dir = tmp_path / "examples" / "playground"
        examples_dir.mkdir(parents=True)
        (examples_dir / "dbt_charts.yml").write_text(
            "sources:\n  dundersign_db:\n    type: duckdb\n    path: dundersign.duckdb\n"
        )

        _patch_project_dir(monkeypatch, tmp_path)

        calls: list[object] = []

        def fake_render_dashboard(
            *,
            board,
            project,
            adapter_registry,
            result_cache,
            builtin_variables,
            format,
        ):
            calls.append(project.root)
            assert project.root == examples_dir
            assert builtin_variables["this_dir"]["name"] == examples_dir.name
            return _ok("<svg>ok</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = fence_dbt_charts_example(
                "source: dundersign_db\n"
                "charts:\n"
                "  c1:\n"
                "    query: q\n"
                "    type: bar\n"
                "    x: a\n"
                "    y: b\n"
                "queries:\n"
                "  q:\n"
                "    sql: SELECT 1 AS a, 2 AS b\n",
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
            )

        assert calls == [examples_dir]
        assert "<svg>ok</svg>" in result

    def test_superfences_markdown_round_trip_keeps_file_attr(
        self, tmp_path, monkeypatch
    ):
        """A real Markdown parse should pass ``file=...`` through to the handler."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            html = markdown.markdown(
                "```dbt-charts-example {file=test.yml format=stacked}\n```\n",
                extensions=["attr_list", "pymdownx.superfences"],
                extension_configs={
                    "pymdownx.superfences": {
                        "custom_fences": [
                            {
                                "name": "dbt-charts-example",
                                "class": "dbt-charts-example",
                                "format": fence_dbt_charts_example,
                            }
                        ]
                    }
                },
            )

        assert "example-stacked" in html
        assert "<svg>ok</svg>" in html
        assert "example-code" in html

    def test_render_error_still_shows_code(self, tmp_path, monkeypatch):
        """When rendering fails, code panel is still shown."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        _patch_project_dir(monkeypatch, tmp_path)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=ValueError("data error"),
        ):
            result = fence_dbt_charts_example(
                "",
                "dbt-charts-example",
                "dbt-charts-example",
                {"file": "test.yml"},
                None,
            )

        assert "example-code" in result
        assert "dbt-charts-error" in result

    def test_invalid_format_rejected(self, tmp_path, monkeypatch):
        """Unknown format= option produces an error div."""

        _patch_project_dir(monkeypatch, tmp_path)
        result = fence_dbt_charts_example(
            "charts:\n  c1:\n    type: bar\n",
            "dbt-charts-example",
            "dbt-charts-example",
            {"format": "invalid-layout"},
            None,
        )
        assert "dbt-charts-error" in result
        assert "unknown format" in result

    def test_playground_link_inlines_external_queries(self, tmp_path, monkeypatch):
        """Docs examples should hand the playground a self-contained YAML payload."""
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "_doc_examples.yaml").write_text(
            "queries:\n"
            "  sales_by_product:\n"
            "    values:\n"
            "      columns: [product, revenue]\n"
            "      values:\n"
            "        - [Widget, 10]\n"
        )

        _patch_project_dir(monkeypatch, tmp_path)

        captured: dict[str, str] = {}

        def fake_playground_url(yaml_source):
            captured["yaml"] = yaml_source
            return "http://localhost:5001/?y=test"

        with (
            patch(
                "dbt_charts.integrations.markdown.render_dashboard",
                return_value=_ok("<svg>ok</svg>"),
            ),
            patch(
                "dbt_charts.integrations.markdown._playground_url",
                side_effect=fake_playground_url,
            ),
        ):
            result = fence_dbt_charts_example(
                "charts:\n"
                "  ranked_products:\n"
                "    query: _doc_examples.yaml#sales_by_product\n"
                "    type: bar\n"
                "    x: product\n"
                "    y: revenue\n",
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
            )

        assert "http://localhost:5001/?y=test" in result
        assert "query: sales_by_product" in captured["yaml"]
        assert "_doc_examples.yaml#sales_by_product" not in captured["yaml"]
        assert "queries:" in captured["yaml"]
        assert "values:" in captured["yaml"]


# ---------------------------------------------------------------------------
# Live render against the shipped playground project (no render_dashboard mock)
# ---------------------------------------------------------------------------


class TestFenceResolvesSharedPlaygroundSource:
    """Docs ``dbt-charts-example`` fences resolve ``examples_db`` from the shipped gallery.

    The docs examples declare ``source: examples_db`` and query the
    ``ecommerce_orders`` table — both live in ``examples/playground``
    (``dbt_charts.yml`` + ``examples.duckdb``), the shared project the fence
    renderer must fall back to. This drives the real render path (no
    ``render_dashboard`` mock): a mocked test can't catch source-wiring drift.
    """

    def test_inline_docs_example_resolves_examples_db(self, caplog):
        """A repo-rooted docs example runs its query with zero source-not-found warnings."""
        example = (
            "source: examples_db\n"
            "queries:\n"
            "  sales_by_category:\n"
            "    sql: |\n"
            "      SELECT category, SUM(revenue) AS revenue FROM ecommerce_orders\n"
            "      GROUP BY category ORDER BY revenue DESC\n"
            "charts:\n"
            "  main_chart:\n"
            "    query: sales_by_category\n"
            "    type: bar\n"
            "    x: category\n"
            "    y: revenue\n"
        )

        with (
            project_dir_override(DBT_CHARTS_DIR),
            caplog.at_level(logging.WARNING, logger="dbt-charts"),
        ):
            result = fence_dbt_charts_example(
                example, "dbt-charts-example", "dbt-charts-example", {}, None
            )

        source_warnings = [
            r.getMessage()
            for r in caplog.records
            if _SOURCE_NOT_FOUND_RE.search(r.getMessage())
        ]
        assert source_warnings == [], source_warnings
        assert "dbt-charts-error" not in result
        assert "<svg" in result


# ---------------------------------------------------------------------------
# Adapter registry construction and cleanup
# ---------------------------------------------------------------------------


class TestFenceDbtChartsClosesAdapterRegistry:
    """fence_dbt_charts builds an adapter registry directly and closes it after rendering."""

    @pytest.fixture
    def registry_spy(self):
        """Yield (fake_registry, build_calls) with build_adapter_registry stubbed."""
        fake_registry = MagicMock()
        build_calls: list[object] = []

        def fake_build_adapter_registry(project, *, read_only=True, **kwargs):
            build_calls.append(project)
            return fake_registry

        with patch(
            "dbt_charts.integrations.markdown.build_adapter_registry",
            side_effect=fake_build_adapter_registry,
        ):
            yield fake_registry, build_calls

    def test_fence_dbt_charts_closes_registry(
        self, tmp_path, monkeypatch, registry_spy
    ):
        """fence_dbt_charts builds an adapter registry and closes it once rendering finishes."""

        _patch_project_dir(monkeypatch, tmp_path)
        fake_registry, build_calls = registry_spy

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts(
                "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n",
                "dbt-charts",
                "dbt-charts",
                {},
                None,
            )

        assert build_calls, "build_adapter_registry was never called"
        assert isinstance(build_calls[0], FilesystemProject)
        assert fake_registry.close.call_count > 0, "adapter registry was never closed"
        assert "<svg>ok</svg>" in result

    def test_fence_dbt_charts_example_closes_registry(
        self, tmp_path, monkeypatch, registry_spy
    ):
        """fence_dbt_charts_example builds an adapter registry and closes it once rendering finishes."""

        _patch_project_dir(monkeypatch, tmp_path)
        fake_registry, build_calls = registry_spy

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = fence_dbt_charts_example(
                "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n",
                "dbt-charts-example",
                "dbt-charts-example",
                {},
                None,
            )

        assert build_calls, "build_adapter_registry was never called"
        assert isinstance(build_calls[0], FilesystemProject)
        assert fake_registry.close.call_count > 0, "adapter registry was never closed"
        assert "<svg>ok</svg>" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestPlaygroundUrl:
    """Test playground URL generation."""

    def test_targets_the_hosted_playground(self):
        url = _playground_url("type: bar")
        assert url.startswith("https://play.dbtcharts.com/?y=")


class TestInlinePlaygroundQueries:
    def test_inlines_external_doc_queries_from_examples_dir(self, tmp_path):
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()
        (examples_dir / "_doc_examples.yaml").write_text(
            "queries:\n"
            "  sales_by_product:\n"
            "    values:\n"
            "      columns: [product, revenue]\n"
            "      values:\n"
            "        - [Widget, 10]\n"
        )

        rewritten = _inline_playground_queries(
            "charts:\n"
            "  ranked_products:\n"
            "    query: _doc_examples.yaml#sales_by_product\n"
            "    type: bar\n"
            "    x: product\n"
            "    y: revenue\n",
            tmp_path,
            tmp_path,
        )

        assert "query: sales_by_product" in rewritten
        assert "_doc_examples.yaml#sales_by_product" not in rewritten
        assert "queries:" in rewritten
        assert "sales_by_product:" in rewritten


class TestResolveProjectDir:
    """_resolve_project_dir walks find_dct_root → find_repo_root → cwd."""

    def test_prefers_dct_root_over_git_root(self, tmp_path, monkeypatch) -> None:
        git_root = tmp_path
        dct_root = tmp_path / "subproject"
        dct_root.mkdir()
        (git_root / ".git").mkdir()
        (dct_root / "dbt_charts.yml").write_text("name: x\n")
        nested = dct_root / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)

        assert _resolve_project_dir() == dct_root.resolve()

    def test_falls_back_to_git_root_when_no_dct_marker(
        self, tmp_path, monkeypatch
    ) -> None:
        """Pyproject-only Python project: .git is the project root."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        nested = tmp_path / "src"
        nested.mkdir()
        monkeypatch.chdir(nested)

        assert _resolve_project_dir() == tmp_path.resolve()

    def test_falls_back_to_cwd_when_no_marker(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _resolve_project_dir() == tmp_path.resolve()

    def test_override_wins_over_walk(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "dbt_charts.yml").write_text("name: x\n")
        pinned = tmp_path / "pinned"
        pinned.mkdir()
        nested = tmp_path / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)

        with project_dir_override(pinned):
            assert _resolve_project_dir() == pinned.resolve()


class TestPlaygroundUrlIgnoresEnv:
    """The playground link always targets the hosted playground — no override."""

    def test_fence_dbt_charts_example_ignores_dct_playground_url_env(
        self, tmp_path, monkeypatch
    ) -> None:
        (tmp_path / "dbt_charts.yml").write_text("name: x\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DCT_PLAYGROUND_URL", "http://should-not-be-read.test")
        source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"

        with patch(
            "dbt_charts.integrations.markdown._render_with_fallback",
            return_value="<svg/>",
        ):
            result = fence_dbt_charts_example(
                source, "dbt-charts-example", "dbt-charts-example", {}, None
            )

        assert "https://play.dbtcharts.com/?y=" in result
        assert "should-not-be-read" not in result


def test_fence_sends_the_post_an_http_source_declares(tmp_path, monkeypatch):
    """Docs authors write the fences, so the renderer keeps POST http sources."""
    _patch_project_dir(monkeypatch, tmp_path)
    source = (
        "queries:\n  hits:\n    type: http\n"
        "    url: https://api.example.com/search\n    method: POST\n"
        "    json_path: $.hits\n"
        "charts:\n  t:\n    query: hits\n    type: table\nrows:\n  - t\n"
    )
    response = MagicMock()
    response.json.return_value = {"hits": [{"title": "a"}]}

    with patch(
        "dbt_charts.core.execute.adapters.http_adapter.httpx.Client"
    ) as mock_client:
        post = mock_client.return_value.__enter__.return_value.post
        post.return_value = response
        fence_dbt_charts(source, "dbt-charts", "dbt-charts", {}, None)

    post.assert_called_once()

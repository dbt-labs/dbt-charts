"""Tests for the dbt Charts Python-Markdown extension.

``charts`` fences render as ``dct`` reads them; ``charts-example`` fences
draw a board beside its YAML.
"""

import logging
import re
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
    _EXAMPLE_CSS,
    _SOURCE_NOT_FOUND_RE,
    ChartFenceError,
    DbtChartsExtension,
    _example_html,
    _inline_playground_queries,
    _playground_url,
    _resolve_project_dir,
)

from .._paths import DBT_CHARTS_DIR


def _ok(svg: str) -> BoardRenderResult:
    """Build a successful BoardRenderResult envelope wrapping raw SVG output."""
    return BoardRenderResult(status="ok", data=svg)


_BAR = """\
```charts
type: bar
title: {title}
query:
  columns: [month, revenue]
  values:
    - [Jan, 100]
    - [Feb, 150]
x: month
y: revenue
```
"""

_SHARED_QUERY = """\
```charts
queries:
  sales:
    columns: [month, revenue]
    values:
      - [Jan, 100]
charts:
  first:
    type: bar
    query: sales
    x: month
    y: revenue
rows:
  - first
```

Then the same query as a line.

```charts
type: line
query: sales
x: month
y: revenue
```
"""

_EXAMPLE_BOARD = (
    "charts:\n  c:\n    type: bar\n    query:\n      columns: [a, b]\n"
    "      values:\n        - [x, 1]\n    x: a\n    y: b\nrows:\n  - c\n"
)


def _md(text: str, project_dir: Path, *extensions: str, **config: object) -> str:
    return markdown.markdown(
        text,
        extensions=[
            DbtChartsExtension(project_dir=str(project_dir), **config),
            *extensions,
        ],
    )


class TestExtension:
    """Real renders, inline data, no project."""

    def test_loads_by_module_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        html = markdown.markdown(
            _BAR.format(title="Revenue"),
            extensions=["dbt_charts.integrations.markdown"],
        )
        assert '<div class="dbt-charts-embed"><svg' in html

    def test_a_bare_chart_renders_in_place(self, tmp_path):
        html = _md(f"Before.\n\n{_BAR.format(title='Revenue')}\nAfter.\n", tmp_path)
        assert html.index("<p>Before.</p>") < html.index("<svg") < html.index("After.")

    def test_fences_share_a_query(self, tmp_path):
        html = _md(_SHARED_QUERY, tmp_path)
        assert html.count('<div class="dbt-charts-embed"><svg') == 2

    def test_fonts_are_embedded_once_per_page(self, tmp_path):
        page = _BAR.format(title="One") + "\n" + _BAR.format(title="Two")
        html = _md(page, tmp_path)
        payloads = re.findall(r"data:font/woff2;base64,([A-Za-z0-9+/]{64})", html)

        assert payloads
        assert len(payloads) == len(set(payloads)) == html.count("@font-face")
        assert html.count("<style>\n@font-face") == 1
        assert "/static/fonts/" not in html

    def test_hosted_fonts_when_embedding_is_off(self, tmp_path):
        html = _md(_BAR.format(title="One"), tmp_path, embed_fonts=False)
        assert "/static/fonts/" in html
        assert "data:font/" not in html

    def test_variables_reach_the_page(self, tmp_path):
        page = (
            "---\nboard:\n  variables:\n    region:\n      default: East\n---\n\n"
            + _BAR.format(title='"Revenue in {{ region }}"')
        )
        assert "Revenue in East" in _md(page, tmp_path)
        assert "Revenue in West" in _md(page, tmp_path, variables={"region": "West"})

    def test_frontmatter_is_not_printed(self, tmp_path):
        page = "---\nboard:\n  title: Sales\n---\n\n" + _BAR.format(title="x")
        html = _md(page, tmp_path, embed_fonts=False)
        assert "board:" not in html
        assert "<hr" not in html
        assert "<p></p>" not in html

    def test_a_broken_fence_fails_naming_its_line(self, tmp_path):
        with pytest.raises(ChartFenceError, match="line 3"):
            _md("Intro.\n\n```charts\ntype: bar\ncolour: red\n```\n", tmp_path)

    def test_a_query_error_fails_the_page(self, tmp_path):
        page = _BAR.format(title="x").replace("y: revenue", "y: no_such_column")
        with pytest.raises(ChartFenceError):
            _md(page, tmp_path)

    def test_a_fence_shown_in_a_longer_fence_stays_code(self, tmp_path):
        html = _md(f"````markdown\n{_BAR.format(title='x')}````\n", tmp_path)
        assert "<svg" not in html
        assert "```charts" in html

    def test_a_page_with_no_fences_is_untouched(self, tmp_path):
        page = "# Hi\n\n```yaml\na: 1\n```\n"
        assert _md(page, tmp_path) == markdown.markdown(page)

    def test_an_example_in_a_tab_renders_inside_it(self, tmp_path):
        indented = "".join(f"    {line}\n" for line in _EXAMPLE_BOARD.splitlines())
        page = (
            '=== "One"\n\n    ```charts-example {format=render-only}\n'
            f"{indented}    ```\n"
        )
        html = _md(page, tmp_path, "pymdownx.tabbed", "pymdownx.superfences")
        tab = html[html.index('class="tabbed-content"') :]

        assert tab.index(
            '<div class="dbt-charts-example dbt-charts-example--render-only"'
        ) < (tab.index("<svg"))
        assert "<p><style>" not in html

    def test_an_example_shows_its_yaml_beside_the_board(self, tmp_path):
        html = _md(f"```charts-example\n{_EXAMPLE_BOARD}```\n", tmp_path)
        assert 'class="dbt-charts-example-code"' in html
        assert 'class="dbt-charts-example-board"><svg' in html

    def test_an_example_page_carries_the_example_css_once(self, tmp_path):
        stacked = _EXAMPLE_BOARD.replace("[x, 1]", "[y, 2]")
        page = (
            f"```charts-example\n{_EXAMPLE_BOARD}```\n\n"
            f"```charts-example {{format=stacked}}\n{stacked}```\n"
        )
        html = _md(page, tmp_path)

        assert html.count(_EXAMPLE_CSS) == 1
        assert html.index(_EXAMPLE_CSS) < html.index('class="dbt-charts-example')

    def test_the_example_css_does_not_depend_on_embedded_fonts(self, tmp_path):
        page = f"```charts-example {{format=yaml-only}}\n{_EXAMPLE_BOARD}```\n"
        assert _EXAMPLE_CSS in _md(page, tmp_path, embed_fonts=False)

    def test_a_page_of_charts_alone_carries_no_example_css(self, tmp_path):
        assert _EXAMPLE_CSS not in _md(_BAR.format(title="One"), tmp_path)

    def test_client_side_highlighters_leave_the_example_yaml_alone(self, tmp_path):
        html = _md(f"```charts-example\n{_EXAMPLE_BOARD}```\n", tmp_path)
        code = html[
            html.index('class="dbt-charts-example-code"') : html.index(
                'class="dbt-charts-example-render"'
            )
        ]

        assert '<code class="nohighlight">' in code
        assert "language-" not in code

    def test_a_tab_example_hides_its_yaml_in_a_drawer_under_the_board(self, tmp_path):
        html = _md(f"```charts-example {{format=tab}}\n{_EXAMPLE_BOARD}```\n", tmp_path)
        board = html.index('class="dbt-charts-example-board"><svg')
        drawer = html.index('<details class="dbt-charts-example-drawer">')

        assert board < drawer < html.index('class="dbt-charts-example-code"')
        assert "See the YAML" in html[drawer:]

    def test_an_example_may_show_a_failed_query(self, tmp_path):
        board = _EXAMPLE_BOARD.replace("y: b", "y: no_such_column")
        page = f"```charts-example {{allow-chart-errors=true}}\n{board}```\n"
        with pytest.raises(ChartFenceError):
            _md(page.replace(" {allow-chart-errors=true}", ""), tmp_path)

        assert 'class="dbt-charts-example-board"><svg' in _md(page, tmp_path)

    def test_a_broken_example_fails_naming_its_line(self, tmp_path):
        with pytest.raises(ChartFenceError, match="line 3"):
            _md(
                "Intro.\n\n```charts-example\ncharts:\n  c:\n    type: nope\n```\n",
                tmp_path,
            )


_RANDOM_BOARD = """\
charts:
  draw:
    type: table
    query:
      sql: SELECT 'drawn' || CAST(CAST(floor(random() * 1e9) AS BIGINT) AS VARCHAR) AS v
      source: mem
rows:
  - draw
"""


class TestQueryCache:
    @pytest.fixture(autouse=True)
    def _no_cache_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DCT_CACHE_PATH", raising=False)

    def _project(self, tmp_path: Path) -> Path:
        (tmp_path / "dbt_charts.yml").write_text(
            "sources:\n  mem:\n    type: duckdb\n    path: ':memory:'\n"
            "cache:\n  path: cache/results.duckdb\n"
        )
        return tmp_path

    def _drawn(self, html: str) -> str:
        (value,) = set(re.findall(r"drawn\d+", html))
        return value

    @pytest.mark.parametrize("fence", ["charts", "charts-example"])
    def test_a_rebuild_reuses_the_query_result(
        self, tmp_path: Path, fence: str
    ) -> None:
        project = self._project(tmp_path)
        page = f"```{fence}\n{_RANDOM_BOARD}```\n"
        assert self._drawn(_md(page, project)) == self._drawn(_md(page, project))

    @pytest.mark.parametrize("fence", ["charts", "charts-example"])
    def test_dct_cache_path_beats_the_project_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fence: str
    ) -> None:
        project = self._project(tmp_path)
        chosen = tmp_path / "chosen.duckdb"
        monkeypatch.setenv("DCT_CACHE_PATH", str(chosen))
        _md(f"```{fence}\n{_RANDOM_BOARD}```\n", project)
        assert chosen.exists()
        assert not (project / "cache").exists()


# ---------------------------------------------------------------------------
# _example_html — code + render
# ---------------------------------------------------------------------------


class TestFenceDbtChartsExample:
    """Test the code-plus-render superfences handler."""

    def test_side_by_side_default(self, tmp_path, monkeypatch):
        """Default layout is side-by-side with code and render panels."""
        board = tmp_path / "test.yml"
        yaml_source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"
        board.write_text(yaml_source)

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = _example_html("", {"file": "test.yml"}, tmp_path, False)

        assert "dbt-charts-example" in result
        assert "dbt-charts-example-code" in result
        assert "dbt-charts-example-render" in result
        assert "<svg>ok</svg>" in result

    def test_stacked_layout(self, tmp_path, monkeypatch):
        """format=stacked adds the dbt-charts-example--stacked CSS class."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = _example_html(
                "", {"file": "test.yml", "format": "stacked"}, tmp_path, False
            )

        assert "dbt-charts-example--stacked" in result

    def test_yaml_only_does_not_render(self, tmp_path, monkeypatch):
        """format=yaml-only should not call render_dashboard."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
        ) as mock_render:
            result = _example_html(
                "", {"file": "test.yml", "format": "yaml-only"}, tmp_path, False
            )

        mock_render.assert_not_called()
        assert "dbt-charts-example--yaml-only" in result
        assert "dbt-charts-example-render" not in result

    def test_render_only_no_code(self, tmp_path, monkeypatch):
        """format=render-only should not include code panel."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = _example_html(
                "", {"file": "test.yml", "format": "render-only"}, tmp_path, False
            )

        assert "dbt-charts-example--render-only" in result
        assert "dbt-charts-example-code" not in result

    def test_playground_link_present(self, tmp_path, monkeypatch):
        """Example output includes a playground link."""
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = _example_html("", {"file": "test.yml"}, tmp_path, False)

        assert "dbt-charts-playground-link" in result

    def test_inline_yaml_example(self, tmp_path, monkeypatch):
        """Inline YAML works for examples too."""

        source = "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n"

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = _example_html(source, {}, tmp_path, False)

        assert "dbt-charts-example" in result

    def test_retries_inline_docs_examples_against_examples_dir(
        self, tmp_path, monkeypatch
    ):
        """Inline doc examples can fall back to the shared ``examples/`` root."""
        examples_dir = tmp_path / "examples"
        examples_dir.mkdir()

        calls: list[object] = []

        def fake_render_dashboard(
            *,
            board,
            project,
            env,
            result_cache,
            builtin_variables,
            format,
            standalone,
        ):
            calls.append(project.root)
            if project.root == tmp_path:
                # The real compile's error for the missing `file#q` file.
                return real_render_dashboard(
                    board=board,
                    project=project,
                    env=env,
                    result_cache=result_cache,
                    builtin_variables=builtin_variables,
                    format=format,
                    standalone=standalone,
                )
            assert project.root == examples_dir
            assert builtin_variables["this_dir"]["name"] == examples_dir.name
            return _ok("<svg>ok</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = _example_html(
                "charts:\n  c1:\n    query: _doc_examples.yaml#sales\n    type: bar\n",
                {},
                tmp_path,
                False,
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

        calls: list[object] = []

        def fake_render_dashboard(
            *,
            board,
            project,
            env,
            result_cache,
            builtin_variables,
            format,
            standalone,
        ):
            calls.append(project.root)
            assert project.root == examples_dir
            assert builtin_variables["this_dir"]["name"] == examples_dir.name
            return _ok("<svg>ok</svg>")

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            side_effect=fake_render_dashboard,
        ):
            result = _example_html(
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
                {},
                tmp_path,
                False,
            )

        assert calls == [examples_dir]
        assert "<svg>ok</svg>" in result

    def test_a_render_error_raises(self, tmp_path):
        board = tmp_path / "test.yml"
        board.write_text("charts:\n  c1:\n    type: bar\n")

        with (
            patch(
                "dbt_charts.integrations.markdown.render_dashboard",
                side_effect=ValueError("data error"),
            ),
            pytest.raises(ValueError, match="data error"),
        ):
            _example_html("", {"file": "test.yml"}, tmp_path, False)

    @pytest.mark.parametrize(
        ("options", "layout"),
        [
            ({}, "side-by-side"),
            ({"format": "stacked"}, "stacked"),
            ({"format": "render-only"}, "render-only"),
            ({"format": "yaml-only"}, "yaml-only"),
            ({"format": "tab"}, "tab"),
        ],
    )
    def test_each_layout_names_its_container(
        self, tmp_path: Path, options: dict[str, str], layout: str
    ) -> None:
        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = _example_html(_EXAMPLE_BOARD, options, tmp_path, False)

        assert result.startswith(
            f'<div class="dbt-charts-example dbt-charts-example--{layout}"'
        )

    @pytest.mark.parametrize(
        "layout", ["side-by-side", "stacked", "render-only", "yaml-only", "tab"]
    )
    def test_every_class_outside_the_board_and_yaml_is_prefixed(
        self, tmp_path: Path, layout: str
    ) -> None:
        """The page carries the stylesheet onto other people's sites, so a
        generic class name would catch the host's own rules, and theirs ours."""
        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg/>"),
        ):
            result = _example_html(_EXAMPLE_BOARD, {"format": layout}, tmp_path, False)
        wrapper = re.sub(r"<code[^>]*>.*?</code>", "", result, flags=re.DOTALL)
        classes = {
            name
            for value in re.findall(r'class="([^"]*)"', wrapper)
            for name in value.split()
        }

        assert classes
        assert {c for c in classes if not c.startswith("dbt-charts-")} == set()

    def test_invalid_format_raises(self, tmp_path):
        with pytest.raises(ChartFenceError, match="unknown format"):
            _example_html("type: bar\n", {"format": "invalid-layout"}, tmp_path, False)

    def test_unknown_option_raises(self, tmp_path):
        with pytest.raises(ChartFenceError, match="'formt'"):
            _example_html("type: bar\n", {"formt": "stacked"}, tmp_path, False)

    def test_a_missing_file_names_the_path_as_written(self, tmp_path):
        with pytest.raises(FileNotFoundError) as raised:
            _example_html("", {"file": "charts/nope.yml"}, tmp_path, False)

        assert str(raised.value) == "File not found: charts/nope.yml"

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
            result = _example_html(
                "charts:\n"
                "  ranked_products:\n"
                "    query: _doc_examples.yaml#sales_by_product\n"
                "    type: bar\n"
                "    x: product\n"
                "    y: revenue\n",
                {},
                tmp_path,
                False,
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
    """Docs ``charts-example`` fences resolve ``examples_db`` from the shipped gallery.

    The docs examples declare ``source: examples_db`` and query the
    ``ecommerce_orders`` table — both live in ``examples/playground``
    (``dbt_charts.yml`` + ``examples.duckdb``), the shared project the fence
    renderer must fall back to. This drives the real render path (no
    ``render_dashboard`` mock): a mocked test can't catch source-wiring drift.
    """

    def test_inline_docs_example_resolves_examples_db(self, caplog, tmp_path):
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

        with caplog.at_level(logging.WARNING, logger="dbt-charts"):
            result = _example_html(example, {}, DBT_CHARTS_DIR, False)

        source_warnings = [
            r.getMessage()
            for r in caplog.records
            if _SOURCE_NOT_FOUND_RE.search(r.getMessage())
        ]
        assert source_warnings == [], source_warnings
        assert "<svg" in result


# ---------------------------------------------------------------------------
# Exec env construction and cleanup
# ---------------------------------------------------------------------------


class TestExampleClosesExecEnv:
    """_example_html builds an exec env directly and closes it after rendering."""

    @pytest.fixture
    def registry_spy(self):
        """Yield (fake_registry, build_calls) with build_exec_env stubbed."""
        fake_registry = MagicMock()
        build_calls: list[object] = []

        def fake_build_exec_env(project, *, read_only=True, **kwargs):
            build_calls.append(project)
            return fake_registry

        with patch(
            "dbt_charts.integrations.markdown.build_exec_env",
            side_effect=fake_build_exec_env,
        ):
            yield fake_registry, build_calls

    def test_example_closes_registry(self, tmp_path, monkeypatch, registry_spy):
        """An example builds an exec env and closes it once rendering finishes."""

        fake_registry, build_calls = registry_spy

        with patch(
            "dbt_charts.integrations.markdown.render_dashboard",
            return_value=_ok("<svg>ok</svg>"),
        ):
            result = _example_html(
                "charts:\n  c1:\n    type: bar\n    x: a\n    y: b\n",
                {},
                tmp_path,
                False,
            )

        assert build_calls, "build_exec_env was never called"
        assert isinstance(build_calls[0], FilesystemProject)
        assert fake_registry.backends.close.call_count > 0, "exec env was never closed"
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
    """_resolve_project_dir takes the configured dir, else walks find_dct_root →
    find_repo_root → cwd."""

    def test_prefers_dct_root_over_git_root(self, tmp_path, monkeypatch) -> None:
        git_root = tmp_path
        dct_root = tmp_path / "subproject"
        dct_root.mkdir()
        (git_root / ".git").mkdir()
        (dct_root / "dbt_charts.yml").write_text("name: x\n")
        nested = dct_root / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)

        assert _resolve_project_dir("") == dct_root.resolve()

    def test_falls_back_to_git_root_when_no_dct_marker(
        self, tmp_path, monkeypatch
    ) -> None:
        """Pyproject-only Python project: .git is the project root."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        nested = tmp_path / "src"
        nested.mkdir()
        monkeypatch.chdir(nested)

        assert _resolve_project_dir("") == tmp_path.resolve()

    def test_falls_back_to_cwd_when_no_marker(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _resolve_project_dir("") == tmp_path.resolve()

    def test_configured_dir_wins_over_walk(self, tmp_path, monkeypatch) -> None:
        (tmp_path / "dbt_charts.yml").write_text("name: x\n")
        pinned = tmp_path / "pinned"
        pinned.mkdir()
        nested = tmp_path / "nested"
        nested.mkdir()
        monkeypatch.chdir(nested)

        assert _resolve_project_dir(str(pinned)) == pinned.resolve()


class TestPlaygroundUrlIgnoresEnv:
    """The playground link always targets the hosted playground — no override."""

    def test_example_ignores_dct_playground_url_env(
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
            result = _example_html(source, {}, tmp_path, False)

        assert "https://play.dbtcharts.com/?y=" in result
        assert "should-not-be-read" not in result

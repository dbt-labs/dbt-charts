"""Tests for tonal foundation defaults."""

from collections.abc import Callable
from pathlib import Path
from unittest import mock

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import get_config, reset_config
from dbt_charts.core.execute.adapters import build_adapter_registry

from .._svg_normalize import normalize_same_run_svg


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


class TestTonalFoundationDefaults:
    """Default ink, surface, and canvas colors — structural and cascade tests only."""

    def test_export_formats_inherit_board_background(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """Export surfaces do not force a separate page background; the board
        background from the board style propagates into SVG output."""
        from pathlib import Path

        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.execute.adapters import build_adapter_registry
        from dbt_charts.core.render import render

        yaml = (
            "title: T\nstyle:\n  background: '#cc1122'\n"
            "queries:\n  q:\n    type: values\n"
            "    rows:\n      - {month: Jan, revenue: 100}\ncharts:\n  c:\n    query: q\n    type: bar\n"
            "    x: month\n    y: revenue\nrows:\n  - c"
        )
        result = compile(yaml)
        assert result.board is not None
        assert result.query_registry is not None
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        svg = render(result.board, executor, format="svg").output
        assert "#cc1122" in svg

    def test_dbt_gray_scale_has_expected_slots(self):
        grays = get_config().dbt_grays
        assert "canvas" in grays
        assert "ink" in grays
        # creams uses the same D-025 vocabulary
        assert "canvas" in get_config().dbt_creams
        assert "ink" in get_config().dbt_creams

    def test_editorial_cream_theme_neutrals_match_dbt_creams_scale(self):
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        config = get_config()
        creams = config.dbt_creams
        theme = get_theme_style("paper")
        assert theme.background == creams["canvas"]
        assert theme.font.color == creams["ink"]
        assert theme.variables.font.color == creams["muted"]
        assert theme.border.color == creams["border"]
        # Title color inherits from font.color (InheritSlot) — titles, headings,
        # and prose share one ink. Resolve the cascade to see the inherited
        # value; the raw theme's slot is unset (None) until resolved.
        resolved = resolve_style(theme)
        assert resolved.title.font.color == resolved.font.color


class TestHtmlPageCanvas:
    """HTML converter's outer <body> background tracks the board's own
    style.background, independent of the SVG background override option."""

    _YAML = (
        "title: Test\nqueries:\n  q:\n    type: values\n"
        "    rows:\n      - {month: Jan, revenue: 100}\ncharts:\n  c:\n"
        "    query: q\n    type: bar\n    x: month\n    y: revenue\nrows:\n  - c"
    )

    def _render_html(
        self,
        local_project: Callable[..., FilesystemProject],
        yaml: str | None = None,
        **opts,
    ):
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import render

        result = compile(yaml if yaml is not None else self._YAML)
        assert result.board is not None
        assert result.query_registry is not None
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        return render(result.board, executor, format="html", **opts).output

    def test_html_body_uses_board_background_not_the_svg_background_override(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """HTML body background tracks the board's own style.background; the
        SVG background override option affects the SVG canvas only, not the
        outer HTML page."""
        from dbt_charts.core.compile.config import get_theme_style

        default_background = get_theme_style().background
        html = self._render_html(local_project, background="#ff0000")
        assert "background-color: #ff0000;" not in html
        assert f"background-color: {default_background};" in html

    def test_html_body_tracks_a_non_white_board_background(
        self, local_project: Callable[..., FilesystemProject]
    ):
        """A board authoring a non-white style.background reaches the HTML
        body — the outer page canvas is not hardcoded."""
        yaml = self._YAML.replace(
            "title: Test\n", "title: Test\nstyle:\n  background: '#3b2f2f'\n"
        )
        html = self._render_html(local_project, yaml=yaml)
        assert "background-color: #3b2f2f;" in html

    @pytest.mark.parametrize("theme_name", ["paper", "neon"])
    def test_html_body_tracks_a_non_white_theme_background(
        self, theme_name: str, local_project: Callable[..., FilesystemProject]
    ):
        """A board on a theme whose own style.background isn't white (paper's
        cream, neon's near-black) must reach the HTML body as that theme's
        real color — this is the case the removed, independently-configurable
        style.page.background used to cover; the deletion is a no-op only if
        the board's own background still reaches the page canvas."""
        from dbt_charts.core.compile.config import get_theme_style

        theme_background = get_theme_style(theme_name).background
        assert theme_background != "#FFFFFF"
        yaml = self._YAML.replace(
            "title: Test\n", f"title: Test\ntheme: {theme_name}\n"
        )
        html = self._render_html(local_project, yaml=yaml)
        assert f"background-color: {theme_background};" in html


class TestRenderedSurfaceBackground:
    """Root export surface background — override-based test only."""

    _YAML = (
        "title: Test\nqueries:\n  q:\n    type: values\n"
        "    rows:\n      - {month: Jan, revenue: 100}\ncharts:\n  c:\n"
        "    query: q\n    type: bar\n    x: month\n    y: revenue\nrows:\n  - c"
    )

    def test_png_render_uses_configured_default_scale(
        self, monkeypatch, local_project: Callable[..., FilesystemProject]
    ):
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import renderer as renderer_module

        config = get_config()
        monkeypatch.setattr(config.rendering.png, "scale", 1.5)
        fake_to_png = mock.Mock(return_value=b"png-bytes")
        monkeypatch.setattr(renderer_module, "to_png", fake_to_png)

        result = compile(self._YAML)
        assert result.board is not None
        assert result.query_registry is not None
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        output = renderer_module.render(result.board, executor, format="png").output

        assert output == b"png-bytes"
        fake_to_png.assert_called_once()
        assert fake_to_png.call_args.args[1] == 1.5

    def test_png_conversion_receives_the_viewer_svg(
        self, monkeypatch, local_project: Callable[..., FilesystemProject]
    ):
        """Format selection converts the canonical SVG instead of rerendering it."""
        from dbt_charts.core.compile import compile
        from dbt_charts.core.execute import Executor
        from dbt_charts.core.render import renderer as renderer_module

        yaml = """\
title: Canonical SVG
variables:
  region:
    input: select
    default: North
    options:
      static: [North, South]
queries:
  q:
    type: values
    rows:
      - {month: Jan, revenue: 100}
charts:
  c:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - c
"""
        result = compile(yaml)
        assert result.board is not None
        assert result.query_registry is not None
        assert result.success
        executor = Executor(
            result.board,
            adapter_registry=build_adapter_registry(local_project(Path.cwd())),
            query_registry=result.query_registry,
        )
        viewer_svg = renderer_module.render(result.board, executor, format="svg").output
        assert isinstance(viewer_svg, str)

        fake_to_png = mock.Mock(return_value=b"png-bytes")
        monkeypatch.setattr(renderer_module, "to_png", fake_to_png)
        renderer_module.render(result.board, executor, format="png")
        converted_svg = fake_to_png.call_args.args[0]

        assert normalize_same_run_svg(converted_svg) == normalize_same_run_svg(
            viewer_svg
        )


class TestResolveStyleCaching:
    """resolve_style caching — must reuse cascade results across repeated calls."""

    def test_reuses_resolved_default_style(self):
        """Repeated default style resolution must return the identical cached object."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style

        style = get_theme_style()
        result1 = resolve_style(style)
        result2 = resolve_style(style)
        result3 = resolve_style(style)
        assert result1 is result2
        assert result1 is result3

    # Shared-instance identity and top-level frozen contract are covered by
    # tests in dbt-charts/tests/core/compile/test_style_cascade.py
    # (TestResolveStyleCacheIdentity). They live in the cascade test module
    # because that is where resolve_style itself is exercised; this class
    # retains only the caching identity test above.

"""Regression tests for validate-and-error-fast violations.

Each test documents a case where silent fallback was replaced with an explicit
error.  Tests are written first — they must FAIL before the corresponding
production change and PASS after.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import get_theme_style

# ---------------------------------------------------------------------------
# 1. normalize.queries — unknown query type
# ---------------------------------------------------------------------------


def test_unknown_query_type_raises():
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.normalize.queries import normalize_query

    with pytest.raises(CompilationError, match="unknown type 'foobar'"):
        normalize_query(
            "q1", {"type": "foobar", "sql": "SELECT 1", "source": "x"}, sources={}
        )


# ---------------------------------------------------------------------------
# 4. meta.py — invalid meta file propagates error instead of silently skipping
# ---------------------------------------------------------------------------


def test_invalid_meta_file_raises(local_project: Callable[..., FilesystemProject]):
    from dbt_charts.core.compile.errors import CompilationError
    from dbt_charts.core.compile.parse.meta import load_meta_file

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "meta.yml").write_text("source: [unclosed list\n")
        meta_file = local_project(root).path("meta.yml")

        with pytest.raises(CompilationError):
            load_meta_file(meta_file)


# ---------------------------------------------------------------------------
# 5. compiler.py — empty board YAML returns an error result
# ---------------------------------------------------------------------------


def test_empty_board_yaml_returns_error(
    local_project: Callable[..., FilesystemProject],
):
    from dbt_charts.core.compile.compiler import compile_file

    with tempfile.TemporaryDirectory() as tmpdir:
        board_path = Path(tmpdir) / "empty.yaml"
        board_path.write_text("")
        project = local_project(Path(tmpdir))
        result = compile_file(project.path("empty.yaml").read_board())
    assert result.errors, "Expected a CompileResult with errors for empty YAML"
    assert any("empty" in (e.message or "").lower() for e in result.errors)


# ---------------------------------------------------------------------------
# 7. geo — all built-in sources live in config, not a parallel Python dict
# ---------------------------------------------------------------------------

_EXPECTED_GEO_SOURCES = {
    "us-states",
    "us-counties",
    "world-countries",
    "world-50m",
    "sf-neighborhoods",
    "nyc-neighborhoods",
    "chicago-neighborhoods",
    "la-neighborhoods",
}


def test_all_geo_sources_in_config():
    """Every built-in geo source must be present in get_config().geo_sources."""
    from dbt_charts.core.compile.config import get_config

    geo_sources = get_config().geo_sources
    missing = _EXPECTED_GEO_SOURCES - set(geo_sources.keys())
    assert not missing, f"Missing from config geo_sources: {missing}"


def test_resolve_geo_source_city_neighborhoods():
    """geo source fields for sf-neighborhoods must come from config, not a Python dict."""
    from dbt_charts.core.compile.config import get_config
    from dbt_charts.core.compile.resolve.chart.geo import (
        _resolve_geo_projection,
        _resolve_geo_source_fields,
    )

    geo_sources = get_config().geo_sources
    geo_info = geo_sources.get("sf-neighborhoods")
    assert geo_info is not None, (
        "sf-neighborhoods must be present in config.geo_sources"
    )

    fields = _resolve_geo_source_fields(geo_info, "sf-neighborhoods", None, None)
    proj_type, _ = _resolve_geo_projection(None, geo_info)

    # URL must come from config — non-empty absolute URL, not a hardcoded literal.
    # Don't pin the domain; the underlying data provider may change.
    assert fields.geo_url.startswith("http")
    assert fields.geo_format_type == "json"
    assert proj_type == "mercator"


# ---------------------------------------------------------------------------
# 8. sizing — height constants live in the theme, not Python literals
# ---------------------------------------------------------------------------


def test_sizing_constants_in_theme():
    """DEFAULT_CHART_HEIGHT, DEFAULT_TABLE_HEIGHT, TITLE_INLINE_BAND_BOTTOM_PAD
    must be present in the theme style cascade (not hard-coded Python literals)."""

    charts = get_theme_style().charts
    assert float(charts.default_chart_height) > 0
    assert float(charts.default_table_height) > 0
    # title_inline_band_bottom_pad lives under style.variables
    variables = get_theme_style().variables
    assert float(variables.title_inline_band_bottom_pad) > 0

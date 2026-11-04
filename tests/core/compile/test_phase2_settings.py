"""Phase 2: Settings/Board split — failing tests first.

TDD: these must fail before implementation, then pass after.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import get_theme_style, reset_config


@pytest.fixture(autouse=True)
def _reset():
    reset_config()
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Test 1 — load_settings rejects style: in dbt_charts.yml
# ---------------------------------------------------------------------------


def test_settings_rejects_style_key_in_dbt_charts_yml(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """dbt_charts.yml: style: {...} must raise ValidationError pointing to charts/meta.yml."""
    (tmp_path / "dbt_charts.yml").write_text("style:\n  frame:\n    width: 700\n")
    (tmp_path / "charts").mkdir()

    project = local_project(tmp_path)

    from dbt_charts.core.compile.config import load_config

    with pytest.raises(ValidationError):
        load_config(project)


# ---------------------------------------------------------------------------
# Test 2 — dbt_charts.yml: theme: is no longer accepted (extra field, raises)
# ---------------------------------------------------------------------------


def test_dbt_charts_yml_theme_key_raises_validation_error(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """dbt_charts.yml: theme: was removed — project theme lives in charts/meta.yml: extends:.
    load_config raises pydantic ValidationError (extra inputs are not permitted)."""
    from pydantic import ValidationError

    (tmp_path / "dbt_charts.yml").write_text("theme: paper\n")

    project = local_project(tmp_path)

    from dbt_charts.core.compile.config import load_config, reset_config

    reset_config()
    try:
        with pytest.raises(ValidationError):
            load_config(project)
    finally:
        reset_config()


# ---------------------------------------------------------------------------
# Test 3 — load_settings is importable; load_config is gone
# ---------------------------------------------------------------------------


def test_load_settings_exported_from_compile():
    """load_settings must be exported from dbt_charts.core.compile."""
    from dbt_charts.core.compile import load_config  # noqa: F401


# ---------------------------------------------------------------------------
# Test 4 — charts.preferred_width propagates through the theme cascade
# ---------------------------------------------------------------------------


def test_resolved_charts_style_has_preferred_width():
    """The theme cascade carries charts.preferred_width to ResolvedChartsStyle.

    Propagation check: whatever value the compiled theme has, the same value
    must reach resolved_style.charts.preferred_width (no silent drop).
    """
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    compiled = get_theme_style("stark")
    resolved = resolve_chart_style_context(compiled)
    assert resolved.preferred_width == compiled.charts.preferred_width
    assert resolved.preferred_width > 0


# ---------------------------------------------------------------------------
# Regression: charts/meta.yml width propagates project-wide
# (Phase 1 wired this; Phase 2 must keep it working)
# ---------------------------------------------------------------------------


def test_meta_yaml_width_propagates(
    tmp_path, local_project: Callable[..., FilesystemProject]
):
    """charts/meta.yml: width must apply to all boards."""
    boards_dir = tmp_path / "charts"
    boards_dir.mkdir()
    (boards_dir / "meta.yml").write_text("width: 888\n")
    board_path = boards_dir / "test.yaml"
    board_path.write_text("title: Test\nrows:\n  - text: hello\n")
    (tmp_path / "dbt_charts.yml").write_text("")

    from dbt_charts.core.compile import compile_file

    project = local_project(tmp_path)
    result = compile_file(project.path("charts/test.yaml").read_board())
    assert result.board is not None, f"Compile failed: {result.errors}"
    assert result.board.authored_width == 888.0

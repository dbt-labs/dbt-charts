"""Tests for narrow domain getters in dbt_charts.core.compile.config."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.compile.config import (
    get_export_config,
    get_rendering_config,
    get_terminal_config,
    reset_config,
)


def test_get_terminal_config_max_labels():
    reset_config()
    assert get_terminal_config().max_labels > 0


def test_get_rendering_config_png_scale():
    reset_config()
    assert isinstance(get_rendering_config().png.scale, float)


def test_get_export_config_reads_project_public_url(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """get_export_config returns the project-level public_url from dbt_charts.yml."""
    (tmp_path / "dbt_charts.yml").write_text("public_url: https://example.com\n")
    project = local_project(tmp_path)
    result = get_export_config(project)
    assert result.public_url == "https://example.com"


def test_get_export_config_falls_back_to_global_default_when_no_project(
    tmp_path: Path,
) -> None:
    """Without a project, get_export_config falls back to the global config default."""
    reset_config()
    result = get_export_config(None)
    # Default config has public_url == "" (root-relative behavior).
    assert result.public_url == ""


def test_get_export_config_strips_trailing_slash_from_project_url(
    tmp_path: Path, local_project: Callable[..., FilesystemProject]
) -> None:
    """A trailing slash in dbt_charts.yml public_url must not produce double-slash links."""
    (tmp_path / "dbt_charts.yml").write_text("public_url: https://example.com/\n")
    project = local_project(tmp_path)
    result = get_export_config(project)
    assert result.public_url == "https://example.com"


def test_rendering_config_carries_only_live_fields():
    """RenderingConfig now carries only ``png.scale``; footer/timestamp have
    moved to the style cascade. Pin that the dead blocks are gone and png survives."""
    reset_config()
    data = get_rendering_config().to_plain_dict(exclude_none=False)
    for dead in (
        "svg",
        "pdf",
        "html",
        "terminal",
        "timestamp",
        "footer_text",
        "footer",
    ):
        assert dead not in data
    assert set(data["png"]) == {"scale"}

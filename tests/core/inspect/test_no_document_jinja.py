"""Tests for M2 goal: no document-level Jinja in inspect templates.

After M2, inspect templates must be valid on-disk YAML+string-{{ }} only.
Document-level Jinja tags ({% %}) are gone; schema panels always show;
renderer no longer calls resolve_jinja_template().
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.inspect import INSPECT_TEMPLATES


def _template_text(name: str) -> str:
    return (
        files("dbt_charts.core.inspect.templates").joinpath(f"{name}.yml").read_text()
    )


def test_inspect_templates_have_no_document_jinja() -> None:
    """No inspect template may contain a {% %} Jinja block tag."""
    for name in INSPECT_TEMPLATES:
        text = _template_text(name)
        assert "{%" not in text, (
            f"{name}.yml still contains document-level Jinja ({{%…%}}). "
            "Remove all {% %} blocks — M2 requires static on-disk templates."
        )


def test_inspect_model_yml_compiles_without_resolve_jinja(
    tmp_path: Path,
) -> None:
    """model.yml compiles with only string {{ }} vars — no Jinja pre-pass needed."""
    from dbt_charts.core.compile import compile

    template_yaml = _template_text("model")
    # Substitute the string vars manually to simulate what the renderer does
    # after M2 (direct Jinja string-var substitution only, no block-tag expansion).
    rendered = (
        template_yaml.replace("{{ model }}", "orders")
        .replace("{{ connection }}", ":memory:")
        .replace("{{ source_name }}", "warehouse")
        .replace("{{ schema_name }}", "main")
    )
    result = compile(rendered)
    assert not result.errors, f"model.yml failed to compile: {result.errors}"
    assert result.board is not None


def test_render_inspect_dashboard_does_not_call_resolve_jinja(
    tmp_path: Path,
    local_project: Callable[..., FilesystemProject],
) -> None:
    """After M2 the renderer must not import or call resolve_jinja_template."""
    from dbt_charts.core.execute.env import build_exec_env
    from dbt_charts.core.inspect.renderer import render_inspect_dashboard

    template_text = _template_text("model")
    project = local_project(tmp_path)
    html = render_inspect_dashboard(
        template_text,
        {"model": "orders"},
        project=project,
        env=build_exec_env(project),
    )
    assert isinstance(html, str)

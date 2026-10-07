"""In-process CLI tests for dbt_charts' always-registered `dct inspect` subcommands.

Covers: inspect eject, inspect templates, inspect validate-templates — these
are dbt_charts' own template-management commands, defined directly in
``dbt_charts.cli.main`` and available with or without the private profiler
package.

inspect (batch profiling), inspect table, and inspect audit are registered
onto `inspect_app` only when dbt_charts_super_schema's CLI plugin is loaded,
so they're intentionally not covered here.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# TestInspectEject
# ---------------------------------------------------------------------------


class TestInspectEject:
    """dct inspect eject."""

    def test_happy_path_single_template(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "inspect",
                "eject",
                "model",
                "--output",
                str(tmp_path / "out"),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "model.yml").exists()

    def test_all_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        out_dir = tmp_path / "out"
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "inspect",
                "eject",
                "--all",
                "--output",
                str(out_dir),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # All templates should have been ejected
        from dbt_charts.core.inspect import INSPECT_TEMPLATES

        for name in INSPECT_TEMPLATES:
            assert (out_dir / f"{name}.yml").exists()

    def test_force_flag_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir(parents=True)
        # Pre-create with dummy content
        (out_dir / "model.yml").write_text("# custom\n")
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "inspect",
                "eject",
                "model",
                "--force",
                "--output",
                str(out_dir),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        # Content should be overwritten with built-in template
        content = (out_dir / "model.yml").read_text()
        assert "# custom" not in content

    def test_multiple_templates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out_dir = tmp_path / "out"
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "inspect",
                "eject",
                "model",
                "quality",
                "--output",
                str(out_dir),
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert (out_dir / "model.yml").exists()
        assert (out_dir / "quality.yml").exists()

    def test_no_templates_no_all_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No positional args and no --all → eject_command prints error and exits 1
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["inspect", "eject"],
        )
        assert result.exit_code == 1  # eject_command raises typer.Exit(1)

    def test_unknown_template_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out_dir = tmp_path / "out"
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            [
                "inspect",
                "eject",
                "unknown_template_xyz",
                "--output",
                str(out_dir),
            ],
        )
        assert result.exit_code == 1  # ValueError from eject_templates → typer.Exit(1)


# ---------------------------------------------------------------------------
# TestInspectTemplates
# ---------------------------------------------------------------------------


class TestInspectTemplates:
    """dct inspect templates — list available templates."""

    def test_happy_path_exits_0(self) -> None:
        result = runner.invoke(app, ["inspect", "templates"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_output_names_builtin_templates(self) -> None:
        from dbt_charts.core.inspect import INSPECT_TEMPLATES

        result = runner.invoke(app, ["inspect", "templates"], catch_exceptions=False)
        assert result.exit_code == 0
        for name in INSPECT_TEMPLATES:
            assert name in result.output


# ---------------------------------------------------------------------------
# TestInspectValidateTemplates
# ---------------------------------------------------------------------------


class TestInspectValidateTemplates:
    """dct inspect validate-templates."""

    def test_happy_path_after_eject(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        out_dir = tmp_path / "out"
        # First eject so there's a manifest to validate
        monkeypatch.chdir(tmp_path)
        eject_result = runner.invoke(
            app,
            ["inspect", "eject", "--all", "--output", str(out_dir)],
            catch_exceptions=False,
        )
        assert eject_result.exit_code == 0

        result = runner.invoke(
            app,
            ["inspect", "validate-templates", "--output", str(out_dir)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_missing_output_dir_exits_1(self, tmp_path: Path) -> None:
        # Directory with no manifest → validate_ejected_templates raises FileNotFoundError → exit 1
        nonexistent = tmp_path / "no_such_dir"
        result = runner.invoke(
            app,
            ["inspect", "validate-templates", "--output", str(nonexistent)],
        )
        assert result.exit_code == 1  # FileNotFoundError → typer.Exit(1)

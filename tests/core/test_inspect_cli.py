"""Tests for the inspect CLI commands.

Tests for:
- dct inspect templates - list available templates
- dct inspect eject - copy templates to charts/inspect/
"""

import json
import re
import tempfile
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from dbt_charts.cli.commands.inspect import (
    eject_command,
    validate_ejected_templates_command,
)
from dbt_charts.core.inspect import INSPECT_TEMPLATES
from dbt_charts.core.inspect.manifest_utils import INSPECT_TEMPLATE_MANIFEST

runner = CliRunner()

# Regex to strip ANSI escape codes from CLI output
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from text for clean assertions."""
    return ANSI_ESCAPE_PATTERN.sub("", text)


class TestInspectTemplatesList:
    """Tests for dct inspect templates command."""

    def test_inspect_templates_constant(self) -> None:
        """INSPECT_TEMPLATES should contain expected templates."""
        expected = {
            "model",
            "quality",
            "numeric_column",
            "date_column",
            "string_column",
            "categorical_column",
        }
        # INSPECT_TEMPLATES is auto-discovered from package as a list
        assert set(INSPECT_TEMPLATES) == expected


class TestInspectEject:
    """Tests for dct inspect eject command."""

    def test_eject_single_template(self) -> None:
        """Ejecting a single template should create the file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"

            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )

            # File should exist
            model_file = output_dir / "model.yml"
            assert model_file.exists()

            # Content should contain model template content
            content = model_file.read_text()
            assert "model" in content.lower() or "Model" in content
            assert (output_dir / INSPECT_TEMPLATE_MANIFEST).exists()

    def test_eject_multiple_templates(self) -> None:
        """Ejecting multiple templates should create all files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"

            eject_command(
                templates=["model", "quality"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )

            # Both files should exist
            assert (output_dir / "model.yml").exists()
            assert (output_dir / "quality.yml").exists()
            manifest = json.loads((output_dir / INSPECT_TEMPLATE_MANIFEST).read_text())
            assert "model" in manifest["templates"]
            assert "quality" in manifest["templates"]

    def test_eject_all_templates(self) -> None:
        """Ejecting all templates should create all files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"

            eject_command(
                templates=[],
                all_templates=True,
                force=False,
                output_dir=output_dir,
            )

            # All template files should exist
            for template in INSPECT_TEMPLATES:
                assert (output_dir / f"{template}.yml").exists(), (
                    f"{template}.yml not found"
                )

    def test_eject_skips_existing_without_force(self) -> None:
        """Ejecting should skip existing files without --force."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            output_dir.mkdir(parents=True)

            # Create existing file with custom content
            existing_file = output_dir / "model.yml"
            existing_file.write_text("# Custom content - should not be overwritten")

            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )

            # Original content should be preserved
            content = existing_file.read_text()
            assert "Custom content" in content

    def test_eject_overwrites_with_force(self) -> None:
        """Ejecting with --force should overwrite existing files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            output_dir.mkdir(parents=True)

            # Create existing file with custom content
            existing_file = output_dir / "model.yml"
            existing_file.write_text("# Custom content - should be overwritten")

            eject_command(
                templates=["model"],
                all_templates=False,
                force=True,
                output_dir=output_dir,
            )

            # Original content should be replaced
            content = existing_file.read_text()
            assert "Custom content" not in content

    def test_eject_invalid_template_raises(self) -> None:
        """Ejecting invalid template name should raise error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"

            with pytest.raises(typer.Exit):
                eject_command(
                    templates=["nonexistent_template"],
                    all_templates=False,
                    force=False,
                    output_dir=output_dir,
                )

    def test_eject_creates_directory_if_missing(self) -> None:
        """Ejecting should create output directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "nested" / "charts" / "inspect"

            # Directory doesn't exist yet
            assert not output_dir.exists()

            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )

            # Directory should be created
            assert output_dir.exists()
            assert (output_dir / "model.yml").exists()

    def test_validate_templates_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )
            validate_ejected_templates_command(output_dir=output_dir)

    def test_validate_templates_fails_when_manifest_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            output_dir.mkdir(parents=True)
            with pytest.raises(typer.Exit):
                validate_ejected_templates_command(output_dir=output_dir)

    def test_validate_templates_fails_when_template_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )
            (output_dir / "model.yml").unlink()
            with pytest.raises(typer.Exit):
                validate_ejected_templates_command(output_dir=output_dir)


class TestEjectRecoveryHint:
    """Eject output should mention recovery via --force."""

    def test_eject_prints_recovery_hint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )
            captured = capsys.readouterr().out
            assert "eject" in captured.lower() and "force" in captured.lower()

    def test_eject_suppresses_recovery_hint_on_force(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "charts" / "inspect"
            eject_command(
                templates=["model"],
                all_templates=False,
                force=False,
                output_dir=output_dir,
            )
            capsys.readouterr()  # discard first eject output
            eject_command(
                templates=["model"],
                all_templates=False,
                force=True,
                output_dir=output_dir,
            )
            captured = capsys.readouterr().out
            assert "reset to built-in" not in captured.lower()


class TestInspectCLIIntegration:
    """Integration tests for inspect CLI via Typer testing."""

    def test_inspect_help_shows_subcommands(self) -> None:
        """dct inspect --help should show subcommands."""
        from dbt_charts.cli.main import app

        result = runner.invoke(app, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "table" in result.output
        assert "eject" in result.output
        assert "templates" in result.output
        assert "validate-templates" in result.output

    def test_inspect_templates_command(self) -> None:
        """dct inspect templates should list templates."""
        from dbt_charts.cli.main import app

        result = runner.invoke(app, ["inspect", "templates"])
        assert result.exit_code == 0
        assert "model" in result.output
        assert "quality" in result.output

    def test_inspect_eject_help(self) -> None:
        """dct inspect eject --help should show options."""
        from dbt_charts.cli.main import app

        result = runner.invoke(app, ["inspect", "eject", "--help"])
        assert result.exit_code == 0
        # Strip ANSI codes to avoid escape sequences breaking string matching
        output = strip_ansi(result.output)
        assert "--all" in output
        assert "--force" in output

    def test_inspect_eject_with_custom_output(self) -> None:
        """dct inspect eject -o custom/ should work."""
        from dbt_charts.cli.main import app

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "custom"

            result = runner.invoke(
                app, ["inspect", "eject", "model", "-o", str(output_dir)]
            )
            assert result.exit_code == 0
            assert (output_dir / "model.yml").exists()

    def test_inspect_validate_templates_command(self) -> None:
        from dbt_charts.cli.main import app

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "custom"
            runner.invoke(app, ["inspect", "eject", "model", "-o", str(output_dir)])

            result = runner.invoke(
                app,
                ["inspect", "validate-templates", "-o", str(output_dir)],
            )
            assert result.exit_code == 0
            assert "compatible" in result.output.lower()

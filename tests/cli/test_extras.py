"""Tests for dbt_charts.cli._extras — optional-dependency gate."""

from __future__ import annotations

import importlib.metadata
import io
import shlex
import sys
from unittest.mock import patch

import pytest
import typer


def _fake_find_spec_all_present(name: str, *args, **kwargs):
    """Simulate all agent extras installed."""
    return object()  # truthy spec


def _fake_find_spec_mcp_missing(name: str, *args, **kwargs):
    """Simulate the mcp package missing."""
    if name == "mcp":
        return None
    return object()


def _make_non_tty() -> io.StringIO:
    buf = io.StringIO("")
    buf.isatty = lambda: False
    return buf


class TestRequireExtras:
    def test_all_present_returns_silently(self):
        """When all extras are importable, require_extras returns None."""
        from dbt_charts.cli._extras import require_extras

        with patch("importlib.util.find_spec", side_effect=_fake_find_spec_all_present):
            result = require_extras("mcp")

        assert result is None

    def test_missing_extra_non_tty_raises_exit(self, capsys, monkeypatch):
        """Non-TTY path: missing extras print install command and raise Exit(1)."""
        from dbt_charts.cli._extras import require_extras

        monkeypatch.setattr("sys.stdin", _make_non_tty())
        monkeypatch.delenv("DCT_NO_AUTO_INSTALL", raising=False)

        with (
            patch(
                "importlib.util.find_spec",
                side_effect=_fake_find_spec_mcp_missing,
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            require_extras("mcp")

        assert exc_info.value.exit_code == 1
        captured = capsys.readouterr()
        assert "mcp" in captured.out + captured.err

    def test_no_auto_install_env_raises_exit(self, capsys, monkeypatch):
        """DCT_NO_AUTO_INSTALL=1 forces non-interactive path even on TTY."""
        from dbt_charts.cli._extras import require_extras

        monkeypatch.setenv("DCT_NO_AUTO_INSTALL", "1")

        with (
            patch(
                "importlib.util.find_spec",
                side_effect=_fake_find_spec_mcp_missing,
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            require_extras("mcp")

        assert exc_info.value.exit_code == 1

    def test_missing_shows_pip_install_command(self, capsys, monkeypatch):
        """Non-TTY pip-resolver: minimal-install line shows `pip install <pkgs>`.

        The reinstall-with-extras line below is always the canonical
        `pip install "dbt-charts[…]"` form regardless of detected
        installer — that's the documented dbt-charts install command.

        The package argument carries the bound declared in metadata, quoted
        for the shell, rather than a bare dist name.
        """
        from dbt_charts.cli._extras import require_extras

        monkeypatch.setattr("sys.stdin", _make_non_tty())
        monkeypatch.delenv("UV", raising=False)

        with (
            patch(
                "importlib.util.find_spec",
                side_effect=_fake_find_spec_mcp_missing,
            ),
            pytest.raises(typer.Exit),
        ):
            require_extras("mcp")

        captured = capsys.readouterr()
        full_output = captured.out + captured.err
        assert "pip install 'mcp" in full_output
        assert "uv pip install --python" not in full_output
        assert 'pip install "dbt-charts[mcp]"' in full_output

    def test_missing_shows_uv_pip_install_when_uv_active(self, capsys, monkeypatch):
        """Non-TTY uv-resolver (UV env set): shows uv pip install --python in output."""
        from dbt_charts.cli._extras import require_extras

        monkeypatch.setattr("sys.stdin", _make_non_tty())
        monkeypatch.setenv("UV", "/opt/uv")

        with (
            patch(
                "importlib.util.find_spec",
                side_effect=_fake_find_spec_mcp_missing,
            ),
            pytest.raises(typer.Exit),
        ):
            require_extras("mcp")

        captured = capsys.readouterr()
        full_output = captured.out + captured.err
        assert "uv pip install --python" in full_output
        assert "pip install mcp" not in full_output
        assert 'pip install "dbt-charts[mcp]"' in full_output

    def test_require_extras_exits_when_no_installer(self, capsys, monkeypatch):
        """TTY path with no installer: exits with no-installer panel, no subprocess."""
        from dbt_charts.cli._extras import require_extras

        tty_stdin = io.StringIO("")
        tty_stdin.isatty = lambda: True
        monkeypatch.setattr("sys.stdin", tty_stdin)
        monkeypatch.delenv("UV", raising=False)
        monkeypatch.delenv("DCT_NO_AUTO_INSTALL", raising=False)

        def find_spec_fn(name: str, *a: object, **kw: object) -> object | None:
            if name in ("pip", "mcp"):
                return None
            return object()

        with (
            patch("importlib.util.find_spec", side_effect=find_spec_fn),
            patch("shutil.which", return_value=None),
            pytest.raises(typer.Exit) as exc_info,
        ):
            require_extras("mcp")

        assert exc_info.value.exit_code == 1
        captured = capsys.readouterr()
        full_output = captured.out + captured.err
        assert "No installer found" in full_output
        assert "mcp" in full_output

    def test_missing_metadata_raises_exit(self, capsys):
        """PackageNotFoundError from metadata is caught and converted to Exit(1)."""
        from dbt_charts.cli._extras import require_extras

        with (
            patch(
                "importlib.metadata.requires",
                side_effect=importlib.metadata.PackageNotFoundError("dbt-charts"),
            ),
            pytest.raises(typer.Exit) as exc_info,
        ):
            require_extras("mcp")

        assert exc_info.value.exit_code == 1


class TestResolveInstaller:
    def test_resolve_installer_prefers_uv_when_UV_env_set(self, monkeypatch):
        from dbt_charts.cli._extras import _resolve_installer

        monkeypatch.setenv("UV", "/opt/uv")
        with patch("importlib.util.find_spec", return_value=object()):
            assert _resolve_installer() == "uv"

    def test_resolve_installer_uses_pip_when_no_uv_env_and_pip_importable(
        self, monkeypatch
    ):
        from dbt_charts.cli._extras import _resolve_installer

        monkeypatch.delenv("UV", raising=False)

        def _find_spec(name: str, *a: object, **kw: object) -> object | None:
            return object() if name == "pip" else None

        with (
            patch(
                "importlib.util.find_spec",
                side_effect=_find_spec,
            ),
            patch("shutil.which", return_value=None),
        ):
            assert _resolve_installer() == "pip"

    def test_resolve_installer_falls_back_to_uv_on_path_when_pip_missing(
        self, monkeypatch
    ):
        from dbt_charts.cli._extras import _resolve_installer

        monkeypatch.delenv("UV", raising=False)
        with (
            patch("importlib.util.find_spec", return_value=None),
            patch("shutil.which", return_value="/opt/uv"),
        ):
            assert _resolve_installer() == "uv"

    def test_resolve_installer_returns_none_when_neither_available(self, monkeypatch):
        from dbt_charts.cli._extras import _resolve_installer

        monkeypatch.delenv("UV", raising=False)
        with (
            patch("importlib.util.find_spec", return_value=None),
            patch("shutil.which", return_value=None),
        ):
            assert _resolve_installer() is None


class TestInstallerCommand:
    def test_installer_command_pip_branch(self):
        from dbt_charts.cli._extras import _installer_command

        assert _installer_command("pip", ["some-dist"]) == [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-input",
            "some-dist",
        ]

    def test_installer_command_uv_branch(self):
        from dbt_charts.cli._extras import _installer_command

        assert _installer_command("uv", ["some-dist"]) == [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "some-dist",
        ]


class TestDeclaredVersionBounds:
    """The gate's remediation must respect the bounds it read from metadata."""

    def test_missing_packages_keep_their_version_specifier(self) -> None:
        """Missing entries carry the specifier declared in package metadata.

        This list is both printed to the user and passed to the installer. A
        bare dist name resolves to the newest major, so for a capped extra the
        offered remedy reinstalls precisely the release the cap excludes — the
        gate would hand the user a fix that reproduces the crash it repairs.
        """
        from dbt_charts.cli._extras import _missing_packages

        with patch("importlib.util.find_spec", side_effect=_fake_find_spec_mcp_missing):
            missing = _missing_packages("mcp")

        assert missing, "mcp was mocked missing, so it must be reported"
        requirement = next(m for m in missing if m.startswith("mcp"))
        assert any(op in requirement for op in ("<", ">", "=")), (
            f"version specifier stripped from {requirement!r}; the offered "
            "install would float to the newest major"
        )

    def test_missing_packages_still_probe_the_bare_module_name(self) -> None:
        """Keeping the specifier must not leak into the importability check.

        `find_spec` takes a module name — handing it `mcp>=1.0.0,<2` would
        report every capped package as permanently missing.
        """
        from dbt_charts.cli._extras import _missing_packages

        probed: list[str] = []

        def _record(name: str, *args: object, **kwargs: object) -> object:
            probed.append(name)
            return None

        with patch("importlib.util.find_spec", side_effect=_record):
            _missing_packages("mcp")

        assert "mcp" in probed

    def test_display_command_quotes_specifiers_for_the_shell(self) -> None:
        """A displayed requirement is copy-pasted into a shell.

        `<` and `>` in an unquoted specifier are redirection operators: the
        paste would truncate the file `2` and install an unbounded `mcp`.
        """
        from dbt_charts.cli._extras import _installer_display_command

        command = _installer_display_command("pip", ["mcp>=1.0.0,<2"])

        # Round-trip through the shell lexer: the requirement must survive as a
        # single argument with its operators intact, which is the property the
        # user depends on when pasting. A substring check cannot show this.
        assert shlex.split(command)[-1] == "mcp>=1.0.0,<2"


class TestBuildExtrasPanel:
    def _render(self, extra: str, missing: list[str], installer: object) -> str:
        from dbt_charts.cli._extras import _build_extras_panel

        buf = io.StringIO()
        from rich.console import Console

        con = Console(file=buf, no_color=True)
        con.print(_build_extras_panel(extra, missing, installer))  # type: ignore[arg-type]
        return buf.getvalue()

    def test_pip_branch_shows_pip_install_for_missing_packages(self):
        """pip-resolver: minimal-install line uses pip; reinstall line uses
        the canonical `pip install "dbt-charts[extra]"` form."""
        text = self._render("mcp", ["mcp"], "pip")
        assert "pip install mcp" in text
        assert "uv pip install --python" not in text
        assert 'pip install "dbt-charts[mcp]"' in text

    def test_uv_branch_shows_uv_pip_install_python(self):
        text = self._render("mcp", ["mcp"], "uv")
        assert "uv pip install --python" in text
        assert 'pip install "dbt-charts[mcp]"' in text

    def test_no_installer_branch_lists_packages_not_commands(self):
        text = self._render("mcp", ["mcp"], None)
        assert "mcp" in text
        assert "No installer found" in text
        assert "pip install mcp" not in text
        assert "uv pip install" not in text


class TestInstallExtrasLog:
    @pytest.mark.parametrize("returncode", [0, 1])
    def test_installer_log_printed_only_on_failure(
        self, capsys: pytest.CaptureFixture[str], returncode: int
    ) -> None:
        import subprocess

        from dbt_charts.cli._extras import install_extras

        missing = [["mcp"], [] if returncode == 0 else ["mcp"]]
        completed = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="INSTALLER-LOG-LINE\n", stderr=""
        )
        with (
            patch("dbt_charts.cli._extras._missing_packages", side_effect=missing),
            patch("dbt_charts.cli._extras._resolve_installer", return_value="pip"),
            patch("dbt_charts.cli._extras._console.input", return_value="y"),
            patch(
                "dbt_charts.cli._extras.subprocess.run", return_value=completed
            ) as run,
        ):
            if returncode == 0:
                install_extras("mcp")
            else:
                with pytest.raises(typer.Exit):
                    install_extras("mcp")

        assert run.call_args.kwargs["capture_output"] is True
        captured = capsys.readouterr()
        output = captured.out + captured.err
        assert "Installing the mcp extra" in output
        assert ("INSTALLER-LOG-LINE" in output) == (returncode != 0)

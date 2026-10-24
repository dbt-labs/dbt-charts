"""Tests for the ``dct import`` CLI command."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from dbt_charts.agent_api import HexProject
from dbt_charts.agent_api.omni import OmniDashboard
from dbt_charts.agent_api.sigma import SigmaWorkbook
from dbt_charts.agent_api.source_bundle import BundleError
from dbt_charts.cli.commands import import_source
from dbt_charts.cli.main import app

runner = CliRunner()


def test_import_help_lists_looker() -> None:
    result = runner.invoke(app, ["import", "--help"])

    assert result.exit_code == 0
    assert "looker" in result.output


def test_import_looker_forwards_arguments_and_prints_bundle_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, object] = {}

    def fake_import(
        ref: str, *, out: Path | None, base_url: str | None, force: bool
    ) -> Path:
        received.update(ref=ref, out=out, base_url=base_url, force=force)
        return tmp_path

    monkeypatch.setattr(import_source, "import_looker", fake_import)

    result = runner.invoke(
        app,
        [
            "import",
            "looker",
            "42",
            "--base-url",
            "https://x:19999",
            "-o",
            str(tmp_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert received == {
        "ref": "42",
        "out": tmp_path,
        "base_url": "https://x:19999",
        "force": True,
    }
    assert str(tmp_path) in result.output.replace("\n", "")


def test_import_looker_reads_base_url_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []

    def fake_import(
        ref: str, *, out: Path | None, base_url: str | None, force: bool
    ) -> Path:
        seen.append(base_url)
        return tmp_path

    monkeypatch.setattr(import_source, "import_looker", fake_import)
    monkeypatch.setenv("LOOKER_BASE_URL", "https://env:19999")

    assert runner.invoke(app, ["import", "looker", "42"]).exit_code == 0
    assert seen == ["https://env:19999"]


def test_import_looker_bundle_error_exits_nonzero_naming_the_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ref: str, *, out: Path | None, base_url: str | None, force: bool) -> Path:
        raise BundleError("bundle is missing png/dashboard.png")

    monkeypatch.setattr(import_source, "import_looker", fail)

    result = runner.invoke(app, ["import", "looker", "42"])

    assert result.exit_code == 1
    assert "png/dashboard.png" in result.output


def test_import_help_lists_omni() -> None:
    result = runner.invoke(app, ["import", "--help"])

    assert "omni" in result.output


def test_import_omni_forwards_arguments_and_reads_base_url_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, object] = {}

    def fake_import(
        ref: str, *, out: Path | None, base_url: str | None, force: bool
    ) -> Path:
        received.update(ref=ref, out=out, base_url=base_url, force=force)
        return tmp_path

    monkeypatch.setattr(import_source, "import_omni", fake_import)
    monkeypatch.setenv("OMNI_BASE_URL", "https://env.omniapp.test")

    result = runner.invoke(
        app, ["import", "omni", "abc12345", "-o", str(tmp_path), "--force"]
    )

    assert result.exit_code == 0
    assert received == {
        "ref": "abc12345",
        "out": tmp_path,
        "base_url": "https://env.omniapp.test",
        "force": True,
    }
    assert str(tmp_path) in result.output.replace("\n", "")


def test_import_omni_list_prints_one_line_per_dashboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_list(*, base_url: str | None) -> list[OmniDashboard]:
        assert base_url == "https://x.omniapp.test"
        return [
            OmniDashboard(
                "abc12345",
                "organization",
                "Sales overview",
                "https://x.omniapp.test/dashboards/abc12345",
            )
        ]

    monkeypatch.setattr(import_source, "list_omni_dashboards", fake_list)

    result = runner.invoke(
        app,
        ["import", "omni", "--list", "--base-url", "https://x.omniapp.test"],
    )

    assert result.exit_code == 0
    assert result.output.strip() == (
        "abc12345\torganization\tSales overview"
        "\thttps://x.omniapp.test/dashboards/abc12345"
    )


@pytest.mark.parametrize("args", [[], ["abc12345", "--list"]])
def test_import_omni_needs_exactly_one_of_dashboard_or_list(args: list[str]) -> None:
    result = runner.invoke(app, ["import", "omni", *args])

    assert result.exit_code == 2
    assert "--list" in result.output


def test_import_omni_bundle_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ref: str, *, out: Path | None, base_url: str | None, force: bool) -> Path:
        raise BundleError("set OMNI_API_KEY to import from Omni")

    monkeypatch.setattr(import_source, "import_omni", fail)

    result = runner.invoke(app, ["import", "omni", "abc12345"])

    assert result.exit_code == 1
    assert "OMNI_API_KEY" in result.output


def test_import_help_lists_sigma() -> None:
    assert "sigma" in runner.invoke(app, ["import", "--help"]).output


def test_import_sigma_forwards_arguments_and_reads_base_url_from_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, object] = {}

    def fake_import(
        ref: str, *, out: Path | None, base_url: str | None, force: bool
    ) -> Path:
        received.update(ref=ref, out=out, base_url=base_url, force=force)
        return tmp_path

    monkeypatch.setattr(import_source, "import_sigma", fake_import)
    monkeypatch.setenv("SIGMA_API_BASE", "https://env.sigma.test")

    result = runner.invoke(
        app, ["import", "sigma", "AbC", "-o", str(tmp_path), "--force"]
    )

    assert result.exit_code == 0
    assert received == {
        "ref": "AbC",
        "out": tmp_path,
        "base_url": "https://env.sigma.test",
        "force": True,
    }
    assert str(tmp_path) in result.output.replace("\n", "")


def test_import_sigma_list_prints_lines_and_skips_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> Path:
        raise AssertionError("list mode must not import")

    def fake_list(text: str | None, *, base_url: str | None) -> list[SigmaWorkbook]:
        assert (text, base_url) == ("rev", "https://x")
        return [
            SigmaWorkbook("U1", "Revenue", "Team", "https://u", "2026-10-01T00:00:00Z")
        ]

    monkeypatch.setattr(import_source, "import_sigma", fail)
    monkeypatch.setattr(import_source, "list_sigma_workbooks", fake_list)

    result = runner.invoke(
        app, ["import", "sigma", "--list", "rev", "--base-url", "https://x"]
    )

    assert result.exit_code == 0
    assert "2026-10-01\tRevenue\t[Team]\thttps://u" in result.output


def test_import_sigma_needs_a_workbook_or_list() -> None:
    result = runner.invoke(app, ["import", "sigma"])

    assert result.exit_code != 0
    assert "workbook" in result.output


def test_import_sigma_bundle_error_exits_nonzero_naming_the_problem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ref: str, *, out: Path | None, base_url: str | None, force: bool) -> Path:
        raise BundleError("Sigma export kpi/x-1.csv failed: HTTP 500")

    monkeypatch.setattr(import_source, "import_sigma", fail)

    result = runner.invoke(app, ["import", "sigma", "AbC"])

    assert result.exit_code == 1
    assert "kpi/x-1.csv" in result.output


@pytest.mark.parametrize("flag", [["-o", "dir"], ["--force"]])
def test_import_sigma_list_rejects_import_only_flags(
    monkeypatch: pytest.MonkeyPatch, flag: list[str]
) -> None:
    def fake_list(text: str | None, *, base_url: str | None) -> list[SigmaWorkbook]:
        return []

    monkeypatch.setattr(import_source, "list_sigma_workbooks", fake_list)

    result = runner.invoke(app, ["import", "sigma", "--list", *flag])

    assert result.exit_code != 0
    assert "--list" in result.output


def test_import_hex_forwards_arguments_and_prints_bundle_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: dict[str, object] = {}

    def fake_import(
        ref: str, *, out: Path | None, base_url: str | None, force: bool
    ) -> Path:
        received.update(ref=ref, out=out, base_url=base_url, force=force)
        return tmp_path

    monkeypatch.setattr(import_source, "import_hex", fake_import)
    monkeypatch.setenv("HEX_BASE_URL", "https://env/api/v1")

    result = runner.invoke(
        app, ["import", "hex", "abc", "--force", "-o", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert received == {
        "ref": "abc",
        "out": tmp_path,
        "base_url": "https://env/api/v1",
        "force": True,
    }
    assert str(tmp_path) in result.output.replace("\n", "")


def test_import_hex_list_prints_projects_with_brackets_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def fake_list(base_url: str | None) -> list[HexProject]:
        return [
            HexProject("a1", "[Ad-Hoc] Renewals", "2026-01-02"),
            HexProject("b2", "Draft only", None),
        ]

    monkeypatch.setattr(import_source, "list_hex_projects", fake_list)

    result = runner.invoke(app, ["import", "hex", "--list"])

    assert result.exit_code == 0
    assert "a1" in result.output and "[Ad-Hoc] Renewals" in result.output
    assert "b2" in result.output and "unpublished" in result.output


def test_import_hex_needs_a_project_or_list() -> None:
    result = runner.invoke(app, ["import", "hex"])

    assert result.exit_code == 2
    assert "pass either a project URL or id, or --list" in result.output


def test_import_hex_rejects_a_project_together_with_list() -> None:
    result = runner.invoke(app, ["import", "hex", "abc", "--list"])

    assert result.exit_code == 2
    assert "--list" in result.output


def test_import_hex_bundle_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(ref: str, *, out: Path | None, base_url: str | None, force: bool) -> Path:
        raise BundleError("set HEX_API_TOKEN to import from Hex")

    monkeypatch.setattr(import_source, "import_hex", fail)

    result = runner.invoke(app, ["import", "hex", "abc"])

    assert result.exit_code == 1
    assert "HEX_API_TOKEN" in result.output

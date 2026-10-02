"""`dct doctor`: --json round-trips into DoctorReport; exit code follows failures."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from dbt_charts.agent_api.doctor import DoctorReport
from dbt_charts.cli.main import app

runner = CliRunner()


def _project(tmp_path: Path, sources: dict[str, dict[str, str]]) -> Path:
    (tmp_path / "dbt_charts.yml").write_text(yaml.safe_dump({"sources": sources}))
    return tmp_path


@pytest.fixture(autouse=True)
def no_dbt_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "nohome"))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)


def test_json_round_trips_and_exit_is_zero_when_healthy(tmp_path: Path) -> None:
    root = _project(tmp_path, {"local": {"type": "duckdb", "path": ":memory:"}})

    result = runner.invoke(app, ["doctor", "--project-dir", str(root), "--json"])

    assert result.exit_code == 0, result.output
    report = DoctorReport.model_validate_json(result.stdout)
    assert report.success is True
    assert [c.code for c in report.checks][:2] == ["install", "project"]


def test_exit_is_one_when_a_check_fails(tmp_path: Path) -> None:
    root = _project(tmp_path, {"legacy": {"type": "oracle"}})

    result = runner.invoke(app, ["doctor", "--project-dir", str(root), "--json"])

    assert result.exit_code == 1
    assert DoctorReport.model_validate_json(result.stdout).success is False


def test_text_output_marks_each_check_and_summarizes(tmp_path: Path) -> None:
    root = _project(tmp_path, {"legacy": {"type": "oracle"}})

    result = runner.invoke(app, ["doctor", "--project-dir", str(root)])

    assert result.exit_code == 1
    assert "✓ install" in result.stdout
    assert "✗ adapter" in result.stdout
    assert "legacy" in result.stdout
    assert "1 failed" in result.stdout


def test_stdout_noise_from_dbt_stays_off_the_json_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """dbt prints "Error importing adapter" to stdout while a missing adapter
    is probed; that must not corrupt the --json document."""
    from dbt_charts.agent_api import doctor

    real_run_doctor = doctor.run_doctor

    def noisy(project_dir: Path, with_warehouse: bool) -> DoctorReport:
        print("Error importing adapter: No module named 'dbt.adapters.x'")
        return real_run_doctor(project_dir, with_warehouse)

    monkeypatch.setattr(doctor, "run_doctor", noisy)
    root = _project(tmp_path, {"local": {"type": "duckdb", "path": ":memory:"}})

    result = runner.invoke(app, ["doctor", "--project-dir", str(root), "--json"])

    assert DoctorReport.model_validate_json(result.stdout).success is True

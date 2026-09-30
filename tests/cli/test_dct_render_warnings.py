"""Tests for --no-warnings and --ignore-warning CLI flags on dct render.

Uses a monkeypatched fake detector so these tests are independent of any real
detector module. The fake detector emits a single warning carrying a real
registered WARN-* code (--ignore-warning validates against the registry, so
the fake code must be one the registry actually knows about).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner

from dbt_charts.cli.commands.render import _validate_ignore_codes
from dbt_charts.cli.main import app
from dbt_charts.core.diagnostics import WARN_REDUNDANT_ENCODING, Diagnostic
from dbt_charts.core.render.warnings.base import WarningContext

runner = CliRunner()

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
_FAKE_CODE = "WARN-REDUNDANT-ENCODING"
_OTHER_CODE = "WARN-PIE-TOO-MANY-SEGMENTS"
_REAL_ERROR_CODE = "ERR-BAR-DUPLICATE-ROWS"


def _make_fake_detector() -> ModuleType:
    """Return a module-like object that acts as a detector emitting _FAKE_CODE."""
    mod = ModuleType("fake_cli_test_detector")

    def detect(ctx: WarningContext) -> list[Diagnostic]:
        return [
            Diagnostic.from_code(
                WARN_REDUNDANT_ENCODING,
                chart="chart1",
                message="fake warning for CLI test",
                fix="fix the fake issue",
            )
        ]

    mod.detect = detect  # type: ignore[attr-defined]
    return mod


@pytest.fixture(autouse=True)
def inject_fake_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject the fake detector as the sole detector for all tests in this module."""
    import importlib

    # `import dbt_charts.core.render.warnings.registry` fails at runtime because
    # dbt_charts.core.render.__init__ exposes a `render` function that shadows the
    # submodule lookup on dotted-import. Use importlib to bypass the shadowing.
    registry_mod = importlib.import_module("dbt_charts.core.render.warnings.registry")
    renderer_mod = importlib.import_module("dbt_charts.core.render.renderer")

    fake = _make_fake_detector()
    monkeypatch.setattr(registry_mod, "DETECTORS", [fake])
    # The renderer re-imports run_all at call time; patch on the renderer module
    # so the monkeypatched list is used during each test.
    monkeypatch.setattr(renderer_mod, "run_all", lambda ctx: fake.detect(ctx))  # type: ignore[attr-defined]


def _project_copy(tmp_path: Path) -> Path:
    """Copy the no-warehouse fixture into tmp_path and return the project dir."""
    shutil.copytree(_FIXTURE_DIR / "no-warehouse-board", tmp_path / "project")
    (tmp_path / "project" / "dbt_charts.yml").write_text("# project marker\n")
    return tmp_path / "project"


class TestWarningsDefaultOutput:
    """Without any flags, warnings print to stderr after a successful render."""

    def test_warning_code_appears_in_stderr(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert _FAKE_CODE in (result.stderr or ""), (
            f"Expected {_FAKE_CODE} in stderr, got: {result.stderr!r}"
        )

    def test_warning_header_shows_count(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "1 warning" in (result.stderr or ""), (
            f"Expected warning count header in stderr, got: {result.stderr!r}"
        )

    def test_fix_line_appears_in_stderr(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "Fix:" in (result.stderr or ""), (
            f"Expected a Fix: line in stderr, got: {result.stderr!r}"
        )


class TestNoWarningsFlag:
    """--no-warnings suppresses stderr but keeps warnings in JSON output."""

    def test_no_warnings_suppresses_stderr(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--no-warnings",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert _FAKE_CODE not in (result.stderr or ""), (
            f"--no-warnings should suppress [{_FAKE_CODE}] from stderr"
        )

    def test_no_warnings_keeps_warnings_in_json(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "json",
                "--no-warnings",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        payload = json.loads(result.stdout)
        warnings: list[Any] = payload.get("warnings", [])
        codes = [w["code"] for w in warnings]
        assert _FAKE_CODE in codes, (
            f"--no-warnings must not strip {_FAKE_CODE} from JSON warnings; got {codes}"
        )


class TestIgnoreWarningFlag:
    """--ignore-warning moves matching warnings to suppressed_warnings."""

    def test_ignore_known_code_removes_from_stderr(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                _FAKE_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert _FAKE_CODE not in (result.stderr or ""), (
            f"--ignore-warning {_FAKE_CODE} should suppress it from stderr"
        )

    def test_ignore_known_code_moves_to_suppressed_in_json(
        self, tmp_path: Path
    ) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "json",
                "--ignore-warning",
                _FAKE_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        payload = json.loads(result.stdout)
        warnings: list[Any] = payload.get("warnings", [])
        suppressed: list[Any] = payload.get("suppressed_warnings", [])
        active_codes = [w["code"] for w in warnings]
        suppressed_codes = [w["code"] for w in suppressed]
        assert _FAKE_CODE not in active_codes, (
            f"{_FAKE_CODE} should not be in warnings when ignored; got {active_codes}"
        )
        assert _FAKE_CODE in suppressed_codes, (
            f"{_FAKE_CODE} should be in suppressed_warnings; got {suppressed_codes}"
        )

    def test_ignore_unrelated_code_leaves_fake_warning_active(
        self, tmp_path: Path
    ) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                _OTHER_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert _FAKE_CODE in (result.stderr or ""), (
            f"Ignoring an unrelated code should not suppress [{_FAKE_CODE}]"
        )

    def test_ignore_repeatable(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                _FAKE_CODE,
                "--ignore-warning",
                _OTHER_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert _FAKE_CODE not in (result.stderr or ""), (
            "Multiple --ignore-warning flags should be additive"
        )


class TestIgnoreWarningUnknownCode:
    """--ignore-warning with an unregistered or non-warning code hard-fails (exit 1).

    Reversed from the old "print a notice but exit 0" contract: an unknown
    code is a typo, and a typo in a suppression list must never look like a
    successful suppression.
    """

    def test_unknown_code_exits_1(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                "UNKNOWN-TYPO-CODE",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 1, (
            f"Unknown --ignore-warning code must exit non-zero; "
            f"exit={result.exit_code}, stderr={result.stderr!r}"
        )

    def test_unknown_code_prints_notice_to_stderr(self, tmp_path: Path) -> None:
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                "UNKNOWN-TYPO-CODE",
                "--project-dir",
                str(proj),
            ],
        )
        combined_stderr = result.stderr or ""
        assert "UNKNOWN-TYPO-CODE" in combined_stderr, (
            f"Expected notice for unknown code 'UNKNOWN-TYPO-CODE' in stderr; got: {combined_stderr!r}"
        )
        assert "unknown warning code" in combined_stderr.lower(), (
            f"Expected 'unknown warning code' notice in stderr; got: {combined_stderr!r}"
        )

    def test_known_fake_code_does_not_print_unknown_notice(
        self, tmp_path: Path
    ) -> None:
        """The fake code is a real registered WARN-* code — no notice expected."""
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "charts/board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                _FAKE_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "unknown warning code" not in (result.stderr or "").lower(), (
            f"{_FAKE_CODE} is registered; should not print unknown notice; got: {result.stderr!r}"
        )

    def test_error_code_exits_1_with_distinguishable_message(
        self, tmp_path: Path
    ) -> None:
        """Ignoring a real ERR- code is rejected differently from an unknown code."""
        proj = _project_copy(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                _REAL_ERROR_CODE,
                "--project-dir",
                str(proj),
            ],
        )
        stderr = result.stderr or ""
        assert result.exit_code == 1, stderr
        assert "unknown warning code" not in stderr.lower(), (
            f"{_REAL_ERROR_CODE} is registered (as an error code) — "
            f"'unknown' is the wrong message: {stderr!r}"
        )
        assert _REAL_ERROR_CODE in stderr and "error code" in stderr, (
            f"Expected a message distinguishing an error code from an unknown "
            f"code; got: {stderr!r}"
        )


class TestValidateIgnoreCodesDirect:
    """Direct unit coverage of _validate_ignore_codes's three branches."""

    def test_known_warning_code_does_not_exit(self) -> None:
        _validate_ignore_codes({_FAKE_CODE}, diagnostics_json=False)

    def test_unknown_code_exits_with_unknown_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _validate_ignore_codes({"NOT-A-REAL-CODE"}, diagnostics_json=False)
        assert exc_info.value.code == 1
        assert "unknown warning code" in capsys.readouterr().err.lower()

    def test_error_code_exits_with_distinguishable_message(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _validate_ignore_codes({_REAL_ERROR_CODE}, diagnostics_json=False)
        assert exc_info.value.code == 1
        stderr = capsys.readouterr().err.lower()
        assert "unknown warning code" not in stderr
        assert "error code" in stderr


_ORPHAN_BOARD_YAML = """\
title: Orphan Chart Board

queries:
  kpis:
    type: values
    columns: [label, value]
    values:
      - [Active Users, 1042]

charts:
  used:
    type: table
    query: kpis
  unused:
    type: table
    query: kpis

rows:
  - cols:
      - used
"""


class TestCompileWarningSuppression:
    """Compile-side warning codes (e.g. WARN-UNREFERENCED-CHART) must honor --ignore-warning."""

    def _orphan_project(self, tmp_path: Path) -> Path:
        proj = tmp_path / "orphan-project"
        proj.mkdir()
        (proj / "dbt_charts.yml").write_text("# project marker\n")
        (proj / "board.yml").write_text(_ORPHAN_BOARD_YAML)
        return proj

    def test_unreferenced_chart_in_warnings_by_default(self, tmp_path: Path) -> None:
        proj = self._orphan_project(tmp_path)
        result = runner.invoke(
            app,
            ["render", "board.yml", "--format", "json", "--project-dir", str(proj)],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        payload = json.loads(result.stdout)
        codes = [w["code"] for w in payload.get("warnings", [])]
        assert "WARN-UNREFERENCED-CHART" in codes, codes

    def test_ignore_unreferenced_chart_moves_to_suppressed(
        self, tmp_path: Path
    ) -> None:
        proj = self._orphan_project(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "board.yml",
                "--format",
                "json",
                "--ignore-warning",
                "WARN-UNREFERENCED-CHART",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        payload = json.loads(result.stdout)
        active = [w["code"] for w in payload.get("warnings", [])]
        suppressed = [w["code"] for w in payload.get("suppressed_warnings", [])]
        assert "WARN-UNREFERENCED-CHART" not in active, active
        assert "WARN-UNREFERENCED-CHART" in suppressed, suppressed

    def test_unreferenced_chart_is_not_reported_as_unknown_code(
        self, tmp_path: Path
    ) -> None:
        proj = self._orphan_project(tmp_path)
        result = runner.invoke(
            app,
            [
                "render",
                "board.yml",
                "--format",
                "terminal",
                "--ignore-warning",
                "WARN-UNREFERENCED-CHART",
                "--project-dir",
                str(proj),
            ],
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        assert "unknown warning code: 'WARN-UNREFERENCED-CHART'" not in (
            result.stderr or ""
        ), result.stderr

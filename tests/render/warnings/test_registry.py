"""Tests for the render-warnings registry: run_all and DETECTORS list."""

from __future__ import annotations

from types import ModuleType

import pytest

from dbt_charts.core.diagnostics import WARN_REDUNDANT_ENCODING, Diagnostic
from dbt_charts.core.render.warnings import (
    WarningContext,
    registry as _registry,
    run_all,
)

from ...core._board_utils import make_test_resolved_board


def _make_ctx() -> WarningContext:
    return WarningContext(
        board_spec=make_test_resolved_board(),
        chart_results={"c1": [{"x": 1}]},
    )


def test_empty_detectors_returns_empty_list() -> None:
    ctx = _make_ctx()
    assert run_all(ctx) == []


def test_fake_detector_produces_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """A module with detect(ctx) returning a Diagnostic is called by run_all."""
    fake = ModuleType("fake_detector")

    def _detect(ctx: WarningContext) -> list[Diagnostic]:
        return [Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="x", chart="c1")]

    fake.detect = _detect  # type: ignore[attr-defined]

    monkeypatch.setattr(_registry, "DETECTORS", [fake])

    result = run_all(_make_ctx())
    assert len(result) == 1
    assert result[0].code == WARN_REDUNDANT_ENCODING.code
    assert result[0].message == "x"
    assert result[0].chart == "c1"


def test_run_all_derives_path_from_chart_when_detector_did_not_set_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A code-agnostic path: any detector that stamps .chart gets .path filled
    in centrally by run_all, so a new detector needs zero position wiring of
    its own to be source-map-stampable downstream."""
    fake = ModuleType("fake_detector")

    def _detect(ctx: WarningContext) -> list[Diagnostic]:
        return [Diagnostic.from_code(WARN_REDUNDANT_ENCODING, message="x", chart="c1")]

    fake.detect = _detect  # type: ignore[attr-defined]

    monkeypatch.setattr(_registry, "DETECTORS", [fake])

    result = run_all(_make_ctx())
    assert result[0].path == "charts.c1"


def test_run_all_does_not_overwrite_a_detector_authored_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = ModuleType("fake_detector")

    def _detect(ctx: WarningContext) -> list[Diagnostic]:
        return [
            Diagnostic.from_code(
                WARN_REDUNDANT_ENCODING, message="x", chart="c1", path="charts.c1.style"
            )
        ]

    fake.detect = _detect  # type: ignore[attr-defined]

    monkeypatch.setattr(_registry, "DETECTORS", [fake])

    result = run_all(_make_ctx())
    assert result[0].path == "charts.c1.style"


def test_buggy_detector_does_not_propagate_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detector that raises must not break run_all — returns [] (or other successful output)."""
    buggy = ModuleType("buggy_detector")

    def _broken_detect(ctx: WarningContext) -> list[Diagnostic]:
        raise RuntimeError("detector exploded")

    buggy.detect = _broken_detect  # type: ignore[attr-defined]

    monkeypatch.setattr(_registry, "DETECTORS", [buggy])

    # Must not raise; returns [] because the only detector failed.
    result = run_all(_make_ctx())
    assert result == []

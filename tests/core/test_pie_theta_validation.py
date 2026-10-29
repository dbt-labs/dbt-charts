"""Pie theta values must be validated before they reach share math or labels.

Regression for the wheel-first labeling gauntlet (2026-07-20): a NULL theta
value silently coerced to a zero share in ``compute_shares``, then crashed
formatting ``None`` with ``'{:,.0f}'.format(value)`` in the default per-slice
label template — a raw ``TypeError: unsupported format string passed to
NoneType.__format__`` reaching the CLI instead of a diagnostic naming the
chart and column. A negative theta is a sibling defect: a pie slice cannot
represent a negative share of the whole.

Drives the real end-to-end path (``render_dashboard``, in-process static
data — no warehouse needed) so the resolve/emit wiring is pinned too, not
just a validator called in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import (
    ERR_PIE_NEGATIVE_THETA,
    ERR_PIE_NULL_THETA,
)
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import InMemoryBoard


def _render_pie(
    tmp_path: Path, rows: list[tuple[str, Any]], *, chart_type: str = "pie"
) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    registry = build_adapter_registry(project, read_only=False)
    values = json.dumps([list(row) for row in rows])
    yaml_content = f"""
title: pie theta validation
queries:
  data:
    columns: [k, v]
    values: {values}
charts:
  c:
    type: {chart_type}
    query: data
    color: k
    theta: v
rows:
  - c
"""
    return render_dashboard(
        board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
        project=project,
        adapter_registry=registry,
        result_cache=None,
    )


def _diagnostic(result: BoardRenderResult) -> Diagnostic:
    """The single diagnostic wherever the render pipeline surfaced it."""
    if result.board_error is not None:
        return result.board_error
    assert result.chart_errors, f"expected an error, got status={result.status!r}"
    return result.chart_errors[0]


class TestNullTheta:
    def test_null_theta_raises_clean_diagnostic_not_raw_typeerror(
        self, tmp_path: Path
    ) -> None:
        result = _render_pie(tmp_path, [("A", None), ("B", 12), ("C", 7)])

        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-PIE-NULL-THETA"
        assert "c" in (diagnostic.chart or diagnostic.message)
        assert "v" in diagnostic.message


class TestNegativeTheta:
    def test_negative_theta_raises_clean_diagnostic(self, tmp_path: Path) -> None:
        result = _render_pie(tmp_path, [("A", -5), ("B", 12), ("C", 7)])

        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-PIE-NEGATIVE-THETA"
        assert "v" in diagnostic.message


class TestAcceptedShapes:
    """Ordinary, valid pie data must still render — no false-positive guard."""

    def test_all_positive_pie_renders(self, tmp_path: Path) -> None:
        result = _render_pie(tmp_path, [("A", 50), ("B", 30), ("C", 20)])
        assert result.status == "ok"
        assert not result.chart_errors

    def test_genuine_zero_slice_renders(self, tmp_path: Path) -> None:
        """A zero share is valid data; must not be conflated with null."""
        result = _render_pie(tmp_path, [("A", 50), ("B", 0), ("C", 50)])
        assert result.status == "ok"
        assert not result.chart_errors

    def test_single_slice_pie_renders(self, tmp_path: Path) -> None:
        result = _render_pie(tmp_path, [("A", 100)])
        assert result.status == "ok"
        assert not result.chart_errors

    def test_donut_renders(self, tmp_path: Path) -> None:
        result = _render_pie(
            tmp_path, [("A", 50), ("B", 30), ("C", 20)], chart_type="donut"
        )
        assert result.status == "ok"
        assert not result.chart_errors

    def test_null_in_a_non_theta_column_renders(self, tmp_path: Path) -> None:
        """Null in `color`, not `theta`, must not trip the theta guard."""
        result = _render_pie(tmp_path, [(None, 50), ("B", 30), ("C", 20)])
        assert result.status == "ok"
        assert not result.chart_errors

    def test_theta_naming_a_column_absent_from_data_does_not_false_positive(
        self,
    ) -> None:
        """theta names a column the query never returned — a different, older
        shape (`compute_shares` already treats it as "no numeric slices").
        `.get()` would return None for a missing key exactly like a real
        None value, so the guard must check key presence, not just truthiness.
        """
        from dbt_charts.core.compile.resolve.chart.pie_attachment import (
            validate_theta_values,
        )

        validate_theta_values("c", "not_a_column", [{"category": "A", "amount": 980}])


def test_validate_theta_values_rejects_null() -> None:
    from dbt_charts.core.compile.resolve.chart.pie_attachment import (
        validate_theta_values,
    )

    with pytest.raises(ChartDataError) as exc_info:
        validate_theta_values("c", "v", [{"v": None}, {"v": 12}, {"v": 7}])
    assert exc_info.value.code is ERR_PIE_NULL_THETA


def test_validate_theta_values_rejects_negative() -> None:
    from dbt_charts.core.compile.resolve.chart.pie_attachment import (
        validate_theta_values,
    )

    with pytest.raises(ChartDataError) as exc_info:
        validate_theta_values("c", "v", [{"v": -5}, {"v": 12}, {"v": 7}])
    assert exc_info.value.code is ERR_PIE_NEGATIVE_THETA


def test_validate_theta_values_accepts_zero_and_positive() -> None:
    from dbt_charts.core.compile.resolve.chart.pie_attachment import (
        validate_theta_values,
    )

    validate_theta_values("c", "v", [{"v": 0}, {"v": 12}, {"v": 7}])


@pytest.mark.parametrize(
    "values", [[1e308, 1e308], [float("nan"), 1.0], [float("inf")], ["inf", 2]]
)
def test_a_non_finite_slice_total_is_refused_by_name(values: list[Any]) -> None:
    from dbt_charts.core.compile.resolve.chart.pie_attachment import compute_shares

    with pytest.raises(ChartDataError, match="non-finite"):
        compute_shares("v", [{"v": value} for value in values])

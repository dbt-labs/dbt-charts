"""Documented `multiples:` restrictions raise typed codes, never ERR-INTERNAL.

An error must name a field the author actually wrote. `MirrorAxisFeature`
picks the code at the one point both facts are known: a chart carrying
`multiples:` gets ERR-MULTIPLES-ENDPOINT-LABELS, whether or not its
both-edge y-axis was auto-derived; anything else gets
ERR-MIRROR-ENDPOINT-LABELS, where a truthy mirror can only be authored or
theme-set.

The endpoint-label collision is now reachable only by authoring the rail on:
the shipped default steps aside for `multiples:` and a legend above the panels
names the series instead. Both halves are pinned here.

Drives the real end-to-end path (`render_dashboard`, in-process static data)
so the resolve -> render wiring is pinned, not just a feature called in
isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.diagnostics.diagnostic import Diagnostic
from dbt_charts.core.execute.adapters import build_adapter_registry
from dbt_charts.core.project import InMemoryBoard

# `segment` exists so a rows+columns test can build a real 2-D grid. Facing the
# same field on both axes only fills the diagonal and is refused at compile, and
# facing a field that is also an encoding is reported as redundant — neither is
# the shape these tests are about.
_ROWS = [
    {"month": "2025-01", "region": "North", "segment": "SMB", "revenue": 100},
    {"month": "2025-02", "region": "North", "segment": "Ent", "revenue": 140},
    {"month": "2025-01", "region": "South", "segment": "SMB", "revenue": 80},
    {"month": "2025-02", "region": "South", "segment": "Ent", "revenue": 120},
]


def _values_query() -> str:
    columns = json.dumps(["month", "region", "segment", "revenue"])
    values = json.dumps(
        [[r["month"], r["region"], r["segment"], r["revenue"]] for r in _ROWS]
    )
    return f"columns: {columns}\n    values: {values}"


def _render(
    tmp_path: Path,
    chart_type: str,
    chart_extra: dict[str, Any],
    board_style: dict[str, Any] | None = None,
) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    registry = build_adapter_registry(project, read_only=False)
    extra_yaml = "\n".join(f"    {k}: {json.dumps(v)}" for k, v in chart_extra.items())
    board_style_yaml = (
        f"style: {json.dumps(board_style)}\n" if board_style is not None else ""
    )
    yaml_content = f"""
title: multiples + endpoint labels
{board_style_yaml}queries:
  data:
    {_values_query()}
charts:
  c:
    type: {chart_type}
    query: data
    x: month
    y: revenue
    color: region
{extra_yaml}
rows:
  - c
"""
    return render_dashboard(
        board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
        project=project,
        adapter_registry=registry,
        result_cache=None,
        format="svg",
        width=600,
    )


def _diagnostic(result: BoardRenderResult) -> Diagnostic:
    """The single diagnostic wherever the render pipeline surfaced it."""
    if result.board_error is not None:
        return result.board_error
    assert result.chart_errors, f"expected an error, got status={result.status!r}"
    return result.chart_errors[0]


class TestAuthoredEndpointLabelsCollision:
    """A documented restriction must never surface as ERR-INTERNAL.

    Only an author who wrote the rail on reaches this now — the shipped default
    steps aside for `multiples:` (TestAcceptedShapes below). The opt-in is what
    keeps these paths live and worth pinning.
    """

    def test_line_multiples_rows_raises_typed_code(self, tmp_path: Path) -> None:
        result = self._render_multiples(tmp_path, "line", {"rows": "region"})
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-ENDPOINT-LABELS"
        assert "axis_y.mirror" not in diagnostic.message

    def test_line_multiples_columns_raises_typed_code_not_mirror(
        self, tmp_path: Path
    ) -> None:
        """The headline case: columns triggers the engine's auto-mirror, but the
        author never wrote axis_y.mirror — the message must not say so."""
        result = self._render_multiples(tmp_path, "line", {"columns": "region"})
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-ENDPOINT-LABELS"
        assert "axis_y.mirror" not in diagnostic.message

    def test_line_multiples_rows_and_columns_raises_typed_code(
        self, tmp_path: Path
    ) -> None:
        result = self._render_multiples(
            tmp_path, "line", {"rows": "region", "columns": "segment"}
        )
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-ENDPOINT-LABELS"
        assert "axis_y.mirror" not in diagnostic.message

    def test_area_multiples_columns_raises_typed_code_not_mirror(
        self, tmp_path: Path
    ) -> None:
        result = self._render_multiples(tmp_path, "area", {"columns": "region"})
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-ENDPOINT-LABELS"
        assert "axis_y.mirror" not in diagnostic.message

    def test_bar_opt_in_multiples_raises_typed_code(self, tmp_path: Path) -> None:
        """bar's default already stepped aside before this change — the opt-in is
        the only way its rail meets a facet, and it must fail the same way."""
        result = self._render_multiples(tmp_path, "bar", {"rows": "region"})
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-ENDPOINT-LABELS"

    def test_board_level_opt_in_raises_typed_code(self, tmp_path: Path) -> None:
        """A board-level `charts.line.endpoint_labels.visible: true` is as
        concrete as a chart-local one."""
        result = _render(
            tmp_path,
            "line",
            {"multiples": {"rows": "region"}},
            board_style={"charts": {"line": {"endpoint_labels": {"visible": True}}}},
        )
        assert _diagnostic(result).code == "ERR-MULTIPLES-ENDPOINT-LABELS"

    @staticmethod
    def _render_multiples(
        tmp_path: Path, chart_type: str, multiples: dict[str, str]
    ) -> BoardRenderResult:
        return _render(
            tmp_path,
            chart_type,
            {
                "multiples": multiples,
                "style": {"endpoint_labels": {"visible": True}},
            },
        )


class TestAuthoredMirrorStillNamesItself:
    """Defect 2's other half: when the author DID write axis_y.mirror, the
    rejection must still name it — that field really is theirs to fix."""

    def test_authored_mirror_plus_endpoint_labels_names_mirror(
        self, tmp_path: Path
    ) -> None:
        result = _render(
            tmp_path,
            "line",
            {"style": {"axis_y": {"mirror": True}}},
        )
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MIRROR-ENDPOINT-LABELS"
        assert "axis_y.mirror" in diagnostic.message


class TestSiblingRestrictionsAlsoTyped:
    """The two documented `multiples:` restrictions this task didn't originally
    touch had the identical defect (a bare raise stamping ERR-INTERNAL) — same
    fix, same test shape."""

    def test_multiples_plus_support_table_raises_typed_code(
        self, tmp_path: Path
    ) -> None:
        result = _render(
            tmp_path,
            "bar",
            {
                "multiples": {"rows": "region"},
                "support_table": {"entries": [{"source": "revenue"}]},
            },
        )
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-SUPPORT-TABLE"

    def test_multiples_independent_scale_plus_mirror_raises_typed_code(
        self, tmp_path: Path
    ) -> None:
        result = _render(
            tmp_path,
            "line",
            {
                "multiples": {"columns": "region", "scale": "independent"},
                "style": {"axis_y": {"mirror": True}},
            },
        )
        diagnostic = _diagnostic(result)
        assert diagnostic.code == "ERR-MULTIPLES-INDEPENDENT-SCALE-MIRROR"


class TestAcceptedShapes:
    """Ordinary, valid combinations must still render — no false-positive guard."""

    def test_line_without_multiples_renders(self, tmp_path: Path) -> None:
        result = _render(tmp_path, "line", {})
        assert result.status == "ok"
        assert not result.chart_errors

    def test_area_without_multiples_renders(self, tmp_path: Path) -> None:
        result = _render(tmp_path, "area", {})
        assert result.status == "ok"
        assert not result.chart_errors

    def test_line_multiples_columns_endpoint_labels_fix_hint_round_trip(
        self, tmp_path: Path
    ) -> None:
        """The error message's own hint — "Set style.endpoint_labels.visible:
        false ... to keep the small multiples" — must actually fix the chart it
        was raised on. Prove the round trip on the exact opted-in config that
        raises: start from ``visible: true`` (raises), flip to the literal
        remedy (renders). ``visible: false`` is a no-op against the *default*
        (nothing opted in), so this only proves something the default-off
        sweep in ``TestAcceptedShapes`` can't: that the hint text is honest
        advice for the chart that actually needed it."""
        multiples = {"columns": "region"}
        raised = _render(
            tmp_path,
            "line",
            {"multiples": multiples, "style": {"endpoint_labels": {"visible": True}}},
        )
        assert _diagnostic(raised).code == "ERR-MULTIPLES-ENDPOINT-LABELS"

        fixed = _render(
            tmp_path,
            "line",
            {"multiples": multiples, "style": {"endpoint_labels": {"visible": False}}},
        )
        assert fixed.status == "ok"
        assert not fixed.chart_errors

    def test_line_multiples_rows_endpoint_labels_fix_hint_round_trip(
        self, tmp_path: Path
    ) -> None:
        multiples = {"rows": "region"}
        raised = _render(
            tmp_path,
            "line",
            {"multiples": multiples, "style": {"endpoint_labels": {"visible": True}}},
        )
        assert _diagnostic(raised).code == "ERR-MULTIPLES-ENDPOINT-LABELS"

        fixed = _render(
            tmp_path,
            "line",
            {"multiples": multiples, "style": {"endpoint_labels": {"visible": False}}},
        )
        assert fixed.status == "ok"
        assert not fixed.chart_errors

    def test_bar_multiples_columns_renders(self, tmp_path: Path) -> None:
        """Control case: bar's shipped default is a legend, not endpoint
        labels, so multiples: columns must keep rendering unaffected."""
        result = _render(tmp_path, "bar", {"multiples": {"columns": "region"}})
        assert result.status == "ok"
        assert not result.chart_errors

    @pytest.mark.parametrize("chart_type", ["area", "bar", "line"])
    @pytest.mark.parametrize("axis", ["rows", "columns"])
    def test_multiples_with_a_color_series_renders(
        self, tmp_path: Path, chart_type: str, axis: str
    ) -> None:
        """The shipped default names the series with a legend above the panels
        rather than refusing the composition — on every cartesian family that
        carries a rail, whichever way the grid is split."""
        result = _render(tmp_path, chart_type, {"multiples": {axis: "region"}})
        assert result.status == "ok"
        assert not result.chart_errors

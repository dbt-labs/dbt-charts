"""Multi-row pie/donut with no `color:` still names every wedge directly.

Regression for the F1 follow-up left open by PR #4125 (default 2-line label
template): a pie/donut authored without `color:` renders wedges with a
default no-color label template ("{{ percent }}\\n{{ value }}") that has no
category name at all -- the query's own remaining column (e.g. `segment` in
`[segment, value]`) is simply dropped on the floor. RJ's decision (task
`default-identification-path-for-multi-row-pie-and-donut-without-color`,
option A): name every wedge directly, using the query's sole non-theta
column as the identity text -- without turning it into a distinct-hue color
encoding. Wedges stay monochrome (`palette[0]`); only the label text gains a
name. When the query returns theta plus two or more other columns, which one
is "the" category is genuinely ambiguous, so nothing is inferred -- the
no-color template is unchanged (no guessing among candidates).

Drives the real end-to-end path (`render_dashboard`, in-process static data)
so the resolve/emit wiring is pinned too, not just a helper in isolation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from dbt_charts.cli.filesystem_project import FilesystemProject
from dbt_charts.core.board import BoardRenderResult, render_dashboard
from dbt_charts.core.execute.env import build_exec_env
from dbt_charts.core.project import InMemoryBoard


def _render_pie(
    tmp_path: Path,
    columns: list[str],
    rows: list[tuple[Any, ...]],
    *,
    chart_type: str = "pie",
    theta: str = "value",
) -> BoardRenderResult:
    project = FilesystemProject(tmp_path)
    registry = build_exec_env(project, read_only=False)
    values = json.dumps([list(row) for row in rows])
    columns_yaml = json.dumps(columns)
    yaml_content = f"""
title: pie implicit identity
queries:
  data:
    columns: {columns_yaml}
    values: {values}
charts:
  c:
    type: {chart_type}
    query: data
    theta: {theta}
rows:
  - c
"""
    return render_dashboard(
        board=InMemoryBoard(yaml_content, path=project.path("charts/_t.yml")),
        project=project,
        env=registry,
        result_cache=None,
        format="svg",
    )


def _label_texts(svg: str) -> list[str]:
    """Every slice label's rendered text, tspans joined with a space."""
    out = []
    for match in re.finditer(r"<text[^>]*>(.*?)</text>", svg, re.DOTALL):
        tspans = re.findall(r"<tspan[^>]*>(.*?)</tspan>", match.group(1))
        if tspans:
            out.append(" ".join(tspans))
    return out


def _wedge_fills(svg: str) -> list[str]:
    return re.findall(
        r'aria-roledescription="arc mark"[^>]*fill="([^"]*)"', svg
    ) or re.findall(r'fill="([^"]*)"[^>]*aria-roledescription="arc mark"', svg)


class TestSoleOtherColumnNamesWedges:
    def test_no_color_pie_names_wedges_from_sole_other_column(
        self, tmp_path: Path
    ) -> None:
        result = _render_pie(
            tmp_path,
            ["segment", "value"],
            [("Enterprise", 10000), ("Mid-Market", 6000), ("SMB", 4000)],
        )
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        texts = _label_texts(result.data)
        assert any("Enterprise" in t for t in texts), texts
        assert any("Mid-Market" in t for t in texts), texts
        assert any("SMB" in t for t in texts), texts

    def test_no_color_pie_names_wedges_on_donut_too(self, tmp_path: Path) -> None:
        result = _render_pie(
            tmp_path,
            ["segment", "value"],
            [("Enterprise", 10000), ("Mid-Market", 6000), ("SMB", 4000)],
            chart_type="donut",
        )
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        texts = _label_texts(result.data)
        assert any("Enterprise" in t for t in texts), texts

    def test_no_color_pie_wedges_stay_monochrome_when_named(
        self, tmp_path: Path
    ) -> None:
        """Naming the wedge is a label-text change only -- fill stays palette[0]
        for every wedge, the same single-series contract as before."""
        result = _render_pie(
            tmp_path,
            ["segment", "value"],
            [("Enterprise", 10000), ("Mid-Market", 6000), ("SMB", 4000)],
        )
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        fills = _wedge_fills(result.data)
        assert len(fills) == 3, result.data
        assert len(set(fills)) == 1, fills


class TestAmbiguousColumnsAreNotGuessed:
    def test_no_color_pie_with_two_extra_columns_keeps_plain_template(
        self, tmp_path: Path
    ) -> None:
        """Two candidate name columns are genuinely ambiguous -- the engine
        must not guess between them. Labels stay percent + value only."""
        result = _render_pie(
            tmp_path,
            ["segment", "region", "value"],
            [("Enterprise", "West", 10000), ("Mid-Market", "East", 6000)],
        )
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        texts = _label_texts(result.data)
        assert not any("Enterprise" in t for t in texts), texts
        assert not any("West" in t for t in texts), texts


class TestNoExtraColumnUnaffected:
    def test_no_color_pie_with_only_theta_renders_plain_template(
        self, tmp_path: Path
    ) -> None:
        """Nothing to name when the query returns only the theta column --
        unchanged from today's percent + value labels."""
        result = _render_pie(tmp_path, ["value"], [(60,), (40,)])
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        texts = _label_texts(result.data)
        assert texts, "expected direct slice labels"


def test_infer_implicit_color_field_unit_excludes_nothing_itself() -> None:
    """The inference helper only ever sees the raw row shape -- it keeps
    ignoring the theta column itself when it's the only other key (an empty
    candidate set, not a false match). Previously grouped under a
    conditional_formatting-on-pie test class; pie can no longer author
    conditional_formatting at all, but this property of the helper itself
    is unrelated to that and still holds."""
    from dbt_charts.core.compile.resolve.chart.pie_attachment import (
        infer_implicit_color_field,
    )

    assert infer_implicit_color_field("revenue", [{"revenue": 100}]) is None


class TestNullCategoryDoesNotRenderLiteralNone:
    """A NULL in the inferred identity column must not surface as the
    literal string "None" in a wedge label -- that is a Jinja
    ``{{ color }}`` artifact of a Python ``None`` being stringified, not a
    real category name."""

    def test_null_category_row_has_no_none_literal_in_its_label(
        self, tmp_path: Path
    ) -> None:
        result = _render_pie(
            tmp_path,
            ["segment", "value"],
            [("Enterprise", 6000), (None, 4000)],
        )
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        texts = _label_texts(result.data)
        assert any("Enterprise" in t for t in texts), texts
        assert not any(re.search(r"\bNone\b", t) for t in texts), texts


class TestFullTableModeNamesAttachedRows:
    """`plan_attachment` only runs in hybrid/full_table mode -- a pie with
    enough near-equal-share rows to starve every direct label must still
    populate the attached table's name column from the inferred identity
    field, not leave it blank."""

    def test_no_color_pie_full_table_names_every_row(self, tmp_path: Path) -> None:
        rows = [(f"Segment{i}", 100) for i in range(13)]
        result = _render_pie(tmp_path, ["segment", "value"], rows)
        assert result.status == "ok", result.chart_errors
        assert isinstance(result.data, str)
        for name, _ in rows:
            assert f">{name}</text>" in result.data, (name, result.data)


class TestInferImplicitColorFieldUnit:
    def test_returns_sole_other_column(self) -> None:
        from dbt_charts.core.compile.resolve.chart.pie_attachment import (
            infer_implicit_color_field,
        )

        assert (
            infer_implicit_color_field(
                "value", [{"segment": "A", "value": 1}, {"segment": "B", "value": 2}]
            )
            == "segment"
        )

    def test_returns_none_with_no_other_columns(self) -> None:
        from dbt_charts.core.compile.resolve.chart.pie_attachment import (
            infer_implicit_color_field,
        )

        assert infer_implicit_color_field("value", [{"value": 1}, {"value": 2}]) is None

    def test_returns_none_with_ambiguous_other_columns(self) -> None:
        from dbt_charts.core.compile.resolve.chart.pie_attachment import (
            infer_implicit_color_field,
        )

        assert (
            infer_implicit_color_field(
                "value",
                [{"segment": "A", "region": "West", "value": 1}],
            )
            is None
        )

    def test_returns_none_with_empty_data(self) -> None:
        from dbt_charts.core.compile.resolve.chart.pie_attachment import (
            infer_implicit_color_field,
        )

        assert infer_implicit_color_field("value", []) is None

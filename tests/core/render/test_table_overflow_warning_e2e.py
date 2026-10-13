"""End-to-end: TABLE_COLUMNS_OVERFLOW surfaces from render() for a cramped table,
and the overflowing table stays inside its slot.

Proves the full seam — the table renderer records its real slot overflow, then
renderer.py threads that capture into the WarningContext, and the detector's
warning lands on RenderResult.warnings — that a table with room to render
stays silent, and that a data format (json/text/yaml/data) reports the same
warning as svg: every format draws the board before choosing what to emit, so
the capture that feeds this detector is never empty just because the caller
asked for data instead of svg.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from unittest.mock import Mock

from dbt_charts.core.compile import compile
from dbt_charts.core.execute import Executor
from dbt_charts.core.render import render


def _rows(n: int) -> list[dict[str, object]]:
    return [
        {
            "org_id": "act_2zGAXPCi8w3MckVTMpVT3OSQ3Ni",
            "table_name": "weekly_skip_events",
            "skips": 175000,
            "clones": 226,
            "views": 439000000,
        }
        for _ in range(n)
    ]


def _make_executor(
    board: object, query_registry: object, rows: list[dict[str, object]]
):
    ok = Mock()
    ok.is_success = True
    ok.data = rows
    ok.column_descriptions = None
    ok.resolved_relations = None
    ok.truncated_reason = None
    mock_registry = Mock()
    mock_registry.execute.return_value = ok
    return Executor(
        board, adapter_registry=mock_registry, query_registry=query_registry
    )


_NARROW = """
title: Cramped table
charts:
  orgs:
    query: q
    type: table
  filler_text:
    query: q
    type: kpi
    value: skips
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
cols:
  - id: narrow
    width: 220
    rows:
      - orgs
  - filler_text
"""

_ROOMY = """
title: Roomy table
charts:
  orgs:
    query: q
    type: table
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - orgs
"""


def test_cramped_table_warns() -> None:
    """Five wide columns forced into a 220px tile overflow it — the warning fires."""
    result = compile(_NARROW)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(5))

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert "WARN-TABLE-COLUMNS-OVERFLOW" in codes, (
        "five wide columns crammed into a 220px tile overflow it — the warning "
        "must surface on warnings"
    )
    warning = next(
        w for w in render_result.warnings if w.code == "WARN-TABLE-COLUMNS-OVERFLOW"
    )
    assert warning.chart == "orgs"
    assert warning.fix


def test_roomy_table_silent() -> None:
    """A table with a full-width row has room; the warning stays quiet."""
    result = compile(_ROOMY)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(5))

    render_result = render(result.board, executor, format="svg")

    codes = {w.code for w in render_result.warnings}
    assert "WARN-TABLE-COLUMNS-OVERFLOW" not in codes


def test_non_svg_format_reports_same_warning_as_svg() -> None:
    """JSON output draws the board the same as svg, so it reports the same
    overflow warning — not the empty capture a data walk alone would produce.
    """
    result = compile(_NARROW)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _rows(5))

    render_result = render(result.board, executor, format="json")

    codes = {w.code for w in render_result.warnings}
    assert "WARN-TABLE-COLUMNS-OVERFLOW" in codes


_SIDE_BY_SIDE = """
title: Wide table beside another
charts:
  wide_table:
    query: q
    type: table
  other_table:
    query: q
    type: table
queries:
  q:
    sql: SELECT * FROM t
    source: test_source
rows:
  - cols: [wide_table, other_table]
"""

_SVG = "{http://www.w3.org/2000/svg}"


def _wide_rows(n: int) -> list[dict[str, object]]:
    return [
        {
            "email": f"someone.with.a.long.name{r}@example-company.com",
            **{f"metric_{i:02d}": 1234567 + r * 1000 + i for i in range(22)},
        }
        for r in range(n)
    ]


def _translate_x(el: ET.Element) -> float:
    match = re.match(r"translate\(([-\d.]+)", el.get("transform", ""))
    return float(match.group(1)) if match else 0.0


def test_overflowing_table_never_paints_past_its_slot() -> None:
    """A 23-column table in half a row paints nothing past its slot's right edge.

    Every drawn element whose x lies past the slot must sit under a clip whose
    right edge is inside the slot, or it paints over the neighboring chart.
    """
    result = compile(_SIDE_BY_SIDE)
    assert result.success and result.board is not None, result.errors
    executor = _make_executor(result.board, result.query_registry, _wide_rows(5))

    render_result = render(result.board, executor, format="svg")

    assert "WARN-TABLE-COLUMNS-OVERFLOW" in {w.code for w in render_result.warnings}
    assert isinstance(render_result.output, str)
    root = ET.fromstring(render_result.output)
    chart = next(
        g for g in root.iter(f"{_SVG}g") if g.get("data-chart-id") == "wide_table"
    )
    slot_width = float(chart.get("data-chart-width", ""))
    clip_right = {
        clip.get("id"): max(
            float(r.get("x", "0")) + float(r.get("width", "0"))
            for r in clip.iter(f"{_SVG}rect")
        )
        for clip in root.iter(f"{_SVG}clipPath")
    }

    escapes: list[str] = []

    def walk(el: ET.Element, offset: float, clip: float | None) -> None:
        if (ref := el.get("clip-path")) is not None:
            right = offset + _translate_x(el) + clip_right[ref[5:-1]]
            clip = right if clip is None else min(clip, right)
        offset += _translate_x(el)
        right_edges = [
            float(el.get("x", "0")) + float(el.get("width", "0"))
            if el.tag == f"{_SVG}rect"
            else float(el.get(attr, "0"))
            for attr in ("x", "x1", "x2")
            if attr in el.attrib
        ]
        for edge in right_edges:
            x = offset + edge
            if x > slot_width and (clip is None or clip > slot_width):
                escapes.append(f"{el.tag} reaching x={x:.1f}")
        for child in el:
            walk(child, offset, clip)

    walk(chart, 0.0, None)
    assert not escapes, (
        f"{len(escapes)} elements paint past the {slot_width:.0f}px slot "
        f"onto the neighboring chart, e.g. {escapes[:3]}"
    )

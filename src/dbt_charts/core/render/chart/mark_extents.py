"""Mark geometry read back out of rendered SVG.

Stage: RENDER

Measures the actual painted extent of a chart's marks from its SVG output —
the only place that can see whether a chart's rows produced anything a reader
can see. Distinct from ``render/svg_utils.py``, which owns the chart-agnostic
SVG envelope (dimensions, inner-content extraction); this module is
path-command and elliptical-arc math that only means anything for chart
marks. Keep mark geometry here — don't grow a second SVG-parsing home in
``svg_utils.py``.

Two consumers:
- ``render_chart_item`` (``rendering.py``) calls ``all_marks_degenerate`` (and
  ``chart_has_nonzero_measure``, which reads the chart's own resolved measure
  channel rather than any SVG geometry) to guard against painting nothing.
- The visual-test corpus sweep (``dbt-charts/tests/visual/series_naming.py``)
  calls ``mark_extents`` on a whole rendered board to measure every chart's
  marks at once, keyed by chart id.

Both build on the same subtree walk and the same ``_path_extent``/
``_arc_points`` geometry, so a fix here (e.g. the ``<a xlink:href>``
transparency below) reaches both without a second implementation to drift.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from dbt_charts.core.compile.models.chart.resolved import (
    ResolvedAreaChart,
    ResolvedBarChart,
    ResolvedChart,
    ResolvedHeatmapChart,
    ResolvedLineChart,
    ResolvedPieChart,
    ResolvedScatterChart,
)
from dbt_charts.core.compile.resolve.chart._wide_fields import wide_measure_fields
from dbt_charts.core.render.chart.artifacts import ChartRenderData

SVG = "{http://www.w3.org/2000/svg}"

# vl_convert's mark-family classes for filled shapes (rect/arc/area) plus
# `mark-rule` (the always-on zero-baseline and mirror-axis reference lines,
# `features/baseline.py` / `features/mirror_axis.py`): a mark is degenerate if
# it has zero extent in EITHER axis. `mark-rule` sits with the filled group
# rather than the stroked one on purpose — a bare zero-height baseline with no
# accompanying data marks conveys nothing, same as a filled shape with no
# height. This is safe: a real chart always paints its own data marks
# alongside the baseline rule (the "all marks degenerate" quantifier only
# fires when EVERY mark is degenerate, so a healthy chart's real, non-
# degenerate bars/lines keep the guard silent regardless of the rule).
# Everything else (line/trail/symbol, and any future mark class) is treated
# as stroked/symmetric: degenerate only when BOTH axes are zero, since a flat
# horizontal *data* line still has a real, visible stroke along its drawn axis.
FILLED_MARK_CLASSES = frozenset({"mark-rect", "mark-arc", "mark-area", "mark-rule"})


@dataclass(frozen=True)
class MarkExtent:
    """The drawn size of one chart's marks, aggregated across its whole SVG."""

    chart_id: str
    count: int
    max_width: float
    max_height: float


def _str_attr(element: ET.Element, key: str) -> str:
    """``element.get(key)``, defaulting a missing attribute to the empty string."""
    value = element.get(key)
    return value if value is not None else ""


def _measure_role_marks(root: ET.Element) -> list[tuple[str, float, float]]:
    """(mark_class, width, height) for every mark under a ``role-mark`` group
    anywhere in ``root``.

    Descends the full subtree of each ``role-mark`` group, not just its direct
    children — vl_convert wraps a ``link:``-linked mark's path in
    ``<a xlink:href="…">``, so a direct-children-only scan sees the ``<a>`` and
    reports no mark at all.

    Skips ``opacity="0"`` elements inside ``mark-rect`` groups only. VL bar
    marks render as ``mark-rect`` in the SVG; the hover band
    (``features/bar_hover_band.py``) adds a second ``mark-rect`` layer whose
    paths are always opacity=0 but always have non-zero pixel extent.  Without
    this filter, ``all_marks_degenerate`` would never fire for a blank bar chart
    because the band's 10%-of-height extent is always non-zero even when every
    real bar collapses to zero.

    Other mark families (e.g. ``mark-symbol`` hover points on line charts) are
    legitimately opacity=0 in their initial static state and must still be
    counted so their charts are not mistakenly flagged as blank.

    A ``<path>`` carrying no ``d`` at all counts as a zero-extent mark rather
    than an unmeasurable one. That is the shape vl_convert emits for a line
    layer whose every datum is null: geometry read exactly, not geometry the
    measurer failed to read. Abstaining would leave the vote empty, and an
    empty vote reads as "not degenerate", so a board that painted nothing
    would report success. Only ``<path>`` is treated this way; ``<g>``/``<a>``
    wrappers under the same group are containers, not marks.
    """
    marks: list[tuple[str, float, float]] = []
    for node in root.iter():
        mark_class = _str_attr(node, "class")
        if "role-mark" not in mark_class:
            continue
        is_rect_group = "mark-rect" in mark_class
        for child in node.iter():
            if is_rect_group and child.get("opacity") == "0":
                continue
            box = _path_extent(child)
            if box is None and child.tag == f"{SVG}path" and child.get("d") is None:
                box = (0.0, 0.0)
            if box is not None:
                marks.append((mark_class, box[0], box[1]))
    return marks


def mark_extents(svg: str) -> list[MarkExtent]:
    """Per-chart mark geometry, measured from a rendered board's SVG.

    Width alone is not enough. A bar chart on a quantitative x emits the right
    number of marks, at the right x positions, in the right colors — with
    height 0. Every naming and legibility check passed it; the chart was
    blank. Measure both axes.
    """
    root = ET.fromstring(svg)
    extents = []
    for group in root.iter(f"{SVG}g"):
        if "dbt-chart" not in _str_attr(group, "class").split():
            continue
        marks = _measure_role_marks(group)
        extents.append(
            MarkExtent(
                chart_id=_str_attr(group, "data-chart-id"),
                count=len(marks),
                max_width=max((w for _cls, w, _h in marks), default=0.0),
                max_height=max((h for _cls, _w, h in marks), default=0.0),
            )
        )
    return extents


def _is_degenerate_mark(mark_class: str, width: float, height: float) -> bool:
    is_filled = any(cls in FILLED_MARK_CLASSES for cls in mark_class.split())
    if is_filled:
        return width == 0 or height == 0
    return width == 0 and height == 0


def all_marks_degenerate(svg: str) -> bool:
    """True when every mark measured in ``svg`` is degenerate — including when
    the SVG has no ``role-mark`` group at all (the blank-chart case).

    ``svg`` is a single chart's own rendered output (not a whole board), so
    every ``role-mark`` group found anywhere in it belongs to that one chart.
    A chart with one zero-valued bar among several is not degenerate — only a
    chart where nothing at all paints is.

    A ``role-mark`` group whose geometry ``_path_extent`` cannot read (e.g. a
    rotated elliptical arc, or a quadratic/smooth Bézier — cubic Béziers,
    including a rounded bar corner, are measured) is excluded from the vote
    rather than treated as degenerate: an unmeasurable mark is evidence the
    measurer has a gap, not evidence the chart is blank. Firing on that would
    replace a healthy render with an error card — worse than staying silent
    for a shape the measurer doesn't yet understand.

    The one exception is a ``<path>`` carrying no ``d`` at all: that is not an
    unreadable shape but an explicitly empty one (what an all-null line layer
    emits), so it votes degenerate rather than abstaining. Abstaining left the
    vote empty, and an empty vote read as "not degenerate" — a blank chart
    that raised nothing.
    """
    root = ET.fromstring(svg)
    if not any("role-mark" in _str_attr(node, "class") for node in root.iter()):
        return True
    marks = _measure_role_marks(root)
    if not marks:
        return False
    return all(_is_degenerate_mark(cls, w, h) for cls, w, h in marks)


def _measure_columns(chart: ResolvedChart) -> list[str]:
    """The data column(s) this chart's mark family paints from.

    A bar/line/area/scatter always measures on ``y`` — ``orientation`` only
    swaps which *VL channel* the measure is drawn on (``bar.py``: "chart.x is
    the category field on both orientations"); the resolved model's own x/y
    semantics never swap. Heatmap encodes its measure through ``color`` (x/y
    are its two categorical dimensions, not measures). Pie/donut measures
    ``theta``. The geo families raise ``ERR-CHART-PAINTED-NO-MARKS`` in the
    engine and never reach this guard.

    Returns an empty list when the family has no resolvable measure column
    (e.g. a non-plotting family this is never called
    for — see ``PAINTS_MARKS``) — the caller treats that as "can't tell",
    not as "all zero".
    """
    if wide_fields := wide_measure_fields(chart):
        # Wide charts fold query columns via VL's transform; the measure columns
        # exist in the query rows, not the synthetic WIDE_VALUE_FIELD.
        return list(wide_fields)
    if isinstance(
        chart,
        (ResolvedBarChart, ResolvedLineChart, ResolvedAreaChart, ResolvedScatterChart),
    ):
        column: str | None = chart.y
    elif isinstance(chart, ResolvedHeatmapChart):
        column = chart.color
    elif isinstance(chart, ResolvedPieChart):
        column = chart.theta
    else:
        return []
    if column is None:
        return []
    return [column]


def chart_has_nonzero_measure(chart: ResolvedChart, rows: ChartRenderData) -> bool:
    """True when the chart's rows carry at least one non-null, non-zero measure
    value — the fact that separates a legitimately-zero render (the engine
    painting exactly what the data said) from a genuinely broken one.

    A chart whose family has no resolvable measure column (see
    ``_measure_columns``) returns True: there is nothing to read the
    all-zero verdict from, so the guard falls through to geometry alone,
    unchanged from before this carve-out existed.
    """
    columns = _measure_columns(chart)
    if not columns:
        return True
    return any(row.get(column) not in (None, 0) for row in rows for column in columns)


def _float_attr(element: ET.Element, key: str) -> float:
    """``element.get(key)`` as a float, defaulting a missing attribute to 0 —
    the SVG spec's own default for an omitted ``line``/``rect`` coordinate."""
    value = element.get(key)
    return float(value) if value is not None else 0.0


def _path_extent(element: ET.Element) -> tuple[float, float] | None:
    """Width and height of a mark, from explicit attributes or its path outline.

    Bar marks are emitted as relative-command paths (`M14,310h6v0h-6Z`), so a parser
    that bails on `h`/`v` measures nothing at all while appearing to pass. Scatter
    symbols and pie wedges are drawn with elliptical arcs, so one that bails on `A`
    measures nothing for two whole families — which is what this did, silently, for
    eighteen charts.

    Returns None only for geometry it genuinely cannot read, so a skip stays a skip.
    """
    if element.get("width") is not None and element.get("height") is not None:
        return _float_attr(element, "width"), _float_attr(element, "height")
    if element.get("x1") is not None or element.get("x2") is not None:
        # `mark-rule` (the always-on zero-baseline, and mirror-axis rules) draws
        # a plain SVG <line>, not a <path> — x1/y1/x2/y2, missing ends default
        # to 0 per the SVG spec.
        return (
            abs(_float_attr(element, "x2") - _float_attr(element, "x1")),
            abs(_float_attr(element, "y2") - _float_attr(element, "y1")),
        )
    d = element.get("d")
    if not d:
        return None
    if re.search(r"[sqtSQT]", d):
        # Quadratic/smooth Béziers are not emitted by any mark renderer this
        # corpus exercises. Bounding them by their control hull would be sound
        # but untested; refuse instead. Cubic (C/c) is handled below — vega's
        # rounded-corner rect (bar mark's cornerRadius) emits it.
        return None

    xs: list[float] = []
    ys: list[float] = []
    x = y = 0.0
    for cmd, arg_text in re.findall(r"([MmLlHhVvAaCcZz])([^MmLlHhVvAaCcZz]*)", d):
        args = [float(n) for n in re.findall(r"-?\d*\.?\d+(?:e-?\d+)?", arg_text)]
        if cmd in "Zz":
            continue
        if cmd in "Hh":
            for a in args:
                x = a if cmd == "H" else x + a
                xs.append(x)
                ys.append(y)
            continue
        if cmd in "Vv":
            for a in args:
                y = a if cmd == "V" else y + a
                xs.append(x)
                ys.append(y)
            continue
        if cmd in "Cc":
            # Cubic Bézier: a curve always lies within the convex hull of its
            # start point plus the two control points plus its end point, so
            # folding all three trailing points (both controls and the end)
            # into the running bbox conservatively bounds it — never smaller
            # than the true extent, which is what matters for a degenerate-
            # mark check (a real curve never measures as zero this way).
            for i in range(0, len(args) - 5, 6):
                x1, y1, x2, y2, ex, ey = args[i : i + 6]
                if cmd == "c":
                    x1, y1, x2, y2, ex, ey = (
                        x + x1,
                        y + y1,
                        x + x2,
                        y + y2,
                        x + ex,
                        y + ey,
                    )
                xs += [x1, x2, ex]
                ys += [y1, y2, ey]
                x, y = ex, ey
            continue
        if cmd in "Aa":
            for i in range(0, len(args) - 6, 7):
                rx, ry, rotation, large_arc, sweep, ex, ey = args[i : i + 7]
                ex, ey = (ex, ey) if cmd == "A" else (x + ex, y + ey)
                points = _arc_points(x, y, rx, ry, rotation, large_arc, sweep, ex, ey)
                if points is None:
                    return None
                xs += [p[0] for p in points]
                ys += [p[1] for p in points]
                x, y = ex, ey
            continue
        for i in range(0, len(args) - 1, 2):
            dx, dy = args[i], args[i + 1]
            x, y = (dx, dy) if cmd.isupper() else (x + dx, y + dy)
            xs.append(x)
            ys.append(y)

    if not xs:
        return None
    return max(xs) - min(xs), max(ys) - min(ys)


def _arc_points(  # the SVG arc command's own parameter list
    x0: float,
    y0: float,
    rx: float,
    ry: float,
    rotation: float,
    large_arc: float,
    sweep: float,
    x1: float,
    y1: float,
) -> list[tuple[float, float]] | None:
    """Points that bound an elliptical arc: its endpoints plus any axis extreme on it.

    A wedge's bulge lies outside its chord, so measuring endpoints alone under-reports
    every pie slice. This converts to the center parameterization (SVG F.6.5) and keeps
    whichever of the ellipse's four axis extremes fall inside the arc's actual sweep.
    """
    if rotation % 360 != 0:
        # Not emitted by any mark renderer here, and the math below assumes it away.
        return None
    if not rx or not ry or (x0, y0) == (x1, y1):
        return [(x0, y0), (x1, y1)]

    dx2, dy2 = (x0 - x1) / 2, (y0 - y1) / 2
    rx, ry = abs(rx), abs(ry)
    oversize = dx2**2 / rx**2 + dy2**2 / ry**2
    if oversize > 1:
        rx, ry = rx * math.sqrt(oversize), ry * math.sqrt(oversize)

    numerator = rx**2 * ry**2 - rx**2 * dy2**2 - ry**2 * dx2**2
    denominator = rx**2 * dy2**2 + ry**2 * dx2**2
    scale = math.sqrt(max(numerator, 0) / denominator) * (
        -1 if large_arc == sweep else 1
    )
    cx = scale * rx * dy2 / ry + (x0 + x1) / 2
    cy = -scale * ry * dx2 / rx + (y0 + y1) / 2

    start = math.atan2((y0 - cy) / ry, (x0 - cx) / rx)
    end = math.atan2((y1 - cy) / ry, (x1 - cx) / rx)
    traveled = (end - start) % (2 * math.pi)
    if not sweep:
        traveled -= 2 * math.pi

    points = [(x0, y0), (x1, y1)]
    for quarter in range(4):
        angle = quarter * math.pi / 2
        delta = (angle - start) % (2 * math.pi)
        if traveled < 0:
            delta -= 2 * math.pi
        if abs(delta) <= abs(traveled):
            points.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))
    return points

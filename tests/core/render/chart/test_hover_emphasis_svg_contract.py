"""What the hover runtime reads off the rendered SVG, pinned on the render side.

Hover emphasis itself is decided entirely in the runtime; render's job is to
make sure the facts the runtime needs are present in the SVG. Some of those
facts Vega-Lite already writes on its own: which marks are big painted shapes
worth receding (``aria-roledescription="bar"`` or ``"rect mark"`` -- bars and
heatmap cells, the two families whose hover emphasis has been reviewed).
Others render must stamp on explicitly, since no VL attribute says them
directly: ``data-dbt-chart-canvas`` is the color receded marks blend toward
(Vega paints no ground rect for a chart transparent over its board),
``data-dbt-magnitude-colored`` answers "is rewriting this chart's color safe",
and ``data-dbt-value-label`` marks a mark's own printed value so its twin
search can be scoped to it. If any of these stopped arriving, hovering would
silently do nothing (or worse, recede a gradient chart's color) -- so these
tests are the render-side half of that contract.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from ..._svg_render import render_board_to_svg

_SVG_NS = "http://www.w3.org/2000/svg"

_QUERY = """
queries:
  q:
    type: values
    rows:
      - {cat: A, val: 10, series: X}
      - {cat: B, val: 4, series: Y}
"""

_CASES: dict[str, tuple[str, bool]] = {
    # name: (chart body, its data marks are bars)
    "plain bar": ("type: bar\n    x: cat\n    y: val", True),
    "histogram": ("type: histogram\n    x: val", True),
    "line": ("type: line\n    x: cat\n    y: val", False),
    "heatmap": ("type: heatmap\n    x: cat\n    y: series\n    color: val", False),
}


def _render(body: str) -> str:
    return render_board_to_svg(f"""
title: T
{_QUERY}
charts:
  c:
    query: q
    {body}
rows:
  - c
""")


@pytest.mark.parametrize(("name", "case"), _CASES.items())
def test_vega_lite_names_bar_marks_and_nothing_else_bar(
    name: str, case: tuple[str, bool]
) -> None:
    """A heatmap's cells are rect marks too; only the bar family carries "bar"."""
    body, is_bar = case
    svg = _render(body)

    assert ('aria-roledescription="bar"' in svg) is is_bar, (
        f"{name}: Vega-Lite's bar marker disagrees with the family"
    )


def test_a_heatmaps_cells_are_named_rect_mark() -> None:
    """The other half of the geometry gate: RECEDE_ROLES admits "rect mark"
    (a heatmap's cells) alongside "bar" -- this pins that Vega-Lite actually
    writes that string onto a heatmap's cells, the fact the runtime's
    isRecedableMark() depends on."""
    svg = _render("type: heatmap\n    x: cat\n    y: series\n    color: series")

    assert 'aria-roledescription="rect mark"' in svg


def test_a_kpi_has_no_canvas_to_stamp_and_still_renders() -> None:
    """A KPI paints no marks that recede, so its resolved chart carries no
    canvas; the wrapper must simply omit the stamp rather than fail."""
    svg = render_board_to_svg("""
title: T
queries:
  q:
    type: values
    rows:
      - {val: 10}
charts:
  c:
    query: q
    type: kpi
    value: val
rows:
  - c
""")

    assert '<g class="dbt-chart"' in svg
    assert "data-dbt-chart-canvas" not in svg


def test_a_faceted_chart_emits_one_shared_cell_with_unclassed_panel_children() -> None:
    """chart_interactivity.js's panelScope() walks up from a hovered mark to
    the direct child of `.role-scope.cell` that contains it, and treats that
    child as the panel. That walk only works if Vega-Lite emits exactly one
    shared cell wrapper with plain, unclassed `<g>` panels as its direct
    children -- this pins that structural fact so a Vega-Lite upgrade that
    changes it fails here, not silently in the runtime.
    """
    svg = _render(
        "type: bar\n    x: cat\n    y: val\n    multiples:\n      columns: series"
    )
    root = ET.fromstring(svg)

    cells = root.findall(f".//{{{_SVG_NS}}}g[@class='mark-group role-scope cell']")
    assert len(cells) == 1, (
        "a facet must emit exactly one shared .role-scope.cell wrapper"
    )

    panels = list(cells[0])
    assert len(panels) >= 2, (
        "the faceted board must actually split into multiple panels"
    )
    assert all(panel.get("class") is None for panel in panels), (
        "each panel must be an unclassed direct child of the cell -- "
        "panelScope() cannot otherwise tell a panel boundary from any other node"
    )


def test_value_label_marks_a_bars_label_but_not_a_donuts_outside_or_center() -> None:
    """``data-dbt-value-label`` is the join key recedableMarks() reads to
    scope its twin search to a mark's OWN printed value -- a bar's label must
    carry it, and a donut's leader-lined outside label and center-total
    layers (painted by the engine's pie family, never through the value-label
    feature) must not, or they would incorrectly recede with an unrelated
    wedge.
    """
    bar_svg = _render(
        "type: bar\n    x: cat\n    y: val\n"
        "    style:\n      marks:\n        bar:\n          labels:\n            visible: true"
    )
    assert bar_svg.count('data-dbt-value-label="true"') == 1

    donut_svg = _render("type: donut\n    theta: val\n    color: series")
    # Not a bare substring check: the embedded chart_interactivity.js runtime
    # mentions the attribute name in its own source (the selector that reads
    # it), which is legitimately present on every rendered chart regardless.
    assert 'data-dbt-value-label="true"' not in donut_svg


def test_value_label_marks_every_facet_panels_own_value() -> None:
    """vl_convert names a faceted panel's mark groups ``child_layer_N_marks``,
    not the bare ``layer_N_marks`` a plain unit spec gets -- and repeats that
    same class once per panel. The stamp must land on every panel's group,
    not just the first one ``str.find`` would have hit.
    """
    svg = render_board_to_svg(f"""
title: T
{_QUERY}
charts:
  c:
    query: q
    type: bar
    x: cat
    y: val
    multiples:
      columns: series
    style:
      marks:
        bar:
          labels:
            visible: true
rows:
  - c
""")
    assert svg.count('data-dbt-value-label="true"') == 2


def test_magnitude_colored_attribute_marks_a_gradient_bar_but_not_a_series_bar() -> (
    None
):
    """The palette gate reads compile-time truth (resolved_channels["color"].mode),
    not a rendered legend node -- this pins that the fact reaches the wrapper
    element as ``data-dbt-magnitude-colored`` regardless of legend visibility."""
    gradient_svg = _render(
        "type: bar\n    x: cat\n    y: val\n    color: val\n"
        "    style:\n      color:\n        gradient:\n          palette: dbt-seq-blue"
    )
    series_svg = _render("type: bar\n    x: cat\n    y: val\n    color: series")

    assert 'data-dbt-magnitude-colored="true"' in gradient_svg
    # Not a bare substring check: the embedded chart_interactivity.js runtime
    # mentions the attribute name in its own source (isMagnitudeColored()'s
    # hasAttribute call), which is present on every rendered chart regardless.
    assert 'data-dbt-magnitude-colored="true"' not in series_svg


def test_magnitude_colored_attribute_survives_a_hidden_legend() -> None:
    """The bug the old DOM sniff had: a gradient chart with no rendered legend
    node used to look categorical and wrongly recede. The attribute is baked
    from the resolved channel, so hiding the legend must not remove it."""
    svg = _render(
        "type: bar\n    x: cat\n    y: val\n    color: val\n"
        "    style:\n      color:\n        gradient:\n          palette: dbt-seq-blue\n"
        "      legend:\n        visible: false"
    )

    assert 'class="role-legend-gradient' not in svg
    assert 'data-dbt-magnitude-colored="true"' in svg


def test_magnitude_colored_attribute_on_heatmap_tracks_the_color_fields_own_data() -> (
    None
):
    """A heatmap can use either palette type -- the family never decides this,
    the color field's own data does. A categorical color field stays
    recede-eligible; a numeric one (the default, undecorated authoring every
    real heatmap in this codebase uses) must not."""
    categorical_svg = _render(
        "type: heatmap\n    x: cat\n    y: series\n    color: series"
    )
    numeric_svg = _render("type: heatmap\n    x: cat\n    y: series\n    color: val")

    # Not a bare substring check -- see the comment in the gradient/series test above.
    assert 'data-dbt-magnitude-colored="true"' not in categorical_svg
    assert 'data-dbt-magnitude-colored="true"' in numeric_svg


_MAGNITUDE_CASES: dict[str, tuple[str, bool]] = {
    # A bare `color: <field>` never sets mode to "gradient" -- VL still
    # renders a continuous gradient legend for a numeric field, regardless
    # of chart family. The gate must catch this for every cartesian family,
    # not just heatmap.
    "bar bare numeric color": (
        "type: bar\n    x: cat\n    y: val\n    color: val",
        True,
    ),
    "scatter bare numeric color": (
        "type: scatter\n    x: val\n    y: val\n    color: val",
        True,
    ),
    "heatmap bare numeric color": (
        "type: heatmap\n    x: cat\n    y: series\n    color: val",
        True,
    ),
    "bar explicit gradient hidden legend": (
        "type: bar\n    x: cat\n    y: val\n    color: val\n"
        "    style:\n      color:\n        gradient:\n          palette: dbt-seq-blue\n"
        "      legend:\n        visible: false",
        True,
    ),
    "bar string category": (
        "type: bar\n    x: cat\n    y: val\n    color: series",
        False,
    ),
    "heatmap string category": (
        "type: heatmap\n    x: cat\n    y: series\n    color: series",
        False,
    ),
    "single-series bar no color": ("type: bar\n    x: cat\n    y: val", False),
}


@pytest.mark.parametrize(("name", "case"), _MAGNITUDE_CASES.items())
def test_magnitude_colored_attribute_across_cartesian_families(
    name: str, case: tuple[str, bool]
) -> None:
    """A bare numeric ``color:`` field resolves to Vega-Lite's continuous
    scale (a gradient legend) the same way for every cartesian family --
    not just heatmap. Regression pin for the bug where a bar or scatter
    colored by a bare numeric field rendered a gradient legend but carried
    no ``data-dbt-magnitude-colored`` attribute, so hovering wrongly
    receded marks whose color encoded a real magnitude."""
    body, expected = case
    svg = _render(body)

    assert ('data-dbt-magnitude-colored="true"' in svg) is expected, name

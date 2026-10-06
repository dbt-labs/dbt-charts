"""``y: [a, b]`` + ``color: dim`` on bar/area/line — the wide fold's synthetic
series key widens to a ``<dimension> - <measure>`` composite, so a pivoted
result (N measures as columns, grouped by a dimension) draws N × k series.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.chart._wide_fields import (
    WIDE_LABEL_FIELD,
    WIDE_VALUE_FIELD,
    unfold_wide_rows,
    wide_measure_labels_for,
    wide_series_names,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style_and_context,
)
from dbt_charts.core.diagnostics import (
    ERR_BAR_DUPLICATE_ROWS,
    ERR_MULTI_Y_COLOR_CONFLICT,
    ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR,
)
from dbt_charts.core.diagnostics.chart_data import ChartDataError
from dbt_charts.core.diagnostics.codes_render import ERR_COLOR_NULL_SERIES
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart

_BOARD_STYLE, _ = resolve_style_and_context(get_theme_style("stark"))
_CTX = resolve_chart_style_context(get_theme_style("stark"))

# Two measures × two lists: the shape every semantic-layer / pivoted result has.
_DATA: list[dict[str, Any]] = [
    {"date": "2024-01-01", "list": "bugs", "messages": 312, "fixes": 128},
    {"date": "2024-01-01", "list": "hackers", "messages": 2306, "fixes": 903},
    {"date": "2024-02-01", "list": "bugs", "messages": 300, "fixes": 100},
    {"date": "2024-02-01", "list": "hackers", "messages": 2400, "fixes": 950},
]
_SERIES = ["bugs - fixes", "bugs - messages", "hackers - fixes", "hackers - messages"]


def _color_domains(node: Any) -> list[list[str]]:
    """Every color encoding's pinned ``scale.domain``, at any nesting depth."""
    found: list[list[str]] = []
    if isinstance(node, dict):
        color = node.get("color")
        if isinstance(color, dict) and isinstance(color.get("scale"), dict):
            domain = color["scale"].get("domain")
            if isinstance(domain, list):
                found.append(domain)
        for value in node.values():
            found.extend(_color_domains(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_color_domains(item))
    return found


def _painted_series(node: Any) -> list[tuple[str, str | None]]:
    """``(computed label, fill)`` for every point symbol in a Vega scenegraph.

    vl-convert's scenegraph carries no datum, but each symbol's accessibility
    description lists the encoded fields — the color field's value sits after
    the U+2062 marker — and its fill is the palette slot Vega resolved for
    that value (None when the value fell outside the pinned domain).
    """
    found: list[tuple[str, str | None]] = []
    if isinstance(node, dict):
        if node.get("marktype") == "symbol":
            for item in node.get("items", []):
                match = re.search("\u2062([^;]*);", item.get("description", ""))
                if match:
                    found.append((match.group(1), item.get("fill")))
        for value in node.values():
            found.extend(_painted_series(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_painted_series(item))
    return found


def _render(chart, data):
    rc = resolve(chart, data, chart_style_context=_CTX)
    return rc, render_resolved_chart(rc, data, _BOARD_STYLE, width=480).payload


class TestCompositeSeries:
    @pytest.mark.parametrize(
        ("chart_type", "stack"),
        [
            ("line", None),
            ("area", "none"),
            ("area", "zero"),
            ("area", "normalize"),
            ("area", "center"),
            ("bar", "none"),
            ("bar", "zero"),
            ("bar", "normalize"),
            ("bar", "center"),
            ("scatter", None),
        ],
    )
    def test_wide_plus_dimension_renders_four_series(
        self, make_chart, chart_type, stack
    ):
        """Every family and stack mode: the pinned domain is the four
        composites, and Vega compiles the spec (the stacked paths add an
        order channel keyed off the same labels)."""
        import vl_convert as vlc

        kwargs = {"stack": stack} if stack else {}
        chart = make_chart(
            chart_type, x="date", y=["messages", "fixes"], color="list", **kwargs
        )
        rc, spec = _render(chart, _DATA)
        assert rc.wide_measures == ("messages", "fixes")
        assert rc.color == "list"
        domains = _color_domains(spec)
        assert domains, f"no pinned color domain in spec: {list(spec)}"
        for domain in domains:
            assert sorted(domain) == _SERIES
        assert "<svg" in vlc.vegalite_to_svg(json.dumps(spec))

    @pytest.mark.parametrize(
        ("chart_type", "stack"),
        [
            ("line", None),
            ("area", "none"),
            ("area", "zero"),
            ("area", "normalize"),
            ("bar", "none"),
            ("bar", "zero"),
            ("bar", "normalize"),
            ("scatter", None),
        ],
    )
    def test_measure_null_across_one_dimension_value_still_renders(
        self, make_chart, chart_type, stack
    ):
        """A composite absent from every row (``fixes`` null on every
        ``hackers`` row) must not trip the paint order derived from the
        observed rows: the chart still pins a domain of composites (area's
        spatial order drops the row-less series, the others keep the full
        cross product) and Vega compiles it."""
        import vl_convert as vlc

        data = [
            dict(row, fixes=None) if row["list"] == "hackers" else row for row in _DATA
        ]
        kwargs = {"stack": stack} if stack else {}
        chart = make_chart(
            chart_type, x="date", y=["messages", "fixes"], color="list", **kwargs
        )
        _, spec = _render(chart, data)
        domains = _color_domains(spec)
        assert domains
        for domain in domains:
            assert domain and set(domain) <= set(_SERIES)
        assert "<svg" in vlc.vegalite_to_svg(json.dumps(spec))

    @pytest.mark.parametrize("chart_type", ["line", "area", "bar"])
    def test_dimension_in_data_no_longer_trips_duplicate_rows(
        self, make_chart, chart_type
    ):
        """Two rows per x (one per list) is the right grain once color: names
        the list — the uniqueness key is (x, dim), not bare x.

        Scatter is deliberately excluded: two points sharing one x is not
        a duplicate for scatter the way it is for bar/line/area (no
        stack/line-continuity to corrupt), so it never runs
        ERR_BAR_DUPLICATE_ROWS's check at all -- see
        ``ScatterEmitter.emit()``, which calls neither
        ``validate_preaggregated_data_per_panel`` nor
        ``validate_color_series``.
        """
        chart = make_chart(chart_type, x="date", y=["messages", "fixes"], color="list")
        _render(chart, _DATA)
        without_color = make_chart(chart_type, x="date", y=["messages", "fixes"])
        with pytest.raises(ChartDataError) as exc:
            _render(without_color, _DATA)
        assert exc.value.code == ERR_BAR_DUPLICATE_ROWS

    @pytest.mark.parametrize(
        "values",
        [
            ["bugs", "hackers"],
            [True, False],
            [1.0, 2.5],
            [Decimal("1.50"), Decimal("2")],
            [7, 8],
        ],
        ids=["str", "bool", "float", "decimal", "int"],
    )
    def test_vl_fold_and_python_unfold_name_the_same_series(self, make_chart, values):
        """The VL ``calculate`` label and ``unfold_wide_rows`` are two
        implementations of one rule. Vega evaluates the transform for real
        here (scenegraph): every painted point's computed label must be a name
        Python pinned into the scale domain, and every point must have taken a
        palette fill — a datum outside the pinned domain gets none. Python
        ``str()`` and Vega ``toString()`` disagree on bool/float/Decimal, so
        a label that re-stringifies on the VL side fails this on those ids.
        """
        import vl_convert as vlc

        data = [dict(row, list=values[i % 2]) for i, row in enumerate(_DATA)]
        chart = make_chart("line", x="date", y=["messages", "fixes"], color="list")
        rc, spec = _render(chart, data)
        expected = set(
            wide_series_names(
                rc.wide_measures,
                rc.color,
                data,
                wide_measure_labels_for(rc.wide_measures, rc.field_labels),
            )
        )
        assert len(expected) == 4
        scene = vlc.vegalite_to_scenegraph(json.dumps(spec))
        painted = _painted_series(scene)
        by_label = {
            label: {fill for painted_label, fill in painted if painted_label == label}
            for label in expected
        }
        assert set(by_label) == {label for label, _ in painted}
        assert all(None not in fills for fills in by_label.values()), painted
        # Every point also draws a halo in one shared color; the series
        # colors are what is left once that common fill is removed, and each
        # composite must own one of its own.
        halo = set.intersection(*by_label.values())
        assert len({frozenset(fills - halo) for fills in by_label.values()}) == 4

    @pytest.mark.parametrize(
        ("chart_type", "stack"),
        [("line", None), ("bar", "zero"), ("area", "zero"), ("scatter", None)],
    )
    def test_empty_result_set_still_compiles_in_vega(
        self, make_chart, chart_type, stack
    ):
        """No rows means no dimension values and no series: the label and
        stack-order expressions must still be Vega expressions, and the
        chart must draw an empty plot rather than die in the tokenizer."""
        import vl_convert as vlc

        kwargs = {"stack": stack} if stack else {}
        chart = make_chart(
            chart_type, x="date", y=["messages", "fixes"], color="list", **kwargs
        )
        _, spec = _render(chart, [])
        assert "<svg" in vlc.vegalite_to_svg(json.dumps(spec))

    def test_single_measure_list_still_crosses_the_dimension(self, make_chart):
        """``y: [a]`` is wide (the emitters branch on shape, not length), so
        one measure by two lists is two composite series."""
        chart = make_chart("line", x="date", y=["messages"], color="list")
        _, spec = _render(chart, _DATA)
        for domain in _color_domains(spec):
            assert sorted(domain) == ["bugs - messages", "hackers - messages"]

    def test_null_dimension_is_rejected_whatever_its_dtype(self, make_chart):
        """A null dimension names no series in Python but VL's fold still
        paints it — so it is refused up front, even on a numeric column that
        the ordinary series-color null check would wave through as continuous."""
        data = [dict(r, list=(1 if r["list"] == "bugs" else None)) for r in _DATA]
        chart = make_chart("line", x="date", y=["messages", "fixes"], color="list")
        with pytest.raises(ChartDataError) as exc:
            _render(chart, data)
        assert exc.value.code == ERR_COLOR_NULL_SERIES

    def test_gradient_color_still_conflicts(self, make_chart):
        """A gradient binds color to a numeric scale; it names no series to
        cross the measures with, so the fold still refuses it."""
        chart = make_chart(
            "line",
            x="date",
            y=["messages", "fixes"],
            color="messages",
            style={"color": {"gradient": {"palette": ["#ffffff", "#0000ff"]}}},
        )
        with pytest.raises(CompilationError) as exc:
            resolve(chart, _DATA, chart_style_context=_CTX)
        assert exc.value.code == ERR_MULTI_Y_COLOR_CONFLICT

    def test_measure_name_containing_separator_rejected_with_no_data(self, make_chart):
        """A measure column whose own name contains the ``<value> - <measure>``
        composite separator can't be split back apart -- this must be caught
        at resolve() from the authored y: list alone, with EMPTY rows. Before
        this check moved here, it only fired from `humanize_wide_series_name`
        once real rows produced a dimension value, so a board with no
        distinct dimension values yet in its query result compiled clean and
        only crashed the first time data supplied one."""
        chart = make_chart(
            "line", x="date", y=["messages", "gross - net"], color="list"
        )
        with pytest.raises(CompilationError) as exc:
            resolve(chart, [], chart_style_context=_CTX)
        assert exc.value.code == ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR


class TestUnfoldHelpers:
    def test_unfold_labels_are_dimension_dash_measure(self):
        rows = unfold_wide_rows(_DATA[:2], ("messages", "fixes"), "list")
        assert [(r[WIDE_LABEL_FIELD], r[WIDE_VALUE_FIELD]) for r in rows] == [
            ("bugs - messages", 312),
            ("bugs - fixes", 128),
            ("hackers - messages", 2306),
            ("hackers - fixes", 903),
        ]

    def test_unfold_without_dimension_keeps_measure_labels(self):
        rows = unfold_wide_rows(_DATA[:1], ("messages", "fixes"), None)
        assert [r[WIDE_LABEL_FIELD] for r in rows] == ["messages", "fixes"]

    def test_series_names_cross_authored_measures_with_observed_dimension(self):
        # An all-null measure still owns its palette slots; a null dimension
        # value never names a series (same rule as distinct_series_values).
        data = [
            {"date": "d", "list": "bugs", "messages": 1, "fixes": None},
            {"date": "d", "list": None, "messages": 2, "fixes": None},
        ]
        labels = {"messages": "messages", "fixes": "fixes"}
        assert wide_series_names(("messages", "fixes"), "list", data, labels) == [
            "bugs - fixes",
            "bugs - messages",
        ]
        assert wide_series_names(("messages", "fixes"), None, data, labels) == [
            "fixes",
            "messages",
        ]

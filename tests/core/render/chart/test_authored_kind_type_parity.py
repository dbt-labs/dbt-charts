"""Parity gate: every resolved chart family's painted headline run carries a
resolvable ``data-authored-kind`` leaf marker.

This is the test that should have caught the table gap before it shipped
(the table's title/subtitle runs painted no marker at all, so a table's
title only ever selected the chart for the Design panel — inline edit never
opened, unlike the same gesture on a bar chart). Building it surfaced two more
instances of the identical gap, fixed alongside it: ``callout.py``'s title and
``spark_bar.py``'s title/subtitle. KPI needed no fix — its label run already
carried the marker.

The type set iterated is not a hand list: it is introspected directly off the
``ResolvedChart`` discriminated union, so a newly registered chart family is
picked up automatically and must be accounted for in ``_NO_PAINTED_TITLE``
(explicitly declaring "this family paints no title text at all") or it will
fail here until someone declares an expectation for it.
"""

from __future__ import annotations

import typing
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.models.chart.normalized.callout import CalloutChart
from dbt_charts.core.compile.models.chart.resolved import ResolvedChart
from dbt_charts.core.compile.models.query.normalized import ValuesQuery
from dbt_charts.core.compile.normalize.charts import normalize_chart
from dbt_charts.core.compile.resolve import resolve
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context
from dbt_charts.core.render.chart.vega_lite import render_resolved_chart
from dbt_charts.core.render.converters.chart import render_chart_artifact

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "parity"
# Every fixture-backed family renders at the same slot the VL parity fixtures
# already assume (test_render_parity.py's _DEFAULT_BOX) — pie's resolve bakes
# its width in, and a mismatched render width raises.
_WIDTH = 600.0
_HEIGHT = 300.0


def _resolved_chart_type_registry() -> frozenset[str]:
    """Every resolved-stage ``chart_type`` string the renderer dispatches on.

    Introspects the ``ResolvedChart`` discriminated union's member models
    (rather than a hand-maintained list): each variant's ``chart_type`` field
    is a ``Literal`` naming the string(s) it resolves to (``ResolvedBarChart``
    alone covers both ``"bar"`` and ``"histogram"``).
    """
    union_type = typing.get_args(ResolvedChart)[0]
    types: set[str] = set()
    for model_cls in typing.get_args(union_type):
        annotation = model_cls.model_fields["chart_type"].annotation
        types.update(typing.get_args(annotation))
    return frozenset(types)


# Resolved chart_type strings that paint no title-like text run at all, so no
# data-authored-kind leaf marker is expected for them. Empty today: every
# resolved family paints something (KPI its label, callout its title,
# everything else its title/subtitle) — kept as an explicit, reasoned set
# rather than an absence, so a family with a genuine gap here declares it
# instead of silently going untested.
_NO_PAINTED_TITLE: frozenset[str] = frozenset()

_REGISTRY = _resolved_chart_type_registry()
_TESTED_TYPES = sorted(_REGISTRY - _NO_PAINTED_TITLE)


def _expected_kind(chart_type: str) -> str:
    """The authored key a chart type's headline run is edited through.

    KPI is the one outlier: its authored field is ``label:``, not ``title:``
    (``title:``/``subtitle:`` are rejected outright on ``type: kpi``).
    """
    return "label" if chart_type == "kpi" else "title"


def _supports_subtitle(chart_type: str) -> bool:
    """KPI has no subtitle concept; callout's model carries ``title`` only."""
    return chart_type not in ("kpi", "callout")


def _board_style() -> Any:
    return resolve_style_and_context(get_theme_style(get_default_theme_name()))


def _load_fixture(
    chart_type: str,
) -> tuple[str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Reuse test_render_parity.py's fixture shape: one authored chart + query
    per family, already solved for the tricky ones (geoshape's geo_source,
    point_map's lat/lng)."""
    raw = yaml.safe_load(
        (_FIXTURE_DIR / f"{chart_type}.yml").read_text(encoding="utf-8")
    )
    chart_id: str = raw["chart_id"]
    chart_def: dict[str, Any] = dict(raw["chart"])
    query_registry: dict[str, Any] = {}
    for qname, qdef in (raw.get("queries") or {}).items():
        query_registry[qname] = ValuesQuery(
            columns=qdef.get("columns"),
            values=qdef.get("values"),
            rows=qdef.get("rows") or [],
        )
    query_name: str = chart_def.get("query") or next(iter(query_registry))
    data: list[dict[str, Any]] = query_registry[query_name].rows
    return chart_id, chart_def, query_registry, data


def _render_svg(chart_type: str) -> str:
    """Render one titled (and, where supported, subtitled) chart of
    ``chart_type`` through the real resolve -> render pipeline — the same two
    calls ``_render_chart_item_inner`` makes in production
    (``rendering.py``), stopping short of the outer authoring-group wrapper
    (``data-authored-path``), which this test doesn't need.
    """
    board_rs, board_ctx = _board_style()
    if chart_type == "callout":
        # No fixture: CalloutChart is a minimal static model with no query.
        compiled: Any = CalloutChart(
            id="chart1", type="callout", message="hi", title="Chart Title"
        )
        data: list[dict[str, Any]] = []
    else:
        chart_id, chart_def, query_registry, data = _load_fixture(chart_type)
        if chart_type == "kpi":
            chart_def["label"] = "Chart Title"
        else:
            chart_def["title"] = "Chart Title"
            if _supports_subtitle(chart_type):
                chart_def["subtitle"] = "Chart Subtitle"
        compiled = normalize_chart(chart_id, chart_def, query_registry, sources={})

    resolved = resolve(compiled, data, chart_style_context=board_ctx, width=_WIDTH)
    artifact = render_resolved_chart(
        resolved, data, board_rs, width=_WIDTH, height=_HEIGHT, is_placeholder=False
    )
    return render_chart_artifact(
        artifact,
        "svg",
        board_rs,
        width=_WIDTH,
        height=_HEIGHT,
        is_placeholder=False,
        chart_id=resolved.id,
    )


def test_registry_fully_accounted_for() -> None:
    """Every resolved chart_type is either tested below or explicitly declared
    as painting no title at all — a newly registered family lands in neither
    set until a human decides which."""
    assert _NO_PAINTED_TITLE <= _REGISTRY
    assert set(_TESTED_TYPES) | _NO_PAINTED_TITLE == _REGISTRY


@pytest.mark.parametrize("chart_type", _TESTED_TYPES)
def test_title_run_carries_authored_kind(chart_type: str) -> None:
    svg = _render_svg(chart_type)
    kind = _expected_kind(chart_type)
    assert f'data-authored-kind="{kind}"' in svg, (
        f"{chart_type!r}'s painted {kind!r} run has no data-authored-kind leaf "
        "marker — inline-edit can select the chart but never opens the overlay"
    )


@pytest.mark.parametrize(
    "chart_type", [t for t in _TESTED_TYPES if _supports_subtitle(t)]
)
def test_subtitle_run_carries_authored_kind(chart_type: str) -> None:
    svg = _render_svg(chart_type)
    assert 'data-authored-kind="subtitle"' in svg, (
        f"{chart_type!r}'s painted subtitle run has no data-authored-kind leaf marker"
    )

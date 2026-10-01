"""A temporal layout this module could not fit must not forbid Vega to thin.

``label_overlap: "allow"`` maps to VL ``labelOverlap: false`` — an instruction
NOT to remove overlapping labels. Asserting it on a layout whose own
measurement just reported a residual collision spends the one fallback left:
Vega's adaptive removal measures the real painted text after layout, which is
exactly the check a width prediction can get wrong.

Temporal only. A dropped date label is recoverable from its neighbors on a
ruler; a dropped category is not, which is why the discrete branch keeps
asserting the directive — pinned below, and by the "without skipping" tests in
``test_overlap_strategy.py`` / ``test_label_cadence_ladder.py``.
"""

from __future__ import annotations

import dataclasses
from typing import Any


def _axis(channel_type: str, skip: bool, tilt: bool) -> Any:
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
    from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context

    charts = resolve_chart_style_context(get_theme_style())
    axis_x = resolved_axis_style(
        charts, "axis_x", channel_type, chart_type="", label_authored=False
    )
    labels = dataclasses.replace(
        axis_x.labels,
        overlap=dataclasses.replace(axis_x.labels.overlap, skip=skip, tilt=tilt),
        angle=None,
    )
    return dataclasses.replace(axis_x, labels=labels)


def _temporal_layout(skip: bool, tilt: bool, chart_width: float = 200.0) -> Any:
    """Eleven months of 2022 with a wide authored format, on a narrow card.

    A sub-year domain is what makes this unfittable rather than merely
    coarse: the ladder runs out of rungs at ``yearquarter`` because ``year``
    has no two openers to step to, so no cadence clears "November 2022" at a
    ~16px band. ``tilt: false`` closes the last exit.
    """
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    axis = _axis("temporal", skip, tilt)
    axis = dataclasses.replace(
        axis, labels=dataclasses.replace(axis.labels, format="%B %Y")
    )
    data: list[dict[str, Any]] = [
        {"d": f"2022-{month:02d}-01", "y": 1} for month in range(2, 13)
    ]
    return resolve_axis_x_overlap(
        axis,
        "d",
        data,
        label_usable_ratio=0.9,
        edge_labels_flushed=False,
        chart_width=chart_width,
        continuous_temporal=False,
    )


def test_unfittable_temporal_layout_hands_thinning_back_to_vega() -> None:
    """``skip: true, tilt: false`` is an author who will accept dropped labels
    but not rotated ones — so when nothing fits, let Vega drop them."""
    layout = _temporal_layout(skip=True, tilt=False)
    assert layout.collision_label_count is not None
    assert layout.label_overlap is None


def test_skip_false_keeps_the_no_thinning_assertion() -> None:
    """``skip: false`` is the author saying never drop a label. A collision
    doesn't overrule that — it earns a warning, not a silent thinning."""
    layout = _temporal_layout(skip=False, tilt=False)
    assert layout.collision_label_count is not None
    assert layout.label_overlap == "allow"


def test_a_temporal_layout_that_fits_still_forbids_thinning() -> None:
    """The assertion is correct when the prediction holds: the same axis on a
    wide card fits, so Vega must not drop any of its labels."""
    layout = _temporal_layout(skip=True, tilt=False, chart_width=1200.0)
    assert layout.collision_label_count is None
    assert layout.label_overlap == "allow"


def test_a_discrete_axis_forbids_thinning_even_when_it_collides() -> None:
    """A skipped category is unrecoverable — nothing on the axis says what the
    unlabeled band was — so the discrete branch keeps the directive."""
    from dbt_charts.core.render.chart.emitters._label_overlap import (
        resolve_axis_x_overlap,
    )

    data: list[dict[str, Any]] = [
        {"region": f"Metropolitan Statistical Area {i:02d}", "y": 1} for i in range(40)
    ]
    layout = resolve_axis_x_overlap(
        _axis("nominal", skip=True, tilt=False),
        "region",
        data,
        label_usable_ratio=0.9,
        edge_labels_flushed=False,
        chart_width=300.0,
        continuous_temporal=False,
    )
    assert layout.collision_label_count is not None
    assert layout.label_overlap == "allow"

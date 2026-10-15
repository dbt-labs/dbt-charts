"""The mark-style ``curve`` field is a curated Literal, not a free string.

``Curve`` (compile/models/primitives.py) is the single source of truth for
valid interpolation keywords, shared by both the authored (theme) and
resolved mark-style models. ``step-band`` is a retired keyword — only the
band-aware ``step`` is authorable — so it must hard-error like any other
bogus value.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.resolved import (
    ResolvedAreaMarkStyle,
    ResolvedLineMarkStyle,
    ResolvedStrokeStyle,
)
from dbt_charts.core.compile.models.style.theme import (
    AreaMarkStyle,
    LineMarkStyle,
    PointLabelsStyle,
)


@pytest.mark.parametrize("bad_curve", ["step-band", "bogus"])
def test_line_mark_style_rejects_invalid_curve(bad_curve):
    with pytest.raises(ValidationError, match="curve"):
        LineMarkStyle(curve=bad_curve)


@pytest.mark.parametrize("bad_curve", ["step-band", "bogus"])
def test_area_mark_style_rejects_invalid_curve(bad_curve):
    with pytest.raises(ValidationError, match="curve"):
        AreaMarkStyle(curve=bad_curve)


@pytest.mark.parametrize("bad_curve", ["step-band", "bogus"])
def test_resolved_line_mark_style_rejects_invalid_curve(bad_curve):
    with pytest.raises(ValidationError, match="curve"):
        ResolvedLineMarkStyle(
            stroke=ResolvedStrokeStyle(width=1.5),
            halo_multiplier=2.0,
            curve=bad_curve,
            labels=PointLabelsStyle(),
            disconnected_width=0.9,
        )


@pytest.mark.parametrize("bad_curve", ["step-band", "bogus"])
def test_resolved_area_mark_style_rejects_invalid_curve(bad_curve):
    with pytest.raises(ValidationError, match="curve"):
        ResolvedAreaMarkStyle(opacity=0.3, backdrop=True, curve=bad_curve)


@pytest.mark.parametrize(
    "curve",
    [
        "linear",
        "monotone",
        "natural",
        "basis",
        "cardinal",
        "step",
        "step-before",
        "step-after",
    ],
)
def test_curated_curve_values_are_accepted(curve):
    LineMarkStyle(curve=curve)
    AreaMarkStyle(curve=curve)

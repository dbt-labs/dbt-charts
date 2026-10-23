"""TDD tests for y-axis position: auto resolution.

After this feature:
- BaseAxisStyle.position accepts Literal["left","right","top","bottom","auto"]
- "auto" → "right" when endpoint labels are not enabled (default)
- "auto" → "left" when line endpoint labels are on the right edge
- Explicit "left"/"right" always pass through unchanged
- Invalid values raise ValidationError at compile time
"""

from __future__ import annotations

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context

_RESOLVED_STYLE, _BOARD_STYLE = resolve_style_and_context(get_theme_style())


# Positive cases (position flips to "left" when the endpoint-label pane
# actually fires, i.e., multi-series + non-horizontal-bar) and negative
# cases (single-series → no flip; horizontal bar → no flip) are covered
# by `test_endpoint_label_pane_predicate.py`. The orient resolver
# delegates the firing condition to
# `EndpointLabelFeature.applies_to()` in `features/endpoint_labels.py`.


def test_invalid_orientation_raises_validation_error():
    """position: 'diagonal' raises ValidationError at compile time."""
    import pytest
    from pydantic import ValidationError

    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.models.style.theme import AxisYStyle

    base = get_theme_style().charts.axis_y
    data = base.model_dump()
    data["position"] = "diagonal"
    with pytest.raises(ValidationError, match="diagonal"):
        AxisYStyle.model_validate(data)


def test_clarity_theme_preserves_auto_orientation_sentinel():
    """clarity (and descendants like paper) must keep axis_y.position
    as the 'auto' sentinel so the endpoint-labels collision flip fires.

    Hardcoding 'right' in a theme short-circuits ``_resolve_position_auto`` and
    causes the y-axis to collide with the right-edge endpoint label pane on
    multi-series line/area charts.
    """
    from dbt_charts.core.compile.config import get_theme_style

    for theme_name in ("clarity", "paper"):
        compiled = get_theme_style(theme_name)
        assert compiled.charts.axis_y.position == "auto", (
            f"{theme_name} theme overrides axis_y.position to "
            f"{compiled.charts.axis_y.position!r}; this short-circuits the "
            f"endpoint-labels auto-flip helper. Use 'auto' instead."
        )

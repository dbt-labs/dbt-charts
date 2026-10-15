"""Tests for the ``clock`` authoring field's own model + cascade behavior.

Companion to ``test_subday_clock_label_expr.py`` (the pure labelExpr
function) and ``test_subday_clock_render.py`` (real renders) — this module
covers the schema/cascade layer those two don't: that ``clock`` is a real
DimensionLabelStyle field with the right validation, that an authored value
actually survives the axis cascade into ``ResolvedAxisStyle.labels.clock``,
and that it stays x-only. Mirrors ``test_axis_label_tilt.py``'s coverage of
the sibling ``tilt_increments`` field, added alongside ``clock`` on the same
model.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_default_theme_name, get_theme_style
from dbt_charts.core.compile.resolve.style.axis_cascade import resolved_axis_style
from dbt_charts.core.compile.resolve.style.board import resolve_chart_style_context


@pytest.fixture(autouse=True)
def _reset():
    from dbt_charts.core.compile.config import reset_config

    reset_config()
    yield
    reset_config()


class TestClockField:
    def test_accepts_24_and_12(self):
        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        assert DimensionLabelStyle(clock=24).clock == 24
        assert DimensionLabelStyle(clock=12).clock == 12

    def test_rejects_any_other_value(self):
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import DimensionLabelStyle

        with pytest.raises(ValidationError):
            DimensionLabelStyle(clock=13)

    def test_axis_y_rejects_clock_as_an_extra_field(self):
        """Y-axis label is AxisLabelStyle — clock is structurally absent, so
        authoring it there must fail the same way any unknown field would
        under extra="forbid" (matching tilt_increments' own x-only
        enforcement). Behavioral check, not a model_fields introspection.
        """
        from pydantic import ValidationError

        from dbt_charts.core.compile.models.style.theme import AxisLabelStyle

        with pytest.raises(ValidationError):
            AxisLabelStyle(clock=12)


class TestClockCascade:
    def test_authored_theme_clock_survives_to_resolved_axis(self):
        """An authored clock value at the theme layer must reach
        ResolvedAxisStyle.labels.clock through the axis cascade — the same
        precedent tilt_increments sets in
        test_axis_label_tilt.py::test_honors_theme_override_ladder.
        """
        patched = get_theme_style().model_copy(
            deep=True,
            update={
                "charts": get_theme_style().charts.model_copy(
                    deep=True,
                    update={
                        "axis_x": get_theme_style(
                            get_default_theme_name()
                        ).charts.axis_x.model_copy(
                            update={
                                "labels": get_theme_style(
                                    get_default_theme_name()
                                ).charts.axis_x.labels.model_copy(update={"clock": 24})
                            }
                        )
                    },
                )
            },
        )
        charts = resolve_chart_style_context(patched)
        axis = resolved_axis_style(
            charts, "axis_x", "temporal", chart_type="", labeled=False
        )
        assert axis.labels.clock == 24

    def test_unauthored_clock_resolves_to_the_house_default(self):
        """No fixed literal here — the theme's own value is the source of
        truth, so a future change to the house default doesn't require
        touching this test (dbt-charts/AGENTS.md: don't pin theme/default
        values in tests). The presence check below still catches the
        degenerate case where the house default is deleted entirely: with
        no theme value to compare against, both sides of the equality below
        would silently agree on ``None``.
        """
        charts = resolve_chart_style_context(get_theme_style())
        axis = resolved_axis_style(
            charts, "axis_x", "temporal", chart_type="", labeled=False
        )
        assert axis.labels.clock is not None
        assert axis.labels.clock == get_theme_style().charts.axis_x.labels.clock

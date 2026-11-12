"""Regression tests: charts reject unknown top-level keys."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from dbt_charts.core.compile.compiler import compile as compile_board
from dbt_charts.core.compile.models.chart.authored import (
    AuthoredChart,
    BarLayer,
    ChartTotal,
    LayerAxisYStyle,
)
from dbt_charts.core.compile.models.conditional_predicate import ConditionalPredicate
from dbt_charts.core.compile.models.style.theme import SliceLabelsStyle

_chart_patch_adapter = TypeAdapter(AuthoredChart)

# ---------------------------------------------------------------------------
# Behavioral regression: all chart helper classes must reject unknown fields.
# ChartTotal, SliceLabelsStyle, LayerAxisYStyle, BarLayer each declare their
# own model_config with extra="forbid" (pydantic-model-consistency Axis 3).
# ---------------------------------------------------------------------------

_HELPER_CLASSES = [ChartTotal, SliceLabelsStyle, LayerAxisYStyle, BarLayer]


@pytest.mark.parametrize("cls", _HELPER_CLASSES, ids=lambda c: c.__name__)
def test_chart_helper_classes_reject_extra_fields(cls: type) -> None:
    with pytest.raises(ValidationError, match="bogus_key"):
        cls(**{"bogus_key": True})  # type: ignore[arg-type]


def test_predicate_base_rejects_extra_fields() -> None:
    """Regression: ConditionalPredicate must have extra='forbid' like all authored models."""
    with pytest.raises(ValidationError, match="bogus_key"):
        ConditionalPredicate(bogus_key=True)  # type: ignore[call-arg]


def test_chart_patch_rejects_unknown_key():
    with pytest.raises(ValidationError, match="bogus_key"):
        _chart_patch_adapter.validate_python(
            {"type": "bar", "x": "month", "y": "revenue", "bogus_key": True}
        )


def test_yaml_compile_rejects_unknown_chart_key():
    result = compile_board(
        """
title: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
    colr: region
rows:
  - revenue
"""
    )

    assert not result.success
    # result.errors is list[Diagnostic] — check message and hint
    all_text = " ".join((e.message or "") + " " + (e.hint or "") for e in result.errors)
    assert "colr" in all_text
    assert "Did you mean 'color'" in all_text


def test_yaml_compile_does_not_label_board_extra_as_chart_field():
    result = compile_board(
        """
titel: T
queries:
  q:
    source: db
    sql: SELECT 'Jan' AS month, 100 AS revenue
charts:
  revenue:
    query: q
    type: bar
    x: month
    y: revenue
rows:
  - revenue
"""
    )

    assert not result.success
    # result.errors is list[Diagnostic] — check message and hint
    all_text = " ".join((e.message or "") + " " + (e.hint or "") for e in result.errors)
    assert "titel" in all_text
    assert "Unknown chart field" not in all_text
    assert "Did you mean 'title'" in all_text


# ---------------------------------------------------------------------------
# ChartDependencies is the only dataclass in compile/models/ — must be frozen.
# ---------------------------------------------------------------------------


def test_chart_dependencies_post_construction_assignment_raises() -> None:
    """Frozen dataclass must raise FrozenInstanceError on post-construction mutation."""
    import dataclasses  # noqa: PLC0415

    from dbt_charts.core.compile.models.chart.normalized import (
        ChartDependencies,
    )  # noqa: PLC0415

    deps = ChartDependencies(
        sources=frozenset({"s"}), variables=frozenset(), queries=frozenset()
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        deps.sources = frozenset({"new"})  # type: ignore[misc]


def test_chart_dependencies_sources_is_immutable_container() -> None:
    """frozenset fields must reject .add() — container contents are also immutable."""
    from dbt_charts.core.compile.models.chart.normalized import (
        ChartDependencies,
    )  # noqa: PLC0415

    deps = ChartDependencies(
        sources=frozenset({"s"}), variables=frozenset(), queries=frozenset()
    )
    with pytest.raises(AttributeError):
        deps.sources.add("new")  # type: ignore[union-attr]

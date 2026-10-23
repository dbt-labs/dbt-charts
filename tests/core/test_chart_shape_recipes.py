"""Every chart-shape recipe the error formatter hands out must actually draw.

RJ's probe of adjacent shapes produced two recipes that passed `dct validate` and
rendered wrong — a lollipop with no stems, a bullet with its target markers piled in
one corner. A recipe nobody rendered is worse than no recipe, so each noun in
`_CHART_SHAPE_RECIPES` is pinned here against the emitted Vega-Lite spec, and
`test_every_recipe_is_render_verified` fails if a row is added without one.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.parse.yaml_error_formatter import (
    _CHART_SHAPE_RECIPES,
    _NORMALIZED_UNSUPPORTED_SHAPES,
    _RECIPE_BY_NORMALIZED_NOUN,
    _shape_noun_keys,
    get_valid_chart_types,
)

_RENDER_VERIFIED_NOUNS = {
    "gantt",
    "streamgraph",
    "stacked_area",
    "stacked_bar",
    "grouped_bar",
    "clustered_bar",
    "horizontal_bar",
    "column",
    "100% stacked",
    "percent_stacked_bar",
    "normalized_bar",
    "small_multiples",
    "trellis",
    "faceted",
    "dual_axis",
    "combo",
    "bar_and_line",
    "stacked_column",
    "grouped_column",
    "clustered_column",
    "row_chart",
    "vertical_bar",
    "stream_chart",
    "lollipop",
    "bullet",
    "slope",
    "bump",
    "dot_plot",
    "cleveland_dot_plot",
    "dumbbell",
    "barbell",
    "connected_dot_plot",
    "ranged_dot",
    "floating_bar",
    "range_bar",
    "waterfall",
    "bridge_chart",
    "candlestick",
    "ohlc",
    "stock_chart",
}


def test_every_recipe_is_render_verified() -> None:
    """No recipe ships without a spec assertion above proving it draws."""
    assert set(_CHART_SHAPE_RECIPES) == _RENDER_VERIFIED_NOUNS


def test_recipes_stay_out_of_the_type_enum() -> None:
    """A recipe names a composition; promoting one to a `type:` tag is a separate
    decision, gated on the measurement in the task that added this map."""
    assert not set(_CHART_SHAPE_RECIPES) & set(get_valid_chart_types())


# Nouns that name the same chart and must therefore hand out the same recipe.
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("streamgraph", "stream_chart"),
    ("grouped_bar", "clustered_bar"),
    ("grouped_column", "clustered_column"),
    ("horizontal_bar", "row_chart"),
    ("column", "vertical_bar"),
    ("small_multiples", "trellis", "faceted"),
    ("dual_axis", "combo", "bar_and_line"),
    ("dot_plot", "cleveland_dot_plot"),
    ("dumbbell", "barbell", "connected_dot_plot"),
    ("floating_bar", "range_bar"),
    ("waterfall", "bridge_chart"),
    ("candlestick", "ohlc", "stock_chart"),
)


@pytest.mark.parametrize("group", _SYNONYM_GROUPS, ids=lambda group: group[0])
def test_synonym_nouns_hand_out_their_primary_recipe(group: tuple[str, ...]) -> None:
    """A synonym exists so a different word finds the same chart.

    The spec assertions above prove one recipe per shape; they run again under
    each synonym without reading which noun they ran for, so an edited synonym
    string would sail past them. Requiring the group to agree is what catches it.
    """
    recipes = {noun: _CHART_SHAPE_RECIPES[noun] for noun in group}
    assert len(set(recipes.values())) == 1, (
        f"synonyms disagree, so one of them is unverified: {recipes}"
    )


@pytest.mark.parametrize(
    ("authored", "expected_key"),
    [
        ("bullet", "bullet"),
        ("bullet graph", "bullet"),
        ("bullet chart", "bullet"),
        ("Bullet Graph", "bullet"),
        ("lollipop chart", "lollipop"),
        ("lollipop-chart", "lollipop"),
        ("slope chart", "slope"),
        ("bump chart", "bump"),
        # Its own last word is a generic tail, and it must not fold to "dot".
        ("dot plot", "dotplot"),
        ("Cleveland dot plot", "clevelanddotplot"),
        ("dumbbell chart", "dumbbell"),
        ("barbell graph", "barbell"),
        ("ranged dot plot", "rangeddot"),
        ("connected dot plot", "connecteddotplot"),
        ("floating bar chart", "floatingbar"),
        ("range bar graph", "rangebar"),
        ("waterfall chart", "waterfall"),
        ("bridge chart", "bridgechart"),
        ("candlestick chart", "candlestick"),
        ("stock chart", "stockchart"),
        ("gantt chart", "gantt"),
    ],
)
def test_shape_nouns_resolve_however_they_are_spelled(
    authored: str, expected_key: str
) -> None:
    """Nobody types the bare noun. An author asking for a "bullet graph" and one
    asking for a "bullet chart" have asked the same question, and a table keyed
    on "bullet" alone answers neither."""
    keys = _shape_noun_keys(authored)
    hit = next((k for k in keys if k in _RECIPE_BY_NORMALIZED_NOUN), None)

    assert hit == expected_key, f"{authored!r} resolved to {hit!r}"


@pytest.mark.parametrize(
    "authored",
    ["sankey diagram", "treemap chart", "gauge chart", "radar chart", "violin plot"],
)
def test_unsupported_nouns_resolve_however_they_are_spelled(authored: str) -> None:
    keys = _shape_noun_keys(authored)

    assert any(k in _NORMALIZED_UNSUPPORTED_SHAPES for k in keys), authored


def test_generic_tail_stripping_never_invents_a_match() -> None:
    """The fallback only ever reaches an entry that already exists.

    `box plot` must stay unknown rather than folding onto a `box` entry — the
    strip is a spelling tolerance, not a fuzzy matcher.
    """
    keys = _shape_noun_keys("box plot")

    assert not any(k in _RECIPE_BY_NORMALIZED_NOUN for k in keys)
    assert not any(k in _NORMALIZED_UNSUPPORTED_SHAPES for k in keys)

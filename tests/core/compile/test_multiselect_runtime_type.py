"""A `multiselect` variable reaches templates as `list[str]`, always.

One declared input type used to produce three runtime shapes — `list` from a
`default:`, `str` after any interaction (the control degrades to a scalar and
the runtime writes it back as a string), and `None` with no default. Authors
paid for that: `{{ v | join(', ') }}` raised `TypeError` on an unset
multiselect, which surfaces as `ERR-INTERNAL` and takes down the whole board
rather than one tile.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.template.variables import coerce_variable_values
from dbt_charts.core.render.variables_resolve import format_variable_display_value

_REGISTRY = {"regions": Variable(input="multiselect")}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("Rappi", ["Rappi"]),
        (["Rappi"], ["Rappi"]),
        (["Rappi", "iFood"], ["Rappi", "iFood"]),
        ([], []),
    ],
    ids=["unset", "empty-string", "scalar", "single-list", "multi-list", "empty-list"],
)
def test_multiselect_always_resolves_to_a_list(
    raw: object, expected: list[str]
) -> None:
    """Every shape the runtime boundary can produce lands as `list[str]`."""
    resolved = coerce_variable_values({"regions": raw}, _REGISTRY)
    assert resolved["regions"] == expected


def test_multiselect_members_keep_their_own_types() -> None:
    """The container is narrowed, not the elements.

    Re-typing an integer option to a string would bind a varchar where the
    column is numeric — the silent-stringification failure the surrounding
    coercers exist to prevent. Display sites stringify for display; the SQL
    path must not.
    """
    resolved = coerce_variable_values({"regions": [1, 2]}, _REGISTRY)
    assert resolved["regions"] == [1, 2]


def test_numeric_multiselect_still_displays_as_text() -> None:
    """Stringification happens at the display site, where it belongs."""
    var = Variable(input="multiselect")
    assert format_variable_display_value(var, [1, 2]) == "(2) 1"


def test_unset_multiselect_joins_to_empty_rather_than_raising() -> None:
    """The board-killer: `join` on an unset multiselect must not raise.

    Reproduces the PR #6200 failure — a default-less multiselect reaching a
    heading template as `None`, where `| join(', ')` raised `TypeError` and the
    whole board rendered as `ERR-INTERNAL`.
    """
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    resolved = coerce_variable_values({"regions": None}, _REGISTRY)
    rendered = resolve_jinja_template("{{ regions | join(', ') }}", variables=resolved)
    assert rendered == ""


def test_populated_multiselect_joins_without_a_guard() -> None:
    """A bare `join` is enough — no `is string` ternary needed at the call site."""
    from dbt_charts.core.compile.template.jinja import resolve_jinja_template

    resolved = coerce_variable_values({"regions": ["Rappi", "iFood"]}, _REGISTRY)
    rendered = resolve_jinja_template("{{ regions | join(', ') }}", variables=resolved)
    assert rendered == "Rappi, iFood"


def test_display_value_accepts_the_resolved_shape() -> None:
    """The read-only strip renders the same list the queries see."""
    var = Variable(input="multiselect")
    assert format_variable_display_value(var, ["Rappi", "iFood"]) == "(2) Rappi"


def test_display_value_of_an_unset_multiselect_is_the_unset_label() -> None:
    """An empty list is unset, and reads as such rather than as an empty string."""
    var = Variable(input="multiselect")
    assert format_variable_display_value(var, []) == "All"

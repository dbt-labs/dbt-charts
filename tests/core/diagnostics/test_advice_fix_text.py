"""Fix text that names the author's concrete next move for noisy warnings."""

import pytest

from dbt_charts.core.diagnostics import (
    WARN_MISSING_JOIN_PREDICATE,
    WARN_TOO_MANY_COLOR_CATEGORIES,
)


@pytest.mark.parametrize(
    ("code", "needle"),
    [
        (WARN_TOO_MANY_COLOR_CATEGORIES, "'Other'"),
        (WARN_MISSING_JOIN_PREDICATE, "ignore: [WARN-MISSING-JOIN-PREDICATE]"),
    ],
)
def test_fix_names_the_concrete_move(code, needle: str) -> None:  # noqa: ANN001
    assert needle in code.fix_template

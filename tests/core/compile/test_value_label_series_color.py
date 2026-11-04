"""`labels.font.color: series` is a value-label-only keyword."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.tokens import _resolve_tokens_on_patch


@pytest.mark.parametrize("mark", ["point", "line"])
def test_series_is_a_value_label_font_color(mark: str) -> None:
    patch = StylePatch.model_validate(
        {"charts": {"marks": {mark: {"labels": {"font": {"color": "series"}}}}}}
    )
    resolved = _resolve_tokens_on_patch(patch, get_theme_style())
    assert (
        resolved.charts.marks.model_dump()[mark]["labels"]["font"]["color"] == "series"
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"charts": {"marks": {"point": {"color": "series"}}}},
        {"charts": {"font": {"color": "series"}}},
        {"charts": {"marks": {"bar": {"labels": {"font": {"color": "series"}}}}}},
        {
            "charts": {
                "marks": {"point": {"labels": {"font": {"background": "series"}}}}
            }
        },
    ],
)
def test_series_elsewhere_still_raises(patch: dict) -> None:
    with pytest.raises((CompilationError, ValueError), match="series"):
        _resolve_tokens_on_patch(StylePatch.model_validate(patch), get_theme_style())

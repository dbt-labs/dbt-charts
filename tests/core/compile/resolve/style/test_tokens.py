"""A bare theme role name is a color wherever a color field reads it."""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style, user_facing_theme_names
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.resolve.style.board import resolve_style_and_context


@pytest.mark.parametrize("name", user_facing_theme_names())
def test_the_muted_and_accent_roles_are_the_theme_s_muted_and_accent(
    name: str,
) -> None:
    theme = get_theme_style(name)
    patch = StylePatch.model_validate(
        {
            "annotations": {
                "rule": {
                    "marks": {
                        "rule": {"stroke": {"color": "muted"}},
                        "text": {"font": {"color": "accent"}},
                    }
                }
            }
        }
    )
    _style, ctx = resolve_style_and_context(theme, patch)
    marks = ctx.cascaded.annotations.rule.marks
    assert (marks.rule.stroke.color, marks.text.font.color) == (
        theme.secondary_ink(),
        theme.accent,
    )


def test_a_role_name_outside_a_color_field_stays_text() -> None:
    patch = StylePatch.model_validate({"title": {"font": {"family": "muted"}}})
    style, _ctx = resolve_style_and_context(get_theme_style(), patch)
    assert style.title.font.family.startswith("muted,")


def test_a_role_name_in_a_column_ref_color_field_names_the_column() -> None:
    patch = StylePatch.model_validate(
        {"charts": {"table": {"columns": {"amount": {"background": "ink"}}}}}
    )
    _style, ctx = resolve_style_and_context(get_theme_style(), patch)
    columns = ctx.cascaded.charts.table.columns
    assert columns is not None and columns["amount"].background == "ink"


def test_series_token_is_kept_only_inside_annotation_styles() -> None:
    from dbt_charts.core.compile.errors import CompilationError

    theme = get_theme_style("clarity")
    kept = StylePatch.model_validate(
        {
            "annotations": {
                "rule": {"marks": {"rule": {"stroke": {"color": "theme.series"}}}}
            }
        }
    )
    resolve_style_and_context(theme, kept)
    rejected = StylePatch.model_validate(
        {"charts": {"axis": {"grid": {"color": "theme.series"}}}}
    )
    with pytest.raises(CompilationError, match="theme.series"):
        resolve_style_and_context(theme, rejected)

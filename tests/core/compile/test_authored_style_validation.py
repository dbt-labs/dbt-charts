"""TDD regression tests for AuthoredBoard.style typed as StylePatch | None.

These tests were written BEFORE the implementation and must fail until:
  - AuthoredBoard.style is typed as StylePatch | None (not dict[str, Any] | None)
  - StylePatch accepts CSS shorthand for padding, margin, border, gap
"""

import pytest
from pydantic import ValidationError

_VALID_BOARD_BASE = {"text": "hello"}  # minimal board that passes layout validator


class TestAuthoredBoardStyleTyping:
    """Unknown keys in style: block must raise ValidationError at parse time."""

    def test_unknown_key_rejected(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        with pytest.raises(ValidationError, match="flibbertigibbet"):
            AuthoredBoard.model_validate(
                {**_VALID_BOARD_BASE, "style": {"flibbertigibbet": 5}}
            )

    def test_valid_style_accepted(self) -> None:
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        board = AuthoredBoard.model_validate(
            {**_VALID_BOARD_BASE, "style": {"background": "#f5f5f5"}}
        )
        assert board.style is not None
        assert board.style.background == "#f5f5f5"


class TestStylePatchCSSShorthands:
    """StylePatch must accept CSS shorthand strings for spacing/border/gap fields."""

    def test_padding_css_shorthand(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"padding": "16px"})
        assert patch.padding is not None
        assert patch.padding.top == 16.0
        assert patch.padding.right == 16.0
        assert patch.padding.bottom == 16.0
        assert patch.padding.left == 16.0

    def test_padding_multi_value(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"padding": "8px 16px"})
        assert patch.padding is not None
        assert patch.padding.top == 8.0
        assert patch.padding.right == 16.0
        assert patch.padding.bottom == 8.0
        assert patch.padding.left == 16.0

    def test_margin_css_shorthand(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"margin": "8px 0 0 0"})
        assert patch.margin is not None
        assert patch.margin.top == 8.0
        assert patch.margin.right == 0.0
        assert patch.margin.bottom == 0.0
        assert patch.margin.left == 0.0

    def test_border_css_shorthand(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"border": "2px solid #333"})
        assert patch.border is not None
        assert patch.border.width == 2.0
        assert patch.border.color == "#333"

    def test_gap_string(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"gap": "12px"})
        assert patch.gap == 12.0

    def test_gap_numeric(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"gap": 12})
        assert patch.gap == 12.0

    def test_text_align(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"text": {"align": "center"}})
        assert patch.text is not None
        assert patch.text.align == "center"


class TestMergedStyleOverlayFields:
    """background and border in ResolvedStyle — authored values cascade over theme."""

    def test_authored_background_overrides_theme(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.normalize.dispatch import (
            compile_board_resolved_style,
        )

        base, _, _ = compile_board_resolved_style(None, None, None)
        patch = StylePatch.model_validate({"background": "#aabbcc"})
        resolved, _, _ = compile_board_resolved_style(patch, None, None)
        assert resolved.background == "#aabbcc"
        assert resolved.background != base.background

    def test_authored_border_cascades(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.normalize.dispatch import (
            compile_board_resolved_style,
        )

        patch = StylePatch.model_validate({"border": "2px solid #333"})
        resolved, _, _ = compile_board_resolved_style(patch, None, None)
        assert resolved.border.width == 2.0
        assert resolved.border.color == "#333"

    def test_effective_padding_no_border(self) -> None:
        from dbt_charts.core.compile.models.style.resolved import effective_padding
        from dbt_charts.core.compile.normalize.dispatch import (
            compile_board_resolved_style,
        )

        resolved, _, _ = compile_board_resolved_style(None, None, None)
        ep = effective_padding(resolved)
        assert ep.top == 0.0
        assert ep.left == 0.0

    def test_effective_padding_includes_border_width(self) -> None:
        from dbt_charts.core.compile.models.style.authored import StylePatch
        from dbt_charts.core.compile.models.style.resolved import effective_padding
        from dbt_charts.core.compile.normalize.dispatch import (
            compile_board_resolved_style,
        )

        patch = StylePatch.model_validate(
            {
                "padding": {"top": 10, "right": 10, "bottom": 10, "left": 10},
                "border": "4px solid #333",
            }
        )
        resolved, _, _ = compile_board_resolved_style(patch, None, None)
        ep = effective_padding(resolved)
        assert ep.top == 14.0  # 10 padding + 4 border
        assert ep.left == 14.0


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_an_authored_style_float_rejects_a_non_finite_value(value: float) -> None:
    """`.inf` / `.nan` in YAML must fail at compile, not mid-render."""
    from dbt_charts.core.compile.models.style.authored import TableChartStylePatch

    with pytest.raises(ValidationError, match="finite"):
        TableChartStylePatch.model_validate({"preferred_width": value})

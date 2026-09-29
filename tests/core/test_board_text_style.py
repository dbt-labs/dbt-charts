"""Tests for board body-text style settings (style.text.column, align).

Covers the body-text style surface on ``StylePatch`` — column layout
and alignment live alongside each other under ``text``.

- TextColumnStyle / TextStyle model validation (extra="forbid", bounds, conflicts)
- StylePatch.model_validate() parsing of style.text
- Render output: pure SVG columns when text layout is active (no foreignObject)
- Both interactive and non-interactive export use the same SVG path
- align maps to correct SVG text-anchor values
- Columns produce multiple translate groups with ascending x offsets
- column.rule produces a vertical SVG <line> element
- Security: script/event-handler injection absent from SVG output
- Sizing: multi-column height estimation via per-column-width measurement
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.models.style.theme import ColumnRuleStyle, TextColumnStyle

from ._svg_render import render_board_file

# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestTextColumnStyle:
    def test_defaults(self):
        col = TextColumnStyle()
        assert col.max_number is None
        assert col.gap is None
        assert col.rule is None
        assert col.max_chars is None

    def test_explicit_values(self):
        col = TextColumnStyle(
            max_number=3, gap=24.0, rule=ColumnRuleStyle(width=1, color="#e5e7eb")
        )
        assert col.max_number == 3
        assert col.gap == 24.0
        assert col.rule == ColumnRuleStyle(width=1, color="#e5e7eb")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            TextColumnStyle(max_number=2, bogus="nope")

    def test_width_field_rejected(self):
        """column.width was removed in Stage 3; max_chars is the replacement."""
        with pytest.raises(ValidationError):
            TextColumnStyle(width=300.0)  # type: ignore[call-arg]

    def test_max_chars_sets_column_measure(self):
        col = TextColumnStyle(max_chars=80)
        assert col.max_chars == 80
        assert col.max_number is None

    def test_max_number_zero_rejected(self):
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            TextColumnStyle(max_number=0)

    def test_max_number_negative_rejected(self):
        with pytest.raises(ValidationError):
            TextColumnStyle(max_number=-1)

    def test_max_chars_zero_rejected(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            TextColumnStyle(max_chars=0)

    def test_max_chars_negative_rejected(self):
        with pytest.raises(ValidationError):
            TextColumnStyle(max_chars=-80)

    def test_gap_and_rule_stand_alone(self):
        """Columns are automatic, so decorating them needs no count.

        These once required ``max_number > 1`` or ``max_chars``. Requiring a
        count to set a gutter would mean pinning a ceiling the author does not
        want just to decorate a layout the renderer chose.
        """
        assert TextColumnStyle(gap=24.0).gap == 24.0
        rule = ColumnRuleStyle(width=1, color="#ccc")
        assert TextColumnStyle(rule=rule).rule == rule

    def test_column_rule_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ColumnRuleStyle(width=1, color="#ccc", bogus="nope")

    def test_column_rule_width_zero_rejected(self):
        with pytest.raises(ValidationError, match="greater than 0"):
            ColumnRuleStyle(width=0, color="#ccc")

    def test_column_rule_invalid_style_rejected(self):
        with pytest.raises(ValidationError):
            ColumnRuleStyle(width=1, color="#ccc", style="groove")  # type: ignore[arg-type]

    def test_column_rule_style_defaults_to_solid(self):
        rule = ColumnRuleStyle(width=1, color="#ccc")
        assert rule.style == "solid"

    def test_column_rule_accepts_named_colors(self):
        """Unlike the old CSS-shorthand string, a structured rule takes any color."""
        rule = ColumnRuleStyle(width=1, color="red")
        assert rule.color == "red"


# ---------------------------------------------------------------------------
# StylePatch.model_validate() parsing
# ---------------------------------------------------------------------------


class TestStylePatchTextParsing:
    def test_no_text_override_uses_theme_defaults(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"padding": "16px"})
        assert patch.text is None

    def test_empty_text_returns_none(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"text": {}})
        assert patch.text is not None
        assert patch.text.column is None
        assert patch.text.align is None

    def test_text_column_max_number(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"text": {"column": {"max_number": 3}}})
        assert patch.text is not None
        assert patch.text.column.max_number == 3

    def test_text_column_gap_and_rule(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate(
            {
                "text": {
                    "column": {
                        "max_number": 2,
                        "gap": 24,
                        "rule": {"width": 1, "color": "#ccc"},
                    }
                }
            }
        )
        assert patch.text is not None
        assert patch.text.column.gap == 24.0
        assert patch.text.column.rule.width == 1.0
        assert patch.text.column.rule.color == "#ccc"

    def test_text_align(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate({"text": {"align": "center"}})
        assert patch.text is not None
        assert patch.text.align == "center"

    def test_text_max_width_rejected(self):
        """text.max_width was never in TextStyle; must error."""
        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"text": {"align": "center", "max_width": 600}})

    def test_text_invalid_key_raises(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"text": {"nonexistent": True}})

    def test_text_non_dict_raises(self):
        """Non-dict text value must error, not silently coerce."""
        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"text": "column"})

    def test_text_alongside_other_styles(self):
        from dbt_charts.core.compile.models.style.authored import StylePatch

        patch = StylePatch.model_validate(
            {
                "padding": "16px",
                "background": "#f0f0f0",
                "text": {"column": {"max_number": 2}},
            }
        )
        assert patch.padding is not None
        assert patch.padding.top == 16.0
        assert patch.background == "#f0f0f0"
        assert patch.text is not None
        assert patch.text.column.max_number == 2

    def test_unknown_top_level_key_rejected(self):
        """Extra keys at style level must error — extra="forbid"."""
        from dbt_charts.core.compile.models.style.authored import StylePatch

        with pytest.raises(ValidationError):
            StylePatch.model_validate({"prose": {"column": {"max_number": 2}}})


# ---------------------------------------------------------------------------
# Render integration: pure SVG (no foreignObject)
# ---------------------------------------------------------------------------


_LONG_BODY_TEXT = """\
text: |
  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

  Revenue is recognized when control of goods or services is transferred to the
  customer, at an amount that reflects the consideration the entity expects to
  receive in exchange, measured at the transaction price net of discounts and
  variable consideration that is not yet resolved at the reporting date.

"""

_BODY_TEXT = """\
text: |
  **Revenue recognition.** Revenue is recognized when control of goods
  or services is transferred to the customer, at an amount that reflects
  the consideration the entity expects to receive.

  **Cost allocation.** Operating costs are allocated to segments based
  on direct usage, with shared costs distributed proportionally.

  **Currency.** All figures are reported in USD using period-end rates
  for balance sheet items and average rates for income statement items.
"""


def _column_x_offsets(svg: str) -> list[float]:
    """Return unique non-zero x offsets from translate(...) attributes."""
    matches = re.findall(r'transform="translate\(([^,)]+)', svg)
    offsets = {float(m) for m in matches if float(m) > 0}
    return sorted(offsets)


class TestTextRendering:
    def test_multi_column_renders_pure_svg(self, tmp_path: Path):
        """Multi-column text must produce pure SVG columns — no foreignObject."""
        board = tmp_path / "charts" / "cols.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _LONG_BODY_TEXT
            + """\
style:
  text:
    column:
      max_number: 3
      gap: 24
"""
        )
        svg = render_board_file(board)
        assert "<foreignObject" not in svg
        assert "<svg" in svg
        assert "<text" in svg
        # Three-column layout: at least two groups with different positive x offsets
        x_offsets = _column_x_offsets(svg)
        assert len(x_offsets) >= 2, f"Expected ≥2 column x offsets, got {x_offsets}"

    def test_single_column_uses_mdsvg_path(self, tmp_path: Path):
        board = tmp_path / "charts" / "single.yml"
        board.parent.mkdir(parents=True)
        board.write_text("text: |\n  Simple single-column text.\n")
        svg = render_board_file(board)
        assert "<svg" in svg
        assert "<foreignObject" not in svg

    def test_column_rule_renders_as_svg_line(self, tmp_path: Path):
        """column.rule must produce a vertical <line> in the SVG, not a CSS property."""
        board = tmp_path / "charts" / "rule.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _LONG_BODY_TEXT
            + """\
style:
  text:
    column:
      max_number: 2
      rule:
        width: 1
        color: "#e5e7eb"
"""
        )
        svg = render_board_file(board)
        assert "<foreignObject" not in svg
        assert "<line" in svg
        assert 'stroke="#e5e7eb"' in svg

    def test_word_wrap_in_yaml_errors(self, tmp_path: Path):
        """word_wrap was removed from the text layout API; YAML using it must error."""
        board = tmp_path / "charts" / "wrap.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    word_wrap: break-word
"""
        )
        with pytest.raises(ValueError, match="word_wrap"):
            render_board_file(board)

    def test_align_center_uses_middle_anchor(self, tmp_path: Path):
        """style.text.align: center must produce SVG text-anchor=middle."""
        board = tmp_path / "charts" / "align.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    align: center
"""
        )
        svg = render_board_file(board)
        assert "<foreignObject" not in svg
        assert 'text-anchor="middle"' in svg

    def test_align_justify_in_yaml_errors(self, tmp_path: Path):
        """align: justify was removed; YAML using it must error."""
        board = tmp_path / "charts" / "justify.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    align: justify
"""
        )
        with pytest.raises(ValueError, match="justify"):
            render_board_file(board)

    def test_max_width_in_yaml_errors(self, tmp_path: Path):
        """style.text.max_width was never valid; YAML using it must error."""
        board = tmp_path / "charts" / "maxw.yml"
        board.parent.mkdir(parents=True)
        board.write_text("text: |\n  Some text.\nstyle:\n  text:\n    max_width: 600\n")
        with pytest.raises(ValueError, match="max_width"):
            render_board_file(board)

    def test_hyphens_in_yaml_errors(self, tmp_path: Path):
        """hyphens was removed from the text API; YAML using it must error."""
        board = tmp_path / "charts" / "hyphens.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    hyphens: auto
"""
        )
        with pytest.raises(ValueError, match="hyphens"):
            render_board_file(board)

    def test_board_text_align_falls_through_to_text_align(self, tmp_path: Path):
        """style.text.align: center → SVG text-anchor middle."""
        board = tmp_path / "charts" / "fallback.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    align: center
    column:
      max_number: 2
"""
        )
        svg = render_board_file(board)
        assert "<foreignObject" not in svg
        assert 'text-anchor="middle"' in svg

    def test_svg_columned_text_path_splits_one_paragraph_across_columns(self):
        """A single long paragraph fills both columns, splitting at a line boundary.

        This asserted the opposite until continuous flow landed: whole-block fill
        could not divide a paragraph, so a lone block occupied column 0 and left
        column 1 empty. Flowing by line is the fix, and both columns carrying part
        of one paragraph is the observable difference.

        The slot has to be wide enough for two readable columns and the paragraph
        long enough to fill them -- two columns of 30 characters, or of one line
        each, are not a two-column layout and the renderer will decline to set
        them.
        """
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.prose import render_prose_svg

        theme_text = get_theme_style(None).text
        text_style = theme_text.model_copy(
            update={"column": theme_text.column.model_copy(update={"max_number": 2})}
        )
        long_para = "Word " * 200
        svg, _ = render_prose_svg(
            long_para,
            900.0,
            text_style,
            resolve_style(get_theme_style()),
        )
        assert "<text" in svg
        assert len(_column_x_offsets(svg)) == 1, (
            "the second column must receive the continuation"
        )

    def test_svg_text_path_produces_svg(self):
        """SVG multi-column text path must produce valid SVG — no foreignObject."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.boards import _render_text_svg

        theme_text = get_theme_style(None).text
        text_style = theme_text.model_copy(
            update={"column": theme_text.column.model_copy(update={"max_number": 3})}
        )
        svg, height = _render_text_svg(
            "Some multi-column text.",
            {},
            400.0,
            text_style=text_style,
            resolved_style=resolve_style(get_theme_style()),
            painted_canvas=None,
        )
        assert "<foreignObject" not in svg
        assert "<svg" in svg
        assert height > 0

    def test_prose_text_keeps_full_render_width(self):
        """Body text renders to the container width it is given."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.boards import _render_text_svg

        resolved_style = resolve_style(get_theme_style())
        container_width = 1000.0
        svg, _height = _render_text_svg(
            " ".join(["Long prose still uses the full slot in PR1."] * 30),
            {},
            container_width,
            text_style=resolved_style.text,
            resolved_style=resolved_style,
            painted_canvas=None,
        )

        assert f'width="{container_width:g}"' in svg

    def test_html_in_markdown_has_no_execution_context(self, tmp_path: Path):
        """Text layout SVG path must have no HTML execution context for injected markup.

        Without foreignObject there is no HTML parser — script tags and event
        handlers in the markdown source become escaped visible text, not code.
        """
        board = tmp_path / "charts" / "xss.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            """\
text: |
  Normal text <script>window.__xss=true</script> more text.
  <div onclick="alert(1)">Click me</div>
style:
  text:
    column:
      max_number: 2
"""
        )
        svg = render_board_file(board)
        # No HTML execution context at all
        assert "<foreignObject" not in svg
        # No unescaped script element that a browser could execute
        assert "<script>window.__xss" not in svg

    def test_column_gap_alone_renders(self, tmp_path: Path):
        """A gutter needs no count now that the renderer picks one."""
        board = tmp_path / "charts" / "gap.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    column:
      gap: 40
"""
        )
        assert "<text" in render_board_file(board)

    def test_column_gap_with_max_number_uses_svg_path(self, tmp_path: Path):
        """column.gap + column.max_number > 1 renders as SVG columns."""
        board = tmp_path / "charts" / "gap.yml"
        board.parent.mkdir(parents=True)
        board.write_text(
            _BODY_TEXT
            + """\
style:
  text:
    column:
      max_number: 2
      gap: 40
"""
        )
        svg = render_board_file(board)
        assert "<foreignObject" not in svg
        x_offsets = _column_x_offsets(svg)
        assert len(x_offsets) >= 1, "Expected at least one non-zero column x offset"


# ---------------------------------------------------------------------------
# Sizing: multi-column height estimation
# ---------------------------------------------------------------------------


class TestTextSizing:
    def test_sizing_and_render_heights_agree(self):
        """Sizing height must match render height within 1px for multi-column text."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.prose import render_prose_svg
        from dbt_charts.core.render.sizing import (
            get_markdown_text_height,
        )

        paragraph = (
            "This is a paragraph of text with enough words to wrap at column width. "
            * 8
        )
        long_text = "\n\n".join([paragraph] * 3)
        width = 800.0
        n_cols = 2
        theme_text = get_theme_style(None).text
        text_style = theme_text.model_copy(
            update={
                "column": theme_text.column.model_copy(update={"max_number": n_cols})
            }
        )

        resolved = resolve_style(get_theme_style(None))
        sized_height = get_markdown_text_height(
            long_text, width, text_style=text_style, resolved_style=resolved
        )
        _, render_height = render_prose_svg(
            long_text,
            width,
            text_style,
            resolve_style(get_theme_style()),
        )

        assert abs(sized_height - render_height) < 2.0, (
            f"Sizing ({sized_height:.1f}) and render ({render_height:.1f}) heights diverge"
        )

    def test_column_width_derives_auto_columns(self):
        """column.max_chars derives column count from container width, not a text clamp.

        A wider container derives more columns for the same column.max_chars target,
        yielding a shorter total height for the same long text.
        """
        import dataclasses

        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.render.sizing import get_markdown_text_height

        text = ("Some text with enough words to need multiple lines. " * 20).strip()
        narrow_width = 300.0
        wide_width = 600.0
        # Pin the body size instead of inheriting the theme default. The derived
        # column count is a function of font size, so a default change silently
        # moves the widths this fixture depends on. The size must go through
        # resolved_style: get_markdown_text_height reads base_font_size from the
        # resolved style, and takes only column config from text_style.
        # At 11px SourceSerif4 (xAvgCharWidth/upm ≈ 0.558), max_chars=40 → col ≈ 245px,
        # so narrow (300px) → ceil(300/245) = 2 cols; wide (600px) → 3 cols.
        resolved = resolve_style(get_theme_style())
        text_style = resolved.text.model_copy(
            update={
                "column": resolved.text.column.model_copy(update={"max_chars": 40}),
                "font": resolved.text.font.model_copy(update={"size": 11.0}),
            }
        )
        resolved = dataclasses.replace(resolved, text=text_style)

        h_narrow = get_markdown_text_height(
            text, narrow_width, text_style=text_style, resolved_style=resolved
        )
        h_wide = get_markdown_text_height(
            text, wide_width, text_style=text_style, resolved_style=resolved
        )

        # Wider container yields more columns → each column shorter → total height smaller
        assert h_wide < h_narrow

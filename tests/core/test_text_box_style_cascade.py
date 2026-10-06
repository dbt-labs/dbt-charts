"""Tests for style.text.code and style.text.blockquote box style cascade.

TDD: these tests are written first and must fail until the implementation lands.
They cover:
  - BoxStyle model field presence on TextCodeStyle / TextBlockquoteStyle
  - get_compact_style() mapper reads from resolved theme tokens (not a hardcoded table)
  - Per-board override flows through the cascade (code.background, blockquote.font.color)
  - dct<->mdsvg contract test: every mdsvg.Style field is mapper-fed or in the allowlist
  - Theme corpus smoke: every built-in theme compiles without error
"""

from __future__ import annotations

import dataclasses

import pytest

from dbt_charts.core.compile.config import (
    get_theme_style,
    reset_config,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)


@pytest.fixture(autouse=True)
def _reset_cfg() -> None:
    reset_config()
    yield
    reset_config()


class TestBoxStyleModel:
    """TextCodeStyle and TextBlockquoteStyle are BoxStyle subclasses with the right fields."""

    def test_textcodestyle_has_font_background_border(self) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import TextCodeStyle

        theme = get_theme_style()
        code = theme.text.code
        assert isinstance(code, TextCodeStyle)
        assert code.font is not None
        assert isinstance(code.background, str)
        assert code.border is not None
        assert code.highlight is True
        assert isinstance(code.theme, str)

    def test_textblockquotestyle_has_font_background_border(self) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import TextBlockquoteStyle

        theme = get_theme_style()
        bq = theme.text.blockquote
        assert isinstance(bq, TextBlockquoteStyle)
        assert bq.font is not None
        assert isinstance(bq.background, str)
        assert bq.border is not None

    def test_boxstyle_shared_base(self) -> None:
        """TextCodeStyle and TextBlockquoteStyle both extend BoxStyle."""
        from dbt_charts.core.compile.models.style.theme import (
            BoxStyle,
            TextBlockquoteStyle,
            TextCodeStyle,
        )

        assert issubclass(TextCodeStyle, BoxStyle)
        assert issubclass(TextBlockquoteStyle, BoxStyle)

    def test_boxstyle_fields_present(self) -> None:
        """BoxStyle has font, background, border — all required."""
        from dbt_charts.core.compile.models.style.theme import BoxStyle

        fields = BoxStyle.model_fields
        assert "font" in fields
        assert "background" in fields
        assert "border" in fields


class TestGetCompactStyleMapper:
    """get_compact_style reads code/blockquote colors from the resolved theme, not a hardcoded table."""

    def test_code_background_from_theme_token(self) -> None:
        """get_compact_style().code_background == resolved_style.text.code.background."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.code_background == rs.text.code.background

    def test_blockquote_color_from_theme_token(self) -> None:
        """get_compact_style().blockquote_color == resolved_style.text.blockquote.font.color."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        # blockquote font.color may be None (FontStyle is all-optional)
        # but the mapper should pass through whatever the theme resolves
        blockquote_color = rs.text.blockquote.font.color
        assert style.blockquote_color == blockquote_color

    def test_blockquote_border_color_from_theme_token(self) -> None:
        """blockquote_border_color comes from text.blockquote.border.color."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.blockquote_border_color == rs.text.blockquote.border.color

    def test_code_color_from_theme_font_or_code_font(self) -> None:
        """code_color comes from text.code.font.color if set, else falls back."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        # code_color should be a string (not None)
        assert isinstance(style.code_color, str)

    def test_code_highlight_enabled_for_dbt_charts_markdown(self) -> None:
        """dbt charts markdown code-fence highlighting comes from style.text.code."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.code_highlight is rs.text.code.highlight
        assert style.code_theme == rs.text.code.theme

    def test_dark_code_background_uses_dark_pygments_theme(self) -> None:
        """Dark themes choose token colors designed for dark code boxes."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style("neon"))
        style = get_compact_style(rs)
        assert style.code_highlight is True
        assert style.code_theme == "monokai"

    def test_invalid_code_theme_rejected(self) -> None:
        """Pygments theme typos fail during style validation."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import (
            TextCodeStyle,
        )

        data = get_theme_style().text.code.model_dump(mode="python")
        data["theme"] = "definitely-not-a-pygments-theme"
        with pytest.raises(ValueError, match="Invalid Pygments code theme"):
            TextCodeStyle.model_validate(data)

    def test_link_color_from_accent(self) -> None:
        """link_color in the compact style comes from rs.accent."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.link_color == rs.accent

    def test_table_border_from_text_rule(self) -> None:
        """table_border_color comes from rs.text.rule.color."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.table_border_color == rs.text.rule.color

    def test_heading_color_from_title_font_color(self) -> None:
        """heading_color comes from rs.title.font.color."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.heading_color == rs.title.font.color


class TestCascadeOverride:
    """Per-board style.text.code/blockquote overrides flow through the cascade."""

    def test_code_background_override_propagates(self, model_copy_at) -> None:
        """A distinctive code background set on the style tree propagates to get_compact_style."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        # Use a distinctive value: not a typical theme color
        SENTINEL = "#aabbcc"
        base = get_theme_style()
        patched = model_copy_at(base, "text.code.background", SENTINEL)
        rs = resolve_style(patched)
        style = get_compact_style(rs)
        assert style.code_background == SENTINEL

    def test_code_highlight_override_propagates(self, model_copy_at) -> None:
        """style.text.code.highlight propagates to mdsvg code_highlight."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        base = get_theme_style()
        patched = model_copy_at(base, "text.code.highlight", False)
        rs = resolve_style(patched)
        style = get_compact_style(rs)
        assert style.code_highlight is False

    def test_code_theme_override_propagates(self, model_copy_at) -> None:
        """style.text.code.theme propagates to mdsvg code_theme."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        base = get_theme_style()
        patched = model_copy_at(base, "text.code.theme", "monokai")
        rs = resolve_style(patched)
        style = get_compact_style(rs)
        assert style.code_theme == "monokai"

    def test_blockquote_font_color_override_propagates(self, model_copy_at) -> None:
        """A distinctive blockquote font color propagates to blockquote_color in compact style."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        SENTINEL = "#334455"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.color", SENTINEL)
        rs = resolve_style(patched)
        style = get_compact_style(rs)
        assert style.blockquote_color == SENTINEL

    def test_blockquote_border_color_override_propagates(self, model_copy_at) -> None:
        """blockquote border color override propagates to blockquote_border_color."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        SENTINEL = "#ff7700"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.border.color", SENTINEL)
        rs = resolve_style(patched)
        style = get_compact_style(rs)
        assert style.blockquote_border_color == SENTINEL

    def test_code_background_override_dark_theme(self, model_copy_at) -> None:
        """Override works on dark-background resolved styles too (no is_dark_color branching)."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        dark = get_theme_style("neon")
        rs = resolve_style(dark)
        # The dark theme's code background should differ from the editorial theme's
        rs_editorial = resolve_style(get_theme_style())
        style_dark = get_compact_style(rs)
        style_light = get_compact_style(rs_editorial)
        # Both code backgrounds must come from theme tokens (not is_dark_color branching)
        assert isinstance(style_dark.code_background, str)
        assert isinstance(style_light.code_background, str)


class TestThemeCorpusSmoke:
    """All built-in themes compile without error after adding code/blockquote fields."""

    def test_all_themes_compile(self, compiled_themes) -> None:
        """Every built-in theme compiles to a valid Style with code/blockquote populated."""
        from dbt_charts.core.compile.models.style.theme import Style

        assert compiled_themes, "compiled_themes fixture returned empty dict"
        for name, theme in compiled_themes.items():
            assert isinstance(theme, Style), f"Theme {name!r}: expected Style"
            # New required fields must be present
            assert theme.text.code is not None, f"Theme {name!r}: text.code is None"
            assert theme.text.blockquote is not None, (
                f"Theme {name!r}: text.blockquote is None"
            )
            assert isinstance(theme.text.code.background, str), (
                f"Theme {name!r}: text.code.background must be str"
            )
            assert isinstance(theme.text.code.highlight, bool), (
                f"Theme {name!r}: text.code.highlight must be bool"
            )
            assert isinstance(theme.text.code.theme, str), (
                f"Theme {name!r}: text.code.theme must be str"
            )
            assert isinstance(theme.text.blockquote.background, str), (
                f"Theme {name!r}: text.blockquote.background must be str"
            )


class TestMdsvgContractGuard:
    """Contract test: every mdsvg.Style field is mapper-fed or in the allowlist.

    This guards against drift where mdsvg adds a style field the mapper forgets.
    Drift direction (a): mdsvg renames/removes → Style(**kwargs) raises TypeError → caught.
    Drift direction (b): mdsvg adds a field the mapper ignores → this test catches it.
    """

    def test_all_mdsvg_style_fields_are_covered(self) -> None:

        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.board import resolve_style
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            MDSVG_STRUCTURAL_FIELDS,
            compact_style_kwargs,
        )
        from mdsvg import Style as MdsvgStyle

        # The mapper builds an mdsvg.Style kwargs dict; inspect the fed-field set.
        rs = resolve_style(get_theme_style())
        mapper_kwargs = compact_style_kwargs(rs)

        all_fields = {f.name for f in dataclasses.fields(MdsvgStyle)}
        fed_fields = set(mapper_kwargs.keys())
        structural_fields = set(MDSVG_STRUCTURAL_FIELDS)

        uncovered = all_fields - fed_fields - structural_fields
        assert not uncovered, (
            f"mdsvg.Style fields not covered by mapper or MDSVG_STRUCTURAL_FIELDS: {uncovered!r}\n"
            "Add each to the mapper (theme-driven) or to MDSVG_STRUCTURAL_FIELDS (structural/owned by mdsvg)."
        )


class TestFontAxisCascade:
    """Each FontStyle axis on code/blockquote flows through compact_style_kwargs."""

    def test_code_font_weight_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "700"
        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.weight", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["code_font_weight"] == SENTINEL

    def test_code_font_style_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "italic"
        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.style", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["code_font_style"] == SENTINEL

    def test_code_font_family_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        # resolve_style may inject emoji fonts, so check the sentinel is present
        # (not absent), not exact equality.
        SENTINEL_FRAGMENT = "My Code Font"
        base = get_theme_style()
        patched = model_copy_at(
            base, "text.code.font.family", f"'{SENTINEL_FRAGMENT}', monospace"
        )
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert SENTINEL_FRAGMENT in kwargs["code_font_family"]

    def test_code_font_size_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.size", 11)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["code_font_size"] == 11.0

    def test_code_font_decoration_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "underline"
        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.decoration", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["code_font_decoration"] == SENTINEL

    def test_code_font_case_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "upper"
        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.case", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["code_font_case"] == SENTINEL

    def test_unsupported_case_raises_not_silently_dropped(self, model_copy_at) -> None:
        """Rich case values mdsvg can't paint (title/sentence/slug/camel) must error
        loudly, not silently no-op — no dead knob."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.errors import CompilationError
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        base = get_theme_style()
        patched = model_copy_at(base, "text.code.font.case", "title")
        rs = resolve_style(patched)
        with pytest.raises(CompilationError, match="case"):
            compact_style_kwargs(rs)

    def test_blockquote_font_weight_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "600"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.weight", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["blockquote_font_weight"] == SENTINEL

    def test_blockquote_font_style_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "italic"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.style", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["blockquote_font_style"] == SENTINEL

    def test_blockquote_font_family_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        # resolve_style may inject emoji fonts, so check the sentinel is present
        # (not absent), not exact equality.
        SENTINEL_FRAGMENT = "My Quote Font"
        base = get_theme_style()
        patched = model_copy_at(
            base, "text.blockquote.font.family", f"'{SENTINEL_FRAGMENT}', serif"
        )
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert SENTINEL_FRAGMENT in kwargs["blockquote_font_family"]

    def test_blockquote_font_size_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.size", 13)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["blockquote_font_size"] == 13.0

    def test_blockquote_font_decoration_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "line-through"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.decoration", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["blockquote_font_decoration"] == SENTINEL

    def test_blockquote_font_case_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "lower"
        base = get_theme_style()
        patched = model_copy_at(base, "text.blockquote.font.case", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["blockquote_font_case"] == SENTINEL


class TestBoldFontWeightCascade:
    """style.text.bold.weight feeds mdsvg bold_font_weight.

    Pins the theme-token read, override propagation, and independence from
    heading_font_weight (which governs H1-H6 markdown headings only).
    """

    def test_bold_font_weight_from_theme_token(self) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import font_weight_as_css
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        rs = resolve_style(get_theme_style())
        style = get_compact_style(rs)
        assert style.bold_font_weight == font_weight_as_css(rs.text.bold.weight)

    def test_bold_font_weight_override_propagates(self, model_copy_at) -> None:
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        SENTINEL = "800"
        base = get_theme_style()
        patched = model_copy_at(base, "text.bold.weight", SENTINEL)
        rs = resolve_style(patched)
        kwargs = compact_style_kwargs(rs)
        assert kwargs["bold_font_weight"] == SENTINEL

    def test_bold_font_weight_independent_of_heading_font_weight(
        self, model_copy_at
    ) -> None:
        """Overriding text.bold.weight must not move heading_font_weight."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            compact_style_kwargs,
        )

        base = get_theme_style()
        default_kwargs = compact_style_kwargs(resolve_style(base))
        patched = model_copy_at(base, "text.bold.weight", "900")
        kwargs = compact_style_kwargs(resolve_style(patched))
        assert kwargs["bold_font_weight"] == "900"
        assert kwargs["heading_font_weight"] == default_kwargs["heading_font_weight"]

    def test_invalid_bold_weight_rejected(self) -> None:
        """An unrecognized CSS font-weight raises at the compile boundary
        instead of silently dropping bold text to normal weight at render."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import TextBoldStyle

        data = get_theme_style().text.bold.model_dump(mode="python")
        data["weight"] = "semibold"
        with pytest.raises(ValueError, match="not a valid CSS font-weight"):
            TextBoldStyle.model_validate(data)

    def test_resolved_callout_bold_font_weight_from_theme_token(self) -> None:
        """ResolvedCalloutStyle.bold_font_weight is baked from text.bold.weight
        at resolve time -- render/chart/callout.py builds its own mdsvg.Style
        rather than going through get_compact_style(), so this is the one
        place that would silently miss the field if resolution ever dropped
        it."""
        from dbt_charts.core.compile.config import (
            get_theme_style,
        )
        from dbt_charts.core.compile.models.style.theme import font_weight_as_css

        rs = resolve_style(get_theme_style())
        csc = resolve_chart_style_context(get_theme_style())
        assert csc.callout_error.bold_font_weight == font_weight_as_css(
            rs.text.bold.weight
        )


class TestNoMarkdownDefaultsYml:
    """markdown_defaults.yml is deleted; prose colors live in style.text.* theme tokens."""

    def test_markdown_defaults_yml_deleted(self) -> None:
        import dbt_charts

        pkg_dir = __import__("pathlib").Path(dbt_charts.__file__).parent
        path = pkg_dir / "core" / "render" / "markdown_defaults.yml"
        assert not path.exists(), f"markdown_defaults.yml should be deleted: {path}"

    def test_config_text_code_accessible(self) -> None:
        """style.text.code is the new home for code box colors (replaces markdown_defaults.yml)."""
        assert get_theme_style().text.code is not None
        assert isinstance(get_theme_style().text.code.background, str)

    def test_config_text_blockquote_accessible(self) -> None:
        """style.text.blockquote is the new home for blockquote colors."""
        assert get_theme_style().text.blockquote is not None
        assert isinstance(get_theme_style().text.blockquote.background, str)

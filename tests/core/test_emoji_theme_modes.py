"""Tests for style.font.emoji three-value mode (monochrome/system-default/disabled).

Guards:
1. Default themes have emoji == "monochrome" on Style.font.emoji.
2. Resolved font stacks include/exclude the correct emoji family per mode.
3. The mode propagates through apply_emoji_to_family and _append_emoji_family.
4. Schema rejects bool emoji, nested-font emoji, out-of-Literal values, and missing emoji.
5. The old CompiledFontsConfig / style.fonts surface is fully gone.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.models.style.theme import (
    FontStyle,
    RootFontStyle,
)
from dbt_charts.core.compile.resolve.style.board import (
    resolve_chart_style_context,
    resolve_style,
)
from dbt_charts.core.fonts import NOTO_EMOJI_FONT_FAMILY

_MONO_QUOTED = f"'{NOTO_EMOJI_FONT_FAMILY}'"


class TestDefaultIsMonochrome:
    def test_dbt_charts_default_compiled_emoji_mode(self) -> None:
        compiled = get_theme_style("clarity")
        assert compiled.font.emoji == "monochrome"

    def test_dbt_charts_default_resolved_root_font_has_noto_emoji(self) -> None:
        compiled = get_theme_style("clarity")
        resolved = resolve_style(compiled)
        assert _MONO_QUOTED in resolved.font.family

    def test_editorial_cream_resolved_title_font_has_noto_emoji(self) -> None:
        compiled = get_theme_style("paper")
        resolved = resolve_style(compiled)
        assert _MONO_QUOTED in resolved.title.font.family

    def test_editorial_cream_source_serif_4_is_primary(self) -> None:
        compiled = get_theme_style("paper")
        resolved = resolve_style(compiled)
        family = resolved.title.font.family
        serif_pos = family.find("Source Serif 4")
        emoji_pos = family.find(NOTO_EMOJI_FONT_FAMILY)
        assert serif_pos < emoji_pos, (
            f"'Source Serif 4' should appear before 'Noto Emoji' in stack: {family!r}"
        )


class TestSystemDefaultMode:
    def _with_emoji(self, mode: str):
        compiled = get_theme_style("clarity")
        return compiled.model_copy(
            deep=True,
            update={"font": compiled.font.model_copy(update={"emoji": mode})},
        )

    def test_system_default_removes_bundled_emoji_from_root(self) -> None:
        compiled = self._with_emoji("system-default")
        resolved = resolve_style(compiled)
        assert NOTO_EMOJI_FONT_FAMILY not in resolved.font.family

    def test_system_default_removes_bundled_emoji_from_title(self) -> None:
        compiled = get_theme_style("paper")
        with_sd = compiled.model_copy(
            deep=True,
            update={
                "font": compiled.font.model_copy(update={"emoji": "system-default"})
            },
        )
        resolved = resolve_style(with_sd)
        assert NOTO_EMOJI_FONT_FAMILY not in resolved.title.font.family


class TestDisabledMode:
    def _with_emoji(self, mode: str):
        compiled = get_theme_style("clarity")
        return compiled.model_copy(
            deep=True,
            update={"font": compiled.font.model_copy(update={"emoji": mode})},
        )

    def test_disabled_removes_bundled_emoji_from_root(self) -> None:
        compiled = self._with_emoji("disabled")
        resolved = resolve_style(compiled)
        assert NOTO_EMOJI_FONT_FAMILY not in resolved.font.family


class TestHelpersModeAware:
    """chart_title_spec, board_title_spec, and get_compact_style must honor font.emoji."""

    def _inject_config(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        import sys

        _cfg_mod = sys.modules["dbt_charts.core.compile.config"]
        compiled = get_theme_style("clarity")
        patched_compiled = compiled.model_copy(
            deep=True,
            update={"font": compiled.font.model_copy(update={"emoji": mode})},
        )
        # After Phase 2, style derives from _compiled_theme_cache. Inject the
        # patched compiled theme so get_theme_style() returns
        # it for the duration of this test. The resolve_style cache is cleared
        # automatically by the isolate_resolve_style_cache autouse fixture.
        monkeypatch.setitem(
            _cfg_mod._compiled_theme_cache,
            _cfg_mod.get_default_theme_name(),
            patched_compiled,
        )

    def _resolved_charts(self, mode: str):
        compiled = get_theme_style("clarity")
        patched = compiled.model_copy(
            deep=True,
            update={"font": compiled.font.model_copy(update={"emoji": mode})},
        )
        return resolve_chart_style_context(patched)

    def test_chart_title_spec_system_default_opts_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.resolve.style.typography import chart_title_spec

        self._inject_config(monkeypatch, "system-default")
        charts = self._resolved_charts("system-default")
        _, _, family = chart_title_spec(800.0, chart_style_context=charts)
        assert NOTO_EMOJI_FONT_FAMILY not in family

    def test_get_compact_style_system_default_opts_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from dbt_charts.core.compile.resolve.style.markdown_style import (
            get_compact_style,
        )

        self._inject_config(monkeypatch, "system-default")
        style = get_compact_style(resolve_style(get_theme_style()))
        assert NOTO_EMOJI_FONT_FAMILY not in style.font_family


class TestCompiledRootFontStyleSchema:
    def test_rejects_bool_emoji_true(self) -> None:
        with pytest.raises(ValidationError):
            RootFontStyle(
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
                emoji=True,  # type: ignore[arg-type]
            )

    def test_rejects_bool_emoji_false(self) -> None:
        with pytest.raises(ValidationError):
            RootFontStyle(
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
                emoji=False,  # type: ignore[arg-type]
            )

    def test_rejects_out_of_literal_emoji(self) -> None:
        with pytest.raises(ValidationError):
            RootFontStyle(
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
                emoji="rainbow",  # type: ignore[arg-type]
            )

    def test_accepts_valid_emoji_modes(self) -> None:
        for mode in ("monochrome", "system-default", "disabled"):
            s = RootFontStyle(
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
                emoji=mode,  # type: ignore[arg-type]
            )
            assert s.emoji == mode

    def test_rejects_color_emoji_mode(self) -> None:
        """The color emoji font is deleted — 'color' is no longer a valid mode."""
        with pytest.raises(ValidationError):
            RootFontStyle(
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
                emoji="color",  # type: ignore[arg-type]
            )

    def test_rejects_missing_emoji(self) -> None:
        # emoji is required — no default
        with pytest.raises(ValidationError):
            RootFontStyle(  # type: ignore[call-arg]
                family="Inter",
                color="#000",
                size=14.0,
                weight="400",
            )


class TestNestedFontRejectsEmojiField:
    """FontStyle (used for nested chart/axis/legend fonts) must not accept emoji."""

    def test_font_style_rejects_emoji_monochrome(self) -> None:
        with pytest.raises(ValidationError):
            FontStyle(emoji="monochrome")  # type: ignore[call-arg]

    def test_font_style_rejects_emoji_bool(self) -> None:
        with pytest.raises(ValidationError):
            FontStyle(emoji=True)  # type: ignore[call-arg]


class TestOldFontsSurfaceGone:
    """style.fonts.emoji (old bool surface) must no longer exist on Style."""

    def test_old_fonts_field_rejected(self) -> None:
        """Style must reject the old 'fonts' extra field at construction."""
        from dbt_charts.core.compile.models.style.theme import Style

        with pytest.raises(ValidationError, match=r"fonts"):
            Style.model_validate({"fonts": {"emoji": True}})

    def test_theme_missing_font_emoji_raises_validation_error(self) -> None:
        """A theme that inherits no emoji value should fail loudly at load time."""
        from dbt_charts.core.compile.models.style.theme import Style

        with pytest.raises(ValidationError, match=r"emoji"):
            # Build a Style with font missing the emoji field
            Style.model_validate(
                {
                    "font": {
                        "family": "Inter",
                        "color": "#000",
                        "size": 14,
                        "weight": "400",
                        # emoji omitted — must raise with emoji in error message
                    },
                }
            )

    def test_authored_board_yaml_setting_emoji_color_is_rejected(self) -> None:
        """The `color` mode must die on the real parse path, not only at the model.

        The sibling tests here call ``model_validate`` directly, which skips
        ``prepare_board_mapping`` and the schema catalog. That leaves the promise
        an author actually cares about — writing ``emoji: color`` in a board and
        being told no — pinned nowhere. A change to how migration resolves
        "current" could quietly start accepting it again.
        """
        import yaml

        from dbt_charts.core.compile.migrations.migrations import prepare_board_mapping
        from dbt_charts.core.compile.models.board.authored import AuthoredBoard

        mapping = yaml.safe_load(
            """
            title: Emoji mode
            style:
              font:
                emoji: color
            text: hello
            """
        )
        with pytest.raises(ValidationError, match=r"emoji"):
            AuthoredBoard.model_validate(prepare_board_mapping(mapping))

"""TDD tests for palette role indirection (Task C).

Tests cover:
- Alias chain resolution (ink → gray-90 → 11 → colors[10])
- Theme palettes: block → role dispatch
- Bracket form [N] 1-indexed
- Theme roles: shortcuts
- Cycle detection
- Error cases
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.resolve.style.palette import (
    UnknownColorError,
    color,
    color_from_theme,
)

# ---------------------------------------------------------------------------
# Alias chain resolution
# ---------------------------------------------------------------------------


class TestAliasChainResolution:
    def test_ink_resolves_via_integer_alias(self):
        """dbt-grays: ink → 11 (1-indexed) → colors[10] = #222222."""
        result = color_from_theme("chrome.ink", palettes={"chrome": "dbt-grays"})
        assert result.lower() == "#222222"

    def test_direct_hex_alias_resolves(self):
        """Aliases that hold a terminal hex return it directly."""
        result = color_from_theme("chrome.white", palettes={"chrome": "dbt-grays"})
        assert result.lower() == "#ffffff"

    def test_integer_alias_resolves_via_colors_array(self):
        """ink: 11 → colors[10] in dbt-grays (1-indexed)."""
        result = color_from_theme("chrome.ink", palettes={"chrome": "dbt-grays"})
        assert result.lower() == "#222222"

    def test_canvas_resolves_to_first_color(self):
        """canvas: 1 → colors[0] = #FAFAFA."""
        result = color_from_theme("chrome.canvas", palettes={"chrome": "dbt-grays"})
        assert result.lower() == "#fafafa"


# ---------------------------------------------------------------------------
# Tone palette resolution
# ---------------------------------------------------------------------------


class TestToneResolution:
    def test_negative_solid_resolves(self):
        """negative.solid resolves from tone/negative.yml aliases."""
        result = color_from_theme("negative.solid", palettes={"negative": "negative"})
        assert result.lower() == "#94001e"

    def test_negative_bg_resolves(self):
        result = color_from_theme("negative.bg", palettes={"negative": "negative"})
        assert result.lower() == "#ffedec"

    def test_positive_solid_resolves(self):
        result = color_from_theme("positive.solid", palettes={"positive": "positive"})
        assert result.lower() == "#00884d"

    def test_warning_bg_resolves(self):
        """warning palette resolves its bg slot."""
        result = color_from_theme("warning.bg", palettes={"warning": "warning"})
        # just ensure it returns a hex string
        assert result.startswith("#")


# ---------------------------------------------------------------------------
# Bracket form [N] — 1-indexed positional
# ---------------------------------------------------------------------------


class TestBracketForm:
    def test_category_bracket_1_returns_first_color(self):
        """category[1] returns colors[0] (1-indexed)."""
        result = color_from_theme("category[1]", palettes={"category": "vivid-10"})
        # vivid-10 first stop
        from dbt_charts.core.compile.resolve.style.palette import palette as raw_palette

        first = raw_palette("vivid-10")[0]
        assert result.lower() == first.lower()

    def test_category_bracket_2_returns_second(self):
        from dbt_charts.core.compile.resolve.style.palette import palette as raw_palette

        result = color_from_theme("category[2]", palettes={"category": "vivid-10"})
        second = raw_palette("vivid-10")[1]
        assert result.lower() == second.lower()

    def test_bracket_out_of_range_raises(self):
        with pytest.raises(UnknownColorError, match="slot"):
            color_from_theme("category[999]", palettes={"category": "vivid-10"})

    def test_bracket_zero_raises(self):
        """0 is not a valid 1-indexed slot."""
        with pytest.raises(UnknownColorError, match="1-indexed"):
            color_from_theme("category[0]", palettes={"category": "vivid-10"})


# ---------------------------------------------------------------------------
# single_series[N] — indexes the board's resolved single-series ink list,
# not a `palettes:` role. A theme's `single_series_palette` is a literal,
# already-cascaded list, so this bracket form is handed the list directly
# rather than a role name to look up in `palettes`.
# ---------------------------------------------------------------------------


class TestSingleSeriesBracketForm:
    def test_slot_1_returns_first_ink(self):
        result = color_from_theme(
            "single_series[1]",
            palettes={},
            single_series_palette=["#111111", "#222222"],
        )
        assert result.lower() == "#111111"

    def test_slot_2_returns_second_ink(self):
        result = color_from_theme(
            "single_series[2]",
            palettes={},
            single_series_palette=["#111111", "#222222"],
        )
        assert result.lower() == "#222222"

    def test_out_of_range_raises(self):
        with pytest.raises(UnknownColorError, match="slot"):
            color_from_theme(
                "single_series[2]", palettes={}, single_series_palette=["#111111"]
            )

    def test_zero_raises_one_indexed(self):
        with pytest.raises(UnknownColorError, match="1-indexed"):
            color_from_theme(
                "single_series[0]", palettes={}, single_series_palette=["#111111"]
            )

    def test_missing_single_series_palette_raises(self):
        """No single-series context supplied (caller never threaded it) —
        fails loudly, same as any other unresolvable token."""
        with pytest.raises(UnknownColorError, match="single.series"):
            color_from_theme("single_series[1]", palettes={})


# ---------------------------------------------------------------------------
# Categorical dotted-integer form palette.N — 1-indexed positional
# ---------------------------------------------------------------------------


class TestCategoricalDottedInteger:
    """color('palette.N') addresses the Nth stop, 1-indexed — matching the
    bracket form and scaffold/tone alias integers."""

    def test_dot_1_returns_first_color(self) -> None:
        """vivid-10.1 returns colors[0] (1-indexed)."""
        from dbt_charts.core.compile.resolve.style.palette import palette as raw_palette

        assert color("vivid-10.1").lower() == raw_palette("vivid-10")[0].lower()

    def test_dot_n_matches_bracket_n(self) -> None:
        """The dot form and the bracket form now agree at the same N."""
        for n in (1, 4, 10):
            dotted = color(f"vivid-10.{n}")
            bracket = color_from_theme(
                f"category[{n}]", palettes={"category": "vivid-10"}
            )
            assert dotted.lower() == bracket.lower()

    def test_dot_last_returns_last_color(self) -> None:
        """vivid-10.10 returns colors[9] (the tenth, final stop)."""
        from dbt_charts.core.compile.resolve.style.palette import palette as raw_palette

        stops = raw_palette("vivid-10")
        assert color(f"vivid-10.{len(stops)}").lower() == stops[-1].lower()

    def test_dot_zero_raises_one_indexed(self) -> None:
        """0 is not a valid 1-indexed slot."""
        with pytest.raises(UnknownColorError, match="1-indexed"):
            color("vivid-10.0")

    def test_dot_out_of_range_raises(self) -> None:
        with pytest.raises(UnknownColorError, match="out of range"):
            color("vivid-10.999")


# ---------------------------------------------------------------------------
# Categorical named-hue roles — swap-invariant color tokens
# ---------------------------------------------------------------------------


class TestCategoricalNamedHueRoles:
    """Named hue aliases on the categorical defaults (and their -dark/-light/
    -ghost/-ink companions) make tokens like ``category.green`` swap-invariant:
    the same token resolves to each palette's own green slot, so swapping
    stark (vivid-10) ↔ editorial (editorial-10) keeps green green without any
    author change. Companions pair positionally, so each shares its base's map.
    """

    # 1-indexed slot per role, per palette family. Pinned as a slot mapping,
    # not hex literals, so it survives palette hex retunes.
    VIVID_ROLES = {
        "blue": 1,
        "sky": 2,
        "green": 3,
        "gold": 4,
        "orange": 5,
        "purple": 6,
        "sage": 7,
        "brown": 8,
        "gray": 9,
        "charcoal": 10,
    }
    EDITORIAL_ROLES = {
        "blue": 1,
        "sky": 2,
        "green": 3,
        "brown": 8,
        "gold": 5,
        "purple": 4,
        "orange": 6,
        "sage": 7,
        "gray": 9,
        "charcoal": 10,
    }
    # A companion pairs positionally with its base, so it carries the base map.
    VIVID_FAMILY = [
        "vivid-10",
        "vivid-10-dark",
        "vivid-10-light",
        "vivid-10-ghost",
        "vivid-10-ink",
    ]
    EDITORIAL_FAMILY = [
        "editorial-10",
        "editorial-10-dark",
        "editorial-10-light",
        "editorial-10-ghost",
        "editorial-10-ink",
    ]

    def test_named_role_resolves_to_mapped_slot(self) -> None:
        from dbt_charts.core.compile.resolve.style.palette import palette as raw_palette

        for family, roles in (
            (self.VIVID_FAMILY, self.VIVID_ROLES),
            (self.EDITORIAL_FAMILY, self.EDITORIAL_ROLES),
        ):
            for pal in family:
                stops = raw_palette(pal)
                for name, slot in roles.items():
                    assert (
                        color_from_theme(
                            f"category.{name}", palettes={"category": pal}
                        ).lower()
                        == stops[slot - 1].lower()
                    ), f"{pal} {name}"

    def test_all_family_members_define_identical_vocabulary(self) -> None:
        """Swap-invariance requires every family member — base and companions —
        to define the same role names, so a token never resolves under one theme
        or emphasis tier and 500s under another."""
        from dbt_charts.core.compile.resolve.style.palette import _load_spine

        vocab = set(self.VIVID_ROLES)
        assert set(self.EDITORIAL_ROLES) == vocab  # both families share role names
        for pal in self.VIVID_FAMILY + self.EDITORIAL_FAMILY:
            assert set(_load_spine(pal).aliases or {}) == vocab, pal

    def test_hue_token_against_tonal_palette_errors(self) -> None:
        """A hue token against a mono/tonal palette (no hue vocabulary) fails
        loudly rather than silently picking a color."""
        with pytest.raises(UnknownColorError, match="green"):
            color_from_theme(
                "category.green", palettes={"category": "category-6-tonal-blue"}
            )


# ---------------------------------------------------------------------------
# Theme roles: block
# ---------------------------------------------------------------------------


class TestThemeRoles:
    def test_theme_role_resolves_via_palette(self):
        """roles.ink: chrome.heading means color('ink') → chrome.heading → #313233."""
        result = color_from_theme(
            "ink",
            palettes={"chrome": "dbt-grays"},
            roles={"ink": "chrome.heading"},
        )
        assert result.lower() == "#313233"

    def test_theme_role_chain_two_hops(self):
        """roles.text → chrome.ink → gray-90 → 11 → #222222."""
        result = color_from_theme(
            "text",
            palettes={"chrome": "dbt-grays"},
            roles={"text": "chrome.ink"},
        )
        assert result.lower() == "#222222"

    def test_unknown_role_raises(self):
        with pytest.raises(UnknownColorError):
            color_from_theme(
                "nonexistent",
                palettes={"chrome": "dbt-grays"},
                roles={},
            )


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    def test_alias_cycle_raises(self):
        """palette aliases a→b, b→a must raise at resolve time."""
        # We test this by pointing at a synthetic palette loaded from a dict.
        # The cycle-detect path is exposed via the internal resolver used by
        # color_from_theme.
        from dbt_charts.core.compile.resolve.style.palette import resolve_alias_chain

        aliases = {"a": "b", "b": "a"}
        with pytest.raises(UnknownColorError, match="cycle"):
            resolve_alias_chain("a", aliases, colors=None)

    def test_self_alias_raises(self):
        """a: a is a trivial cycle."""
        from dbt_charts.core.compile.resolve.style.palette import resolve_alias_chain

        aliases = {"a": "a"}
        with pytest.raises(UnknownColorError, match="cycle"):
            resolve_alias_chain("a", aliases, colors=None)

    def test_theme_roles_self_cycle_raises(self):
        """roles: {a: a} must raise UnknownColorError, not RecursionError."""
        with pytest.raises(UnknownColorError, match="cycle"):
            color_from_theme(
                "a",
                palettes={"chrome": "dbt-grays"},
                roles={"a": "a"},
            )

    def test_theme_roles_two_cycle_raises(self):
        """roles: {a: b, b: a} must raise UnknownColorError, not RecursionError."""
        with pytest.raises(UnknownColorError, match="cycle"):
            color_from_theme(
                "a",
                palettes={"chrome": "dbt-grays"},
                roles={"a": "b", "b": "a"},
            )


# ---------------------------------------------------------------------------
# color() backward compat — existing direct palette.slot tokens
# ---------------------------------------------------------------------------


class TestColorDirectPaletteAccess:
    def test_negative_solid_via_color(self):
        """Existing color() call for tone palettes still works."""
        assert color("negative.solid").lower() == "#94001e"

    def test_dbt_grays_ink_via_color(self):
        """Scaffold palette resolved via color() using semantic alias."""
        assert color("dbt-grays.ink").lower() == "#222222"


# ---------------------------------------------------------------------------
# ToneLiteral — neutral removed (D-029)
# ---------------------------------------------------------------------------


class TestToneLiteral:
    def test_positive_negative_warning_info_in_tone_literal(self):
        """Tone vocabulary (KPI + table conditional glyphs) is a fixed four-set."""
        from typing import get_args

        from dbt_charts.core.compile.models.chart.authored import ToneLiteral

        allowed = get_args(ToneLiteral)
        assert set(allowed) == {"positive", "negative", "warning", "info"}


# ---------------------------------------------------------------------------
# Style model palettes: and roles: fields (D-033, D-018)
# ---------------------------------------------------------------------------


class TestStylePalettesAndRolesFields:
    def test_default_theme_has_chrome_palette(self):
        """stark.yaml seeds palettes.chrome: dbt-grays."""
        from dbt_charts.core.compile.config import (
            reset_config,
        )

        reset_config()
        try:
            theme = get_theme_style()
            assert theme.palettes is not None
            assert theme.palettes.get("chrome") == "dbt-grays"
        finally:
            reset_config()

    def test_default_theme_has_tone_palettes(self):
        """stark.yaml seeds negative/positive/warning palette roles."""
        from dbt_charts.core.compile.config import (
            reset_config,
        )

        reset_config()
        try:
            theme = get_theme_style()
            assert theme.palettes is not None
            assert theme.palettes.get("negative") == "negative"
            assert theme.palettes.get("positive") == "positive"
            assert theme.palettes.get("warning") == "warning"
        finally:
            reset_config()

    def test_default_theme_has_roles_ink(self):
        """stark.yaml seeds roles.ink: chrome.heading."""
        from dbt_charts.core.compile.config import (
            reset_config,
        )

        reset_config()
        try:
            theme = get_theme_style()
            assert theme.roles is not None
            assert theme.roles.get("ink") == "chrome.heading"
        finally:
            reset_config()

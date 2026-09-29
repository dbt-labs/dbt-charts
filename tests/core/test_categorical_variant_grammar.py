"""The categorical color variant grammar's third token segment
(`category.blue.dark`, `vivid-10.3.pale`, `tableau.1.deep`).

`_split_trailing_variant` (palette.py) is the one place the segment is split
off; every consumer (`palette()`, `color()`, `color_from_theme()`,
`resolve_palette_ref()`) applies it after the base token resolves.
"""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from dbt_charts.core.compile.migrations.versions.v0_9_0 import (
    ROLE_ALIASES as _ROLE_ALIASES,
)
from dbt_charts.core.compile.resolve.style.palette import (
    UnknownColorError,
    UnknownPaletteError,
    color,
    color_from_theme,
    palette,
    variant,
)
from dbt_charts.core.diagnostics.hints import (
    RETIRED_VARIANT_FAMILIES as _DOOMED_FAMILIES,
    RETIRED_VARIANT_SUFFIX_SUCCESSORS as _RETIRED_SUFFIX_TO_VARIANT,
)


class TestReplacementResolvesAfterCompanionFilesAreDeleted:
    """The eight companion files (vivid-10-dark.yml etc.) are deleted --
    resolving a *retired* spelling now raises UnknownPaletteError/
    UnknownColorError (pinned below), and the *replacement* spelling
    (variant(), computed live) keeps resolving with no file to back it.
    """

    def test_retired_role_bracket_and_family_slot_forms_no_longer_resolve(
        self,
    ) -> None:
        for family in _DOOMED_FAMILIES:
            palettes = {"category": family, "category_dark": f"{family}-dark"}
            with pytest.raises(UnknownColorError):
                color_from_theme("category_dark[1]", palettes=palettes)
            with pytest.raises(UnknownColorError):
                color(f"{family}-dark.1")

    def test_replacement_role_bracket_and_family_slot_forms_still_resolve(
        self,
    ) -> None:
        for family in _DOOMED_FAMILIES:
            palettes = {"category": family}
            for word in _RETIRED_SUFFIX_TO_VARIANT.values():
                assert color_from_theme(f"category[1].{word}", palettes=palettes)
                assert color(f"{family}.1.{word}")


class TestWholeListVariant:
    def test_dark_variant_reproduces_the_move_toward_pole_rule(self) -> None:
        base = palette("vivid-10")
        dark = palette("vivid-10.dark")
        assert dark == [variant(c, "dark") for c in base]

    def test_every_variant_word_resolves(self) -> None:
        for word in ("dark", "light", "pale", "deep"):
            stops = palette(f"vivid-10.{word}")
            assert len(stops) == len(palette("vivid-10"))

    def test_variant_composes_with_leading_steps_shorthand(self) -> None:
        """`vivid-10:4.dark` = first four stops, darkened -- variant splits
        off the end first, then `:N` is parsed on what remains."""
        base = palette("vivid-10", steps=4)
        result = palette("vivid-10:4.dark")
        assert result == [variant(c, "dark") for c in base]

    def test_variant_before_steps_shorthand_is_rejected(self) -> None:
        """`vivid-10.dark:4` -- the variant segment must be the outermost,
        rightmost thing; a variant word followed by `:N` is not recognized
        as a variant at all (nothing at the string's end matches one of the
        four words), so the whole "vivid-10.dark" is treated as a literal
        palette name and fails there. Not the same failure mode the brief
        anticipated ("rejected by the integer parse") -- `_parse_palette_reference`
        parses ":4" as a valid integer steps count without complaint; the
        rejection is `UnknownPaletteError` on the unresolvable base name.
        Documented here as the actual, tested behavior.
        """
        with pytest.raises(UnknownPaletteError, match="vivid-10.dark"):
            palette("vivid-10.dark:4")

    def test_deleted_variant_family_names_still_resolve_bare(self) -> None:
        """A plain family name unaffected by variant splitting (no trailing
        dot-word) resolves exactly as before -- pinned as a stable,
        10-stop result across repeated calls, not a hardcoded hex: tableau
        is a retune of the classic palette, so its own stop values are not
        this test's business to freeze."""
        stops = palette("tableau")
        assert len(stops) == 10
        assert stops == palette("tableau")

    def test_a_continuous_family_rejects_a_variant_with_one_sentence(self) -> None:
        """`variant()` moves lightness toward a pole -- meaningful for a
        categorical family's independent hues, not for a continuous ramp
        whose own stops already encode a monotonic lightness order (and,
        on `surface="table"`, would double-darken the dark-canvas swap
        `palette()` already applies). The grammar's third segment is
        categorical-only; a continuous family raises instead of silently
        resolving."""
        with pytest.raises(
            UnknownPaletteError,
            match=r"unknown palette 'dbt-seq-teal\.dark'\. "
            r"Variants apply to categorical palettes\.",
        ):
            palette("dbt-seq-teal.dark")

    def test_a_sequential_slot_rejects_a_variant_through_color(self) -> None:
        """`palette()`'s categorical-only restriction was not mirrored in
        `color()` -- a single slot off a continuous family split its
        variant same as a categorical one, `color('dbt-seq-blue.3.dark')`
        resolving clean."""
        with pytest.raises(
            UnknownColorError,
            match=r"unknown color token 'dbt-seq-blue\.3\.dark'\. "
            r"Variants apply to categorical palettes\.",
        ):
            color("dbt-seq-blue.3.dark")

    def test_a_diverging_slot_rejects_a_variant_through_color(self) -> None:
        with pytest.raises(
            UnknownColorError,
            match=r"unknown color token 'dbt-div-sunset\.2\.deep'\. "
            r"Variants apply to categorical palettes\.",
        ):
            color("dbt-div-sunset.2.deep")

    def test_a_scaffold_alias_rejects_a_variant_through_color(self) -> None:
        with pytest.raises(
            UnknownColorError,
            match=r"unknown color token 'dbt-grays\.muted\.pale'\. "
            r"Variants apply to categorical palettes\.",
        ):
            color("dbt-grays.muted.pale")


class TestBracketAndDottedTokenVariant:
    def test_direct_name_slot_variant(self) -> None:
        base = color("vivid-10.3")
        assert color("vivid-10.3.pale") == variant(base, "pale")

    def test_role_bracket_variant(self) -> None:
        palettes = {"category": "vivid-10"}
        base = color_from_theme("category[2]", palettes=palettes)
        assert color_from_theme("category[2].dark", palettes=palettes) == variant(
            base, "dark"
        )

    def test_role_dotted_alias_variant(self) -> None:
        palettes = {"category": "vivid-10"}
        base = color_from_theme("category.blue", palettes=palettes)
        assert color_from_theme("category.blue.dark", palettes=palettes) == variant(
            base, "dark"
        )

    def test_bare_role_variant_recurses_through_roles_table(self) -> None:
        palettes = {"category": "vivid-10"}
        roles = {"ink": "category.blue"}
        base = color_from_theme("ink", palettes=palettes, roles=roles)
        assert color_from_theme("ink.dark", palettes=palettes, roles=roles) == variant(
            base, "dark"
        )

    def test_a_variant_already_encoded_in_the_roles_table_applies_once(self) -> None:
        """A theme's `roles:` target may itself carry a variant
        (`roles: {ink: category.blue.dark}`); a board author writing the
        bare role gets that variant, not a double application."""
        palettes = {"category": "vivid-10"}
        roles = {"ink": "category.blue.dark"}
        expected = color_from_theme("category.blue.dark", palettes=palettes)
        assert color_from_theme("ink", palettes=palettes, roles=roles) == expected


class TestReservedVariantWords:
    def test_categorical_palette_alias_named_dark_fails_to_load(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        no_glob_traversable: Callable[[Path], Any],
    ) -> None:
        from dbt_charts.core.compile.resolve.style import palette as palette_mod

        cat_dir = tmp_path / "categorical"
        cat_dir.mkdir()
        (cat_dir / "zz-fake.yml").write_text(
            "name: zz-fake\ncolors: ['#111111', '#222222']\naliases:\n  dark: 1\n"
        )

        monkeypatch.setattr(palette_mod, "_PALETTES_DIR", no_glob_traversable(tmp_path))
        monkeypatch.setattr(palette_mod, "_index", None)
        try:
            with pytest.raises(
                ValueError,
                match="categorical palette 'zz-fake' alias 'dark' collides "
                "with the variant segment; rename the alias.",
            ):
                palette_mod._load_spine_raw("zz-fake")
        finally:
            monkeypatch.setattr(palette_mod, "_index", None)
            palette_mod._spine_cache.pop("zz-fake", None)

    def test_scaffold_alias_named_ink_is_untouched(self) -> None:
        """`dbt-grays.ink` -- a scaffold/tone `<role>.<alias>` -- is
        unaffected: "ink" is not one of the four reserved variant words."""
        assert color("dbt-grays.ink")

    def test_a_variant_split_that_still_fails_reports_the_authored_token(
        self,
    ) -> None:
        """`dbt-grays` has no alias literally named `light`, and splitting
        `dbt-grays.light` on the variant grammar leaves `dbt-grays` -- a
        bare name with no dot at all, which `_color_without_variant`
        rejects for an unrelated reason ("must be dotted"). The error
        reported must name what the author actually wrote
        (`dbt-grays.light`, no such alias), not the post-split base."""
        with pytest.raises(
            UnknownColorError,
            match=r"palette 'dbt-grays' has no alias 'light'",
        ):
            color("dbt-grays.light")

    def test_color_from_theme_variant_split_that_still_fails_reports_the_authored_token(
        self,
    ) -> None:
        """`color_from_theme()`'s identical re-raise: `dbt-grays` is not a
        role bound in `palettes` at all (only `category` is), and splitting
        `dbt-grays.light` on the variant grammar leaves `dbt-grays` --
        which fails the dotted-form dispatch the same unbound-role way.
        The error reported must name the token as authored, not the
        post-split base."""
        with pytest.raises(
            UnknownColorError,
            match=r"theme has no palette assigned to role 'dbt-grays'",
        ):
            color_from_theme("dbt-grays.light", palettes={"category": "vivid-10"})


class TestColorFromThemeCategoricalGuardThreadsThroughRoleIndirection:
    """`color_from_theme()`'s categorical-only guard needs the family of
    whichever concrete palette a token ultimately bottoms out on -- however
    many `roles:` levels of bare-role indirection sit in between, and
    whether or not an intermediate role target carries its own embedded
    variant. Each shape here is a distinct path through
    `_color_from_theme_dispatch`'s bracket/dotted/bare-role recursion;
    deleting the guard (or reverting to a helper that re-walks the token
    string instead of threading the family through as a return value)
    fails at least one of them -- the CRITICAL this class pins was exactly
    a re-walk crashing on the first shape below."""

    def test_a_role_target_with_its_own_variant_still_applies_the_outer_one(
        self,
    ) -> None:
        """`ink: category[1].dark`, token `ink.pale` -- the role target
        already carries a `.dark` segment of its own; splitting the outer
        token's `.pale` and resolving `ink` must reach that variant
        (applied first), then the outer `.pale` applies on top of it. This
        is the exact shape that crashed with `KeyError: 'category[1]'`
        under the string-re-walking helper -- the failure mode this class
        exists to catch."""
        palettes = {"category": "vivid-10"}
        roles = {"ink": "category[1].dark"}
        base = color_from_theme("category[1]", palettes=palettes)
        expected = variant(variant(base, "dark"), "pale")
        assert color_from_theme("ink.pale", palettes=palettes, roles=roles) == expected

    def test_a_role_target_through_a_bare_role_reaching_categorical(self) -> None:
        """`ink: category.blue`, token `ink.deep` -- the role target
        carries no variant of its own; the token's `.deep` is the only
        variant segment, applied after the bare-role recursion resolves
        `ink` -> `category.blue`. Categorical, so this must resolve, not
        raise."""
        palettes = {"category": "vivid-10"}
        roles = {"ink": "category.blue"}
        base = color_from_theme("category.blue", palettes=palettes)
        assert color_from_theme("ink.deep", palettes=palettes, roles=roles) == variant(
            base, "deep"
        )

    def test_a_role_target_through_a_bare_role_reaching_a_continuous_palette(
        self,
    ) -> None:
        """`spine: sequence[3]` (a sequential slot, reached via a second
        role `sequence` bound in `palettes`), token `spine.dark` -- the
        bare-role recursion bottoms out on a sequential palette, not a
        categorical one. Must fail with the same sentence `palette()` and
        `color()` use, not resolve and not crash."""
        palettes = {"sequence": "dbt-seq-blue"}
        roles = {"spine": "sequence[3]"}
        with pytest.raises(
            UnknownColorError,
            match=r"unknown color token 'spine\.dark'\. "
            r"Variants apply to categorical palettes\.",
        ):
            color_from_theme("spine.dark", palettes=palettes, roles=roles)

    def test_a_role_target_through_a_bare_role_reaching_a_scaffold_alias(
        self,
    ) -> None:
        """`muted: chrome.heading` (a scaffold alias, reached via a second
        role `chrome` bound in `palettes`), token `muted.pale` -- same
        rejection as the continuous case, for a scaffold family instead.
        `chrome.ink` (no variant, no bare-role layer) must still resolve
        directly -- the guard only ever gates the variant-split fallback."""
        palettes = {"chrome": "dbt-grays"}
        roles = {"muted": "chrome.heading"}
        with pytest.raises(
            UnknownColorError,
            match=r"unknown color token 'muted\.pale'\. "
            r"Variants apply to categorical palettes\.",
        ):
            color_from_theme("muted.pale", palettes=palettes, roles=roles)
        assert color_from_theme("chrome.ink", palettes=palettes, roles=roles)


class TestUnknownVariantWord:
    def test_an_unrecognized_trailing_word_fails_as_an_unknown_alias_naming_the_four_variants(
        self,
    ) -> None:
        """`category.blue.darker` -- "darker" is not one of the four
        variant words, so `_split_trailing_variant` leaves the token whole;
        it fails downstream as an unknown alias (`partition(".")` on the
        first dot means the lookup key is "blue.darker", not "darker"
        alone) -- the existing unknown-alias error, with the four variant
        words appended as a hint (gated on the failed alias containing a
        dot, which only ever happens when a third segment was appended
        after a real alias)."""
        palettes = {"category": "vivid-10"}
        with pytest.raises(
            UnknownColorError,
            match=r"has no alias 'blue\.darker'.*dark, light, pale, deep",
        ):
            color_from_theme("category.blue.darker", palettes=palettes)


class TestRetiredSpellingSurvivorHint:
    """A retired spelling that survives migration (an old board never
    migrated, or a spelling the migration can't reach -- see
    versions/v0_9_0.py's reach paragraph) fails with the existing
    unknown-role/unknown-palette error, plus a hint naming the
    third-segment replacement."""

    def test_bracket_role_form(self) -> None:
        """The hint respells the *whole* authored token, not the bare role:
        `category.dark` would be an alias lookup that cannot resolve."""
        with pytest.raises(
            UnknownColorError,
            match=r"no palette assigned to role 'category_dark'.*"
            r"'category_dark\[2\]' was retired; use 'category\[2\]\.dark' instead",
        ):
            color_from_theme("category_dark[2]", palettes={"category": "vivid-10"})

    def test_dotted_role_alias_form(self) -> None:
        with pytest.raises(
            UnknownColorError,
            match=r"no palette assigned to role 'category_ghost'.*"
            r"'category_ghost\.blue' was retired; use 'category\.blue\.pale' instead",
        ):
            color_from_theme("category_ghost.blue", palettes={"category": "vivid-10"})

    def test_direct_family_slot_form(self) -> None:
        """`color()` sees only the palette-name segment when it looks the
        family up, but the hint must still respell what the author typed --
        `vivid-10.dark` is the whole-list spelling, wrong in a `color()` slot."""
        with pytest.raises(
            UnknownColorError,
            match=r"unknown palette 'vivid-10-dark'.*"
            r"'vivid-10-dark\.3' was retired; use 'vivid-10\.3\.dark' instead",
        ):
            color("vivid-10-dark.3")

    def test_whole_list_form(self) -> None:
        with pytest.raises(
            UnknownPaletteError,
            match=r"unknown palette 'editorial-10-ink'.*use 'editorial-10\.deep' instead",
        ):
            palette("editorial-10-ink")

    def test_vivid_10_dark_colon_4_rejects_on_the_literal_name_unhinted(self) -> None:
        """`vivid-10.dark:4` -- the variant word precedes the `:N`
        shorthand, so `_split_trailing_variant` finds nothing at the
        string's end to strip; the literal name "vivid-10.dark" is not a
        retired role/family spelling (dot, not hyphen or underscore, before
        "dark"), so this is the ordinary difflib fuzzy-match message, not
        the retirement hint."""
        with pytest.raises(
            UnknownPaletteError,
            match=r"unknown palette 'vivid-10\.dark'\. Did you mean 'vivid-10'\?",
        ):
            palette("vivid-10.dark:4")

    def test_a_live_continuous_fork_typo_gets_a_did_you_mean_not_a_retired_claim(
        self,
    ) -> None:
        """`dbt-seq-teal-dark` is a real, shipped continuous-palette fork --
        never retired, never a companion file. A typo of it must reach the
        ordinary `difflib` suggestion, not the retirement hint: the retired
        family pattern is scoped to `vivid-10`/`editorial-10` only (the two
        families that ever shipped `-dark`/`-light`/`-ghost`/`-ink`
        companion *files*), not "any name ending in one of the four
        suffixes"."""
        with pytest.raises(
            UnknownPaletteError,
            match=r"unknown palette 'dbt-seq-tael-dark'\. "
            r"Did you mean 'dbt-seq-teal-dark'\?",
        ):
            palette("dbt-seq-tael-dark")

    def test_a_live_continuous_fork_family_gets_no_successor_hint(self) -> None:
        """`dbt-seq-blue` never shipped an `-ink` fork -- unlike
        `vivid-10`/`editorial-10`, whose full four-suffix set was real
        companion files. This must not be told it "was retired" either."""
        with pytest.raises(UnknownPaletteError) as exc_info:
            palette("dbt-seq-blue-ink")
        assert "was retired" not in str(exc_info.value)

    def test_a_live_continuous_fork_dark_variant_resolves_clean(self) -> None:
        """The correctly-spelled, real `dbt-seq-blue-dark` fork must resolve
        with no error and no retirement hint at all -- the case the
        unscoped family pattern used to misfire on."""
        assert len(palette("dbt-seq-blue-dark")) == len(palette("dbt-seq-blue"))

    def test_whole_list_shorthand_form_keeps_its_shorthand_in_the_hint(self) -> None:
        """`palette()` strips the `:N`/`_r` shorthand before the catalog
        lookup, so the name it fails on is `vivid-10-dark` -- but the hint
        must respell what the author typed, shorthand included, or it names
        a whole list four stops longer than the one they asked for."""
        with pytest.raises(
            UnknownPaletteError,
            match=r"unknown palette 'vivid-10-dark'.*"
            r"'vivid-10-dark:4' was retired; use 'vivid-10:4\.dark' instead",
        ):
            palette("vivid-10-dark:4")


class TestEveryHintedReplacementResolvesOnTheShippedThemes:
    """A hint that names a token which does not resolve is worse than no
    hint. For every retired spelling the old grammar accepted on a shipped
    theme, the replacement `respell_retired_variant_token` names -- the
    same string the migration writes -- must resolve on that theme through
    the same resolver the retired form would have used.
    """

    @staticmethod
    def _themes() -> list[str]:
        from dbt_charts.core.compile.config import list_built_in_themes

        return [
            t for t in list_built_in_themes() if not t.startswith(("_", "diagnostics-"))
        ]

    @pytest.mark.parametrize("theme", _themes.__func__())
    def test_role_forms_resolve_through_color_from_theme(self, theme: str) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

        style = get_theme_style(theme)
        assert style.palettes is not None
        palettes, roles = style.palettes, style.roles
        stop_count = len(palette(palettes["category"]))
        for suffix in _RETIRED_SUFFIX_TO_VARIANT:
            for slot in range(1, stop_count + 1):
                hinted = respell_retired_variant_token(f"category_{suffix}[{slot}]")
                assert hinted is not None
                assert color_from_theme(hinted, palettes=palettes, roles=roles)
            # The alias set lives on the palette, not on the retired
            # companion: every alias the migration enumerates was addressable
            # as `category_<suffix>.<alias>` under the old grammar, so its
            # replacement must resolve on every shipped theme -- a miss here
            # is a real gap, never one to skip past.
            for alias in _ROLE_ALIASES:
                hinted = respell_retired_variant_token(f"category_{suffix}.{alias}")
                assert hinted is not None
                assert color_from_theme(hinted, palettes=palettes, roles=roles)

    @pytest.mark.parametrize("theme", _themes.__func__())
    def test_bare_role_form_resolves_as_a_whole_list_reference(
        self, theme: str
    ) -> None:
        from dbt_charts.core.compile.config import get_theme_style
        from dbt_charts.core.compile.resolve.style.palette import resolve_palette_ref
        from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

        style = get_theme_style(theme)
        assert style.palettes is not None
        for suffix in _RETIRED_SUFFIX_TO_VARIANT:
            hinted = respell_retired_variant_token(f"category_{suffix}")
            assert hinted is not None
            assert palette(resolve_palette_ref(hinted, style.palettes))

    def test_family_forms_resolve_theme_independently(self) -> None:
        from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

        for family in _DOOMED_FAMILIES:
            for suffix in _RETIRED_SUFFIX_TO_VARIANT:
                hinted = respell_retired_variant_token(f"{family}-{suffix}")
                assert hinted is not None
                stops = palette(hinted)
                for slot in range(1, len(stops) + 1):
                    hinted_slot = respell_retired_variant_token(
                        f"{family}-{suffix}.{slot}"
                    )
                    assert hinted_slot is not None
                    assert color(hinted_slot)


class TestRoleBoundToANameVariantPalette:
    """`style.palettes: {category: vivid-10.deep}` validates (the enum lists
    every `<categorical>.<variant>` spelling), so it must also *bind*: a
    single-color token through that role resolves the base palette and
    applies the variant to the stop, exactly as `resolve_palette_ref` does
    for a whole list. Before, `_color_from_theme_dispatch` looked the bound
    value up in the catalog verbatim and raised "not found in catalog" --
    ERR-INTERNAL at render for every `category[N]`/`category.<alias>` on a
    board that authored the binding.
    """

    _PALETTES = {"category": "vivid-10.deep"}

    def test_bracket_form_applies_the_bound_variant_to_the_stop(self) -> None:
        assert color_from_theme("category[2]", palettes=self._PALETTES) == variant(
            palette("vivid-10")[1], "deep"
        )

    def test_dotted_alias_form_applies_the_bound_variant_to_the_alias(self) -> None:
        plain = color_from_theme("category.blue", palettes={"category": "vivid-10"})
        assert color_from_theme("category.blue", palettes=self._PALETTES) == variant(
            plain, "deep"
        )

    def test_a_token_variant_composes_on_top_of_the_bound_one(self) -> None:
        """`category[2].dark` under a `.deep` binding: the binding's variant
        applies first (it is part of resolving the role), the token's own
        second -- `variant()` is plain color math and composes, so this
        neither errors nor drops either move. Pinned so a future change to
        either order is deliberate."""
        deep = variant(palette("vivid-10")[1], "deep")
        assert color_from_theme("category[2].dark", palettes=self._PALETTES) == variant(
            deep, "dark"
        )

    @pytest.mark.parametrize(
        ("ref", "substituted", "steps", "reverse"),
        [
            ("category:4", "vivid-10:4.deep", 4, False),
            ("category_r", "vivid-10_r.deep", None, True),
        ],
    )
    def test_whole_list_shorthand_composes_with_the_bound_variant(
        self, ref: str, substituted: str, steps: int | None, reverse: bool
    ) -> None:
        """`resolve_palette_ref` is the whole-list counterpart of
        `_bound_palette`: the bound value's own variant is split off before
        the ref's shorthand is re-attached, so the result is a spelling
        `palette()` parses (variant last) -- not `vivid-10.deep:4`, an
        unknown name."""
        from dbt_charts.core.compile.resolve.style.palette import resolve_palette_ref

        assert resolve_palette_ref(ref, self._PALETTES) == substituted
        expected = [
            variant(stop, "deep")
            for stop in palette("vivid-10", steps=steps, reverse=reverse)
        ]
        assert palette(substituted) == expected

    def test_a_whole_list_variant_composes_on_top_of_the_bound_one(self) -> None:
        """`category.pale` on a `.deep` binding is plain color math, like
        `category[2].dark` is on the token path: the binding's variant
        applies first, the ref's second."""
        from dbt_charts.core.compile.resolve.style.palette import resolve_palette_ref

        substituted = resolve_palette_ref("category.pale", self._PALETTES)
        assert substituted == "vivid-10.deep.pale"
        assert palette(substituted) == [
            variant(variant(stop, "deep"), "pale") for stop in palette("vivid-10")
        ]


class TestWholeListVariantThroughFullCompile:
    """`style.charts.color.categorical.palette` carrying a trailing variant,
    exercised through the full compile pipeline (`compile()`), not just the
    resolver functions directly -- the same "unit tests pass, the pipeline
    doesn't" gap the `_COLOR_TOKEN_PATTERN` fix (`core/colors.py`) was
    caught by.

    A literal family name + variant (`palette: tableau.dark`) compiles
    clean -- see `test_literal_family_name_plus_variant_compiles`.

    A *role* + variant board-patch override (`palette: category.dark`)
    does not, but not because of the variant grammar: the identical
    role-only form with no variant at all (`palette: category`) already
    fails to compile with the same "not a role the active theme binds"
    diagnostic -- reproduced by dropping the variant segment. That is
    the documented contract
    (`test_palette_roles.py::test_board_may_not_name_a_role`:
    "Board-level `style:` cannot bind `category` -- roles are
    theme-scoped"), not a gap this PR found or is scoped to fix.

    What this PR's own `_walk` fix (tokens.py) did change for the role
    form: before the fix, `is_color_token` matched the 2-segment dotted
    shape (indistinguishable, by shape alone, from a single-color
    `role.alias` token like `category.blue` -- extending
    `_COLOR_TOKEN_PATTERN` for the variant grammar made `role.<variant-word>`
    match too), so `_resolve_tokens_on_patch`'s generic walk dispatched it
    to `color_from_theme` -- wrong for a `palette`/`single_series_palette`
    field, which `expand_palette_refs` handles separately, later, once
    `style.palettes` is final -- and crashed as an opaque `ERR-INTERNAL`
    once `color_from_theme`'s own "try original, fall back to variant-split"
    retry failed a second time on the bare split-off role name. After the
    fix, the same input reaches `expand_palette_refs` and fails as a
    proper `ERR-PALETTE-UNKNOWN` diagnostic instead -- see
    `test_role_plus_variant_whole_list_palette_field_is_a_clean_diagnostic_not_a_crash`.
    """

    def test_literal_family_name_plus_variant_compiles(self) -> None:
        from dbt_charts.core.compile.compiler import compile as compile_board

        board = textwrap.dedent(
            """\
            title: Test Board
            queries:
              q1:
                type: values
                rows:
                  - {x: 1, y: 2}
            style:
              charts:
                color:
                  categorical:
                    palette: tableau.dark
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - c1
            """
        )
        result = compile_board(board)
        assert result.success, result.errors
        chart_defaults = result.board.resolved_style.chart_defaults
        assert chart_defaults.palette == [
            variant(c, "dark") for c in palette("tableau")
        ]

    @pytest.mark.parametrize("field", ["palette", "single_series_palette"])
    def test_shorthand_steps_plus_variant_compiles(self, field: str) -> None:
        """`vivid-10:4.dark` -- shorthand steps *and* a trailing variant,
        composed -- must resolve through the board-level whole-list field,
        not just through `palette()` called directly.

        `resolve_palette_alias` (the whole-list resolver `tokens.py`'s
        `_resolve_color_tokens` walk calls) used to parse the `:N` shorthand
        before splitting off the variant, so `_parse_palette_reference` saw
        `"4.dark"` as the steps segment and raised -- a path `palette()`
        itself always handled correctly, since it splits in the opposite
        order. Regression for that ordering bug, at board-patch scope.
        """
        from dbt_charts.core.compile.compiler import compile as compile_board

        board = textwrap.dedent(
            f"""\
            title: Test Board
            queries:
              q1:
                type: values
                rows:
                  - {{x: 1, y: 2}}
            style:
              charts:
                color:
                  categorical:
                    {field}: vivid-10:4.dark
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - c1
            """
        )
        result = compile_board(board)
        assert result.success, result.errors
        chart_defaults = result.board.resolved_style.chart_defaults
        resolved = getattr(chart_defaults, field)
        assert resolved == [variant(c, "dark") for c in palette("vivid-10", steps=4)]

    def test_role_plus_variant_whole_list_palette_field_is_a_clean_diagnostic_not_a_crash(
        self,
    ) -> None:
        from dbt_charts.core.compile.compiler import compile as compile_board
        from dbt_charts.core.diagnostics.codes_compile import ERR_PALETTE_UNKNOWN

        board = textwrap.dedent(
            """\
            title: Test Board
            theme: clarity
            queries:
              q1:
                type: values
                rows:
                  - {x: 1, y: 2}
            style:
              charts:
                color:
                  categorical:
                    palette: category.dark
            charts:
              c1:
                query: q1
                type: bar
                x: x
                y: y
            rows:
              - c1
            """
        )
        result = compile_board(board)
        assert not result.success
        assert result.errors[0].code == ERR_PALETTE_UNKNOWN.code

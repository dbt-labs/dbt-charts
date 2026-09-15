"""Tests for the fonts module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from mdsvg.fonts import (
    FontMeasurer,
    _cached_measurer,
    _emoji_cluster_length,
    create_precise_wrapper,
    get_system_font,
    split_token_precise,
    wrap_text_precise,
)


class TestFontMeasurer:
    """Test FontMeasurer class."""

    def test_system_font_found(self) -> None:
        """Test that a system font can be found."""
        font_path = get_system_font()
        # May be None on some systems, but shouldn't error
        assert font_path is None or isinstance(font_path, str)

    def test_measure_empty_string(self, measurer: FontMeasurer) -> None:
        """Test measuring empty string returns 0."""
        assert measurer.measure("", 14) == 0.0

    def test_measure_single_char(self, measurer: FontMeasurer) -> None:
        """Test measuring single character."""
        width = measurer.measure("a", 14)
        assert width > 0

    def test_measure_scales_with_font_size(self, measurer: FontMeasurer) -> None:
        """Test that width scales linearly with font size."""
        w14 = measurer.measure("Hello", 14)
        w28 = measurer.measure("Hello", 28)
        assert pytest.approx(w28, rel=0.01) == w14 * 2

    def test_longer_text_wider(self, measurer: FontMeasurer) -> None:
        """Test that longer text is wider."""
        short = measurer.measure("Hi", 14)
        long = measurer.measure("Hello World", 14)
        assert long > short

    def test_narrow_chars_narrower(self, measurer: FontMeasurer) -> None:
        """Test that narrow characters (i, l) are narrower than wide (W, M)."""
        narrow = measurer.measure("iiii", 14)
        wide = measurer.measure("WWWW", 14)
        assert wide > narrow

    def test_system_default(self) -> None:
        """Test FontMeasurer.system_default()."""
        measurer = FontMeasurer.system_default()
        # May be None on some systems
        if measurer is not None:
            assert measurer.is_available

    def test_has_glyph_true_for_an_ordinary_ascii_letter(
        self, measurer: FontMeasurer
    ) -> None:
        assert measurer.has_glyph("A") is True

    def test_has_glyph_false_for_a_private_use_codepoint(
        self, measurer: FontMeasurer
    ) -> None:
        """A Private Use Area codepoint has no assigned glyph in any real
        font — the deterministic "definitely absent" case, unlike guessing
        at a codepoint that happens to be missing from one specific font.
        """
        assert measurer.has_glyph("\U0010fffd") is False

    def test_has_glyph_false_for_empty_string(self, measurer: FontMeasurer) -> None:
        assert measurer.has_glyph("") is False

    def test_has_glyph_does_not_use_measures_fallback_width(
        self, measurer: FontMeasurer
    ) -> None:
        """``measure()`` degrades an unmapped codepoint to a placeholder
        width rather than raising (the right behavior for prose) -- confirm
        ``has_glyph`` sees through that and reports the codepoint as truly
        absent rather than "measurable"."""
        missing = "\U0010fffd"
        assert measurer.measure(missing, 14.0) > 0.0  # the silent fallback
        assert measurer.has_glyph(missing) is False  # the honest answer

    def test_measure_unmapped_cjk_ideograph_gets_a_full_width(
        self, measurer: FontMeasurer
    ) -> None:
        """An unmapped CJK ideograph is East Asian Wide -- it should book a
        full em-square advance, not the placeholder (space-like) fallback an
        ordinary unmapped narrow codepoint gets."""
        ideograph = "中"
        if measurer.has_glyph(ideograph):
            pytest.skip("this font has a real glyph for the ideograph under test")
        assert measurer.measure(ideograph, 16.0) == pytest.approx(16.0)

    def test_measure_unmapped_combining_kana_mark_books_zero_width(
        self, measurer: FontMeasurer
    ) -> None:
        """The combining voiced-sound mark (U+3099) is East Asian Wide, but
        it stacks onto the preceding kana with zero visual advance -- it
        must book nothing at all, not even the ordinary placeholder
        fallback, so it never widens a line or gets orphaned onto the next
        one by the wrapper's character-break fallback."""
        mark = "゙"
        if measurer.has_glyph(mark):
            pytest.skip("this font has a real glyph for the mark under test")
        assert measurer.measure(mark, 16.0) == 0.0

    def test_measure_nfd_kana_syllable_matches_its_nfc_form(
        self, measurer: FontMeasurer
    ) -> None:
        """An NFD-normalized voiced kana syllable (base + combining mark)
        must measure identically to its NFC (precomposed) form -- only the
        base character is East Asian Wide; the mark books zero."""
        base = "か"  # か
        mark = "゙"  # combining voiced sound mark
        precomposed = "が"  # が, NFC form of base + mark
        if measurer.has_glyph(base):
            pytest.skip("this font has a real glyph for the kana under test")
        assert measurer.measure(base + mark, 16.0) == measurer.measure(
            precomposed, 16.0
        )


class TestPreciseWrapper:
    """Test precise text wrapping."""

    def test_short_text_single_line(self, measurer: FontMeasurer) -> None:
        """Test short text stays on one line."""
        wrap = create_precise_wrapper(500, 14, measurer)
        lines = wrap("Hello")
        assert len(lines) == 1
        assert lines[0] == "Hello"

    def test_long_text_wraps(self, measurer: FontMeasurer) -> None:
        """Test long text wraps to multiple lines."""
        wrap = create_precise_wrapper(100, 14, measurer)
        lines = wrap("This is a long sentence that should wrap")
        assert len(lines) > 1

    def test_long_cjk_text_wraps_without_spaces(self, measurer: FontMeasurer) -> None:
        """CJK text has no spaces between words, so it arrives as one
        unbreakable token; it must still wrap at character boundaries the
        way a browser does by default for these scripts, instead of
        measuring artificially narrow and fitting on one clipped line."""
        ideograph = "中"
        if measurer.has_glyph(ideograph):
            pytest.skip("this font has real CJK glyphs; the repro needs unmapped ones")
        max_width = 800.0
        wrap = create_precise_wrapper(max_width, 16, measurer)
        text = "中文" * 50
        lines = wrap(text)
        assert len(lines) > 1
        for line in lines:
            assert measurer.measure(line, 16) <= max_width

    def test_empty_text(self, measurer: FontMeasurer) -> None:
        """Test empty text returns empty line."""
        wrap = create_precise_wrapper(100, 14, measurer)
        lines = wrap("")
        assert lines == [""]

    def test_lines_fit_width(self, measurer: FontMeasurer) -> None:
        """Test that wrapped lines fit within max width."""
        max_width = 200
        wrap = create_precise_wrapper(max_width, 14, measurer)
        text = "This is a test sentence that should wrap properly within the width"
        lines = wrap(text)

        for line in lines:
            width = measurer.measure(line, 14)
            # Allow small overflow for single words longer than max_width
            assert width <= max_width or len(line.split()) == 1


class TestFontMeasurerWeight:
    """FontMeasurer(weight=...) instances a variable font at that weight."""

    def test_weight_produces_different_advance_widths(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        """Instancing the same variable font at two weights must measure differently."""
        font_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        sample = "The quick brown fox jumps over the lazy dog"
        light = FontMeasurer(font_path, weight=400)
        heavy = FontMeasurer(font_path, weight=900)
        assert light.is_available and heavy.is_available
        assert heavy.measure(sample, 14) > light.measure(sample, 14)

    def test_weight_on_static_font_raises(self, dbt_charts_fonts_dir: Path) -> None:
        """Requesting a weight on a font with no 'fvar' table is a caller error."""
        font_path = str(dbt_charts_fonts_dir / "SourceCodePro-Regular.ttf")
        with pytest.raises(ValueError, match="fvar"):
            FontMeasurer(font_path, weight=700)

    @pytest.mark.parametrize(
        "font_file", ["InterVariable.ttf", "SourceSerif4-Italic.ttf"]
    )
    @pytest.mark.parametrize("weight", [400, 500, 600, 700])
    def test_hvar_advances_match_full_instancing(
        self, dbt_charts_fonts_dir: Path, tmp_path: Path, font_file: str, weight: int
    ) -> None:
        """Reading HVAR deltas agrees with rebuilding the whole font at that weight.

        HVAR is the fast path (~200x quicker than ``instantiateVariableFont``, which
        rebuilds glyf/gvar/GPOS/avar to produce the same advances). This pins it
        against that reference so a mistake in axis normalization — forgetting
        ``avar`` is the easy one, and it silently misplaces every non-default
        weight — cannot pass unnoticed.

        The reference is measured by writing a genuinely instanced font to disk and
        reading it back through the same code path, so the only difference under test
        is how the advances were obtained. The tolerance covers the instancer rounding
        advances to whole font units while HVAR keeps the unrounded interpolation.
        """
        from fontTools.ttLib import (  # pyright: ignore[reportMissingTypeStubs]
            TTFont,
        )
        from fontTools.varLib.instancer import (  # pyright: ignore[reportMissingTypeStubs]
            instantiateVariableFont,
        )

        sample = "The quick brown fox jumps over the lazy dog 0123456789"
        font_path = str(dbt_charts_fonts_dir / font_file)

        instanced = instantiateVariableFont(
            TTFont(font_path), {"wght": weight}, inplace=False
        )
        reference_path = tmp_path / f"instanced-{weight}.ttf"
        instanced.save(str(reference_path))

        measured = FontMeasurer(font_path, weight=weight).measure(sample, 14.0)
        reference = FontMeasurer(str(reference_path)).measure(sample, 14.0)

        assert measured == pytest.approx(reference, abs=0.15)


class TestCachedMeasurerWeight:
    """_cached_measurer's LRU key must include weight, not just path."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        _cached_measurer.cache_clear()
        yield
        _cached_measurer.cache_clear()

    def test_different_weights_return_different_objects(
        self, dbt_charts_fonts_dir: Path
    ) -> None:
        font_path = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        light = _cached_measurer(font_path, 0, 400)
        heavy = _cached_measurer(font_path, 0, 900)
        assert light is not heavy
        assert light.measure("Hello", 14) != heavy.measure("Hello", 14)


class TestNoFallback:
    """Precise wrapping must fail loudly when measurement is unavailable."""

    def test_wrapper_raises_without_measurer(self) -> None:
        measurer = FontMeasurer("/definitely/missing/font.ttf")
        with pytest.raises(RuntimeError, match="FontMeasurer"):
            create_precise_wrapper(200, 14, measurer=measurer)


class TestGoogleFonts:
    """Test Google Fonts download functionality."""

    def test_get_font_cache_dir(self) -> None:
        """Test cache directory is created."""
        import os

        from mdsvg.fonts import get_font_cache_dir

        cache_dir = get_font_cache_dir()
        assert isinstance(cache_dir, str)
        assert os.path.isdir(cache_dir)

    def test_list_cached_fonts(self) -> None:
        """Test listing cached fonts."""
        from mdsvg.fonts import list_cached_fonts

        fonts = list_cached_fonts()
        assert isinstance(fonts, list)

    @pytest.mark.network
    def test_download_google_font(self, tmp_path: Path) -> None:
        """Test downloading a font from Google Fonts."""
        from mdsvg.fonts import download_google_font

        # Use tmp_path to avoid polluting cache
        font_path = download_google_font("Roboto", cache_dir=str(tmp_path))
        assert os.path.exists(font_path)
        assert font_path.endswith(".ttf")

        # Test the downloaded font works (skips when fonttools is not installed)
        measurer = FontMeasurer(font_path)
        if not measurer.is_available:
            pytest.skip("FontMeasurer not available (fonttools not installed)")
        width = measurer.measure("Test", 14)
        assert width > 0

    @pytest.mark.network
    def test_download_caches_font(self, tmp_path: Path) -> None:
        """Test that downloading caches the font."""
        import os

        from mdsvg.fonts import download_google_font

        # First download
        path1 = download_google_font("Lato", cache_dir=str(tmp_path))
        mtime1 = os.path.getmtime(path1)

        # Second download should return cached
        path2 = download_google_font("Lato", cache_dir=str(tmp_path))
        mtime2 = os.path.getmtime(path2)

        assert path1 == path2
        assert mtime1 == mtime2  # File wasn't re-downloaded

    @pytest.mark.network
    def test_download_invalid_font_raises(self, tmp_path: Path) -> None:
        """Test that invalid font name raises error."""
        from mdsvg.fonts import download_google_font

        with pytest.raises(RuntimeError, match="not found"):
            download_google_font("NotARealFontName12345", cache_dir=str(tmp_path))


class TestSplitTokenPreciseSeamBreaks:
    """split_token_precise must prefer natural seam characters over mid-char breaks."""

    def _measure_fn(self, measurer: FontMeasurer, font_size: float = 14.0):
        def measure(text: str) -> float:
            return float(measurer.measure(text, font_size))

        return measure

    def test_split_token_precise_prefers_seam_breaks(
        self, measurer: FontMeasurer
    ) -> None:
        """Long snake_case token must break at underscore, not mid-character."""
        token = "top_5_destinations_by_of_qualified_customers"
        measure = self._measure_fn(measurer)
        # Use a width that forces a break somewhere in the token
        full_width = measure(token)
        max_width = full_width * 0.7  # force at least one split

        pieces = split_token_precise(token, max_width, measure)

        # Every piece except the last must end at a seam character
        seam_chars = set("_/.-?&=:")
        for piece in pieces[:-1]:
            assert piece[-1] in seam_chars, (
                f"Expected break at seam char but got piece ending with {piece[-1]!r}: {piece!r}"
            )

    def test_split_token_precise_url_seam_breaks(self, measurer: FontMeasurer) -> None:
        """Long URL must break at / and - rather than mid-character."""
        token = "https://docs.example.com/guides/error-reference/column-not-found"
        measure = self._measure_fn(measurer)
        full_width = measure(token)
        max_width = full_width * 0.55  # force multiple splits

        pieces = split_token_precise(token, max_width, measure)

        assert len(pieces) >= 2
        seam_chars = set("_/.-?&=:")
        for piece in pieces[:-1]:
            assert piece[-1] in seam_chars, (
                f"URL piece should end at seam char, got ending {piece[-1]!r}: {piece!r}"
            )

    def test_wrap_text_precise_snake_case_breaks_at_seam(
        self, measurer: FontMeasurer
    ) -> None:
        """wrap_text_precise must produce lines where long snake_case tokens break at underscores."""
        # Use a narrow budget that forces the long token to break
        token = "top_5_destinations_by_of_qualified_customers"
        measure_fn = self._measure_fn(measurer)
        full_width = measure_fn(token)
        max_width = full_width * 0.7

        lines, _ = wrap_text_precise(token, max_width, 14.0, measurer)

        # Reassembled text must equal original (no characters lost)
        reassembled = "".join(lines)
        assert reassembled == token, (
            f"Characters lost in wrap: {reassembled!r} != {token!r}"
        )

        # Each line must fit within max_width
        for line in lines:
            assert measure_fn(line) <= max_width, (
                f"Line exceeds max_width: {line!r} width={measure_fn(line):.1f} > {max_width:.1f}"
            )

    def test_authored_trailing_ellipsis_does_not_signal_truncation(
        self, measurer: FontMeasurer
    ) -> None:
        """A title that already ends with '…' but fits is NOT a truncation signal.

        Pins the regression where lines[-1].endswith('…') incorrectly fired for
        authored ellipsis text that was never actually cut by the wrapper.
        """
        text = "Revenue…"
        lines, truncated = wrap_text_precise(
            text, 1000.0, 14.0, measurer, max_lines=2, ellipsis=True
        )
        assert not truncated, "authored trailing ellipsis must not set truncation flag"
        assert any("Revenue" in line for line in lines)

    def test_genuine_truncation_sets_flag(self, measurer: FontMeasurer) -> None:
        """Text exceeding two lines at a narrow budget → truncated=True."""
        long_text = " ".join(["connections"] * 20)
        lines, truncated = wrap_text_precise(
            long_text, 40.0, 14.0, measurer, max_lines=2, ellipsis=True
        )
        assert truncated
        assert len(lines) <= 2
        assert lines[-1].endswith("…")


class TestEmojiMeasurement:
    """Emoji clusters must book one emoji-glyph advance, not a quarter-em per codepoint.

    Every assertion isolates the width contributed by the emoji cluster by
    diffing against the same string with the cluster removed -- additive
    since ``measure()`` has no kerning -- and states the expectation as
    ``1.27 * font_size`` rather than a raw number, so it doesn't depend on
    which system font's ``units_per_em`` happens to be present.
    """

    def _cluster_width(
        self, measurer: FontMeasurer, text: str, cluster: str, font_size: float
    ) -> float:
        without = text.replace(cluster, "")
        return measurer.measure(text, font_size) - measurer.measure(without, font_size)

    def test_prose_with_one_emoji_measures_at_emoji_width(
        self, measurer: FontMeasurer
    ) -> None:
        font_size = 14.0
        cluster = "✅"  # checkmark emoji: default-emoji-presentation pictograph
        width = self._cluster_width(
            measurer, f"Status: {cluster} done", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_colored_status_dot_measures_at_emoji_width(
        self, measurer: FontMeasurer
    ) -> None:
        """Pins the `(0x1F7E0, 0x1F7EB)` pictograph range: without it, a
        legend written "\U0001f7e2 up / \U0001f534 down" would measure the green dot at
        a quarter-em (\U0001f534 is in 1F300-1F5FF and was already covered) while its
        paired red dot measured correctly -- a silent 5x mismatch between two
        glyphs painted identically."""
        font_size = 14.0
        cluster = "\U0001f7e2"  # large green circle: colored status-dot pictograph
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_zwj_sequence_measures_as_one_emoji_unit(
        self, measurer: FontMeasurer
    ) -> None:
        font_size = 14.0
        cluster = "\U0001f468\u200d\U0001f4bb"  # man technologist (3 codepoints)
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_flag_measures_as_one_emoji_unit(self, measurer: FontMeasurer) -> None:
        font_size = 14.0
        cluster = "\U0001f1fa\U0001f1f8"  # regional indicators U + S (flag: US)
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_keycap_measures_as_one_emoji_unit(self, measurer: FontMeasurer) -> None:
        font_size = 14.0
        cluster = "1️⃣"  # keycap digit one (3 codepoints)
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_non_emoji_unknown_codepoint_keeps_quarter_em_fallback(
        self, measurer: FontMeasurer
    ) -> None:
        """Regression guard: a PUA codepoint is not an emoji cluster and must
        keep exactly today's quarter-em placeholder width, unchanged."""
        font_size = 14.0
        missing = "\U0010fffd"
        assert measurer.measure(missing, font_size) == pytest.approx(0.25 * font_size)

    def test_pictograph_with_redundant_vs16_measures_as_one_emoji_unit(
        self, measurer: FontMeasurer
    ) -> None:
        """A pictograph's own trailing VS16 must be absorbed, not orphaned --
        otherwise a ZWJ sequence starting with one (see the flag test below)
        can never fold."""
        font_size = 14.0
        cluster = "🌧️"  # cloud with rain: pictograph (1F327) + redundant VS16
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_zwj_flag_starting_with_a_vs16_pictograph_folds_to_one_unit(
        self, measurer: FontMeasurer
    ) -> None:
        """Regression for the fold that broke when a pictograph's own
        trailing VS16 was orphaned: the rainbow flag's first codepoint (waving
        white flag) is a pictograph authored with a canonical trailing VS16,
        so the whole 4-codepoint ZWJ sequence must still measure as one unit,
        not one unit plus two orphaned quarter-ems plus a second unit."""
        font_size = 14.0
        cluster = "\U0001f3f3️‍\U0001f308"  # 🏳️‍🌈 rainbow flag
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

    def test_vs15_suppresses_emoji_booking_instead_of_extending_it(
        self, measurer: FontMeasurer
    ) -> None:
        """U+FE0E (VS15) explicitly requests text presentation. A pictograph
        followed by VS15 must not book the (larger) emoji-advance unit -- the
        opposite of what VS16 does."""
        font_size = 14.0
        cluster = "✅︎"  # ✅︎ white heavy check mark, text-presentation
        assert _emoji_cluster_length(cluster, 0) == 0
        width = self._cluster_width(
            measurer, f"before {cluster} after", cluster, font_size
        )
        # The checkmark itself is East Asian Wide (books >= one em); VS15's
        # own contribution is font-dependent (a zero-width glyph in some
        # fonts, the generic placeholder in others) -- the invariant under
        # test is only that the pair never reaches the full emoji advance.
        assert font_size <= width < 1.27 * font_size

    def test_bare_warning_sign_without_vs16_is_not_an_emoji_cluster(self) -> None:
        """Pins the "no golden churn" claim: a bare U+26A0 (no VS16) is a
        text-presentation-by-default symbol and must not be classified as an
        emoji cluster start."""
        assert _emoji_cluster_length("⚠", 0) == 0

    def test_keycap_base_alone_is_not_a_cluster(self) -> None:
        """A bare '#' with no VS16+U+20E3 following it is ordinary text, not
        a keycap -- the only sub-U+2000 shape the classifier recognizes at
        all, so its negative case needs its own direct assertion."""
        assert _emoji_cluster_length("#", 0) == 0

    def test_skin_tone_modifier_folds_into_its_base(
        self, measurer: FontMeasurer
    ) -> None:
        """A skin-tone modifier (U+1F3FB-U+1F3FF) is itself inside the
        pictograph range, so without folding it into the preceding base it
        would be classified as its own separate emoji cluster -- silently
        doubling the width of every skin-toned glyph, and breaking the ZWJ
        fold for a skin-toned person-plus-ZWJ sequence."""
        font_size = 14.0
        thumbs_up_medium = "\U0001f44d\U0001f3fd"  # 👍🏽 thumbs up + medium skin tone
        width = self._cluster_width(
            measurer, f"before {thumbs_up_medium} after", thumbs_up_medium, font_size
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)

        woman_technologist = "\U0001f469\U0001f3fd‍\U0001f4bb"  # 👩🏽‍💻
        width = self._cluster_width(
            measurer,
            f"before {woman_technologist} after",
            woman_technologist,
            font_size,
        )
        assert width == pytest.approx(1.27 * font_size, rel=1e-6)


class TestCachedMeasurer:
    """_cached_measurer must return the same object for the same path."""

    @pytest.fixture(autouse=True)
    def clear_cache(self):
        """Clear the lru_cache before and after each test."""
        _cached_measurer.cache_clear()
        yield
        _cached_measurer.cache_clear()

    def test_same_path_returns_same_object(self) -> None:
        """Calling _cached_measurer twice with the same path returns the identical object."""
        font_path = get_system_font()
        if not font_path:
            pytest.skip("No system font available")
        m1 = _cached_measurer(font_path)
        m2 = _cached_measurer(font_path)
        assert m1 is m2

    def test_ttfont_opened_once_per_path(self) -> None:
        """TTFont is constructed only once when the same path is requested multiple times."""
        font_path = get_system_font()
        if not font_path:
            pytest.skip("No system font available")

        ttfont_call_count = 0
        original_init_font = FontMeasurer._init_font

        def counting_init_font(self_inner: FontMeasurer) -> None:
            nonlocal ttfont_call_count
            ttfont_call_count += 1
            original_init_font(self_inner)

        with patch.object(FontMeasurer, "_init_font", counting_init_font):
            _cached_measurer.cache_clear()
            _cached_measurer(font_path)
            _cached_measurer(font_path)
            _cached_measurer(font_path)

        assert ttfont_call_count == 1, (
            f"Expected 1 TTFont construction, got {ttfont_call_count}"
        )


class TestUsedFaces:
    """`SVGRenderer.used_faces` — which supplied font files a render actually reached.

    A caller that ships the fonts with the SVG cannot know this in advance: italic
    is reached through emphasis inside the text, not through the style.
    """

    @pytest.fixture(autouse=True)
    def _fonts(self, dbt_charts_fonts_dir: Path) -> None:
        from mdsvg.fonts import FontFace, FontFaces

        roman = str(dbt_charts_fonts_dir / "InterVariable.ttf")
        italic = str(dbt_charts_fonts_dir / "InterVariable-Italic.ttf")
        mono = str(dbt_charts_fonts_dir / "SourceCodePro-Regular.ttf")
        # All five slots. `bold_italic` is not decoration: a caller packaging fonts
        # reads it to decide whether it needs the italic file at all, so a renderer
        # that reported `bold` for a bold-italic run would drop that file.
        self.faces = FontFaces(
            regular=FontFace(path=roman),
            bold=FontFace(path=roman, weight=700),
            italic=FontFace(path=italic),
            bold_italic=FontFace(path=italic, weight=700),
            mono=FontFace(path=mono),
        )

    def _faces(self):
        return self.faces

    def _render(self, markdown: str) -> frozenset[str]:
        from mdsvg import parse
        from mdsvg.renderer import SVGRenderer

        renderer = SVGRenderer(fonts=self._faces())
        renderer.render(parse(markdown), width=300, padding=0.0)
        return renderer.used_faces

    def test_empty_before_rendering(self) -> None:
        from mdsvg.renderer import SVGRenderer

        assert SVGRenderer(fonts=self._faces()).used_faces == frozenset()

    def test_plain_text_reaches_only_regular(self) -> None:
        assert self._render("Plain words") == {"regular"}

    def test_emphasis_reaches_italic(self) -> None:
        assert "italic" in self._render("Some *emphasis* here")

    def test_code_reaches_mono(self) -> None:
        assert "mono" in self._render("Some `code` here")

    def test_strong_reaches_bold(self) -> None:
        assert "bold" in self._render("Some **strong** here")

    def test_bold_italic_reaches_the_bold_italic_face(self) -> None:
        """Not `bold`, and not `italic` — the combined font file it measured against."""
        assert "bold_italic" in self._render("Some ***both*** here")

    def test_accumulates_across_calls(self) -> None:
        from mdsvg import parse
        from mdsvg.renderer import SVGRenderer

        renderer = SVGRenderer(fonts=self._faces())
        renderer.render(parse("Plain"), width=300, padding=0.0)
        renderer.render(parse("*Emphasized*"), width=300, padding=0.0)
        assert {"regular", "italic"} <= renderer.used_faces


class TestResolveFontsDir:
    """Pins the vendored-fonts lookup: the prefix, and the fail-vs-skip split.

    The lookup once searched a directory name that no longer existed, so the
    fixture skipped and ~20 font tests went quietly green.
    """

    def _make(self, root: Path, *prefix: str) -> Path:
        fonts = root.joinpath(*prefix, "dbt_charts", "core", "render", "fonts")
        fonts.mkdir(parents=True)
        return fonts

    def test_finds_monorepo_layout(self, resolve_fonts_dir, tmp_path: Path) -> None:
        fonts = self._make(tmp_path, "dbt-charts", "src")
        assert resolve_fonts_dir(tmp_path) == fonts

    def test_finds_standalone_export_layout(
        self, resolve_fonts_dir, tmp_path: Path
    ) -> None:
        fonts = self._make(tmp_path, "src")
        assert resolve_fonts_dir(tmp_path) == fonts

    def test_raises_when_the_monorepo_layout_is_missing_fonts(
        self, resolve_fonts_dir, tmp_path: Path
    ) -> None:
        """A stale monorepo path must be loud — a skip here is the bug, not a pass."""
        (tmp_path / "dbt-charts").mkdir()
        # Skipped must be in the tuple: it is a BaseException, so on its own
        # pytest.raises(AssertionError) would let it escape and report green —
        # which is exactly the mutation (dropping the raise) this test catches.
        with pytest.raises((AssertionError, pytest.skip.Exception)) as caught:
            resolve_fonts_dir(tmp_path)
        assert isinstance(caught.value, AssertionError), (
            f"a stale monorepo path must raise, not skip — got {caught.value!r}"
        )

    def test_skips_outside_the_monorepo(
        self, resolve_fonts_dir, tmp_path: Path
    ) -> None:
        with pytest.raises(pytest.skip.Exception):
            resolve_fonts_dir(tmp_path)

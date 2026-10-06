"""Structural guards on the vendored font registry.

``FONT_REGISTRY`` is the single home for family → file. These tests keep it honest:
the files it names exist, the stylesheet the browser downloads is generated from it
rather than hand-maintained beside it, and the set of families we measure but never
serve stays an explicit, argued-for list instead of quietly growing.

The pristine Noto Emoji source hash pin is intentionally not covered
here, since it pins a design-experiment copy outside dbt-charts/.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.fonts import (
    DBT_SANS_TABULAR_FONT_FAMILY,
    DBT_SANS_TABULAR_MEDIUM_FONT_FAMILY,
    DBT_SANS_TABULAR_SEMIBOLD_FONT_FAMILY,
    DBT_SERIF_MEDIUM_FONT_FAMILY,
    DBT_SERIF_SEMIBOLD_FONT_FAMILY,
    EMOJI_CODEPOINTS,
    FONT_FACE_CSS_HEADER,
    FONT_REGISTRY,
    INTER_VARIABLE_FONT_FAMILY,
    INTER_VARIABLE_MEDIUM_FONT_FAMILY,
    INTER_VARIABLE_SEMIBOLD_FONT_FAMILY,
    NOTO_EMOJI_FONT_FAMILY,
    TEXT_SUBSET_RANGES,
    VendoredFace,
    font_is_tabular,
    get_face,
    get_fonts_dir,
    render_embedded_font_face_css,
    render_font_face_css,
    served_faces,
)

# Families measured for layout but never served to the browser, so the client paints
# them from its own stack and the widths we compute cannot be verified against what
# the reader sees. Each entry needs a reason; adding one is a deliberate act.
_UNSERVED_FAMILIES = {
    # vl-convert-only weight faces (fonts.WEIGHT_FACE_ALIASES): never named in a
    # theme font stack a browser would resolve, so no @font-face declaration for
    # them is meaningful — the browser paints the real weight from the served
    # variable woff2 it already has. See fonts/README.md "Select figure style by
    # family" (weight section).
    DBT_SERIF_MEDIUM_FONT_FAMILY,
    DBT_SERIF_SEMIBOLD_FONT_FAMILY,
    DBT_SANS_TABULAR_MEDIUM_FONT_FAMILY,
    DBT_SANS_TABULAR_SEMIBOLD_FONT_FAMILY,
    INTER_VARIABLE_MEDIUM_FONT_FAMILY,
    INTER_VARIABLE_SEMIBOLD_FONT_FAMILY,
}


@pytest.mark.parametrize(
    "face", FONT_REGISTRY, ids=lambda f: f"{f.family}-{f.style}".replace(" ", "-")
)
def test_registered_files_are_shipped(face: VendoredFace) -> None:
    """Every file the registry names is actually in the wheel."""
    assert face.measure_path.exists(), f"missing measurement file {face.measure_file}"
    if face.web_file is not None:
        assert (get_fonts_dir() / face.web_file).exists(), (
            f"missing web file {face.web_file}"
        )


def test_served_stylesheet_is_generated_from_the_registry() -> None:
    """The committed stylesheet matches what the registry would emit.

    It is committed because three apps serve it as a static asset, and generated
    because the files it names must stay in lockstep with the files dbt charts measures.
    """
    committed = (get_fonts_dir() / "_font_face.css").read_text(encoding="utf-8")
    assert committed == FONT_FACE_CSS_HEADER + render_font_face_css(served_faces()), (
        "The committed _font_face.css is out of date with FONT_REGISTRY. Regenerate "
        "it with `just gen-font-face-css` and commit the result — hand-editing it is "
        "what let measurement and painting drift apart."
    )


def test_families_without_a_webfont_are_the_known_set() -> None:
    """A family we measure but never serve is a real gap, so it stays explicit.

    For these the browser paints something we did not measure, which is the same class
    of defect that clipped prose. Listing them here means a new one has to be argued
    for rather than inherited.
    """
    unserved = {f.family for f in FONT_REGISTRY if f.web_file is None}
    assert unserved == _UNSERVED_FAMILIES


@pytest.mark.parametrize("theme", ["clarity", "paper", "stark"])
def test_body_prose_measures_every_run_against_a_real_face(theme: str) -> None:
    """No board falls through to mdsvg's ratio estimate for bold or italic.

    mdsvg keeps a constant-ratio fallback for callers who supply a single font file.
    dbt charts must never land there: those constants assume bold is ~21% wider and
    italic ~8% wider, while the real faces measure bold at ~3% wider and Source Serif
    italic ~14% *narrower*. If a future change drops a face from the registry, prose
    would quietly revert to wrapping against invented numbers — so fail loudly here
    instead.
    """
    from dbt_charts.core.compile.config import get_theme_style
    from dbt_charts.core.compile.resolve.style.board import resolve_style
    from dbt_charts.core.compile.resolve.style.markdown_style import (
        body_text_font_family,
        get_compact_style,
    )
    from dbt_charts.core.font_measure import markdown_font_faces

    resolved = resolve_style(get_theme_style(theme))
    style = get_compact_style(resolved)
    faces = markdown_font_faces(body_text_font_family(resolved), style)

    missing = [
        name
        for name in ("regular", "bold", "italic", "bold_italic", "mono")
        if getattr(faces, name) is None
    ]
    assert not missing, f"{theme} prose would ratio-estimate these runs: {missing}"


def test_serif_italic_measures_narrower_than_upright() -> None:
    """Italic Source Serif is narrower than its upright — mdsvg's default assumes wider.

    Pins the direction of the correction, not a magic number: the shipped fallback
    ratio (0.52 against a 0.48 regular) claimed italic runs ~8% *wider*, so any board
    with italic prose wrapped short by roughly a fifth of an em per character.
    """
    from dbt_charts.core.fonts import SOURCE_SERIF_4_FONT_FAMILY, get_face
    from mdsvg.fonts import FontMeasurer

    sample = "The quick brown fox jumps over the lazy dog"
    upright = FontMeasurer(str(get_face(SOURCE_SERIF_4_FONT_FAMILY).measure_path))
    italic = FontMeasurer(
        str(get_face(SOURCE_SERIF_4_FONT_FAMILY, "italic").measure_path)
    )

    assert italic.measure(sample, 14.0) < upright.measure(sample, 14.0)


def test_oldstyle_faces_are_served_losslessly_not_subset() -> None:
    """The oldstyle woff2 files cover every codepoint their TTF measures.

    ``test_font_metric_parity`` compares only the *shared* cmap, so a future re-subset
    of either face would pass it while silently dropping glyphs. These are narrative
    faces set in running prose, where a dropped codepoint surfaces as a fallback glyph
    mid-sentence rather than as a failing test — so pin the full-coverage choice here.
    """
    from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

    oldstyle = [
        f
        for f in FONT_REGISTRY
        if f.family.startswith("dbt Serif Oldstyle") and f.web_file is not None
    ]
    assert len(oldstyle) == 2, "expected both oldstyle faces to be served"

    for face in oldstyle:
        assert face.web_file is not None
        measured = set(TTFont(face.measure_path).getBestCmap())
        painted = set(TTFont(get_fonts_dir() / face.web_file).getBestCmap())
        assert painted == measured, (
            f"{face.web_file} covers {len(painted)} codepoints against the TTF's "
            f"{len(measured)}. These faces are served losslessly on purpose; "
            f"missing: {sorted(measured - painted)[:5]}"
        )


# Characters a board full of statistics actually sets: Greek used as notation, the
# comparison and aggregation operators, and the trend arrows. Not an alphabet — a
# vocabulary, which is why it is pinned by character rather than by block.
_CHART_VOCABULARY = "μσαβΔπ≤≥≈≠±×÷∑√∞−‰°²₂→←↑↓"


def _served_text_faces() -> list[VendoredFace]:
    """Served faces that set text, i.e. everything but the curated emoji face."""
    return [
        f
        for f in FONT_REGISTRY
        if f.web_file is not None and f.family != NOTO_EMOJI_FONT_FAMILY
    ]


@pytest.mark.parametrize(
    "face",
    _served_text_faces(),
    ids=lambda f: f"{f.family}-{f.style}".replace(" ", "-"),
)
def test_served_faces_paint_every_recipe_codepoint_they_measure(
    face: VendoredFace,
) -> None:
    """No face may measure a recipe codepoint it cannot paint.

    ``test_font_metric_parity`` compares only the *shared* cmap, so a face whose
    measurement file carries Greek and whose served file does not passes it — the
    widths are computed from a glyph the browser never receives and the OS supplies
    something else at a different advance. That is the defect this whole subset
    recipe exists to close, and it is invisible to a parity check by construction.
    """
    from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

    assert face.web_file is not None
    measured = set(TTFont(face.measure_path).getBestCmap())
    painted = set(TTFont(get_fonts_dir() / face.web_file).getBestCmap())
    recipe = {cp for start, end in TEXT_SUBSET_RANGES for cp in range(start, end + 1)}

    unpainted = sorted((measured & recipe) - painted)
    assert not unpainted, (
        f"{face.family} ({face.style}) measures {len(unpainted)} recipe codepoints "
        f"its served {face.web_file} cannot paint, e.g. "
        f"{''.join(chr(cp) for cp in unpainted[:12])!r}. Re-subset it with "
        f"`just rebuild-text-font-subsets`."
    )


@pytest.mark.parametrize(
    "face",
    _served_text_faces(),
    ids=lambda f: f"{f.family}-{f.style}".replace(" ", "-"),
)
def test_served_text_faces_paint_the_chart_vocabulary(face: VendoredFace) -> None:
    """Every served text face can draw the notation a chart is written in.

    The recipe test above is relative — it only asks that a face paint what it
    measures, so a face missing Greek in *both* files passes it. This one is
    absolute: μ, σ, ≥, and ∑ are the vocabulary of a tool that draws statistics, and
    a serif theme that cannot set them is broken whether or not its two files agree.
    """
    from fontTools.ttLib import TTFont  # pyright: ignore[reportMissingTypeStubs]

    assert face.web_file is not None
    painted = set(TTFont(get_fonts_dir() / face.web_file).getBestCmap())

    missing = [c for c in _CHART_VOCABULARY if ord(c) not in painted]
    assert not missing, (
        f"{face.family} ({face.style}) cannot paint {''.join(missing)!r} — "
        f"{face.web_file} is missing {len(missing)} of the chart vocabulary."
    )


def test_font_is_tabular_true_for_the_vendored_tabular_family() -> None:
    assert font_is_tabular(DBT_SANS_TABULAR_FONT_FAMILY) is True


def test_font_is_tabular_reads_the_primary_family_of_a_css_stack() -> None:
    assert (
        font_is_tabular(f"'{DBT_SANS_TABULAR_FONT_FAMILY}', Inter, sans-serif") is True
    )


def test_font_is_tabular_false_for_a_proportional_vendored_family() -> None:
    assert font_is_tabular(INTER_VARIABLE_FONT_FAMILY) is False


def test_font_is_tabular_false_for_an_unvendored_family() -> None:
    """A system font we don't measure can't be guaranteed tabular — degrades
    to False (not a KeyError like ``get_face``) since this is a yes/no
    capability check, not a measurement lookup that must resolve to a real
    file.
    """
    assert font_is_tabular("Comic Sans MS") is False


def test_font_is_tabular_false_for_none() -> None:
    assert font_is_tabular(None) is False


def test_curated_emoji_faces_expose_identical_cmaps() -> None:
    """The measured TTF and the served woff2 must cover the exact same codepoints.

    Curating one file but not the other is the exact defect
    ``test_font_metric_parity.py`` exists to catch for shared codepoints — this test
    catches the case where the two files disagree on *which* codepoints exist at all,
    which parity (scoped to the shared cmap) would not see.
    """
    from fontTools.ttLib import TTFont

    face = get_face(NOTO_EMOJI_FONT_FAMILY)
    assert face.web_file is not None
    measured = set(TTFont(face.measure_path).getBestCmap())
    painted = set(TTFont(get_fonts_dir() / face.web_file).getBestCmap())
    assert measured == painted, (
        f"NotoEmoji-Regular.ttf and .woff2 disagree on their cmap: "
        f"measured-only {sorted(measured - painted)[:5]}, "
        f"painted-only {sorted(painted - measured)[:5]}"
    )


def test_curated_emoji_cmap_is_exactly_the_pinned_set() -> None:
    """The shipped emoji glyphs are exactly EMOJI_CODEPOINTS — no more, no fewer."""
    from fontTools.ttLib import TTFont

    measured = set(TTFont(get_face(NOTO_EMOJI_FONT_FAMILY).measure_path).getBestCmap())
    assert measured == set(EMOJI_CODEPOINTS)


def test_generated_unicode_range_excludes_ascii_digits() -> None:
    """The emoji face must never claim the Latin digit range U+0030-0039.

    Regression test: the digit-keycap group used to decompose under ``ord()`` into
    the bare ASCII digits, so the emoji ``@font-face`` claimed U+0030-0039 — the
    same range the text/Latin face serves. Keycaps are gone; nothing in the curated
    set should ever reintroduce a claim on plain digits.
    """
    css = render_font_face_css(served_faces())
    assert "U+0030" not in css
    assert 0x30 not in EMOJI_CODEPOINTS
    assert 0x39 not in EMOJI_CODEPOINTS


def test_generated_unicode_range_drops_keycap_composition_codepoints() -> None:
    """U+20E3 and U+200D have no purpose without keycaps or ZWJ sequences.

    Neither composes with anything left in the curated set (see fonts.py), so
    the emoji face should not claim either.
    """
    css = render_font_face_css(served_faces())
    assert "U+20E3" not in css
    assert "U+200D" not in css
    assert 0x20E3 not in EMOJI_CODEPOINTS
    assert 0x200D not in EMOJI_CODEPOINTS


def test_generated_unicode_range_is_derived_from_the_pinned_set() -> None:
    """The emitted unicode-range is computed from EMOJI_CODEPOINTS, not hand-written.

    Parses the ``unicode-range`` the CSS emits for Noto Emoji, expands every
    ``U+XXXX`` / ``U+XXXX-YYYY`` token back into codepoints, and checks that set is
    exactly ``EMOJI_CODEPOINTS`` — so the declared range and the pinned set cannot
    silently drift apart.
    """
    import re

    css = render_font_face_css(served_faces())
    match = re.search(r"'Noto Emoji';.*?unicode-range:\s*([^;]+);", css, re.DOTALL)
    assert match is not None

    covered: set[int] = set()
    for token in match.group(1).replace("\n", " ").split(","):
        stripped = token.strip().removeprefix("U+")
        if "-" in stripped:
            start, end = stripped.split("-")
            covered.update(range(int(start, 16), int(end, 16) + 1))
        else:
            covered.add(int(stripped, 16))

    assert covered == set(EMOJI_CODEPOINTS)


def test_no_family_declares_two_faces_for_one_style() -> None:
    """One (family, style) resolves to exactly one file.

    Two files claiming the same family is precisely what broke Source Serif 4: both a
    static and a variable build sat in the registered font directory, and which one
    vl-convert bound to was undefined.
    """
    seen: set[tuple[str, str]] = set()
    duplicates = []
    for face in FONT_REGISTRY:
        key = (face.family, face.style)
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    assert not duplicates, f"duplicate (family, style) rows: {duplicates}"


class TestEmbeddedFontFaceCss:
    """``render_embedded_font_face_css`` — the ``data:`` URI mode for offline exports.

    A standalone export cannot resolve ``/static/fonts/…``, so it carries the bytes.
    Because every listed face is then paid for unconditionally, this mode takes an
    explicit face set rather than iterating the registry the way URL mode does.
    """

    def test_embeds_only_the_faces_it_was_given(self) -> None:
        faces = (
            get_face(INTER_VARIABLE_FONT_FAMILY),
            get_face(NOTO_EMOJI_FONT_FAMILY),
        )
        css = render_embedded_font_face_css(faces)

        assert css.count("@font-face") == 2
        assert f"font-family: '{INTER_VARIABLE_FONT_FAMILY}'" in css
        assert f"font-family: '{NOTO_EMOJI_FONT_FAMILY}'" in css
        assert DBT_SANS_TABULAR_FONT_FAMILY not in css

    def test_src_carries_the_bytes_and_no_url(self) -> None:
        css = render_embedded_font_face_css([get_face(NOTO_EMOJI_FONT_FAMILY)])

        assert "data:font/woff2;base64," in css
        assert "/static/fonts" not in css
        assert "http://" not in css and "https://" not in css

    def test_embedded_bytes_are_the_served_file(self) -> None:
        import base64
        import re

        face = get_face(NOTO_EMOJI_FONT_FAMILY)
        css = render_embedded_font_face_css([face])

        match = re.search(r"data:font/woff2;base64,([A-Za-z0-9+/=]+)", css)
        assert match is not None
        assert face.web_file is not None
        assert (
            base64.b64decode(match.group(1))
            == (get_fonts_dir() / face.web_file).read_bytes()
        )

    def test_each_face_is_embedded_exactly_once(self) -> None:
        """Two copies of one face's base64 in a file is a bug, not a rounding error."""
        css = render_embedded_font_face_css(
            [get_face(INTER_VARIABLE_FONT_FAMILY), get_face(NOTO_EMOJI_FONT_FAMILY)]
        )
        assert css.count("data:font/woff2;base64,") == 2

    def test_a_measure_only_face_raises(self) -> None:
        """No silent skip: a face we never serve has no bytes to embed."""
        with pytest.raises(ValueError, match="not served"):
            render_embedded_font_face_css([get_face(DBT_SERIF_MEDIUM_FONT_FAMILY)])

    def test_carries_no_comment_of_any_kind(self) -> None:
        """A ``/*`` trips the exported-SVG comment ban; a Jinja comment reaches the
        browser literally."""
        css = render_embedded_font_face_css([get_face(NOTO_EMOJI_FONT_FAMILY)])
        assert "/*" not in css
        assert "{#" not in css

    def test_url_mode_names_files_rather_than_carrying_them(self) -> None:
        """The mode a host serves: URLs, no payload."""
        css = render_font_face_css(served_faces())
        assert css.count("@font-face") == len(served_faces())
        assert "base64" not in css

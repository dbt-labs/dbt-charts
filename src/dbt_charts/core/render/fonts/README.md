## Vendored Chart-Lab Fonts

This directory vendors the custom fonts required for the chart-lab import
review experience in dbt charts.

The fonts themselves ship with this package. The `just rebuild-*` /
`gen-font-face-css` recipes and the `design/` build tooling cited throughout are
monorepo-only — they regenerate the vendored files, they are not needed to use them.

Included assets:

- `InterVariable.ttf`
  - Existing dbt charts default sans variable font (used for SVG measurement
    and ReportLab-backed export paths).
- `InterVariable-Italic.ttf`
  - Google Fonts Inter variable italic font, used when prose asks for
    `font-style: italic` on sans themes such as `stark`. Measured, not served —
    the browser paints from the woff2 below.
  - Source: https://github.com/google/fonts/blob/main/ofl/inter/Inter-Italic%5Bopsz%2Cwght%5D.ttf
  - SHA-256: `acd98e64795781b2058f07b18475e0ecee2a0fe2b42a49e2f9e37d0d6bf66ce6`
    (upstream). `just rebuild-weight-faces` renames the vendored copy from
    `Inter` to `Inter Variable` (style `Italic`, PostScript
    `InterVariable-Italic`) — the family `font_support` rewrites `Inter` to for
    vl-convert. Under the upstream name no family held both faces, so every
    italic Inter request rasterized upright. Run it after re-vendoring.
- `InterVariable.woff2`, `InterVariable-Italic.woff2`
  - The served web fonts, subsetted to the shared text recipe (see "The subset
    recipe" below) and committed alongside the source TTFs. Rebuild both with
    `just rebuild-text-font-subsets`.
- `InterVariable-Medium.ttf`, `InterVariable-SemiBold.ttf`
  - Static wght=500/600 instances of `InterVariable.ttf`, renamed to their own
    families. Same vl-convert-only role and mechanism as the Source Serif
    Medium/SemiBold pair below — see "Select figure style by family". Built
    with `just rebuild-weight-faces`.
- `InterVariable-MediumItalic.ttf`, `InterVariable-SemiBoldItalic.ttf`,
  `SourceSerif4-MediumItalic.ttf`, `SourceSerif4-SemiBoldItalic.ttf`
  - Static wght=500/600 instances of the two vendored italics, named into the
    same alias family as their upright sibling (`Inter Variable Medium` Italic,
    `dbt Serif SemiBold` Italic, …). resvg never synthesizes an oblique, so
    without them the weight rewrite drops `font-style: italic` on the floor.
    Built with `just rebuild-weight-faces`.
- `DBTSansTabular-Regular.ttf`
  - In-house numeric companion font (derived from InterVariable) used for tabular figures and
    chart value labels. Variable font with wght axis 100–900 (default 400),
    so `font-weight` requests resolve to the correct master. The tabular
    `tnum` substitution is baked across all masters so digits are
    always tabular by default without any OpenType feature toggle.
  - Rebuilt from `InterVariable.ttf` via
    `design/experiments/chart-lab/tools/build_dbt_charts_sans_tabular.py`
    (run `just rebuild-dbt-sans-tabular` to regenerate).
- `DBTSansTabular-Regular.woff2`
  - Served at `/static/fonts/DBTSansTabular-Regular.woff2` so quantitative axis
    tick columns render in tabular figures (instead of falling back to
    system-ui proportional digits) in the browser. Subsetted to the shared text
    recipe below; rebuild with `just rebuild-text-font-subsets`.
- `DBTSansTabular-Medium.ttf`, `DBTSansTabular-SemiBold.ttf`
  - Static wght=500/600 instances of `DBTSansTabular-Regular.ttf`, renamed to
    their own families. Same vl-convert-only role and mechanism as the Source
    Serif Medium/SemiBold pair above — see "Select figure style by family".
    Built with `just rebuild-weight-faces`.
- `DBTSerifOldstyleTabular-Regular.ttf`
  - In-house serif companion with baked oldstyle figures that remain
    tabular for aligned numeric surfaces.
- `DBTSerifOldstyleTabular-Regular.woff2`
  - The same font in a woff2 container, served at
    `/static/fonts/DBTSerifOldstyleTabular-Regular.woff2` so a theme font stack
    naming the family resolves in the browser.
- `DBTSerifOldstyleProportional-Regular.ttf`
  - In-house serif companion with baked proportional oldstyle figures
    for editorial or narrative serif surfaces that want oldstyle numerals
    without tabular alignment.
- `DBTSerifOldstyleProportional-Regular.woff2`
  - Browser-facing container for the proportional cut, same arrangement.
  - Both oldstyle woff2 files are **lossless** conversions rather than recipe
    subsets like `InterVariable.woff2` beside them: these are narrative faces set
    in running prose, so the ~37 KB a subset would save is not worth dropping 513
    of the TTF's 920 codepoints. `design/experiments/chart-lab/tools/build_text_font_subsets.py`
    skips them by name
    and `tests/core/render/test_font_registry.py` pins the full-coverage choice.
    Rebuild either with `TTFont(ttf).flavor = "woff2"; save(...)`. Because the
    glyph table is carried over untouched, advance widths are identical by
    construction, which is what
    `tests/core/render/test_font_metric_parity.py` checks.
- `SourceSerif4Variable.ttf`
  - **Adobe Source Serif 4, tag `4.004R`, `VAR/SourceSerif4Variable-Roman.ttf`,
    instanced at `opsz=14` with the `wght` 200–900 axis kept.** Read for
    measurement by fontTools and ReportLab, and painted from directly by
    vl-convert in static exports — none of the three can read woff2.
  - **`opsz=14` is the load-bearing part of that sentence, not the version.**
    Adobe's face varies advance width with optical size and defaults to
    `opsz=20`; at that default, 209 of the 231 codepoints we already shipped
    disagree with our widths by around 5%. At `opsz=14`, all 231 match exactly.
    A re-cut that records only "Adobe 4.004" and drops the optical size
    reintroduces the error, which is what happened once already: this file
    replaced a separately-sourced `SourceSerif4-Regular.ttf` blamed on being
    build 4.005, when the real variable was the optical size. `hotconv`/
    `makeotfexe` version strings are identical across the two, so the version
    string does not distinguish them — the metrics do.
  - Rebuild with `just rebuild-source-serif-roman`. That script pins the source
    tag and its SHA-256, and refuses to write a font that changes the family name
    (Adobe names this face `Source Serif 4 Variable`; the registry, the themes,
    and vl-convert's family lookup all say `Source Serif 4`) or moves any shared
    codepoint's `hmtx` advance width.
  - **`hmtx` equality does not cover FreeType measures.** Wrap points and title
    columns come from summed `hmtx` advances (mdsvg), so a swap that keeps every
    advance identical keeps them identical. A measure through PIL/FreeType
    `getlength` (the test geometry sweeps) grid-fits the outline instead:
    Adobe's build carries no `gasp`/`prep` hinting tables where the old file did,
    so single glyphs measure a pixel differently — `A` 13→14, `W` 18→20 — with
    every `hmtx` advance unchanged. `GPOS` is `kern, mark, mkmk` in both, and PIL
    is built without raqm here, so no `GPOS` is applied on that path. The build
    script reports the FreeType width of a fixed probe string across the swap;
    expect goldens that depend on FreeType metrics to move, and get the diff
    viz-reviewed.
  - It carries 918 codepoints. The file it replaced carried 231 — it had been
    produced by decompressing the Latin woff2, so Greek and Mathematical
    Operators were absent from the measurement face too and could not be subset
    back in.
- `SourceSerif4Variable.woff2`, `SourceSerif4-Italic.woff2`
  - The served serif web fonts, subsetted to the shared text recipe from the
    TTFs beside them. Rebuild with `just rebuild-text-font-subsets`.
  - Both serif assets are used for chart titles and other narrative serif
    surfaces.
  - License text is included in `SOURCE_SERIF_4_LICENSE.txt`.
- `SourceSerif4-Italic.ttf`
  - Adobe Source Serif 4 variable italic TTF, used when prose asks for
    `font-style: italic` on editorial serif themes. Measured, not served.
  - It carries the `opsz` axis at Adobe's default of 20, while the roman beside
    it is pinned at 14 — so the italic is measured at a different optical size
    than its upright, and a browser applying `font-optical-sizing: auto` paints
    it at a third. Pinning it would move every italic wrap point, so it is
    recorded here rather than changed in passing. If this face is ever re-cut,
    run the same advance-width check the roman's build script performs.
  - Source: https://github.com/google/fonts/blob/main/ofl/sourceserif4/SourceSerif4-Italic%5Bopsz%2Cwght%5D.ttf
  - SHA-256: `15fbc7e4679489a501998c3669272637a6646388ef7e4bd77eebb5bf967a1f42`
- `SourceSerif4-Medium.ttf`, `SourceSerif4-SemiBold.ttf`
  - Static wght=500/600 instances of Adobe's original (unstripped) Source Serif 4
    Variable Roman release, renamed to their own families (`dbt Serif Medium`,
    `dbt Serif SemiBold` — not `Source Serif 4 …`, see the vl-convert prefix-collision
    note below). vl-convert-only: `fonts.WEIGHT_FACE_ALIASES` maps
    `(Source Serif 4, weight)` to these, and
    `font_support.normalize_svg_font_weights_for_vl_convert` rewrites the vl-convert
    copy of any `font-family="Source Serif 4…" font-weight="500|600"` tag to the
    matching family — the browser never sees these names, since it correctly
    interpolates the real weight from `SourceSerif4Variable.woff2`. See
    "Select figure style by family" below. Built with
    `design/experiments/chart-lab/tools/build_static_weight_instance.py`
    (`just rebuild-weight-faces`) from Adobe's GitHub release, **not** from
    `SourceSerif4Variable.ttf` beside them — that file is a reduced build (0 named
    `fvar` instances, no `DSIG`/`MVAR`/`avar`) that resvg silently fails to
    re-instance correctly; Adobe's full release does not have that problem.

- `NotoEmoji-Regular.ttf`
  - Google Noto Emoji monochrome static font, registered for vl-convert-backed
    chart rendering. ReportLab fallback is deferred (see task worksheet).
  - Subsetted to `dbt_charts.core.fonts.EMOJI_CODEPOINTS` — a fixed, approved
    72-codepoint chart-emoji set (status, trend, time, money, ops, org/geo,
    marks, docs, faces, weather). No digit keycaps: a keycap glyph needs a bare
    ASCII digit as its base character, which would claim U+0030-0039 for this
    face — the same range the text/Latin face serves — and the canonical
    spelling every emoji picker emits (`digit + U+FE0F + U+20E3`) is painted by
    the OS color font regardless (see the VS16 note below), so the claim never
    even fired. Adding or removing a codepoint means editing that constant,
    then running `just rebuild-noto-emoji-chart-set` to resubset both files and
    `just gen-font-face-css` to regenerate the stylesheet — the set, the
    shipped glyphs, and the `unicode-range` cannot drift apart because all
    three are derived from the one constant.
  - The subsetter reads from a separate pristine, un-subset copy rather than
    this file, so a future codepoint addition is never subsetting a glyph
    that a prior subset already discarded:
    `design/experiments/chart-lab/tools/NotoEmoji-Regular-full.ttf`.
  - The Google Fonts specimen page (below) serves whatever Noto Emoji build is
    current on the day someone visits it, so it names a moving target rather
    than a fixed file. Two facts about the pristine copy actually pin what
    was vendored: its `name` table ID 5 reads `Version 3.005`, and it is
    byte-identical to the `NotoEmoji-Regular.ttf` that was already vendored at
    merge base `fab6cc71` — this is the same cut carried forward through the
    curation change, not a fresh download.
  - Source (pristine copy): https://fonts.google.com/noto/specimen/Noto+Emoji
    (static monochrome text-presentation cut, no COLR/CPAL tables; build
    `Version 3.005` per the facts above, not necessarily what the page serves
    today)
  - SHA-256 (pristine copy): `3c4aea565060fa91575a851e2718a5b14b9fe8856ead696b374c5a7e672179cb` —
    pinned by `test_pristine_noto_emoji_source_matches_pinned_sha256` in the
    monorepo's chart-lab tests (deliberately not in this package's
    `test_font_registry.py`, which pins the vendored copy, not the source),
    since nothing else reads this file's hash and a silent source refresh
    would otherwise replace all 72 glyph outlines with the codepoint-set
    checks none the wiser.
- `NotoEmoji-Regular.woff2`
  - Google Noto Emoji monochrome web font, used by `@font-face` CSS in all
    HTML surfaces for deterministic cross-platform emoji rendering.
  - Subsetted to the exact same `EMOJI_CODEPOINTS` set as the TTF beside it —
    `test_font_registry.py` fails if the two files' cmaps ever disagree, since
    measuring against a wider file than we paint from is exactly the defect
    `test_font_metric_parity.py` exists to catch.
  - License: OFL-1.1 (same family as Inter and Source Serif 4).
  - License text is included in `NOTO_EMOJI_LICENSE.txt`.
  - Same pristine source and subsetting as `NotoEmoji-Regular.ttf` above.
  - No composition group: U+200D (ZWJ) and U+20E3 (combining enclosing keycap)
    were only useful when *both* halves of a sequence were in the shipped glyph
    set. That was true of the digit-keycap group and nothing else — no
    person/profession ZWJ emoji (e.g. 👨‍💻) is in the curated set, so any such
    sequence always fell through to the browser's own emoji font regardless.
    Once keycaps were removed for the reason above, neither composition
    codepoint had a group left to compose, so both were dropped too.
  - **U+FE0F defeats this font in a browser, but the subset keeps it anyway.**
    Variation selector-16 requests emoji *presentation*, and browsers answer
    that with a color font in preference to a text-presentation face — so
    `digit + U+FE0F + U+20E3` (the canonical keycap every emoji picker emitted)
    was painted by the OS at 1.00 em, while the bare form painted from here at
    1.27 em. The same split hits any curated codepoint with a VS16 spelling:
    ⚠ vs ⚠️, ℹ vs ℹ️, ⏰ vs ⏰️. Measured in Chromium with `font-variant-emoji: text`
    applied and this font loaded; the declaration does not change the
    selection. This is not caused by curation — it predates it and applies to
    any monochrome emoji font. Several curated codepoints (⚠️, ℹ️, the arrows,
    ☀️/🌧️/❄️, ⚙️/🛠️, ✏️, 🗓️) are authored in their canonical VS16-forcing
    spelling, so U+FE0F lands in `EMOJI_CODEPOINTS` as a side effect of keeping
    those glyphs; it was kept deliberately rather than stripped back out,
    because vl-convert/resvg static exports (PNG/SVG) have no such
    color-font-preference override — only the browser path is defeated.
    Don't test or document composition against a VS16 spelling, or against a
    sequence this font doesn't carry both halves of.

- `SourceCodePro-Regular.ttf`
  - Adobe Source Code Pro Regular TTF, used for strict monospace measurement of
    inline `code` spans, callout badges, and code-shaped table cells. Vendored so
    the mono FontMeasurer is available in every deployment environment (deployed
    containers, slim Linux base images) without requiring system fonts.
  - Source: https://github.com/adobe-fonts/source-code-pro/releases/tag/2.042R-u%2F1.062R-i%2F1.026R-vf
    (release 2.042R-u / 1.062R-i / 1.026R-vf, `TTF/SourceCodePro-Regular.ttf`)
  - SHA-256: `74bd80d3e42a08517cd7e1108ba3d86f2da29ac0f3065be95e0357956ab9db37`
  - License: OFL-1.1 (same as Inter, Source Serif 4, and Noto). License text is
    included in `SOURCE_CODE_PRO_LICENSE.txt`.
- `SourceCodePro-Regular.woff2`
  - Served at `/static/fonts/SourceCodePro-Regular.woff2` so inline `code` spans and
    fenced blocks paint from the same file the widths were measured against, instead
    of whatever monospace face the browser resolves. Subsetted to the shared text
    recipe below; rebuild with `just rebuild-text-font-subsets`. The callout badge and
    code-shaped table cell surfaces still measure against this file but are not yet
    threaded onto the served family in their own emitted CSS — tracked separately.

Chart-lab also serves browser-facing copies of the shared fonts from
`design/experiments/chart-lab/app/fonts/` for local review. Tests assert that
the chart-lab copies remain byte-for-byte identical with the vendored runtime
copies.

## The subset recipe

Every served text face carries the same nine Unicode blocks, roman and italic alike.
The recipe is `dbt_charts.core.fonts.TEXT_SUBSET_RANGES`; `just rebuild-text-font-subsets`
applies it to every face the registry serves as woff2, and
`tests/core/render/test_font_registry.py` fails if a served face falls short of it.

| block | range | |
|---|---|---|
| Basic Latin + Latin-1 Supplement | U+0000–00FF | |
| Latin Extended-A | U+0100–017F | |
| Latin Extended-B | U+0180–024F | |
| Greek and Coptic | U+0370–03FF | μ σ α β Δ π |
| General Punctuation | U+2000–206F | dashes, quotes, ‰ |
| Superscripts and Subscripts | U+2070–209F | |
| Currency Symbols | U+20A0–20BF | |
| Arrows | U+2190–21FF | |
| Mathematical Operators | U+2200–22FF | ≤ ≥ ≈ ≠ ∑ √ ∞ |

Greek and the operator blocks are in the recipe because they are chart notation, not
alphabets: a tool that draws statistics sets μ and ≥, and the previous recipe measured
text against faces that had them while painting from files that did not. **Cyrillic and
Vietnamese were considered and deliberately left out** — those are internationalization,
a separate question from what a chart says, and adding them means committing to
rendering whole scripts rather than a vocabulary.

Two things about applying it, both learned the expensive way:

- **Keep every OpenType feature the source carries** (`layout_features = ["*"]`).
  fontTools' subsetter defaults to a standard set and silently drops the discretionary
  ones — `tnum`, `sups`/`subs`, `smcp`, the `ssXX` alternates — along with the alternate
  glyphs they reach. That is a second narrowing, unrelated to the codepoint recipe, and
  no cmap-based check can see it. It is also most of the file size: Inter subsets to
  109 KiB with features dropped and 147 KiB with them kept.
- **The measured TTF stays wider than the served woff2, on purpose.** vl-convert paints
  static PNG and PDF exports from the TTF, so narrowing it to the recipe would cost
  export coverage to save nothing a browser downloads. The asymmetry is safe because
  `test_font_metric_parity.py` compares the shared cmap and
  `test_font_registry.py` fails on any recipe codepoint the TTF measures and the woff2
  cannot paint — the gap that is left is what falls through to the reader's own fonts.

Measured against Inter, against a strict-Latin baseline of 147.5 KiB: Greek +17.8 KiB,
Mathematical Operators +3.9, Superscripts and Subscripts +0.1, Arrows +4.8.

## Select figure style by family, never by OpenType feature

`font-feature-settings: "onum"` and `font-variant-numeric: oldstyle-nums` are both
valid CSS, both work in a browser, and both are **silently dropped by the static
export renderer**. vl-convert rasterizes through resvg, which does not apply OpenType
feature settings; the exported PNG is byte-identical to one rendered with no feature
request at all. A board selecting oldstyle that way looks correct on screen and
reverts to lining figures in every exported PNG and PDF, with no warning at any layer.

So figure style is selected by *naming the family* — `dbt Serif Oldstyle Tabular` or
`dbt Serif Oldstyle Proportional`, whose oldstyle figures are baked into the glyph
outlines and therefore survive any renderer. The same reasoning is why
`dbt Sans Tabular` bakes `tnum` across its masters instead of asking for the feature.
the engine's table family still emits `font-feature-settings: 'tnum'` beside its tabular
family, which is fine: the family is doing the work and the declaration is inert
decoration for renderers that would honor it.

The same defect, same fix, applies to **font weight**. resvg does not interpolate a
variable font's `wght` axis from a numeric `font-weight` request either: below its
internal synthetic-bold threshold (~600) every weight paints as Regular, and at/above
it resvg applies a fixed embolden pass rather than the font's real SemiBold cut —
confirmed by rendering the same weight through `to_png()` and diffing the output
byte-for-byte, not by eye (two visually-similar-looking renders turned out to be
literally the same PNG more than once while chasing this). The browser has no such
limitation — it paints the real weight from the served variable woff2. So, same
mechanism as oldstyle figures: `fonts.WEIGHT_FACE_ALIASES` maps every
`(vendored family, cascaded weight)` pair the built-in theme system produces to a
dedicated static face selected by family name, and
`font_support.normalize_svg_font_weights_for_vl_convert` rewrites the vl-convert-bound
SVG copy only. `tests/core/render/test_theme_weight_face_coverage.py` walks
every built-in theme and fails if one starts cascading a weight with no row in
`WEIGHT_FACE_ALIASES` — the covered set is closed (currently 500 and 600 across all
three variable families) and is meant to stay that way; a new weight needs a new
static face, not a broader fallback.

**A new family name sharing a string prefix with an already-registered family is not
safe by default — verify it, don't assume it.** `Source Serif 4 Medium`/
`Source Serif 4 SemiBold` looked like the obvious names and rendered *identically* to
Regular — vl-convert silently redirected any query family beginning with the exact
already-registered `Source Serif 4` string back to that file, regardless of the
suffix, even a nonsense one (confirmed with a `Source Serif 4 XyzUnique` control).
`dbt Sans Tabular Medium`/`SemiBold` and `Inter Variable Medium`/`SemiBold` share the
same kind of prefix with their own base families and were confirmed, by the same
byte-diff method, to **not** hit this — the collision is specific to `Source Serif 4`
(most likely tied to its base file's reduced metadata, described above), not to prefix
sharing in general. Source Serif's derived faces are named `dbt Serif Medium` /
`dbt Serif SemiBold` — no shared prefix with `Source Serif 4` — specifically to
sidestep it. Because "shares a prefix" is not a reliable predictor either way, verify
any new weight-face family name empirically: render it isolated, then again alongside
the base family's own file, and diff PNG bytes, not eyes.

Serving both oldstyle faces is what makes the family route real. A registered family
with no `@font-face` binds for measurement but not for painting, so prose wraps against
oldstyle advance widths while the browser draws the fallback — measured one file,
painted another, which is the defect `test_font_metric_parity.py` exists to catch.

## This directory's stylesheet is generated, and cannot carry comments

`_font_face.css` is written by `just gen-font-face-css` from `FONT_REGISTRY`; edit the
registry, not the file. It also cannot hold explanatory comments of any kind. It is
inlined into every rendered board's `<style>` block, where a `/*` comment trips the
test forbidding CSS comments in exported SVG, and it is served raw as CSS, where a
Jinja `{# #}` comment would reach the browser literally. Reasoning about these assets
belongs in this README.

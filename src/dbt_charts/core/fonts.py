"""The vendored font registry: one home for every family we measure or paint.

Text width is decided twice — once here, when fontTools reads advance widths to pick
wrap points, and once in the browser, when it paints from the ``@font-face`` files this
module also names. Those two answers must come from the same glyph bytes. When they did
not, wide prose wrapped against widths narrower than it was drawn at and ran past the
nested ``<svg>`` viewport that clips it, slicing the last word off the line with no error.

So family → file lives in exactly one place: ``FONT_REGISTRY``. Measurement, the emitted
``@font-face`` CSS, and vl-convert all read it. Adding a font means adding a
row, not adding an accessor.
"""

import base64
import re
import threading
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass
from functools import cache
from pathlib import Path  # noqa: TID251 — package-shipped vendored font files

from importlib_resources import as_file, files

_fonts_stack = ExitStack()
_fonts_lock = threading.Lock()
_fonts_dir: Path | None = None


def get_fonts_dir() -> Path:
    """Return the directory holding every vendored font asset.

    Resolves once per process via ``importlib_resources.as_file()`` (the
    backport — stdlib ``as_file`` gains directory support only in 3.12+) and
    caches the persistent real directory in a never-closed ``ExitStack``: a
    no-op on the normal unpacked install, a single extraction on a zip
    install. Every consumer (PIL, fontTools, mdsvg, Starlette
    ``StaticFiles``, vl-convert) keeps getting a real directory/path.
    """
    global _fonts_dir
    # Double-checked lock: the outer check avoids the lock on the common
    # (already-resolved) path; the inner check stops a second thread that
    # was already waiting on the lock from resolving it twice.
    if _fonts_dir is None:
        with _fonts_lock:
            if _fonts_dir is None:
                _fonts_dir = _fonts_stack.enter_context(
                    as_file(files("dbt_charts.core.render").joinpath("fonts"))
                )
    return _fonts_dir


@dataclass(frozen=True)
class VendoredFace:
    """One face of one family: the bytes we measure and the bytes we paint.

    ``measure_file`` and ``web_file`` must carry identical advance widths for every
    codepoint they share — that equality is what keeps wrap points honest, and
    ``tests/core/render/test_font_metric_parity.py`` enforces it. They are two files
    rather than one only because browsers want woff2 compression and fontTools and
    vl-convert want an uncompressed table.

    ``web_file`` is ``None`` for a family we measure but never serve — the client
    paints those from its own font stack, so the widths we compute are an estimate we
    cannot verify. That is a real gap, and it is recorded here rather than hidden;
    ``test_font_registry.py`` pins the set so a new family cannot quietly join it.
    """

    family: str
    style: str
    measure_file: str
    web_file: str | None
    weight_range: tuple[int, int] | None = None
    unicode_range: tuple[str, ...] = ()
    # True only for a family whose digits render tabular (fixed-advance) by
    # default, with no CSS feature toggle required — dbt Sans Tabular and its
    # oldstyle sibling are built that way; every other vendored family
    # (including InterVariable) is proportional by default. A column of
    # numbers (a vertical ruler, a ledger) needs this guarantee: the
    # suffix-field reservation (font_measure.compose_suffix_reservation) is
    # composed from measured space characters to match the suffix's
    # advance, and the column holds together only because every digit
    # shares that one fixed advance.
    tabular: bool = False

    @property
    def measure_path(self) -> Path:
        """Absolute path to the file fontTools/vl-convert should read."""
        return get_fonts_dir() / self.measure_file


# The approved chart-emoji set — the only codepoints NotoEmoji-Regular.{ttf,woff2}
# are subset to (rebuild with `just rebuild-noto-emoji-chart-set`). Adding a codepoint
# means adding it here, resubsetting both files, and regenerating _font_face.css — the
# three cannot drift apart because the CSS and the subset command both read this set.
# No keycap or ZWJ group: a keycap glyph needs a bare ASCII digit as its base
# character, which would claim U+0030-0039 for the emoji face — the same range the
# text/Latin face serves — and the canonical spelling every emoji picker emits
# (digit + U+FE0F + U+20E3) is painted by the OS color font regardless, so the
# claim never even fires. No person/profession ZWJ emoji is in this set either, so
# a ZWJ sequence always falls to the OS on both halves. Neither composition
# codepoint (U+200D, U+20E3) earns a place without a group that needs it. U+FE0F
# (variation selector-16) still lands in EMOJI_CODEPOINTS below as a side effect
# of the several codepoints above that are text-presentation by default and are
# authored here in their canonical VS16-forcing spelling (\u26a0\ufe0f, \u2139\ufe0f, the
# arrows, \u2600\ufe0f/\U0001f327\ufe0f/\u2744\ufe0f, \u2699\ufe0f/\U0001f6e0\ufe0f, \u270f\ufe0f, \U0001f5d3\ufe0f). Per
# render/fonts/README.md, that selector defeats this font in a browser — Chromium
# always paints those from the OS color font regardless of what we subset — but
# vl-convert/resvg static exports have no such override, so a subset that still
# carries U+FE0F is what lets those glyphs pair correctly there. Kept deliberately;
# not worth stripping VS16 from the glyphs above just to also drop it from the
# browser path where it was already inert.
_EMOJI_STATUS = "✅❌⚠️🚫⛔❗❓ℹ️"
_EMOJI_TREND = "📈📉📊⬆️⬇️➡️⬅️↗️↘️🔺🔻"
_EMOJI_TIME = "⏰⌛⏳📅🗓️🕐"
_EMOJI_MONEY = "💰💵💲💳🏦🧾📦🛒"
_EMOJI_OPS = "🚀🔧🔨⚙️🛠️🐛🔥⚡💡🔍📌📍"
_EMOJI_ORG_GEO = "👤👥🏢🌍🌎🌏"
_EMOJI_MARKS = "⭐🏆🥇🎯✨💯"
_EMOJI_DOCS = "📄📁📋📝✏️🔗"
_EMOJI_FACES = "😀🙂😐🙁😞"
_EMOJI_WEATHER = "☀️🌧️❄️"

EMOJI_CODEPOINTS: frozenset[int] = frozenset(
    ord(char)
    for group in (
        _EMOJI_STATUS,
        _EMOJI_TREND,
        _EMOJI_TIME,
        _EMOJI_MONEY,
        _EMOJI_OPS,
        _EMOJI_ORG_GEO,
        _EMOJI_MARKS,
        _EMOJI_DOCS,
        _EMOJI_FACES,
        _EMOJI_WEATHER,
    )
    for char in group
)


def _emoji_unicode_range_lines(codepoints: frozenset[int]) -> tuple[str, ...]:
    """Collapse a codepoint set into wrapped ``U+XXXX[-YYYY]`` CSS unicode-range lines.

    Consecutive codepoints collapse into one ``U+XXXX-YYYY`` run; runs are chunked
    four to a line so the emitted CSS (inlined into every board's ``<style>``) stays
    readable in a diff instead of landing as one long line.
    """
    ordered = sorted(codepoints)
    runs: list[tuple[int, int]] = []
    for cp in ordered:
        if runs and cp == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], cp)
        else:
            runs.append((cp, cp))
    tokens = [
        f"U+{start:04X}" if start == end else f"U+{start:04X}-{end:04X}"
        for start, end in runs
    ]
    chunk_size = 4
    return tuple(
        ", ".join(tokens[i : i + chunk_size]) for i in range(0, len(tokens), chunk_size)
    )


_EMOJI_UNICODE_RANGES = _emoji_unicode_range_lines(EMOJI_CODEPOINTS)

# The subset recipe: what every served text face carries, roman and italic alike.
# Latin and punctuation are the alphabet; Greek and the operator blocks are the
# notation — a tool that draws statistics sets μ, σ, ≥, and ∑, and measuring text
# against a face the browser cannot paint it from is how wrap points go wrong.
# Cyrillic and Vietnamese were considered and left out: those are
# internationalization, a separate question from what a chart says.
# `just rebuild-text-font-subsets` applies this to every served face;
# `tests/core/render/test_font_registry.py` fails if a served face falls short of it.
TEXT_SUBSET_RANGES: tuple[tuple[int, int], ...] = (
    (0x0000, 0x00FF),  # Basic Latin + Latin-1 Supplement
    (0x0100, 0x017F),  # Latin Extended-A
    (0x0180, 0x024F),  # Latin Extended-B
    (0x0370, 0x03FF),  # Greek and Coptic
    (0x2000, 0x206F),  # General Punctuation
    (0x2070, 0x209F),  # Superscripts and Subscripts
    (0x20A0, 0x20BF),  # Currency Symbols
    (0x2190, 0x21FF),  # Arrows
    (0x2200, 0x22FF),  # Mathematical Operators
)

INTER_FONT_FAMILY = "Inter"
INTER_VARIABLE_FONT_FAMILY = "Inter Variable"
DBT_SANS_TABULAR_FONT_FAMILY = "dbt Sans Tabular"
DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY = "dbt Serif Oldstyle Tabular"
DBT_SERIF_OLDSTYLE_PROPORTIONAL_FONT_FAMILY = "dbt Serif Oldstyle Proportional"
SOURCE_SERIF_4_FONT_FAMILY = "Source Serif 4"
SOURCE_CODE_PRO_FONT_FAMILY = "Source Code Pro"
NOTO_EMOJI_FONT_FAMILY = "Noto Emoji"

# vl-convert (resvg) cannot bind a variable font's wght axis from a numeric
# font-weight request — see fonts/README.md "Select figure style by family, never
# by OpenType feature". These are static instances of the three vendored variable
# families, selected by family name instead, the same way oldstyle figures are.
# Never served to the browser (web_file=None below): the browser already
# interpolates the real weight from the served variable woff2.
DBT_SERIF_MEDIUM_FONT_FAMILY = "dbt Serif Medium"
DBT_SERIF_SEMIBOLD_FONT_FAMILY = "dbt Serif SemiBold"
DBT_SANS_TABULAR_MEDIUM_FONT_FAMILY = "dbt Sans Tabular Medium"
DBT_SANS_TABULAR_SEMIBOLD_FONT_FAMILY = "dbt Sans Tabular SemiBold"
INTER_VARIABLE_MEDIUM_FONT_FAMILY = "Inter Variable Medium"
INTER_VARIABLE_SEMIBOLD_FONT_FAMILY = "Inter Variable SemiBold"

# (base vendored family, cascaded font-weight) -> the internal family vl-convert
# must select instead of the base family for that weight to render as anything
# other than Regular. Keys are the closed, theme-cascaded weight set — every
# non-400 (family, weight) pair the built-in themes ever produce for these three
# families; dbt-charts/tests/core/render/test_theme_weight_face_coverage.py fails
# the moment a theme starts cascading a weight with no row here.
WEIGHT_FACE_ALIASES: dict[str, dict[str, str]] = {
    SOURCE_SERIF_4_FONT_FAMILY: {
        "500": DBT_SERIF_MEDIUM_FONT_FAMILY,
        "600": DBT_SERIF_SEMIBOLD_FONT_FAMILY,
    },
    DBT_SANS_TABULAR_FONT_FAMILY: {
        "500": DBT_SANS_TABULAR_MEDIUM_FONT_FAMILY,
        "600": DBT_SANS_TABULAR_SEMIBOLD_FONT_FAMILY,
    },
    INTER_VARIABLE_FONT_FAMILY: {
        "500": INTER_VARIABLE_MEDIUM_FONT_FAMILY,
        "600": INTER_VARIABLE_SEMIBOLD_FONT_FAMILY,
    },
}


def offered_font_families() -> tuple[str, ...]:
    """The families an editor can offer for a `family:` value.

    The browser-served vendored faces (``web_file`` set — the internal
    weight-alias instances are vl-convert plumbing, never an offer), plus the
    CSS generics. Shortcuts, not a closed set: any family or stack stays a
    legal value.
    """
    vendored = dict.fromkeys(
        face.family for face in FONT_REGISTRY if face.web_file is not None
    )
    return (*vendored, "system-ui", "sans-serif", "serif", "monospace")


FONT_REGISTRY: tuple[VendoredFace, ...] = (
    VendoredFace(
        family=INTER_VARIABLE_FONT_FAMILY,
        style="normal",
        measure_file="InterVariable.ttf",
        web_file="InterVariable.woff2",
        weight_range=(100, 900),
    ),
    VendoredFace(
        family=INTER_VARIABLE_FONT_FAMILY,
        style="italic",
        measure_file="InterVariable-Italic.ttf",
        web_file="InterVariable-Italic.woff2",
        weight_range=(100, 900),
    ),
    VendoredFace(
        family=DBT_SANS_TABULAR_FONT_FAMILY,
        style="normal",
        measure_file="DBTSansTabular-Regular.ttf",
        web_file="DBTSansTabular-Regular.woff2",
        weight_range=(100, 900),
        tabular=True,
    ),
    VendoredFace(
        family=SOURCE_SERIF_4_FONT_FAMILY,
        style="normal",
        # Adobe's 4.004 release instanced at opsz=14 — the optical size, not the
        # version, is what keeps its advance widths equal to the cut we have always
        # shipped. See fonts/README.md before re-cutting it.
        measure_file="SourceSerif4Variable.ttf",
        web_file="SourceSerif4Variable.woff2",
        weight_range=(200, 900),
    ),
    VendoredFace(
        family=SOURCE_SERIF_4_FONT_FAMILY,
        style="italic",
        measure_file="SourceSerif4-Italic.ttf",
        web_file="SourceSerif4-Italic.woff2",
        weight_range=(200, 900),
    ),
    VendoredFace(
        family=DBT_SERIF_MEDIUM_FONT_FAMILY,
        style="normal",
        measure_file="SourceSerif4-Medium.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=DBT_SERIF_MEDIUM_FONT_FAMILY,
        style="italic",
        measure_file="SourceSerif4-MediumItalic.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=DBT_SERIF_SEMIBOLD_FONT_FAMILY,
        style="normal",
        measure_file="SourceSerif4-SemiBold.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=DBT_SERIF_SEMIBOLD_FONT_FAMILY,
        style="italic",
        measure_file="SourceSerif4-SemiBoldItalic.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=DBT_SANS_TABULAR_MEDIUM_FONT_FAMILY,
        style="normal",
        measure_file="DBTSansTabular-Medium.ttf",
        web_file=None,
        tabular=True,
    ),
    VendoredFace(
        family=DBT_SANS_TABULAR_SEMIBOLD_FONT_FAMILY,
        style="normal",
        measure_file="DBTSansTabular-SemiBold.ttf",
        web_file=None,
        tabular=True,
    ),
    VendoredFace(
        family=INTER_VARIABLE_MEDIUM_FONT_FAMILY,
        style="normal",
        measure_file="InterVariable-Medium.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=INTER_VARIABLE_MEDIUM_FONT_FAMILY,
        style="italic",
        measure_file="InterVariable-MediumItalic.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=INTER_VARIABLE_SEMIBOLD_FONT_FAMILY,
        style="normal",
        measure_file="InterVariable-SemiBold.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=INTER_VARIABLE_SEMIBOLD_FONT_FAMILY,
        style="italic",
        measure_file="InterVariable-SemiBoldItalic.ttf",
        web_file=None,
    ),
    VendoredFace(
        family=NOTO_EMOJI_FONT_FAMILY,
        style="normal",
        measure_file="NotoEmoji-Regular.ttf",
        web_file="NotoEmoji-Regular.woff2",
        unicode_range=_EMOJI_UNICODE_RANGES,
    ),
    VendoredFace(
        family=SOURCE_CODE_PRO_FONT_FAMILY,
        style="normal",
        measure_file="SourceCodePro-Regular.ttf",
        web_file="SourceCodePro-Regular.woff2",
    ),
    VendoredFace(
        family=DBT_SERIF_OLDSTYLE_TABULAR_FONT_FAMILY,
        style="normal",
        # No built-in theme emits either oldstyle face; both are reachable by naming
        # them in a theme font stack. That is why they are served rather than
        # measure-only — selecting oldstyle figures through `font-feature-settings`
        # instead does not survive static export (see fonts/README.md), so the family
        # is the only route, and a family the browser cannot download is not a route.
        measure_file="DBTSerifOldstyleTabular-Regular.ttf",
        web_file="DBTSerifOldstyleTabular-Regular.woff2",
        tabular=True,
    ),
    VendoredFace(
        family=DBT_SERIF_OLDSTYLE_PROPORTIONAL_FONT_FAMILY,
        style="normal",
        measure_file="DBTSerifOldstyleProportional-Regular.ttf",
        web_file="DBTSerifOldstyleProportional-Regular.woff2",
    ),
)


AUTHORED_FONT_STACK_PATTERN = r'^[^;{}<>"\\*\x00-\x1f\x7f]*$'
_AUTHORED_FONT_STACK_RE = re.compile(AUTHORED_FONT_STACK_PATTERN)
_CSS_IDENT_WORD = re.compile(r"^-?[A-Za-z_][A-Za-z0-9_-]*$")


def check_authored_font_stack(stack: str) -> str:
    """Return ``stack`` with every entry that is not a valid CSS identifier quoted.

    A word cannot start with a digit, so ``Source Serif 4`` is written
    ``'Source Serif 4'``; a browser would drop the whole declaration otherwise.
    Raises ``ValueError`` on a forbidden character, an empty entry, or a stray quote.
    """
    if not _AUTHORED_FONT_STACK_RE.fullmatch(stack):
        raise ValueError(
            f'{stack!r} is not a font family: remove any of ; {{ }} < > " \\ * '
            "or a control character"
        )
    if not stack.strip():
        return stack
    entries = []
    for raw in stack.split(","):
        entry = raw.strip()
        if not entry:
            raise ValueError(f"{stack!r} has an empty entry")
        if entry[0] == "'":
            if len(entry) < 3 or entry[-1] != "'" or "'" in entry[1:-1]:
                raise ValueError(f"{entry!r} in {stack!r} is not a quoted family name")
        elif "'" in entry:
            raise ValueError(f"{entry!r} in {stack!r} has a stray quote")
        elif not all(_CSS_IDENT_WORD.match(word) for word in entry.split()):
            entry = f"'{entry}'"
        entries.append(entry)
    return ", ".join(entries)


def registry_family(font_family: str) -> str:
    """The registry family name a CSS font stack resolves to.

    The first entry is what matters — that is the family the browser reaches for
    first, and therefore the one whose metrics govern layout. Any ``Source Serif``
    spelling names the single vendored cut of it.

    Returns the primary entry as written for a family we do not vendor; callers
    decide what that means, since a stack naming Georgia is asking for the
    reader's own font, not for one of ours.
    """
    primary = font_family.split(",", 1)[0].strip().strip("'\"")
    if primary.startswith("Source Serif"):
        return SOURCE_SERIF_4_FONT_FAMILY
    return primary


def get_face(font_family: str, style: str = "normal") -> VendoredFace:
    """Return the registry row for a family name, or raise if we do not ship it.

    ``font_family`` may be a full CSS stack — the first entry is what matters, since
    that is the family the browser reaches for first and therefore the one whose
    metrics govern layout.
    """
    primary = font_family.split(",", 1)[0].strip().strip("'\"")
    for face in FONT_REGISTRY:
        if face.family == primary and face.style == style:
            return face
    raise KeyError(
        f"No vendored font registered for family {primary!r} style {style!r}. "
        f"Known: {sorted({f.family for f in FONT_REGISTRY})}"
    )


def font_is_tabular(font_family: str | None) -> bool:
    """Whether ``font_family`` guarantees tabular (fixed-advance) digits.

    A yes/no capability check, not a measurement lookup — an unset or
    unvendored family (a system font the browser resolves that we cannot
    read) degrades to False rather than raising the way ``get_face`` does,
    since there is no file to fail to find here, only a guarantee that
    cannot be made.
    """
    if not font_family:
        return False
    primary = font_family.split(",", 1)[0].strip().strip("'\"")
    return any(face.family == primary and face.tabular for face in FONT_REGISTRY)


def get_font_path(font_family: str | None = None, *, numeric: bool = False) -> str:
    """Return the file to measure for a rendered font family.

    Falls back to the sans body face for any family we do not vendor: the browser will
    resolve such a stack to a system font we cannot read, so Inter's metrics are the
    closest honest estimate available rather than a silent substitution of something
    arbitrary.
    """
    if font_family and registry_family(font_family) == SOURCE_SERIF_4_FONT_FAMILY:
        return str(get_face(SOURCE_SERIF_4_FONT_FAMILY).measure_path)

    if numeric:
        return str(get_face(DBT_SANS_TABULAR_FONT_FAMILY).measure_path)

    return str(get_face(INTER_VARIABLE_FONT_FAMILY).measure_path)


def get_mono_font_path() -> str:
    """Return the monospace file used for strict code measurement."""
    return str(get_face(SOURCE_CODE_PRO_FONT_FAMILY).measure_path)


FONT_FACE_CSS_HEADER = (
    "/* Generated from dbt_charts.core.fonts.FONT_REGISTRY — regenerate with "
    "`just gen-font-face-css`. Do not edit by hand. */\n"
)


def served_faces() -> tuple[VendoredFace, ...]:
    """Every registry row a browser can download, in registry order.

    The complement of the measure-only rows (``web_file`` is ``None``) — the mono
    face and the static weight instances vl-convert needs. What
    ``/static/fonts/_font_face.css`` declares, and the candidate set any narrower
    per-board selection draws from.
    """
    return tuple(face for face in FONT_REGISTRY if face.web_file is not None)


STATIC_FONT_URL_PREFIX = "/static/fonts"


def render_font_face_css(faces: Iterable[VendoredFace]) -> str:
    """Emit ``@font-face`` declarations naming each file by URL, for a host to serve.

    Generated from ``FONT_REGISTRY`` rather than hand-maintained, so the file the
    browser downloads cannot drift from the file we measured to place the text.

    Pass ``served_faces()`` for the shared stylesheet, which is one file for every
    board and so declares everything. Narrowing it is cheap either way here — the
    browser fetches only the faces it resolves, so a spare declaration costs one
    text block and no download. ``render_embedded_font_face_css`` is where that
    stops being true and the set starts mattering.

    Returns the declarations alone. ``FONT_FACE_CSS_HEADER`` is prepended only when
    writing the committed stylesheet — this text is also inlined into every rendered
    board's ``<style>`` block, and a provenance comment has no business shipping there.
    """
    return _font_face_css(
        faces, lambda face: f"url('{STATIC_FONT_URL_PREFIX}/{face.web_file}')"
    )


def render_embedded_font_face_css(faces: Iterable[VendoredFace]) -> str:
    """Emit ``@font-face`` blocks carrying the font bytes inline, as ``data:`` URIs.

    For an artifact that has no host to fetch from: a standalone HTML export opened
    from disk, mailed, or dropped in a bucket. ``/static/fonts/…`` is root-relative
    and resolves only while a server is running, so an export that names it paints in
    fallback type — laid out for wrap points measured against a font it isn't using.

    Takes an explicit face set rather than iterating the registry, because here every
    listed face is paid for in bytes whether the board paints with it or not.
    """
    return _font_face_css(faces, _embedded_src)


@cache
def _embedded_src(face: VendoredFace) -> str:
    """The ``src`` value for a face carried inline: a ``data:`` URI of its bytes.

    Container, MIME, and payload all come from one read of ``web_file``. They were
    briefly two functions and the MIME won the evaluation race, turning a
    measure-only face's clear refusal into a bare assertion — and a MIME that
    disagrees with the ``format()`` token is silent besides: the browser discards
    the face and paints the next in the stack.
    """
    if face.web_file is None:
        raise ValueError(
            f"{face.family} ({face.style}) is not served, so it has no bytes to "
            "embed. Measure-only faces are painted from the client's own font "
            "stack; see VendoredFace.web_file."
        )
    # Cached because the answer cannot change: these are vendored package files,
    # read-only for the life of the process. Uncached, a board that exports 20 faces
    # re-read and re-encoded ~1.4 MiB twenty times — measured at ~660 ms the first
    # time and ~1 ms once the OS page cache is warm, so the cache is worth having for
    # the cold path alone. Nothing caches in URL mode, which never calls this.
    mime = "font/woff2" if face.web_file.endswith(".woff2") else "font/ttf"
    payload = base64.b64encode((get_fonts_dir() / face.web_file).read_bytes()).decode(
        "ascii"
    )
    return f"url('data:{mime};base64,{payload}')"


def _font_face_css(
    faces: Iterable[VendoredFace], src: Callable[[VendoredFace], str]
) -> str:
    """Render ``@font-face`` declarations, ``src`` deciding how each file is named.

    No comment of any kind is emitted: a ``/*`` trips the ban on CSS comments in
    exported SVG, and a Jinja comment would reach the browser literally.
    """
    blocks = []
    for face in faces:
        lines = [
            "@font-face {",
            f"  font-family: '{face.family}';",
            f"  src: {src(face)} format('{_css_format(face)}');",
        ]
        if face.weight_range is not None:
            lines.append(
                f"  font-weight: {face.weight_range[0]} {face.weight_range[1]};"
            )
        if face.style != "normal":
            lines.append(f"  font-style: {face.style};")
        elif face.weight_range is not None:
            lines.append("  font-style: normal;")
        lines.append("  font-display: swap;")
        if face.unicode_range:
            ranges = ",\n    ".join(face.unicode_range)
            lines.append(f"  unicode-range:\n    {ranges};")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks) + "\n"


def _css_format(face: VendoredFace) -> str:
    """CSS ``format()`` hint: container, plus ``-variations`` for variable faces.

    Every served face is woff2 today; the container is still read off the suffix rather
    than assumed, because getting this token wrong is silent — a `woff2` hint on a
    truetype file makes the browser discard the face and paint the next in the stack.
    """
    if face.web_file is None:
        raise ValueError(
            f"{face.family} ({face.style}) is not served; it has no format"
        )
    container = "woff2" if face.web_file.endswith(".woff2") else "truetype"
    return f"{container}-variations" if face.weight_range is not None else container

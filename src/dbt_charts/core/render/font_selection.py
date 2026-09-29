"""Which vendored faces a board paints with — the set an offline export carries.

Stage: RENDER
Purpose: Decide the ``@font-face`` set for one board's rendered SVG.

A board rendered for a live host names its fonts at ``/static/fonts/…`` and the
browser fetches the two or three it needs, so declaring the whole registry there
costs one text block per unused face and nothing else. An exported file has no host
to ask and carries the bytes instead — and embedded bytes are paid for whether they
are painted or not, so that set has to be what the board actually uses.

Two questions decide a face:

**Which families does this board name?** The ``font-family`` stacks in the emitted
SVG are the entire vocabulary a viewer resolves against, so a family absent from
them cannot be painted and every family present in them can be. Reading the emitted
markup answers this without an accumulator that could miss an emission site — and
missing one would mean an export that silently paints in fallback type, which is the
defect this whole area exists to close. It also makes emoji fall out for free: the
emoji family is inserted into the stacks only in ``monochrome`` mode.

**Which italic faces got painted?** Italic reaches a board three ways and only one of
them is a face mdsvg measured against, so it takes two readings to see all three:

*Declared italic* — a theme style (``text.blockquote.font.style: italic``, which every
built-in theme inherits from ``stark``), an authored ``conditional_formatting``, or a
renderer writing italic ``<text>`` directly (a truncated table's "+ N more rows", a
spark_bar's "+ N more", a callout's hint line). In every one of those the family and
``font-style: italic`` land in the *same declaration scope* — one start tag, or one CSS
rule body — so ``italic_families_in`` pairs them off the finished markup. Reading the
markup is what makes this work for a callout, which renders during the sizing pass and
is replayed from cache in the main one, where no render-time sink can see it.

*Consumed italic* — markdown emphasis, which mdsvg paints as an inline ``<tspan>`` that
inherits its family from an ancestor. Nothing in that tspan says which family went
italic, so pairing cannot help and the sink below carries it instead:
``SVGRenderer.used_faces`` reports the faces a render actually reached, recorded
against the family the caller handed it.

Two cases stay unbacked, both deliberately. The tooltip's "+ N more" overflow row
(``templates/scripts/chart_interactivity.js``) is painted by the hover runtime, so
render time cannot know whether a reader will ever trigger it — backing it would cost
the board sans italic on every export for a row that may never appear. Emphasis inside
a callout *message* is the other: ``_callout_md_renderer`` measures from a bare
``font_path`` rather than a face set, so mdsvg has no italic face to report having used,
and the emphasis is measured against the roman today regardless. Both fall to a
browser-synthesized oblique, which is metrically harmless — neither is laid out against
our measurements.
"""

from __future__ import annotations

import contextvars
import html
import re
from collections.abc import Generator, Set
from contextlib import contextmanager

from dbt_charts.core.fonts import (
    NOTO_EMOJI_FONT_FAMILY,
    VendoredFace,
    registry_family,
    render_embedded_font_face_css,
    render_font_face_css,
    served_faces,
)

_sinks: contextvars.ContextVar[tuple[set[str], ...]] = contextvars.ContextVar(
    "painted_italic_family_sinks", default=()
)


@contextmanager
def collect_painted_italic_families() -> Generator[set[str]]:
    """Open a fresh sink; yield the families whose italic face got painted.

    ``record_painted_faces`` writes into the innermost open sink while the context
    is open. Nested opens do not leak — each yields its own set.
    """
    collected: set[str] = set()
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def record_painted_faces(font_family: str, used_faces: Set[str]) -> None:
    """Record one mdsvg render's consumed faces against the family it painted.

    ``used_faces`` is ``SVGRenderer.used_faces``. Only italic is kept, because it is
    the only one this sink has to carry. ``regular`` and ``bold`` are one variable
    woff2 at two weights, so the roman row covers both. ``mono`` is a served face now
    that the code stack leads with Source Code Pro, but it needs no sink: mdsvg names
    that family in the CSS class rule it emits for code, so the family scan sees it
    the same way it sees any other stack.

    No-op when no sink is open, which is every render not being packaged for offline
    use.
    """
    sinks = _sinks.get()
    if not sinks or not used_faces & {"italic", "bold_italic"}:
        return
    sinks[-1].add(registry_family(font_family))


def italic_sink_is_open() -> bool:
    """Whether anything is collecting painted italics right now.

    Lets a caller that *needs* the answer tell "no italics were painted" apart from
    "nobody was listening" — two states ``painted_italic_families`` cannot
    distinguish, and which differ by a whole font file in the output.
    """
    return bool(_sinks.get())


def painted_italic_families() -> Set[str]:
    """The families whose italic face has been painted, per the innermost sink.

    Empty when no sink is open — a render nobody is packaging fonts for.
    """
    sinks = _sinks.get()
    return frozenset(sinks[-1]) if sinks else frozenset()


# Both patterns require the italic *inside* the scope, so a scan never walks the
# thousands of tags and rules that have nothing to do with fonts. Enumerating every
# start tag and testing each one cost 20s on a 400 KiB board.
# The italic spelling both scope patterns embed, exposed so a guard test can hold
# every renderer to the same one rather than to a narrower literal.
DECLARES_ITALIC = r"font-style\s*[:=]\s*[\"']?\s*italic"
_ITALIC_TAG = re.compile(rf"<[^<>]*{DECLARES_ITALIC}[^<>]*>")
_ITALIC_CLASS_RULE = re.compile(
    rf"\.([\w-]+)[^{{}}]*\{{[^{{}}]*{DECLARES_ITALIC}[^{{}}]*\}}"
)
_CLASS_ATTR = re.compile(r'class="([^"]*)"')
_STYLE_BLOCK = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL)
# Skips a `&#x27;`-style character reference as one unit — its own trailing
# `;` would otherwise look like the declaration's terminator.
_ENTITY_OR_CHAR = r"(?:&#?\w+;|[^;}])"
_DECLARES_FAMILY = re.compile(
    rf"font-family\s*[:=]\s*(\"[^\"]*\"|'[^']*'|{_ENTITY_OR_CHAR}+)"
)


def _declared_family(scope: str) -> frozenset[str]:
    """The family named in a scope already known to declare italic."""
    family = _DECLARES_FAMILY.search(scope)
    if family is None:
        return frozenset()
    # Renderers that escape their attributes write the stack's quotes as
    # entities (`&#x27;Inter Variable&#x27;`), which no registry family matches.
    return frozenset({registry_family(html.unescape(family.group(1)))})


def italic_families_in(painted_markup: str) -> frozenset[str]:
    """Families whose italic is declared next to them, scope by scope.

    Only a scope carrying *both* the family and the italic counts. That is what
    keeps the answer specific — searching the whole document for
    ``font-style: italic`` finds every emphasis run and cannot say whose italic it
    was.

    A CSS rule additionally has to be *worn* by something. mdsvg emits its
    ``.md-blockquote`` rule into the style block of every prose render whether or
    not the text contains a blockquote, so trusting the rule's presence alone would
    embed the serif italic in every export that has prose at all.
    """
    if "italic" not in painted_markup:
        return frozenset()

    families: set[str] = set()
    for tag in _ITALIC_TAG.findall(painted_markup):
        families |= _declared_family(tag)

    # Only inside <style>, which is the only place a CSS rule can live — and the
    # reason this matters is cost, not tidiness: `[^{}]*` scanning a board with few
    # braces will happily walk the whole document from every `.` in it, and a table
    # of decimals offers thousands of those. Restricting the pattern to style
    # blocks took a 400 KiB board's scan from 20s to milliseconds.
    stylesheets = "\n".join(_STYLE_BLOCK.findall(painted_markup))
    rules = list(_ITALIC_CLASS_RULE.finditer(stylesheets))
    if rules:
        # Collected once. Re-scanning the whole board per rule to ask whether its
        # class is worn is the other half of what made this quadratic.
        worn = {
            name
            for attr in _CLASS_ATTR.findall(painted_markup)
            for name in attr.split()
        }
        for rule in rules:
            if rule.group(1) in worn:
                families |= _declared_family(rule.group(0))
    return frozenset(families)


def board_font_faces(
    painted_markup: str, painted_italic_families: Set[str]
) -> tuple[VendoredFace, ...]:
    """The served faces ``painted_markup`` needs, in registry order.

    ``painted_markup`` is everything the board paints text through — its content
    SVG plus the font stacks the page itself applies. A face joins the set when its
    family is named there; an italic row additionally needs that family to have gone
    italic somewhere, either declared in the markup or reported by mdsvg.
    """
    italic = frozenset(painted_italic_families) | italic_families_in(painted_markup)
    return tuple(
        face
        for face in served_faces()
        if face.family in painted_markup
        and (face.style != "italic" or face.family in italic)
    )


def board_font_face_css(
    painted_markup: str,
    emoji_mode: str,
    painted_italic_families: Set[str],
    *,
    embed: bool,
) -> str:
    """One board's ``@font-face`` block: URLs for a host to serve, or bytes inline.

    URL mode declares every served face whatever this board paints — the browser
    fetches only the faces it resolves, so a spare declaration costs a text block
    and no download, and one shape for every board is one less thing to differ.
    Embed mode pays for every byte it lists, so it lists what this board paints.

    The emoji face is the one row a mode withdraws. It enters a font stack only
    under ``monochrome``; under ``system-default`` the reader's own emoji font
    paints, and under ``disabled`` nothing does — so in both there is nothing for a
    declaration of ours to serve. Embed mode gets that for free, since a family no
    stack names is a family ``board_font_faces`` does not see.
    """
    if embed:
        return render_embedded_font_face_css(
            board_font_faces(painted_markup, painted_italic_families)
        )
    return render_font_face_css(
        face
        for face in served_faces()
        if face.family != NOTO_EMOJI_FONT_FAMILY or emoji_mode == "monochrome"
    )

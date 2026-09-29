"""Render-time contrast check + capture: WARN-LOW-TEXT-CONTRAST.

Checks a text block's heading and body ink against the background it paints
on. Call ``check_text_contrast`` (or the markdown-aware
``check_markdown_contrast``) wherever a resolved font is about to paint,
passing the ink and the opaque background it paints on. It computes WCAG 2.1
contrast and — only when the pair misses the configured floor — records a
``ContrastRecord`` into the open sink. ``render/warnings/low_text_contrast.py``
turns each record into a ``Diagnostic`` once the render pass finishes.

Mirrors ``render/chart/text_truncation.py``'s sink pattern: a no-op when no
sink is open (e.g. a synthetic re-render outside the warning-collection
pass). One flat list rather than a chart-keyed dict, since a low-contrast
pair comes from a board-level text block with no chart id.

``composite_over_canvas`` computes the background a text block actually
paints on: an item's own resolved ``style.background`` composited over its
parent's own painted canvas, walked from the board down. Callers thread the
result down the layout walk (``render/boards.py``) — this module has no
access to the layout tree itself.

``kind`` names the element that painted the pair; ``_REMEDY_KEYS`` is the
YAML key that governs that element's ink: ``text_heading`` names
``style.title.font.color`` and ``text_body`` names ``style.font.color``.
"""

from __future__ import annotations

import contextvars
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from dbt_charts.core.colors import (
    InvalidColorError,
    composite_over,
    parse_css_color,
    rgb01_to_hex,
    wcag_contrast,
)
from dbt_charts.core.compile.config import get_chart_rendering

if TYPE_CHECKING:
    from dbt_charts.core.compile.models.style.resolved import ResolvedStyle

ContrastKind = Literal["text_heading", "text_body"]

_REMEDY_KEYS: dict[ContrastKind, str] = {
    "text_heading": "style.title.font.color",
    "text_body": "style.font.color",
}

_ELEMENT_NAMES: dict[ContrastKind, str] = {
    "text_heading": "Heading",
    "text_body": "Body text",
}

# WCAG's own "large text" exception: >=24px (18pt) at any weight, or
# >=18.67px (14pt) bold. The normal floor applies below this size; large
# text gets the more permissive floor (both configurable — see
# compile/models/config.py's TextContrastConfig).
_LARGE_TEXT_PX = 24.0
_LARGE_BOLD_TEXT_PX = 18.6667
_BOLD_WEIGHTS = frozenset({"bold", "700", "800", "900"})


class ContrastRecord(BaseModel):
    """One text/background pair whose contrast missed the configured floor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ContrastKind
    ink: str  # resolved 6-digit hex
    background: str  # resolved 6-digit hex
    ratio: float
    floor: float
    large_text: bool
    path: str | None = None

    @property
    def element(self) -> str:
        return _ELEMENT_NAMES[self.kind]

    @property
    def key(self) -> str:
        return _REMEDY_KEYS[self.kind]


_sinks: contextvars.ContextVar[tuple[list[ContrastRecord], ...]] = (
    contextvars.ContextVar("contrast_warning_sinks", default=())
)


@contextmanager
def collect_contrast_warnings() -> Generator[list[ContrastRecord]]:
    """Open a fresh sink; yield the collected list of low-contrast pairs.

    ``check_text_contrast`` writes into the innermost open sink while the
    context is open. Nested opens do not leak — each yields its own list.
    """
    collected: list[ContrastRecord] = []
    token = _sinks.set((*_sinks.get(), collected))
    try:
        yield collected
    finally:
        _sinks.reset(token)


def _ink_hex_over(ink: str, background_hex: str) -> str | None:
    """``ink``'s effective opaque hex once painted over the fully-opaque
    ``background_hex`` — composited via ``composite_over`` when ``ink``
    carries its own alpha, passed straight through when it is already
    opaque. None when ``ink`` is unparseable or fully transparent (nothing
    painted, so there is no ink to check)."""
    try:
        r, g, b, a = parse_css_color(ink)
    except InvalidColorError:
        return None
    if a <= 0.0:
        return None
    if a >= 1.0:
        return rgb01_to_hex(r, g, b)
    bg_r, bg_g, bg_b, _bg_a = parse_css_color(background_hex)
    composited = composite_over((r, g, b, a), (bg_r, bg_g, bg_b, 1.0))
    return rgb01_to_hex(*composited[:3])


def composite_over_canvas(background: str, parent_canvas: str | None) -> str | None:
    """The opaque canvas a layer painting ``background`` leaves behind for
    whatever it contains, given ``parent_canvas`` (the same fact for
    whatever is behind this layer, or None when that is itself unknown).

    A ``background`` this engine's color parser cannot read (a pattern,
    gradient, or ``url()`` reference) makes the result unknown from this
    layer down — there is no basis to assume either its own paint or
    whatever is behind it, so this returns None rather than guessing.
    A fully opaque ``background`` fully determines the result regardless of
    ``parent_canvas``. Anything in between (transparent, or a partial alpha)
    composites over ``parent_canvas`` via ``composite_over``, and is None
    when ``parent_canvas`` is itself None.
    """
    try:
        r, g, b, a = parse_css_color(background)
    except InvalidColorError:
        return None
    if a >= 1.0:
        return rgb01_to_hex(r, g, b)
    if parent_canvas is None:
        return None
    under_r, under_g, under_b, _under_a = parse_css_color(parent_canvas)
    composited = composite_over((r, g, b, a), (under_r, under_g, under_b, 1.0))
    return rgb01_to_hex(*composited[:3])


def is_large_text(font_size: float, font_weight: float | str | None) -> bool:
    """WCAG "large text": >=24px at any weight, or >=18.67px (14pt) bold."""
    if font_size >= _LARGE_TEXT_PX:
        return True
    if font_size < _LARGE_BOLD_TEXT_PX:
        return False
    if isinstance(font_weight, (int, float)):
        return font_weight >= 700
    return isinstance(font_weight, str) and font_weight.strip().lower() in _BOLD_WEIGHTS


def check_text_contrast(
    kind: ContrastKind,
    ink: str,
    background: str | None,
    font_size: float,
    font_weight: float | str | None = None,
    *,
    path: str | None = None,
) -> None:
    """Record a low-contrast pair when ``ink`` painted on ``background`` misses
    the configured WCAG floor.

    ``background``, when not None, is already a fully opaque hex color —
    every real caller reaches this through ``composite_over_canvas``, which
    only ever returns that or None. No-op when no sink is open, when
    ``background`` is None, or when ``ink`` is unparseable or fully
    transparent.
    """
    sinks = _sinks.get()
    if not sinks:
        return
    if background is None:
        return
    bg_hex = background
    ink_hex = _ink_hex_over(ink, bg_hex)
    if ink_hex is None:
        return
    ratio = wcag_contrast(ink_hex, bg_hex)
    contrast_cfg = get_chart_rendering().text_contrast
    large_text = is_large_text(font_size, font_weight)
    floor = contrast_cfg.large_text_min_ratio if large_text else contrast_cfg.min_ratio
    if ratio >= floor:
        return
    sinks[-1].append(
        ContrastRecord(
            kind=kind,
            ink=ink_hex,
            background=bg_hex,
            ratio=ratio,
            floor=floor,
            large_text=large_text,
            path=path,
        )
    )


def check_markdown_contrast(
    markdown_text: str,
    resolved_style: ResolvedStyle,
    background: str | None,
    *,
    path: str | None = None,
) -> None:
    """Check heading and body ink a markdown block paints against ``background``.

    mdsvg scopes one CSS class to each role (``.md-<hash>-heading``,
    ``.md-<hash>-text``), so every heading in the block shares one ink and
    every paragraph/list/table/raw-HTML block shares another — this is
    exactly one contrast check per role, however many headings or paragraphs
    the block has.

    The heading check uses the SMALLEST size among the heading levels
    actually present: WCAG's large-text exception is a floor per font size,
    and a block mixing an authored H1 with an H4 in the same ink is only as
    readable as its smallest heading. Callers must already have validated
    ``resolved_style.title.sizes`` has 6 entries (rendering the block does
    this) — ``sizes[level - 1]`` indexes it directly.

    No-op (no work at all, including the markdown parse) when no sink is
    open or ``background`` is None — the caller (``composite_over_canvas``)
    has already decided the painted canvas cannot be determined.
    """
    if not _sinks.get():
        return
    if background is None:
        return

    from mdsvg import (
        Heading,
        OrderedList,
        Paragraph,
        RawHtmlBlock,
        Table,
        UnorderedList,
        parse as parse_markdown,
    )

    heading_levels: list[int] = []
    has_body = False
    for block in parse_markdown(markdown_text):
        if isinstance(block, Heading):
            heading_levels.append(block.level)
        elif isinstance(
            block, (Paragraph, UnorderedList, OrderedList, Table, RawHtmlBlock)
        ):
            has_body = True

    if heading_levels:
        sizes = resolved_style.title.sizes
        heading_size = min(sizes[level - 1] for level in heading_levels)
        heading_color = resolved_style.title.font.color
        assert heading_color is not None, (
            "cascade should populate style.title.font.color"
        )
        check_text_contrast(
            "text_heading",
            heading_color,
            background,
            heading_size,
            resolved_style.title.font.weight,
            path=path,
        )
    if has_body:
        body_color = resolved_style.text.font.color
        body_size = resolved_style.text.font.size
        assert body_color is not None, "cascade should populate style.font.color"
        assert body_size is not None, "cascade should populate style.text.font.size"
        check_text_contrast(
            "text_body",
            body_color,
            background,
            body_size,
            resolved_style.text.font.weight,
            path=path,
        )

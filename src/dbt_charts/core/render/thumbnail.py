"""Abstract a finished board SVG into a recognizable-but-unreadable thumbnail.

The transform is SVG -> SVG and never consults the board: every ``<text>``
becomes a rounded placeholder rect sized by the same font measurement the
renderer laid the text out with, chart furniture (axis labels, ticks, grid,
legends, the footer link) is dropped, variable controls collapse to one pill
each, and tables are redrawn as per-column blocks plus value bars. Data marks
are left exactly as drawn.

Tables are recognized by the ``data-chart-type="table"`` hook on the chart
wrapper; cells carry ``data-col`` (column index) and numeric cells
``data-value`` (the raw number), so no formatted string is ever re-parsed.
Text with no font-family of its own (tab labels, ``details`` disclosures) is
measured in the typeface the root declares in ``data-dbt-font-family``, and text
the renderer set in the tabular numeric typeface is measured in it too.

Tuning knobs live in ``chart_rendering.thumbnail`` (``default_config.yml``).
"""

from __future__ import annotations

import functools
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from dbt_charts.core.colors import (
    InvalidColorError,
    composite_over,
    is_light_canvas,
    parse_css_color,
    relative_luminance,
    rgb01_to_hex,
)
from dbt_charts.core.compile.config import get_chart_rendering
from dbt_charts.core.diagnostics import ERR_INPUT_INVALID
from dbt_charts.core.font_measure import (
    FontMeasurer,
    get_font_measurer,
    get_weighted_font_measurer,
)
from dbt_charts.core.fonts import DBT_SANS_TABULAR_FONT_FAMILY, registry_family
from dbt_charts.core.render.errors import RenderError

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
# Without these, ElementTree serializes the SVG namespace as ``ns0:`` and
# ``xlink:href`` as ``ns1:href``.
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)

Rgb = tuple[float, float, float]

_REMOVED_CLASSES = frozenset(
    {"role-axis-label", "role-axis-tick", "role-axis-grid", "role-legend"}
)
_FOOTER_CLASS = "dbt-footer-link"
_A11Y_ATTRS = ("aria-label", "aria-roledescription", "aria-hidden", "role")
# Attributes that carry the board's own words, which a thumbnail must not keep.
_PROSE_ATTRS = (
    "data-chart-title",
    "data-chart-notes",
    "data-dbt-page-title",
    "data-dbt-series",
)
_ANCHORS = ("start", "middle", "end")
_FONT_WEIGHT_KEYWORDS = {"normal": 400, "bold": 700}
_FONT_FACE = re.compile(r"@font-face\s*\{[^{}]*\}")
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CLASS_SELECTOR = re.compile(r"(?:text|tspan)?\.([\w-]+)")
_LENGTH = re.compile(r"\s*(-?\d+(?:\.\d+)?|-?\.\d+)(px|em)?\s*")


@dataclass(frozen=True)
class _Paint:
    color: str
    # Set only on a transparent page: the ink's share, left to the host canvas.
    opacity: float | None = None


def cartoonize(svg: str) -> str:
    """Return ``svg`` redrawn as an abstract thumbnail: same size, no text, no fonts.

    Placeholders are one color: ``placeholder_ink`` of the page's ink mixed into
    the page background, read from the root's ``data-dbt-page-background``. Ink
    is black on a light page and white on a dark one (``is_light_canvas``). A
    transparent (or partially transparent) page has no known canvas, so placeholders, bars, and status blocks are
    drawn as black ink (or the source color) at the equivalent ``fill-opacity``
    and blend into whatever the host paints behind.

    Raises:
        RenderError: the SVG has no readable ``data-dbt-page-background`` (every
            board render carries one), is not well-formed XML, or carries text
            or hooks this module cannot read.
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise _invalid(f"cartoonize input is not well-formed XML: {exc}") from exc
    background = root.get("data-dbt-page-background")
    if background is None:
        raise _invalid("cartoonize needs the root's data-dbt-page-background attribute")
    try:
        r, g, b, alpha = parse_css_color(background)
    except InvalidColorError as exc:
        raise _invalid(f"unreadable data-dbt-page-background {background!r}") from exc
    ctx = _Ctx(root, (r, g, b) if alpha >= 1.0 else None)

    _remove_furniture(ctx)
    _replace_variable_controls(ctx)
    for chart in [e for e in root.iter() if e.get("data-chart-type") == "table"]:
        _redraw_table(chart, ctx)
    for text in [e for e in root.iter() if _local(e.tag) == "text"]:
        lines = _lines(text, ctx)
        ctx.replace(
            text,
            [
                _text_rect(line, line.left(), line.right(), ctx.placeholder, text, ctx)
                for line in lines
            ],
        )
    _strip_fonts(ctx)
    _strip_inert(ctx)
    return ET.tostring(root, encoding="unicode")


# ---- document context -------------------------------------------------------


class _Ctx:
    def __init__(self, root: ET.Element, canvas: Rgb | None) -> None:
        self.root = root
        self.canvas = canvas
        self.config = get_chart_rendering().thumbnail
        self.ink: Rgb = (
            (0.0, 0.0, 0.0)
            if canvas is None or is_light_canvas(rgb01_to_hex(*canvas))
            else (1.0, 1.0, 1.0)
        )
        self.placeholder = self.tint(self.ink, self.config.placeholder_ink)
        self.rules = _class_rules(root)
        self.parents = _parent_map(root)
        self.bar_color = self._bar_color()

    def tint(self, color: Rgb, weight: float) -> _Paint:
        """``color`` at ``weight`` over the page; translucent when the page is not known."""
        if self.canvas is None:
            return _Paint(rgb01_to_hex(*color), weight)
        r, g, b, _ = composite_over((*color, weight), (*self.canvas, 1.0))
        return _Paint(rgb01_to_hex(r, g, b))

    def refresh(self) -> None:
        self.parents = _parent_map(self.root)

    def replace(self, el: ET.Element, new: list[ET.Element]) -> None:
        parent = self.parents[el]
        at = list(parent).index(el)
        parent.remove(el)
        for offset, child in enumerate(new):
            parent.insert(at + offset, child)

    def prop(self, el: ET.Element, name: str) -> str | None:
        """CSS-cascade lookup: inline style, then class rules, then the attribute,
        walking up through ancestors (every property read here inherits)."""
        node: ET.Element | None = el
        while node is not None:
            inline = _declarations(node.get("style")).get(name)
            if inline is not None:
                return inline
            for cls in _classes(node):
                declared = self.rules.get(cls)
                if declared is not None and name in declared:
                    return declared[name]
            attr = node.get(name)
            if attr is not None:
                return attr
            node = self.parents.get(node)
        return None

    def _bar_color(self) -> _Paint:
        """The board's darkest saturated mark color blended toward the page, or
        the placeholder gray when no mark is saturated."""
        marks: list[Rgb] = []
        for group in self.root.iter():
            if "role-mark" not in _classes(group):
                continue
            for mark in group.iter():
                if _local(mark.tag) not in ("path", "rect"):
                    continue
                for attr in ("fill", "stroke"):
                    color = _color(mark.get(attr))
                    if color is not None and self.chromatic(color):
                        marks.append(color)
        if not marks:
            return self.placeholder
        darkest = min(marks, key=lambda c: relative_luminance(rgb01_to_hex(*c)))
        return self.tint(darkest, self.config.bar_color_weight)

    def chromatic(self, color: Rgb) -> bool:
        top = max(color)
        saturation = (top - min(color)) / top if top else 0.0
        return saturation > self.config.chromatic_saturation


def _invalid(message: str) -> RenderError:
    return RenderError.from_code(ERR_INPUT_INVALID, message=message)


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _classes(el: ET.Element) -> list[str]:
    cls = el.get("class")
    return cls.split() if cls else []


@functools.lru_cache(maxsize=4096)
def _declarations(css: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if css:
        for part in css.split(";"):
            key, _, value = part.partition(":")
            if value:
                out[key.strip()] = value.strip()
    return out


def _class_rules(root: ET.Element) -> dict[str, dict[str, str]]:
    """Declarations from inline ``<style>`` rules whose selector is one class
    (optionally qualified by ``text``/``tspan``); anything more specific is not
    used by the text this module measures."""
    rules: dict[str, dict[str, str]] = {}
    for style in root.iter(f"{{{SVG_NS}}}style"):
        if not style.text:
            continue
        css = _CSS_COMMENT.sub("", _FONT_FACE.sub("", style.text))
        for selectors, body in _CSS_RULE.findall(css):
            for selector in selectors.split(","):
                match = _CLASS_SELECTOR.fullmatch(selector.strip())
                if match:
                    rules.setdefault(match[1], {}).update(_declarations(body))
    return rules


# ---- colors -----------------------------------------------------------------


def _color(raw: str | None) -> Rgb | None:
    """The opaque color a paint attribute names, or None for anything that is
    not one (absent, ``none``/``transparent``, ``url(#gradient)``, ``currentColor``)."""
    if raw is None:
        return None
    try:
        r, g, b, alpha = parse_css_color(raw)
    except InvalidColorError:
        return None
    return (r, g, b) if alpha > 0 else None


def _number(raw: str, what: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = math.nan
    if not math.isfinite(value):
        raise _invalid(f"{what} is not a finite number: {raw!r}")
    return value


# ---- text geometry ----------------------------------------------------------


def _length(value: str, size: float | None) -> float:
    match = _LENGTH.fullmatch(value)
    if match is None:
        raise _invalid(f"unsupported SVG length {value!r}")
    number = float(match[1])
    if match[2] != "em":
        return number
    if size is None:
        raise _invalid(f"em length {value!r} on text with no font-size")
    return number * size


def _first_length(value: str | None, size: float | None) -> float | None:
    if value is None:
        return None
    return _length(value.replace(",", " ").split()[0], size)


@dataclass
class _Run:
    left: float
    right: float
    # A tspan painted in its own color (a conditional-format glyph).
    glyph_fill: str | None

    @property
    def glyph(self) -> bool:
        return self.glyph_fill is not None


@dataclass
class _Line:
    runs: list[_Run]
    baseline: float
    size: float
    ascent: float
    descent: float
    anchor: str

    def _span(self, glyphs: bool) -> list[_Run]:
        return [r for r in self.runs if glyphs or not r.glyph]

    def left(self, glyphs: bool = True) -> float:
        return min(r.left for r in self._span(glyphs))

    def right(self, glyphs: bool = True) -> float:
        return max(r.right for r in self._span(glyphs))

    @property
    def box(self) -> float:
        return self.ascent + self.descent

    @property
    def center(self) -> float:
        return self.baseline - (self.ascent - self.descent) / 2


@dataclass
class _Part:
    text: str
    lead: float
    y: float
    size: float
    family: str
    weight: int
    glyph_fill: str | None


@dataclass
class _Chunk:
    x: float
    anchor: str
    parts: list[_Part]


def _events(el: ET.Element) -> list[tuple[ET.Element, str | None]]:
    """Document-order text events: ``(owner, string)``, with ``(tspan, None)``
    marking where a tspan begins."""
    out: list[tuple[ET.Element, str | None]] = []
    if el.text:
        out.append((el, el.text))
    for child in el:
        if _local(child.tag) == "tspan":
            out.append((child, None))
            out.extend(_events(child))
        if child.tail:
            out.append((el, child.tail))
    return out


def _lines(text: ET.Element, ctx: _Ctx) -> list[_Line]:
    """The text's visual lines with every run placed, in the text's own coordinates.

    Follows SVG text chunks: a tspan with an absolute ``x`` starts a new chunk,
    and each chunk is anchored (start/middle/end) by its first run.
    """
    size0 = _declared_size(text, ctx)
    x = _first_length(text.get("x"), size0)
    y = _first_length(text.get("y"), size0)
    x = 0.0 if x is None else x
    y = 0.0 if y is None else y
    dy = _first_length(text.get("dy"), size0)
    y += 0.0 if dy is None else dy
    dx0 = _first_length(text.get("dx"), size0)
    pending_dx = 0.0 if dx0 is None else dx0

    chunks: list[_Chunk] = []
    new_chunk = True
    for owner, raw in _events(text):
        if raw is None:
            size = _declared_size(owner, ctx)
            nx = _first_length(owner.get("x"), size)
            if nx is not None:
                x, new_chunk = nx, True
            ny = _first_length(owner.get("y"), size)
            if ny is not None:
                y = ny
            ody = _first_length(owner.get("dy"), size)
            y += 0.0 if ody is None else ody
            odx = _first_length(owner.get("dx"), size)
            pending_dx += 0.0 if odx is None else odx
            continue
        collapsed = re.sub(r"\s+", " ", raw)
        if new_chunk:
            collapsed = collapsed.lstrip()
        if not collapsed:
            continue
        size = _size(owner, ctx)
        if new_chunk:
            anchor = ctx.prop(owner, "text-anchor")
            if anchor is None:
                anchor = "start"
            if anchor not in _ANCHORS:
                raise _invalid(f"unsupported text-anchor {anchor!r}")
            chunks.append(_Chunk(x, anchor, []))
            new_chunk = False
        chunks[-1].parts.append(
            _Part(
                collapsed,
                pending_dx,
                y,
                size,
                _family(owner, ctx),
                _weight(owner, ctx),
                None if owner is text else owner.get("fill"),
            )
        )
        pending_dx = 0.0

    placed: list[tuple[float, _Chunk, list[_Run], _Part]] = []
    for chunk in chunks:
        chunk.parts[-1].text = chunk.parts[-1].text.rstrip()
        parts = [p for p in chunk.parts if p.text]
        widths = [_measurer(p.family, p.weight).measure(p.text, p.size) for p in parts]
        total = sum(p.lead + w for p, w in zip(parts, widths, strict=True))
        if total <= 0:
            continue
        offset = {"start": 0.0, "middle": total / 2, "end": total}[chunk.anchor]
        cursor = chunk.x - offset
        runs: list[_Run] = []
        for part, width in zip(parts, widths, strict=True):
            cursor += part.lead
            runs.append(_Run(cursor, cursor + width, part.glyph_fill))
            cursor += width
        main = max(parts, key=lambda p: p.size)
        placed.append((main.y, chunk, runs, main))

    lines: list[_Line] = []
    for baseline, chunk, runs, main in sorted(placed, key=lambda p: p[0]):
        last = lines[-1] if lines else None
        if last is not None and abs(baseline - last.baseline) <= (
            ctx.config.same_line_share * max(main.size, last.size)
        ):
            last.runs.extend(runs)
            if main.size > last.size:
                last.baseline, last.size = baseline, main.size
                last.ascent, last.descent = _metrics(main)
            continue
        ascent, descent = _metrics(main)
        lines.append(_Line(runs, baseline, main.size, ascent, descent, chunk.anchor))
    return lines


def _measurer(family: str, weight: int) -> FontMeasurer:
    """The measurer the renderer laid this text out with: the tabular numeric
    typeface for the stack it emits on numeric table cells, the weighted body typeface
    otherwise."""
    if registry_family(family) == DBT_SANS_TABULAR_FONT_FAMILY:
        return get_font_measurer(family, numeric=True)
    return get_weighted_font_measurer(family, weight)


def _metrics(part: _Part) -> tuple[float, float]:
    measurer = _measurer(part.family, part.weight)
    return measurer.ascent_em * part.size, measurer.descent_em * part.size


def _declared_size(el: ET.Element, ctx: _Ctx) -> float | None:
    raw = ctx.prop(el, "font-size")
    return None if raw is None else _length(raw, None)


def _size(el: ET.Element, ctx: _Ctx) -> float:
    size = _declared_size(el, ctx)
    if size is None:
        raise _invalid(f"<{_local(el.tag)}> has no resolvable font-size")
    return size


def _family(el: ET.Element, ctx: _Ctx) -> str:
    family = ctx.prop(el, "font-family")
    if family is None:
        family = ctx.root.get("data-dbt-font-family")
    if family is None:
        raise _invalid(f"<{_local(el.tag)}> has no resolvable font-family")
    return family


def _weight(el: ET.Element, ctx: _Ctx) -> int:
    raw = ctx.prop(el, "font-weight")
    if raw is None:
        return 400
    raw = raw.strip()
    if raw in _FONT_WEIGHT_KEYWORDS:
        return _FONT_WEIGHT_KEYWORDS[raw]
    try:
        return round(float(raw))
    except ValueError as exc:
        raise _invalid(f"unsupported font-weight {raw!r}") from exc


# ---- drawing ----------------------------------------------------------------


def _n(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _rect(
    x: float,
    y: float,
    w: float,
    h: float,
    paint: _Paint,
    rx: float,
    transform: str | None = None,
) -> ET.Element:
    el = ET.Element(
        f"{{{SVG_NS}}}rect",
        {
            "x": _n(x),
            "y": _n(y),
            "width": _n(w),
            "height": _n(h),
            "rx": _n(rx),
            "fill": paint.color,
        },
    )
    if paint.opacity is not None:
        el.set("fill-opacity", _n(paint.opacity))
    if transform is not None:
        el.set("transform", transform)
    return el


def _text_rect(
    line: _Line,
    left: float,
    right: float,
    paint: _Paint,
    text: ET.Element,
    ctx: _Ctx,
) -> ET.Element:
    config = ctx.config
    large = line.size >= config.large_text_size
    share = config.large_text_height if large else config.text_height
    h = max(config.min_text_height, share * line.box)
    return _rect(
        left, line.center - h / 2, right - left, h, paint, h / 2, text.get("transform")
    )


def _remove_furniture(ctx: _Ctx) -> None:
    doomed = [
        e
        for e in ctx.root.iter()
        if _REMOVED_CLASSES.intersection(_classes(e)) or _FOOTER_CLASS in _classes(e)
    ]
    for el in doomed:
        ctx.parents[el].remove(el)
    ctx.refresh()


def _box(el: ET.Element, attr: str) -> float:
    raw = el.get(attr)
    if raw is None:
        raise _invalid(f"variable control is missing {attr}")
    return _number(raw, attr)


def _replace_variable_controls(ctx: _Ctx) -> None:
    for control in [
        e for e in ctx.root.iter() if e.get("data-dbt-variable") is not None
    ]:
        x, y = _box(control, "data-dbt-x"), _box(control, "data-dbt-y")
        w, h = _box(control, "data-dbt-width"), _box(control, "data-dbt-height")
        pill = ctx.config.variable_pill_height * h
        ctx.replace(
            control,
            [
                _rect(
                    x,
                    y + (h - pill) / 2,
                    w,
                    pill,
                    ctx.placeholder,
                    pill / 2,
                    control.get("transform"),
                )
            ],
        )
    ctx.refresh()


def _strip_fonts(ctx: _Ctx) -> None:
    for style in [e for e in ctx.root.iter() if _local(e.tag) == "style"]:
        css = _FONT_FACE.sub("", style.text) if style.text else ""
        if css.strip():
            style.text = css
        else:
            ctx.parents[style].remove(style)


def _strip_inert(ctx: _Ctx) -> None:
    """Drop what paints nothing and says nothing in a picture: accessibility
    and prose attributes and pointer-target rects (transparent fill, no stroke)."""
    ctx.refresh()
    for el in list(ctx.root.iter()):
        for attr in (*_A11Y_ATTRS, *_PROSE_ATTRS):
            el.attrib.pop(attr, None)
        if (
            _local(el.tag) == "rect"
            and el.get("fill") in ("transparent", "none")
            and el.get("stroke") in (None, "none")
        ):
            ctx.parents[el].remove(el)


# ---- tables -----------------------------------------------------------------


@dataclass
class _Cell:
    el: ET.Element
    col: int
    lines: list[_Line]


@dataclass
class _Column:
    left: float
    right: float
    widest: float
    max_value: float
    anchor: str


def _redraw_table(chart: ET.Element, ctx: _Ctx) -> None:
    cells: list[_Cell] = []
    for text in chart.iter():
        col = text.get("data-col")
        if _local(text.tag) == "text" and col is not None:
            index = _number(col, "data-col")
            cells.append(_Cell(text, int(index), _lines(text, ctx)))

    columns: dict[int, _Column] = {}
    for cell in cells:
        for line in cell.lines:
            if all(r.glyph for r in line.runs):
                continue
            left, right = line.left(False), line.right(False)
            value = _value(cell.el)
            known = columns.get(cell.col)
            if known is None:
                columns[cell.col] = _Column(
                    left,
                    right,
                    right - left,
                    0.0 if value is None else abs(value),
                    line.anchor,
                )
                continue
            if right - left > known.widest:
                known.widest, known.anchor = right - left, line.anchor
            known.left, known.right = min(known.left, left), max(known.right, right)
            if value is not None:
                known.max_value = max(known.max_value, abs(value))
    if not columns:
        return
    ordered = sorted(columns.values(), key=lambda c: c.left)
    table_left, table_right = ordered[0].left, max(c.right for c in ordered)
    backgrounds = _cell_backgrounds(chart, cells, ctx)

    for cell in cells:
        drawn: list[ET.Element] = []
        for line in cell.lines:
            for run in line.runs:
                if run.glyph and run.glyph_fill is not None:
                    r = max(
                        ctx.config.min_glyph_dot_radius,
                        line.box * ctx.config.glyph_dot_share,
                    )
                    circle = ET.Element(
                        f"{{{SVG_NS}}}circle",
                        {
                            "cx": _n((run.left + run.right) / 2),
                            "cy": _n(line.center),
                            "r": _n(r),
                            "fill": run.glyph_fill,
                        },
                    )
                    transform = cell.el.get("transform")
                    if transform is not None:
                        circle.set("transform", transform)
                    drawn.append(circle)
            if all(r.glyph for r in line.runs):
                continue
            column = columns[cell.col]
            value = _value(cell.el)
            if value is not None and not backgrounds(cell, line):
                bar = _value_bar(
                    cell, line, column, value, ordered, table_left, table_right, ctx
                )
                if bar is not None:
                    drawn.append(bar)
                    continue
            drawn.append(_cell_block(cell, line, column, ctx))
        ctx.replace(cell.el, drawn)
    ctx.refresh()


def _value(text: ET.Element) -> float | None:
    raw = text.get("data-value")
    if raw is None:
        return None
    return _number(raw, "data-value")


def _cell_block(cell: _Cell, line: _Line, column: _Column, ctx: _Ctx) -> ET.Element:
    config = ctx.config
    width = min(
        max(config.min_block_width, config.block_share * column.widest),
        column.widest,
    )
    if column.anchor == "end":
        left = column.right - width
    elif column.anchor == "middle":
        left = (column.left + column.right) / 2 - width / 2
    else:
        left = column.left
    rgb = _color(ctx.prop(cell.el, "fill"))
    paint = ctx.placeholder
    if rgb is not None and ctx.chromatic(rgb):
        paint = ctx.tint(rgb, config.status_color_weight)
    return _text_rect(line, left, left + width, paint, cell.el, ctx)


def _value_bar(
    cell: _Cell,
    line: _Line,
    column: _Column,
    value: float,
    ordered: list[_Column],
    table_left: float,
    table_right: float,
    ctx: _Ctx,
) -> ET.Element | None:
    config = ctx.config
    at = ordered.index(column)
    start = ordered[at - 1].right + config.bar_gap if at else column.left
    room = min(column.right - start, config.bar_room_share * (table_right - table_left))
    if room <= 0:
        return None
    share = abs(value) / column.max_value if column.max_value else 0.0
    h = config.bar_height * line.box
    return _rect(
        column.right - room,
        line.center - h / 2,
        max(config.min_bar_width, share * room),
        h,
        ctx.bar_color,
        min(h / 2, config.bar_max_corner_radius),
        cell.el.get("transform"),
    )


def _frame(el: ET.Element, stop: ET.Element, ctx: _Ctx) -> tuple[str, ...]:
    """The transforms between ``stop`` and ``el``: two elements are in one
    coordinate space exactly when their frames are equal."""
    frame: list[str] = []
    node: ET.Element | None = el
    while node is not None and node is not stop:
        transform = node.get("transform")
        if transform is not None:
            frame.append(transform)
        node = ctx.parents.get(node)
    return tuple(frame)


def _coordinate(el: ET.Element, attr: str) -> float:
    raw = el.get(attr)
    # An omitted rect x/y/width/height is 0 by the SVG specification.
    return (
        0.0 if raw is None else _number(raw, attr)
    )  # type-state: silent_fallback — SVG default


def _cell_backgrounds(
    chart: ET.Element, cells: list[_Cell], ctx: _Ctx
) -> Callable[[_Cell, _Line], bool]:
    """Predicate: does this cell sit on a fill painted for it alone?

    A fill is a cell background when exactly one column's cells sit inside it;
    a row stripe covers several columns at once and does not count.
    """
    frames = {id(c): _frame(c.el, chart, ctx) for c in cells}
    centers = [
        (c.col, frames[id(c)], (ln.left(False) + ln.right(False)) / 2, ln.center)
        for c in cells
        for ln in c.lines
        if not all(r.glyph for r in ln.runs)
    ]
    solo: list[tuple[tuple[str, ...], float, float, float, float]] = []
    for rect in chart.iter(f"{{{SVG_NS}}}rect"):
        if _color(rect.get("fill")) is None or any(
            c.startswith("dbt-box") for c in _classes(rect)
        ):
            continue
        frame = _frame(rect, chart, ctx)
        x, y = _coordinate(rect, "x"), _coordinate(rect, "y")
        w, h = _coordinate(rect, "width"), _coordinate(rect, "height")
        inside = {
            col
            for col, f, cx, cy in centers
            if f == frame and x <= cx <= x + w and y <= cy <= y + h
        }
        if len(inside) == 1:
            solo.append((frame, x, y, w, h))

    def on_fill(cell: _Cell, line: _Line) -> bool:
        cx, cy = (line.left(False) + line.right(False)) / 2, line.center
        return any(
            f == frames[id(cell)] and x <= cx <= x + w and y <= cy <= y + h
            for f, x, y, w, h in solo
        )

    return on_fill

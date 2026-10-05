"""One walk over parsed markdown that rewrites its spans, and the one reading
of which of them are images.

Spans sit in paragraphs, headings, list items and table cells, nested inside
blockquotes and lists; the walk reaches every one, so a rewrite (resolving an
image ref, transforming a title's case) cannot miss a place a span can be.
The parse is mdsvg's, the one the renderer paints from, so text in a code span
or fence is never taken for an image.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from mdsvg.types import (
    AnyBlock,
    Blockquote,
    Heading,
    ImageBlock,
    ListItem,
    OrderedList,
    Paragraph,
    Span,
    SpanType,
    Table,
    TableRow,
    UnorderedList,
)

SpanRun = tuple[Span, ...]


def map_span_runs(
    blocks: list[AnyBlock],
    run_fn: Callable[[SpanRun], SpanRun],
    image_fn: Callable[[ImageBlock], ImageBlock],
) -> list[AnyBlock]:
    """Return *blocks* with ``run_fn`` applied to each block's run of spans (a
    paragraph, heading, list item, table cell) and ``image_fn`` to every
    standalone image block."""

    def row(table_row: TableRow) -> TableRow:
        return replace(
            table_row,
            cells=tuple(replace(c, spans=run_fn(c.spans)) for c in table_row.cells),
        )

    def item(list_item: ListItem) -> ListItem:
        return replace(
            list_item,
            spans=run_fn(list_item.spans),
            children=tuple(block(b) for b in list_item.children),
        )

    def block(b: AnyBlock) -> AnyBlock:
        if isinstance(b, ImageBlock):
            return image_fn(b)
        if isinstance(b, (Paragraph, Heading)):
            return replace(b, spans=run_fn(b.spans))
        if isinstance(b, Blockquote):
            return replace(b, blocks=tuple(block(inner) for inner in b.blocks))
        if isinstance(b, (UnorderedList, OrderedList)):
            return replace(b, items=tuple(item(i) for i in b.items))
        if isinstance(b, Table):
            return replace(b, header=row(b.header), rows=tuple(row(r) for r in b.rows))
        return b

    return [block(b) for b in blocks]


def image_urls(blocks: list[AnyBlock]) -> list[str]:
    """Every image destination in *blocks*, in document order."""
    urls: list[str] = []

    def run(spans: SpanRun) -> SpanRun:
        urls.extend(
            s.url for s in spans if s.span_type is SpanType.IMAGE and s.url is not None
        )
        return spans

    def image(block: ImageBlock) -> ImageBlock:
        urls.append(block.url)
        return block

    map_span_runs(blocks, run, image)
    return urls

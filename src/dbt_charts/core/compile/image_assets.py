"""Local image refs in board markdown -> embedded ``data:`` URIs.

A relative ref resolves through ``resolve_image`` against the directory of the
file its markdown was written in, and its bytes embed as a ``data:`` URI, so
every export format shows the image with no separate asset serving. Remote
refs pass through untouched: nothing here fetches over the network.
"""

from __future__ import annotations

import base64
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from dbt_charts.core.compile.errors import CompilationError, ReferenceError
from dbt_charts.core.compile.file_refs import (
    check_images_written_out,
    is_local_image,
    resolve_image,
)
from dbt_charts.core.compile.markdown_blocks import SpanRun, image_urls, map_span_runs
from dbt_charts.core.diagnostics.codes_render import ERR_IMAGE_REF_UNRESOLVED
from dbt_charts.core.utils import IMAGE_SUFFIXES
from mdsvg import MarkdownParser, parse as parse_markdown
from mdsvg.images import get_image_size
from mdsvg.types import AnyBlock, ImageBlock, SpanType

if TYPE_CHECKING:
    from dbt_charts.core.diagnostics.diagnostic import Diagnostic
    from dbt_charts.core.project import Project

# mdsvg's own pattern for an inline image, the one its parser matches.
_IMAGE_SYNTAX = MarkdownParser.IMAGE

# The project the compile or render in flight reads images from. Scoped, like
# ``svg_cache_scope``, because images resolve in painters a dozen frames below
# the entry point. The anchor is never ambient: each surface passes its
# content's own ``base_dir``.
_image_source: ContextVar[Project | None] = ContextVar("_image_source", default=None)


@contextmanager
def image_scope(project: Project) -> Generator[None]:
    """Bind *project* as where images are read from for the duration of a compile or render.

    Restored on exit, so a scope nested in another puts the outer project back.
    """
    token = _image_source.set(project)
    try:
        yield
    finally:
        _image_source.reset(token)


def _unresolved(ref: str, reason: str) -> CompilationError:
    # A data: ref is named by its header and size: echoing the payload turns a
    # one-line error into pages of base64 that mdsvg must wrap, slowly.
    if ref.lower().startswith("data:"):
        ref = f"{ref.partition(',')[0]},… ({max(1, round(len(ref) / 1024))} KB)"
    return CompilationError.from_code(ERR_IMAGE_REF_UNRESOLVED, ref=ref, reason=reason)


def _validate_embeddable(url: str, ref: str) -> None:
    """Raise ``CompilationError`` if mdsvg cannot parse *url*'s own image payload.

    mdsvg's size probe raises ``ValueError`` on a malformed payload (a renamed
    non-SVG ``.svg``, a truncated export, a raster header declaring a zero
    dimension, a hand-typed ``data:`` URI with garbage bytes). Left uncaught it
    surfaces deep inside painting as an internal error that names no ref.
    """
    try:
        get_image_size(url)
    except ValueError as exc:
        raise _unresolved(ref, f"malformed image data: {exc}") from exc


def _read_image(ref: str, base_dir: str | None) -> str:
    project = _image_source.get()
    if project is None:
        raise _unresolved(ref, "no project is in scope to read images from")
    written_in = None if base_dir is None else project.directory(base_dir)
    try:
        path = resolve_image(ref, written_in)
    except ReferenceError as exc:
        raise _unresolved(ref, exc.message) from exc
    mime = IMAGE_SUFFIXES[PurePosixPath(path.relpath).suffix.lower()]
    encoded = base64.b64encode(project.read_bytes(path.relpath)).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def resolve_image_ref(ref: str, base_dir: str | None) -> str:
    """Return an embeddable URL for *ref*, written in the file whose directory is
    *base_dir*, reading it from the project bound by ``image_scope``.

    Raises ``CompilationError`` (ERR-IMAGE-REF-UNRESOLVED) when a local ref does not
    resolve or any embedded payload, local or an authored ``data:`` URI, is
    malformed.
    """
    if not is_local_image(ref):
        if ref.lower().startswith("data:"):
            _validate_embeddable(ref, ref)
        return ref
    url = _read_image(ref, base_dir)
    _validate_embeddable(url, ref)
    return url


def rewrite_markdown_images(
    blocks: list[AnyBlock],
    base_dir: str | None,
) -> list[AnyBlock]:
    """Resolve every image in *blocks* (see ``resolve_image_ref``).

    Rewrites the parsed blocks rather than mapping mdsvg's emitted href:
    mdsvg sizes an image from its URL before painting, so a mapper applied
    after would measure one image and embed another.
    """

    def run(spans: SpanRun) -> SpanRun:
        return tuple(
            replace(s, url=resolve_image_ref(s.url, base_dir))
            if s.span_type is SpanType.IMAGE and s.url is not None
            else s
            for s in spans
        )

    def image(block: ImageBlock) -> ImageBlock:
        return replace(block, url=resolve_image_ref(block.url, base_dir))

    return map_span_runs(blocks, run, image)


def rewrite_markdown_text_images(markdown: str, base_dir: str | None) -> str:
    """*markdown* with each image's destination replaced by its
    ``resolve_image_ref`` URL, for a consumer that parses the text itself.

    mdsvg's parse decides which ``![](..)`` is an image: each candidate the
    parser's own image pattern finds is probed (is its destination read back as
    an image?), so one inside a code span or fence stays text. The result must
    parse to exactly ``rewrite_markdown_images``'s blocks, else it is refused.
    """
    expected = rewrite_markdown_images(list(parse_markdown(markdown)), base_dir)
    resolved: dict[str, str] = {}
    pieces: list[str] = []
    cursor = 0
    for index, candidate in enumerate(_IMAGE_SYNTAX.finditer(markdown)):
        start, end = candidate.span(2)
        probe = f"mdsvg-probe-{index}"
        probed = markdown[:start] + probe + markdown[end:]
        if probe not in image_urls(list(parse_markdown(probed))):
            continue
        ref = candidate.group(2)
        if ref not in resolved:
            resolved[ref] = resolve_image_ref(ref, base_dir)
        pieces += [markdown[cursor:start], resolved[ref]]
        cursor = end
    rewritten = "".join(pieces) + markdown[cursor:]
    if list(parse_markdown(rewritten)) != expected:
        raise _unresolved(
            next(iter(resolved), markdown[:40]),
            "the image syntax could not be rewritten without changing other markdown",
        )
    return rewritten


def rewrite_board_markdown_images(
    blocks: list[AnyBlock],
    authored: str,
    rendered: str,
    base_dir: str | None,
    error_collector: list[Diagnostic] | None,
) -> list[AnyBlock]:
    """``rewrite_markdown_images`` for a board's own title or text, parsed from
    *rendered*, the Jinja rendering of *authored*, which must write out every
    project image it names (``check_images_written_out``).

    A bad image degrades that one block to its error message instead of
    failing the board. *error_collector* receives the diagnostic; the sizing
    pass passes ``None`` so the paint pass alone reports it.
    """
    try:
        check_images_written_out(authored, rendered)
        return rewrite_markdown_images(blocks, base_dir)
    except CompilationError as exc:
        if error_collector is not None:
            error_collector.append(exc.to_diagnostic())
        return list(parse_markdown(f"Image error: {exc.message}"))

"""The ``charts`` fences of a markdown page, each drawn as its own chart.

For a host that renders the prose itself: a markdown extension inlines each
chart, and a static-site build swaps each fence for an SVG file and an image
link. The page compiles exactly as ``dct`` compiles a markdown board, so a fence
holds one ``rows:`` item and query and chart names cross fences; each fence's
row then renders alone.
"""

from __future__ import annotations

import dataclasses
import re

from pydantic import BaseModel, ConfigDict

from dbt_charts.agent_api.project_session import ProjectSession
from dbt_charts.core.compile.compiler import compile_file
from dbt_charts.core.compile.models.board.normalized import LayoutItem
from dbt_charts.core.compile.models.chart.normalized import Chart, KpiChart
from dbt_charts.core.compile.normalize.chart_focus import focus_on_item
from dbt_charts.core.compile.parse.markdown import (
    ChartFence,
    chart_fences,
    has_chart_fence,
)
from dbt_charts.core.diagnostics import Diagnostic
from dbt_charts.core.project import BoardFile

_ROW_PATH_RE = re.compile(r"rows\.(\d+)(?:\.|$)")


class RenderedFence(BaseModel):
    """One chart fence: its lines in the page (0-based, inclusive) and its SVG."""

    model_config = ConfigDict(frozen=True)

    first_line: int
    last_line: int
    title: str
    svg: str


class ChartFencesRender(BaseModel):
    """Every chart fence of a page, or the errors that stopped them.

    ``fences`` is empty whenever ``errors`` is not: a page renders whole or not
    at all, so no host publishes it with a chart missing.
    """

    model_config = ConfigDict(frozen=True)

    fences: list[RenderedFence]
    errors: list[Diagnostic]
    warnings: list[Diagnostic]


class MarkdownWithImages(BaseModel):
    """A page with each chart fence swapped for an image link, and the images.

    ``images`` maps a file name, relative to the page, to its SVG.
    """

    model_config = ConfigDict(frozen=True)

    markdown: str
    images: dict[str, str]
    errors: list[Diagnostic]
    warnings: list[Diagnostic]


def render_chart_fences(
    session: ProjectSession,
    page: BoardFile,
    standalone: bool,
    variables: dict[str, str] | None = None,
    ignore_codes: set[str] | None = None,
    max_workers: int | None = None,
    allow_chart_errors: bool = False,
) -> ChartFencesRender:
    """Render each chart fence of the markdown *page* on its own.

    *page* must be located at a markdown path: that path anchors relative refs
    and the ``meta.yml`` cascade, as for any board. *standalone* embeds each
    SVG's fonts, for output nothing will serve ``/static/fonts/`` to.
    *variables*, *ignore_codes* and *max_workers* mean what they mean to
    ``render_board``. A chart whose query fails is an error unless
    *allow_chart_errors*, which draws its error card and reports it as a warning.
    """
    text = page.content
    if not has_chart_fence(text):
        return ChartFencesRender(fences=[], errors=[], warnings=[])
    compiled = compile_file(page)
    if not compiled.success or compiled.board is None:
        return ChartFencesRender(
            fences=[], errors=_by_fence(compiled.errors, text), warnings=[]
        )
    fences = chart_fences(text)
    # The host draws the prose, so a warning about a prose row is not for it.
    fence_rows = {fence.row for fence in fences}

    rendered: list[RenderedFence] = []
    errors: list[Diagnostic] = []
    warnings: dict[str, Diagnostic] = {}
    for fence in fences:
        focused = focus_on_item(compiled.board, fence.row)
        result = session.render_board(
            board=page,
            compile_result=dataclasses.replace(compiled, board=focused),
            only_charts=[c.id for c in focused.placed_charts() if c.id],
            variables=variables,
            format="svg",
            ignore_codes=ignore_codes,
            max_workers=max_workers,
            standalone=standalone,
        )
        board_error = [result.board_error] if result.board_error else []
        chart_errors = [_at_fence(e, fence) for e in result.chart_errors]
        failed = [
            *(_at_fence(e, fence) for e in [*result.validation_errors, *board_error]),
            *([] if allow_chart_errors else chart_errors),
        ]
        errors.extend(failed)
        warnings |= {
            w.model_dump_json(): w
            for w in [*result.warnings, *(chart_errors if allow_chart_errors else [])]
            if _row_of(w) in (None, *fence_rows)
        }
        if not failed:
            assert isinstance(result.data, str)  # format="svg" renders text
            rendered.append(
                RenderedFence(
                    first_line=fence.first_line,
                    last_line=fence.last_line,
                    title=_title(focused.layout.items[0]),
                    svg=result.data,
                )
            )
    if errors:
        return ChartFencesRender(
            fences=[], errors=errors, warnings=list(warnings.values())
        )
    return ChartFencesRender(
        fences=rendered, errors=[], warnings=list(warnings.values())
    )


def render_markdown_with_images(
    session: ProjectSession,
    page: BoardFile,
    image_stem: str,
    variables: dict[str, str] | None = None,
    ignore_codes: set[str] | None = None,
    max_workers: int | None = None,
    allow_chart_errors: bool = False,
) -> MarkdownWithImages:
    """*page* with each chart fence swapped for an image link to its own SVG.

    The images are ``<image_stem>-<n>.svg`` beside the page. Each carries its
    fonts, because an SVG loaded through ``<img>`` cannot fetch any. The other
    arguments are ``render_chart_fences``'s.
    """
    rendered = render_chart_fences(
        session,
        page,
        True,
        variables,
        ignore_codes,
        max_workers,
        allow_chart_errors,
    )
    images = {
        f"{image_stem}-{n}.svg": fence.svg for n, fence in enumerate(rendered.fences, 1)
    }
    links = [
        f"![{_alt_text(fence.title)}]({name})"
        for name, fence in zip(images, rendered.fences, strict=True)
    ]
    return MarkdownWithImages(
        markdown=splice_lines(
            page.content,
            [
                (fence.first_line, fence.last_line, link)
                for fence, link in zip(rendered.fences, links, strict=True)
            ],
        ),
        images=images,
        errors=rendered.errors,
        warnings=rendered.warnings,
    )


def splice_lines(text: str, spans: list[tuple[int, int, str]]) -> str:
    """*text* with each ``(first_line, last_line, replacement)`` span's lines
    replaced, every replacement line at the indent of the span's first line.
    Blank lines stay empty: an indented one would not end a markdown block.
    Spans are in order and apart."""
    lines = text.split("\n")
    out: list[str] = []
    next_line = 0
    for first_line, last_line, replacement in spans:
        opener = lines[first_line]
        indent = opener[: len(opener) - len(opener.lstrip())]
        out.extend(lines[next_line:first_line])
        out.extend(indent + line if line else "" for line in replacement.split("\n"))
        next_line = last_line + 1
    out.extend(lines[next_line:])
    return "\n".join(out)


def _title(item: LayoutItem) -> str:
    """What the item's picture is called: its chart's title, a board's title, or
    the titles of the board's charts."""
    if item.chart is not None:
        return _chart_title(item.chart)
    assert item.board is not None  # a layout item holds a chart or a board
    if item.board.title:
        return item.board.title
    return "; ".join(
        title for chart in item.board.placed_charts() if (title := _chart_title(chart))
    )


def _chart_title(chart: Chart) -> str:
    # A KPI is named by its label; a callout's title is optional.
    return (chart.label if isinstance(chart, KpiChart) else chart.title) or ""


def _alt_text(title: str) -> str:
    return " ".join(title.replace("[", "").replace("]", "").split()) or "Chart"


def _by_fence(errors: list[Diagnostic], text: str) -> list[Diagnostic]:
    """Compile *errors*, each naming its fence when its path points into one.

    A path into ``rows:`` means the page parsed, so its fences can be read.
    """
    rows = [_row_of(e) for e in errors]
    if rows == [None] * len(rows):
        return errors
    fence_at = {fence.row: fence for fence in chart_fences(text)}
    return [
        _at_fence(e, fence_at[row]) if row in fence_at else e
        for e, row in zip(errors, rows, strict=True)
    ]


def _row_of(diagnostic: Diagnostic) -> int | None:
    """The ``rows:`` index *diagnostic*'s path points into, if any."""
    match = _ROW_PATH_RE.match(diagnostic.path or "")
    return int(match.group(1)) if match else None


def _at_fence(diagnostic: Diagnostic, fence: ChartFence) -> Diagnostic:
    """*diagnostic* naming the fence it came from, by its line in the page."""
    return diagnostic.model_copy(
        update={
            "message": f"`charts` fence on line {fence.first_line + 1}: "
            f"{diagnostic.message}"
        }
    )

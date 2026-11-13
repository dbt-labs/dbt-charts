"""Markdown report file loader and transformer.

Translates .md files with YAML frontmatter and ``charts`` fences into YAML
board definitions that the normal compile pipeline accepts.

This is a pre-compile step — the output is a YAML string that feeds
directly into compile().

Frontmatter design
------------------
A markdown file's frontmatter is document metadata, full stop — except for a
single optional ``board:`` key whose value is the board config (title, queries,
charts, rows, variables, theme, etc.).  Every other frontmatter key is
free-form document metadata and is NEVER validated as board config — so a task
file with ``status``, ``owner``, ``milestone``, etc. compiles without error,
and a research note with ``theme: topic-name`` will not crash on the theme
validator.

Chart fences
------------
A ``charts`` fence holds one ``rows:`` item: a chart, a whole board, or the
name of a chart or board file. The prose around it becomes text rows.
``chart_fences`` says where each fence sits in the file and in ``rows:``, so a
host that renders the prose itself can draw each fence's row in its place.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import yaml

from dbt_charts.core.compile.template.markdown_fence import (
    MASKED_FENCE_RE,
    escape_jinja_outside_fences,
    mask_fenced_code,
)
from dbt_charts.core.utils import YAML_LOADER, UniqueKeyLoader, deep_merge_dict

# Up to three spaces of indent: four make a code block, per CommonMark.
_CHART_FENCE_RE = re.compile(r" {0,3}(?:`{3,}|~{3,})[ \t]*charts(?![\w-])(.*)")

_Row = dict[str, Any] | str  # type-state: explicit_any — authored board YAML


@dataclass(frozen=True)
class ChartFence:
    """A chart fence: its lines in the file (0-based, inclusive) and its index in
    the board's ``rows:``."""

    first_line: int
    last_line: int
    row: int


# Lowest priority: meta.yml, extends and frontmatter all override it.
_DOCUMENT_STYLE: dict[str, Any] = {  # type-state: explicit_any — board YAML patch
    "text": {"column": {"max_number": 1, "max_chars": 90}}
}


def with_document_style(
    board_data: dict[str, Any],  # type-state: explicit_any — authored board YAML
) -> dict[str, Any]:  # type-state: explicit_any — authored board YAML
    """*board_data* with ``_DOCUMENT_STYLE`` as its lowest-priority style layer,
    beneath meta.yml, extends and frontmatter.

    A ``style`` that is not a mapping is left for validation to report.
    """
    style = board_data.get("style")
    if style is None:
        style = {}
    elif not isinstance(style, dict):
        return board_data
    return {**board_data, "style": deep_merge_dict(_DOCUMENT_STYLE, style)}


def _fence_row(fence: str, options: str, first_line: int) -> _Row:
    """The ``rows:`` item a chart fence holds.

    *first_line* is the fence's line in the file. The padding makes a YAML
    error's position a line of the file, as in ``_extract_frontmatter``.
    """
    if options.strip():
        raise ValueError(
            f"A `charts` fence takes no options, got {options.strip()!r}: "
            "write a file path as the fence's content"
        )
    _opening, *lines, _closing = fence.splitlines(keepends=True)
    padding = "\n" * (first_line + 1)
    item = yaml.load(padding + "".join(lines), Loader=UniqueKeyLoader)
    if item is None:
        raise ValueError(f"The `charts` fence on line {first_line + 1} is empty")
    if not isinstance(item, dict | str):
        raise ValueError(
            f"The `charts` fence on line {first_line + 1} must hold one chart, "
            "board or file"
        )
    return item


def _prose_rows(prose: str, is_document: bool) -> list[_Row]:
    text = prose.strip()
    if not text:
        return []
    return [{"text": escape_jinja_outside_fences(text) if is_document else text}]


def _pieces(body: str) -> Iterator[tuple[str, re.Match[str] | None]]:
    """*body* as prose and fenced code, in order, each fence with its chart match."""
    masked, fences = mask_fenced_code(body)
    for piece in MASKED_FENCE_RE.split(masked):
        if piece in fences:
            yield fences[piece], _CHART_FENCE_RE.match(fences[piece])
        else:
            yield piece, None


def _body_rows(
    body: str, first_line: int, is_document: bool
) -> tuple[list[_Row], list[ChartFence]]:
    """*body* as ``rows:`` items: its chart fences, and the prose between them.

    A document's Jinja tokens are prose, so its text prints as written; only a
    board's text is a template. *first_line* is the body's line in the file.
    """
    rows: list[_Row] = []
    fences: list[ChartFence] = []
    prose = ""
    line = first_line
    for source, chart_fence in _pieces(body):
        if chart_fence is not None:
            rows.extend(_prose_rows(prose, is_document))
            last_line = line + source.rstrip("\n").count("\n")
            fences.append(ChartFence(line, last_line, len(rows)))
            rows.append(_fence_row(source, chart_fence.group(1), line))
            prose = ""
        else:
            prose += source
        line += source.count("\n")
    rows.extend(_prose_rows(prose, is_document))
    return rows, fences


def _extract_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Extract optional YAML frontmatter and markdown body from text."""
    lines = md_text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, md_text

    closing_index = next(
        (i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise ValueError(
            "Markdown board file frontmatter is missing a closing --- delimiter"
        )

    fm_text = "".join(lines[1:closing_index])
    body = "".join(lines[closing_index + 1 :])
    if not fm_text.strip():
        return {}, body

    # The blank first line stands for the opening `---`, so a YAML error's
    # position is a line of the markdown file, not of the frontmatter block.
    fm = yaml.load(f"\n{fm_text}", Loader=UniqueKeyLoader)
    if fm is None:
        return {}, body
    if not isinstance(fm, dict):
        raise ValueError("Markdown board file frontmatter must be a YAML mapping")

    return fm, body


def _metadata_to_markdown_table(metadata: dict[str, Any]) -> str:
    """Render a flat metadata dict as a two-column markdown table.

    Produces:
        | Key | Value |
        |---|---|
        | status | in_progress |
        ...
    """

    def _cell(v: object) -> str:
        # Escape the column separator and flatten newlines so a value never
        # splits a row into extra columns or breaks the table grid. Metadata is
        # data, never a template: its Jinja tokens print as written.
        return escape_jinja_outside_fences(
            str(v).replace("|", "\\|").replace("\n", " ")
        )

    lines = ["| Key | Value |", "|---|---|"]
    for key, value in metadata.items():
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    return "\n".join(lines)


def _parse(
    md_text: str,
) -> tuple[
    dict[str, Any],  # type-state: explicit_any — authored board YAML
    dict[str, Any],  # type-state: explicit_any — free-form document metadata
    list[ChartFence],
]:
    fm, body = _extract_frontmatter(md_text)

    board_config: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    is_document = "board" not in fm
    if is_document:
        # No board: block — every frontmatter key is document metadata.
        metadata = dict(fm)
    else:
        board_cfg = fm["board"]
        if not isinstance(board_cfg, dict):
            raise ValueError("'board:' in markdown frontmatter must be a YAML mapping")
        board_config = board_cfg
        metadata = {k: v for k, v in fm.items() if k != "board"}

    body_line = md_text[: len(md_text) - len(body)].count("\n")
    rows, fences = _body_rows(body, body_line, is_document)

    # Build the board dict from board config + generated rows
    board_dict = dict(board_config)
    if rows:
        board_dict["rows"] = rows
    elif "text" not in board_dict:
        # No body content and no content in frontmatter — add empty placeholder
        board_dict["text"] = ""
    return board_dict, metadata, fences


def parse_markdown_board(
    md_text: str,
) -> tuple[str, dict[str, Any]]:  # type-state: explicit_any — document metadata
    """Parse a markdown board file and return (yaml_str, metadata_dict).

    ``yaml_str`` is the YAML string ready to feed to compile().
    ``metadata_dict`` contains all frontmatter keys that are NOT board config
    (i.e. everything except the optional ``board:`` block).

    Board config lives exclusively under a ``board:`` key.  All other frontmatter
    keys are document metadata and are never interpreted as board configuration.

    This is the low-level function; ``markdown_to_yaml`` is the high-level
    convenience wrapper that also handles the metadata_table flag.
    """
    board_dict, metadata, _fences = _parse(md_text)
    yaml_str = yaml.dump(
        board_dict, default_flow_style=False, allow_unicode=True, sort_keys=False
    )
    return yaml_str, metadata


def chart_fences(md_text: str) -> list[ChartFence]:
    """The chart fences of a markdown board, as ``parse_markdown_board`` reads it."""
    return _parse(md_text)[2]


def markdown_to_yaml(md_text: str, *, metadata_table: bool = False) -> str:
    """Transform a markdown board file into a YAML string for the compiler.

    Parses frontmatter, splits the body into content/chart blocks,
    and emits a board definition with a rows layout.

    Args:
        md_text: Raw markdown file contents.
        metadata_table: When True and document metadata is non-empty, prepend
            the metadata as a markdown table text row before the body rows.
    """
    yaml_str, metadata = parse_markdown_board(md_text)

    if not metadata_table or not metadata:
        return yaml_str

    # Inject metadata table as the first text row
    board_dict = yaml.load(yaml_str, Loader=YAML_LOADER)
    if not isinstance(board_dict, dict):
        return yaml_str

    # parse_markdown_board emits a body as either a rows: list or a single text:
    # placeholder, never both. Prepend the metadata table and fold whichever
    # body form was present back in after it.
    table_row = {"text": _metadata_to_markdown_table(metadata)}
    existing_rows = board_dict.pop("rows", None)
    body_text = board_dict.pop("text", None)
    rows = [table_row]
    if existing_rows:
        rows.extend(existing_rows)
    elif body_text:
        rows.append({"text": body_text})
    board_dict["rows"] = rows

    return yaml.dump(
        board_dict, default_flow_style=False, allow_unicode=True, sort_keys=False
    )


def has_chart_fence(text: str) -> bool:
    """Whether markdown *text* holds a chart fence, without reading what is in it."""
    return any(match for _, match in _pieces(text))


def is_markdown_board_content(text: str, *, in_boards: bool, named: bool) -> bool:
    """Decide whether markdown *text* is a dbt charts board.

    Content-only decision — no disk access. Callers pair this with a suffix
    check (``ProjectPath.is_markdown`` / ``suffix in MARKDOWN_SUFFIXES``) and
    supply *in_boards* themselves (``CHARTS_SUBDIR in <path>.parts``) from text they
    already read, so no caller needs a second disk read to detect a markdown board.

    Rules:
    - If *in_boards* is True, any readable markdown is a board (path convention).
    - A file the user *named* directly is a board when its body has a chart fence.
      A folder listing never names its files, so a README that shows a fence is
      not swept up as a board.
    - Otherwise the frontmatter must contain a top-level ``board:`` key.
    """
    if in_boards:
        return True
    try:
        fm, body = _extract_frontmatter(text)
    except (ValueError, yaml.YAMLError):
        return False
    return "board" in fm or (named and has_chart_fence(body))

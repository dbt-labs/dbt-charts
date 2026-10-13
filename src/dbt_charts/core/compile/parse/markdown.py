"""Markdown report file loader and transformer.

Translates .md files with YAML frontmatter and {{ chart <id> }} embeds
into YAML board definitions that the normal compile pipeline accepts.

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
"""

import re
from typing import Any

import yaml

from dbt_charts.core.compile.merge import deep_merge_dict
from dbt_charts.core.utils import YAML_LOADER, UniqueKeyLoader

_CHART_EMBED_RE = re.compile(r"^\s*\{\{\s*chart\s+(\w+)\s*\}\}\s*$", re.MULTILINE)

MARKDOWN_NOT_BOARD_MESSAGE = (
    "Not a dbt charts board file: markdown files outside charts/ must declare a "
    "top-level 'board:' key in their YAML frontmatter"
)


# Lowest priority: meta.yml, extends and frontmatter all override it.
_DOCUMENT_STYLE: dict[str, Any] = {  # type-state: explicit_any — board YAML patch
    "text": {"column": {"max_number": 1}}
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


def parse_chart_embeds(body: str) -> list[tuple[str, str]]:
    """Split markdown body into ordered (type, value) blocks.

    Returns a list of tuples:
      ("text", <markdown text>)
      ("chart", <chart_id>)

    Empty/whitespace-only blocks are dropped.
    """
    if not body or not body.strip():
        return []

    blocks: list[tuple[str, str]] = []
    last_end = 0

    for match in _CHART_EMBED_RE.finditer(body):
        # Text before this embed
        text = body[last_end : match.start()].strip()
        if text:
            blocks.append(("text", text))
        blocks.append(("chart", match.group(1)))
        last_end = match.end()

    # Trailing text after last embed
    text = body[last_end:].strip()
    if text:
        blocks.append(("text", text))

    return blocks


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
        # splits a row into extra columns or breaks the table grid.
        return str(v).replace("|", "\\|").replace("\n", " ")

    lines = ["| Key | Value |", "|---|---|"]
    for key, value in metadata.items():
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    return "\n".join(lines)


def parse_markdown_board(md_text: str) -> tuple[str, dict[str, Any]]:
    """Parse a markdown board file and return (yaml_str, metadata_dict).

    ``yaml_str`` is the YAML string ready to feed to compile().
    ``metadata_dict`` contains all frontmatter keys that are NOT board config
    (i.e. everything except the optional ``board:`` block).

    Board config lives exclusively under a ``board:`` key.  All other frontmatter
    keys are document metadata and are never interpreted as board configuration.

    This is the low-level function; ``markdown_to_yaml`` is the high-level
    convenience wrapper that also handles the metadata_table flag.
    """
    fm, body = _extract_frontmatter(md_text)

    board_config: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    if "board" in fm:
        board_cfg = fm["board"]
        if not isinstance(board_cfg, dict):
            raise ValueError("'board:' in markdown frontmatter must be a YAML mapping")
        board_config = board_cfg
        metadata = {k: v for k, v in fm.items() if k != "board"}
    else:
        # No board: block — every frontmatter key is document metadata.
        metadata = dict(fm)

    # Build rows from markdown body
    blocks = parse_chart_embeds(body)
    rows: list[dict[str, Any] | str] = []
    for block_type, value in blocks:
        if block_type == "text":
            rows.append({"text": value})
        elif block_type == "chart":
            rows.append(value)

    # If body had content but no embeds, ensure we have at least one row
    if not rows and body.strip():
        rows.append({"text": body.strip()})

    # Build the board dict from board config + generated rows
    board_dict = dict(board_config)
    if rows:
        board_dict["rows"] = rows
    elif "text" not in board_dict:
        # No body content and no content in frontmatter — add empty placeholder
        board_dict["text"] = ""

    yaml_str = yaml.dump(board_dict, default_flow_style=False, allow_unicode=True)
    return yaml_str, metadata


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

    return yaml.dump(board_dict, default_flow_style=False, allow_unicode=True)


def is_markdown_board_content(text: str, *, in_boards: bool) -> bool:
    """Decide whether markdown *text* is a dbt charts board.

    Content-only decision — no disk access. Callers pair this with a suffix
    check (``ProjectPath.is_markdown`` / ``suffix in MARKDOWN_SUFFIXES``) and
    supply *in_boards* themselves (``CHARTS_SUBDIR in <path>.parts``) from text they
    already read, so no caller needs a second disk read to detect a markdown board.

    Rules:
    - If *in_boards* is True, any readable markdown is a board (path convention).
    - Otherwise the frontmatter must contain a top-level ``board:`` key.
    """
    if in_boards:
        return True
    try:
        fm, _body = _extract_frontmatter(text)
    except (ValueError, yaml.YAMLError):
        return False
    return "board" in fm

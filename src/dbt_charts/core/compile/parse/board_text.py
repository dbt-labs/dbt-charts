"""A board file's title and notes, read from its raw text.

The one reading of "what is this board called" for every listing and search
surface: it never validates, so a board that does not compile is still named.
"""

from typing import NamedTuple

import yaml

from dbt_charts.core.compile.parse.markdown import parse_markdown_board
from dbt_charts.core.utils import YAML_LOADER

RawYaml = dict[str, object]  # type-state: object_annotation — unvalidated YAML


class BoardText(NamedTuple):
    title: str
    notes: str
    #: Lowercased ``tags:`` strings; a tag of another type is not a tag.
    tags: tuple[str, ...]
    #: The board mapping the text parsed to; ``{}`` when it is not one.
    board: RawYaml
    #: Distinguishes an empty board from a broken one — both give ``{}``.
    parsed_ok: bool


def _tags(board: RawYaml) -> tuple[str, ...]:
    tags = board.get("tags")
    if not isinstance(tags, list):
        return ()
    return tuple(tag.lower() for tag in tags if isinstance(tag, str))


def _first_text(*values: object) -> str:  # type-state: object_annotation — YAML scalars
    """The first truthy value as text; none is an empty string."""
    return str(
        next((v for v in values if v), "")
    )  # type-state: silent_fallback — optional prose


def parse_yaml_board_text(slug: str, content: str) -> BoardText:
    """Title and notes of a YAML board; the title falls back to *slug*."""
    board: RawYaml = {}
    try:
        loaded = yaml.load(content, Loader=YAML_LOADER)  # noqa: S506 — CSafeLoader, a safe loader
    except yaml.YAMLError:
        parsed_ok = False
    else:
        parsed_ok = isinstance(loaded, dict)
        if isinstance(loaded, dict):
            board = loaded
    return BoardText(
        title=_first_text(board.get("title"), slug),
        notes=_first_text(board.get("notes")),
        tags=_tags(board),
        board=board,
        parsed_ok=parsed_ok,
    )


def parse_markdown_board_text(slug: str, content: str) -> BoardText:
    """Title and notes of a markdown board: frontmatter, then the board config,
    then the first ``# `` heading, then *slug*."""
    parsed: RawYaml = {}
    metadata: RawYaml = {}
    try:
        compile_yaml, metadata = parse_markdown_board(content)
    except (ValueError, yaml.YAMLError):
        pass
    else:
        try:
            loaded = yaml.load(compile_yaml, Loader=YAML_LOADER)  # noqa: S506 — CSafeLoader, a safe loader
            if isinstance(loaded, dict):
                parsed = loaded
        except yaml.YAMLError:
            parsed = {}
    heading = next(
        (
            line.strip()[2:].strip()
            for line in content.splitlines()
            if line.strip().startswith("# ")
        ),
        slug,
    )
    return BoardText(
        title=_first_text(metadata.get("title"), parsed.get("title"), heading),
        notes=_first_text(metadata.get("notes"), parsed.get("notes")),
        tags=_tags(parsed),
        board=parsed,
        parsed_ok=bool(parsed),
    )


def parse_board_text(slug: str, content: str, *, is_markdown: bool) -> BoardText:
    """Title and notes of a board file of either format."""
    if is_markdown:
        return parse_markdown_board_text(slug, content)
    return parse_yaml_board_text(slug, content)

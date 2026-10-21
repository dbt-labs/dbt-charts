"""``glob_boards`` / ``glob_folders`` in board text: call extraction, the
row models and the context callables. The data provider resolves calls against
the project (``core/board_listings.py``) at render, as it runs text queries.
"""

from __future__ import annotations

from collections.abc import Callable

from jinja2 import Environment, TemplateSyntaxError, nodes as jinja_nodes
from pydantic import BaseModel, Field

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.template.markdown_fence import mask_fenced_code
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_TEXT_LISTING_NONLITERAL,
    ERR_TEXT_LISTING_PATTERN,
)

GLOB_BOARDS = "glob_boards"
GLOB_FOLDERS = "glob_folders"
LISTING_FUNCTIONS = frozenset({GLOB_BOARDS, GLOB_FOLDERS})

ListingCall = tuple[str, str]


class BoardListing(BaseModel):
    """One ``glob_boards`` row: a board's link path and its own labels."""

    path: str
    title: str
    notes: str
    tags: list[str]


class FolderListing(BaseModel):
    """One ``glob_folders`` row: a folder holding at least one board."""

    path: str
    name: str
    title: str


class TextListings(BaseModel):
    """What one text's listing calls return, keyed by literal pattern."""

    boards: dict[str, list[BoardListing]] = Field(default_factory=dict)
    folders: dict[str, list[FolderListing]] = Field(default_factory=dict)


def listing_pattern_error(call: ListingCall, reason: str) -> CompilationError:
    function, pattern = call
    return CompilationError.from_code(
        ERR_TEXT_LISTING_PATTERN, function=function, pattern=pattern, reason=reason
    )


def extract_listing_calls(text: str) -> set[ListingCall]:
    """``(function, pattern)`` for every listing call in *text*.

    Raises:
        CompilationError: ``ERR-TEXT-LISTING-NONLITERAL`` for any use of a
            listing name other than a call with one string literal.
    """
    masked, _ = mask_fenced_code(text)
    if "glob_" not in masked:
        return set()
    try:
        ast = Environment().parse(masked)
    except TemplateSyntaxError:
        return set()

    calls: set[ListingCall] = set()
    called: set[int] = set()
    for call in ast.find_all(jinja_nodes.Call):
        fn = call.node
        if not (isinstance(fn, jinja_nodes.Name) and fn.name in LISTING_FUNCTIONS):
            continue
        args = call.args
        if (
            len(args) != 1
            or call.kwargs
            or call.dyn_args
            or call.dyn_kwargs
            or not isinstance(args[0], jinja_nodes.Const)
            or not isinstance(args[0].value, str)
        ):
            raise CompilationError.from_code(
                ERR_TEXT_LISTING_NONLITERAL, function=fn.name
            )
        calls.add((fn.name, args[0].value))
        called.add(id(fn))
    for name in ast.find_all(jinja_nodes.Name):
        if name.name in LISTING_FUNCTIONS and id(name) not in called:
            raise CompilationError.from_code(
                ERR_TEXT_LISTING_NONLITERAL, function=name.name
            )
    return calls


def check_listing_calls(title: str | None, text: str | None) -> None:
    """Compile-time check of a board's listing calls; needs no project.

    Raises:
        CompilationError: a non-literal call in *text*, or any call in *title*.
    """
    if text:
        extract_listing_calls(text)
    reject_listing_calls(title, "a board title")


def reject_listing_calls(text: str | None, surface: str) -> None:
    """Raise if *text*, which is not board text, calls a listing function."""
    if text:
        for call in sorted(extract_listing_calls(text)):
            raise listing_pattern_error(
                call, f"only board text can list boards, not {surface}"
            )


ListingRow = dict[str, str | list[str]]


def listing_helpers(
    listings: TextListings,
) -> dict[str, Callable[[str], list[ListingRow]]]:
    """The two context callables, answering from already-resolved *listings*."""

    def glob_boards(pattern: str) -> list[ListingRow]:
        return [e.model_dump() for e in listings.boards[pattern]]

    def glob_folders(pattern: str) -> list[ListingRow]:
        return [e.model_dump() for e in listings.folders[pattern]]

    return {GLOB_BOARDS: glob_boards, GLOB_FOLDERS: glob_folders}

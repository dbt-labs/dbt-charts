"""Key sets derived from schema facets.

``keys_with`` / ``board_keys_with`` replace five hand-written key lists that
answered questions ``AuthoredBoard`` already knew. The partition test here is
the gate that keeps the answer honest as the model grows.
"""

from __future__ import annotations

import pytest

from dbt_charts.core.compile.models.board.authored import AuthoredBoard
from dbt_charts.core.compile.models.markers import (
    Content,
    DisplayText,
    Extends,
    Facet,
    Markdown,
    Url,
)
from dbt_charts.core.compile.schema import board_keys_with, keys_with


def test_every_authored_board_field_is_classified() -> None:
    """``Content`` and ``Extends`` partition ``AuthoredBoard``'s top-level fields.

    A content field nobody classified reads as "not a board" to core's listing
    and "blank draft" to Cloud's triage — the bug that has already been fixed
    twice (``rows``-only, then ``extends``). A new top-level field fails here
    until someone decides which side it belongs on.
    """
    declares_content = {"rows", "cols", "grid", "tabs", "charts", "text", "queries"}
    inherits_content = {"extends"}
    configures_only = {
        "aliases",
        "auto_link",
        "cache",
        "card_gap",
        "chart_focus",
        "details",
        "height",
        "html_policy",
        "id",
        "incremental",
        "notes",
        "regional",
        "schema_version",
        "source",
        "style",
        "tags",
        "title",
        "variables",
        "visible",
        "width",
    }

    assert board_keys_with(Content) == declares_content
    assert board_keys_with(Extends) == inherits_content
    assert (
        set(AuthoredBoard.model_fields)
        == declares_content | inherits_content | configures_only
    )


def test_board_keys_are_top_level_only() -> None:
    """``board_keys_with`` answers about the board's own mapping.

    Its callers check a top-level YAML mapping, where a name that only exists
    deeper in the graph — a chart's ``label``, a callout's ``message`` — would
    misfire as a board-level key.
    """
    assert board_keys_with(DisplayText) == {"title", "notes"}
    assert keys_with(DisplayText) > board_keys_with(DisplayText)


def test_keys_with_reaches_every_depth() -> None:
    """``keys_with`` answers about names at any depth — how the consumers walk."""
    assert keys_with(Url) == {"link", "header_link"}
    assert keys_with(Markdown) == {"text"}
    assert keys_with(DisplayText) == {
        "expanded_title",
        "label",
        "message",
        "notes",
        "subtitle",
        "summary",
        "title",
    }


def test_unattached_facet_is_an_error() -> None:
    """An empty key set silently disables the gate that reads it."""

    class Unattached(Facet):
        pass

    with pytest.raises(ValueError, match="Unattached"):
        keys_with(Unattached)
    with pytest.raises(ValueError, match="Unattached"):
        board_keys_with(Unattached)

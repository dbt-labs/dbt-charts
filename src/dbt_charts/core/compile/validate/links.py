"""Compile-time validation of authored link schemes.

Rejects a chart ``link:``, table column ``link:``/``header_link:``, or
footer ``link:`` whose scheme is not a board path, http, https, or mailto.
Walks the same shape as ``validate/formats.py``'s ``_iter_format_slots``,
keyed on the ``Url`` facet (``compile/models/markers.py``) instead of a
fixed field-name set.

Compile-time validation cannot see two cases: a table cell/row link is built
from query row data (unknown until execute), and a chart link's sentinel
prefix is stripped only after Vega has rendered it. Both are re-checked at
render time by ``core.render.svg_utils.checked_href`` — see
``core/render/converters/chart.py`` and ``core/render/chart/table_support.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.models.board.normalized import Board
from dbt_charts.core.compile.models.markers import Url
from dbt_charts.core.compile.models.style.authored.table import TableColumnConfig
from dbt_charts.core.diagnostics.codes_compile import (
    ERR_LINK_SCHEME_UNANCHORED,
    ERR_LINK_SCHEME_UNSAFE,
)
from dbt_charts.core.links import is_safe_href, link_scheme

# A scheme can't contain `/`, `?` or `#`, so once the literal prefix before
# `{{` holds one, data can't complete a scheme (`sales?region={{ x }}` is safe).
_ANCHOR_CHARS = frozenset("/?#")


def _is_url_field(model: type[BaseModel], name: str) -> bool:
    return any(isinstance(m, Url) for m in model.model_fields[name].metadata)


def _iter_url_slots(
    node: object,  # type-state: object_annotation — generic tree walker narrowed inside the body
    path: str,
) -> Iterator[tuple[str, str, type[BaseModel] | None]]:
    """Yield (field_path, link, owning_model) per ``Url``-faceted string."""
    if isinstance(node, BaseModel):
        model = type(node)
        for name in model.model_fields:
            if name == "query":  # SQL text and source/cache config, no link slots
                continue
            value = getattr(node, name)
            if value is None:
                continue
            child = f"{path}.{name}"
            if isinstance(value, str) and _is_url_field(model, name):
                yield child, value, model
            else:
                yield from _iter_url_slots(value, child)
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_url_slots(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _iter_url_slots(value, f"{path}.{index}")


def _check_link(link: str, field_path: str, exempt_from_anchoring: bool) -> None:
    """Raise unless *link*'s scheme (or template-anchored prefix) is safe.

    The literal text before the first ``{{`` fixes the final scheme:
    ``/x?id={{id}}`` is safe whatever the row holds; ``java{{x}}`` is not.
    ``exempt_from_anchoring`` is set by the caller for a link whose render
    path re-checks the fully-resolved value with ``checked_href`` regardless
    of anchoring: a table column link, or a table chart's own chart-root
    ``link:`` (both resolve through ``core/render/chart/table_support.py``).
    """
    prefix = link.split("{{", 1)[0]
    if not is_safe_href(prefix):
        raise CompilationError.from_code(
            ERR_LINK_SCHEME_UNSAFE,
            link=link,
            scheme=link_scheme(prefix),
            field_path=field_path,
        )
    anchored = link_scheme(prefix) is not None or not _ANCHOR_CHARS.isdisjoint(prefix)
    if "{{" in link and not anchored and not exempt_from_anchoring:
        raise CompilationError.from_code(
            ERR_LINK_SCHEME_UNANCHORED, link=link, field_path=field_path
        )


def validate_board_links(board: Board) -> None:
    """Walk a normalized Board tree, validating every authored link's scheme."""
    _validate_board_links(board, set())


def _validate_board_links(board: Board, validated: set[int]) -> None:
    # Nested boards first — normalization hoists a nested board's charts into
    # every ancestor's `charts` registry by reference (see
    # `validate/formats.py`'s `_validate_board` for the same shape), so
    # `validated` is shared across the recursion to walk each chart once.
    for item in board.layout.items:
        if item.type == "board" and item.board is not None:
            _validate_board_links(item.board, validated)

    if board.authored_style is not None:
        for field_path, link, owner in _iter_url_slots(board.authored_style, "style"):
            _check_link(link, field_path, owner is TableColumnConfig)

    for chart_id, chart in board.charts.items():
        if id(chart) in validated:
            continue
        validated.add(id(chart))
        table_link_path = f"charts.{chart_id}.link" if chart.type == "table" else None
        for field_path, link, owner in _iter_url_slots(chart, f"charts.{chart_id}"):
            exempt = owner is TableColumnConfig or field_path == table_link_path
            _check_link(link, field_path, exempt)

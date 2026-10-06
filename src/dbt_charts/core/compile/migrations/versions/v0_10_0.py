"""Version-migration module for the 0.9.0 -> 0.10.0 boundary.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place. See ``migrations/AGENTS.md``'s "If you catch yourself thinking..."
table.

Changes in this release:

- **`legend.position` became a mapping** of `edge`, `align` and `overlay`: the
  key survives and its value's shape changed, so this is identity-path `Move`s
  carrying an `expansion` (a string rewrites to its mapping; a mapping is never
  touched; `sub_board_prefixes` reaches nested boards). A cardinal pins `overlay: false`
  and leaves `align` to the engine (start; a pie table's own default); a corner pins its edge, alignment and `overlay: true`.
  A pie's key never overlays, so `overlay` is dropped there.
- **The heading ramp moves to the font registry**: ``style.title.sizes``
  becomes ``style.fonts.heading.sizes``. One ``Move`` per board-owned
  ``style:`` block, resolved mechanically from ``AuthoredBoard`` down to
  ``BOARD_NESTING_DEPTH`` levels of nested boards.
- **Chart-level heading ramps are removed**: ``style.charts.title.sizes``
  (and ``style.charts.<family>.title.sizes``) and a chart-local
  ``style.title.sizes`` have no successor, because the ramp is a board-wide
  font. Each is a ``Deletion`` that can only reach a chart-level style.
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.migrations.migrations import (
    Deletion,
    MappedScalar,
    Move,
    YamlKeyPath,
    _child_positions,
    _declared_names,
    _declares_tail,
    _relative_field_paths,
    _schema_path_exists,
    _without_composition,
    board_nesting_prefixes,
    suffix_rename_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonObject,
    YamlSchemaCatalog,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

LEGEND_POSITION_TAIL: YamlKeyPath = ("legend", "position")


LEGEND_POSITION_EXPANSION: dict[str, dict[str, MappedScalar]] = {
    "left": {"edge": "left", "overlay": False},
    "right": {"edge": "right", "overlay": False},
    "top": {"edge": "top", "overlay": False},
    "bottom": {"edge": "bottom", "overlay": False},
    "top-left": {"edge": "top", "align": "start", "overlay": True},
    "top-right": {"edge": "top", "align": "end", "overlay": True},
    "bottom-left": {"edge": "bottom", "align": "start", "overlay": True},
    "bottom-right": {"edge": "bottom", "align": "end", "overlay": True},
}

LEGEND_POSITION_DROP_NOTES: dict[str, str] = {
    "overlay": (
        "A pie legend corner (`top-left`, `top-right`, `bottom-left`, "
        "`bottom-right`) floated over the wedges. A pie key never overlays, so "
        "the corner became its edge and alignment, and the overlay was dropped."
    )
}

# `_relative_field_paths` treats a board nested in a board as opaque, so deeper
# boards are reached by composing the one-level steps. Past this depth a board
# ramp is left in place and the board fails to parse loudly.
BOARD_NESTING_DEPTH = 3

_BOARD_STEP_SEGMENTS = frozenset({"rows", "cols", "grid", "items", "item", "tabs", "*"})

_RAMP_TAIL: YamlKeyPath = ("style", "title", "sizes")

_RAMP_DELETION_REASON = (
    "The H1-H6 heading ramp is one board-wide font, so a chart-level "
    "title.sizes was dropped. Set style.fonts.heading.sizes to resize headings."
)


def _board_style_paths() -> list[tuple[str, ...]]:
    """Paths of every board-owned ``style`` block, root included."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    try:
        one_level = dict.fromkeys(
            _relative_field_paths(AuthoredBoard, frozenset(), frozenset({("style",)}))
        )
    finally:
        # Not persisted across calls -- see suffix_rename_moves's docstring.
        _relative_field_paths.cache_clear()
    prefixes = {
        path[:-1]
        for path in one_level
        if path[-1:] == ("style",)
        and path[:-1]
        and set(path[:-1]) <= _BOARD_STEP_SEGMENTS
    }
    # A step that is two others end to end adds nothing once steps compose.
    steps = [
        p
        for p in prefixes
        if not any(p[: len(q)] == q and p[len(q) :] in prefixes for q in prefixes)
    ]
    levels: list[tuple[str, ...]] = [()]
    frontier: list[tuple[str, ...]] = [()]
    for _ in range(BOARD_NESTING_DEPTH):
        frontier = [(*base, *step) for base in frontier for step in steps]
        levels.extend(frontier)
    return [(*prefix, "style") for prefix in dict.fromkeys(levels)]


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.9.0 -> 0.10.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    nesting = board_nesting_prefixes(AuthoredBoard)
    return _heading_ramp_moves(source_schema, target_schema, catalog) + tuple(
        dataclasses.replace(
            move,
            expansion=LEGEND_POSITION_EXPANSION,
            drop_notes=LEGEND_POSITION_DROP_NOTES,
            sub_board_prefixes=nesting,
        )
        for move in suffix_rename_moves(
            AuthoredBoard,
            source_schema,
            target_schema,
            [(LEGEND_POSITION_TAIL, LEGEND_POSITION_TAIL)],
            catalog=catalog,
        )
    )


def _heading_ramp_moves(
    source_schema: str, target_schema: str, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:

    source_schema_obj = catalog.schema_for(source_schema)
    target_schema_obj = catalog.schema_for(target_schema)
    result: list[Move] = []
    for path in _board_style_paths():
        old = (*path, "title", "sizes")
        new = (*path, "fonts", "heading", "sizes")
        if _schema_path_exists(source_schema_obj, old) and _schema_path_exists(
            target_schema_obj, new, open_map_keys=True
        ):
            result.append(Move(source_schema, target_schema, old, new))
    return tuple(result)


def _chart_types_with_title_ramp(schema: JsonObject) -> tuple[str, ...]:
    """Chart ``type:`` literals whose own ``style.title`` declares ``sizes``."""
    definitions = schema["$defs"]
    assert isinstance(definitions, dict)
    types: set[str] = set()
    for variant in definitions.values():
        assert isinstance(variant, dict)
        properties = variant.get("properties")
        type_schema = properties.get("type") if isinstance(properties, dict) else None
        enum = type_schema.get("enum") if isinstance(type_schema, dict) else None
        own = _without_composition(variant)
        if (
            isinstance(enum, list)
            and own is not None
            and _declares_tail(schema, [own], _RAMP_TAIL)
        ):
            types.update(t for t in enum if isinstance(t, str))
    return tuple(sorted(types))


def _chart_families_with_title_ramp(schema: JsonObject) -> tuple[str, ...]:
    """``style.charts`` families whose ``title`` declares ``sizes``."""
    charts = [schema]
    for key in ("style", "charts"):
        charts = _child_positions(schema, charts, key)
    return tuple(
        sorted(
            family
            for family in _declared_names(schema, charts)
            if _declares_tail(
                schema, _child_positions(schema, charts, family), ("title", "sizes")
            )
        )
    )


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return the Deletions of chart-level ``title.sizes``."""
    source_schema_obj = catalog.schema_for(source_schema)
    tails = [
        (*prefix, "title", "sizes")
        for prefix in [
            ("style", "charts"),
            *(
                ("style", "charts", family)
                for family in _chart_families_with_title_ramp(source_schema_obj)
            ),
        ]
        if _schema_path_exists(source_schema_obj, (*prefix, "title", "sizes"))
    ]
    deletions = [
        Deletion(source_schema, target_schema, tail, reason=_RAMP_DELETION_REASON)
        for tail in tails
    ]
    deletions.extend(
        Deletion(
            source_schema,
            target_schema,
            _RAMP_TAIL,
            reason=_RAMP_DELETION_REASON,
            chart_type=chart_type,
        )
        for chart_type in _chart_types_with_title_ramp(source_schema_obj)
    )
    return tuple(deletions)

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
- **`style.formats` names move under `style.formats.aliases`**, so the block's
  fixed keys cannot collide with a name an author chose. A trailing-``*`` `Move`
  per board-level `style.formats` (the board's and each tab's), following
  `sub_board_prefixes` into every nested board; an ``aliases:`` map already in
  the new shape stays put (``_source_locations``).
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.migrations.migrations import (
    MappedScalar,
    Move,
    YamlKeyPath,
    _relative_field_paths,
    _schema_path_exists,
    board_nesting_prefixes,
    suffix_rename_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

LEGEND_POSITION_TAIL: YamlKeyPath = ("legend", "position")

FORMAT_ALIASES_TAIL: YamlKeyPath = ("formats", "aliases")

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


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.9.0 -> 0.10.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    nesting = board_nesting_prefixes(AuthoredBoard)
    return _format_alias_moves(source_schema, target_schema, catalog, nesting) + tuple(
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


def _format_alias_moves(
    source_schema: str,
    target_schema: str,
    catalog: YamlSchemaCatalog,
    nesting: tuple[YamlKeyPath, ...],
) -> tuple[Move, ...]:
    """One Move per board-level `style.formats`: each flat name under `aliases`."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    try:
        paths = dict.fromkeys(
            _relative_field_paths(
                AuthoredBoard, frozenset(), frozenset({FORMAT_ALIASES_TAIL})
            )
        )
    finally:
        _relative_field_paths.cache_clear()
    source = catalog.schema_for(source_schema)
    target = catalog.schema_for(target_schema)
    return tuple(
        Move(
            source_schema,
            target_schema,
            (*path[:-1], "*"),
            (*path, "*"),
            sub_board_prefixes=nesting,
        )
        for path in paths
        if path[-2:] == FORMAT_ALIASES_TAIL
        and _schema_path_exists(source, (*path[:-1], "*"))
        and _schema_path_exists(target, (*path, "*"))
    )

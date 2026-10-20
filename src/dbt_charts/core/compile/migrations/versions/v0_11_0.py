"""Version-migration module for the 0.10.0 -> 0.11.0 boundary.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place. See ``migrations/AGENTS.md``'s "If you catch yourself thinking..."
table.

Changes in this release:

- **Five stray scaffold aliases are retired** from `dbt-grays` and `dbt-creams`:
  `separator`, `fill-muted`, `spark-empty` and `placeholder` respell to the
  ladder step they sat nearest (`border`, `grid`, `grid-emphasis`, `muted`) and
  `accent` to `info.solid-hover`, the blue stark, clarity, paper and vivid now use. Identity-path
  `Move`s with a `value_map`, on every color field the 0.10.0 grammar carries,
  `style.palettes.*` and `style.charts.category_colors.*.values.*`, nested
  boards included. The respell is not hex-identical. A board older than 0.10.0
  is renamed to the 0.10.0 spelling first (`row.rule.color` becomes
  `row.divider.color`) and respelled after. A field added after 0.10.0 holds no
  retired spelling, so none is declared for it.
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.migrations.migrations import (
    MappedScalar,
    Move,
    YamlKeyPath,
    board_nesting_prefixes,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

_SCAFFOLD_ALIAS_STEPS = {
    "separator": "border",
    "fill-muted": "grid",
    "spark-empty": "grid-emphasis",
    "placeholder": "muted",
    "accent": "info.solid-hover",
}

SCAFFOLD_ALIAS_RESPELL: dict[MappedScalar, MappedScalar] = {
    f"{palette}.{alias}": step if "." in step else f"{palette}.{step}"
    for palette in ("dbt-grays", "dbt-creams")
    for alias, step in _SCAFFOLD_ALIAS_STEPS.items()
}

# Self-mapped current spellings keep the identity-Move contract: a value the
# map leaves alone is one it names.
_SCAFFOLD_ALIAS_VALUE_MAP: dict[MappedScalar, MappedScalar] = {
    **{current: current for current in set(SCAFFOLD_ALIAS_RESPELL.values())},
    **SCAFFOLD_ALIAS_RESPELL,
}

_CATEGORY_COLOR_VALUES: YamlKeyPath = (
    "style",
    "charts",
    "category_colors",
    "*",
    "values",
    "*",
)


def _scaffold_alias_moves(
    source_schema: str,
    target_schema: str,
    catalog: YamlSchemaCatalog,
    nesting: tuple[YamlKeyPath, ...],
) -> tuple[Move, ...]:
    """Respell the retired scaffold aliases on every field a color token reaches.

    The field paths are the 0.9.0 respell's walk of the board model, gated on the
    source grammar, so a field 0.10.0 introduced is included, and each Move
    reaches nested boards through ``sub_board_prefixes``.
    """
    from dbt_charts.core.compile.migrations.versions import v0_9_0

    token_moves = [
        *v0_9_0.moves(source_schema, target_schema, catalog=catalog),
        Move(
            source_schema,
            target_schema,
            _CATEGORY_COLOR_VALUES,
            _CATEGORY_COLOR_VALUES,
        ),
    ]
    return tuple(
        dataclasses.replace(
            move, value_map=_SCAFFOLD_ALIAS_VALUE_MAP, sub_board_prefixes=nesting
        )
        for move in token_moves
    )


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.10.0 -> 0.11.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    return _scaffold_alias_moves(
        source_schema,
        target_schema,
        catalog,
        board_nesting_prefixes(AuthoredBoard),
    )

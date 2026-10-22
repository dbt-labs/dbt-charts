"""Version-migration module for the 0.10.0 -> 0.11.0 boundary.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place. See ``migrations/AGENTS.md``'s "If you catch yourself thinking..."
table.

Changes in this release:

- **Scaffold steps are addressed by number.** `dbt-grays` and `dbt-creams` named
  their twelve steps after jobs (`canvas`, `muted`, `ink`, ...); the steps keep
  their order, lightest (1) to darkest (12), and are now addressed by that
  number, with step `0` white and `100` black. Each retired name
  (`RETIRED_SCAFFOLD_TOKEN_MAP`) respells to the step at the same index:
  `dbt-grays.muted`, `dbt-creams.border` and `chrome.heading` become
  `dbt-grays.8`, `dbt-creams.4` and `chrome[10]`. Identity-path `Move`s from
  `token_respell_moves` on every color-token field, `single_series_palette`
  items, `style.palettes.*` and `style.roles.*` values and
  `style.charts.category_colors.*.values.*`, nested boards followed through
  `sub_board_prefixes`. The map is idempotent: no value it produces is a key. A
  scaffold bound to a role other than `chrome` (`palettes: {surface:
  dbt-creams}`, `surface.canvas`) is out of an exact-match map's reach and
  raises `ERR-COLOR-TOKEN`.
- **`paper` binds `chrome` to the cream scaffold** (`palettes.chrome: dbt-creams`),
  so an authored `chrome[N]` or `chrome.white`-style token now resolves on the
  cream ladder under `theme: paper` where it resolved gray. The token is
  unchanged and valid in both grammars, so it cannot be migrated or detected.
  The same holds for the `ink` theme role, which follows the bound scaffold.
- **Five stray scaffold aliases are retired** from `dbt-grays` and `dbt-creams`:
  `separator`, `fill-muted`, `spark-empty` and `placeholder` respell to the step
  they sat nearest (4, 3, 5 and 8) and `accent` to `info.solid-hover`, the blue
  stark, clarity, paper and vivid now use (neon: `info.border`). They sit in the
  same `RETIRED_SCAFFOLD_TOKEN_MAP` as the job names, straight to the final
  spelling, so a board never sees an intermediate one. The respell is not
  hex-identical. A board older than 0.10.0 is renamed to the 0.10.0 spelling
  first (`row.rule.color` becomes `row.divider.color`) and respelled after. A
  field added after 0.10.0 holds no retired spelling, so none is declared for it.
- **`style.fonts.section` is reserved for the section-title size ramp.** A board
  that named one of its own fonts `section` now has it merged over the
  theme's `section`, whose `sizes` size every section title. The name is valid
  in both grammars, so it cannot be migrated or recognized. The detectable
  case, an authored `section` with a `size`, raises `The 'section' font takes
  sizes (H2–H6), not size.`
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.migrations.migrations import (
    MappedScalar,
    Move,
    YamlKeyPath,
    board_nesting_prefixes,
    token_respell_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

# A retired scaffold job name -> the numbered step at the same index of the
# unchanged light-to-dark ladder. Frozen: the names no longer exist in any
# palette.
_SCAFFOLD_STOP_BY_ROLE: dict[str, int] = {
    "canvas": 1,
    "surface-subtle": 2,
    "grid": 3,
    "border": 4,
    "grid-emphasis": 5,
    "disabled": 6,
    "subtitle": 7,
    "muted": 8,
    "inactive": 9,
    "heading": 10,
    "ink": 11,
    "void": 12,
}

# The five stray aliases retired alongside the job names, respelled straight to
# the final spelling: the step each sat nearest (`separator` -> `border` ->
# step 4), and `accent` to `info.solid-hover`, the blue the themes now use. A
# board never sees the intermediate job name.
_SCAFFOLD_ALIAS_STOPS: dict[str, int] = {
    "separator": 4,
    "fill-muted": 3,
    "spark-empty": 5,
    "placeholder": 8,
}
_ACCENT_SUCCESSOR = "info.solid-hover"


def _scaffold_alias_respell() -> dict[MappedScalar, MappedScalar]:
    value_map: dict[MappedScalar, MappedScalar] = {}
    for alias, stop in _SCAFFOLD_ALIAS_STOPS.items():
        for palette_name in ("dbt-grays", "dbt-creams"):
            value_map[f"{palette_name}.{alias}"] = f"{palette_name}.{stop}"
        value_map[f"chrome.{alias}"] = f"chrome[{stop}]"
    for palette_name in ("dbt-grays", "dbt-creams", "chrome"):
        value_map[f"{palette_name}.accent"] = _ACCENT_SUCCESSOR
    return value_map


SCAFFOLD_ALIAS_RESPELL: dict[MappedScalar, MappedScalar] = _scaffold_alias_respell()


def _retired_scaffold_token_map() -> dict[MappedScalar, MappedScalar]:
    value_map: dict[MappedScalar, MappedScalar] = dict(SCAFFOLD_ALIAS_RESPELL)
    for role, stop in _SCAFFOLD_STOP_BY_ROLE.items():
        for palette_name in ("dbt-grays", "dbt-creams"):
            value_map[f"{palette_name}.{role}"] = f"{palette_name}.{stop}"
        value_map[f"chrome.{role}"] = f"chrome[{stop}]"
    # Current spellings mapped to themselves: an identity Move's map is total
    # over the field's live domain, and the value gate needs something to prove
    # itself against (see `v0_9_0._retired_token_value_map`).
    for current in (
        "dbt-grays.5",
        "dbt-creams.12",
        "chrome[2]",
        "chrome.white",
        _ACCENT_SUCCESSOR,
    ):
        value_map[current] = current
    return value_map


RETIRED_SCAFFOLD_TOKEN_MAP: dict[MappedScalar, MappedScalar] = (
    _retired_scaffold_token_map()
)


_ROLES_AND_PALETTES: tuple[YamlKeyPath, ...] = (
    ("style", "palettes", "*"),
    ("style", "roles", "*"),
)


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.10.0 -> 0.11.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    nesting = board_nesting_prefixes(AuthoredBoard)
    return tuple(
        dataclasses.replace(move, sub_board_prefixes=nesting)
        for move in token_respell_moves(
            source_schema,
            target_schema,
            RETIRED_SCAFFOLD_TOKEN_MAP,
            catalog=catalog,
            category_pins=True,
            open_map_values=_ROLES_AND_PALETTES,
        )
    )

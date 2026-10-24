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
- **The theme `projection` key is removed** from the geo families: `Deletion`s
  of `style.charts.geoshape.projection`, `style.charts.point_map.projection` and
  the chart-local `style.projection` on `map`, `geoshape`, `point_map` and
  `bubble_map`. Nothing ever read it; a chart's projection is its root
  `projection:`, else its geo source's.
- **`style.muted` becomes the secondary font's color**: `style.muted` becomes
  `style.fonts.secondary.color` on the root, every nested board and every tab
  item (`tabs.items.*.style`), at any depth: one `Move` whose
  `sub_board_prefixes` follow both. The field no longer exists in the models, so neither
  the 0.9.0 variant respell nor this boundary's scaffold respell reaches it;
  the `Move`'s `rewrite` applies both on the way (`category_dark.blue` becomes
  `category.blue.dark`, `dbt-grays.muted` becomes `dbt-grays.8`).
"""

from __future__ import annotations

import dataclasses

from dbt_charts.core.compile.migrations.migrations import (
    Deletion,
    Dropped,
    MappedScalar,
    Move,
    YamlKeyPath,
    board_nesting_prefixes,
    token_respell_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    JsonValue,
    YamlSchemaCatalog,
)
from dbt_charts.core.diagnostics.hints import respell_retired_variant_token

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


def _respell_secondary_ink(value: JsonValue) -> JsonValue | Dropped:
    if not isinstance(value, str):
        return value
    token = respell_retired_variant_token(value) or value
    return RETIRED_SCAFFOLD_TOKEN_MAP.get(
        token, token
    )  # type-state: silent_fallback — absent from the map means not a retired scaffold spelling; it passes through unchanged


_ROLES_AND_PALETTES: tuple[YamlKeyPath, ...] = (
    ("style", "palettes", "*"),
    ("style", "roles", "*"),
)


_PROJECTION_REASON = (
    "The theme projection was never read. Set `projection:` on the chart, or "
    "use a geo_source, which carries its own."
)
_GEO_CHART_TYPES = ("map", "geoshape", "point_map", "bubble_map")


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return the Deletions of the geo theme ``projection`` key."""
    theme = tuple(
        Deletion(
            source_schema,
            target_schema,
            ("style", "charts", family, "projection"),
            reason=_PROJECTION_REASON,
        )
        for family in ("geoshape", "point_map")
    )
    chart_local = tuple(
        Deletion(
            source_schema,
            target_schema,
            ("style", "projection"),
            reason=_PROJECTION_REASON,
            chart_type=chart_type,
        )
        for chart_type in _GEO_CHART_TYPES
    )
    return theme + chart_local


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.10.0 -> 0.11.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    nesting = board_nesting_prefixes(AuthoredBoard)
    # A tab item is its own model, not an AuthoredBoard, so the board nesting
    # walk never steps into one; following it as a prefix reaches its own
    # `style:` and, prefixes composing, tabs and boards nested to any depth.
    secondary_ink = Move(
        source_schema,
        target_schema,
        ("style", "muted"),
        ("style", "fonts", "secondary", "color"),
        rewrite=_respell_secondary_ink,
        sub_board_prefixes=(*nesting, ("tabs", "items", "*")),
    )
    return (
        *(
            dataclasses.replace(move, sub_board_prefixes=nesting)
            for move in token_respell_moves(
                source_schema,
                target_schema,
                RETIRED_SCAFFOLD_TOKEN_MAP,
                catalog=catalog,
                category_pins=True,
                open_map_values=_ROLES_AND_PALETTES,
            )
        ),
        secondary_ink,
    )

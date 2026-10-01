"""Version-migration module for the 0.7.0 -> 0.9.0 boundary.

THIS FILE IS SCHEMA CHANGES ONLY. Do not add an entry here for anything that
is not a key rename, a key removal, or an authored value's meaning changing
in place. See ``migrations/AGENTS.md``'s "If you catch yourself thinking..."
table.

Changes in this release:

- **Categorical color variants gain a third token segment** (`category.blue.dark`,
  `vivid-10.3.pale`, `tableau.1.deep`) and the four hard-coded companion roles
  (`category_dark`/`category_light`/`category_ghost`/`category_ink`) retire --
  every categorical palette now derives every tier live (`variant()`,
  `palette.py`). Two grammar changes, neither schema-visible (a retired token
  spelling is still a valid string; a retired role key is still a valid entry
  in the open map `style.palettes`), so both are declared here even though
  neither shows up in `_current_schema_rejections`:

  - `TokenRespell` (below, as identity-path `Move`s: `old_path == new_path`,
    carrying `RETIRED_TOKEN_VALUE_MAP`) respells every retired color-token or
    palette-name spelling to its literal-variant replacement, wherever a
    color or palette value is authored -- scalar leaves (`color`, `fill`,
    `stroke`, ...), whole-list palette-name fields (`palette`,
    `single_series_palette`, each addressed both as a bare scalar and as a
    list item), and every value inside `style.palettes` (a role's value can
    itself be a retired whole-name spelling, regardless of which role holds
    it -- `("style", "palettes", "*")`, an ungated trailing wildcard, see
    `_source_locations`). The path pattern reaches chart-level tokens
    (`charts.<id>.style.….color: category_dark[2]`) by construction: the
    resolved paths come from `_relative_field_paths` walking the whole
    `AuthoredBoard` model tree, `charts:`'s open map (`dict[str, AuthoredChart]`)
    included -- `charts:` is keyed by the author, so the `*` binds an
    author-named chart id. What keeps that safe is what the Move may touch:
    only a leaf *string value* that equals a retired spelling exactly, never
    a key, and never a value that does not match
    (`_identity_value_would_change`'s gate) -- an author-named chart id is
    traversed to reach its style block, never rewritten itself.
  - `MapKeyDeletion` (below) strikes the four now-meaningless role keys from
    `style.palettes` wherever that field is authored -- board root, theme
    fragment, or nested board under rows/cols/grid/tabs. `Deletion` cannot
    express this: `style.palettes` is `dict[str, PaletteName]`
    (`models/style/theme/style.py`), an open map, and `_declares_tail`
    deliberately never consults `additionalProperties` (an author-named
    role key must never be mistaken for a retired one). `MapKeyDeletion`
    instead names the *container* (`style.palettes`, a genuinely declared
    field) and strikes a closed, literal key set inside it.

- **`style.text.column.gap` removed.** Prose columns sit on the board's card
  grid, so the gutter between two columns is the layout's card gap + `2 * card_padding`
  and no longer a text setting. A `Deletion` on the tail
  `("style", "text", "column", "gap")` strips the authored value and reports
  its `reason`; `style.text.column.max_number`, `max_chars` and `rule` stay.

**Reach**: `TokenRespell`'s paths come from `_relative_field_paths` walking
`AuthoredBoard`'s model tree once, from the root -- the same walk
`suffix_rename_moves` uses for every other rename in this package, with the
same limit. Reached: the root board's own leaves, every chart's style block,
a nested board's `style:` block one level down, and a chart authored
directly as a list item under `rows`/`cols`/`grid.items`/`tabs.items.*.cols`.
Not reached: a nested sub-board's *own* `charts:` map --
`_relative_field_paths`' `seen` guard makes a self-nested `AuthoredBoard`
opaque past the first level. Not a regression this boundary introduces:
`v0_7_0.py`'s `THRESHOLD_RENAMES`/`TOTAL_FORMAT_RENAMES` share the exact
same reach. Pinned by `test_a_nested_sub_boards_own_chart_token_is_a_known_gap`
(unreached) and `test_a_chart_directly_under_a_tab_item_is_respelled`
(reached), both in `test_token_respell_migration.py`.

A retired token authored as a **list item** respells both in memory and
(for a block-style list) on disk; a flow-style list still refuses. See the
worksheet's Implementation Progress and
`test_token_respell_migration.py::TestRetiredTokensInsideAList` /
`test_yaml_patch.py::TestScalarSequenceItemLeafRewrite` for the exact
mechanics and the still-refused shapes.
"""

from __future__ import annotations

from dbt_charts.core.compile.migrations.migrations import (
    Deletion,
    MapKeyDeletion,
    MappedScalar,
    Move,
    YamlKeyPath,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)
from dbt_charts.core.diagnostics.hints import (
    RETIRED_VARIANT_FAMILIES,
    RETIRED_VARIANT_ROLE_PREFIX,
    RETIRED_VARIANT_SUFFIX_SUCCESSORS,
    respell_retired_variant_token,
)

THEME_RENAMES: dict[MappedScalar, MappedScalar] = {}

# Every scalar leaf field that may hold a color TOKEN (not just a literal hex
# or rgb()/rgba() string) -- every `Color()`-annotated field in
# `compile/models` (grepped, not guessed: `rg -n -B2 "Color\(\)" src/.../models`).
_SCALAR_TOKEN_FIELD_NAMES: tuple[str, ...] = (
    "color",
    "fill",
    "stroke",
    "null_color",
    "glyph_color",
    "static",
    "positive",
    "negative",
    "warning",
    "info",
    "color_active",
    "color_inactive",
    "color_disabled",
    "focus_color",
    "drop_line_color",
    "accent",
    "muted",
    "background",
)
_SCALAR_TOKEN_FIELD_TAILS: tuple[YamlKeyPath, ...] = tuple(
    (name,) for name in _SCALAR_TOKEN_FIELD_NAMES
)

# Whole-list palette-name fields -- authored either as a bare scalar
# (`palette: vivid-10-dark`) or as a list (`single_series_palette:
# [category_dark.blue]`); both are declared below, the bare form as an
# ordinary field-path Move, the list form via a trailing-wildcard Move over
# the same path (`(*path, "*")`) that reaches each item.
_PALETTE_NAME_FIELD_TAILS: tuple[YamlKeyPath, ...] = (
    ("palette",),
    ("single_series_palette",),
)

# The alias vocabulary every shipped categorical palette shares (vivid-10.yml:
# "This vocabulary is identical in editorial-10.yml"). A role-indirected
# token (`category_dark.blue`) does not know which family `category` is
# bound to on a given board's theme, so the respell must cover every alias
# any shipped family could resolve, not just the family a test board happens
# to use.
ROLE_ALIASES: tuple[str, ...] = (
    "blue",
    "sky",
    "green",
    "gold",
    "orange",
    "purple",
    "sage",
    "brown",
    "gray",
    "charcoal",
)

# No categorical palette ships more than this many stops today (vivid-10,
# editorial-10, tableau: 10; hero-6 and the five category-6-tonal-* palettes:
# 6) -- `test_no_categorical_palette_exceeds_the_respell_slot_ceiling` in
# `tests/core/compile/test_token_respell_migration.py` fails loud if a future
# palette ever ships more, so this ceiling cannot go silently stale.
MAX_CATEGORICAL_SLOTS = 10

# The two families that shipped companion files (`RETIRED_VARIANT_FAMILIES`,
# `core/diagnostics/hints.py`), and their own stop count (both 10 today) --
# read from the files directly here, one time, not at migration run time:
# this module has to keep respelling correctly after the eight companion
# files are deleted from the catalog, so the map below is a frozen data
# snapshot, never a live `list_palettes(...)` read.
_DOOMED_FAMILY_SLOTS: dict[str, int] = dict.fromkeys(RETIRED_VARIANT_FAMILIES, 10)


def _retired_token_value_map() -> dict[MappedScalar, MappedScalar]:
    """Every retired color-token / palette-name spelling this boundary respells.

    A regex-shaped rewrite ("for any N, any alias") has no home in
    ``Move.value_map`` -- an exact-match table -- so this enumerates the
    domain the retired spellings actually range over instead: it is closed,
    not open-ended, because the only alias names that were ever addressable
    are the ones the shipped catalog declared, and no shipped palette (before
    or after this PR) exceeds ``MAX_CATEGORICAL_SLOTS`` stops.
    """
    retired: list[str] = []
    for suffix in RETIRED_VARIANT_SUFFIX_SUCCESSORS:
        old_role = f"{RETIRED_VARIANT_ROLE_PREFIX}_{suffix}"
        # Bare whole-list role reference: `palette: category_dark`.
        retired.append(old_role)
        # Bracket role[N]: `color: category_dark[2]`.
        retired.extend(
            f"{old_role}[{slot}]" for slot in range(1, MAX_CATEGORICAL_SLOTS + 1)
        )
        # Dotted role.alias: `color: category_dark.blue`.
        retired.extend(f"{old_role}.{alias}" for alias in ROLE_ALIASES)
        # Direct family name + integer slot (color()'s own "palette.slot"
        # grammar, 1-indexed, never alias for a stop-list family): `color:
        # vivid-10-dark.3`, and the bare whole-list form `palette:
        # vivid-10-dark`.
        for family, slots in _DOOMED_FAMILY_SLOTS.items():
            old_family = f"{family}-{suffix}"
            retired.append(old_family)
            retired.extend(f"{old_family}.{slot}" for slot in range(1, slots + 1))
            # The whole-list `:N`/`_r`/`:N_r` shorthand on a retired family
            # (`palette: vivid-10-dark:4`) compiled at the branch point too,
            # so it is a retired spelling like the bare name -- and a finite
            # one: `slots` bounds N, and `_r` either trails or does not.
            retired.append(f"{old_family}_r")
            for slot in range(1, slots + 1):
                retired.append(f"{old_family}:{slot}")
                retired.append(f"{old_family}:{slot}_r")
    value_map: dict[MappedScalar, MappedScalar] = {}
    for spelling in retired:
        replacement = respell_retired_variant_token(spelling)
        if replacement is None:
            # The enumeration above and the shared shapes have drifted apart
            # -- a programming error, never a spelling to leave unmigrated.
            raise ValueError(
                f"{spelling!r} is enumerated as retired but "
                "respell_retired_variant_token names no replacement for it"
            )
        value_map[spelling] = replacement
    # A handful of currently-valid values, mapped to themselves -- an
    # identity-path Move's `value_map` is total over its field's real domain
    # (`Move.value_map`'s own comment), and `theme:`'s `THEME_VALUE_MAP` sets
    # the precedent: current names included, mapped to themselves, so the
    # value gate (`_identity_value_would_change`) has something to prove
    # itself against for spellings that closely resemble a retired one but
    # are not (`test_no_retired_path_survives_into_the_current_grammar`
    # probes every identity Move's self-mapped entries this way). Not
    # exhaustive -- the live token/palette-name domain is not a closed enum
    # the way `ThemeName` is -- just representative of each retired shape's
    # still-current sibling.
    for current in ("category[2]", "category.blue", "vivid-10", "vivid-10.3"):
        value_map[current] = current
    return value_map


RETIRED_TOKEN_VALUE_MAP: dict[MappedScalar, MappedScalar] = _retired_token_value_map()

# style.palettes.category_dark / _light / _ghost / _ink -- removed, not
# respelled: the four keys have no replacement spelling, only their *values*
# might (an author who wrote `category_dark: vivid-10-dark` loses the role
# entirely; if the same retired family name is used as some *other* role's
# value -- `category: vivid-10-dark` -- that value is respelled by the
# `style.palettes.*` Move below, the same as any other palette-name-typed
# field, independent of which key it sits under).
RETIRED_PALETTE_ROLE_KEYS: dict[str, str] = {
    f"{RETIRED_VARIANT_ROLE_PREFIX}_{suffix}": variant
    for suffix, variant in RETIRED_VARIANT_SUFFIX_SUCCESSORS.items()
}


def _map_key_deletion_message(role: str, variant: str) -> str:
    return (
        f"style.palettes.{role} is no longer a role: write category[N].{variant} "
        f"or category.<alias>.{variant}. Remove the key."
    )


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return the TokenRespell Move objects for the 0.7.0 -> 0.9.0 boundary.

    Every Move here is identity-path (``old_path == new_path``) -- a
    value-only rewrite on a field that never disappears from the grammar. The
    field *paths* are resolved mechanically from the model tree
    (``_relative_field_paths``, the same walk ``suffix_rename_moves`` uses),
    never hand-listed: that is what reaches every chart family's own style
    block and every nested board under rows/cols/grid/tabs without naming
    each one. Only the *tails* (leaf field names) are hand-declared, because
    that is the one thing the model tree cannot answer on its own -- see
    ``_SCALAR_TOKEN_FIELD_NAMES``'s docstring for how they were derived.
    """
    from dbt_charts.core.compile.migrations.migrations import (
        _relative_field_paths,
        _schema_path_exists,
    )
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    tails = frozenset(_SCALAR_TOKEN_FIELD_TAILS + _PALETTE_NAME_FIELD_TAILS)
    list_tails = frozenset(_PALETTE_NAME_FIELD_TAILS)
    try:
        paths = list(
            dict.fromkeys(_relative_field_paths(AuthoredBoard, frozenset(), tails))
        )
    finally:
        # Not persisted across calls -- see suffix_rename_moves's docstring
        # for why an uncleared memo pins hundreds of megabytes per process.
        _relative_field_paths.cache_clear()

    source_schema_obj = catalog.schema_for(source_schema)
    target_schema_obj = catalog.schema_for(target_schema)
    result: list[Move] = []
    for path in paths:
        tail = path[-1:]
        if tail not in tails:
            continue
        result.append(
            Move(source_schema, target_schema, path, path, RETIRED_TOKEN_VALUE_MAP)
        )
        # A palette-name field may also be authored as a list
        # (`single_series_palette: [category_dark.blue]`, `palette:
        # [category_dark[2], "#abc"]`) -- a *separate* trailing-wildcard Move
        # reaches each item. Not every "palette"-named field is list-typed
        # (the gradient/scale `palette:` on some models is a bare
        # `ScalePaletteName | str`, no list arm) -- `_relative_field_paths`
        # only knows field *names*, not types, so existence in both schemas
        # is checked explicitly here, the same guard
        # `suffix_rename_moves` applies before trusting a candidate.
        # `move_source_locations`'s `_identity_value_would_change` gate
        # skips this Move when the field is authored as a scalar (a list
        # index never matches) and skips the bare Move above when it is
        # authored as a list (a non-scalar value never matches
        # `_mapped_value`'s domain), so the two coexist without
        # double-firing on either shape.
        if (
            tail in list_tails
            and _schema_path_exists(source_schema_obj, (*path, "*"))
            and _schema_path_exists(target_schema_obj, (*path, "*"))
        ):
            result.append(
                Move(
                    source_schema,
                    target_schema,
                    (*path, "*"),
                    (*path, "*"),
                    RETIRED_TOKEN_VALUE_MAP,
                )
            )

    # style.palettes.* -- every role's *value* may itself be a retired
    # whole-name spelling, regardless of which role holds it. A trailing
    # wildcard is ungated (`_source_locations`: "a trailing `*` is ungated
    # ... no declaration produces one today") -- exactly what an open map's
    # own values need, since `_declares_tail` cannot answer "declared
    # property?" for a key the schema never names.
    result.append(
        Move(
            source_schema,
            target_schema,
            ("style", "palettes", "*"),
            ("style", "palettes", "*"),
            RETIRED_TOKEN_VALUE_MAP,
        )
    )
    return tuple(result)


def map_key_deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[MapKeyDeletion, ...]:
    """Return the MapKeyDeletion for the 0.7.0 -> 0.9.0 boundary: the four retired
    `style.palettes` role keys, struck wherever that field is authored."""
    return (
        MapKeyDeletion(
            source_schema,
            target_schema,
            ("style", "palettes"),
            {
                role: _map_key_deletion_message(role, variant)
                for role, variant in RETIRED_PALETTE_ROLE_KEYS.items()
            },
        ),
    )


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return the Deletion for the 0.7.0 -> 0.9.0 boundary: the authored column gap."""
    return (
        Deletion(
            source_schema,
            target_schema,
            ("style", "text", "column", "gap"),
            reason=(
                "Prose columns sit on the board's card grid now, so the gutter "
                "between them is the gap the layout puts between cards plus "
                "twice the card padding and cannot be set on the text. The "
                "authored column gap was dropped; "
                "style.text.column.max_number, max_chars and rule still apply."
            ),
        ),
    )

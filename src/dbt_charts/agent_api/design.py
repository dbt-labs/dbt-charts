"""Typed design verb — what is editable on the clickable board object at a path.

A path's type is a property of the document, not of the schema: `charts` is a
dict over a many-member chart union, so the schema alone cannot say what sits
under it. A *parsed* board can, because pydantic discriminated that union on
`type:` while parsing. This walks down the requested path through the parsed
instance tree and reads that node's own model out of the same `AuthorableSchema`
every other surface uses, so no consumer has to curate a path list and no
per-chart-type form ever gets written.

One target per request, described to its leaves. Building every target on the
board to answer one lookup is what forced the old surface to expand two style
groups and drop the other sixteen a chart carries — font size was unreachable
because of a cost the API's shape created, not anything about fonts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import sqlglot
import sqlglot.errors
from pydantic import BaseModel, ConfigDict, Field

from dbt_charts.core.compile.errors import CompilationError
from dbt_charts.core.compile.merge import scope_patch
from dbt_charts.core.compile.models.board.authored import AuthoredBoard, TabItem
from dbt_charts.core.compile.models.chart.authored import (
    CHART_SUPPORT_TABLE_SUPPORTED_TYPES,
    ChartSupportTablePerSeries,
    ChartSupportTableSource,
)
from dbt_charts.core.compile.models.markers import Channel, FontFamily, Format
from dbt_charts.core.compile.models.query.authored import (
    AuthoredCompactValuesQuery,
    AuthoredSqlQuery,
    AuthoredValuesQuery,
)
from dbt_charts.core.compile.models.refs import CrossFileRef
from dbt_charts.core.compile.models.style.authored import StylePatch
from dbt_charts.core.compile.models.variable.authored import Variable
from dbt_charts.core.compile.normalize.variables import (
    detect_variable_input_type,
    validate_choice_type,
    validate_variable_value,
)
from dbt_charts.core.compile.parse.parser import (
    compose_yaml,
    mapping_from_node,
    parse_mapping,
)
from dbt_charts.core.compile.parse.source_map import build_source_index_from_node
from dbt_charts.core.compile.schema.introspection import (
    AuthorableModel,
    AuthorableSchema,
    SchemaField,
    introspect,
)
from dbt_charts.core.compile.sql_guard import (
    SKELETON_PLACEHOLDER_PREFIX,
    build_skeleton,
)
from dbt_charts.core.compile.template.variables import (
    coerce_variable_values,
    variable_value_is_absent,
)
from dbt_charts.core.diagnostics.execution import ExecutionError, UnparseableSqlError
from dbt_charts.core.fonts import offered_font_families
from dbt_charts.core.project import Project

Widget = Literal["text", "number", "checkbox", "select", "combo", "list"]

# ---------------------------------------------------------------------------
# The tables below hold rules this module did not invent, so each says how it is
# held to its source of truth. "There is a passing test beside the constant"
# means three different things here, and only one of them is a guarantee:
#
#   GATED     the property sweeps in `tests/agent_api/test_design.py` call the
#             real validator, so a table that drifts from it fails them.
#   AGREED    no sweep can reach the rule — it is about a key *pair* (the sweeps
#             set one key at a time), or a value the compiler silently discards
#             (nothing raises), or a control the render sweep skips (it is bound
#             to the `list` widget for cost). A named test asserts the
#             compiler's behavior beside the panel's instead.
#   POLICY    nothing in the compiler agrees or disagrees. There is no source of
#             truth to drift from; the entry is a scoping decision with a reason.
#   STRUCTURE not a rule at all — a fact about the schema's own shape.
#
# Spelling is gated separately for all of them, by
# `test_every_rule_table_names_a_field_that_still_exists`: a renamed field
# otherwise leaves an entry that stops matching rather than failing.
# ---------------------------------------------------------------------------

# The six fields that hold child *targets* rather than properties: the target
# walk descends them, so the property walk must not. That exclusion is what
# bounds the recursion — the nested-model graph has seven cycles reachable from
# `AuthoredBoard` and every one of them runs through a layout container.
# STRUCTURE — pinned by `test_the_group_graph_is_acyclic_so_the_ancestor_guard_never_fires`.
_CHILD_TARGETS = frozenset({"rows", "cols", "grid", "tabs", "charts", "variables"})

# Authoring surfaces that belong to a different editor. `query`/`queries` are
# the query editor's, while the rest are 0..n sub-forms rather than controls,
# and each needs its own affordance before it can be edited here.
# POLICY — several categories in one set, and whether to split them is an open
# design question; membership is a scoping decision, not a copy of anything the
# compiler enforces.
_NOT_DESIGN = frozenset(
    {
        "query",
        "queries",
        # The board's default warehouse connection — query-layer, not design
        # (root `AGENTS.md` non-negotiable #6). A string parses and nothing in
        # `normalize/` checks the name, so the write gate cannot see it; the
        # failure lands at execute, where `resolve_source_config` raises
        # ERR-SOURCE-NOT-FOUND for every query on the board. The panel commits
        # on blur, so one stray focus repoints the whole board's data.
        "source",
        # A serving behavior flag — whether rendered links auto-resolve — not
        # an appearance. A design control that changes navigation is the wrong
        # affordance for it.
        "auto_link",
        # Query-result cache TTL — the query layer's, on the same footing as
        # `source`. Invisible until the walk recursed fully, because a nested
        # model projected to no control at all.
        "cache",
        # An HTML-rendering security tier, and a deployment gets a ceiling it
        # can pin below the authored value (`html_policy_ceiling`). Security
        # posture is not appearance, and a select whose chosen value a host may
        # legitimately override is a control that misreports what is in force.
        "html_policy",
        # `type` is the chart union's discriminator — the object's identity, not
        # a property of it. Offering it as a select meant one click could turn a
        # bar into a histogram beside a `support_table` the family does not
        # support, or into a `bar` whose multi-metric `y` now needs an `x`;
        # every such pair is a compiler rule the panel would have to re-encode.
        # Changing a chart's type is a structural edit, and the code panel is
        # where structure is edited.
        "type",
        # Identity, not a property of the object — `type`'s sibling, and a
        # silent no-op on both shapes a design target can take. On a chart the
        # compiler overwrites it from the `charts:` map key. On a *nested*
        # board it is never read at all: the explicit `board_id=` callers are
        # the nested-layout paths, and the root short-circuits before consulting
        # it. `TabLayout.id` (a URL param) is genuinely authorable and
        # unaffected — the tabs container is never a design target, only the
        # items under it are, which `test_a_layout_container_is_not_a_target`
        # pins.
        "id",
        # Two `list[str]` fields the `list` widget made reachable — `aliases` on
        # the board, `warnings_ignore` on every chart. The board's third such
        # field, `tags`, keeps its control: free-text labels with no vocabulary
        # to violate. Neither of these two is design: `aliases` is routing (URLs
        # that redirect here) and `warnings_ignore` silences a render lint. Both
        # also validate their *items* against a vocabulary the type does not
        # state — an alias must be absolute, a code must be a known one — so a
        # free-text list control on either is an edit that mostly writes a board
        # that stops parsing.
        "aliases",
        "warnings_ignore",
        # dct migrate-written only, informational (see AuthoredBoard.schema_version's
        # description) -- a design control would let a Cloud user hand-author a
        # value that lies about the file's actual grammar. Named by its YAML
        # key/alias, _schema_version, matching every other entry in this set:
        # introspection's field.name is the alias, not the Python attribute.
        "_schema_version",
        # Names a chart to show alone. A wrong name raises at normalize time
        # (past the parse gate), a right one collapses the board to one chart,
        # and on a nested board it is ignored entirely — every outcome is
        # structural rather than design.
        "chart_focus",
        "layers",
        "conditional_formatting",
        # Authoring sugar for `extends:`, desugared before the panel ever sees
        # an instance -- `getattr(instance, "theme", None)` always finds
        # nothing, so a control here renders empty. A writable control would
        # also fight `extends`, whose reconciliation with a theme value is
        # one-directional (extends -> theme), and on a nested board every
        # write would be silently discarded (`extends` is withheld there for
        # the same reason `_ROOT_ONLY` names it, but this field isn't in that
        # set).
        "theme",
    }
)

# Suppressed on every target, by dotted key rather than by field name — the
# names in `_NOT_DESIGN` mean what they say only at the target's own level.
# `style.charts` is the theme's per-family defaults block: twelve families of
# chart styling, 4,100 of a board's 4,700 leaves, and none of it is a property
# of the board object a user clicked. It is a theme editor's surface and wants
# its own one.
# `style.color.gradient` is out with `style.charts`: resolve requires its
# `palette` sibling (`ScaleTargetConfig`), which projects to no control here,
# and it raises without a bound `color:` channel — so any single gradient
# edit writes a board that parses and stops rendering, with no panel path
# back to validity (a table has no `color:` field at all). The group needs a
# form that writes the whole object at once.
# POLICY — a scoping decision about what the design surface is for.
_NOT_DESIGN_PATHS = ("style.charts", "style.color.gradient")

# `{model: ((key, guard), …)}` — the exclusion the two tables above cannot
# express, in the two ways it differs from them. `guard` names a dotted key that
# must be authored for `key` to be a control at all, and the model key means one
# target answers for its own fields rather than every target answering for a
# spelling.
#
# On a variable these are the query layer reached one level down, and the
# board's `model:`/`query:` are already out for that reason. Their failure is
# the worse kind: `normalize/variables.py:320` wants `table.column` dot
# notation, so the obvious bare column name parses, commits, and raises
# `ERR-VALIDATION-FIELD` at compile — nothing renders, from a control the panel
# offered. `options.query` names a query and lands later still, as `ERR-INTERNAL`
# at render.
#
# The guard is what the flat lists could not do. That rule has two branches: it
# fires only when the variable names no query of its own, and *with* one a bare
# column name is exactly what the field wants — a wrong one leaves the option
# list empty rather than breaking the board. So `options.column` is a hazard on
# one branch and a real control on the other, and a name that is simply in or
# out gets one of the two wrong.
# GATED — the render sweep walks every Variable control in full.
_NOT_DESIGN_ON: dict[str, tuple[tuple[str, str | None], ...]] = {
    "Variable": (
        ("column", None),
        ("options.query", None),
        ("options.column", "options.query"),
        ("options.label_column", "options.query"),
    ),
}

# A layout container earns a design entry only once it carries content of its
# own. A bare row/col is a hit-testing waypoint, not something to style.
# POLICY — what makes a bare container worth styling.
_CONTENT_FIELDS = ("title", "text")

# STRUCTURE — the schema's own scalar spellings.
_NUMERIC_TYPES = frozenset({"int", "float"})
_SCALAR_TYPES = _NUMERIC_TYPES | {"str", "bool"}

# Every wide-capable family folds its measures onto one mark family, which spends
# the layer stack (and the mark fill) on the measures themselves — so a
# list-valued `y` and any of these siblings cannot both be authored.
#
# The rule is one rule; where it *fires* is not. Bar and area raise from
# `reject_multi_series_channel_conflicts` at parse time, line and scatter from
# `resolve_wide_measure_channels` (`resolve/chart/_wide_fields.py`) at resolve.
# The save endpoint re-parses and nothing more, so the resolve-time half is the
# worse one: the panel's edit passes the gate, is written to git, and the chart
# stops rendering — which is why line and scatter have to be in this table
# even though the parse-gate test cannot see them. `_RENDER_FIXTURES` in the
# tests reaches it.
#
# The two halves do not cover the same siblings. `resolve_wide_measure_channels`
# reads `layers:` (and refuses a `color:` that is not a plain column) and
# nothing else, so a line or scatter chart carrying a list `y` and `layers:`
# renders with no diagnostic caught at parse — their rows exist for the render
# sweep only, because a table that claims more than the compiler enforces
# hides a control on a board that works. `color` is in neither row: a column
# there composes with a list `y` (one series per value per measure).
#
# Forward: once `y` holds a list, the conflicting sibling is not a control, or
# the panel shows one half of a mutually exclusive pair and hides the other.
# Mirror: once a sibling is authored, `y` is not a *multi-value* control, since
# the list is the edit that creates the collision. `layers` has no control
# (`_NOT_DESIGN`), so only the mirror direction is live today.
# GATED — the parse sweep (bar, area) and the render sweep (line, scatter).
_LIST_CONFLICTS = {
    "bar": ("y", ("layers",)),
    "AreaChart": ("y", ("layers",)),
    "LineChart": ("y", ("layers",)),
    "ScatterChart": ("y", ("layers",)),
}

# What a list-valued `y` collides with that is neither a sibling field nor a
# whole field — a *value* under one. `{model: ((list_field, key, expected), …)}`,
# where `expected is None` means any truthy value.
#
# `emitters/line.py:311` and `emitters/area.py:260` raise `ERR-INTERNAL` —
# "band-aware step curve is not supported for multi-metric (`y: [...]`) charts"
# — when the curve is authored `step`. *Band-aware* means the x column resolved
# to a categorical scale, which `resolve_cartesian_x` reads off the data: the
# panel cannot know whether a given board is one of the broken ones, so it
# withholds the pair on every board that could be.
#
# `features/mirror_axis.py:96` raises `ERR-MIRROR-MULTI-SERIES` on all three
# cartesian families the moment `y` folds: a mirrored axis restates one shared
# y-scale, and folded measures have no single scale to restate. Truthiness
# rather than `== True`, because `mirror` is `bool | AxisMirrorStyle` and the
# object form (`mirror: {format: percent}`) breaks the board the same way.
#
# In both cases the board parses either way, so the save gate waves it through
# and the chart fails the next time anyone opens it.
#
# Both directions where both are reachable: `y` is not a multi-value control
# while the value is authored, and the value is no control at all while `y`
# holds a list. Only curve has both — for mirror the second half is inert, since
# `bool | AxisMirrorStyle | None` draws no widget and so appears in no control
# set to withhold from. The table carries it anyway rather than splitting the
# rule in two, and it starts working the day mirror earns a control.
#
# For curve the second half is over-strict — the four other curve values are
# legal on a multi-metric chart — and it is the honest shape available: the
# alternative is a select that offers every value but one, which no other
# control on this surface does and which nothing else needs.
#
# Both halves read the authored value off the chart alone. A board- or
# theme-level `style: charts: line: axis_y: mirror: true` reaches the same
# raise and is invisible here, so the multi-value control stays live on a chart
# whose default it is. Not a regression — the gap was total before — but it is a
# gap, and closing it means resolving the style cascade from the panel.
# GATED — the render sweep, via the stepped fixtures.
# `style.color.gradient` is the third: it upgrades an authored `color:` column
# to a gradient channel (`resolve/chart/channel.py`), and the wide fold takes
# only a plain series column to cross its measures with
# (`resolve_wide_measure_channels` raises `ERR-MULTI-Y-COLOR-CONFLICT` on any
# other mode). Parse no longer looks at color at all, so the save endpoint's
# re-parse waves a list `y` through and the chart dies at resolve.
# GATED — the render sweep, via the gradient fixtures.
_VALUE_CONFLICTS = {
    "bar": (
        ("y", "style.axis_y.mirror", None),
        ("y", "style.color.gradient", None),
    ),
    "LineChart": (
        ("y", "style.marks.line.curve", "step"),
        ("y", "style.axis_y.mirror", None),
        ("y", "style.color.gradient", None),
    ),
    "AreaChart": (
        ("y", "style.marks.area.curve", "step"),
        ("y", "style.axis_y.mirror", None),
        ("y", "style.color.gradient", None),
    ),
    "ScatterChart": (
        ("y", "style.axis_y.mirror", None),
        ("y", "style.color.gradient", None),
    ),
}

# The families whose validator is stricter than their own declared type.
# `SparkBarChart.y` is typed `str | list[str]` and `_validate_single_series`
# refuses every list but a one-element one: a spark bar is one row of bars
# against one value column, so there is no second encoding for a second measure.
#
# `HeatmapChart.y` is the other, and the only one with no validator behind it at
# all. `reject_multi_series_channel_conflicts` states the rule every wide family
# obeys — the fold spends the mark fill on the measures — and a heatmap's color
# *is* its measure, so there is nothing left to spend. Heatmap neither calls that
# validator nor refuses from its own resolver, so `y: [region, revenue]` renders
# clean: a y band stacking two columns' values against each other, and a cell
# color that has stopped encoding magnitude. Neither gate can see a chart that
# is merely wrong, so this one is pinned by an explicit test.
# GATED for spark bar (both sweeps). The `HeatmapChart` row is
# POLICY: a heatmap renders a list `y` clean, so there is no rule to agree with
# — `test_a_heatmap_spends_its_color_channel_on_the_measure_and_takes_no_list`
# is the whole of it.
_NEVER_A_LIST = {
    "SparkBarChart": ("y",),
    "HeatmapChart": ("y",),
}

# The pivot's arity rule, and the only one that spans three fields.
# `render/chart/table.py` raises `ERR-INTERNAL` — "declare at least one of
# `rows` or `values`" — for a table that sets `columns:` and neither of the
# pair: with both omitted the row and measure dimensions are ambiguous. The
# board parses, so the save endpoint's re-parse waves it through and the panel
# is left showing a chart that no longer renders.
#
# `{model: (pair, gated)}`. Two panel interactions reach the raise and each maps
# to one half of the rule: setting `columns` on a table that declares neither of
# the pair (so `gated` is no control while none of `pair` is authored), and
# clearing whichever of the pair is the last one standing on a table that is
# already pivoting (so that one is `required`, and the panel offers no clear).
# GATED — the render sweep.
_PIVOT_ARITY = {"TableChart": (("rows", "values"), ("columns",))}

# Groups that mean something only while a sibling holds a particular value.
# `ScaleContinuousStyle._validate_log_pow_symlog_params` rejects a per-type
# parameter block whose `type` does not name that type, and it fires on the
# *merged* result — so a chart-local `log.base` under a non-log type parses
# fine and then raises in `merge_onto_base` at style resolution. The parse gate
# cannot see it (the board parses), and the failure is a broken render rather
# than a silent no-op, so it is worse than `_NESTED_ONLY` and later than
# `card_gap`. Gating on the sibling is also the better control — the group
# appears exactly when it does something.
#
# Under-inclusive on purpose: `theme/axis.py` documents a theme setting
# `type: log` while a chart-local patch sets only `base`, and this reads the
# local patch's sibling, so it hides `log` in that case. No rule local to one
# patch can be both safe and complete here — asking the compiler is the fix,
# which is the hand-curated-lists task's whole argument.
# Keyed by the compiled model's name and matched with `Patch` stripped: the
# same style node arrives as `ScaleContinuousStylePatch` under a chart and as
# `ScaleContinuousStyle` under the board's theme block, and the rule is the
# model's either way.
# AGREED — `test_a_scale_parameter_block_is_refused_without_its_own_scale_type`.
# The raise is on the merged style and the control is a number, so the render
# sweep (bounded to `list`) cannot see it.
_CONDITIONAL_GROUPS = {
    "ScaleContinuousStyle": {
        "log": ("type", "log"),
        "pow": ("type", "pow"),
        "symlog": ("type", "symlog"),
    },
}

# The mirror rule, bar's alone: a multi-metric `y` makes `x` mandatory
# (`emitters/bar.py`, "multi-metric (y: [...]) charts require an x field").
# The schema calls `x` optional because a single-metric chart can omit it, so
# `required` has to be raised here or the panel renders a way to clear it.
# GATED — the parse sweep.
_REQUIRED_WITH_LIST = {"bar": {"x": "y"}}

# The unconditional half of the same rule, and the reason the exemption below
# cannot be all-or-nothing. `_emit_histogram` raises `ERR-HISTOGRAM-NON-NUMERIC`
# when there is no `x` to bin — on every histogram, whatever `y` holds. Bar's
# rule is the *weaker* one; histogram's is stronger, not absent. The board parses
# without an `x`, so the save endpoint's re-parse waves the clear through and the
# chart stops rendering.
# AGREED — `test_a_histogram_that_loses_its_x_stops_rendering`. Clearing an `x`
# is not a `list` edit, so the render sweep never makes it.
_ALWAYS_REQUIRED = {"histogram": ("x",)}

# Required once a sibling is authored: `{model: {field: (siblings, …)}}`. A
# bar's `y` is optional to the schema and a bar with neither `x` nor `y`
# parses and renders, but `BarChart._validate_multi_series` refuses an `x`
# with no measure ("x requires a y field"). Clearing `y` *is* a `list` edit —
# `y` stays a list control beside `color:`, since the two compose — so the
# panel has to withhold the clear exactly while an `x` stands.
# GATED — the parse sweep. AGREED — `test_a_bar_that_loses_its_y_stops_rendering`.
_REQUIRED_WITH = {"bar": {"y": ("x",)}}

# The tables above are filed by model, and one model backs two `type:` values.
# `BarChart` covers `Literal["bar", "histogram"]` and `_validate_multi_series`
# returns early on a histogram — it bins `x` and derives its measure by counting,
# so it never reads `y` and none of bar's conflicts reach it. Filing bar's rules
# under the class would apply them to a chart the compiler exempts: a `y` control
# withheld from a board that renders, and a `color` hidden beside it. The panel
# erroring on valid input is the validate-and-error-fast rule inverted.
#
# So this one model's rules are filed under the authored `type:` instead, which
# makes the over-application unrepresentable rather than corrected after the
# fact. Only this model, because `type` is not a chart's alone: a table's spark
# column style is typed `line`/`area`/`bar` too, and keying every node on its
# `type` would hand line's rules to a sparkline.
# STRUCTURE — which key the tables above are filed under, not a rule.
_BY_DISCRIMINATOR = {"BarChart"}

# Fields the compiler honors on the root board and not on a nested one.
# `card_gap` raises there (`dispatch.normalize_board`: "card_gap can only be
# set on the root board") — a single click from a board that will not compile,
# and a checkbox commits on that first click. The `frame` group is the quiet
# half of the same rule (`compile/AGENTS.md`, "Root-only board frame": nested
# boards render into the parent's grid and their `FrameStyle` is ignored): the
# save succeeds, the value is discarded, the board is unchanged, and the panel
# shows it as authored. Prefixes match a key and everything under it.
# `extends` joins them for the same reason and arrived by the same route as
# `card_gap`: it is a field of every `AuthoredBoard` node, and `merged_patch`
# folds the extends chain once, on the root document (`compiler.py:957`). A
# nested board's `extends:` is never merged — the save succeeds, the value is
# discarded, the board is unchanged, and the panel shows it as authored.
# AGREED — `test_card_gap_is_read_at_the_root_and_refused_on_a_nested_board` and
# `test_a_nested_boards_frame_changes_nothing_the_reader_can_see`. The frame half
# is discarded at *render* with no diagnostic, which neither sweep can observe.
_ROOT_ONLY = ("card_gap", "style.frame", "extends")

# The mirror: honored on a nested board and read nowhere at the root.
# `AuthoredBoard.height` is documented "Height when nested" and reached only
# from `normalize_layout`'s layout-item branch, so at the root a save succeeds,
# the value is discarded, the board is unchanged, and the panel renders it back
# as authored — the `card_gap` shape without the raise that caught it, which
# the parse gate is structurally unable to see. (`width` is not here: the root
# desugars it, and `_ROOT_SUGAR` already picks one of that pair.)
# AGREED — `test_height_is_read_on_a_nested_board_and_discarded_at_the_root`.
_NESTED_ONLY = ("height",)

# On the root board only, `width:` is sugar for `style.frame.width:` and
# authoring both raises (`dispatch._root_width_style_patch`). Offering the
# pair as two independent controls is two clicks from a board that will not
# compile, so the surface emits exactly one. On a nested board `width:` is
# layout placement instead — a different field with a different meaning — so
# the pair is legitimate there.
# AGREED — `test_the_width_pair_the_panel_splits_is_the_pair_the_compiler_refuses`.
# A rule about authoring *both* keys, which a one-key-at-a-time sweep cannot reach.
_ROOT_SUGAR = (("width", "style.frame.width"),)


class DesignProperty(BaseModel):
    """One editable property of one board object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    widget: Widget = Field(description="Control kind this property renders as.")
    type_repr: str = Field(description="The authored type, as the schema states it.")
    value: str | int | float | bool | list[str] | None = Field(
        description=(
            "The value authored in this file, or None when unset. A list where "
            "the field takes one — `y: [revenue, cost]` is two series, and "
            "flattening it to a scalar would render the control empty on the "
            "charts that most need it."
        )
    )
    authored_here: bool = Field(
        description=(
            "True when this file sets the value. False means it falls back to a "
            "meta.yml, an `extends:` template, or a theme default — which of "
            "those is not yet tracked."
        )
    )
    required: bool = Field(description="Whether the authored schema demands a value.")
    description: str = Field(default="", description="Field help from the schema.")
    enum_values: tuple[str | bool, ...] | None = Field(
        default=None,
        description=(
            "Values on offer: what the schema declares, plus — on a field the "
            "schema facets as a `format` — the aliases in force in the "
            "target's scope. Every value on a `select`; on a `combo`, the "
            "shortcuts. On a `list` control whose field also carries the "
            "`palette` facet, these name whole-value shortcuts only — write "
            "one as the field's entire value, never as one item of the list "
            "(see `Palette`'s own docstring: a palette name is not a color)."
        ),
    )
    default_repr: str | None = Field(
        default=None,
        description="Human-readable default, or None where there is no meaningful one.",
    )
    options_complete: bool = Field(
        default=False,
        description=(
            "Whether `enum_values` names every value this control can take "
            "here: a closed schema enum, or a column offer that names every "
            "output of the chart's query. False on shortcut offers (format "
            "aliases, font faces) and wherever inference could not name "
            "everything — a consumer must not render an incomplete offer as "
            "a closed choice."
        ),
    )
    facets: tuple[str, ...] = Field(
        default=(),
        description=(
            "Semantic facets the schema declares on this field, lowercased, "
            "naming what the value means where its type cannot. A consumer "
            "picks a control from these; the schema does not name controls, "
            "because what a color should look like is the editor's decision "
            "and not the model's."
        ),
    )
    list_only: bool = Field(
        default=False,
        description=(
            "True when every arm of the type is a list. A `list` control can be "
            "handed a single value, and on these fields that value has to be "
            "wrapped before it is written: a bar's `y` takes `revenue` or "
            "`[revenue, cost]`, but a table's `rows` takes only the list, and "
            "writing the bare string back leaves a board that stops parsing."
        ),
    )
    readonly: bool = Field(
        default=False,
        description=(
            "True when this property is shown for information but cannot be "
            "saved from this control: the authored value carries more than the "
            "widget can hold, and writing what the widget shows back would "
            "silently drop the rest — `chart.support_table` with a `format:` or "
            "`aggregate:` entry, on the `list` control that can only author "
            "bare column names."
        ),
    )


class DesignNode(BaseModel):
    """One authored model's own controls, and the groups nested under it.

    Structure the consumer can render directly, rather than dotted key names it
    would have to re-parse: `style.font.size` is `children["style"]
    .children["font"].properties["size"]`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(
        description="Name of the authored model backing this node, as the schema knows it."
    )
    properties: dict[str, DesignProperty] = Field(
        description="This node's own scalar controls, keyed by field name."
    )
    children: dict[str, DesignNode] = Field(
        description="Nested groups, keyed by field name. Empty groups are not emitted."
    )
    description: str = Field(
        default="",
        description=(
            "Description of the field that introduced this node — a leaf's "
            "`data-hint` and a group's title should carry the same schema "
            "prose. Empty at the root, which no field introduces."
        ),
    )


class DesignTarget(DesignNode):
    """The one clickable object a request asked about, and everything on it."""

    path: str = Field(
        description=(
            "Absolute path of the object this resolved to, which is not always "
            "the path asked for: a click lands on whatever the renderer "
            "stamped, and a leaf or a bare container resolves to its owner."
        )
    )
    writes_at: str = Field(
        description=(
            "Absolute path a property of this object is written at, which is "
            "not always the path it is clicked at. A grid item is clicked at "
            "`grid.items.N` — the spelling the renderer stamps — while its "
            "content lives one segment deeper, under `.item`."
        )
    )


def _arms(type_repr: str) -> set[str]:
    """The union's arms as the schema spells them, minus `None`."""
    return {arm.strip() for arm in type_repr.split("|")} - {"None"}


def _list_only(field: SchemaField) -> bool:
    """Whether a list is the only shape this field takes.

    The `list` control submits one value or several, and which of those a bare
    value means is the field's call: `y: str | list[str]` authors `revenue` as
    written, while `TableChart.rows: list[str] | None` needs `[revenue]`.

    The IR answers this directly. `container` is `"list"` only when the list is
    the whole field — a union with a scalar arm reports `None`, which is the
    same question asked once by the schema builder instead of re-derived here
    from a string it printed.
    """
    return field.container == "list"


def _widget_for(field: SchemaField) -> Widget | None:
    """The control this field projects to, or None when it does not project.

    A container or nested model is not a scalar control; rendering one anyway is
    how a lossy widget silently drops half a value.
    """
    # `list` is the one container with a control. A table's pivot channels are
    # `list[str] | None` — plain lists, not unions — and refusing every
    # container left the whole family with no data binding at all while its
    # fonts were editable.
    if (field.container is not None and field.container != "list") or (
        field.nested_models
    ):
        return None
    # A union like `str | list[str] | None` reports `container=None` — the
    # container is one arm, not the whole field, so the guard above misses it.
    # A text box there shows an authored two-series `y` as empty and collapses
    # it to a string on write, which is why these were refused outright. The
    # `list` widget is the control that can hold one; a `dict[...]` arm still
    # has none, and a list of anything but scalars still has none.
    if "dict[" in field.type_repr:
        return None
    # Per arm, not on the whole `type_repr`, and every arm must be a scalar the
    # widget set can hold. A chart's `height` is `int | float | None`, so
    # matching the joined string against bare names sent the two most obvious
    # numeric controls to a text box; `projection` is `str | Projection | None`,
    # a model arm the IR does not report as nested, so a text box showed an
    # authored object as empty and one keystroke replaced it with a string.
    arms = _arms(field.type_repr)
    list_arms = {arm for arm in arms if arm.startswith("list[")}
    if list_arms:
        # Strings only, not every scalar: the control submits text and the
        # writer stores exactly what it submits, so a `list[float]` (`dash`,
        # `domain`) would save `["4", "2"]` and fail its own re-parse. A
        # control that cannot save is worse than no control.
        if not all(
            arm.removeprefix("list[").removesuffix("]") == "str" for arm in list_arms
        ):
            return None
        scalars = arms - list_arms
        if field.enum_values and "str" in scalars:
            # A closed vocabulary is a set of strings, so the box holds it as
            # well as it holds the `str` arm — `extends` and a categorical
            # `palette` are both `<enum> | str | list[str]`, and failing the
            # scalar check on the enum arm left the board's theme and its
            # palette with no control at all. The values ride along on
            # `enum_values` for whoever offers them — the IDE and `dct` do;
            # Cloud's panel draws a suggestion menu for the `list` widget
            # below too. The `str` arm is the same condition `combo` answers
            # to below: without one, a free-text box takes what no arm
            # accepts.
            scalars = {arm for arm in scalars if not arm.startswith("one of: ")}
        return "list" if scalars <= _SCALAR_TYPES else None
    if field.enum_values:
        # `select` says these are the values; `combo` says these are the
        # shortcuts. A format field is `<alias Literal> | str` — the engine's
        # names for that slot's kind, plus any d3 spec — so a select there makes
        # `,.0f` unauthorable. Only a `str` arm earns the combo: `zero` is
        # `bool | 'auto'` and `title.level` is `int | 'auto'`, where "type
        # anything else" is false, and a text box would take what neither arm
        # accepts. A `bool` arm is closed, so those selects carry it in full
        # (see the enum_values assembly below); an `int` arm is not, which
        # leaves `title.level` unable to reach its non-enum arm — a control
        # the widget set does not have yet, not one to fake.
        return "combo" if "str" in arms else "select"
    if not arms or not arms <= _SCALAR_TYPES:
        return None
    if arms == {"bool"}:
        return "checkbox"
    if arms <= _NUMERIC_TYPES:
        return "number"
    # Text is the fallback only when a string is actually one of the arms.
    # `style.axis.labels.bound` is `bool | float | None`: a text box there takes
    # anything, and neither arm accepts what it takes.
    return "text" if "str" in arms else None


def _scalar(value: Any) -> str | int | float | bool | list[str] | None:
    """The authored value, when the widget set can hold it.

    A list of scalars is held as a list rather than dropped: `y: [revenue, cost]`
    is the shape the `list` widget exists for, and returning None there is the
    empty-control-on-a-two-series-chart bug.
    """
    if isinstance(value, (str, int, float, bool)):
        return value
    # Strings only, matching the one list `_widget` hands out a control for: a
    # `list[float]` has no widget, so coercing its items to text here would only
    # fill a control that is never rendered.
    if isinstance(value, (list, tuple)) and all(
        isinstance(item, str) for item in value
    ):
        return list(value)
    return None


def _drills_into(field: SchemaField) -> str | None:
    """The one authored model this field opens as a group, or None.

    Exactly one model or nothing: a union carrying a scalar arm as well
    (`visible: bool | str | SingleRowBoolProbe | None`) is two shapes at once
    and a group would show only half of it, and a container holds 0..n of them,
    which is a sub-form rather than a group.
    """
    if field.container is not None or len(field.nested_models) != 1:
        return None
    arms = {arm.strip() for arm in field.type_repr.split("|")} - {"None"}
    return field.nested_models[0] if arms == {field.nested_models[0]} else None


def _suppressed(
    node: Any, is_root: bool, writes_at: str, authored: frozenset[str]
) -> tuple[str, ...]:
    """Dotted keys this target does not offer, as exact matches or prefixes.

    Root sugar is decided from the source map rather than from the built
    properties: whichever of an aliased pair the file authors is the one kept,
    and when it authors neither the canonical key wins.

    The two model-keyed tables are here rather than beside their mirror in
    `_takes_a_list` because they withhold controls the walk reaches four levels
    deeper, under models that have no idea which object they hang off.
    """
    rules = _rule_key(type(node).__name__, node)
    withheld = (
        *(
            key
            for key, guard in _NOT_DESIGN_ON.get(rules, ())
            # Truthiness, not presence: the validator this mirrors tests
            # `not var.options.query`, so an authored `query: ""` is no query.
            if guard is None or not _dotted(node, guard)
        ),
        *(
            key
            for field, key, _ in _VALUE_CONFLICTS.get(rules, ())
            if isinstance(getattr(node, field, None), list)
        ),
    )
    # Not in the table above, because the table restates rules and this one asks
    # the renderer's. `required: true` on a variable with nothing to satisfy it
    # raises before a query runs, and `renderer.py` answers with a board error and
    # no variables strip — so the checkbox that did it stamps no path and has no
    # click target left to undo it from. Absent is `variable_value_is_absent`
    # rather than falsiness for the reason that predicate exists: `default: false`
    # on a checkbox is a value, and a guard keyed on truthiness would withhold a
    # control that works.
    if isinstance(node, Variable):
        if variable_value_is_absent(node.default):
            withheld = (*withheld, "required")
        # And the numeric fields, for the same reason on the other side: writing
        # one can strand the default or promote the variable to an input its
        # default was never valid for, and either is the edit the panel cannot
        # take back.
        withheld = (
            *withheld,
            *(
                f
                for f in ("min", "max", "step")
                if not _variable_bound_is_safe(node, f)
            ),
        )
    if not is_root:
        return (*_NOT_DESIGN_PATHS, *_ROOT_ONLY, *withheld)
    drop: list[str] = [*_NOT_DESIGN_PATHS, *_NESTED_ONLY, *withheld]
    for sugar, canonical in _ROOT_SUGAR:
        drop.append(canonical if _absolute(writes_at, sugar) in authored else sugar)
    return tuple(drop)


def _variable_accepts(var: Variable, **edit: Any) -> bool:
    """Whether the compiler still takes this variable's options and default after that edit.

    Asked rather than restated, and this is the one rule on the surface where
    asking is possible: the three functions called below *are* the functions
    that raise, so every branch they carry — the per-input type check, the
    daterange's arity, the slider's bounds against `min`/`max`, the option
    list against `data_type` — answers here at no cost and a copy of them
    could not drift. The tables above exist because their rules have no such
    entry point; where one does, a table would be the wrong shape.

    Four calls, in the pipeline's own order, because the pipeline validates a
    variable in that many steps and skipping any one of them asks a different
    question than the compiler will:

    - `detect_variable_input_type` first, exactly as `normalize/dispatch.py`
      runs it before validating. `input:` defaults to `auto`, and the detector
      resolves it from the *other* authored fields — so setting `min:` does not
      bound a slider, it makes the variable a slider and then bounds it. Probing
      the unresolved `auto` asks the validator about an input it accepts
      unconditionally, and every answer comes back yes.
    - `validate_choice_type`, the compile-time gate on the option list — the
      one step that does not need a default to have something to judge.
    - `validate_variable_value`, the compile-time gate on the default.
    - `coerce_variable_values`, the *runtime* gate on the render path. Not a
      strict narrowing of `validate_variable_value` in every direction: a
      `bool` subclasses `int`, so the type table takes a bool wherever it
      takes a number, while `_coerce_number` rejects it by name — compile
      alone says yes to `input: number` on a checkbox and the render says
      "must be numeric, got a boolean". It diverges the other way for
      `daterange` — `coerce_variable_values` reads `[]`/`""` as unset where
      `validate_variable_value` rejects both — but never observably here:
      both calls take `hypothesis.default` independently under one `except`,
      so the answer is "either raised" whatever the order.

    `model_copy` builds the hypothesis, not a patch: nothing keeps the result,
    and the question is what the compiler *would* say.
    """
    hypothesis = var.model_copy(update=edit)
    hypothesis.input = detect_variable_input_type(hypothesis)
    try:
        validate_choice_type("", hypothesis)
        validate_variable_value("", hypothesis, hypothesis.default)
        coerce_variable_values({"": hypothesis.default}, {"": hypothesis})
    except (CompilationError, ExecutionError):
        return False
    return True


def _variable_bound_is_safe(var: Variable, field: str) -> bool:
    """Whether a numeric control can be written at all without breaking the board.

    `min:`/`max:`/`step:` and `default:` are one decision spelled as two fields,
    which is the shape `input:` has — but a bound is free numeric text rather
    than a finite enum, so there is nothing to narrow and the question becomes
    whether *any* value breaks. It is not a repairable break: a slider whose
    default sits outside its bounds fails to compile, Cloud renders the error
    with no SVG, and nothing stamps the path the panel would need for the undo.

    Two ways to break, and the field only tells you which probe answers:

    - *Promotion.* On an `auto` variable, writing any of the three makes it a
      slider — whose default must be a number. So when the default is not one,
      one probe with an arbitrary value settles all three fields at once, and
      `step` is in the guard for this reason alone.
    - *Crossing.* Once the default is numeric, only `min`/`max` are compared
      against it, and a bound is monotone: if the value one step past the
      default is refused then every value past it is too, and the control is a
      trap for half its domain. `step` is compared against nothing, so a numeric
      default leaves it an ordinary control.

    A `bool` default takes the promotion probe, not the crossing one. `bool`
    subclasses `int`, so `isinstance` alone would call it numeric and hand
    `step` the early-out below — but numeric is exactly what `_coerce_number`
    says it is not, and promotion to `slider` is what puts it in front of that
    coercer. The probe is the only thing that asks, so a bool has to reach it.

    Narrowing the *control* — a numeric ceiling the way `input:` gets a value
    list — is the better answer and is the follow-up's, not this one's:
    `DesignProperty` has no bound to carry it, and inventing one is a control
    template, not a guard.
    """
    default = var.default
    if not isinstance(default, (int, float)) or isinstance(default, bool):
        return _variable_accepts(var, **{field: 0})
    if field == "step":
        return True
    across = default + 1 if field == "min" else default - 1
    return _variable_accepts(var, **{field: across})


def _absolute(writes_at: str, key: str) -> str:
    return f"{writes_at}.{key}" if writes_at else key


def _dotted(instance: Any, path: str) -> Any:
    """The value at a dotted key on a parsed instance, or None where it stops."""
    for segment in path.split("."):
        instance = getattr(instance, segment, None)
        if instance is None:
            return None
    return instance


def _rule_key(model_name: str, instance: Any) -> str:
    """The key this node's compiler rules are filed under.

    Its model, except where the model backs more than one `type:` and the
    validators branch on the discriminator rather than on the class — there, the
    authored type. One lookup, so every chart rule reads the same key, and a rule
    written for one of the two tags cannot reach the other.

    `_CONDITIONAL_GROUPS` is deliberately not a caller: it is keyed on style
    models, which this never renames.
    """
    if model_name in _BY_DISCRIMINATOR:
        return str(getattr(instance, "type", model_name))
    return model_name


def _takes_a_list(rules: str, name: str, instance: Any) -> bool:
    """Whether this chart, as authored, accepts a multi-value `name`.

    Four tables, one question. A control the compiler will not take is one whose
    every use fails, and the panel that broke the board cannot repair it; the
    save endpoint's re-parse only backstops that, and it cannot see the ones
    here that fire past parse.
    """
    if name in _NEVER_A_LIST.get(rules, ()):
        return False
    for field, key, expected in _VALUE_CONFLICTS.get(rules, ()):
        if name != field:
            continue
        authored = _dotted(instance, key)
        if bool(authored) if expected is None else authored == expected:
            return False
    conflict = _LIST_CONFLICTS.get(rules)
    if conflict is not None and name == conflict[0]:
        # `is not None` mirrors the validator, which tests presence: an authored
        # `color: ""` collides there while passing a truthiness check here.
        if any(getattr(instance, other, None) is not None for other in conflict[1]):
            return False
    # Bar's multi-metric `y` requires an `x` (`emitters/bar.py`), and a
    # single-metric bar may legally omit one — so this is an ordering constraint
    # rather than an impossibility: set `x` and the control comes back.
    return not any(
        gated == name and not getattr(instance, needs, None)
        for needs, gated in _REQUIRED_WITH_LIST.get(rules, {}).items()
    )


def _describe(
    schema: AuthorableSchema,
    model_name: str,
    instance: Any,
    prefix: str,
    writes_at: str,
    authored: frozenset[str],
    drop: tuple[str, ...],
    ancestors: frozenset[str],
    aliases: tuple[str, ...],
    channel_options: tuple[str, ...] = (),
    channel_options_complete: bool = False,
    description: str = "",
) -> DesignNode:
    """One model's controls and groups, recursed to the leaves.

    Bounded by `_CHILD_TARGETS`, which the target walk descends instead; the
    `ancestors` guard is defense in depth for a self-referential style model.

    `description` is the description of the *field that introduced this call* —
    not a property of `model_name` itself — so the root call leaves it at the
    default "" and the one recursive call site (below) passes the introducing
    `SchemaField.description` along.
    """
    model: AuthorableModel | None = schema.models.get(model_name)
    if model is None:
        return DesignNode(
            model=model_name, properties={}, children={}, description=description
        )

    properties: dict[str, DesignProperty] = {}
    children: dict[str, DesignNode] = {}
    rules = _rule_key(model_name, instance)
    for field in model.fields:
        # Both sets name *authored board and chart* fields, so they only mean
        # what they say at the target's own level. Deeper in the style graph the
        # same bare names belong to different models entirely — `StylePatch
        # .charts` is the chart-style subtree, `LayoutStylePatch.rows` is a gap
        # style, `AxisXStylePatch.grid` is gridlines, `AxisXStylePatch.type` is
        # a scale-type select — and matching them by name amputated 23 real
        # groups and controls with nothing to say so. A genuine deeper
        # suppression is spelled as a dotted key, the way `_ROOT_ONLY` does it.
        # A channel is never one of those: `TableChart.rows` names query columns
        # for the pivot's row dimension and shares only its spelling with the
        # layout container, which is what a name list cannot tell apart.
        channel = any(isinstance(f, Channel) for f in field.facets)
        if (
            not prefix
            and not channel
            and (field.name in _NOT_DESIGN or field.name in _CHILD_TARGETS)
        ):
            continue
        key = f"{prefix}{field.name}"
        if any(key == one or key.startswith(f"{one}.") for one in drop):
            continue
        value = getattr(instance, field.name, None) if instance is not None else None

        if field.name == "support_table" and not prefix:
            # `ChartSupportTable` is a nested model, so the generic walk below
            # would drill into it as a group and offer its own `entries:`
            # field — a list of a three-shape union, which projects to no
            # control. The panel's actual affordance is the field itself, as
            # a flat `list` of column names, the same shape `y:` already has.
            prop = _support_table_property(
                instance, field, key, writes_at, authored, channel_options
            )
            if prop is not None:
                properties["support_table"] = prop
            continue

        group = _drills_into(field)
        if group is not None:
            group_model = schema.models.get(group)
            # A group the panel cannot bring into existence one control at a
            # time is not a group it can offer: writing `details.expanded_title`
            # on a board with no `details:` mints `{expanded_title: …}`, which
            # is missing the required `summary`, and the board stops parsing.
            # `empty_is_valid` is the model's own answer, so a validator rule
            # (`MultiplesConfig` wanting `rows` or `columns`) counts the same as
            # a required field. Offering these needs a form that writes the
            # whole object at once.
            if group_model is None or not group_model.empty_is_valid:
                continue
            conditional = _CONDITIONAL_GROUPS.get(
                model_name.removesuffix("Patch"), {}
            ).get(field.name)
            if conditional is not None:
                sibling_name, required_value = conditional
                if getattr(instance, sibling_name, None) != required_value:
                    continue
            if group in ancestors:
                # Unreachable while the group graph stays acyclic, which
                # `test_the_group_graph_is_acyclic_so_the_ancestor_guard_never_fires`
                # pins. Raising rather than skipping keeps the walk finite the
                # same way, without the failure mode a skip would have: a whole
                # group vanishing from the panel with nothing to say so.
                raise ValueError(
                    f"design group {key!r} reaches its own ancestor {group!r}; "
                    "the nested-model graph is no longer acyclic"
                )
            child = _describe(
                schema,
                group,
                value,
                f"{key}.",
                writes_at,
                authored,
                drop,
                ancestors | {group},
                aliases,
                description=field.description,
            )
            # An empty group is a drill-in that opens onto nothing.
            if child.properties or child.children:
                children[field.name] = child
            continue

        widget = _widget_for(field)
        if widget is None:
            continue
        conflict = _LIST_CONFLICTS.get(rules)
        if (
            conflict is not None
            and field.name in conflict[1]
            and isinstance(getattr(instance, conflict[0], None), list)
        ):
            continue
        pair, gated = _PIVOT_ARITY.get(rules, ((), ()))
        standing = [n for n in pair if getattr(instance, n, None) is not None]
        # Offered when the file already holds one: the gate is there to stop the
        # panel *creating* the illegal shape, and a board hand-authored into it
        # was broken before any click — clearing this is the edit that fixes it.
        if (
            field.name in gated
            and not standing
            and getattr(instance, field.name, None) is None
        ):
            continue
        gate = _REQUIRED_WITH_LIST.get(rules, {}).get(field.name)
        required = (
            field.required
            or field.name in _ALWAYS_REQUIRED.get(rules, ())
            or any(
                getattr(instance, sibling, None) is not None
                for sibling in _REQUIRED_WITH.get(rules, {}).get(field.name, ())
            )
            or (gate is not None and isinstance(getattr(instance, gate, None), list))
            # Only while the chart is actually pivoting: a flat table clears its
            # `rows:` freely, and the raise needs a `columns:` to fire at all.
            or (
                standing == [field.name]
                and any(getattr(instance, n, None) is not None for n in gated)
            )
        )
        if widget == "list" and not _takes_a_list(rules, field.name, instance):
            # The field admits a list in general, this chart will not take one,
            # and a text box holds a name rather than a list of them. One entry
            # *is* that name. More than one has no honest spelling in a text
            # box: `[revenue, cost]` invites an edit inside the brackets, and
            # the whole bracketed string then writes as one column name — legal
            # for the `str` arm, so the save endpoint's re-parse passes it.
            value = _scalar(value)
            if isinstance(value, list):
                if len(value) != 1:
                    continue
                value = value[0]
            widget = "text"
        enum_values = tuple(field.enum_values) if field.enum_values else None
        if enum_values is not None and "bool" in _arms(field.type_repr):
            # A `bool | <Literal> | None` slot is a real boolean that also
            # takes a named mode -- `axis_x.ticks.visible` is `bool | "auto"`.
            # Offering only the literal would strip the control down to its
            # mode and leave an authored true/false unreachable: the panel
            # could neither show it nor put it back. The bool arm is closed
            # (exactly two values), so unlike a `str` arm it can be offered in
            # full rather than left to a combo's free text.
            enum_values = (True, False) + tuple(
                v for v in enum_values if not isinstance(v, bool)
            )
        options_complete = enum_values is not None and widget == "select"
        if channel and channel_options and enum_values is None:
            # The query's statically-inferred output columns, as suggestions:
            # `combo` keeps the input free-text because the offer can be
            # partial (a projection the skeleton cannot name), and
            # `options_complete` says which — a consumer may close a complete
            # offer into a choice, never a partial one. A `list` control
            # keeps its widget and gains the same suggestions.
            enum_values = channel_options
            options_complete = channel_options_complete
            if widget == "text":
                widget = "combo"
        if (
            enum_values is None
            and widget == "text"
            and any(isinstance(f, FontFamily) for f in field.facets)
        ):
            # The wheel knows which faces it ships — offered as shortcuts, the
            # way a format field offers its aliases. Combo keeps free text:
            # any CSS family or stack stays authorable.
            enum_values = offered_font_families()
            widget = "combo"
        if enum_values and any(isinstance(f, Format) for f in field.facets):
            # The field's Literal is the engine's own names for that slot's
            # kind (`FormatAlias` on a kind-agnostic `format:`, the narrower
            # `NumberFormatAlias`/`TimeFormatAlias` where the slot knows what it
            # paints), which is all a wheel can know; the aliases this node's
            # scope authors are legal here too — in every slot, since a user's
            # alias carries no kind — and offering the snapshot alone tells an
            # author the alias they wrote themselves is not a value. The dedup is not shadowing
            # support — a `style.formats` key spelling a predefined name raises
            # `ERR_FORMAT_PREDEFINED_SHADOW` — it keeps the offer honest for a
            # board that does not compile, which this verb still parses.
            enum_values += tuple(a for a in aliases if a not in enum_values)
        if (
            enum_values
            and isinstance(instance, Variable)
            and field.name in ("input", "data_type")
        ):
            # `input:` (and `data_type:`) and `default:` are one decision
            # spelled as two fields, and the panel writes one at a time. Eight
            # of the thirteen input types break a variable defaulting to a
            # string, past the save gate; `data_type: number` breaks one whose
            # options are words. The values the compiler takes are the values
            # on offer — the rest are a control that only fails, which is the
            # rule the tables above serve and this one can reach exactly.
            enum_values = tuple(
                value
                for value in enum_values
                if _variable_accepts(instance, **{field.name: value})
            )
        properties[field.name] = DesignProperty(
            widget=widget,
            type_repr=field.type_repr,
            value=_scalar(value),
            authored_here=_absolute(writes_at, key) in authored,
            required=required,
            description=field.description,
            enum_values=enum_values,
            # `default_repr` rather than `default`: a required field holds
            # `dataclasses.MISSING`, which would serialize as a repr of the
            # sentinel object.
            default_repr=field.default_repr,
            options_complete=options_complete,
            facets=tuple(type(f).__name__.lower() for f in field.facets),
            list_only=_list_only(field),
        )
    return DesignNode(
        model=model_name,
        properties=properties,
        children=children,
        description=description,
    )


def _is_target(schema: AuthorableSchema, node: Any, path: str) -> bool:
    """Whether this node is something a user can click and style.

    Every authored model is a target except a layout container that carries no
    content of its own — an empty row or col is scaffolding, and offering its
    board-level properties would bury the chart the user actually clicked.
    """
    # The definition is in another file, so the only control here is `ref`, and
    # every edit to it repoints rather than styles. The click resolves up, the
    # way an unwalked path already does.
    if isinstance(node, CrossFileRef):
        return False
    name = type(node).__name__
    if name not in schema.models:
        return False
    if name != "AuthoredBoard" or path == "":
        # The root board is always a target — it owns the frame, and a
        # charts-only board (no title, no text) is valid, so gating it on
        # content would drop the whole board-level surface.
        return True
    return any(getattr(node, field, None) is not None for field in _CONTENT_FIELDS)


def _unwrap(node: Any, writes_at: str) -> tuple[Any, str]:
    """`- my_chart: {type: bar, …}` is a chart, not a mapping of one.

    A documented layout shape the compiler stamps `rows.N`, same as an inline
    chart, but it parses to a plain dict of one entry. Unwrapped, the walk never
    reached the chart and a click resolved up to the board — where a save wrote
    board-level keys. The name is a wrapper key, so the chart's own YAML sits
    one segment deeper, the same hop a grid item needs.
    """
    if isinstance(node, dict) and len(node) == 1:
        ((name, only),) = node.items()
        return only, _absolute(writes_at, name)
    return node, writes_at


def _hop(node: Any, segments: list[str]) -> tuple[Any, int, str] | None:
    """Consume one child-target hop from the front of `segments`.

    Returns the child, how many segments it took, and the suffix that hop adds
    to `writes_at` — which is the consumed segments except under a grid item,
    whose content lives one level deeper than the renderer stamps it.
    """
    head, rest = segments[0], segments[1:]
    if head in ("rows", "cols") and rest and rest[0].isdigit():
        items = getattr(node, head, None) or []
        index = int(rest[0])
        if index >= len(items):
            return None
        return items[index], 2, f"{head}.{index}"
    if head in ("grid", "tabs") and len(rest) >= 2 and rest[0] == "items":
        if not rest[1].isdigit():
            return None
        items = getattr(getattr(node, head, None), "items", None) or []
        index = int(rest[1])
        if index >= len(items):
            return None
        item = items[index]
        suffix = f"{head}.items.{index}"
        if head == "tabs":
            return item, 3, suffix
        return item.item, 3, f"{suffix}.item"
    if head in ("charts", "variables") and rest:
        named = getattr(node, head, None) or {}
        if rest[0] not in named:
            return None
        return named[rest[0]], 2, f"{head}.{rest[0]}"
    return None


def _resolve(
    schema: AuthorableSchema, board: Any, path: str
) -> tuple[Any, str, str, tuple[str, ...], Mapping[str, Any]]:
    """The nearest design target at or above `path`, and the aliases in force there.

    A click lands on whatever the renderer stamped — often a leaf (`title`) or a
    bare container (`rows.0`), neither of which is something to style. Walking
    *down* and remembering the last target is O(depth) and gives the same answer
    as popping segments off a whole-board map, without building one. The root
    board is always a target, so the walk always terminates on something.

    The format aliases come back with it because they are a property of the
    walk, not of the node: they are whatever is in force at the innermost
    enclosing scope — the parent's table merged with anything that scope
    authors of its own — which only the descent knows.
    """
    node, walked, writes_at = board, "", ""
    style_patch = _scope_style_patch(board, None)
    queries = _scope_queries(board, {})
    found = (board, "", "", _scope_aliases(style_patch), queries)
    segments = path.split(".") if path else []
    while segments:
        hop = _hop(node, segments)
        if hop is None:
            break
        child, taken, suffix = hop
        walked = _absolute(walked, ".".join(segments[:taken]))
        node, writes_at = _unwrap(child, _absolute(writes_at, suffix))
        style_patch = _scope_style_patch(node, style_patch)
        queries = _scope_queries(node, queries)
        segments = segments[taken:]
        if _is_target(schema, node, walked):
            found = (node, walked, writes_at, _scope_aliases(style_patch), queries)
    return found


# What `build_skeleton` turns a `{{ expr }}` into. A placeholder that survives
# into the projection is a column whose name only a render would know.
_JINJA_PLACEHOLDER_RE = re.compile(rf"^{SKELETON_PLACEHOLDER_PREFIX}\d+__$")


def _sql_output_columns(sql: str) -> tuple[tuple[str, ...], bool]:
    """The statically-knowable output columns of one SQL string.

    Jinja is flattened to the same skeleton the SQL guard validates, so
    `{{ filter(...) }}` in a WHERE clause costs nothing; a projection the
    skeleton cannot name (`*`, an unaliased aggregate, a Jinja expression)
    is dropped rather than guessed at. The second return says whether the
    columns name *every* projection — counted against ``parsed.selects``,
    which is projection-aligned where ``named_selects`` silently omits
    unnamed expressions. Dialect-agnostic parse: a dialect-specific string
    that fails to parse degrades to no offer.
    """
    try:
        parsed = sqlglot.parse_one(build_skeleton(sql, all_branches=False))
    except (UnparseableSqlError, sqlglot.errors.SqlglotError):
        return (), False
    names = getattr(parsed, "named_selects", None) or []
    columns = tuple(
        name
        for name in names
        if name and name != "*" and not _JINJA_PLACEHOLDER_RE.match(name)
    )
    selects = getattr(parsed, "selects", None)
    complete = (
        selects is not None
        and len(columns) == len(selects)
        # The skeleton is not projection-faithful under Jinja control blocks:
        # an `{% if %}` emits only its primary branch and a `{% for %}` body
        # runs once, so the count proves nothing about the other branches —
        # and a `{{ }}` embedded inside an alias survives as a fabricated
        # `__dct_jN__` name that counts as named.
        and "{%" not in sql
        and not any(SKELETON_PLACEHOLDER_PREFIX in c for c in columns)
    )
    return columns, complete


def _query_columns(query: Any) -> tuple[tuple[str, ...], bool]:
    """The output columns one authored query declares, where they are static.

    HTTP and schema queries answer only at execution time; a cross-file ref is
    authored elsewhere. All degrade to no suggestions rather than a guess. The
    second return says whether the columns name every output — heterogeneous
    values rows and unnameable SQL projections make the offer partial.
    """
    if isinstance(query, AuthoredSqlQuery):
        return _sql_output_columns(query.sql) if query.sql else ((), False)
    if isinstance(query, AuthoredValuesQuery):
        if not query.rows:
            return (), False
        first = tuple(query.rows[0])
        return first, all(set(row) == set(first) for row in query.rows)
    if isinstance(query, AuthoredCompactValuesQuery):
        return tuple(query.columns), True
    return (), False


def _support_table_bare_strings(entries: Any) -> list[str] | None:
    """The entries as the `list` widget's bare-string spelling would write
    them back, or None when that spelling would lose something.

    `model_dump(exclude_defaults=True).keys() == {"source"}` rather than
    naming `format`/`label` individually: a field added to `ChartSupportTableSource`
    later is caught by construction instead of silently round-tripping through
    a check written for today's two optional fields.
    """
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, ChartSupportTableSource):
            return None
        if set(entry.model_dump(exclude_defaults=True)) != {"source"}:
            return None
        names.append(entry.source)
    return names


def _support_table_display_names(entries: Any) -> list[str]:
    """A read-only strip's entries, named for display only.

    Never round-tripped — this is what a read-only row shows in place of the
    generic control's `unset` placeholder, so a formatted or aggregated strip
    still reads as "here", not as an empty control the panel forgot to fill.
    """
    return [
        entry.per_series
        if isinstance(entry, ChartSupportTablePerSeries)
        else entry.source
        for entry in entries
    ]


def _support_table_attach_blocked(instance: Any) -> bool:
    """Whether offering an *empty* support_table attach point would itself be
    the edit that breaks the board — every check here is decidable from the
    authored instance alone, with no query run.

    - Unsupported chart type, or a multi-metric `y:` list (`chart.support_table`
      accepts only `per_series:`/`by_measure:` entries there, which this
      control cannot author) — both `_validate_support_table`'s own checks
      (`compile/models/chart/authored/_base.py`).
    - `multiples:` — `render/chart/features/facet.py` refuses `support_table`
      unconditionally once a chart facets into panels.
    - No `x:` — `support_table_attachment.py` requires an x-encoding to align
      strip columns to.
    - `color:` — a color-encoded chart is long-format, and the correct entry
      there is `per_series:`, which this control cannot author (the same
      standard `_LIST_CONFLICTS["LineChart"]` applies to `y:` against
      `layers:`); `support_table_attachment.py`'s ambiguous-source
      check would also fire on it the moment a query returns more than one
      row per x, which this verb cannot see coming.

    A bar's orientation (vertical: rows above/below the plot; horizontal:
    value columns beside it) is not itself a blocker — both attach cleanly,
    and the empty attach point this offers authors no `position:`, so the
    chart's own default resolves it either way without this verb needing to
    know which orientation the query's actual rows will resolve to.

    - `style.axis_y.mirror` on a bar — a mirrored axis reserves its own
      chrome on both edges of the category axis, and if the query's actual
      rows resolve the bar horizontal that axis is the one the column block
      sits beside; the two reservations can together starve the plot below
      `render/layout_sizing.py`'s width floor on an otherwise ordinary card.
      Line/area never resolve horizontal — their strip only ever costs
      height — so this is a bar-only risk.
    """
    chart_type = getattr(instance, "type", None)
    return (
        chart_type not in CHART_SUPPORT_TABLE_SUPPORTED_TYPES
        or isinstance(getattr(instance, "y", None), list)
        or getattr(instance, "multiples", None) is not None
        or not getattr(instance, "x", None)
        or bool(getattr(instance, "color", None))
        or (
            chart_type == "bar"
            and bool(
                getattr(
                    getattr(getattr(instance, "style", None), "axis_y", None),
                    "mirror",
                    None,
                )
            )
        )
    )


def _support_table_property(
    instance: Any,
    field: SchemaField,
    key: str,
    writes_at: str,
    authored: frozenset[str],
    channel_options: tuple[str, ...],
) -> DesignProperty | None:
    """The `support_table` control for a chart, or None where offering one would
    itself be the edit that breaks the board.

    Read-only whenever the authored block holds more than a source-name list
    can carry (see `_support_table_bare_strings`) — shown so the panel does not
    pretend the strip is empty, saved from nowhere so it is never rewritten.
    An *unset* block only gets the editable, empty attach point past
    `_support_table_attach_blocked`.
    """
    table = getattr(instance, "support_table", None)
    if table is None:
        if _support_table_attach_blocked(instance):
            return None
        value: list[str] | None = None
        readonly = False
    else:
        bare = _support_table_bare_strings(table.entries)
        value = (
            bare if bare is not None else _support_table_display_names(table.entries)
        )
        readonly = bare is None
    return DesignProperty(
        widget="list",
        type_repr=field.type_repr,
        value=value,
        authored_here=_absolute(writes_at, key) in authored,
        required=False,
        description=field.description,
        enum_values=channel_options or None,
        default_repr=field.default_repr,
        # Derived from the field's own `Channel()` marker (`_base.py`), same
        # as every other control this file builds — `enum_values` here are
        # the query's column suggestions, a free-text offer rather than a
        # vocabulary the control restricts input to. Without the marker, a
        # consumer that treats `enum_values` as closed choices (this file's
        # own `_panel_interactions` test helper did) writes a bare suggestion
        # straight onto `support_table`, which is not a column name at that
        # position — it is the whole block.
        facets=tuple(type(f).__name__.lower() for f in field.facets),
        list_only=True,
        readonly=readonly,
    )


def _channel_columns(
    node: Any, queries: Mapping[str, Any]
) -> tuple[tuple[str, ...], bool]:
    """Column suggestions for the target's channel controls, or none.

    A bare string is a named query where the scope defines one, and the SQL
    shorthand otherwise — the same call the normalizer makes. Anything that is
    not this chart's own statically-readable query (a dangling name, a
    cross-file ref) parses as neither and offers nothing.
    """
    query = getattr(node, "query", None)
    if isinstance(query, str):
        # `queries.name` is legal chart-ref spelling; the normalizer strips
        # the prefix before its registry lookup, so this lookup does too.
        named = queries.get(query.removeprefix("queries."))
        return (
            _query_columns(named) if named is not None else _sql_output_columns(query)
        )
    return _query_columns(query)


def _scope_queries(node: Any, inherited: Mapping[str, Any]) -> Mapping[str, Any]:
    """The named queries visible inside `node`, given its parent's.

    Nested boards may declare their own `queries:`; a chart's bare name
    resolves innermost-first, which the descent accumulates the same way it
    does format aliases.
    """
    if not isinstance(node, AuthoredBoard) or not node.queries:
        return inherited
    return {**inherited, **node.queries}


def _scope_style_patch(node: Any, inherited: StylePatch | None) -> StylePatch | None:
    """The style patch in force inside `node`, given its parent's.

    A tab or nested board that authors its own `style:` scopes it onto the
    parent's via `scope_patch` — the same helper `compile_board_resolved_style`
    calls for the real cascade — so `formats` (a plain, unmarked dict field)
    resolves key-wise here exactly as it does at compile. A scope that authors
    `style:` without touching `formats` inherits the parent's table unchanged
    (an unset field falls through in `merge_patches`); an explicit
    `style.formats: null` clears it for that scope and everything under it,
    same as it does at compile.
    """
    if not isinstance(node, (AuthoredBoard, TabItem)):
        return inherited
    return scope_patch(inherited, node.style)


def _scope_aliases(patch: StylePatch | None) -> tuple[str, ...]:
    """The format alias names offered inside a scope, from its merged style patch.

    Aliases an `extends:` template defines are in no scope's table here —
    resolving one is a read, and this half of the verb performs no I/O.
    """
    if patch is None or patch.formats is None:
        return ()
    return tuple(patch.formats)


def design_target(board: Path, path: str, *, project: Project) -> DesignTarget:
    """The design target for `path` on the board at `board`, read through the store.

    Mirrors `describe_board`: project content is reached only through a
    `Project` handle, never a raw filesystem read, so a host with a different
    store (Cloud's git-blob project) works unchanged.
    """
    from dbt_charts.agent_api._paths import resolve_board_path

    resolved = resolve_board_path(board, project)
    return build_design_target(resolved.read_text(), path)


def build_design_target(yaml_text: str, path: str) -> DesignTarget:
    """The design target for `path`, from board YAML already in hand.

    The target-only public form of `build_design`: what `design_target` and
    every consumer that reads no mapping call. Split from `design_target`
    because a host that already holds the text (a Cloud editor buffer, an
    unsaved draft) has nothing to resolve, and forcing it through a path would
    mean writing the buffer to a store to read it back. No I/O at all.
    """
    return build_design(yaml_text, path)[0]


def build_design(yaml_text: str, path: str) -> tuple[DesignTarget, dict[str, Any]]:
    """`build_design_target` plus the mapping the board was parsed from.

    The mapping is the board as written, before migration and validation —
    what a host reads when the model cannot answer for the text (the written
    `type:` behind a `BarChart`, a variable's raw `input:`). It rides along
    because the target is built from it: the board is scanned once, and the
    model, the source map and this mapping are three walks over that one tree.
    """
    schema = introspect()
    node = compose_yaml(yaml_text)
    # The index first: construction flattens merge keys into the tree in
    # place (`<<: *anchor` becomes the anchor's own key nodes), and the map
    # has to read the board as written.
    authored = frozenset(
        build_source_index_from_node(node, yaml_text, "<design-target>").source_map
    )
    mapping = mapping_from_node(node, yaml_text)
    board = parse_mapping(mapping, yaml_text)
    if "theme" in authored:
        # `theme:` is authoring sugar the parser folds into `extends:` before
        # the walk sees either, so the source map holds the spelling the file
        # used while the walk asks for the one it resolved to. Read apart, a
        # board plainly using a theme renders an empty theme control.
        #
        # The top-level key exactly, not any `*.theme`: `extends` is `_ROOT_ONLY`,
        # so a nested board's is withheld and there is no control for a nested
        # `rows.0.theme` to fill.
        authored |= {"extends"}
    node, target_path, writes_at, aliases, queries = _resolve(schema, board, path)
    model_name = type(node).__name__
    described = _describe(
        schema,
        model_name,
        node,
        "",
        writes_at,
        authored,
        _suppressed(node, target_path == "", writes_at, authored),
        frozenset({model_name}),
        aliases,
        *_channel_columns(node, queries),
    )
    return DesignTarget(
        model=model_name,
        properties=described.properties,
        children=described.children,
        path=target_path,
        writes_at=writes_at,
    ), mapping

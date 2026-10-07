"""Version-migration module for the 0.4.0 -> 0.5.0 structural changes.

Declares the frozen ``0.4.0 → 0.5.0`` boundary's renames, key removals, and
conditional relocations. ``moves()``, ``deletions()``, and
``conditional_moves()`` all source from the frozen ``0.4.0`` schema and
target the frozen ``0.5.0`` schema. This file was authored as
``versions/current.py`` while these changes were unreleased (declaring the
``catalog.latest.version → current`` boundary) and renamed here with no
content edit at release time — every function already accepts
``source_schema`` and ``target_schema`` as runtime parameters, so no
hardcoded version strings needed to change.

Changes in this release:

- interactive_legend removed from LegendStyle (field never had any effect in
  the static-SVG render pipeline; see the removal task for rationale).

- tooltip removed from all chart-family styles (both theme-level per-family
  overrides and chart-level style blocks): tooltip config was never consumed
  by the static-SVG pipeline. Affects all 11 families (bar, line, area,
  scatter, heatmap, histogram, pie, geoshape, point_map, kpi, table).
  Auto-stripped by dct migrate; authors should remove the keys.

- legend removed from KpiChartStyle and TableChartStyle theme-level per-family
  overrides (kpi and table never paint a legend). The theme-level
  style.charts.kpi.legend and style.charts.table.legend keys are auto-stripped
  by dct migrate via Deletion; the equivalent chart-level style.legend on a kpi
  or table chart raises a clear error with a removal hint (the style.legend tail
  cannot be expressed as a Deletion because painting families keep it).

  aspect_ratio / min_height / max_height at the theme level
  (style.charts.kpi.* / style.charts.table.*) cannot be expressed as a
  Deletion: the frozen 0.4.0 schema never declared them in KpiChartStylePatch
  or TableChartStylePatch — only a @cache-divergence runtime bug accepted them.
  There is no structural schema history to migrate from, so dct migrate cannot
  strip them. Instead, the compiler now raises a clear error with a manual-
  action hint (per the repo guide's fail-loud exemption for changes with no
  prior schema representation). Authors must remove these keys by hand.

- style.board: renamed to style.frame: (BoardStyle -> FrameStyle) as part of
  the 2026-08 rebrand vocabulary cut (dbt-charts/AGENTS.md's terminology table).
  The rebrand's other rename, ``LayoutItem.type: "face"`` -> ``"board"``, has
  no Move here: that field lives only on the NORMALIZE-stage ``Board`` model
  (compile/models/board/normalized.py), never on ``AuthoredBoard`` -- no
  authored YAML ever writes a literal ``type: face``, so there is no authored
  key for a migration to recognize or rewrite. The rename of that internal
  literal is a pure code change, with nothing for ``dct migrate`` to do.

- marks.square / marks.tick / marks.trail removed from GlobalMarksStyle: these
  three mark slots have no AuthoredChart variant (square/tick/trail are raw
  Vega-Lite marks, not authorable chart types). Style authored under
  style.charts.marks.square/tick/trail was previously validated-and-silently-
  ignored; it now raises an extra-forbidden error. dct migrate strips these keys.

- KPI style.tone retired; support.tone is now the only tone field on KPI
  charts (editorial convention: headline neutral, delta/support colored).
  When a face has an existing support: block, style.tone is moved into
  support.tone automatically.  When there is no support: block, style.tone is
  dropped and a warning names the chart: KpiSupportConfig rejects a support:
  block with only tone set, so inventing one would produce invalid output.
  Callout charts, which also have style.tone, are not touched — the
  ConditionalMove is scoped to chart_type="kpi".

- animation_duration removed from all chart style models (ChartsStyle,
  all 11 per-family theme patches, and chart-level style). The field never
  had any effect in the static-SVG render pipeline.
  Auto-stripped by dct migrate; authors should remove the keys.

- Chart-level style.scale removed from all six cartesian chart families
  (bar, line, area, scatter, histogram, heatmap) and from the per-family
  theme slots.  Only the continuous.zero sub-field had any effect on bar,
  line, and area (the smart-zero baseline decision).  When the chart already
  has a style.axis_y: block, that field is relocated to
  style.axis_y.scale.continuous.zero automatically.  When the chart has no
  style.axis_y block, dct migrate raises IncompleteMigrationError — the
  author must add style.axis_y.scale.continuous.zero by hand.
  Histogram and heatmap had no functional sub-fields;
  their chart-level scale: fails loud on the new schema (extra_forbidden)
  and is not migrated — authors should use style.axis_x.scale and
  style.axis_y.scale directly.  Scatter chart-level scale: also fails loud;
  authors should move domain/type/nice/padding/zero to axis_x.scale or
  axis_y.scale directly.

- allow_html: bool renamed and retyped to html_policy:
  Literal["none","safe-subset","trusted-raw"]. The mapping is total and
  lossless: allow_html: true -> html_policy: "trusted-raw" (the prior field
  wired directly to raw-HTML rendering); allow_html: false -> html_policy:
  "none" (the prior default). Move.value_map carries the value translation;
  an unmapped value raises MigrationError rather than silently passing
  through.

- scale.domain now rejects a null element (`domain: [1.0, null]`), which
  previously validated and was then discarded in silence. No migration: the
  missing bound would have to be invented, so a transform is not expressible
  (the guide's fail-loud carve-out). Authors give both bounds, or omit the key.

- style.variables.padding, style.variables.container_padding, and
  style.variables.popover_rail_background removed from VariablesStyle without
  replacement. Auto-stripped by dct migrate; authors should remove the keys.

- style.variables.value.numeric_variant removed from VariablesValueStyle
  without replacement. Auto-stripped by dct migrate; authors should remove
  the key.

- style.text.column.number renamed to style.text.column.max_number, and its
  meaning changed from a fixed column count to a ceiling the renderer may
  undershoot when there isn't enough text to fill every column. Not a
  lossless rename, but the authored value passes through unchanged rather
  than failing loud -- a deliberate release-time choice, not an oversight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dbt_charts.core.compile.migrations.migrations import (
    ConditionalMove,
    Deletion,
    Move,
    YamlKeyPath,
    suffix_rename_moves,
)
from dbt_charts.core.compile.schema.renderers.yaml_schema_catalog import (
    YamlSchemaCatalog,
)

if TYPE_CHECKING:
    from dbt_charts.core.compile.migrations.migrations import MappedScalar

DELETED_TAILS: tuple[YamlKeyPath, ...] = (
    # LegendStyle.interactive_legend — never had any effect.
    ("legend", "interactive_legend"),
    # theme-level per-family legend override — kpi/table never paint a legend.
    ("kpi", "legend"),
    ("table", "legend"),
    # theme-level per-family tooltip override — never consumed by the renderer.
    ("bar", "tooltip"),
    ("line", "tooltip"),
    ("area", "tooltip"),
    ("scatter", "tooltip"),
    ("heatmap", "tooltip"),
    ("histogram", "tooltip"),
    ("pie", "tooltip"),
    ("geoshape", "tooltip"),
    ("point_map", "tooltip"),
    ("kpi", "tooltip"),
    ("table", "tooltip"),
    # chart-level style.tooltip — never consumed by the renderer.
    ("style", "tooltip"),
    # style.charts.animation_duration — never had any effect; field removed.
    # 3-segment tail anchors this to the theme style path, avoiding a collision
    # with the board-root charts dict (which uses chart names as keys — a chart
    # named "animation_duration" would be silently deleted with a 2-segment tail).
    ("style", "charts", "animation_duration"),
    # theme-level per-family animation_duration — never had any effect.
    ("area", "animation_duration"),
    ("bar", "animation_duration"),
    ("geoshape", "animation_duration"),
    ("heatmap", "animation_duration"),
    ("histogram", "animation_duration"),
    ("kpi", "animation_duration"),
    ("line", "animation_duration"),
    ("pie", "animation_duration"),
    ("point_map", "animation_duration"),
    ("scatter", "animation_duration"),
    ("table", "animation_duration"),
    # chart-level style.animation_duration — never had any effect.
    ("style", "animation_duration"),
    # GlobalMarksStyle.square/tick/trail — these marks have no AuthoredChart variant;
    # style authored under marks.square/tick/trail was accepted-and-silently-ignored.
    ("marks", "square"),
    ("marks", "tick"),
    ("marks", "trail"),
    # VariablesStyle.padding/container_padding/popover_rail_background — removed
    # from the variables-bar theme without replacement.
    ("style", "variables", "padding"),
    ("style", "variables", "container_padding"),
    ("style", "variables", "popover_rail_background"),
    # VariablesValueStyle.numeric_variant — removed without replacement.
    ("style", "variables", "value", "numeric_variant"),
)
FRAME_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = ((("frame",), ("board",)),)

HTML_POLICY_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("html_policy",), ("allow_html",)),
)
HTML_POLICY_VALUE_MAP: dict[MappedScalar, MappedScalar] = {
    True: "trusted-raw",
    False: "none",
}

# TextColumnStyle.number -> max_number: authored value passes through unchanged.
# Not a lossless rename -- "number" meant a fixed column count, "max_number" is
# a ceiling the renderer may undershoot -- but the team chose pass-through over
# fail-loud for this release.
TEXT_COLUMN_NUMBER_RENAMES: tuple[tuple[YamlKeyPath, YamlKeyPath], ...] = (
    (("max_number",), ("number",)),
)


def deletions(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Deletion, ...]:
    """Return Deletion objects for the 0.4.0 -> 0.5.0 boundary."""
    return tuple(Deletion(source_schema, target_schema, tail) for tail in DELETED_TAILS)


def moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[Move, ...]:
    """Return Move objects for the 0.4.0 -> 0.5.0 boundary."""
    from dbt_charts.core.compile.models.board.authored import AuthoredBoard

    frame_moves = suffix_rename_moves(
        AuthoredBoard, source_schema, target_schema, FRAME_RENAMES, catalog=catalog
    )
    html_policy_moves = suffix_rename_moves(
        AuthoredBoard,
        source_schema,
        target_schema,
        HTML_POLICY_RENAMES,
        catalog=catalog,
        value_map=HTML_POLICY_VALUE_MAP,
    )
    text_column_number_moves = suffix_rename_moves(
        AuthoredBoard,
        source_schema,
        target_schema,
        TEXT_COLUMN_NUMBER_RENAMES,
        catalog=catalog,
    )
    return frame_moves + html_policy_moves + text_column_number_moves


def conditional_moves(
    source_schema: str, target_schema: str, *, catalog: YamlSchemaCatalog
) -> tuple[ConditionalMove, ...]:
    """Return ConditionalMove objects for the 0.4.0 -> 0.5.0 boundary."""
    _scale_drop = " Add style.axis_y.scale.continuous.zero: <true|false> manually."
    return (
        ConditionalMove(
            source_schema=source_schema,
            target_schema=target_schema,
            chart_type="kpi",
            old_tail=("style", "tone"),
            new_tail=("support", "tone"),
            sibling_tail=("support",),
            drop_warning=(
                "KPI chart {chart!r}: style.tone was dropped (not migrated) because"
                " the chart has no support: block. Add a support: block with a"
                " value: column reference, then set support.tone manually."
            ),
        ),
        ConditionalMove(
            source_schema=source_schema,
            target_schema=target_schema,
            chart_type="bar",
            old_tail=("style", "scale", "continuous", "zero"),
            new_tail=("style", "axis_y", "scale", "continuous", "zero"),
            sibling_tail=("style", "axis_y"),
            drop_warning=(
                "Bar chart {chart!r}: style.scale.continuous.zero was dropped (not"
                " migrated) because the chart has no style.axis_y: block." + _scale_drop
            ),
        ),
        ConditionalMove(
            source_schema=source_schema,
            target_schema=target_schema,
            chart_type="line",
            old_tail=("style", "scale", "continuous", "zero"),
            new_tail=("style", "axis_y", "scale", "continuous", "zero"),
            sibling_tail=("style", "axis_y"),
            drop_warning=(
                "Line chart {chart!r}: style.scale.continuous.zero was dropped (not"
                " migrated) because the chart has no style.axis_y: block." + _scale_drop
            ),
        ),
        ConditionalMove(
            source_schema=source_schema,
            target_schema=target_schema,
            chart_type="area",
            old_tail=("style", "scale", "continuous", "zero"),
            new_tail=("style", "axis_y", "scale", "continuous", "zero"),
            sibling_tail=("style", "axis_y"),
            drop_warning=(
                "Area chart {chart!r}: style.scale.continuous.zero was dropped (not"
                " migrated) because the chart has no style.axis_y: block." + _scale_drop
            ),
        ),
    )

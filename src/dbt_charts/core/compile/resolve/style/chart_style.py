"""Per-chart style merge: a chart's local style layered onto the board context.

Every authored override (axis, legend, scale, mark, palette, title, ...) is baked
into the returned ``ChartStyleContext`` field values, so a family resolver reads
one merged context and never the chart-local patch.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import Chart
from dbt_charts.core.compile.models.style.authored import ChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import ChartsStyle
from dbt_charts.core.compile.resolve.style.board import _seed_spark_colors
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import (
    apply_inherit,
    assert_style_total,
    defer_base_none_children,
    merge_unset,
    unset_autos,
)
from dbt_charts.core.compile.resolve.style.legend_position import without_engine_decided
from dbt_charts.core.compile.resolve.style.palette import ink_canvas
from dbt_charts.core.compile.resolve.style.tokens import (
    _EMOJI_MODE_TO_FAMILY,
    _append_emoji_family,
    _resolve_color_tokens,
    _resolve_self_tokens,
)

# Family sub-patch keys in author-precedence order.  The cascade reads global
# fields (palette, background, …) from the FIRST non-null sub-patch.
# Exactly one key is set per chart; the first non-null entry wins for shared fields.
_ALL_FAMILY_KEYS: tuple[str, ...] = (
    "bar",
    "line",
    "area",
    "scatter",
    "pie",
    "kpi",
    "spark_bar",
    "spark",
    "table",
    "heatmap",
    "geoshape",
    "point_map",
)

# Families that exist on the pre-inherit ``ChartsStyle`` route through the
# pre-inherit merge + intra-family inherit pass. ``spark`` is the lone
# exception: it is synthesized only on the resolved ``ChartStyleContext``
# (no pre-inherit counterpart, no intra-family font dependents), so it merges
# directly onto the resolved base instead.
_PRE_INHERIT_FAMILY_KEYS: frozenset[str] = frozenset(_ALL_FAMILY_KEYS) & frozenset(
    ChartsStyle.model_fields
)


def primary_patch(
    chart_style: ChartStylePatch,
) -> Any:  # type-state: explicit_any — one of many patch classes
    """Return the first non-null family sub-patch, or None."""
    for key in _ALL_FAMILY_KEYS:
        patch = getattr(
            chart_style, key, None
        )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
        if patch is not None:
            return patch
    return None


def _chart_style_has_overrides(
    style: ChartStylePatch | None,
) -> bool:  # type-state: optional — None when the chart authors no style
    if style is None:
        return False
    return any(v is not None for v in style.model_dump().values())


# Chart type -> family slot whose board-tier `background` is the chart's fill.
# Limited to the radial and map families: callout's theme default is `by_tone`,
# not a fill.
_BOARD_BACKGROUND_SLOT: dict[str, str] = {
    "pie": "pie",
    "donut": "pie",
    "geoshape": "geoshape",
    "map": "geoshape",
    "point_map": "point_map",
    "bubble_map": "point_map",
}

# Fields that cascade global -> theme family (picked by chart.type) -> chart-local.
# Inherit can't express this: its source path is fixed, the family is runtime data.
_FAMILY_THEN_LOCAL_FIELDS: tuple[str, ...] = ("legend", "title")


def _family_field_patch(
    chart_style_context: ChartStyleContext,
    chart_type: str | None,
    field: str,  # type-state: optional — None for a chart type with no family slot
) -> (
    BaseModel | None
):  # type-state: optional — None when the family sets no patch for the field
    """Return the theme family's patch for ``field`` on chart_type, or None."""
    if not chart_type:
        return None
    family = getattr(
        chart_style_context, chart_type, None
    )  # type-state: silent_fallback — chart_type is an authored family-name string; ChartStyleContext carries a fixed attribute set, so an unrecognized name legitimately has no matching field
    if family is None:
        return None
    return getattr(
        family, field, None
    )  # type-state: silent_fallback — family is a per-family style union; not every member (e.g. ResolvedCalloutStyle) carries every cascade field


ChartStyleOverrides = dict[str, Any]  # type-state: explicit_any — mixed field values


def merge_chart_style(
    board_ctx: ChartStyleContext,
    chart: Chart,
) -> ChartStyleOverrides:
    """Merge a chart's local style onto the board context.

    Returns the ``ChartStyleContext`` fields the chart overrides, keyed by field
    name; empty when the chart has no local style overrides and no per-family
    theme patches for its type.

    Per-family merge + re-inherit
    ------------------------------
    Every chart-patched family (bar, line, …, kpi, table, plus the nested
    ``support_table``) is merged onto the pre-inherit ``Style.charts`` subtree
    (``board_context.pre_style``); ``apply_inherit`` then re-runs the full
    inherit graph so a patch that sets a family-root ``font`` propagates to its
    descendant leaves, and the emoji font family is appended. One uniform
    merge+inherit+emoji pass for every family — no font-vs-non-font branching.
    ``apply_inherit`` and ``_append_emoji_family`` are both idempotent, so a
    family that changed no inheritance source is unaffected by the re-inherit,
    and a chart-local font-family literal (e.g. ``kpi.value.font``) gets the
    same emoji fallback that board-level fonts get — consistent resolution, not
    a special case.

    A column's declared format is NOT handled here — it is folded directly into
    the per-axis resolution in ``resolved_axis_style()`` (via its
    ``column_layer_format`` parameter, fed by ``axis_layer_format()``), so
    ``axis_x``/``axis_y``.format is already correct once baked. Value-label
    format falls back to that same baked axis format at chart-resolve time
    (``_marks.py``), not via an inherit-graph edge from ``axis_quantitative``.

    ``spark`` is the lone exception (``_PRE_INHERIT_FAMILY_KEYS``): it is
    synthesized only on ``ChartStyleContext`` and has no pre-inherit
    counterpart, so its patch merges directly onto the base context.

    All other global overrides (axis, legend, scale, pagination, color,
    palette, background, title) are applied via ``dataclasses.replace`` on the
    already-merged base_charts context. Post-merge patching is legal here because
    ``ChartStyleContext`` is compiler working state, not a ``Resolved*`` value —
    axis inheritance is resolved at render time by ``resolved_axis_style``.

    x_label/y_label are NOT read here. They drive the theme's
    ``title.visible: auto`` through the axis cascade
    (``resolved_axis_style``'s ``labeled`` parameter), not a baked override.
    """
    per_family_patch = chart.style
    chart_type_raw = chart.type
    chart_type = chart_type_raw  # Chart.type is always a concrete type string

    # Chart style is a per-family patch; wrap it into a monolithic ChartStylePatch
    # so the rest of this function (which iterates _ALL_FAMILY_KEYS) works. An
    # all-None per-family patch is equivalent to no patch — treat as None so the
    # fast path (return board.charts unchanged) fires for empty overrides.
    if per_family_patch is not None and any(
        v is not None for v in per_family_patch.model_dump().values()
    ):
        from dbt_charts.core.compile.normalize.charts import (  # noqa: PLC0415
            _AUTHORED_FAMILY_TO_MONOLITHIC_KEY,
        )

        # The map covers every family, so the key is always present; model_validate
        # avoids the static-keyword-argument mismatch that **{} would trigger.
        _family_key = _AUTHORED_FAMILY_TO_MONOLITHIC_KEY[chart_type_raw]
        chart_style_patch = ChartStylePatch.model_validate(
            {_family_key: per_family_patch}
        )
    else:
        chart_style_patch = None

    base_charts = board_ctx

    # Resolve palette tokens (e.g. "dbt-creams.cream-50" → "#918878") across
    # all color-bearing fields of the chart-local style patch — before any field
    # is stored as a sentinel or merged into the resolved tree.  Raises
    # UnknownColorError on unknown tokens, matching validate-and-error-fast.
    if chart_style_patch is not None:
        chart_style_patch = _resolve_color_tokens(
            chart_style_patch,
            base_charts.palettes,
            base_charts.roles,
            single_series_palette=base_charts.single_series_palette,
        )

    # A board-tier family background sits between the global background and the
    # chart's own; an unauthored family slot is None, so non-None means authored.
    _background_slot = _BOARD_BACKGROUND_SLOT.get(chart_type_raw)
    _family_background = (
        getattr(base_charts, _background_slot).background
        if _background_slot is not None
        else None
    )

    if (
        not _chart_style_has_overrides(chart_style_patch)
        and _family_background is None
        and all(
            _family_field_patch(base_charts, chart_type, f) is None
            for f in _FAMILY_THEN_LOCAL_FIELDS
        )
    ):
        return {}

    # Primary family sub-patch: the single non-null family patch.
    # Shared global fields (palette, background, axis overrides, …) are read from it.
    primary = (
        primary_patch(chart_style_patch) if chart_style_patch is not None else None
    )

    # Per-family merge: merge every chart-patched family onto the pre-inherit
    # board Style, re-run the full inherit graph so a family-root font patch
    # propagates to its leaves, then append the emoji fallback. Uniform for
    # all families — apply_inherit and _append_emoji_family are both idempotent.
    charts_family_patch: dict[str, Any] = {}  # type-state: explicit_any — patches
    overrides: ChartStyleOverrides = {}
    if chart_style_patch is not None:
        for _key in _ALL_FAMILY_KEYS:
            _fam_patch = getattr(chart_style_patch, _key)
            if _fam_patch is None:
                continue
            if _key in _PRE_INHERIT_FAMILY_KEYS:
                charts_family_patch[_key] = _fam_patch
            else:
                # Synthesized resolved-only family (spark): merge onto the base.
                overrides[_key] = merge_unset(getattr(base_charts, _key), _fam_patch)
        if primary is not None:
            _dt_patch = getattr(
                primary, "support_table", None
            )  # type-state: silent_fallback — primary is a per-family patch union; families with no support_table slot legitimately lack the attribute
            if _dt_patch is not None:
                charts_family_patch["support_table"] = _dt_patch

    # A chart's own background is the canvas its knockouts sit on, so they
    # are substituted here from the token-bearing tree, not inherited from
    # the board's substitution.
    _background = getattr(
        primary, "background", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _background is None:
        _background = _family_background
    # Composite over board_canvas, not ink_canvas (the CHART canvas) --
    # see ChartStyleContext.board_canvas's own comment for why.
    own_canvas = (
        None
        if _background is None
        else ink_canvas(_background, base_charts.board_canvas)
    )

    if charts_family_patch:
        pre = base_charts.pre_style
        if own_canvas is not None:
            pre = pre.model_copy(update={"charts": base_charts.canvas_token_charts})
        merged_charts = pre.charts
        for _key, _fam_patch in charts_family_patch.items():
            _pre_inherit_patch, _, _ = defer_base_none_children(
                getattr(merged_charts, _key), _fam_patch, f"Style.charts.{_key}"
            )
            merged_charts = merged_charts.model_copy(
                update={
                    _key: merge_onto_base(
                        getattr(merged_charts, _key), _pre_inherit_patch
                    )
                }
            )
        cascaded_pre = apply_inherit(
            pre.model_copy(update={"charts": merged_charts}),
            get_inherit_graph(),
        )
        # single_series_palette is a list, so Inherit can't express the spark-color
        # link — _seed_spark_colors is the same imperative step _finalize_style()
        # runs at board level. Without it, a table/spark_bar chart carrying any
        # style patch would extract an unseeded family below, and render's
        # `assert spark...color is not None` would crash.
        cascaded_pre = _seed_spark_colors(cascaded_pre)
        # Re-merge chart-family patches onto the cascaded result.  The first merge
        # (into the pre-inherit tree above) defers model-valued patches whose slot
        # is None until the global-inherit pass populates the required container
        # (e.g. pie.marks.slice.labels ← charts.marks.slice.labels). Re-merging
        # now applies that authored patch onto the complete inherited base.
        # All keys in charts_family_patch come from _PRE_INHERIT_FAMILY_KEYS, which is
        # a subset of ChartsStyle.model_fields, so 2-arg getattr is safe.
        for _key, _fam_patch in charts_family_patch.items():
            _post_inherit_fam = getattr(cascaded_pre.charts, _key)
            _re_merged = merge_onto_base(_post_inherit_fam, _fam_patch)
            if _re_merged is not _post_inherit_fam:
                cascaded_pre = cascaded_pre.model_copy(
                    update={
                        "charts": cascaded_pre.charts.model_copy(
                            update={_key: _re_merged}
                        )
                    }
                )
        assert_style_total(cascaded_pre)
        cascaded_pre = unset_autos(cascaded_pre)
        emoji_family = _EMOJI_MODE_TO_FAMILY.get(cascaded_pre.font.emoji)
        # Extract every family that had a style patch — needed for font
        # propagation (a family-root font patch reaches its descendant leaves
        # via the re-inherit pass above).
        for _key in charts_family_patch:
            _fam = getattr(cascaded_pre.charts, _key)
            if emoji_family is not None:
                _fam = _append_emoji_family(_fam, emoji_family)
            if own_canvas is not None:
                _fam = _resolve_self_tokens(_fam, own_canvas)
            overrides[_key] = _fam

    # --- Palette + Color ---
    # chart-local style.color carries three sub-fields:
    #   .static   → ChartStyleContext.color (static ink, render reads it)
    #   .categorical.palette → ChartStyleContext.palette (categorical stops list)
    #   .categorical.single_series_palette → ChartStyleContext.single_series_palette
    #   .gradient → consumed by normalize_chart_channels; not stored in cascade
    # kpi/spark_bar/callout have no `color` field at all, hence getattr's None
    # default. Every family that does carry one types it as a model, never a
    # bare str — so there is no string arm here.
    _color = getattr(
        primary, "color", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _color is not None:
        if _color.static is not None:
            overrides["color"] = _color.static
        _categorical = getattr(
            _color, "categorical", None
        )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
        if _categorical is not None:
            from dbt_charts.core.compile.resolve.style.palette import (
                palette as resolve_palette,
                resolve_palette_alias,
            )  # noqa: PLC0415

            _palette = _categorical.palette
            if _palette is not None:
                # _categorical is a synthesized CategoricalColorStylePatch (see
                # build_patch_model) — it carries no validators, so a raw string
                # here is still unresolved and must be run through
                # resolve_palette_alias directly (not _categorical's own
                # requested_alias_palette, which is always None on a patch).
                if isinstance(_palette, str):
                    overrides["palette"], overrides["requested_alias_palette"] = (
                        resolve_palette_alias(_palette)
                    )
                else:
                    overrides["palette"] = list(_palette)
                    overrides["requested_alias_palette"] = None
            _ssp = _categorical.single_series_palette
            if _ssp is not None:
                overrides["single_series_palette"] = (
                    resolve_palette(_ssp) if isinstance(_ssp, str) else list(_ssp)
                )
        # _color.gradient is consumed by channel.py; not stored in resolved cascade.

    # --- Top-level Vega-facing ---
    if _background is not None:
        overrides["background"] = _background
        overrides["ink_canvas"] = own_canvas

    # --- Chart-local axis overrides ---
    # Stored as typed axis-variant patch sentinels on resolved_chart_style
    # so the renderer's resolved_axis_style helper can apply them last, in
    # the canonical cascade order. Baking them into base.axis_x/y here would invert
    # the precedence relative to axis_quantitative — the renderer must keep
    # control of the layering. Axis inheritance is resolved at render time by
    # resolved_axis_style; it does NOT go through the pre-inherit Style tree.
    _axis = getattr(
        primary, "axis", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _axis is not None:
        overrides["axis_overrides_global"] = _axis
    _axis_x = getattr(
        primary, "axis_x", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _axis_x is not None:
        overrides["axis_overrides_x"] = _axis_x
    _axis_y = getattr(
        primary, "axis_y", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _axis_y is not None:
        overrides["axis_overrides_y"] = _axis_y
    _axis_quantitative = getattr(
        primary, "axis_quantitative", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _axis_quantitative is not None:
        overrides["axis_overrides_quantitative"] = _axis_quantitative
    _axis_band = getattr(
        primary, "axis_band", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _axis_band is not None:
        overrides["axis_overrides_band"] = _axis_band

    for _field in _FAMILY_THEN_LOCAL_FIELDS:
        _global = getattr(base_charts, _field)
        _effective = _global
        _family_patch = _family_field_patch(base_charts, chart_type, _field)
        if _field == "legend":
            # Placement, direction and visibility are decided per chart (`_base_kwargs`).
            _family_patch = without_engine_decided(_family_patch)
        if _family_patch is not None:
            _effective = merge_unset(_effective, _family_patch)
        _local = None
        if primary is not None:
            _local = getattr(
                primary, _field, None
            )  # type-state: silent_fallback — primary is a per-family patch union; see the support_table precedent above
        if _field == "legend":
            _local = without_engine_decided(_local)
        if _local is not None:
            _effective = merge_unset(_effective, _local)
        if _effective is not _global:
            overrides[_field] = _effective

    # --- Pagination (table charts expose this via TableChartStyle.pagination) ---
    # Chart-local pagination layers on top of the board default. ``enabled``
    # is required on the authored Patch so it always sets; ``page_rows`` is
    # optional — when omitted the cascade falls back to the board-level
    # page_rows. An explicit ``page_rows: null`` means unlimited and is kept.
    _pagination = getattr(
        primary, "pagination", None
    )  # type-state: silent_fallback — the patch is a per-family union; families without the slot legitimately lack the attribute
    if _pagination is not None:
        board = base_charts.pagination
        merged_pagination = _pagination
        if (
            "page_rows" not in merged_pagination.model_fields_set
            and board is not None
            and board.page_rows is not None
        ):
            merged_pagination = merged_pagination.model_copy(
                update={"page_rows": board.page_rows}
            )
        overrides["pagination"] = merged_pagination

    return overrides

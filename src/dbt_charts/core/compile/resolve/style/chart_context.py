"""Per-chart style cascade — merges chart-local ChartStylePatch into ChartStyleContext.

Architectural directive: render code reads only the final per-chart Resolved*Style
(and ResolvedStyle for board chrome) — it never sees the chart-local Patch or this
cascade context. After build_chart_style_context runs, every authored override
(axis, legend, scale, mark, palette, title, …) is baked into the context's
fields, ready for a family resolver to project into its final style slice.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from pydantic import BaseModel

from dbt_charts.core.compile.config import get_theme_style
from dbt_charts.core.compile.merge import merge_onto_base
from dbt_charts.core.compile.models.chart.normalized import Chart, _CartesianChartFields
from dbt_charts.core.compile.models.primitives import FormatConfig
from dbt_charts.core.compile.models.style.authored import ChartStylePatch
from dbt_charts.core.compile.models.style.context import ChartStyleContext
from dbt_charts.core.compile.models.style.theme import ChartsStyle
from dbt_charts.core.compile.resolve.style.board import (
    _seed_spark_colors,
    resolve_chart_style_context,
)
from dbt_charts.core.compile.resolve.style.inherit_graph import get_inherit_graph
from dbt_charts.core.compile.resolve.style.inherit_resolver import apply_inherit
from dbt_charts.core.compile.resolve.style.palette import ink_canvas
from dbt_charts.core.compile.resolve.style.tokens import (
    _EMOJI_MODE_TO_FAMILY,
    _append_emoji_family,
    _resolve_color_tokens,
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


def _get_primary_patch(chart_style: ChartStylePatch) -> Any:
    """Return the first non-null family sub-patch, or None."""
    for key in _ALL_FAMILY_KEYS:
        patch = getattr(chart_style, key, None)
        if patch is not None:
            return patch
    return None


def _chart_style_has_overrides(style: ChartStylePatch | None) -> bool:
    if style is None:
        return False
    return any(v is not None for v in style.model_dump().values())


def _has_family_legend_patch(
    chart_style_context: ChartStyleContext, chart_type: str | None
) -> bool:
    """Return True if chart_type has a non-None legend patch in the theme family style.

    Used to bypass the fast path when a per-family legend override exists in the theme
    but no chart-local style override is authored.
    """
    if not chart_type:
        return False
    family = getattr(chart_style_context, chart_type, None)
    if family is None:
        return False
    return getattr(family, "legend", None) is not None


def _defer_base_none_children(base: BaseModel, patch: BaseModel) -> BaseModel:
    """Omit model-valued patch paths whose pre-inherit base is still absent.

    The original patch is merged again after inheritance. Deferring only these
    paths lets inheritance create their required compiled container first, while
    every reachable patch path still participates in the pre-inherit cascade.
    """
    values: dict[str, Any] = {}
    fields_set: set[str] = set()
    base_fields = type(base).model_fields
    for name in patch.model_fields_set:
        patch_value = getattr(patch, name)
        if name not in base_fields:
            values[name] = patch_value
            fields_set.add(name)
            continue

        base_value = getattr(base, name)
        if base_value is None and isinstance(patch_value, BaseModel):
            continue
        if isinstance(base_value, BaseModel) and isinstance(patch_value, BaseModel):
            patch_value = _defer_base_none_children(base_value, patch_value)
            if not patch_value.model_fields_set:
                continue
        values[name] = patch_value
        fields_set.add(name)

    return type(patch).model_construct(_fields_set=fields_set, **values)


def chart_authored_axis_format(
    chart: Chart, channel_type: str
) -> str | FormatConfig | None:
    """Return the chart-level axis-format fallback for one channel, or None.

    Precedence: style.number_format (quantitative) / style.time_format
    (temporal) → chart.format (quantitative only). Returns None for
    nominal/ordinal channels (no format concept applies) and for
    non-cartesian families (kpi, pie, … have no
    number_format/time_format/chart.format at all).

    ``chart.format`` is a measure-axis (quantitative) shortcut only — it must
    never apply to a temporal axis. A d3 numeric format string baked onto a
    temporal axis breaks the smart month/year cadence labelExpr downstream
    (VL renders a temporal encoding with a numeric format spec, producing
    garbage tick labels). ``style.time_format`` is the only legitimate
    temporal-axis fallback.

    An explicit ``style.axis_x``/``axis_y``/``axis_quantitative.labels.format``
    override is NOT read here — those apply as later cascade layers inside
    ``resolved_axis_style()`` and win over this fallback automatically; folding
    them in here too would just duplicate that merge.

    ``chart.format`` accepts the documented object form (``FormatConfig``,
    e.g. ``format: {spec: ",.0f"}``) as well as a plain D3 string; the caller
    resolves either through ``resolve_format()``, which already extracts
    ``FormatConfig.spec``. ``style.number_format``/``style.time_format`` are
    plain-string theme fields with no object form.
    """
    if channel_type not in ("quantitative", "temporal"):
        return None
    # number_format/time_format only exist on the cartesian family
    # (bar/line/area/scatter/heatmap); other families (kpi, pie, …) don't
    # carry an axis_quantitative concept at all.
    if not isinstance(chart, _CartesianChartFields):
        return None
    primary = chart.style
    if channel_type == "temporal":
        if primary is not None and isinstance(primary.time_format, str):
            return primary.time_format
        return None
    if primary is not None and isinstance(primary.number_format, str):
        return primary.number_format
    if isinstance(chart.format, (str, FormatConfig)):
        return chart.format
    return None


def build_chart_style_context(
    board_context: ChartStyleContext | None,
    chart: Chart,
) -> ChartStyleContext:
    """Build the fully-merged ChartStyleContext for a single chart.

    Fast path: chart has no local style overrides, no per-family theme patches
    for chart_type, and no chart-level number format → return the board context
    unchanged.

    Merge path: apply chart.style on top of the board context.

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

    Chart-level number/time format (``chart.format`` / ``style.number_format`` /
    ``style.time_format``) is NOT handled here — it is folded directly into the
    per-axis resolution in ``resolved_axis_style()`` (via its
    ``chart_fallback_format`` parameter, fed by ``chart_authored_axis_format()``),
    so ``axis_x``/``axis_y``.format is already correct once baked. Value-label
    format falls back to that same baked axis format at chart-resolve time
    (``_marks.py``), not via an inherit-graph edge from ``axis_quantitative``.

    ``spark`` is the lone exception (``_PRE_INHERIT_FAMILY_KEYS``): it is
    synthesized only on ``ChartStyleContext`` and has no pre-inherit
    counterpart, so its patch merges directly onto the base context.

    ``callout`` is never routed through the per-chart merge (it is never a member
    of ``_ALL_FAMILY_KEYS``): its theme (``CalloutChartStyle``) and resolved
    (``ResolvedCalloutStyle``) representations are different Python types, so it
    always merges post-resolve onto ``base_charts.callout``.

    All other global overrides (axis, legend, scale, pagination, color,
    palette, background, title) are applied via ``dataclasses.replace`` on the
    already-merged base_charts context. Post-merge patching is legal here because
    ``ChartStyleContext`` is compiler working state, not a ``Resolved*`` value —
    axis inheritance is resolved at render time by ``resolved_axis_style``.

    x_label/y_label are NOT read here. The label-forcing title.visible default
    they drive is injected directly into the axis cascade
    (``resolved_axis_style``'s ``label_authored`` parameter) rather than baked
    onto ``axis_overrides_x``/``axis_overrides_y`` here — the cascade already
    knows the full chart-local layer order, so it is the one place that default
    can sit beneath every chart-local override without a separate guard.
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

    base_charts = board_context or resolve_chart_style_context(get_theme_style())

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

    if not _chart_style_has_overrides(
        chart_style_patch
    ) and not _has_family_legend_patch(base_charts, chart_type):
        return base_charts

    # Primary family sub-patch: the single non-null family patch.
    # Shared global fields (palette, background, axis overrides, …) are read from it.
    primary = (
        _get_primary_patch(chart_style_patch) if chart_style_patch is not None else None
    )

    # Per-family merge: merge every chart-patched family onto the pre-inherit
    # board Style, re-run the full inherit graph so a family-root font patch
    # propagates to its leaves, then append the emoji fallback. Uniform for
    # all families — apply_inherit and _append_emoji_family are both idempotent.
    charts_family_patch: dict[str, Any] = {}
    overrides: dict[str, Any] = {}
    if chart_style_patch is not None:
        for _key in _ALL_FAMILY_KEYS:
            _fam_patch = getattr(chart_style_patch, _key)
            if _fam_patch is None:
                continue
            if _key in _PRE_INHERIT_FAMILY_KEYS:
                charts_family_patch[_key] = _fam_patch
            else:
                # Synthesized resolved-only family (spark): merge onto the base.
                overrides[_key] = merge_onto_base(
                    getattr(base_charts, _key), _fam_patch
                )
        if primary is not None:
            _dt_patch = getattr(
                primary, "support_table", None
            )  # type-state: silent_fallback — primary is a per-family patch union; families with no support_table slot legitimately lack the attribute
            if _dt_patch is not None:
                charts_family_patch["support_table"] = _dt_patch

    if charts_family_patch:
        pre = base_charts.pre_style
        merged_charts = pre.charts
        for _key, _fam_patch in charts_family_patch.items():
            _pre_inherit_patch = _defer_base_none_children(
                getattr(merged_charts, _key), _fam_patch
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
        emoji_family = _EMOJI_MODE_TO_FAMILY.get(cascaded_pre.font.emoji)
        # Extract every family that had a style patch — needed for font
        # propagation (a family-root font patch reaches its descendant leaves
        # via the re-inherit pass above).
        for _key in charts_family_patch:
            _fam = getattr(cascaded_pre.charts, _key)
            if emoji_family is not None:
                _fam = _append_emoji_family(_fam, emoji_family)
            overrides[_key] = _fam

    # --- Callout (post-resolve: theme/resolved types differ, see docstring) ---
    if chart_style_patch is not None:
        _callout_patch = chart_style_patch.callout
        if _callout_patch is not None:
            overrides["callout"] = merge_onto_base(base_charts.callout, _callout_patch)

    # --- Palette + Color ---
    # chart-local style.color carries three sub-fields:
    #   .static   → ChartStyleContext.color (static ink, render reads it)
    #   .categorical.palette → ChartStyleContext.palette (categorical stops list)
    #   .categorical.single_series_palette → ChartStyleContext.single_series_palette
    #   .gradient → consumed by normalize_chart_channels; not stored in cascade
    # kpi/spark_bar/callout have no `color` field at all, hence getattr's None
    # default. Every family that does carry one types it as a model, never a
    # bare str — so there is no string arm here.
    _color = getattr(primary, "color", None)
    if _color is not None:
        if _color.static is not None:
            overrides["color"] = _color.static
        _categorical = getattr(_color, "categorical", None)
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
    _background = getattr(primary, "background", None)
    if _background is not None:
        overrides["background"] = _background
        # The chart-local background composites over the board's own
        # (already-opaque) canvas -- ink_canvas must be recomputed alongside
        # background, or it silently keeps describing the board's canvas
        # after the chart just painted a different one. _palette.py/pie.py
        # read this field directly instead of hand-compositing themselves.
        overrides["ink_canvas"] = ink_canvas(_background, base_charts.ink_canvas)
    _title = getattr(primary, "title", None)
    if _title is not None:
        overrides["title"] = merge_onto_base(base_charts.title, _title)

    # --- Chart-local axis overrides ---
    # Stored as typed axis-variant patch sentinels on resolved_chart_style
    # so the renderer's resolved_axis_style helper can apply Layers 11/12/13 in
    # the canonical cascade order (after Layer 3 axis_quantitative and Layer
    # 4 chart-type axis). Baking them into base.axis_x/y here would invert
    # the precedence relative to axis_quantitative — the renderer must keep
    # control of the layering. Axis inheritance is resolved at render time by
    # resolved_axis_style; it does NOT go through the pre-inherit Style tree.
    _axis = getattr(primary, "axis", None)
    if _axis is not None:
        overrides["axis_overrides_global"] = _axis
    _axis_x = getattr(primary, "axis_x", None)
    if _axis_x is not None:
        overrides["axis_overrides_x"] = _axis_x
    _axis_y = getattr(primary, "axis_y", None)
    if _axis_y is not None:
        overrides["axis_overrides_y"] = _axis_y
    _axis_quantitative = getattr(primary, "axis_quantitative", None)
    if _axis_quantitative is not None:
        overrides["axis_overrides_quantitative"] = _axis_quantitative
    _axis_band = getattr(primary, "axis_band", None)
    if _axis_band is not None:
        overrides["axis_overrides_band"] = _axis_band

    # --- Legend ---
    # Apply per-family theme patch first (e.g. editorial bar.legend.visible: true),
    # then board-local patch on top. Merge order: global → family theme → board-local.
    _effective_legend = base_charts.legend
    family_style = getattr(base_charts, chart_type, None)
    family_legend_patch = (
        getattr(family_style, "legend", None) if family_style is not None else None
    )
    if family_legend_patch is not None:
        _effective_legend = merge_onto_base(base_charts.legend, family_legend_patch)
    _legend = getattr(primary, "legend", None) if primary is not None else None
    if _legend is not None:
        _effective_legend = merge_onto_base(_effective_legend, _legend)
    if _effective_legend is not base_charts.legend:
        overrides["legend"] = _effective_legend

    # --- Pagination (table charts expose this via TableChartStyle.pagination) ---
    # Chart-local pagination layers on top of the board default. ``enabled``
    # is required on the authored Patch so it always sets; ``page_rows`` is
    # optional — when unset the cascade falls back to the board-level
    # page_rows so the renderer reads a single resolved value off
    # ``effective.pagination`` without re-running the merge.
    _pagination = getattr(primary, "pagination", None)
    if _pagination is not None:
        board = base_charts.pagination
        merged_pagination = _pagination
        if (
            merged_pagination.page_rows is None
            and board is not None
            and board.page_rows is not None
        ):
            merged_pagination = merged_pagination.model_copy(
                update={"page_rows": board.page_rows}
            )
        overrides["pagination"] = merged_pagination

    if not overrides:
        return base_charts

    return dataclasses.replace(base_charts, **overrides)


def family_patch_for(style: ChartStylePatch | Any, chart_type: str) -> Any:
    """Return the family sub-patch for chart_type, or None.

    For V1 monolithic ChartStylePatch: look up the nested family slot.
    For per-family patches: the style IS the family patch — return it directly.
    """
    if style is None:
        return None
    if not isinstance(style, ChartStylePatch):
        # Chart per-family patch is already the family sub-patch.
        return style

    from dbt_charts.core.compile.normalize.charts import (  # noqa: PLC0415
        _AUTHORED_FAMILY_TO_MONOLITHIC_KEY,
    )

    key = _AUTHORED_FAMILY_TO_MONOLITHIC_KEY.get(chart_type)
    if key is not None:
        return getattr(style, key, None)
    return _get_primary_patch(style)


def read_authored_format(chart: Chart) -> str | FormatConfig | None:
    """Extract authored format from style.number_format, style.axis_y.labels.format, or chart.format."""
    patch = family_patch_for(chart.style, chart.type)
    if patch is not None:
        nf = getattr(patch, "number_format", None)
        if nf:
            return nf
        axis_y = getattr(patch, "axis_y", None)
        labels = getattr(axis_y, "labels", None) if axis_y is not None else None
        if labels is not None and getattr(labels, "format", None):
            return labels.format
    return getattr(chart, "format", None)

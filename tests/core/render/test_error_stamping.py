"""Ratchet: raw raise ValueError/TypeError/KeyError/RuntimeError in render/ must be
stamped with an ERR-* code (RenderError.from_code(...)) or explicitly allowlisted.

Unstamped raises surface to users as ERR-INTERNAL (see rendering.py's catch-all
wrap), losing the structured code/fields the error registry exists to provide. This
test doesn't require every raise to be migrated today — it prevents NEW ones from
landing unstamped, and forces the allowlist to shrink as raises are migrated (see
test_allowlist_has_no_stale_entries).

Walker implementation lives in tests/core/_raise_ratchet.py, shared with
tests/core/execute/test_error_stamping.py — one AST walker, two roots.
"""

from __future__ import annotations

from ..._paths import DBT_CHARTS_PKG_DIR
from .._raise_ratchet import (
    assert_allowlist_has_no_stale_entries,
    assert_no_new_unstamped_raises,
)

_RENDER_ROOT = DBT_CHARTS_PKG_DIR / "core" / "render"

# Keyed by (path relative to render/, dotted qualname of enclosing function/method,
# or "<module>" for module-level raises). One entry covers every raise in that
# function — lineno-independent, so refactoring the function body doesn't require
# touching this list. WHY is uniform: none of these are migrated to a render-domain
# code yet, so they still fall back to ERR-INTERNAL. Shrinks only.
ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # chart/kpi.py
        ("chart/kpi.py", "_render_kpi_svg_core"),
        ("chart/kpi.py", "render_kpi_svg"),
        # chart/spark.py
        ("chart/spark.py", "_resolve_spark_color"),
        ("chart/spark.py", "render_spark"),
        # chart/presentation.py
        ("chart/presentation.py", "apply_presentation_defaults"),
        # chart/support_table_attachment.py
        ("chart/support_table_attachment.py", "_aggregate_by_x"),
        ("chart/support_table_attachment.py", "_entry_cell_widths"),
        ("chart/support_table_attachment.py", "apply_chart_support_table_post_pass"),
        # Theme-cascade-guarantee guard (font.family always populated by a
        # fully-resolved support-table style), identical in kind to the
        # allowlisted _entry_cell_widths font.size guard above.
        ("chart/support_table_attachment.py", "resolved_pad_font_family"),
        # chart/table.py
        ("chart/table.py", "_render_spark_cell"),
        # chart/table_support.py
        ("chart/table_support.py", "_validate_strftime_spec"),
        ("chart/table_support.py", "format_temporal_value"),
        ("chart/table_support.py", "resolve_cell_link._sub"),
        ("chart/table_support.py", "resolve_palette_stops"),
        ("chart/table_support.py", "interpolate_scale_color"),
        # chart/time_unit_detect.py
        ("chart/time_unit_detect.py", "detect_time_unit"),
        ("chart/time_unit_detect.py", "_week_to_iso"),
        ("chart/time_unit_detect.py", "_bucket_to_iso"),
        ("chart/time_unit_detect.py", "normalize_labeled_temporal"),
        ("chart/time_unit_detect.py", "_next_bucket"),
        ("chart/time_unit_detect.py", "_floor_to_bucket_start"),
        ("chart/time_unit_detect.py", "next_coarser_label_unit"),
        ("chart/time_unit_detect.py", "_cadence_token_width"),
        # chart/type_inference.py
        ("chart/type_inference.py", "<module>"),
        # chart/emitters/_channels.py
        ("chart/emitters/_channels.py", "channel_to_encoding"),
        # chart/emitters/_label_overlap.py
        ("chart/emitters/_label_overlap.py", "_pick_tilt_for_widths"),
        # chart/emitters/bar.py
        ("chart/emitters/bar.py", "_emit_horizontal"),
        # chart/emitters/geo.py
        ("chart/emitters/geo.py", "GeoshapeEmitter._emit_choropleth"),
        # chart/features/click_interactivity.py
        ("chart/features/click_interactivity.py", "_build_href_calc_expr"),
        # chart/features/endpoint_labels.py
        ("chart/features/endpoint_labels.py", "EndpointLabelFeature.apply"),
        # chart/vega_lite.py
        ("chart/vega_lite.py", "generate_vega_lite_spec"),
        # chart/translate.py
        ("chart/translate.py", "_translate_layer"),
        ("chart/translate.py", "_wrap_endpoint_labels"),
        ("chart/translate.py", "translate_to_vl"),
        # converters/chart.py
        ("converters/chart.py", "render_svg_content"),
        ("converters/chart.py", "render_chart_artifact"),
        # json_format.py
        ("json_format.py", "_json_default"),
        # layouts.py
        ("layouts.py", "render_details_summary"),
        # sizing.py
        ("sizing.py", "compact_style_kwargs"),
        # svg_utils.py
        ("svg_utils.py", "extract_svg_inner_content"),
        # terminal_charts.py
        ("terminal_charts.py", "_terminal_color_field"),
        # variables_resolve.py
        ("variables_resolve.py", "format_variable_display_value"),
        # conditions.py
        ("conditions.py", "coerce_bool"),
        ("conditions.py", "eval_bool_condition"),
    }
)


def test_no_new_unstamped_raises_in_render() -> None:
    """Every raw raise ValueError/TypeError/KeyError/RuntimeError in render/ must be
    either migrated to RenderError.from_code(...) or explicitly allowlisted."""
    assert_no_new_unstamped_raises(_RENDER_ROOT, ALLOWLIST, "render")


def test_allowlist_has_no_stale_entries() -> None:
    """Allowlist entries must correspond to a real raise — forces cleanup on migration."""
    assert_allowlist_has_no_stale_entries(_RENDER_ROOT, ALLOWLIST)

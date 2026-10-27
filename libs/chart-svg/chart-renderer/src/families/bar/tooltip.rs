//! The structured tooltip text of a bar datum.
//!
//! Ports `features/structured_tooltip.py::_cartesian_roles`/`_stack_group_total` for bar's own
//! modeled shapes: single-series, grouped, and stacked (zero/normalize), vertical or horizontal. Wide
//! (`chart.wide_measures`) and `chart.layers` combo charts are out of scope for this pass -- their
//! base marks paint with `aria_label: None`, never an approximate label.

use crate::common::FormatSlot;
use crate::families::legend::LegendConfig;
use crate::families::temporal::{AuthoredTimeFormat, TimeUnit};

use std::collections::HashMap;

use super::legend::grouped_legend_domain;

/// The color scale's own display-order domain for the ORDER role, or
/// `None` when this shape doesn't rank (`emitters/bar.py`'s two branches):
/// a grouped bar ranks on `grouped_legend_domain` ONLY when a board-planned
/// category-color scale binds the field (`emitters/bar.py:1066-1102`'s
/// `category_scale_for` gate) -- `category_scale` is the SAME lookup the
/// fill color already uses, never a second derivation.
///
/// A stacked bar always ranks, but on WHICH domain is orientation-specific
/// and not a blanket reversal: `_emit_vertical` (bar.py:1125) sets
/// `display_order = list(reversed(order))` and bakes THAT into
/// `scale["domain"]` (`spatial_color_scale`'s third argument) --
/// `_series_order_role` (structured_tooltip.py:126-136) ranks over
/// `scale["domain"]`, so a vertical stacked bar ranks over the REVERSED
/// baseline-first `stack_order`. `_emit_horizontal` (bar.py:1522-1524)
/// bakes `order` itself with no reversal ("the 'left-first' convention IS
/// baseline-first, no reversal needed") -- `reverse_for_vertical` is that
/// one orientation switch, set by each render fn's own caller.
pub(super) fn tooltip_order_domain(
    is_stacked: bool,
    is_grouped: bool,
    reverse_for_vertical: bool,
    stack_order: &[String],
    color_domain: &[String],
    legend: &LegendConfig,
    category_scale: Option<&crate::common::CategoryColorScale>,
) -> Option<Vec<String>> {
    if is_stacked {
        let mut domain = stack_order.to_vec();
        if reverse_for_vertical {
            domain.reverse();
        }
        Some(domain)
    } else if is_grouped && category_scale.is_some() {
        Some(grouped_legend_domain(color_domain, legend, &HashMap::new()))
    } else {
        None
    }
}

/// The stacked group-total footer (`("Σ", sum)`) for one category --
/// `_stack_group_total`'s footer always attaches once the stack/negative
/// gate passes (`emitters/bar.py`), so a category present in `cat_domain`
/// with no entry in `category_totals` is this port's own invariant broken,
/// never a silent partial: every painted segment came from a row that also
/// contributed to `category_totals`, so a miss here means the two disagree
/// on which rows exist.
pub(super) fn stack_group_total(
    category_totals: &HashMap<String, f64>,
    cat: &str,
) -> Result<(&'static str, f64), String> {
    let t = category_totals.get(cat).copied().ok_or_else(|| {
        format!("bar: internal error -- no stacked group total computed for category {cat:?}")
    })?;
    Ok(("Σ", t))
}

/// The channel titles of Vega-Lite's own description, which a facet panel's
/// marks carry in place of the structured tooltip (`StructuredTooltipFeature`
/// does not apply under `multiples`).
pub(super) struct NativeTitles {
    pub(super) x: String,
    pub(super) series: String,
}

/// One datum's structured-tooltip aria-label, or `None` when
/// `tooltip_labels` is false -- the shared body every one of bar's six
/// paint-loop arms (grouped/stacked/single-series, vertical/horizontal)
/// calls with its own `series`/`order`/`percent`/`total`, so the role
/// assembly is written once, not duplicated six times.
#[allow(clippy::too_many_arguments)]
pub(super) fn bar_datum_aria(
    tooltip_labels: bool,
    cat: &str,
    detected_tu: Option<TimeUnit>,
    is_standard_date: bool,
    grain_format: Option<AuthoredTimeFormat>,
    series: Option<&str>,
    order: Option<i64>,
    value_title: &str,
    value_format: &FormatSlot,
    value: f64,
    percent: Option<f64>,
    total: Option<(&'static str, f64)>,
    native: Option<&NativeTitles>,
) -> Result<Option<String>, String> {
    if !tooltip_labels {
        return Ok(None);
    }
    if let Some(titles) = native {
        let mut rows = vec![(titles.x.as_str(), cat.to_string()), (value_title, value_format.paint(value)?)];
        rows.extend(series.map(|s| (titles.series.as_str(), s.to_string())));
        return Ok(Some(crate::contract::native_label(&rows)));
    }
    // `is_standard_date`: line/area's own STRICT `all_temporal` gate
    // (`x_values_are_standard_dates`) -- a field this strict types
    // "temporal" even with no detected bucketed grain, so its header still
    // formats through the continuous fallback rather than the raw ISO
    // string. Both orientations derive this from their own domain: it's a
    // STRICTLY broader predicate than `render_horizontal`'s own refusal
    // gate (`detect_time_unit(...).is_some()`, a DETECTED calendar bucket),
    // so a standard-date x with no detected bucket reaches this call
    // without having refused above.
    let header = crate::contract::tooltip_header_text(cat, detected_tu, is_standard_date, grain_format)?;
    let tt = crate::contract::BarTooltip {
        header: &header,
        series,
        order,
        value_title,
        value_format,
        percent,
        total,
    };
    Ok(Some(crate::contract::bar_aria_label(&tt, value)?))
}

/// The description of a (category, series) pair a sparse stack lacks a row for:
/// Vega imputes it as a null datum, which the hover band still draws a bar for.
#[allow(clippy::too_many_arguments)]
pub(super) fn imputed_datum_aria(
    tooltip_labels: bool,
    cat: &str,
    detected_tu: Option<TimeUnit>,
    is_standard_date: bool,
    grain_format: Option<AuthoredTimeFormat>,
    series: &str,
    order: Option<i64>,
    value_title: &str,
    value_format: &FormatSlot,
) -> Result<Option<String>, String> {
    if !tooltip_labels {
        return Ok(None);
    }
    let header = crate::contract::tooltip_header_text(cat, detected_tu, is_standard_date, grain_format)?;
    let tt = crate::contract::BarTooltip {
        header: &header,
        series: Some(series),
        order,
        value_title,
        value_format,
        percent: None,
        total: None,
    };
    Ok(Some(crate::contract::imputed_aria_label(&tt)?))
}

#[cfg(test)]
mod tooltip_order_domain_tests {
    use super::*;

    /// `charts/bar-charts_7/region_revenue_by_category_vertical` (vertical,
    /// stack: zero): `stack_order` is the descending-global-sum baseline
    /// order `[Electronics(197199), Accessories(152504), Tools(93320)]`;
    /// the reference ranks `Tools`=0, `Accessories`=1, `Electronics`=2 --
    /// the REVERSED domain.
    #[test]
    fn vertical_stacked_bar_ranks_over_the_reversed_domain() {
        let stack_order =
            vec!["Electronics".to_string(), "Accessories".to_string(), "Tools".to_string()];
        let legend: LegendConfig = serde_json::from_value(serde_json::json!({
            "visible": true, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}}, "title": {"font": {}},
        }))
        .unwrap();
        let domain =
            tooltip_order_domain(true, true, true, &stack_order, &[], &legend, None).unwrap();
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Electronics"), 2);
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Accessories"), 1);
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Tools"), 0);
    }

    /// `charts/bar-charts_8/region_revenue_by_category` (horizontal, stack:
    /// zero): same `stack_order`, but the reference ranks `Electronics`=0,
    /// `Tools`=2 -- the UNREVERSED domain ("left-first IS baseline-first").
    #[test]
    fn horizontal_stacked_bar_ranks_over_the_unreversed_domain() {
        let stack_order =
            vec!["Electronics".to_string(), "Accessories".to_string(), "Tools".to_string()];
        let legend: LegendConfig = serde_json::from_value(serde_json::json!({
            "visible": true, "position": {"edge": "right", "align": "start", "overlay": false}, "direction": "vertical",
            "label": {"font": {"size": 11.0}}, "title": {"font": {}},
        }))
        .unwrap();
        let domain =
            tooltip_order_domain(true, true, false, &stack_order, &[], &legend, None).unwrap();
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Electronics"), 0);
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Accessories"), 1);
        assert_eq!(crate::contract::tooltip_order_rank(&domain, "Tools"), 2);
    }
}

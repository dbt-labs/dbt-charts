//! Small multiples (`chart.multiples` / facet) — the per-panel pixel budget and facet diagnostics shared by
//! every faceting family. The partition data plane (`regroup`, `canonical_key`, `Panel`) lives in
//! `bind::multiples`; `gridLayout`/`layoutHeaders` live in the sibling `facet_layout` module. Ports
//! `compile/resolve/chart/adaptive_stroke.py`'s `facet_panel_width`.

use crate::constants::estimates::{FACET_CHROME_PX, FACET_MIRROR_AXIS_PX};
use crate::constants::layout::FACET_MIN_PANEL_PX;
use serde_json::Value;

/// Per-panel pixel width for a small-multiples chart —
/// `adaptive_stroke.py::facet_panel_width`. `chrome_px`/`mirror_axis_px` are
/// `FACET_CHROME_PX` / `FACET_MIRROR_AXIS_PX`.
pub fn facet_panel_width(
    width: f64,
    panel_cols: usize,
    has_mirror: bool,
    extra_axis_px: f64,
) -> f64 {
    let chrome = FACET_CHROME_PX + if has_mirror { FACET_MIRROR_AXIS_PX } else { 0.0 };
    let usable = (width - chrome).max(0.0);
    (usable / panel_cols as f64 - extra_axis_px).max(0.0)
}

/// `(extent, panels)` for the x title (the panel width) and for the rotated y title (the card
/// height divided across the rows): what an axis title wraps against in a facet chart.
pub type FacetTitleBoxes = ((f64, f64), (f64, f64));

/// The [`FacetTitleBoxes`] of a facet chart (`RenderBox`).
pub fn facet_title_boxes(
    (width, height): (f64, f64),
    (panel_cols, panel_rows): (usize, usize),
    has_mirror: bool,
    extra_axis_px: f64,
) -> FacetTitleBoxes {
    (
        (facet_panel_width(width, panel_cols, has_mirror, extra_axis_px), panel_cols as f64),
        (height / panel_rows as f64, panel_rows as f64),
    )
}

/// The width each panel draws at: [`facet_panel_width`] while it is positive.
/// A zero width, like no width at all, reads as unset in the spec
/// (`_apply_facet_layout` stamps `unit["width"]` for `width > 0`, and Vega-Lite
/// treats a falsy one as absent), which leaves each panel the theme's default
/// view width for its x scale.
pub fn panel_width(
    payload: &crate::Payload,
    panel_cols: usize,
    has_mirror: bool,
    extra_axis_px: f64,
    x_scale: crate::ViewScale,
) -> Result<f64, String> {
    let declared = facet_panel_width(payload.width, panel_cols, has_mirror, extra_axis_px);
    if declared > 0.0 {
        return Ok(declared);
    }
    crate::view_width(0.0, payload.board_style.as_ref(), x_scale)
}

/// `extra_axis_is_affordable`: narrowing a position channel draws one more axis
/// per column panel, which is worth it only while that leaves a legible panel
/// (`FACET_MIN_PANEL_PX`). No extra axis is always affordable.
pub fn extra_axis_is_affordable(unnarrowed_panel_width: f64, extra_axis_px: f64) -> bool {
    extra_axis_px <= 0.0 || unnarrowed_panel_width - extra_axis_px >= FACET_MIN_PANEL_PX
}

/// Production resolve rejects a both-edge mirror under an independent scale
/// (`_axes.py::_apply_multiples_mirror`): each panel owns its scale, so
/// mirroring a shared one to both edges is meaningless.
pub fn independent_mirror_error(chart_id: &str) -> diagnostic::Diagnostic {
    diagnostic::Diagnostic::new(
        &diagnostic::codes::ERR_MULTIPLES_INDEPENDENT_SCALE_MIRROR,
        [("chart_id", Value::from(chart_id))],
    )
}

/// `WARN-FACET-PANEL-WIDTH-BELOW-MINIMUM` (`facet_panel_width_below_minimum.py`):
/// production judges the `facet_panel_width` its spec gives each panel, before
/// any composite width correction.
pub fn warn_panel_below_minimum(chart_id: &str, panel_cols: usize, panel_width: f64) {
    if panel_width < FACET_MIN_PANEL_PX {
        diagnostic::warn(diagnostic::Diagnostic::new(
            &diagnostic::codes::WARN_FACET_PANEL_WIDTH_BELOW_MINIMUM,
            [
                ("chart_id", Value::from(chart_id)),
                ("min_panel_px", Value::from(FACET_MIN_PANEL_PX)),
                ("panel_cols", Value::from(panel_cols)),
                ("panel_width", Value::from(panel_width)),
            ],
        ));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The x title wraps against a panel's width over the columns, the y title against the card
    /// height over the rows.
    #[test]
    fn facet_title_boxes_divide_the_card_across_the_grid() {
        let ((x_extent, x_panels), (y_extent, y_panels)) = facet_title_boxes((900.0, 400.0), (3, 2), false, 0.0);
        assert_eq!((x_panels, y_panels, y_extent), (3.0, 2.0, 200.0));
        assert_eq!(x_extent, facet_panel_width(900.0, 3, false, 0.0));
    }

    #[test]
    fn facet_panel_width_matches_documented_formula() {
        // chrome_px=120, mirror_axis_px=80
        assert_eq!(facet_panel_width(600.0, 1, false, 0.0), 480.0);
        assert_eq!(facet_panel_width(600.0, 2, false, 0.0), 240.0);
        assert_eq!(facet_panel_width(600.0, 1, true, 0.0), 400.0);
        assert_eq!(facet_panel_width(600.0, 2, false, 30.0), 210.0);
    }
}

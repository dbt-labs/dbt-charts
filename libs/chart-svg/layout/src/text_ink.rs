//! Where a single line of text's ink box sits against its anchor, per vega-scenegraph
//! `marks/text.js bound()`: the box top sits at `baseline_shift - round(0.8 * fs)` and spans one
//! `fs`, and horizontally it spans the text's width from the anchor per `align`. The law is
//! `chart-renderer/src/vega_fit.rs::text_ink`'s.

use measure::baseline_shift;

/// How far the box's top sits from the anchor's y.
pub fn rise(font_size: f64, baseline: &str) -> f64 {
    baseline_shift(baseline, font_size) - (0.8 * font_size).round()
}

/// How far the box's left edge sits from the anchor's x, for text `width` wide.
pub fn left(width: f64, align: &str) -> f64 {
    match align {
        "center" => -width / 2.0,
        "right" => -width,
        _ => 0.0,
    }
}

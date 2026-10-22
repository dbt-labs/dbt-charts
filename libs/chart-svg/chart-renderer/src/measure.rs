//! Text metrics layout reads. Only `layout` imports this module; `print` never
//! does.

/// `offset(item)` from vega-scenegraph `util/text.js` — the font-baseline
/// shift Vega applies to every text item instead of SVG's
/// `alignment-baseline`.
pub fn baseline_shift(baseline: &str, font_size: f64) -> f64 {
    (match baseline {
        "top" => 0.79 * font_size,
        "middle" => 0.30 * font_size,
        "bottom" => -0.21 * font_size,
        _ => 0.0, // alphabetic
    })
    .round()
}

#[cfg(test)]
mod tests {
    use super::baseline_shift;

    #[test]
    fn baseline_shift_matches_vega_rounding() {
        assert_eq!(baseline_shift("top", 11.0), 9.0); // round(8.69)
        assert_eq!(baseline_shift("top", 18.0), 14.0); // round(14.22)
        assert_eq!(baseline_shift("middle", 11.0), 3.0); // round(3.3)
        assert_eq!(baseline_shift("bottom", 11.0), -2.0); // round(-2.31)
        assert_eq!(baseline_shift("alphabetic", 11.0), 0.0);
    }
}

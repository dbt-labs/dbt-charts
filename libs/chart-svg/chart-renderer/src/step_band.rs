//! A line's authored `stroke.dasharray` as production paints it, and the fit
//! a disconnected band-step plateau applies to it. Production reads the string
//! through `_dasharray_to_vl` (`vl_field_maps.py:132`) and, on a disconnected
//! step band, rewrites it with `fit_dash_to_plateau` (`step_band.py:80`).

use d3rs::js_math::js_round;

/// Where a band-step row's two vertices sit across its band: the whole band,
/// or — `connect: false` — each plateau's inset share of it
/// (`step_band.py`'s `offset_range`).
pub(crate) fn band_step_edges(plateau_share: f64) -> (f64, f64) {
    let inset = (1.0 - plateau_share) / 2.0;
    (inset, 1.0 - inset)
}

/// The authored list: whitespace-separated lengths, as `_dasharray_to_vl`
/// splits it. A list SVG cannot paint (empty, negative, non-finite) refuses.
pub(crate) fn parse(family: &str, raw: &str) -> Result<Vec<f64>, String> {
    let unreadable = || {
        format!(
            "{family}: line_mark.stroke.dasharray {raw:?} is not a list of non-negative \
             finite lengths"
        )
    };
    let lengths = raw
        .split_whitespace()
        .map(|part| part.parse::<f64>().map_err(|_| unreadable()))
        .collect::<Result<Vec<f64>, String>>()?;
    if lengths.is_empty() || lengths.iter().any(|v| !v.is_finite() || *v < 0.0) {
        return Err(unreadable());
    }
    Ok(lengths)
}

/// `fit_dash_to_plateau`: the dashes rescaled so a plateau `span` pixels wide
/// (`bandwidth * line_mark.disconnected_width`) holds `n` periods less the trailing gap — ink
/// on both its edges. An odd list repeats first (SVG repeats it, so its last
/// entry is a gap); a list with no ink is returned as repeated. Production
/// emits `d*span/(n*period-last)` as one Vega expression, which evaluates left
/// to right: `(d*span)/(n*period-last)`.
pub(crate) fn fit_to_plateau(dashes: &[f64], span: f64) -> Vec<f64> {
    let repeated: Vec<f64> = if dashes.len() % 2 == 1 { [dashes, dashes].concat() } else { dashes.to_vec() };
    if repeated.iter().step_by(2).sum::<f64>() == 0.0 {
        return repeated;
    }
    let period: f64 = repeated.iter().sum();
    let last = repeated[repeated.len() - 1];
    let periods = js_round((span + last) / period).max(1.0);
    let scaled_period = periods * period - last;
    repeated.iter().map(|d| d * span / scaled_period).collect()
}

/// The `stroke-dasharray` attribute value Vega's SVG renderer writes: each
/// length as a JavaScript number, comma-joined. JavaScript spells a nonzero
/// magnitude below 1e-6 or from 1e21 in exponent notation, which this does
/// not model, and spells negative zero `0`.
pub(crate) fn svg_value(family: &str, dashes: &[f64]) -> Result<String, String> {
    if let Some(v) = dashes.iter().find(|v| **v != 0.0 && (**v < 1e-6 || **v >= 1e21)) {
        return Err(format!(
            "{family}: line_mark.stroke.dasharray gives a dash length of {v}, outside the \
             range JavaScript prints without an exponent, which is not supported"
        ));
    }
    Ok(dashes.iter().map(|v| (v + 0.0).to_string()).collect::<Vec<_>>().join(","))
}

/// The ` stroke-dasharray="..."` attribute of a foreground line, fitted to the
/// plateau when `plateau_span` is the pixel width of a disconnected step's plateau.
pub(crate) fn attr(
    family: &str,
    dashes: &[f64],
    plateau_span: Option<f64>,
) -> Result<String, String> {
    let fitted;
    let dashes = match plateau_span {
        Some(span) => {
            fitted = fit_to_plateau(dashes, span);
            &fitted
        }
        None => dashes,
    };
    Ok(format!(" stroke-dasharray=\"{}\"", svg_value(family, dashes)?))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Production's expression `d*span/(n*period-last)`, spelled out, over the
    /// pace-overlay case: a 5-on 4-off dash on a 122.35-wide band.
    #[test]
    fn a_plateau_holds_n_dashes_and_n_less_one_gaps() {
        let bandwidth = 122.352_941_176_470_59;
        let span = bandwidth * 0.9;
        let fitted = fit_to_plateau(&[5.0, 4.0], span);
        let n = 13.0; // round((span + 4) / 9) = round(12.68)
        assert_eq!(fitted, [5.0 * span / (n * 9.0 - 4.0), 4.0 * span / (n * 9.0 - 4.0)]);
        assert!((n * (fitted[0] + fitted[1]) - fitted[1] - span).abs() < 1e-9);
    }

    #[test]
    fn a_plateau_shorter_than_one_period_still_holds_one_dash() {
        let fitted = fit_to_plateau(&[5.0, 4.0], 1.8);
        assert!((fitted[0] - 5.0 * 1.8 / (9.0 - 4.0)).abs() < 1e-12, "{fitted:?}");
    }

    #[test]
    fn an_odd_list_repeats_and_a_zero_ink_list_stays_as_repeated() {
        assert_eq!(fit_to_plateau(&[6.0, 2.0, 1.0], 90.0).len(), 6);
        assert_eq!(fit_to_plateau(&[0.0, 8.0], 90.0), [0.0, 8.0]);
        assert_eq!(fit_to_plateau(&[0.0, 8.0, 0.0], 90.0).len(), 6);
    }

    #[test]
    fn svg_value_prints_what_javascript_prints() {
        assert_eq!(svg_value("line", &[5.0, 4.5, 0.0]).unwrap(), "5,4.5,0");
        assert!(svg_value("line", &[1e-7]).unwrap_err().contains("exponent"));
    }

    #[test]
    fn svg_value_names_the_field_and_prints_negative_zero_as_zero() {
        let err = svg_value("line", &[1e-7]).unwrap_err();
        assert!(err.contains("line_mark.stroke.dasharray"), "{err}");
        assert_eq!(svg_value("line", &[-0.0, 5.0]).unwrap(), "0,5");
        assert_eq!(svg_value("line", &[1e-6, 1e20]).unwrap(), "0.000001,100000000000000000000");
    }

    /// `d * span / den` is `(d * span) / den`: on this band `5 * (span / den)`
    /// lands one ulp lower (5.061085017313571), and Vega prints the digits.
    #[test]
    fn a_dash_is_scaled_as_d_times_span_then_divided() {
        let bandwidth = 129.338_839_331_346_83;
        let span = bandwidth * 0.9;
        let fitted = fit_to_plateau(&[5.0, 6.0], span);
        let den = 11.0 * 11.0 - 6.0;
        assert_ne!(5.0 * span / den, 5.0 * (span / den));
        assert_eq!(fitted[0], 5.061_085_017_313_572);
        assert_eq!(fitted[1], 6.0 * span / den);
    }

    /// A short trailing gap lets `round((span + last) / period)` reach 0; the
    /// plateau still holds one dash.
    #[test]
    fn a_plateau_below_half_a_period_rounds_up_to_one_dash() {
        let fitted = fit_to_plateau(&[5.0, 1.0], 0.9);
        assert_eq!(fitted, [5.0 * 0.9 / 5.0, 1.0 * 0.9 / 5.0]);
    }
}

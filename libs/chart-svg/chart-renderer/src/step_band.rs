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

/// Python's `float(token)`: ASCII digits with single underscores between digits.
fn python_float(token: &str) -> Option<f64> {
    let bytes = token.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'_' && !(i > 0 && bytes[i - 1].is_ascii_digit() && bytes.get(i + 1).is_some_and(u8::is_ascii_digit)) {
            return None;
        }
    }
    token.replace('_', "").parse().ok()
}

/// The authored list: whitespace-separated Python floats, as `_dasharray_to_vl`
/// reads it. A token Python cannot read raises there, as does a plateau fit
/// (`fit`) whose expression production cannot compile: a non-finite length or
/// period (`inf`, `nan` print into it as unknown signals), or a negative last
/// entry (it prints `--3.0`, a decrement).
pub(crate) fn parse(family: &str, raw: &str, fit: bool) -> Result<Vec<f64>, String> {
    let unreadable = || {
        crate::refuse!(
            "{family}: line_mark.stroke.dasharray {raw:?}, not a list of non-negative \
             finite lengths, is not supported"
        )
    };
    let lengths = raw
        .split_whitespace()
        .map(|part| python_float(part).ok_or_else(unreadable))
        .collect::<Result<Vec<f64>, String>>()?;
    if fit && !lengths.is_empty() {
        let repeated: Vec<f64> = if lengths.len() % 2 == 1 { [&lengths[..], &lengths[..]].concat() } else { lengths.clone() };
        let inked = repeated.iter().step_by(2).sum::<f64>() != 0.0;
        let last = repeated[repeated.len() - 1];
        if inked && (!repeated.iter().sum::<f64>().is_finite() || repeated.iter().any(|v| !v.is_finite()) || last.is_sign_negative()) {
            return Err(unreadable());
        }
    }
    Ok(lengths)
}

/// `fit_dash_to_plateau`: the dashes rescaled so a plateau `span` pixels wide
/// (`bandwidth * line_mark.disconnected_width`) holds `n` periods less the trailing gap — ink
/// on both its edges. An odd list repeats first (SVG repeats it, so its last
/// entry is a gap); a list with no ink is returned as repeated. Production
/// emits `d*span/(n*period-last)` as one Vega expression, which evaluates left
/// to right: `(d*span)/(n*period-last)`, in JavaScript's arithmetic (`max`
/// and `round` pass a NaN on).
pub(crate) fn fit_to_plateau(dashes: &[f64], span: f64) -> Vec<f64> {
    let repeated: Vec<f64> = if dashes.len() % 2 == 1 { [dashes, dashes].concat() } else { dashes.to_vec() };
    if repeated.iter().step_by(2).sum::<f64>() == 0.0 {
        return repeated;
    }
    let period: f64 = repeated.iter().sum();
    let last = repeated[repeated.len() - 1];
    let rounded = js_round((span + last) / period);
    let periods = if rounded.is_nan() { rounded } else { rounded.max(1.0) };
    let scaled_period = periods * period - last;
    repeated.iter().map(|d| d * span / scaled_period).collect()
}

/// A dash length as JavaScript prints it: `String(number)`, with `NaN` and
/// `Infinity` spelled out and negative zero as `0`.
fn js_length(v: f64) -> String {
    if v.is_nan() {
        "NaN".to_string()
    } else if v.is_infinite() {
        if v > 0.0 { "Infinity" } else { "-Infinity" }.to_string()
    } else {
        crate::families::color_scale::js_number_string(v + 0.0)
    }
}

/// The `stroke-dasharray` attribute value Vega's SVG renderer writes: each
/// length as a JavaScript number, comma-joined. A length that never reached
/// the expression (an authored non-finite one) crossed JSON as `null` and
/// prints empty.
pub(crate) fn svg_value(dashes: &[f64], fitted: bool) -> String {
    dashes
        .iter()
        .map(|v| if v.is_finite() || fitted { js_length(*v) } else { String::new() })
        .collect::<Vec<_>>()
        .join(",")
}

/// The ` stroke-dasharray="..."` attribute of a foreground line, fitted to the
/// plateau when `plateau_span` is the pixel width of a disconnected step's plateau.
pub(crate) fn attr(dashes: &[f64], plateau_span: Option<f64>) -> String {
    // A list with no ink is not rescaled, so its authored lengths cross JSON as they are.
    let computed = plateau_span.is_some() && dashes.iter().step_by(2).sum::<f64>() != 0.0;
    let value = match plateau_span {
        Some(span) => svg_value(&fit_to_plateau(dashes, span), computed),
        None => svg_value(dashes, false),
    };
    format!(" stroke-dasharray=\"{value}\"")
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
        assert_eq!(svg_value(&[5.0, 4.5, 0.0], false), "5,4.5,0");
        assert_eq!(svg_value(&[-0.0, 5.0], false), "0,5");
        assert_eq!(svg_value(&[1e-7, 1e21], false), "1e-7,1e+21");
        assert_eq!(svg_value(&[1e-6, 1e20], false), "0.000001,100000000000000000000");
        assert_eq!(svg_value(&[5.0, f64::INFINITY, f64::NAN], false), "5,,");
        assert_eq!(svg_value(&[f64::NAN, f64::NEG_INFINITY], true), "NaN,-Infinity");
    }

    #[test]
    fn python_floats_read_underscores_between_digits_only() {
        assert_eq!(python_float("1_0"), Some(10.0));
        assert_eq!(python_float("_1"), None);
        assert_eq!(python_float("1__0"), None);
        assert_eq!(python_float("1_.5"), None);
        assert_eq!(python_float("-inf"), Some(f64::NEG_INFINITY));
        assert_eq!(python_float("abc"), None);
    }

    /// The fit's expression embeds the last entry and the period as text, so a
    /// negative last entry (`+-3.0`) or a non-finite one fails to compile.
    #[test]
    fn a_plateau_fit_production_cannot_compile_refuses() {
        for raw in ["-3", "1 -1", "5 inf", "nan 1", "1e308 1e308"] {
            assert!(parse("line", raw, true).is_err(), "{raw}");
            assert!(parse("line", raw, false).is_ok() || raw.contains("inf") || raw.contains("nan"), "{raw}");
        }
        for raw in ["", "-1 1", "-5 1", "0 -2", "0 inf"] {
            assert!(parse("line", raw, true).is_ok(), "{raw}");
        }
        assert_eq!(fit_to_plateau(&[-1.0, 1.0], 90.0).iter().map(|v| v.is_nan()).collect::<Vec<_>>(), [true, true]);
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

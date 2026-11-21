//! The one law for a dashed stroke's pattern: stretched so the stroke starts and ends on ink.

use d3rs::js_math::js_round;

/// `dashes` rescaled to fit a stroke `length` px long. An odd list repeats first (SVG
/// repeats it, so its last entry is a gap); a list with no ink is returned as repeated.
///
/// An open stroke holds `n` periods less the trailing gap, so ink sits on both ends. A
/// `closed` outline holds exactly `n` periods, so the pattern closes the loop with no seam.
/// `n` is the period count nearest the authored pattern, at least one. The scale is
/// `d*length/(n*period-tail)`, in JavaScript's arithmetic (`round` passes a NaN on),
/// because a disconnected step-band plateau's fit is production's Vega expression.
pub fn fit(dashes: &[f64], length: f64, closed: bool) -> Vec<f64> {
    let repeated: Vec<f64> = if dashes.len() % 2 == 1 { [dashes, dashes].concat() } else { dashes.to_vec() };
    if repeated.iter().step_by(2).sum::<f64>() == 0.0 {
        return repeated;
    }
    let period: f64 = repeated.iter().sum();
    let tail = if closed { 0.0 } else { repeated[repeated.len() - 1] };
    let rounded = js_round((length + tail) / period);
    let periods = if rounded.is_nan() { rounded } else { rounded.max(1.0) };
    let scaled_period = periods * period - tail;
    repeated.iter().map(|d| d * length / scaled_period).collect()
}

/// [`fit`] for an authored stroke: `dashes` stretched to a stroke `length` px long, or as they are
/// when the stroke has no length or the pattern no ink.
pub fn fit_list(dashes: &[f64], length: f64, closed: bool) -> Vec<f64> {
    if length <= 0.0 || dashes.iter().step_by(2).sum::<f64>() == 0.0 {
        return dashes.to_vec();
    }
    fit(dashes, length, closed)
}

/// [`fit_list`] over an engine `stroke-dasharray` (comma- or space-joined numbers), printed as
/// JavaScript numbers, comma-joined. A list Vega writes as it is (empty, a non-finite length printed
/// as nothing, a negative length) has no pattern to stretch and stays as is.
pub fn fit_pattern(pattern: &str, length: f64, closed: bool) -> String {
    let lengths: Vec<f64> = pattern.split([',', ' ']).filter(|n| !n.is_empty()).map(|n| n.parse().expect("an engine dasharray is numbers")).collect();
    let unfittable = pattern.is_empty() || pattern.split(',').any(str::is_empty) || lengths.iter().any(|v| !v.is_finite() || *v < 0.0);
    if unfittable || length <= 0.0 || lengths.iter().step_by(2).sum::<f64>() == 0.0 {
        return pattern.to_string();
    }
    fit(&lengths, length, closed).iter().map(|v| d3rs::js_math::js_number_string(v + 0.0)).collect::<Vec<_>>().join(",")
}

/// The outline length of a `w` by `h` rectangle with corner radius `rx` (clamped to half the
/// shorter side, as SVG draws it).
pub fn rect_perimeter(w: f64, h: f64, rx: f64) -> f64 {
    let r = rx.min(w / 2.0).min(h / 2.0).max(0.0);
    2.0 * (w + h) - (8.0 - std::f64::consts::TAU) * r
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_open_stroke_holds_n_dashes_and_n_less_one_gaps() {
        let fitted = fit(&[6.0, 3.0], 100.0, false);
        let n = 11.0; // round((100 + 3) / 9)
        assert!((n * (fitted[0] + fitted[1]) - fitted[1] - 100.0).abs() < 1e-9, "{fitted:?}");
    }

    #[test]
    fn a_closed_outline_holds_n_whole_periods() {
        let fitted = fit(&[6.0, 3.0], 100.0, true);
        let n = 11.0; // round(100 / 9)
        assert!((n * (fitted[0] + fitted[1]) - 100.0).abs() < 1e-9, "{fitted:?}");
    }

    #[test]
    fn an_odd_list_repeats_and_a_list_without_ink_is_left_alone() {
        assert_eq!(fit(&[4.0], 40.0, true), [4.0, 4.0]);
        assert_eq!(fit(&[0.0, 5.0], 50.0, false), [0.0, 5.0]);
    }

    #[test]
    fn a_rounded_rect_swaps_each_corner_for_a_quarter_arc() {
        assert_eq!(rect_perimeter(20.0, 10.0, 0.0), 60.0);
        assert!((rect_perimeter(20.0, 10.0, 2.0) - (60.0 - 8.0 * 2.0 + std::f64::consts::TAU * 2.0)).abs() < 1e-12);
        assert!((rect_perimeter(10.0, 10.0, 99.0) - 10.0 * std::f64::consts::PI).abs() < 1e-12);
    }

    #[test]
    fn a_stroke_shorter_than_one_period_still_holds_one_dash() {
        let fitted = fit(&[5.0, 4.0], 1.8, false);
        assert!((fitted[0] - 5.0 * 1.8 / 5.0).abs() < 1e-12, "{fitted:?}");
    }
}

//! JavaScript numeric semantics the d3 and Vega ports depend on.

/// JavaScript truthiness of a number: 0 and NaN are falsy.
pub fn js_truthy(x: f64) -> bool {
    x != 0.0 && !x.is_nan()
}

/// JavaScript's `Math.min` of two numbers: NaN wins (`f64::min` ignores it).
pub fn js_min(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.min(b)
    }
}

/// JavaScript's `Math.max` of two numbers: NaN wins (`f64::max` ignores it).
pub fn js_max(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.max(b)
    }
}

/// JavaScript's `Math.round`: ties go toward +infinity (`f64::round` sends
/// them away from zero). `x - floor(x)` is exact, so no `x + 0.5` rounding.
pub fn js_round(x: f64) -> f64 {
    let floor = x.floor();
    if x - floor >= 0.5 {
        floor + 1.0
    } else {
        floor
    }
}

/// V8's `Math.hypot`: scaled by the largest magnitude and summed with Kahan compensation.
pub fn hypot(values: &[f64]) -> f64 {
    let nums: Vec<f64> = values.iter().map(|v| v.abs()).collect();
    if nums.is_empty() {
        return 0.0;
    }
    let max = nums.iter().copied().fold(0.0_f64, |m, x| if x > m || x.is_infinite() { x } else { m });
    if max.is_infinite() {
        return f64::INFINITY;
    }
    if nums.iter().any(|x| x.is_nan()) {
        return f64::NAN;
    }
    if max == 0.0 {
        return 0.0;
    }
    let (mut sum, mut compensation) = (0.0_f64, 0.0_f64);
    for x in nums {
        let n = x / max;
        let summand = n * n - compensation;
        let preliminary = sum + summand;
        compensation = (preliminary - sum) - summand;
        sum = preliminary;
    }
    sum.sqrt() * max
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn js_round_sends_ties_up_and_never_adds_half() {
        assert_eq!(js_round(2.5), 3.0);
        assert_eq!(js_round(-2.5), -2.0);
        assert_eq!(js_round(0.499_999_999_999_999_94), 0.0);
        assert_eq!(js_round(12.68), 13.0);
    }

    #[test]
    fn js_min_lets_nan_win() {
        assert_eq!(js_min(1.0, 2.0), 1.0);
        assert!(js_min(f64::NAN, 2.0).is_nan());
        assert!(js_min(1.0, f64::NAN).is_nan());
    }

    #[test]
    fn js_max_lets_nan_win() {
        assert_eq!(js_max(1.0, 2.0), 2.0);
        assert!(js_max(f64::NAN, 2.0).is_nan());
        assert!(js_max(1.0, f64::NAN).is_nan());
    }
}

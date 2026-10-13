//! JavaScript numeric semantics the d3 and Vega ports depend on.

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
}

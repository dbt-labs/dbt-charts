//! The smart-zero verdict: whether a value axis includes zero. One verdict feeds the
//! ladder, the floor and the baseline rule.

use crate::bound::Zero;

/// Data at or below this ratio of near edge to far edge starts "close to zero".
const ZERO_EXTEND_THRESHOLD: f64 = 0.25;

/// `scale.continuous.zero` as authored; absent is `None` at the call. `Auto` is authored
/// but not a bool: a measure axis still runs the smart-zero law, but the readers that
/// test "was it set" (the bar `y_start` branch, the scatter x bake) skip their carve-out.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Setting {
    Auto,
    Value(bool),
}

impl Setting {
    fn pin(self) -> Option<bool> {
        match self {
            Setting::Auto => None,
            Setting::Value(value) => Some(value),
        }
    }
}

/// The families that differ in their zero carve-outs; each flag is a fact of the chart.
#[derive(Debug, Clone, Copy)]
pub enum Family {
    /// A date span never anchors; `y_start` anchors only when the bars straddle zero.
    Bar { stacked: bool, y_start: bool, date_span: bool },
    Area { center: bool, stacked: bool, independent: bool, log: bool },
    Line,
    Scatter,
}

/// `values` are every number drawn against the axis (shared-scale union). A `Pinned`
/// verdict is the author's; otherwise the family's law decides.
pub fn verdict(family: Family, setting: Option<Setting>, values: &[f64]) -> Zero {
    let pin = setting.and_then(Setting::pin);
    let extent = extent(values);
    match family {
        Family::Bar { stacked, y_start, date_span } => {
            if date_span {
                return Zero::Free;
            }
            if let Some(pin) = pin {
                return Zero::Pinned(pin);
            }
            match extent {
                Some((lo, hi)) if y_start && setting.is_none() => anchored(lo <= 0.0 && 0.0 <= hi),
                _ => anchored(!stacked),
            }
        }
        Family::Area { center, stacked, independent, log } => {
            let all_negative = extent.is_some_and(|(_, hi)| hi < 0.0);
            if center || log || (all_negative && (stacked || independent)) {
                return Zero::Free;
            }
            match extent {
                None => Zero::Anchored,
                Some(extent) => smart(pin, extent, false),
            }
        }
        Family::Line => extent.map_or(Zero::Anchored, |extent| smart(pin, extent, true)),
        Family::Scatter => extent.map_or(Zero::Free, |extent| smart(pin, extent, true)),
    }
}

/// A scatter x: a percent format anchors, an authored domain, log scale or `auto` zero is
/// left unbaked (`Free`), any other column takes the y law of a line.
pub fn scatter_x(setting: Option<Setting>, domain_or_log: bool, percent_format: bool, values: &[f64]) -> Zero {
    if let Some(pin) = setting.and_then(Setting::pin) {
        return Zero::Pinned(pin);
    }
    if domain_or_log || setting == Some(Setting::Auto) {
        return Zero::Free;
    }
    if percent_format {
        return Zero::Anchored;
    }
    extent(values).map_or(Zero::Free, |extent| smart(None, extent, true))
}

fn anchored(yes: bool) -> Zero {
    if yes { Zero::Anchored } else { Zero::Free }
}

/// `_pick_scale`'s verdict, then the sign of the data decides where it abstains: an
/// abstention anchors non-negative data and fits the rest.
fn smart(pin: Option<bool>, (lo, hi): (f64, f64), optional: bool) -> Zero {
    match pin {
        Some(pin) => Zero::Pinned(pin),
        None => match pick(lo, hi, optional) {
            Some(verdict) => anchored(verdict),
            None => anchored(lo >= 0.0),
        },
    }
}

/// `Some(true)` extends to zero, `Some(false)` stays data-fitted, `None` has no
/// opinion: the data spans or touches zero.
fn pick(lo: f64, hi: f64, optional: bool) -> Option<bool> {
    if hi == 0.0 || (lo < 0.0 && 0.0 < hi) {
        return None;
    }
    let (near, far) = if hi < 0.0 { (hi, lo) } else { (lo, hi) };
    if optional {
        return (near / far > ZERO_EXTEND_THRESHOLD).then_some(false);
    }
    (near != 0.0).then_some(true)
}

fn extent(values: &[f64]) -> Option<(f64, f64)> {
    let lo = values.iter().copied().reduce(f64::min)?;
    let hi = values.iter().copied().reduce(f64::max)?;
    Some((lo, hi))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bound::Ladder;
    use crate::plot::domain::{linear, Axis, Count, Mark};

    const LINE: Family = Family::Line;

    #[test]
    fn optional_families_zoom_past_a_quarter_and_anchor_below_it() {
        assert_eq!(verdict(LINE, None, &[76.0, 100.0]), Zero::Free);
        assert_eq!(verdict(LINE, None, &[20.0, 100.0]), Zero::Anchored);
        assert_eq!(verdict(Family::Scatter, None, &[25.0, 100.0]), Zero::Anchored);
    }

    #[test]
    fn all_negative_data_mirrors_the_ratio() {
        assert_eq!(verdict(LINE, None, &[-104.0, -76.0]), Zero::Free);
        assert_eq!(verdict(LINE, None, &[-100.0, -10.0]), Zero::Free);
    }

    #[test]
    fn spanning_or_touching_zero_fits_a_line_and_anchors_an_area() {
        assert_eq!(verdict(LINE, None, &[-5.0, 5.0]), Zero::Free);
        let area = Family::Area { center: false, stacked: false, independent: false, log: false };
        assert_eq!(verdict(area, None, &[0.0, 5.0]), Zero::Anchored);
        assert_eq!(verdict(area, None, &[-5.0, 5.0]), Zero::Free);
        assert_eq!(verdict(area, None, &[50.0, 60.0]), Zero::Anchored);
    }

    #[test]
    fn an_authored_pin_wins() {
        assert_eq!(verdict(LINE, Some(Setting::Value(true)), &[76.0, 100.0]), Zero::Pinned(true));
        assert_eq!(verdict(LINE, Some(Setting::Value(false)), &[1.0, 100.0]), Zero::Pinned(false));
    }

    #[test]
    fn area_carve_outs_never_anchor() {
        let area = |center, stacked, independent, log| Family::Area { center, stacked, independent, log };
        assert_eq!(verdict(area(true, false, false, false), None, &[1.0, 9.0]), Zero::Free);
        assert_eq!(verdict(area(false, false, false, true), Some(Setting::Value(true)), &[1.0, 9.0]), Zero::Free);
        assert_eq!(verdict(area(false, true, false, false), None, &[-9.0, -1.0]), Zero::Free);
        assert_eq!(verdict(area(false, false, false, false), None, &[-9.0, -1.0]), Zero::Anchored);
    }

    #[test]
    fn bar_anchors_unless_stacked_and_y_start_anchors_when_straddling() {
        let bar = |stacked, y_start, date_span| Family::Bar { stacked, y_start, date_span };
        assert_eq!(verdict(bar(false, false, false), None, &[5.0, 9.0]), Zero::Anchored);
        assert_eq!(verdict(bar(true, false, false), None, &[5.0, 9.0]), Zero::Free);
        assert_eq!(verdict(bar(true, false, false), Some(Setting::Value(true)), &[5.0]), Zero::Pinned(true));
        assert_eq!(verdict(bar(false, true, false), None, &[-3.0, 8.0]), Zero::Anchored);
        assert_eq!(verdict(bar(false, true, false), None, &[3.0, 8.0]), Zero::Free);
        assert_eq!(verdict(bar(false, false, true), Some(Setting::Value(true)), &[3.0, 8.0]), Zero::Free);
    }

    #[test]
    fn empty_data_anchors_line_and_area_but_never_a_scatter() {
        assert_eq!(verdict(LINE, Some(Setting::Value(false)), &[]), Zero::Anchored);
        assert_eq!(verdict(Family::Scatter, Some(Setting::Value(true)), &[]), Zero::Free);
    }

    #[test]
    fn auto_zero_skips_the_y_start_straddle_rule_that_absent_runs() {
        // The theme defaults: `headroom: 0.08`, `ticks.count: auto`.
        let spans = [100.0, 120.0, 150.0, 170.0];
        let bar = Family::Bar { stacked: false, y_start: true, date_span: false };
        assert_eq!(verdict(bar, None, &spans), Zero::Free);
        assert_eq!(verdict(bar, Some(Setting::Auto), &spans), Zero::Anchored);
        let ladder = |zero| {
            let axis = Axis { zero, headroom: 0.08, percent: false, count: Some(Count::Auto), values: None, domain: None, min_step: None, independent: false, nice: None, mark: Mark::Bar };
            let domain = linear(&axis, &spans, &|v: &[f64]| v.iter().map(f64::to_string).collect()).unwrap().unwrap();
            match domain.ladder {
                Ladder::Fixed(fixed) => fixed.rungs().iter().map(|r| r.value).collect::<Vec<_>>(),
                other => panic!("{other:?}"),
            }
        };
        assert_eq!(ladder(verdict(bar, None, &spans)), [80.0, 100.0, 120.0, 140.0, 160.0, 180.0]);
        assert_eq!(ladder(verdict(bar, Some(Setting::Auto), &spans)), [0.0, 50.0, 100.0, 150.0, 200.0]);
    }

    #[test]
    fn auto_zero_counts_as_authored_and_skips_the_scatter_x_bake() {
        assert_eq!(scatter_x(None, false, true, &[40.0, 90.0]), Zero::Anchored);
        assert_eq!(scatter_x(Some(Setting::Auto), false, true, &[40.0, 90.0]), Zero::Free);
        assert_eq!(scatter_x(None, false, false, &[10.0, 2020.0]), Zero::Anchored);
        assert_eq!(scatter_x(Some(Setting::Auto), false, false, &[10.0, 2020.0]), Zero::Free);
    }

    #[test]
    fn auto_zero_still_runs_the_smart_law_on_a_measure_axis() {
        for values in [[76.0, 100.0], [20.0, 100.0], [-5.0, 5.0]] {
            assert_eq!(verdict(LINE, Some(Setting::Auto), &values), verdict(LINE, None, &values));
        }
    }

    #[test]
    fn scatter_x_anchors_percent_and_zooms_far_columns() {
        assert_eq!(scatter_x(None, false, true, &[40.0, 90.0]), Zero::Anchored);
        assert_eq!(scatter_x(None, false, false, &[1990.0, 2020.0]), Zero::Free);
        assert_eq!(scatter_x(None, false, false, &[10.0, 2020.0]), Zero::Anchored);
        assert_eq!(scatter_x(None, true, true, &[1990.0, 2020.0]), Zero::Free);
    }
}

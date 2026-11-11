//! The value domain of a linear measure axis and the data half of its tick ladder.
//! The nice ladder and the exact bounds come from `d3rs::measure_axis::bake_ticks`,
//! the one law bind and the renderer share.

use d3rs::measure_axis::{apply_headroom, bake_ticks, MeasureAxis};
use diagnostic::{diag, Diagnostic};

use crate::bound::{Authored, Bounds, BoundsReason, Extent, Invalid, Ladder, Pin, PinReason, Rung, ValueDomain, Zero};

/// `ticks.count: auto`.
pub const AUTO_TICK_TARGET: usize = 6;

/// `ticks.count`, when set.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Count {
    Auto,
    N(usize),
}

impl Count {
    pub(crate) fn target(self) -> usize {
        match self {
            Count::Auto => AUTO_TICK_TARGET,
            Count::N(n) => n,
        }
    }
}

/// What the axis authors and the format implies.
pub struct Axis<'a> {
    pub zero: Zero,
    /// `scale.headroom`, 0 when unset.
    pub headroom: f64,
    pub count: Option<Count>,
    /// `scale.values`.
    pub values: Option<&'a [f64]>,
    /// `scale.domain`, in authored order (a reversed domain stays reversed).
    pub domain: Option<(f64, f64)>,
    /// The format's fixed-decimal step floor; `None` when the axis is not a measure.
    pub min_step: Option<f64>,
    /// `multiples.scale: independent`.
    pub independent: bool,
    /// `scale.nice`: absent leaves the default rule, a bool forces it.
    pub nice: Option<bool>,
    pub mark: Mark,
}

/// What draws the axis: the families disagree on when the scale rounds.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mark {
    /// A vertical bar without `y_start` pins `nice: false` unless the author set one.
    VerticalBar,
    /// Any other bar rounds only an unpinned, unauthored axis.
    Bar,
    /// Line and area: rounds after the pins, whatever they are.
    Plotted,
    /// A scatter's measure axis: its scale reads the pins verbatim, so only an unpinned,
    /// unauthored extent rounds, as on a bar.
    Points,
}

/// Prints a ladder's rungs together: the ruler law reads the whole ladder.
pub type RungText<'a> = &'a dyn Fn(&[f64]) -> Vec<String>;

/// The domain of `values`, every number drawn against the axis. `None` when there is
/// nothing to draw. `Ladder::None` when independent panels each own their scale.
pub fn linear(axis: &Axis, values: &[f64], text: RungText) -> Result<Option<ValueDomain>, Invalid> {
    let (Some(lo), Some(hi)) = (
        values.iter().copied().reduce(f64::min),
        values.iter().copied().reduce(f64::max),
    ) else {
        return Ok(None);
    };
    let authored = authored(axis.domain)?;
    let anchored = axis.zero_anchors();
    let (ladder, min, max) = if axis.independent && axis.values.is_none() && axis.domain.is_none() {
        (Ladder::None, None, None)
    } else {
        let baked = bake_ticks(
            &MeasureAxis {
                headroom: Some(axis.headroom),
                authored_domain: axis.domain,
                authored_ladder: axis.values.map(<[f64]>::to_vec),
                target_count: axis.count.map(Count::target),
                min_step: axis.min_step,
            },
            values,
            anchored,
        );
        // An independent panel owns its floor; only the authored domain reaches here.
        let min = if axis.independent { None } else { baked.domain_min };
        let mixed_sign = anchored && lo < 0.0 && hi >= 0.0;
        let min = min.map(|value| Pin {
            value,
            reason: if mixed_sign { PinReason::FormatFloor } else { PinReason::Headroom },
        });
        // A vertical bar's mixed-sign zero-anchored floor is its computed ladder's first rung
        // (`zero_anchor_domain_floor`), unless a floor was already pinned.
        let ladder_floor = (axis.mark == Mark::VerticalBar && mixed_sign && min.is_none() && axis.values.is_none())
            .then(|| baked.ticks.first().map(|&value| Pin { value, reason: PinReason::FormatFloor }))
            .flatten();
        let min = min.or(ladder_floor);
        let ladder = if let Some(authored_values) = axis.values {
            Ladder::Authored(rungs(authored_values, text))
        } else if axis.count.is_none() {
            Ladder::BySize { min_step: axis.min_step }
        } else {
            let ticks = match axis.domain {
                Some(domain) => clip_to_domain(&baked.ticks, domain),
                None => baked.ticks,
            };
            fixed(ticks, authored.map(|a| a.extent), text)?
        };
        let max = baked.domain_max.map(|value| Pin { value, reason: PinReason::Headroom });
        (ladder, min, max)
    };
    let extent = Extent::new(lo, hi)?;
    let bounds = linear_bounds(axis, anchored, (lo, hi), authored, min, max)?;
    Ok(Some(ValueDomain { extent, zero: axis.zero, headroom: axis.headroom, authored, ladder, min, max, bounds }))
}

/// A pin stands as is. A bar or a scatter rounds only an unpinned, unauthored extent (zero
/// included when anchored), and a vertical bar not even that; line and area round after the
/// pins. An authored `scale.nice` overrides either way and rounds a pinned, authored
/// or stacked extent outward. A flat unpinned extent cannot round, so it takes headroom.
fn linear_bounds(
    axis: &Axis,
    anchored: bool,
    (lo, hi): (f64, f64),
    authored: Option<Authored>,
    min: Option<Pin>,
    max: Option<Pin>,
) -> Result<Bounds, Invalid> {
    let (near, far) = if anchored { (lo.min(0.0), hi.max(0.0)) } else { (lo, hi) };
    let pinned = min.is_some() || max.is_some();
    let flat = !pinned && authored.is_none() && axis.mark == Mark::Plotted && far <= near;
    let base = match authored {
        Some(authored) => Bounds { extent: authored.extent, reason: BoundsReason::Authored },
        None if pinned => Bounds {
            extent: Extent::new(min.map_or(near, |pin| pin.value), max.map_or(far, |pin| pin.value))?,
            reason: BoundsReason::Pinned,
        },
        None if flat => Bounds {
            extent: Extent::new(near, headroom_top(far, axis.headroom).max(near))?,
            reason: BoundsReason::Fitted,
        },
        None => Bounds { extent: Extent::new(near, far)?, reason: BoundsReason::Fitted },
    };
    let default = authored.is_none()
        && match axis.mark {
            Mark::VerticalBar => false,
            Mark::Bar | Mark::Points => !pinned,
            Mark::Plotted => !flat,
        };
    nice_bounds(base, axis.nice.unwrap_or(default))
}

/// A data-fit domain rounded out to the axis's own tick boundaries at `tick_count`, then one
/// tick step further past a non-zero end a data value still lands on: a dot on the plot edge
/// reads as clipped, unless the edge is zero. A step past zero stops at zero.
pub fn nice_clear_of_data(lo: f64, hi: f64, (data_lo, data_hi): (f64, f64), tick_count: usize, min_step: Option<f64>) -> (f64, f64) {
    let (nice_lo, nice_hi) = d3rs::ticks::d3_nice(lo, hi, tick_count);
    if nice_hi <= nice_lo {
        return (nice_lo, nice_hi);
    }
    let count = d3rs::ticks::vega_tick_count(nice_lo, nice_hi, tick_count, min_step);
    let step = d3rs::ticks::d3_tick_step(nice_lo, nice_hi, count);
    let clamp_to_zero = |from: f64, to: f64| if from.signum() == to.signum() { to } else { 0.0 };
    (
        if data_lo == nice_lo && nice_lo != 0.0 { clamp_to_zero(nice_lo, nice_lo - step) } else { nice_lo },
        if data_hi == nice_hi && nice_hi != 0.0 { clamp_to_zero(nice_hi, nice_hi + step) } else { nice_hi },
    )
}

/// `bounds` rounded outward by d3's nice at count 10, whatever the ladder's count.
pub(crate) fn nice_bounds(bounds: Bounds, nice: bool) -> Result<Bounds, Invalid> {
    if !nice {
        return Ok(bounds);
    }
    let (lo, hi) = d3rs::ticks::d3_nice(bounds.extent.lo(), bounds.extent.hi(), 10);
    Ok(Bounds { extent: Extent::new(lo, hi)?, reason: BoundsReason::Niced })
}

impl Axis<'_> {
    /// `pin is True or (pin is None and data >= 0)`, already folded into the verdict.
    fn zero_anchors(&self) -> bool {
        matches!(self.zero, Zero::Anchored | Zero::Pinned(true))
    }
}

/// The domain of a log axis: no ladder and no pins, so no headroom applies. The caller
/// has refused non-positive values and a count.
pub fn log(values: &[f64], authored: Option<Authored>) -> Result<Option<ValueDomain>, Invalid> {
    let (Some(lo), Some(hi)) = (
        values.iter().copied().reduce(f64::min),
        values.iter().copied().reduce(f64::max),
    ) else {
        return Ok(None);
    };
    let extent = Extent::new(lo, hi)?;
    let bounds = match authored {
        Some(authored) => Bounds { extent: authored.extent, reason: BoundsReason::Authored },
        None => {
            let niced = d3rs::scale::Continuous::log([lo, hi], [0.0, 1.0]).nice(0).domain;
            Bounds { extent: Extent::new(niced[0], niced[1])?, reason: BoundsReason::Niced }
        }
    };
    Ok(Some(ValueDomain { extent, zero: Zero::Free, headroom: 0.0, authored, ladder: Ladder::None, min: None, max: None, bounds }))
}

/// A log area with no authored domain draws on the data extent plus the positive
/// annotation values.
pub fn log_area_domain(data: &[f64], annotations: &[f64]) -> Result<Option<Extent>, Invalid> {
    let all: Vec<f64> = data.iter().copied().chain(annotations.iter().copied().filter(|v| *v > 0.0)).collect();
    match (all.iter().copied().reduce(f64::min), all.iter().copied().reduce(f64::max)) {
        (Some(lo), Some(hi)) => Extent::new(lo, hi).map(Some),
        _ => Ok(None),
    }
}

/// A normalized stack is always `[0, 1]` unless the author set a domain.
pub fn normalized(authored: Option<Authored>) -> Result<ValueDomain, Invalid> {
    let unit = Extent::new(0.0, 1.0)?;
    let domain = authored.unwrap_or(Authored { extent: unit, reversed: false });
    Ok(ValueDomain {
        extent: unit,
        zero: Zero::Anchored,
        headroom: 0.0,
        authored: Some(domain),
        ladder: Ladder::BySize { min_step: None },
        min: None,
        max: None,
        bounds: Bounds { extent: domain.extent, reason: BoundsReason::Authored },
    })
}

/// An authored `scale.domain` with its direction kept.
pub fn authored(domain: Option<(f64, f64)>) -> Result<Option<Authored>, Invalid> {
    domain
        .map(|(a, b)| Ok(Authored { extent: Extent::new(a.min(b), a.max(b))?, reversed: a > b }))
        .transpose()
}

/// Rungs on the ladder `ticks`, printed together.
pub(crate) fn fixed(ticks: Vec<f64>, authored: Option<Extent>, text: RungText) -> Result<Ladder, Invalid> {
    let bounds = match authored {
        Some(authored) => authored,
        None => {
            let lo = ticks.iter().copied().reduce(f64::min).unwrap_or(f64::NAN);
            let hi = ticks.iter().copied().reduce(f64::max).unwrap_or(f64::NAN);
            Extent::new(lo, hi)?
        }
    };
    Ok(Ladder::Fixed(crate::bound::FixedLadder::new(rungs(&ticks, text), bounds)?))
}

pub(crate) fn rungs(values: &[f64], text: RungText) -> Vec<Rung> {
    let texts = text(values);
    assert_eq!(texts.len(), values.len(), "rung text must name every rung");
    values.iter().zip(texts).map(|(&value, text)| Rung { value, text }).collect()
}

/// Rungs inside the authored domain plus both authored ends. A nice ladder rounds
/// outward, so an end that is not a rung joins only when it sits on the step's
/// magnitude grid (a finer end would print rounded), and an interior rung within half
/// a step of an added end goes.
pub(crate) fn clip_to_domain(ticks: &[f64], authored: (f64, f64)) -> Vec<f64> {
    let (lo, hi) = (authored.0.min(authored.1), authored.0.max(authored.1));
    let inside: Vec<f64> = ticks.iter().copied().filter(|t| lo <= *t && *t <= hi).collect();
    if inside.len() < 2 {
        return inside;
    }
    let step = inside.windows(2).filter(|w| w[0] != w[1]).map(|w| (w[1] - w[0]).abs()).fold(f64::INFINITY, f64::min);
    let unit = 10f64.powf(step.log10().floor());
    let ends: Vec<f64> = [lo, hi]
        .into_iter()
        .filter(|e| !inside.contains(e) && (e / unit - (e / unit).round()).abs() < 1e-9)
        .collect();
    let mut merged: Vec<f64> = inside
        .into_iter()
        .filter(|t| ends.iter().all(|e| (t - e).abs() >= step / 2.0))
        .chain(ends.iter().copied())
        .collect();
    merged.sort_by(f64::total_cmp);
    if authored.0 > authored.1 {
        merged.reverse();
    }
    merged
}

/// The topmost value `headroom` buys above a positive total.
pub fn headroom_top(total: f64, headroom: f64) -> f64 {
    apply_headroom(total, Some(headroom))
}

/// Which axis of a log chart the data refuses.
#[derive(Debug, Clone, Copy)]
pub enum Side {
    X,
    Y,
}

/// Any value `<= 0` on a log axis refuses; every value is scanned. `field` is the
/// column the values came from (a layer's own column for a layer).
pub fn require_positive(chart_id: &str, side: Side, field: &str, values: &[f64]) -> Result<(), Diagnostic> {
    if values.iter().all(|v| *v > 0.0 || v.is_nan()) {
        return Ok(());
    }
    Err(match side {
        Side::X => diag::err_log_scale_requires_positive_x_data(chart_id, field),
        Side::Y => diag::err_log_scale_requires_positive_data(chart_id, field),
    })
}

/// What a family cannot do on a log axis.
#[derive(Debug, Clone, Copy)]
pub enum LogUse<'a> {
    Bar,
    /// `stack` is the resolved mode, `"none"` when unstacked.
    Area { stack: &'a str, independent: bool, authored_domain: bool },
    Other,
}

/// Refusals of a log measure axis in the order production raises them. A concrete
/// `ticks.count` refuses everywhere; `auto` does not.
pub fn refuse_log(chart_id: &str, family: LogUse, count: Option<Count>) -> Result<(), Diagnostic> {
    match family {
        LogUse::Bar => return Err(diag::err_bar_log_scale_not_supported(chart_id)),
        LogUse::Area { stack, .. } if stack != "none" => {
            return Err(diag::err_area_stacked_log_scale_not_supported(chart_id, stack))
        }
        LogUse::Area { independent: true, authored_domain: false, .. } => {
            return Err(diag::err_area_log_scale_independent_multiples(chart_id))
        }
        _ => {}
    }
    match count {
        Some(Count::N(_)) => Err(diag::err_ticks_count_requires_non_log_scale(chart_id)),
        _ => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn digits(values: &[f64]) -> Vec<String> {
        values.iter().map(|v| v.to_string()).collect()
    }

    fn axis(zero: Zero) -> Axis<'static> {
        Axis { zero, headroom: 0.0, count: None, values: None, domain: None, min_step: None, independent: false, nice: None, mark: Mark::Bar }
    }

    fn rung_values(domain: &ValueDomain) -> Vec<f64> {
        match &domain.ladder {
            Ladder::Fixed(fixed) => fixed.rungs().iter().map(|r| r.value).collect(),
            other => panic!("not a fixed ladder: {other:?}"),
        }
    }

    #[test]
    fn a_set_count_fixes_a_nice_ladder_with_headroom_on_the_anchored_top() {
        let axis = Axis { count: Some(Count::N(6)), headroom: 0.1, ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(rung_values(&domain), [0.0, 50.0, 100.0, 150.0]);
        assert_eq!((domain.min, domain.max), (None, Some(Pin { value: 92.0 * 1.1, reason: PinReason::Headroom })));
        assert_eq!((domain.extent.lo(), domain.extent.hi()), (3.0, 92.0));
    }

    #[test]
    fn auto_is_six_and_a_zoomed_axis_gets_span_headroom() {
        let axis = Axis { count: Some(Count::Auto), headroom: 0.1, ..axis(Zero::Free) };
        let domain = linear(&axis, &[100.0, 200.0], &digits).unwrap().unwrap();
        assert_eq!((domain.min.map(|p| p.value), domain.max.map(|p| p.value)), (Some(90.0), Some(210.0)));
        assert_eq!(rung_values(&domain), [50.0, 100.0, 150.0, 200.0, 250.0]);
    }

    #[test]
    fn auto_count_bakes_the_target_ladder_and_an_absent_count_bakes_none() {
        let auto = Axis { count: Some(Count::Auto), ..axis(Zero::Anchored) };
        let explicit = Axis { count: Some(Count::N(AUTO_TICK_TARGET)), ..axis(Zero::Anchored) };
        let absent = axis(Zero::Anchored);
        let ladder = |axis: &Axis| linear(axis, &[1.0, 95.0], &digits).unwrap().unwrap().ladder;
        assert!(matches!(ladder(&auto), Ladder::Fixed(_)));
        assert_eq!(ladder(&auto), ladder(&explicit));
        assert_eq!(ladder(&absent), Ladder::BySize { min_step: None });
    }

    #[test]
    fn an_unset_count_is_by_size_and_authored_values_pass_verbatim() {
        let by_size = Axis { min_step: Some(1.0), ..axis(Zero::Anchored) };
        let domain = linear(&by_size, &[1.0, 5.0], &digits).unwrap().unwrap();
        assert_eq!(domain.ladder, Ladder::BySize { min_step: Some(1.0) });
        let authored = Axis { values: Some(&[25.0, 50.0, 100.0]), ..axis(Zero::Anchored) };
        let domain = linear(&authored, &[1.0, 5.0], &digits).unwrap().unwrap();
        assert_eq!(rung_values_authored(&domain), [25.0, 50.0, 100.0]);
        assert_eq!((domain.min, domain.max), (None, None));
    }

    fn rung_values_authored(domain: &ValueDomain) -> Vec<f64> {
        match &domain.ladder {
            Ladder::Authored(rungs) => rungs.iter().map(|r| r.value).collect(),
            other => panic!("not authored: {other:?}"),
        }
    }

    #[test]
    fn independent_multiples_have_no_ladder_unless_a_domain_is_authored() {
        let independent = Axis { count: Some(Count::N(5)), independent: true, ..axis(Zero::Anchored) };
        let domain = linear(&independent, &[1.0, 5.0], &digits).unwrap().unwrap();
        assert_eq!(domain.ladder, Ladder::None);
        let authored = Axis { domain: Some((0.0, 10.0)), ..independent };
        let domain = linear(&authored, &[1.0, 5.0], &digits).unwrap().unwrap();
        assert_eq!(rung_values(&domain), [0.0, 5.0, 10.0]);
    }

    #[test]
    fn an_authored_domain_clips_the_ladder_and_ticks_its_ends() {
        assert_eq!(clip_to_domain(&[0.0, 5.0, 10.0, 15.0, 20.0, 25.0], (1.0, 20.0)), [1.0, 5.0, 10.0, 15.0, 20.0]);
        assert_eq!(clip_to_domain(&[0.0, 5.0, 10.0], (10.5, 0.0)), [10.0, 5.0, 0.0]);
        assert_eq!(clip_to_domain(&[0.0, 5.0, 10.0, 15.0], (4.0, 11.0)), [4.0, 11.0]);
    }

    #[test]
    fn an_all_negative_anchored_axis_pins_its_data_floor() {
        let axis = Axis { count: Some(Count::N(5)), headroom: 0.1, ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[-100.0, -20.0], &digits).unwrap().unwrap();
        assert_eq!(domain.min, Some(Pin { value: -110.00000000000001, reason: PinReason::Headroom }));
        assert_eq!(domain.max, None);
    }

    #[test]
    fn a_reversed_authored_domain_stays_reversed() {
        let axis = Axis { count: Some(Count::N(5)), domain: Some((10.0, 0.0)), ..axis(Zero::Free) };
        let domain = linear(&axis, &[1.0, 5.0], &digits).unwrap().unwrap();
        let authored = domain.authored.unwrap();
        assert_eq!((authored.extent.lo(), authored.extent.hi(), authored.reversed), (0.0, 10.0, true));
        assert_eq!(rung_values(&domain), [10.0, 5.0, 0.0]);
    }

    #[test]
    fn a_mixed_sign_format_floor_names_its_reason() {
        let axis = Axis { count: Some(Count::N(8)), min_step: Some(10.0), ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[-3.0, 25.0], &digits).unwrap().unwrap();
        assert_eq!(domain.min, Some(Pin { value: -3.0, reason: PinReason::FormatFloor }));
    }

    fn bounds(domain: &ValueDomain) -> (f64, f64, BoundsReason) {
        (domain.bounds.extent.lo(), domain.bounds.extent.hi(), domain.bounds.reason)
    }

    #[test]
    fn an_unpinned_axis_nices_its_extent_at_count_ten() {
        let axis = Axis { headroom: 0.08, ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 100.0, BoundsReason::Niced));
        let zoomed = Axis { zero: Zero::Free, ..axis };
        let domain = linear(&zoomed, &[103.0, 192.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (100.0, 200.0, BoundsReason::Niced));
    }

    #[test]
    fn a_vertical_bar_over_mixed_signs_floors_at_its_first_rung() {
        let bar = Axis { mark: Mark::VerticalBar, count: Some(Count::Auto), headroom: 0.08, ..axis(Zero::Anchored) };
        let domain = linear(&bar, &[-5.0, 40.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (-10.0, 43.2, BoundsReason::Pinned));
        let positive = linear(&bar, &[5.0, 40.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&positive), (0.0, 43.2, BoundsReason::Pinned));
    }

    #[test]
    fn a_vertical_bar_fits_its_extent_instead_of_nicing() {
        let bar = Axis { mark: Mark::VerticalBar, ..axis(Zero::Anchored) };
        let domain = linear(&bar, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 92.0, BoundsReason::Fitted));
        let horizontal = Axis { mark: Mark::Bar, ..bar };
        let domain = linear(&horizontal, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 100.0, BoundsReason::Niced));
    }

    #[test]
    fn line_and_area_round_after_the_pins_and_a_scatter_keeps_them() {
        let line = Axis { mark: Mark::Plotted, headroom: 0.08, count: Some(Count::Auto), ..axis(Zero::Free) };
        let domain = linear(&line, &[20000.0, 40000.0], &digits).unwrap().unwrap();
        assert_eq!(domain.min.map(|p| p.value), Some(18400.0));
        let expected = d3rs::ticks::d3_nice(18400.0, 41600.0, 10);
        assert_eq!(bounds(&domain), (expected.0, expected.1, BoundsReason::Niced));
        for mark in [Mark::Bar, Mark::Points] {
            let pinned = Axis { mark, ..line };
            let domain = linear(&pinned, &[20000.0, 40000.0], &digits).unwrap().unwrap();
            assert_eq!(bounds(&domain), (18400.0, 41600.0, BoundsReason::Pinned));
        }
    }

    #[test]
    fn a_flat_unpinned_line_takes_headroom_instead_of_rounding() {
        let line = Axis { mark: Mark::Plotted, headroom: 0.08, ..axis(Zero::Free) };
        let domain = linear(&line, &[120.0, 120.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (120.0, 120.0 * 1.08, BoundsReason::Fitted));
    }

    #[test]
    fn an_authored_nice_overrides_the_default_either_way() {
        let off = Axis { nice: Some(false), ..axis(Zero::Anchored) };
        let domain = linear(&off, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 92.0, BoundsReason::Fitted));
        let pinned = Axis { nice: Some(true), headroom: 0.08, count: Some(Count::N(5)), mark: Mark::VerticalBar, ..axis(Zero::Anchored) };
        let domain = linear(&pinned, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 100.0, BoundsReason::Niced));
        let authored = Axis { nice: Some(true), domain: Some((0.0, 3654.0)), ..axis(Zero::Anchored) };
        let domain = linear(&authored, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 4000.0, BoundsReason::Niced));
    }

    #[test]
    fn a_pinned_end_is_not_niced() {
        let axis = Axis { headroom: 0.08, count: Some(Count::N(5)), ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (0.0, 92.0 * 1.08, BoundsReason::Pinned));
    }

    #[test]
    fn an_authored_domain_is_not_niced() {
        let axis = Axis { domain: Some((90.0, 3.5)), ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[3.0, 92.0], &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (3.5, 90.0, BoundsReason::Authored));
    }

    #[test]
    fn a_log_axis_nices_to_powers_unless_authored() {
        let domain = log(&[3.0, 920.0], None).unwrap().unwrap();
        assert_eq!(bounds(&domain), (1.0, 1000.0, BoundsReason::Niced));
    }

    #[test]
    fn no_values_have_no_domain() {
        assert!(linear(&axis(Zero::Anchored), &[], &digits).unwrap().is_none());
    }

    #[test]
    fn log_refuses_by_name_and_scans_every_value() {
        let mut values = vec![1.0; 10_000];
        values.push(0.0);
        let refused = require_positive("c", Side::Y, "rev", &values).unwrap_err();
        assert_eq!(refused.code, "ERR-LOG-SCALE-REQUIRES-POSITIVE-DATA");
        let refused = require_positive("c", Side::X, "w", &[-1.0]).unwrap_err();
        assert_eq!(refused.code, "ERR-LOG-SCALE-REQUIRES-POSITIVE-X-DATA");
        assert!(require_positive("c", Side::Y, "rev", &[0.5, 2.0]).is_ok());
    }

    #[test]
    fn log_family_refusals_in_production_order() {
        let code = |family, count| refuse_log("c", family, count).unwrap_err().code.to_string();
        assert_eq!(code(LogUse::Bar, None), "ERR-BAR-LOG-SCALE-NOT-SUPPORTED");
        let area = |stack, independent, authored_domain| LogUse::Area { stack, independent, authored_domain };
        assert_eq!(code(area("zero", true, false), None), "ERR-AREA-STACKED-LOG-SCALE-NOT-SUPPORTED");
        assert_eq!(code(area("none", true, false), None), "ERR-AREA-LOG-SCALE-INDEPENDENT-MULTIPLES");
        assert_eq!(code(LogUse::Other, Some(Count::N(5))), "ERR-TICKS-COUNT-REQUIRES-NON-LOG-SCALE");
        assert!(refuse_log("c", area("none", true, true), Some(Count::Auto)).is_ok());
    }

    #[test]
    fn a_log_area_draws_on_the_data_and_positive_annotations() {
        let extent = log_area_domain(&[2.0, 50.0], &[-1.0, 100.0]).unwrap().unwrap();
        assert_eq!((extent.lo(), extent.hi()), (2.0, 100.0));
    }
}

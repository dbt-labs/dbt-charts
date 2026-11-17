//! The value domain of a linear measure axis and the data half of its tick ladder.
//! The nice ladder and the exact bounds come from `d3rs::measure_axis::bake_ticks`,
//! the one law bind and the renderer share.

use d3rs::measure_axis::{bake_ticks, MeasureAxis};
use d3rs::scale::Continuous;
use diagnostic::{diag, Diagnostic};

use crate::bound::{Authored, Bounds, BoundsReason, Extent, Invalid, Ladder, Pin, PinReason, Rung, ValueDomain, ValueScale, Zero};

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
    /// A bent scale (pow, sqrt, symlog) ticks and rounds as a linear one does, in the data's own
    /// space, and only draws its marks bent: `linear` carries the type through. A log scale has its
    /// own ladder ([`log`]).
    pub scale: ValueScale,
    pub zero: Zero,
    /// `scale.headroom`, 0 when unset.
    pub headroom: f64,
    /// The axis's format is a percent: headroom on values within [0, 1] stops at those bounds.
    pub percent: bool,
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
    if matches!(axis.scale, ValueScale::Log { .. }) {
        return Err(Invalid::LogScaleLinearLadder);
    }
    let authored = authored(axis.domain)?;
    let anchored = axis.zero_anchors();
    let (ladder, min, max) = if axis.independent && axis.values.is_none() && axis.domain.is_none() {
        (Ladder::None, None, None)
    } else {
        let baked = bake_ticks(
            &MeasureAxis {
                headroom: Some(axis.headroom),
                percent: axis.percent,
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
    Ok(Some(ValueDomain { scale: axis.scale, extent, zero: axis.zero, headroom: axis.headroom, authored, ladder, min, max, bounds }))
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
            extent: Extent::new(near, headroom_top(far, axis.headroom, axis.percent, (near, far)).max(near))?,
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

/// The domain of a log axis over `values` at `base`, with the rungs Vega draws on it. Production
/// bakes no ladder for a log axis, so there are no pins and no headroom: the bounds are the authored
/// pair verbatim (a descending one keeps its direction), else the data extent rounded out to powers
/// of the base (`LogAxis::new`, `chart-renderer/src/families/scatter.rs`). The rungs are d3's
/// loggish ticks over those bounds at `count` (`scale.ticks(count)`), the minor ticks `tickFormat`
/// blanks (`log_tick_visible`) left unprinted and the others printed together by `text`. The caller
/// has refused non-positive values.
pub fn log(base: f64, count: usize, values: &[f64], authored_domain: Option<(f64, f64)>, text: RungText) -> Result<Option<ValueDomain>, Invalid> {
    let (Some(lo), Some(hi)) = (
        values.iter().copied().reduce(f64::min),
        values.iter().copied().reduce(f64::max),
    ) else {
        return Ok(None);
    };
    let scale = ValueScale::Log { base };
    let extent = Extent::new(lo, hi)?;
    let authored = authored(authored_domain)?;
    let bounds = match authored {
        Some(authored) => Bounds { extent: authored.extent, reason: BoundsReason::Authored },
        None => {
            let niced = Continuous::new(scale.transform(), [lo, hi], [0.0, 1.0]).nice(0).domain;
            Bounds { extent: Extent::new(niced[0], niced[1])?, reason: BoundsReason::Niced }
        }
    };
    let (start, stop) = match authored {
        Some(Authored { reversed: true, .. }) => (bounds.extent.hi(), bounds.extent.lo()),
        _ => (bounds.extent.lo(), bounds.extent.hi()),
    };
    let ticks = Continuous::new(scale.transform(), [start, stop], [0.0, 1.0]);
    let (values_at, shown) = (ticks.ticks(count), ticks.log_tick_visible(count));
    let printed: Vec<f64> = values_at.iter().copied().filter(|tick| shown(*tick)).collect();
    let mut texts = text(&printed).into_iter();
    let rungs = values_at
        .iter()
        .map(|&value| Rung { value, text: if shown(value) { texts.next().unwrap_or_default() } else { String::new() } })
        .collect();
    Ok(Some(ValueDomain { scale, extent, zero: Zero::Free, headroom: 0.0, authored, ladder: Ladder::Log(rungs), min: None, max: None, bounds }))
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
        scale: ValueScale::Linear,
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
pub fn headroom_top(total: f64, headroom: f64, percent: bool, data: (f64, f64)) -> f64 {
    d3rs::measure_axis::headroom_top(total, Some(headroom), percent, data)
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

/// Whether `error` is one of the compile errors production raises for a log axis (`require_positive`,
/// `refuse_log`): the chart's own error card, which the rest of the board draws beside.
pub fn is_log_error(error: &Diagnostic) -> bool {
    [
        diagnostic::codes::ERR_LOG_SCALE_REQUIRES_POSITIVE_DATA.id,
        diagnostic::codes::ERR_LOG_SCALE_REQUIRES_POSITIVE_X_DATA.id,
        diagnostic::codes::ERR_BAR_LOG_SCALE_NOT_SUPPORTED.id,
        diagnostic::codes::ERR_AREA_STACKED_LOG_SCALE_NOT_SUPPORTED.id,
        diagnostic::codes::ERR_AREA_LOG_SCALE_INDEPENDENT_MULTIPLES.id,
        diagnostic::codes::ERR_TICKS_COUNT_REQUIRES_NON_LOG_SCALE.id,
    ]
    .contains(&error.code)
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
        Axis { scale: ValueScale::Linear, zero, headroom: 0.0, percent: false, count: None, values: None, domain: None, min_step: None, independent: false, nice: None, mark: Mark::Bar }
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
    fn a_percent_zoomed_axis_stops_headroom_at_the_unit_bounds() {
        let axis = Axis { count: Some(Count::Auto), headroom: 0.08, percent: true, mark: Mark::Plotted, ..axis(Zero::Free) };
        let domain = linear(&axis, &[0.38, 0.7, 1.0], &digits).unwrap().unwrap();
        assert_eq!(domain.max.map(|p| p.value), Some(1.0));
        assert_eq!(domain.min.map(|p| p.value), Some(0.38 - 0.08 * 0.62));
        let low = linear(&axis, &[0.02, 0.5], &digits).unwrap().unwrap();
        assert_eq!(low.min.map(|p| p.value), Some(0.0));
        assert_eq!(low.max.map(|p| p.value), Some(0.5 + 0.08 * 0.48));
    }

    #[test]
    fn a_percent_axis_past_the_unit_bounds_or_not_a_percent_keeps_headroom() {
        let axis = Axis { count: Some(Count::Auto), headroom: 0.1, percent: true, ..axis(Zero::Free) };
        let over = linear(&axis, &[0.4, 1.2], &digits).unwrap().unwrap();
        assert_eq!(over.max.map(|p| p.value), Some(1.2 + 0.1 * 0.8));
        let plain = Axis { percent: false, ..axis };
        let domain = linear(&plain, &[0.4, 1.0], &digits).unwrap().unwrap();
        assert_eq!(domain.max.map(|p| p.value), Some(1.0 + 0.1 * 0.6));
    }

    #[test]
    fn a_percent_anchored_top_stops_at_100() {
        let axis = Axis { count: Some(Count::Auto), headroom: 0.08, percent: true, ..axis(Zero::Anchored) };
        let domain = linear(&axis, &[0.4, 1.0], &digits).unwrap().unwrap();
        assert_eq!(domain.max.map(|p| p.value), Some(1.0));
        let below = linear(&axis, &[0.4, 0.5], &digits).unwrap().unwrap();
        assert_eq!(below.max.map(|p| p.value), Some(0.5 * 1.08));
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

    fn log_rungs(domain: &ValueDomain) -> Vec<(f64, String)> {
        match &domain.ladder {
            Ladder::Log(rungs) => rungs.iter().map(|r| (r.value, r.text.clone())).collect(),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn a_log_axis_nices_to_powers_and_prints_the_minor_ticks_d3_keeps() {
        let domain = log(10.0, 10, &[3.0, 920.0], None, &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (1.0, 1000.0, BoundsReason::Niced));
        assert_eq!(domain.scale, ValueScale::Log { base: 10.0 });
        // Under three decades every mantissa ticks; d3's `tickFormat` prints those up to k = 10 * 10 / 28 = 3.57.
        let rungs = log_rungs(&domain);
        assert_eq!(rungs.len(), 28);
        let printed: Vec<f64> = rungs.iter().filter(|(_, text)| !text.is_empty()).map(|r| r.0).collect();
        assert_eq!(printed, [1.0, 2.0, 3.0, 10.0, 20.0, 30.0, 100.0, 200.0, 300.0, 1000.0]);
        assert!(rungs.iter().all(|(value, text)| text.is_empty() || *text == value.to_string()));
    }

    #[test]
    fn an_authored_log_domain_stands_and_keeps_its_direction() {
        let domain = log(10.0, 10, &[7.0, 300.0], Some((500.0, 5.0)), &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (5.0, 500.0, BoundsReason::Authored));
        let rungs = log_rungs(&domain);
        assert_eq!((rungs.first().unwrap().0, rungs.last().unwrap().0), (500.0, 5.0));
    }

    #[test]
    fn a_log_axis_over_a_base_ticks_its_powers() {
        let domain = log(2.0, 10, &[4.0, 7000.0], None, &digits).unwrap().unwrap();
        assert_eq!(bounds(&domain), (4.0, 8192.0, BoundsReason::Niced));
        let values: Vec<f64> = log_rungs(&domain).iter().map(|r| r.0).collect();
        assert_eq!(values, [4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0, 8192.0]);
    }

    #[test]
    fn a_log_scale_has_no_linear_ladder() {
        let axis = Axis { scale: ValueScale::Log { base: 10.0 }, ..axis(Zero::Free) };
        assert_eq!(linear(&axis, &[1.0, 10.0], &digits), Err(Invalid::LogScaleLinearLadder));
    }

    #[test]
    fn a_bent_scale_ticks_and_rounds_as_a_linear_one() {
        let linear_domain = linear(&Axis { count: Some(Count::N(5)), ..axis(Zero::Free) }, &[3000.0, 6000.0], &digits).unwrap().unwrap();
        let sqrt = Axis { scale: ValueScale::Sqrt, count: Some(Count::N(5)), ..axis(Zero::Free) };
        let bent = linear(&sqrt, &[3000.0, 6000.0], &digits).unwrap().unwrap();
        assert_eq!(bent, ValueDomain { scale: ValueScale::Sqrt, ..linear_domain });
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

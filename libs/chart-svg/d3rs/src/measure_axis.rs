//! The law that bakes a measure axis's tick ladder and domain bounds from the values it plots
//! (`tick_values.py::_resolve_cartesian_ticks`), shared by the resolve stage and by the
//! renderer, which runs it again over the shown rows once a legend view refits the axis.

use crate::invariant::Invariant;

/// `_resolve_cartesian_ticks`'s own mirrored negative-headroom formula
/// (the all-negative zero-anchored branch's own floor, and its shared
/// `negative_floor` guard above it): `raw_min * (1 + h)` when headroom is
/// set, else `raw_min` verbatim.
pub fn apply_negative_headroom(raw_min: f64, headroom: Option<f64>) -> f64 {
    match headroom {
        Some(h) if h != 0.0 => raw_min * (1.0 + h),
        _ => raw_min,
    }
}

/// `tick_values.py::apply_headroom`.
pub fn apply_headroom(value: f64, headroom: Option<f64>) -> f64 {
    match headroom {
        Some(h) if h != 0.0 && value > 0.0 => value * (1.0 + h),
        _ => value,
    }
}

/// `value * (1 + headroom)` for a positive `value`, held at 1.0 when `percent` and the data
/// (`lo..=hi`) lies within [0, 1]: a share cannot pass 100%. Data outside the unit interval
/// keeps its headroom.
pub fn headroom_top(value: f64, headroom: Option<f64>, percent: bool, (lo, hi): (f64, f64)) -> f64 {
    let top = apply_headroom(value, headroom);
    if percent && lo >= 0.0 && hi <= 1.0 { top.min(1.0).max(value) } else { top }
}

/// What the law reads of an axis: plain values, so no caller needs the axis's wire form.
pub struct MeasureAxis {
    /// `scale.headroom`.
    pub headroom: Option<f64>,
    /// The axis's format is a percent (see [`headroom_top`]).
    pub percent: bool,
    /// An authored `scale.continuous.domain`.
    pub authored_domain: Option<(f64, f64)>,
    /// An authored `scale.values` ladder.
    pub authored_ladder: Option<Vec<f64>>,
    /// `ticks.count`.
    pub target_count: Option<usize>,
    /// The step the axis's fixed-decimal format floors the ladder at.
    pub min_step: Option<f64>,
}

/// The ladder and the exact (non-nice-rounded) domain bounds one axis bakes.
#[derive(Debug, Clone)]
pub struct TickBake {
    pub ticks: Vec<f64>,
    pub domain_max: Option<f64>,
    pub domain_min: Option<f64>,
}

/// Approximate Vega-Lite tick count on a ladder-less measure axis
/// (height-derived, about ten on a default-height plot).
const VEGA_DEFAULT_TICK_COUNT: usize = 11;

/// `_resolve_cartesian_ticks`: the nice tick ladder and domain bounds for `y_floats`, every value
/// drawn against the axis. An unauthored `ticks.count` bakes no ladder unless the axis's
/// fixed-decimal format floors the step (`min_step`): then the domain bounds bake where the floor
/// could bind, judged by a probe at about Vega-Lite's default density.
pub fn bake_ticks(axis: &MeasureAxis, y_floats: &[f64], zero_anchor: bool) -> TickBake {
    let negative_floor = if zero_anchor
        && !y_floats.is_empty()
        && y_floats.iter().cloned().fold(f64::NEG_INFINITY, f64::max) < 0.0
    {
        let raw_min = y_floats.iter().cloned().fold(f64::INFINITY, f64::min);
        let h = axis.headroom;
        Some(apply_negative_headroom(raw_min, h))
    } else {
        None
    };
    if let Some(ladder) = axis.authored_ladder.clone() {
        return TickBake {
            ticks: ladder,
            domain_max: None,
            domain_min: negative_floor,
        };
    }
    let min_step = axis.min_step;
    let target_count = axis.target_count;
    if (target_count.is_none() && min_step.is_none()) || y_floats.is_empty() {
        return TickBake {
            ticks: Vec::new(),
            domain_max: None,
            domain_min: negative_floor,
        };
    }
    // Set only in the zero-anchored mixed-sign case: the headroom-expanded data
    // floor to pin if the floored ladder's bottom rung falls below it.
    let mut mixed_sign_floor: Option<f64> = None;
    let authored = axis.authored_domain;
    let mut domain_max: Option<f64> = None;
    let mut domain_min: Option<f64> = None;
    let (tick_min, tick_max) = if let Some((lo, hi)) = authored {
        (lo, hi)
    } else if zero_anchor {
        let raw_min = y_floats.iter().cloned().fold(f64::INFINITY, f64::min);
        let raw_max = y_floats.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let h = axis.headroom;
        let tick_min = raw_min.min(0.0);
        let tick_max = headroom_top(raw_max, h, axis.percent, (raw_min, raw_max)).max(0.0);
        if raw_max >= 0.0 {
            // `>= 0`: a data max of exactly 0 is mixed-sign the same way a
            // strictly positive max is — the anchored edge is the data's own
            // ceiling.
            // Rungs above a baked domainMax are not drawn, so the baked top is
            // what tells resolve which positive rungs exist.
            domain_max = Some(tick_max);
            if raw_min < 0.0 {
                mixed_sign_floor = Some(match h {
                    Some(h) if h != 0.0 => raw_min * (1.0 + h),
                    _ => raw_min,
                });
            }
        } else {
            // `_domain.py`'s own `else` arm bakes the mirror-of-positive
            // floor only when the max is strictly BELOW zero (the SAME
            // predicate `negative_floor` above already computed) — an
            // all-zero extent (`raw_max == 0.0`) reaches this branch too
            // (zero-anchored, `raw_max > 0.0` false) but bakes no floor at
            // all, matching production exactly.
            domain_min = negative_floor;
        }
        (tick_min, tick_max)
    } else {
        let data_min = y_floats.iter().cloned().fold(f64::INFINITY, f64::min);
        let raw_max = y_floats.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let h = axis.headroom;
        let span = raw_max - data_min;
        if h.is_some_and(|h| h != 0.0) && span > 0.0 {
            let h = h.held("checked by is_some_and above");
            let (dmax, dmin) = (raw_max + h * span, data_min - h * span);
            let unit = axis.percent && data_min >= 0.0 && raw_max <= 1.0;
            let dmax = if unit { dmax.min(1.0) } else { dmax };
            let dmin = if unit { dmin.max(0.0) } else { dmin };
            domain_max = Some(dmax);
            domain_min = Some(dmin);
            (dmin, dmax)
        } else {
            if span > 0.0 {
                domain_max = Some(raw_max);
            }
            (data_min, raw_max)
        }
    };
    let Some(target_count) = target_count else {
        let floor = min_step.held("checked above: a count-less axis has a floor");
        let probe =
            |step| crate::ticks::nice_tick_values(tick_min, tick_max, VEGA_DEFAULT_TICK_COUNT, step);
        if probe(None) == probe(Some(floor)) {
            return TickBake {
                ticks: Vec::new(),
                domain_max: None,
                domain_min: negative_floor,
            };
        }
        return TickBake {
            ticks: Vec::new(),
            domain_max,
            domain_min,
        };
    };
    let ticks = crate::ticks::nice_tick_values(tick_min, tick_max, target_count, min_step);
    if let (Some(floor_at), Some(_), Some(first)) = (mixed_sign_floor, min_step, ticks.first()) {
        // Compare against the UNFLOORED pick: nice rounding always extends the
        // bottom rung past the data by itself, so only a floor that pushed it
        // FURTHER needs the pin.
        let unfloored = crate::ticks::nice_tick_values(tick_min, tick_max, target_count, None);
        if unfloored.first().is_some_and(|u| first < u) {
            domain_min = Some(floor_at);
        }
    }
    TickBake {
        ticks,
        domain_max,
        domain_min,
    }
}

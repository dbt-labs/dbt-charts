//! Shared cartesian scaffold — band/linear scales used by bar, line, area,
//! scatter, and heatmap.
//!
//! All computation is pure math: no SVG emission here. Families import these
//! types, compute their own margins (by measuring label text with FontBook), and
//! call the scale structs to place marks and axis decorations.

use diagnostic::codes;
use diagnostic::Diagnostic;
use d3rs::scale::Band;
use scene::{Attrs, Chars, Escape, Node, Role, Run, Shape};
use serde_json::json;
use std::collections::HashMap;

/// Ordinal band scale: maps string values to pixel bands.
///
/// Follows the d3-scale band formula exactly:
/// ```text
/// step      = range_width / (n − padding_inner + 2 × padding_outer)
/// bandwidth = step × (1 − padding_inner)
/// origin    = range_start + step × padding_outer
/// position[i] = origin + step × i
/// ```
///
/// `padding_inner` is the fraction of `step` consumed by the gap between
/// adjacent bands; `padding_outer` is the fraction consumed by the half-gap at
/// each range end (matching d3's convention, where `paddingOuter = 0` means the
/// first band starts exactly at `range_start`).
#[derive(Clone)]
pub struct BandScale {
    domain: Vec<String>,
    geometry: Bands,
}

#[derive(Clone)]
enum Bands {
    Uniform(Band),
    /// Bands that sit where their data does (a time axis): one leading edge and one point-mark position per
    /// domain entry, no shared width.
    Anchored { starts: Vec<f64>, anchors: Vec<f64> },
}

/// Pixel position `frac` of the way across band `i` (`0` leading, `1` trailing).
pub fn band_edge(band: &Band, i: usize, frac: f64) -> f64 {
    band.position(i) + band.bandwidth * frac
}

impl BandScale {
    /// Construct from a domain, a pixel range, and d3-style padding fractions.
    pub fn new(
        domain: Vec<String>,
        range_start: f64,
        range_end: f64,
        padding_inner: f64,
        padding_outer: f64,
    ) -> Self {
        let geometry = Bands::Uniform(Band::new(domain.len(), [range_start, range_end], padding_inner, padding_outer, 0.5, false));
        BandScale { domain, geometry }
    }

    /// A band scale whose entry `i` leads at `starts[i]` pixels and whose point marks sit at `anchors[i]`.
    pub fn anchored(domain: Vec<String>, starts: Vec<f64>, anchors: Vec<f64>) -> Self {
        assert!(domain.len() == starts.len() && domain.len() == anchors.len(), "an anchored band scale places every domain entry");
        BandScale { domain, geometry: Bands::Anchored { starts, anchors } }
    }

    /// Leading-edge pixel position of the band for `value`.
    ///
    /// Returns `None` when `value` is not in the domain.
    pub fn position(&self, value: &str) -> Option<f64> {
        Some(self.position_at(self.domain.iter().position(|d| d == value)?))
    }

    /// The bands, in order.
    pub fn domain(&self) -> &[String] {
        &self.domain
    }

    /// Leading-edge pixel position of band `at`.
    pub fn position_at(&self, at: usize) -> f64 {
        match &self.geometry {
            Bands::Uniform(g) => g.position(at),
            Bands::Anchored { starts, .. } => starts[at],
        }
    }

    /// Where a point mark of `value`'s datum sits: its band's center, or on an anchored scale where its data
    /// does.
    pub fn center(&self, value: &str) -> Option<f64> {
        let at = self.domain.iter().position(|d| d == value)?;
        Some(match &self.geometry {
            Bands::Uniform(g) => band_edge(g, at, 0.5),
            Bands::Anchored { anchors, .. } => anchors[at],
        })
    }

    /// Bandwidth: the pixel extent available to a mark within its band.
    ///
    /// Equals `step × (1 − padding_inner)`. An anchored scale has none, its bands own their extents.
    pub fn bandwidth(&self) -> f64 {
        match &self.geometry {
            Bands::Uniform(g) => g.bandwidth,
            Bands::Anchored { .. } => unreachable!("an anchored band scale has no shared bandwidth"),
        }
    }
}

/// An authored `scale.continuous.domain` pair as Vega draws it. The pair
/// reaches Vega verbatim (`vl_field_maps.py:114-119`) and resolve bakes no
/// `domain_min`/`domain_max` beside it, so `nice` is the one term that still
/// applies, only when the author set `scale.nice: true`: `configureDomain`
/// (`vega.js:21556-21559`) runs `scale.nice(null)`, d3's count 10.
pub fn authored_domain_pair(ax: &crate::common::AxisStyle, lo: f64, hi: f64) -> (f64, f64) {
    if ax.scale.get("nice").and_then(serde_json::Value::as_bool) == Some(true) {
        // d3's `nice` rounds a descending domain outward in its own order.
        if lo > hi {
            let (nice_hi, nice_lo) = d3rs::ticks::d3_nice(hi, lo, 10);
            (nice_lo, nice_hi)
        } else {
            d3rs::ticks::d3_nice(lo, hi, 10)
        }
    } else {
        (lo, hi)
    }
}

/// Vega-Lite's default `tickCount` for a quantitative or temporal axis with no
/// authored count: the signal `ceil(<extent>/40)` over the axis's pixel extent
/// (`compile/axis/properties.ts` `tickCount`; observable as `tickCount:
/// {signal: "ceil(height/40)"}` in `vegalite_to_vega` output, pinned by
/// `test_log_axis_compiles_no_tickcount_signal_unlike_linear`).
pub fn vl_default_tick_count(extent: f64) -> usize {
    (extent / 40.0).ceil() as usize
}

/// The d3 ticks Vega draws on a measure axis with no baked ladder, at
/// `count`, capped by the axis's resolved `tickMinStep` (`ticks.step`,
/// `d3rs::ticks::vega_tick_count`).
pub fn unbaked_ticks(ax: &crate::common::AxisStyle, start: f64, stop: f64, count: usize) -> Vec<f64> {
    d3rs::ticks::d3_ticks(start, stop, d3rs::ticks::vega_tick_count(start, stop, count, ax.ticks.step))
}

/// The rungs of a baked ladder a Vega axis draws over the domain `(a, b)`:
/// `validTicks` (`vega.js:14547-14572`, v6.2.0) drops every `axis.values`
/// entry whose scale position falls outside the range, so a rung past either
/// end of the domain (a ladder rounded outward to cover it) never paints. The
/// bounds may arrive in either order — an authored descending pair inverts
/// the axis but keeps the same rungs — and the ladder keeps its own order.
pub fn ladder_within_domain(ticks: &[f64], a: f64, b: f64) -> Vec<f64> {
    let (lo, hi) = if a <= b { (a, b) } else { (b, a) };
    let eps = (hi - lo) * 1e-9;
    ticks.iter().copied().filter(|&tv| tv >= lo - eps && tv <= hi + eps).collect()
}

/// An end tick closer than this fraction of the domain span to a neighboring tick
/// is dropped: the two labels would collide.
const END_TICK_MIN_GAP: f64 = 0.08;

/// The ticks an authored domain `(a, b)` draws: the scale's own ticks plus a tick at
/// each authored end that is not already one, so a `[20, 1]` rank axis shows both 1
/// and 20. An end within `END_TICK_MIN_GAP` of the span from a neighboring tick stays
/// unlabeled. The result runs in the domain's direction (d3 hands a descending
/// domain's ticks back reversed).
pub fn with_authored_end_ticks(ticks: Vec<f64>, a: f64, b: f64) -> Vec<f64> {
    let span = (b - a).abs();
    let eps = span * 1e-9;
    let mut out = ticks;
    for end in [a, b] {
        let gap = out.iter().map(|t| (t - end).abs()).fold(f64::INFINITY, f64::min);
        if gap > eps && gap >= span * END_TICK_MIN_GAP {
            out.push(end);
        }
    }
    out.sort_by(|x, y| if a <= b { x.total_cmp(y) } else { y.total_cmp(x) });
    out
}

#[cfg(test)]
mod end_tick_tests {
    use super::*;

    #[test]
    fn a_rank_domain_gains_its_missing_end() {
        assert_eq!(with_authored_end_ticks(vec![20.0, 15.0, 10.0, 5.0], 20.0, 1.0), [20.0, 15.0, 10.0, 5.0, 1.0]);
    }

    #[test]
    fn an_end_that_is_already_a_tick_is_not_repeated() {
        assert_eq!(with_authored_end_ticks(vec![0.0, 1000.0, 2000.0, 3000.0, 4000.0], 0.0, 4000.0), [0.0, 1000.0, 2000.0, 3000.0, 4000.0]);
    }

    #[test]
    fn an_end_crowding_a_neighbor_stays_unlabeled() {
        // 490 sits 10 from the 500 tick, 0.3% of the span: a collision, so no tick.
        assert_eq!(with_authored_end_ticks(vec![500.0, 1000.0, 4000.0], 490.0, 4000.0), [500.0, 1000.0, 4000.0]);
        // 20 from 3 is far enough on a span of 19.
        assert_eq!(with_authored_end_ticks(vec![5.0], 1.0, 20.0), [1.0, 5.0, 20.0]);
    }

    #[test]
    fn the_minimum_spacing_is_a_fraction_of_the_span() {
        // Span 100: an end 8 away from its neighbor is the closest that still gets a tick.
        assert_eq!(with_authored_end_ticks(vec![8.0, 50.0, 100.0], 0.0, 100.0), [0.0, 8.0, 50.0, 100.0]);
        assert_eq!(with_authored_end_ticks(vec![7.9, 50.0, 100.0], 0.0, 100.0), [7.9, 50.0, 100.0]);
    }
}

#[cfg(test)]
mod ladder_within_domain_tests {
    use super::*;

    #[test]
    fn drops_rungs_past_either_end_and_keeps_both_endpoints() {
        let ladder = [-500.0, 0.0, 500.0, 1000.0, 1500.0];
        assert_eq!(ladder_within_domain(&ladder, -150.0, 1000.0), [0.0, 500.0, 1000.0]);
        assert_eq!(ladder_within_domain(&ladder, -500.0, 1500.0), ladder);
    }

    #[test]
    fn a_descending_pair_filters_the_same_rungs_in_ladder_order() {
        let ladder = [1000.0, 500.0, 0.0, -500.0];
        assert_eq!(ladder_within_domain(&ladder, 1000.0, -150.0), [1000.0, 500.0, 0.0]);
    }
}

/// Resolve a measure (value) axis's rendered `(min, max)` domain.
///
/// Models Vega's `Scale.js` `configureDomain` order: the zero union happens
/// first (callers pass `raw_data_min`/`raw_data_max` already zero-unioned),
/// then a baked `domain_min`/`domain_max` (production's headroom bake from
/// `compile/resolve/chart/_domain.py` — the ONLY way headroom reaches Vega)
/// rewrites the corresponding domain end, and `nice` runs LAST on the result
/// — `nice: true` passes null to `scale.nice()`, d3's default count 10, and
/// d3's nice loops until the re-derived increment stabilizes
/// (`d3rs::ticks::d3_nice`), which can climb a rung past a single-pass
/// estimate. An unbaked domain therefore never sees a headroom multiplier on
/// the nice path: production's resolver either baked the expanded bound or
/// never applied it.
///
/// `apply_nice == false` is the wired single-series right-axis fit arm,
/// whose compiled scales don't nice a BAKED bound: a baked end wins
/// verbatim, and the zoomed (`ticks.count` unset, `zero: false`) leaf keeps
/// the raw extent — that shape is oracle-pinned as-is (D1 doesn't reach or
/// change it; the raw-extent behavior there predates this change and no
/// law here explains it, only the fully unbaked leaf below does). The
/// fully unbaked leaf (no baked ends, `ticks.count` unset, zero-anchored)
/// nices on this arm exactly as it does everywhere else: D1 — production
/// bakes no `domainMax` there either, so Vega auto-fits and nices the same
/// data extent regardless of which side the axis renders on.
///
/// A flat (zero-span) raw extent on the zoomed leaf defeats `nice()`'s
/// span-preserving math, so that degenerate case falls back to the plain
/// headroom expansion rather than surfacing a hard error for data that
/// rendered fine before this leaf existed.
pub fn resolve_measure_domain(
    raw_data_min: f64,
    raw_data_max: f64,
    baked_domain_min: Option<f64>,
    baked_domain_max: Option<f64>,
    ticks_count: Option<f64>,
    headroom: f64,
    zero_false: bool,
    apply_nice: bool,
) -> (f64, f64) {
    if baked_domain_min.is_some() || baked_domain_max.is_some() {
        // Vega order (Scale.js configureDomain): zero union, then domainMin/
        // domainMax rewrite the domain ends, then `nice` runs LAST on the
        // result — `nice: true` passes null to scale.nice(), d3's default
        // count 10. Callers pass raw_data_min/max already zero-unioned.
        // d3's nice re-derives the increment until it stabilizes (ticks.rs
        // d3_nice), which can climb a rung past the single-pass estimate.
        if apply_nice {
            let lo = baked_domain_min.unwrap_or(raw_data_min);
            let hi = baked_domain_max.unwrap_or(raw_data_max);
            return d3rs::ticks::d3_nice(lo, hi, 10);
        }
        let lo = baked_domain_min.unwrap_or(raw_data_min);
        let hi = baked_domain_max.unwrap_or(raw_data_max * (1.0 + headroom));
        return (lo, hi);
    }
    if ticks_count.is_none() && zero_false {
        if raw_data_max <= raw_data_min {
            return (raw_data_min, raw_data_max * (1.0 + headroom));
        }
        return if apply_nice {
            d3rs::ticks::d3_nice(raw_data_min, raw_data_max, 10)
        } else {
            (raw_data_min, raw_data_max)
        };
    }
    // No baked domain bound reached the payload — production's resolver
    // never applied headroom (headroom only travels as a baked domainMax),
    // so Vega sees the plain data extent niced by d3 (count 10) — on EVERY
    // leaf, including the right-orient one whose compiled scale skips
    // `nice` on the other branches above (D1: a right-orient axis compiles
    // no `scale.nice` override either, so it nices exactly like a left one
    // once nothing is baked). A flat extent defeats nice()'s span math
    // (zero step returns it unchanged), so fall back to the headroom
    // expansion — the same guard the zoomed leaf carries — rather than
    // surfacing a degenerate-domain error for a constant series.
    if raw_data_max > raw_data_min {
        d3rs::ticks::d3_nice(raw_data_min, raw_data_max, 10)
    } else {
        (raw_data_min, raw_data_max * (1.0 + headroom))
    }
}

#[cfg(test)]
mod resolve_measure_domain_tests {
    use super::*;

    #[test]
    fn unauthored_ticks_count_and_zero_false_applies_nice_without_headroom() {
        // VL's default scale.nice is true for an unbinned quantitative field:
        // the raw extent is nice()-rounded, but never headroom-expanded.
        let (lo, hi) = resolve_measure_domain(42.5, 97.3, None, None, None, 0.05, true, true);
        assert_eq!((lo, hi), d3rs::ticks::d3_nice(42.5, 97.3, 10));
        assert_ne!(hi, 97.3 * 1.05, "must not apply headroom on this leaf");
    }

    #[test]
    fn unauthored_ticks_count_and_zero_false_without_nice_uses_raw_extent() {
        let (lo, hi) = resolve_measure_domain(42.5, 97.3, None, None, None, 0.05, true, false);
        assert_eq!((lo, hi), (42.5, 97.3));
    }

    #[test]
    fn unauthored_ticks_count_zero_false_flat_data_falls_back_to_headroom() {
        // Regression: flat positive data (raw_min == raw_max) collapses
        // nice()'s span-preserving math to a no-op. Before resolve_measure_domain
        // existed, headroom alone kept y_max > y_min and the chart rendered a
        // flat line; the raw-extent leaf must not turn that into a hard
        // "domain is degenerate" error.
        let (lo, hi) = resolve_measure_domain(120.0, 120.0, None, None, None, 0.08, true, true);
        assert_eq!(lo, 120.0);
        assert!(hi > lo, "flat data must still get a non-degenerate domain: {hi}");
        assert_eq!(hi, 120.0 * 1.08);
    }

    #[test]
    fn unbaked_domain_never_headroom_expands_on_the_nice_path() {
        // Headroom only reaches Vega as a baked domainMax — when the payload
        // carries no baked bound, production's resolver never applied it, and
        // the scale sees nice(data extent, 10). Oracle: dashes-palette-lab's
        // six_series_six_dashes (unbaked ladder, data max 60): the compiled
        // scale tops at 60 flush, not 60 × 1.08.
        let (_lo, hi) = resolve_measure_domain(0.0, 100.0, None, None, None, 0.05, false, true);
        assert_eq!(hi, 100.0, "nice(0, 100) is already nice — no headroom: {hi}");
        let (_lo, hi) = resolve_measure_domain(0.0, 60.0, None, None, Some(6.0), 0.08, false, true);
        assert_eq!(hi, 60.0);
    }

    #[test]
    fn baked_ends_override_first_then_nice_runs_last() {
        // Vega's Scale.js configureDomain order: zero union → domainMin/Max
        // rewrite the ends → nice (d3 default count 10, looping until the
        // increment stabilizes). Oracle: fixtures/axis-grid's grid_area —
        // baked [14920, 45080] nices to [10000, 50000] (the first pass gives
        // step 2000 → [14000, 46000], the re-derived increment climbs to
        // 5000), so the 50,000 tick draws flush at the plot top.
        let (lo, hi) =
            resolve_measure_domain(20000.0, 43000.0, Some(14920.0), Some(45080.0), Some(6.0), 0.08, true, true);
        assert_eq!((lo, hi), (10000.0, 50000.0));
        // A zero-anchored stacked area (charts/types_3's area_example):
        // domainMax 23900.4 overrides the top, nice rounds it to 24000 —
        // never up to the cover-ladder's 25000, so that rung is culled.
        let (lo, hi) =
            resolve_measure_domain(0.0, 22130.0, None, Some(23900.4), Some(6.0), 0.08, false, true);
        assert_eq!((lo, hi), (0.0, 24000.0));
    }

    #[test]
    fn baked_domain_bounds_always_win_regardless_of_zero_false() {
        // Baked bounds still route through the same nice()/no-nice choice the
        // pre-existing (unbaked) path always used — unchanged from before this
        // fix, per the "don't disturb baked-tick-values paths" invariant.
        let (lo, hi) =
            resolve_measure_domain(10.0, 90.0, Some(5.0), Some(95.0), None, 0.05, true, false);
        assert_eq!((lo, hi), (5.0, 95.0));
    }

    #[test]
    fn authored_ticks_count_keeps_existing_headroom_and_nice_behavior_even_when_zero_false() {
        let (lo, hi) =
            resolve_measure_domain(42.5, 97.3, None, None, Some(6.0), 0.05, true, true);
        assert_ne!((lo, hi), (42.5, 97.3), "ticks_count authored — old headroom/nice path applies");
    }

    #[test]
    fn unbaked_domain_nices_on_the_right_orient_leaf_too() {
        // D1: `_resolve_cartesian_ticks` (`_domain.py:383`) bakes no domain
        // bound when `ticks.count` is unset — production emits no
        // `domainMax`, so Vega auto-fits the (zero-unioned) data extent,
        // niced by d3 (count 10), on EVERY leaf, including the right-orient
        // one whose compiled scale skips `nice` elsewhere (`apply_nice ==
        // false`). Headroom only ever reaches Vega as a baked domainMax, so
        // it must not apply here either. Oracle:
        // `measured-label-alignment-estimated-lab/right_orient_numeric_unbaked`
        // (data max 1.5M, headroom 0.08) — the reference ladder tops at
        // 1.6M, not rust's pre-fix 1.62M.
        let (lo, hi) = resolve_measure_domain(0.0, 1_500_000.0, None, None, None, 0.08, false, false);
        assert_eq!((lo, hi), d3rs::ticks::d3_nice(0.0, 1_500_000.0, 10));
        assert_eq!(hi, 1_600_000.0, "d3 nice(0, 1.5M, 10) tops at 1.6M: {hi}");
        assert_ne!(hi, 1_500_000.0 * 1.08, "must not headroom-expand the unbaked right-orient leaf");
    }
}

/// Production's endpoint-rail label pane measurement, shared by the line
/// and bar rail arms: the widest series name plus the 4px edge gap
/// (`_endpoint_rail._LABEL_GAP_PX`), capped at a third of the chart width
/// (`ENDPOINT_LABEL_MAX_WIDTH_FRACTION`).
///
/// The names are measured at `font_weight` (`series_label.font_weight`, the
/// weight the labels paint at): `measure_label_pane_width` instances the font
/// at it (`_endpoint_rail.py:106`), so a bold name is not under-counted.
pub(crate) fn rail_pane_width(
    series: &[String],
    font_family: &str,
    font_size: f64,
    font_weight: &str,
    fonts: &measure::FontBook,
    chart_w: f64,
) -> Result<f64, String> {
    let widest = series.iter().try_fold(0.0_f64, |acc, s| {
        crate::common::measure_at_css_weight(fonts, s, font_family, font_size, font_weight)
            .map(|w| acc.max(w))
    })?;
    Ok((widest + RAIL_LABEL_GAP).min(rail_cap(chart_w)))
}

/// The four layouts a rail can take, in the order it prefers them. One rail takes one step:
/// every label on it reads at the same step.
const STEP_ONE_LINE: usize = 0;
const STEP_WRAPPED: usize = 1;
const STEP_NAME_ONLY: usize = 2;
const STEP_CUT: usize = 3;

/// How one rail label can be laid out at each step, `None` where the label does not fit that
/// step in width. The cut step always exists.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct RailPlan {
    steps: [Option<Vec<String>>; 4],
    has_value: bool,
    step: usize,
    /// What the label reads uncut: `{value} {name}`, or the name alone.
    full: String,
}

impl RailPlan {
    /// This label's lines at `step`: a step it has no layout for reads as the closest earlier
    /// one it has, so a label that fits on one line stays on one line while its neighbors wrap.
    fn lines_at(&self, step: usize) -> Option<&Vec<String>> {
        match step {
            STEP_WRAPPED => self.steps[STEP_ONE_LINE].as_ref().or(self.steps[STEP_WRAPPED].as_ref()),
            STEP_CUT => self.steps[STEP_ONE_LINE].as_ref().or(self.steps[STEP_WRAPPED].as_ref()).or(self.steps[STEP_CUT].as_ref()),
            _ => self.steps[step].as_ref(),
        }
    }

    /// The lines this label paints.
    pub(crate) fn lines(&self) -> &[String] {
        self.lines_at(self.step).expect("a plan's step is one every label has a layout for")
    }

    /// The pixels this label stands taller than a one-line one.
    pub(crate) fn extra_px(&self, line_height: f64) -> f64 {
        (self.lines().len() - 1) as f64 * line_height
    }

    /// The full text of a label painted cut with an ellipsis, for its hover `<title>`.
    pub(crate) fn cut_full_text(&self) -> Option<&str> {
        self.is_cut().then_some(self.full.as_str())
    }

    /// Whether this label paints its name cut with an ellipsis.
    fn is_cut(&self) -> bool {
        self.step == STEP_CUT && self.steps[STEP_ONE_LINE].is_none() && self.steps[STEP_WRAPPED].is_none()
    }
}

/// The first step every label on the rail has a layout for.
fn first_viable_step(plans: &HashMap<String, RailPlan>) -> usize {
    (STEP_ONE_LINE..STEP_CUT).find(|&step| plans.values().all(|plan| plan.lines_at(step).is_some())).unwrap_or(STEP_CUT)
}

/// Every line the rail may paint: those of its first viable step and, because the rail may
/// move on when the stack has no room, of each later one. A rail that fits on one line never
/// moves, so it reserves just that.
pub(crate) fn rail_reserved_lines(plans: &HashMap<String, RailPlan>) -> Vec<String> {
    let first = first_viable_step(plans);
    let last = if first == STEP_ONE_LINE { STEP_ONE_LINE } else { STEP_CUT };
    (first..=last)
        .filter(|&step| plans.values().all(|plan| plan.lines_at(step).is_some()))
        .flat_map(|step| plans.values().flat_map(move |plan| plan.lines_at(step).expect("checked above").clone()))
        .collect()
}

/// One rail label as one text mark: a wrapped label's lines are `<tspan>`s that step a line
/// height down from the first (`attrs` place it), and a label cut with an ellipsis carries its
/// full text in a hover `<title>`.
pub(crate) fn rail_label_node(at: (f64, f64), attrs: Attrs, plan: &RailPlan, line_height: f64) -> Node {
    lines_label_node(Role::Label, at, attrs, plan.lines(), plan.cut_full_text(), 0.0, line_height)
}

/// A label of one or more lines as one text mark: each line after the first is a `<tspan>` at `x`,
/// a line height below the one before, and a cut label carries its `full` text in a hover `<title>`.
pub(crate) fn lines_label_node(role: Role, at: (f64, f64), attrs: Attrs, lines: &[String], full: Option<&str>, x: f64, line_height: f64) -> Node {
    // A line an expression returned as several (`measure::LINE_SEP`) is several here too.
    let lines: Vec<&str> = lines.iter().flat_map(|line| line.split(measure::LINE_SEP)).collect();
    match lines.as_slice() {
        [line] => crate::families::legend::text_revealing_full_name(role, at, attrs, line, full),
        lines => {
            let mut runs: Vec<Run> = lines
                .iter()
                .enumerate()
                .map(|(i, line)| Run::Tspan {
                    x: (i > 0).then_some(x),
                    y: None,
                    dx: None,
                    dy: (i > 0).then_some(line_height),
                    attrs: Attrs::new(),
                    runs: vec![Run::Chars(Chars::new(*line, Escape::Attr))],
                })
                .collect();
            runs.extend(full.map(|full| Run::Title(Chars::new(full, Escape::Attr))));
            Node::new(role, attrs, Shape::Text { at, bounds: None, runs })
        }
    }
}

/// The height of one rail text line (Vega's default `lineHeight`: font size + 2).
pub(crate) fn rail_line_height(font_size: f64) -> f64 {
    font_size + 2.0
}

/// The widest prefix of `words` (at least one when it fits) that fits `first` within the cap;
/// returns the grown line and how many words it took.
fn fill_line(first: String, words: &[&str], fits: &impl Fn(&str) -> Result<bool, String>) -> Result<(String, usize), String> {
    let (mut line, mut taken) = (first, 0);
    for word in words {
        let grown = if line.is_empty() { word.to_string() } else { format!("{line} {word}") };
        if !fits(&grown)? {
            break;
        }
        line = grown;
        taken += 1;
    }
    Ok((line, taken))
}

/// The one wrap law of a label that outgrows its cap: the name on one line, else wrapped at a
/// word boundary onto two, else (when `may_cut`) cut with an ellipsis at the end of line two, or
/// of line one when no word fits it. `None` when it fits neither and may not be cut.
pub(crate) fn two_line_label(
    name: &str,
    fits: &impl Fn(&str) -> Result<bool, String>,
    cut: &impl Fn(&str) -> Result<String, String>,
    may_cut: bool,
) -> Result<Option<Vec<String>>, String> {
    if fits(name)? {
        return Ok(Some(vec![name.to_string()]));
    }
    let words: Vec<&str> = name.split(' ').collect();
    let (first, taken) = fill_line(String::new(), &words, fits)?;
    if taken == 0 {
        return may_cut.then(|| cut(name).map(|cut| vec![cut])).transpose();
    }
    let rest = words[taken..].join(" ");
    if fits(&rest)? {
        return Ok(Some(vec![first, rest]));
    }
    if !may_cut {
        return Ok(None);
    }
    Ok(Some(vec![first, cut(&rest)?]))
}

/// The layouts one rail label can take. The value, when there is one, leads: `{value} {name}`
/// on one line within the value cap; else the name wraps at a word boundary onto a second
/// line; else the value is set aside and the name alone wraps within the rail cap; and a name
/// that fits none of these is cut with an ellipsis at the end of its second line.
pub(crate) fn plan_rail_label(
    value: Option<&str>,
    name: &str,
    chart_w: f64,
    font: (&str, f64, &str),
    fonts: &measure::FontBook,
) -> Result<RailPlan, String> {
    let (family, size, weight) = font;
    let measured = |text: &str| crate::common::measure_at_css_weight(fonts, text, family, size, weight);
    let fits_rail = |text: &str| Ok(measured(text)? <= rail_cap(chart_w));
    let words: Vec<&str> = name.split(' ').collect();
    let name_alone = |may_cut: bool| {
        let cut = |text: &str| crate::common::truncate_label(text, rail_cap(chart_w), family, size, fonts, false);
        two_line_label(name, &fits_rail, &cut, may_cut)
    };
    let Some(value) = value else {
        let whole = name_alone(false)?;
        let cut = name_alone(true)?.expect("a cut layout always exists");
        return Ok(RailPlan { steps: [whole.clone().filter(|l| l.len() == 1), whole.clone().filter(|l| l.len() == 2), whole, Some(cut)], has_value: false, step: STEP_ONE_LINE, full: name.to_string() });
    };
    let cap = rail_value_cap(chart_w);
    let fits_value = |text: &str| Ok(measured(text)? <= cap);
    let one_line = format!("{value} {name}");
    let one = fits_value(&one_line)?.then(|| vec![one_line]);
    let (first, taken) = fill_line(value.to_string(), &words, &fits_value)?;
    let rest = words[taken..].join(" ");
    let wrapped = (!rest.is_empty() && fits_value(&first)? && fits_value(&rest)?).then(|| vec![first.clone(), rest.clone()]);
    let cut = vec![first, crate::common::truncate_label(&rest, cap, family, size, fonts, false)?];
    Ok(RailPlan { steps: [one, wrapped, name_alone(false)?, Some(cut)], has_value: true, step: STEP_ONE_LINE, full: format!("{value} {name}") })
}

/// A rail's labels, each with the layouts it can take. Each entry is `(key, value, name)`.
pub(crate) fn plan_rail_labels(
    entries: &[(String, Option<String>, String)],
    chart_w: f64,
    font: (&str, f64, &str),
    fonts: &measure::FontBook,
) -> Result<HashMap<String, RailPlan>, String> {
    entries
        .iter()
        .map(|(key, value, name)| Ok((key.clone(), plan_rail_label(value.as_deref(), name, chart_w, font, fonts)?)))
        .collect()
}

/// The warnings a rail's final layout earns: `WARN-RAIL-VALUE-DROPPED` naming the series whose
/// fit forced the values off, and `WARN-SERIES-LABEL-TRUNCATED` for each name cut with an
/// ellipsis.
pub(crate) fn warn_rail_plan(
    chart_id: &str,
    authored_field: &str,
    keys: &[String],
    name_of: impl Fn(&str) -> String,
    plans: &HashMap<String, RailPlan>,
    forcing: &[String],
) {
    let cut: Vec<String> = keys.iter().filter(|key| plans[*key].is_cut()).map(|key| name_of(key)).collect();
    warn_rail_labels_cut(chart_id, authored_field, &cut);
    let forcing_names: Vec<String> = forcing.iter().map(|key| name_of(key)).collect();
    warn_rail_values_dropped(chart_id, authored_field, &forcing_names);
}

/// `WARN-RAIL-VALUE-DROPPED`: the series whose rail label lost its value because neither one
/// line nor two fit the rail.
pub(crate) fn warn_rail_values_dropped(chart_id: &str, authored_field: &str, names: &[String]) {
    if names.is_empty() {
        return;
    }
    let many = names.len() != 1;
    let quoted: Vec<String> = names.iter().map(|n| diagnostic::py_repr(&json!(n))).collect();
    let labels = match quoted.split_at_checked(3) {
        Some((head, rest)) if !rest.is_empty() => format!("{} and {} more", head.join(", "), rest.len()),
        _ => quoted.join(", "),
    };
    diagnostic::warn(
        Diagnostic::new(
            &codes::WARN_RAIL_VALUE_DROPPED,
            [
                ("authored_field", json!(authored_field)),
                ("chart_id", json!(chart_id)),
                ("count", json!(names.len())),
                ("labels", json!(labels)),
                ("labels_noun", json!(if many { "labels" } else { "label" })),
                ("were", json!(if many { "were" } else { "was" })),
            ],
        )
        .with_sink_param("series_names", json!(names)),
    );
}

/// A rail label's number: the series' own final datum through the y axis's tick-label
/// law (`format_y_label`: ruler, then `tick_label`, then `labels.format`, then the
/// default), as the sole tick of its axis, so an anchored affix such as a currency
/// symbol always shows.
pub(crate) fn rail_value_text(value: f64, ax_y: &crate::common::AxisStyle) -> Result<String, String> {
    crate::common::format_y_label(value, ax_y.ruler.as_ref(), ax_y.tick_label.as_ref(), ax_y.labels.format.as_ref(), 0, 1)
}

pub(crate) const RAIL_LABEL_GAP: f64 = 4.0;

/// The widest a label carrying a value may be, so the plot keeps its
/// width on a narrow card. Past it the label wraps, then loses its value.
fn rail_value_cap(chart_w: f64) -> f64 {
    chart_w * crate::constants::marks::ENDPOINT_LABEL_VALUE_MAX_WIDTH_FRACTION
}

/// The widest a rail may grow: `endpoint_labels.max_width_fraction` of the chart.
fn rail_cap(chart_w: f64) -> f64 {
    chart_w * crate::constants::marks::ENDPOINT_LABEL_MAX_WIDTH_FRACTION
}

/// `WARN-AXIS-LABEL-COLLISION`: every enabled skip/tilt strategy was tried
/// and the `label_count` labels measured still collide on the x axis.
pub(crate) fn warn_axis_label_collision(chart_id: &str, field: &str, label_count: usize) {
    diagnostic::warn(Diagnostic::new(
        &codes::WARN_AXIS_LABEL_COLLISION,
        [("chart_id", json!(chart_id)), ("field", json!(field)), ("label_count", json!(label_count))],
    ));
}

/// `WARN-LAYER-X-DOMAIN-PAINT-ORDER`: a layer contributed `layer_only` x values
/// the base states no order to place, so the shared domain stays in paint order.
pub(crate) fn warn_x_domain_paint_order(chart_id: &str, x_field: &str, layer_only: &[serde_json::Value]) {
    const SAMPLE: usize = 3;
    let mut sample: Vec<String> = layer_only.iter().take(SAMPLE).map(diagnostic::py_repr).collect();
    if layer_only.len() > SAMPLE {
        sample.push("…".to_string());
    }
    diagnostic::warn(
        Diagnostic::new(
            &codes::WARN_LAYER_X_DOMAIN_PAINT_ORDER,
            [
                ("chart_id", json!(chart_id)),
                ("n_new", json!(layer_only.len())),
                ("plural", json!(if layer_only.len() == 1 { "y" } else { "ies" })),
                ("sample", json!(sample.join(", "))),
                ("x_field", json!(x_field)),
            ],
        )
        .with_sink_param("layer_only", json!(layer_only)),
    );
}

/// `WARN-SERIES-LABEL-TRUNCATED` for the rail names cut with an ellipsis.
pub(crate) fn warn_rail_labels_cut(chart_id: &str, authored_field: &str, series: &[String]) {
    if series.is_empty() {
        return;
    }
    // Production lists the cut names in sorted order.
    let mut cut: Vec<&str> = series.iter().map(String::as_str).collect();
    cut.sort_unstable();
    let many = cut.len() != 1;
    let quoted: Vec<String> = cut.iter().map(|n| diagnostic::py_repr(&json!(n))).collect();
    let labels = match quoted.split_at_checked(3) {
        Some((head, rest)) if !rest.is_empty() => format!("{} and {} more", head.join(", "), rest.len()),
        _ => quoted.join(", "),
    };
    diagnostic::warn(
        Diagnostic::new(
            &codes::WARN_SERIES_LABEL_TRUNCATED,
            [
                ("authored_field", json!(authored_field)),
                ("chart_id", json!(chart_id)),
                ("count", json!(cut.len())),
                ("labels", json!(labels)),
                ("labels_noun", json!(if many { "labels" } else { "label" })),
                ("were", json!(if many { "were" } else { "was" })),
            ],
        )
        .with_sink_param("series_names", json!(cut)),
    );
}

/// Bidirectional greedy collision-avoidance over a set of rail anchors, in
/// data-space y units. Mirrors production's `_apply_label_cascade`
/// (`render/chart/features/endpoint_labels.py`): every anchor is
/// clamped into the caller's y-domain first — an anchor outside
/// `[y_domain_min, y_domain_max]` is off-domain, not a collision — then the
/// branch is chosen by comparing the anchor mean to the *domain* midpoint
/// (not the anchor span's own midpoint), and packed anchors are pushed no
/// closer than `min_gap` to the previously *placed* anchor. Returns the placed
/// labels and the names it dropped.
pub(crate) fn cascade_rail_anchors(
    anchors: &[(String, f64)],
    min_gap: f64,
    extra: &HashMap<String, f64>,
    y_domain_min: f64,
    y_domain_max: f64,
) -> (Vec<(String, f64)>, Vec<String>) {
    let clamped: Vec<(String, f64)> = anchors
        .iter()
        .map(|(name, v)| (name.clone(), v.clamp(y_domain_min, y_domain_max)))
        .collect();
    if clamped.len() <= 1 || min_gap <= 0.0 {
        return (clamped, Vec::new());
    }
    let domain_mid = (y_domain_min + y_domain_max) / 2.0;
    let mean = clamped.iter().map(|(_, v)| *v).sum::<f64>() / clamped.len() as f64;
    // Two neighbors stand `min_gap` apart plus half of what each is taller than one line.
    let gap = |a: &str, b: &str| min_gap + (extra[a] + extra[b]) / 2.0;

    // Anchors arrive in paint order: among equals the top-painted series takes the top label.
    let mut ascending = clamped.clone();
    ascending.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
    let mut descending = clamped.clone();
    descending.reverse();
    descending.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let down = pack_against_bound(descending, |anchor, prev, prev_name, name| anchor.min(prev - gap(prev_name, name)), |y| y < y_domain_min);
    let up = pack_against_bound(ascending, |anchor, prev, prev_name, name| anchor.max(prev + gap(prev_name, name)), |y| y > y_domain_max);
    // The side that keeps the most names nearest their marks; the emptier half of the domain breaks a tie.
    let cost = |(placed, dropped): &(Vec<(String, f64)>, Vec<String>)| {
        let moved: f64 = placed.iter().map(|(name, y)| (y - clamped.iter().find(|(n, _)| n == name).expect("a placed anchor").1).abs()).sum();
        (dropped.len(), moved)
    };
    let (down_cost, up_cost) = (cost(&down), cost(&up));
    let up_wins = up_cost.0 < down_cost.0 || (up_cost.0 == down_cost.0 && up_cost.1 < down_cost.1 - 1e-9);
    let down_wins = down_cost.0 < up_cost.0 || (up_cost.0 == down_cost.0 && down_cost.1 < up_cost.1 - 1e-9);
    if up_wins || (!down_wins && mean < domain_mid) { up } else { down }
}

/// `_pack_against_bound`: greedy-pack extremity-first `items` against a bound.
/// An item that would cross the bound evicts placed items off the top of the
/// stack, but only ones that were themselves pushed off their own anchor, so a
/// label with real room is never dropped for a neighbor's crowding.
fn pack_against_bound(
    items: Vec<(String, f64)>,
    push: impl Fn(f64, f64, &str, &str) -> f64,
    overflows: impl Fn(f64) -> bool,
) -> (Vec<(String, f64)>, Vec<String>) {
    let mut stack: Vec<(String, f64, f64)> = Vec::new(); // (name, forced_y, own_anchor)
    let mut dropped = Vec::new();
    for (name, anchor) in items {
        let forced = |stack: &[(String, f64, f64)]| {
            stack.last().map_or(anchor, |top| push(anchor, top.1, &top.0, &name))
        };
        let mut y = forced(&stack);
        while overflows(y) && stack.last().is_some_and(|top| top.1 != top.2) {
            dropped.push(stack.pop().expect("checked non-empty").0);
            y = forced(&stack);
        }
        if overflows(y) {
            dropped.push(name);
            continue;
        }
        stack.push((name, y, anchor));
    }
    (stack.into_iter().map(|(name, y, _)| (name, y)).collect(), dropped)
}

/// `_distribute_evenly`: the labels spread evenly across the domain in anchor
/// order, top first. Needs no slope.
fn spread_evenly(anchors: &[(String, f64)], y_domain_min: f64, y_domain_max: f64) -> Vec<(String, f64)> {
    let step = (y_domain_max - y_domain_min) / (anchors.len() - 1) as f64;
    let mut ordered: Vec<&(String, f64)> = anchors.iter().collect();
    ordered.sort_by(|a, b| a.1.partial_cmp(&b.1).unwrap());
    let mut placed: Vec<(String, f64)> =
        ordered.iter().enumerate().map(|(i, (name, _))| (name.clone(), y_domain_min + i as f64 * step)).collect();
    placed.reverse();
    placed
}

/// Extra heights for labels that are all one line.
pub(crate) fn one_line_extras(anchors: &[(String, f64)]) -> HashMap<String, f64> {
    anchors.iter().map(|(name, _)| (name.clone(), 0.0)).collect()
}

/// How a rail's labels stack in a pane: the one reading of the pixel gap, the labels' extra
/// heights and the domain behind both `recascade_rail_anchors` and the cascade's room test.
enum RailRoom {
    /// The y scale has no slope, or there is no neighbor to clear.
    Unsloped,
    /// The gaps alone exceed the domain.
    Overflow,
    /// The cascade's placement and the labels it found no room for.
    Packed(Vec<(String, f64)>, Vec<String>),
}

fn rail_room(
    anchors: &[(String, f64)],
    pixel_gap: f64,
    extra_px: &HashMap<String, f64>,
    px_per_unit: Option<f64>,
    y_domain_min: f64,
    y_domain_max: f64,
) -> Result<RailRoom, String> {
    if let Some((name, _)) = anchors.iter().find(|(name, _)| !extra_px.contains_key(name)) {
        return Err(format!("rail label {name:?} has no layout plan"));
    }
    let Some(px_per_unit) = px_per_unit.filter(|_| y_domain_max > y_domain_min && anchors.len() > 1) else {
        return Ok(RailRoom::Unsloped);
    };
    let min_data_gap = pixel_gap / px_per_unit;
    let extra: HashMap<String, f64> = anchors.iter().map(|(name, _)| (name.clone(), extra_px[name] / px_per_unit)).collect();
    if (anchors.len() - 1) as f64 * min_data_gap + extra.values().sum::<f64>() > y_domain_max - y_domain_min {
        return Ok(RailRoom::Overflow);
    }
    let (placed, dropped) = cascade_rail_anchors(anchors, min_data_gap, &extra, y_domain_min, y_domain_max);
    Ok(RailRoom::Packed(placed, dropped))
}

/// `recascade_endpoint_labels`: the rail's final label positions for an
/// intended `pixel_gap`, converted to data units through `px_per_unit` (`None`
/// when the y scale has no slope). A gap that cannot be honored spreads the
/// labels evenly across the domain, and a label that finds no room is
/// dropped; each is reported as the warning production records.
pub(crate) fn recascade_rail_anchors(
    chart_id: &str,
    anchors: &[(String, f64)],
    pixel_gap: f64,
    extra_px: &HashMap<String, f64>,
    px_per_unit: Option<f64>,
    y_domain_min: f64,
    y_domain_max: f64,
) -> Result<Vec<(String, f64)>, String> {
    let n = anchors.len();
    if n <= 1 {
        return Ok(cascade_rail_anchors(anchors, 0.0, &one_line_extras(anchors), y_domain_min, y_domain_max).0);
    }
    match rail_room(anchors, pixel_gap, extra_px, px_per_unit, y_domain_min, y_domain_max)? {
        // Tied anchors spread across a cascade domain with no span still tie, so no
        // slope is measurable from them either.
        RailRoom::Unsloped => {
            diagnostic::warn(
                Diagnostic::new(&codes::WARN_ENDPOINT_LABEL_RAIL_TIED, [("chart_id", json!(chart_id)), ("series_count", json!(n))])
                    .with_sink_param("gap_px", json!(pixel_gap)),
            );
            Ok(spread_evenly(anchors, y_domain_min, y_domain_max))
        }
        RailRoom::Overflow => {
            diagnostic::warn(Diagnostic::new(
                &codes::WARN_ENDPOINT_LABEL_GAP_OVERFLOW,
                [("chart_id", json!(chart_id)), ("gap_px", json!(pixel_gap)), ("series_count", json!(n))],
            ));
            Ok(spread_evenly(anchors, y_domain_min, y_domain_max))
        }
        RailRoom::Packed(placed, dropped) => {
            if !dropped.is_empty() {
                diagnostic::warn(
                    Diagnostic::new(
                        &codes::WARN_ENDPOINT_LABEL_RAIL_OVERFLOW,
                        [
                            ("chart_id", json!(chart_id)),
                            ("dropped_count", json!(dropped.len())),
                            ("gap_px", json!(pixel_gap)),
                            ("series_count", json!(n)),
                        ],
                    )
                    .with_sink_param("dropped_series", json!(dropped)),
                );
            }
            Ok(placed)
        }
    }
}

/// The rail's final label positions and the one layout step they read at: the first step every
/// label fits in width, moved on to the next that the stack has room for, as the cascade has
/// no room for two-line labels. Dropping values helps only if the shorter layout then fits;
/// else the rail keeps what the width allows. Returns the series whose fit forced the values
/// off, for the warning.
pub(crate) fn place_rail_labels(
    chart_id: &str,
    anchors: &[(String, f64)],
    plans: &HashMap<String, RailPlan>,
    pixel_gap: f64,
    line_height: f64,
    px_per_unit: Option<f64>,
    bounds: (f64, f64),
) -> Result<(Vec<(String, f64)>, HashMap<String, RailPlan>, Vec<String>), String> {
    let mut plans = plans.clone();
    let extras_at = |plans: &HashMap<String, RailPlan>, step: usize| -> Option<HashMap<String, f64>> {
        plans
            .iter()
            .map(|(name, plan)| plan.lines_at(step).map(|lines| (name.clone(), (lines.len() - 1) as f64 * line_height)))
            .collect()
    };
    let fits = |extra_px: &HashMap<String, f64>| -> Result<bool, String> {
        Ok(match rail_room(anchors, pixel_gap, extra_px, px_per_unit, bounds.0, bounds.1)? {
            RailRoom::Unsloped => true,
            RailRoom::Overflow => false,
            RailRoom::Packed(_, lost) => lost.is_empty(),
        })
    };
    let first = first_viable_step(&plans);
    let mut chosen = first;
    if first != STEP_ONE_LINE {
        for step in first..=STEP_CUT {
            if let Some(extras) = extras_at(&plans, step) {
                if fits(&extras)? {
                    chosen = step;
                    break;
                }
            }
        }
    }
    plans.values_mut().for_each(|plan| plan.step = chosen);
    let mut forcing = Vec::new();
    if chosen == STEP_NAME_ONLY {
        let width_forced = |plan: &RailPlan| plan.has_value && plan.lines_at(STEP_WRAPPED).is_none();
        forcing = anchors.iter().filter(|(name, _)| width_forced(&plans[name])).map(|(name, _)| name.clone()).collect();
        if forcing.is_empty() {
            let wrapped = |plan: &RailPlan| plan.has_value && plan.lines_at(STEP_WRAPPED).is_some_and(|lines| lines.len() > 1);
            forcing.extend(anchors.iter().find(|(name, _)| wrapped(&plans[name])).map(|(name, _)| name.clone()));
        }
    }
    let extras = extras_at(&plans, chosen).expect("the chosen step is one every label has a layout for");
    let placed = recascade_rail_anchors(chart_id, anchors, pixel_gap, &extras, px_per_unit, bounds.0, bounds.1)?;
    Ok((placed, plans, forcing))
}

#[cfg(test)]
mod rail_cascade_tests {
    use super::{place_rail_labels, rail_reserved_lines, RailPlan, STEP_ONE_LINE};
    use std::collections::HashMap;

    type Steps<'a> = [Option<&'a [&'a str]>; 4];

    fn plan(steps: Steps) -> RailPlan {
        let owned = steps.map(|lines| lines.map(|l| l.iter().map(|s| s.to_string()).collect()));
        RailPlan { steps: owned, has_value: true, step: STEP_ONE_LINE, full: "50 a name".into() }
    }

    /// Series `a` and `b`, 5 data units apart on a 1px/unit scale, line height 13, gap 8.
    fn place(a: Steps, bounds: (f64, f64)) -> (HashMap<String, RailPlan>, Vec<String>) {
        let b: Steps = [Some(&["60 b"]), Some(&["60 b"]), Some(&["b"]), Some(&["60 b"])];
        let plans = HashMap::from([("a".to_string(), plan(a)), ("b".to_string(), plan(b))]);
        let anchors = vec![("a".to_string(), 50.0), ("b".to_string(), 55.0)];
        let (_, plans, forcing) = place_rail_labels("c1", &anchors, &plans, 8.0, 13.0, Some(1.0), bounds).unwrap();
        (plans, forcing)
    }

    fn lines(plans: &HashMap<String, RailPlan>, key: &str) -> Vec<String> {
        plans[key].lines().to_vec()
    }

    const FITS_ONE: Steps = [Some(&["50 a"]), Some(&["50 a"]), Some(&["a"]), Some(&["50 a"])];
    const FITS_WRAPPED: Steps = [None, Some(&["50", "a name"]), Some(&["a", "name"]), Some(&["50", "a name"])];
    const NAME_FITS_ONE_LINE: Steps = [None, Some(&["50", "a name"]), Some(&["a"]), Some(&["50", "a name"])];
    const FITS_NAME_ONLY: Steps = [None, None, Some(&["a", "name"]), Some(&["50", "a n\u{2026}"])];
    const FITS_CUT_ONLY: Steps = [None, None, None, Some(&["50", "a n\u{2026}"])];

    #[test]
    fn step_one_a_rail_that_fits_on_one_line_keeps_every_value() {
        let (plans, forcing) = place(FITS_ONE, (0.0, 200.0));
        assert_eq!((lines(&plans, "a"), lines(&plans, "b")), (vec!["50 a".into()], vec!["60 b".into()]));
        assert!(forcing.is_empty());
    }

    #[test]
    fn step_two_wrapped_values_stay_while_the_stack_has_room_and_neighbors_keep_one_line() {
        let (plans, forcing) = place(FITS_WRAPPED, (0.0, 200.0));
        assert_eq!((lines(&plans, "a"), lines(&plans, "b")), (vec!["50".into(), "a name".into()], vec!["60 b".into()]));
        assert!(forcing.is_empty());
    }

    #[test]
    fn step_three_by_vertical_room_every_label_drops_its_value_and_the_wrapped_one_is_named() {
        // 14px fits two one-line labels (8px apart) but not the taller pair (14.5px apart).
        let (plans, forcing) = place(NAME_FITS_ONE_LINE, (0.0, 14.0));
        assert_eq!(forcing, vec!["a".to_string()]);
        assert_eq!((lines(&plans, "a"), lines(&plans, "b")), (vec!["a".into()], vec!["b".into()]));
    }

    #[test]
    fn step_three_by_width_drops_every_value_and_names_the_label_that_forced_it() {
        let (plans, forcing) = place(FITS_NAME_ONLY, (0.0, 200.0));
        assert_eq!(forcing, vec!["a".to_string()]);
        assert_eq!((lines(&plans, "a"), lines(&plans, "b")), (vec!["a".into(), "name".into()], vec!["b".into()]));
        assert!(!plans["a"].is_cut());
    }

    #[test]
    fn step_four_when_dropping_values_would_still_cut_the_values_stay_and_line_two_is_cut() {
        let (plans, forcing) = place(FITS_CUT_ONLY, (0.0, 200.0));
        assert!(forcing.is_empty(), "no value drop when it would not prevent the cut");
        assert_eq!((lines(&plans, "a"), lines(&plans, "b")), (vec!["50".into(), "a n\u{2026}".into()], vec!["60 b".into()]));
        assert!(plans["a"].is_cut() && !plans["b"].is_cut());
    }

    #[test]
    fn a_rail_too_cramped_for_the_name_only_layout_keeps_its_values() {
        // Two-line names stand 14.5px apart; 5px of room fits nothing, so the width answer stands.
        let (plans, forcing) = place(FITS_WRAPPED, (0.0, 5.0));
        assert!(forcing.is_empty());
        assert_eq!(lines(&plans, "a"), vec!["50".to_string(), "a name".to_string()]);
    }

    #[test]
    fn a_late_move_paints_only_lines_the_pane_reserved() {
        let plans = HashMap::from([("a".to_string(), plan(NAME_FITS_ONE_LINE))]);
        let reserved = rail_reserved_lines(&plans);
        let (placed, _) = place(NAME_FITS_ONE_LINE, (0.0, 14.0));
        assert!(lines(&placed, "a").iter().all(|line| reserved.contains(line)), "{reserved:?}");
    }

    #[test]
    fn a_rail_that_fits_on_one_line_reserves_nothing_more() {
        let plans = HashMap::from([("a".to_string(), plan(FITS_ONE))]);
        assert_eq!(rail_reserved_lines(&plans), vec!["50 a".to_string()]);
    }
}

#[cfg(test)]
mod recascade_tests {
    use super::recascade_rail_anchors;
    use diagnostic::collect;
    use serde_json::json;

    #[test]
    fn a_rail_on_a_scale_with_no_slope_spreads_evenly_and_reports_it() {
        let anchors = vec![("a".to_string(), 5.0), ("b".to_string(), 5.0), ("c".to_string(), 5.0)];
        let (placed, warnings) = collect(|| recascade_rail_anchors("c1", &anchors, 18.2, &super::one_line_extras(&anchors), None, 0.0, 10.0).unwrap());
        assert_eq!(placed, vec![("c".into(), 10.0), ("b".into(), 5.0), ("a".into(), 0.0)]);
        let [tied] = warnings.as_slice() else { panic!("{warnings:?}") };
        assert_eq!(tied.code, "WARN-ENDPOINT-LABEL-RAIL-TIED");
        assert_eq!(
            serde_json::to_value(&tied.params).unwrap(),
            json!({"chart_id": "c1", "series_count": 3, "gap_px": 18.2})
        );
    }
}

#[cfg(test)]
mod rail_weight_tests {
    use super::rail_pane_width;
    use measure::FontBook;

    fn fonts() -> FontBook {
        FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load")
    }

    fn pane(weight: &str) -> f64 {
        let names = ["Northeast Territory Revenue".to_string()];
        rail_pane_width(&names, "'Inter Variable', Inter", 14.0, weight, &fonts(), 2000.0).unwrap()
    }

    #[test]
    fn the_rail_widens_with_the_weight_the_labels_paint_at() {
        let (regular, bold) = (pane("400"), pane("700"));
        assert!(bold - regular > 2.0, "{regular} vs {bold}");
    }

    #[test]
    fn an_unparseable_series_label_weight_leaves_the_rail_at_the_regular_width() {
        assert_eq!(pane("semibold"), pane("400"));
    }
}

#[cfg(test)]
mod lines_label_tests {
    use super::*;

    /// The rail's wrapped label keeps its anchor and a line-height step at `x = 0`: the bar
    /// category axis shares the builder but places its own first baseline.
    #[test]
    fn a_two_line_rail_label_steps_a_line_height_from_the_given_baseline() {
        let lines = ["105 Region 03".to_string(), "Growth Segment".to_string()];
        let node = lines_label_node(Role::Label, (0.0, 10.0), Attrs::new(), &lines, None, 0.0, 15.4);
        let svg = print::print(&[node]);
        assert_eq!(
            svg,
            "<text x=\"0\" y=\"10\"><tspan>105 Region 03</tspan><tspan x=\"0\" dy=\"15.4\">Growth Segment</tspan></text>"
        );
    }
}

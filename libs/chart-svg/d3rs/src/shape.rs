//! d3-shape line and area path generators.
//!
//! Ported from:
//! - https://github.com/d3/d3-shape/blob/main/src/line.js
//! - https://github.com/d3/d3-shape/blob/main/src/area.js
//! - https://github.com/d3/d3-shape/blob/main/src/curve/linear.js
//! - https://github.com/d3/d3-shape/blob/main/src/curve/monotone.js
//! - https://github.com/d3/d3-shape/blob/main/src/curve/step.js

use crate::path::{Path, WITH_PATH_DIGITS};

/// The curve a line/area mark interpolates with — production compiles
/// `line_mark.curve` / `area_mark.curve` to VL's `interpolate`, which Vega
/// hands straight to the d3-shape curve of the same name. `Step`/`StepBefore`/
/// `StepAfter` are d3's one `Step(context, t)` generator at `t = 0.5 / 0 / 1`
/// — the riser's fractional position within each segment.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Curve {
    Linear,
    MonotoneX,
    Step,
    StepBefore,
    StepAfter,
}

impl Curve {
    /// d3's `Step.lineEnd` ends every edge with
    /// `if (this._line >= 0) this._t = 1 - this._t, this._line = 1 - this._line;`
    /// — so an area's SECOND edge (the bottom, drawn through the same curve
    /// instance right after the top's own `lineEnd` toggles it) runs with
    /// `t' = 1 - t`: `step-after`'s bottom edge is `step-before` and vice
    /// versa, and centered `step` toggles to itself. `Linear`/`MonotoneX`
    /// have no `_t` and are unaffected.
    fn toggled_for_area_bottom(self) -> Curve {
        match self {
            Curve::StepBefore => Curve::StepAfter,
            Curve::StepAfter => Curve::StepBefore,
            other => other,
        }
    }
}

/// d3's `_line` flag, which decides how an edge opens and whether it closes.
///
/// A bare line generator never sets it (`NaN`), so only a single-point run
/// closes; an area's `areaStart` sets it to 0 for the top edge and its
/// `lineEnd` flips it to 1 for the bottom edge, which therefore continues the
/// top edge with an `L` instead of an `M` and closes the whole subpath.
#[derive(Clone, Copy, PartialEq, Eq)]
enum Edge {
    Bare,
    AreaTop,
    AreaBottom,
}

/// Build an SVG path string for the line through each RUN of defined points,
/// interpolated with `curve`.
///
/// d3-shape's `line.defined` splits the data at every datum the predicate
/// rejects and emits one `M…` subpath per surviving run — Vega-Lite gives
/// every line mark the default predicate `isValid(x) && isFinite(+x) &&
/// isValid(y) && isFinite(+y)`, so a null measure ends the current subpath
/// instead of drawing across the hole. Each run is curved on its own, as d3
/// does.
///
/// A run of exactly ONE point is closed: `lineEnd` is
/// `if (this._line || (this._line !== 0 && this._point === 1)) closePath()`,
/// and a plain line generator leaves `_line` at `NaN`, so `_point === 1`
/// decides it. That `Z` is load-bearing — under the mark's round cap a
/// zero-length CLOSED subpath paints a dot where a lone `moveto` paints
/// nothing (Vega's own SVG for three isolated points:
/// `M40,133.333ZM200,133.333ZM360,133.333Z`). A longer run stays open; an
/// empty run draws nothing, and so does empty input.
pub fn line_path(runs: &[Vec<(f64, f64)>], curve: Curve) -> String {
    let mut path = Path::rounded(WITH_PATH_DIGITS);
    for run in runs {
        append_edge(&mut path, run, curve, Edge::Bare);
    }
    path.into_string()
}

/// Build an SVG path string for a filled area under `curve`, flat bottom at
/// `bottom_y` — one CLOSED subpath per run, split by the same `defined`
/// predicate [`line_path`] honors.
///
/// Each run walks its top edge left-to-right, then its bottom edge back
/// right-to-left, then closes, exactly as d3's area generator buffers and
/// replays its two edges — through the SAME curve on both, so a curved area
/// has a curved baseline too. A single-point run is therefore
/// `M x,top L x,bottom Z`.
pub fn area_path(runs: &[Vec<(f64, f64)>], bottom_y: f64, curve: Curve) -> String {
    let runs3: Vec<Vec<(f64, f64, f64)>> =
        runs.iter().map(|run| run.iter().map(|&(x, y)| (x, y, bottom_y)).collect()).collect();
    stacked_area_path(&runs3, curve)
}

/// Build an SVG path string for a filled area whose baseline varies PER
/// POINT — a stacked area's own vertex list, `(x, top, bottom)`, where
/// `bottom` is the running sum of the series stacked below it at that `x`
/// (Vega's `stack` transform). Same edge-pairing as [`area_path`]: each run
/// walks its top edge left-to-right, then its own bottom edge (not a flat
/// line) back right-to-left, then closes — [`area_path`] is this function
/// at one flat `bottom` for every point.
pub fn stacked_area_path(runs: &[Vec<(f64, f64, f64)>], curve: Curve) -> String {
    let mut path = Path::rounded(WITH_PATH_DIGITS);
    for run in runs {
        let top: Vec<(f64, f64)> = run.iter().map(|&(x, top, _)| (x, top)).collect();
        append_edge(&mut path, &top, curve, Edge::AreaTop);
        let bottom: Vec<(f64, f64)> = run.iter().rev().map(|&(x, _, bottom)| (x, bottom)).collect();
        append_edge(&mut path, &bottom, curve.toggled_for_area_bottom(), Edge::AreaBottom);
    }
    path.into_string()
}

/// Run one `lineStart … point* … lineEnd` cycle of `curve` over `points`.
fn append_edge(path: &mut Path, points: &[(f64, f64)], curve: Curve, edge: Edge) {
    let drawn = match curve {
        Curve::Linear => linear_edge(path, points, edge),
        Curve::MonotoneX => monotone_x_edge(path, points, edge),
        Curve::Step => step_edge(path, points, 0.5, edge),
        Curve::StepBefore => step_edge(path, points, 0.0, edge),
        Curve::StepAfter => step_edge(path, points, 1.0, edge),
    };
    // Every d3 curve ends `lineEnd` with the same tail:
    // `if (_line || (_line !== 0 && _point === 1)) closePath()` — the
    // load-bearing lone-point `Z` [`line_path`] documents.
    if edge == Edge::AreaBottom || (edge == Edge::Bare && drawn == 1) {
        path.close_path();
    }
}

/// d3's `curveLinear`, returning its `_point` count.
fn linear_edge(path: &mut Path, points: &[(f64, f64)], edge: Edge) -> usize {
    for (i, &(x, y)) in points.iter().enumerate() {
        if i == 0 && edge != Edge::AreaBottom {
            path.move_to(x, y);
        } else {
            path.line_to(x, y);
        }
    }
    points.len()
}

/// d3's `curveMonotoneX`, returning its `_point` count.
///
/// Each segment is a cubic whose control points sit a third of the segment's
/// dx either side of its ends, lifted by that dx times the tangent there:
/// interior tangents come from [`slope3`], the two endpoints' from
/// [`slope2`]. A run too short for a tangent falls back to `lineEnd`'s own
/// shapes — a straight `L` for two points, nothing for one.
fn monotone_x_edge(path: &mut Path, points: &[(f64, f64)], edge: Edge) -> usize {
    let (mut x0, mut y0) = (f64::NAN, f64::NAN);
    let (mut x1, mut y1) = (f64::NAN, f64::NAN);
    let mut t0 = f64::NAN;
    let mut drawn = 0_usize;
    for &(x, y) in points {
        // d3 drops a point identical to its predecessor before any state moves.
        if x == x1 && y == y1 {
            continue;
        }
        let mut t1 = f64::NAN;
        match drawn {
            0 => {
                if edge == Edge::AreaBottom {
                    path.line_to(x, y);
                } else {
                    path.move_to(x, y);
                }
            }
            1 => {}
            2 => {
                t1 = slope3(x0, y0, x1, y1, x, y);
                bezier(path, x0, y0, x1, y1, slope2(x0, y0, x1, y1, t1), t1);
            }
            _ => {
                t1 = slope3(x0, y0, x1, y1, x, y);
                bezier(path, x0, y0, x1, y1, t0, t1);
            }
        }
        drawn += 1;
        (x0, y0, x1, y1, t0) = (x1, y1, x, y, t1);
    }
    match drawn {
        0 | 1 => {}
        2 => path.line_to(x1, y1),
        _ => bezier(path, x0, y0, x1, y1, t0, slope2(x0, y0, x1, y1, t0)),
    }
    drawn
}

/// d3's `Step(context, t)`: a riser at each segment's `t`-fraction of its
/// width — `t=0.5` centers it, `t=0` puts it at the segment start
/// (`step-before`), `t=1` at its end (`step-after`). Returns the point count
/// fed in: d3's own `_point` counter saturates at 2 after the second call
/// (every point past the first runs the same riser code) and, unlike
/// [`monotone_x_edge`], `Step` never drops a coincident point.
fn step_edge(path: &mut Path, points: &[(f64, f64)], t: f64, edge: Edge) -> usize {
    let (mut x_prev, mut y_prev) = (f64::NAN, f64::NAN);
    let mut drawn = 0_usize;
    for &(x, y) in points {
        if drawn == 0 {
            if edge == Edge::AreaBottom {
                path.line_to(x, y);
            } else {
                path.move_to(x, y);
            }
        } else if t <= 0.0 {
            path.line_to(x_prev, y);
            path.line_to(x, y);
        } else {
            let x1 = x_prev * (1.0 - t) + x * t;
            path.line_to(x1, y_prev);
            path.line_to(x1, y);
        }
        drawn += 1;
        (x_prev, y_prev) = (x, y);
    }
    // d3's `lineEnd`: a centered riser (0 < t < 1) never reaches the last
    // point's own x during `point()` — only the final segment's midpoint —
    // so a run of 2+ points needs one more flat run out to it. `step-before`/
    // `step-after` (t=0/1) already end exactly there, so this is a no-op.
    if t > 0.0 && t < 1.0 && drawn >= 2 {
        path.line_to(x_prev, y_prev);
    }
    drawn
}

/// One cubic segment — d3's `point(that, t0, t1)`.
fn bezier(path: &mut Path, x0: f64, y0: f64, x1: f64, y1: f64, t0: f64, t1: f64) {
    let dx = (x1 - x0) / 3.0;
    path.bezier_curve_to(x0 + dx, y0 + dx * t0, x1 - dx, y1 - dx * t1, x1, y1);
}

/// The Fritsch–Carlson tangent at the middle of three points: the harmonic
/// slope, clamped to half the parabola's so the interpolant cannot overshoot,
/// and zeroed wherever the two segment slopes disagree in sign (an extremum).
fn slope3(x0: f64, y0: f64, x1: f64, y1: f64, x2: f64, y2: f64) -> f64 {
    let (h0, h1) = (x1 - x0, x2 - x1);
    let s0 = (y1 - y0) / span(h0, h1);
    let s1 = (y2 - y1) / span(h1, h0);
    let p = (s0 * h1 + s1 * h0) / (h0 + h1);
    let t = (sign(s0) + sign(s1)) * js_min(s0.abs(), js_min(s1.abs(), 0.5 * p.abs()));
    // d3's trailing `|| 0`: a coincident pair leaves the slope NaN, and a
    // tangent must be a number. Unreachable from this renderer — `monotone_x_edge`
    // has already dropped the coincident point — but it is d3's code, and the
    // port's job is d3, not the corpus. Same for [`span`] and [`js_min`].
    if t.is_nan() {
        0.0
    } else {
        t
    }
}

/// The endpoint tangent — d3's `slope2`, extrapolated from the end segment's
/// own slope and its neighbor's tangent.
fn slope2(x0: f64, y0: f64, x1: f64, y1: f64, t: f64) -> f64 {
    let h = x1 - x0;
    if h == 0.0 {
        t
    } else {
        (3.0 * (y1 - y0) / h - t) / 2.0
    }
}

/// d3's `h0 || (h1 < 0 && -0)`: a zero-width span borrows its NEIGHBOR's
/// direction, so the slope runs off to the correctly-signed infinity and the
/// clamp still picks the finite neighbor. A faithfulness guard this
/// renderer's own inputs never reach (see [`slope3`]).
fn span(h: f64, other: f64) -> f64 {
    if h != 0.0 {
        h
    } else if other < 0.0 {
        -0.0
    } else {
        0.0
    }
}

fn sign(x: f64) -> f64 {
    if x < 0.0 {
        -1.0
    } else {
        1.0
    }
}

/// `Math.min` propagates NaN where `f64::min` discards it — and d3 leans on
/// that: a NaN slope must reach the `|| 0` in [`slope3`], not be quietly
/// replaced by the other candidate. Same unreachable-but-faithful status.
fn js_min(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else {
        a.min(b)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One defined run — the shape both generators take.
    fn one_run(points: &[(f64, f64)]) -> Vec<Vec<(f64, f64)>> {
        vec![points.to_vec()]
    }

    /// Seven pixel positions read from vl-convert 1.9.0's `vegalite_to_scenegraph` for a
    /// 300x177 chart (x domain 0..6, y domain 0..177, y data `[76.9375, 5.1234, 177.0625,
    /// 100.0625, 177.0005, 54.321, 54.321]`). They carry a fourth decimal, a binary-exact tie
    /// (`76.9375`), a value just below a negative tie (`-0.06250000000000311`, which prints
    /// `-0.063`; an exact `-0.0625` prints `-0.062`, JavaScript's `Math.round(-62.5)` being
    /// `-62`) and a negative value that rounds to zero (`-0.0005`).
    const ORACLE_PTS: [(f64, f64); 7] = [
        (0.0, 100.06249999999999),
        (50.0, 171.8766),
        (100.0, -0.06250000000000311),
        (150.0, 76.9375),
        (200.0, -0.0004999999999732996),
        (250.0, 122.67899999999999),
        (300.0, 122.67899999999999),
    ];

    /// The `d` of each mark in the SVG `vegalite_to_svg` returns for that chart: d3-shape's
    /// `withPath` hands every generator `new Path(3)`, and d3-path's `_append` prints
    /// `Math.round(v * 1e3) / 1e3` (`M0,100.062`, `L100,-0.063`, `L150,76.938`, `L200,0`).
    #[test]
    fn a_linear_line_prints_the_digits_vega_prints() {
        assert_eq!(
            line_path(&one_run(&ORACLE_PTS), Curve::Linear),
            "M0,100.062L50,171.877L100,-0.063L150,76.938L200,0L250,122.679L300,122.679"
        );
    }

    #[test]
    fn a_monotone_line_prints_the_digits_vega_prints() {
        assert_eq!(
            line_path(&one_run(&ORACLE_PTS), Curve::MonotoneX),
            "M0,100.062C16.667,135.97,33.333,171.877,50,171.877\
             C66.667,171.877,83.333,-0.063,100,-0.063\
             C116.667,-0.063,133.333,76.938,150,76.938\
             C166.667,76.938,183.333,0,200,0\
             C216.667,0,233.333,122.679,250,122.679\
             C266.667,122.679,283.333,122.679,300,122.679"
        );
    }

    #[test]
    fn a_step_line_prints_the_digits_vega_prints() {
        assert_eq!(
            line_path(&one_run(&ORACLE_PTS), Curve::Step),
            "M0,100.062L25,100.062L25,171.877L75,171.877L75,-0.063L125,-0.063\
             L125,76.938L175,76.938L175,0L225,0L225,122.679L275,122.679L275,122.679L300,122.679"
        );
    }

    #[test]
    fn an_area_prints_the_digits_vega_prints() {
        assert_eq!(
            area_path(&one_run(&ORACLE_PTS), 177.0, Curve::Linear),
            "M0,100.062L50,171.877L100,-0.063L150,76.938L200,0L250,122.679L300,122.679\
             L300,177L250,177L200,177L150,177L100,177L50,177L0,177Z"
        );
    }

    /// The shapes d3 emits either side of a `defined` break, pinned against
    /// `editorial-stress-test/area_won_stacked`'s reference SVG — whose
    /// shortest area run reads `M833.043,244.55L833.043,335Z` and whose
    /// halo line reads `…M416.522,294.8ZM458.174,254.6Z…`, one closing `Z`
    /// per one-point run and none on the longer ones.
    #[test]
    fn each_defined_run_is_its_own_subpath() {
        let runs = vec![vec![(0.0, 1.0), (2.0, 3.0)], vec![(6.0, 7.0)]];
        assert_eq!(line_path(&runs, Curve::Linear), "M0,1L2,3M6,7Z");
        assert_eq!(area_path(&runs, 9.0, Curve::Linear), "M0,1L2,3L2,9L0,9ZM6,7L6,9Z");
        assert_eq!(line_path(&[], Curve::Linear), "");
    }

    /// A stacked area's baseline varies per point (each series' running sum
    /// below it), unlike the flat `bottom_y` a plain (unstacked) area draws
    /// against — [`stacked_area_path`] takes a per-point `(x, top, bottom)`
    /// triple instead. `area_path` is the flat-baseline special case.
    #[test]
    fn stacked_area_path_takes_a_per_point_baseline() {
        let runs = vec![vec![(0.0, 1.0, 9.0), (2.0, 3.0, 5.0)], vec![(6.0, 7.0, 9.0)]];
        assert_eq!(stacked_area_path(&runs, Curve::Linear), "M0,1L2,3L2,5L0,9ZM6,7L6,9Z");
    }

    /// The seven points below and their expected control points come from a
    /// direct transcription of d3-shape's `monotone.js` (`slope3`, `slope2`,
    /// `point`) run outside this crate, on a series chosen so each candidate of
    /// the Fritsch–Carlson clamp `min(|s0|, |s1|, 0.5·|p|)` is the strict
    /// minimum at some vertex: `|s1|` at x=10 and x=40, `0.5·|p|` at x=25,
    /// `|s0|` at x=50 (the flat-then-plunge shape, where the incoming segment
    /// is the shallow one). At x=30 the slopes change sign, so the tangent is 0
    /// whatever the clamp returns, and both endpoints come from `slope2`.
    const MONO_PTS: [(f64, f64); 7] = [
        (0.0, 0.0),
        (10.0, 30.0),
        (25.0, 45.0),
        (30.0, 50.0),
        (40.0, 10.0),
        (50.0, 9.9),
        (60.0, -20.1),
    ];

    #[test]
    fn monotone_x_emits_d3_s_cubic_control_points() {
        assert_eq!(
            line_path(&one_run(&MONO_PTS), Curve::MonotoneX),
            "M0,0C3.333,11.667,6.667,23.333,10,30\
             C15,40,20,40,25,45\
             C26.667,46.667,28.333,50,30,50\
             C33.333,50,36.667,10.067,40,10\
             C43.333,9.933,46.667,9.967,50,9.9\
             C53.333,9.833,56.667,-5.133,60,-20.1"
                .replace(' ', "")
        );
    }

    /// d3's area generator runs BOTH edges through the curve: the top edge
    /// forward, then the flat bottom back through every x, then one `Z`.
    #[test]
    fn monotone_x_curves_the_area_s_bottom_edge_too() {
        assert_eq!(
            area_path(&one_run(&MONO_PTS), 100.0, Curve::MonotoneX),
            "M0,0C3.333,11.667,6.667,23.333,10,30\
             C15,40,20,40,25,45\
             C26.667,46.667,28.333,50,30,50\
             C33.333,50,36.667,10.067,40,10\
             C43.333,9.933,46.667,9.967,50,9.9\
             C53.333,9.833,56.667,-5.133,60,-20.1\
             L60,100C56.667,100,53.333,100,50,100\
             C46.667,100,43.333,100,40,100\
             C36.667,100,33.333,100,30,100\
             C28.333,100,26.667,100,25,100\
             C20,100,15,100,10,100\
             C6.667,100,3.333,100,0,100Z"
                .replace(' ', "")
        );
    }

    /// d3's `MonotoneX.point` opens `if (x !== this._x1 || y !== this._y1)`, so
    /// a repeat of the previous datum never advances `_point` and never earns a
    /// segment — `curveLinear.point` has no such guard, which is why the drop
    /// lives in the monotone machine alone.
    #[test]
    fn a_repeated_point_is_dropped_before_the_tangents_are_taken() {
        let doubled = one_run(&[(0.0, 0.0), (10.0, 10.0), (10.0, 10.0), (20.0, 5.0)]);
        let once = one_run(&[(0.0, 0.0), (10.0, 10.0), (20.0, 5.0)]);
        assert_eq!(
            line_path(&doubled, Curve::MonotoneX),
            "M0,0C3.333,5,6.667,10,10,10C13.333,10,16.667,7.5,20,5"
        );
        assert_eq!(
            line_path(&doubled, Curve::MonotoneX),
            line_path(&once, Curve::MonotoneX)
        );
    }

    /// A two-point run has no interior tangent, so d3's `lineEnd` draws it as
    /// a straight `L`; a one-point run draws only its `moveto` and the `Z`
    /// that makes a round-capped mark paint a dot.
    #[test]
    fn short_monotone_runs_take_d3_s_line_end_shapes() {
        assert_eq!(line_path(&one_run(&MONO_PTS[..2]), Curve::MonotoneX), "M0,0L10,30");
        assert_eq!(line_path(&one_run(&MONO_PTS[..1]), Curve::MonotoneX), "M0,0Z");
    }

    // Three points chosen so the riser's direction is visible: (0,0) →
    // (10,5) → (20,-5). Expected `d` strings are a hand-transcription of
    // d3-shape's `Step(context, t).point` — see `step_edge`'s own doc
    // comment for the per-point algorithm.
    const STEP_PTS: [(f64, f64); 3] = [(0.0, 0.0), (10.0, 5.0), (20.0, -5.0)];

    #[test]
    fn step_after_puts_the_riser_at_each_segment_s_end() {
        // t=1: x1 = x_prev*(1-1) + x*1 = x — the riser lands at the NEW
        // point's own x, so the pen is already there after `point()` (no
        // lineEnd extra).
        assert_eq!(
            line_path(&one_run(&STEP_PTS), Curve::StepAfter),
            "M0,0L10,0L10,5L20,5L20,-5"
        );
    }

    #[test]
    fn step_before_puts_the_riser_at_each_segment_s_start() {
        // t=0: x1 = x_prev — the riser lands at the PREVIOUS point's x.
        assert_eq!(
            line_path(&one_run(&STEP_PTS), Curve::StepBefore),
            "M0,0L0,5L10,5L10,-5L20,-5"
        );
    }

    #[test]
    fn centered_step_puts_the_riser_at_each_segment_s_midpoint() {
        // t=0.5: x1 is the segment midpoint, and `lineEnd`'s extra `lineTo`
        // (0 < t < 1) draws the final flat run out to the last real point —
        // `point()` alone only ever reaches the last MIDPOINT.
        assert_eq!(
            line_path(&one_run(&STEP_PTS), Curve::Step),
            "M0,0L5,0L5,5L15,5L15,-5L20,-5"
        );
    }

    /// The `lineEnd` extra fires for ANY run of 2+ points under a centered
    /// riser, not just exactly-two — d3's own `_point` counter saturates at
    /// 2 after the second `point()` call and never advances further, so the
    /// guard (`_point === 2`) is really "at least 2 points seen".
    #[test]
    fn centered_step_two_point_run_reaches_its_endpoint_only_via_the_lineend_extra() {
        let two = one_run(&[(0.0, 0.0), (10.0, 10.0)]);
        assert_eq!(line_path(&two, Curve::Step), "M0,0L5,0L5,10L10,10");
    }

    /// d3's area generator curves BOTH edges with the same curve — a flat
    /// step-after bottom still emits the doubled-vertex risers, just with no
    /// visible y change (`fixtures/step-band-curve-connected`'s own
    /// reference: the bottom edge is exactly this shape).
    #[test]
    fn step_after_curves_the_area_s_flat_bottom_edge_too() {
        // The bottom edge replays the curve TOGGLED (`toggled_for_area_bottom`:
        // d3's `_t = 1 - _t` on `lineEnd`) — `step-after` (t=1) on top becomes
        // `step-before` (t=0) on the bottom — at constant y=100, so every
        // riser's two lineTo calls land on the same point either way; this
        // renderer doesn't dedupe consecutive commands, and neither does d3
        // (see `fixtures/step-band-curve-connected`'s own reference SVG, whose
        // disconnected plateaus carry the identical doubled-vertex pattern).
        // Geometrically equivalent to the untoggled sequence on this FLAT
        // baseline (same ink, y never changes) — but this IS d3's own vertex
        // sequence, transcribed from `curve/step.js`, not merely "equivalent".
        let d = area_path(&one_run(&STEP_PTS), 100.0, Curve::StepAfter);
        assert_eq!(
            d,
            "M0,0L10,0L10,5L20,5L20,-5\
             L20,100L20,100L10,100L10,100L0,100Z"
        );
    }

    /// The toggle is observable even under this renderer's flat (scalar
    /// `bottom_y`) baseline: `step-before`'s bottom edge (toggled to
    /// `step-after`) puts its FIRST riser at the segment's end rather than
    /// its start, landing a DIFFERENT vertex than the untoggled sequence
    /// would — proof `toggled_for_area_bottom` actually runs, not just that
    /// the ink is unaffected on a flat baseline.
    #[test]
    fn the_t_toggle_changes_the_bottom_edge_s_vertex_sequence() {
        let toggled = area_path(&one_run(&STEP_PTS), 100.0, Curve::StepBefore);
        // Top (step-before, t=0): risers at each segment's OWN start.
        // Bottom (toggled to step-after, t=1): risers at each segment's end.
        assert_eq!(
            toggled,
            "M0,0L0,5L10,5L10,-5L20,-5\
             L20,100L10,100L10,100L0,100L0,100Z"
        );
    }

    /// A lone point under any step variant draws only its `moveto` (plus the
    /// shared `Z` a round-capped one-point run gets) — `step_edge` never
    /// enters its riser branch.
    #[test]
    fn a_lone_point_under_step_after_draws_only_its_moveto() {
        assert_eq!(line_path(&one_run(&STEP_PTS[..1]), Curve::StepAfter), "M0,0Z");
    }
}

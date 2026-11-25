//! A name set along its line: the stretch of a run the name spans, how sharply that stretch bends,
//! and each glyph's center and angle on the stretch moved to the name's side.

use crate::vega_fit::Bounds;
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

/// A name follows a smooth of its line this many font sizes wide on each side, so it reads the
/// line's trend, not each wiggle of the chain.
pub(crate) const SMOOTH_FONT_SIZES: f64 = 1.0;
/// The most a curved name's stretch may turn in all: past it the name stays straight.
const MAX_TURNING_DEGREES: f64 = 60.0;
/// The tightest radius a curved name's stretch may have, in font sizes.
const MIN_RADIUS_FONT_SIZES: f64 = 2.0;
/// A curved name is drawn along a smooth widened until it bends no tighter than this, in font
/// sizes: a stretch can pass the gate and still bend too sharply for letters to read as a line.
const DRAW_RADIUS_FONT_SIZES: f64 = 12.0;
/// The widest smooth a curved name is drawn along, in font sizes on each side.
pub(crate) const MAX_DRAW_SMOOTH_FONT_SIZES: f64 = 4.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Bend {
    /// Total absolute turning across the stretch, in radians: an S-curve turns both ways and adds.
    pub(crate) turning: f64,
    /// The tightest radius over any `window` pixels of the stretch; infinite when straight.
    pub(crate) min_radius: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Glyph {
    pub(crate) at: (f64, f64),
    /// Clockwise in degrees, as SVG's `rotate()` reads it.
    pub(crate) angle: f64,
}

/// `run` resampled every pixel of arc length, keeping both ends.
fn resample(run: &[(f64, f64)]) -> Vec<(f64, f64)> {
    let mut out = vec![run[0]];
    let mut carry = 0.0;
    for w in run.windows(2) {
        let (a, b) = (w[0], w[1]);
        let len = (b.0 - a.0).hypot(b.1 - a.1);
        let mut s = 1.0 - carry;
        while s <= len {
            let t = s / len;
            out.push((a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t));
            s += 1.0;
        }
        carry = len - (s - 1.0);
    }
    if carry > 1e-9 {
        out.push(run[run.len() - 1]);
    }
    out
}

/// Each point averaged with `k` neighbors on each side, fewer near the ends.
fn smooth(points: &[(f64, f64)], k: usize) -> Vec<(f64, f64)> {
    let mut sums = vec![(0.0, 0.0)];
    for p in points {
        let last = sums[sums.len() - 1];
        sums.push((last.0 + p.0, last.1 + p.1));
    }
    (0..points.len())
        .map(|i| {
            let r = k.min(i).min(points.len() - 1 - i);
            let (lo, hi) = (i - r, i + r + 1);
            let n = (hi - lo) as f64;
            ((sums[hi].0 - sums[lo].0) / n, (sums[hi].1 - sums[lo].1) / n)
        })
        .collect()
}

/// A run resampled every pixel, with each smooth of it kept once asked for: a name tries many
/// spots along the same run.
pub(crate) struct Chain {
    points: Vec<(f64, f64)>,
    smooths: RefCell<HashMap<usize, Rc<Path>>>,
}

impl Chain {
    pub(crate) fn new(run: &[(f64, f64)]) -> Self {
        Chain {
            points: resample(run),
            smooths: RefCell::new(HashMap::new()),
        }
    }

    /// The run smoothed twice over `k` points on each side: a triangle kernel.
    fn smoothed(&self, k: usize) -> Rc<Vec<(f64, f64)>> {
        Rc::clone(
            self.smooths
                .borrow_mut()
                .entry(k)
                .or_insert_with(|| Rc::new(smooth(&smooth(&self.points, k), k))),
        )
    }
}

/// The `length` pixels of `chain` centered where it crosses `x`, smoothed over `smooth_px` on each
/// side; `None` when the stretch reaches the run's ends, or the two windows' width from them.
pub(crate) fn stretch(
    chain: &Chain,
    x: f64,
    length: f64,
    smooth_px: f64,
) -> Option<Vec<(f64, f64)>> {
    if chain.points.len() < 2 {
        return None;
    }
    let k = smooth_px.round() as usize;
    let pts = chain.smoothed(k);
    let center = pts.iter().position(|p| p.0 >= x)?;
    let half = (length / 2.0).ceil() as usize;
    // Near an end the window narrows and the smooth bends with the chain again: keep clear of it.
    let (lo, hi) = (center.checked_sub(half + 2 * k)? + 2 * k, center + half);
    (hi + 2 * k < pts.len()).then(|| pts[lo..=hi].to_vec())
}

/// The stretch a curved name is drawn along: the gate's smooth, widened half a font size at a
/// time until it bends no tighter than `DRAW_RADIUS_FONT_SIZES`, reaches the widest smooth, or a
/// wider smooth would reach the run's ends.
pub(crate) fn drawn(chain: &Chain, x: f64, length: f64, font_size: f64) -> Option<Vec<(f64, f64)>> {
    let mut smooth = SMOOTH_FONT_SIZES;
    let mut path = stretch(chain, x, length, smooth * font_size)?;
    while bend(&path, font_size / 2.0).min_radius < DRAW_RADIUS_FONT_SIZES * font_size
        && smooth < MAX_DRAW_SMOOTH_FONT_SIZES
    {
        smooth += 0.5;
        match stretch(chain, x, length, smooth * font_size) {
            Some(wider) => path = wider,
            None => break,
        }
    }
    Some(path)
}

/// Each segment's heading, unwrapped so neighbors never differ by more than half a turn.
fn headings(path: &[(f64, f64)]) -> Vec<f64> {
    let mut out: Vec<f64> = Vec::with_capacity(path.len().saturating_sub(1));
    for w in path.windows(2) {
        let raw = (w[1].1 - w[0].1).atan2(w[1].0 - w[0].0);
        let h = match out.last() {
            Some(&prev) => {
                let tau = std::f64::consts::TAU;
                prev + (raw - prev + std::f64::consts::PI).rem_euclid(tau) - std::f64::consts::PI
            }
            None => raw,
        };
        out.push(h);
    }
    out
}

/// How a per-pixel stretch bends; `window` sets the span its tightest radius is read over.
pub(crate) fn bend(path: &[(f64, f64)], window: f64) -> Bend {
    let h = headings(path);
    // Rounding leaves a straight path turning by ~1e-12 rad: below this it does not turn.
    let turn = |a: f64, b: f64| Some((b - a).abs()).filter(|d| *d > 1e-9);
    let turning = h.windows(2).filter_map(|w| turn(w[0], w[1])).sum();
    let k = (window.round() as usize).max(1);
    let min_radius = h
        .windows(k + 1)
        .filter_map(|w| turn(w[0], w[k]).map(|d| k as f64 / d))
        .fold(f64::INFINITY, f64::min);
    Bend {
        turning,
        min_radius,
    }
}

/// Whether a name bends gently enough over `bend` to run along its line at `font_size`.
pub(crate) fn gentle(bend: &Bend, font_size: f64) -> bool {
    bend.turning <= MAX_TURNING_DEGREES.to_radians()
        && bend.min_radius >= MIN_RADIUS_FONT_SIZES * font_size
}

/// How far up the screen `path` must move for no point of `chain` under it to sit above it: a
/// smooth runs through its chain's peaks, and a name set along the smooth must clear them.
pub(crate) fn lift(chain: &Chain, path: &[(f64, f64)]) -> f64 {
    let last = path.len() - 1;
    let (left, right) = (path[0].0, path[last].0);
    chain
        .points
        .iter()
        .copied()
        .filter(|p| p.0 >= left && p.0 <= right)
        .filter_map(|p| {
            // A run goes left to right and so does its smooth: the nearest point of the path is
            // among those within `reach` of `p` in x.
            let reach = (p.1 - super::lerp_at(path, p.0)).abs() + 2.0;
            let lo = path.partition_point(|q| q.0 < p.0 - reach);
            let hi = path.partition_point(|q| q.0 <= p.0 + reach);
            let (i, q) = path[lo..hi.max(lo + 1).min(path.len())]
                .iter()
                .enumerate()
                .map(|(j, q)| (lo + j, q))
                .min_by(|a, b| {
                    let d = |q: &(f64, f64)| (p.0 - q.0).hypot(p.1 - q.1);
                    d(a.1).total_cmp(&d(b.1))
                })?;
            if i == 0 || i == last {
                return None;
            }
            let (a, b) = (path[i - 1], path[i + 1]);
            let len = (b.0 - a.0).hypot(b.1 - a.1);
            let (tx, ty) = ((b.0 - a.0) / len, (b.1 - a.1) / len);
            Some((p.0 - q.0) * ty - (p.1 - q.1) * tx)
        })
        .fold(0.0, f64::max)
}

/// The height of ascending `path` at `x`, held flat past its ends.
/// A glyph's ink box as its four corners, clockwise from the top left on screen; a straight box is
/// one too, and the tests below read either alike.
pub(crate) type Quad = [(f64, f64); 4];
type Path = Vec<(f64, f64)>;

/// A glyph's ink, `advance` wide, from `above` over its baseline to `below` under it, turned with
/// the glyph.
pub(crate) fn ink_quad(g: &Glyph, advance: f64, above: f64, below: f64) -> Quad {
    let (sin, cos) = g.angle.to_radians().sin_cos();
    [(-1.0, -above), (1.0, -above), (1.0, below), (-1.0, below)].map(|(side, dy)| {
        let dx = side * advance / 2.0;
        (g.at.0 + dx * cos - dy * sin, g.at.1 + dx * sin + dy * cos)
    })
}

/// `b`'s corners, clockwise from the top left.
pub(crate) fn box_quad(b: &Bounds) -> Quad {
    [(b.x1, b.y1), (b.x2, b.y1), (b.x2, b.y2), (b.x1, b.y2)]
}

/// The axis-aligned box around `q`.
pub(crate) fn quad_bounds(q: &Quad) -> Bounds {
    scene::Bounds::of_points(*q).expect("four corners").into()
}

fn edges(q: &Quad) -> [((f64, f64), (f64, f64)); 4] {
    [(q[0], q[1]), (q[1], q[2]), (q[2], q[3]), (q[3], q[0])]
}

fn cross(o: (f64, f64), a: (f64, f64), b: (f64, f64)) -> f64 {
    (a.0 - o.0) * (b.1 - o.1) - (a.1 - o.1) * (b.0 - o.0)
}

fn contains(q: &Quad, p: (f64, f64)) -> bool {
    let sides = edges(q).map(|(a, b)| cross(a, b, p));
    sides.iter().all(|s| *s >= 0.0) || sides.iter().all(|s| *s <= 0.0)
}

fn segments_cross((a, b): ((f64, f64), (f64, f64)), (c, d): ((f64, f64), (f64, f64))) -> bool {
    let (d1, d2) = (cross(c, d, a), cross(c, d, b));
    let (d3, d4) = (cross(a, b, c), cross(a, b, d));
    d1 * d2 <= 0.0 && d3 * d4 <= 0.0 && (d1 != d2 || d3 != d4)
}

/// How near the segment `a`–`b` comes to the ink `q`: 0 when it touches or crosses it.
pub(crate) fn quad_segment_distance(q: &Quad, a: (f64, f64), b: (f64, f64)) -> f64 {
    if contains(q, a) || contains(q, b) || edges(q).into_iter().any(|e| segments_cross(e, (a, b))) {
        return 0.0;
    }
    let to_edges = |p: (f64, f64)| {
        edges(q)
            .into_iter()
            .map(|(c, d)| crate::placement::path_distance(&[c, d], p))
            .fold(f64::INFINITY, f64::min)
    };
    let to_ab = q
        .iter()
        .map(|c| crate::placement::path_distance(&[a, b], *c))
        .fold(f64::INFINITY, f64::min);
    to_edges(a).min(to_edges(b)).min(to_ab)
}

/// Whether the ink `q` and the box `b` share any point: no axis of either separates them.
pub(crate) fn quad_meets_box(q: &Quad, b: &Bounds) -> bool {
    let corners = box_quad(b);
    let axes = [(1.0, 0.0), (0.0, 1.0)].into_iter().chain(
        edges(q)
            .into_iter()
            .take(2)
            .map(|(a, c)| (a.1 - c.1, c.0 - a.0)),
    );
    axes.into_iter().all(|(ax, ay)| {
        let span = |pts: &Quad| {
            pts.iter()
                .map(|p| p.0 * ax + p.1 * ay)
                .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), v| {
                    (lo.min(v), hi.max(v))
                })
        };
        let ((a0, a1), (b0, b1)) = (span(q), span(&corners));
        a0 <= b1 && b0 <= a1
    })
}

/// `path` moved `d` pixels along its normal; a positive `d` moves it up the screen.
pub(crate) fn offset(path: &[(f64, f64)], d: f64) -> Vec<(f64, f64)> {
    (0..path.len())
        .map(|i| {
            let (a, b) = (path[i.saturating_sub(1)], path[(i + 1).min(path.len() - 1)]);
            let len = (b.0 - a.0).hypot(b.1 - a.1);
            let (tx, ty) = ((b.0 - a.0) / len, (b.1 - a.1) / len);
            (path[i].0 + d * ty, path[i].1 - d * tx)
        })
        .collect()
}

/// The glyph centers at arc lengths `mids` from the middle of `path`, each turned to the path there;
/// `None` when a center falls off either end.
pub(crate) fn glyphs(path: &[(f64, f64)], mids: &[f64]) -> Option<Vec<Glyph>> {
    let mut acc = vec![0.0];
    for w in path.windows(2) {
        acc.push(acc[acc.len() - 1] + (w[1].0 - w[0].0).hypot(w[1].1 - w[0].1));
    }
    let total = acc[acc.len() - 1];
    mids.iter()
        .map(|&m| {
            let s = total / 2.0 + m;
            if !(0.0..=total).contains(&s) {
                return None;
            }
            let k = acc.partition_point(|&a| a < s).clamp(1, path.len() - 1);
            let (a, b) = (path[k - 1], path[k]);
            let seg = acc[k] - acc[k - 1];
            let t = if seg > 0.0 {
                (s - acc[k - 1]) / seg
            } else {
                0.0
            };
            Some(Glyph {
                at: (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t),
                angle: (b.1 - a.1).atan2(b.0 - a.0).to_degrees(),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arc(radius: f64, cx: f64, cy: f64, from: f64, to: f64) -> Vec<(f64, f64)> {
        (0..=200)
            .map(|k| {
                let a = from + (to - from) * f64::from(k) / 200.0;
                (cx + radius * a.cos(), cy - radius * a.sin())
            })
            .collect()
    }

    #[test]
    fn a_drawn_path_smooths_a_tight_hump_to_its_floor_and_leaves_a_round_arc_alone() {
        let font = 14.0;
        let hump: Vec<(f64, f64)> = (0..=400)
            .map(|x| {
                let x = f64::from(x);
                (x, 300.0 - 30.0 * (-((x - 200.0) / 30.0).powi(2)).exp())
            })
            .collect();
        let tight = bend(
            &stretch(&Chain::new(&hump), 200.0, 80.0, font).expect("fits"),
            font / 2.0,
        );
        let drawn = bend(
            &drawn(&Chain::new(&hump), 200.0, 80.0, font).expect("fits"),
            font / 2.0,
        );
        assert!(
            tight.min_radius < DRAW_RADIUS_FONT_SIZES * font,
            "{tight:?}"
        );
        assert!(
            drawn.min_radius >= DRAW_RADIUS_FONT_SIZES * font,
            "{drawn:?}"
        );
        let round = arc(400.0, 400.0, 600.0, 2.0, 1.14);
        assert_eq!(
            drawn_path_of(&round, font),
            stretch(&Chain::new(&round), 400.0, 80.0, font).expect("fits")
        );
    }

    fn drawn_path_of(run: &[(f64, f64)], font: f64) -> Vec<(f64, f64)> {
        drawn(&Chain::new(run), 400.0, 80.0, font).expect("fits")
    }

    #[test]
    fn a_straight_line_does_not_bend() {
        let s = stretch(
            &Chain::new(&[(0.0, 100.0), (300.0, 40.0)]),
            150.0,
            80.0,
            4.0,
        )
        .expect("fits");
        let b = bend(&s, 7.0);
        assert!(b.turning < 1e-9, "{b:?}");
        assert!(b.min_radius.is_infinite(), "{b:?}");
    }

    #[test]
    fn an_arc_turns_by_its_angle_and_bends_at_its_radius() {
        // The top of a circle of radius 200, read left to right: 80px of it turns 0.4 rad.
        let run = arc(200.0, 200.0, 300.0, 2.6, 0.54);
        let s = stretch(&Chain::new(&run), 200.0, 80.0, 4.0).expect("fits");
        let b = bend(&s, 7.0);
        assert!((b.turning - 0.4).abs() < 0.03, "{b:?}");
        assert!((b.min_radius - 200.0).abs() < 20.0, "{b:?}");
    }

    #[test]
    fn a_sawtooth_turns_far_more_than_a_half_turn() {
        let run: Vec<(f64, f64)> = (0..=30)
            .map(|k| (f64::from(k) * 10.0, if k % 2 == 0 { 100.0 } else { 90.0 }))
            .collect();
        let b = bend(
            &stretch(&Chain::new(&run), 150.0, 80.0, 4.0).expect("fits"),
            7.0,
        );
        assert!(b.turning > std::f64::consts::PI, "{b:?}");
        assert!(b.min_radius < 14.0, "{b:?}");
    }

    #[test]
    fn a_stretch_past_the_run_s_end_does_not_fit() {
        assert_eq!(
            stretch(&Chain::new(&[(0.0, 50.0), (60.0, 50.0)]), 50.0, 40.0, 4.0),
            None
        );
    }

    #[test]
    fn a_positive_offset_moves_a_level_path_up_the_screen() {
        let o = offset(&[(0.0, 50.0), (1.0, 50.0), (2.0, 50.0)], 5.0);
        assert!(o.iter().all(|p| (p.1 - 45.0).abs() < 1e-9), "{o:?}");
    }

    #[test]
    fn glyphs_sit_at_their_arc_length_from_the_middle_turned_to_the_path() {
        let level: Vec<(f64, f64)> = (0..=100).map(|x| (f64::from(x), 50.0)).collect();
        let g = glyphs(&level, &[-20.0, 0.0, 20.0]).expect("fits");
        assert_eq!(
            g.iter().map(|g| g.at).collect::<Vec<_>>(),
            vec![(30.0, 50.0), (50.0, 50.0), (70.0, 50.0)]
        );
        assert!(g.iter().all(|g| g.angle == 0.0));
        let rising: Vec<(f64, f64)> = (0..=100)
            .map(|k| (f64::from(k), 100.0 - f64::from(k)))
            .collect();
        assert!(glyphs(&rising, &[0.0])
            .expect("fits")
            .iter()
            .all(|g| (g.angle + 45.0).abs() < 1e-9));
    }

    #[test]
    fn a_box_quad_measures_like_its_box() {
        let b = Bounds::new(10.0, 10.0, 30.0, 20.0);
        let q = box_quad(&b);
        for (p, r) in [
            ((0.0, 0.0), (40.0, 0.0)),
            ((0.0, 15.0), (40.0, 15.0)),
            ((35.0, 25.0), (50.0, 40.0)),
            ((15.0, 12.0), (16.0, 13.0)),
        ] {
            let d = crate::placement::segment_box_distance(p, r, &b);
            assert!(
                (quad_segment_distance(&q, p, r) - d).abs() < 1e-9,
                "{p:?}-{r:?}: {d}"
            );
        }
        assert!(quad_meets_box(&q, &Bounds::new(29.0, 19.0, 40.0, 40.0)));
        assert!(!quad_meets_box(&q, &Bounds::new(31.0, 19.0, 40.0, 40.0)));
    }

    #[test]
    fn a_turned_glyph_is_judged_by_its_ink_not_the_box_around_it() {
        // Turned 45°: the box around the ink reaches a corner the ink does not.
        let g = Glyph {
            at: (0.0, 0.0),
            angle: 45.0,
        };
        let q = ink_quad(&g, 10.0, 5.0, 5.0);
        let corner = Bounds::new(4.0, -7.0, 7.0, -4.0);
        assert!(crate::placement::intersection(&quad_bounds(&q), &corner) > 0.0);
        assert!(!quad_meets_box(&q, &corner));
        assert!(quad_segment_distance(&q, (5.0, -7.0), (7.0, -5.0)) > 1.0);
        assert!(quad_meets_box(&q, &Bounds::new(-1.0, -1.0, 1.0, 1.0)));
    }

    #[test]
    fn a_name_longer_than_its_path_has_no_glyphs() {
        let level: Vec<(f64, f64)> = (0..=40).map(|x| (f64::from(x), 50.0)).collect();
        assert_eq!(glyphs(&level, &[-25.0, 0.0, 25.0]), None);
    }

    #[test]
    fn a_stretch_keeps_clear_of_where_the_smooth_narrows_at_the_run_ends() {
        let run = [(0.0, 100.0), (300.0, 40.0)];
        // 40px each side of x plus two 14px windows: the stretch fits from about x = 67 to x = 233.
        assert!(stretch(&Chain::new(&run), 60.0, 80.0, 14.0).is_none());
        assert!(stretch(&Chain::new(&run), 80.0, 80.0, 14.0).is_some());
        assert!(stretch(&Chain::new(&run), 240.0, 80.0, 14.0).is_none());
    }

    #[test]
    fn a_name_curves_up_to_sixty_degrees_and_two_font_sizes_of_radius() {
        let bend = |deg: f64, r: f64| Bend {
            turning: deg.to_radians(),
            min_radius: r * 14.0,
        };
        assert!(gentle(&bend(34.0, 2.5), 14.0));
        assert!(gentle(&bend(0.0, f64::INFINITY), 14.0));
        assert!(!gentle(&bend(85.0, 2.2), 14.0));
        assert!(!gentle(&bend(40.0, 1.5), 14.0));
    }

    #[test]
    fn the_path_lifts_until_the_chain_s_highest_point_under_it_is_level_with_it() {
        let chain: Vec<(f64, f64)> = (0..=40)
            .map(|k| (f64::from(k) * 5.0, if k % 2 == 0 { 100.0 } else { 90.0 }))
            .collect();
        let path: Vec<(f64, f64)> = (0..=100).map(|x| (50.0 + f64::from(x), 95.0)).collect();
        let l = lift(&Chain::new(&chain), &path);
        assert!((l - 5.0).abs() < 0.1, "{l}");
        assert_eq!(lift(&Chain::new(&path), &path), 0.0);
    }

    #[test]
    fn a_noisy_rise_is_gentle_along_its_smooth() {
        let run: Vec<(f64, f64)> = (0..=40)
            .map(|k| {
                let x = f64::from(k) * 14.0;
                (x, 210.0 - 0.15 * x + if k % 2 == 0 { 6.0 } else { -6.0 })
            })
            .collect();
        let s = stretch(&Chain::new(&run), 280.0, 100.0, SMOOTH_FONT_SIZES * 14.0).expect("fits");
        assert!(gentle(&bend(&s, 7.0), 14.0), "{:?}", bend(&s, 7.0));
    }
}

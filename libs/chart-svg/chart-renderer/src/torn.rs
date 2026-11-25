//! Torn paper: one tear for the torn baseline (`axis.grid.torn_baseline`), torn
//! outliers and off-the-chart bars, and the law for when an axis tears. Cubics are
//! in (along, across) coordinates of the edge; a vertical edge transposes them
//! when it paints.

use d3rs::scale::{Continuous, Transform};
use scene::{Attrs, Node, PathCommand, PathData, Role};

use crate::common::TornBaseline;

/// One cubic Bézier of the tear: start, two controls, end.
pub type Cubic = [(f64, f64); 4];

/// A torn edge across `along` as (along, across) points: paper torn at two scales, a slow wander
/// and fine fibers, sized to `thickness` (the torn bar's), its highest points on `edge` and its
/// bites running `inward` (+1 toward larger across, -1 toward smaller). `seed` makes the same
/// edge tear the same way every time.
pub fn ragged(along: (f64, f64), edge: f64, inward: f64, thickness: f64, seed: u64) -> Vec<(f64, f64)> {
    let wander = (thickness / 16.0).clamp(1.5, 5.0) * RAGGED_DEPTH;
    let fiber = (thickness / 40.0).clamp(0.6, 2.2) * RAGGED_DEPTH;
    let step = (thickness / 28.0).clamp(1.2, 3.5);
    let mut noise = Noise(seed);
    let mut drift = 0.2 + 0.6 * noise.next();
    let dip = |noise: &mut Noise, drift: f64| wander * drift + fiber * noise.next();
    let mut edge_pts = vec![(along.0, dip(&mut noise, drift))];
    let mut at = along.0 + step * (0.5 + 0.9 * noise.next());
    while at < along.1 {
        drift = (drift + 0.36 * noise.next() - 0.18).clamp(0.0, 1.0);
        edge_pts.push((at, dip(&mut noise, drift)));
        at += step * (0.5 + 0.9 * noise.next());
    }
    edge_pts.push((along.1, dip(&mut noise, drift)));
    let high = edge_pts.iter().map(|p| p.1).fold(f64::MAX, f64::min);
    edge_pts.into_iter().map(|(a, d)| (a, edge + inward * (d - high))).collect()
}

/// `ragged`'s edge as straight cubics, for what slices and fills cubics.
pub fn ragged_cubics(along: (f64, f64), edge: f64, inward: f64, thickness: f64, seed: u64) -> Vec<Cubic> {
    straight(&ragged(along, edge, inward, thickness, seed))
}

/// A torn edge across `along` with bites shaped like `WIDE_PAPER`'s, scaled so the deepest is `depth`.
pub fn ragged_to(along: (f64, f64), edge: f64, inward: f64, depth: f64, seed: u64) -> Vec<Cubic> {
    let dips = ragged(along, 0.0, 1.0, WIDE_PAPER, seed);
    let deepest = dips.iter().map(|p| p.1).fold(0.0, f64::max);
    straight(&dips.into_iter().map(|(a, d)| (a, edge + inward * d * depth / deepest)).collect::<Vec<_>>())
}

/// A bar thickness at which every grain of `ragged` sits at its cap: the widest torn bar's paper.
const WIDE_PAPER: f64 = 98.0;

fn straight(points: &[(f64, f64)]) -> Vec<Cubic> {
    points
        .windows(2)
        .map(|w| {
            let ((a0, c0), (a1, c1)) = (w[0], w[1]);
            [(a0, c0), (a0 + (a1 - a0) / 3.0, c0 + (c1 - c0) / 3.0), (a0 + 2.0 * (a1 - a0) / 3.0, c0 + 2.0 * (c1 - c0) / 3.0), (a1, c1)]
        })
        .collect()
}

/// The gap between a torn baseline's deepest bite and the lowest rung it sits below, in px.
pub const RUNG_CLEARANCE: f64 = 4.0;

/// How deep a ragged tear bites, as a multiple of its natural depth.
const RAGGED_DEPTH: f64 = 1.8;

/// splitmix64: a seeded stream of numbers in [0, 1).
struct Noise(u64);

impl Noise {
    fn next(&mut self) -> f64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^= z >> 31;
        (z >> 11) as f64 / (1u64 << 53) as f64
    }
}

pub fn torn_path(cubics: &[Cubic], vertical: bool) -> PathData {
    let at = |(along, across): (f64, f64)| if vertical { (across, along) } else { (along, across) };
    let (x0, y0) = at(cubics[0][0]);
    let mut commands = vec![PathCommand::M(x0, y0)];
    for [_, c1, c2, b] in cubics {
        let ((c1x, c1y), (c2x, c2y), (bx, by)) = (at(*c1), at(*c2), at(*b));
        commands.push(PathCommand::C(c1x, c1y, c2x, c2y, bx, by));
    }
    PathData(commands)
}

/// The torn baseline's stroke: the tear replacing a gridline.
pub fn torn_node(cubics: &[Cubic], vertical: bool, ink: &str, width: f64) -> Node {
    Node::path(
        Role::Grid,
        torn_path(cubics, vertical),
        Attrs::new().set("class", "torn-baseline").set("fill", "none").set("stroke", ink).num("stroke-width", width),
    )
}

/// The closed fill between the wave and the straight edge at `far` (in the
/// wave's across coordinate): the bar or cap whose one edge is torn.
pub fn torn_fill(cubics: &[Cubic], far: f64, vertical: bool) -> PathData {
    let at = |(along, across): (f64, f64)| if vertical { (across, along) } else { (along, across) };
    let mut commands = torn_path(cubics, vertical).0;
    let (ex, ey) = at((cubics[cubics.len() - 1][3].0, far));
    let (sx, sy) = at((cubics[0][0].0, far));
    commands.extend([PathCommand::L(ex, ey), PathCommand::L(sx, sy), PathCommand::Z]);
    PathData(commands)
}

/// The domain end that tears: the one nearest the off-screen zero. `None`
/// when the domain holds zero, where the axis has a real floor to draw.
pub fn torn_edge_value(lo: f64, hi: f64) -> Option<f64> {
    if lo > 0.0 {
        Some(lo)
    } else if hi < 0.0 {
        Some(hi)
    } else {
        None
    }
}

/// Whether a tick's `value` is the torn `edge`, up to float noise.
pub fn same_value(value: f64, edge: f64) -> bool {
    (value - edge).abs() <= 1e-9 * edge.abs().max(1.0)
}

/// `domain`, ascending, widened past its end `edge` so that end sits `pad` of the span in from
/// the plot edge.
pub fn widen_past(transform: Transform, (lo, hi): (f64, f64), edge: f64, pad: f64) -> (f64, f64) {
    if edge == lo {
        (Continuous::new(transform, [lo, hi], [pad, 1.0]).invert(0.0), hi)
    } else {
        (lo, Continuous::new(transform, [lo, hi], [0.0, 1.0 - pad]).invert(1.0))
    }
}

/// `visible` (a value axis's per-rung label mask, `None` showing every rung) with the torn
/// edge's rung hidden: a tear is no floor, so its value goes unlabeled.
pub fn unlabeled(visible: Option<Vec<bool>>, ticks: &[f64], edge: Option<f64>) -> Option<Vec<bool>> {
    let Some(edge) = edge else {
        return visible;
    };
    Some(ticks.iter().enumerate().map(|(i, &tv)| tv != edge && visible.as_ref().is_none_or(|v| v[i])).collect())
}

/// Which way the crests point from an edge at `edge` on a span of `extent`
/// starting at `start`: toward the middle, `-1` (down the axis' pixel
/// coordinate) from the far half, `+1` from the near one.
pub fn torn_inward(edge: f64, start: f64, extent: f64) -> f64 {
    if edge > start + extent / 2.0 {
        -1.0
    } else {
        1.0
    }
}

/// The parameter at which a cubic whose x is monotone reaches `x`, by bisection.
fn param_at(c: &Cubic, x: f64) -> f64 {
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..40 {
        let mid = (lo + hi) / 2.0;
        if point(c, mid).0 < x { lo = mid } else { hi = mid }
    }
    (lo + hi) / 2.0
}

fn point(c: &Cubic, t: f64) -> (f64, f64) {
    let u = 1.0 - t;
    let f = |p: fn(&(f64, f64)) -> f64| u * u * u * p(&c[0]) + 3.0 * u * u * t * p(&c[1]) + 3.0 * u * t * t * p(&c[2]) + t * t * t * p(&c[3]);
    (f(|p| p.0), f(|p| p.1))
}

/// `c` split at `t` (de Casteljau): the part before and the part after.
fn split(c: &Cubic, t: f64) -> (Cubic, Cubic) {
    let lerp = |a: (f64, f64), b: (f64, f64)| (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t);
    let (p01, p12, p23) = (lerp(c[0], c[1]), lerp(c[1], c[2]), lerp(c[2], c[3]));
    let (p012, p123) = (lerp(p01, p12), lerp(p12, p23));
    let mid = lerp(p012, p123);
    ([c[0], p01, p012, mid], [mid, p123, p23, c[3]])
}

/// The tear's across-coordinate at `along` (clamped to its span). Each cubic's x is monotone,
/// so bisect the parameter.
pub fn torn_across_at(cubics: &[Cubic], x: f64) -> f64 {
    let first = cubics[0][0].0;
    let x = x.max(first).min(cubics[cubics.len() - 1][3].0);
    let c = cubics.iter().find(|c| x <= c[3].0).expect("x is clamped to the last cubic's end");
    point(c, param_at(c, x)).1
}

/// The stretch of the tear from `x1` to `x2`, moved `by` across: a bar's or a cap's
/// share of the one tear across the plot.
pub fn torn_slice(cubics: &[Cubic], x1: f64, x2: f64, by: f64) -> Vec<Cubic> {
    cubics
        .iter()
        .filter(|c| c[3].0 > x1 && c[0].0 < x2)
        .map(|c| {
            let mut c = if c[0].0 < x1 { split(c, param_at(c, x1)).1 } else { *c };
            if c[3].0 > x2 {
                c = split(&c, param_at(&c, x2)).0;
            }
            (c[0].0, c[3].0) = (c[0].0.max(x1), c[3].0.min(x2));
            c.map(|(along, across)| (along, across + by))
        })
        .collect()
}

/// The tear of a value axis whose domain excludes zero, on the edge nearest
/// zero.
pub struct ValueTear<'a> {
    pub tear: &'a TornBaseline,
    pub edge: f64,
}

impl<'a> ValueTear<'a> {
    pub fn of(tear: Option<&'a TornBaseline>, lo: f64, hi: f64) -> Option<Self> {
        let tear = tear.filter(|t| t.enabled)?;
        torn_edge_value(lo, hi).map(|edge| Self { tear, edge })
    }

    /// The tear, biting into a plot `across` px deep on the value axis and
    /// `along` px wide the other way. `edge_px` is the edge's position on the
    /// value axis.
    pub fn cubics(&self, edge_px: f64, across: f64, along: f64) -> Vec<Cubic> {
        self.tear.cubics((0.0, along), edge_px, torn_inward(edge_px, 0.0, across), self.edge)
    }
}

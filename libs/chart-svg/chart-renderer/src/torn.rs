//! The torn baseline (`axis.grid.torn_baseline`): one law for the wave and for
//! when an axis tears, shared by every family that draws it. Cubics are in
//! (along, across) coordinates of the edge; a vertical edge transposes them
//! when it paints.

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
    ragged(along, edge, inward, thickness, seed)
        .windows(2)
        .map(|w| {
            let ((a0, c0), (a1, c1)) = (w[0], w[1]);
            [(a0, c0), (a0 + (a1 - a0) / 3.0, c0 + (c1 - c0) / 3.0), (a0 + 2.0 * (a1 - a0) / 3.0, c0 + 2.0 * (c1 - c0) / 3.0), (a1, c1)]
        })
        .collect()
}

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

/// The torn edge from `x1` to `x2` as cubics: troughs on `edge`, crests at
/// `amplitude` toward `inward` (-1 up, +1 down), so the wave never
/// leaves the plot. `sharpness` blends each quarter-wave from a sine toward
/// its straight chord. When `ticks` are evenly spaced, the period snaps to
/// a subdivision or multiple of the spacing so a trough lands on every
/// tick it spans, except a tick too close to either end for a whole
/// period (under period/√2 from the previous stop), which carries no
/// trough; otherwise the whole width holds `round(width / wavelength)`.
pub fn torn_cubics(x1: f64, x2: f64, edge: f64, inward: f64, ticks: &[f64], tear: &TornBaseline) -> Vec<Cubic> {
    // Cubic control ratios approximating `sin` on [0, π/2].
    const STEEP_TO_FLAT: [(f64, f64); 2] = [(0.326_115, 0.512_261), (0.638_087, 1.0)];
    let gaps: Vec<f64> = ticks.windows(2).map(|w| w[1] - w[0]).collect();
    let (lo, hi) = gaps.iter().fold((f64::MAX, f64::MIN), |(lo, hi), &g| (lo.min(g), hi.max(g)));
    let regular = !gaps.is_empty() && lo > 0.0 && hi <= lo * 1.15;
    let (stops, period) = if regular {
        let mean = gaps.iter().sum::<f64>() / gaps.len() as f64;
        // The period is `mean × r`, r ∈ {1/n} ∪ {n}: subdivide a column or
        // span n of them, whichever lands nearest the wavelength in log
        // space. Spanning n puts troughs on every n-th gridline; a stretch
        // between two stops that isn't a whole multiple of the period (the
        // ends, when the ticks don't divide evenly) gets the nearest whole
        // number of periods, pinned at both stops.
        let divides = (mean / tear.wavelength).ceil() as usize + 1;
        let (span, divide) = (1..=gaps.len())
            .map(|n| (n, 1))
            .chain((1..=divides).map(|n| (1, n)))
            .min_by(|&(s1, d1), &(s2, d2)| {
                let off = |s: usize, d: usize| (mean * s as f64 / d as f64 / tear.wavelength).ln().abs();
                off(s1, d1).total_cmp(&off(s2, d2))
            })
            .expect("at least one candidate");
        let period = mean * span as f64 / divide as f64;
        // A stretch shorter than period/√2 — the bound every other tooth
        // keeps — would squeeze a tooth, so its stop is dropped: ticks
        // left to right against the previous stop, then the last stop
        // against `x2` (band-step ticks sit half a step inside the plot).
        let min_stretch = period / std::f64::consts::SQRT_2;
        let mut stops = vec![x1];
        for t in ticks.iter().copied().step_by(span) {
            if t - stops[stops.len() - 1] >= min_stretch {
                stops.push(t);
            }
        }
        while stops.len() > 1 && x2 - stops[stops.len() - 1] < min_stretch {
            stops.pop();
        }
        stops.push(x2);
        (stops, period)
    } else {
        (vec![x1, x2], tear.wavelength)
    };
    let s = tear.sharpness;
    let mut cubics = Vec::new();
    for seg in stops.windows(2) {
        let n = ((seg[1] - seg[0]) / period).round().max(1.0) as usize;
        let width = (seg[1] - seg[0]) / n as f64;
        for i in 0..n {
            // Heights above the edge at the period's five quarter points.
            let h = [0.0, tear.amplitude / 2.0, tear.amplitude, tear.amplitude / 2.0, 0.0];
            let x0 = seg[0] + width * i as f64;
            let xs = [0.0, 0.25, 0.5, 0.75, 1.0].map(|f| x0 + width * f);
            for k in 0..4 {
                let (ax, bx) = (xs[k], xs[k + 1]);
                let (ay, by) = (edge + inward * h[k], edge + inward * h[k + 1]);
                // Odd quarters run steep→flat; even ones are their reverses.
                let ctrl = |c: usize| {
                    let (fx, fy) = if k % 2 == 1 {
                        STEEP_TO_FLAT[c]
                    } else {
                        let (rx, ry) = STEEP_TO_FLAT[1 - c];
                        (1.0 - rx, 1.0 - ry)
                    };
                    let chord = (c + 1) as f64 / 3.0;
                    let (fx, fy) = (fx + s * (chord - fx), fy + s * (chord - fy));
                    (ax + fx * (bx - ax), ay + fy * (by - ay))
                };
                cubics.push([(ax, ay), ctrl(0), ctrl(1), (bx, by)]);
            }
        }
    }
    cubics
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
/// zero. No gridline crosses a bar chart's tear, so the wave takes the value
/// gridlines' own ink and snaps to nothing.
pub struct ValueTear<'a> {
    pub tear: &'a TornBaseline,
    pub edge: f64,
}

impl<'a> ValueTear<'a> {
    pub fn of(tear: Option<&'a TornBaseline>, lo: f64, hi: f64) -> Option<Self> {
        let tear = tear.filter(|t| t.enabled)?;
        torn_edge_value(lo, hi).map(|edge| Self { tear, edge })
    }

    /// The wave with crests pointing into a plot `across` px deep on the
    /// value axis and `along` px wide the other way. `edge_px` is the edge's
    /// position on the value axis; `vertical` is an edge that runs top to bottom.
    pub fn cubics(&self, edge_px: f64, across: f64, along: f64) -> Vec<Cubic> {
        torn_cubics(0.0, along, edge_px, torn_inward(edge_px, 0.0, across), &[], self.tear)
    }

    pub fn node(&self, cubics: &[Cubic], vertical: bool, ink: &str, width: f64) -> Node {
        Node::path(
            Role::Grid,
            torn_path(cubics, vertical),
            Attrs::new().set("class", "torn-baseline").set("fill", "none").set("stroke", ink).num("stroke-width", width),
        )
    }
}

//! The value-axis tear (`axis.grid.torn_baseline`, RJ Andrews' "tear up your baseline"): when a
//! value domain excludes zero, the domain end nearest the off-screen zero is drawn as a wave, so
//! the cut cannot be read as a floor at zero. The wave's crests point into the plot, so a tear
//! takes no gutter; what it decides is its edge, its crests' direction, its cubics, the rung it
//! replaces and where a gridline crossing it stops.
//!
//! Bind states the slot (`enabled`, `amplitude`, `wavelength`, `sharpness`) and the domain's final
//! bounds. The family states whether it tears at all (a line chart does, an area does not; only a
//! ranged bar with numeric ends does) and which [`Law`] it draws by. Everything else follows the
//! plot's size.

use bind::bound::ValueDomain;
use scene::{PathCommand, PathData};

use crate::geom::Size;
use crate::plot::value_axis::fraction;

/// Where a rule snaps: Vega's crisp half pixel (`AXIS_CRISP`).
const CRISP: f64 = 0.5;

/// The resolved slot of an enabled tear; a disabled one is not stated at all.
///
/// Which charts tear is the caller's to state, by not building a tear for the rest: a line
/// chart's y axis tears (an area does not, it fills from its floor); a bar tears only when it is
/// ranged (`y_start`) with numeric ends, never with date ends, and never as a continuous-x bar or
/// a facet panel; no other family reads the slot.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TornBaseline {
    /// Crest height above the edge, in px.
    pub amplitude: f64,
    /// One full wave, in px.
    pub wavelength: f64,
    /// 0 is a sine, 1 a zigzag; between blends the two.
    pub sharpness: f64,
}

/// How a family draws its tear; production's two paths disagree, and each shape is the one
/// production draws.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Law<'a> {
    /// A ranged bar: the edge sits at its exact scale position, the wave spans the plot from 0
    /// and keeps its own wavelength, a rung gives way when it is the edge's own value, and no
    /// gridline crosses the wave (`ValueTear` in `bar/vertical.rs`, `bar/horizontal.rs`).
    Bar(Edge),
    /// A line chart's y axis: the edge and the crossing rules snap to `round(position) + 0.5`,
    /// the wave spans the plot shifted by that half pixel and times itself to `ticks`, a rung
    /// gives way when its snapped rule lands on the edge's, and each x gridline stops on the wave
    /// (`line/layout.rs`). `ticks` are the x rules' positions as the x axis lays them, snapped.
    Line { ticks: &'a [f64] },
}

/// Which way the wave runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Edge {
    /// Along x, the value axis being y: a vertical bar (and every line).
    Horizontal,
    /// Along y, the value axis being x: a horizontal bar.
    Vertical,
}

/// One cubic Bézier of the wave in (along, across) coordinates of the edge: start, two controls,
/// end.
type Cubic = [(f64, f64); 4];

/// A value axis's tear over a plot.
#[derive(Debug, Clone, PartialEq)]
pub struct Tear {
    value: f64,
    crisp: bool,
    edge: Edge,
    across: f64,
    /// The wave's crests point down the across axis (+1) or up it (-1).
    inward: f64,
    cubics: Vec<Cubic>,
}

impl Tear {
    /// The tear of `domain` on a plot of `plot`, `None` when the domain holds zero and its axis has
    /// a real floor.
    pub fn new(baseline: TornBaseline, domain: &ValueDomain, law: Law, plot: Size) -> Option<Self> {
        let bounds = domain.bounds.extent;
        let value = torn_edge_value(bounds.lo(), bounds.hi())?;
        let (edge, crisp, ticks) = match law {
            Law::Bar(edge) => (edge, false, &[][..]),
            Law::Line { ticks } => (Edge::Horizontal, true, ticks),
        };
        let (along, across) = match edge {
            Edge::Horizontal => (plot.width, plot.height),
            Edge::Vertical => (plot.height, plot.width),
        };
        let frac = fraction(domain, value);
        let position = match edge {
            Edge::Horizontal => across * (1.0 - frac),
            Edge::Vertical => across * frac,
        };
        let (origin, at) = if crisp {
            (CRISP, snap(position))
        } else {
            (0.0, position)
        };
        let inward = torn_inward(at, origin, across);
        let cubics = torn_cubics(origin, along + origin, at, inward, ticks, &baseline);
        Some(Self {
            value,
            crisp,
            edge,
            across,
            inward,
            cubics,
        })
    }

    /// The wave as a path in the plot's coordinates.
    pub fn path(&self) -> PathData {
        let at = |(along, across): (f64, f64)| match self.edge {
            Edge::Horizontal => (along, across),
            Edge::Vertical => (across, along),
        };
        let (x0, y0) = at(self.cubics[0][0]);
        let mut commands = vec![PathCommand::M(x0, y0)];
        for [_, c1, c2, end] in &self.cubics {
            let ((c1x, c1y), (c2x, c2y), (x, y)) = (at(*c1), at(*c2), at(*end));
            commands.push(PathCommand::C(c1x, c1y, c2x, c2y, x, y));
        }
        PathData(commands)
    }

    /// Whether the rung `value`, which the axis puts `position` px along it, gives way to the wave
    /// instead of drawing its gridline.
    pub fn replaces(&self, value: f64, position: f64) -> bool {
        if self.crisp {
            (snap(position) - self.cubics[0][0].1).abs() < 0.5
        } else {
            value == self.value
        }
    }

    /// The span across the plot that a gridline running across a line's edge at `along` keeps:
    /// it stops on the wave at its own x instead of poking through it. The span is (start, end)
    /// in the across axis, from the far side of the plot onto the wave. `None` for a bar, which
    /// draws no such gridline.
    pub fn crossing(&self, along: f64) -> Option<(f64, f64)> {
        if !self.crisp {
            return None;
        }
        let origin = self.cubics[0][0].0;
        let wave = across_at(&self.cubics, along);
        Some(if self.inward < 0.0 {
            (origin, wave)
        } else {
            (wave, origin + self.across)
        })
    }
}

fn snap(position: f64) -> f64 {
    position.round() + CRISP
}

/// The domain end that tears: the one nearest the off-screen zero. `None` when the domain holds
/// zero, where the axis has a real floor to draw.
fn torn_edge_value(lo: f64, hi: f64) -> Option<f64> {
    if lo > 0.0 {
        Some(lo)
    } else if hi < 0.0 {
        Some(hi)
    } else {
        None
    }
}

/// Which way the crests point from an edge at `edge` on a span of `extent` starting at `start`:
/// toward the middle, `-1` (down the axis' pixel coordinate) from the far half, `+1` from the near
/// one.
fn torn_inward(edge: f64, start: f64, extent: f64) -> f64 {
    if edge > start + extent / 2.0 {
        -1.0
    } else {
        1.0
    }
}

/// The wave's across-coordinate at `along` (clamped to its span). Each cubic's along is monotone,
/// so bisect the parameter.
fn across_at(cubics: &[Cubic], along: f64) -> f64 {
    let first = cubics[0][0].0;
    let x = along.max(first).min(cubics[cubics.len() - 1][3].0);
    let c = cubics
        .iter()
        .find(|c| x <= c[3].0)
        .expect("x is clamped to the last cubic's end");
    let at = |t: f64, f: fn(&(f64, f64)) -> f64| {
        let u = 1.0 - t;
        u * u * u * f(&c[0])
            + 3.0 * u * u * t * f(&c[1])
            + 3.0 * u * t * t * f(&c[2])
            + t * t * t * f(&c[3])
    };
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..40 {
        let mid = (lo + hi) / 2.0;
        if at(mid, |p| p.0) < x {
            lo = mid
        } else {
            hi = mid
        }
    }
    at((lo + hi) / 2.0, |p| p.1)
}

/// The torn edge from `x1` to `x2` as cubics: troughs on `edge`, crests at `amplitude` toward
/// `inward`, so the wave never leaves the plot. `sharpness` blends each quarter-wave from a sine
/// toward its straight chord. When `ticks` are evenly spaced, the period snaps to a subdivision or
/// multiple of the spacing so a trough lands on every tick it spans, except a tick too close to
/// either end for a whole period (under period/√2 from the previous stop), which carries no
/// trough; otherwise the whole width holds `round(width / wavelength)`.
fn torn_cubics(
    x1: f64,
    x2: f64,
    edge: f64,
    inward: f64,
    ticks: &[f64],
    tear: &TornBaseline,
) -> Vec<Cubic> {
    // Cubic control ratios approximating `sin` on [0, π/2].
    const STEEP_TO_FLAT: [(f64, f64); 2] = [(0.326_115, 0.512_261), (0.638_087, 1.0)];
    let gaps: Vec<f64> = ticks.windows(2).map(|w| w[1] - w[0]).collect();
    let (lo, hi) = gaps
        .iter()
        .fold((f64::MAX, f64::MIN), |(lo, hi), &g| (lo.min(g), hi.max(g)));
    let regular = !gaps.is_empty() && lo > 0.0 && hi <= lo * 1.15;
    let (stops, period) = if regular {
        let mean = gaps.iter().sum::<f64>() / gaps.len() as f64;
        // The period is `mean × r`, r ∈ {1/n} ∪ {n}: subdivide a column or span n of them,
        // whichever lands nearest the wavelength in log space. Spanning n puts troughs on every
        // n-th gridline; a stretch between two stops that isn't a whole multiple of the period
        // (the ends, when the ticks don't divide evenly) gets the nearest whole number of
        // periods, pinned at both stops.
        let divides = (mean / tear.wavelength).ceil() as usize + 1;
        let (span, divide) = (1..=gaps.len())
            .map(|n| (n, 1))
            .chain((1..=divides).map(|n| (1, n)))
            .min_by(|&(s1, d1), &(s2, d2)| {
                let off =
                    |s: usize, d: usize| (mean * s as f64 / d as f64 / tear.wavelength).ln().abs();
                off(s1, d1).total_cmp(&off(s2, d2))
            })
            .expect("at least one candidate");
        let period = mean * span as f64 / divide as f64;
        // A stretch shorter than period/√2 — the bound every other tooth keeps — would squeeze a
        // tooth, so its stop is dropped: ticks left to right against the previous stop, then the
        // last stop against `x2` (band-step ticks sit half a step inside the plot).
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
            let h = [
                0.0,
                tear.amplitude / 2.0,
                tear.amplitude,
                tear.amplitude / 2.0,
                0.0,
            ];
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

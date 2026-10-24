//! The torn baseline (`axis.grid.torn_baseline`): one law for the wave and for
//! when an axis tears, shared by every family that draws it. Cubics are in
//! (along, across) coordinates of the edge; a vertical edge transposes them
//! when it paints.

use std::fmt::Write;

use crate::common::TornBaseline;

/// One cubic Bézier of the tear: start, two controls, end.
pub type Cubic = [(f64, f64); 4];

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

pub fn torn_path(cubics: &[Cubic], vertical: bool) -> String {
    let at = |(along, across): (f64, f64)| if vertical { (across, along) } else { (along, across) };
    let (x0, y0) = at(cubics[0][0]);
    let mut d = format!("M{x0:.2},{y0:.2}");
    for [_, c1, c2, b] in cubics {
        let ((c1x, c1y), (c2x, c2y), (bx, by)) = (at(*c1), at(*c2), at(*b));
        let _ = write!(d, "C{c1x:.2},{c1y:.2} {c2x:.2},{c2y:.2} {bx:.2},{by:.2}");
    }
    d
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

/// The tear's across-coordinate at `along` (clamped to its span). Each cubic's x is monotone,
/// so bisect the parameter.
pub fn torn_across_at(cubics: &[Cubic], x: f64) -> f64 {
    let first = cubics[0][0].0;
    let x = x.max(first).min(cubics[cubics.len() - 1][3].0);
    let c = cubics.iter().find(|c| x <= c[3].0).expect("x is clamped to the last cubic's end");
    let at = |t: f64, f: fn(&(f64, f64)) -> f64| {
        let u = 1.0 - t;
        u * u * u * f(&c[0]) + 3.0 * u * u * t * f(&c[1]) + 3.0 * u * t * t * f(&c[2]) + t * t * t * f(&c[3])
    };
    let (mut lo, mut hi) = (0.0, 1.0);
    for _ in 0..40 {
        let mid = (lo + hi) / 2.0;
        if at(mid, |p| p.0) < x { lo = mid } else { hi = mid }
    }
    at((lo + hi) / 2.0, |p| p.1)
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
        let tear = tear.filter(|t| t.visible)?;
        torn_edge_value(lo, hi).map(|edge| Self { tear, edge })
    }

    /// Paints the wave with crests pointing into a plot `across` px deep on the
    /// value axis and `along` px wide the other way. `edge_px` is the edge's
    /// position on the value axis; `vertical` is an edge that runs top to bottom.
    pub fn paint(&self, svg: &mut String, edge_px: f64, across: f64, along: f64, vertical: bool, ink: &str, width: f64) {
        let inward = torn_inward(edge_px, 0.0, across);
        let cubics = torn_cubics(0.0, along, edge_px, inward, &[], self.tear);
        let d = torn_path(&cubics, vertical);
        let _ = write!(
            svg,
            "<path class=\"torn-baseline\" d=\"{d}\" fill=\"none\" stroke=\"{}\" stroke-width=\"{width}\"/>",
            print::xml_escape_attr(ink)
        );
    }
}

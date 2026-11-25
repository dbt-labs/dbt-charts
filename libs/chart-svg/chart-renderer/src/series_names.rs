//! In-plot series names: each series' name set horizontally beside its own line, or
//! inside its own band on an area chart. Pure and deterministic. Candidates are scored
//! with `placement`'s geometry, obstacle model and ownership test; a placed name becomes
//! an obstacle for the names after it.

use crate::placement::{
    nearer_foreign, path_distance, segment_box_distance, Field, Obstacle, EPS, LINE_PAD,
};
use crate::vega_fit::Bounds;
use diagnostic::{codes, Diagnostic};
use serde_json::json;
use std::cell::RefCell;
use std::cmp::Ordering;
use std::collections::HashMap;
use std::rc::Rc;

pub(crate) mod curve;

/// A name whose line rises or falls less than this across it, in font sizes, reads best straight:
/// a short name on a slope, or a name centered on a peak.
const LEVEL_RISE_FONT_SIZES: f64 = 0.25;
/// A straight name whose line rises or falls at least this across it, in font sizes, leans off
/// its line: a name along the line reads better wherever one fits.
const LEAN_RISE_FONT_SIZES: f64 = 0.1;
/// A peak or dip counts as a crest once it stands this far past one side of a name, in font
/// sizes: shallower is a wiggle.
const CREST_FONT_SIZES: f64 = 0.5;
/// How much further than the nearest a name along its line may stand off it, in font sizes.
const OFFS: [f64; 4] = [0.0, 0.25, 0.5, 0.75];
/// A line turning more than this at one point has a corner a name along it would round.
const CORNER_DEGREES: f64 = 30.0;
/// The most a glyph of a name along its line tilts, in degrees: steeper, the name stays straight.
const MAX_TILT_DEGREES: f64 = 40.0;
/// A glyph whose path tilts up to this many degrees past `MAX_TILT_DEGREES` is drawn at the cap,
/// so a name stays tight on its line: a difference this small doesn't show. Steeper, the name
/// stays straight.
const TILT_CLAMP_DEGREES: f64 = 3.0;
/// The most x positions tried per series.
const MAX_SAMPLES: usize = 64;
/// Air between a line's clearance (`LINE_PAD`) and the name set beside it.
const NAME_GAP: f64 = 1.0;
/// A band never thicker than this cannot be read at all.
const TOO_THIN: f64 = 2.0;
/// Scores are bucketed to this many pixels so near-equal spots tie and a later term decides.
const STEP: f64 = 2.0;
/// A name keeps this share of the plot width from each side, or a label height if larger:
/// names stacked against a side look crowded.
const SIDE_SHARE: f64 = 0.04;
/// Names this share of the plot width apart count as clearly spread; farther is no better.
const SPREAD_CAP_SHARE: f64 = 1.0 / 3.0;
/// The x distance between names that separates one spread step from the next.
const SPREAD_STEP_SHARE: f64 = 0.05;
/// Stands in for "no other series" when ranking clearance.
const FAR: f64 = 1e6;

/// `WARN-DIRECT-OVERLAY-COLLIDES`: `names` found no clear spot and are drawn where they overlap least.
pub(crate) fn warn_collisions(chart_id: &str, names: &[&str]) {
    if !names.is_empty() {
        diagnostic::warn(Diagnostic::new(
            &codes::WARN_DIRECT_OVERLAY_COLLIDES,
            [
                ("chart_id", json!(chart_id)),
                ("names", json!(names.join(", "))),
            ],
        ));
    }
}

/// What a name is set against.
pub(crate) enum Geometry {
    /// The runs of a polyline in plot pixels, ascending in x; nothing is drawn between runs.
    Line(Vec<Vec<(f64, f64)>>),
    /// Runs of `(x, y_hi, y_lo)`, ascending in x; `y_hi` is the band's upper (smaller y) edge.
    Band(Vec<(f64, f64, f64)>),
}

/// A stacked series' vertices as band runs: each `(x, y, y')` with its edges ordered, and the
/// vertices a link's imputed rows pile at one x merged into the widest span.
pub(crate) fn band_of(runs: &[Vec<(f64, f64, f64)>]) -> Vec<(f64, f64, f64)> {
    let mut band: Vec<(f64, f64, f64)> = Vec::new();
    for &(x, a, b) in runs.iter().flatten() {
        let (hi, lo) = (a.min(b), a.max(b));
        match band.last_mut() {
            Some(last) if last.0 == x => (last.1, last.2) = (last.1.min(hi), last.2.max(lo)),
            _ => band.push((x, hi, lo)),
        }
    }
    band
}

pub(crate) struct Name {
    /// Tells the series apart: a line name is judged against every other owner's runs.
    pub(crate) owner: usize,
    /// The name's ink box size.
    pub(crate) size: (f64, f64),
    pub(crate) text: String,
    /// Where each `char` of `text` starts and the last ends, along the name: `size.0` last.
    pub(crate) edges: Vec<f64>,
    pub(crate) geometry: Geometry,
    /// `key.direct.overlay_x`: the plot-pixel x the author wants this name set at. An acceptable spot
    /// there beats every other; with none, the name takes the usual search.
    pub(crate) prefer_x: Option<f64>,
}

/// Each glyph's center and angle, with its turned ink.
pub(crate) type Glyphs = Vec<(curve::Glyph, curve::Quad)>;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Spot {
    /// The ink box; around every glyph when the name follows its line.
    pub(crate) b: Bounds,
    /// Set when the name runs along its line, glyph by glyph; `None` draws it straight in `b`.
    pub(crate) glyphs: Option<Glyphs>,
}

impl Spot {
    /// The boxes the name's ink fills: one per glyph along a line, else `b`.
    pub(crate) fn boxes(&self) -> Vec<Bounds> {
        match &self.glyphs {
            Some(glyphs) => glyphs.iter().map(|(_, q)| curve::quad_bounds(q)).collect(),
            None => vec![self.b],
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Outcome {
    Placed(Spot),
    /// No clear spot: the least-overlapping spot, to draw and warn about.
    Collides(Spot),
    /// The band is never as tall as the text: the caller names it in the key beside the plot.
    Thin,
    /// The band is never thicker than `TOO_THIN`: the caller names it in the key beside the plot.
    TooThin,
}

struct Cand {
    b: Bounds,
    glyphs: Option<Glyphs>,
    /// Pixels the box overflows its room or fit, or the line strokes it touches.
    misfit: f64,
    /// Nearer another series than its own.
    foreign: bool,
    /// Straight beside a stretch of its line that rises or falls across it.
    askew: bool,
    /// Straight beside a stretch that rises or falls a little across it, short of `askew`.
    leans: bool,
    /// How far the peak or dip a straight name sits over stands out from its line within a name's
    /// width on each side; 0 for none.
    crest: f64,
    overlap: f64,
    /// Pixels to the nearest other series, mark or placed name, all around the box.
    clear: f64,
    /// How much gridline runs through the ink: a level name along a gridline is worst.
    grid: f64,
    /// A band's thickness under the box's center; 0 for a line name.
    thick: f64,
    /// Set at the name's `prefer_x`.
    preferred: bool,
    /// A preferred spot centered on its x, the way a label sits over its value.
    centered: bool,
    /// How far a preferred spot sits from the name centered on its x at its usual height: the
    /// lift a centered spot needs to clear its line, or half the name's width for a spot aligned
    /// to one side; 0 for every other spot.
    moved: f64,
    /// How much further off its line than the nearest a name along it stands.
    off: f64,
}

impl Cand {
    /// Where the name reads from: its middle glyph along a line, else its box's center.
    fn center(&self) -> (f64, f64) {
        match &self.glyphs {
            Some(glyphs) => glyphs[glyphs.len() / 2].0.at,
            None => Law::center(&self.b),
        }
    }

    fn acceptable(&self) -> bool {
        self.misfit < EPS && !self.foreign && self.overlap < EPS
    }
}

fn cmp_eps(a: f64, b: f64) -> Ordering {
    if (a - b).abs() < EPS {
        Ordering::Equal
    } else {
        a.total_cmp(&b)
    }
}

/// Places every name against `field`. The name with the fewest acceptable spots goes first;
/// each placed name joins `field.obstacles` for the rest.
///
/// A name with an acceptable spot at its `prefer_x` goes before the rest, so no other name's
/// pick takes the spot first.
///
/// Among acceptable spots the law is, in order: the spot at the name's `prefer_x`, if any; not
/// against a plot side (`Law::inset`); not straight beside a slope; along its line or beside a
/// level stretch, not straight beside a stretch that leans; straight over the peak or dip of its
/// line that stands out most; a band name centered where its band is thickest; the most open room
/// around the box, by the label height; the least
/// gridline under the ink; farthest in x from names already placed; a name along its line
/// nearest it; highest above the plot's baseline; then nearest the plot's center,
/// leftmost, topmost.
pub(crate) fn place_series_names(names: &[Name], field: &mut Field, follow: bool) -> Vec<Outcome> {
    let along: Vec<Along> = names.iter().map(|n| Along::of(n, follow)).collect();
    let mut out: Vec<Option<Outcome>> = names.iter().map(unreadable_band).collect();
    let mut order: Vec<(bool, usize, usize)> = (0..names.len())
        .filter(|&i| out[i].is_none())
        .map(|i| {
            let cands = candidates(&names[i], &along[i], names, field);
            (
                !cands.iter().any(|c| c.preferred && c.acceptable()),
                cands.iter().filter(|c| c.acceptable()).count(),
                i,
            )
        })
        .collect();
    order.sort_unstable();
    let mut named: Vec<Bounds> = vec![];
    for (_, _, i) in order {
        let cands = candidates(&names[i], &along[i], names, field);
        let law = Law::new(field.plot, names[i].size.1, &named);
        let best = cands
            .iter()
            .reduce(|a, b| if law.beats(b, a) { b } else { a })
            .expect("a name has candidates");
        let spot = Spot {
            b: best.b,
            glyphs: best.glyphs.clone(),
        };
        named.push(spot.b);
        field
            .obstacles
            .extend(spot.boxes().into_iter().map(Obstacle::label));
        out[i] = Some(if best.acceptable() {
            Outcome::Placed(spot)
        } else {
            Outcome::Collides(spot)
        });
    }
    out.into_iter()
        .map(|o| o.expect("every name has an outcome"))
        .collect()
}

/// Names follow their lines only where they all still clear: a name along its line takes no spot
/// another name needed. Under `auto`, which chooses between the plot and its fallback by straight
/// names alone, a name along its line never turns a chart from one to the other either.
pub(crate) fn follow_where_clear(place: impl Fn(bool) -> Vec<Outcome>, auto: bool) -> Vec<Outcome> {
    let collides = |out: &[Outcome]| out.iter().any(|o| matches!(o, Outcome::Collides(_)));
    let straight = place(false);
    if collides(&straight) {
        return if auto { straight } else { place(true) };
    }
    Some(place(true))
        .filter(|along| !collides(along))
        .unwrap_or(straight)
}

fn unreadable_band(n: &Name) -> Option<Outcome> {
    let Geometry::Band(runs) = &n.geometry else {
        return None;
    };
    let thickest = runs.iter().map(|r| r.2 - r.1).fold(0.0, f64::max);
    if thickest <= TOO_THIN {
        Some(Outcome::TooThin)
    } else if thickest < n.size.1 {
        Some(Outcome::Thin)
    } else {
        None
    }
}

/// Every data x, plus evenly spaced ones up to `MAX_SAMPLES` (or an even thinning of a longer series).
fn sample_xs(data: &[f64]) -> Vec<f64> {
    let (first, last) = (data[0], data[data.len() - 1]);
    let mut xs: Vec<f64> = if data.len() >= MAX_SAMPLES {
        (0..MAX_SAMPLES)
            .map(|k| data[k * (data.len() - 1) / (MAX_SAMPLES - 1)])
            .collect()
    } else {
        let extra = MAX_SAMPLES - data.len();
        data.iter()
            .copied()
            .chain((0..extra).map(|k| first + (last - first) * (k as f64 + 0.5) / extra as f64))
            .collect()
    };
    xs.sort_by(f64::total_cmp);
    xs.dedup();
    xs
}

/// The piecewise-linear value of ascending `(x, y)` points at `x`, held flat past either end.
/// The height at `x` of the polyline through `pts`, ascending in x, held level past its ends.
fn lerp_at(pts: &[(f64, f64)], x: f64) -> f64 {
    if pts.len() == 1 {
        return pts[0].1;
    }
    let k = pts.partition_point(|p| p.0 < x).clamp(1, pts.len() - 1);
    let (a, b) = (pts[k - 1], pts[k]);
    if (b.0 - a.0).abs() < EPS {
        a.1
    } else {
        a.1 + (b.1 - a.1) * ((x - a.0) / (b.0 - a.0)).clamp(0.0, 1.0)
    }
}

fn run_box_distance(run: &[(f64, f64)], b: &Bounds) -> f64 {
    match run {
        [only] => segment_box_distance(*only, *only, b),
        _ => run
            .windows(2)
            .map(|w| segment_box_distance(w[0], w[1], b))
            .fold(f64::INFINITY, f64::min),
    }
}

fn runs_box_distance(runs: &[Vec<(f64, f64)>], b: &Bounds) -> f64 {
    runs.iter()
        .map(|run| run_box_distance(run, b))
        .fold(f64::INFINITY, f64::min)
}

/// The height of the run under `x`, nearest run's end where none covers it.
fn runs_y_at(runs: &[Vec<(f64, f64)>], x: f64) -> f64 {
    let run = runs
        .iter()
        .filter(|r| !r.is_empty())
        .min_by(|a, b| {
            let off = |r: &Vec<(f64, f64)>| (r[0].0 - x).max(x - r[r.len() - 1].0).max(0.0);
            off(a).total_cmp(&off(b))
        })
        .expect("a named line has a point");
    lerp_at(&run, x)
}

fn runs_distance(runs: &[Vec<(f64, f64)>], p: (f64, f64)) -> f64 {
    runs.iter()
        .map(|run| path_distance(run, p))
        .fold(f64::INFINITY, f64::min)
}

/// How far `b` sticks out of `room`, in pixels.
fn overflow(b: &Bounds, room: &Bounds) -> f64 {
    (room.x1 - b.x1).max(0.0)
        + (b.x2 - room.x2).max(0.0)
        + (room.y1 - b.y1).max(0.0)
        + (b.y2 - room.y2).max(0.0)
}

/// The boxes a name keeps off: strokes are the runs' business, fills and gridlines may be crossed.
fn solid(field: &Field) -> impl Iterator<Item = &Obstacle> {
    field
        .obstacles
        .iter()
        .filter(|o| o.hard() && o.stroke.is_none())
}

fn obstacle_overlap(b: &Bounds, field: &Field) -> f64 {
    solid(field).map(|o| o.overlap(b)).sum()
}

/// The area the obstacle `o`, padded, shares with the boxes around `inks` that it truly meets.
fn ink_overlap(inks: &[curve::Quad], o: &Obstacle) -> f64 {
    inks.iter()
        .map(|q| (q, o.overlap(&curve::quad_bounds(q))))
        .filter(|(q, area)| *area > 0.0 && curve::quad_meets_box(q, &o.b.expand(o.pad)))
        .map(|(_, area)| area)
        .sum()
}

fn runs_ink_distance(runs: &[Vec<(f64, f64)>], q: &curve::Quad) -> f64 {
    // The ink lies inside its box, so the box's distance bounds the ink's from below: a segment
    // no nearer by box than the best so far needs no exact test.
    let b = curve::quad_bounds(q);
    let mut best = f64::INFINITY;
    for run in runs {
        let segments: Vec<((f64, f64), (f64, f64))> = match run.as_slice() {
            [only] => vec![(*only, *only)],
            _ => run.windows(2).map(|w| (w[0], w[1])).collect(),
        };
        for (a, c) in segments {
            if segment_box_distance(a, c, &b) < best {
                best = best.min(curve::quad_segment_distance(q, a, c));
            }
        }
    }
    best
}

/// The boxes anchored at `x` (left, center and right aligned) for each box top in `tops`.
fn boxes(x: f64, tops: &[f64], (w, h): (f64, f64)) -> Vec<Bounds> {
    tops.iter()
        .flat_map(|&y| [x, x - w / 2.0, x - w].map(|x1| Bounds::new(x1, y, x1 + w, y + h)))
        .collect()
}

fn candidates(n: &Name, along: &Along, names: &[Name], field: &Field) -> Vec<Cand> {
    let plot = Bounds::new(0.0, 0.0, field.plot.0, field.plot.1);
    match &n.geometry {
        Geometry::Line(runs) => {
            let data: Vec<f64> = runs.iter().flatten().map(|p| p.0).collect();
            let extent = Bounds::new(
                data[0].max(plot.x1),
                plot.y1,
                data[data.len() - 1].min(plot.x2),
                plot.y2,
            );
            let others: Vec<&[Vec<(f64, f64)>]> = names
                .iter()
                .filter(|o| o.owner != n.owner)
                .filter_map(|o| match &o.geometry {
                    Geometry::Line(runs) => Some(runs.as_slice()),
                    Geometry::Band(_) => None,
                })
                .collect();
            // At an author's x a name stands off its line over the box's whole width, not just at
            // x, and by the pad measured square to the line, so a sloped line lifts it. Each spot
            // scores how far it moved from centered at its usual height: the lift plus any
            // sideways shift, and the smaller move wins.
            let stood_off = |x: f64| {
                let run = runs.iter().find(|r| r[0].0 <= x && x <= r[r.len() - 1].0)?;
                let line = move |e: f64| lerp_at(run, e);
                let (w, h, pad) = (n.size.0, n.size.1, LINE_PAD + NAME_GAP);
                let y = line(x);
                let spots = [x - w / 2.0, x, x - w].into_iter().flat_map(move |x1| {
                    let x2 = x1 + w;
                    let mut pts: Vec<(f64, f64)> = run
                        .iter()
                        .copied()
                        .filter(|p| p.0 > x1 && p.0 < x2)
                        .collect();
                    pts.insert(0, (x1, line(x1)));
                    pts.push((x2, line(x2)));
                    let slope = pts
                        .windows(2)
                        .filter(|s| s[1].0 - s[0].0 > EPS)
                        .map(|s| ((s[1].1 - s[0].1) / (s[1].0 - s[0].0)).abs())
                        .fold(0.0, f64::max);
                    let gap = pad * (1.0 + slope * slope).sqrt();
                    let (lo, hi) = pts
                        .iter()
                        .fold((f64::INFINITY, f64::NEG_INFINITY), |(lo, hi), p| {
                            (lo.min(p.1), hi.max(p.1))
                        });
                    let shift = ((x1 + x2) / 2.0 - x).abs();
                    let (above, below) = (lo - gap - h, hi + gap);
                    [(above, y - pad - h - above), (below, below - (y + pad))]
                        .map(|(top, lift)| (Bounds::new(x1, top, x2, top + h), lift + shift))
                });
                Some(spots)
            };
            let cand = |b: Bounds, prefer: Option<f64>, moved: f64| Cand {
                preferred: prefer.is_some(),
                centered: prefer.is_some_and(|x| ((b.x1 + b.x2) / 2.0 - x).abs() < EPS),
                moved,
                ..line_cand(&[curve::box_quad(&b)], None, runs, &others, &extent, field)
            };
            // The twins along the line beside `x`, centered on it: a line's points can block the
            // name right against it, so it stands off a little further only then.
            let twins = |k: usize, x: f64, below: bool| {
                let mut twins: Vec<Cand> = vec![];
                for (off, glyphs) in along.at(n, k, x, below).iter().cloned() {
                    let inks: Vec<curve::Quad> = glyphs.iter().map(|(_, q)| *q).collect();
                    let twin = Cand {
                        off,
                        ..line_cand(&inks, Some(glyphs), runs, &others, &extent, field)
                    };
                    // A name rounding a corner of its line bends where the line does not: near one
                    // it sits straight. The smooth it is drawn along reaches past its ends.
                    let reach = curve::MAX_DRAW_SMOOTH_FONT_SIZES * n.size.1;
                    if corner_near(
                        runs,
                        twin.b.x1 - reach,
                        twin.b.x2 + reach,
                        n.size.1,
                        n.size.0,
                    ) {
                        break;
                    }
                    let fits = twin.acceptable();
                    twins.push(twin);
                    if fits {
                        break;
                    }
                }
                twins
            };
            let run_at = |x: f64| {
                runs.iter()
                    .position(|r| r[0].0 <= x && x <= r[r.len() - 1].0)
            };
            // At an author's x a name along its line is centered on x and moves only as far as it
            // stands off its line.
            let preferred: Vec<Cand> = n
                .prefer_x
                .into_iter()
                .flat_map(|x| {
                    let straight = stood_off(x)
                        .into_iter()
                        .flatten()
                        .map(move |(b, moved)| cand(b, Some(x), moved));
                    let along = run_at(x).into_iter().flat_map(move |k| {
                        [false, true]
                            .into_iter()
                            .flat_map(move |below| twins(k, x, below))
                            .map(|c| Cand {
                                preferred: true,
                                centered: true,
                                moved: c.off,
                                ..c
                            })
                    });
                    straight.chain(along).collect::<Vec<_>>()
                })
                .collect();
            sample_xs(&data)
                .into_iter()
                .filter_map(|x| {
                    let k = run_at(x)?;
                    let y = lerp_at(&runs[k], x);
                    let tops = [
                        (y - LINE_PAD - NAME_GAP - n.size.1, false),
                        (y + LINE_PAD + NAME_GAP, true),
                    ];
                    // Each spot beside the line also has its twin along the line; the law prefers
                    // the twin to a box askew of a slope.
                    Some(
                        tops.into_iter()
                            .flat_map(|(top, below)| {
                                boxes(x, &[top], n.size)
                                    .into_iter()
                                    .map(|b| cand(b, None, 0.0))
                                    .chain(twins(k, x, below))
                            })
                            .collect::<Vec<_>>(),
                    )
                })
                .flatten()
                .chain(preferred)
                .collect()
        }
        Geometry::Band(runs) => {
            let data: Vec<f64> = runs.iter().map(|r| r.0).collect();
            let extent = Bounds::new(
                data[0].max(plot.x1),
                plot.y1,
                data[data.len() - 1].min(plot.x2),
                plot.y2,
            );
            let covered = |x: f64| data[0] <= x && x <= data[data.len() - 1];
            let cand = |b: Bounds, prefer: Option<f64>| Cand {
                preferred: prefer.is_some(),
                centered: prefer.is_some_and(|x| ((b.x1 + b.x2) / 2.0 - x).abs() < EPS),
                moved: prefer.map_or(0.0, |x| ((b.x1 + b.x2) / 2.0 - x).abs()),
                ..band_cand(b, runs, &extent, field)
            };
            sample_xs(&data)
                .into_iter()
                .flat_map(|x| boxes(x, &[0.0], n.size))
                .map(|b| cand(b, None))
                .chain(
                    n.prefer_x
                        .filter(|&x| covered(x))
                        .into_iter()
                        .flat_map(|x| boxes(x, &[0.0], n.size).into_iter().map(move |b| (b, x)))
                        .map(|(b, x)| cand(b, Some(x))),
                )
                .collect()
        }
    }
}

/// Whether `text` draws as one glyph per `char`, which a name along its line sets one by one: no
/// empty name, and nothing a shaper joins or stacks (combining marks, joiners, scripts that
/// connect their letters), which only reads set whole.
fn curves_by_char(text: &str) -> bool {
    !text.is_empty()
        && text.chars().all(|c| {
            let u = u32::from(c);
            // Basic Latin through Cyrillic, minus the combining marks of both.
            u < 0x0530 && !(0x0300..0x0370).contains(&u) && !(0x0483..0x048A).contains(&u)
        })
}

/// A run as drawn and turned over, for names above it and below it: below the line is above it
/// with the plot turned over, so the same smooth, lift and offset serve both.
type Chains = (curve::Chain, curve::Chain);
/// The names along a line at one spot, each with how far past the nearest it stands off.
type Twins = Rc<Vec<(f64, Glyphs)>>;

/// A line name's runs as chains, and each spot along them already worked out: where a name
/// can sit along its line never depends on what is placed, and every pass over the candidates
/// asks again.
struct Along {
    /// `None` when names stay straight.
    chains: Option<Vec<Chains>>,
    spots: RefCell<HashMap<(usize, u64, bool), Twins>>,
}

impl Along {
    fn of(n: &Name, follow: bool) -> Self {
        let chains = match &n.geometry {
            Geometry::Line(runs) if follow => Some(
                runs.iter()
                    .map(|r| {
                        let turned: Vec<(f64, f64)> = r.iter().copied().map(flip(true)).collect();
                        (curve::Chain::new(r), curve::Chain::new(&turned))
                    })
                    .collect(),
            ),
            _ => None,
        };
        Along {
            chains,
            spots: RefCell::new(HashMap::new()),
        }
    }

    /// `along_line` for run `k` at `x`, worked out once.
    fn at(&self, n: &Name, k: usize, x: f64, below: bool) -> Twins {
        let Some(chains) = &self.chains else {
            return Rc::default();
        };
        let key = (k, x.to_bits(), below);
        if let Some(spot) = self.spots.borrow().get(&key) {
            return Rc::clone(spot);
        }
        let spot = Rc::new(along_line(n, &chains[k], x, below));
        self.spots.borrow_mut().insert(key, Rc::clone(&spot));
        spot
    }
}

fn flip(below: bool) -> impl Fn((f64, f64)) -> (f64, f64) {
    move |(x, y)| if below { (x, -y) } else { (x, y) }
}

/// A name beside its line at `x`, above it or `below`, glyph by glyph along a smooth of the line
/// and lifted clear of the chain, at each of `OFFS` further out, with how far; none where the
/// stretch bends past `curve::gentle`, runs too near the line's ends, tilts past
/// `MAX_TILT_DEGREES`, or barely rises across the name, where a straight box reads better.
fn along_line(n: &Name, chains: &Chains, x: f64, below: bool) -> Vec<(f64, Glyphs)> {
    if !curves_by_char(&n.text) {
        return vec![];
    }
    assert_eq!(
        n.text.chars().count() + 1,
        n.edges.len(),
        "an edge for every char"
    );
    let ((width, font), smooth) = (n.size, curve::SMOOTH_FONT_SIZES * n.size.1);
    let (flip, run) = (flip(below), if below { &chains.1 } else { &chains.0 });
    let Some(stretch) = curve::stretch(run, x, width, smooth) else {
        return vec![];
    };
    if !curve::gentle(&curve::bend(&stretch, font / 2.0), font) {
        return vec![];
    }
    // A font size spare at each end: an offset over a dip is shorter than the smooth under it.
    let Some(path) = curve::drawn(run, x, width + 2.0 * font, font) else {
        return vec![];
    };
    let above = crate::vega_fit::baseline_rise(font, "alphabetic");
    let gap = LINE_PAD + NAME_GAP + if below { above } else { font - above };
    let lift = curve::lift(run, &path);
    let mids: Vec<f64> = n
        .edges
        .windows(2)
        .map(|e| (e[0] + e[1]) / 2.0 - width / 2.0)
        .collect();
    OFFS.iter()
        .filter_map(|share| {
            let off = share * font;
            let glyphs = curve::glyphs(&curve::offset(&path, gap + off + lift), &mids)?;
            let rise = (glyphs[glyphs.len() - 1].at.1 - glyphs[0].at.1).abs();
            if rise < LEVEL_RISE_FONT_SIZES * font
                || glyphs
                    .iter()
                    .any(|g| g.angle.abs() > MAX_TILT_DEGREES + TILT_CLAMP_DEGREES)
            {
                return None;
            }
            let glyphs = glyphs
                .into_iter()
                .map(|g| {
                    let angle = g.angle.clamp(-MAX_TILT_DEGREES, MAX_TILT_DEGREES);
                    curve::Glyph {
                        at: flip(g.at),
                        angle: if below { -angle } else { angle },
                    }
                })
                .zip(n.edges.windows(2))
                .map(|(g, e)| (g, curve::ink_quad(&g, e[1] - e[0], above, font - above)))
                .collect();
            Some((off, glyphs))
        })
        .collect()
}

/// A line name's candidate inking `inks`: one box when straight, a turned box per glyph along the
/// line.
/// A name along its line clears its own chain by construction (`curve::lift`), so only a straight
/// box is tested against it.
fn line_cand(
    inks: &[curve::Quad],
    glyphs: Option<Glyphs>,
    own: &[Vec<(f64, f64)>],
    others: &[&[Vec<(f64, f64)>]],
    room: &Bounds,
    field: &Field,
) -> Cand {
    let mut b = curve::quad_bounds(&inks[0]);
    for q in &inks[1..] {
        b.union(&curve::quad_bounds(q));
    }
    // A straight box is its own ink: the box tests read it exactly, and cheaper.
    let straight = glyphs.is_none();
    let distance = |runs: &[Vec<(f64, f64)>], q: &curve::Quad| {
        if straight {
            runs_box_distance(runs, &b)
        } else {
            runs_ink_distance(runs, q)
        }
    };
    let overlap = |o: &Obstacle| {
        if straight {
            o.overlap(&b)
        } else {
            ink_overlap(inks, o)
        }
    };
    let stroke_hits = others
        .iter()
        .filter(|runs| inks.iter().any(|q| distance(runs, q) < LINE_PAD))
        .count()
        + usize::from(straight && distance(own, &inks[0]) < LINE_PAD);
    let mean = |pts: &[(f64, f64)]| {
        let n = pts.len() as f64;
        (
            pts.iter().map(|p| p.0).sum::<f64>() / n,
            pts.iter().map(|p| p.1).sum::<f64>() / n,
        )
    };
    let (first, last) = (inks[0], inks[inks.len() - 1]);
    // A name is wide: it belongs to its own line along its whole length, not just at its center.
    let foreign = [
        mean(&[first[0], first[3]]),
        mean(&inks[inks.len() / 2]),
        mean(&[last[1], last[2]]),
    ]
    .into_iter()
    .any(|p| {
        let nearest_other = others
            .iter()
            .map(|runs| runs_distance(runs, p))
            .fold(FAR, f64::min);
        nearer_foreign(runs_distance(own, p), nearest_other)
    });
    let clear = inks
        .iter()
        .flat_map(|q| {
            others
                .iter()
                .map(|runs| distance(runs, q))
                .chain(solid(field).map(|o| o.gap(&curve::quad_bounds(q))))
        })
        .fold(FAR, f64::min);
    let (rise, crests) = if straight {
        let rise = (runs_y_at(own, b.x2) - runs_y_at(own, b.x1)).abs();
        // Its sides differing a little is no lean: a straight name reads level over a crest.
        (
            rise / (b.y2 - b.y1),
            crest_height(own, b.x1, b.x2, b.y2 - b.y1),
        )
    } else {
        (0.0, 0.0)
    };
    Cand {
        misfit: stroke_hits as f64 + overflow(&b, room),
        foreign,
        askew: rise >= LEVEL_RISE_FONT_SIZES,
        leans: rise >= LEAN_RISE_FONT_SIZES && crests == 0.0,
        crest: crests,
        overlap: solid(field).map(overlap).sum(),
        clear,
        grid: field
            .obstacles
            .iter()
            .filter(|o| o.faint)
            .map(overlap)
            .sum(),
        thick: 0.0,
        preferred: false,
        centered: false,
        moved: 0.0,
        off: 0.0,
        b,
        glyphs,
    }
}

/// Whether `own` has a corner between `x1` and `x2`: a point where it turns more than
/// `CORNER_DEGREES` read over both a `short` and a `long` arm on each side. A wiggle turns only
/// over the short arm and a rounded hump only over the long one; a corner turns over both.
fn corner_near(own: &[Vec<(f64, f64)>], x1: f64, x2: f64, short: f64, long: f64) -> bool {
    own.iter().any(|run| {
        let (first, last) = (run[0].0, run[run.len() - 1].0);
        let heading = |from: (f64, f64), to: (f64, f64)| (to.1 - from.1).atan2(to.0 - from.0);
        let turn = |(x, y): (f64, f64), arm: f64| {
            let (a, c) = ((x - arm).max(first), (x + arm).min(last));
            (a < x && x < c).then(|| {
                (heading((x, y), (c, lerp_at(run, c))) - heading((a, lerp_at(run, a)), (x, y)))
                    .abs()
            })
        };
        let sharp =
            |p: (f64, f64), arm: f64| turn(p, arm).is_some_and(|t| t > CORNER_DEGREES.to_radians());
        run.iter()
            .any(|&p| p.0 > x1 && p.0 < x2 && sharp(p, short) && sharp(p, long))
    })
}

/// How far a peak or dip of `own` between `x1` and `x2` stands out, for a name `font` high: the
/// smaller of its rises or falls within the name's width on each side. 0 when none is deeper
/// than a wiggle on at least one side of the name.
fn crest_height(own: &[Vec<(f64, f64)>], x1: f64, x2: f64, font: f64) -> f64 {
    let (y1, y2) = (runs_y_at(own, x1), runs_y_at(own, x2));
    let (near, far, depth) = (y1.min(y2), y1.max(y2), CREST_FONT_SIZES * font);
    let w = x2 - x1;
    own.iter()
        .flat_map(|run| run.iter().map(move |&p| (run, p)))
        .filter(|&(_, (x, y))| {
            x > x1
                && x < x2
                && ((y < near - EPS && y < far - depth) || (y > far + EPS && y > near + depth))
        })
        .map(|(run, (x, y))| {
            let drop = |lo: f64, hi: f64| {
                run.iter()
                    .filter(|p| p.0 >= lo && p.0 <= hi)
                    .map(|p| (p.1 - y).abs())
                    .chain([(lerp_at(run, lo) - y).abs(), (lerp_at(run, hi) - y).abs()])
                    .fold(0.0, f64::max)
            };
            drop(x - w, x).min(drop(x, x + w))
        })
        .fold(0.0, f64::max)
}

/// A band name sits in the vertical window the band keeps open across the box's whole width.
fn band_cand(b: Bounds, runs: &[(f64, f64, f64)], room: &Bounds, field: &Field) -> Cand {
    let inside = || runs.iter().filter(|r| r.0 > b.x1 && r.0 < b.x2);
    let at = |pick: fn(&(f64, f64, f64)) -> f64, x: f64| {
        lerp_at(&runs.iter().map(|r| (r.0, pick(r))).collect::<Vec<_>>(), x)
    };
    let hi = inside()
        .map(|r| r.1)
        .chain([b.x1, b.x2].map(|x| at(|r| r.1, x)))
        .fold(f64::NEG_INFINITY, f64::max);
    let lo = inside()
        .map(|r| r.2)
        .chain([b.x1, b.x2].map(|x| at(|r| r.2, x)))
        .fold(f64::INFINITY, f64::min);
    let h = b.y2 - b.y1;
    let slack = lo - hi - h;
    let top = (hi + lo) / 2.0 - h / 2.0;
    let b = Bounds::new(b.x1, top, b.x2, top + h);
    Cand {
        misfit: (-slack).max(0.0) + overflow(&b, room),
        foreign: false,
        askew: false,
        leans: false,
        crest: 0.0,
        overlap: obstacle_overlap(&b, field),
        clear: (slack / 2.0).min(solid(field).map(|o| o.gap(&b)).fold(FAR, f64::min)),
        grid: field
            .obstacles
            .iter()
            .filter(|o| o.faint)
            .map(|o| o.overlap(&b))
            .sum(),
        thick: at(|r| r.2, (b.x1 + b.x2) / 2.0) - at(|r| r.1, (b.x1 + b.x2) / 2.0),
        preferred: false,
        centered: false,
        moved: 0.0,
        off: 0.0,
        b,
        glyphs: None,
    }
}

/// What the ranking reads besides the candidates: the plot, and the names already placed.
struct Law<'a> {
    plot: (f64, f64),
    inset: f64,
    label_height: f64,
    named: &'a [Bounds],
}

impl<'a> Law<'a> {
    fn new(plot: (f64, f64), label_height: f64, named: &'a [Bounds]) -> Self {
        Law {
            plot,
            inset: side_inset(plot.0, label_height),
            label_height,
            named,
        }
    }

    fn bucket(v: f64, step: f64) -> i64 {
        ((v + EPS) / step).floor() as i64
    }

    /// Whether `a` ranks above `b`: acceptable before not, then an acceptable preferred spot
    /// (the one that moves the name least from centered on its x, centered on a tie), a worse
    /// fit last, then the law.
    fn beats(&self, a: &Cand, b: &Cand) -> bool {
        let rank = match (a.acceptable(), b.acceptable()) {
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
            // The author's spot beats every other acceptable one, side inset included; a
            // preferred spot that is not acceptable earns nothing.
            (true, true) => b
                .preferred
                .cmp(&a.preferred)
                .then_with(|| cmp_eps(a.moved, b.moved))
                .then(b.centered.cmp(&a.centered))
                .then_with(|| self.order(a, b)),
            (false, false) => cmp_eps(a.misfit, b.misfit)
                .then(a.foreign.cmp(&b.foreign))
                .then(cmp_eps(a.overlap, b.overlap))
                .then_with(|| self.order(a, b)),
        };
        rank == Ordering::Less
    }

    fn center(b: &Bounds) -> (f64, f64) {
        ((b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0)
    }

    fn at_side(&self, b: &Bounds) -> bool {
        b.x1 < self.inset - EPS || b.x2 > self.plot.0 - self.inset + EPS
    }

    fn spread(&self, c: &Cand) -> i64 {
        let cap = SPREAD_CAP_SHARE * self.plot.0;
        let nearest = self
            .named
            .iter()
            .map(|n| (Self::center(n).0 - c.center().0).abs())
            .fold(cap, f64::min);
        Self::bucket(nearest, SPREAD_STEP_SHARE * self.plot.0)
    }

    /// Smaller sorts first, by the law on `place_series_names` after its preferred spot.
    fn order(&self, a: &Cand, b: &Cand) -> Ordering {
        // Where a line stands apart, the name has more room: every label height of it counts.
        let open = |c: &Cand| Self::bucket(c.clear, self.label_height);
        let grid = |c: &Cand| Self::bucket(c.grid, STEP);
        // A peak or dip that stands out more from its line beats a slighter one.
        let extreme = |c: &Cand| Self::bucket(c.crest, self.label_height / 2.0);
        let thick = |c: &Cand| Self::bucket(c.thick, STEP);
        let height = |c: &Cand| Self::bucket(self.plot.1 - c.center().1, STEP);
        let off_center = |c: &Cand| (c.center().0 - self.plot.0 / 2.0).abs();
        self.at_side(&a.b)
            .cmp(&self.at_side(&b.b))
            .then(a.askew.cmp(&b.askew))
            .then(a.leans.cmp(&b.leans))
            .then(extreme(b).cmp(&extreme(a)))
            .then(thick(b).cmp(&thick(a)))
            .then(open(b).cmp(&open(a)))
            .then(grid(a).cmp(&grid(b)))
            .then(self.spread(b).cmp(&self.spread(a)))
            .then(Self::bucket(a.off, STEP).cmp(&Self::bucket(b.off, STEP)))
            .then(height(b).cmp(&height(a)))
            .then_with(|| cmp_eps(off_center(a), off_center(b)))
            .then_with(|| cmp_eps(a.b.x1, b.b.x1))
            .then_with(|| cmp_eps(a.b.y1, b.b.y1))
    }
}

/// The share of the plot width a name keeps from each side, never less than one label height.
fn side_inset(plot_width: f64, label_height: f64) -> f64 {
    (SIDE_SHARE * plot_width).max(label_height)
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLOT: (f64, f64) = (400.0, 200.0);

    fn field(plot: (f64, f64)) -> Field {
        Field {
            plot,
            obstacles: vec![],
            paths: vec![],
        }
    }

    fn line_names(paths: &[Vec<(f64, f64)>], w: f64) -> Vec<Name> {
        paths
            .iter()
            .enumerate()
            .map(|(owner, p)| Name {
                owner,
                size: (w, 14.0),
                text: TEN.into(),
                edges: even(w),
                geometry: Geometry::Line(vec![p.clone()]),
                prefer_x: None,
            })
            .collect()
    }

    /// A name of ten glyphs, which `even` sets.
    const TEN: &str = "Abcdefghij";

    /// The glyph edges of a name `w` wide set in ten even glyphs.
    pub(super) fn even(w: f64) -> Vec<f64> {
        (0..=10).map(|k| w * f64::from(k) / 10.0).collect()
    }

    /// How near `path` comes to a name's ink `advance`-wide glyphs: the turned glyphs themselves
    /// along a line, not the boxes around them.
    fn ink_distance(spot: &Spot, path: &[(f64, f64)], advance: f64) -> f64 {
        let Some(glyphs) = &spot.glyphs else {
            return run_box_distance(path, &spot.b);
        };
        let above = (0.8 * 14.0_f64).round();
        let samples: Vec<(f64, f64)> = path
            .windows(2)
            .flat_map(|w| {
                (0..=400).map(move |k| {
                    let t = f64::from(k) / 400.0;
                    (
                        w[0].0 + (w[1].0 - w[0].0) * t,
                        w[0].1 + (w[1].1 - w[0].1) * t,
                    )
                })
            })
            .collect();
        glyphs
            .iter()
            .flat_map(|(g, _)| {
                let (sin, cos) = g.angle.to_radians().sin_cos();
                samples.iter().map(move |p| {
                    let (dx, dy) = (p.0 - g.at.0, p.1 - g.at.1);
                    let (u, v) = (dx * cos + dy * sin, -dx * sin + dy * cos);
                    let out_u = (u.abs() - advance / 2.0).max(0.0);
                    let out_v = (-above - v).max(v - (14.0 - above)).max(0.0);
                    out_u.hypot(out_v)
                })
            })
            .fold(f64::INFINITY, f64::min)
    }

    fn crossing() -> Vec<Vec<(f64, f64)>> {
        vec![
            vec![(0.0, 180.0), (400.0, 20.0)],
            vec![(0.0, 20.0), (400.0, 180.0)],
        ]
    }

    fn placed(o: &Outcome) -> Bounds {
        match o {
            Outcome::Placed(spot) | Outcome::Collides(spot) => spot.b,
            other => panic!("expected a box, got {other:?}"),
        }
    }

    #[test]
    fn a_name_on_a_gentle_slope_runs_along_it_clear_of_the_stroke_and_a_level_line_keeps_it_straight(
    ) {
        let rise = vec![(0.0, 180.0), (400.0, 60.0)];
        let out = place_series_names(
            &line_names(std::slice::from_ref(&rise), 60.0),
            &mut field(PLOT),
            true,
        );
        let Outcome::Placed(spot) = &out[0] else {
            panic!("{:?}", out[0])
        };
        let glyphs = spot
            .glyphs
            .as_ref()
            .expect("a gentle slope curves its name");
        let slope = (-120.0_f64).atan2(400.0).to_degrees();
        for (g, _) in glyphs {
            assert!((g.angle - slope).abs() < 0.5, "{g:?}");
        }
        let d = ink_distance(spot, &rise, 6.0);
        assert!(d >= LINE_PAD, "{d}");
        for b in spot.boxes() {
            assert!(
                b.x1 >= spot.b.x1 && b.x2 <= spot.b.x2 && b.y1 >= spot.b.y1 && b.y2 <= spot.b.y2
            );
        }
        let level = vec![(0.0, 100.0), (400.0, 100.0)];
        let out = place_series_names(&line_names(&[level], 60.0), &mut field(PLOT), true);
        let Outcome::Placed(spot) = &out[0] else {
            panic!("{:?}", out[0])
        };
        assert_eq!(spot.glyphs, None);
    }

    #[test]
    fn a_name_keeps_off_a_gridline_that_would_run_along_it() {
        // Over a level line, the left half of the plot has a gridline through every name above it.
        let level = vec![(0.0, 100.0), (400.0, 100.0)];
        let mut f = field(PLOT);
        f.obstacles
            .push(Obstacle::grid(Bounds::new(0.0, 89.5, 220.0, 90.5), false));
        let out = place_series_names(&line_names(&[level], 60.0), &mut f, true);
        let b = placed(&out[0]);
        assert!(b.x1 > 220.0 || b.y2 < 90.0 || b.y1 > 90.0, "{b:?}");
    }

    #[test]
    fn a_band_name_keeps_off_a_gridline_through_its_band() {
        // An even band whose left half has a gridline through the middle, where its name sits.
        let names = bands(&[60.0]);
        let mut f = field(PLOT);
        f.obstacles
            .push(Obstacle::grid(Bounds::new(0.0, 169.5, 220.0, 170.5), false));
        let b = placed(&place_series_names(&names, &mut f, true)[0]);
        assert!(b.x1 >= 220.0, "{b:?}");
    }

    #[test]
    fn a_name_below_a_gentle_slope_runs_along_it_too() {
        // A wall over the line, stepped to its slope: no name fits above it.
        let rise = vec![(0.0, 120.0), (400.0, 40.0)];
        let mut f = field(PLOT);
        f.obstacles.extend((0..40).map(|k| {
            let x = f64::from(k) * 10.0;
            Obstacle::mark(Bounds::new(
                x,
                0.0,
                x + 10.0,
                lerp_at(&rise, x + 10.0) - 8.0,
            ))
        }));
        let out = place_series_names(&line_names(std::slice::from_ref(&rise), 60.0), &mut f, true);
        let Outcome::Placed(spot) = &out[0] else {
            panic!("{:?}", out[0])
        };
        let glyphs = spot
            .glyphs
            .as_ref()
            .expect("a gentle slope curves its name");
        let slope = (-80.0_f64).atan2(400.0).to_degrees();
        for (g, _) in glyphs {
            assert!((g.angle - slope).abs() < 0.5, "{g:?}");
            assert!(g.at.1 > lerp_at(&rise, g.at.0), "below: {g:?}");
        }
        assert!(ink_distance(spot, &rise, 6.0) >= LINE_PAD);
    }

    #[test]
    fn a_name_whose_line_barely_rises_across_it_stays_straight() {
        // A short name on a slope, and a name centered on a broad peak: their ends sit level.
        let slope = vec![(0.0, 160.0), (400.0, 120.0)];
        let peak: Vec<(f64, f64)> = (0..=80)
            .map(|k| {
                let x = f64::from(k) * 5.0;
                (x, 60.0 + ((x - 200.0) / 120.0).powi(2) * 60.0)
            })
            .collect();
        for (line, width) in [(slope, 24.0), (peak, 60.0)] {
            let out = place_series_names(&line_names(&[line], width), &mut field(PLOT), true);
            let Outcome::Placed(spot) = &out[0] else {
                panic!("{:?}", out[0])
            };
            assert_eq!(spot.glyphs, None, "{spot:?}");
        }
    }

    #[test]
    fn a_name_never_tilts_past_forty_degrees_and_follows_a_line_just_past_thirty() {
        // Nothing but a steep ramp: the name has nowhere level to go.
        let steep = vec![(100.0, 195.0), (200.0, 5.0)];
        let out = place_series_names(&line_names(&[steep], 60.0), &mut field(PLOT), true);
        let (Outcome::Placed(spot) | Outcome::Collides(spot)) = &out[0] else {
            panic!("{:?}", out[0])
        };
        for (g, _) in spot.glyphs.iter().flatten() {
            assert!(g.angle.abs() <= MAX_TILT_DEGREES, "{g:?}");
        }
        // About 31°: one of two crossing lines, which name alike.
        let rise = vec![(0.0, 190.0), (300.0, 10.0), (400.0, 10.0)];
        let n = &line_names(&[rise], 60.0)[0];
        let cands = candidates(
            n,
            &Along::of(n, true),
            std::slice::from_ref(n),
            &field(PLOT),
        );
        assert!(cands.iter().any(|c| c.glyphs.is_some() && c.acceptable()));
    }

    #[test]
    fn a_line_just_past_forty_degrees_keeps_its_name_tight_with_letters_held_at_forty() {
        let curved = |degrees: f64| {
            let line = vec![
                (0.0, 195.0),
                (180.0, 195.0 - 180.0 * degrees.to_radians().tan()),
            ];
            let n = &line_names(&[line], 60.0)[0];
            candidates(
                n,
                &Along::of(n, true),
                std::slice::from_ref(n),
                &field(PLOT),
            )
            .into_iter()
            .filter_map(|c| c.glyphs.map(|g| (c.off, g)))
            .collect::<Vec<_>>()
        };
        // About 41.5°: right against the line, every letter at the cap.
        let tight: Vec<_> = curved(41.5)
            .into_iter()
            .filter(|(off, _)| *off == 0.0)
            .collect();
        assert!(!tight.is_empty());
        for (_, glyphs) in &tight {
            assert!(glyphs
                .iter()
                .all(|(g, _)| g.angle.abs() <= MAX_TILT_DEGREES + EPS));
        }
        // Well past the cap the name stays straight, however far off it would stand.
        assert!(curved(50.0).is_empty());
    }

    #[test]
    fn a_name_along_its_line_only_adds_to_the_straight_boxes_it_could_take() {
        let rise = vec![(0.0, 180.0), (400.0, 60.0)];
        let n = &line_names(&[rise], 60.0)[0];
        let cands = candidates(
            n,
            &Along::of(n, true),
            std::slice::from_ref(n),
            &field(PLOT),
        );
        let straight = cands.iter().filter(|c| c.glyphs.is_none()).count();
        assert_eq!(straight, 6 * sample_xs(&[0.0, 400.0]).len());
        assert!(cands.iter().any(|c| c.glyphs.is_some()));
    }

    #[test]
    fn a_long_name_on_a_dotted_line_stands_off_the_dots_and_still_follows_it() {
        // Two crossing lines with a 7px dot at every 40px: a 76px name spans a dot wherever it
        // sits.
        let emea: Vec<(f64, f64)> = vec![(0.0, 24.58), (447.0, 261.39)];
        let na: Vec<(f64, f64)> = vec![(0.0, 279.0), (447.0, 20.67)];
        let names = vec![
            Name {
                owner: 0,
                size: (28.0, 11.0),
                text: "EMEA".into(),
                edges: (0..=4).map(|k| 7.0 * f64::from(k)).collect(),
                geometry: Geometry::Line(vec![emea]),
                prefer_x: None,
            },
            Name {
                owner: 1,
                size: (76.0, 11.0),
                text: "North America".into(),
                edges: (0..=13).map(|k| 76.0 * f64::from(k) / 13.0).collect(),
                geometry: Geometry::Line(vec![na]),
                prefer_x: None,
            },
        ];
        let mut f = field((447.0, 290.0));
        for (a, b) in [(24.58, 261.39), (279.0, 20.67)] {
            for k in 0..12 {
                let x = 40.64 * f64::from(k);
                let y = a + (b - a) * x / 447.0;
                f.obstacles
                    .push(crate::placement::Obstacle::mark(Bounds::new(
                        x - 3.5,
                        y - 3.5,
                        x + 3.5,
                        y + 3.5,
                    )));
            }
        }
        let out = place_series_names(&names, &mut f, true);
        for o in &out {
            let Outcome::Placed(spot) = o else {
                panic!("{o:?}")
            };
            assert!(spot.glyphs.is_some(), "{spot:?}");
        }
    }

    #[test]
    fn every_sample_beside_a_gentle_line_has_its_twin_along_it() {
        let rise = vec![(0.0, 150.0), (400.0, 100.0)];
        let n = &line_names(&[rise], 60.0)[0];
        let along = Along::of(n, true);
        let cands = candidates(n, &along, std::slice::from_ref(n), &field(PLOT));
        let spots = sample_xs(&[0.0, 400.0])
            .into_iter()
            .flat_map(|x| [false, true].map(|below| along.at(n, 0, x, below).len()))
            .filter(|count| *count > 0)
            .count();
        assert!(spots > 0);
        assert_eq!(cands.iter().filter(|c| c.glyphs.is_some()).count(), spots);
    }

    #[test]
    fn an_empty_name_or_one_a_shaper_joins_stays_straight() {
        let rise = vec![(0.0, 180.0), (400.0, 60.0)];
        for text in ["", "Cafe\u{301}", "\u{645}\u{635}\u{631}"] {
            let count = text.chars().count();
            let width = 6.0 * count as f64;
            let n = Name {
                owner: 0,
                size: (width, 14.0),
                text: text.into(),
                edges: (0..=count).map(|k| 6.0 * k as f64).collect(),
                geometry: Geometry::Line(vec![rise.clone()]),
                prefer_x: None,
            };
            let out = place_series_names(std::slice::from_ref(&n), &mut field(PLOT), true);
            let (Outcome::Placed(spot) | Outcome::Collides(spot)) = &out[0] else {
                panic!("{text:?}: {:?}", out[0])
            };
            assert_eq!(spot.glyphs, None, "{text:?}");
        }
    }

    #[test]
    fn names_follow_lines_that_ease_rather_than_sit_straight_where_they_still_lean() {
        // Six lines that ease rather than bend: where a straight name in the open sits beside a
        // stretch that leans off it, the name along its line reads better.
        let lines: Vec<(&str, Vec<(f64, f64)>)> = vec![
            (
                "alpha",
                vec![
                    (5.4, 258.3),
                    (54.52, 248.51),
                    (98.88, 228.94),
                    (148.0, 209.38),
                    (195.53, 180.02),
                    (244.65, 160.46),
                    (292.18, 131.1),
                    (341.3, 116.43),
                    (390.42, 101.75),
                    (437.95, 77.29),
                    (487.07, 47.94),
                    (534.6, 23.48),
                ],
            ),
            (
                "bravo",
                vec![
                    (5.4, 219.16),
                    (54.52, 214.27),
                    (98.88, 209.38),
                    (148.0, 199.59),
                    (195.53, 189.81),
                    (244.65, 175.13),
                    (292.18, 155.56),
                    (341.3, 131.1),
                    (390.42, 111.54),
                    (437.95, 96.86),
                    (487.07, 87.08),
                    (534.6, 72.4),
                ],
            ),
            (
                "charlie",
                vec![
                    (5.4, 170.24),
                    (54.52, 160.46),
                    (98.88, 145.78),
                    (148.0, 140.89),
                    (195.53, 136.0),
                    (244.65, 140.89),
                    (292.18, 150.67),
                    (341.3, 155.56),
                    (390.42, 145.78),
                    (437.95, 131.1),
                    (487.07, 111.54),
                    (534.6, 101.75),
                ],
            ),
            (
                "delta",
                vec![
                    (5.4, 96.86),
                    (54.52, 111.54),
                    (98.88, 126.21),
                    (148.0, 145.78),
                    (195.53, 165.35),
                    (244.65, 180.02),
                    (292.18, 194.7),
                    (341.3, 204.48),
                    (390.42, 209.38),
                    (437.95, 219.16),
                    (487.07, 224.05),
                    (534.6, 233.84),
                ],
            ),
            (
                "echo",
                vec![
                    (5.4, 277.86),
                    (54.52, 258.3),
                    (98.88, 238.73),
                    (148.0, 209.38),
                    (195.53, 189.81),
                    (244.65, 170.24),
                    (292.18, 155.56),
                    (341.3, 145.78),
                    (390.42, 140.89),
                    (437.95, 140.89),
                    (487.07, 145.78),
                    (534.6, 155.56),
                ],
            ),
            (
                "foxtrot",
                vec![
                    (5.4, 72.4),
                    (54.52, 82.19),
                    (98.88, 87.08),
                    (148.0, 87.08),
                    (195.53, 82.19),
                    (244.65, 72.4),
                    (292.18, 57.73),
                    (341.3, 47.94),
                    (390.42, 38.16),
                    (437.95, 33.27),
                    (487.07, 33.27),
                    (534.6, 38.16),
                ],
            ),
        ];
        let names: Vec<Name> = lines
            .into_iter()
            .enumerate()
            .map(|(owner, (text, line))| {
                let count = text.chars().count();
                Name {
                    owner,
                    size: (7.0 * count as f64, 14.0),
                    text: text.into(),
                    edges: (0..=count).map(|k| 7.0 * k as f64).collect(),
                    geometry: Geometry::Line(vec![line]),
                    prefer_x: None,
                }
            })
            .collect();
        let out = place_series_names(&names, &mut field((540.0, 290.0)), true);
        for (n, o) in names.iter().zip(&out) {
            let Outcome::Placed(spot) = o else {
                panic!("{}: {o:?}", n.text)
            };
            let Geometry::Line(runs) = &n.geometry else {
                unreachable!()
            };
            if spot.glyphs.is_none() {
                let rise = (runs_y_at(runs, spot.b.x2) - runs_y_at(runs, spot.b.x1)).abs();
                assert!(rise < LEAN_RISE_FONT_SIZES * 14.0, "{}: {spot:?}", n.text);
            }
        }
    }

    #[test]
    fn a_crest_outranks_open_room_and_more_room_counts_by_the_label_height() {
        let line = vec![vec![(0.0, 100.0), (400.0, 100.0)]];
        let plot = Bounds::new(0.0, 0.0, PLOT.0, PLOT.1);
        let at = |y: f64, clear: f64, crest: f64| Cand {
            clear,
            crest,
            ..line_cand(
                &[curve::box_quad(&Bounds::new(170.0, y, 230.0, y + 14.0))],
                None,
                &line,
                &[],
                &plot,
                &field(PLOT),
            )
        };
        let law = Law::new(PLOT, 14.0, &[]);
        // Over a crest beats a level spot with more room around it.
        assert_eq!(
            law.order(&at(80.0, 4.0, 10.0), &at(80.0, 40.0, 0.0)),
            Ordering::Less
        );
        // Every label height of room counts, however much there is.
        assert_eq!(
            law.order(&at(40.0, 22.0, 0.0), &at(80.0, 60.0, 0.0)),
            Ordering::Greater
        );
        // Within one label height, room ties and the higher spot wins.
        assert_eq!(
            law.order(&at(40.0, 15.0, 0.0), &at(80.0, 26.0, 0.0)),
            Ordering::Less
        );
        // A more prominent peak or dip beats a slight bump with more room.
        assert_eq!(
            law.order(&at(40.0, 22.0, 40.0), &at(80.0, 68.0, 8.0)),
            Ordering::Less
        );
    }

    #[test]
    fn a_name_sits_on_its_lines_open_side_where_the_gap_is_widest() {
        // Three level lines: the middle one has 60 above it and 110 below it to its neighbors.
        let paths: Vec<Vec<(f64, f64)>> = [20.0, 80.0, 190.0]
            .map(|y| vec![(0.0, y), (400.0, y)])
            .to_vec();
        let out = place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        let (top, middle) = (placed(&out[0]), placed(&out[1]));
        assert!(top.y2 <= 20.0, "the top line is named above it: {top:?}");
        assert!(
            middle.y1 >= 80.0,
            "the middle line takes its wider gap, below: {middle:?}"
        );
    }

    #[test]
    fn a_name_along_its_line_never_rounds_a_peak() {
        // Two lines as one fixture draws them: alpha rises straight to a sharp peak, with open
        // room above it. A name along it near the peak would bend around the corner; on a line
        // of two straight runs, a name along it tilts evenly or sits straight.
        let lines = [
            (
                "alpha",
                36.0,
                vec![(0.0, 174.36), (223.5, 24.05), (447.0, 249.51)],
            ),
            ("gamma", 45.0, vec![(0.0, 234.48), (223.5, 144.3)]),
        ];
        let names: Vec<Name> = lines
            .into_iter()
            .enumerate()
            .map(|(owner, (text, w, line))| Name {
                owner,
                size: (w, 14.0),
                text: text.into(),
                edges: (0..=5).map(|k| w * f64::from(k) / 5.0).collect(),
                geometry: Geometry::Line(vec![line]),
                prefer_x: None,
            })
            .collect();
        let along = Along::of(&names[0], true);
        let cands = candidates(&names[0], &along, &names, &field((447.0, 270.0)));
        assert!(cands.iter().any(|c| c.glyphs.is_some()));
        for glyphs in cands.iter().filter_map(|c| c.glyphs.as_ref()) {
            let angles = glyphs.iter().map(|(g, _)| g.angle);
            let spread = angles.clone().fold(f64::MIN, f64::max) - angles.fold(f64::MAX, f64::min);
            assert!(spread < 1.0, "{glyphs:?}");
        }
    }

    #[test]
    fn a_straight_name_astride_a_crest_does_not_lean() {
        // A crest whose sides under a centered name differ by more than a lean: the name still
        // reads level over the peak.
        let core = vec![vec![
            (400.5, 67.1),
            (406.8, 57.1),
            (413.0, 47.0),
            (419.3, 36.9),
            (425.6, 26.9),
            (431.9, 23.5),
            (438.2, 20.1),
            (444.5, 23.5),
            (450.7, 30.2),
            (457.0, 36.9),
            (463.3, 50.3),
            (469.6, 60.4),
        ]];
        let b = Bounds::new(438.2 - 16.0, 3.0, 438.2 + 16.0, 17.0);
        let plot = Bounds::new(0.0, 0.0, 493.0, 292.0);
        let c = line_cand(
            &[curve::box_quad(&b)],
            None,
            &core,
            &[],
            &plot,
            &field((493.0, 292.0)),
        );
        assert!(
            !c.leans && !c.askew,
            "rise across the box is no lean over a crest"
        );
        assert!(c.crest > 0.0);
        // A dip shallower than half a font size is a wiggle, not a crest to sit over.
        let cand = |deep: f64| {
            let line = vec![vec![
                (380.0, 40.0),
                (400.0, 40.0 + deep),
                (420.0, 40.0),
                (440.0, 40.0),
            ]];
            let b = Bounds::new(384.0, 20.0, 416.0, 34.0);
            line_cand(
                &[curve::box_quad(&b)],
                None,
                &line,
                &[],
                &plot,
                &field((493.0, 292.0)),
            )
        };
        assert!(cand(3.0).crest == 0.0 && cand(10.0).crest > 0.0);
        // A dip stands out as far as a peak of the same depth.
        let peak = {
            let line = vec![vec![
                (380.0, 60.0),
                (400.0, 40.0),
                (420.0, 60.0),
                (440.0, 60.0),
            ]];
            let b = Bounds::new(384.0, 20.0, 416.0, 34.0);
            line_cand(
                &[curve::box_quad(&b)],
                None,
                &line,
                &[],
                &plot,
                &field((493.0, 292.0)),
            )
        };
        let dip = {
            let line = vec![vec![
                (380.0, 40.0),
                (400.0, 60.0),
                (420.0, 40.0),
                (440.0, 40.0),
            ]];
            let b = Bounds::new(384.0, 20.0, 416.0, 34.0);
            line_cand(
                &[curve::box_quad(&b)],
                None,
                &line,
                &[],
                &plot,
                &field((493.0, 292.0)),
            )
        };
        assert!(peak.crest > 0.0 && (peak.crest - dip.crest).abs() < EPS);
    }

    #[test]
    fn auto_keeps_colliding_straight_names_where_following_their_lines_would_clear() {
        let spot = Spot {
            b: Bounds::new(0.0, 0.0, 10.0, 10.0),
            glyphs: None,
        };
        // Straight names collide; names along their lines would clear.
        let place = |follow: bool| {
            vec![if follow {
                Outcome::Placed(spot.clone())
            } else {
                Outcome::Collides(spot.clone())
            }]
        };
        assert!(matches!(
            follow_where_clear(&place, true)[0],
            Outcome::Collides(_)
        ));
        assert!(matches!(
            follow_where_clear(&place, false)[0],
            Outcome::Placed(_)
        ));
    }

    #[test]
    fn names_stay_straight_where_following_their_lines_would_collide() {
        // Four crowded lines: every straight name clears, but a name along its line takes a spot
        // that leaves another nowhere to go.
        let paths = vec![
            vec![
                (0.0, 43.2),
                (50.0, 25.2),
                (100.0, 35.1),
                (150.0, 22.2),
                (200.0, 24.2),
                (250.0, 23.1),
                (300.0, 37.9),
                (350.0, 46.9),
                (400.0, 49.2),
            ],
            vec![
                (0.0, 49.8),
                (50.0, 35.5),
                (100.0, 38.3),
                (150.0, 33.0),
                (200.0, 41.5),
                (250.0, 52.0),
                (300.0, 77.0),
                (350.0, 84.0),
                (400.0, 106.5),
            ],
            vec![
                (0.0, 87.1),
                (50.0, 89.4),
                (100.0, 75.8),
                (150.0, 63.4),
                (200.0, 50.0),
                (250.0, 36.2),
                (300.0, 41.0),
                (350.0, 25.6),
                (400.0, 48.7),
            ],
            vec![
                (0.0, 58.9),
                (50.0, 75.5),
                (100.0, 83.4),
                (150.0, 93.1),
                (200.0, 79.7),
                (250.0, 72.4),
                (300.0, 84.8),
                (350.0, 77.7),
                (400.0, 79.5),
            ],
        ];
        let names = line_names(&paths, 81.0);
        let place = |follow: bool| place_series_names(&names, &mut field(PLOT), follow);
        let collides = |out: &[Outcome]| out.iter().any(|o| matches!(o, Outcome::Collides(_)));
        assert!(collides(&place(true)), "the scene no longer sets the trap");
        for auto in [true, false] {
            let out = follow_where_clear(&place, auto);
            assert!(!collides(&out), "{out:?}");
            assert!(out
                .iter()
                .all(|o| matches!(o, Outcome::Placed(s) if s.glyphs.is_none())));
        }
    }

    #[test]
    fn a_name_on_a_sharp_zigzag_stays_straight() {
        let zigzag: Vec<(f64, f64)> = (0..=20)
            .map(|k| (f64::from(k) * 20.0, if k % 2 == 0 { 140.0 } else { 100.0 }))
            .collect();
        let out = place_series_names(&line_names(&[zigzag], 60.0), &mut field(PLOT), true);
        let (Outcome::Placed(spot) | Outcome::Collides(spot)) = &out[0] else {
            panic!("{:?}", out[0])
        };
        assert_eq!(spot.glyphs, None);
    }

    #[test]
    fn a_band_orders_its_edges_and_merges_the_vertices_a_link_piles_at_one_x() {
        let runs = vec![vec![
            (0.0, 50.0, 80.0),
            (10.0, 90.0, 60.0),
            (10.0, 70.0, 70.0),
            (20.0, 40.0, 70.0),
        ]];
        assert_eq!(
            band_of(&runs),
            [(0.0, 50.0, 80.0), (10.0, 60.0, 90.0), (20.0, 40.0, 70.0)]
        );
    }

    #[test]
    fn two_crossing_lines_each_name_clears_every_stroke_and_stays_nearer_its_own() {
        let paths = crossing();
        let mut f = field(PLOT);
        let out = place_series_names(&line_names(&paths, 60.0), &mut f, true);
        for (i, o) in out.iter().enumerate() {
            let Outcome::Placed(spot) = o else {
                panic!("series {i} is {o:?}")
            };
            for p in &paths {
                let d = ink_distance(spot, p, 6.0);
                assert!(d >= LINE_PAD, "series {i} name touches a stroke ({d})");
            }
            let b = &spot.b;
            let c = ((b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0);
            let (own, other) = (path_distance(&paths[i], c), path_distance(&paths[1 - i], c));
            assert!(own <= other, "series {i} name sits nearer the other line");
        }
    }

    #[test]
    fn crossing_lines_split_the_plot_high_line_names_its_left_low_line_its_right() {
        // paths[1] (EMEA) starts high and falls; paths[0] (North America) starts low and rises.
        let paths = crossing();
        let out = place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        let (north_america, emea) = (placed(&out[0]), placed(&out[1]));
        let inset = side_inset(PLOT.0, 14.0);
        assert!(emea.x2 < PLOT.0 / 2.0, "EMEA {emea:?}");
        assert!(
            north_america.x1 > PLOT.0 / 2.0,
            "North America {north_america:?}"
        );
        for b in [emea, north_america] {
            assert!(b.x1 >= inset && b.x2 <= PLOT.0 - inset, "{b:?}");
        }
    }

    #[test]
    fn a_name_keeps_off_the_plot_sides_when_an_interior_spot_is_acceptable() {
        // The line is highest and most open at its right end, which is against the side.
        let paths = vec![vec![(0.0, 180.0), (400.0, 20.0)]];
        let out = place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        let inset = side_inset(PLOT.0, 14.0);
        assert!(b.x1 >= inset && b.x2 <= PLOT.0 - inset, "{b:?}");
        assert!(
            b.x2 > PLOT.0 - 2.0 * inset - 60.0,
            "stays as high as the inset allows: {b:?}"
        );
    }

    #[test]
    fn of_two_equally_open_spots_the_one_farther_from_the_baseline_wins() {
        let paths = vec![vec![
            (0.0, 150.0),
            (200.0, 150.0),
            (200.0, 60.0),
            (400.0, 60.0),
        ]];
        let out = place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        let (Outcome::Placed(spot) | Outcome::Collides(spot)) = &out[0] else {
            panic!("{:?}", out[0])
        };
        // A name along its line may round the step's corner; it reads from its middle glyph.
        let reads_at = match &spot.glyphs {
            Some(glyphs) => glyphs[glyphs.len() / 2].0.at.1,
            None => spot.b.y2,
        };
        assert!(reads_at <= 60.0, "{spot:?}");
    }

    #[test]
    fn well_separated_parallel_lines_get_names_at_clearly_different_x() {
        let paths: Vec<Vec<(f64, f64)>> = [40.0, 100.0, 160.0]
            .map(|y| vec![(0.0, y), (400.0, y)])
            .to_vec();
        let out = place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        let xs: Vec<f64> = out
            .iter()
            .map(|o| (placed(o).x1 + placed(o).x2) / 2.0)
            .collect();
        for (a, b) in [(0, 1), (0, 2), (1, 2)] {
            assert!((xs[a] - xs[b]).abs() >= 60.0, "{xs:?}");
        }
    }

    fn bands(thickness: &[f64]) -> Vec<Name> {
        let xs: Vec<f64> = (0..=8).map(|k| f64::from(k) * 50.0).collect();
        let mut top = PLOT.1;
        thickness
            .iter()
            .enumerate()
            .map(|(owner, t)| {
                let runs = xs.iter().map(|&x| (x, top - t, top)).collect();
                top -= t;
                Name {
                    owner,
                    size: (70.0, 14.0),
                    text: TEN.into(),
                    edges: even(70.0),
                    geometry: Geometry::Band(runs),
                    prefer_x: None,
                }
            })
            .collect()
    }

    #[test]
    fn each_stacked_name_lies_wholly_inside_its_own_band() {
        let names = bands(&[60.0, 40.0, 30.0]);
        let mut f = field(PLOT);
        let out = place_series_names(&names, &mut f, true);
        for (n, o) in names.iter().zip(&out) {
            let b = placed(o);
            assert!(matches!(o, Outcome::Placed(_)), "{o:?}");
            let Geometry::Band(runs) = &n.geometry else {
                unreachable!()
            };
            assert!(b.x1 >= runs[0].0 && b.x2 <= runs[runs.len() - 1].0);
            assert!(
                runs.iter().all(|r| r.1 <= b.y1 + EPS && r.2 >= b.y2 - EPS),
                "name {} leaves its band",
                n.owner
            );
        }
    }

    #[test]
    fn a_band_name_is_centered_where_its_band_is_thickest() {
        let thickness = [20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0, 50.0, 40.0];
        let runs = thickness
            .iter()
            .enumerate()
            .map(|(k, t)| (k as f64 * 50.0, PLOT.1 - t, PLOT.1))
            .collect();
        let names = [Name {
            owner: 0,
            size: (70.0, 14.0),
            text: TEN.into(),
            edges: even(70.0),
            geometry: Geometry::Band(runs),
            prefer_x: None,
        }];
        let out = place_series_names(&names, &mut field(PLOT), true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(((b.x1 + b.x2) / 2.0 - 300.0).abs() < EPS, "{b:?}");
    }

    #[test]
    fn a_line_name_keeps_a_label_height_from_an_annotation_rule() {
        // The open spot above the line sits on the rule; the name goes below it instead.
        let paths = vec![vec![(0.0, 100.0), (400.0, 100.0)]];
        let rule = Bounds::new(0.0, 84.0, 400.0, 84.0);
        let mut f = field(PLOT);
        f.obstacles.push(Obstacle::annotation(rule));
        let out = place_series_names(&line_names(&paths, 60.0), &mut f, true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        let gap = (rule.y1 - b.y2).max(b.y1 - rule.y2);
        assert!(gap >= crate::placement::ANNOTATION_RULE_PAD - EPS, "{b:?}");
    }

    #[test]
    fn a_band_obstacle_covering_the_only_open_spot_is_avoided() {
        // One flat line near the top of a short plot: the room above is the only open side,
        // and a band shades all of it.
        let paths = vec![vec![(0.0, 40.0), (400.0, 40.0)]];
        let band = Bounds::new(0.0, 0.0, 400.0, 28.0);
        let mut f = field((400.0, 90.0));
        f.obstacles.push(Obstacle::annotation(band));
        let out = place_series_names(&line_names(&paths, 60.0), &mut f, true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(b.y1 > 40.0, "{b:?}");
    }

    #[test]
    fn the_undrawn_span_across_a_gap_is_no_obstacle() {
        // B's run breaks between x=150 and x=250 where a joined path would cross A's spot.
        // A's only roomy spot is the gap's middle, so it must be placed there, not refused.
        let a = vec![vec![(0.0, 60.0), (400.0, 60.0)]];
        let gapped = vec![
            vec![(0.0, 20.0), (150.0, 20.0)],
            vec![(250.0, 100.0), (400.0, 100.0)],
        ];
        let names = vec![
            Name {
                owner: 0,
                size: (60.0, 14.0),
                text: TEN.into(),
                edges: even(60.0),
                geometry: Geometry::Line(a),
                prefer_x: None,
            },
            Name {
                owner: 1,
                size: (60.0, 14.0),
                text: TEN.into(),
                edges: even(60.0),
                geometry: Geometry::Line(gapped),
                prefer_x: None,
            },
        ];
        // The line of A sits at y=60; the joined B segment (150,20)-(250,100) crosses it at x=200,
        // and its box region (x 170..230) would be refused if the segment counted.
        let mut f = field((400.0, 120.0));
        let out = place_series_names(&names, &mut f, true);
        assert!(
            out.iter().all(|o| matches!(o, Outcome::Placed(_))),
            "{out:?}"
        );
        let open = |b: Bounds| runs_box_distance(&[vec![(150.0, 20.0), (250.0, 100.0)]], &b);
        let reaches_gap = candidates(&names[0], &Along::of(&names[0], true), &names, &f)
            .iter()
            .any(|c| c.acceptable() && c.b.x1 > 160.0 && c.b.x2 < 240.0 && open(c.b) < LINE_PAD);
        assert!(reaches_gap, "a name beside the gap is acceptable");
    }

    fn flat(prefer_x: Option<f64>) -> Vec<Name> {
        vec![Name {
            owner: 0,
            size: (60.0, 14.0),
            text: TEN.into(),
            edges: even(60.0),
            geometry: Geometry::Line(vec![vec![(0.0, 100.0), (400.0, 100.0)]]),
            prefer_x,
        }]
    }

    fn holds(b: &Bounds, x: f64) -> bool {
        b.x1 <= x && x <= b.x2
    }

    fn centered_on(b: &Bounds, x: f64) -> bool {
        ((b.x1 + b.x2) / 2.0 - x).abs() < EPS
    }

    #[test]
    fn an_acceptable_spot_at_the_preferred_x_wins_over_the_law() {
        let unpreferred = placed(&place_series_names(&flat(None), &mut field(PLOT), true)[0]);
        assert!(!holds(&unpreferred, 330.0), "{unpreferred:?}");
        let out = place_series_names(&flat(Some(330.0)), &mut field(PLOT), true);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(centered_on(&placed(&out[0]), 330.0), "{:?}", out[0]);
    }

    #[test]
    fn a_preferred_x_at_the_plot_side_centers_as_near_as_the_plot_allows() {
        let out = place_series_names(&flat(Some(PLOT.0)), &mut field(PLOT), true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!((b.x2 - PLOT.0).abs() < EPS, "{b:?}");
    }

    #[test]
    fn a_name_on_a_steep_line_still_centers_on_its_preferred_x() {
        let names = vec![Name {
            owner: 0,
            size: (60.0, 14.0),
            text: TEN.into(),
            edges: even(60.0),
            geometry: Geometry::Line(vec![vec![(0.0, 190.0), (400.0, 10.0)]]),
            prefer_x: Some(200.0),
        }];
        let out = place_series_names(&names, &mut field(PLOT), false);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(centered_on(&placed(&out[0]), 200.0), "{:?}", out[0]);
    }

    #[test]
    fn a_centered_name_takes_the_side_of_its_line_it_moves_least_from() {
        // Flat to x=200, then rising: above, the name must climb with the rise; below, it need not.
        let names = vec![Name {
            owner: 0,
            size: (60.0, 14.0),
            text: TEN.into(),
            edges: even(60.0),
            geometry: Geometry::Line(vec![vec![(0.0, 100.0), (200.0, 100.0), (400.0, 20.0)]]),
            prefer_x: Some(200.0),
        }];
        let b = placed(&place_series_names(&names, &mut field(PLOT), false)[0]);
        assert!(centered_on(&b, 200.0), "{b:?}");
        assert!(b.y1 > 100.0, "below the line: {b:?}");
    }

    #[test]
    fn on_a_very_steep_line_a_side_aligned_spot_beats_a_long_lift() {
        // Centered, the name would climb about 54px to clear this line; aligned to one side of
        // its x it moves 30px (half its width).
        let names = vec![Name {
            owner: 0,
            size: (60.0, 14.0),
            text: TEN.into(),
            edges: even(60.0),
            geometry: Geometry::Line(vec![vec![(100.0, 190.0), (300.0, -170.0)]]),
            prefer_x: Some(200.0),
        }];
        let b = placed(&place_series_names(&names, &mut field(PLOT), true)[0]);
        assert!(
            (b.x1 - 200.0).abs() < EPS || (b.x2 - 200.0).abs() < EPS,
            "{b:?}"
        );
    }

    #[test]
    fn a_blocked_centered_spot_keeps_the_name_at_its_x_aligned_to_one_side() {
        let mut f = field(PLOT);
        f.obstacles
            .push(Obstacle::label(Bounds::new(280.0, 0.0, 318.0, PLOT.1)));
        let out = place_series_names(&flat(Some(330.0)), &mut f, true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!((b.x1 - 330.0).abs() < EPS, "{b:?}");
    }

    #[test]
    fn a_blocked_preferred_x_falls_back_to_the_usual_search() {
        let mut f = field(PLOT);
        f.obstacles
            .push(Obstacle::label(Bounds::new(300.0, 0.0, 360.0, PLOT.1)));
        let out = place_series_names(&flat(Some(330.0)), &mut f, true);
        let b = placed(&out[0]);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(b.x2 <= 300.0 || b.x1 >= 360.0, "{b:?}");
    }

    #[test]
    fn a_preferred_x_no_run_covers_adds_no_spot() {
        let unpreferred = place_series_names(&flat(None), &mut field(PLOT), true);
        let off_the_data = place_series_names(&flat(Some(900.0)), &mut field(PLOT), true);
        assert_eq!(unpreferred, off_the_data);
    }

    #[test]
    fn a_name_pinned_on_a_gentle_line_follows_the_line_at_its_preferred_x() {
        let mut names = line_names(&[vec![(0.0, 160.0), (400.0, 80.0)]], 60.0);
        names[0].prefer_x = Some(200.0);
        let out = place_series_names(&names, &mut field(PLOT), true);
        let Outcome::Placed(spot) = &out[0] else {
            panic!("{:?}", out[0])
        };
        let glyphs = spot.glyphs.as_ref().expect("along its line");
        assert!(
            (glyphs[glyphs.len() / 2].0.at.0 - 200.0).abs() < 6.0,
            "{spot:?}"
        );
    }

    #[test]
    fn a_band_name_honors_its_preferred_x() {
        let mut names = bands(&[60.0]);
        names[0].prefer_x = Some(330.0);
        let out = place_series_names(&names, &mut field(PLOT), true);
        assert!(matches!(out[0], Outcome::Placed(_)), "{:?}", out[0]);
        assert!(centered_on(&placed(&out[0]), 330.0), "{:?}", out[0]);
    }

    #[test]
    fn a_name_with_an_acceptable_preferred_spot_is_placed_before_the_rest() {
        // Both lines are flat; the unpinned name would take the open middle, the pinned one
        // wants the middle too and must not be pushed off it by the other's pick.
        let paths = [
            vec![(0.0, 60.0), (400.0, 60.0)],
            vec![(0.0, 75.0), (400.0, 75.0)],
        ];
        let mut names = line_names(&paths, 60.0);
        names[1].prefer_x = Some(200.0);
        let out = place_series_names(&names, &mut field(PLOT), true);
        assert!(centered_on(&placed(&out[1]), 200.0), "{:?}", out[1]);
        assert!(matches!(out[1], Outcome::Placed(_)), "{:?}", out[1]);
    }

    #[test]
    fn a_band_shorter_than_the_text_is_thin_and_one_under_two_pixels_is_too_thin() {
        let names = bands(&[60.0, 9.0, 1.5]);
        let mut f = field(PLOT);
        let out = place_series_names(&names, &mut f, true);
        assert!(matches!(out[0], Outcome::Placed(_)));
        assert_eq!(out[1], Outcome::Thin);
        assert_eq!(out[2], Outcome::TooThin);
    }

    #[test]
    fn a_band_thick_only_briefly_collides_rather_than_vanishing() {
        let runs = vec![
            (0.0, 100.0, 105.0),
            (200.0, 100.0, 140.0),
            (400.0, 100.0, 105.0),
        ];
        let names = [Name {
            owner: 0,
            size: (350.0, 14.0),
            text: TEN.into(),
            edges: even(350.0),
            geometry: Geometry::Band(runs),
            prefer_x: None,
        }];
        let out = place_series_names(&names, &mut field(PLOT), true);
        assert!(matches!(out[0], Outcome::Collides(_)), "{:?}", out[0]);
    }

    #[test]
    fn lines_packed_tight_in_a_short_plot_collide_with_a_box_each() {
        let paths: Vec<Vec<(f64, f64)>> = (0..6)
            .map(|k| {
                vec![
                    (0.0, 16.0 + 2.0 * f64::from(k)),
                    (400.0, 16.0 + 2.0 * f64::from(k)),
                ]
            })
            .collect();
        let mut f = field((400.0, 40.0));
        let out = place_series_names(&line_names(&paths, 60.0), &mut f, true);
        assert!(
            out.iter().all(|o| matches!(o, Outcome::Collides(_))),
            "{out:?}"
        );
    }

    #[test]
    fn the_same_input_places_the_same_names() {
        let paths = crossing();
        let run = || place_series_names(&line_names(&paths, 60.0), &mut field(PLOT), true);
        assert_eq!(run(), run());
    }
}

/// Synthetic scenes at 640x360 for a visual review. Set `SERIES_NAME_FIXTURE_DIR` to write one
/// SVG per scene.
#[cfg(test)]
mod fixtures {
    use super::*;
    use std::fmt::Write as _;

    const PLOT: (f64, f64) = (560.0, 300.0);
    const FONT: f64 = 14.0;
    const COLORS: [&str; 6] = [
        "#1f77b4", "#d95f02", "#2a9d5c", "#8e44ad", "#c0392b", "#7f8c8d",
    ];
    const NAMES: [&str; 6] = [
        "North America",
        "EMEA",
        "Self-serve",
        "Rest of world",
        "APAC",
        "LATAM",
    ];

    struct Scene {
        title: &'static str,
        geometry: Vec<Geometry>,
        /// How many names the scene expects to be unlabeled.
        unlabeled: usize,
    }

    /// `n` points from x = 0 to `end` of the plot width, with y from `y(t)` for t in 0..=1.
    fn curve(n: usize, end: f64, y: impl Fn(f64) -> f64) -> Vec<(f64, f64)> {
        (0..n)
            .map(|k| {
                let t = k as f64 / (n - 1) as f64;
                (t * end * PLOT.0, y(t))
            })
            .collect()
    }

    fn lines(paths: Vec<Vec<(f64, f64)>>, title: &'static str) -> Scene {
        Scene {
            title,
            geometry: paths.into_iter().map(|p| Geometry::Line(vec![p])).collect(),
            unlabeled: 0,
        }
    }

    fn scenes() -> Vec<Scene> {
        let wiggle = |t: f64, k: f64| 9.0 * (t * 9.0 + k).sin();
        let two = lines(
            vec![
                curve(12, 1.0, |t| 230.0 - 150.0 * t + wiggle(t, 0.0)),
                curve(12, 1.0, |t| 70.0 + 150.0 * t + wiggle(t, 2.0)),
            ],
            "two-crossing",
        );
        let five = lines(
            (0..5)
                .map(|k| {
                    let (a, b) = (
                        [40.0, 100.0, 170.0, 230.0, 280.0][k],
                        150.0 + 12.0 * k as f64,
                    );
                    curve(12, 1.0, move |t| {
                        a + (b - a) * t.powf(1.5) + wiggle(t, k as f64)
                    })
                })
                .collect(),
            "five-lines",
        );
        let converge = lines(
            (0..3)
                .map(|k| {
                    let a = [40.0, 150.0, 260.0][k];
                    curve(12, 1.0, move |t| {
                        a + (150.0 - a) * t.powi(2) + wiggle(t, k as f64) * (1.0 - t)
                    })
                })
                .collect(),
            "converge-end",
        );
        let early = lines(
            vec![
                curve(12, 1.0, |t| 90.0 + 60.0 * t + wiggle(t, 1.0)),
                curve(12, 0.6, |t| 200.0 - 40.0 * t + wiggle(t, 3.0)),
                curve(12, 1.0, |t| 260.0 - 30.0 * t + wiggle(t, 5.0)),
            ],
            "early-end",
        );
        let xs: Vec<f64> = (0..=10).map(|k| f64::from(k) * PLOT.0 / 10.0).collect();
        let thickness = |k: usize, x: f64| {
            let t = x / PLOT.0;
            match k {
                0 => 90.0 + 30.0 * (t * 6.0).sin(),
                1 => 60.0 - 25.0 * t,
                2 => 9.0 + 3.0 * (t * 5.0).sin(),
                3 => 50.0 + 20.0 * t,
                _ => 1.5,
            }
        };
        let mut tops = vec![PLOT.1; xs.len()];
        let geometry = (0..5)
            .map(|k| {
                Geometry::Band(
                    xs.iter()
                        .enumerate()
                        .map(|(i, &x)| {
                            let lo = tops[i];
                            tops[i] -= thickness(k, x);
                            (x, tops[i], lo)
                        })
                        .collect(),
                )
            })
            .collect();
        let area = Scene {
            title: "stacked-area",
            geometry,
            unlabeled: 2,
        };
        let packed = lines(
            (0..6)
                .map(|k| {
                    curve(12, 1.0, move |t| {
                        150.0 + 4.0 * k as f64 + 2.0 * (t * 7.0 + k as f64).sin()
                    })
                })
                .collect(),
            "no-room",
        );
        vec![two, five, converge, early, area, packed]
    }

    fn text_width(fonts: &measure::FontBook, text: &str) -> f64 {
        fonts
            .measure(text, "'Inter Variable', Inter", FONT)
            .expect("name measures")
    }

    fn render(scene: &Scene, outcomes: &[Outcome]) -> String {
        let (left, top) = (40.0, 20.0);
        let mut svg = format!("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"640\" height=\"360\" viewBox=\"0 0 640 360\" font-family=\"Inter, sans-serif\" font-size=\"{FONT}\"><rect width=\"640\" height=\"360\" fill=\"#fff\"/>");
        let _ = write!(svg, "<text x=\"{left}\" y=\"14\" font-size=\"11\" fill=\"#555\">{}</text><g transform=\"translate({left} {top})\"><rect width=\"{}\" height=\"{}\" fill=\"none\" stroke=\"#ddd\"/>", scene.title, PLOT.0, PLOT.1);
        let mut small = vec![];
        for (k, g) in scene.geometry.iter().enumerate() {
            let color = COLORS[k];
            match g {
                Geometry::Line(runs) => {
                    for p in runs {
                        let pts: Vec<String> =
                            p.iter().map(|(x, y)| format!("{x:.1},{y:.1}")).collect();
                        let _ = write!(svg, "<polyline points=\"{}\" fill=\"none\" stroke=\"{color}\" stroke-width=\"2\"/>", pts.join(" "));
                    }
                }
                Geometry::Band(r) => {
                    let up: Vec<String> = r
                        .iter()
                        .map(|(x, hi, _)| format!("{x:.1},{hi:.1}"))
                        .collect();
                    let down: Vec<String> = r
                        .iter()
                        .rev()
                        .map(|(x, _, lo)| format!("{x:.1},{lo:.1}"))
                        .collect();
                    let _ = write!(svg, "<polygon points=\"{} {}\" fill=\"{color}\" fill-opacity=\"0.35\" stroke=\"{color}\" stroke-width=\"0.5\"/>", up.join(" "), down.join(" "));
                }
            }
            match &outcomes[k] {
                Outcome::Placed(spot) | Outcome::Collides(spot) => {
                    let b = spot.b;
                    let collides = matches!(outcomes[k], Outcome::Collides(_));
                    let (stroke, dash) = if collides {
                        ("#e00", "")
                    } else {
                        ("#888", " stroke-dasharray=\"2 2\"")
                    };
                    let _ = write!(svg, "<rect x=\"{:.1}\" y=\"{:.1}\" width=\"{:.1}\" height=\"{:.1}\" fill=\"none\" stroke=\"{stroke}\" stroke-width=\"0.75\"{dash}/>", b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1);
                    match &spot.glyphs {
                        Some(glyphs) => {
                            for (c, (g, _)) in NAMES[k].chars().zip(glyphs) {
                                let _ = write!(svg, "<text transform=\"translate({:.2} {:.2}) rotate({:.2})\" text-anchor=\"middle\" fill=\"{color}\" font-weight=\"400\">{c}</text>", g.at.0, g.at.1, g.angle);
                            }
                        }
                        None => {
                            let _ = write!(svg, "<text x=\"{:.1}\" y=\"{:.1}\" fill=\"{color}\" font-weight=\"400\">{}</text>", b.x1, b.y2 - 3.0, NAMES[k]);
                        }
                    }
                }
                Outcome::TooThin => small.push(NAMES[k]),
                Outcome::Thin => {}
            }
        }
        svg.push_str("</g>");
        if !small.is_empty() {
            let _ = write!(svg, "<text x=\"{left}\" y=\"350\" font-size=\"11\" fill=\"#555\">Too small to label: {}</text>", small.join(", "));
        }
        svg.push_str("</svg>");
        svg
    }

    /// The bend scenes for setting the curve threshold: each named line of a scene, drawn straight
    /// and along its line, with the stretch's measured bend.
    fn bend_scenes() -> Vec<Scene> {
        let mut seed = 7_u64;
        let mut noise = move || {
            seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1);
            ((seed >> 33) as f64 / f64::from(u32::MAX >> 1)) - 0.5
        };
        let noisy: Vec<(f64, f64)> = (0..40)
            .map(|k| {
                let t = f64::from(k) / 39.0;
                (t * PLOT.0, 210.0 - 90.0 * t + 16.0 * noise())
            })
            .collect();
        let pi = std::f64::consts::PI;
        vec![
            lines(
                vec![curve(24, 1.0, |t| 250.0 - 140.0 * t.powf(1.6))],
                "slow-rise",
            ),
            lines(
                vec![curve(24, 1.0, |t| 230.0 - 110.0 * (pi * t).sin())],
                "shallow-arc",
            ),
            lines(
                vec![curve(24, 1.0, |t| 240.0 - 180.0 * (pi * t).sin())],
                "rounder-arc",
            ),
            lines(
                vec![curve(24, 1.0, |t| {
                    250.0 - 190.0 / (1.0 + (-9.0 * (t - 0.5)).exp())
                })],
                "s-curve",
            ),
            lines(
                vec![curve(48, 1.0, |t| {
                    250.0 - 200.0 * (-((t - 0.5) / 0.1).powi(2)).exp()
                })],
                "steep-hump",
            ),
            lines(
                vec![curve(48, 1.0, |t| {
                    250.0 - 160.0 * (-((t - 0.5) / 0.2).powi(2)).exp()
                })],
                "medium-hump",
            ),
            lines(
                vec![curve(24, 1.0, |t| 280.0 - 260.0 * (pi * t).sin().powi(3))],
                "tight-arc",
            ),
            lines(
                vec![curve(24, 1.0, |t| {
                    220.0 - 100.0 * t + 9.0 * (t * 9.0).sin()
                })],
                "wiggly-rise",
            ),
            lines(
                vec![(0..=28)
                    .map(|k| {
                        (
                            f64::from(k) * PLOT.0 / 28.0,
                            if k % 2 == 0 { 170.0 } else { 130.0 },
                        )
                    })
                    .collect()],
                "sawtooth",
            ),
            lines(vec![noisy], "noisy"),
        ]
    }

    /// Each glyph edge of `name`: the width of every prefix, from 0 to the whole name.
    fn edges(fonts: &measure::FontBook, name: &str) -> Vec<f64> {
        let chars: Vec<char> = name.chars().collect();
        (0..=chars.len())
            .map(|i| text_width(fonts, &chars[..i].iter().collect::<String>()))
            .collect()
    }

    /// One name at one spot above its line: its bend along the smooth, whether it curves, and an
    /// SVG of the spot, the name set straight on the left and along its line on the right, with
    /// the smooth it follows dashed.
    fn spot_svg(
        scene: &Scene,
        owner: usize,
        x: f64,
        fonts: &measure::FontBook,
    ) -> Option<(curve::Bend, bool, String)> {
        let name = NAMES[owner];
        let width = text_width(fonts, name);
        let mut paths = String::new();
        for (k, g) in scene.geometry.iter().enumerate() {
            let Geometry::Line(runs) = g else {
                panic!("bend scenes are lines");
            };
            for p in runs {
                let pts: Vec<String> = p.iter().map(|(x, y)| format!("{x:.1},{y:.1}")).collect();
                let _ = write!(paths, "<polyline points=\"{}\" fill=\"none\" stroke=\"{}\" stroke-width=\"2\" stroke-linejoin=\"round\"/>", pts.join(" "), COLORS[k]);
            }
        }
        let Geometry::Line(runs) = &scene.geometry[owner] else {
            panic!("bend scenes are lines");
        };
        let run = &runs[0];
        let y = lerp_at(&run, x);
        // The ink box clears the line by `LINE_PAD + NAME_GAP`; the baseline sits `FONT - ascent`
        // over the box bottom.
        let d = LINE_PAD + NAME_GAP + FONT - (0.8 * FONT).round();
        let smooth = curve::SMOOTH_FONT_SIZES * FONT;
        let chain = curve::Chain::new(run);
        let bend = curve::bend(&curve::stretch(&chain, x, width, smooth)?, FONT / 2.0);
        let curves = curve::gentle(&bend, FONT);
        let along = curve::drawn(&chain, x, width + 4.0, FONT)?;
        let base = curve::offset(&along, d + curve::lift(&chain, &along));
        let guide: Vec<String> = along
            .iter()
            .map(|(x, y)| format!("{x:.1},{y:.1}"))
            .collect();
        let chars: Vec<char> = name.chars().collect();
        let mids: Vec<f64> = edges(fonts, name)
            .windows(2)
            .map(|w| (w[0] + w[1]) / 2.0 - width / 2.0)
            .collect();
        let color = COLORS[owner];
        let (vw, vh) = (width + 120.0, 110.0);
        let (x0, y0) = (x - vw / 2.0, y - 70.0);
        let flat: Vec<(f64, f64)> = (0..=(width + 4.0).ceil() as usize)
            .map(|i| (x - width / 2.0 - 2.0 + i as f64, y))
            .collect();
        let straight = |lift: f64| {
            format!(
                "<text x=\"{:.1}\" y=\"{:.1}\" fill=\"{color}\">{name}</text>",
                x - width / 2.0,
                y - d - lift
            )
        };
        let mut svg = format!("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{w}\" height=\"{h}\" viewBox=\"0 0 {w} {h}\" font-family=\"Inter, sans-serif\" font-size=\"{FONT}\"><rect width=\"100%\" height=\"100%\" fill=\"#fff\"/>", w = 2.0 * vw + 20.0, h = vh);
        for (side, new) in [(0.0, false), (vw + 20.0, true)] {
            let _ = write!(svg, "<svg x=\"{side}\" width=\"{vw}\" height=\"{vh}\" viewBox=\"{x0:.1} {y0:.1} {vw} {vh}\" overflow=\"hidden\">{paths}");
            if !new {
                svg.push_str(&straight(0.0));
            } else if !curves {
                svg.push_str(&straight(curve::lift(&chain, &flat)));
            } else {
                let _ = write!(svg, "<polyline points=\"{}\" fill=\"none\" stroke=\"#999\" stroke-width=\"1\" stroke-dasharray=\"3 3\"/>", guide.join(" "));
                for (c, g) in chars.iter().zip(curve::glyphs(&base, &mids)?) {
                    let c = if *c == ' ' {
                        "&#160;".to_string()
                    } else {
                        c.to_string()
                    };
                    let _ = write!(svg, "<text transform=\"translate({:.2} {:.2}) rotate({:.2})\" text-anchor=\"middle\" fill=\"{color}\">{c}</text>", g.at.0, g.at.1, g.angle);
                }
            }
            svg.push_str("</svg>");
        }
        svg.push_str("</svg>");
        Some((bend, curves, svg))
    }

    #[test]
    fn bend_scenes_measure_every_spot_that_fits_and_dump_straight_and_curved_svgs_on_request() {
        let fonts = measure::FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load");
        let dir = std::env::var_os("SERIES_NAME_CURVE_DIR").map(std::path::PathBuf::from);
        let mut index =
            String::from("id\tscene\tname\tturning_deg\tmin_radius_font_sizes\tcurves\n");
        for scene in bend_scenes() {
            for (owner, name) in NAMES.iter().enumerate().take(scene.geometry.len()) {
                for (k, share) in [0.25, 0.5, 0.75].into_iter().enumerate() {
                    // A spot too near its line's ends has no stretch to measure.
                    let Some((bend, curves, svg)) = spot_svg(&scene, owner, share * PLOT.0, &fonts)
                    else {
                        continue;
                    };
                    assert!(bend.turning >= 0.0 && bend.min_radius > 0.0, "{bend:?}");
                    let id = format!("{}-{}-{}", scene.title, owner, k + 1);
                    let _ = writeln!(
                        index,
                        "{id}\t{}\t{}\t{:.1}\t{:.2}\t{curves}",
                        scene.title,
                        name,
                        bend.turning.to_degrees(),
                        bend.min_radius / FONT
                    );
                    if let Some(dir) = &dir {
                        std::fs::create_dir_all(dir).expect("fixture dir");
                        std::fs::write(dir.join(format!("{id}.svg")), svg)
                            .expect("fixture written");
                    }
                }
            }
        }
        if let Some(dir) = &dir {
            std::fs::write(dir.join("spots.tsv"), index).expect("index written");
        }
    }

    #[test]
    fn every_scene_places_each_labelable_name_and_dumps_svgs_on_request() {
        let fonts = measure::FontBook::load_dir(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        ))
        .expect("shipped fonts load");
        let widths: Vec<f64> = NAMES.iter().map(|n| text_width(&fonts, n)).collect();
        let dir = std::env::var_os("SERIES_NAME_FIXTURE_DIR").map(std::path::PathBuf::from);
        for scene in scenes() {
            {
                let names: Vec<Name> = scene
                    .geometry
                    .iter()
                    .enumerate()
                    .map(|(owner, g)| Name {
                        owner,
                        size: (widths[owner], FONT),
                        text: NAMES[owner].into(),
                        edges: edges(&fonts, NAMES[owner]),
                        geometry: match g {
                            Geometry::Line(p) => Geometry::Line(p.clone()),
                            Geometry::Band(r) => Geometry::Band(r.clone()),
                        },
                        prefer_x: None,
                    })
                    .collect();
                let mut field = Field {
                    plot: PLOT,
                    obstacles: vec![],
                    paths: vec![],
                };
                let out = place_series_names(&names, &mut field, true);
                let unlabeled = out
                    .iter()
                    .filter(|o| matches!(o, Outcome::Thin | Outcome::TooThin))
                    .count();
                assert_eq!(unlabeled, scene.unlabeled, "{}: {out:?}", scene.title);
                if let Some(dir) = &dir {
                    std::fs::create_dir_all(dir).expect("fixture dir");
                    std::fs::write(
                        dir.join(format!("{}.svg", scene.title)),
                        render(&scene, &out),
                    )
                    .expect("fixture written");
                }
            }
        }
    }
}

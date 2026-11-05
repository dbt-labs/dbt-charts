//! Collision-aware placement. `best_spot` picks the candidate box that overlaps the
//! least ink, for annotation notes (`annotation::place::arrange`). `place_value_labels`
//! ranks a value label's spots on more than overlap: which series is nearest, fills and
//! gridlines, and how far the label moves.

use crate::common::{value_label_anchor, CaptionSpot};
use crate::annotation::DataMark;
use diagnostic::codes;
use diagnostic::Diagnostic;
use scene::{Node, Role, Shape};
use serde_json::json;
use crate::vega_fit::{self, Bounds};

/// Overlap areas and distances closer than this tie.
pub(crate) const EPS: f64 = 1e-6;

/// The area `a` and `b` share.
pub(crate) fn intersection(a: &Bounds, b: &Bounds) -> f64 {
    (a.x2.min(b.x2) - a.x1.max(b.x1)).max(0.0) * (a.y2.min(b.y2) - a.y1.max(b.y1)).max(0.0)
}

/// The summed area `b` shares with `obstacles`.
pub(crate) fn overlap(b: &Bounds, obstacles: &[Bounds]) -> f64 {
    obstacles.iter().map(|o| intersection(b, o)).sum()
}

/// The index of the best of `candidates` (`(group, box)`; the text's own box
/// first). A box not within `room` is skipped. The least overlap wins, then
/// the earlier group, then the shortest move from the first box's center.
pub(crate) fn best_spot(candidates: &[(u8, Bounds)], room: &Bounds, obstacles: &[Bounds]) -> usize {
    let inside =
        |b: &Bounds| b.x1 >= room.x1 - EPS && b.y1 >= room.y1 - EPS && b.x2 <= room.x2 + EPS && b.y2 <= room.y2 + EPS;
    let center = |b: &Bounds| ((b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0);
    let (sx, sy) = center(&candidates[0].1);
    let mut best: Option<(f64, u8, f64, usize)> = None;
    for (k, (group, b)) in candidates.iter().enumerate() {
        if !inside(b) {
            continue;
        }
        let (cx, cy) = center(b);
        let (o, d) = (overlap(b, obstacles), (cx - sx).hypot(cy - sy));
        let better = |(bo, bg, bd, _): (f64, u8, f64, usize)| {
            o < bo - EPS || (o < bo + EPS && (*group < bg || (*group == bg && d < bd - EPS)))
        };
        if best.is_none_or(better) {
            best = Some((o, *group, d, k));
        }
    }
    best.map_or(0, |(_, _, _, k)| k)
}

/// The gap an `auto` value label keeps from marks and from other labels.
pub(crate) const LABEL_PAD: f64 = 3.0;
/// The gap it keeps from another value label: a word space, so two numbers never read as one.
pub(crate) const LABEL_GAP: f64 = 6.0;
/// The gap it keeps from a baseline rule.
pub(crate) const RULE_PAD: f64 = 1.0;
/// The gap it keeps from a line, which its own label necessarily passes close to.
pub(crate) const LINE_PAD: f64 = 2.0;
/// A preferred spot with at least this much room to spare stays put.
const COMFORT: f64 = 2.0 * LABEL_PAD;
/// More dropped labels than this share of a chart's labels raises the thinned warning.
pub(crate) const THINNED_SHARE: f64 = 0.25;
/// Slack past this buys nothing: a spot this open ranks by tier, so a label stays centered on its datum.
const OPEN_ENOUGH: f64 = LABEL_PAD;
/// How much further out than its default offset a label may step off a sloped line.
const STEP_OUT: [f64; 2] = [4.0, 8.0];
/// Points nearer than this read as one point.
const COINCIDENT: f64 = 6.0;
/// Scatter points nearer than this share a label's height, so their labels part sideways.
const PAIR_REACH: f64 = 12.0;
/// How much of its text box's top and bottom an axis-channel label gives up: digits fill
/// far less than a line box, so two numbers read apart once they are a digit height away.
const DIGIT_INSET: f64 = 4.5;
/// Both offsets of a label set off a corner of its datum.
const DIAGONAL: f64 = 4.0;
/// The radius a label's default offset already clears; a larger marker pushes it out.
const OWN_REF_RADIUS: f64 = 3.0;

/// Something a value label keeps off, with the gap it keeps from it: a box, or a
/// stroked polyline (`stroke`: its points and half width), which `b` bounds.
#[derive(Clone)]
pub(crate) struct Obstacle {
    pub(crate) b: Bounds,
    pub(crate) pad: f64,
    /// A fill a label may sit over; a spot clear of every soft box ranks above one over it.
    soft: bool,
    /// A gridline a label may cross; a spot clear of every faint box ranks above one on it.
    faint: bool,
    stroke: Option<(Vec<(f64, f64)>, f64)>,
}

impl Obstacle {
    pub(crate) fn mark(b: Bounds) -> Self {
        Obstacle { b, pad: LABEL_PAD, soft: false, faint: false, stroke: None }
    }

    pub(crate) fn label(b: Bounds) -> Self {
        Obstacle { pad: LABEL_GAP, ..Obstacle::mark(b) }
    }

    pub(crate) fn rule(b: Bounds) -> Self {
        Obstacle { b, pad: RULE_PAD, soft: false, faint: false, stroke: None }
    }

    pub(crate) fn grid(b: Bounds) -> Self {
        Obstacle { faint: true, ..Obstacle::rule(b) }
    }

    /// A chart mark as obstacles: a line is its true stroke, an area's fill soft column boxes.
    pub(crate) fn of_mark(mark: &DataMark) -> Vec<Obstacle> {
        match mark {
            DataMark::Tinted(inner, ..) => Obstacle::of_mark(inner),
            DataMark::Line { points, width } if !points.is_empty() => {
                let xs = points.iter().map(|p| p.0);
                let ys = points.iter().map(|p| p.1);
                let (x1, x2) = (xs.clone().fold(f64::INFINITY, f64::min), xs.fold(f64::NEG_INFINITY, f64::max));
                let (y1, y2) = (ys.clone().fold(f64::INFINITY, f64::min), ys.fold(f64::NEG_INFINITY, f64::max));
                vec![Obstacle { b: Bounds::new(x1, y1, x2, y2), pad: LINE_PAD, soft: false, faint: false, stroke: Some((points.clone(), width / 2.0)) }]
            }
            DataMark::Polygon(_) => mark.boxes().into_iter().map(|b| Obstacle { soft: true, ..Obstacle::mark(b) }).collect(),
            _ => mark.boxes().into_iter().map(Obstacle::mark).collect(),
        }
    }

    /// The distance from `b` to this obstacle's ink (0 when they touch).
    fn gap(&self, b: &Bounds) -> f64 {
        let Some((points, half)) = &self.stroke else { return gap(b, &self.b) };
        if gap(b, &self.b) - half > 3.0 * LABEL_PAD + 16.0 {
            return f64::INFINITY;
        }
        let nearest = match points.as_slice() {
            [only] => segment_box_distance(*only, *only, b),
            _ => points.windows(2).map(|w| segment_box_distance(w[0], w[1], b)).fold(f64::INFINITY, f64::min),
        };
        (nearest - half).max(0.0)
    }
}

/// The distance between segment `p`-`q` and `b`.
fn segment_box_distance(p: (f64, f64), q: (f64, f64), b: &Bounds) -> f64 {
    // Liang-Barsky: a segment through the box is at distance 0.
    let (mut t0, mut t1) = (0.0_f64, 1.0_f64);
    let d = (q.0 - p.0, q.1 - p.1);
    let mut crosses = true;
    for (delta, lo, hi, start) in [(d.0, b.x1, b.x2, p.0), (d.1, b.y1, b.y2, p.1)] {
        if delta.abs() < EPS {
            crosses &= start >= lo && start <= hi;
        } else {
            let (ta, tb) = ((lo - start) / delta, (hi - start) / delta);
            t0 = t0.max(ta.min(tb));
            t1 = t1.min(ta.max(tb));
        }
    }
    if crosses && t0 <= t1 {
        return 0.0;
    }
    let to_box = |pt: (f64, f64)| (b.x1 - pt.0).max(pt.0 - b.x2).max(0.0).hypot((b.y1 - pt.1).max(pt.1 - b.y2).max(0.0));
    let corners = [(b.x1, b.y1), (b.x2, b.y1), (b.x1, b.y2), (b.x2, b.y2)];
    corners.iter().map(|c| path_distance(&[p, q], *c)).fold(to_box(p).min(to_box(q)), f64::min)
}

/// Laid-out nodes and the shift that takes their frame into the plot's.
pub(crate) type Frame<'a> = (&'a [Node], (f64, f64));

/// What a value label keeps off, read from the laid-out nodes: the marks among `plot`, and in
/// `plot` and every `beside` frame the gridlines (`Role::Grid` lines), reference rules
/// (`Role::Rule` lines) and measured axis and label text. A line is inflated by its stroke width.
pub(crate) fn obstacles(plot: &[Node], beside: &[Frame]) -> Vec<Obstacle> {
    let mut out: Vec<Obstacle> = DataMark::plot_marks(plot).iter().flat_map(Obstacle::of_mark).collect();
    for (nodes, (dx, dy)) in std::iter::once((plot, (0.0, 0.0))).chain(beside.iter().copied()) {
        for d in scene::drawn(nodes, |role| matches!(role, Role::Grid | Role::Rule | Role::Axis | Role::Label)) {
            // 1 is SVG's initial `stroke-width`: a line that sets none draws that wide.
            let (b, half) = (&d.bounds, d.paint_num("stroke-width").unwrap_or(1.0) * d.map.a.hypot(d.map.b) / 2.0);
            let shifted = |pad: f64| Bounds::new(b.x1 - pad + dx, b.y1 - pad + dy, b.x2 + pad + dx, b.y2 + pad + dy);
            match (&d.node.role, &d.node.shape) {
                (Role::Grid, Shape::Line { .. }) => out.push(Obstacle::grid(shifted(half))),
                (Role::Rule, Shape::Line { .. }) => out.push(Obstacle::rule(shifted(half))),
                (Role::Axis | Role::Label, Shape::Text { .. }) => out.push(Obstacle::mark(shifted(0.0))),
                _ => {}
            }
        }
    }
    out
}

/// The measured axis text among `frames` as `(center y, text)` in the plot's frame: the drawn tick labels.
pub(crate) fn tick_labels(frames: &[Frame]) -> Vec<(f64, String)> {
    let axis_text = frames.iter().flat_map(|&(nodes, shift)| scene::drawn(nodes, |role| *role == Role::Axis).into_iter().map(move |d| (d, shift)));
    axis_text.filter_map(|(d, (_, dy))| Some(((d.bounds.y1 + d.bounds.y2) / 2.0 + dy, d.node.text_content()?))).collect()
}

/// The height of every horizontal gridline among `nodes`, in their frame.
pub(crate) fn grid_rows(nodes: &[Node]) -> Vec<f64> {
    let grid = scene::drawn(nodes, |role| *role == Role::Grid);
    grid.into_iter().filter(|d| matches!(d.node.shape, Shape::Line { .. }) && d.bounds.y1 == d.bounds.y2).map(|d| d.bounds.y1).collect()
}

/// Whether a datum's value label only repeats its y axis: the datum sits on the
/// axis edge `edge_x`, level with a drawn tick whose label reads the same text.
/// `ticks` are the drawn ticks' `(y, label)`.
pub(crate) fn repeats_axis(anchor: (f64, f64), text: &str, edge_x: f64, ticks: &[(f64, String)]) -> bool {
    (anchor.0 - edge_x).abs() < 1.0 && ticks.iter().any(|(y, label)| (anchor.1 - y).abs() < 1.0 && label == text)
}

/// `WARN-VALUE-LABELS-THINNED`: raised when more than `THINNED_SHARE` of a chart's
/// `total` auto labels were `dropped`. Returns whether it fired.
pub(crate) fn warn_value_labels_thinned(chart_id: &str, dropped: usize, total: usize) -> bool {
    let fires = dropped as f64 > THINNED_SHARE * total as f64;
    if fires {
        diagnostic::warn(Diagnostic::new(
            &codes::WARN_VALUE_LABELS_THINNED,
            [("chart_id", json!(chart_id)), ("dropped", json!(dropped)), ("total", json!(total))],
        ));
    }
    fires
}

/// `WARN-VALUE-LABELS-NO-MARKERS`: a line or area with value labels and no point markers,
/// where a label left its default spot or several series are labeled, so a label
/// cannot be traced to its point.
pub(crate) fn warn_value_labels_no_markers(chart_id: &str) {
    diagnostic::warn(Diagnostic::new(&codes::WARN_VALUE_LABELS_NO_MARKERS, [("chart_id", json!(chart_id))]));
}

/// Which of `points` keep a label: of points that read as one, the top-painted.
pub(crate) fn top_of_each_cluster(points: &[(f64, f64)], paint_rank: &[usize]) -> Vec<bool> {
    let mut by_rank: Vec<usize> = (0..points.len()).collect();
    by_rank.sort_by_key(|&i| std::cmp::Reverse(paint_rank[i]));
    let mut keep = vec![false; points.len()];
    for i in by_rank {
        let near = |j: usize| (points[i].0 - points[j].0).hypot(points[i].1 - points[j].1) < COINCIDENT;
        keep[i] = !(0..points.len()).any(|j| keep[j] && near(j));
    }
    keep
}

/// For each point, the side its label takes when one other point sits on top of it:
/// `right` for the upper-right of the two, `left` for the other, so neither label
/// sits between them.
pub(crate) fn pair_sides(points: &[(f64, f64)]) -> Vec<Option<&'static str>> {
    let distance = |i: usize, j: usize| (points[i].0 - points[j].0).hypot(points[i].1 - points[j].1);
    let nearest = |i: usize| (0..points.len()).filter(|&j| j != i).min_by(|&a, &b| distance(i, a).total_cmp(&distance(i, b)));
    (0..points.len())
        .map(|i| {
            let j = nearest(i).filter(|&j| distance(i, j) < PAIR_REACH && nearest(j) == Some(i))?;
            let (dx, dy) = (points[i].0 - points[j].0, points[j].1 - points[i].1);
            Some(if dx + dy > 0.0 || (dx + dy == 0.0 && i > j) { "right" } else { "left" })
        })
        .collect()
}

/// The spot a parted pair's label takes: beside its point, or on the same diagonal
/// when a gridline `rows` would run through the digits, or on the plot's inner side
/// when the side spot leaves the plot, so a pair on an edge stays together inside it.
pub(crate) fn pair_spot(side: &str, anchor: (f64, f64), width: f64, size: f64, plot_w: f64, rows: &[f64]) -> CaptionSpot {
    let (right, reach) = (side == "right", DIAGONAL + width);
    let inside = if right { anchor.0 + reach <= plot_w } else { anchor.0 - reach >= 0.0 };
    if inside && !rows.iter().any(|y| (y - anchor.1).abs() <= size / 2.0) {
        let (align, baseline, dx, dy) = value_label_anchor(side).expect("a value-label position");
        return CaptionSpot { edge: None, align, baseline, dx, dy };
    }
    let ahead = right == inside;
    let (dy, baseline) = if right { (-DIAGONAL, "bottom") } else { (DIAGONAL, "top") };
    CaptionSpot { edge: None, align: if ahead { "left" } else { "right" }, baseline, dx: if ahead { DIAGONAL } else { -DIAGONAL }, dy }
}

/// One value label to place.
pub(crate) struct LabelReq {
    /// The datum's pixel.
    pub(crate) anchor: (f64, f64),
    pub(crate) width: f64,
    pub(crate) preferred: CaptionSpot,
    /// An authored position, or a spot tied to its band, never moves or drops.
    pub(crate) movable: bool,
    /// Which of the field's `paths` the datum belongs to.
    pub(crate) owner: usize,
    /// Labels of one group share a side of their marks where they can.
    pub(crate) group: usize,
    /// The datum's own marker radius (0 when it draws none).
    pub(crate) own_radius: f64,
    /// `preferred` is the axis labels' channel, where it may sit tighter against the numbers.
    pub(crate) channel: bool,
}

/// What a chart's value labels are placed against.
pub(crate) struct Field {
    /// The plot's size.
    pub(crate) plot: (f64, f64),
    /// Marks and chrome the labels keep off.
    pub(crate) obstacles: Vec<Obstacle>,
    /// Per owner, the points of the series (a line) or the lone point (a scatter);
    /// a label belongs nearer its own owner than any other.
    pub(crate) paths: Vec<Vec<(f64, f64)>>,
}

fn gap(a: &Bounds, b: &Bounds) -> f64 {
    let dx = (b.x1 - a.x2).max(a.x1 - b.x2).max(0.0);
    let dy = (b.y1 - a.y2).max(a.y1 - b.y2).max(0.0);
    dx.max(dy)
}

fn path_distance(path: &[(f64, f64)], p: (f64, f64)) -> f64 {
    let to_segment = |a: (f64, f64), b: (f64, f64)| {
        let (vx, vy) = (b.0 - a.0, b.1 - a.1);
        let len2 = vx * vx + vy * vy;
        let t = if len2 < EPS { 0.0 } else { (((p.0 - a.0) * vx + (p.1 - a.1) * vy) / len2).clamp(0.0, 1.0) };
        (p.0 - (a.0 + t * vx)).hypot(p.1 - (a.1 + t * vy))
    };
    match path {
        [] => f64::INFINITY,
        [only] => to_segment(*only, *only),
        _ => path.windows(2).map(|w| to_segment(w[0], w[1])).fold(f64::INFINITY, f64::min),
    }
}

/// The labels' placement order: every series' last datum, then each series' first,
/// highest and lowest, then local peaks and troughs, then everything else in data
/// order. `series[i]` and `values[i]` describe label `i`; a series' labels are taken in data order.
pub(crate) fn priority_order(series: &[usize], values: &[f64]) -> Vec<usize> {
    let mut seen: Vec<usize> = Vec::new();
    for &s in series {
        if !seen.contains(&s) {
            seen.push(s);
        }
    }
    let own = |s: usize| (0..series.len()).filter(move |&i| series[i] == s);
    let mut order: Vec<usize> = seen.iter().filter_map(|&s| own(s).last()).collect();
    for &s in &seen {
        let extreme = |better: fn(f64, f64) -> bool| own(s).reduce(|a, b| if better(values[b], values[a]) { b } else { a });
        for i in [own(s).next(), extreme(|x, y| x > y), extreme(|x, y| x < y)].into_iter().flatten() {
            if !order.contains(&i) {
                order.push(i);
            }
        }
    }
    let turns = |i: &usize| {
        let own: Vec<usize> = (0..series.len()).filter(|&j| series[j] == series[*i]).collect();
        let at = own.iter().position(|j| j == i).expect("its own series");
        at > 0 && at + 1 < own.len() && (values[*i] - values[own[at - 1]]) * (values[*i] - values[own[at + 1]]) > 0.0
    };
    let (peaks, slopes): (Vec<usize>, Vec<usize>) = (0..series.len()).filter(|i| !order.contains(i)).partition(turns);
    order.extend(peaks);
    order.extend(slopes);
    order
}

/// Where each `auto` value label goes, or `None` when it has no acceptable spot
/// and is dropped. Authored labels (`movable == false`) stay at their preferred
/// spot and are placed first, so the rest keep off them. The rest are placed in
/// `order`, each joining the obstacles for the next.
///
/// A spot is acceptable when it keeps its pad from every obstacle and stays within
/// the plot, or no further out than the preferred spot already sat. Gridlines
/// never make a spot unacceptable. The preferred spot stays when it is acceptable
/// with room to spare and no other series is nearer it than the label's own;
/// otherwise the best acceptable spot wins: no nearer a foreign series, not over a
/// fill, off the gridlines, then open enough, then the earlier tier (preferred; the
/// four sides; the sides stepped out; diagonals), then the shortest move.
///
/// A label offered the axis labels' channel takes it whenever it collides with nothing.
///
/// A second pass moves each group to the side most of its labels took, where any
/// acceptable spot there will do. It stands unless it drops more labels.
pub(crate) fn place_value_labels(reqs: &[LabelReq], order: &[usize], size: f64, field: &mut Field) -> Vec<Option<CaptionSpot>> {
    let mut spots: Vec<Option<CaptionSpot>> = vec![None; reqs.len()];
    let ink = |r: &LabelReq, s: &CaptionSpot| vega_fit::text_ink(r.anchor.0 + s.dx, r.anchor.1 + s.dy, r.width, size, s.align, s.baseline);
    for (i, r) in reqs.iter().enumerate().filter(|(_, r)| !r.movable) {
        spots[i] = Some(r.preferred);
        field.obstacles.push(Obstacle::mark(ink(r, &r.preferred)));
    }
    let fixed = field.obstacles.len();
    let pass = |reqs: &[LabelReq], comfort: f64, field: &mut Field| {
        let mut placed = spots.clone();
        for &i in order.iter().filter(|&&i| reqs[i].movable) {
            placed[i] = place_one(&reqs[i], size, comfort, field);
            if let Some(s) = &placed[i] {
                field.obstacles.push(Obstacle::label(ink(&reqs[i], s)));
            }
        }
        placed
    };
    let first = pass(reqs, COMFORT, field);
    field.obstacles.truncate(fixed);
    let sided = with_majority_sides(reqs, &first);
    let second = pass(&sided, 0.0, field);
    let drops = |placed: &[Option<CaptionSpot>]| placed.iter().filter(|s| s.is_none()).count();
    if drops(&second) > drops(&first) {
        return first;
    }
    second
}

/// `reqs` with each movable label preferring the side, above or below its datum,
/// that most of its group's `placed` labels took. A tied group keeps its preference.
fn with_majority_sides(reqs: &[LabelReq], placed: &[Option<CaptionSpot>]) -> Vec<LabelReq> {
    let lean = |group: usize| -> i32 {
        let spots = reqs.iter().zip(placed).filter(|(r, _)| r.group == group);
        spots.filter_map(|(_, s)| s.as_ref()).map(|s| if s.dx != 0.0 { 0 } else { s.dy.signum() as i32 }).sum()
    };
    reqs.iter()
        .map(|r| {
            let side = match lean(r.group) {
                0 => None,
                l if l < 0 => Some("top"),
                _ => Some("bottom"),
            };
            let preferred = side.filter(|_| r.movable && r.preferred.dx == 0.0).map_or(r.preferred, |position| {
                let (align, baseline, dx, dy) = value_label_anchor(position).expect("a value-label position");
                CaptionSpot { edge: None, align, baseline, dx, dy }
            });
            LabelReq { preferred, ..*r }
        })
        .collect()
}

fn place_one(r: &LabelReq, size: f64, comfort: f64, field: &Field) -> Option<CaptionSpot> {
    let push = (r.own_radius - OWN_REF_RADIUS).max(0.0);
    let out = |d: f64| if d == 0.0 { 0.0 } else { d + push * d.signum() };
    let ink = |s: &CaptionSpot| vega_fit::text_ink(r.anchor.0 + s.dx, r.anchor.1 + s.dy, r.width, size, s.align, s.baseline);
    let spot = |align, baseline, dx: f64, dy: f64| CaptionSpot { edge: None, align, baseline, dx: out(dx), dy: out(dy) };
    let preferred = spot(r.preferred.align, r.preferred.baseline, r.preferred.dx, r.preferred.dy);
    let anchors = ["top", "bottom", "right", "left"].map(|position| {
        let (align, baseline, dx, dy) = value_label_anchor(position).expect("a value-label position");
        spot(align, baseline, dx, dy)
    });
    let diagonals = [("left", "bottom", DIAGONAL, -DIAGONAL), ("right", "bottom", -DIAGONAL, -DIAGONAL), ("left", "top", DIAGONAL, DIAGONAL), ("right", "top", -DIAGONAL, DIAGONAL)]
        .map(|(align, baseline, dx, dy)| spot(align, baseline, dx, dy));
    let away = |v: f64, d: f64| if v == 0.0 { 0.0 } else { v + d * v.signum() };
    let stepped = |s: CaptionSpot| STEP_OUT.map(|d| CaptionSpot { dx: away(s.dx, d), dy: away(s.dy, d), ..s });
    let spots: Vec<(u8, CaptionSpot)> = std::iter::once((0, preferred))
        .chain(stepped(preferred).into_iter().chain(anchors).map(|s| (1, s)))
        .chain(anchors.into_iter().flat_map(stepped).map(|s| (2, s)))
        .chain(diagonals.into_iter().map(|s| (3, s)))
        .collect();
    let start = ink(&preferred);
    // A bubble's push may not carry a label past what the fit reserved; a channel's spot is reserved.
    let reserved = if r.channel { start } else { ink(&r.preferred) };
    let room = Bounds::new(reserved.x1.min(0.0), reserved.y1.min(0.0), reserved.x2.max(field.plot.0), reserved.y2.max(field.plot.1));
    let (sx, sy) = ((start.x1 + start.x2) / 2.0, (start.y1 + start.y2) / 2.0);
    // (spot index, tier, slack, nearer a foreign series, over a fill, on a gridline, move distance)
    let mut best: Option<(usize, u8, f64, bool, bool, bool, f64)> = None;
    for (k, (tier, s)) in spots.iter().enumerate() {
        let b = ink(s);
        if b.x1 < room.x1 - EPS || b.y1 < room.y1 - EPS || b.x2 > room.x2 + EPS || b.y2 > room.y2 + EPS {
            continue;
        }
        // A box centered on the datum is its own marker; the offset clears its radius instead.
        let own_marker = |o: &Obstacle| o.stroke.is_none() && o.pad != RULE_PAD && (o.b.x1 + o.b.x2 - 2.0 * r.anchor.0).abs() < 1.0 && (o.b.y1 + o.b.y2 - 2.0 * r.anchor.1).abs() < 1.0;
        let hard = field.obstacles.iter().filter(|o| !o.soft && !o.faint && !own_marker(o));
        let squeezed = k == 0 && r.channel;
        let inked = if squeezed { Bounds::new(b.x1, b.y1 + DIGIT_INSET, b.x2, b.y2 - DIGIT_INSET) } else { b };
        let slack = hard.map(|o| if squeezed { o.gap(&inked) - EPS } else { o.gap(&b) - o.pad }).fold(size, f64::min);
        let over_fill = field.obstacles.iter().any(|o| o.soft && gap(&b, &o.b) < EPS);
        // A vertical gridline barely touches the digits it passes through; a horizontal one runs along them.
        let on_grid = field.obstacles.iter().any(|o| o.faint && o.b.x2 - o.b.x1 > 3.0 && gap(&b, &o.b) < EPS);
        if slack < -EPS || (squeezed && slack <= 0.0) {
            continue;
        }
        let center = ((b.x1 + b.x2) / 2.0, (b.y1 + b.y2) / 2.0);
        // Outside the plot a label reads only at its point's height.
        if r.channel && k > 0 && (center.0 < -EPS || center.0 > field.plot.0 + EPS) {
            continue;
        }
        let own = path_distance(&field.paths[r.owner], center);
        let foreign = field.paths.iter().enumerate().filter(|(o, _)| *o != r.owner).map(|(_, p)| path_distance(p, center)).fold(f64::INFINITY, f64::min);
        let (nearer_foreign, moved) = (foreign < own - EPS, (center.0 - sx).hypot(center.1 - sy));
        if k == 0 && (r.channel || (slack >= comfort && !nearer_foreign && !over_fill)) {
            return Some(*s);
        }
        let better = |(_, bt, bs, bf, bo, bg, bd): (usize, u8, f64, bool, bool, bool, f64)| {
            if nearer_foreign != bf {
                return !nearer_foreign;
            }
            if over_fill != bo {
                return !over_fill;
            }
            if on_grid != bg {
                return !on_grid;
            }
            let (open, best_open) = (slack.min(OPEN_ENOUGH), bs.min(OPEN_ENOUGH));
            if (open - best_open).abs() > EPS {
                return open > best_open;
            }
            if *tier != bt {
                return *tier < bt;
            }
            moved < bd - EPS
        };
        if best.is_none_or(better) {
            best = Some((k, *tier, slack, nearer_foreign, over_fill, on_grid, moved));
        }
    }
    best.map(|(k, ..)| spots[k].1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use scene::Attrs;

    fn b(x: f64, y: f64) -> Bounds {
        Bounds::new(x, y, x + 10.0, y + 10.0)
    }

    #[test]
    fn least_overlap_wins_then_group_then_distance() {
        let room = Bounds::new(0.0, 0.0, 100.0, 100.0);
        let wall = [b(0.0, 0.0)];
        // Start overlaps the wall; the later group's clear box beats it.
        let picked = best_spot(&[(0, b(0.0, 0.0)), (2, b(40.0, 0.0)), (1, b(60.0, 0.0))], &room, &wall);
        assert_eq!(picked, 2, "equal overlap: the earlier group wins");
        let picked = best_spot(&[(0, b(0.0, 0.0)), (1, b(60.0, 0.0)), (1, b(20.0, 0.0))], &room, &wall);
        assert_eq!(picked, 2, "same group: the shorter move wins");
    }

    #[test]
    fn a_box_outside_room_is_never_picked_and_a_clear_start_stays() {
        let room = Bounds::new(0.0, 0.0, 30.0, 30.0);
        assert_eq!(best_spot(&[(0, b(0.0, 0.0)), (1, b(50.0, 0.0))], &room, &[]), 0);
        let wall = [b(0.0, 0.0)];
        assert_eq!(best_spot(&[(0, b(0.0, 0.0)), (1, b(50.0, 0.0))], &room, &wall), 0);
    }

    /// Full-span lines of `role` across a 200 x 200 plot: `vertical` are `(x, width)`, `horizontal` `(y, width)`.
    fn lines(role: Role, vertical: &[(f64, f64)], horizontal: &[(f64, f64)]) -> Vec<Node> {
        let v = vertical.iter().map(|&(x, w)| Node::line(role.clone(), (x, 0.0), (x, 200.0), Attrs::new().num("stroke-width", w)));
        let h = horizontal.iter().map(|&(y, w)| Node::line(role.clone(), (0.0, y), (200.0, y), Attrs::new().num("stroke-width", w)));
        v.chain(h).collect()
    }

    #[test]
    fn the_builder_maps_each_role_to_its_obstacle_kind_and_reads_stroke_width_from_the_tree() {
        let mark = Node::rect(Role::Mark { layer: 0, series: None, datum: Some(0) }, (10.0, 10.0, 6.0, 6.0), Attrs::new());
        let wide = Node::group(Role::Grid, Attrs::new().num("stroke-width", 4.0), lines(Role::Grid, &[], &[(50.0, 4.0)]));
        let own = Node::line(Role::Rule, (0.0, 80.0), (200.0, 80.0), Attrs::new().num("stroke-width", 2.0));
        let torn = Node::path(Role::Grid, scene::PathData::new().move_to(0.0, 0.0).line_to(9.0, 9.0), Attrs::new());
        let plot = vec![mark, wide, own, torn];
        let tick = Node::text(Role::Axis, (0.0, 0.0), Attrs::new(), "20%").with_text_bounds(scene::Bounds { x1: -30.0, y1: 95.0, x2: -6.0, y2: 106.0 });
        let unmeasured = Node::text(Role::Axis, (0.0, 0.0), Attrs::new(), "title");
        let rail = Node::group(Role::Chrome, Attrs::new(), vec![Node::text(Role::Label, (0.0, 0.0), Attrs::new(), "Series").with_text_bounds(scene::Bounds { x1: 0.0, y1: 20.0, x2: 40.0, y2: 31.0 })]).translate(210.0, 5.0);
        let axis = [tick, unmeasured];
        let found = obstacles(&plot, &[(&axis, (0.0, 0.0)), (std::slice::from_ref(&rail), (-10.0, 0.0))]);
        assert_eq!(found.len(), 5, "mark, grid, rule, tick label, rail label; the torn path and the unmeasured title are no obstacle");
        let kinds: Vec<(f64, bool, bool)> = found.iter().map(|o| (o.pad, o.soft, o.faint)).collect();
        assert_eq!(kinds, vec![(LABEL_PAD, false, false), (RULE_PAD, false, true), (RULE_PAD, false, false), (LABEL_PAD, false, false), (LABEL_PAD, false, false)]);
        assert_eq!((found[1].b.y1, found[1].b.y2), (48.0, 52.0), "a group's stroke width reaches its line");
        assert_eq!((found[2].b.y1, found[2].b.y2), (79.0, 81.0), "a line's own stroke width");
        assert_eq!((found[3].b.x1, found[3].b.y1), (-30.0, 95.0));
        assert_eq!((found[4].b.x1, found[4].b.y1, found[4].b.x2), (200.0, 25.0, 240.0), "a frame's text lands in the plot's frame");
    }

    #[test]
    fn a_tick_label_is_the_measured_axis_text_at_its_center_height() {
        let tick = Node::text(Role::Axis, (0.0, 0.0), Attrs::new(), "20%").with_text_bounds(scene::Bounds { x1: -30.0, y1: 95.0, x2: -6.0, y2: 106.0 });
        let other = Node::text(Role::Label, (0.0, 0.0), Attrs::new(), "x").with_text_bounds(scene::Bounds { x1: 0.0, y1: 0.0, x2: 1.0, y2: 1.0 });
        let frame = Node::group(Role::Axis, Attrs::new(), vec![tick, other]).translate(0.0, 10.0);
        let ticks = tick_labels(&[(std::slice::from_ref(&frame), (0.0, 0.0))]);
        assert_eq!(ticks, vec![(110.5, "20%".to_string())]);
        assert!(repeats_axis((0.0, 110.0), "20%", 0.0, &ticks));
        assert!(!repeats_axis((0.0, 110.0), "30%", 0.0, &ticks));
    }

    fn top() -> CaptionSpot {
        CaptionSpot::top()
    }

    fn req(anchor: (f64, f64), owner: usize) -> LabelReq {
        LabelReq { anchor, width: 20.0, preferred: top(), movable: true, owner, group: owner, own_radius: 0.0, channel: false }
    }

    fn field(obstacles: Vec<Obstacle>, paths: Vec<Vec<(f64, f64)>>) -> Field {
        Field { plot: (200.0, 200.0), obstacles, paths }
    }

    fn ink(s: &CaptionSpot, r: &LabelReq) -> Bounds {
        vega_fit::text_ink(r.anchor.0 + s.dx, r.anchor.1 + s.dy, r.width, 11.0, s.align, s.baseline)
    }

    fn place(r: LabelReq, f: &mut Field) -> Option<CaptionSpot> {
        place_value_labels(&[r], &[0], 11.0, f)[0]
    }

    #[test]
    fn a_label_over_a_neighbor_marker_steps_to_a_clear_spot_with_a_gap() {
        let neighbor = Bounds::new(96.0, 86.0, 104.0, 94.0);
        let r = req((100.0, 100.0), 0);
        let mut f = field(vec![Obstacle::mark(neighbor)], vec![vec![(100.0, 100.0)]]);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        assert!(gap(&ink(&spot, &r), &neighbor) >= LABEL_PAD - EPS);
    }

    #[test]
    fn a_spot_that_clears_but_touches_the_pad_is_not_acceptable() {
        // Top's box sits 1px above the wall: clear of overlap, inside the pad.
        let r = req((100.0, 100.0), 0);
        let top_box = ink(&top(), &r);
        let wall = Bounds::new(0.0, top_box.y1 - 1.0 - 5.0, 200.0, top_box.y1 - 1.0);
        let mut f = field(vec![Obstacle::mark(wall)], vec![vec![(100.0, 100.0)]]);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        assert_ne!((spot.dx, spot.dy), (top().dx, top().dy));
    }

    #[test]
    fn a_bubble_pushes_its_label_off_its_own_radius() {
        let mut r = req((100.0, 100.0), 0);
        r.own_radius = 12.0;
        let mut f = field(vec![], vec![vec![(100.0, 100.0)]]);
        let spot = place(r, &mut f).expect("a spot");
        let bubble = Bounds::new(88.0, 88.0, 112.0, 112.0);
        assert!(intersection(&ink(&spot, &req((100.0, 100.0), 0)), &bubble) < EPS);
    }

    #[test]
    fn a_label_with_no_acceptable_spot_is_dropped() {
        let wall = Bounds::new(0.0, 0.0, 200.0, 85.0);
        let floor = Bounds::new(0.0, 115.0, 200.0, 200.0);
        let left = Bounds::new(0.0, 85.0, 90.0, 115.0);
        let right = Bounds::new(110.0, 85.0, 200.0, 115.0);
        let mut f = field([wall, floor, left, right].map(Obstacle::mark).to_vec(), vec![vec![(100.0, 100.0)]]);
        assert!(place(req((100.0, 100.0), 0), &mut f).is_none());
    }

    #[test]
    fn an_authored_label_never_moves_or_drops() {
        let wall = Bounds::new(0.0, 0.0, 200.0, 200.0);
        let mut r = req((100.0, 100.0), 0);
        r.movable = false;
        let mut f = field(vec![Obstacle::mark(wall)], vec![vec![(100.0, 100.0)]]);
        let spot = place(r, &mut f).expect("kept");
        assert_eq!((spot.dx, spot.dy), (top().dx, top().dy));
    }

    #[test]
    fn labels_of_different_series_keep_a_gap_from_each_other() {
        let reqs = [req((100.0, 100.0), 0), req((104.0, 102.0), 1)];
        let mut f = field(vec![], vec![vec![(100.0, 100.0)], vec![(104.0, 102.0)]]);
        let spots = place_value_labels(&reqs, &[0, 1], 11.0, &mut f);
        let a = ink(&spots[0].expect("first"), &reqs[0]);
        let b = ink(&spots[1].expect("second"), &reqs[1]);
        assert!(gap(&a, &b) >= LABEL_PAD - EPS, "{a:?} {b:?}");
    }

    #[test]
    fn a_label_avoids_the_spot_nearer_another_series() {
        // Own line is flat at y=100; the other crosses just above the top spot.
        let own = vec![(0.0, 100.0), (200.0, 100.0)];
        let other = vec![(0.0, 90.0), (200.0, 90.0)];
        let mut f = field(vec![], vec![own, other]);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        assert!(spot.dy >= 0.0, "kept off the foreign series above: {}", spot.dy);
    }

    #[test]
    fn a_label_on_a_gentle_slope_rises_clear_of_the_line_and_stays_centered() {
        let line = DataMark::Line { points: vec![(0.0, 120.0), (200.0, 80.0)], width: 3.0 };
        let mut f = field(Obstacle::of_mark(&line), vec![vec![(0.0, 120.0), (200.0, 80.0)]]);
        let s = place(req((100.0, 100.0), 0), &mut f).expect("placed");
        assert_eq!(s.align, "center");
        assert_eq!(s.dx, 0.0);
        assert!(s.dy < -4.0, "rises off the line: {}", s.dy);
    }

    #[test]
    fn one_of_two_points_at_a_steep_crossing_keeps_a_label() {
        let (a, b) = ([(60.0, 20.0), (100.0, 96.0), (140.0, 170.0)], [(60.0, 180.0), (100.0, 104.0), (140.0, 30.0)]);
        let lines = [a, b].map(|points| DataMark::Line { points: points.to_vec(), width: 3.0 });
        let mut obstacles: Vec<Obstacle> = lines.iter().flat_map(Obstacle::of_mark).collect();
        obstacles.extend([(100.0, 96.0), (100.0, 104.0)].map(|(x, y)| Obstacle::mark(Bounds::new(x - 3.0, y - 3.0, x + 3.0, y + 3.0))));
        let mut f = Field { plot: (200.0, 200.0), obstacles, paths: vec![a.to_vec(), b.to_vec()] };
        let reqs = [req((100.0, 96.0), 0), req((100.0, 104.0), 1)];
        let spots = place_value_labels(&reqs, &[0, 1], 11.0, &mut f);
        assert!(spots.iter().any(Option::is_some), "a crossing keeps a label");
    }

    #[test]
    fn neighbor_labels_keep_a_word_gap() {
        let r = |x: f64| req((x, 100.0), 0);
        let mut f = field(Vec::new(), vec![vec![(100.0, 100.0)], vec![(140.0, 100.0)]]);
        let first = place(r(100.0), &mut f).expect("placed");
        f.obstacles.push(Obstacle::label(ink(&first, &r(100.0))));
        let mut second = r(100.0 + 20.0 + 3.0 + 1.0);
        second.owner = 1;
        let b = place(second, &mut f).map(|s| ink(&s, &r(124.0)));
        assert!(b.is_none_or(|b| gap(&b, &ink(&first, &r(100.0))) >= LABEL_GAP), "tight neighbors separate or thin");
    }

    #[test]
    fn a_gridline_never_costs_a_label() {
        let v: Vec<(f64, f64)> = (0..20).map(|i| (i as f64 * 10.0 + 5.0, 1.0)).collect();
        let h: Vec<(f64, f64)> = (0..20).map(|i| (i as f64 * 10.0 + 5.0, 1.0)).collect();
        let mut f = field(obstacles(&lines(Role::Grid, &v, &h), &[]), vec![vec![(100.0, 100.0)]]);
        assert!(place(req((100.0, 100.0), 0), &mut f).is_some());
    }

    #[test]
    fn a_spot_clear_of_gridlines_ranks_above_one_on_them() {
        // A wall makes every spot leave the preferred one; of the rest, only the one off the gridline stays clean.
        let r = req((100.0, 100.0), 0);
        let top_box = ink(&top(), &r);
        let wall = Bounds::new(0.0, top_box.y1 - 4.0, 200.0, top_box.y1 - 1.0);
        let bottom = CaptionSpot { edge: None, align: "center", baseline: "top", dx: 0.0, dy: 4.0 };
        let grid = (ink(&bottom, &r).y1 + ink(&bottom, &r).y2) / 2.0;
        let mut found = vec![Obstacle::mark(wall)];
        found.extend(obstacles(&lines(Role::Grid, &[], &[(grid, 1.0)]), &[]));
        let mut f = field(found, vec![vec![(100.0, 100.0)]]);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        assert!(gap(&ink(&spot, &r), &Bounds::new(0.0, grid, 200.0, grid)) > 0.0);
    }

    #[test]
    fn a_heavy_rule_through_the_datum_is_never_straddled() {
        let r = req((100.0, 100.0), 0);
        let mut f = field(obstacles(&lines(Role::Rule, &[(100.0, 2.0)], &[]), &[]), vec![vec![(100.0, 100.0)]]);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        let b = ink(&spot, &r);
        assert!(b.x1 >= 101.0 || b.x2 <= 99.0, "{b:?}");
    }

    #[test]
    fn a_series_keeps_its_labels_on_one_side() {
        // The middle label is pinched above but still acceptable there.
        let reqs = [req((50.0, 100.0), 0), req((100.0, 100.0), 0), req((150.0, 100.0), 0)];
        let top_box = ink(&top(), &reqs[1]);
        let wall = Bounds::new(90.0, top_box.y1 - 6.0, 110.0, top_box.y1 - 4.0);
        let path = reqs.iter().map(|r| r.anchor).collect();
        let mut f = field(vec![Obstacle::mark(wall)], vec![path]);
        let spots = place_value_labels(&reqs, &[0, 1, 2], 11.0, &mut f);
        assert!(spots.iter().all(|s| s.expect("a spot").dy < 0.0));
    }

    #[test]
    fn priority_takes_local_peaks_and_troughs_before_the_slopes() {
        let series = [0; 7];
        let values = [1.0, 2.0, 3.0, 9.0, 4.0, 5.0, 2.0];
        // Last 6, first 0, max 3, min 0; then the local peak 5 and trough 4; the slopes last.
        assert_eq!(priority_order(&series, &values), vec![6, 0, 3, 4, 5, 1, 2]);
    }

    #[test]
    fn priority_takes_each_series_extremes_before_the_rest() {
        let series = [0, 0, 0, 0, 0, 1, 1, 1];
        let values = [5.0, 9.0, 1.0, 4.0, 6.0, 2.0, 3.0, 1.0];
        // Every series' last point goes first, then each series' first, max and min.
        assert_eq!(priority_order(&series, &values), vec![4, 7, 0, 1, 2, 5, 6, 3]);
    }

    #[test]
    fn coincident_points_keep_one_label_on_the_top_painted_series() {
        let points = [(10.0, 50.0), (10.0, 52.0), (10.0, 54.0), (10.0, 80.0)];
        assert_eq!(top_of_each_cluster(&points, &[0, 2, 1, 3]), vec![false, true, false, true]);
    }

    #[test]
    fn a_channel_label_squeezes_beside_a_near_tick_but_never_onto_it() {
        let channel = CaptionSpot { edge: None, align: "left", baseline: "middle", dx: 30.0, dy: 0.0 };
        let dx_with_tick_at = |tick_y: f64| {
            let mut r = req((195.0, 100.0), 0);
            r.preferred = channel;
            r.channel = true;
            let tick = Obstacle::mark(Bounds::new(225.0, tick_y - 5.5, 245.0, tick_y + 5.5));
            place(r, &mut field(vec![tick], vec![vec![(195.0, 100.0)]])).expect("placed").dx
        };
        assert_eq!(dx_with_tick_at(90.0), 30.0);
        assert_ne!(dx_with_tick_at(94.0), 30.0);
    }

    #[test]
    fn a_blocked_channel_label_comes_back_into_the_plot_at_no_other_height() {
        let channel = CaptionSpot { edge: None, align: "right", baseline: "middle", dx: -6.0, dy: 0.0 };
        let mut r = req((0.0, 100.0), 0);
        r.preferred = channel;
        r.channel = true;
        let blocker = Obstacle::mark(Bounds::new(-30.0, 94.0, -6.0, 106.0));
        let spot = place(r, &mut field(vec![blocker], vec![vec![(0.0, 100.0)]])).expect("placed");
        let b = vega_fit::text_ink(spot.dx, 100.0 + spot.dy, 20.0, 11.0, spot.align, spot.baseline);
        assert!(b.x1 + b.x2 >= 0.0, "a moved label left the plot: {b:?}");
    }

    #[test]
    fn a_close_pair_parts_to_opposite_sides() {
        let points = [(100.0, 100.0), (103.0, 97.0), (150.0, 50.0), (10.0, 180.0), (11.0, 190.0)];
        let sides = pair_sides(&points);
        assert_eq!(&sides[..3], &[Some("left"), Some("right"), None]);
        assert_eq!(&sides[3..], &[Some("right"), Some("left")]);
    }

    #[test]
    fn a_pair_on_a_gridline_or_the_plot_edge_stays_on_a_diagonal_inside() {
        let spot = |side, x, rows: &[f64]| {
            let s = pair_spot(side, (x, 100.0), 20.0, 11.0, 200.0, rows);
            (s.align, s.baseline, s.dx, s.dy)
        };
        assert_eq!(spot("right", 100.0, &[]).0, "left");
        assert_eq!(spot("right", 100.0, &[]).3, 0.0);
        assert_eq!(spot("right", 100.0, &[102.0]), ("left", "bottom", 4.0, -4.0));
        assert_eq!(spot("left", 100.0, &[102.0]), ("right", "top", -4.0, 4.0));
        assert_eq!(spot("left", 2.0, &[]), ("left", "top", 4.0, 4.0));
        assert_eq!(spot("right", 195.0, &[]), ("right", "bottom", -4.0, -4.0));
    }

    #[test]
    fn a_bubble_on_the_plot_edge_never_pushes_its_label_past_the_reserved_top() {
        let mut r = req((100.0, 8.0), 0);
        r.own_radius = 15.0;
        let top = vega_fit::text_ink(100.0, 8.0 + top().dy, 20.0, 11.0, top().align, top().baseline).y1;
        let spot = place(r, &mut field(Vec::new(), vec![vec![(100.0, 8.0)]])).expect("placed");
        let y1 = vega_fit::text_ink(100.0 + spot.dx, 8.0 + spot.dy, 20.0, 11.0, spot.align, spot.baseline).y1;
        assert!(y1 >= top.min(0.0) - EPS, "{y1} past {top}");
    }

    #[test]
    fn the_first_pass_stands_when_the_shared_side_costs_a_label() {
        let points = [(28.0, 28.0), (76.0, 74.0), (82.0, 88.0), (54.0, 34.0), (78.0, 46.0), (34.0, 22.0)];
        let reqs: Vec<LabelReq> = points.iter().map(|p| req(*p, 0)).collect();
        let order: Vec<usize> = (0..points.len()).collect();
        let mut f = field(vec![Obstacle::mark(Bounds::new(116.0, 92.0, 140.0, 104.0))], vec![points.to_vec()]);
        assert!(place_value_labels(&reqs, &order, 11.0, &mut f).iter().all(Option::is_some));
    }

    #[test]
    fn a_label_offered_a_side_channel_takes_it_unless_it_collides() {
        let channel = CaptionSpot { edge: None, align: "left", baseline: "middle", dx: 30.0, dy: 0.0 };
        let mut r = req((195.0, 100.0), 0);
        r.preferred = channel;
        r.channel = true;
        let mut f = field(Vec::new(), vec![vec![(195.0, 100.0)]]);
        assert_eq!(place(LabelReq { ..r }, &mut f).expect("placed").dx, 30.0);
        let tick = Obstacle::mark(Bounds::new(225.0, 95.0, 245.0, 105.0));
        let mut blocked = field(vec![tick], vec![vec![(195.0, 100.0)]]);
        assert_ne!(place(r, &mut blocked).expect("placed").dx, 30.0);
    }

    #[test]
    fn a_spot_over_a_fill_ranks_below_one_clear_of_it() {
        // The fill covers the top spot and everything below the datum: the label goes beside it, above the fill.
        let fill = DataMark::Polygon(vec![(0.0, 100.0), (200.0, 100.0), (200.0, 200.0), (0.0, 200.0)]);
        let mut f = field(Obstacle::of_mark(&fill), vec![vec![(100.0, 100.0)]]);
        let r = req((100.0, 100.0), 0);
        let spot = place(req((100.0, 100.0), 0), &mut f).expect("a spot");
        assert!(ink(&spot, &r).y2 <= 100.0 + EPS, "{:?}", ink(&spot, &r));
    }

    #[test]
    fn a_label_wholly_on_a_dark_fill_takes_the_canvas_color() {
        use crate::annotation::text_over_fills;
        let dark = DataMark::Tinted(Box::new(DataMark::rect(0.0, 0.0, 100.0, 100.0)), "#10204a".into(), 1.0);
        let on = Bounds::new(10.0, 10.0, 30.0, 21.0);
        assert_eq!(text_over_fills(&[dark.clone()], "#ffffff", &on, "#313233").unwrap(), "#ffffff");
        let half_off = Bounds::new(90.0, 10.0, 110.0, 21.0);
        assert_eq!(text_over_fills(&[dark], "#ffffff", &half_off, "#313233").unwrap(), "#313233");
    }
}

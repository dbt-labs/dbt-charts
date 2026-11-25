//! Placement of rule labels that crowd each other or the plot's edge. A rule runs from the plot's far edge
//! to its own label and stops there; a label covers another rule's position only where that rule stops
//! short (`Cell::clear_of`). Labels stay as authored, flip to end at their rule, take up to `LEVELS`
//! lines, then give way to circled numbers and a key.

use super::place::{put, settle, text_box, text_gap, Geom, Mark, Placed, TextAnchor, TextBox, GAP};
use super::model::{Align, Edge, Position};
use super::{edge_hits, text, AnnotationCtx};
use crate::vega_fit::Bounds;
use diagnostic::Diagnostic;

/// Lines of labels a crowd may take, or columns beside a y rule.
const LEVELS: usize = 3;
/// A crowd of this many labels is numbered outright.
const NUMBERED_FROM: usize = 4;
/// A numbered circle's radius, px.
pub(crate) const BADGE_R: f64 = 7.0;
/// Runs `first`; if it numbered the rules, runs `again` with numbers forced, so the fit reserves the
/// key's room (only paint sees the data that can force them).
pub(crate) fn reserve_key<T>(
    first: impl FnOnce() -> Result<T, Diagnostic>,
    again: impl FnOnce() -> Result<T, Diagnostic>,
    findings: impl Fn(&T) -> &[Diagnostic],
) -> Result<T, Diagnostic> {
    let numbered = |d: &Diagnostic| d.code == diagnostic::codes::WARN_ANNOTATION_RULE_LABELS_NUMBERED.id;
    let (out, warnings) = diagnostic::collect(first);
    if warnings.iter().any(numbered) || out.as_ref().is_ok_and(|laid| findings(laid).iter().any(numbered)) {
        return again();
    }
    warnings.into_iter().for_each(diagnostic::warn);
    out
}

pub(crate) fn force_numbers(annotations: &[super::Annotation]) {
    annotations.iter().for_each(|a| drop(a.numbered.set(())));
}

/// Most placements a numbered circle's search tries before it gives up.
const BADGE_BUDGET: usize = 200_000;
/// How near a rule's coordinate must sit to a label's edge for the label to cover it.
const COVER_EPS: f64 = 0.5;
/// Rules this near draw as one line, so a label does not cover the other's.
const COINCIDENT: f64 = 1.0;
const KEY_GAP: f64 = 14.0;
const KEY_PAD: f64 = 4.0;

/// A label or circle on a line: `row` counts outward, `reach` is how far out its rule runs.
#[derive(Clone, Copy)]
struct Cell {
    at: f64,
    lo: f64,
    hi: f64,
    row: i32,
    reach: i32,
    gap: f64,
}

impl Cell {
    fn clear_of(&self, o: &Cell) -> bool {
        let gap = self.gap.max(o.gap);
        let apart = self.row != o.row || self.lo >= o.hi + gap || o.lo >= self.hi + gap;
        let covers = |a: &Cell, b: &Cell| (a.at - b.at).abs() > COINCIDENT && b.reach >= a.row && a.lo - COVER_EPS < b.at && b.at < a.hi + COVER_EPS;
        apart && !covers(self, o) && !covers(o, self)
    }
}

/// A rule label along its line: rows run along x for a vertical rule, along y for a y rule's column.
#[derive(Clone)]
struct Lane {
    item: usize,
    vertical: bool,
    inside: bool,
    near_hi: bool,
    at: f64,
    span: (f64, f64),
    size: (f64, f64),
    base: f64,
    /// Along-row starts, the authored side first; `hi` is the one that starts at the rule.
    starts: Vec<f64>,
    hi: f64,
    /// The authored start along the row, and its box's extent in the plot.
    auth: f64,
    plot_len: f64,
    gap: f64,
    auto_align: bool,
}

impl Lane {
    /// The label of `p` as a lane, when it sits where this module places it: at a vertical
    /// rule's end or inside the plot, or against a y rule on an `auto` edge or a side.
    fn of(item: usize, p: &Placed, ctx: &AnnotationCtx) -> Option<Lane> {
        let (spot, t, Some(Mark::Segment { .. })) = (p.spot?, p.text.as_ref()?, &p.mark) else { return None };
        let pos = spot.pos;
        let (vertical, at, span) = match spot.geom {
            Geom::VSeg { x, y0, y1 } => (true, x, (y0, y1)),
            Geom::HSeg { y, x0, x1 } => (false, y, (x0, x1)),
            _ => return None,
        };
        let seated = match (vertical, pos.overlay) {
            (true, true) => true,
            (true, false) => matches!(pos.edge, Edge::Top | Edge::Bottom),
            (false, true) => false,
            (false, false) => pos.auto_edge || matches!(pos.edge, Edge::Left | Edge::Right),
        };
        if !seated {
            return None;
        }
        let near_hi = if vertical { pos.edge == Edge::Bottom } else { pos.edge != Edge::Left };
        let (lane_nudge, across_nudge) = if vertical { spot.nudge } else { (spot.nudge.1, spot.nudge.0) };
        let size = if vertical { (t.w, t.h) } else { (t.h, t.w) };
        let (auth, plot_len) = if vertical { (t.x, ctx.plot.w) } else { (t.y, ctx.plot.h) };
        let hi = at + lane_nudge;
        let lo = at - size.0 + lane_nudge;
        let starts = if pos.auto_align {
            // The side nearer where it was authored first; a tie keeps the start at the rule.
            if (hi - auth).abs() <= (lo - auth).abs() { vec![hi, lo] } else { vec![lo, hi] }
        } else {
            vec![auth]
        };
        let base = base(vertical, pos.overlay, near_hi, span, at, size, across_nudge, ctx);
        Some(Lane { item, vertical, inside: pos.overlay, near_hi, at, span, size, base, starts, hi, auth, plot_len, gap: text_gap(p), auto_align: pos.auto_align })
    }

    fn badge(&self, ctx: &AnnotationCtx, at: f64) -> Lane {
        let d = 2.0 * BADGE_R;
        let base = base(self.vertical, self.inside, self.near_hi, self.span, at, (d, d), 0.0, ctx);
        let starts = vec![at - BADGE_R, at, at - d];
        Lane { at, size: (d, d), base, starts, hi: at, auth: at - BADGE_R, ..self.clone() }
    }

    fn rect(&self, start: f64, across: f64) -> Bounds {
        let (w, h) = (self.size.0, self.size.1);
        if self.vertical { Bounds::new(start, across, start + w, across + h) } else { Bounds::new(across, start, across + h, start + w) }
    }

    /// Whether a box inside the plot would sit on the chart's data.
    fn on_data(&self, start: f64, across: f64, data: &[Bounds]) -> bool {
        let b = self.rect(start, across);
        self.inside && data.iter().any(|m| m.x1 < b.x2 && m.x2 > b.x1 && m.y1 < b.y2 && m.y2 > b.y1)
    }

    fn group(&self) -> (bool, bool, bool) {
        (self.vertical, self.inside, self.near_hi)
    }

    fn step(&self) -> f64 {
        if self.near_hi != self.inside { 1.0 } else { -1.0 }
    }

    fn across(&self, level: usize, pitch: f64) -> f64 {
        self.base + self.step() * (level - 1) as f64 * pitch
    }

    fn cell(&self, level: usize, start: f64) -> Cell {
        let row = if self.inside { -(level as i32) } else { level as i32 };
        Cell { at: self.at, lo: start, hi: start + self.size.0, row, reach: row, gap: self.gap }
    }

    /// The label as authored, which no crowd moves.
    fn pinned(&self) -> Cell {
        let row = if self.inside { -1 } else { 1 };
        let reach = if self.inside { i32::MAX } else { i32::MIN };
        Cell { at: self.at, lo: self.auth, hi: self.auth + self.size.0, row, reach, gap: self.gap }
    }

    /// The along-row range a start may take: the plot's, or where the label was authored if
    /// that already reached past it.
    fn room(&self, strict: bool) -> (f64, f64) {
        if strict { (0.0, self.plot_len) } else { (self.auth.min(0.0), (self.auth + self.size.0).max(self.plot_len)) }
    }
}

/// Where line 1 starts across the row, in screen px: past the plot's edge and the chart's
/// own ink there, or inside the plot from its edge.
#[allow(clippy::too_many_arguments)]
fn base(vertical: bool, inside: bool, near_hi: bool, span: (f64, f64), at: f64, (along, across): (f64, f64), nudge: f64, ctx: &AnnotationCtx) -> f64 {
    if inside {
        return nudge + if near_hi { span.1 - GAP - across } else { span.0 + GAP };
    }
    let raw = nudge + if near_hi { span.1 + GAP } else { span.0 - GAP - across };
    if vertical { settle(at, raw, (along, across), ctx).1 } else { settle(raw, at, (across, along), ctx).0 }
}

fn bounds_of(p: &Placed) -> Option<Bounds> {
    let (x0, y0, x1, y1) = text_box(p)?;
    Some(Bounds::new(x0, y0, x1, y1))
}

/// Whether two labels, as authored, overlap or one covers the other's rule.
fn conflict(items: &[Placed], a: &Lane, b: &Lane) -> bool {
    let (Some(ba), Some(bb)) = (bounds_of(&items[a.item]), bounds_of(&items[b.item])) else { return false };
    let gap = a.gap.max(b.gap);
    let overlap = ba.x1 - gap < bb.x2 && bb.x1 - gap < ba.x2 && ba.y1 - gap < bb.y2 && bb.y1 - gap < ba.y2;
    let hits = |label: &Bounds, rule: &Placed| match rule.mark {
        Some(Mark::Segment { a, b }) => edge_hits(a, b, label),
        _ => false,
    };
    overlap || hits(&ba, &items[b.item]) || hits(&bb, &items[a.item])
}

/// Groups of two or more lanes whose labels conflict.
fn crowds(items: &[Placed], lanes: &[Lane]) -> Vec<Vec<usize>> {
    let mut crowd_of: Vec<usize> = (0..lanes.len()).collect();
    for i in 0..lanes.len() {
        for j in i + 1..lanes.len() {
            if lanes[i].group() == lanes[j].group() && conflict(items, &lanes[i], &lanes[j]) {
                let (from, to) = (crowd_of[j], crowd_of[i]);
                crowd_of.iter_mut().filter(|c| **c == from).for_each(|c| *c = to);
            }
        }
    }
    let mut out: Vec<Vec<usize>> = Vec::new();
    for (i, &c) in crowd_of.iter().enumerate() {
        match out.iter_mut().find(|m| crowd_of[m[0]] == c) {
            Some(m) => m.push(i),
            None => out.push(vec![i]),
        }
    }
    out.retain(|c| c.len() > 1);
    out
}

/// The cheapest way to seat every lane of a crowd: fewest lines, then the least total line,
/// then the fewest labels off their authored side. A pick is (line, index into `starts`).
fn seat(crowd: &[&Lane], pinned: &[Cell], pitch: f64, data: &[Bounds], strict: bool) -> Option<Vec<(usize, usize)>> {
    type Cost = (usize, usize, usize);
    fn go(crowd: &[&Lane], pinned: &[Cell], pitch: f64, data: &[Bounds], strict: bool, chosen: &mut Vec<(usize, usize, Cell)>, best: &mut Option<(Cost, Vec<(usize, usize)>)>) {
        let Some(lane) = crowd.get(chosen.len()) else {
            let cost = (
                chosen.iter().map(|c| c.0).max().unwrap_or(0),
                chosen.iter().map(|c| c.0).sum(),
                chosen.iter().filter(|c| c.1 > 0).count(),
            );
            if best.as_ref().is_none_or(|(b, _)| cost < *b) {
                *best = Some((cost, chosen.iter().map(|c| (c.0, c.1)).collect()));
            }
            return;
        };
        let (lo, hi) = lane.room(strict);
        for level in 1..=LEVELS {
            for (k, &start) in lane.starts.iter().enumerate() {
                if start < lo || start + lane.size.0 > hi || lane.on_data(start, lane.across(level, pitch), data) {
                    continue;
                }
                let cell = lane.cell(level, start);
                if pinned.iter().chain(chosen.iter().map(|c| &c.2)).all(|o| cell.clear_of(o)) {
                    chosen.push((level, k, cell));
                    go(crowd, pinned, pitch, data, strict, chosen, best);
                    chosen.pop();
                }
            }
        }
    }
    let mut best = None;
    go(crowd, pinned, pitch, data, strict, &mut Vec::new(), &mut best);
    best.map(|(_, picks)| picks)
}

/// The run from a rule's end to its label or circle, starting past the chart's own ink.
fn stem(lane: &Lane, across: f64, ctx: &AnnotationCtx, through: bool, pad: f64) -> [(f64, f64); 2] {
    let r = ctx.reach;
    let end = if lane.near_hi { lane.span.1 } else { lane.span.0 };
    let edge_len = if lane.vertical { ctx.plot.h } else { ctx.plot.w };
    let touches = if lane.near_hi { end >= edge_len - 1e-9 } else { end <= 1e-9 };
    let ink = match (lane.vertical, lane.near_hi) {
        (true, false) => -r.top,
        (true, true) => r.bottom,
        (false, false) => -r.left,
        (false, true) => r.right,
    };
    let from = if touches { end + ink } else { end };
    let (near, far) = if lane.near_hi { (across, across + lane.size.1) } else { (across + lane.size.1, across) };
    let to = if through { far } else if lane.near_hi { near - pad } else { near + pad };
    if lane.vertical { [(lane.at, from), (lane.at, to)] } else { [(from, lane.at), (to, lane.at)] }
}

/// Cuts an overlay rule short where its label begins.
fn shorten(p: &mut Placed, lane: &Lane, across: f64) {
    let Some(Mark::Segment { a, b }) = &mut p.mark else { return };
    if lane.near_hi {
        b.1 = b.1.min(across - GAP);
    } else {
        a.1 = a.1.max(across + lane.size.1 + GAP);
    }
}

fn seated_label(p: &mut Placed, lane: &Lane, level: usize, k: usize, pitch: f64, ctx: &AnnotationCtx) {
    let start = lane.starts[k];
    let across = lane.across(level, pitch);
    let t = p.text.as_mut().expect("a lane has a text");
    (t.x, t.y) = if lane.vertical { (start, across) } else { (across, start) };
    if !lane.vertical {
        t.anchor = TextAnchor::Start;
    } else if lane.auto_align {
        t.anchor = if start == lane.hi { TextAnchor::Start } else { TextAnchor::End };
    }
    p.settled = true;
    if lane.inside {
        shorten(p, lane, across);
    } else if level > 1 || !lane.vertical {
        p.stem = Some(stem(lane, across, ctx, !lane.vertical, GAP));
    }
}

/// Seats the rule labels that crowd one another, then moves the rest off the plot's edge;
/// numbers every rule when a crowd is too big or finds no seats.
pub(crate) fn arrange_rules(items: &mut [Placed], ctx: &AnnotationCtx, data: &[Bounds]) -> Result<(), Diagnostic> {
    items.iter_mut().for_each(|p| fit_to_plot(p, ctx));
    let lanes: Vec<Lane> = items.iter().enumerate().filter_map(|(i, p)| Lane::of(i, p, ctx)).collect();
    let crowds = crowds(items, &lanes);
    let forced = ctx.annotations.iter().any(|a| a.numbered.get().is_some());
    let Some(seats) = seats(&lanes, &crowds, data).filter(|_| !forced) else {
        return number(items, &lanes, ctx, data);
    };
    for (crowd, pitch, picks) in seats {
        for (&i, (level, k)) in crowds[crowd].iter().zip(picks) {
            seated_label(&mut items[lanes[i].item], &lanes[i], level, k, pitch, ctx);
        }
    }
    Ok(())
}

/// Per crowd: its index, line pitch and each member's (line, side).
type Seats = Vec<(usize, f64, Vec<(usize, usize)>)>;

/// Every crowd's seats; `None` when a crowd is too big or finds none.
fn seats(lanes: &[Lane], crowds: &[Vec<usize>], data: &[Bounds]) -> Option<Seats> {
    if crowds.iter().any(|c| c.len() >= NUMBERED_FROM) {
        return None;
    }
    let mut out = Vec::new();
    for (n, crowd) in crowds.iter().enumerate() {
        let members: Vec<&Lane> = crowd.iter().map(|&i| &lanes[i]).collect();
        let pinned: Vec<Cell> = lanes
            .iter()
            .enumerate()
            .filter(|(i, l)| !crowd.contains(i) && l.group() == members[0].group())
            .map(|(_, l)| l.pinned())
            .collect();
        let pitch = members.iter().map(|l| l.size.1 + l.gap).fold(0.0, f64::max);
        let picks = seat(&members, &pinned, pitch, data, true).or_else(|| seat(&members, &pinned, pitch, data, false))?;
        out.push((n, pitch, picks));
    }
    Some(out)
}

/// A rule label with an `auto` align or edge that would cross the plot's side moves to its
/// other side when that keeps it inside, so a lone rule near an edge reserves no margin.
fn fit_to_plot(p: &mut Placed, ctx: &AnnotationCtx) {
    let (Some(spot), Some(t), Some(Mark::Segment { .. })) = (p.spot, p.text.as_ref(), &p.mark) else { return };
    let pos = spot.pos;
    if pos.overlay || !(pos.auto_align || pos.auto_edge) {
        return;
    }
    // The text runs along x on a top or bottom edge. Against a line's side it also stays within the plot across.
    let along_x = matches!(pos.edge, Edge::Top | Edge::Bottom);
    let beside = matches!((spot.geom, pos.edge), (Geom::HSeg { .. }, Edge::Top | Edge::Bottom) | (Geom::VSeg { .. }, Edge::Left | Edge::Right));
    let (w, h) = (t.w, t.h);
    let within = |x: f64, y: f64| {
        let (along, across) = if along_x { ((x, w, ctx.plot.w), (y, h, ctx.plot.h)) } else { ((y, h, ctx.plot.h), (x, w, ctx.plot.w)) };
        let fits = |(at, size, max): (f64, f64, f64)| at >= -1e-9 && at + size <= max + 1e-9;
        fits(along) && (!beside || fits(across))
    };
    if within(t.x, t.y) {
        return;
    }
    let other_align = match pos.align {
        Align::Start => Align::End,
        Align::End => Align::Start,
        Align::Center => Align::Center,
    };
    let other_edge = match pos.edge {
        Edge::Top => Edge::Bottom,
        Edge::Bottom => Edge::Top,
        Edge::Left => Edge::Right,
        Edge::Right => Edge::Left,
    };
    let mut tries = Vec::new();
    if pos.auto_align {
        tries.push((pos.edge, other_align));
    }
    if pos.auto_edge && beside {
        tries.push((other_edge, pos.align));
    }
    if pos.auto_align && pos.auto_edge && beside {
        tries.push((other_edge, other_align));
    }
    for (edge, align) in tries {
        let (x, y, anchor) = put(&spot, Position { edge, align, ..pos }, 0.0, ctx);
        if within(x, y) {
            let t = p.text.as_mut().expect("checked above");
            (t.x, t.y, t.anchor) = (x, y, anchor);
            return;
        }
    }
}

/// Depth first in order, the lowest line and the centered circle first: each circle's (line, side),
/// or `None` when no arrangement is found within the search budget.
fn seat_badges(badges: &[Lane], pitch: f64, data: &[Bounds]) -> Option<Vec<(usize, usize)>> {
    let mut chosen: Vec<(usize, usize, Cell)> = Vec::new();
    let mut options: Vec<Vec<(usize, usize)>> = vec![Vec::new(); badges.len()];
    let mut tried = 0;
    while chosen.len() < badges.len() {
        let n = chosen.len();
        let lane = &badges[n];
        if options[n].is_empty() {
            options[n] = (1..=LEVELS).rev().flat_map(|level| (0..lane.starts.len()).rev().map(move |k| (level, k))).collect();
        }
        let mut seated = false;
        while let Some((level, k)) = options[n].pop() {
            tried += 1;
            if tried > BADGE_BUDGET {
                return None;
            }
            let cell = lane.cell(level, lane.starts[k]);
            let clear = chosen.iter().enumerate().filter(|&(i, _)| badges[i].group() == lane.group()).all(|(_, c)| cell.clear_of(&c.2));
            if clear && !lane.on_data(lane.starts[k], lane.across(level, pitch), data) {
                chosen.push((level, k, cell));
                seated = true;
                break;
            }
        }
        if !seated {
            chosen.pop()?;
        }
    }
    Some(chosen.into_iter().map(|(level, k, _)| (level, k)).collect())
}

/// Merges the neighboring circles of one kind that lie nearest each other, with any chained within the same
/// reach (rules on one spot, a pixel or two apart, merge whole); `false` when none can merge.
fn merge_nearest(groups: &mut Vec<Vec<usize>>, lanes: &[Lane]) -> bool {
    let kind = |g: &[usize]| lanes[g[0]].group();
    let gap = |a: &[usize], b: &[usize]| (lanes[b[0]].at - lanes[a[a.len() - 1]].at).abs();
    let adjacent = |groups: &[Vec<usize>], i: usize| (i + 1..groups.len()).find(|&j| kind(&groups[j]) == kind(&groups[i]));
    let nearest = (0..groups.len()).filter_map(|i| Some(gap(&groups[i], &groups[adjacent(groups, i)?]))).min_by(f64::total_cmp);
    let Some(nearest) = nearest else { return false };
    let reach = nearest.max(COINCIDENT) + COVER_EPS;
    let mut i = 0;
    while i < groups.len() {
        match adjacent(groups, i).filter(|&j| gap(&groups[i], &groups[j]) <= reach) {
            Some(j) => {
                let joined = groups.remove(j);
                groups[i].extend(joined);
            }
            None => i += 1,
        }
    }
    true
}

/// Every rule in the chart as a circled number, placed by the same law, with a key under the plot. Rules
/// whose circles cannot be seated apart share one, and the key joins their labels with `; `.
fn number(items: &mut [Placed], lanes: &[Lane], ctx: &AnnotationCtx, data: &[Bounds]) -> Result<(), Diagnostic> {
    let mut order: Vec<usize> = (0..lanes.len()).collect();
    order.sort_by(|&a, &b| (!lanes[a].vertical, lanes[a].at).partial_cmp(&(!lanes[b].vertical, lanes[b].at)).expect("rule positions are finite"));
    let pitch = 2.0 * BADGE_R + GAP;
    let mut groups: Vec<Vec<usize>> = order.iter().map(|&i| vec![i]).collect();
    let (badges, picks) = loop {
        let badges: Vec<Lane> = groups
            .iter()
            .map(|g| {
                let at = g.iter().map(|&i| lanes[i].at).sum::<f64>() / g.len() as f64;
                lanes[g[0]].badge(ctx, at)
            })
            .collect();
        // A circle over the data still names its rule, so only a search that fails clear of the data may sit on it.
        if let Some(picks) = seat_badges(&badges, pitch, data).or_else(|| seat_badges(&badges, pitch, &[])) {
            break (badges, picks);
        }
        // Neighboring circles that cannot all seat merge; a lone circle of each kind always seats, so this ends.
        assert!(merge_nearest(&mut groups, lanes), "a lone circle always seats");
    };
    for (n, ((level, k), group)) in picks.iter().zip(&groups).enumerate() {
        let circle = &badges[n];
        let across = circle.across(*level, pitch);
        let center = if circle.vertical { (circle.starts[*k] + BADGE_R, across + BADGE_R) } else { (across + BADGE_R, circle.starts[*k] + BADGE_R) };
        for (m, &i) in group.iter().enumerate() {
            let own = lanes[i].badge(ctx, lanes[i].at);
            let p = &mut items[lanes[i].item];
            p.number = Some(n + 1);
            p.settled = true;
            if m == 0 {
                p.badges = vec![center];
            }
            if own.inside {
                shorten(p, &own, across);
            } else {
                p.stem = Some(stem(&own, across, ctx, !own.vertical && *k > 0, 0.0));
            }
        }
    }
    // The key wraps to the plot's width, under the plot where the fit reserved it.
    let (mut x, mut y, mut row_h) = (0.0, ctx.plot.h + ctx.reach.bottom + 2.0 * GAP, 0.0_f64);
    let mut entries = Vec::new();
    for group in &groups {
        let label = group.iter().map(|&i| items[lanes[i].item].text.take().expect("a lane has a text").lines.join(" ")).collect::<Vec<_>>().join("; ");
        let p = &mut items[lanes[group[0]].item];
        let (pad, anchor_style) = (p.style.text.frame.visible.then_some(p.style.text.frame.padding).unwrap_or(0.0), p.style.text.clone());
        let block = text::measure_block(ctx.fonts, &label, &anchor_style, Some((ctx.plot.w - 2.0 * BADGE_R - KEY_PAD - 2.0 * pad).max(1.0)))?;
        let (w, h) = (block.w + 2.0 * pad, block.h + 2.0 * pad);
        let width = 2.0 * BADGE_R + KEY_PAD + w;
        if x > 0.0 && x + width > ctx.plot.w {
            (x, y, row_h) = (0.0, y + row_h + KEY_PAD, 0.0);
        }
        let slot = h.max(2.0 * BADGE_R);
        row_h = row_h.max(slot);
        entries.push((lanes[group[0]].item, x, y, slot, TextBox { x: x + 2.0 * BADGE_R + KEY_PAD, y: 0.0, w, h, pad, ascent: block.ascent, anchor: TextAnchor::Start, lines: block.lines, line_h: block.line_h }));
        x += width + KEY_GAP;
    }
    for (item, x, y, slot, mut text) in entries {
        text.y = y + (slot - text.h) / 2.0;
        let p = &mut items[item];
        p.badges.push((x + BADGE_R, y + slot / 2.0));
        p.text = Some(text);
    }
    Ok(())
}

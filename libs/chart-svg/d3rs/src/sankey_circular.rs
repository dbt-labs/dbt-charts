//! Port of sankey-plus as vendored in Semiotic (`src/vendor/sankey-plus`,
//! nteract/semiotic@7534c9e; MIT, © Tom Shanley, with Semiotic's changes
//! under Apache-2.0): a sankey layout that accepts cycles. A weighted Eades
//! feedback ordering picks the backward ("circular") links, which are routed
//! as arcs over the top or under the bottom of the diagram at their full
//! flow width, nested with a gap. `fit` ports the scale-to-fit step of
//! Semiotic's `sankeyLayoutPlugin`.
//!
//! Faithful to the JS, including its quirks (JS truthiness of a 0.0 mean,
//! comparators that are not total orders — hence `js_sort`). Only the
//! defaults Semiotic's `SankeyDiagram` uses are ported: no `nodeSort`, no
//! `nodePaddingRatio`, horizontal orientation.
//!
//! Node identity for the feedback order's tie-break is the caller's `ids`,
//! compared by UTF-16 code unit as JS compares strings.

use crate::js_sort::js_sort;
use crate::sankey::Align;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoopSide {
    Top,
    Bottom,
}

#[derive(Clone, Debug, Default)]
pub struct Node {
    pub source_links: Vec<usize>,
    pub target_links: Vec<usize>,
    pub value: f64,
    pub depth: i64,
    pub height: i64,
    pub column: i64,
    pub part_of_cycle: bool,
    pub circular_link_type: Option<LoopSide>,
    pub x0: f64,
    pub x1: f64,
    pub y0: f64,
    pub y1: f64,
}

/// The centerline route of a circular link (sankey-plus `circularPathData`).
#[derive(Clone, Debug, Default)]
pub struct CircularPath {
    pub source_x: f64,
    pub source_y: f64,
    pub target_x: f64,
    pub target_y: f64,
    pub right_small_arc_radius: f64,
    pub right_large_arc_radius: f64,
    pub left_small_arc_radius: f64,
    pub left_large_arc_radius: f64,
    pub vertical_buffer: f64,
    pub vertical_full_extent: f64,
    pub vertical_right_inner_extent: f64,
    pub vertical_left_inner_extent: f64,
    pub right_inner_extent: f64,
    pub left_inner_extent: f64,
    pub right_full_extent: f64,
    pub left_full_extent: f64,
}

#[derive(Clone, Debug)]
pub struct Link {
    pub source: usize,
    pub target: usize,
    pub value: f64,
    pub width: f64,
    pub y0: f64,
    pub y1: f64,
    pub circular: bool,
    pub circular_link_type: Option<LoopSide>,
    pub circular_path: Option<CircularPath>,
}

#[derive(Debug)]
pub struct Graph {
    pub nodes: Vec<Node>,
    pub links: Vec<Link>,
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    /// Pixels per unit of flow (`graph.ky`).
    pub ky: f64,
}

pub struct SankeyCircular {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub node_width: f64,
    pub node_padding: f64,
    pub align: Align,
    pub iterations: usize,
    pub circular_gap: f64,
    pub base_radius: f64,
    pub vertical_margin: f64,
}

impl Default for SankeyCircular {
    fn default() -> Self {
        SankeyCircular {
            x0: 0.0,
            y0: 0.0,
            x1: 1.0,
            y1: 1.0,
            node_width: 24.0,
            node_padding: 8.0,
            align: Align::Justify,
            iterations: 32,
            circular_gap: 2.0,
            base_radius: 10.0,
            vertical_margin: 8.0,
        }
    }
}

/// sankey-plus's right-edge buffer between a node and a loop's first arc.
const BUFFER: f64 = 5.0;

impl SankeyCircular {
    /// Lay out `ids.len()` nodes joined by `(source, target, value)` links.
    /// Node coordinates start at 0, as Semiotic's plugin resets them.
    pub fn layout(&self, ids: &[String], links: &[(usize, usize, f64)]) -> Result<Graph, String> {
        let n = ids.len();
        let mut g = Graph {
            nodes: vec![Node::default(); n],
            links: Vec::with_capacity(links.len()),
            x0: self.x0,
            y0: self.y0,
            x1: self.x1,
            y1: self.y1,
            ky: 0.0,
        };
        for (i, &(s, t, v)) in links.iter().enumerate() {
            if s >= n || t >= n {
                return Err(format!("missing: {}", if s >= n { s } else { t }));
            }
            g.links.push(Link {
                source: s,
                target: t,
                value: v,
                width: 0.0,
                y0: 0.0,
                y1: 0.0,
                circular: false,
                circular_link_type: None,
                circular_path: None,
            });
            g.nodes[s].source_links.push(i);
            g.nodes[t].target_links.push(i);
        }
        identify_circles(&mut g, ids);
        select_circular_link_types(&mut g);
        compute_node_values(&mut g);
        self.compute_node_depths(&mut g);
        adjust_sankey_size(&mut g, self.node_padding, self.node_width);
        for _ in 0..2 {
            compute_node_breadths(&mut g)?;
            self.resolve_collisions_and_relax(&mut g)?;
            compute_link_breadths(&mut g)?;
        }
        sort_source_links(&mut g)?;
        sort_target_links(&mut g)?;
        fill_height(&mut g);
        self.add_circular_path_data(&mut g)?;
        Ok(g)
    }

    fn compute_node_depths(&self, g: &mut Graph) {
        let n = g.nodes.len();
        let mut indegree = vec![0i32; n];
        let mut queue = Vec::with_capacity(n);
        let mut levels = 1;
        for (v, degree) in indegree.iter_mut().enumerate() {
            g.nodes[v].depth = 0;
            g.nodes[v].height = 0;
            *degree =
                g.nodes[v].target_links.iter().filter(|&&l| !g.links[l].circular).count() as i32;
            if *degree == 0 {
                queue.push(v);
            }
        }
        let mut i = 0;
        while i < queue.len() {
            let v = queue[i];
            levels = levels.max(g.nodes[v].depth + 1);
            for k in 0..g.nodes[v].source_links.len() {
                let l = g.nodes[v].source_links[k];
                if g.links[l].circular {
                    continue;
                }
                let t = g.links[l].target;
                g.nodes[t].depth = g.nodes[t].depth.max(g.nodes[v].depth + 1);
                indegree[t] -= 1;
                if indegree[t] == 0 {
                    queue.push(t);
                }
            }
            i += 1;
        }
        for &v in queue.iter().rev() {
            for k in 0..g.nodes[v].source_links.len() {
                let l = g.nodes[v].source_links[k];
                if !g.links[l].circular {
                    let h = g.nodes[g.links[l].target].height + 1;
                    g.nodes[v].height = g.nodes[v].height.max(h);
                }
            }
        }
        for v in 0..n {
            let d = &g.nodes[v];
            g.nodes[v].column = match self.align {
                Align::Left => d.depth,
                Align::Right => levels - 1 - d.height,
                Align::Justify => {
                    if d.source_links.is_empty() {
                        levels - 1
                    } else {
                        d.depth
                    }
                }
                Align::Center => {
                    if !d.target_links.is_empty() {
                        d.depth
                    } else if !d.source_links.is_empty() {
                        d.source_links.iter().map(|&l| g.nodes[g.links[l].target].depth).min().unwrap() - 1
                    } else {
                        0
                    }
                }
            };
        }
    }

    fn resolve_collisions_and_relax(&self, g: &mut Graph) -> Result<(), String> {
        let mut columns = columns(g);
        let py = self.node_padding;
        resolve_collisions(g, &mut columns, py)?;
        let mut alpha = 1.0;
        for _ in 0..self.iterations {
            alpha *= 0.99;
            relax_left_and_right(g, &columns, alpha);
            resolve_collisions(g, &mut columns, py)?;
        }
        Ok(())
    }

    fn add_circular_path_data(&self, g: &mut Graph) -> Result<(), String> {
        let (gap, base) = (self.circular_gap, self.base_radius);
        let min_y = g.links.iter().map(|l| g.nodes[l.source].y0).fold(f64::INFINITY, f64::min);
        let circular: Vec<usize> = (0..g.links.len()).filter(|&l| g.links[l].circular).collect();
        for &l in &circular {
            g.links[l].circular_path = Some(CircularPath::default());
        }
        let mut source_counts = vec![0usize; g.nodes.len()];
        let mut target_counts = vec![0usize; g.nodes.len()];
        for &l in &circular {
            source_counts[g.links[l].source] += 1;
            target_counts[g.links[l].target] += 1;
        }
        let isolated: Vec<bool> = (0..g.links.len())
            .map(|l| {
                let k = &g.links[l];
                k.circular && k.source == k.target && source_counts[k.source] == 1 && target_counts[k.target] == 1
            })
            .collect();
        for side in [LoopSide::Top, LoopSide::Bottom] {
            let links: Vec<usize> =
                circular.iter().copied().filter(|&l| g.links[l].circular_link_type == Some(side)).collect();
            calc_vertical_buffer(g, links.clone(), &isolated, gap)?;
            for source_end in [true, false] {
                let mut groups: Vec<(i64, Vec<usize>)> = Vec::new();
                for &l in &links {
                    let node = if source_end { g.links[l].source } else { g.links[l].target };
                    let col = g.nodes[node].column;
                    match groups.iter_mut().find(|(c, _)| *c == col) {
                        Some((_, v)) => v.push(l),
                        None => groups.push((col, vec![l])),
                    }
                }
                for (_, mut group) in groups {
                    let bottom = side == LoopSide::Bottom;
                    js_sort(&mut group, |a, b| {
                        let (la, lb) = (&g.links[a], &g.links[b]);
                        match (source_end, bottom) {
                            (true, true) => lb.y0 - la.y0,
                            (true, false) => la.y0 - lb.y0,
                            (false, true) => lb.y1 - la.y1,
                            (false, false) => la.y1 - lb.y1,
                        }
                    })?;
                    let mut offset = 0.0;
                    for (i, &l) in group.iter().enumerate() {
                        let w = g.links[l].width;
                        let small = base + w / 2.0 + offset;
                        let large = base + w / 2.0 + i as f64 * gap + offset;
                        let p = g.links[l].circular_path.as_mut().unwrap();
                        if source_end {
                            p.right_small_arc_radius = small;
                            p.right_large_arc_radius = large;
                        } else {
                            p.left_small_arc_radius = small;
                            p.left_large_arc_radius = large;
                        }
                        offset += w;
                    }
                }
            }
        }
        let vm = self.vertical_margin;
        for l in circular {
            let (s, t) = (g.links[l].source, g.links[l].target);
            let (sx0, sx1, sy0, sy1) = (g.nodes[s].x0, g.nodes[s].x1, g.nodes[s].y0, g.nodes[s].y1);
            let (tx0, ty1) = (g.nodes[t].x0, g.nodes[t].y1);
            let gy1 = g.y1;
            let k = &mut g.links[l];
            let (w, y0, y1, side) = (k.width, k.y0, k.y1, k.circular_link_type);
            let p = k.circular_path.as_mut().unwrap();
            p.source_x = sx0 + (sx1 - sx0);
            p.target_x = tx0;
            p.source_y = y0;
            p.target_y = y1;
            if isolated[l] {
                let r = base + w / 2.0;
                p.right_small_arc_radius = r;
                p.right_large_arc_radius = r;
                p.left_small_arc_radius = r;
                p.left_large_arc_radius = r;
            }
            let bottom = side == Some(LoopSide::Bottom);
            p.vertical_full_extent = match (isolated[l], bottom) {
                (true, true) => sy1 + vm + p.vertical_buffer,
                (true, false) => sy0 - vm - p.vertical_buffer,
                (false, true) => gy1.max(sy1).max(ty1) + vm + p.vertical_buffer,
                (false, false) => min_y - vm - p.vertical_buffer,
            };
            let sign = if bottom { -1.0 } else { 1.0 };
            p.vertical_right_inner_extent = p.vertical_full_extent + sign * p.right_large_arc_radius;
            p.vertical_left_inner_extent = p.vertical_full_extent + sign * p.left_large_arc_radius;
            p.right_inner_extent = p.source_x + BUFFER;
            p.left_inner_extent = p.target_x - BUFFER;
            p.right_full_extent = p.source_x + p.right_large_arc_radius + BUFFER;
            p.left_full_extent = p.target_x - p.left_large_arc_radius - BUFFER;
        }
        Ok(())
    }
}

impl Graph {
    /// Semiotic's scale-to-fit: when nodes or loop bands overrun
    /// `[0, width] × [0, height]`, scale everything by one factor and center
    /// it, so every width stays proportional. Semiotic's frame is anchored at
    /// the origin; this takes the target rect explicitly.
    pub fn fit(&mut self, x0: f64, y0: f64, x1: f64, y1: f64) {
        let (mut min_x, mut max_x, mut min_y, mut max_y) =
            (f64::INFINITY, f64::NEG_INFINITY, f64::INFINITY, f64::NEG_INFINITY);
        for n in &self.nodes {
            min_x = min_x.min(n.x0);
            max_x = max_x.max(n.x1);
            min_y = min_y.min(n.y0);
            max_y = max_y.max(n.y1);
        }
        for l in &self.links {
            if let Some(p) = &l.circular_path {
                let cw = l.width / 2.0;
                min_x = min_x.min(p.left_full_extent - cw);
                max_x = max_x.max(p.right_full_extent + cw);
                min_y = min_y.min(p.vertical_full_extent - cw);
                max_y = max_y.max(p.vertical_full_extent + cw);
            }
        }
        let (bw, bh) = (max_x - min_x, max_y - min_y);
        let overruns = min_x < x0 || min_y < y0 || max_x > x1 || max_y > y1;
        if !(bw > 0.0 && bh > 0.0 && overruns) {
            return;
        }
        let scale = ((x1 - x0) / bw).min((y1 - y0) / bh);
        let ox = x0 - min_x * scale + ((x1 - x0) - bw * scale) / 2.0;
        let oy = y0 - min_y * scale + ((y1 - y0) - bh * scale) / 2.0;
        let (sx, sy) = (|v: f64| v * scale + ox, |v: f64| v * scale + oy);
        for n in &mut self.nodes {
            (n.x0, n.x1, n.y0, n.y1) = (sx(n.x0), sx(n.x1), sy(n.y0), sy(n.y1));
        }
        for l in &mut self.links {
            l.y0 = sy(l.y0);
            l.y1 = sy(l.y1);
            l.width *= scale;
            if let Some(p) = &mut l.circular_path {
                p.source_x = sx(p.source_x);
                p.target_x = sx(p.target_x);
                p.source_y = sy(p.source_y);
                p.target_y = sy(p.target_y);
                p.right_full_extent = sx(p.right_full_extent);
                p.left_full_extent = sx(p.left_full_extent);
                p.vertical_full_extent = sy(p.vertical_full_extent);
                p.right_inner_extent = sx(p.right_inner_extent);
                p.left_inner_extent = sx(p.left_inner_extent);
                p.vertical_right_inner_extent = sy(p.vertical_right_inner_extent);
                p.vertical_left_inner_extent = sy(p.vertical_left_inner_extent);
                p.right_small_arc_radius *= scale;
                p.right_large_arc_radius *= scale;
                p.left_small_arc_radius *= scale;
                p.left_large_arc_radius *= scale;
            }
        }
    }

    /// The link as a filled band of half-width `hw`: Semiotic's
    /// `circularAreaLink` for a loop — the centerline route offset by `hw`
    /// on both sides, keeping the engine's nested corner radii — and
    /// `sankey::ribbon_area` otherwise.
    pub fn link_area(&self, l: usize, hw: f64, num: impl Fn(f64) -> String) -> String {
        let k = &self.links[l];
        let Some(p) = &k.circular_path else {
            let (sx, tx) = (self.nodes[k.source].x1, self.nodes[k.target].x0);
            return crate::sankey::ribbon_area(sx, k.y0, tx, k.y1, hw, num);
        };
        let s = if k.circular_link_type == Some(LoopSide::Bottom) { 1.0 } else { -1.0 };
        let sweep = if s == 1.0 { 1 } else { 0 };
        let pt = |x: f64, y: f64| format!("{},{}", num(x), num(y));
        let turn = |rx: f64, ry: f64, cw: i32, x: f64, y: f64| {
            format!("A{},{} 0 0 {cw} {}", num(rx), num(ry), pt(x, y))
        };
        let (sx, sy, tx, ty) = (p.source_x, p.source_y, p.target_x, p.target_y);
        let (ri, li, rf, lf) = (p.right_inner_extent, p.left_inner_extent, p.right_full_extent, p.left_full_extent);
        let (vf, vr, vl) = (p.vertical_full_extent, p.vertical_right_inner_extent, p.vertical_left_inner_extent);
        let (rr, rs, lr, ls) = (
            p.right_large_arc_radius,
            p.right_small_arc_radius,
            p.left_large_arc_radius,
            p.left_small_arc_radius,
        );
        [
            format!("M{}L{}", pt(sx, sy - s * hw), pt(ri, sy - s * hw)),
            turn(rr + hw, rs + hw, sweep, rf + hw, sy + s * rs),
            format!("L{}", pt(rf + hw, vr)),
            turn(rr + hw, rr + hw, sweep, ri, vf + s * hw),
            format!("L{}", pt(li, vf + s * hw)),
            turn(lr + hw, lr + hw, sweep, lf - hw, vl),
            format!("L{}", pt(lf - hw, ty + s * ls)),
            turn(lr + hw, ls + hw, sweep, li, ty - s * hw),
            format!("L{}L{}L{}", pt(tx, ty - s * hw), pt(tx, ty + s * hw), pt(li, ty + s * hw)),
            turn(lr - hw, ls - hw, 1 - sweep, lf + hw, ty + s * ls),
            format!("L{}", pt(lf + hw, vl)),
            turn(lr - hw, lr - hw, 1 - sweep, li, vf - s * hw),
            format!("L{}", pt(ri, vf - s * hw)),
            turn(rr - hw, rr - hw, 1 - sweep, rf - hw, vr),
            format!("L{}", pt(rf - hw, sy + s * rs)),
            turn(rr - hw, rs - hw, 1 - sweep, ri, sy + s * hw),
            format!("L{}Z", pt(sx, sy + s * hw)),
        ]
        .concat()
    }
}

// ── feedbackOrder.ts ─────────────────────────────────────────────────────────

/// Weighted Eades ordering: peel sinks right and sources left, else take the
/// node with the greatest out-minus-in flow. Returns each node's rank; a
/// link is circular when its source ranks at or after its target. The heap
/// only accelerates "pick the first remaining node by `before`", a total
/// order, so a linear scan picks the same node.
fn feedback_order(g: &Graph, ids: &[String]) -> Vec<usize> {
    let n = g.nodes.len();
    let keys: Vec<Vec<u16>> = ids.iter().map(|id| format!("string:{id}").encode_utf16().collect()).collect();
    let mut in_deg = vec![0i64; n];
    let mut out_deg = vec![0i64; n];
    let mut balance = vec![0f64; n];
    let mut incoming: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut outgoing: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut weights = vec![0f64; g.links.len()];
    let max_weight = g
        .links
        .iter()
        .filter(|l| l.source != l.target && l.value.is_finite())
        .fold(0f64, |m, l| m.max(l.value));
    let mut edge_order: Vec<usize> = (0..g.links.len()).collect();
    edge_order.sort_by(|&a, &b| {
        let (la, lb) = (&g.links[a], &g.links[b]);
        keys[la.source]
            .cmp(&keys[lb.source])
            .then(keys[la.target].cmp(&keys[lb.target]))
            .then(la.value.partial_cmp(&lb.value).unwrap_or(std::cmp::Ordering::Equal))
    });
    for i in edge_order {
        let l = &g.links[i];
        if l.source == l.target {
            continue;
        }
        let w = if l.value.is_finite() && max_weight > 0.0 { l.value.max(0.0) / max_weight } else { 0.0 };
        weights[i] = w;
        outgoing[l.source].push(i);
        incoming[l.target].push(i);
        out_deg[l.source] += 1;
        in_deg[l.target] += 1;
        balance[l.source] += w;
        balance[l.target] -= w;
    }
    let priority = |v: usize, in_deg: &[i64], out_deg: &[i64]| {
        if out_deg[v] == 0 {
            2
        } else if in_deg[v] == 0 {
            1
        } else {
            0
        }
    };
    let mut alive = vec![true; n];
    let mut rank = vec![0usize; n];
    let (mut left, mut right) = (0usize, n);
    for _ in 0..n {
        let mut best: Option<usize> = None;
        for v in (0..n).filter(|&v| alive[v]) {
            best = Some(match best {
                None => v,
                Some(b) => {
                    let (pv, pb) = (priority(v, &in_deg, &out_deg), priority(b, &in_deg, &out_deg));
                    let v_first = if pv != pb {
                        pv > pb
                    } else if pv == 0 && balance[v] != balance[b] {
                        balance[v] > balance[b]
                    } else {
                        keys[v] < keys[b] || (keys[v] == keys[b] && v < b)
                    };
                    if v_first { v } else { b }
                }
            });
        }
        let v = best.expect("a live node remains");
        if out_deg[v] == 0 {
            right -= 1;
            rank[v] = right;
        } else {
            rank[v] = left;
            left += 1;
        }
        alive[v] = false;
        for &i in &outgoing[v] {
            let t = g.links[i].target;
            if alive[t] {
                in_deg[t] -= 1;
                balance[t] += weights[i];
            }
        }
        for &i in &incoming[v] {
            let s = g.links[i].source;
            if alive[s] {
                out_deg[s] -= 1;
                balance[s] -= weights[i];
            }
        }
    }
    rank
}

// ── index.js ────────────────────────────────────────────────────────────────

fn identify_circles(g: &mut Graph, ids: &[String]) {
    let order = feedback_order(g, ids);
    for l in &mut g.links {
        l.circular = order[l.source] >= order[l.target];
    }
}

fn select_circular_link_types(g: &mut Graph) {
    let (mut tops, mut bottoms) = (0, 0);
    for i in 0..g.links.len() {
        if !g.links[i].circular {
            continue;
        }
        let (s, t) = (g.links[i].source, g.links[i].target);
        let side = match (g.nodes[s].circular_link_type, g.nodes[t].circular_link_type) {
            (Some(side), _) | (None, Some(side)) => side,
            (None, None) => {
                if tops < bottoms {
                    LoopSide::Top
                } else {
                    LoopSide::Bottom
                }
            }
        };
        g.links[i].circular_link_type = Some(side);
        if side == LoopSide::Top {
            tops += 1;
        } else {
            bottoms += 1;
        }
        g.nodes[s].circular_link_type = Some(side);
        g.nodes[t].circular_link_type = Some(side);
    }
    for i in 0..g.links.len() {
        if !g.links[i].circular {
            continue;
        }
        let (s, t) = (g.links[i].source, g.links[i].target);
        if g.nodes[s].circular_link_type == g.nodes[t].circular_link_type || s == t {
            g.links[i].circular_link_type = g.nodes[s].circular_link_type;
        }
    }
}

fn compute_node_values(g: &mut Graph) {
    for v in 0..g.nodes.len() {
        let out: f64 = g.nodes[v].source_links.iter().map(|&l| g.links[l].value).sum();
        let inn: f64 = g.nodes[v].target_links.iter().map(|&l| g.links[l].value).sum();
        g.nodes[v].value = out.max(inn);
        g.nodes[v].part_of_cycle = false;
        let touching: Vec<usize> =
            g.nodes[v].source_links.iter().chain(&g.nodes[v].target_links).copied().collect();
        for l in touching {
            if g.links[l].circular {
                g.nodes[v].part_of_cycle = true;
                g.nodes[v].circular_link_type = g.links[l].circular_link_type;
            }
        }
    }
}

/// d3-array `groups(nodes, d => d.column)` sorted by column: nodes in input
/// order within each column.
fn columns(g: &Graph) -> Vec<Vec<usize>> {
    let mut keys: Vec<i64> = Vec::new();
    let mut cols: Vec<Vec<usize>> = Vec::new();
    for (v, node) in g.nodes.iter().enumerate() {
        match keys.iter().position(|&c| c == node.column) {
            Some(i) => cols[i].push(v),
            None => {
                keys.push(node.column);
                cols.push(vec![v]);
            }
        }
    }
    let mut order: Vec<usize> = (0..keys.len()).collect();
    order.sort_by_key(|&i| keys[i]);
    order.into_iter().map(|i| std::mem::take(&mut cols[i])).collect()
}

fn adjust_sankey_size(g: &mut Graph, py: f64, dx: f64) {
    let ky = columns(g)
        .iter()
        .map(|c| {
            let total: f64 = c.iter().map(|&v| g.nodes[v].value).sum();
            (g.y1 - g.y0 - (c.len() as f64 - 1.0) * py) / total
        })
        .fold(f64::INFINITY, f64::min);
    for l in &mut g.links {
        l.width = l.value * ky;
    }
    let max_col = g.nodes.iter().map(|n| n.column).max().unwrap_or(0);
    for n in &mut g.nodes {
        n.x0 = if max_col > 0 { g.x0 + n.column as f64 * ((g.x1 - g.x0 - dx) / max_col as f64) } else { g.x0 };
        n.x1 = n.x0 + dx;
    }
    g.ky = ky;
}

fn center(n: &Node) -> f64 {
    (n.y0 + n.y1) / 2.0
}

fn non_self_cycles(g: &Graph, v: usize) -> usize {
    let n = &g.nodes[v];
    n.source_links
        .iter()
        .chain(&n.target_links)
        .filter(|&&l| g.links[l].circular && g.links[l].source != g.links[l].target)
        .count()
}

fn barycenter(g: &Graph, v: usize) -> f64 {
    let (mut sum, mut total) = (0.0, 0.0);
    for &l in &g.nodes[v].target_links {
        let k = &g.links[l];
        if !k.circular {
            let w = if k.value != 0.0 { k.value } else { 1.0 };
            sum += center(&g.nodes[k.source]) * w;
            total += w;
        }
    }
    for &l in &g.nodes[v].source_links {
        let k = &g.links[l];
        if !k.circular {
            let w = if k.value != 0.0 { k.value } else { 1.0 };
            sum += center(&g.nodes[k.target]) * w;
            total += w;
        }
    }
    if total > 0.0 {
        sum / total
    } else {
        f64::NAN
    }
}

fn side_rank(t: Option<LoopSide>) -> Option<bool> {
    t.map(|s| s == LoopSide::Top)
}

fn compute_node_breadths(g: &mut Graph) -> Result<(), String> {
    let ky = g.ky;
    let cols = columns(g);
    let ncols = cols.len() as i64;
    for (ci, mut nodes) in cols.into_iter().enumerate() {
        let len = nodes.len();
        if ci > 0 {
            let bary: Vec<(usize, f64, usize)> =
                nodes.iter().enumerate().map(|(idx, &v)| (v, barycenter(g, v), idx)).collect();
            let info = |v: usize| *bary.iter().find(|b| b.0 == v).unwrap();
            js_sort(&mut nodes, |a, b| {
                let (ta, tb) = (g.nodes[a].circular_link_type, g.nodes[b].circular_link_type);
                if ta != tb {
                    match (side_rank(ta), side_rank(tb)) {
                        (Some(true), _) => return -1.0,
                        (_, Some(true)) => return 1.0,
                        (Some(false), _) => return 1.0,
                        (_, Some(false)) => return -1.0,
                        _ => {}
                    }
                }
                let ((_, ba, ia), (_, bb, ib)) = (info(a), info(b));
                match (ba.is_nan(), bb.is_nan()) {
                    (false, false) => ba - bb,
                    (false, true) => -1.0,
                    (true, false) => 1.0,
                    (true, true) => ia as f64 - ib as f64,
                }
            })?;
        } else {
            js_sort(&mut nodes, |a, b| {
                let (na, nb) = (&g.nodes[a], &g.nodes[b]);
                if na.circular_link_type == nb.circular_link_type {
                    non_self_cycles(g, b) as f64 - non_self_cycles(g, a) as f64
                } else if (na.circular_link_type == Some(LoopSide::Top)
                    && (nb.circular_link_type == Some(LoopSide::Bottom) || !nb.part_of_cycle))
                    || (!na.part_of_cycle && nb.circular_link_type == Some(LoopSide::Bottom))
                {
                    -1.0
                } else {
                    0.0
                }
            })?;
        }
        for (i, &v) in nodes.iter().enumerate() {
            let fi = i as f64;
            let h = g.nodes[v].value * ky;
            let lone = len == 1 && (g.nodes[v].depth == ncols - 1 || g.nodes[v].depth == 0);
            let y0 = if lone {
                g.y1 / 2.0 - h
            } else if g.nodes[v].part_of_cycle {
                if non_self_cycles(g, v) == 0 {
                    g.y1 / 2.0 + fi
                } else if g.nodes[v].circular_link_type == Some(LoopSide::Top) {
                    g.y0 + fi
                } else {
                    g.y1 - h - fi
                }
            } else if g.y0 == 0.0 || g.y1 == 0.0 {
                (g.y1 - g.y0) / len as f64 * fi
            } else {
                (g.y1 - g.y0) / 2.0 - len as f64 / 2.0 + fi
            };
            g.nodes[v].y0 = y0;
            g.nodes[v].y1 = y0 + h;
        }
    }
    Ok(())
}

/// sankey-plus's `ascendingBreadth`: nodes on a cycle go above/below the rest.
fn ascending_breadth(g: &Graph, a: usize, b: usize) -> f64 {
    let (na, nb) = (&g.nodes[a], &g.nodes[b]);
    if na.part_of_cycle == nb.part_of_cycle {
        na.y0 - nb.y0
    } else if na.circular_link_type == Some(LoopSide::Top) || nb.circular_link_type == Some(LoopSide::Bottom) {
        -1.0
    } else {
        1.0
    }
}

fn resolve_collisions(g: &mut Graph, columns: &mut [Vec<usize>], py: f64) -> Result<(), String> {
    for nodes in columns.iter_mut() {
        js_sort(nodes, |a, b| ascending_breadth(g, a, b))?;
        let n = nodes.len();
        let mut y = g.y0;
        for &v in nodes.iter() {
            let dy = y - g.nodes[v].y0;
            if dy > 0.0 {
                g.nodes[v].y0 += dy;
                g.nodes[v].y1 += dy;
            }
            y = g.nodes[v].y1 + py;
        }
        let dy = y - py - g.y1;
        if dy > 0.0 {
            let last = nodes[n - 1];
            g.nodes[last].y0 -= dy;
            g.nodes[last].y1 -= dy;
            let mut y = g.nodes[last].y0;
            for i in (0..n.saturating_sub(1)).rev() {
                let v = nodes[i];
                let dy = g.nodes[v].y1 + py - y;
                if dy > 0.0 {
                    g.nodes[v].y0 -= dy;
                    g.nodes[v].y1 -= dy;
                }
                y = g.nodes[v].y0;
            }
        }
    }
    Ok(())
}

/// d3-array `mean` read through JS truthiness: `None` for no values *and* for
/// a mean of exactly 0, which the caller's `avgT && avgS` treats as absent.
fn truthy_mean(vals: impl Iterator<Item = f64>) -> Option<f64> {
    let (mut s, mut c) = (0.0, 0usize);
    for v in vals.filter(|v| !v.is_nan()) {
        s += v;
        c += 1;
    }
    let m = if c > 0 { s / c as f64 } else { return None };
    (m != 0.0).then_some(m)
}

fn relax_left_and_right(g: &mut Graph, columns: &[Vec<usize>], alpha: f64) {
    let ncols = columns.len() as i64;
    for nodes in columns {
        let n = nodes.len();
        let depth = g.nodes[nodes[0]].depth;
        for &v in nodes {
            let node = &g.nodes[v];
            if node.source_links.is_empty() && node.target_links.is_empty() {
                continue;
            }
            let avg_t = truthy_mean(node.source_links.iter().map(|&l| center(&g.nodes[g.links[l].target])));
            let avg_s = truthy_mean(node.target_links.iter().map(|&l| center(&g.nodes[g.links[l].source])));
            let avg = match (avg_t, avg_s) {
                (Some(t), Some(s)) => Some((t + s) / 2.0),
                (t, s) => t.or(s),
            };
            let h = node.y1 - node.y0;
            if node.part_of_cycle && non_self_cycles(g, v) > 0 {
                if let Some(a) = avg.filter(|&a| a != 0.0) {
                    let dy = (a - center(node)) * alpha * 0.3;
                    g.nodes[v].y0 += dy;
                    g.nodes[v].y1 += dy;
                }
            } else if (depth == 0 || depth == ncols - 1) && n == 1 {
                g.nodes[v].y0 = g.y1 / 2.0 - h / 2.0;
                g.nodes[v].y1 = g.y1 / 2.0 + h / 2.0;
            } else if node.target_links.len() == 1
                && g.nodes[g.links[node.target_links[0]].source].source_links.len() == 1
            {
                let y0 = g.nodes[g.links[node.target_links[0]].source].y0;
                g.nodes[v].y0 = y0;
                g.nodes[v].y1 = y0 + h;
            } else {
                // `avg` undefined makes `dy` NaN, and NaN poisons the node.
                let dy = (avg.unwrap_or(f64::NAN) - center(node)) * alpha;
                g.nodes[v].y0 += dy;
                g.nodes[v].y1 += dy;
            }
        }
    }
}

/// `ascendingTargetBreadth` / `ascendingSourceBreadth`: by the far node's
/// breadth, then link index (`||` also skips a NaN).
fn link_breadth(g: &Graph, a: usize, b: usize, by_target: bool) -> f64 {
    let (na, nb) = if by_target {
        (g.links[a].target, g.links[b].target)
    } else {
        (g.links[a].source, g.links[b].source)
    };
    let r = ascending_breadth(g, na, nb);
    if r == 0.0 || r.is_nan() {
        a as f64 - b as f64
    } else {
        r
    }
}

fn compute_link_breadths(g: &mut Graph) -> Result<(), String> {
    for v in 0..g.nodes.len() {
        let mut s = std::mem::take(&mut g.nodes[v].source_links);
        js_sort(&mut s, |a, b| link_breadth(g, a, b, true))?;
        g.nodes[v].source_links = s;
        let mut t = std::mem::take(&mut g.nodes[v].target_links);
        js_sort(&mut t, |a, b| link_breadth(g, a, b, false))?;
        g.nodes[v].target_links = t;
    }
    for v in 0..g.nodes.len() {
        let (mut y0, mut y0c) = (g.nodes[v].y0, g.nodes[v].y1);
        let (mut y1, mut y1c) = (y0, y0c);
        for i in 0..g.nodes[v].source_links.len() {
            let l = g.nodes[v].source_links[i];
            let w = g.links[l].width;
            if g.links[l].circular {
                g.links[l].y0 = y0c - w / 2.0;
                y0c -= w;
            } else {
                g.links[l].y0 = y0 + w / 2.0;
                y0 += w;
            }
        }
        for i in 0..g.nodes[v].target_links.len() {
            let l = g.nodes[v].target_links[i];
            let w = g.links[l].width;
            if g.links[l].circular {
                g.links[l].y1 = y1c - w / 2.0;
                y1c -= w;
            } else {
                g.links[l].y1 = y1 + w / 2.0;
                y1 += w;
            }
        }
    }
    Ok(())
}

fn fill_height(g: &mut Graph) {
    let top = g.links.iter().any(|l| l.circular_link_type == Some(LoopSide::Top));
    let bottom = g.links.iter().any(|l| l.circular_link_type == Some(LoopSide::Bottom));
    if top && bottom {
        return;
    }
    let min_y0 = g.nodes.iter().map(|n| n.y0).fold(f64::INFINITY, f64::min);
    let max_y1 = g.nodes.iter().map(|n| n.y1).fold(f64::NEG_INFINITY, f64::max);
    let ratio = (g.y1 - g.y0) / (max_y1 - min_y0);
    let (gy0, gy1) = (g.y0, g.y1);
    let mv = |v: f64| (v - min_y0) / (max_y1 - min_y0) * (gy1 - gy0) + gy0;
    if ratio < 1.0 {
        for n in &mut g.nodes {
            n.y0 = mv(n.y0);
            n.y1 = mv(n.y1);
        }
        for l in &mut g.links {
            l.y0 = mv(l.y0);
            l.y1 = mv(l.y1);
            l.width *= ratio;
        }
    } else {
        for v in 0..g.nodes.len() {
            let h = g.nodes[v].y1 - g.nodes[v].y0;
            let dy = mv(g.nodes[v].y0) - g.nodes[v].y0;
            g.nodes[v].y0 = mv(g.nodes[v].y0);
            g.nodes[v].y1 = g.nodes[v].y0 + h;
            for i in 0..g.nodes[v].source_links.len() {
                let l = g.nodes[v].source_links[i];
                g.links[l].y0 += dy;
            }
            for i in 0..g.nodes[v].target_links.len() {
                let l = g.nodes[v].target_links[i];
                g.links[l].y1 += dy;
            }
        }
    }
}

// ── sortGraph.js / linkAttributes.js ────────────────────────────────────────

fn up(l: &Link) -> bool {
    l.y0 - l.y1 > 0.0
}

fn link_angle(g: &Graph, l: &Link) -> f64 {
    let adjacent = (l.y1 - l.y0).abs();
    let opposite = (g.nodes[l.target].x0 - g.nodes[l.source].x1).abs();
    (opposite / adjacent).atan()
}

fn x_length(g: &Graph, l: &Link) -> f64 {
    g.nodes[l.target].x0 - g.nodes[l.source].x1
}

/// `linkPerpendicularYToLinkTarget` (`to_target`) / `…ToLinkSource`.
fn perpendicular_y(g: &Graph, longer: &Link, shorter: &Link, to_target: bool) -> f64 {
    let h = x_length(g, shorter) / link_angle(g, longer).tan();
    if up(longer) == to_target {
        longer.y1 - h
    } else {
        longer.y1 + h
    }
}

/// The shared comparator of `sortSourceLinks` (`by_target`) and
/// `sortTargetLinks`: forward links by where they land, loops pushed to
/// their side and nested by reach.
fn endpoint_order(g: &Graph, a: usize, b: usize, by_target: bool) -> f64 {
    let (l1, l2) = (&g.links[a], &g.links[b]);
    let far = |l: &Link| if by_target { l.target } else { l.source };
    let (col1, col2) = (g.nodes[far(l1)].column, g.nodes[far(l2)].column);
    let near_y = |l: &Link| if by_target { l.y1 } else { l.y0 };
    if !l1.circular && !l2.circular {
        if col1 == col2 || up(l1) != up(l2) {
            return near_y(l1) - near_y(l2);
        }
        return if by_target {
            if col1 > col2 {
                l1.y1 - perpendicular_y(g, l2, l1, true)
            } else {
                perpendicular_y(g, l1, l2, true) - l2.y1
            }
        } else if col2 < col1 {
            l1.y0 - perpendicular_y(g, l2, l1, false)
        } else {
            perpendicular_y(g, l1, l2, false) - l2.y0
        };
    }
    let top = |l: &Link| l.circular_link_type == Some(LoopSide::Top);
    if l1.circular && !l2.circular {
        return if top(l1) { -1.0 } else { 1.0 };
    }
    if l2.circular && !l1.circular {
        return if top(l2) { 1.0 } else { -1.0 };
    }
    if l1.circular_link_type != l2.circular_link_type {
        return if top(l1) { -1.0 } else { 1.0 };
    }
    let (y1, y2) = (g.nodes[far(l1)].y1, g.nodes[far(l2)].y1);
    let (c1, c2) = (col1 as f64, col2 as f64);
    match (top(l1), by_target, col1 == col2) {
        (true, true, true) => y1 - y2,
        (true, true, false) => c2 - c1,
        (false, true, true) => y2 - y1,
        (false, true, false) => c1 - c2,
        (true, false, true) => y1 - y2,
        (true, false, false) => c1 - c2,
        (false, false, true) => y1 - y2,
        (false, false, false) => c2 - c1,
    }
}

fn sort_source_links(g: &mut Graph) -> Result<(), String> {
    sort_endpoint_links(g, true)
}

fn sort_target_links(g: &mut Graph) -> Result<(), String> {
    sort_endpoint_links(g, false)
}

/// Re-sort a copy of each node's links and restack their ends — top loops
/// and forward links from the top, bottom loops from the bottom. The node's
/// own link lists keep their order.
fn sort_endpoint_links(g: &mut Graph, source_end: bool) -> Result<(), String> {
    for v in 0..g.nodes.len() {
        let mut ls = if source_end { g.nodes[v].source_links.clone() } else { g.nodes[v].target_links.clone() };
        ls.sort_unstable();
        if ls.len() > 1 {
            js_sort(&mut ls, |a, b| endpoint_order(g, a, b, source_end))?;
        }
        let set = |g: &mut Graph, l: usize, y: f64| {
            if source_end {
                g.links[l].y0 = y
            } else {
                g.links[l].y1 = y
            }
        };
        let mut off = g.nodes[v].y0;
        for &l in &ls {
            let w = g.links[l].width;
            set(g, l, off + w / 2.0);
            off += w;
        }
        let mut from_bottom = 0.0;
        for &l in ls.iter().rev() {
            let w = g.links[l].width;
            if g.links[l].circular_link_type == Some(LoopSide::Bottom) {
                let y = g.nodes[v].y1 - from_bottom - w / 2.0;
                set(g, l, y);
            }
            from_bottom += w;
        }
    }
    Ok(())
}

// ── circularPath.js / columnOccupancy.ts ────────────────────────────────────

/// Range maximum with monotone range updates (a segment tree), the stacking
/// height already taken over a span of columns.
struct ColumnOccupancy {
    size: usize,
    maximum: Vec<f64>,
    floor: Vec<f64>,
}

impl ColumnOccupancy {
    fn new(size: usize) -> Self {
        ColumnOccupancy { size, maximum: vec![0.0; 4 * size], floor: vec![0.0; 4 * size] }
    }

    fn query(&self, start: usize, end: usize) -> f64 {
        fn visit(o: &ColumnOccupancy, node: usize, lo: usize, hi: usize, s: usize, e: usize) -> f64 {
            if s <= lo && hi <= e {
                return o.maximum[node];
            }
            let mid = (lo + hi) >> 1;
            let l = if s <= mid { visit(o, node * 2, lo, mid, s, e) } else { 0.0 };
            let r = if e > mid { visit(o, node * 2 + 1, mid + 1, hi, s, e) } else { 0.0 };
            o.floor[node].max(l).max(r)
        }
        visit(self, 1, 0, self.size - 1, start, end)
    }

    fn reserve(&mut self, start: usize, end: usize, value: f64) {
        fn visit(o: &mut ColumnOccupancy, node: usize, lo: usize, hi: usize, s: usize, e: usize, v: f64) {
            o.maximum[node] = o.maximum[node].max(v);
            if s <= lo && hi <= e {
                o.floor[node] = o.floor[node].max(v);
                return;
            }
            let mid = (lo + hi) >> 1;
            if s <= mid {
                visit(o, node * 2, lo, mid, s, e, v);
            }
            if e > mid {
                visit(o, node * 2 + 1, mid + 1, hi, s, e, v);
            }
        }
        let size = self.size;
        visit(self, 1, 0, size - 1, start, end, value);
    }
}

/// Stack loops that span overlapping columns: longest spans first (they go
/// outermost), each reserving its width plus the gap over its span.
fn calc_vertical_buffer(g: &mut Graph, mut links: Vec<usize>, isolated: &[bool], gap: f64) -> Result<(), String> {
    if links.is_empty() {
        return Ok(());
    }
    js_sort(&mut links, |a, b| {
        let dist = |l: usize| (g.nodes[g.links[l].target].column - g.nodes[g.links[l].source].column) as f64;
        if dist(a) == dist(b) {
            if g.links[a].circular_link_type == Some(LoopSide::Bottom) {
                g.links[b].y0 - g.links[a].y0
            } else {
                g.links[a].y0 - g.links[b].y0
            }
        } else {
            dist(b) - dist(a)
        }
    })?;
    let mut cols: Vec<i64> = Vec::new();
    for &l in &links {
        for c in [g.nodes[g.links[l].source].column, g.nodes[g.links[l].target].column] {
            if !cols.contains(&c) {
                cols.push(c);
            }
        }
    }
    cols.sort();
    let index = |c: i64| cols.iter().position(|&x| x == c).unwrap();
    let mut occ = ColumnOccupancy::new(cols.len());
    for l in links {
        let s = index(g.nodes[g.links[l].source].column);
        let t = index(g.nodes[g.links[l].target].column);
        let (lo, hi) = (s.min(t), s.max(t));
        let buffer = if isolated[l] { 0.0 } else { occ.query(lo, hi) };
        let w = g.links[l].width;
        g.links[l].circular_path.as_mut().unwrap().vertical_buffer = buffer + w / 2.0;
        occ.reserve(lo, hi, buffer + w + gap);
    }
    Ok(())
}

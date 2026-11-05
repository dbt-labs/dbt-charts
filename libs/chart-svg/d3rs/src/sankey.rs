//! Port of d3-sankey's layout (v0.12.3, BSD-3-Clause, © Mike Bostock):
//! column assignment, iterative relaxation, collision resolution, and ribbon
//! ordering at each node. Nodes and links are indices instead of objects.
//! Reference: https://github.com/d3/d3-sankey/blob/master/src/sankey.js
//!
//! One extension: `fixed_layers` pins every node's column, the hook a
//! data-assigned `step` column needs (d3's `nodeAlign` only sees depth, and
//! a pinned node's column can exceed its depth). `nodeSort`/`linkSort` stay
//! at d3's defaults — relaxation-order nodes and breadth-ordered links.

// sankey.js skips a node with `if (!(w > 0)) continue`, which is also true for
// NaN; `w <= 0` is not. The negated comparisons stay as written upstream.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

use crate::path::Command;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Align {
    Left,
    Right,
    Center,
    Justify,
}

#[derive(Clone, Debug, Default)]
pub struct Node {
    pub source_links: Vec<usize>,
    pub target_links: Vec<usize>,
    pub value: f64,
    pub depth: usize,
    pub height: usize,
    pub layer: usize,
    pub x0: f64,
    pub x1: f64,
    pub y0: f64,
    pub y1: f64,
}

#[derive(Clone, Debug)]
pub struct Link {
    pub source: usize,
    pub target: usize,
    pub value: f64,
    pub width: f64,
    /// Ribbon center at the source edge (d3's `link.y0`).
    pub y0: f64,
    /// Ribbon center at the target edge (d3's `link.y1`).
    pub y1: f64,
}

#[derive(Debug)]
pub struct Graph {
    pub nodes: Vec<Node>,
    pub links: Vec<Link>,
}

pub struct Sankey {
    pub x0: f64,
    pub y0: f64,
    pub x1: f64,
    pub y1: f64,
    pub node_width: f64,
    pub node_padding: f64,
    pub align: Align,
    pub iterations: usize,
}

impl Default for Sankey {
    fn default() -> Self {
        Sankey {
            x0: 0.0,
            y0: 0.0,
            x1: 1.0,
            y1: 1.0,
            node_width: 24.0,
            node_padding: 8.0,
            align: Align::Justify,
            iterations: 6,
        }
    }
}

impl Sankey {
    /// Lay out `n_nodes` nodes joined by `(source, target, value)` links.
    /// `fixed_layers`, when given, is each node's column and replaces `align`.
    pub fn layout(
        &self,
        n_nodes: usize,
        links: &[(usize, usize, f64)],
        fixed_layers: Option<&[usize]>,
    ) -> Result<Graph, String> {
        let mut g = Graph {
            nodes: vec![Node::default(); n_nodes],
            links: Vec::with_capacity(links.len()),
        };
        for (i, &(s, t, v)) in links.iter().enumerate() {
            if s >= n_nodes || t >= n_nodes {
                return Err(format!("missing: {}", if s >= n_nodes { s } else { t }));
            }
            g.links.push(Link { source: s, target: t, value: v, width: 0.0, y0: 0.0, y1: 0.0 });
            g.nodes[s].source_links.push(i);
            g.nodes[t].target_links.push(i);
        }
        compute_node_values(&mut g);
        compute_node_depths(&mut g)?;
        compute_node_heights(&mut g)?;
        let mut st = State { s: self, py: self.node_padding };
        st.compute_node_breadths(&mut g, fixed_layers)?;
        compute_link_breadths(&mut g);
        Ok(g)
    }
}


/// A forward link as a filled band of half-width `hw` (Semiotic's
/// `areaLink` → `buildRibbonGeometry`, curvature 0.5): the top edge as
/// d3's horizontal cubic, down at the target, the bottom edge back. Filling
/// the band, unlike stroking the centerline, keeps a thick, steep link from
/// growing horns where the stroke's offset curve folds over itself.
pub fn ribbon_area(sx: f64, sy: f64, tx: f64, ty: f64, hw: f64) -> Vec<Command> {
    let xm = sx + (tx - sx) * 0.5;
    let (st, sb, tt, tb) = (sy - hw, sy + hw, ty - hw, ty + hw);
    vec![
        Command::MoveTo(sx, st),
        Command::BezierCurveTo(xm, st, xm, tt, tx, tt),
        Command::LineTo(tx, tb),
        Command::BezierCurveTo(xm, tb, xm, sb, sx, sb),
        Command::ClosePath,
    ]
}

fn compute_node_values(g: &mut Graph) {
    for n in 0..g.nodes.len() {
        let out: f64 = g.nodes[n].source_links.iter().map(|&l| g.links[l].value).sum();
        let inn: f64 = g.nodes[n].target_links.iter().map(|&l| g.links[l].value).sum();
        g.nodes[n].value = out.max(inn);
    }
}

/// Breadth-first sweep in JS `Set` insertion order: every node starts at
/// `x = 0`, and each pass pushes the frontier one step along the links.
fn sweep(g: &mut Graph, forward: bool) -> Result<(), String> {
    let n = g.nodes.len();
    let mut current: Vec<usize> = (0..n).collect();
    let mut x = 0;
    while !current.is_empty() {
        let mut seen = vec![false; n];
        let mut next = Vec::new();
        for &node in &current {
            let links = if forward {
                g.nodes[node].depth = x;
                &g.nodes[node].source_links
            } else {
                g.nodes[node].height = x;
                &g.nodes[node].target_links
            };
            for &l in links {
                let other = if forward { g.links[l].target } else { g.links[l].source };
                if !seen[other] {
                    seen[other] = true;
                    next.push(other);
                }
            }
        }
        x += 1;
        if x > n {
            return Err("circular link".into());
        }
        current = next;
    }
    Ok(())
}

fn compute_node_depths(g: &mut Graph) -> Result<(), String> {
    sweep(g, true)
}

fn compute_node_heights(g: &mut Graph) -> Result<(), String> {
    sweep(g, false)
}

fn compute_link_breadths(g: &mut Graph) {
    for n in 0..g.nodes.len() {
        let mut y0 = g.nodes[n].y0;
        let mut y1 = y0;
        for i in 0..g.nodes[n].source_links.len() {
            let l = g.nodes[n].source_links[i];
            g.links[l].y0 = y0 + g.links[l].width / 2.0;
            y0 += g.links[l].width;
        }
        for i in 0..g.nodes[n].target_links.len() {
            let l = g.nodes[n].target_links[i];
            g.links[l].y1 = y1 + g.links[l].width / 2.0;
            y1 += g.links[l].width;
        }
    }
}

struct State<'a> {
    s: &'a Sankey,
    py: f64,
}

impl State<'_> {
    fn align(&self, g: &Graph, node: usize, n: usize) -> f64 {
        let d = &g.nodes[node];
        match self.s.align {
            Align::Left => d.depth as f64,
            Align::Right => n as f64 - 1.0 - d.height as f64,
            Align::Justify => {
                if d.source_links.is_empty() {
                    n as f64 - 1.0
                } else {
                    d.depth as f64
                }
            }
            Align::Center => {
                if !d.target_links.is_empty() {
                    d.depth as f64
                } else if !d.source_links.is_empty() {
                    d.source_links
                        .iter()
                        .map(|&l| g.nodes[g.links[l].target].depth)
                        .min()
                        .map_or(0.0, |m| m as f64 - 1.0)
                } else {
                    0.0
                }
            }
        }
    }

    fn compute_node_layers(
        &self,
        g: &mut Graph,
        fixed_layers: Option<&[usize]>,
    ) -> Result<Vec<Vec<usize>>, String> {
        let x = match fixed_layers {
            Some(f) => {
                if f.len() != g.nodes.len() {
                    return Err(format!(
                        "fixed_layers has {} entries for {} nodes",
                        f.len(),
                        g.nodes.len()
                    ));
                }
                f.iter().max().map_or(0, |m| m + 1)
            }
            None => g.nodes.iter().map(|d| d.depth).max().map_or(0, |m| m + 1),
        };
        let kx = (self.s.x1 - self.s.x0 - self.s.node_width) / (x as f64 - 1.0);
        let mut columns: Vec<Vec<usize>> = vec![Vec::new(); x];
        for node in 0..g.nodes.len() {
            let i = match fixed_layers {
                Some(f) => f[node],
                None => (self.align(g, node, x).floor().min(x as f64 - 1.0).max(0.0)) as usize,
            };
            let d = &mut g.nodes[node];
            d.layer = i;
            d.x0 = self.s.x0 + i as f64 * kx;
            d.x1 = d.x0 + self.s.node_width;
            columns[i].push(node);
        }
        if let Some(empty) = columns.iter().position(Vec::is_empty) {
            return Err(format!("column {empty} has no nodes"));
        }
        Ok(columns)
    }

    fn compute_node_breadths(
        &mut self,
        g: &mut Graph,
        fixed_layers: Option<&[usize]>,
    ) -> Result<(), String> {
        let mut columns = self.compute_node_layers(g, fixed_layers)?;
        let widest = columns.iter().map(Vec::len).max().unwrap_or(0);
        self.py = crate::js_math::js_min(self.s.node_padding, (self.s.y1 - self.s.y0) / (widest as f64 - 1.0));
        self.initialize_node_breadths(g, &columns);
        let iterations = self.s.iterations;
        for i in 0..iterations {
            let alpha = crate::js_math::pow(0.99, i as f64);
            let beta = (1.0 - alpha).max((i + 1) as f64 / iterations as f64);
            self.relax_right_to_left(g, &mut columns, alpha, beta);
            self.relax_left_to_right(g, &mut columns, alpha, beta);
        }
        Ok(())
    }

    fn initialize_node_breadths(&self, g: &mut Graph, columns: &[Vec<usize>]) {
        let (y0, y1, py) = (self.s.y0, self.s.y1, self.py);
        let ky = columns
            .iter()
            .map(|c| {
                let total: f64 = c.iter().map(|&n| g.nodes[n].value).sum();
                (y1 - y0 - (c.len() as f64 - 1.0) * py) / total
            })
            .fold(f64::INFINITY, f64::min);
        for nodes in columns {
            let mut y = y0;
            for &node in nodes {
                let d = &mut g.nodes[node];
                d.y0 = y;
                d.y1 = y + d.value * ky;
                y = d.y1 + py;
                for &l in &g.nodes[node].source_links.clone() {
                    g.links[l].width = g.links[l].value * ky;
                }
            }
            let y = (y1 - y + py) / (nodes.len() as f64 + 1.0);
            for (i, &node) in nodes.iter().enumerate() {
                g.nodes[node].y0 += y * (i as f64 + 1.0);
                g.nodes[node].y1 += y * (i as f64 + 1.0);
            }
            for &node in nodes {
                sort_source_links(g, node);
                sort_target_links(g, node);
            }
        }
    }

    fn relax_left_to_right(&self, g: &mut Graph, columns: &mut [Vec<usize>], alpha: f64, beta: f64) {
        for column in columns.iter_mut().skip(1) {
            for &target in column.iter() {
                let (mut y, mut w) = (0.0, 0.0);
                for &l in &g.nodes[target].target_links {
                    let source = g.links[l].source;
                    let v = g.links[l].value
                        * (g.nodes[target].layer as f64 - g.nodes[source].layer as f64);
                    y += self.target_top(g, source, target) * v;
                    w += v;
                }
                if !(w > 0.0) {
                    continue;
                }
                let dy = (y / w - g.nodes[target].y0) * alpha;
                g.nodes[target].y0 += dy;
                g.nodes[target].y1 += dy;
                reorder_node_links(g, target);
            }
            sort_by_breadth(g, column);
            self.resolve_collisions(g, column, beta);
        }
    }

    fn relax_right_to_left(&self, g: &mut Graph, columns: &mut [Vec<usize>], alpha: f64, beta: f64) {
        for i in (0..columns.len().saturating_sub(1)).rev() {
            for &source in &columns[i] {
                let (mut y, mut w) = (0.0, 0.0);
                for &l in &g.nodes[source].source_links {
                    let target = g.links[l].target;
                    let v = g.links[l].value
                        * (g.nodes[target].layer as f64 - g.nodes[source].layer as f64);
                    y += self.source_top(g, source, target) * v;
                    w += v;
                }
                if !(w > 0.0) {
                    continue;
                }
                let dy = (y / w - g.nodes[source].y0) * alpha;
                g.nodes[source].y0 += dy;
                g.nodes[source].y1 += dy;
                reorder_node_links(g, source);
            }
            sort_by_breadth(g, &mut columns[i]);
            self.resolve_collisions(g, &columns[i], beta);
        }
    }

    fn resolve_collisions(&self, g: &mut Graph, nodes: &[usize], alpha: f64) {
        let i = nodes.len() >> 1;
        let subject = nodes[i];
        let (sy0, sy1) = (g.nodes[subject].y0, g.nodes[subject].y1);
        self.bottom_to_top(g, nodes, sy0 - self.py, i as isize - 1, alpha);
        self.top_to_bottom(g, nodes, sy1 + self.py, i + 1, alpha);
        self.bottom_to_top(g, nodes, self.s.y1, nodes.len() as isize - 1, alpha);
        self.top_to_bottom(g, nodes, self.s.y0, 0, alpha);
    }

    /// Push any overlapping nodes down.
    fn top_to_bottom(&self, g: &mut Graph, nodes: &[usize], mut y: f64, from: usize, alpha: f64) {
        for &node in nodes.iter().skip(from) {
            let d = &mut g.nodes[node];
            let dy = (y - d.y0) * alpha;
            if dy > 1e-6 {
                d.y0 += dy;
                d.y1 += dy;
            }
            y = d.y1 + self.py;
        }
    }

    /// Push any overlapping nodes up.
    fn bottom_to_top(&self, g: &mut Graph, nodes: &[usize], mut y: f64, from: isize, alpha: f64) {
        let mut i = from;
        while i >= 0 {
            let d = &mut g.nodes[nodes[i as usize]];
            let dy = (d.y1 - y) * alpha;
            if dy > 1e-6 {
                d.y0 -= dy;
                d.y1 -= dy;
            }
            y = d.y0 - self.py;
            i -= 1;
        }
    }

    /// The `target.y0` that would produce an ideal link from source to target.
    fn target_top(&self, g: &Graph, source: usize, target: usize) -> f64 {
        let s = &g.nodes[source];
        let mut y = s.y0 - (s.source_links.len() as f64 - 1.0) * self.py / 2.0;
        for &l in &s.source_links {
            if g.links[l].target == target {
                break;
            }
            y += g.links[l].width + self.py;
        }
        for &l in &g.nodes[target].target_links {
            if g.links[l].source == source {
                break;
            }
            y -= g.links[l].width;
        }
        y
    }

    /// The `source.y0` that would produce an ideal link from source to target.
    fn source_top(&self, g: &Graph, source: usize, target: usize) -> f64 {
        let t = &g.nodes[target];
        let mut y = t.y0 - (t.target_links.len() as f64 - 1.0) * self.py / 2.0;
        for &l in &t.target_links {
            if g.links[l].source == source {
                break;
            }
            y += g.links[l].width + self.py;
        }
        for &l in &g.nodes[source].source_links {
            if g.links[l].target == target {
                break;
            }
            y -= g.links[l].width;
        }
        y
    }
}

/// d3's `ascendingBreadth` (`a.y0 - b.y0`): `-0` and `0` tie, as in JS.
fn breadth(a: f64, b: f64) -> std::cmp::Ordering {
    a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal)
}

fn sort_by_breadth(g: &Graph, column: &mut [usize]) {
    column.sort_by(|&a, &b| breadth(g.nodes[a].y0, g.nodes[b].y0));
}

/// d3's `ascendingTargetBreadth`: by the target's `y0`, then link index.
fn sort_source_links(g: &mut Graph, node: usize) {
    let mut ls = std::mem::take(&mut g.nodes[node].source_links);
    ls.sort_by(|&a, &b| {
        let (ta, tb) = (g.links[a].target, g.links[b].target);
        breadth(g.nodes[ta].y0, g.nodes[tb].y0).then(a.cmp(&b))
    });
    g.nodes[node].source_links = ls;
}

/// d3's `ascendingSourceBreadth`: by the source's `y0`, then link index.
fn sort_target_links(g: &mut Graph, node: usize) {
    let mut ls = std::mem::take(&mut g.nodes[node].target_links);
    ls.sort_by(|&a, &b| {
        let (sa, sb) = (g.links[a].source, g.links[b].source);
        breadth(g.nodes[sa].y0, g.nodes[sb].y0).then(a.cmp(&b))
    });
    g.nodes[node].target_links = ls;
}

/// After `node` moves, re-sort the link lists of every neighbor it touches.
fn reorder_node_links(g: &mut Graph, node: usize) {
    for i in 0..g.nodes[node].target_links.len() {
        let source = g.links[g.nodes[node].target_links[i]].source;
        sort_source_links(g, source);
    }
    for i in 0..g.nodes[node].source_links.len() {
        let target = g.links[g.nodes[node].source_links[i]].target;
        sort_target_links(g, target);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() < 1e-9
    }

    #[test]
    fn a_cycle_is_refused() {
        let err = Sankey::default().layout(2, &[(0, 1, 1.0), (1, 0, 1.0)], None).unwrap_err();
        assert_eq!(err, "circular link");
    }

    #[test]
    fn fixed_layers_pin_columns() {
        let s = Sankey { x1: 100.0, y1: 100.0, node_width: 10.0, ..Sankey::default() };
        // Node 2 has depth 1 but is pinned to column 2.
        let g = s.layout(3, &[(0, 1, 1.0), (0, 2, 1.0)], Some(&[0, 1, 2])).unwrap();
        assert_eq!(g.nodes.iter().map(|n| n.layer).collect::<Vec<_>>(), [0, 1, 2]);
        assert!(close(g.nodes[2].x0, 90.0));
    }

    #[test]
    fn an_empty_pinned_column_is_refused() {
        let err = Sankey::default().layout(2, &[(0, 1, 1.0)], Some(&[0, 2])).unwrap_err();
        assert_eq!(err, "column 1 has no nodes");
    }
}

//! Reading a tree back: every node with its composed map, and the marks.

use crate::{Affine, Attrs, Bounds, Node, Role, Shape};

/// Pre-order over `nodes` and everything drawn inside them. See [`walk`].
pub struct Walk<'a> {
    stack: Vec<(&'a Node, Affine)>,
}

impl<'a> Iterator for Walk<'a> {
    type Item = (&'a Node, Affine);

    fn next(&mut self) -> Option<Self::Item> {
        let (node, map) = self.stack.pop()?;
        // Definitions and clip shapes are not drawn where they sit.
        if !matches!(node.shape, Shape::Defs(_) | Shape::ClipPath(_) | Shape::Mask { .. } | Shape::Pattern { .. }) {
            for child in node.children().iter().rev() {
                self.stack.push((child, map.then_inner(&child.affine())));
            }
        }
        Some((node, map))
    }
}

/// Each node under `nodes` with the map from its own frame to the frame of the
/// slice given: its transforms and every ancestor's inside the slice, nothing
/// above it.
pub fn walk(nodes: &[Node]) -> Walk<'_> {
    Walk { stack: nodes.iter().rev().map(|node| (node, node.affine())).collect() }
}

/// A node that draws something and the box it covers in the frame of the slice [`drawn`] was given.
#[derive(Clone, Debug, PartialEq)]
pub struct Drawn<'a> {
    pub node: &'a Node,
    pub bounds: Bounds,
    /// The attributes of every node it is drawn inside, outermost first.
    ancestors: Vec<&'a Attrs>,
    /// The map from the node's own frame to the frame of the slice given.
    pub map: Affine,
}

impl<'a> Drawn<'a> {
    fn nearest<T>(&self, get: impl Fn(&'a Attrs) -> Option<T>) -> Option<T> {
        std::iter::once(&self.node.attrs).chain(self.ancestors.iter().rev().copied()).find_map(get)
    }

    /// The text of presentation attribute `name`: the node's own, else the nearest ancestor's
    /// (SVG inherits `fill` and `stroke`); `None` where none is set.
    pub fn paint(&self, name: &str) -> Option<&'a str> {
        self.nearest(|attrs| attrs.get(name))
    }

    /// The number of presentation attribute `name`, inherited like [`Drawn::paint`].
    pub fn paint_num(&self, name: &str) -> Option<f64> {
        self.nearest(|attrs| attrs.get_num(name))
    }
}

/// Every node under `nodes` whose role `keep` accepts and that draws something, in paint order.
pub fn drawn(nodes: &[Node], keep: impl Fn(&Role) -> bool) -> Vec<Drawn<'_>> {
    fn visit<'a>(node: &'a Node, map: Affine, ancestors: &mut Vec<&'a Attrs>, keep: &impl Fn(&Role) -> bool, out: &mut Vec<Drawn<'a>>) {
        if keep(&node.role) {
            if let Some(bounds) = node.bounds() {
                out.push(Drawn { node, bounds: bounds.transformed(&map), ancestors: ancestors.clone(), map });
            }
        }
        // Definitions and clip shapes are not drawn where they sit.
        if !matches!(node.shape, Shape::Defs(_) | Shape::ClipPath(_) | Shape::Mask { .. } | Shape::Pattern { .. }) {
            ancestors.push(&node.attrs);
            for child in node.children() {
                visit(child, map.then_inner(&child.affine()), ancestors, keep, out);
            }
            ancestors.pop();
        }
    }
    let mut out = Vec::new();
    for node in nodes {
        visit(node, node.affine(), &mut Vec::new(), &keep, &mut out);
    }
    out
}

/// A mark and the box it covers in the frame of the slice [`marks`] was given.
#[derive(Clone, Debug, PartialEq)]
pub struct MarkBox<'a> {
    pub layer: usize,
    pub series: Option<&'a str>,
    pub datum: Option<usize>,
    pub bounds: Bounds,
    /// The mark node's own presentation attributes.
    pub attrs: &'a Attrs,
    drawn: Drawn<'a>,
    /// The mark node's own geometry, in its own frame.
    pub shape: &'a Shape,
    /// The map from that frame to the frame of the slice given.
    pub map: Affine,
}

impl<'a> MarkBox<'a> {
    /// The text of presentation attribute `name`: the mark's own, else the nearest ancestor's
    /// (SVG inherits `fill` and `stroke`); `None` where none is set.
    pub fn paint(&self, name: &str) -> Option<&'a str> {
        self.drawn.paint(name)
    }

    /// The number of presentation attribute `name`, inherited like [`MarkBox::paint`].
    pub fn paint_num(&self, name: &str) -> Option<f64> {
        self.drawn.paint_num(name)
    }
}

/// Every `Role::Mark` under `nodes` that draws something, in paint order.
pub fn marks(nodes: &[Node]) -> Vec<MarkBox<'_>> {
    drawn(nodes, |role| matches!(role, Role::Mark { .. }))
        .into_iter()
        .map(|drawn| {
            let Role::Mark { layer, series, datum } = &drawn.node.role else { unreachable!("drawn kept marks only") };
            MarkBox { layer: *layer, series: series.as_deref(), datum: *datum, bounds: drawn.bounds, attrs: &drawn.node.attrs, shape: &drawn.node.shape, map: drawn.map, drawn }
        })
        .collect()
}

/// The measured box of every `Role::Label` text under `nodes` that has one, in the frame of the
/// slice given, in paint order.
pub fn labels(nodes: &[Node]) -> Vec<Bounds> {
    walk(nodes)
        .filter(|(node, _)| matches!(node.role, Role::Label))
        .filter_map(|(node, map)| Some(node.bounds()?.transformed(&map)))
        .collect()
}

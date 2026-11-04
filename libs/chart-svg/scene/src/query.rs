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
        if !matches!(node.shape, Shape::Defs(_) | Shape::ClipPath(_) | Shape::Pattern { .. }) {
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

/// A mark and the box it covers in the frame of the slice [`marks`] was given.
#[derive(Clone, Debug, PartialEq)]
pub struct MarkBox<'a> {
    pub layer: usize,
    pub series: Option<&'a str>,
    pub datum: Option<usize>,
    pub bounds: Bounds,
    /// The mark node's own presentation attributes.
    pub attrs: &'a Attrs,
    /// The attributes of every node it is drawn inside, outermost first.
    ancestors: Vec<&'a Attrs>,
    /// The mark node's own geometry, in its own frame.
    pub shape: &'a Shape,
    /// The map from that frame to the frame of the slice given.
    pub map: Affine,
}

impl<'a> MarkBox<'a> {
    fn nearest<T>(&self, get: impl Fn(&'a Attrs) -> Option<T>) -> Option<T> {
        std::iter::once(self.attrs).chain(self.ancestors.iter().rev().copied()).find_map(get)
    }

    /// The text of presentation attribute `name`: the mark's own, else the nearest ancestor's
    /// (SVG inherits `fill` and `stroke`); `None` where none is set.
    pub fn paint(&self, name: &str) -> Option<&'a str> {
        self.nearest(|attrs| attrs.get(name))
    }

    /// The number of presentation attribute `name`, inherited like [`MarkBox::paint`].
    pub fn paint_num(&self, name: &str) -> Option<f64> {
        self.nearest(|attrs| attrs.get_num(name))
    }
}

/// Every `Role::Mark` under `nodes` that draws something, in paint order.
pub fn marks(nodes: &[Node]) -> Vec<MarkBox<'_>> {
    fn visit<'a>(node: &'a Node, map: Affine, ancestors: &mut Vec<&'a Attrs>, out: &mut Vec<MarkBox<'a>>) {
        if let Role::Mark { layer, series, datum } = &node.role {
            if let Some(bounds) = node.bounds() {
                out.push(MarkBox {
                    layer: *layer,
                    series: series.as_deref(),
                    datum: *datum,
                    bounds: bounds.transformed(&map),
                    attrs: &node.attrs,
                    ancestors: ancestors.clone(),
                    shape: &node.shape,
                    map,
                });
            }
        }
        // Definitions and clip shapes are not drawn where they sit.
        if !matches!(node.shape, Shape::Defs(_) | Shape::ClipPath(_) | Shape::Pattern { .. }) {
            ancestors.push(&node.attrs);
            for child in node.children() {
                visit(child, map.then_inner(&child.affine()), ancestors, out);
            }
            ancestors.pop();
        }
    }
    let mut out = Vec::new();
    for node in nodes {
        visit(node, node.affine(), &mut Vec::new(), &mut out);
    }
    out
}

/// The measured box of every `Role::Label` text under `nodes` that has one, in the frame of the
/// slice given, in paint order.
pub fn labels(nodes: &[Node]) -> Vec<Bounds> {
    walk(nodes)
        .filter(|(node, _)| matches!(node.role, Role::Label))
        .filter_map(|(node, map)| Some(node.bounds()?.transformed(&map)))
        .collect()
}

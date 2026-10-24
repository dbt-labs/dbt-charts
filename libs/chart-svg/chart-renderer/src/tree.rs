//! Builds a node tree in document order, for a family whose layout opens and
//! closes elements as it walks its data (the table, whose rows, cells and
//! pagination nest several levels deep). `close` names the element it expects
//! so a mismatched walk fails loudly. Text, titles and attributes are quick-xml
//! escaped (`Escape::Quick`).

use scene::{Attrs, Chars, Escape, Extent, Node, TextBlock};

/// An element that holds children, named so `close` can check the walk.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Open {
    Svg,
    Group,
    Link,
    Text,
    Tspan,
    Defs,
    ClipPath,
    Script,
}

/// The elements opened and not yet closed, innermost last, over the finished
/// siblings at the root.
#[derive(Default)]
pub struct Tree {
    root: Vec<Node>,
    open: Vec<(Open, Attrs, Vec<Node>)>,
    extent: Option<Extent>,
}

fn chars(text: &str) -> Chars {
    Chars::new(text, Escape::Quick)
}

impl Tree {
    pub fn new() -> Self {
        Self::default()
    }

    fn push(&mut self, node: Node) {
        match self.open.last_mut() {
            Some((_, _, children)) => children.push(node),
            None => self.root.push(node),
        }
    }

    /// Opens the document root at `extent`, printed as Rust's `Display` (a
    /// whole value has no `.0`).
    pub fn open_svg(&mut self, extent: Extent) {
        self.extent = Some(extent);
        self.open.push((Open::Svg, Attrs::new(), Vec::new()));
    }

    pub fn open(&mut self, kind: Open, attrs: Attrs) {
        assert_ne!(kind, Open::Svg, "the root opens with open_svg");
        self.open.push((kind, attrs, Vec::new()));
    }

    pub fn close(&mut self, kind: Open) {
        let (top, attrs, children) = self
            .open
            .pop()
            .unwrap_or_else(|| panic!("close {kind:?} with nothing open"));
        assert_eq!(top, kind, "closed {kind:?} while {top:?} was open");
        let node = match kind {
            Open::Svg => {
                let extent = self.extent.take().expect("open_svg sets the extent");
                let (width, height) = (extent.width.to_string(), extent.height.to_string());
                let attrs = Attrs::new()
                    .quick("xmlns", "http://www.w3.org/2000/svg")
                    .quick("width", width.as_str())
                    .quick("height", height.as_str())
                    .quick("viewBox", format!("0 0 {width} {height}"));
                Node::Svg { extent, attrs, children }
            }
            Open::Group => Node::Group { attrs, children },
            Open::Link => Node::Link { attrs, children },
            Open::Text => Node::TextRuns { attrs, children },
            Open::Tspan => Node::TspanRuns { attrs, children },
            Open::Defs => Node::Defs(children),
            Open::ClipPath => Node::ClipPath { attrs, children },
            Open::Script => Node::Script { attrs, children },
        };
        self.push(node);
    }

    pub fn rect(&mut self, attrs: Attrs) {
        self.push(Node::Rect(attrs));
    }

    pub fn line(&mut self, attrs: Attrs) {
        self.push(Node::Line(attrs));
    }

    pub fn circle(&mut self, attrs: Attrs) {
        self.push(Node::Circle(attrs));
    }

    pub fn polygon(&mut self, attrs: Attrs) {
        self.push(Node::Polygon(attrs));
    }

    pub fn polyline(&mut self, attrs: Attrs) {
        self.push(Node::Polyline(attrs));
    }

    pub fn text_el(&mut self, attrs: Attrs, text: &str) {
        self.push(Node::Text(TextBlock { attrs, text: chars(text) }));
    }

    pub fn tspan(&mut self, attrs: Attrs, text: &str) {
        self.push(Node::Tspan(TextBlock { attrs, text: chars(text) }));
    }

    pub fn title(&mut self, text: &str) {
        self.push(Node::Title(chars(text)));
    }

    pub fn text(&mut self, text: &str) {
        self.push(Node::Chars(chars(text)));
    }

    pub fn cdata(&mut self, text: &str) {
        self.push(Node::Cdata(text.to_string()));
    }

    pub fn extend(&mut self, nodes: Vec<Node>) {
        nodes.into_iter().for_each(|node| self.push(node));
    }

    pub fn finish(self) -> Vec<Node> {
        assert!(self.open.is_empty(), "an element was left open");
        self.root
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use print::print;

    #[test]
    fn elements_nest_in_the_order_they_open() {
        let mut tree = Tree::new();
        tree.open_svg(Extent { width: 10.0, height: 2.5 });
        tree.open(Open::Group, Attrs::new().quick("class", "r"));
        tree.open(Open::Link, Attrs::new().quick("href", "/x"));
        tree.text_el(Attrs::new().quick("x", "1"), "a<b");
        tree.close(Open::Link);
        tree.rect(Attrs::new().quick("y", "2"));
        tree.close(Open::Group);
        tree.close(Open::Svg);
        assert_eq!(
            print(&tree.finish()),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"2.5\" viewBox=\"0 0 10 2.5\"><g class=\"r\"><a href=\"/x\"><text x=\"1\">a&lt;b</text></a><rect y=\"2\"/></g></svg>"
        );
    }

    #[test]
    #[should_panic(expected = "closed Link while Group was open")]
    fn closing_the_wrong_element_panics() {
        let mut tree = Tree::new();
        tree.open(Open::Group, Attrs::new());
        tree.close(Open::Link);
    }
}

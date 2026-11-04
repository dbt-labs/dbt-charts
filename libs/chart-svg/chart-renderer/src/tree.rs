//! Builds a node tree in document order, for a family whose layout opens and
//! closes elements as it walks its data (the table, whose rows, cells and
//! pagination nest several levels deep). `close` names the element it expects
//! so a mismatched walk fails loudly. Text, titles and attributes are quick-xml
//! escaped (`Escape::Quick`). Geometry goes in as numbers; `print` writes it.

use scene::{Attrs, Chars, Escape, Extent, Node, Role, Run, Shape, Transform};

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
}

/// Where a `<tspan>` sits: `x` and `y` place it absolutely, `dx` and `dy` move
/// it from where the text left off.
#[derive(Clone, Copy, Debug, Default)]
pub struct Span {
    pub x: Option<f64>,
    pub y: Option<f64>,
    pub dx: Option<f64>,
    pub dy: Option<f64>,
}

impl Span {
    pub fn at(x: f64, y: f64) -> Self {
        Self { x: Some(x), y: Some(y), ..Self::default() }
    }

    pub fn x(x: f64) -> Self {
        Self { x: Some(x), ..Self::default() }
    }

    pub fn dx(dx: Option<f64>) -> Self {
        Self { dx, ..Self::default() }
    }
}

/// An element opened and not yet closed. A text or tspan holds runs, any other
/// element holds nodes.
struct Frame {
    kind: Open,
    role: Role,
    transform: Vec<Transform>,
    attrs: Attrs,
    /// A text's anchor.
    at: (f64, f64),
    span: Span,
    children: Vec<Node>,
    runs: Vec<Run>,
}

/// The elements opened and not yet closed, innermost last, over the finished
/// siblings at the root.
#[derive(Default)]
pub struct Tree {
    root: Vec<Node>,
    open: Vec<Frame>,
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
            Some(frame) => {
                assert!(!matches!(frame.kind, Open::Text | Open::Tspan), "a text holds runs, not elements");
                frame.children.push(node);
            }
            None => self.root.push(node),
        }
    }

    fn push_run(&mut self, run: Run) {
        let frame = self.open.last_mut().expect("a run belongs inside a text");
        assert!(matches!(frame.kind, Open::Text | Open::Tspan), "a run belongs inside a text");
        frame.runs.push(run);
    }

    fn frame(&mut self, kind: Open, role: Role, transform: Vec<Transform>, attrs: Attrs, at: (f64, f64), span: Span) {
        self.open.push(Frame { kind, role, transform, attrs, at, span, children: Vec::new(), runs: Vec::new() });
    }

    /// Opens the document root at `extent`, printed as Rust's `Display` (a
    /// whole value has no `.0`).
    pub fn open_svg(&mut self, extent: Extent) {
        self.extent = Some(extent);
        self.frame(Open::Svg, Role::Chrome, Vec::new(), Attrs::new(), (0.0, 0.0), Span::default());
    }

    /// Opens a group, link, definitions or clip path.
    pub fn open(&mut self, kind: Open, role: Role, attrs: Attrs) {
        self.open_at(kind, role, Vec::new(), attrs);
    }

    /// [`Tree::open`] under `transform`.
    pub fn open_at(&mut self, kind: Open, role: Role, transform: Vec<Transform>, attrs: Attrs) {
        assert!(!matches!(kind, Open::Svg | Open::Text | Open::Tspan), "{kind:?} opens through its own method");
        self.frame(kind, role, transform, attrs, (0.0, 0.0), Span::default());
    }

    /// Opens a `<text>` anchored at `at`; its runs follow.
    pub fn open_text(&mut self, role: Role, at: (f64, f64), attrs: Attrs) {
        self.frame(Open::Text, role, Vec::new(), attrs, at, Span::default());
    }

    /// Opens a `<tspan>` inside a text, for runs of its own.
    pub fn open_tspan(&mut self, span: Span, attrs: Attrs) {
        self.frame(Open::Tspan, Role::Label, Vec::new(), attrs, (0.0, 0.0), span);
    }

    pub fn close(&mut self, kind: Open) {
        let frame = self.open.pop().unwrap_or_else(|| panic!("close {kind:?} with nothing open"));
        assert_eq!(frame.kind, kind, "closed {kind:?} while {:?} was open", frame.kind);
        let Frame { role, transform, attrs, at, span, children, runs, .. } = frame;
        let shape = match kind {
            Open::Svg => Shape::Svg { extent: self.extent.take().expect("open_svg sets the extent"), children },
            Open::Group => Shape::Group(children),
            Open::Link => Shape::Link(children),
            Open::Defs => Shape::Defs(children),
            Open::ClipPath => Shape::ClipPath(children),
            Open::Tspan => {
                let Span { x, y, dx, dy } = span;
                return self.push_run(Run::Tspan { x, y, dx, dy, attrs, runs });
            }
            Open::Text => Shape::Text { at, bounds: None, runs },
        };
        let mut node = Node::new(role, attrs, shape);
        node.transform = transform;
        self.push(node);
    }

    pub fn rect(&mut self, role: Role, (x, y, w, h): (f64, f64, f64, f64), attrs: Attrs) {
        self.push(Node::rect(role, (x, y, w, h), attrs));
    }

    /// A rect with corner radius `rx`.
    pub fn rounded(&mut self, role: Role, (x, y, w, h): (f64, f64, f64, f64), rx: f64, attrs: Attrs) {
        self.push(Node::new(role, attrs, Shape::Rect { x, y, w, h, rx }));
    }

    pub fn line(&mut self, role: Role, from: (f64, f64), to: (f64, f64), attrs: Attrs) {
        self.push(Node::line(role, from, to, attrs));
    }

    pub fn circle(&mut self, role: Role, at: (f64, f64), r: f64, attrs: Attrs) {
        self.push(Node::circle(role, at, r, attrs));
    }

    /// A `<polygon>` when `closed`, else a `<polyline>`.
    pub fn poly(&mut self, role: Role, points: Vec<(f64, f64)>, closed: bool, attrs: Attrs) {
        self.push(Node::poly(role, points, closed, attrs));
    }

    /// A `<text>` of one run of characters.
    pub fn text_el(&mut self, role: Role, at: (f64, f64), attrs: Attrs, text: &str) {
        self.open_text(role, at, attrs);
        self.text(text);
        self.close(Open::Text);
    }

    /// A `<tspan>` of one run of characters, inside a text.
    pub fn tspan(&mut self, span: Span, attrs: Attrs, text: &str) {
        self.open_tspan(span, attrs);
        self.text(text);
        self.close(Open::Tspan);
    }

    /// `<title>`: the full text a truncated run stands for, inside a text.
    pub fn title(&mut self, text: &str) {
        self.push_run(Run::Title(chars(text)));
    }

    /// Character data inside a text or tspan.
    pub fn text(&mut self, text: &str) {
        self.push_run(Run::Chars(chars(text)));
    }

    /// An inline `<script>` holding `source` as one CDATA section.
    pub fn script(&mut self, attrs: Attrs, source: &str) {
        self.push(Node::new(Role::Chrome, attrs, Shape::Script(source.to_string())));
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
        tree.open(Open::Group, Role::Chrome, Attrs::new().quick("class", "r"));
        tree.open(Open::Link, Role::Chrome, Attrs::new().quick("href", "/x"));
        tree.text_el(Role::Label, (1.0, 2.0), Attrs::new(), "a<b");
        tree.close(Open::Link);
        tree.rect(Role::Chrome, (0.0, 2.0, 1.0, 1.0), Attrs::new());
        tree.close(Open::Group);
        tree.close(Open::Svg);
        assert_eq!(
            print(&tree.finish()),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"2.5\" viewBox=\"0 0 10 2.5\"><g class=\"r\"><a href=\"/x\"><text x=\"1\" y=\"2\">a&lt;b</text></a><rect x=\"0\" y=\"2\" width=\"1\" height=\"1\"/></g></svg>"
        );
    }

    #[test]
    fn a_text_nests_tspans_and_titles_as_runs() {
        let mut tree = Tree::new();
        tree.open_text(Role::Label, (4.0, 5.0), Attrs::new().quick("fill", "red"));
        tree.title("full");
        tree.open_tspan(Span::at(4.0, 5.0), Attrs::new());
        tree.tspan(Span::dx(Some(1.5)), Attrs::new().quick("fill", "blue"), "g ");
        tree.text("one");
        tree.close(Open::Tspan);
        tree.close(Open::Text);
        assert_eq!(
            print(&tree.finish()),
            "<text x=\"4\" y=\"5\" fill=\"red\"><title>full</title><tspan x=\"4\" y=\"5\"><tspan dx=\"1.5\" fill=\"blue\">g </tspan>one</tspan></text>"
        );
    }

    #[test]
    #[should_panic(expected = "closed Link while Group was open")]
    fn closing_the_wrong_element_panics() {
        let mut tree = Tree::new();
        tree.open(Open::Group, Role::Chrome, Attrs::new());
        tree.close(Open::Link);
    }
}

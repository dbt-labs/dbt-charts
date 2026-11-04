//! The scene: typed nodes a family's `layout` emits and `print` serializes.
//! A node is an SVG primitive with its geometry as numbers and a role saying
//! what it is for; a chart concept (an axis, a legend, a bar) is built from
//! primitives by layout and never named here.
//!
//! Markup layout does not own (a markdown block, a custom chart's template output) rides as
//! an [`Element`] or its [`Chars`] among the typed nodes.

use std::borrow::Cow;

mod geom;
mod path;
mod query;

pub use geom::{compose, Affine, Bounds, Transform};
pub use path::{PathCommand, PathData};
pub use query::{drawn, labels, marks, walk, Drawn, MarkBox, Walk};

/// A width and height in user units.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Extent {
    pub width: f64,
    pub height: f64,
}

/// Which serializer's escaping `print` applies to a value. Each variant pins
/// the bytes of the serializer the engine's goldens came from.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Escape {
    /// Python's `html.escape`. An attribute value also writes the quotes and
    /// `\n`, `\r`, `\t` as references; character data escapes `& < >` only.
    Attr,
    /// Vega's SVG serializer: `font-family` keeps its apostrophes. Character
    /// data escapes `& < >`.
    Vega,
    /// quick-xml's escaping, for values and character data alike.
    Quick,
    /// mdsvg's markup: `html.escape` with quotes, and `\t`, `\n`, `\r` as
    /// character references, for character data as well as values.
    Mdsvg,
}

/// The attribute names that carry geometry. A typed node holds that geometry in
/// its [`Shape`] and [`Transform`]s, so none of these appears in its [`Attrs`].
pub const GEOMETRIC_ATTRS: [&str; 19] = [
    "x", "y", "width", "height", "d", "transform", "cx", "cy", "r", "rx", "ry", "x1", "y1", "x2", "y2", "points", "viewBox", "dx",
    "dy",
];

/// An attribute value: authored text, or a number `print` writes by its one
/// rule for numbers that are not geometry.
#[derive(Clone, Debug, PartialEq)]
pub enum AttrValue {
    Text(String),
    Num(f64),
    /// A coordinate or length: printed rounded like geometry.
    Geom(f64),
}

impl AttrValue {
    pub fn as_text(&self) -> Option<&str> {
        match self {
            AttrValue::Text(text) => Some(text),
            AttrValue::Num(_) | AttrValue::Geom(_) => None,
        }
    }
}

/// An element's presentation attributes in print order (fill, stroke, class,
/// aria, data-*). Text is escaped at print by the [`Escape`] it carries.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attrs(Vec<(Cow<'static, str>, AttrValue, Escape)>);

impl Attrs {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, AttrValue::Text(value.into()), Escape::Attr)
    }

    /// A numeric attribute (`stroke-width`, `opacity`, `font-size`).
    pub fn num(self, name: impl Into<Cow<'static, str>>, value: f64) -> Self {
        self.with(name, AttrValue::Num(value), Escape::Attr)
    }

    /// A coordinate or length attribute (`x`, `width`), written like geometry.
    pub fn geom(self, name: impl Into<Cow<'static, str>>, value: f64) -> Self {
        self.with(name, AttrValue::Geom(value), Escape::Attr)
    }

    /// [`Attrs::num`] when `value` is present, else unchanged.
    pub fn num_some(self, name: &'static str, value: Option<f64>) -> Self {
        match value {
            Some(value) => self.num(name, value),
            None => self,
        }
    }

    /// An attribute printed with Vega's escaping (`font-family`).
    pub fn vega(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, AttrValue::Text(value.into()), Escape::Vega)
    }

    /// An attribute printed with quick-xml's escaping.
    pub fn quick(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, AttrValue::Text(value.into()), Escape::Quick)
    }

    /// [`Attrs::quick`] when `value` is present, else unchanged.
    pub fn quick_some(self, name: &'static str, value: Option<impl Into<String>>) -> Self {
        match value {
            Some(value) => self.quick(name, value),
            None => self,
        }
    }

    fn with(mut self, name: impl Into<Cow<'static, str>>, value: AttrValue, escape: Escape) -> Self {
        self.0.push((name.into(), value, escape));
        self
    }

    /// `other`'s attributes after these.
    pub fn extend(mut self, other: Attrs) -> Self {
        self.0.extend(other.0);
        self
    }

    pub fn set_if(self, condition: bool, name: &'static str, value: impl Into<String>) -> Self {
        if condition {
            self.set(name, value)
        } else {
            self
        }
    }

    /// The text of the first attribute called `name`; `None` if it is absent or a number.
    pub fn get(&self, name: &str) -> Option<&str> {
        self.0.iter().find(|(n, _, _)| n == name).and_then(|(_, value, _)| value.as_text())
    }

    /// The number of the first attribute called `name`; `None` if it is absent or text.
    pub fn get_num(&self, name: &str) -> Option<f64> {
        self.0.iter().find(|(n, _, _)| n == name).and_then(|(_, value, _)| match value {
            AttrValue::Num(n) | AttrValue::Geom(n) => Some(*n),
            AttrValue::Text(_) => None,
        })
    }

    /// These attributes with each text value named in `names` replaced by `f(value)`.
    pub fn map_values(mut self, names: &[&str], f: impl Fn(&str) -> String) -> Self {
        for (name, value, _) in &mut self.0 {
            if let AttrValue::Text(text) = value {
                if names.contains(&name.as_ref()) {
                    *text = f(text);
                }
            }
        }
        self
    }

    /// `(name, value, escape)` in print order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &AttrValue, Escape)> {
        self.0.iter().map(|(name, value, escape)| (name.as_ref(), value, *escape))
    }

    /// The first attribute that names geometry, if any.
    pub fn geometric_name(&self) -> Option<&str> {
        self.iter().map(|(name, ..)| name).find(|name| GEOMETRIC_ATTRS.contains(name))
    }
}

/// Character data and the escaping its serializer applied.
#[derive(Clone, Debug, PartialEq)]
pub struct Chars {
    pub text: String,
    pub escape: Escape,
}

impl Chars {
    pub fn new(text: impl Into<String>, escape: Escape) -> Self {
        Self { text: text.into(), escape }
    }
}

/// What a node is for. Consumers read the role, never a class string.
#[derive(Clone, Debug, PartialEq)]
pub enum Role {
    /// A drawn datum or series. `layer` 0 is the base marks; `datum` is the
    /// bound row a mark draws, `None` for a series-level mark (a line, an area).
    Mark { layer: usize, series: Option<String>, datum: Option<usize> },
    Label,
    Axis,
    Grid,
    /// A reference rule across the plot: a zero or unity baseline, drawn heavier than a gridline.
    Rule,
    Legend,
    Annotation,
    Title,
    /// Background, frame, hover bands, ghosts, clip shapes and plain containers.
    Chrome,
}

/// A run inside a [`Shape::Text`].
#[derive(Clone, Debug, PartialEq)]
pub enum Run {
    Chars(Chars),
    /// `<title>`: the full text a truncated run stands for.
    Title(Chars),
    /// `<tspan>`; `x` and `y` place the run absolutely when present, `dx` and `dy` move it from where
    /// the text left off.
    Tspan { x: Option<f64>, y: Option<f64>, dx: Option<f64>, dy: Option<f64>, attrs: Attrs, runs: Vec<Run> },
}

impl Run {
    fn map_attrs(self, f: &impl Fn(Attrs) -> Attrs) -> Self {
        match self {
            Run::Tspan { x, y, dx, dy, attrs, runs } => {
                Run::Tspan { x, y, dx, dy, attrs: f(attrs), runs: runs.into_iter().map(|run| run.map_attrs(f)).collect() }
            }
            other => other,
        }
    }
}

/// A node's geometry, `f64` in user units.
#[derive(Clone, Debug, PartialEq)]
pub enum Shape {
    /// `rx` 0 is square corners.
    Rect { x: f64, y: f64, w: f64, h: f64, rx: f64 },
    Circle { cx: f64, cy: f64, r: f64 },
    Line { x1: f64, y1: f64, x2: f64, y2: f64 },
    Path(PathData),
    /// `<polygon>` when `closed`, else `<polyline>`.
    Poly { points: Vec<(f64, f64)>, closed: bool },
    /// The anchor point, the box layout measured for the text (`None` until a
    /// layout measures it) and its runs; no runs prints an empty `<text/>`.
    Text { at: (f64, f64), bounds: Option<Bounds>, runs: Vec<Run> },
    Group(Vec<Node>),
    /// `<a>`: the href, in the attributes, wraps the datum's mark.
    Link(Vec<Node>),
    Defs(Vec<Node>),
    /// `<clipPath>`: what a group's `clip-path="url(#id)"` clips to.
    ClipPath(Vec<Node>),
    /// `<pattern>` in user space: the `w` by `h` tile its children paint, placed
    /// by `transform` (`patternTransform`) when present, that a `fill="url(#id)"`
    /// repeats; the id rides in the attributes.
    Pattern { w: f64, h: f64, transform: Option<Transform>, children: Vec<Node> },
    /// `<linearGradient>`: the vector from `from` to `to` in the painted box's unit space and the
    /// `(offset, color)` stops along it; the id is in the attributes.
    LinearGradient { from: (f64, f64), to: (f64, f64), stops: Vec<(f64, String)> },
    /// The document root; `width`, `height` and `viewBox` print from `extent`.
    Svg { extent: Extent, children: Vec<Node> },
    /// `<script>`: the source, written as one CDATA section.
    Script(String),
    /// A generic element among typed nodes: markup whose shape layout does not own.
    Element(Element),
    /// Character data between elements (the newlines a serializer writes).
    Chars(Chars),
}

#[derive(Clone, Debug, PartialEq)]
pub struct Node {
    pub role: Role,
    /// Applies left to right, as SVG reads a `transform` list.
    pub transform: Vec<Transform>,
    pub attrs: Attrs,
    pub shape: Shape,
}

impl Node {
    /// A typed node. Panics if `attrs` names geometry (it belongs in the
    /// shape) or if a mark has no geometry to measure: both are bugs in the
    /// layout that built the node.
    pub fn new(role: Role, attrs: Attrs, shape: Shape) -> Self {
        let mut run_attrs = Vec::new();
        if let Shape::Text { runs, .. } = &shape {
            for run in runs {
                if let Run::Tspan { attrs, .. } = run {
                    run_attrs.push(attrs);
                }
            }
        }
        if let Some(name) = std::iter::once(&attrs).chain(run_attrs).find_map(Attrs::geometric_name) {
            panic!("attribute `{name}` is geometry: it belongs in the node's shape or transform");
        }
        if matches!(role, Role::Mark { .. }) {
            match &shape {
                Shape::Rect { .. } | Shape::Circle { .. } | Shape::Line { .. } | Shape::Path(_) | Shape::Poly { .. } => {}
                Shape::Text { bounds: Some(_), .. } => {}
                Shape::Text { .. } => panic!("a text mark needs its measured box, or `marks` cannot place it"),
                other => panic!("a mark is a primitive with geometry, not {other:?}"),
            }
        }
        Self { role, transform: Vec::new(), attrs, shape }
    }

    pub fn with_transform(mut self, transform: Transform) -> Self {
        self.transform.push(transform);
        self
    }

    pub fn translate(self, x: f64, y: f64) -> Self {
        self.with_transform(Transform::Translate { x, y })
    }

    pub fn rect(role: Role, (x, y, w, h): (f64, f64, f64, f64), attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Rect { x, y, w, h, rx: 0.0 })
    }

    pub fn line(role: Role, (x1, y1): (f64, f64), (x2, y2): (f64, f64), attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Line { x1, y1, x2, y2 })
    }

    pub fn circle(role: Role, (cx, cy): (f64, f64), r: f64, attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Circle { cx, cy, r })
    }

    pub fn poly(role: Role, points: Vec<(f64, f64)>, closed: bool, attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Poly { points, closed })
    }

    pub fn path(role: Role, path: PathData, attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Path(path))
    }

    pub fn group(role: Role, attrs: Attrs, children: Vec<Node>) -> Self {
        Self::new(role, attrs, Shape::Group(children))
    }

    /// A text run at `at`, its content escaped as `Escape::Attr` escapes character data.
    pub fn text(role: Role, at: (f64, f64), attrs: Attrs, text: impl Into<String>) -> Self {
        let runs = vec![Run::Chars(Chars::new(text, Escape::Attr))];
        Self::new(role, attrs, Shape::Text { at, bounds: None, runs })
    }

    /// `<text/>`: a label with no content that still stands for its datum.
    pub fn empty_text(role: Role, at: (f64, f64), attrs: Attrs) -> Self {
        Self::new(role, attrs, Shape::Text { at, bounds: None, runs: Vec::new() })
    }

    /// The characters of a text node's top-level runs; `None` for any other shape.
    pub fn text_content(&self) -> Option<String> {
        let Shape::Text { runs, .. } = &self.shape else { return None };
        Some(runs.iter().filter_map(|run| if let Run::Chars(chars) = run { Some(chars.text.as_str()) } else { None }).collect())
    }

    /// The box layout measured for this text.
    pub fn with_text_bounds(mut self, measured: Bounds) -> Self {
        let Shape::Text { bounds, .. } = &mut self.shape else { panic!("only a text node has a measured box") };
        *bounds = Some(measured);
        self
    }

    /// Markup layout does not own, as written.
    pub fn element(element: Element) -> Self {
        Self::new(Role::Chrome, Attrs::new(), Shape::Element(element))
    }

    pub fn chars(chars: Chars) -> Self {
        Self::new(Role::Chrome, Attrs::new(), Shape::Chars(chars))
    }

    /// This node and every node beneath it with its attributes passed through `f`: a typed node's
    /// own `attrs` (and a text node's `Tspan` runs).
    pub fn map_attrs(mut self, f: &impl Fn(Attrs) -> Attrs) -> Self {
        let all = |children: Vec<Node>| -> Vec<Node> { children.into_iter().map(|child| child.map_attrs(f)).collect() };
        self.attrs = f(self.attrs);
        self.shape = match self.shape {
            Shape::Group(children) => Shape::Group(all(children)),
            Shape::Link(children) => Shape::Link(all(children)),
            Shape::Text { at, bounds, runs } => Shape::Text { at, bounds, runs: runs.into_iter().map(|run| run.map_attrs(f)).collect() },
            other => other,
        };
        self
    }

    /// The transforms composed into one map from this node's frame to its parent's.
    pub fn affine(&self) -> Affine {
        compose(&self.transform)
    }

    /// The nodes drawn inside this one, in paint order.
    pub fn children(&self) -> &[Node] {
        match &self.shape {
            Shape::Group(children) | Shape::Link(children) | Shape::Defs(children) | Shape::ClipPath(children) => children,
            Shape::Pattern { children, .. } => children,
            Shape::Svg { children, .. } => children,
            _ => &[],
        }
    }

    /// The geometric box in this node's own frame (before its transform), no
    /// stroke: path extrema come from the commands, text from its measured box.
    /// `None` for a node that draws nothing measurable (definitions, markup, a path with no point, text with no measured box).
    pub fn bounds(&self) -> Option<Bounds> {
        match &self.shape {
            Shape::Rect { x, y, w, h, .. } => Some(Bounds { x1: *x, y1: *y, x2: x + w, y2: y + h }),
            Shape::Circle { cx, cy, r } => Some(Bounds { x1: cx - r, y1: cy - r, x2: cx + r, y2: cy + r }),
            Shape::Line { x1, y1, x2, y2 } => Bounds::of_points([(*x1, *y1), (*x2, *y2)]),
            Shape::Path(path) => path.bounds(),
            Shape::Poly { points, .. } => Bounds::of_points(points.iter().copied()),
            Shape::Text { bounds, .. } => *bounds,
            Shape::Group(children) | Shape::Link(children) | Shape::Svg { children, .. } => children
                .iter()
                .filter_map(|child| child.bounds().map(|b| b.transformed(&child.affine())))
                .reduce(Bounds::union),
            Shape::Defs(_) | Shape::ClipPath(_) | Shape::Pattern { .. } | Shape::LinearGradient { .. } | Shape::Script(_) | Shape::Element(_) | Shape::Chars(_) => None,
        }
    }
}

/// The extent of the document root when `nodes` is exactly that one root.
pub fn root_extent(nodes: &[Node]) -> Option<Extent> {
    match nodes {
        [Node { shape: Shape::Svg { extent, .. }, .. }] => Some(*extent),
        _ => None,
    }
}

/// A generic SVG element, for markup whose shape layout does not own: a custom
/// chart's checked template output, with tag and attribute names it chose.
#[derive(Clone, Debug, PartialEq)]
pub struct Element {
    pub tag: String,
    pub attrs: Attrs,
    pub children: Vec<Child>,
}

/// Text and elements interleave inside an element (`a<tspan>b</tspan>c`), so
/// an element's text is a child, not a field.
#[derive(Clone, Debug, PartialEq)]
pub enum Child {
    Text(Chars),
    Element(Element),
}

#[cfg(test)]
mod tests;

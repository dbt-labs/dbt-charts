//! The scene: typed nodes a family's `layout` emits and `print` serializes.
//! Nodes are SVG primitives only: a chart concept (an axis, a legend, a bar)
//! is built from them by layout and never named here.

use std::borrow::Cow;

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

/// An element's attributes in print order. Values are authored text, escaped at
/// print; numbers arrive already formatted by the layout that owns their
/// precision.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attrs(Vec<(Cow<'static, str>, String, Escape)>);

impl Attrs {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, value, Escape::Attr)
    }

    /// An attribute printed with Vega's escaping (`font-family`).
    pub fn vega(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, value, Escape::Vega)
    }

    /// An attribute printed with quick-xml's escaping.
    pub fn quick(self, name: impl Into<Cow<'static, str>>, value: impl Into<String>) -> Self {
        self.with(name, value, Escape::Quick)
    }

    fn with(mut self, name: impl Into<Cow<'static, str>>, value: impl Into<String>, escape: Escape) -> Self {
        self.0.push((name.into(), value.into(), escape));
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

    /// `(name, value, escape)` in print order.
    pub fn iter(&self) -> impl Iterator<Item = (&str, &str, Escape)> {
        self.0.iter().map(|(name, value, escape)| (name.as_ref(), value.as_str(), *escape))
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

/// A run of text. Layout owns its position and any truncation; print writes
/// `text` as given.
#[derive(Clone, Debug, PartialEq)]
pub struct TextBlock {
    pub attrs: Attrs,
    pub text: Chars,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Node {
    /// The document root. `extent` is the size the layout drew; `attrs` carry
    /// the `xmlns`, `width`, `height` and `viewBox` it printed from it.
    Svg { extent: Extent, attrs: Attrs, children: Vec<Node> },
    Group { attrs: Attrs, children: Vec<Node> },
    Rect(Attrs),
    Path(Attrs),
    Text(TextBlock),
    /// Character data between elements (the newlines a serializer writes).
    Chars(Chars),
    Defs(Vec<Node>),
    /// `<a>`: the href wraps the datum's mark.
    Link { attrs: Attrs, children: Vec<Node> },
    /// `<clipPath>`: what a group's `clip-path="url(#id)"` clips to.
    ClipPath { attrs: Attrs, children: Vec<Node> },
    LinearGradient { attrs: Attrs, stops: Vec<Node> },
    Stop(Attrs),
    /// A generic element among typed nodes: markup whose shape layout does not
    /// own (mdsvg's markdown output).
    Element(Element),
}

impl Node {
    pub fn group(attrs: Attrs, children: Vec<Node>) -> Self {
        Self::Group { attrs, children }
    }

    /// A text run escaped as `Escape::Attr` escapes character data.
    pub fn text(attrs: Attrs, text: impl Into<String>) -> Self {
        Self::Text(TextBlock { attrs, text: Chars::new(text, Escape::Attr) })
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

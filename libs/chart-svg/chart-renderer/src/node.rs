//! The typed node tree a family's `layout` emits and [`crate::print`]
//! serializes. Nodes are SVG primitives only: a chart concept (an axis, a
//! legend, a bar) is built from them by layout and never named here.

/// Which escaper the printer applies to an attribute value. The engine's
/// goldens pin two: Vega's serializer for font families, `html.escape` for
/// everything else.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Escape {
    Attr,
    Vega,
}

/// An element's attributes in print order. Values are authored text, escaped
/// at print; numbers arrive already formatted by the layout that owns their
/// precision.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Attrs(pub(crate) Vec<(&'static str, String, Escape)>);

impl Attrs {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn set(mut self, name: &'static str, value: impl Into<String>) -> Self {
        self.0.push((name, value.into(), Escape::Attr));
        self
    }

    /// An attribute printed with Vega's escaping (`font-family`).
    pub fn vega(mut self, name: &'static str, value: impl Into<String>) -> Self {
        self.0.push((name, value.into(), Escape::Vega));
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
}

/// A run of text. Layout owns its position and any truncation; print writes
/// `text` as given.
#[derive(Clone, Debug, PartialEq)]
pub struct TextBlock {
    pub attrs: Attrs,
    pub text: String,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Node {
    Group { attrs: Attrs, children: Vec<Node> },
    Rect(Attrs),
    Path(Attrs),
    Text(TextBlock),
    Defs(Vec<Node>),
    LinearGradient { attrs: Attrs, stops: Vec<Node> },
    Stop(Attrs),
}

impl Node {
    pub fn group(attrs: Attrs, children: Vec<Node>) -> Self {
        Self::Group { attrs, children }
    }

    pub fn text(attrs: Attrs, text: impl Into<String>) -> Self {
        Self::Text(TextBlock { attrs, text: text.into() })
    }
}

//! The one serializer for [`scene::Node`]. Printing is pure and decision-free:
//! every attribute, its order and its text arrive in the node; nothing here
//! measures, defaults, or reads a sibling or parent. Each value is escaped by
//! the [`Escape`] it carries: the goldens pin the bytes of four serializers
//! (Python's `html.escape`, Vega's, quick-xml's, mdsvg's) and they differ on
//! quotes and whitespace.
//!
//! The escapers are public for the families that still write markup strings.

use std::borrow::Cow;

use quick_xml::events::attributes::Attribute;
use scene::{Attrs, Chars, Child, Element, Escape, Node};

pub fn print(nodes: &[Node]) -> String {
    let mut out = String::new();
    for node in nodes {
        node_into(&mut out, node);
    }
    out
}

/// An element's content: text and elements, with no tag around them.
pub fn print_content(children: &[Child]) -> String {
    let mut out = String::new();
    for child in children {
        child_into(&mut out, child);
    }
    out
}

/// `s` with each character `escape` maps replaced by its reference, in one
/// pass; borrowed when nothing needs escaping.
fn escape_with(s: &str, escape: impl Fn(char) -> Option<&'static str>) -> Cow<'_, str> {
    let Some(first) = s.find(|c| escape(c).is_some()) else {
        return Cow::Borrowed(s);
    };
    let mut out = String::with_capacity(s.len() + 16);
    out.push_str(&s[..first]);
    for c in s[first..].chars() {
        match escape(c) {
            Some(reference) => out.push_str(reference),
            None => out.push(c),
        }
    }
    Cow::Owned(out)
}

fn xml_reference(c: char) -> Option<&'static str> {
    match c {
        '&' => Some("&amp;"),
        '<' => Some("&lt;"),
        '>' => Some("&gt;"),
        _ => None,
    }
}

/// Character data with `&`, `<` and `>` escaped; quotes and newlines stay.
pub fn xml_escape(s: &str) -> Cow<'_, str> {
    escape_with(s, xml_reference)
}

/// Text content escaped the way production's bare `html.escape(text)` does:
/// `xml_escape` plus both quote characters, and no whitespace references — a
/// text node keeps its literal newline, an attribute value does not.
pub fn html_escape(s: &str) -> Cow<'_, str> {
    escape_with(s, |c| match c {
        '"' => Some("&quot;"),
        '\'' => Some("&#x27;"),
        _ => xml_reference(c),
    })
}

/// The one attribute-value escaper — production's `svg_utils.escape_attr`
/// (`html.escape(s, quote=True)`, then newline, CR, and tab as numeric
/// character references). Every authored string that lands in an attribute
/// value goes through it; character data never needs the quotes escaped, so it
/// uses `xml_escape`.
pub fn xml_escape_attr(s: &str) -> String {
    escape_with(s, |c| match c {
        '"' => Some("&quot;"),
        '\'' => Some("&#x27;"),
        '\n' => Some("&#10;"),
        '\r' => Some("&#13;"),
        '\t' => Some("&#9;"),
        _ => xml_reference(c),
    })
    .into_owned()
}

/// An attribute value as Vega's SVG serializer writes it (vega-scenegraph
/// `src/util/markup.js`, `attrText`): `&`, `<`, `>`, and `"` escaped, tab,
/// newline, and CR as hex references, `'` left bare. A Vega-drawn chart body's
/// `font-family` keeps its single-quoted stack byte-for-byte; every other
/// attribute value, and every Python-drawn one, uses `xml_escape_attr`.
pub fn vega_escape_attr(s: &str) -> Cow<'_, str> {
    escape_with(s, |c| match c {
        '"' => Some("&quot;"),
        '\t' => Some("&#x9;"),
        '\n' => Some("&#xA;"),
        '\r' => Some("&#xD;"),
        _ => xml_reference(c),
    })
}

fn attr_value(value: &str, name: &str, escape: Escape) -> String {
    match escape {
        Escape::Attr | Escape::Mdsvg => xml_escape_attr(value),
        Escape::Vega => vega_escape_attr(value).into_owned(),
        Escape::Quick => Attribute::from((name, value)).value.into_owned(),
    }
}

fn chars_into(out: &mut String, chars: &Chars) {
    match chars.escape {
        Escape::Attr | Escape::Vega => out.push_str(&xml_escape(&chars.text)),
        Escape::Quick => out.push_str(&quick_xml::escape::escape(&chars.text)),
        Escape::Mdsvg => out.push_str(&xml_escape_attr(&chars.text)),
    }
}

fn attrs_into(out: &mut String, attrs: &Attrs) {
    for (name, value, escape) in attrs.iter() {
        out.push(' ');
        out.push_str(name);
        out.push_str("=\"");
        out.push_str(&attr_value(value, name, escape));
        out.push('"');
    }
}

fn element_into(out: &mut String, tag: &str, attrs: &Attrs, children: &[Node]) {
    out.push('<');
    out.push_str(tag);
    attrs_into(out, attrs);
    out.push('>');
    for child in children {
        node_into(out, child);
    }
    out.push_str("</");
    out.push_str(tag);
    out.push('>');
}

fn empty_into(out: &mut String, tag: &str, attrs: &Attrs) {
    out.push('<');
    out.push_str(tag);
    attrs_into(out, attrs);
    out.push_str("/>");
}

fn node_into(out: &mut String, node: &Node) {
    match node {
        Node::Svg { attrs, children, .. } => element_into(out, "svg", attrs, children),
        Node::Group { attrs, children } => element_into(out, "g", attrs, children),
        Node::Rect(attrs) => empty_into(out, "rect", attrs),
        Node::Path(attrs) => empty_into(out, "path", attrs),
        Node::Stop(attrs) => empty_into(out, "stop", attrs),
        Node::Text(block) => {
            out.push_str("<text");
            attrs_into(out, &block.attrs);
            out.push('>');
            chars_into(out, &block.text);
            out.push_str("</text>");
        }
        Node::Chars(chars) => chars_into(out, chars),
        Node::Link { attrs, children } => element_into(out, "a", attrs, children),
        Node::ClipPath { attrs, children } => element_into(out, "clipPath", attrs, children),
        Node::Defs(children) => element_into(out, "defs", &Attrs::new(), children),
        Node::LinearGradient { attrs, stops } => element_into(out, "linearGradient", attrs, stops),
        Node::Element(element) => generic_into(out, element),
    }
}

fn generic_into(out: &mut String, Element { tag, attrs, children }: &Element) {
    out.push('<');
    out.push_str(tag);
    attrs_into(out, attrs);
    if children.is_empty() {
        out.push_str("/>");
        return;
    }
    out.push('>');
    for child in children {
        child_into(out, child);
    }
    out.push_str("</");
    out.push_str(tag);
    out.push('>');
}

fn child_into(out: &mut String, child: &Child) {
    match child {
        Child::Text(chars) => chars_into(out, chars),
        Child::Element(element) => generic_into(out, element),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use scene::Extent;

    #[test]
    fn attributes_print_in_authored_order() {
        let node = Node::Rect(Attrs::new().set("y", "2").set("x", "1").set("fill", "#fff"));
        assert_eq!(print(&[node]), "<rect y=\"2\" x=\"1\" fill=\"#fff\"/>");
    }

    #[test]
    fn a_group_prints_open_and_close_even_when_empty() {
        let node = Node::group(Attrs::new().set("class", "a"), vec![]);
        assert_eq!(print(&[node]), "<g class=\"a\"></g>");
    }

    #[test]
    fn nested_nodes_print_depth_first() {
        let node = Node::group(
            Attrs::new().set("class", "outer"),
            vec![Node::Path(Attrs::new().set("d", "M0,0")), Node::text(Attrs::new().set("x", "1"), "hi")],
        );
        assert_eq!(print(&[node]), "<g class=\"outer\"><path d=\"M0,0\"/><text x=\"1\">hi</text></g>");
    }

    #[test]
    fn attribute_values_use_html_escaping_and_text_leaves_quotes() {
        let node = Node::text(Attrs::new().set("fill", "a\"b'c<&>\n"), "t\"'<&>");
        assert_eq!(print(&[node]), "<text fill=\"a&quot;b&#x27;c&lt;&amp;&gt;&#10;\">t\"'&lt;&amp;&gt;</text>");
    }

    #[test]
    fn a_vega_attribute_keeps_apostrophes_and_references_whitespace() {
        let node = Node::text(Attrs::new().vega("font-family", "'A' \"B\"\t\n"), "");
        assert_eq!(print(&[node]), "<text font-family=\"'A' &quot;B&quot;&#x9;&#xA;\"></text>");
    }

    #[test]
    fn a_quick_attribute_and_text_escape_every_metacharacter() {
        let node = Node::Text(scene::TextBlock {
            attrs: Attrs::new().quick("font-family", "a \"b\" 'c' <&>\n"),
            text: Chars::new("t\"'<&>", Escape::Quick),
        });
        assert_eq!(
            print(&[node]),
            "<text font-family=\"a &quot;b&quot; &apos;c&apos; &lt;&amp;&gt;&#10;\">t&quot;&apos;&lt;&amp;&gt;</text>"
        );
    }

    #[test]
    fn a_link_wraps_its_children_and_a_clip_path_holds_its_shape() {
        let link = Node::Link {
            attrs: Attrs::new().set("href", "/a?b=1&c=2"),
            children: vec![Node::Path(Attrs::new().set("d", "M0,0"))],
        };
        let clip = Node::ClipPath {
            attrs: Attrs::new().set("id", "clip0"),
            children: vec![Node::Rect(Attrs::new().set("width", "5"))],
        };
        assert_eq!(
            print(&[link, clip]),
            "<a href=\"/a?b=1&amp;c=2\"><path d=\"M0,0\"/></a><clipPath id=\"clip0\"><rect width=\"5\"/></clipPath>"
        );
    }

    #[test]
    fn a_gradient_prints_its_defs_and_stops() {
        let node = Node::Defs(vec![Node::LinearGradient {
            attrs: Attrs::new().set("id", "g").set("x1", "0"),
            stops: vec![Node::Stop(Attrs::new().set("offset", "0").set("stop-color", "red"))],
        }]);
        assert_eq!(
            print(&[node]),
            "<defs><linearGradient id=\"g\" x1=\"0\"><stop offset=\"0\" stop-color=\"red\"/></linearGradient></defs>"
        );
    }

    #[test]
    fn the_root_prints_its_attributes_and_the_characters_between_children() {
        let root = Node::Svg {
            extent: Extent { width: 168.0, height: 82.5 },
            attrs: Attrs::new().quick("width", "168.0").quick("height", "82.5"),
            children: vec![
                Node::Chars(Chars::new("\n", Escape::Quick)),
                Node::group(Attrs::new().quick("opacity", "0.4"), vec![]),
            ],
        };
        assert_eq!(print(&[root]), "<svg width=\"168.0\" height=\"82.5\">\n<g opacity=\"0.4\"></g></svg>");
    }

    #[test]
    fn an_element_tree_prints_mixed_text_with_html_escaping() {
        let tspan = Element {
            tag: "tspan".into(),
            attrs: Attrs::new(),
            children: vec![Child::Text(Chars::new("b", Escape::Attr))],
        };
        let empty = Element { tag: "rect".into(), attrs: Attrs::new().set(String::from("x"), "1"), children: vec![] };
        let root = Element {
            tag: "text".into(),
            attrs: Attrs::new().set(String::from("title"), "it's \"q\" <&>\n").set("y", "2"),
            children: vec![
                Child::Text(Chars::new("a \"'<&>", Escape::Attr)),
                Child::Element(tspan),
                Child::Text(Chars::new("c", Escape::Attr)),
                Child::Element(empty),
            ],
        };
        assert_eq!(
            print_content(&[Child::Element(root)]),
            "<text title=\"it&#x27;s &quot;q&quot; &lt;&amp;&gt;&#10;\" y=\"2\">a \"'&lt;&amp;&gt;<tspan>b</tspan>c<rect x=\"1\"/></text>"
        );
    }

    #[test]
    fn mdsvg_text_escapes_the_quotes_and_whitespace_html_text_leaves() {
        let title = Element {
            tag: "title".into(),
            attrs: Attrs::new(),
            children: vec![
                Child::Text(Chars::new("a \"b\" 'c' <&>\n\t\r", Escape::Mdsvg)),
                Child::Text(Chars::new(" \"d\"", Escape::Attr)),
            ],
        };
        assert_eq!(
            print_content(&[Child::Element(title)]),
            "<title>a &quot;b&quot; &#x27;c&#x27; &lt;&amp;&gt;&#10;&#9;&#13; \"d\"</title>"
        );
        assert_eq!(
            print(&[Node::Chars(Chars::new("\t", Escape::Mdsvg))]),
            "&#9;"
        );
    }

    #[test]
    fn an_element_node_prints_inside_its_parent() {
        let rect = Element { tag: "rect".into(), attrs: Attrs::new().set("x", "1"), children: vec![] };
        let node = Node::group(Attrs::new(), vec![Node::Element(rect)]);
        assert_eq!(print(&[node]), "<g><rect x=\"1\"/></g>");
    }

    #[test]
    fn attribute_escape_is_production_escape_attr() {
        assert_eq!(xml_escape_attr("x\" onmouseover=\"alert(1)"), "x&quot; onmouseover=&quot;alert(1)");
        assert_eq!(xml_escape_attr("'A' <b> & c"), "&#x27;A&#x27; &lt;b&gt; &amp; c");
        assert_eq!(xml_escape_attr("a\nb\rc\td"), "a&#10;b&#13;c&#9;d");
    }

    #[test]
    fn vega_attribute_escape_is_vega_scenegraph_attr_text() {
        assert_eq!(
            vega_escape_attr("'A' \"b\" <c> & d\te\nf\rg"),
            "'A' &quot;b&quot; &lt;c&gt; &amp; d&#x9;e&#xA;f&#xD;g"
        );
    }

    #[test]
    fn text_escapes_only_the_three_markup_characters() {
        assert_eq!(xml_escape("a\"'\n"), "a\"'\n");
        assert!(matches!(xml_escape("plain"), Cow::Borrowed(_)));
        assert_eq!(xml_escape("&<>"), "&amp;&lt;&gt;");
        assert_eq!(html_escape("a\"'\n&"), "a&quot;&#x27;\n&amp;");
    }
}

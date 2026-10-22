//! The one SVG serializer for [`crate::node::Node`]. Printing is pure and
//! decision-free: every attribute, its order and its text arrive in the node;
//! nothing here measures, defaults, or reads a sibling. Escaping uses the
//! engine's byte-parity escapers in [`crate::svg`] (quick-xml's, in
//! [`crate::xml`], differ on quote characters, which the goldens pin).

use crate::node::{Attrs, Escape, Node};
use crate::svg::{vega_escape_attr, xml_escape, xml_escape_attr};

pub fn print(nodes: &[Node]) -> String {
    let mut out = String::new();
    for node in nodes {
        node_into(&mut out, node);
    }
    out
}

fn attrs_into(out: &mut String, attrs: &Attrs) {
    for (name, value, escape) in &attrs.0 {
        let value = match escape {
            Escape::Attr => xml_escape_attr(value),
            Escape::Vega => vega_escape_attr(value).into_owned(),
        };
        out.push(' ');
        out.push_str(name);
        out.push_str("=\"");
        out.push_str(&value);
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
        Node::Group { attrs, children } => element_into(out, "g", attrs, children),
        Node::Rect(attrs) => empty_into(out, "rect", attrs),
        Node::Path(attrs) => empty_into(out, "path", attrs),
        Node::Stop(attrs) => empty_into(out, "stop", attrs),
        Node::Text(block) => {
            out.push_str("<text");
            attrs_into(out, &block.attrs);
            out.push('>');
            out.push_str(&xml_escape(&block.text));
            out.push_str("</text>");
        }
        Node::Defs(children) => element_into(out, "defs", &Attrs::new(), children),
        Node::LinearGradient { attrs, stops } => element_into(out, "linearGradient", attrs, stops),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
}

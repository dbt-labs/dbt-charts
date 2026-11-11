//! The one serializer for [`scene::Node`]. Printing is pure and decision-free:
//! nothing here measures, defaults, or reads a sibling or parent.
//!
//! This is the only place a number becomes text, by two rules. Geometry (a
//! shape's coordinates, a translation, a path command) rounds to 0.01 user
//! units by its exact decimal value, as `format!("{:.2}")` does, and prints trimmed:
//! `12.5`, `3`, never `-0`. The root's extent is the drawn size callers read back, so it prints
//! as a plain number.
//! Any other number (an angle, a scale, an opacity, a stroke width) prints
//! shortest round-trip. Paths print one syntax, d3-path's compact form;
//! transforms one form, `translate(x,y) rotate(a)`; a typed node's attributes
//! print its transform, then its geometry, then its [`Attrs`] in build order.
//!
//! Each text value is escaped by the [`Escape`] it carries: the goldens pin the
//! bytes of four serializers (Python's `html.escape`, Vega's, quick-xml's,
//! mdsvg's) and they differ on quotes and whitespace.
//!
//! The escapers are public for the families that still write markup strings.

#![cfg_attr(
    not(test),
    deny(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::panic,
        clippy::indexing_slicing
    )
)]

use std::borrow::Cow;

use quick_xml::events::attributes::Attribute;
use scene::{AttrValue, Attrs, Chars, Child, Element, Escape, Node, PathCommand, PathData, Run, Shape, Transform};

/// `attrs` as an element's attribute run, each led by a space: the fragment a
/// caller that still writes an opening tag by hand splices in.
pub fn attributes(attrs: &Attrs) -> String {
    let mut out = String::new();
    attrs_into(&mut out, attrs);
    out
}

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

/// How far a printed coordinate can sit from the value it was computed from.
pub const GEOMETRY_TOLERANCE: f64 = 0.005;

/// A geometric coordinate as `print` writes it: rounded to 0.01 by its exact decimal value, and trimmed.
/// An infinity is a layout bug and panics. `NaN` prints as `NaN`: Vega's 0/0 for a normalized stack
/// with no total is output the goldens pin.
pub fn geometry(v: f64) -> String {
    assert!(!v.is_infinite(), "a coordinate must not be infinite");
    let fixed = format!("{v:.2}");
    match fixed.trim_end_matches('0').trim_end_matches('.') {
        "-0" | "" => "0".to_string(),
        trimmed => trimmed.to_string(),
    }
}

/// Any other number: shortest round-trip, with `-0` as `0`. A family that hashes a number into an
/// id hashes this text.
pub fn number(v: f64) -> String {
    assert!(!v.is_infinite(), "a number must not be infinite");
    if v == 0.0 {
        "0".to_string()
    } else {
        v.to_string()
    }
}

/// A path as d3-path writes it: `M12.5,3L4,0A5,5,0,0,1,9,2Z`, no spaces.
pub fn path_d(path: &PathData) -> String {
    let mut out = String::new();
    for command in path.commands() {
        match *command {
            PathCommand::M(x, y) => out.push_str(&format!("M{},{}", geometry(x), geometry(y))),
            PathCommand::L(x, y) => out.push_str(&format!("L{},{}", geometry(x), geometry(y))),
            PathCommand::C(x1, y1, x2, y2, x, y) => out.push_str(&format!(
                "C{},{},{},{},{},{}",
                geometry(x1),
                geometry(y1),
                geometry(x2),
                geometry(y2),
                geometry(x),
                geometry(y)
            )),
            PathCommand::A { rx, ry, rot, large, sweep, x, y } => out.push_str(&format!(
                "A{},{},{},{},{},{},{}",
                geometry(rx),
                geometry(ry),
                number(rot),
                u8::from(large),
                u8::from(sweep),
                geometry(x),
                geometry(y)
            )),
            PathCommand::Z => out.push('Z'),
        }
    }
    out
}

fn transform_text(transforms: &[Transform]) -> String {
    transforms
        .iter()
        .map(|t| match *t {
            Transform::Translate { x, y } => format!("translate({},{})", geometry(x), geometry(y)),
            Transform::Scale(k) => format!("scale({})", number(k)),
            Transform::Rotate { deg, about: None } => format!("rotate({})", number(deg)),
            Transform::Rotate { deg, about: Some((x, y)) } => {
                format!("rotate({} {} {})", number(deg), geometry(x), geometry(y))
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
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

fn attr_into(out: &mut String, name: &str, value: &str) {
    out.push(' ');
    out.push_str(name);
    out.push_str("=\"");
    out.push_str(value);
    out.push('"');
}

fn attrs_into(out: &mut String, attrs: &Attrs) {
    for (name, value, escape) in attrs.iter() {
        match value {
            AttrValue::Text(text) => attr_into(out, name, &attr_value(text, name, escape)),
            AttrValue::Num(n) => attr_into(out, name, &number(*n)),
            AttrValue::Geom(n) => attr_into(out, name, &geometry(*n)),
        }
    }
}

fn open_into(out: &mut String, tag: &str, geometry_attrs: &[(&str, String)], node: &Node) {
    out.push('<');
    out.push_str(tag);
    if !node.transform.is_empty() {
        attr_into(out, "transform", &transform_text(&node.transform));
    }
    for (name, value) in geometry_attrs {
        attr_into(out, name, value);
    }
    attrs_into(out, &node.attrs);
}

fn parent_into(out: &mut String, tag: &str, geometry_attrs: &[(&str, String)], node: &Node, children: &[Node]) {
    open_into(out, tag, geometry_attrs, node);
    out.push('>');
    for child in children {
        node_into(out, child);
    }
    out.push_str("</");
    out.push_str(tag);
    out.push('>');
}

fn leaf_into(out: &mut String, tag: &str, geometry_attrs: &[(&str, String)], node: &Node) {
    open_into(out, tag, geometry_attrs, node);
    out.push_str("/>");
}

fn runs_into(out: &mut String, runs: &[Run]) {
    for run in runs {
        match run {
            Run::Chars(chars) => chars_into(out, chars),
            Run::Title(chars) => {
                out.push_str("<title>");
                chars_into(out, chars);
                out.push_str("</title>");
            }
            Run::Tspan { x, y, dx, dy, attrs, runs } => {
                out.push_str("<tspan");
                for (name, value) in [("x", x), ("y", y), ("dx", dx), ("dy", dy)] {
                    if let Some(value) = value {
                        attr_into(out, name, &geometry(*value));
                    }
                }
                attrs_into(out, attrs);
                out.push('>');
                runs_into(out, runs);
                out.push_str("</tspan>");
            }
        }
    }
}

fn empty_into(out: &mut String, tag: &str, attrs: &Attrs) {
    out.push('<');
    out.push_str(tag);
    attrs_into(out, attrs);
    out.push_str("/>");
}

fn node_into(out: &mut String, node: &Node) {
    match &node.shape {
        Shape::Rect { x, y, w, h, rx } => {
            let mut geometry_attrs = vec![("x", geometry(*x)), ("y", geometry(*y)), ("width", geometry(*w)), ("height", geometry(*h))];
            if *rx > 0.0 {
                geometry_attrs.push(("rx", geometry(*rx)));
            }
            leaf_into(out, "rect", &geometry_attrs, node);
        }
        Shape::Circle { cx, cy, r } => {
            leaf_into(out, "circle", &[("cx", geometry(*cx)), ("cy", geometry(*cy)), ("r", geometry(*r))], node)
        }
        Shape::Line { x1, y1, x2, y2 } => leaf_into(
            out,
            "line",
            &[("x1", geometry(*x1)), ("y1", geometry(*y1)), ("x2", geometry(*x2)), ("y2", geometry(*y2))],
            node,
        ),
        Shape::Path(path) => leaf_into(out, "path", &[("d", path_d(path))], node),
        Shape::Poly { points, closed } => {
            let points = points.iter().map(|(x, y)| format!("{},{}", geometry(*x), geometry(*y))).collect::<Vec<_>>().join(" ");
            leaf_into(out, if *closed { "polygon" } else { "polyline" }, &[("points", points)], node);
        }
        Shape::Text { at, runs, .. } => {
            let at = [("x", geometry(at.0)), ("y", geometry(at.1))];
            if runs.is_empty() {
                leaf_into(out, "text", &at, node);
            } else {
                open_into(out, "text", &at, node);
                out.push('>');
                runs_into(out, runs);
                out.push_str("</text>");
            }
        }
        Shape::Group(children) => parent_into(out, "g", &[], node, children),
        Shape::Link(children) => parent_into(out, "a", &[], node, children),
        Shape::Defs(children) => parent_into(out, "defs", &[], node, children),
        Shape::ClipPath(children) => parent_into(out, "clipPath", &[], node, children),
        Shape::Mask { region: (x, y, w, h), children } => {
            let units = [("maskUnits", "userSpaceOnUse".to_string()), ("x", geometry(*x)), ("y", geometry(*y)), ("width", geometry(*w)), ("height", geometry(*h))];
            parent_into(out, "mask", &units, node, children)
        }
        Shape::Pattern { w, h, transform, children } => {
            let mut tile = vec![("width", geometry(*w)), ("height", geometry(*h))];
            tile.extend(transform.map(|t| ("patternTransform", transform_text(&[t]))));
            parent_into(out, "pattern", &tile, node, children)
        }
        Shape::LinearGradient { from, to, stops } => {
            let vector = [("x1", number(from.0)), ("y1", number(from.1)), ("x2", number(to.0)), ("y2", number(to.1))];
            open_into(out, "linearGradient", &vector, node);
            out.push('>');
            for (offset, color) in stops {
                empty_into(out, "stop", &Attrs::new().num("offset", *offset).set("stop-color", color.as_str()));
            }
            out.push_str("</linearGradient>");
        }
        Shape::Svg { extent, children } => {
            let (w, h) = (number(extent.width), number(extent.height));
            let geometry_attrs = [("xmlns", "http://www.w3.org/2000/svg".to_string()), ("width", w.clone()), ("height", h.clone()), ("viewBox", format!("0 0 {w} {h}"))];
            parent_into(out, "svg", &geometry_attrs, node, children);
        }
        Shape::Script(source) => {
            open_into(out, "script", &[], node);
            out.push_str(">\n<![CDATA[");
            out.push_str(&format!("\n{source}\n").replace("]]>", "]]]]><![CDATA[>"));
            out.push_str("]]>\n</script>");
        }
        Shape::Element(element) => generic_into(out, element),
        Shape::Chars(chars) => chars_into(out, chars),
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
    use scene::{Extent, Role};

    #[test]
    fn an_attribute_run_leads_each_attribute_with_a_space() {
        let attrs = Attrs::new().set("a", "1 & 2").vega("font-family", "'x'");
        assert_eq!(attributes(&attrs), " a=\"1 &amp; 2\" font-family=\"'x'\"");
        assert_eq!(attributes(&Attrs::new()), "");
    }

    #[test]
    fn attributes_print_in_authored_order() {
        let node = Node::group(Role::Chrome, Attrs::new().set("b", "2").set("a", "1").set("fill", "#fff"), vec![]);
        assert_eq!(print(&[node]), "<g b=\"2\" a=\"1\" fill=\"#fff\"></g>");
    }

    #[test]
    fn nested_nodes_print_depth_first() {
        let node = Node::group(
            Role::Chrome,
            Attrs::new().set("class", "outer"),
            vec![Node::path(Role::Chrome, PathData::new().move_to(0.0, 0.0), Attrs::new()), Node::text(Role::Label, (1.0, 2.0), Attrs::new(), "hi")],
        );
        assert_eq!(print(&[node]), "<g class=\"outer\"><path d=\"M0,0\"/><text x=\"1\" y=\"2\">hi</text></g>");
    }

    #[test]
    fn attribute_values_use_html_escaping_and_text_leaves_quotes() {
        let node = Node::text(Role::Label, (0.0, 0.0), Attrs::new().set("fill", "a\"b'c<&>\n"), "t\"'<&>");
        assert_eq!(print(&[node]), "<text x=\"0\" y=\"0\" fill=\"a&quot;b&#x27;c&lt;&amp;&gt;&#10;\">t\"'&lt;&amp;&gt;</text>");
    }

    #[test]
    fn a_vega_attribute_keeps_apostrophes_and_references_whitespace() {
        let node = Node::text(Role::Label, (0.0, 0.0), Attrs::new().vega("font-family", "'A' \"B\"\t\n"), "");
        assert_eq!(print(&[node]), "<text x=\"0\" y=\"0\" font-family=\"'A' &quot;B&quot;&#x9;&#xA;\"></text>");
    }

    #[test]
    fn a_quick_attribute_and_text_escape_every_metacharacter() {
        let runs = vec![Run::Chars(Chars::new("t\"'<&>", Escape::Quick))];
        let node = Node::new(
            Role::Label,
            Attrs::new().quick("font-family", "a \"b\" 'c' <&>\n"),
            Shape::Text { at: (0.0, 0.0), bounds: None, runs },
        );
        assert_eq!(
            print(&[node]),
            "<text x=\"0\" y=\"0\" font-family=\"a &quot;b&quot; &apos;c&apos; &lt;&amp;&gt;&#10;\">t&quot;&apos;&lt;&amp;&gt;</text>"
        );
    }

    #[test]
    fn text_runs_print_their_tspans_titles_and_the_characters_between() {
        let tspan = Run::Tspan {
            x: None,
            y: Some(30.5),
            dx: None,
            dy: None,
            attrs: Attrs::new().quick("font-family", "'Inter'"),
            runs: vec![Run::Chars(Chars::new("a<b", Escape::Quick))],
        };
        let runs = vec![Run::Title(Chars::new("it's", Escape::Quick)), Run::Chars(Chars::new("\n", Escape::Quick)), tspan];
        let node = Node::new(Role::Label, Attrs::new().quick("text-anchor", "start"), Shape::Text { at: (14.0, 0.0), bounds: None, runs });
        assert_eq!(
            print(&[node]),
            "<text x=\"14\" y=\"0\" text-anchor=\"start\"><title>it&apos;s</title>\n<tspan y=\"30.5\" font-family=\"&apos;Inter&apos;\">a&lt;b</tspan></text>"
        );
    }

    #[test]
    fn a_mask_prints_its_region_and_children() {
        let mask = Node::new(
            Role::Chrome,
            Attrs::new().set("id", "m"),
            Shape::Mask { region: (-1.0, -1.0, 12.0, 7.0), children: vec![Node::rect(Role::Chrome, (-1.0, -1.0, 12.0, 7.0), Attrs::new().set("fill", "#fff"))] },
        );
        assert_eq!(
            print(&[mask]),
            "<mask maskUnits=\"userSpaceOnUse\" x=\"-1\" y=\"-1\" width=\"12\" height=\"7\" id=\"m\"><rect x=\"-1\" y=\"-1\" width=\"12\" height=\"7\" fill=\"#fff\"/></mask>"
        );
    }

    #[test]
    fn a_link_wraps_its_children_and_a_clip_path_holds_its_shape() {
        let mark = Node::path(Role::Chrome, PathData::new().move_to(0.0, 0.0), Attrs::new());
        let link = Node::new(Role::Chrome, Attrs::new().set("href", "/a?b=1&c=2"), Shape::Link(vec![mark]));
        let clip = Node::new(
            Role::Chrome,
            Attrs::new().set("id", "clip0"),
            Shape::ClipPath(vec![Node::rect(Role::Chrome, (0.0, 0.0, 5.0, 5.0), Attrs::new())]),
        );
        assert_eq!(
            print(&[link, clip]),
            "<a href=\"/a?b=1&amp;c=2\"><path d=\"M0,0\"/></a><clipPath id=\"clip0\"><rect x=\"0\" y=\"0\" width=\"5\" height=\"5\"/></clipPath>"
        );
    }

    #[test]
    fn a_gradient_prints_its_defs_and_stops() {
        let gradient = Node::new(
            Role::Chrome,
            Attrs::new().set("id", "g"),
            Shape::LinearGradient { from: (0.0, 1.0), to: (0.0, 0.0), stops: vec![(0.0, "red".into()), (0.5, "#00f".into())] },
        );
        let node = Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![gradient]));
        assert_eq!(
            print(&[node]),
            "<defs><linearGradient x1=\"0\" y1=\"1\" x2=\"0\" y2=\"0\" id=\"g\"><stop offset=\"0\" stop-color=\"red\"/><stop offset=\"0.5\" stop-color=\"#00f\"/></linearGradient></defs>"
        );
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
            print(&[Node::chars(Chars::new("\t", Escape::Mdsvg))]),
            "&#9;"
        );
    }

    #[test]
    fn an_element_node_prints_inside_its_parent() {
        let rect = Element { tag: "rect".into(), attrs: Attrs::new().set("x", "1"), children: vec![] };
        let node = Node::group(Role::Chrome, Attrs::new(), vec![Node::element(rect)]);
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

    #[test]
    fn a_script_wraps_its_source_in_one_cdata_section() {
        let script = Node::new(Role::Chrome, Attrs::new().quick("type", "text/javascript"), Shape::Script("a<b".into()));
        assert_eq!(print(&[script]), "<script type=\"text/javascript\">\n<![CDATA[\na<b\n]]>\n</script>");
    }

    #[test]
    fn a_literal_cdata_close_in_a_script_splits_the_section() {
        let printed = |source: &str| print(&[Node::new(Role::Chrome, Attrs::new(), Shape::Script(source.into()))]);
        assert_eq!(printed("a]]>b"), "<script>\n<![CDATA[\na]]]]><![CDATA[>b\n]]>\n</script>");
        for text in ["]]>", "]]>]]>", "x]]", "]>]]>y"] {
            let quick: String = quick_xml::events::BytesCData::escaped(format!("\n{text}\n").as_str())
                .map(|section| format!("<![CDATA[{}]]>", section.into_inner()))
                .collect();
            assert_eq!(printed(text), format!("<script>\n{quick}\n</script>"), "{text}");
        }
    }

    mod typed {
        use super::*;
        use scene::Role;

        #[test]
        fn geometry_rounds_the_exact_value_to_a_hundredth_and_trims() {
            assert_eq!(geometry(12.5), "12.5");
            assert_eq!(geometry(3.0), "3");
            assert_eq!(geometry(1.0 / 3.0), "0.33");
            assert_eq!(geometry(-0.0001), "0");
            assert_eq!(geometry(-0.0), "0");
            // 0.015 and 0.045 are stored just below the tie, so they round down; scaling
            // by 100 first would land on the tie and round up.
            assert_eq!(geometry(0.015), "0.01");
            assert_eq!(geometry(-0.045), "-0.04");
        }

        #[test]
        fn other_numbers_print_shortest_round_trip() {
            assert_eq!(number(0.1 + 0.2), "0.30000000000000004");
            assert_eq!(number(1.0), "1");
            assert_eq!(number(-0.0), "0");
            assert_eq!(number(1234.5678), "1234.5678");
        }

        #[test]
        fn a_rect_prints_transform_then_geometry_then_attrs_in_build_order() {
            let node = Node::rect(Role::Chrome, (1.234, 2.0, 30.0, 4.5), Attrs::new().set("fill", "#fff").num("opacity", 0.5))
                .translate(10.0, 0.005)
                .with_transform(Transform::Rotate { deg: 45.0, about: None });
            assert_eq!(
                print(&[node]),
                "<rect transform=\"translate(10,0.01) rotate(45)\" x=\"1.23\" y=\"2\" width=\"30\" height=\"4.5\" fill=\"#fff\" opacity=\"0.5\"/>"
            );
        }

        #[test]
        #[should_panic(expected = "infinite")]
        fn an_infinite_coordinate_is_a_bug_not_an_attribute() {
            geometry(f64::INFINITY);
        }

        #[test]
        #[should_panic(expected = "infinite")]
        fn an_infinite_number_is_a_bug_not_an_attribute() {
            number(f64::NEG_INFINITY);
        }

        #[test]
        fn nan_prints_as_vega_prints_it() {
            assert_eq!((geometry(f64::NAN), number(f64::NAN)), ("NaN".into(), "NaN".into()));
        }

        #[test]
        fn transforms_print_in_one_form() {
            let rotated = Transform::Rotate { deg: -30.0, about: Some((1.0, 2.555)) };
            let moved = Transform::Translate { x: 0.1 + 0.2, y: 1.0 };
            assert_eq!(transform_text(&[rotated, moved]), "rotate(-30 1 2.56) translate(0.3,1)");
        }

        #[test]
        fn a_path_prints_d3_paths_compact_form() {
            let path = PathData(vec![
                PathCommand::M(12.5, -0.001),
                PathCommand::L(3.0, 4.0),
                PathCommand::C(1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
                PathCommand::A { rx: 5.0, ry: 5.0, rot: 0.0, large: false, sweep: true, x: 9.0, y: 2.0 },
                PathCommand::Z,
            ]);
            assert_eq!(path_d(&path), "M12.5,0L3,4C1,2,3,4,5,6A5,5,0,0,1,9,2Z");
            let node = Node::path(Role::Chrome, path, Attrs::new().set("fill", "none"));
            assert_eq!(print(&[node]), "<path d=\"M12.5,0L3,4C1,2,3,4,5,6A5,5,0,0,1,9,2Z\" fill=\"none\"/>");
        }

        #[test]
        fn circles_and_lines_print_their_geometry() {
            assert_eq!(print(&[Node::circle(Role::Chrome, (1.0, 2.0), 0.456, Attrs::new())]), "<circle cx=\"1\" cy=\"2\" r=\"0.46\"/>");
            assert_eq!(print(&[Node::line(Role::Chrome, (0.0, 1.0), (2.0, 3.0), Attrs::new().set("stroke", "red"))]), "<line x1=\"0\" y1=\"1\" x2=\"2\" y2=\"3\" stroke=\"red\"/>");
        }

        #[test]
        fn text_prints_its_anchor_and_runs_and_an_empty_one_closes_itself() {
            let label = Node::text(Role::Label, (5.0, 6.255), Attrs::new().num("font-size", 11.0), "a<b");
            let empty = Node::empty_text(Role::Label, (1.0, 2.0), Attrs::new());
            assert_eq!(print(&[label, empty]), "<text x=\"5\" y=\"6.25\" font-size=\"11\">a&lt;b</text><text x=\"1\" y=\"2\"/>");
        }

        #[test]
        fn a_tspan_restarts_at_its_x() {
            let runs = vec![
                Run::Tspan { x: None, y: None, dx: None, dy: None, attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new("a", Escape::Attr))] },
                Run::Tspan { x: Some(4.0), y: None, dx: None, dy: Some(13.0), attrs: Attrs::new(), runs: vec![Run::Chars(Chars::new("b", Escape::Attr))] },
            ];
            let node = Node::new(Role::Label, Attrs::new(), Shape::Text { at: (4.0, 0.0), bounds: None, runs });
            assert_eq!(print(&[node]), "<text x=\"4\" y=\"0\"><tspan>a</tspan><tspan x=\"4\" dy=\"13\">b</tspan></text>");
        }

        #[test]
        fn a_closed_poly_is_a_polygon_and_an_open_one_a_polyline() {
            let points = vec![(0.0, 1.0), (2.504, 3.0)];
            assert_eq!(
                print(&[Node::poly(Role::Chrome, points.clone(), true, Attrs::new().set("fill", "red"))]),
                "<polygon points=\"0,1 2.5,3\" fill=\"red\"/>"
            );
            assert_eq!(print(&[Node::poly(Role::Chrome, points, false, Attrs::new())]), "<polyline points=\"0,1 2.5,3\"/>");
        }

        #[test]
        fn a_tspan_places_itself_by_x_y_dx_and_dy() {
            let run = Run::Tspan { x: Some(1.0), y: Some(2.0), dx: Some(0.5), dy: Some(3.0), attrs: Attrs::new(), runs: vec![] };
            let node = Node::new(Role::Label, Attrs::new(), Shape::Text { at: (0.0, 0.0), bounds: None, runs: vec![run] });
            assert_eq!(print(&[node]), "<text x=\"0\" y=\"0\"><tspan x=\"1\" y=\"2\" dx=\"0.5\" dy=\"3\"></tspan></text>");
        }

        #[test]
        fn a_pattern_prints_its_tile_and_a_scale_prints_unrounded() {
            let tile = Node::rect(Role::Chrome, (0.0, 0.0, 1.0, 6.0), Attrs::new());
            let pattern = Node::new(Role::Chrome, Attrs::new().set("id", "p"), Shape::Pattern { w: 6.0, h: 6.0, transform: Some(Transform::Rotate { deg: 45.0, about: None }), children: vec![tile] });
            assert_eq!(print(&[pattern]), "<pattern width=\"6\" height=\"6\" patternTransform=\"rotate(45)\" id=\"p\"><rect x=\"0\" y=\"0\" width=\"1\" height=\"6\"/></pattern>");
            let scaled = Node::group(Role::Chrome, Attrs::new(), vec![]).translate(1.0, 2.0).with_transform(Transform::Scale(11.0 / 12.0));
            assert_eq!(print(&[scaled]), "<g transform=\"translate(1,2) scale(0.9166666666666666)\"></g>");
        }

        #[test]
        fn the_root_prints_its_namespace_extent_and_view_box_first() {
            let root = Node::new(
                Role::Chrome,
                Attrs::new().set("class", "marks"),
                Shape::Svg { extent: Extent { width: 600.0, height: 320.256 }, children: vec![] },
            );
            assert_eq!(
                print(&[root]),
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"600\" height=\"320.256\" viewBox=\"0 0 600 320.256\" class=\"marks\"></svg>"
            );
        }

        #[test]
        fn a_link_and_a_clip_path_wrap_typed_children() {
            let clip = Node::new(Role::Chrome, Attrs::new().set("id", "c"), Shape::ClipPath(vec![Node::rect(Role::Chrome, (0.0, 0.0, 5.0, 5.0), Attrs::new())]));
            let link = Node::new(Role::Chrome, Attrs::new().set("href", "/a"), Shape::Link(vec![]));
            assert_eq!(print(&[clip, link]), "<clipPath id=\"c\"><rect x=\"0\" y=\"0\" width=\"5\" height=\"5\"/></clipPath><a href=\"/a\"></a>");
        }
    }
}

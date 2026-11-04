//! Multi-line labels: [`LINE_SEP`]-joined lines, their count and height, and the
//! text node that paints a `tspan` per line. Measuring them is the `measure`
//! crate's.

use crate::families::cartesian::lines_label_node;
use measure::LINE_SEP;
use scene::{Attrs, Node, Role};

/// The number of lines a label paints on.
pub fn line_count(text: &str) -> usize {
    text.matches(LINE_SEP).count() + 1
}

/// Vega's default line height for text of `font_size`.
pub fn line_height(font_size: f64) -> f64 {
    font_size + 2.0
}

/// A text node painting `text`, whose lines [`LINE_SEP`] joins: one `tspan` per line, each after the first
/// a line height below the one before and back at the text's own `x`. Needs the `font-size` attribute.
pub fn label(role: Role, at: (f64, f64), attrs: Attrs, text: impl AsRef<str>) -> Node {
    let size = attrs.get_num("font-size").expect("a label carries its font-size");
    lines_label_node(role, at, attrs, &[text.as_ref().to_string()], None, at.0, line_height(size))
}

#[cfg(test)]
mod line_tests {
    use super::*;

    #[test]
    fn a_label_of_several_lines_is_a_tspan_each_at_its_own_x() {
        let at = (10.0, 5.0);
        let three = label(Role::Axis, at, Attrs::new().num("font-size", 11.0), &format!("a{LINE_SEP}b{LINE_SEP}c"));
        assert_eq!(
            print::print(&[three]),
            "<text x=\"10\" y=\"5\" font-size=\"11\"><tspan>a</tspan><tspan x=\"10\" dy=\"13\">b</tspan><tspan x=\"10\" dy=\"13\">c</tspan></text>"
        );
        let plain = label(Role::Axis, at, Attrs::new().num("font-size", 9.0), "plain");
        assert_eq!(print::print(&[plain]), "<text x=\"10\" y=\"5\" font-size=\"9\">plain</text>");
    }

    #[test]
    fn a_label_counts_its_lines() {
        assert_eq!(line_count(&format!("a{LINE_SEP}b{LINE_SEP}c")), 3);
        assert_eq!(line_count("plain"), 1);
    }
}

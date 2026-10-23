//! Multi-line labels: [`LINE_SEP`]-joined lines, their count and height, and the
//! rewrite of the painted SVG into Vega's `tspan` per line. Measuring them is
//! the `measure` crate's.

use measure::LINE_SEP;

/// The number of lines a label paints on.
pub fn line_count(text: &str) -> usize {
    text.matches(LINE_SEP).count() + 1
}

/// Vega's default line height for text of `font_size`.
pub fn line_height(font_size: f64) -> f64 {
    font_size + 2.0
}

/// Rewrites every `<text>` whose content joins lines with [`LINE_SEP`] into Vega's multi-line form: one `tspan`
/// per line, each after the first dropped a line height below the one before.
pub fn expand_lines(svg: String) -> String {
    if !svg.contains(LINE_SEP) {
        return svg;
    }
    let mut out = String::with_capacity(svg.len() + 64);
    let mut rest = svg.as_str();
    while let Some(open) = rest.find("<text") {
        let (head, tail) = rest.split_at(open);
        out.push_str(head);
        let Some(tag_end) = tail.find('>') else { break };
        if tail[..tag_end].ends_with('/') {
            out.push_str(&tail[..=tag_end]);
            rest = &tail[tag_end + 1..];
            continue;
        }
        let Some(close) = tail.find("</text>") else { break };
        let (tag, content) = (&tail[..=tag_end], &tail[tag_end + 1..close]);
        if content.contains(LINE_SEP) && !content.contains('<') {
            let size = tag
                .split("font-size=\"")
                .nth(1)
                .and_then(|v| v.split('"').next())
                .and_then(|v| v.parse::<f64>().ok())
                .unwrap_or(11.0);
            let dy = crate::svg::num_full(line_height(size));
            // Vega positions a text with a transform, so its lines return to `x = 0`; a text placed by its own `x`
            // returns to that.
            let x = tag
                .split(" x=\"")
                .nth(1)
                .and_then(|v| v.split('"').next())
                .unwrap_or("0");
            out.push_str(tag);
            for (i, line) in content.split(LINE_SEP).enumerate() {
                if i == 0 {
                    out.push_str("<tspan>");
                } else {
                    out.push_str(&format!("<tspan x=\"{x}\" dy=\"{dy}\">"));
                }
                out.push_str(line);
                out.push_str("</tspan>");
            }
        } else {
            out.push_str(tag);
            out.push_str(content);
        }
        out.push_str("</text>");
        rest = &tail[close + "</text>".len()..];
    }
    out.push_str(rest);
    out
}


#[cfg(test)]
mod line_tests {
    use super::*;

    #[test]
    fn labels_that_paint_several_lines_become_a_tspan_each() {
        let svg = format!("<g><text x=\"10\" y=\"5\" font-size=\"11\">a{LINE_SEP}b{LINE_SEP}c</text><text font-size=\"9\">plain</text></g>");
        assert_eq!(
            expand_lines(svg),
            "<g><text x=\"10\" y=\"5\" font-size=\"11\"><tspan>a</tspan><tspan x=\"10\" dy=\"13\">b</tspan><tspan x=\"10\" dy=\"13\">c</tspan></text><text font-size=\"9\">plain</text></g>"
        );
        let beside_empty = format!("<text x=\"1\"/><text x=\"2\" font-size=\"11\">a{LINE_SEP}b</text>");
        assert_eq!(expand_lines(beside_empty), "<text x=\"1\"/><text x=\"2\" font-size=\"11\"><tspan>a</tspan><tspan x=\"2\" dy=\"13\">b</tspan></text>");
        let rotated = format!("<text transform=\"rotate(30)\" font-size=\"10\">a{LINE_SEP}b</text>");
        assert_eq!(expand_lines(rotated), "<text transform=\"rotate(30)\" font-size=\"10\"><tspan>a</tspan><tspan x=\"0\" dy=\"12\">b</tspan></text>");
    }

    #[test]
    fn a_label_counts_its_lines() {
        assert_eq!(line_count(&format!("a{LINE_SEP}b{LINE_SEP}c")), 3);
        assert_eq!(line_count("plain"), 1);
    }
}

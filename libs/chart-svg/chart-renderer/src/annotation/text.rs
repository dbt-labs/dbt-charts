//! Text box measurement and the text, halo and box nodes.

use super::model::{FontStyle, TextStyle};
use super::place::{TextAnchor, TextBox};
use crate::diagnostic::Diagnostic;
use crate::svg::{num};
use scene::{Attrs, Chars, Escape, Node, TextBlock};
use measure::FontBook;

pub(crate) fn css_family(families: &[String]) -> String {
    mdsvg::css::css_font_family_stack(&families.join(","))
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct Metrics {
    pub w: f64,
    pub h: f64,
    pub ascent: f64,
}

pub(crate) fn measure(fonts: &FontBook, text: &str, style: &TextStyle) -> Result<Metrics, Diagnostic> {
    let font = &style.font;
    // The raw list, not the escaped CSS stack: `FontBook` matches names.
    let family = font.family.join(",");
    let weight = measure::fonts::css_weight_axis(&num(font.weight))?;
    let w = fonts.measure_at_weight(text, &family, font.size, weight)?;
    let measurer = fonts.measurer(&family)?;
    Ok(Metrics {
        w,
        h: (measurer.ascent_em() + measurer.descent_em()) * font.size,
        ascent: measurer.ascent_em() * font.size,
    })
}

/// Text measured as a block: `lines` word-wrapped to the wrap width, `w` the
/// widest line, `h` the first line's face height plus `line_h` per further line.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Block {
    pub lines: Vec<String>,
    pub line_h: f64,
    pub w: f64,
    pub h: f64,
    pub ascent: f64,
}

/// Greedy word wrap on spaces; a word wider than `limit` keeps its own line.
pub(crate) fn measure_block(fonts: &FontBook, text: &str, style: &TextStyle, limit: Option<f64>) -> Result<Block, Diagnostic> {
    let first = measure(fonts, text, style)?;
    let line_h = style.font.size * style.font.line_height;
    let single = || Block { lines: vec![text.to_string()], line_h, w: first.w, h: first.h, ascent: first.ascent };
    let Some(limit) = limit.filter(|l| first.w > *l) else {
        return Ok(single());
    };
    let mut lines: Vec<String> = Vec::new();
    for word in text.split(' ').filter(|w| !w.is_empty()) {
        if let Some(last) = lines.last_mut() {
            let joined = format!("{last} {word}");
            if measure(fonts, &joined, style)?.w <= limit {
                *last = joined;
                continue;
            }
        }
        lines.push(word.to_string());
    }
    if lines.is_empty() {
        return Ok(single());
    }
    let mut w: f64 = 0.0;
    for line in &lines {
        w = w.max(measure(fonts, line, style)?.w);
    }
    let h = first.h + line_h * (lines.len() - 1) as f64;
    Ok(Block { lines, line_h, w, h, ascent: first.ascent })
}

/// The text's box, halo and glyphs as nodes; `halo` is its color, `None` to draw none.
pub(crate) fn nodes(b: &TextBox, style: &TextStyle, halo: Option<&str>) -> Vec<Node> {
    let mut out = Vec::new();
    let frame = &style.frame;
    if frame.visible {
        let attrs = Attrs::new()
            .set("x", num(b.x))
            .set("y", num(b.y))
            .set("width", num(b.w))
            .set("height", num(b.h))
            .set("rx", num(frame.radius))
            .set("fill", frame.fill.as_str())
            .set("opacity", num(frame.opacity));
        let attrs = match &frame.stroke {
            Some(stroke) => attrs.set("stroke", stroke.color.as_str()).set("stroke-width", num(stroke.width)),
            None => attrs,
        };
        out.push(Node::Rect(attrs.set("aria-hidden", "true")));
    }
    let (x, anchor) = match b.anchor {
        TextAnchor::Start => (b.x + b.pad, "start"),
        TextAnchor::Middle => (b.x + b.w / 2.0, "middle"),
        TextAnchor::End => (b.x + b.w - b.pad, "end"),
    };
    // The halo is one canvas-colored, outlined copy of the text faded as a
    // group, so overlapping glyph outlines never darken.
    if let Some(canvas) = halo.filter(|_| style.halo > 0.0 && style.halo_opacity > 0.0) {
        let paint = Attrs::new()
            .set("fill", canvas)
            .set("stroke", canvas)
            .set("stroke-width", num(2.0 * style.halo))
            .set("stroke-linejoin", "round");
        out.push(Node::group(
            Attrs::new().set("opacity", num(style.halo_opacity)).set("aria-hidden", "true"),
            vec![text_element(b, style, (x, anchor), paint)],
        ));
    }
    out.push(text_element(b, style, (x, anchor), Attrs::new().set("fill", style.font.color.as_str())));
    out
}

fn text_element(b: &TextBox, style: &TextStyle, (x, anchor): (f64, &str), paint: Attrs) -> Node {
    let font = &style.font;
    let attrs = Attrs::new()
        .set("x", num(x))
        .set("y", num(b.y + b.pad + b.ascent))
        .set("text-anchor", anchor)
        .set("font-family", css_family(&font.family))
        .set("font-size", num(font.size))
        .set("font-weight", num(font.weight))
        .set_if(font.style == FontStyle::Italic, "font-style", "italic")
        .extend(paint);
    let runs = b
        .lines
        .iter()
        .enumerate()
        .map(|(i, line)| {
            if i == 0 {
                Node::Chars(Chars::new(line.as_str(), Escape::Attr))
            } else {
                Node::Tspan(TextBlock {
                    attrs: Attrs::new().set("x", num(x)).set("dy", num(b.line_h)),
                    text: Chars::new(line.as_str(), Escape::Attr),
                })
            }
        })
        .collect();
    Node::TextRuns { attrs, children: runs }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    pub(crate) fn fonts() -> FontBook {
        let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(dir).expect("shipped fonts load")
    }

    #[test]
    fn measure_is_the_weighted_advance_and_the_face_height() {
        let fonts = fonts();
        let style = &crate::annotation::model::tests::contract()[0].style.text;
        let m = measure(&fonts, "FY24 goal", style).unwrap();
        let family = style.font.family.join(",");
        let w = fonts.measure_at_weight("FY24 goal", &family, 11.0, Some(400.0)).unwrap();
        assert_eq!(m.w, w);
        assert!(m.h > m.ascent && m.ascent > 0.0);
        let mut bold = style.clone();
        bold.font.weight = 700.0;
        assert!(measure(&fonts, "FY24 goal", &bold).unwrap().w > m.w);
    }

    #[test]
    fn prose_wrapped_to_twenty_eight_glyph_widths_stays_within_forty_characters_a_line() {
        let fonts = fonts();
        let style = &crate::annotation::model::tests::contract()[0].style.text;
        let prose = "Renewals slipped after the pricing change in March, so the second half \
            of the year leans on expansion from the largest accounts, which closed later \
            than planned and carried smaller discounts than the committed forecast assumed.";
        let informal = "Fine, so basically the whole thing is driven by a handful of big accounts that all \
            renewed in the final week of the quarter, which is why the line jumps right at the end.";
        let little = "Still, it is too early to tell whether the retail trial is lifting sales, though the \
            early results line up with the plan that we set in the fall.";
        let limit = measure(&fonts, &"n".repeat(28), style).unwrap().w;
        for text in [prose, informal, little] {
            let block = measure_block(&fonts, text, style, Some(limit)).unwrap();
            assert!(block.lines.len() > 1);
            for line in &block.lines {
                assert!(line.chars().count() <= 40, "{line:?}");
            }
        }
    }

    #[test]
    fn blank_text_wider_than_the_limit_stays_one_line() {
        let fonts = fonts();
        let style = &crate::annotation::model::tests::contract()[0].style.text;
        let block = measure_block(&fonts, &" ".repeat(40), style, Some(1.0)).unwrap();
        assert_eq!(block.lines.len(), 1);
    }
}

//! Text box measurement and the text, halo and box emission.

use std::fmt::Write as _;

use super::model::{FontStyle, TextStyle};
use super::place::{TextAnchor, TextBox};
use crate::diagnostic::Diagnostic;
use crate::svg::{num};
use print::{xml_escape, xml_escape_attr};
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
    let weight = mdsvg::fonts::css_weight_axis(&num(font.weight))?;
    let w = fonts.measure_at_weight(text, &family, font.size, weight)?;
    let measurer = fonts.measurer(&family)?;
    Ok(Metrics {
        w,
        h: (measurer.ascent_em() + measurer.descent_em()) * font.size,
        ascent: measurer.ascent_em() * font.size,
    })
}

pub(crate) fn emit(svg: &mut String, b: &TextBox, style: &TextStyle, canvas: &str) {
    let frame = &style.frame;
    if frame.visible {
        let _ = write!(
            svg,
            "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" rx=\"{}\" fill=\"{}\" opacity=\"{}\"",
            num(b.x),
            num(b.y),
            num(b.w),
            num(b.h),
            num(frame.radius),
            xml_escape_attr(&frame.fill),
            num(frame.opacity),
        );
        if let Some(stroke) = &frame.stroke {
            let _ = write!(
                svg,
                " stroke=\"{}\" stroke-width=\"{}\"",
                xml_escape_attr(&stroke.color),
                num(stroke.width)
            );
        }
        svg.push_str(" aria-hidden=\"true\"/>");
    }
    let (x, anchor) = match b.anchor {
        TextAnchor::Start => (b.x + b.pad, "start"),
        TextAnchor::Middle => (b.x + b.w / 2.0, "middle"),
        TextAnchor::End => (b.x + b.w - b.pad, "end"),
    };
    let font = &style.font;
    let _ = write!(
        svg,
        "<text x=\"{}\" y=\"{}\" text-anchor=\"{anchor}\" font-family=\"{}\" font-size=\"{}\" font-weight=\"{}\"",
        num(x),
        num(b.y + b.pad + b.ascent),
        xml_escape_attr(&css_family(&font.family)),
        num(font.size),
        num(font.weight),
    );
    if font.style == FontStyle::Italic {
        svg.push_str(" font-style=\"italic\"");
    }
    let _ = write!(svg, " fill=\"{}\"", xml_escape_attr(&font.color));
    if style.halo > 0.0 {
        let _ = write!(
            svg,
            " stroke=\"{}\" stroke-width=\"{}\" stroke-linejoin=\"round\" paint-order=\"stroke\"",
            xml_escape_attr(canvas),
            num(2.0 * style.halo)
        );
    }
    let _ = write!(svg, ">{}</text>", xml_escape(&b.text));
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
}

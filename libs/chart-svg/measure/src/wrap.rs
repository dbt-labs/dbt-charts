//! Breaking plain text into lines that fit a width.

use crate::fonts::{cached_measurer, wrap_text_precise};

use crate::font_files::font_path;
use crate::FontBook;

/// `wrap_text_precise(..., ellipsis=True) or [text]` against the font
/// `get_font_measurer(family)` loads.
pub fn wrap_plain(
    fonts_dir: &str,
    text: &str,
    width: f64,
    size: f64,
    family: &str,
    max_lines: usize,
) -> Result<Vec<String>, String> {
    let path = font_path(fonts_dir, family);
    // unweighted: its one caller, the callout placard, paints no `font-weight`, so the browser
    // draws it at 400.
    let measurer = cached_measurer(&path, 0, None);
    if !measurer.is_available() {
        return Err(format!("font file {path} cannot be read to measure text"));
    }
    let (lines, _) = wrap_text_precise(text, width, size, &measurer, Some(max_lines), true, true);
    Ok(if lines.is_empty() {
        vec![text.to_string()]
    } else {
        lines
    })
}

/// `title_overflow.py::prepare_title_text`'s wrap (title_overflow.py:33-46,
/// 49-82): the measurer of the face `family` resolves to, instanced at the CSS
/// `weight` the text paints at, whitespace-normalized, at most
/// `max_lines` lines with an ellipsis when `ellipsis`. Returns the lines and
/// whether any text was cut.
pub fn wrap_label(
    book: &FontBook,
    family: &str,
    weight: &str,
    text: &str,
    limit: f64,
    size: f64,
    max_lines: Option<usize>,
    ellipsis: bool,
) -> Result<(Vec<String>, bool), String> {
    let measurer = book.measurer_at_weight(family, weight)?;
    Ok(wrap_text_precise(text, limit, size, &measurer, max_lines, ellipsis, true))
}

#[cfg(test)]
mod tests {
    use super::*;

    const FAMILY: &str = "Inter";

    #[test]
    fn a_label_wraps_at_the_weight_it_paints_at() {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        let fonts = FontBook::load_dir(dir).expect("shipped fonts load");
        let text = "Quarterly recurring revenue";
        let regular = fonts.measure(text, FAMILY, 14.0).unwrap();
        let wraps_at = |weight: &str| {
            wrap_label(&fonts, FAMILY, weight, text, regular + 0.5, 14.0, None, false).unwrap().0.len() > 1
        };
        assert!(!wraps_at("400"), "the regular label fits its own width");
        assert!(wraps_at("700"), "the bold label is wider than that width");
    }
}

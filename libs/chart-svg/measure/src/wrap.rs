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
/// 49-82): the unweighted measurer of the face `family` resolves to
/// (`get_font_measurer(font_family)`), whitespace-normalized, at most
/// `max_lines` lines with an ellipsis when `ellipsis`. Returns the lines and
/// whether any text was cut.
pub fn wrap_label(
    book: &FontBook,
    family: &str,
    text: &str,
    limit: f64,
    size: f64,
    max_lines: Option<usize>,
    ellipsis: bool,
) -> Result<(Vec<String>, bool), String> {
    let measurer = book.measurer(family)?;
    Ok(wrap_text_precise(text, limit, size, &measurer, max_lines, ellipsis, true))
}

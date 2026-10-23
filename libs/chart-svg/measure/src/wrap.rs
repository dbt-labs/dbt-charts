//! Breaking plain text into lines that fit a width.

use mdsvg::fonts::{cached_measurer, wrap_text_precise};

use crate::font_files::font_path;

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

//! The width of text set in one weighted, possibly italic or tabular face: the
//! HVAR-exact `mdsvg` measurer production's `get_weighted_font_measurer` models.

use crate::FontBook;

/// `fonts.registry_family` (fonts.py:357-370): the first stack entry, with any
/// `Source Serif` spelling naming the one vendored cut.
pub fn registry_family(family: &str) -> &str {
    let primary = family
        .split(',')
        .next()
        .unwrap_or_default()
        .trim()
        .trim_matches(|c| c == '\'' || c == '"');
    if primary.starts_with("Source Serif") {
        "Source Serif 4"
    } else {
        primary
    }
}

/// The vendored font `get_weighted_font_measurer` measures with: the family's
/// italic when one is registered (Inter Variable, Source Serif 4), else
/// `get_font_path` (fonts.py:406-420) — Source Serif keeps its own file, a
/// numeric run measures as dbt Sans Tabular, every other family as Inter.
pub fn font_file(family: &str, italic: bool, numeric: bool) -> &'static str {
    match (registry_family(family), italic, numeric) {
        ("Source Serif 4", true, _) => "Source Serif 4 Italic",
        ("Inter Variable", true, _) => "Inter Variable Italic",
        ("Source Serif 4", false, _) => "Source Serif 4",
        (_, _, true) => "dbt Sans Tabular",
        _ => "Inter Variable",
    }
}

/// `get_weighted_font_measurer(family, weight, italic=, numeric=)`
/// (`font_measure.py:134-178`) at `size`. The HVAR-exact `mdsvg` measurer, not
/// `FontBook::measure`, which instances the same axis ~0.004px apart.
pub fn weighted_width(
    book: &FontBook,
    family: &str,
    weight: &str,
    italic: bool,
    numeric: bool,
    size: f64,
    text: &str,
) -> Result<f64, String> {
    let file = book.measurer(font_file(family, italic, numeric))?;
    let axis = crate::fonts::css_weight_axis(weight)?;
    Ok(crate::fonts::cached_measurer(&file.font_path, 0, axis).measure(text, size))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_registry_family_is_the_first_stack_entry_with_every_source_serif_spelling_folded() {
        assert_eq!(registry_family("'Inter Variable', 'Noto Emoji', sans-serif"), "Inter Variable");
        assert_eq!(registry_family("\"Georgia\", serif"), "Georgia");
        assert_eq!(registry_family("Source Serif Pro, serif"), "Source Serif 4");
        assert_eq!(registry_family(""), "");
    }

    #[test]
    fn the_font_file_follows_family_italic_and_numeric() {
        assert_eq!(font_file("'Source Serif Pro', serif", true, false), "Source Serif 4 Italic");
        assert_eq!(font_file("Source Serif 4", false, true), "Source Serif 4");
        assert_eq!(font_file("Inter Variable, sans-serif", true, true), "Inter Variable Italic");
        assert_eq!(font_file("Georgia", false, true), "dbt Sans Tabular");
        assert_eq!(font_file("Georgia", false, false), "Inter Variable");
    }
}

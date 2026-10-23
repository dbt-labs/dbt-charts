//! Which vendored font file measures a CSS family stack for markdown and plain
//! wrapped text: the files mdsvg loads, apart from [`FontBook`](crate::FontBook)'s
//! face matching.

/// `fonts.py::registry_family` + `get_font_path`, restricted to the one
/// branch markdown measuring ever reaches (`numeric=False`): a "Source
/// Serif" stack measures against the vendored Source Serif 4 file, every
/// other family — including one we do not vendor — falls back to Inter
/// Variable, the documented "closest honest estimate" default.
pub fn font_path(fonts_dir: &str, family_css: &str) -> String {
    let primary = family_css
        .split(',')
        .next()
        .unwrap_or("")
        .trim()
        .trim_matches(['\'', '"']);
    let file = if primary.starts_with("Source Serif") {
        "SourceSerif4Variable.ttf"
    } else {
        "InterVariable.ttf"
    };
    format!("{fonts_dir}/{file}")
}

/// `fonts.py::get_mono_font_path` — always Source Code Pro, no family input.
pub fn mono_font_path(fonts_dir: &str) -> String {
    format!("{fonts_dir}/SourceCodePro-Regular.ttf")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_source_serif_stack_measures_with_its_file_and_every_other_family_with_inter() {
        assert_eq!(font_path("/f", "'Source Serif 4', serif"), "/f/SourceSerif4Variable.ttf");
        assert_eq!(font_path("/f", "Georgia, serif"), "/f/InterVariable.ttf");
        assert_eq!(mono_font_path("/f"), "/f/SourceCodePro-Regular.ttf");
    }
}

//! Which vendored font file measures a CSS family stack for markdown and plain
//! wrapped text: the files mdsvg loads, apart from [`FontBook`](crate::FontBook)'s
//! face matching.

use crate::weighted::registry_family;

/// `fonts.py::get_font_path` restricted to the one branch markdown measuring ever reaches
/// (`numeric=False`): a "Source Serif" stack measures against the vendored Source Serif 4 file, every
/// other family, including one we do not vendor, falls back to Inter Variable, the documented
/// "closest honest estimate" default.
pub fn font_file(family_css: &str) -> &'static str {
    if registry_family(family_css) == "Source Serif 4" {
        "SourceSerif4Variable.ttf"
    } else {
        "InterVariable.ttf"
    }
}

// legacy: the chart-renderer measures from a font directory.
pub fn font_path(fonts_dir: &str, family_css: &str) -> String {
    format!("{fonts_dir}/{}", font_file(family_css))
}

/// `font_measure.py::_face_for(registry_family(family), "italic", ..)`: the italic file a CSS stack's
/// primary family ships. Only Inter Variable and Source Serif 4 at its regular and bold weights are
/// returned here; the registry's other italics (the medium and semibold cuts) and a family it does
/// not vendor give `None`, which board prose refuses rather than estimates.
pub fn italic_font_file(family_css: &str) -> Option<&'static str> {
    let primary = registry_family(family_css);
    if primary == "Source Serif 4" {
        Some("SourceSerif4-Italic.ttf")
    } else if primary == "Inter Variable" {
        Some("InterVariable-Italic.ttf")
    } else {
        None
    }
}

/// `fonts.py::get_mono_font_path` — always Source Code Pro, no family input.
pub const MONO_FILE: &str = "SourceCodePro-Regular.ttf";

// legacy: the chart-renderer measures from a font directory.
pub fn mono_font_path(fonts_dir: &str) -> String {
    format!("{fonts_dir}/{MONO_FILE}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_source_serif_stack_measures_with_its_file_and_every_other_family_with_inter() {
        assert_eq!(font_path("/f", "'Source Serif 4', serif"), "/f/SourceSerif4Variable.ttf");
        assert_eq!(font_path("/f", "Georgia, serif"), "/f/InterVariable.ttf");
        assert_eq!(mono_font_path("/f"), "/f/SourceCodePro-Regular.ttf");
        assert_eq!(italic_font_file("'Source Serif 4', serif"), Some("SourceSerif4-Italic.ttf"));
        assert_eq!(italic_font_file("'Inter Variable', Inter"), Some("InterVariable-Italic.ttf"));
        assert_eq!(italic_font_file("Georgia, serif"), None);
    }
}

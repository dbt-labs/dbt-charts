//! Text measurement from the shipped dbt charts TTFs via ttf-parser —
//! the production equivalent of what `font_measure.py` does with mdsvg.
//! Family resolution is by normalized-name substring against the font
//! filenames, the first entry of the CSS family list names the face (the body
//! face when nothing matches). A codepoint the face has no glyph for books
//! the advance `crate::fonts::FontMeasurer::measure` books (an emoji cluster
//! at one emoji advance, otherwise `unmapped_advance_em`), as in production.
//!
//! `bind` never receives a [`Measure`]; only `layout`, `intrinsic` and
//! `height_for_width` do. Each family's `measure_*` helpers move here when it
//! is split onto the contract; spark_bar sizes its columns from style
//! constants and measures nothing, so the handle is all it takes.

pub mod east_asian_width;
pub mod font_files;
pub mod fonts;
pub mod reservation;
pub mod weighted;
pub mod wrap;
mod column_demand;
mod label_widths;
mod pager;
mod row_numbers;
mod stand_in;
#[cfg(test)]
mod table_metrics_tests;
mod word_floor;

pub use column_demand::SAMPLE_ROWS;
pub use stand_in::stand_in_family;

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

pub struct Measure<'a> {
    pub fonts: &'a FontBook,
}

/// `offset(item)` from vega-scenegraph `util/text.js` — the font-baseline
/// shift Vega applies to every text item instead of SVG's
/// `alignment-baseline`.
pub fn baseline_shift(baseline: &str, font_size: f64) -> f64 {
    (match baseline {
        "top" => 0.79 * font_size,
        "middle" => 0.30 * font_size,
        "bottom" => -0.21 * font_size,
        _ => 0.0, // alphabetic
    })
    .round()
}

/// Joins the lines of a label that paints on several (an array a `labels.expr` returns): a noncharacter, so no
/// text meets it by accident. Measuring takes the widest line; the SVG finishes as Vega's `tspan` per line
/// (`chart-renderer`'s `text::expand_lines`).
pub const LINE_SEP: char = '\u{FDD0}';

pub struct Face {
    data: Box<[u8]>,
    path: String,
    norm_name: String,
}

/// `font_measure.py::centered_baseline_offset`: how far below a container's
/// vertical center a centered line's baseline sits.
pub fn centered_baseline_offset(measurer: &crate::fonts::FontMeasurer, font_size: f64) -> f64 {
    (measurer.ascent_em() - measurer.descent_em()) / 2.0 * font_size
}

/// The advance, in font units, of the cluster that starts at
/// `codepoints[i]`, the glyph it reads (a mapped glyph only), and how many
/// codepoints it spans. A mapped glyph is its
/// own advance; an emoji cluster or an unmapped codepoint books what
/// `crate::fonts::FontMeasurer::measure` books, never an `Err` — production
/// does not fail a chart over one rare character.
fn cluster_advance(
    face: &ttf_parser::Face<'_>,
    codepoints: &[u32],
    i: usize,
) -> (f64, Option<u16>, usize) {
    let upem = face.units_per_em() as f64;
    let cluster = crate::fonts::emoji_cluster_length(codepoints, i);
    if cluster > 0 {
        return (upem * crate::fonts::EMOJI_ADVANCE_EM, None, cluster);
    }
    let ch = char::from_u32(codepoints[i]).expect("came from chars()");
    match face.glyph_index(ch).and_then(|g| Some((g, face.glyph_hor_advance(g)?))) {
        Some((g, advance)) => (advance as f64, Some(g.0), 1),
        None => (upem * crate::fonts::unmapped_advance_em(codepoints[i]), None, 1),
    }
}

/// vl-convert's text width over a string's shaped advances, the last step of
/// [`FontBook::measure_shaped`].
///
/// vl-convert measures a string as the bounding box of usvg's text layout
/// (`vl-convert-rs/src/text.rs` `extract_text_width`), and usvg books a glyph
/// cluster whose advance is zero as a one-unit rectangle at the pen
/// (`usvg-0.45.1/src/text/layout.rs` `convert_span`: `if advance <= 0.0
/// { advance = 1.0 }`). The unit is user space, not an em fraction, so a label
/// ending in U+200B (the guard `compose_suffix_reservation` ends its padding
/// with, zero-wide in the shipped faces) reaches one pixel past its advances
/// at any font size. A zero-advance cluster before a wider one adds nothing.
///
/// `pen_end_units` is the string's advance sum and `zero_cluster_pens_units`
/// the pen position of each cluster whose advance is zero, both in font units.
fn book_zero_advance_clusters(
    pen_end_units: f64,
    zero_cluster_pens_units: impl IntoIterator<Item = f64>,
    upem: f64,
    size: f64,
) -> f64 {
    zero_cluster_pens_units
        .into_iter()
        .fold(pen_end_units / upem * size, |width, pen| {
            width.max(pen / upem * size + ZERO_ADVANCE_CLUSTER_PX)
        })
}

/// A parsed face lives as long as the book, so measuring never re-parses
/// font tables or recompiles a shape plan.
pub struct FontBook {
    shapers: Vec<OnceLock<rustybuzz::Face<'static>>>,
    /// Default instances only: a weighted measurement reads HVAR deltas for
    /// just the glyphs it measures, so no authored weight becomes a key in a
    /// book that lives for the process.
    parsed: Vec<OnceLock<ttf_parser::Face<'static>>>,
    /// Keyed by closed enums and a face index, so bounded by the fonts.
    plans:
        Mutex<HashMap<(usize, rustybuzz::Direction, rustybuzz::Script), Arc<rustybuzz::ShapePlan>>>,
    /// (face index, units_per_em) resolved per normalized family key.
    cache: Mutex<HashMap<String, Option<usize>>>,
    /// Declared last: every parsed face above borrows these bytes, and
    /// fields drop in declaration order.
    faces: Vec<Face>,
}

/// The width usvg books for a zero-advance glyph cluster
/// (`usvg-0.45.1/src/text/layout.rs` `convert_span`), in user-space px.
const ZERO_ADVANCE_CLUSTER_PX: f64 = 1.0;

/// The registry name of the body face, `fonts.py::INTER_VARIABLE_FONT_FAMILY`.
const BODY_FAMILY: &str = "Inter Variable";

fn norm(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_ascii_lowercase()
}

impl FontBook {
    pub fn empty() -> Self {
        Self::new(Vec::new())
    }

    fn new(faces: Vec<Face>) -> Self {
        FontBook {
            shapers: faces.iter().map(|_| OnceLock::new()).collect(),
            parsed: faces.iter().map(|_| OnceLock::new()).collect(),
            plans: Default::default(),
            cache: Default::default(),
            faces,
        }
    }

    fn face_data(&self, i: usize) -> &'static [u8] {
        let data: &[u8] = &self.faces[i].data;
        // SAFETY: `faces` is never mutated after construction and a boxed
        // slice's heap buffer does not move with the book, so the bytes
        // outlive every parsed face cached in `shapers`, `parsed` and
        // `plans`, which drop before `faces` and never leave the book
        // except behind `&self`.
        unsafe { std::slice::from_raw_parts(data.as_ptr(), data.len()) }
    }

    pub fn load_dir(dir: &str) -> Result<Self, String> {
        let mut faces = Vec::new();
        let entries = std::fs::read_dir(dir).map_err(|e| format!("fonts dir {dir:?}: {e}"))?;
        for e in entries.flatten() {
            let p = e.path();
            if p.extension().and_then(|x| x.to_str()) != Some("ttf") {
                continue;
            }
            let Some(stem) = p.file_stem().and_then(|x| x.to_str()) else {
                continue;
            };
            if let Ok(data) = std::fs::read(&p) {
                if ttf_parser::Face::parse(&data, 0).is_ok() {
                    faces.push(Face {
                        data: data.into_boxed_slice(),
                        path: p.to_string_lossy().into_owned(),
                        norm_name: norm(stem),
                    });
                }
            }
        }
        if faces.is_empty() {
            return Err(format!("fonts dir {dir:?}: no valid .ttf faces found"));
        }
        // Stable sort by norm_name → deterministic matching regardless of readdir order.
        faces.sort_by(|a, b| a.norm_name.cmp(&b.norm_name));
        Ok(Self::new(faces))
    }

    /// Pick a face for a CSS font-family list like
    /// `'dbt Sans Tabular', 'Noto Emoji', Inter, system-ui, sans-serif`.
    ///
    /// Only the first entry names the face, and a primary that no shipped face
    /// matches — an unvendored family, a generic keyword, an authored string
    /// — measures with the body face, as production's `get_font_path` does. A
    /// later entry never stands in: the emoji face the stack appends has no
    /// glyph for a space.
    ///
    /// Ranking within a family (lower = preferred):
    ///   0 — exact stem match
    ///   1 — stem is a prefix AND face ends with "regular" or "variable"
    ///   2 — stem is a prefix (any other suffix like Bold/Medium/Italic)
    ///   3 — face.norm_name contains stem or stem contains face.norm_name
    fn face_for(&self, family_list: &str) -> Option<usize> {
        let key = norm(family_list);
        if let Some(hit) = self.cache.lock().expect("face cache poisoned").get(&key) {
            return *hit;
        }
        let primary = family_list.split(',').next().unwrap_or_default();
        let found = self
            .match_family(primary)
            .or_else(|| self.match_family(BODY_FAMILY));
        self.cache
            .lock()
            .expect("face cache poisoned")
            .insert(key, found);
        found
    }

    fn match_family(&self, family: &str) -> Option<usize> {
        let f = norm(family.trim().trim_matches('\''));
        if f.is_empty() || f == "systemui" || f == "sansserif" || f == "serif" {
            return None;
        }
        let mut best: Option<(u8, usize)> = None;
        for (i, face) in self.faces.iter().enumerate() {
            let n = &face.norm_name;
            let score = if n == &f {
                0
            } else if n.starts_with(&f)
                && (n[f.len()..].contains("regular") || n[f.len()..].contains("variable"))
            {
                1
            } else if n.starts_with(&f) {
                2
            } else if n.contains(f.as_str()) || f.contains(n.as_str()) {
                3
            } else {
                continue;
            };
            if best.is_none_or(|(s, _)| score < s) {
                best = Some((score, i));
            }
        }
        best.map(|(_, i)| i)
    }

    /// Advance width of `text` at `size` px in the first resolvable family.
    ///
    /// Returns Err when no face resolves for `family_list`.
    pub fn measure(&self, text: &str, family_list: &str, size: f64) -> Result<f64, String> {
        self.measure_at_weight(text, family_list, size, None)
    }

    /// The mdsvg measurer for the face `family_list` resolves to, so a law
    /// ported over `fonts` (truncation, baseline centering) runs on
    /// the face every other measurement here uses.
    pub fn measurer(
        &self,
        family_list: &str,
    ) -> Result<std::sync::Arc<crate::fonts::FontMeasurer>, String> {
        let i = self
            .face_for(family_list)
            .ok_or_else(|| format!("text: no font face resolved for {family_list:?}"))?;
        Ok(crate::fonts::cached_measurer(&self.faces[i].path, 0, None))
    }

    /// The font file `family_list` resolves to, for a weighted
    /// `crate::fonts::cached_measurer` built where this book cannot travel
    /// (a template helper outliving the render call).
    pub fn font_path(&self, family_list: &str) -> Result<String, String> {
        let i = self
            .face_for(family_list)
            .ok_or_else(|| format!("text: no font face resolved for {family_list:?}"))?;
        Ok(self.faces[i].path.clone())
    }

    /// `measure`, with the variable face instanced at `weight` when given.
    ///
    /// A run painted heavier than its neighbors must be measured heavier or
    /// whatever is positioned against it overlaps — 1.41px at the footer's
    /// 11px for "dbt Charts", which `weighted_measure.rs` pins.
    ///
    /// A weighted run adds each glyph's HVAR delta unrounded
    /// ([`fonts::weighted_advance_deltas`]), exactly as production's
    /// `font_measure.get_weighted_font_measurer` does. `ttf_parser`'s own
    /// `set_variation` + `glyph_hor_advance` rounds every advance to a whole
    /// font unit, which lands ~0.004px off — enough to snap a footer run to
    /// the other side of a half pixel.
    ///
    /// A face with no `fvar` table returns `Err` rather than silently
    /// measuring at its default instance: production raises there
    /// (`mdsvg/fonts.py`), and a silent default would put the preceding run
    /// on top of the brand.
    pub fn measure_at_weight(
        &self,
        text: &str,
        family_list: &str,
        size: f64,
        weight: Option<f32>,
    ) -> Result<f64, String> {
        let mut widest = 0.0_f64;
        for line in text.split(LINE_SEP) {
            widest = widest.max(self.measure_line_at_weight(line, family_list, size, weight)?);
        }
        Ok(widest)
    }

    fn measure_line_at_weight(
        &self,
        text: &str,
        family_list: &str,
        size: f64,
        weight: Option<f32>,
    ) -> Result<f64, String> {
        let i = self
            .face_for(family_list)
            .ok_or_else(|| format!("text: no font face resolved for {family_list:?}"))?;
        let face = self.parsed[i].get_or_init(|| {
            ttf_parser::Face::parse(self.face_data(i), 0).expect("validated at load")
        });
        let codepoints: Vec<u32> = text.chars().map(|c| c as u32).collect();
        let deltas = match weight {
            Some(w) => {
                let wght = ttf_parser::Tag::from_bytes(b"wght");
                if !face.variation_axes().into_iter().any(|axis| axis.tag == wght) {
                    return Err(format!("text: {family_list:?} has no wght axis to instance at {w}"));
                }
                let glyphs = text.chars().filter_map(|c| face.glyph_index(c)).map(|g| g.0);
                Some(
                    fonts::weighted_advance_deltas(face, w, glyphs)
                        .map_err(|e| format!("text: {family_list:?}: {e}"))?,
                )
            }
            None => None,
        };
        let mut units = 0.0_f64;
        let mut pos = 0;
        while pos < codepoints.len() {
            let (advance, glyph, len) = cluster_advance(face, &codepoints, pos);
            // Two adds, not `advance + delta`: production sums them as two
            // statements, and float addition is not associative.
            units += advance;
            if let (Some(deltas), Some(g)) = (&deltas, glyph) {
                units += deltas.get(&g).copied().unwrap_or(0.0);
            }
            pos += len;
        }
        Ok(units / face.units_per_em() as f64 * size)
    }

    /// Shaped width of `text` at `size` px — what vl-convert's own layout
    /// measures: the family-matched face's default instance with GPOS pair
    /// kerning applied (probed empirically: a non-400 `fontWeight` does NOT
    /// re-instance the variable face — the shipped weighted statics carry
    /// their own family names, so the base face always wins the family
    /// query), then [`book_zero_advance_clusters`]. Every classified
    /// Vega-measured width goes through here, never through `measure`'s raw
    /// advance sums (the raw sites still to classify are listed in
    /// `docs/laws-shared.md`, "Which measurer a width takes"): kerning
    /// moves a lettered label (the strip's stub row labels) and a
    /// comma-grouped tabular one alike (`"50,000"` in `dbt Sans Tabular`
    /// kerns the digit-comma pairs 104 units, 0.5586px at 11px: 38.0596
    /// against the raw 38.6182), fractions of a pixel that cross Vega's
    /// canvas-ceil boundary and shift the fit's whole left margin.
    pub fn measure_shaped(&self, text: &str, family_list: &str, size: f64) -> Result<f64, String> {
        let mut widest = 0.0_f64;
        for line in text.split(LINE_SEP) {
            widest = widest.max(self.measure_shaped_line(line, family_list, size)?);
        }
        Ok(widest)
    }

    fn measure_shaped_line(&self, text: &str, family_list: &str, size: f64) -> Result<f64, String> {
        let i = self
            .face_for(family_list)
            .ok_or_else(|| format!("text: no font face resolved for {family_list:?}"))?;
        let face = match self.shapers[i].get() {
            Some(face) => face,
            None => {
                let face = rustybuzz::Face::from_slice(self.face_data(i), 0)
                    .ok_or_else(|| format!("text: rustybuzz refused face for {family_list:?}"))?;
                self.shapers[i].get_or_init(|| face)
            }
        };
        let upem = face.units_per_em() as f64;
        let mut buf = rustybuzz::UnicodeBuffer::new();
        buf.push_str(text);
        buf.guess_segment_properties();
        let key = (i, buf.direction(), buf.script());
        let plan = self
            .plans
            .lock()
            .expect("plan cache poisoned")
            .get(&key)
            .cloned();
        let plan = match plan {
            Some(plan) => plan,
            None => {
                // `script()` reads an unguessed script as UNKNOWN; the plan
                // `rustybuzz::shape` builds takes it as `None`.
                let script = Some(key.2).filter(|s| *s != rustybuzz::script::UNKNOWN);
                let plan = Arc::new(rustybuzz::ShapePlan::new(face, key.1, script, None, &[]));
                self.plans
                    .lock()
                    .expect("plan cache poisoned")
                    .insert(key, plan.clone());
                plan
            }
        };
        let glyphs = rustybuzz::shape_with_plan(face, &plan, buf);
        let mut pen_units = 0_i64;
        let mut zero_cluster_pens = Vec::new();
        // A cluster is the run of glyphs sharing a `cluster` value; harfbuzz
        // merges a combining mark into its base's.
        let mut cluster: Option<(u32, i64, i32)> = None; // (id, pen at start, widest advance)
        let mut close = |open: Option<(u32, i64, i32)>| {
            if let Some((_, pen, widest)) = open {
                if widest <= 0 {
                    zero_cluster_pens.push(pen as f64);
                }
            }
        };
        for (info, pos) in glyphs.glyph_infos().iter().zip(glyphs.glyph_positions()) {
            cluster = match cluster {
                Some((id, pen, widest)) if id == info.cluster => {
                    Some((id, pen, widest.max(pos.x_advance)))
                }
                open => {
                    close(open);
                    Some((info.cluster, pen_units, pos.x_advance))
                }
            };
            pen_units += pos.x_advance as i64;
        }
        close(cluster);
        Ok(book_zero_advance_clusters(
            pen_units as f64,
            zero_cluster_pens,
            upem,
            size,
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn baseline_shift_matches_vega_rounding() {
        assert_eq!(baseline_shift("top", 11.0), 9.0); // round(8.69)
        assert_eq!(baseline_shift("top", 18.0), 14.0); // round(14.22)
        assert_eq!(baseline_shift("middle", 11.0), 3.0); // round(3.3)
        assert_eq!(baseline_shift("bottom", 11.0), -2.0); // round(-2.31)
        assert_eq!(baseline_shift("alphabetic", 11.0), 0.0);
    }

    #[test]
    fn a_wide_line_measures_the_whole_label() {
        let dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        let fonts = FontBook::load_dir(dir).expect("shipped fonts load");
        let (short, long) = (
            fonts.measure("ab", "Inter", 11.0).unwrap(),
            fonts.measure("abcdef", "Inter", 11.0).unwrap(),
        );
        assert_eq!(
            fonts
                .measure(&format!("ab{LINE_SEP}abcdef"), "Inter", 11.0)
                .unwrap(),
            long
        );
        assert!(short < long);
    }
}

//! The one text measurer: every run shaped (GPOS kerning) in the face the
//! browser paints it with, at its painted size, weight and style.
//!
//! The painted face is the served `@font-face` the CSS stack selects
//! (`dbt_charts.core.fonts.FONT_REGISTRY`, the rows with a web file): a
//! variable face instanced at the requested `wght`, the italic file when the
//! family ships one and the upright otherwise (the browser slants the upright,
//! which keeps its advances). Each code point is set in the first face of the
//! stack that maps it (joiners and variation selectors stay with the code
//! point before them). Shaping is HarfBuzz's, through its rustybuzz port;
//! ascent and descent are the face's own vertical metrics (OS/2 typo metrics
//! when the face asks for them, else hhea), at the instance.

use std::path::Path;

use rustybuzz::Face;

/// (CSS family, italic, file). One served face per family and style.
const PAINTED_FACES: &[(&str, bool, &str)] = &[
    ("Inter Variable", false, "InterVariable.ttf"),
    ("Inter Variable", true, "InterVariable-Italic.ttf"),
    ("dbt Sans Tabular", false, "DBTSansTabular-Regular.ttf"),
    ("Source Serif 4", false, "SourceSerif4Variable.ttf"),
    ("Source Serif 4", true, "SourceSerif4-Italic.ttf"),
    ("Noto Emoji", false, "NotoEmoji-Regular.ttf"),
    ("Source Code Pro", false, "SourceCodePro-Regular.ttf"),
    (
        "dbt Serif Oldstyle Tabular",
        false,
        "DBTSerifOldstyleTabular-Regular.ttf",
    ),
    (
        "dbt Serif Oldstyle Proportional",
        false,
        "DBTSerifOldstyleProportional-Regular.ttf",
    ),
];

/// What a stack naming no served face measures in.
const DEFAULT_SANS: &str = "Inter Variable";

fn normalize(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect::<String>()
        .to_ascii_lowercase()
}

const ELLIPSIS: &str = "…";

/// Where a long word may break before falling back to a character break.
const SEAM_CHARS: &[char] = &['_', '/', '.', '-', '?', '&', '=', ':'];

/// How a run paints: the CSS `font-family` stack, size in px, numeric
/// `font-weight`, and whether `font-style` is italic.
#[derive(Clone, Copy, Debug)]
pub struct Font<'a> {
    pub family: &'a str,
    pub size: f64,
    pub weight: f32,
    pub italic: bool,
}

/// A measured run, in px: the advance, and the largest ascent above and
/// descent below the baseline (both positive) among the stack's first face and
/// every face the run uses.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Run {
    pub advance: f64,
    pub ascent: f64,
    pub descent: f64,
}

/// Lines that fit a width; `truncated` when text was cut to an ellipsis.
#[derive(Clone, Debug, PartialEq)]
pub struct Wrapped {
    pub lines: Vec<String>,
    pub truncated: bool,
}

pub struct FontBook {
    /// Indexed like `PAINTED_FACES`.
    data: Vec<Vec<u8>>,
    /// Every `.ttf` of the fonts directory as production's measurer loads them, by
    /// normalized stem (sorted) and file name: the faces [`Self::raw_advance`] resolves among.
    shipped: Vec<(String, String)>,
    dir: std::path::PathBuf,
}

impl FontBook {
    /// Reads every painted face from `dir`; a missing or unreadable file is an
    /// error, so a book never measures with a face missing.
    pub fn load_dir(dir: &Path) -> Result<Self, String> {
        let data = PAINTED_FACES
            .iter()
            .map(|(_, _, file)| {
                let path = dir.join(file);
                let bytes =
                    std::fs::read(&path).map_err(|e| format!("font {}: {e}", path.display()))?;
                ttf_parser::Face::parse(&bytes, 0)
                    .map_err(|e| format!("font {}: {e}", path.display()))?;
                Ok(bytes)
            })
            .collect::<Result<_, String>>()?;
        let mut shipped = Vec::new();
        for entry in std::fs::read_dir(dir)
            .map_err(|e| format!("font {}: {e}", dir.display()))?
            .flatten()
        {
            let path = entry.path();
            let (Some(stem), Some("ttf")) = (
                path.file_stem().and_then(|s| s.to_str()),
                path.extension().and_then(|e| e.to_str()),
            ) else {
                continue;
            };
            shipped.push((normalize(stem), format!("{stem}.ttf")));
        }
        shipped.sort();
        Ok(FontBook {
            data,
            shipped,
            dir: dir.to_path_buf(),
        })
    }

    /// The bytes of the face production's `FontBook::face_for` picks for a CSS family list: only
    /// the first entry names the face, matched against the shipped stems (exact, then a prefix
    /// with "regular" or "variable" after it, then any prefix, then containment, the first of
    /// the sorted stems winning a tie), else the body face. Only [`Self::raw_advance`] reads it.
    fn production_face(&self, family_list: &str) -> Result<std::borrow::Cow<'_, [u8]>, String> {
        let primary = family_list.split(',').next().unwrap_or_default();
        let found = self
            .match_shipped(primary)
            .or_else(|| self.match_shipped(DEFAULT_SANS))
            .expect("the body face is a shipped file");
        let file = &self.shipped[found].1;
        match PAINTED_FACES.iter().position(|(_, _, f)| f == file) {
            Some(i) => Ok(std::borrow::Cow::Borrowed(&self.data[i])),
            None => {
                let path = self.dir.join(file);
                std::fs::read(&path)
                    .map(std::borrow::Cow::Owned)
                    .map_err(|e| format!("font {}: {e}", path.display()))
            }
        }
    }

    fn match_shipped(&self, family: &str) -> Option<usize> {
        let f = normalize(family);
        if f.is_empty() || f == "systemui" || f == "sansserif" || f == "serif" {
            return None;
        }
        let mut best: Option<(u8, usize)> = None;
        for (i, (n, _)) in self.shipped.iter().enumerate() {
            let score = if *n == f {
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

    /// The served faces `font.family` lists, in order; an entry the book does
    /// not serve (a system font, a generic keyword) is skipped, since its
    /// metrics are unreadable here. A stack naming no served face measures as
    /// Inter, as production does.
    fn stack(&self, font: &Font) -> Result<Vec<Face<'_>>, String> {
        let mut faces = Vec::new();
        let served = |name: &str| {
            let face = |italic: bool| {
                PAINTED_FACES
                    .iter()
                    .position(|(family, i, _)| family.eq_ignore_ascii_case(name) && *i == italic)
            };
            face(font.italic).or_else(|| face(false))
        };
        for entry in font.family.split(',') {
            if let Some(i) = served(entry.trim().trim_matches(|c| c == '\'' || c == '"')) {
                faces.push(self.instance(i, font.weight)?);
            }
        }
        if faces.is_empty() {
            let inter = served(DEFAULT_SANS).expect("the default sans is a painted face");
            faces.push(self.instance(inter, font.weight)?);
        }
        Ok(faces)
    }

    /// Face `i` instanced at `weight`, clamped to its `wght` axis; a face
    /// with no `wght` axis paints every weight with its one instance.
    fn instance(&self, i: usize, weight: f32) -> Result<Face<'_>, String> {
        let mut face = Face::from_slice(&self.data[i], 0).ok_or("rustybuzz refused a face")?;
        let wght = ttf_parser::Tag::from_bytes(b"wght");
        let axis = face
            .variation_axes()
            .into_iter()
            .find(|axis| axis.tag == wght);
        if let Some(axis) = axis {
            let value = weight.clamp(axis.min_value, axis.max_value);
            face.set_variations(&[rustybuzz::Variation { tag: wght, value }]);
        }
        Ok(face)
    }

    /// The advance, ascent and descent of `text` set on one line in `font`.
    pub fn run(&self, text: &str, font: &Font) -> Result<Run, String> {
        let faces = self.stack(font)?;
        let mut run = vertical(&faces[0], font.size, None);
        for (face, segment) in segments(&faces, text) {
            let f = &faces[face];
            run.advance += shaped_units(f, segment) / f.units_per_em() as f64 * font.size;
            run = vertical(f, font.size, Some(run));
        }
        Ok(run)
    }

    /// The advance of `text` summed as production's axis-label measurer sums it
    /// (`FontBook::measure_at_weight` in this crate): each cluster's `hmtx` advance at the
    /// face's default instance plus its `HVAR` delta at `font.weight`, no kerning, no
    /// shaping. A face with no `wght` axis books no delta. The face is the one production's
    /// `face_for` resolves ([`Self::production_face`]: the stack's first entry only, matched
    /// fuzzily, else Inter); production first maps the family to its stand-in
    /// ([`crate::stand_in_family`]), which the caller applies. Only a verdict that production
    /// takes on these sums may read it.
    pub fn raw_advance(&self, text: &str, font: &Font) -> Result<f64, String> {
        let bytes = self.production_face(font.family)?;
        let face = ttf_parser::Face::parse(&bytes, 0).map_err(|e| e.to_string())?;
        let wght = ttf_parser::Tag::from_bytes(b"wght");
        let deltas = if face.variation_axes().into_iter().any(|a| a.tag == wght) {
            let glyphs = text
                .chars()
                .filter_map(|c| face.glyph_index(c))
                .map(|g| g.0);
            Some(crate::fonts::weighted_advance_deltas(
                &face,
                font.weight,
                glyphs,
            )?)
        } else {
            None
        };
        let codepoints: Vec<u32> = text.chars().map(|c| c as u32).collect();
        let (mut units, mut at) = (0.0_f64, 0);
        while at < codepoints.len() {
            let (advance, glyph, len) = crate::cluster_advance(&face, &codepoints, at);
            // Two adds, not one: production sums them as two statements.
            units += advance;
            if let (Some(deltas), Some(g)) = (&deltas, glyph) {
                units += deltas.get(&g).copied().unwrap_or(0.0);
            }
            at += len;
        }
        Ok(units / face.units_per_em() as f64 * font.size)
    }

    fn advance(&self, text: &str, font: &Font) -> Result<f64, String> {
        Ok(self.run(text, font)?.advance)
    }

    /// `text` whole when it fits `width`; otherwise its longest prefix, with
    /// trailing spaces dropped, that fits with an ellipsis after it, and when
    /// not even that fits, its first character. The flag says whether it was cut.
    pub fn truncate(&self, text: &str, font: &Font, width: f64) -> Result<(String, bool), String> {
        if text.is_empty() || self.advance(text, font)? <= width {
            return Ok((text.to_string(), false));
        }
        let chars: Vec<char> = text.chars().collect();
        let candidate = |n: usize| {
            let prefix: String = chars[..n].iter().collect();
            format!("{}{ELLIPSIS}", prefix.trim_end())
        };
        let (mut lo, mut hi, mut best) = (0, chars.len(), None);
        while lo <= hi {
            let mid = (lo + hi) / 2;
            let c = candidate(mid);
            if self.advance(&c, font)? <= width {
                best = Some(c);
                lo = mid + 1;
            } else if mid == 0 {
                break;
            } else {
                hi = mid - 1;
            }
        }
        let first = || chars.first().map(char::to_string).unwrap_or_default();
        Ok((best.unwrap_or_else(first), true))
    }

    /// Greedy word wrap of `text`, whitespace collapsed, to lines that fit
    /// `width`. A word wider than the line breaks after its last seam
    /// character that fits, else after its longest fitting prefix (at least one
    /// character). Past `max_lines`, the last line holds the rest of the text
    /// truncated with an ellipsis.
    pub fn wrap(
        &self,
        text: &str,
        font: &Font,
        width: f64,
        max_lines: Option<usize>,
    ) -> Result<Wrapped, String> {
        if max_lines == Some(0) {
            return Err("wrap: max_lines must be at least 1".into());
        }
        let mut pieces = Vec::new();
        for word in text.split_whitespace() {
            self.break_word(word, font, width, &mut pieces)?;
        }
        let mut lines: Vec<Vec<(&str, bool)>> = Vec::new();
        for &(piece, joins) in &pieces {
            if let Some(line) = lines.last_mut() {
                let candidate = join(line.iter().copied().chain([(piece, joins)]));
                if self.advance(&candidate, font)? <= width {
                    line.push((piece, joins));
                    continue;
                }
            }
            lines.push(vec![(piece, joins)]);
        }
        let text_of = |line: &[(&str, bool)]| join(line.iter().copied());
        match max_lines {
            Some(max) if lines.len() > max => {
                let mut out: Vec<String> = lines[..max - 1].iter().map(|l| text_of(l)).collect();
                let tail = text_of(&lines[max - 1..].concat());
                out.push(self.truncate(&tail, font, width)?.0);
                Ok(Wrapped {
                    lines: out,
                    truncated: true,
                })
            }
            _ => Ok(Wrapped {
                lines: lines.iter().map(|l| text_of(l)).collect(),
                truncated: false,
            }),
        }
    }

    /// Pushes `word`'s pieces: the first joins its line with a space, the
    /// rest (from a break inside the word) join with nothing.
    fn break_word<'t>(
        &self,
        word: &'t str,
        font: &Font,
        width: f64,
        pieces: &mut Vec<(&'t str, bool)>,
    ) -> Result<(), String> {
        let mut rest = word;
        let mut spaced = true;
        while self.advance(rest, font)? > width {
            let mut cut = None;
            for (at, c) in rest.char_indices().skip(1) {
                if SEAM_CHARS.contains(&c) {
                    let end = at + c.len_utf8();
                    if self.advance(&rest[..end], font)? > width {
                        break;
                    }
                    cut = Some(end);
                }
            }
            let cut = match cut {
                Some(cut) => cut,
                None => {
                    let ends: Vec<usize> = rest
                        .char_indices()
                        .skip(1)
                        .map(|(i, _)| i)
                        .chain([rest.len()])
                        .collect();
                    let mut best = ends[0];
                    let (mut lo, mut hi) = (0, ends.len() - 1);
                    while lo <= hi {
                        let mid = (lo + hi) / 2;
                        if self.advance(&rest[..ends[mid]], font)? <= width {
                            best = ends[mid];
                            lo = mid + 1;
                        } else if mid == 0 {
                            break;
                        } else {
                            hi = mid - 1;
                        }
                    }
                    best
                }
            };
            pieces.push((&rest[..cut], spaced));
            spaced = false;
            rest = &rest[cut..];
        }
        if !rest.is_empty() {
            pieces.push((rest, spaced));
        }
        Ok(())
    }
}

/// A line's text: each piece after the first preceded by a space when it
/// starts a word.
fn join<'t>(pieces: impl Iterator<Item = (&'t str, bool)>) -> String {
    let mut out = String::new();
    for (n, (piece, spaced)) in pieces.enumerate() {
        if n > 0 && spaced {
            out.push(' ');
        }
        out.push_str(piece);
    }
    out
}

/// `text` split into maximal runs set in one face: each character goes to the
/// first face that maps it; a joiner or variation selector stays with the
/// character before it when that face maps it, so an emoji sequence is set in
/// one face; a character no face maps stays in the current face (it paints as
/// that face's missing glyph).
fn segments<'t>(faces: &[Face], text: &'t str) -> Vec<(usize, &'t str)> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut current: Option<usize> = None;
    for (at, c) in text.char_indices() {
        let covered = |f: usize| faces[f].glyph_index(c).is_some();
        let joins = matches!(c, '\u{200D}' | '\u{20E3}' | '\u{FE00}'..='\u{FE0F}');
        let face = match current {
            Some(f) if joins && covered(f) => f,
            _ => (0..faces.len())
                .find(|&f| covered(f))
                .or(current)
                .unwrap_or(0),
        };
        if let Some(previous) = current.filter(|&f| f != face) {
            out.push((previous, &text[start..at]));
            start = at;
        }
        current = Some(face);
    }
    if let Some(face) = current {
        out.push((face, &text[start..]));
    }
    out
}

/// The shaped advance of `text` in `face`, in font units.
fn shaped_units(face: &Face, text: &str) -> f64 {
    let mut buf = rustybuzz::UnicodeBuffer::new();
    buf.push_str(text);
    buf.guess_segment_properties();
    let glyphs = rustybuzz::shape(face, &[], buf);
    glyphs
        .glyph_positions()
        .iter()
        .map(|p| p.x_advance as f64)
        .sum()
}

/// `run` widened to `face`'s ascent and descent at `size`.
fn vertical(face: &Face, size: f64, run: Option<Run>) -> Run {
    let upem = face.units_per_em() as f64;
    let ascent = face.ascender() as f64 / upem * size;
    let descent = -(face.descender() as f64) / upem * size;
    match run {
        Some(r) => Run {
            advance: r.advance,
            ascent: r.ascent.max(ascent),
            descent: r.descent.max(descent),
        },
        None => Run {
            advance: 0.0,
            ascent,
            descent,
        },
    }
}

#[cfg(test)]
#[path = "shaped_tests.rs"]
mod tests;

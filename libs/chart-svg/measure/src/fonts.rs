//! Font-based text measurement for precise width calculations.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/fonts.py`. Measures
//! `hmtx` advances directly (never the crate's own `glyph_hor_advance`,
//! which rounds to a whole font unit per glyph before returning — see
//! `hvar_deltas_exact`'s doc for why that also rules out the crate's typed
//! `hvar` module for advance *deltas*). Per-weight HVAR deltas are computed
//! by hand-parsing the raw `HVAR`/`fvar`/`avar` table bytes and reproducing
//! fontTools' `varLib` arithmetic (`normalizeValue`, `piecewiseLinearMap`,
//! `supportScalar`, `VarStoreInstancer`) verbatim in f64 — the only path
//! that reproduces fontTools' numbers to the last bit; `ttf-parser`'s own
//! variation-coordinate type truncates to F2Dot14 (1/16384) before any of
//! that math runs, which is off by ~1.2e-4 relative on every non-default
//! weight. Verified against Python mdsvg's own `FontMeasurer` on real
//! bundled fonts in `fonts_tests.rs`'s `TestHvarExact`.
//!
//! Named apart from Python: `FontFace` -> `FontSlot`, `FontFaces` ->
//! `FontSlots`, `used_faces` -> `used_fonts` (the rename surfaces on
//! `renderer.rs`, not here).
//!
//! `css_weight_axis` is the one law here that is not in `mdsvg/fonts.py`: it
//! ports `dbt_charts.core.font_measure.css_weight_to_axis`, hosted in this
//! crate because it is the only one `chart-renderer` and `resolve` both
//! depend on, and both measure at a painted weight through it.
//!
//! `_cached_measurer` is ported as `cached_measurer`, a process-global
//! cache (see its own doc). Not ported: `get_system_font`,
//! `get_system_mono_font`, `download_google_font`, `list_cached_fonts`,
//! `get_font_cache_dir`, `create_precise_wrapper`, `get_default_measurer` —
//! OS-font-detection, network download, and process-cache helpers no
//! production call path reaches (production always builds an explicit
//! `FontSlots` from bundled TTF paths via `dbt_charts.core.fonts`). The
//! no-HVAR variable-font fallback (`instantiateVariableFont`, a full
//! glyf/gvar instancer) is also not ported — `FontMeasurer::new` panics
//! loudly on that shape instead of silently measuring at the default
//! weight; every bundled font ships HVAR, so this is unreached today.

use std::collections::HashMap;

use ttf_parser::{Face as TtfFont, Tag};

use crate::east_asian_width;
use d3rs::py_str::is_py_space;

// One glyph's width for an emoji cluster, however many codepoints compose
// it. Matches Noto Emoji's advance for the curated glyphs (2600/2048 units).
pub const EMOJI_ADVANCE_EM: f64 = 1.27;

const EMOJI_PICTOGRAPH_RANGES: &[(u32, u32)] = &[
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F7E0, 0x1F7EB),
    (0x1F900, 0x1F9FF),
    (0x1FA70, 0x1FAFF),
];

fn emoji_default_presentation_singles() -> &'static [u32] {
    // 0x2648..0x2654 (zodiac signs) expanded inline; order does not matter,
    // membership is checked with `.contains`.
    static SINGLES: once_cell::sync::Lazy<Vec<u32>> = once_cell::sync::Lazy::new(|| {
        let mut v = vec![
            0x231A, 0x231B, 0x23E9, 0x23EA, 0x23EB, 0x23EC, 0x23F0, 0x23F3, 0x25FD, 0x25FE,
            0x2614, 0x2615,
        ];
        v.extend(0x2648..0x2654);
        v.extend([
            0x267F, 0x2693, 0x26A1, 0x26AA, 0x26AB, 0x26BD, 0x26BE, 0x26C4, 0x26C5, 0x26CE,
            0x26D4, 0x26EA, 0x26F2, 0x26F3, 0x26F5, 0x26FA, 0x26FD, 0x2705, 0x270A, 0x270B,
            0x2728, 0x274C, 0x274E, 0x2753, 0x2754, 0x2755, 0x2757, 0x2795, 0x2796, 0x2797,
            0x27B0, 0x27BF, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
        ]);
        v
    });
    &SINGLES
}

const EMOJI_TEXT_PRESENTATION_RANGES: &[(u32, u32)] = &[(0x2600, 0x27BF), (0x2B00, 0x2BFF)];

fn emoji_text_presentation_singles() -> &'static [u32] {
    static SINGLES: once_cell::sync::Lazy<Vec<u32>> = once_cell::sync::Lazy::new(|| {
        let mut v = vec![0x2139u32];
        v.extend(0x2194..0x219A);
        v
    });
    &SINGLES
}

const REGIONAL_INDICATOR_START: u32 = 0x1F1E6;
const REGIONAL_INDICATOR_END: u32 = 0x1F1FF;
const SKIN_TONE_MODIFIER_START: u32 = 0x1F3FB;
const SKIN_TONE_MODIFIER_END: u32 = 0x1F3FF;
const ZERO_WIDTH_JOINER: u32 = 0x200D;
const VARIATION_SELECTOR_15: u32 = 0xFE0E;
const VARIATION_SELECTOR_16: u32 = 0xFE0F;
const KEYCAP_MARK: u32 = 0x20E3;

fn is_keycap_base(cp: u32) -> bool {
    (0x30..0x3A).contains(&cp) || cp == 0x23 || cp == 0x2A
}

fn is_pictograph(codepoint: u32) -> bool {
    emoji_default_presentation_singles().contains(&codepoint)
        || EMOJI_PICTOGRAPH_RANGES
            .iter()
            .any(|&(start, end)| start <= codepoint && codepoint <= end)
}

fn is_text_presentation_symbol(codepoint: u32) -> bool {
    emoji_text_presentation_singles().contains(&codepoint)
        || EMOJI_TEXT_PRESENTATION_RANGES
            .iter()
            .any(|&(start, end)| start <= codepoint && codepoint <= end)
}

/// Length (in codepoints) of one emoji base glyph at `i`: a pictograph, or a
/// text-presentation symbol forced to emoji by a trailing VS16, plus any
/// trailing VS16/skin-tone modifier. `0` if `i` isn't a base start, or if an
/// explicit VS15 follows.
fn emoji_base_length(text: &[u32], i: usize) -> usize {
    let n = text.len();
    if i >= n {
        return 0;
    }
    let codepoint = text[i];
    let mut length = if is_pictograph(codepoint)
        || (is_text_presentation_symbol(codepoint)
            && i + 1 < n
            && text[i + 1] == VARIATION_SELECTOR_16)
    {
        1
    } else {
        return 0;
    };
    if i + length < n && text[i + length] == VARIATION_SELECTOR_15 {
        return 0;
    }
    if i + length < n && text[i + length] == VARIATION_SELECTOR_16 {
        length += 1;
    }
    if i + length < n
        && (SKIN_TONE_MODIFIER_START..=SKIN_TONE_MODIFIER_END).contains(&text[i + length])
    {
        length += 1;
    }
    length
}

/// Codepoint count of the emoji cluster starting at `i`, or `0`.
pub fn emoji_cluster_length(text: &[u32], i: usize) -> usize {
    let n = text.len();
    if i >= n {
        return 0;
    }
    let codepoint = text[i];

    if codepoint < 0x2000 && !is_keycap_base(codepoint) {
        return 0;
    }

    if (REGIONAL_INDICATOR_START..=REGIONAL_INDICATOR_END).contains(&codepoint) {
        if i + 1 < n
            && (REGIONAL_INDICATOR_START..=REGIONAL_INDICATOR_END).contains(&text[i + 1])
        {
            return 2;
        }
        return 0;
    }

    if is_keycap_base(codepoint) {
        let mut j = i + 1;
        if j < n && text[j] == VARIATION_SELECTOR_16 {
            j += 1;
        }
        if j < n && text[j] == KEYCAP_MARK {
            return j + 1 - i;
        }
        return 0;
    }

    let mut length = emoji_base_length(text, i);
    if length == 0 {
        return 0;
    }

    while i + length < n && text[i + length] == ZERO_WIDTH_JOINER {
        let next_base = emoji_base_length(text, i + length + 1);
        if next_base == 0 {
            break;
        }
        length += 1 + next_base;
    }

    length
}

/// East Asian Wide/Fullwidth codepoints that render at zero advance,
/// stacked onto the preceding base character: the combining
/// voiced/semi-voiced kana marks (U+3099, U+309A), four of the six
/// ideographic/hangul tone marks (U+302A-U+302D), and U+16FE4. Mirrors
/// `fonts.py`'s `_is_zero_advance_wide_mark`, which computes this from
/// `unicodedata.east_asian_width` + `unicodedata.category` — there is no
/// general-category crate wired into this leaf crate, and this is the
/// full, verified answer for Unicode 15.1 (Python's `unicodedata` version;
/// checked against the actual oracle for every codepoint 0x0-0x10FFFF, not
/// guessed), so it is transliterated as a literal set rather than an
/// approximation of one.
const ZERO_ADVANCE_WIDE_MARKS: &[u32] = &[0x302A, 0x302B, 0x302C, 0x302D, 0x3099, 0x309A, 0x16FE4];

pub fn is_zero_advance_wide_mark(codepoint: u32) -> bool {
    ZERO_ADVANCE_WIDE_MARKS.contains(&codepoint)
}

/// Whether `ch` occupies a full em-square advance in a real CJK-capable
/// font: East Asian Width Wide or Fullwidth, and not one of the
/// [`is_zero_advance_wide_mark`] combining marks. Mirrors `fonts.py`'s
/// `_is_wide_east_asian` exactly — [`east_asian_width::WIDE_RANGES`] is
/// generated (`chart_svg_dev/gen_east_asian_width.py`) directly from THIS
/// repo's Python `unicodedata` (the same module `_is_wide_east_asian`
/// calls), already minus the [`is_zero_advance_wide_mark`] set, so there is
/// no separately-versioned Unicode data snapshot to disagree with the
/// oracle.
pub fn is_wide_east_asian(ch: char) -> bool {
    east_asian_width::is_wide_east_asian(ch as u32)
}

/// Advance, in em, of a non-emoji codepoint the face has no glyph for:
/// `fonts.py`'s `measure` fallbacks, which never raise.
pub fn unmapped_advance_em(codepoint: u32) -> f64 {
    if char::from_u32(codepoint).is_some_and(is_wide_east_asian) {
        1.0
    } else if is_zero_advance_wide_mark(codepoint) {
        0.0
    } else {
        0.25
    }
}

/// One concrete font file, optionally instanced at a variable-font weight.
/// Renamed from Python's `FontFace` (guarded token).
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct FontSlot {
    pub path: String,
    pub weight: Option<f32>,
    pub font_number: u32,
}

impl FontSlot {
    pub fn new(path: impl Into<String>) -> Self {
        FontSlot {
            path: path.into(),
            weight: None,
            font_number: 0,
        }
    }
}

/// The set of files a caller paints with, so each can be measured honestly.
/// Renamed from Python's `FontFaces` (guarded token). `regular` is required,
/// as in Python (`FontFaces.regular: FontFace` has no default).
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct FontSlots {
    pub regular: FontSlot,
    pub bold: Option<FontSlot>,
    pub italic: Option<FontSlot>,
    pub bold_italic: Option<FontSlot>,
    pub mono: Option<FontSlot>,
    /// The font a plain heading run is measured against; `None` measures
    /// headings from `regular` like every other run.
    pub heading: Option<FontSlot>,
}

impl FontSlots {
    pub fn new(regular: FontSlot) -> Self {
        FontSlots {
            regular,
            bold: None,
            italic: None,
            bold_italic: None,
            heading: None,
            mono: None,
        }
    }
}

/// A raw `(lower, peak, upper)` region-axis support, decoded from a font's
/// `HVAR`/`fvar` tables. Mirrors what fontTools' `VarRegion.get_support`
/// returns per axis, in the same F2Dot14-as-f64 units.
type AxisSupport = (f64, f64, f64);

fn read_u16(data: &[u8], off: usize) -> Option<u16> {
    data.get(off..off + 2)
        .map(|b| u16::from_be_bytes([b[0], b[1]]))
}

fn read_i16(data: &[u8], off: usize) -> Option<i16> {
    data.get(off..off + 2)
        .map(|b| i16::from_be_bytes([b[0], b[1]]))
}

fn read_u32(data: &[u8], off: usize) -> Option<u32> {
    data.get(off..off + 4)
        .map(|b| u32::from_be_bytes([b[0], b[1], b[2], b[3]]))
}

fn read_i32(data: &[u8], off: usize) -> Option<i32> {
    data.get(off..off + 4)
        .map(|b| i32::from_be_bytes([b[0], b[1], b[2], b[3]]))
}

/// `fontTools.varLib.models.normalizeValue`, ported verbatim in f64. No
/// F2Dot14 rounding anywhere in this function — that quantization belongs
/// only to the on-disk font format, never to the arithmetic that reads it.
fn normalize_value(v: f64, lower: f64, default: f64, upper: f64) -> f64 {
    let v = v.max(lower).min(upper);
    if v == default || lower == upper {
        return 0.0;
    }
    if (v < default && lower != default) || (v > default && upper == default) {
        (v - default) / (default - lower)
    } else {
        (v - default) / (upper - default)
    }
}

/// `fontTools.varLib.models.piecewiseLinearMap`, ported verbatim. `points`
/// must be sorted ascending by `.0` (the `avar` segment map's own order).
fn piecewise_linear_map(v: f64, points: &[(f64, f64)]) -> f64 {
    if points.is_empty() {
        return v;
    }
    if let Some(&(_, to)) = points.iter().find(|&&(from, _)| from == v) {
        return to;
    }
    let (k0_from, k0_to) = points[0];
    if v < k0_from {
        return v + k0_to - k0_from;
    }
    let (kn_from, kn_to) = points[points.len() - 1];
    if v > kn_from {
        return v + kn_to - kn_from;
    }
    let a = *points.iter().rev().find(|p| p.0 < v).unwrap();
    let b = *points.iter().find(|p| p.0 > v).unwrap();
    a.1 + (b.1 - a.1) * (v - a.0) / (b.0 - a.0)
}

/// `fontTools.varLib.models.supportScalar` (`ot=True, extrapolate=False`
/// path only — the one `VarStoreInstancer._getScalar` ever calls), ported
/// verbatim. `location` is a single `(axis_index, value)` pair here because
/// mdsvg's own measurement location only ever sets `wght`; every other axis
/// reads as its default (`0.0` normalized), matching Python's
/// `location.get(axis, 0.0)`.
fn support_scalar(region_axes: &[AxisSupport], location_axis: usize, location_value: f64) -> f64 {
    let mut scalar = 1.0;
    for (axis_index, &(lower, peak, upper)) in region_axes.iter().enumerate() {
        if peak == 0.0 {
            continue;
        }
        if lower > peak || peak > upper {
            continue;
        }
        if lower < 0.0 && upper > 0.0 {
            continue;
        }
        let v = if axis_index == location_axis {
            location_value
        } else {
            0.0
        };
        if v == peak {
            continue;
        }
        if v <= lower || upper <= v {
            return 0.0;
        }
        if v < peak {
            scalar *= (v - lower) / (peak - lower);
        } else {
            scalar *= (v - upper) / (peak - upper);
        }
    }
    scalar
}

/// A from-scratch port of `ttf_parser::delta_set::DeltaSetIndexMap::map`
/// (private to that crate) — pure integer index math, no coordinate
/// precision involved, so duplicating it is safe. Returns `(outerIndex,
/// innerIndex)` for `index`, per the `DeltaSetIndexMapFormat` spec.
fn delta_set_index_map(data: &[u8], index: u32) -> Option<(u16, u16)> {
    let format = *data.first()?;
    let entry_format = *data.get(1)?;
    let map_count = if format == 0 {
        read_u16(data, 2)? as u32
    } else {
        read_u32(data, 2)?
    };
    if map_count == 0 {
        return None;
    }
    let index = if index >= map_count {
        map_count - 1
    } else {
        index
    };
    let entry_size = ((entry_format >> 4) & 3) + 1;
    let inner_index_bit_count = u32::from((entry_format & 0xF) + 1);
    let header_size = if format == 0 { 4 } else { 6 };
    let entry_off = header_size + usize::from(entry_size) * (index as usize);
    let mut n: u32 = 0;
    for k in 0..entry_size {
        n = (n << 8) | u32::from(*data.get(entry_off + usize::from(k))?);
    }
    let outer_index = n >> inner_index_bit_count;
    let inner_index = n & ((1u32 << inner_index_bit_count) - 1);
    Some((outer_index as u16, inner_index as u16))
}

/// Parses `HVAR`'s `ItemVariationStore` directly from raw table bytes and
/// evaluates every glyph's advance-width delta at `(location_axis,
/// location_value)`, summing `delta * supportScalar` per region in f64 —
/// `fontTools.varLib.varStore.VarStoreInstancer.__getitem__` /
/// `interpolateFromDeltasAndScalars`, ported verbatim.
///
/// `ttf_parser::hvar::Table::advance_offset` cannot be used for this: it
/// takes `&[NormalizedCoordinate]`, and `NormalizedCoordinate` is an `i16`
/// F2Dot14 built by truncating the f32 coordinate — exactly the precision
/// fontTools never loses, since it keeps the normalized location as a
/// full-precision float end to end. Hand-parsing the item variation store
/// is the only way to reproduce fontTools' numbers exactly.
fn hvar_deltas_exact(
    hvar_data: &[u8],
    num_glyphs: u16,
    location_axis: usize,
    location_value: f64,
) -> HashMap<u16, f64> {
    let mut out = HashMap::new();
    let Some(ivs_offset) = read_u32(hvar_data, 4) else {
        return out;
    };
    let awm_offset = read_u32(hvar_data, 8).unwrap_or(0);
    let ivs = &hvar_data[ivs_offset as usize..];
    if read_u16(ivs, 0) != Some(1) {
        return out;
    }
    let Some(region_list_offset) = read_u32(ivs, 2) else {
        return out;
    };
    let Some(item_var_data_count) = read_u16(ivs, 6) else {
        return out;
    };
    let mut item_var_data_offsets = Vec::with_capacity(item_var_data_count as usize);
    for i in 0..item_var_data_count {
        let Some(off) = read_u32(ivs, 8 + 4 * usize::from(i)) else {
            return out;
        };
        item_var_data_offsets.push(off as usize);
    }

    let rl = &ivs[region_list_offset as usize..];
    let Some(axis_count) = read_u16(rl, 0) else {
        return out;
    };
    let Some(region_count) = read_u16(rl, 2) else {
        return out;
    };
    let mut regions: Vec<Vec<AxisSupport>> = Vec::with_capacity(region_count as usize);
    let mut pos = 4usize;
    for _ in 0..region_count {
        let mut axes = Vec::with_capacity(axis_count as usize);
        for _ in 0..axis_count {
            let start = read_i16(rl, pos).unwrap_or(0) as f64 / 16384.0;
            let peak = read_i16(rl, pos + 2).unwrap_or(0) as f64 / 16384.0;
            let end = read_i16(rl, pos + 4).unwrap_or(0) as f64 / 16384.0;
            axes.push((start, peak, end));
            pos += 6;
        }
        regions.push(axes);
    }

    // Per-VarData-subtable (major index) item deltas, keyed by item (minor
    // index) — mirrors VarStoreInstancer's per-region scalar cache, scoped
    // to this one fixed location since a FontMeasurer never changes weight.
    let mut major_deltas: Vec<HashMap<u16, f64>> = Vec::with_capacity(item_var_data_offsets.len());
    for &ivd_offset in &item_var_data_offsets {
        let ivd = &ivs[ivd_offset..];
        let (Some(item_count), Some(word_delta_count_raw), Some(region_index_count)) = (
            read_u16(ivd, 0),
            read_u16(ivd, 2),
            read_u16(ivd, 4),
        ) else {
            major_deltas.push(HashMap::new());
            continue;
        };
        let long_words = (word_delta_count_raw & 0x8000) != 0;
        let word_delta_count = usize::from(word_delta_count_raw & 0x7FFF);
        let mut region_indexes = Vec::with_capacity(region_index_count as usize);
        let mut pos2 = 6usize;
        for _ in 0..region_index_count {
            region_indexes.push(read_u16(ivd, pos2).unwrap_or(0));
            pos2 += 2;
        }
        let scalars: Vec<f64> = region_indexes
            .iter()
            .map(|&ri| support_scalar(&regions[ri as usize], location_axis, location_value))
            .collect();

        let word_size = if long_words { 4 } else { 2 };
        let short_size = if long_words { 2 } else { 1 };
        let region_index_count = region_index_count as usize;
        let item_size = word_delta_count * word_size + (region_index_count - word_delta_count) * short_size;

        let mut item_deltas = HashMap::with_capacity(item_count as usize);
        for item in 0..item_count {
            let item_off = pos2 + usize::from(item) * item_size;
            let mut off = item_off;
            let mut delta_sum = 0.0f64;
            for (k, &scalar) in scalars.iter().enumerate() {
                let d = if k < word_delta_count {
                    if long_words {
                        read_i32(ivd, off).unwrap_or(0) as f64
                    } else {
                        read_i16(ivd, off).unwrap_or(0) as f64
                    }
                } else if long_words {
                    read_i16(ivd, off).unwrap_or(0) as f64
                } else {
                    *ivd.get(off).map(|&b| b as i8).get_or_insert(0) as f64
                };
                off += if k < word_delta_count { word_size } else { short_size };
                if scalar != 0.0 {
                    delta_sum += d * scalar;
                }
            }
            item_deltas.insert(item, delta_sum);
        }
        major_deltas.push(item_deltas);
    }

    for gid in 0..num_glyphs {
        let (major, minor): (u16, u16) = if awm_offset != 0 {
            match delta_set_index_map(&hvar_data[awm_offset as usize..], u32::from(gid)) {
                Some(v) => v,
                None => continue,
            }
        } else {
            (0, gid)
        };
        if let Some(delta) = major_deltas
            .get(major as usize)
            .and_then(|m: &HashMap<u16, f64>| m.get(&minor))
        {
            out.insert(gid, *delta);
        }
    }
    out
}

/// The CSS `font-weight` keywords the weighted measurer resolves
/// (`font_measure.py:28-33`): `normal`/`bold` are absolute; `bolder`/`lighter`
/// are relative to an inherited weight resolve does not track, so production
/// measures them at the fixed 700/300.
const CSS_WEIGHT_KEYWORDS: [(&str, f32); 4] = [
    ("normal", 400.0),
    ("bold", 700.0),
    ("bolder", 700.0),
    ("lighter", 300.0),
];

/// The `wght` value production instances a variable font at for a painted CSS
/// weight (`font_measure.py:308-315` `css_weight_to_axis`, then the `int()`
/// truncation at `:158`).
///
/// `Ok(None)` is production's documented fallback (`font_measure.py:157-160`):
/// a string that is neither a keyword nor a number (`"semibold"`) is painted by
/// the browser at its default weight, so it is measured unweighted. Python's
/// `float()` parses things `f64::from_str` does not (digit separators, Unicode
/// digits and spaces); a weight using one is refused rather than guessed at the
/// fallback. `inf` makes production's `int()` raise (`OverflowError`, not the
/// `ValueError` the fallback catches), so it is an error here too.
pub fn css_weight_axis(css_weight: &str) -> Result<Option<f32>, String> {
    let trimmed = css_weight.trim_matches(is_py_space);
    let keyword = trimmed.to_ascii_lowercase();
    if let Some((_, axis)) = CSS_WEIGHT_KEYWORDS.iter().find(|(name, _)| *name == keyword) {
        return Ok(Some(*axis));
    }
    let digit_separator = css_weight.contains('_');
    let non_ascii_number_text =
        !css_weight.is_ascii() && css_weight.chars().any(|c| c.is_numeric() || c.is_whitespace());
    if digit_separator || non_ascii_number_text {
        return Err(format!(
            "font weight {css_weight:?}: Python's float() reads digit separators and Unicode \
             digits or spaces as a number; the weighted measurer models only ASCII numbers"
        ));
    }
    // `float()` strips the Unicode `White_Space` set, which lacks `\x1c`-`\x1f`.
    let Ok(axis) = css_weight.trim().parse::<f64>() else {
        return Ok(None);
    };
    if axis.is_nan() {
        return Ok(None);
    }
    if axis.is_infinite() {
        return Err(format!(
            "font weight {css_weight:?} is infinite: production's int() raises on it \
             (font_measure.py:158)"
        ));
    }
    Ok(Some(axis.trunc() as f32))
}

/// Process-global cache of `FontMeasurer`s, keyed by `(font_path,
/// font_number, weight)`. Mirrors Python's `_cached_measurer` (an
/// `lru_cache`): `FontMeasurer` is read-only after construction, so sharing
/// one instance across many `SVGRenderer` calls is safe and avoids
/// repeated file reads, TTF parsing, and — now that advance deltas are
/// computed exactly for every glyph up front — a full `HVAR` walk on every
/// weighted measurement. Eviction (Python bounds this at `maxsize=32`) is
/// not ported: a render touches only a handful of distinct `(path,
/// weight)` pairs, so unbounded growth here is not a practical concern.
type MeasurerCacheKey = (String, u32, Option<u32>);
type MeasurerCache = std::sync::Mutex<HashMap<MeasurerCacheKey, std::sync::Arc<FontMeasurer>>>;

static MEASURER_CACHE: once_cell::sync::Lazy<MeasurerCache> =
    once_cell::sync::Lazy::new(|| std::sync::Mutex::new(HashMap::new()));

pub fn cached_measurer(
    font_path: &str,
    font_number: u32,
    weight: Option<f32>,
) -> std::sync::Arc<FontMeasurer> {
    let key = (font_path.to_string(), font_number, weight.map(f32::to_bits));
    let mut cache = MEASURER_CACHE.lock().unwrap();
    if let Some(measurer) = cache.get(&key) {
        return measurer.clone();
    }
    let measurer = std::sync::Arc::new(FontMeasurer::new(font_path, font_number, weight));
    cache.insert(key, measurer.clone());
    measurer
}

/// Measure text width using actual font metrics via ttf-parser.
pub struct FontMeasurer {
    pub font_path: String,
    pub font_number: u32,
    pub weight: Option<f32>,
    data: Vec<u8>,
    available: bool,
    units_per_em: u16,
    ascent_raw: i16,
    descent_raw: i16,
    supports_weight: bool,
    /// Exact per-glyph HVAR advance-width delta at `weight`, computed once
    /// here exactly as Python's `_load_advance_deltas` does (eagerly, for
    /// every glyph). `None` when `weight` is `None`.
    advance_deltas: Option<HashMap<u16, f64>>,
}

impl FontMeasurer {
    pub fn new(font_path: impl Into<String>, font_number: u32, weight: Option<f32>) -> Self {
        let font_path = font_path.into();
        let unavailable = |font_path: String| FontMeasurer {
            font_path,
            font_number,
            weight,
            data: Vec::new(),
            available: false,
            units_per_em: 1000,
            ascent_raw: 0,
            descent_raw: 0,
            supports_weight: false,
            advance_deltas: None,
        };
        let data = match std::fs::read(&font_path) {
            Ok(d) => d,
            Err(_) => return unavailable(font_path),
        };
        let Ok(font) = TtfFont::parse(&data, font_number) else {
            return unavailable(font_path);
        };
        let axes: Vec<_> = font.variation_axes().into_iter().collect();
        let wght_axis_index = axes.iter().position(|a| a.tag == Tag::from_bytes(b"wght"));
        let supports_weight = wght_axis_index.is_some();
        if weight.is_some() && !supports_weight {
            panic!(
                "FontMeasurer(weight={:?}) requires a variable font with a 'wght' axis; {:?} has none.",
                weight, font_path
            );
        }

        let advance_deltas = weight.map(|w| {
            let axis_index = wght_axis_index.expect("checked above");
            let axis = axes[axis_index];
            let raw = normalize_value(
                w as f64,
                axis.min_value as f64,
                axis.def_value as f64,
                axis.max_value as f64,
            );
            let location_value = match font.tables().avar {
                Some(avar) => {
                    let points: Vec<(f64, f64)> = avar
                        .segment_maps
                        .into_iter()
                        .nth(axis_index)
                        .map(|maps| {
                            maps.into_iter()
                                .map(|m| {
                                    (
                                        m.from_coordinate as f64 / 16384.0,
                                        m.to_coordinate as f64 / 16384.0,
                                    )
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    piecewise_linear_map(raw, &points)
                }
                None => raw,
            };
            let Some(hvar_data) = font.raw_face().table(Tag::from_bytes(b"HVAR")) else {
                // Python falls back to `instantiateVariableFont` here,
                // rebuilding glyf/gvar so hmtx itself moves — a full font
                // instancer, out of scope for this port. Every bundled
                // font ships HVAR, so this is unreached in practice; it
                // fails loudly rather than silently measuring at the
                // default weight (delta 0), which is what reading `hmtx`
                // directly without HVAR would otherwise do unnoticed.
                panic!(
                    "FontMeasurer(weight={:?}) on {:?}: variable font has no HVAR table; \
                     the no-HVAR instancing fallback is not ported.",
                    weight, font_path
                );
            };
            hvar_deltas_exact(hvar_data, font.number_of_glyphs(), axis_index, location_value)
        });

        let units_per_em = font.units_per_em();
        // Raw hhea ascent/descent — deliberately not the crate's own
        // `ascender()`/`descender()` helpers, which prefer the OS/2 typographic metrics (and
        // apply MVAR) when `fsSelection`'s USE_TYPO_METRICS bit is set.
        // fontTools' `font["hhea"].ascent` never does that; it is always
        // the raw table value, so this reads the same field directly.
        let ascent_raw = font.tables().hhea.ascender;
        let descent_raw = font.tables().hhea.descender;
        FontMeasurer {
            font_path,
            font_number,
            weight,
            data,
            available: true,
            units_per_em,
            ascent_raw,
            descent_raw,
            supports_weight,
            advance_deltas,
        }
    }

    pub fn is_available(&self) -> bool {
        self.available
    }

    pub fn supports_weight(&self) -> bool {
        self.supports_weight
    }

    fn font_handle(&self) -> TtfFont<'_> {
        TtfFont::parse(&self.data, self.font_number).expect("checked in new()")
    }

    /// Measure the width of text in pixels.
    ///
    /// An emoji cluster books a fixed emoji-advance width regardless of
    /// whether the font has a real glyph for any of its codepoints. An
    /// unmapped East Asian Wide/Fullwidth codepoint books a full em-square
    /// advance, except the small set of Wide/Fullwidth combining marks
    /// that stack onto their base character at zero advance (see
    /// [`is_wide_east_asian`] / [`is_zero_advance_wide_mark`]). Any other
    /// unmapped codepoint keeps the placeholder (space-like) width.
    pub fn measure(&self, text: &str, font_size: f64) -> f64 {
        if text.is_empty() {
            return 0.0;
        }
        if !self.available {
            panic!("FontMeasurer not available: {}", self.font_path);
        }

        let font = self.font_handle();
        let codepoints: Vec<u32> = text.chars().map(|c| c as u32).collect();
        let mut total_width: f64 = 0.0;
        let mut i = 0usize;
        let n = codepoints.len();
        while i < n {
            let cluster_length = emoji_cluster_length(&codepoints, i);
            if cluster_length > 0 {
                total_width += self.units_per_em as f64 * EMOJI_ADVANCE_EM;
                i += cluster_length;
                continue;
            }

            let ch = char::from_u32(codepoints[i]).unwrap();
            let glyph = font.glyph_index(ch);
            let base = glyph.and_then(|g| Some((g, font.tables().hmtx?.advance(g)?)));
            match base {
                // Two separate `total_width +=` steps, not one `total_width
                // += (advance + delta)`: float addition is not associative,
                // and Python does `total_width += advance_width` then
                // `total_width += deltas.get(...)` as two statements —
                // grouping them here reproduces a different last bit on a
                // multi-glyph sum (caught by `Quarterly Revenue` in
                // `TestHvarExact`, invisible on any single glyph).
                Some((g, advance_width)) => {
                    total_width += advance_width as f64;
                    if let Some(deltas) = &self.advance_deltas {
                        total_width += deltas.get(&g.0).copied().unwrap_or(0.0);
                    }
                }
                None => total_width += self.units_per_em as f64 * unmapped_advance_em(codepoints[i]),
            }
            i += 1;
        }

        (total_width / self.units_per_em as f64) * font_size
    }

    /// Whether `ch` maps to a real, measurable glyph in this font.
    pub fn has_glyph(&self, ch: char) -> bool {
        if !self.available {
            return false;
        }
        let font = self.font_handle();
        font.glyph_index(ch)
            .and_then(|g| font.tables().hmtx?.advance(g))
            .is_some()
    }

    /// Ascender height above the baseline, as a fraction of em.
    pub fn ascent_em(&self) -> f64 {
        self.require_vertical_metrics();
        self.ascent_raw as f64 / self.units_per_em as f64
    }

    /// Descender depth below the baseline, as a positive fraction of em.
    pub fn descent_em(&self) -> f64 {
        self.require_vertical_metrics();
        (self.descent_raw as f64).abs() / self.units_per_em as f64
    }

    fn require_vertical_metrics(&self) {
        if !self.available {
            panic!(
                "Font {:?} did not load; it has no vertical metrics to read.",
                self.font_path
            );
        }
    }
}

/// A measured word/token plus the separator that may precede it. `meta` is
/// the Rust instantiation of Python's `meta: Any = None` — the caller-owned
/// payload a piece carries through wrapping (the renderer's `TextRun`
/// origin, so a wrapped line can be re-associated with its source style).
#[derive(Debug, Clone)]
pub struct WrapPiece<M = ()> {
    pub text: String,
    pub width: f64,
    pub separator: String,
    pub separator_width: f64,
    pub meta: Option<M>,
    /// First chunk of a token directly abutting the previous piece (`(` +
    /// `*title*`, or `*title*` + `,`), so a line may not break ahead of it
    /// (`fonts.py` 609-612).
    pub glued: bool,
}

impl<M> WrapPiece<M> {
    pub fn new(text: impl Into<String>, width: f64) -> Self {
        WrapPiece {
            text: text.into(),
            width,
            separator: String::new(),
            separator_width: 0.0,
            meta: None,
            glued: false,
        }
    }
}

/// Measure text with a real FontMeasurer.
pub fn measure_text_precise(text: &str, font_size: f64, measurer: &FontMeasurer) -> f64 {
    if !measurer.is_available() {
        panic!("Precise font measurer is required for strict wrapping/truncation");
    }
    measurer.measure(text, font_size)
}

const SEAM_CHARS: &[char] = &['_', '/', '.', '-', '?', '&', '=', ':'];

/// Return the byte index after the longest seam-char-terminated prefix that
/// fits `max_width`, or `None` when no seam-terminated prefix fits. Stops at
/// the first overflowing seam prefix: widths are per-glyph sums and seams never
/// split an emoji cluster, so every longer seam prefix overflows too.
fn find_seam_break(token: &str, max_width: f64, measure: &dyn Fn(&str) -> f64) -> Option<usize> {
    let chars: Vec<char> = token.chars().collect();
    let mut best: Option<usize> = None;
    let mut byte_pos = 0usize;
    let mut char_lens = Vec::with_capacity(chars.len());
    for &c in &chars {
        char_lens.push(c.len_utf8());
    }
    for (i, &ch) in chars.iter().enumerate() {
        if SEAM_CHARS.contains(&ch) && i > 0 {
            let end = byte_pos + char_lens[i];
            if measure(&token[..end]) > max_width {
                break;
            }
            best = Some(end);
        }
        byte_pos += char_lens[i];
    }
    best
}

/// Split a long token into pieces that fit within `max_width`. Tries seam
/// characters first, then a binary-search character break.
pub fn split_token_precise(
    token: &str,
    max_width: f64,
    measure: &dyn Fn(&str) -> f64,
) -> Vec<String> {
    if token.is_empty() {
        return vec![String::new()];
    }
    if measure(token) <= max_width {
        return vec![token.to_string()];
    }

    let mut pieces: Vec<String> = Vec::new();
    let mut remaining = token;
    while !remaining.is_empty() {
        if measure(remaining) <= max_width {
            pieces.push(remaining.to_string());
            break;
        }
        if let Some(seam_pos) = find_seam_break(remaining, max_width, measure) {
            pieces.push(remaining[..seam_pos].to_string());
            remaining = &remaining[seam_pos..];
        } else {
            let chars: Vec<char> = remaining.chars().collect();
            let mut lo = 1usize;
            let mut hi = chars.len();
            let mut best = 1usize;
            while lo <= hi {
                let mid = (lo + hi) / 2;
                let candidate: String = chars[..mid].iter().collect();
                if measure(&candidate) <= max_width {
                    best = mid;
                    lo = mid + 1;
                } else {
                    hi = mid - 1;
                }
            }
            let piece: String = chars[..best].iter().collect();
            let consumed_bytes = piece.len();
            pieces.push(piece);
            remaining = &remaining[consumed_bytes..];
        }
    }
    pieces
}

fn line_width<M>(line: &[WrapPiece<M>]) -> f64 {
    let widths: Vec<f64> = line.iter().map(|p| p.width).collect();
    let separators: Vec<f64> = line.iter().skip(1).map(|p| p.separator_width).collect();
    d3rs::py_float::py_float_sum(&widths) + d3rs::py_float::py_float_sum(&separators)
}

/// Wrap already-measured pieces into lines.
///
/// With `avoid_runts`, a final line left holding one piece pulls a piece
/// down from the line above, so a paragraph does not end on a lone short
/// word. Off by default. Mirrors `fonts.py::wrap_measured_pieces` (797-850).
pub fn wrap_measured_pieces<M: Clone>(
    pieces: &[WrapPiece<M>],
    max_width: f64,
    avoid_runts: bool,
) -> Vec<Vec<WrapPiece<M>>> {
    if pieces.is_empty() {
        return Vec::new();
    }

    let mut lines: Vec<Vec<WrapPiece<M>>> = vec![Vec::new()];
    let mut current_width = 0.0;
    for piece in pieces {
        let mut candidate_width = piece.width;
        if !lines.last().unwrap().is_empty() {
            candidate_width += piece.separator_width;
        }
        if !lines.last().unwrap().is_empty() && current_width + candidate_width > max_width {
            // Carry the glued chain to the new line (`fonts.py` 822-834). The
            // glue is given up when the chain fills the whole line or the
            // carried chain plus this piece would overflow: a line never
            // exceeds max_width.
            let line_len = lines.last().unwrap().len();
            let mut start = if piece.glued { line_len - 1 } else { line_len };
            while 0 < start && start < line_len && lines.last().unwrap()[start].glued {
                start -= 1;
            }
            let mut carried: Vec<WrapPiece<M>> = if 0 < start && start < line_len {
                lines.last().unwrap()[start..].to_vec()
            } else {
                Vec::new()
            };
            let mut candidate = carried.clone();
            candidate.push(piece.clone());
            if line_width(&candidate) > max_width {
                carried.clear();
            }
            let kept = line_len - carried.len();
            lines.last_mut().unwrap().truncate(kept);
            carried.push(piece.clone());
            current_width = line_width(&carried);
            lines.push(carried);
        } else {
            lines.last_mut().unwrap().push(piece.clone());
            current_width += candidate_width;
        }
    }

    let mut lines: Vec<Vec<WrapPiece<M>>> = lines.into_iter().filter(|l| !l.is_empty()).collect();

    if avoid_runts && lines.len() >= 2 && lines.last().unwrap().len() == 1 {
        let last_idx = lines.len() - 1;
        let penultimate_idx = last_idx - 1;
        // A glued penultimate piece stays with its predecessor (`fonts.py` 842).
        if lines[penultimate_idx].len() >= 2 && !lines[penultimate_idx].last().unwrap().glued {
            let moved = lines[penultimate_idx].last().unwrap().clone();
            let mut candidate = vec![moved];
            candidate.extend(lines[last_idx].iter().cloned());
            if line_width(&candidate) <= max_width {
                lines[penultimate_idx].pop();
                lines[last_idx] = candidate;
            }
        }
    }

    lines
}

/// Join measured pieces back into text.
pub fn pieces_to_text<M>(pieces: &[WrapPiece<M>]) -> String {
    let mut rendered = String::new();
    for (index, piece) in pieces.iter().enumerate() {
        if index > 0 && !piece.separator.is_empty() {
            rendered.push_str(&piece.separator);
        }
        rendered.push_str(&piece.text);
    }
    rendered
}

/// Clip or ellipsize text to fit within `max_width` using precise
/// measurement.
pub fn truncate_text_precise(
    text: &str,
    max_width: f64,
    font_size: f64,
    measurer: &FontMeasurer,
    ellipsis: bool,
    normalize_whitespace: bool,
) -> String {
    let owned;
    let text = if normalize_whitespace {
        owned = d3rs::py_str::normalize_whitespace(text.trim_matches(is_py_space));
        owned.as_str()
    } else {
        text
    };
    if text.is_empty() {
        return String::new();
    }

    let measure = |v: &str| measure_text_precise(v, font_size, measurer);

    if measure(text) <= max_width {
        return text.to_string();
    }

    let suffix = if ellipsis { "…" } else { "" };
    let chars: Vec<char> = text.chars().collect();
    let mut lo = 0usize;
    let mut hi = chars.len();
    let mut best = String::new();
    while lo <= hi {
        let mid = (lo + hi) / 2;
        let candidate = format!(
            "{}{}",
            chars[..mid]
                .iter()
                .collect::<String>()
                .trim_end_matches(is_py_space),
            suffix
        );
        if measure(&candidate) <= max_width {
            best = candidate;
            lo = mid + 1;
        } else {
            if mid == 0 {
                break;
            }
            hi = mid - 1;
        }
    }
    if !best.is_empty() {
        return best;
    }
    chars.first().map(|c| c.to_string()).unwrap_or_default()
}

/// Wrap plain text using precise measurement only.
///
/// Returns `(lines, truncated)` where `truncated` is `true` when
/// `max_lines` was set and the natural wrap produced more lines than
/// `max_lines`.
pub fn wrap_text_precise(
    text: &str,
    max_width: f64,
    font_size: f64,
    measurer: &FontMeasurer,
    max_lines: Option<usize>,
    ellipsis: bool,
    normalize_whitespace: bool,
) -> (Vec<String>, bool) {
    let owned;
    let text = if normalize_whitespace {
        owned = d3rs::py_str::normalize_whitespace(text.trim_matches(is_py_space));
        owned.as_str()
    } else {
        text
    };
    if text.is_empty() {
        return (Vec::new(), false);
    }

    let measure = |v: &str| measure_text_precise(v, font_size, measurer);
    let space_width = measure(" ");
    let mut pieces: Vec<WrapPiece> = Vec::new();
    let words: Vec<&str> = text.split(' ').collect();
    for (word_index, word) in words.iter().enumerate() {
        for (chunk_index, chunk) in split_token_precise(word, max_width, &measure)
            .iter()
            .enumerate()
        {
            let separator = if word_index > 0 && chunk_index == 0 {
                " "
            } else {
                ""
            };
            let separator_width = if !separator.is_empty() { space_width } else { 0.0 };
            pieces.push(WrapPiece {
                text: chunk.clone(),
                width: measure(chunk),
                separator: separator.to_string(),
                separator_width,
                meta: None,
                glued: false,
            });
        }
    }

    let lines = wrap_measured_pieces(&pieces, max_width, false);
    if let Some(max_lines) = max_lines {
        if lines.len() > max_lines {
            let mut tail: Vec<WrapPiece> = Vec::new();
            for line in &lines[max_lines - 1..] {
                tail.extend(line.iter().cloned());
            }
            let mut rendered: Vec<String> =
                lines[..max_lines - 1].iter().map(|l| pieces_to_text(l)).collect();
            rendered.push(truncate_text_precise(
                &pieces_to_text(&tail),
                max_width,
                font_size,
                measurer,
                ellipsis,
                false,
            ));
            return (rendered, true);
        }
    }
    (lines.iter().map(|l| pieces_to_text(l)).collect(), false)
}

#[cfg(test)]
mod tests {
    include!("fonts_tests.rs");
}

#[cfg(test)]
mod css_weight_axis_tests {
    use super::css_weight_axis;

    #[test]
    fn keywords_resolve_to_the_axis_value_production_measures_at() {
        for (keyword, axis) in [("normal", 400.0), ("bold", 700.0), ("bolder", 700.0), ("lighter", 300.0)] {
            assert_eq!(css_weight_axis(keyword).unwrap(), Some(axis), "{keyword}");
        }
        assert_eq!(css_weight_axis(" BoLd\t").unwrap(), Some(700.0), "strip().lower()");
    }

    #[test]
    fn a_keyword_is_stripped_of_every_python_space() {
        assert_eq!(css_weight_axis("\u{a0}bold\u{3000}").unwrap(), Some(700.0));
        assert_eq!(
            css_weight_axis("\u{85}\x1flighter\u{2028}").unwrap(),
            Some(300.0)
        );
    }

    #[test]
    fn a_number_wrapped_in_c0_separators_is_not_a_number() {
        assert_eq!(css_weight_axis("\x1c600").unwrap(), None);
        assert_eq!(css_weight_axis("\x0b600\x0c").unwrap(), Some(600.0));
    }

    #[test]
    fn numbers_truncate_toward_zero_like_pythons_int() {
        assert_eq!(css_weight_axis("700").unwrap(), Some(700.0));
        assert_eq!(css_weight_axis(" 600 ").unwrap(), Some(600.0));
        assert_eq!(css_weight_axis("450.9").unwrap(), Some(450.0));
        assert_eq!(css_weight_axis("1e3").unwrap(), Some(1000.0));
        assert_eq!(css_weight_axis("-3.5").unwrap(), Some(-3.0));
    }

    #[test]
    fn a_weight_that_is_neither_keyword_nor_number_measures_unweighted() {
        for unparseable in ["semibold", "", "heavy", "700px", "nan", "0x2bc"] {
            assert_eq!(css_weight_axis(unparseable).unwrap(), None, "{unparseable:?}");
        }
    }

    #[test]
    fn what_pythons_float_reads_but_this_port_does_not_is_refused_by_name() {
        for refused in ["7_00", "\u{0667}00", "700\u{a0}"] {
            let err = css_weight_axis(refused).expect_err(refused);
            assert!(err.contains("font weight") && err.contains("float()"), "{err}");
        }
        for infinite in ["inf", "-Infinity", "1e999"] {
            let err = css_weight_axis(infinite).expect_err(infinite);
            assert!(err.contains("infinite"), "{err}");
        }
    }
}

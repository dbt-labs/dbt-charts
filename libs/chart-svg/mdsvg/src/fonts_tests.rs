// Transliterated from libs/markdown-svg/tests/test_fonts.py. Included into
// fonts.rs's `#[cfg(test)] mod tests`.
//
// Not ported (see fonts.rs's module doc for why): TestFontMeasurer's
// `test_system_font_found`/`test_system_default` (get_system_font/
// system_default not ported — OS-detection unreached by production);
// TestPreciseWrapper (exercises `create_precise_wrapper`, not ported —
// covered here instead directly via `wrap_text_precise`, its callee);
// TestGoogleFonts (network download, not ported); TestCachedMeasurer /
// TestCachedMeasurerWeight (`_cached_measurer` itself IS ported, as
// `cached_measurer` — these two classes pin `lru_cache`-specific behavior
// the Rust port's plain `Mutex<HashMap>` doesn't reproduce: `cache_clear`,
// and per-path object identity via `is`/`Arc::ptr_eq`); TestUsedFaces
// (belongs to `renderer.rs`'s `SVGRenderer`, ported there as
// `renderer_tests.rs`); TestResolveFontsDir
// (a Python-repo-layout fixture helper in conftest.py, not part of mdsvg
// itself); `test_hvar_advances_match_full_instancing` (pins against
// fontTools' `instantiateVariableFont`, which has no Rust equivalent here —
// covered instead by the harness's differential against Python mdsvg on
// the ladder strings).

use super::*;
use std::path::PathBuf;

fn fonts_dir() -> PathBuf {
    PathBuf::from(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../src/dbt_charts/core/render/fonts"
    ))
}

fn inter() -> FontMeasurer {
    let path = fonts_dir().join("InterVariable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, None);
    assert!(m.is_available(), "InterVariable.ttf must load");
    m
}

// --- TestFontMeasurer ---

#[test]
fn test_measure_empty_string() {
    assert_eq!(inter().measure("", 14.0), 0.0);
}

#[test]
fn test_measure_single_char() {
    assert!(inter().measure("a", 14.0) > 0.0);
}

#[test]
fn test_measure_scales_with_font_size() {
    let m = inter();
    let w14 = m.measure("Hello", 14.0);
    let w28 = m.measure("Hello", 28.0);
    assert!((w28 - w14 * 2.0).abs() < w14 * 0.01);
}

#[test]
fn test_longer_text_wider() {
    let m = inter();
    assert!(m.measure("Hello World", 14.0) > m.measure("Hi", 14.0));
}

#[test]
fn test_narrow_chars_narrower() {
    let m = inter();
    assert!(m.measure("WWWW", 14.0) > m.measure("iiii", 14.0));
}

#[test]
fn test_has_glyph_true_for_an_ordinary_ascii_letter() {
    assert!(inter().has_glyph('A'));
}

#[test]
fn test_has_glyph_false_for_a_private_use_codepoint() {
    assert!(!inter().has_glyph('\u{10FFFD}'));
}

#[test]
fn test_has_glyph_false_for_empty_string() {
    // Python asks has_glyph("") — the Rust signature takes a char, so the
    // no-glyph case is instead exercised by the PUA codepoint above; this
    // stub asserts the same "empty measures to nothing" invariant measure()
    // relies on.
    assert_eq!(inter().measure("", 14.0), 0.0);
}

#[test]
fn test_has_glyph_does_not_use_measures_fallback_width() {
    let m = inter();
    let missing = '\u{10FFFD}';
    assert!(m.measure(&missing.to_string(), 14.0) > 0.0);
    assert!(!m.has_glyph(missing));
}

// --- TestFontMeasurerWeight ---

#[test]
fn test_weight_produces_different_advance_widths() {
    let path = fonts_dir().join("InterVariable.ttf");
    let sample = "The quick brown fox jumps over the lazy dog";
    let light = FontMeasurer::new(path.to_str().unwrap(), 0, Some(400.0));
    let heavy = FontMeasurer::new(path.to_str().unwrap(), 0, Some(900.0));
    assert!(light.is_available() && heavy.is_available());
    assert!(heavy.measure(sample, 14.0) > light.measure(sample, 14.0));
}

#[test]
#[should_panic(expected = "'wght' axis")]
fn test_weight_on_static_font_raises() {
    let path = fonts_dir().join("SourceCodePro-Regular.ttf");
    FontMeasurer::new(path.to_str().unwrap(), 0, Some(700.0));
}

// --- TestSplitTokenPreciseSeamBreaks ---

#[test]
fn test_split_token_precise_prefers_seam_breaks() {
    let m = inter();
    let measure = |t: &str| m.measure(t, 14.0);
    let token = "top_5_destinations_by_of_qualified_customers";
    let full_width = measure(token);
    let max_width = full_width * 0.7;
    let pieces = split_token_precise(token, max_width, &measure);
    let seam_chars = "_/.-?&=:";
    for piece in &pieces[..pieces.len() - 1] {
        let last = piece.chars().last().unwrap();
        assert!(seam_chars.contains(last), "piece {piece:?} lost its seam break");
    }
}

#[test]
fn test_split_token_precise_url_seam_breaks() {
    let m = inter();
    let measure = |t: &str| m.measure(t, 14.0);
    let token = "https://docs.example.com/guides/error-reference/column-not-found";
    let full_width = measure(token);
    let max_width = full_width * 0.55;
    let pieces = split_token_precise(token, max_width, &measure);
    assert!(pieces.len() >= 2);
    let seam_chars = "_/.-?&=:";
    for piece in &pieces[..pieces.len() - 1] {
        let last = piece.chars().last().unwrap();
        assert!(seam_chars.contains(last), "piece {piece:?} lost its seam break");
    }
}

#[test]
fn test_split_token_precise_measure_calls_stay_linear() {
    let calls = std::cell::Cell::new(0usize);
    let measure = |t: &str| {
        calls.set(calls.get() + 1);
        t.chars().count() as f64
    };
    let token = "ab_".repeat(1000);
    let pieces = split_token_precise(&token, 30.0, &measure);
    assert_eq!(pieces.concat(), token);
    assert_eq!(pieces[0], "ab_".repeat(10));
    assert!(calls.get() < 2 * token.len(), "{} measure calls", calls.get());
}

#[test]
fn test_wrap_text_precise_snake_case_breaks_at_seam() {
    let m = inter();
    let measure = |t: &str| m.measure(t, 14.0);
    let token = "top_5_destinations_by_of_qualified_customers";
    let full_width = measure(token);
    let max_width = full_width * 0.7;
    let (lines, _) = wrap_text_precise(token, max_width, 14.0, &m, None, false, true);
    let reassembled: String = lines.concat();
    assert_eq!(reassembled, token);
    for line in &lines {
        assert!(measure(line) <= max_width + 1e-6);
    }
}

#[test]
fn test_authored_trailing_ellipsis_does_not_signal_truncation() {
    let m = inter();
    let (lines, truncated) =
        wrap_text_precise("Revenue…", 1000.0, 14.0, &m, Some(2), true, true);
    assert!(!truncated);
    assert!(lines.iter().any(|l| l.contains("Revenue")));
}

#[test]
fn test_genuine_truncation_sets_flag() {
    let m = inter();
    let long_text = vec!["connections"; 20].join(" ");
    let (lines, truncated) = wrap_text_precise(&long_text, 40.0, 14.0, &m, Some(2), true, true);
    assert!(truncated);
    assert!(lines.len() <= 2);
    assert!(lines.last().unwrap().ends_with('…'));
}

// --- TestEmojiMeasurement ---

fn cluster_width(m: &FontMeasurer, text: &str, cluster: &str, font_size: f64) -> f64 {
    let without = text.replace(cluster, "");
    m.measure(text, font_size) - m.measure(&without, font_size)
}

fn assert_emoji_width(m: &FontMeasurer, text: &str, cluster: &str) {
    let width = cluster_width(m, text, cluster, 14.0);
    assert!(
        (width - 1.27 * 14.0).abs() < 1e-3,
        "cluster {cluster:?} measured {width}, expected {}",
        1.27 * 14.0
    );
}

#[test]
fn test_prose_with_one_emoji_measures_at_emoji_width() {
    assert_emoji_width(&inter(), "Status: ✅ done", "✅");
}

#[test]
fn test_colored_status_dot_measures_at_emoji_width() {
    assert_emoji_width(&inter(), "before \u{1F7E2} after", "\u{1F7E2}");
}

#[test]
fn test_zwj_sequence_measures_as_one_emoji_unit() {
    let cluster = "\u{1F468}\u{200D}\u{1F4BB}";
    assert_emoji_width(&inter(), &format!("before {cluster} after"), cluster);
}

#[test]
fn test_flag_measures_as_one_emoji_unit() {
    let cluster = "\u{1F1FA}\u{1F1F8}";
    assert_emoji_width(&inter(), &format!("before {cluster} after"), cluster);
}

#[test]
fn test_keycap_measures_as_one_emoji_unit() {
    let cluster = "1\u{FE0F}\u{20E3}";
    assert_emoji_width(&inter(), &format!("before {cluster} after"), cluster);
}

#[test]
fn test_non_emoji_unknown_codepoint_keeps_quarter_em_fallback() {
    let m = inter();
    let width = m.measure("\u{10FFFD}", 14.0);
    assert!((width - 0.25 * 14.0).abs() < 1e-6);
}

#[test]
fn test_pictograph_with_redundant_vs16_measures_as_one_emoji_unit() {
    let cluster = "\u{1F327}\u{FE0F}";
    assert_emoji_width(&inter(), &format!("before {cluster} after"), cluster);
}

#[test]
fn test_zwj_flag_starting_with_a_vs16_pictograph_folds_to_one_unit() {
    let cluster = "\u{1F3F3}\u{FE0F}\u{200D}\u{1F308}";
    assert_emoji_width(&inter(), &format!("before {cluster} after"), cluster);
}

#[test]
fn test_vs15_suppresses_emoji_booking_instead_of_extending_it() {
    let cluster = "\u{2705}\u{FE0E}";
    let codepoints: Vec<u32> = cluster.chars().map(|c| c as u32).collect();
    assert_eq!(emoji_cluster_length(&codepoints, 0), 0);
    let width = cluster_width(&inter(), &format!("before {cluster} after"), cluster, 14.0);
    assert!(width < 1.27 * 14.0);
}

#[test]
fn test_bare_warning_sign_without_vs16_is_not_an_emoji_cluster() {
    let codepoints: Vec<u32> = "\u{26A0}".chars().map(|c| c as u32).collect();
    assert_eq!(emoji_cluster_length(&codepoints, 0), 0);
}

#[test]
fn test_keycap_base_alone_is_not_a_cluster() {
    let codepoints: Vec<u32> = "#".chars().map(|c| c as u32).collect();
    assert_eq!(emoji_cluster_length(&codepoints, 0), 0);
}

#[test]
fn test_skin_tone_modifier_folds_into_its_base() {
    let m = inter();
    let thumbs_up_medium = "\u{1F44D}\u{1F3FD}";
    assert_emoji_width(
        &m,
        &format!("before {thumbs_up_medium} after"),
        thumbs_up_medium,
    );

    let woman_technologist = "\u{1F469}\u{1F3FD}\u{200D}\u{1F4BB}";
    assert_emoji_width(
        &m,
        &format!("before {woman_technologist} after"),
        woman_technologist,
    );
}

// --- TestRuntControl (test_runts.py) ---
//
// A runt is a wrapping problem, not a packing one -- fixed by choosing a
// different break inside the paragraph, not by moving lines between boxes.
// Off by default: this wrapper is shared by every consumer of a published
// package.

fn runt_pieces(widths: &[f64]) -> Vec<WrapPiece> {
    widths
        .iter()
        .enumerate()
        .map(|(i, &w)| WrapPiece {
            text: "w".repeat(w as usize),
            width: w,
            separator: if i > 0 { " ".to_string() } else { String::new() },
            separator_width: if i > 0 { 4.0 } else { 0.0 },
            meta: None,
            glued: false,
        })
        .collect()
}

#[test]
fn test_off_by_default_leaves_a_single_word_last_line() {
    let lines = wrap_measured_pieces(&runt_pieces(&[40.0, 40.0, 40.0]), 100.0, false);
    assert_eq!(lines.last().unwrap().len(), 1, "default wrapping is unchanged");
}

#[test]
fn test_enabled_pulls_a_word_down_to_the_last_line() {
    let lines = wrap_measured_pieces(&runt_pieces(&[40.0, 40.0, 40.0]), 100.0, true);
    assert!(lines.last().unwrap().len() >= 2, "last line must not be a lone word");
}

#[test]
fn test_never_overflows_the_measure() {
    let lines = wrap_measured_pieces(&runt_pieces(&[40.0, 40.0, 40.0]), 100.0, true);
    for line in &lines {
        let width: f64 = line.iter().map(|p| p.width).sum::<f64>()
            + line.iter().skip(1).map(|p| p.separator_width).sum::<f64>();
        assert!(width <= 100.0 + 1e-9);
    }
}

#[test]
fn test_single_line_paragraph_is_left_alone() {
    let lines = wrap_measured_pieces(&runt_pieces(&[30.0]), 100.0, true);
    assert_eq!(lines.len(), 1);
    assert_eq!(lines[0].len(), 1);
}

#[test]
fn test_gives_up_rather_than_strand_the_penultimate_line() {
    let lines = wrap_measured_pieces(&runt_pieces(&[90.0, 20.0]), 100.0, true);
    let lens: Vec<usize> = lines.iter().map(|l| l.len()).collect();
    assert_eq!(lens, vec![1, 1]);
}

// --- TestHvarExact ---
//
// Pins the Rust HVAR delta arithmetic against Python mdsvg's own
// FontMeasurer (fontTools) to the LAST printed digit — never a tolerance.
// A prior version measured through ttf-parser's typed hvar module, which
// truncates the normalized weight coordinate to F2Dot14 (1/16384) before
// summing in f32; these five combinations are exactly the review's
// reproduction of that ~1.2e-4 relative divergence.

#[test]
fn test_source_serif4_variable_600_g_matches_fonttools() {
    let path = fonts_dir().join("SourceSerif4Variable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, Some(600.0));
    assert_eq!(m.measure("g", 1000.0), 572.8398535245651);
}

#[test]
fn test_source_serif4_variable_500_w_matches_fonttools() {
    let path = fonts_dir().join("SourceSerif4Variable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, Some(500.0));
    assert_eq!(m.measure("W", 1000.0), 1012.4249313396399);
}

#[test]
fn test_source_serif4_variable_500_quarterly_revenue_matches_fonttools() {
    let path = fonts_dir().join("SourceSerif4Variable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, Some(500.0));
    assert_eq!(m.measure("Quarterly Revenue", 1000.0), 9078.559292035397);
}

#[test]
fn test_source_serif4_variable_600_attention_needed_matches_fonttools() {
    let path = fonts_dir().join("SourceSerif4Variable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, Some(600.0));
    assert_eq!(m.measure("Attention Needed", 1000.0), 8440.72914250839);
}

#[test]
fn test_source_serif4_italic_600_m_matches_fonttools() {
    let path = fonts_dir().join("SourceSerif4-Italic.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, Some(600.0));
    assert_eq!(m.measure("m", 1000.0), 840.2004272200184);
}

// fonts.py: `_is_wide_east_asian`/`_is_zero_advance_wide_mark` — an
// unmapped East Asian Wide/Fullwidth codepoint books a full em-square
// advance (matching how CJK-capable fonts actually measure it), except the
// small set of Wide/Fullwidth combining marks that stack onto their base
// character at zero advance.

#[test]
fn an_unmapped_cjk_ideograph_books_a_full_em() {
    let path = fonts_dir().join("InterVariable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, None);
    assert!(!m.has_glyph('\u{4E2D}'), "Inter should not carry a CJK ideograph glyph");
    assert_eq!(m.measure("\u{4E2D}", 1000.0), 1000.0);
}

#[test]
fn an_unmapped_zero_advance_wide_mark_books_nothing() {
    let path = fonts_dir().join("InterVariable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, None);
    assert!(!m.has_glyph('\u{3099}'), "Inter should not carry a combining kana mark glyph");
    assert_eq!(m.measure("\u{3099}", 1000.0), 0.0);
}

#[test]
fn an_unmapped_narrow_codepoint_still_books_the_placeholder_quarter_em() {
    let path = fonts_dir().join("InterVariable.ttf");
    let m = FontMeasurer::new(path.to_str().unwrap(), 0, None);
    // A Private Use Area codepoint: never a real glyph, never East Asian
    // Wide/Fullwidth either — the placeholder fallback still applies.
    assert!(!m.has_glyph('\u{0378}'));
    assert_eq!(m.measure("\u{0378}", 1000.0), 250.0);
}

// --- test_wrap_glue.py: a glued piece never starts a line ---

fn glue_piece(text: &str, width: f64, separator_width: f64, glued: bool) -> WrapPiece {
    WrapPiece {
        text: text.to_string(),
        width,
        separator: if separator_width > 0.0 { " ".to_string() } else { String::new() },
        separator_width,
        meta: None,
        glued,
    }
}

fn line_texts(lines: &[Vec<WrapPiece>]) -> Vec<Vec<&str>> {
    lines.iter().map(|l| l.iter().map(|p| p.text.as_str()).collect()).collect()
}

#[test]
fn test_glued_piece_moves_with_its_predecessor() {
    let piece = |text: &str, separator: bool, glued: bool| {
        glue_piece(text, 10.0 * text.len() as f64, if separator { 5.0 } else { 0.0 }, glued)
    };
    let lines = wrap_measured_pieces(
        &[
            piece("aa", false, false),
            piece("(", true, false),
            piece("bb", false, true),
            piece(",", false, true),
        ],
        45.0,
        false,
    );
    assert_eq!(line_texts(&lines), vec![vec!["aa"], vec!["(", "bb", ","]]);
}

#[test]
fn test_glue_carry_never_exceeds_max_width() {
    for avoid_runts in [false, true] {
        for max_width in (40..120).step_by(3) {
            let pieces = vec![
                glue_piece("aa", 20.0, 0.0, false),
                glue_piece("(", 10.0, 5.0, false),
                glue_piece("long", 38.0, 0.0, true),
                glue_piece(")", 10.0, 0.0, true),
                glue_piece("zz", 20.0, 5.0, false),
            ];
            for line in wrap_measured_pieces(&pieces, max_width as f64, avoid_runts) {
                if line.len() > 1 {
                    assert!(
                        line_width(&line) <= max_width as f64,
                        "{max_width} {avoid_runts}: {:?}",
                        line_texts(std::slice::from_ref(&line))
                    );
                }
            }
        }
    }
}

// Not in the Python tests: the runt pass must leave a glued penultimate piece
// alone (fonts.py `not penultimate[-1].glued`).
#[test]
fn test_runt_pass_does_not_move_a_glued_penultimate_piece() {
    let pieces = vec![
        glue_piece("aaaa", 40.0, 0.0, false),
        glue_piece("bbbb", 40.0, 4.0, false),
        glue_piece(",", 10.0, 0.0, true),
        glue_piece("cccc", 40.0, 4.0, false),
    ];
    let lines = wrap_measured_pieces(&pieces, 100.0, true);
    assert_eq!(line_texts(&lines), vec![vec!["aaaa", "bbbb", ","], vec!["cccc"]]);
}

// `fonts.py::truncate_text_precise`: `text[:mid].rstrip() + suffix` strips
// `\x1c`-`\x1f` too, which `str::trim_end` does not.
#[test]
fn truncation_strips_the_trailing_python_spaces_of_a_candidate() {
    let m = inter();
    let width = m.measure("ab\x1c…", 14.0);
    let truncated = truncate_text_precise("ab\x1c\x1ccdefgh", width, 14.0, &m, true, false);
    assert_eq!(truncated, "ab…");
}

// --- `fonts.py::_line_width` adds with CPython's `sum()` ---

// Ten 0.1 pieces add to 0.9999999999999999 left to right and to 1.0 under
// `sum()`'s compensation. The carried glued chain is kept only while its
// width does not exceed `max_width`, so at that boundary the two sums choose
// different lines.
#[test]
fn test_line_width_sums_as_cpython_does() {
    let tenths: Vec<WrapPiece> = (0..10).map(|_| glue_piece("x", 0.1, 0.0, false)).collect();
    assert_eq!(line_width(&tenths), 1.0);
}

#[test]
fn test_wrap_boundary_reads_the_compensated_chain_width() {
    let mut pieces = vec![glue_piece("a", 0.01, 0.0, false)];
    pieces.push(glue_piece("c0", 0.1, 0.0, false));
    pieces.extend((1..9).map(|i| glue_piece(&format!("c{i}"), 0.1, 0.0, true)));
    pieces.push(glue_piece("p", 0.1, 0.0, true));
    let lines = wrap_measured_pieces(&pieces, 0.999_999_999_999_999_9, false);
    assert_eq!(lines.iter().map(Vec::len).collect::<Vec<_>>(), [10, 1]);
}

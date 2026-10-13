// Transliterated from libs/markdown-svg/tests/test_renderer.py and
// tests/test_fonts.py::TestUsedFaces (renamed used_faces -> used_fonts).
// Included into renderer.rs's `#[cfg(test)] mod tests`.
//
// Not ported here (need table rendering, `render_blocks`, or system-font-
// detection/mono-font-monkeypatch, none in this crate's scope):
// TestTables, TestRenderBlocks,
// `test_renderer_allows_plain_text_without_mono_font` /
// `test_renderer_raises_when_mono_content_has_no_mono_font` (monkeypatch
// `get_system_mono_font`, not ported), `test_fonts_and_font_path_mutually_
// exclusive` (font_path/mono_font_path constructor arm not ported — see
// `SVGRenderer::new`'s doc), the `TestMaxContentWidth` table/code-overflow
// cases, `TestComplexDocuments`, `TestRenderContent`'s composability demos,
// `TestCodeHighlighting` (syntax highlighting, not ported),
// `TestInlineCodeChip::test_inline_code_in_table_cell_no_brackets` (needs
// table rendering).

use super::*;
use crate::fonts::{FontMeasurer, FontSlot};
use crate::parser::parse;
use crate::style::TextAlign;
use crate::types::contains_raw_html;
use std::path::PathBuf;

type StyleMutation<'a> = Box<dyn Fn(&mut Style) + 'a>;

// Class names carry a hash-derived scope (see Python's
// TestClassPrefixScoping) so two boards' SVGs sharing one HTML page can't
// collide on `.md-heading`. Tests that only care "did the heading rule
// render" match on the bare name. Mirrors `_has_md_class`/`_md_rule_body`
// in `libs/markdown-svg/tests/test_renderer.py`.
const MD_CLASS: &str = r"md-[0-9a-f]{8}";

fn has_md_class(haystack: &str, name: &str) -> bool {
    regex::Regex::new(&format!(r"{MD_CLASS}-{name}\b"))
        .unwrap()
        .is_match(haystack)
}

fn md_rule_body(haystack: &str, name: &str) -> String {
    let re = regex::Regex::new(&format!(r"\.{MD_CLASS}-{name} \{{([^}}]*)\}}")).unwrap();
    let caps = re
        .captures(haystack)
        .unwrap_or_else(|| panic!("no .md-*-{name} rule found"));
    caps[1].to_string()
}

fn fonts_dir() -> PathBuf {
    PathBuf::from(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../src/dbt_charts/core/render/fonts"
    ))
}

fn inter_path(name: &str) -> String {
    fonts_dir().join(name).to_str().unwrap().to_string()
}

fn test_fonts() -> FontSlots {
    FontSlots {
        regular: FontSlot::new(inter_path("InterVariable.ttf")),
        bold: Some(FontSlot::new(inter_path("InterVariable.ttf"))),
        italic: Some(FontSlot::new(inter_path("InterVariable-Italic.ttf"))),
        bold_italic: None,
        mono: Some(FontSlot::new(inter_path("SourceCodePro-Regular.ttf"))),
        heading: None,
    }
}

fn render(markdown: &str, width: f64, style: Style) -> String {
    super::render(markdown, width, 20.0, style, test_fonts()).unwrap()
}

fn render_default(markdown: &str) -> String {
    render(markdown, 400.0, Style::default())
}

// --- TestBasicRendering ---

#[test]
fn test_render_simple_text() {
    let svg = render_default("Hello World");
    assert!(svg.contains("<svg"));
    assert!(svg.contains("</svg>"));
    assert!(svg.contains("Hello World"));
}

#[test]
fn test_render_heading() {
    let svg = render_default("# Hello");
    assert!(svg.contains("<svg"));
    assert!(svg.contains("Hello"));
    assert!(has_md_class(&svg, "heading"));
}

#[test]
fn test_render_bold() {
    let svg = render_default("**bold**");
    assert!(svg.contains("bold"));
    assert!(svg.contains("font-weight: bold"));
}

#[test]
fn test_render_italic() {
    let svg = render_default("*italic*");
    assert!(svg.contains("italic"));
    assert!(svg.contains("font-style: italic"));
}

#[test]
fn test_render_code() {
    let svg = render_default("`code`");
    assert!(svg.contains("code"));
    assert!(has_md_class(&svg, "code"));
}

#[test]
fn test_render_inline_code_renders_as_chip() {
    let svg = render("Inline `code` should stand out", 400.0, Style::default());
    assert!(svg.contains("Inline "));
    assert!(svg.contains("code"));
    assert!(svg.contains(" should stand out"));
    assert!(!svg.contains("[code]"));
    assert!(svg.contains("<rect"));
}

#[test]
fn test_render_inline_code_uses_code_background_for_chip() {
    let style = Style {
        code_background: "#eeeeee".to_string(),
        code_block_border_color: "#cccccc".to_string(),
        ..Style::default()
    };
    let svg = render("Inline `code` example", 400.0, style);
    assert!(svg.contains(r##"fill="#eeeeee""##));
    assert!(!svg.contains(r##"stroke="#cccccc""##));
    assert!(!svg.contains("[code]"));
}

#[test]
fn test_render_inline_code_keeps_internal_spaces_styled() {
    let svg = render("Use `line_height = 1.25` here", 400.0, Style::default());
    assert!(svg.contains("line_height = 1.25"));
    assert!(!svg.contains("[line_height = 1.25]"));
}

#[test]
fn test_render_inline_code_wraps_across_lines_as_chips() {
    let svg = render("Use `line_height = 1.25` here", 80.0, Style::default());
    assert!(!svg.contains("[lin"));
    assert!(!svg.contains("25]"));
    assert!(svg.contains("1.25"));
    assert!(svg.matches("<rect").count() >= 2);
}

#[test]
fn test_render_heading_inline_code_renders_as_chip() {
    let svg = render_default("# Check `status`");
    assert!(!svg.contains("[status]"));
    assert!(svg.contains("status"));
    assert!(has_md_class(&svg, "code"));
    assert!(svg.contains("<rect"));
}

#[test]
fn test_render_link() {
    let svg = render_default("[link](https://example.com)");
    assert!(svg.contains("<a href="));
    assert!(svg.contains("example.com"));
}

#[test]
fn test_render_link_uses_tight_solid_underline() {
    let svg = render_default("[link](https://example.com)");
    assert!(svg.contains("text-decoration: underline"));
    assert!(svg.contains("text-decoration-style: solid"));
    assert!(svg.contains("text-decoration-thickness: 1px"));
    assert!(svg.contains("text-underline-offset: 1px"));
}

#[test]
fn test_render_mixed_style_sentence_keeps_boundary_spaces() {
    let svg = render(
        "Hello [link](https://example.com) world",
        400.0,
        Style::default(),
    );
    let re = regex::Regex::new(r"(?s)<text[^>]*>(.*?)</text>").unwrap();
    let text = re.captures(&svg).unwrap();
    let tag_re = regex::Regex::new(r"<[^>]+>").unwrap();
    let visible = tag_re.replace_all(&text[1], "");
    assert_eq!(visible, "Hello link world");
}

// --- TestStyling ---

#[test]
fn test_custom_text_color() {
    let style = Style {
        text_color: "#ff0000".to_string(),
        ..Style::default()
    };
    assert!(render("Hello", 400.0, style).contains("#ff0000"));
}

#[test]
fn test_custom_font_size() {
    let style = Style {
        base_font_size: 20.0,
        ..Style::default()
    };
    assert!(render("Hello", 400.0, style).contains(r#"font-size="20""#));
}

#[test]
fn test_default_font_weight_not_emitted() {
    let svg = render_default("Hello");
    assert!(has_md_class(&svg, "text"));
    assert!(!md_rule_body(&svg, "text").contains("font-weight:"));
}

#[test]
fn test_custom_font_weight() {
    let style = Style {
        font_weight: Some("600".to_string()),
        ..Style::default()
    };
    assert!(render("Hello", 400.0, style).contains("font-weight: 600"));
}

// --- TestDimensions ---

#[test]
fn test_custom_width() {
    assert!(render_default_width("Hello", 600.0).contains(r#"width="600""#));
}

fn render_default_width(markdown: &str, width: f64) -> String {
    render(markdown, width, Style::default())
}

#[test]
fn test_custom_padding() {
    let svg = super::render("Hello", 400.0, 30.0, Style::default(), test_fonts()).unwrap();
    assert!(svg.contains("<svg"));
}

// --- TestMeasure ---

fn measure(markdown: &str, width: f64) -> Size {
    super::measure(markdown, width, 20.0, Style::default(), test_fonts()).unwrap()
}

#[test]
fn test_measure_returns_size() {
    let size = measure("Hello World", 400.0);
    assert!(size.width > 0.0);
    assert!(size.height > 0.0);
}

#[test]
fn test_measure_respects_width() {
    assert_eq!(measure("Hello", 200.0).width, 200.0);
}

#[test]
fn test_longer_text_taller() {
    let short_size = measure("Hi", 400.0);
    let long_size = measure(
        "Hi\n\nThis is a much longer paragraph that will wrap.",
        400.0,
    );
    assert!(long_size.height > short_size.height);
}

// --- TestSVGRenderer ---

#[test]
fn test_renderer_with_style() {
    let style = Style {
        text_color: "#123456".to_string(),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let blocks = parse("Hello");
    let svg = renderer.render(&blocks, 400.0, 20.0).unwrap();
    assert!(svg.contains("#123456"));
}

#[test]
fn test_renderer_measure() {
    let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    let blocks = parse("Hello");
    let size = renderer.measure(&blocks, 400.0, 20.0).unwrap();
    assert_eq!(size.width, 400.0);
    assert!(size.height > 0.0);
}

#[test]
fn test_renderer_raises_when_precise_font_measurement_unavailable() {
    let fonts = FontSlots::new(FontSlot::new("/definitely/missing/font.ttf"));
    match SVGRenderer::new(Style::default(), fonts) {
        Ok(_) => panic!("expected missing-font error"),
        Err(e) => assert!(e.contains("Precise text measurement")),
    }
}

// --- TestFontFaces ---

#[test]
fn test_measure_text_italic_narrower_than_regular_with_real_fontface() {
    let regular_path = fonts_dir().join("SourceSerif4Variable.ttf");
    let italic_path = fonts_dir().join("SourceSerif4-Italic.ttf");
    let fonts = FontSlots::new(FontSlot::new(regular_path.to_str().unwrap()));
    let fonts = FontSlots {
        italic: Some(FontSlot::new(italic_path.to_str().unwrap())),
        ..fonts
    };
    let renderer = SVGRenderer::new(Style::default(), fonts).unwrap();
    let sample = "so it may run to eighty and keep going indefinitely";

    let regular_width = renderer
        .measure_text(sample, 14.0, false, false, false, None, false)
        .unwrap();
    let italic_width = renderer
        .measure_text(sample, 14.0, false, true, false, None, false)
        .unwrap();

    assert!(italic_width < regular_width);
}

#[test]
fn test_bold_measurement_uses_supplied_fontface_not_ratio() {
    let variable_path = fonts_dir().join("InterVariable.ttf");
    let mut regular = FontSlot::new(variable_path.to_str().unwrap());
    regular.weight = Some(400.0);
    let mut bold = FontSlot::new(variable_path.to_str().unwrap());
    bold.weight = Some(700.0);
    let fonts = FontSlots {
        bold: Some(bold),
        ..FontSlots::new(regular)
    };
    let renderer = SVGRenderer::new(Style::default(), fonts).unwrap();
    let sample = "measured bold text width against regular";

    let regular_width = renderer
        .measure_text(sample, 14.0, false, false, false, None, false)
        .unwrap();
    let ratio_scaled =
        regular_width * (renderer.style.bold_char_width_ratio / renderer.style.char_width_ratio);
    let bold_width = renderer
        .measure_text(sample, 14.0, true, false, false, None, false)
        .unwrap();

    assert!(regular_width < bold_width && bold_width < ratio_scaled);
}

// --- TestMaxContentWidth (subset reachable without lists/tables/code) ---

#[test]
fn test_zero_for_empty_content() {
    let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    let result = renderer.render_content(&parse(""), 400.0, 20.0).unwrap();
    assert_eq!(result.max_content_width, 0.0);
}

#[test]
fn test_wider_for_longer_paragraph_content() {
    let short = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .render_content(&parse("Hi"), 600.0, 20.0)
        .unwrap();
    let long = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .render_content(
            &parse(
                "A considerably longer sentence that occupies much more horizontal space on the line.",
            ),
            600.0,
            20.0,
        )
        .unwrap();
    assert!(long.max_content_width > short.max_content_width);
}

#[test]
fn test_includes_heading_text() {
    let result = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .render_content(
            &parse("# A reasonably long heading spanning several words"),
            600.0,
            20.0,
        )
        .unwrap();
    assert!(result.max_content_width > 0.0);
}

// --- TestEscaping ---

#[test]
fn test_escapes_angle_brackets() {
    // A line that opens with a tag parses as a RawHtmlBlock; it paints as
    // escaped text (see the raw-HTML section below), never as markup.
    let svg = render_default("<script>alert('xss')</script>");
    assert!(!svg.contains("<script"));
    assert!(svg.contains("&lt;script&gt;alert("));

    let svg2 = render_default("a < b > c");
    assert!(svg2.contains("&lt;"));
    assert!(svg2.contains("&gt;"));
}

#[test]
fn test_escapes_ampersand() {
    assert!(render_default("A & B").contains("&amp;"));
}

#[test]
fn test_escapes_quotes() {
    let svg = render_default(r#"Text with "quotes""#);
    assert!(svg.contains("&quot;") || !svg.contains(r#""quotes""#));
}

// --- TestEdgeCases ---

#[test]
fn test_empty_input() {
    let svg = render_default("");
    assert!(svg.contains("<svg"));
    assert!(svg.contains("</svg>"));
}

#[test]
fn test_whitespace_only() {
    assert!(render_default("   \n\n   ").contains("<svg"));
}

#[test]
fn test_very_long_text() {
    let long_text = "word ".repeat(100);
    let svg = render(&long_text, 200.0, Style::default());
    assert!(svg.contains("<svg"));
    assert!(svg.matches("<text").count() > 1);
}

// --- TestTextAlignment ---

#[test]
fn test_default_alignment_is_left() {
    assert!(render_default("Hello").contains(r#"text-anchor="start""#));
}

#[test]
fn test_center_alignment() {
    let style = Style {
        text_align: TextAlign::Center,
        ..Style::default()
    };
    assert!(render("Hello", 400.0, style).contains(r#"text-anchor="middle""#));
}

#[test]
fn test_right_alignment() {
    let style = Style {
        text_align: TextAlign::Right,
        ..Style::default()
    };
    assert!(render("Hello", 400.0, style).contains(r#"text-anchor="end""#));
}

#[test]
fn test_heading_respects_alignment() {
    let style = Style {
        text_align: TextAlign::Center,
        ..Style::default()
    };
    assert!(render("# Centered Title", 400.0, style).contains(r#"text-anchor="middle""#));
}

#[test]
fn test_style_get_text_anchor_method() {
    assert_eq!(
        Style {
            text_align: TextAlign::Left,
            ..Style::default()
        }
        .get_text_anchor(),
        "start"
    );
    assert_eq!(
        Style {
            text_align: TextAlign::Center,
            ..Style::default()
        }
        .get_text_anchor(),
        "middle"
    );
    assert_eq!(
        Style {
            text_align: TextAlign::Right,
            ..Style::default()
        }
        .get_text_anchor(),
        "end"
    );
}

// --- TestUsedFaces (test_fonts.py, renamed used_faces -> used_fonts) ---

fn used_font_slots() -> FontSlots {
    let roman = inter_path("InterVariable.ttf");
    let italic = inter_path("InterVariable-Italic.ttf");
    let mono = inter_path("SourceCodePro-Regular.ttf");
    let mut regular = FontSlot::new(&roman);
    regular.weight = None;
    let mut bold = FontSlot::new(&roman);
    bold.weight = Some(700.0);
    let mut bold_italic = FontSlot::new(&italic);
    bold_italic.weight = Some(700.0);
    FontSlots {
        regular,
        bold: Some(bold),
        italic: Some(FontSlot::new(&italic)),
        bold_italic: Some(bold_italic),
        mono: Some(FontSlot::new(&mono)),
        heading: None,
    }
}

fn render_used_fonts(markdown: &str) -> HashSet<&'static str> {
    let renderer = SVGRenderer::new(Style::default(), used_font_slots()).unwrap();
    renderer.render(&parse(markdown), 300.0, 0.0).unwrap();
    renderer.used_fonts()
}

#[test]
fn test_empty_before_rendering() {
    let renderer = SVGRenderer::new(Style::default(), used_font_slots()).unwrap();
    assert!(renderer.used_fonts().is_empty());
}

#[test]
fn test_plain_text_reaches_only_regular() {
    let fonts = render_used_fonts("Plain words");
    assert_eq!(fonts, HashSet::from(["regular"]));
}

#[test]
fn test_emphasis_reaches_italic() {
    assert!(render_used_fonts("Some *emphasis* here").contains("italic"));
}

#[test]
fn test_code_reaches_mono() {
    assert!(render_used_fonts("Some `code` here").contains("mono"));
}

#[test]
fn test_strong_reaches_bold() {
    assert!(render_used_fonts("Some **strong** here").contains("bold"));
}

#[test]
fn test_bold_italic_reaches_the_bold_italic_slot() {
    assert!(render_used_fonts("Some ***both*** here").contains("bold_italic"));
}

#[test]
fn test_accumulates_across_calls() {
    let renderer = SVGRenderer::new(Style::default(), used_font_slots()).unwrap();
    renderer.render(&parse("Plain"), 300.0, 0.0).unwrap();
    renderer.render(&parse("*Emphasized*"), 300.0, 0.0).unwrap();
    let fonts = renderer.used_fonts();
    assert!(fonts.contains("regular") && fonts.contains("italic"));
}

// --- TestCodeBlocks ---

#[test]
fn test_code_block_has_background() {
    assert!(render_default("```\ncode\n```").contains("<rect"));
}

#[test]
fn test_short_code_block_background_shrinks_to_content() {
    let svg = render("```\ncode\n```", 400.0, Style::default());
    let re = regex::Regex::new(r#"<rect [^>]*width="([^"]+)""#).unwrap();
    let width: f64 = re.captures(&svg).unwrap()[1].parse().unwrap();
    assert!(width < 80.0);
}

#[test]
fn test_code_block_vertical_padding_is_tighter_than_horizontal() {
    let style = Style {
        code_font_size: 10.0,
        code_block_padding: 12.0,
        ..Style::default()
    };
    let svg = render(
        "```\nline_height = 1.25\nparagraph_gap = 0.5\n```",
        400.0,
        style,
    );
    let re = regex::Regex::new(r#"<rect [^>]*height="([^"]+)""#).unwrap();
    let height: f64 = re.captures(&svg).unwrap()[1].parse().unwrap();
    assert_eq!(height, 44.0);
}

#[test]
fn test_long_code_block_background_caps_at_available_width() {
    let long_line = "x".repeat(200);
    let svg = render(&format!("```\n{long_line}\n```"), 400.0, Style::default());
    let re = regex::Regex::new(r#"<rect [^>]*width="([^"]+)""#).unwrap();
    let width: f64 = re.captures(&svg).unwrap()[1].parse().unwrap();
    assert!(width <= 400.0);
    assert!(width > 300.0);
}

#[test]
fn test_code_block_preserves_content() {
    assert!(render_default("```python\nprint('hello')\n```").contains("print"));
}

#[test]
fn test_code_block_preserves_indentation() {
    let md = "```python\ndef foo():\n    return 42\n```";
    let svg = render_default(md);
    assert!(svg.contains("    return 42"));
    assert!(svg.contains(r#"xml:space="preserve""#));
}

// --- TestLists ---

#[test]
fn test_unordered_list_has_bullets() {
    let svg = render_default("- Item 1\n- Item 2");
    assert!(svg.contains("<circle"));
    assert!(svg.contains(r#"r="1.5""#));
    let marker_re = regex::Regex::new(r#"<circle cx="([^"]+)"[^>]*r="([^"]+)""#).unwrap();
    let text_re = regex::Regex::new(r#"<text x="([^"]+)"[^>]*>Item 1</text>"#).unwrap();
    let marker = marker_re.captures(&svg).unwrap();
    let text = text_re.captures(&svg).unwrap();
    let marker_right: f64 = marker[1].parse::<f64>().unwrap() + marker[2].parse::<f64>().unwrap();
    let text_left: f64 = text[1].parse().unwrap();
    assert_eq!(text_left - marker_right, 6.5);
}

#[test]
fn test_ordered_list_has_numbers() {
    let svg = render_default("1. First\n2. Second");
    assert!(svg.contains("1."));
    assert!(svg.contains("2."));
}

#[test]
fn test_multiline_item_renders_full_text() {
    let svg = render("- first line\n  second line", 400.0, Style::default());
    assert!(svg.contains("first line"));
    assert!(svg.contains("second line"));
}

#[test]
fn test_nested_child_list_renders() {
    let svg = render("- parent\n  - child", 400.0, Style::default());
    assert!(svg.contains("parent"));
    assert!(svg.contains("child"));
    assert_eq!(svg.matches("<circle").count(), 2);
}

#[test]
fn test_flat_list_height_unchanged() {
    let single_size = measure("- only item", 400.0);
    let two_size = measure("- item one\n- item two", 400.0);
    assert!(two_size.height > single_size.height);
}

// --- TestBlockquoteBackground (behavior subset — Python's dataclass-field-
// introspection cases have no Rust analog and are covered by style.rs's
// own Default tests instead) ---

#[test]
fn test_blockquote_has_background_rect_when_set() {
    let style = Style {
        blockquote_background: "#f0f0f0".to_string(),
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("#f0f0f0"));
}

#[test]
fn test_blockquote_inline_code_renders_as_chip() {
    let svg = render_default("> Quote `status` carefully");
    assert!(!svg.contains("[status]"));
    assert!(svg.contains("status"));
    assert!(has_md_class(&svg, "code"));
}

#[test]
fn test_blockquote_no_background_rect_when_empty() {
    let bg = render(
        "> A quote",
        400.0,
        Style {
            blockquote_background: "#eeeeee".to_string(),
            ..Style::default()
        },
    );
    let no_bg = render(
        "> A quote",
        400.0,
        Style {
            blockquote_background: String::new(),
            ..Style::default()
        },
    );
    assert_eq!(
        no_bg.matches("<rect").count(),
        bg.matches("<rect").count() - 1
    );
}

#[test]
fn test_blockquote_background_honors_border_radius() {
    let style = Style {
        blockquote_background: "#f0f0f0".to_string(),
        blockquote_border_radius: 6.0,
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains(r#"rx="6""#));
}

#[test]
fn test_blockquote_background_no_radius_when_zero() {
    let style = Style {
        blockquote_background: "#f0f0f0".to_string(),
        blockquote_border_radius: 0.0,
        ..Style::default()
    };
    assert!(!render("> A quote", 400.0, style).contains("rx="));
}

// --- TestCodeBlockBorderStroke (behavior subset) ---

#[test]
fn test_code_block_border_stroke_emitted_when_set() {
    let style = Style {
        code_block_border_color: "#aabbcc".to_string(),
        code_block_border_width: 2.0,
        ..Style::default()
    };
    assert!(render("```\ncode\n```", 400.0, style).contains("#aabbcc"));
}

#[test]
fn test_code_block_no_stroke_when_color_empty() {
    let svg = render(
        "```\ncode\n```",
        400.0,
        Style {
            code_block_border_color: String::new(),
            ..Style::default()
        },
    );
    assert!(svg.contains("<rect"));
    assert!(!svg.contains("stroke="));
}

#[test]
fn test_code_block_stroke_present_when_color_set() {
    let style = Style {
        code_block_border_color: "#333333".to_string(),
        ..Style::default()
    };
    assert!(render("```\ncode\n```", 400.0, style).contains(r##"stroke="#333333""##));
}

// --- TestCodeFontOverrides ---

#[test]
fn test_code_font_weight_appears_in_svg() {
    let style = Style {
        code_font_weight: "700".to_string(),
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("font-weight: 700"));
}

#[test]
fn test_code_font_style_italic_appears_in_svg() {
    let style = Style {
        code_font_style: "italic".to_string(),
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("font-style: italic"));
}

#[test]
fn test_code_font_family_overrides_mono() {
    let style = Style {
        code_font_family: "'My Code Font', monospace".to_string(),
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("My Code Font"));
}

#[test]
fn test_code_font_size_override_applies() {
    let style = Style {
        code_font_size: 11.0,
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("font-size: 11"));
}

#[test]
fn test_code_font_decoration_underline_appears() {
    let style = Style {
        code_font_decoration: "underline".to_string(),
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("text-decoration: underline"));
}

#[test]
fn test_code_font_case_upper_transforms_text() {
    let style = Style {
        code_font_case: "upper".to_string(),
        ..Style::default()
    };
    let svg = render("Use `hello` here", 400.0, style);
    assert!(svg.contains("HELLO"));
    assert!(!svg.contains("hello"));
}

#[test]
fn test_code_unset_falls_back_to_mono_family() {
    let style = Style {
        mono_font_family: "ui-monospace, 'Test Mono', monospace".to_string(),
        code_font_family: String::new(),
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("Test Mono"));
}

#[test]
fn test_code_block_font_weight_applies() {
    let style = Style {
        code_font_weight: "700".to_string(),
        ..Style::default()
    };
    assert!(render("```\nhello\n```", 400.0, style).contains("font-weight"));
}

#[test]
fn test_code_block_font_case_upper_transforms() {
    let style = Style {
        code_font_case: "upper".to_string(),
        ..Style::default()
    };
    assert!(render("```\nhello world\n```", 400.0, style).contains("HELLO WORLD"));
}

#[test]
fn test_code_block_font_family_applies() {
    let style = Style {
        code_font_family: "'My Code Font', monospace".to_string(),
        ..Style::default()
    };
    assert!(render("```\nhello\n```", 400.0, style).contains("My Code Font"));
}

#[test]
fn test_code_block_font_size_applies() {
    let style = Style {
        code_font_size: 11.0,
        ..Style::default()
    };
    assert!(render("```\nhello\n```", 400.0, style).contains(r#"font-size="11""#));
}

#[test]
fn test_code_block_font_decoration_applies() {
    let style = Style {
        code_font_decoration: "underline".to_string(),
        ..Style::default()
    };
    assert!(render("```\nhello\n```", 400.0, style).contains(r#"text-decoration="underline""#));
}

// --- TestCodeFontScale (non-table subset) ---

#[test]
fn test_inline_code_scales_off_body_prose() {
    let style = Style {
        base_font_size: 14.0,
        code_font_scale: 0.9,
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("font-size: 12.5px"));
}

#[test]
fn test_inline_code_in_heading_scales_off_the_heading() {
    let style = Style {
        base_font_size: 14.0,
        h2_size: Some(18.0),
        code_font_scale: 0.9,
        ..Style::default()
    };
    let svg = render("## Heading with `code`", 400.0, style);
    assert!(svg.contains("font-size: 16px"));
    assert!(!svg.contains("font-size: 12.5px"));
}

#[test]
fn test_fenced_block_scales_off_base_not_host() {
    let style = Style {
        base_font_size: 14.0,
        code_font_scale: 0.9,
        ..Style::default()
    };
    assert!(render("```\ncode\n```", 400.0, style).contains(r#"font-size="12.5""#));
}

#[test]
fn test_scaled_size_rounds_to_nearest_half_pixel() {
    let style = Style {
        base_font_size: 13.0,
        code_font_scale: 0.9,
        ..Style::default()
    };
    assert!(render("Use `code` here", 400.0, style).contains("font-size: 11.5px"));
}

#[test]
fn test_absolute_code_font_size_still_wins() {
    let style = Style {
        base_font_size: 14.0,
        code_font_scale: 0.9,
        code_font_size: 11.0,
        ..Style::default()
    };
    let svg = render("Use `code` here\n\n```\ncode\n```", 400.0, style);
    assert!(svg.contains("font-size: 11px"));
    assert!(!svg.contains("font-size: 12.5px"));
}

#[test]
fn test_chip_width_tracks_the_scaled_size() {
    let chip_width = |scale: f64| -> f64 {
        let style = Style {
            base_font_size: 14.0,
            code_font_scale: scale,
            ..Style::default()
        };
        let svg = render("Use `code` here", 400.0, style);
        let re = regex::Regex::new(r##"<rect [^>]*width="([\d.]+)"[^>]*fill="#f3f4f6""##).unwrap();
        re.captures(&svg)
            .expect("expected an inline-code chip rect")[1]
            .parse()
            .unwrap()
    };
    assert!(chip_width(1.2) > chip_width(0.6));
}

#[test]
fn test_chip_box_is_sized_off_the_code_size_not_the_host() {
    let chip_box = |style: Style| -> (String, String) {
        let svg = super::render("Use `code` here", 400.0, 20.0, style, test_fonts()).unwrap();
        let re = regex::Regex::new(r#"<rect [^>]*y="([\d.]+)"[^>]*height="([\d.]+)""#).unwrap();
        let m = re
            .captures(&svg)
            .expect("expected an inline-code chip rect");
        (m[1].to_string(), m[2].to_string())
    };
    let scaled = chip_box(Style {
        base_font_size: 14.0,
        code_font_scale: 0.9,
        ..Style::default()
    });
    let pinned = chip_box(Style {
        base_font_size: 14.0,
        code_font_size: 12.5,
        ..Style::default()
    });
    assert_eq!(scaled, pinned);
}

// --- TestBlockquoteFontOverrides ---

#[test]
fn test_blockquote_font_weight_appears_in_svg() {
    let style = Style {
        blockquote_font_weight: "700".to_string(),
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("font-weight: 700"));
}

#[test]
fn test_blockquote_font_style_italic_appears_in_svg() {
    let style = Style {
        blockquote_font_style: "italic".to_string(),
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("font-style: italic"));
}

#[test]
fn test_blockquote_font_family_applies() {
    let style = Style {
        blockquote_font_family: "'My Quote Font', serif".to_string(),
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("My Quote Font"));
}

#[test]
fn test_table_cell_code_span_style_attribute_is_escaped() {
    let svg = render("| a |\n|---|\n| `x` |", 400.0, Style::default());
    assert!(svg.contains("<tspan style=\"font-family: ui-monospace, &#x27;SF Mono&#x27;"), "{svg}");
}

#[test]
fn test_blockquote_font_family_falls_back_to_body_family() {
    let style = Style {
        font_family: "'Body Serif', serif".to_string(),
        ..Style::default()
    };
    let svg = render("> A quote", 400.0, style);
    assert!(has_md_class(&svg, "blockquote"));
    assert!(svg.contains("font-family: &#x27;Body Serif&#x27;, serif"));
}

// --- CSS escaping of authored font-family stacks and refusal of CSS-breaking
// style values (`css.py`, `style.py:281-290`, `renderer.py:1348-1377`,
// `:1468`). Expected bytes are Python mdsvg's own output for the same style.

#[test]
fn plain_font_stack_renders_byte_identical() {
    let stack = "'Source Serif 4', Georgia, serif";
    let style = Style {
        font_family: stack.to_string(),
        ..Style::default()
    };
    let svg = render("hello", 400.0, style);
    assert!(svg.contains(&format!("font-family: {};", stack.replace('\'', "&#x27;"))));
}

#[test]
fn authored_font_stacks_are_css_escaped_in_every_rule_and_the_inline_code_style() {
    const EVIL: &str = "x;} svg{background:url(https://evil)} .a{";
    const EVIL_ESCAPED: &str = r"&quot;x\3b \7d  svg\7b background\3a url\28 https\3a \2f \2f evil\29 \7d  \2e a\7b &quot;";
    let style = Style {
        font_family: "Georgia; x".to_string(),
        mono_font_family: EVIL.to_string(),
        heading_font_family: "'H1', SANS-SERIF;".to_string(),
        blockquote_font_family: "Q<b>, MONOSPACE".to_string(),
        ..Style::default()
    };
    let svg = render("# Head\n\nBody `code` text\n\n> quote", 400.0, style);
    assert!(md_rule_body(&svg, "text").starts_with(r" font-family: &quot;Georgia\3b  x&quot;; fill:"));
    assert_eq!(md_rule_body(&svg, "mono"), format!(" font-family: {EVIL_ESCAPED}; "));
    assert!(md_rule_body(&svg, "heading").starts_with(r" font-family: &quot;H1&quot;, &quot;SANS-SERIF\3b &quot;; fill:"));
    assert!(md_rule_body(&svg, "code").starts_with(&format!(" font-family: {EVIL_ESCAPED}; fill:")));
    assert!(md_rule_body(&svg, "blockquote")
        .contains(r" font-family: &quot;Q\3c b\3e &quot;, monospace;"));
    assert!(svg.contains(&format!("<tspan style=\"font-family: {EVIL_ESCAPED}; fill:")), "{svg}");
}

#[test]
fn font_family_hostile_stack_cannot_open_new_css_rules() {
    let evil = "x;} svg{background:url(https://evil)} .a{".to_string();
    let style = Style {
        font_family: evil.clone(),
        mono_font_family: evil,
        ..Style::default()
    };
    let svg = render("hello `code`", 400.0, style);
    let css = regex::Regex::new(r"(?s)<style>(.*?)</style>").unwrap();
    let css = css.captures(&svg).unwrap()[1].to_string();
    let opener = regex::Regex::new(r"([^{}]*)\{").unwrap();
    let openers: Vec<&str> = opener.captures_iter(&css).map(|c| c.get(1).unwrap().as_str()).collect();
    assert!(!openers.is_empty());
    assert!(openers.iter().all(|sel| sel.trim().starts_with(".md-")), "{openers:?}");
}

#[test]
fn css_breaking_style_value_is_refused_by_name_when_the_renderer_is_built() {
    let style = Style {
        text_color: "red;} svg{background:url(//evil)}".to_string(),
        ..Style::default()
    };
    let err = SVGRenderer::new(style, test_fonts()).err().expect("must refuse");
    assert_eq!(
        err,
        "ERR-STYLE-CSS-VALUE-INVALID: Invalid style value for markdown text: \
         Style.text_color is not valid CSS: 'red;} svg{background:url(//evil)}'."
    );
}

#[test]
fn render_refuses_a_css_breaking_style_value_instead_of_painting_it() {
    let style = Style {
        hr_color: "red;".to_string(),
        ..Style::default()
    };
    let err = super::render("hello", 400.0, 20.0, style, test_fonts()).unwrap_err();
    assert!(err.starts_with("ERR-STYLE-CSS-VALUE-INVALID: "), "{err}");
}

#[test]
fn test_blockquote_font_size_applies() {
    let style = Style {
        blockquote_font_size: 13.0,
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("font-size: 13"));
}

#[test]
fn test_blockquote_font_decoration_line_through_appears() {
    let style = Style {
        blockquote_font_decoration: "line-through".to_string(),
        ..Style::default()
    };
    assert!(render("> A quote", 400.0, style).contains("text-decoration: line-through"));
}

#[test]
fn test_blockquote_font_case_lower_transforms_text() {
    let style = Style {
        blockquote_font_case: "lower".to_string(),
        ..Style::default()
    };
    let svg = render("> HELLO WORLD", 400.0, style);
    assert!(svg.contains("hello world"));
    assert!(!svg.contains("HELLO WORLD"));
}

// --- TestBoldFontWeight (non-table subset) ---

#[test]
fn test_default_bold_font_weight_unchanged_on_bold_tspan() {
    let svg = render_default("**bold**");
    let re = regex::Regex::new(r"<tspan[^>]*>bold</tspan>").unwrap();
    let tspan = re.find(&svg).unwrap();
    assert!(tspan.as_str().contains("font-weight: bold"));
}

#[test]
fn test_custom_bold_font_weight_applies_to_inline_bold() {
    let style = Style {
        bold_font_weight: "600".to_string(),
        ..Style::default()
    };
    assert!(render("A **bold** word", 400.0, style).contains("font-weight: 600"));
}

#[test]
fn test_heading_font_weight_independent_of_bold_font_weight() {
    let style = Style {
        bold_font_weight: "999".to_string(),
        ..Style::default()
    };
    let svg = render("# Heading", 400.0, style);
    let heading_rule_body = md_rule_body(&svg, "heading");
    assert!(heading_rule_body.contains("font-weight: bold"));
    assert!(!heading_rule_body.contains("999"));
}

// --- _round_half: Python round() is round-half-to-even, not
// round-half-away-from-zero ---

#[test]
fn test_round_half_ties_to_even() {
    // round_half(size) = round_ties_even(size * 2) / 2, so a tie in
    // round_half's own input space is a *.25/*.75 boundary (24.5/25.5
    // after doubling), not a *.5 one.
    assert_eq!(round_half(12.25), 12.0);
    assert_eq!(round_half(12.75), 13.0);
}

#[test]
fn test_round_ties_even_primitive_matches_pythons_round() {
    // The underlying primitive round_half relies on: Python's round(0.5)
    // is 0 and round(1.5) is 2 (ties to the nearest EVEN integer) —
    // f64::round would give 1.0 and 2.0 (ties away from zero), the wrong
    // one of the two.
    assert_eq!(0.5f64.round_ties_even(), 0.0);
    assert_eq!(1.5f64.round_ties_even(), 2.0);
}

#[test]
fn test_round_half_non_ties_unaffected() {
    assert_eq!(round_half(11.75), 12.0);
    assert_eq!(round_half(11.6), 11.5);
}

// --- wrap_runs propagates a measurement failure instead of swallowing it
// to a free 0.0 width ---
//
// `split_token_precise`'s own measure closure now uses `?` like every other
// `measure(...)` call in `wrap_runs`, rather than `unwrap_or(0.0)`. For an
// `is_code` run specifically, the guarding `if run.is_code && ... &&
// measure(&run.text)? <= max_width` already `?`-propagates the identical
// failure (mono-font absence is text-independent — it fails the same way
// for the whole run and every substring) before this closure is ever
// reached, so this test pins the refusal contract for that shape rather
// than proving this one line unreachable-without-the-fix; it passes with
// or without it. The fix still earns its place: it removes the one
// `measure(...)` in this function that silently discarded an `Err`, which
// a future change to the fallback loop's shape could make reachable.
#[test]
fn test_long_inline_code_with_no_mono_font_refuses_instead_of_zero_width() {
    let fonts = FontSlots {
        mono: None,
        ..test_fonts()
    };
    let renderer = SVGRenderer::new(Style::default(), fonts).unwrap();
    let long_code = "a".repeat(50);
    let err = renderer
        .render(&parse(&format!("Use `{long_code}` here")), 80.0, 20.0)
        .unwrap_err();
    assert!(err.contains("monospace"));
}

// --- TestHeadingMarginPxOverrides ---

fn measure_with(markdown: &str, width: f64, padding: f64, style: Style) -> Size {
    super::measure(markdown, width, padding, style, test_fonts()).unwrap()
}

#[test]
fn test_top_px_override_used_when_set() {
    let em_only = Style {
        heading_margin_top: 2.0,
        heading_margin_top_px: None,
        ..Style::default()
    };
    let px_override = Style {
        heading_margin_top: 2.0,
        heading_margin_top_px: Some(40.0),
        ..Style::default()
    };
    // Preceded by a paragraph: an opening heading collapses its top margin,
    // so a lone heading cannot show a top-margin override at all.
    let h1 = "Body.\n\n# Heading";

    let em_size = measure_with(h1, 400.0, 20.0, em_only);
    let px_size = measure_with(h1, 400.0, 20.0, px_override);

    assert_ne!(em_size.height, px_size.height);
}

#[test]
fn test_bottom_px_override_used_when_set() {
    let em_only = Style {
        heading_margin_bottom: 2.0,
        heading_margin_bottom_px: None,
        ..Style::default()
    };
    let px_override = Style {
        heading_margin_bottom: 2.0,
        heading_margin_bottom_px: Some(40.0),
        ..Style::default()
    };
    let h1 = "# Heading";

    let em_size = measure_with(h1, 400.0, 20.0, em_only);
    let px_size = measure_with(h1, 400.0, 20.0, px_override);

    assert_ne!(em_size.height, px_size.height);
}

#[test]
fn test_px_override_gives_constant_gap_across_h_levels() {
    let style = Style {
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(20.0),
        ..Style::default()
    };
    let lead = measure_with("Body.", 400.0, 20.0, style.clone()).height;
    // Each heading follows a paragraph, so its top margin is drawn; an
    // opening heading collapses it and would leave only the bottom half
    // of the constant this test is about.
    let h1 = measure_with("Body.\n\n# H", 400.0, 20.0, style.clone());
    let h2 = measure_with("Body.\n\n## H", 400.0, 20.0, style.clone());
    let h6 = measure_with("Body.\n\n###### H", 400.0, 20.0, style);

    let h1_text = h1.height - lead - 40.0;
    let h2_text = h2.height - lead - 40.0;
    let h6_text = h6.height - lead - 40.0;

    assert!(h1_text > h2_text && h2_text > h6_text);
    assert_eq!(h1.height - lead - h1_text, 40.0);
    assert_eq!(h2.height - lead - h2_text, 40.0);
    assert_eq!(h6.height - lead - h6_text, 40.0);
}

#[test]
fn test_px_top_none_falls_back_to_em() {
    let em_only_3 = Style {
        heading_margin_top: 3.0,
        heading_margin_top_px: None,
        ..Style::default()
    };
    let em_only_1 = Style {
        heading_margin_top: 1.0,
        heading_margin_top_px: None,
        ..Style::default()
    };

    let size_3 = measure_with("Body.\n\n# H", 400.0, 20.0, em_only_3);
    let size_1 = measure_with("Body.\n\n# H", 400.0, 20.0, em_only_1);

    assert!(size_3.height > size_1.height);
}

// --- TestHeadingAdjacentSpacingSkipsParagraphSpacing ---

#[test]
fn test_paragraph_to_heading_skips_paragraph_spacing() {
    let style = Style {
        heading_margin_top_px: Some(40.0),
        heading_margin_bottom_px: Some(10.0),
        paragraph_spacing: 100.0, // Would inflate if heading-adjacent gap applied.
        ..Style::default()
    };

    let text_alone = measure_with("Para.", 400.0, 0.0, style.clone());
    let h_alone = measure_with("## H", 400.0, 0.0, style.clone());
    let seq = measure_with("Para.\n\n## H", 400.0, 0.0, style);

    // h_alone opens its own document, so it collapsed the 40px top margin
    // the heading in `seq` draws — the joint itself still adds nothing.
    let expected = text_alone.height + h_alone.height + 40.0;
    assert_eq!(seq.height, expected);
}

#[test]
fn test_heading_to_paragraph_skips_paragraph_spacing() {
    let style = Style {
        heading_margin_top_px: Some(40.0),
        heading_margin_bottom_px: Some(10.0),
        paragraph_spacing: 100.0,
        ..Style::default()
    };

    let h_alone = measure_with("## H", 400.0, 0.0, style.clone());
    let text_alone = measure_with("Para.", 400.0, 0.0, style.clone());
    let seq = measure_with("## H\n\nPara.", 400.0, 0.0, style);

    assert_eq!(seq.height, h_alone.height + text_alone.height);
}

#[test]
fn test_heading_to_heading_margin_collapses() {
    // margin_bottom = 10, margin_top = 40 — collapse drops the 10.
    let style = Style {
        heading_margin_top_px: Some(40.0),
        heading_margin_bottom_px: Some(10.0),
        paragraph_spacing: 100.0,
        ..Style::default()
    };

    let h_alone = measure_with("## H", 400.0, 0.0, style.clone());
    let seq = measure_with("## H\n\n## H", 400.0, 0.0, style);

    // h_alone collapsed its own top margin; the second heading in `seq`
    // draws its 40, and the collapse drops the first one's trailing 10.
    let expected = 2.0 * h_alone.height + 40.0 - 10.0;
    assert_eq!(seq.height, expected);
}

#[test]
fn test_paragraph_to_paragraph_unchanged() {
    let style = Style {
        heading_margin_top_px: Some(40.0),
        heading_margin_bottom_px: Some(10.0),
        paragraph_spacing: 20.0,
        ..Style::default()
    };

    let text_alone = measure_with("Para.", 400.0, 0.0, style.clone());
    let seq = measure_with("Para.\n\nPara.", 400.0, 0.0, style);

    let joint = seq.height - 2.0 * text_alone.height;
    assert_eq!(joint, 20.0);
}

// --- TestLinkSanitization ---

#[test]
fn test_javascript_href_is_stripped() {
    let svg = render_default("[click](javascript:alert(document.cookie))");
    assert!(!svg.contains("javascript:"));
    assert!(svg.contains(r##"href="#""##));
}

#[test]
fn test_data_href_is_stripped() {
    let svg = render_default("[img](data:text/html,<script>alert(1)</script>)");
    assert!(!svg.contains("data:text"));
    assert!(svg.contains(r##"href="#""##));
}

#[test]
fn test_https_href_is_preserved() {
    let svg = render_default("[docs](https://example.com/guide)");
    assert!(svg.contains("https://example.com/guide"));
}

#[test]
fn test_relative_href_is_preserved() {
    let svg = render_default("[page](/dashboard/index)");
    assert!(svg.contains("/dashboard/index"));
}

#[test]
fn test_anchor_href_is_preserved() {
    let svg = render_default("[section](#intro)");
    assert!(svg.contains("#intro"));
}

#[test]
fn test_colon_free_relative_href_is_preserved() {
    let svg = render_default("[guide](guide/setup)");
    assert!(svg.contains("guide/setup"));
    assert!(!svg.contains(r##"href="#""##));
}

// --- TestInlineCodeChip (table-cell case excluded — table rendering isn't
// ported) ---

#[test]
fn test_no_brackets_in_output() {
    let svg = render_default_width("Use `foo` here", 400.0);
    assert!(!svg.contains("[foo]"));
    assert!(svg.contains("foo"));
}

#[test]
fn test_inline_code_emits_rect() {
    let svg = render_default_width("`code`", 400.0);
    assert!(svg.contains("<rect"));
}

#[test]
fn test_inline_code_rect_has_code_background() {
    let style = Style {
        code_background: "#aabbcc".to_string(),
        ..Style::default()
    };
    let svg = render("`code`", 400.0, style);
    assert!(svg.contains(r##"fill="#aabbcc""##));
}

#[test]
fn test_inline_code_rect_has_border_radius() {
    let style = Style {
        inline_code_border_radius: 5.0,
        ..Style::default()
    };
    let svg = render("`code`", 400.0, style);
    assert!(svg.contains(r#"rx="5""#));
}

#[test]
fn test_inline_code_rect_emitted_before_text() {
    let svg = render_default_width("`code`", 400.0);
    let rect_pos = svg.find("<rect").unwrap();
    let text_pos = svg.find("<text").unwrap();
    assert!(rect_pos < text_pos);
}

#[test]
fn test_inline_code_rect_width_includes_padding() {
    let padding = 4.0;
    let style = Style {
        inline_code_padding: padding,
        inline_code_border_radius: 3.0,
        ..Style::default()
    };
    let svg = render("`abc`", 400.0, style);

    let re = regex::Regex::new(r#"<rect[^>]*width="([^"]+)""#).unwrap();
    let rect_width: f64 = re.captures(&svg).unwrap()[1].parse().unwrap();

    assert!(rect_width > 0.0);
    assert!(rect_width > 2.0 * padding);
}

#[test]
fn test_inline_code_rect_height_covers_font_size() {
    let padding = 3.0;
    let font_size = 14.0;
    let style = Style {
        base_font_size: font_size,
        inline_code_padding: padding,
        inline_code_border_radius: 3.0,
        ..Style::default()
    };
    let svg = render("`word`", 400.0, style);

    let re = regex::Regex::new(r#"<rect[^>]*height="([^"]+)""#).unwrap();
    let rect_height: f64 = re.captures(&svg).unwrap()[1].parse().unwrap();
    assert!(rect_height >= font_size);
}

#[test]
fn test_inline_code_chip_geometry_uses_code_font_size() {
    fn chip_geometry(code_font_size: f64) -> (f64, f64) {
        let style = Style {
            base_font_size: 14.0,
            code_font_size,
            ..Style::default()
        };
        let svg = render("`code`", 400.0, style);
        let re = regex::Regex::new(r#"<rect[^>]*width="([^"]+)"[^>]*height="([^"]+)""#).unwrap();
        let caps = re.captures(&svg).unwrap();
        (caps[1].parse().unwrap(), caps[2].parse().unwrap())
    }

    let smaller_chip = chip_geometry(10.0);
    let larger_chip = chip_geometry(12.0);

    assert!(smaller_chip.0 < larger_chip.0);
    assert!(smaller_chip.1 < larger_chip.1);
}

#[test]
fn test_non_code_run_emits_no_rect() {
    let svg = render_default_width("plain text here", 400.0);
    assert!(!svg.contains("<rect"));
}

#[test]
fn test_wrapped_code_run_emits_two_rects() {
    // Use a very narrow width so the code token wraps.
    let svg = render_default_width("Use `long_variable_name` carefully", 60.0);
    assert!(svg.matches("<rect").count() >= 2);
}

#[test]
fn test_center_aligned_code_chip_present() {
    let style = Style {
        text_align: TextAlign::Center,
        ..Style::default()
    };
    let svg = render("`foo`", 400.0, style);
    assert!(svg.contains("<rect"));
    assert!(!svg.contains("[foo]"));
}

#[test]
fn test_right_aligned_code_chip_present() {
    let style = Style {
        text_align: TextAlign::Right,
        ..Style::default()
    };
    let svg = render("`foo`", 400.0, style);
    assert!(svg.contains("<rect"));
    assert!(!svg.contains("[foo]"));
}

#[test]
fn test_inline_code_in_heading_no_brackets() {
    let svg = render_default("# Check `status`");
    assert!(!svg.contains("[status]"));
    assert!(svg.contains("status"));
    assert!(svg.contains("<rect"));
}

#[test]
fn test_inline_code_in_blockquote_no_brackets() {
    let svg = render_default("> Quote `status` carefully");
    assert!(!svg.contains("[status]"));
    assert!(svg.contains("status"));
}

#[test]
fn test_chip_rect_y_aligns_with_cap_height_not_full_em() {
    let font_size = 14.0;
    let padding = 3.0;
    let style = Style {
        base_font_size: font_size,
        line_height: 1.25, // serif-typical; chip geometry must not depend on line_height
        inline_code_padding: padding,
        inline_code_border_radius: 3.0,
        // This test is about where the chip sits, not how code is sized.
        // Scale 1.0 makes the code size equal font_size, which is what the
        // cap-height arithmetic below assumes; the scale itself is covered
        // by TestCodeFontScale.
        code_font_scale: 1.0,
        ..Style::default()
    };
    let svg = super::render("`word`", 400.0, 20.0, style, test_fonts()).unwrap();

    let rect_y: f64 = regex::Regex::new(r#"<rect[^>]*y="([^"]+)""#)
        .unwrap()
        .captures(&svg)
        .unwrap()[1]
        .parse()
        .unwrap();
    let baseline_y: f64 = regex::Regex::new(r#"<text[^>]+y="([^"]+)""#)
        .unwrap()
        .captures(&svg)
        .unwrap()[1]
        .parse()
        .unwrap();

    // How far above baseline does the rect top sit?
    let above_baseline = baseline_y - rect_y;

    // Old formula gives above_baseline == font_size + padding == 17.
    // New formula gives above_baseline ≈ CAP_HEIGHT_RATIO * font_size +
    // padding ≈ 13. Cap height ratio for typical fonts is ~0.72, so cap top
    // is at 10.08px above baseline. With padding=3, rect top should be
    // ~13px above baseline, not 17.
    assert!(above_baseline < font_size + padding);
    // And the rect must still cover at least cap height above baseline
    // (plus padding). Cap height ≈ 0.65 * font_size (conservative lower
    // bound for most fonts).
    let min_above = 0.65 * font_size + padding;
    assert!(above_baseline >= min_above);
}

#[test]
fn test_chip_rect_bottom_includes_descender_allowance() {
    let font_size = 14.0;
    let padding = 3.0;
    let style = Style {
        base_font_size: font_size,
        line_height: 1.25,
        inline_code_padding: padding,
        inline_code_border_radius: 3.0,
        code_font_scale: 1.0, // see the sibling test — isolate anchoring from sizing
        ..Style::default()
    };
    let svg = super::render("`word`", 400.0, 20.0, style, test_fonts()).unwrap();

    let rect_caps = regex::Regex::new(r#"<rect[^>]*y="([^"]+)"[^>]*height="([^"]+)""#)
        .unwrap()
        .captures(&svg)
        .unwrap();
    let rect_y: f64 = rect_caps[1].parse().unwrap();
    let rect_height: f64 = rect_caps[2].parse().unwrap();
    let baseline_y: f64 = regex::Regex::new(r#"<text[^>]+y="([^"]+)""#)
        .unwrap()
        .captures(&svg)
        .unwrap()[1]
        .parse()
        .unwrap();

    let rect_bottom = rect_y + rect_height;
    let below_baseline = rect_bottom - baseline_y;

    // Old formula: below_baseline == padding == 3.
    // New formula: below_baseline == descender_ratio * font_size + padding
    // > padding. Descender ratio for common fonts ≈ 0.2, so
    // below_baseline ≈ 0.2*14 + 3 = 5.8.
    assert!(below_baseline > padding);
}

// --- TestBaselinePlacement ---

fn baselines(svg: &str) -> Vec<f64> {
    regex::Regex::new(r#"<text[^>]*\by="([\d.]+)""#)
        .unwrap()
        .captures_iter(svg)
        .map(|c| c[1].parse().unwrap())
        .collect()
}

fn expected_baseline_offset(measurer: &FontMeasurer, font_size: f64, line_height: f64) -> f64 {
    let ascent = measurer.ascent_em() * font_size;
    let descent = measurer.descent_em() * font_size;
    (font_size * line_height - ascent - descent) / 2.0 + ascent
}

#[test]
fn test_paragraph_baseline_splits_the_leading() {
    let style = Style {
        base_font_size: 14.0,
        line_height: 1.4,
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let svg = renderer.render(&parse("Body copy."), 400.0, 0.0).unwrap();

    let expected = expected_baseline_offset(&renderer.measurer, 14.0, 1.4);
    assert!((baselines(&svg)[0] - expected).abs() < 0.01);
}

#[test]
fn test_heading_baseline_splits_the_leading_below_its_margin() {
    // A heading's own top margin offsets the line box; the split is the
    // same. Its leading is negative — the shipped heading line heights are
    // tighter than a font's ascent-to-descender — so this is also the case
    // that pins the model holding when the text overflows the box it is
    // allotted.
    let style = Style {
        base_font_size: 14.0,
        heading_line_height: Some(1.1),
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style.clone(), test_fonts()).unwrap();
    // After a paragraph, so the heading draws the margin this test offsets by.
    let lead = renderer
        .measure(&parse("Body."), 400.0, 0.0)
        .unwrap()
        .height;
    let svg = renderer
        .render(&parse("Body.\n\n# Heading"), 400.0, 0.0)
        .unwrap();

    let font_size = style.get_heading_size(1);
    let expected = lead + 20.0 + expected_baseline_offset(&renderer.measurer, font_size, 1.1);
    assert!((baselines(&svg)[1] - expected).abs() < 0.01);
}

#[test]
fn test_line_advance_is_unchanged_by_where_the_baseline_sits() {
    // Only the baseline moves — the leading it splits is the same leading.
    // Consecutive baselines stay exactly one advance apart, so blocks keep
    // their heights and nothing stacked below a text block has to move.
    let style = Style {
        base_font_size: 14.0,
        line_height: 1.4,
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    // Narrow enough to wrap, so the two baselines are lines of one paragraph.
    let svg = renderer
        .render(&parse("One two three four five"), 60.0, 0.0)
        .unwrap();

    let ys = baselines(&svg);
    assert!(ys.len() >= 2, "{svg}");
    assert!((ys[1] - ys[0] - 14.0 * 1.4).abs() < 0.01);
}

#[test]
fn test_heading_baseline_reports_the_line_the_heading_was_drawn_on() {
    // Including at a zero heading line height, which is a reachable style.
    // `heading_line_height` is optional with no validator, so 0.0 is a
    // value a consumer of this library can set. Resolving it with `or`
    // treats it as unset and falls back to the body line height, which
    // reports a baseline far from the one drawn — on the one method whose
    // whole contract is "the number the heading was actually drawn with".
    for multiplier in [0.0, 1.1, 1.5] {
        let style = Style {
            base_font_size: 14.0,
            heading_line_height: Some(multiplier),
            heading_margin_top_px: Some(20.0),
            heading_margin_bottom_px: Some(10.0),
            ..Style::default()
        };
        let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
        let svg = renderer.render(&parse("# Heading"), 400.0, 0.0).unwrap();

        let drawn = baselines(&svg)[0];
        assert!(
            (renderer.first_baseline(&parse("# Heading"), 400.0).unwrap() - drawn).abs() < 0.01,
            "heading_line_height={multiplier}"
        );
    }
}

#[test]
fn test_heading_line_box_is_the_block_without_its_margins() {
    // The bottom margin is rhythm; what is left is the text. The top
    // margin is not in the block at all — an opening heading collapses it
    // — so the line box starts at the block's own top edge.
    let style = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let block = renderer
        .measure(&parse("# Heading"), 400.0, 0.0)
        .unwrap()
        .height;

    let (top, height) = renderer.heading_line_box(1, block);
    assert!(top.abs() < 1e-9);
    assert!((height - (block - 10.0)).abs() < 1e-9);
    let baseline = renderer.first_baseline(&parse("# Heading"), 400.0).unwrap();
    assert!(top <= baseline && baseline < top + height);
}

#[test]
fn test_vertical_metrics_come_from_the_face() {
    // Both are em fractions of the loaded font, and both refuse to guess.
    let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    let measurer = &renderer.measurer;

    assert!(measurer.ascent_em() > 0.0 && measurer.ascent_em() <= 2.0);
    assert!(measurer.descent_em() > 0.0 && measurer.descent_em() <= 2.0);
}

#[test]
#[should_panic(expected = "did not load")]
fn test_vertical_metrics_refuse_to_guess_on_a_missing_font() {
    let _ = FontMeasurer::new("/nonexistent/path/that/cannot/load.ttf", 0, None).ascent_em();
}

#[test]
fn test_a_list_marker_sits_on_its_item_s_own_baseline() {
    // A number labels the words beside it, so it shares their baseline.
    // The two used to be the same expression, so this held by
    // construction and nothing pinned it. Moving the text's baseline
    // without moving the marker left ordered lists with their numbers
    // riding above the words, growing with the font size.
    for font_size in [14.0, 24.0] {
        let style = Style {
            base_font_size: font_size,
            ..Style::default()
        };
        let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
        let svg = renderer
            .render(&parse("1. First item"), 400.0, 0.0)
            .unwrap();

        let ys = baselines(&svg);
        assert_eq!(ys.len(), 2, "{svg}");
        assert!(
            (ys[0] - ys[1]).abs() < 0.01,
            "at {font_size}px the marker sits at y={} and its item at y={}",
            ys[0],
            ys[1]
        );
    }
}

#[test]
fn test_a_bullet_is_centered_on_its_item_s_text() {
    // A bullet sits at the middle of its item's text. It gets there via
    // the line box's middle, which is the same point: the half-leading
    // above the ascent equals the half-leading below the descent, so the
    // two midpoints coincide at any line height.
    let style = Style {
        base_font_size: 14.0,
        line_height: 1.8,
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let svg = renderer.render(&parse("- First item"), 400.0, 0.0).unwrap();

    let cy: f64 = regex::Regex::new(r#"<circle[^>]*\bcy="([\d.]+)""#)
        .unwrap()
        .captures(&svg)
        .unwrap()[1]
        .parse()
        .unwrap();
    let baseline = baselines(&svg)[0];
    let measurer = &renderer.measurer;
    let expected = baseline - (measurer.ascent_em() - measurer.descent_em()) / 2.0 * 14.0;
    assert!((cy - expected).abs() < 0.01);
}

// --- TestOpeningBlockMarginCollapse ---

#[test]
fn test_a_heading_opening_a_document_sits_at_the_top_of_the_box() {
    let style = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let svg = renderer.render(&parse("# Heading"), 400.0, 0.0).unwrap();

    assert!((baselines(&svg)[0] - renderer.first_baseline(&parse("# Heading"), 400.0).unwrap()).abs() < 0.01);

    let wider = SVGRenderer::new(
        Style {
            base_font_size: 14.0,
            heading_margin_top_px: Some(200.0),
            heading_margin_bottom_px: Some(10.0),
            ..Style::default()
        },
        test_fonts(),
    )
    .unwrap();
    let wider_svg = wider.render(&parse("# Heading"), 400.0, 0.0).unwrap();
    assert!(
        (baselines(&wider_svg)[0] - baselines(&svg)[0]).abs() < 0.01,
        "the opening heading is still carrying its own top margin"
    );
}

#[test]
fn test_a_heading_after_a_paragraph_keeps_its_leading_margin() {
    // Only the opener collapses -- mid-document rhythm is untouched.
    let style = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();

    let opener = renderer
        .measure(&parse("# Heading"), 400.0, 0.0)
        .unwrap()
        .height;
    let with_paragraph = renderer
        .measure(&parse("Body text.\n\n# Heading"), 400.0, 0.0)
        .unwrap()
        .height;
    let paragraph = renderer
        .measure(&parse("Body text."), 400.0, 0.0)
        .unwrap()
        .height;

    assert!((with_paragraph - (paragraph + opener + 20.0)).abs() < 0.01);
}

#[test]
fn test_the_collapsed_margin_comes_off_the_block_s_height() {
    // The box shrinks with the ink -- otherwise the gap moves to the bottom.
    let margin_top = 20.0;
    let drawn = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(margin_top),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let none = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(0.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let blocks = parse("# Heading\n\nBody text.");

    let drawn_height = SVGRenderer::new(drawn, test_fonts())
        .unwrap()
        .measure(&blocks, 400.0, 0.0)
        .unwrap()
        .height;
    let none_height = SVGRenderer::new(none, test_fonts())
        .unwrap()
        .measure(&blocks, 400.0, 0.0)
        .unwrap()
        .height;
    assert!((drawn_height - none_height).abs() < 0.01);
}

#[test]
fn test_a_heading_opening_a_blockquote_collapses_too() {
    // A blockquote is a box, and the rule is the box's, not the document's.
    let style = Style {
        base_font_size: 14.0,
        heading_margin_top_px: Some(20.0),
        heading_margin_bottom_px: Some(10.0),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();

    let quoted = renderer
        .measure(&parse("> # Heading"), 400.0, 0.0)
        .unwrap()
        .height;
    let after = renderer
        .measure(&parse("> Body text.\n> # Heading"), 400.0, 0.0)
        .unwrap()
        .height;
    let body = renderer
        .measure(&parse("> Body text."), 400.0, 0.0)
        .unwrap()
        .height;

    assert!((after - body - quoted - 20.0).abs() < 0.01);
}

#[test]
fn hide_overflow_clip_id_matches_python() {
    // No ladder rung sets `hide`, so byte parity with Python is not covered by
    // the differential and is pinned here instead.
    //
    // Oracle digests, regenerate with:
    //   python -c "import hashlib; print(hashlib.sha256('a\x00b'.encode()).hexdigest()[:8])"
    assert_eq!(sha256_hex8("a\u{0}b"), "59b271ae");
    // The NUL separator is load-bearing: without it these two inputs collide.
    assert_eq!(sha256_hex8("ab"), "fb8e20fc");

    let style = Style {
        code_block_overflow: crate::style::CodeBlockOverflow::Hide,
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let render = |md: &str| renderer.render(&parse(md), 400.0, 0.0).unwrap();
    let clip_id = |svg: &str| {
        let start = svg.find("code-clip-").expect("no clip id emitted");
        svg[start..start + "code-clip-".len() + 8].to_string()
    };

    let one = clip_id(&render("```py\nx\n```"));
    assert_eq!(one, clip_id(&render("```py\nx\n```")), "id must be deterministic");
    assert!(
        one["code-clip-".len()..].chars().all(|c| c.is_ascii_hexdigit()),
        "id must be a hex digest, not an address: {one}"
    );

    // Swapping content and language across the separator must not collapse to
    // one id — the failure mode of a missing separator or a wrong field order.
    assert_ne!(one, clip_id(&render("```x\npy\n```")));
}

// --- test_block_windows.py: the column-packer contract ---

const LONG: &str = "North America's primary issue is insufficient MQL supply, not a regional \
Stage 0 target miss or a broad loss of selling capacity. Event sources \
explain most of the decline, while target-aligned Stage 0 remained above \
target and the funnel table stays the single source for the counts.";

fn block_windows_renderer() -> SVGRenderer {
    SVGRenderer::new(Style::default(), test_fonts()).unwrap()
}

#[test]
fn test_measure_blocks_reports_one_entry_per_block() {
    let renderer = block_windows_renderer();
    let blocks = parse(&format!("# Heading\n\n{LONG}\n\n{LONG}"));
    let metrics = renderer.measure_blocks(&blocks, 300.0).unwrap();
    assert_eq!(metrics.len(), blocks.len());
    assert_eq!(metrics.len(), 3);
}

#[test]
fn test_measure_blocks_line_count_tracks_available_width() {
    let renderer = block_windows_renderer();
    let blocks = parse(LONG);
    let narrow = renderer.measure_blocks(&blocks, 200.0).unwrap()[0];
    let wide = renderer.measure_blocks(&blocks, 600.0).unwrap()[0];
    assert!(narrow.line_count > wide.line_count);
    assert!(wide.line_count > 1);
}

#[test]
fn test_measure_blocks_headings_keep_with_next() {
    let renderer = block_windows_renderer();
    let blocks = parse(&format!("## Heading\n\n{LONG}"));
    let metrics = renderer.measure_blocks(&blocks, 300.0).unwrap();
    assert!(metrics[0].keep_with_next);
    assert!(!metrics[1].keep_with_next);
}

#[test]
fn test_measure_blocks_paragraphs_split_but_code_does_not() {
    let renderer = block_windows_renderer();
    let blocks = parse(&format!("{LONG}\n\n```\nx = 1\ny = 2\n```"));
    let metrics = renderer.measure_blocks(&blocks, 300.0).unwrap();
    assert!(metrics[0].splittable);
    assert!(!metrics[1].splittable);
}

#[test]
fn test_render_block_window_renders_only_the_requested_lines() {
    let renderer = block_windows_renderer();
    let blocks = parse(LONG);
    let block = &blocks[0];
    let total = renderer.measure_blocks(&blocks, 300.0).unwrap()[0].line_count;
    assert!(
        total >= 4,
        "fixture must wrap to several lines to be meaningful"
    );

    let head = renderer
        .render_block_window(block, 300.0, 0, Some(2))
        .unwrap();
    let tail = renderer
        .render_block_window(block, 300.0, 2, Some(total - 2))
        .unwrap();
    assert_eq!(head.elements.matches("<text").count(), 2);
    assert_eq!(tail.elements.matches("<text").count(), total - 2);
}

#[test]
fn test_render_block_window_lines_concatenate_to_the_whole_block() {
    fn words(svg: &str) -> String {
        let re = regex_lite_strip_tags(svg);
        re.split_whitespace().collect::<Vec<_>>().join(" ")
    }
    fn regex_lite_strip_tags(svg: &str) -> String {
        let mut out = String::new();
        let mut in_tag = false;
        for c in svg.chars() {
            match c {
                '<' => in_tag = true,
                '>' => in_tag = false,
                _ if !in_tag => out.push(c),
                _ => {}
            }
        }
        out
    }

    let renderer = block_windows_renderer();
    let blocks = parse(LONG);
    let block = &blocks[0];
    let total = renderer.measure_blocks(&blocks, 300.0).unwrap()[0].line_count;

    let whole = words(
        &renderer
            .render_block_window(block, 300.0, 0, Some(total))
            .unwrap()
            .elements,
    );
    let split = format!(
        "{} {}",
        words(
            &renderer
                .render_block_window(block, 300.0, 0, Some(2))
                .unwrap()
                .elements
        ),
        words(
            &renderer
                .render_block_window(block, 300.0, 2, Some(total - 2))
                .unwrap()
                .elements
        )
    );
    assert_eq!(split, whole);
}

#[test]
fn test_render_block_window_starts_at_the_top_of_its_own_box() {
    let renderer = block_windows_renderer();
    let blocks = parse(LONG);
    let block = &blocks[0];
    let total = renderer.measure_blocks(&blocks, 300.0).unwrap()[0].line_count;
    let tail = renderer
        .render_block_window(block, 300.0, total - 2, Some(2))
        .unwrap();

    let idx = tail
        .elements
        .find("y=\"")
        .expect("the window must render text");
    let rest = &tail.elements[idx + 3..];
    let end = rest.find('"').unwrap();
    let first_y: f64 = rest[..end].parse().unwrap();
    assert!(
        first_y < 40.0,
        "continuation should start near y=0, got {first_y}"
    );
}

#[test]
fn test_heading_leading_margin_tracks_the_heading_size() {
    let renderer = block_windows_renderer(); // default Style: the em branch, not the px one
    let margins: Vec<f64> = [1u8, 2, 3]
        .iter()
        .map(|&level| {
            renderer
                .measure_blocks(
                    &parse(&format!("{} Heading", "#".repeat(level as usize))),
                    300.0,
                )
                .unwrap()[0]
                .leading_margin
        })
        .collect();
    assert!(margins[0] > margins[1]);
    assert!(margins[1] > margins[2]);
}

#[test]
fn test_heading_leading_margin_matches_what_the_renderer_draws() {
    let renderer = block_windows_renderer();
    let blocks = parse("# Heading");
    let reported = renderer.measure_blocks(&blocks, 300.0).unwrap()[0].leading_margin;
    let drawn = renderer.heading_margins(&blocks[0]).0;
    assert!((reported - drawn).abs() < 1e-9);
}

// --- Class prefix and style-rule bodies (pinned against Python mdsvg) ---

#[test]
fn test_class_prefix_matches_python_for_default_style() {
    // Python: SVGRenderer(style=Style())._class_prefix
    assert!(render_default("hello").contains(".md-da98a35b-text "));
}

#[test]
fn test_heading_font_family_reaches_heading_rule_and_class_prefix() {
    let style = Style {
        font_family: "'Body Serif', serif".to_string(),
        heading_font_family: "'Head Sans', sans-serif".to_string(),
        ..Style::default()
    };
    let svg = render("# Title", 300.0, style);
    // Quotes are escaped inside the style block, and the whole rule set hashes
    // to the prefix Python derives from the same style.
    assert!(svg.contains(
        ".md-8d7cd083-heading { font-family: &#x27;Head Sans&#x27;, sans-serif; fill:"
    ));
}

#[test]
fn test_empty_heading_font_family_inherits_body_family() {
    let style = Style {
        font_family: "Georgia, serif".to_string(),
        ..Style::default()
    };
    let svg = render("# Title", 300.0, style);
    assert!(md_rule_body(&svg, "heading").starts_with(" font-family: Georgia, serif;"));
}

#[test]
fn test_escape_xml_escapes_whitespace_like_python() {
    assert_eq!(crate::utils::escape_xml("a\tb\nc\rd"), "a&#9;b&#10;c&#13;d");
}

// --- Heading measurement font ---

fn heading_lines(heading: Option<FontSlot>, markdown: &str) -> (usize, HashSet<&'static str>) {
    let fonts = FontSlots { heading, ..test_fonts() };
    let renderer = SVGRenderer::new(Style::default(), fonts).unwrap();
    let svg = renderer.render(&parse(markdown), 200.0, 0.0).unwrap();
    (svg.matches("<text ").count(), renderer.used_fonts())
}

#[test]
fn test_plain_heading_run_measures_against_the_heading_slot() {
    let markdown = "# A heading that wraps only in the wide font";
    let (body_lines, body_used) = heading_lines(None, markdown);
    let wide = FontSlot::new(inter_path("SourceCodePro-Regular.ttf"));
    let (wide_lines, wide_used) = heading_lines(Some(wide), markdown);
    assert!(wide_lines > body_lines, "{wide_lines} vs {body_lines}");
    assert!(!body_used.contains("heading"));
    assert!(wide_used.contains("heading"));
}

#[test]
fn test_emphasized_heading_span_keeps_the_body_font() {
    let wide = FontSlot::new(inter_path("SourceCodePro-Regular.ttf"));
    let (_, used) = heading_lines(Some(wide), "# **bold**");
    assert!(!used.contains("heading"));
}


/// A static heading typeface (no `wght` axis to re-instance) still measures
/// the heading, not the body, when a weight is requested: a `None` from the
/// re-weighting must fall back to the un-reweighted heading typeface, never
/// through to the body's.
#[test]
fn test_heading_measurement_uses_a_static_heading_typeface_with_a_weight() {
    let heading_file = "DBTSerifOldstyleProportional-Regular.ttf";
    let fonts = FontSlots {
        heading: Some(FontSlot::new(inter_path(heading_file))),
        ..test_fonts()
    };
    let renderer = SVGRenderer::new(Style::default(), fonts).unwrap();
    let sample = "measured heading text width against body";
    let expected =
        crate::fonts::cached_measurer(&inter_path(heading_file), 0, None).measure(sample, 14.0);
    let body = renderer
        .measure_text(sample, 14.0, false, false, false, None, false)
        .unwrap();
    let actual = renderer
        .measure_text(sample, 14.0, false, false, false, Some("bold"), true)
        .unwrap();
    assert_eq!(actual, expected);
    assert_ne!(actual, body);
}

// --- escaped style values (d2499ef6) ---

/// Port of `libs/markdown-svg/tests/test_svg_attribute_escaping.py`: every
/// authored `String` style field that reaches a CSS declaration or an
/// attribute value carries its escaped form, at every site it lands.
///
/// Production guards the sites with an AST scan; a byte oracle cannot, since
/// escaping a hex color is a no-op. Here each field in turn takes a hostile
/// value while one document renders every block kind (heading, paragraph,
/// bold, link, inline code, both lists, table, blockquote, fenced code, rule,
/// image). The escaped value must appear — the field reaches the output — and
/// no raw hostile character may follow a marker anywhere in it. A font-family
/// stack takes the full hostile payload (`style.py:287` escapes it instead of
/// refusing, `css_font_family_stack`); every other field takes the one
/// character `CSS_BREAKING_CHARS_RE` (`css.py:9`) lets through that XML still
/// escapes, `&`. Fields with no path to the output are listed with the reason.
const EVERY_BLOCK_KIND: &str = "# Heading\n\nPlain **bold** [link](https://example.com) `code`\n\n\
    - item\n\n1. item\n\n\
    | a | b |\n|---|---|\n| 1 | 2 |\n\n\
    > quoted\n\n```\ncode\n```\n\n---\n\n![alt](u.png)";

#[test]
fn test_every_authored_style_string_is_escaped_at_every_site() {
    const RAW_FORM: &str = "Q&Q";
    let hostile = || RAW_FORM.to_string();
    let fields: Vec<(&str, StyleMutation<'_>)> = vec![
        ("font_weight", Box::new(|s| s.font_weight = Some(hostile()))),
        ("bold_font_weight", Box::new(|s| s.bold_font_weight = hostile())),
        ("text_color", Box::new(|s| s.text_color = hostile())),
        ("heading_color", Box::new(|s| s.heading_color = Some(hostile()))),
        ("link_color", Box::new(|s| s.link_color = hostile())),
        ("code_color", Box::new(|s| s.code_color = hostile())),
        ("code_background", Box::new(|s| s.code_background = hostile())),
        (
            "code_block_border_color",
            Box::new(|s| s.code_block_border_color = hostile()),
        ),
        ("blockquote_color", Box::new(|s| s.blockquote_color = hostile())),
        (
            "blockquote_border_color",
            Box::new(|s| s.blockquote_border_color = hostile()),
        ),
        (
            "blockquote_background",
            Box::new(|s| s.blockquote_background = hostile()),
        ),
        ("code_font_weight", Box::new(|s| s.code_font_weight = hostile())),
        ("code_font_style", Box::new(|s| s.code_font_style = hostile())),
        (
            "code_font_decoration",
            Box::new(|s| s.code_font_decoration = hostile()),
        ),
        (
            "blockquote_font_weight",
            Box::new(|s| s.blockquote_font_weight = hostile()),
        ),
        (
            "blockquote_font_style",
            Box::new(|s| s.blockquote_font_style = hostile()),
        ),
        (
            "blockquote_font_decoration",
            Box::new(|s| s.blockquote_font_decoration = hostile()),
        ),
        (
            "heading_font_weight",
            Box::new(|s| s.heading_font_weight = hostile()),
        ),
        (
            "table_border_color",
            Box::new(|s| s.table_border_color = hostile()),
        ),
        (
            "table_header_background",
            Box::new(|s| s.table_header_background = hostile()),
        ),
        ("hr_color", Box::new(|s| s.hr_color = hostile())),
        (
            "image_preserve_aspect_ratio",
            Box::new(|s| s.image_preserve_aspect_ratio = hostile()),
        ),
    ];
    // No path to the output: `code_font_case`/`blockquote_font_case` (a case
    // transform, never interpolated), `code_theme` (highlighting is not
    // ported).
    let escaped = escape_xml(RAW_FORM);
    for (name, set) in &fields {
        let mut style = Style::default();
        set(&mut style);
        let svg = unfetched(EVERY_BLOCK_KIND, 400.0, style).0;
        assert!(svg.contains(&escaped), "{name}: escaped value never reached the SVG");
        assert!(!svg.contains(RAW_FORM), "{name}: raw {RAW_FORM:?} in the SVG");
    }
}

#[test]
fn test_every_authored_font_family_is_css_escaped_at_every_site() {
    const PAYLOAD: &str = "Q<Q>Q&Q\"Q'Q\nE";
    const RAW_FORMS: [&str; 6] = ["Q<", "Q>", "Q\"", "Q'", "Q\n", "Q&Q"];
    let fields: Vec<(&str, StyleMutation<'_>)> = vec![
        ("font_family", Box::new(|s| s.font_family = PAYLOAD.to_string())),
        ("mono_font_family", Box::new(|s| s.mono_font_family = PAYLOAD.to_string())),
        ("code_font_family", Box::new(|s| s.code_font_family = PAYLOAD.to_string())),
        (
            "blockquote_font_family",
            Box::new(|s| s.blockquote_font_family = PAYLOAD.to_string()),
        ),
        (
            "heading_font_family",
            Box::new(|s| s.heading_font_family = PAYLOAD.to_string()),
        ),
    ];
    let escaped = escape_xml(&crate::css::css_font_family_stack(PAYLOAD));
    for (name, set) in &fields {
        let mut style = Style::default();
        set(&mut style);
        let svg = unfetched(EVERY_BLOCK_KIND, 400.0, style).0;
        assert!(
            svg.contains(&format!("font-family: {escaped}")),
            "{name}: css-escaped stack never reached a CSS declaration"
        );
        // `mono`/`code` also reach the fenced-code `font-family="..."`
        // attribute, which production writes with `escape_xml` alone.
        let attribute = format!("font-family=\"{}\"", escape_xml(PAYLOAD));
        for raw in RAW_FORMS {
            assert!(
                !svg.replace(&attribute, "").contains(raw),
                "{name}: raw {raw:?} in the SVG"
            );
        }
    }
}

// --- Markdown images (`renderer.py::_render_image_block`) ---
//
// Board prose renders with `fetch_image_sizes=False` (`prose.py`), so every
// pinned string below is Python's output for that constructor, at
// `Style::default()` unless a test names its own style, padding 20.

fn unfetched(markdown: &str, width: f64, style: Style) -> (String, f64) {
    let renderer = SVGRenderer::new(style, test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false);
    let blocks = parse(markdown);
    let svg = renderer.render(&blocks, width, 20.0).unwrap();
    let height = renderer.measure(&blocks, width, 20.0).unwrap().height;
    (svg, height)
}

fn image_lines(svg: &str) -> Vec<&str> {
    svg.lines()
        .filter(|l| l.contains("<image") || l.contains("<title>"))
        .collect()
}

#[test]
fn image_block_without_dims_fills_the_column_at_the_fallback_ratio() {
    let (svg, height) = unfetched("![Logo](assets/images/dbt-logo.svg)", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg),
        [
            r#"  <image x="20" y="20" width="360" height="202.5" href="assets/images/dbt-logo.svg" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>Logo</title>",
        ]
    );
    assert_eq!(height, 242.5);
}

#[test]
fn image_block_explicit_width_alone_keeps_the_fallback_ratio() {
    let (svg, height) = unfetched("![Logo](u.png){width=140}", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="140" height="78.75" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 118.75);
}

#[test]
fn image_block_width_is_capped_to_the_column_but_height_is_not() {
    let (svg, height) = unfetched("![Logo](u.png){width=412 height=128}", 300.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="260" height="128" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 168.0);
}

#[test]
fn image_block_explicit_height_alone_spans_the_column() {
    let (svg, height) = unfetched("![Logo](u.png){height=50}", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="360" height="50" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 90.0);
}

#[test]
fn image_block_fractional_dims_pass_through_format_number() {
    let (svg, height) = unfetched("![Logo](u.png){width=140.5 height=12.25}", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="140.5" height="12.25" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 52.25);
}

#[test]
fn image_block_empty_alt_draws_no_title() {
    let (svg, _) = unfetched("![](u.png)", 400.0, Style::default());
    assert_eq!(image_lines(&svg).len(), 1);
}

#[test]
fn image_block_escapes_alt_and_href() {
    let (svg, _) = unfetched(
        r#"![a <b> & "q"](https://x.test/a.png?x=1&y=2)"#,
        400.0,
        Style::default(),
    );
    assert_eq!(
        image_lines(&svg),
        [
            r#"  <image x="20" y="20" width="360" height="202.5" href="https://x.test/a.png?x=1&amp;y=2" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>a &lt;b&gt; &amp; &quot;q&quot;</title>",
        ]
    );
}

#[test]
fn image_block_href_passes_every_scheme_through_unfiltered() {
    // A markdown link's href is scheme-filtered; an image's is not.
    for url in [
        "javascript:alert",
        "file:///etc/passwd",
    ] {
        let (svg, height) = unfetched(&format!("![x]({url})"), 400.0, Style::default());
        assert_eq!(
            image_lines(&svg)[0],
            format!(
                r#"  <image x="20" y="20" width="360" height="202.5" href="{url}" preserveAspectRatio="xMidYMid meet"/>"#
            )
        );
        assert_eq!(height, 242.5);
    }
}

#[test]
fn image_block_height_ties_round_like_format_number() {
    // 10 / (16/9) = 5.625 exactly; `format_number` rounds the tie to even.
    let (svg, height) = unfetched("![x](u.png){width=10}", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="10" height="5.62" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 45.625);
}

#[test]
fn image_block_style_defaults_and_preserve_aspect_ratio() {
    let style = Style {
        image_width: Some(100.0),
        image_height: Some(30.0),
        image_preserve_aspect_ratio: "xMinYMin slice".to_string(),
        ..Style::default()
    };
    let (svg, height) = unfetched("![Logo](u.png)", 400.0, style.clone());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="100" height="30" href="u.png" preserveAspectRatio="xMinYMin slice"/>"#
    );
    assert_eq!(height, 70.0);

    // An authored width beats the style width; the style height still applies.
    let (svg, _) = unfetched("![Logo](u.png){width=90}", 400.0, style.clone());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="90" height="30" href="u.png" preserveAspectRatio="xMinYMin slice"/>"#
    );

    // An authored height beats the style height, and the style width still applies.
    let (svg, height) = unfetched("![Logo](u.png){height=50}", 400.0, style);
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="100" height="50" href="u.png" preserveAspectRatio="xMinYMin slice"/>"#
    );
    assert_eq!(height, 90.0);
}

#[test]
fn image_block_authored_height_is_not_capped_to_the_width() {
    let (svg, height) = unfetched("![Logo](u.png){width=100 height=300}", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="100" height="300" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 340.0);
}

#[test]
fn image_block_flows_between_paragraphs_and_after_headings() {
    let (svg, height) = unfetched("Intro para\n\n![Logo](u.png)\n\nAfter para", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="53" width="360" height="202.5" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 308.5);

    let (svg, height) = unfetched("# H\n![Logo](u.png)", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="76" width="360" height="202.5" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 298.5);
}

#[test]
fn image_block_in_a_blockquote_sits_inside_the_rule() {
    let (svg, height) = unfetched("> ![Logo](u.png)", 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="36" y="20" width="344" height="193.5" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
    assert_eq!(height, 233.5);
}

#[test]
fn image_block_with_size_fetching_on_is_refused_by_name() {
    let err = super::render("![Logo](u.png)", 400.0, 20.0, Style::default(), test_fonts())
        .unwrap_err();
    assert_eq!(err, "mdsvg-rs: image size fetch not supported");
}

/// `<svg viewBox="0 0 412 128"/>`: the corpus logo's intrinsic size.
const SVG_412X128: &str = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0MTIgMTI4Ii8+";

fn image_with(md: &str, style: Style, fetch_image_sizes: bool) -> Result<String, String> {
    let renderer = SVGRenderer::new(style, test_fonts())
        .unwrap()
        .with_fetch_image_sizes(fetch_image_sizes);
    let svg = renderer.render(&parse(md), 400.0, 20.0)?;
    Ok(image_lines(&svg)[0].replace(SVG_412X128, "SVG"))
}

#[test]
fn image_block_height_comes_from_a_data_uri_s_decoded_size() {
    let cases = [
        // An authored width alone scales by the decoded aspect ratio...
        ("{width=206}", Style::default(), "206", "64"),
        // ...ahead of `style.image_height`, which only beats the decoded
        // size when no width was authored (`renderer.py:2262-2273`).
        ("{width=206}", Style { image_height: Some(30.0), ..Style::default() }, "206", "64"),
        ("", Style { image_height: Some(30.0), ..Style::default() }, "360", "30"),
        ("", Style::default(), "360", "111.84"),
    ];
    for (attrs, style, width, height) in cases {
        let md = format!("![Logo]({SVG_412X128}){attrs}");
        assert_eq!(
            image_with(&md, style, false).unwrap(),
            format!(r#"  <image x="20" y="20" width="{width}" height="{height}" href="SVG" preserveAspectRatio="xMidYMid meet"/>"#),
            "{attrs:?}"
        );
    }
}

#[test]
fn image_block_data_uri_is_decoded_with_size_fetching_on() {
    // A data URI is measured from its own bytes; fetching gates only reads.
    let md = format!("![Logo]({SVG_412X128}){{width=206}}");
    assert_eq!(
        image_with(&md, Style::default(), true).unwrap(),
        r#"  <image x="20" y="20" width="206" height="64" href="SVG" preserveAspectRatio="xMidYMid meet"/>"#
    );
}

#[test]
fn image_block_unsized_data_uri_keeps_the_fallback_ratio() {
    assert_eq!(
        image_with("![Logo](data:image/svg+xml;base64,PHN2Zy8+){width=140}", Style::default(), false).unwrap(),
        r#"  <image x="20" y="20" width="140" height="78.75" href="data:image/svg+xml;base64,PHN2Zy8+" preserveAspectRatio="xMidYMid meet"/>"#
    );
}

#[test]
fn image_block_corrupt_data_uri_raises_even_with_both_dimensions_authored() {
    // `_render_image_block` reads the size before it looks at the authored dims.
    let md = "![Logo](data:image/png;base64){width=10 height=10}";
    assert_eq!(
        image_with(md, Style::default(), false).unwrap_err(),
        "Malformed data: URI \u{2014} no ',' separating header from payload"
    );
    // Enforcing the aspect ratio skips the read, so nothing is decoded.
    let enforced = Style {
        image_enforce_aspect_ratio: true,
        ..Style::default()
    };
    assert_eq!(
        image_with(md, enforced, false).unwrap(),
        r#"  <image x="20" y="20" width="10" height="10" href="data:image/png;base64" preserveAspectRatio="xMidYMid meet"/>"#
    );
}

#[test]
fn image_block_with_a_data_uri_and_both_dimensions_needs_no_size() {
    let uri = "data:image/svg+xml;base64,PHN2Zy8+";
    let (svg, _) = unfetched(&format!("![Logo]({uri}){{width=140 height=40}}"), 400.0, Style::default());
    assert_eq!(
        image_lines(&svg)[0],
        format!(
            r#"  <image x="20" y="20" width="140" height="40" href="{uri}" preserveAspectRatio="xMidYMid meet"/>"#
        )
    );
}

#[test]
fn image_block_enforcing_the_aspect_ratio_never_fetches() {
    // Python skips the fetch when `image_enforce_aspect_ratio` is set, so the
    // size is unknown either way and the fallback ratio applies.
    let style = Style {
        image_enforce_aspect_ratio: true,
        ..Style::default()
    };
    let svg = super::render("![Logo](u.png)", 400.0, 20.0, style, test_fonts()).unwrap();
    assert_eq!(
        image_lines(&svg)[0],
        r#"  <image x="20" y="20" width="360" height="202.5" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#
    );
}

#[test]
fn image_block_with_a_non_positive_fallback_ratio_is_refused_by_name() {
    let style = Style {
        image_fallback_aspect_ratio: 0.0,
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false);
    let err = renderer
        .render(&parse("![Logo](u.png)"), 400.0, 20.0)
        .unwrap_err();
    assert_eq!(err, "mdsvg-rs: image non-positive fallback aspect ratio not supported");
}

// --- Fenced code highlighting (`renderer.py::_logical_code_lines`) ---

#[test]
fn fenced_code_with_an_unported_language_refuses_by_name_while_highlighting_is_on() {
    let style = Style {
        code_highlight: true,
        ..Style::default()
    };
    let md = "```json\n{\"x\": 1}\n```";
    let want = "mdsvg-rs: fenced code highlighting for language `json` not ported";
    let err = super::render(md, 400.0, 20.0, style.clone(), test_fonts()).unwrap_err();
    assert_eq!(err, want);
    let err = super::measure(md, 400.0, 20.0, style, test_fonts()).unwrap_err();
    assert_eq!(err, want);
}

#[test]
fn fenced_code_paints_plain_without_a_language_or_without_highlighting() {
    let on = Style {
        code_highlight: true,
        ..Style::default()
    };
    // No fence language: Python takes the plain branch even with highlighting on.
    assert!(super::render("```\nkey: value\n```", 400.0, 20.0, on.clone(), test_fonts()).is_ok());
    // An alias Pygments does not know is the plain block, not a refusal.
    let plain = super::render("```nosuchlang\nkey: value\n```", 400.0, 20.0, on, test_fonts());
    assert!(plain.unwrap().contains(">key: value</text>"));
    assert!(render("```yaml\nkey: value\n```", 400.0, Style::default()).contains("key: value"));
}

#[test]
fn a_yaml_fence_paints_one_tspan_per_pygments_token() {
    // `highlight.py` colors `key` Name.Tag and the space Whitespace; `:` and
    // the plain scalar `value` take the theme's color for their token type,
    // or the block's default fill where it has none
    // (`renderer.py::_render_code_line_element`).
    let dflt = "#be185d";
    for (theme, key, colon, space, value) in [
        ("default", "#008000", dflt, "#bbbbbb", dflt),
        ("monokai", "#ff4689", "#f8f8f2", "#f8f8f2", "#ae81ff"),
    ] {
        let style = Style {
            code_highlight: true,
            code_theme: theme.into(),
            ..Style::default()
        };
        let svg = render("```yaml\nkey: value\n```", 400.0, style);
        let want = format!(
            "<tspan fill=\"{key}\">key</tspan><tspan fill=\"{colon}\">:</tspan>\
             <tspan fill=\"{space}\"> </tspan><tspan fill=\"{value}\">value</tspan></text>"
        );
        assert!(svg.contains(&want), "{theme}: {svg}");
    }
}

// --- Raw HTML (`renderer.py::_render_raw_html_block`, `allow_raw_html=False`) ---
//
// The constructor default, and the only value board prose reaches for the
// `none` and `safe-subset` tiers (`boards.py` passes `allow_raw_html=
// board.html_policy == "trusted-raw"`): a raw block is one plain-text
// paragraph of its source, never parsed, sanitized, or dropped. The
// `foreignObject` arm (`allow_raw_html=True`) is not ported.

fn raw_html_as_paragraph(html: &str) -> Vec<AnyBlock> {
    vec![AnyBlock::Paragraph(Paragraph {
        spans: vec![Span::text(html)],
    })]
}

fn render_blocks_at(blocks: &Document, width: f64) -> (String, f64) {
    let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    (
        renderer.render(blocks, width, 20.0).unwrap(),
        renderer.measure(blocks, width, 20.0).unwrap().height,
    )
}

#[test]
fn raw_html_block_paints_as_one_plain_paragraph_of_its_source() {
    for (markdown, html) in [
        (
            r#"<div style="display:flex">hello</div>"#,
            r#"<div style="display:flex">hello</div>"#,
        ),
        ("<div>\n  <p>a &amp; b</p>\n</div>", "<div>\n  <p>a &amp; b</p>\n</div>"),
        ("<img src=x onerror=alert(1)>", "<img src=x onerror=alert(1)>"),
        ("::: html\n<span>f</span>\n**not bold**\n:::", "<span>f</span>\n**not bold**"),
    ] {
        let blocks = parse(markdown);
        assert_eq!(
            blocks,
            vec![AnyBlock::RawHtmlBlock(RawHtmlBlock { html: html.to_string() })],
            "{markdown:?}"
        );
        assert_eq!(
            render_blocks_at(&blocks, 400.0),
            render_blocks_at(&raw_html_as_paragraph(html), 400.0),
            "{markdown:?}"
        );
    }
}

#[test]
fn raw_html_block_source_reaches_the_svg_escaped() {
    let svg = render_default(r#"<div style="display:flex">hello</div>"#);
    assert!(svg.contains("&lt;div"), "{svg}");
    assert!(svg.contains("display:flex&quot;&gt;hello&lt;/div&gt;"), "{svg}");
    assert!(!svg.contains("<div"), "{svg}");
    assert!(!svg.contains("<foreignObject"), "{svg}");
}

#[test]
fn raw_html_block_inside_a_blockquote_paints_the_same_way() {
    let svg = render_default("> <div>quoted</div>");
    assert!(svg.contains("&lt;div&gt;quoted&lt;/div&gt;"), "{svg}");
    assert!(!svg.contains("<div"), "{svg}");
}

#[test]
fn contains_raw_html_finds_a_block_at_any_depth() {
    assert!(contains_raw_html(&parse("<div>x</div>")));
    assert!(contains_raw_html(&parse("intro\n\n> <div>x</div>")));
    assert!(contains_raw_html(&parse("::: html\nx\n:::")));
    assert!(!contains_raw_html(&parse("plain <b>inline</b> and a < b")));
    assert!(!contains_raw_html(&parse("> quoted <b>inline</b>")));
}

// A block that follows a nested item is re-parsed as that item's children, so
// a list item can hold a raw block; so can a quote inside a quote or a list
// inside a quote.
#[test]
fn contains_raw_html_walks_list_item_children_and_nested_containers() {
    for markdown in [
        "- a\n  - b\n  <div>x</div>",
        "1. a\n   - b\n   <div>x</div>",
        "1. a\n   1. b\n   <div>x</div>",
        "> > <div>x</div>",
        "> - a\n>   - b\n>   <div>x</div>",
        "- a\n  - b\n  > <div>x</div>",
    ] {
        assert!(contains_raw_html(&parse(markdown)), "{markdown:?}");
    }
    assert!(!contains_raw_html(&parse("- a\n  - b <i>inline</i>\n- c")));
}

#[test]
fn raw_html_block_in_a_list_item_paints_as_escaped_text() {
    let svg = render_default("- a\n  - b\n  <div>after nested</div>");
    assert!(svg.contains("&lt;div&gt;after nested&lt;/div&gt;"), "{svg}");
    assert!(!svg.contains("<div"), "{svg}");
}

// --- max_content_width (`renderer.py::SVGRenderer.max_content_width`) ---
//
// The expected widths are production's own, from `SVGRenderer.max_content_width`
// over the same fonts and the default `Style`.

const SENTENCE: &str = "Pipeline coverage held steady across every region this quarter";
const MAX_CONTENT_TABLE: &str =
    "| Region | Pipeline coverage |\n|---|---|\n| Americas | 3.2x |\n| EMEA | 2.9x |";

fn max_content(markdown: &str) -> f64 {
    SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .max_content_width(&parse(markdown))
        .unwrap()
}

fn max_content_cases() -> Vec<(&'static str, String, f64)> {
    vec![
        ("paragraph", SENTENCE.to_string(), 414.27),
        ("heading", format!("# {SENTENCE}"), 855.42),
        ("bullets", format!("- {SENTENCE}\n- short"), 432.27),
        ("nested bullets", format!("- top\n  - {SENTENCE}"), 450.27),
        ("numbered", format!("1. {SENTENCE}"), 432.27),
        ("blockquote", format!("> {SENTENCE}"), 430.27),
        ("bold run", format!("Plain **{SENTENCE}** plain"), 485.49),
        ("code span", format!("Run `{SENTENCE}` now"), 530.13),
        ("table", MAX_CONTENT_TABLE.to_string(), 269.05),
        (
            "code block",
            "```\nlet x = 1;\nlonger_line_of_code(x, y, z)\n```".to_string(),
            234.0,
        ),
        ("block image", "![a](u.png){width=300 height=100}".to_string(), 300.0),
        (
            "widest block wins",
            format!("{SENTENCE}\n\n# {SENTENCE} and more"),
            990.82,
        ),
        ("empty", String::new(), 0.0),
    ]
}

#[test]
fn max_content_width_is_production_s_for_every_block_kind() {
    for (name, markdown, expected) in max_content_cases() {
        assert_eq!(max_content(&markdown), expected, "{name}");
    }
}

#[test]
fn nothing_wraps_at_max_content_and_something_wraps_below() {
    for (name, markdown, _) in max_content_cases() {
        if markdown.is_empty() || markdown.starts_with("![") {
            continue;
        }
        let natural = max_content(&markdown);
        let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
        let blocks = parse(&markdown);
        let height = |width: f64| renderer.measure(&blocks, width, 0.0).unwrap().height;
        let unwrapped = height(10_000.0);
        assert_eq!(height(natural.ceil()), unwrapped, "{name}");
        assert!(height(natural * 0.8) > unwrapped, "{name}");
    }
}

#[test]
fn a_blockquote_asks_for_the_size_and_case_it_is_painted_at() {
    let style = Style {
        blockquote_font_size: 22.0,
        blockquote_font_case: "upper".to_string(),
        ..Style::default()
    };
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    for (markdown, expected) in [
        (format!("> {SENTENCE}"), 825.54),
        ("> Revenue is `net_revenue` minus **returns** overall".to_string(), 581.82),
    ] {
        assert_eq!(renderer.max_content_width(&parse(&markdown)).unwrap(), expected, "{markdown}");
    }
}

// --- Divider style: horizontal rules and table gridlines honor visibility,
// width and dashes (TestDividerStyle in libs/markdown-svg/tests/test_renderer.py).

const HR_MARKDOWN: &str = "above\n\n---\n\nbelow";
const GRID_MARKDOWN: &str = "| a | b |\n|---|---|\n| 1 | 2 |";

fn divider_svg(markdown: &str, style: Style) -> String {
    render(markdown, 300.0, style)
}

#[test]
fn a_solid_horizontal_rule_is_the_unchanged_filled_rect() {
    let svg = divider_svg(HR_MARKDOWN, Style::default());
    assert!(regex::Regex::new(r##"<rect[^>]*height="1"[^>]*fill="#e5e7eb"/>"##)
        .unwrap()
        .is_match(&svg));
    assert!(!svg.contains("stroke-dasharray"));
}

#[test]
fn a_hidden_horizontal_rule_keeps_its_space_and_paints_nothing() {
    let height = |style: Style| {
        super::measure(HR_MARKDOWN, 300.0, 20.0, style, test_fonts()).unwrap().height
    };
    let hidden = Style { hr_visible: false, ..Style::default() };
    assert_eq!(height(hidden.clone()), height(Style::default()));
    assert!(!divider_svg(HR_MARKDOWN, hidden).contains(r##"fill="#e5e7eb""##));
}

#[test]
fn a_dashed_horizontal_rule_is_a_stroked_line_centered_on_its_band() {
    let style = Style {
        hr_dasharray: "6 3".to_string(),
        hr_height: 2.0,
        hr_color: "#123456".to_string(),
        ..Style::default()
    };
    let svg = divider_svg(HR_MARKDOWN, style);
    assert!(regex::Regex::new(
        r##"<line[^>]*stroke="#123456"[^>]*stroke-width="2"[^>]*stroke-dasharray="6 3"/>"##
    )
    .unwrap()
    .is_match(&svg));
    // The line sits mid-band: y1 == y2 == band top + height / 2.
    let rect_y = divider_svg(HR_MARKDOWN, Style { hr_height: 2.0, ..Style::default() });
    let band_y: f64 = regex::Regex::new(r#"<rect x="[^"]*" y="([^"]*)" width="[^"]*" height="2""#)
        .unwrap()
        .captures(&rect_y)
        .unwrap()[1]
        .parse()
        .unwrap();
    assert!(svg.contains(&format!(r#"y1="{0}" x2="#, band_y + 1.0)), "{svg}");
}

#[test]
fn table_gridlines_take_width_and_dashes() {
    let style = Style {
        table_border_width: 2.0,
        table_border_dasharray: "2 2".to_string(),
        ..Style::default()
    };
    let svg = divider_svg(GRID_MARKDOWN, style);
    assert!(svg.contains(r#"stroke-width="2" stroke-dasharray="2 2""#), "{svg}");
    // Every gridline (outer border, column and row separators) shares the stroke.
    assert_eq!(svg.matches(r#"stroke-dasharray="2 2""#).count(), 3, "{svg}");
}

#[test]
fn a_default_table_gridline_carries_no_width_or_dash_attribute() {
    let svg = divider_svg(GRID_MARKDOWN, Style::default());
    assert!(svg.contains(r##"fill="none" stroke="#e5e7eb"/>"##), "{svg}");
    assert!(!svg.contains("stroke-width") && !svg.contains("stroke-dasharray"), "{svg}");
}

#[test]
fn hidden_table_gridlines_paint_no_stroke_and_keep_the_height() {
    let hidden = Style { table_border_visible: false, ..Style::default() };
    assert!(!divider_svg(GRID_MARKDOWN, hidden.clone()).contains("stroke="));
    let height = |style: Style| {
        super::measure(GRID_MARKDOWN, 300.0, 20.0, style, test_fonts()).unwrap().height
    };
    assert_eq!(height(hidden), height(Style::default()));
}

// --- test_wrap_glue.py: no line break between an inline span and the
// punctuation it touches ---

const GLUE_QUOTE: &str = "\u{201c}It may not have been a very clever fellow who invented curves, \
     but he was assuredly lazy\u{201d} (*Charts and Graphs*, 1923).";

const GLUE_CODE_SPAN: &str = "The temporal axis lands on that row (`'25`) at the first label, and then \
     (`Jan'26`) at the second label, so a break must keep each pair together.";

fn wrapped_lines(markdown: &str, width: f64, avoid_runts: bool) -> Vec<String> {
    let style = Style { avoid_runts, ..Style::default() };
    let base_font_size = style.base_font_size;
    let renderer = SVGRenderer::new(style, test_fonts()).unwrap();
    let AnyBlock::Paragraph(paragraph) = &parse(markdown)[0] else {
        panic!("not a paragraph: {markdown}");
    };
    let runs = renderer.build_text_runs(&paragraph.spans, width).unwrap();
    renderer
        .wrap_runs(&runs, width, base_font_size, None, false)
        .unwrap()
        .iter()
        .map(|line| line.iter().map(|run| run.text.as_str()).collect::<String>())
        .collect()
}

fn assert_no_orphaned_punctuation(markdown: &str, widths: std::ops::Range<usize>, closers: &[&str]) {
    for avoid_runts in [false, true] {
        for width in widths.clone().step_by(5) {
            for line in wrapped_lines(markdown, width as f64, avoid_runts) {
                assert!(!line.ends_with('('), "orphaned '(' at width {width}: {line:?}");
                for closer in closers {
                    assert!(
                        !line.starts_with(closer),
                        "orphaned {closer:?} at width {width}: {line:?}"
                    );
                }
            }
        }
    }
}

#[test]
fn test_no_break_between_span_and_adjacent_punctuation() {
    assert_no_orphaned_punctuation(GLUE_QUOTE, 150..420, &[","]);
}

#[test]
fn test_no_break_between_code_span_and_adjacent_punctuation() {
    assert_no_orphaned_punctuation(GLUE_CODE_SPAN, 120..420, &[")", ","]);
}

#[test]
fn test_no_break_between_inline_image_and_adjacent_punctuation() {
    // `<svg ... viewBox="0 0 16 16" width="16" height="16"><rect .../></svg>`.
    let square = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxNiAxNiIgd2lkdGg9IjE2IiBoZWlnaHQ9IjE2Ij48cmVjdCB3aWR0aD0iMTYiIGhlaWdodD0iMTYiLz48L3N2Zz4=";
    let image = format!(
        "A small mark sits beside each name (![mark]({square}){{width=16 height=16}}) \
         in this sentence, so a break must keep the image with its parentheses."
    );
    assert_no_orphaned_punctuation(&image, 120..420, &[")"]);
}

// --- Inline images (`renderer.py::_inline_image`, `_layout_lines`, `_emit_line`) ---
//
// Pinned strings are Python's render at `test_fonts()`, padding 20, with
// `fetch_image_sizes=False`; `SQ40`/`SQ12` are square SVG data URIs and
// `W412` is the 412x128 logo, substituted back for readability.

const SQ40: &str = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0MCIgaGVpZ2h0PSI0MCIvPg==";
const SQ12: &str = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMiIgaGVpZ2h0PSIxMiIvPg==";

fn inline(md: &str, style: Style, width: f64) -> Result<(Vec<String>, f64, f64), String> {
    let subs = [(SQ40, "SQ40"), (SQ12, "SQ12"), (SVG_412X128, "W412")];
    let mut source = md.to_string();
    for (uri, name) in subs {
        source = source.replace(name, uri);
    }
    let renderer = SVGRenderer::new(style, test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false);
    let blocks = parse(&source);
    let svg = renderer.render(&blocks, width, 20.0)?;
    let height = renderer.measure(&blocks, width, 20.0)?.height;
    let max_content = renderer.max_content_width(&blocks)?;
    let lines = svg
        .lines()
        .filter(|l| ["<image", "<title>", "<text", "<circle", "<rect"].iter().any(|t| l.contains(t)))
        .map(|l| subs.iter().fold(l.replace("md-da98a35b-", ""), |l, (uri, name)| l.replace(uri, name)))
        .collect();
    Ok((lines, height, max_content))
}

fn assert_inline(md: &str, style: Style, width: f64, want: &[&str], height: f64, max_content: f64) {
    let (lines, got_height, got_max_content) = inline(md, style, width).unwrap();
    assert_eq!(lines, want, "{md:?}");
    assert_eq!(got_height, height, "{md:?} height");
    assert_eq!(got_max_content, max_content, "{md:?} max-content");
}

#[test]
fn inline_image_sits_on_the_baseline_at_its_decoded_size() {
    // The 40px image is taller than the text ascent, so the line grows upward
    // by the difference and the text baseline moves down with it.
    assert_inline(
        "Before ![logo](SQ40) after",
        Style::default(),
        400.0,
        &[
            r#"  <text x="20" y="60" font-size="14" class="text" text-anchor="start">Before </text>"#,
            r#"  <image x="68.27" y="20" width="40" height="40" href="SQ40" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>logo</title>",
            r#"  <text x="112.21" y="60" font-size="14" class="text" text-anchor="start">after</text>"#,
        ],
        85.4072265625,
        123.27,
    );
}

#[test]
fn inline_image_takes_its_authored_size() {
    assert_inline(
        "See ![logo](u.png){width=60 height=30} here",
        Style::default(),
        400.0,
        &[
            r#"  <text x="20" y="50" font-size="14" class="text" text-anchor="start">See </text>"#,
            r#"  <image x="49.24" y="20" width="60" height="30" href="u.png" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>logo</title>",
            r#"  <text x="113.18" y="50" font-size="14" class="text" text-anchor="start">here</text>"#,
        ],
        75.4072265625,
        123.06,
    );
}

#[test]
fn inline_image_of_unknown_size_fills_the_line_at_the_fallback_ratio() {
    // Sized for the full measure, the image is a line of its own; for
    // max-content it is sized for no width at all, so it asks for none.
    assert_inline(
        "Signed by ![logo](https://example.invalid/logo.png) today",
        Style::default(),
        400.0,
        &[
            r#"  <text x="20" y="35.59" font-size="14" class="text" text-anchor="start">Signed by</text>"#,
            r#"  <image x="20" y="41" width="360" height="202.5" href="https://example.invalid/logo.png" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>logo</title>",
            r#"  <text x="20" y="264.5" font-size="14" class="text" text-anchor="start">today</text>"#,
        ],
        289.9072265625,
        111.5,
    );
}

#[test]
fn inline_image_wider_than_the_column_scales_down_and_the_paragraph_wraps_around_it() {
    assert_inline(
        "word word word word word word word word word word ![logo](W412) word word word word word word word word.",
        Style::default(),
        240.0,
        &[
            r#"  <text x="20" y="35.59" font-size="14" class="text" text-anchor="start">word word word word word</text>"#,
            r#"  <text x="20" y="56.59" font-size="14" class="text" text-anchor="start">word word word word word</text>"#,
            r#"  <image x="20" y="62" width="200" height="62.14" href="W412" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>logo</title>",
            r#"  <text x="20" y="145.14" font-size="14" class="text" text-anchor="start">word word word word word</text>"#,
            r#"  <text x="20" y="166.14" font-size="14" class="text" text-anchor="start">word word word.</text>"#,
        ],
        191.5431488925971,
        1093.41,
    );
}

#[test]
fn list_marker_drops_with_an_image_line() {
    assert_inline(
        "- ![thumb](SQ40) Item one\n- Item two",
        Style::default(),
        400.0,
        &[
            r##"  <circle cx="30" cy="54.91" r="1.5" fill="#1a1a1a"/>"##,
            r#"  <image x="38" y="20" width="40" height="40" href="SQ40" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>thumb</title>",
            r#"  <text x="81.94" y="60" font-size="14" class="text" text-anchor="start">Item one</text>"#,
            r##"  <circle cx="30" cy="76.91" r="1.5" fill="#1a1a1a"/>"##,
            r#"  <text x="38" y="82" font-size="14" class="text" text-anchor="start">Item two</text>"#,
        ],
        107.4072265625,
        119.47,
    );
    assert_inline(
        "1. ![thumb](SQ40) Item one",
        Style::default(),
        400.0,
        &[
            r#"  <text x="30" y="60" class="text" font-size="14" text-anchor="end">1.</text>"#,
            r#"  <image x="38" y="20" width="40" height="40" href="SQ40" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>thumb</title>",
            r#"  <text x="81.94" y="60" font-size="14" class="text" text-anchor="start">Item one</text>"#,
        ],
        85.4072265625,
        119.47,
    );
}

#[test]
fn heading_image_no_taller_than_its_text_paints() {
    assert_inline(
        "## Release ![badge](SQ12)",
        Style::default(),
        400.0,
        &[
            r#"  <text x="20" y="44.95" font-size="22.4" class="heading" text-anchor="start"><tspan style="font-weight: bold">Release </tspan></text>"#,
            r#"  <image x="111.68" y="32.95" width="12" height="12" href="SQ12" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>badge</title>",
        ],
        84.80000000000001,
        103.68,
    );
}

#[test]
fn blockquote_case_transform_keeps_the_authored_image_size() {
    let style = Style {
        blockquote_font_case: "upper".into(),
        ..Style::default()
    };
    assert_inline(
        "> see ![logo](SQ40){width=60 height=30} here",
        style,
        400.0,
        &[
            r##"  <rect x="20" y="20" width="3" height="35.41" fill="#d1d5db"/>"##,
            r#"  <text x="36" y="50" font-size="14" class="blockquote" text-anchor="start">SEE </text>"#,
            r#"  <image x="65.75" y="20" width="60" height="30" href="SQ40" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>LOGO</title>",
            r#"  <text x="129.69" y="50" font-size="14" class="blockquote" text-anchor="start">HERE</text>"#,
        ],
        75.4072265625,
        145.94,
    );
}

#[test]
fn centered_image_line_paints_start_anchored_segments_from_its_left_edge() {
    let style = Style {
        text_align: TextAlign::Center,
        ..Style::default()
    };
    assert_inline(
        "Before ![logo](SQ40) after `code` end",
        style,
        400.0,
        &[
            r##"  <rect x="231.13" y="48" width="36" height="17.5" fill="#f3f4f6" rx="3"/>"##,
            r#"  <text x="106.93" y="60" font-size="14" class="text" text-anchor="start">Before </text>"#,
            r#"  <image x="155.2" y="20" width="40" height="40" href="SQ40" preserveAspectRatio="xMidYMid meet"/>"#,
            "  <title>logo</title>",
            r#"  <text x="199.13" y="60" font-size="14" class="text" text-anchor="start">after <tspan style="font-family: ui-monospace, &#x27;SF Mono&#x27;, Menlo, Consolas, monospace; fill: #be185d; font-size: 12.5px">code</tspan> end</text>"#,
        ],
        85.4072265625,
        189.71,
    );
}

const SVG_120X40_URI: &str = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMjAiIGhlaWdodD0iNDAiLz4=";

/// A renderer built the way `callout.rs` builds its own: from bare font
/// files, so a requested weight is measured at the file's own weight.
fn from_font_path(style: Style) -> SVGRenderer {
    SVGRenderer::from_font_path(
        style,
        inter_path("InterVariable.ttf"),
        inter_path("SourceCodePro-Regular.ttf"),
    )
    .unwrap()
    .with_fetch_image_sizes(false)
}

#[test]
fn stacked_line_heights_sum_like_cpython() {
    let style = Style { base_font_size: 11.0, line_height: 1.35, ..Style::default() };
    let size = from_font_path(style)
        .measure(&parse(&["alpha"; 11].join(" ")), 60.0, 0.0)
        .unwrap();
    assert_eq!(size.height, 89.10000000000001);
}

#[test]
fn a_bare_font_path_measures_a_heading_at_the_files_own_weight() {
    let md = format!("# H ![x]({SVG_120X40_URI}){{height=40}} z");
    let from_path = from_font_path(Style::default()).render(&parse(&md), 400.0, 0.0).unwrap();
    assert!(from_path.contains(r#"<image x="28.68" y="0" width="120" height="40""#), "{from_path}");
    let reweighted = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false)
        .render(&parse(&md), 400.0, 0.0)
        .unwrap();
    assert!(!reweighted.contains(r#"<image x="28.68""#));
}

#[test]
fn first_baseline_is_the_first_line_s_own_ascent() {
    // `renderer.py::first_baseline`: a plain heading's baseline is its text
    // ascent; an inline image taller than the text grows the line, so the
    // baseline the title band aligns to is the image's height.
    let renderer = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false);
    let plain = parse("## Release");
    let multiplier = renderer.style.heading_line_height.unwrap_or(renderer.style.line_height);
    assert_eq!(
        renderer.first_baseline(&plain, 400.0).unwrap(),
        expected_baseline_offset(&renderer.measurer, renderer.style.get_heading_size(2), multiplier)
    );
    let grown = parse(&format!("## Release ![badge]({SQ40}) notes"));
    assert_eq!(renderer.first_baseline(&grown, 400.0).unwrap(), 40.0);
    let (svg, _) = unfetched(&format!("## Release ![badge]({SQ40}) notes"), 400.0, Style::default());
    assert!(image_lines(&svg)[0].contains(r#"y="20" width="40" height="40""#), "{svg}");
    assert_eq!(
        renderer.first_baseline(&parse("- item"), 400.0).unwrap_err(),
        "first_baseline needs a heading or paragraph, got BlockType.UNORDERED_LIST"
    );
}

#[test]
fn inline_image_beyond_the_ported_line_model_refuses_by_name() {
    // A table cell's lines and a packed paragraph whose lines differ in
    // height (`BlockMetrics` carries one advance) refuse.
    let table = "| a |\n| --- |\n| ![x](SQ12) |";
    assert_eq!(
        inline(table, Style::default(), 400.0).unwrap_err(),
        "mdsvg-rs: inline image in a table cell not supported"
    );
    let renderer = SVGRenderer::new(Style::default(), test_fonts())
        .unwrap()
        .with_fetch_image_sizes(false);
    let tall = parse(&format!("word word word word ![x]({SQ40}) word"));
    assert_eq!(
        renderer.measure_blocks(&tall, 120.0).unwrap_err(),
        "mdsvg-rs: inline image taller than its line in measure_blocks not supported"
    );
    // One image line alone is uniform...
    let single = parse(&format!("logo: ![x]({SQ40})"));
    let metrics = renderer.measure_blocks(&single, 120.0).unwrap();
    let window = renderer.render_block_window(&single[0], 120.0, 0, None).unwrap();
    assert_eq!((metrics[0].line_count, metrics[0].line_advance), (1, window.height));
    // ...and a short image never grows its line.
    let short = parse(&format!("word word word word ![x]({SQ12}) word"));
    let metrics = renderer.measure_blocks(&short, 120.0).unwrap();
    assert!(metrics[0].line_count > 1);
    assert_eq!(metrics[0].line_advance, 14.0 * Style::default().line_height);
}

#[test]
fn inline_image_fetch_and_fallback_refusals_match_the_block_image() {
    let fetched = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    assert_eq!(
        fetched.render(&parse("See ![x](u.png) here"), 400.0, 20.0).unwrap_err(),
        "mdsvg-rs: image size fetch not supported"
    );
    let style = Style {
        image_fallback_aspect_ratio: 0.0,
        ..Style::default()
    };
    // Height alone multiplies by the ratio; no size at all divides by it.
    for md in ["See ![x](u.png){height=10} here", "See ![x](u.png) here"] {
        assert_eq!(
            inline(md, style.clone(), 400.0).unwrap_err(),
            "mdsvg-rs: image non-positive fallback aspect ratio not supported"
        );
    }
}

#[test]
fn whitespace_tokens_split_on_every_python_space() {
    assert_eq!(
        split_whitespace_tokens("a\x1c\x1db\u{a0}c"),
        ["a", "\x1c\x1d", "b", "\u{a0}", "c"]
    );
}

fn pieces_of(run: TextRun) -> Vec<WrapPiece<usize>> {
    let renderer = SVGRenderer::new(Style::default(), test_fonts()).unwrap();
    renderer
        .run_pieces(&[run], 1000.0, 14.0, None, false)
        .unwrap()
}

// `renderer.py::_run_pieces`: `token.isspace()` takes `\x1c`-`\x1f` for
// whitespace, which `char::is_whitespace` does not.
#[test]
fn a_python_space_between_words_is_a_separator_not_a_word() {
    let pieces = pieces_of(TextRun::plain("a\x1cb"));
    let texts: Vec<&str> = pieces.iter().map(|p| p.text.as_str()).collect();
    assert_eq!(texts, ["a", "b"]);
    assert_eq!(pieces[1].separator, " ");
}

// `renderer.py::_run_pieces`: a code run is a chip only if `run.text.strip()`.
#[test]
fn an_inline_code_run_of_python_spaces_is_not_a_chip() {
    let run = TextRun {
        is_code: true,
        ..TextRun::plain("\x1c")
    };
    assert!(pieces_of(run).is_empty());
}

// `renderer.py::_numeric_weight`: the keyword lookup is `strip().lower()`, so
// `\x1c`-`\x1f` are stripped there; `float(value)` strips only the Unicode
// `White_Space` set, so a number wrapped in them is not a number.
#[test]
fn a_numeric_weight_is_read_with_its_surrounding_whitespace_stripped() {
    assert_eq!(numeric_weight(Some(" 600 ")), Some(600.0));
    assert_eq!(numeric_weight(Some("\u{a0}600\u{3000}")), Some(600.0));
    assert_eq!(numeric_weight(Some("\x1c600")), None);
    assert_eq!(numeric_weight(Some(" BOLD\x1f")), Some(700.0));
    assert_eq!(numeric_weight(Some("semibold")), None);
}

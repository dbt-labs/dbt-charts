//! Item (d) of R1: `--embed-fonts` embeds the fonts a rendered board actually
//! paints with as standalone `@font-face` rules (base64 `data:` URIs, no
//! external references) so the SVG carries its own type — a byte-parity
//! port of production's embed contract (HIGH 3):
//! `font_selection.py::board_font_face_css(embed=True)` ->
//! `board_font_faces()` + `fonts.py::render_embedded_font_face_css()`.
//! REPLACES the URL `@font-face` roster the composed SVG already carries
//! (`BoardPayload.font_face_css`), never additive to it — production's own
//! `board_font_face_css` returns ONE OR THE OTHER, gated on `embed`, never
//! both.
//!
//! `FONT_REGISTRY`'s own data (family/style/web_file/weight_range/
//! unicode_range) is forwarded from `fonts.py::FONT_REGISTRY` by
//! `build_board_payload` (Core C1; `board_sweep.py::_board_payload` stands in
//! for it today) rather than duplicated as Rust literals — the emoji face's
//! own `unicode_range` is DERIVED at Python import time from
//! `EMOJI_CODEPOINTS`, so a hand-copied Rust literal would silently drift
//! the moment that codepoint set changes.

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use regex::Regex;
use serde::Deserialize;
use std::cell::RefCell;
use std::collections::HashSet;
use std::sync::LazyLock;

/// `fonts.py::VendoredFace`, narrowed to the fields `board_font_faces`/
/// `render_embedded_font_face_css` read — `tabular` is measurement-only
/// and irrelevant to font-face declarations.
#[derive(Deserialize, Debug, Clone)]
pub struct FontRegistryEntry {
    pub family: String,
    pub style: String,
    pub web_file: Option<String>,
    pub weight_range: Option<(u32, u32)>,
    #[serde(default)]
    pub unicode_range: Vec<String>,
}

/// `fonts.py::registry_family` — the registry family name a CSS font stack
/// resolves to; any `Source Serif` spelling names the one vendored cut.
fn registry_family(font_family: &str) -> String {
    let primary = font_family
        .split(',')
        .next()
        .unwrap_or(font_family)
        .trim()
        .trim_matches(['\'', '"']);
    if primary.starts_with("Source Serif") {
        "Source Serif 4".to_string()
    } else {
        primary.to_string()
    }
}

/// `font_selection.py::collect_painted_italic_families`' sink: the registry
/// families whose italic an mdsvg render consumed. Interior-mutable and carried
/// by the payload for the same reason the render report is — every paint site
/// already holds the payload, so it reaches each nesting level without another
/// parameter on the layout arms.
pub type PaintedItalics = RefCell<HashSet<String>>;

/// The sink-recording function at `font_selection.py:88-104`: record one mdsvg
/// render's consumed fonts against the family it was laid out in. Only italic
/// is kept — the roman row already covers regular and bold (one variable file
/// at two weights), and the mono family is named by the CSS class rule mdsvg
/// emits for code. `sink` is `None` for a render that only measures (sizing),
/// which paints nothing and so must not credit a family; `used` is
/// `SVGRenderer::used_fonts`.
pub fn record_painted_italics(
    sink: Option<&PaintedItalics>,
    font_family: &str,
    used: &HashSet<&'static str>,
) {
    let Some(sink) = sink else { return };
    if used.contains("italic") || used.contains("bold_italic") {
        sink.borrow_mut().insert(registry_family(font_family));
    }
}

// `font_selection.py`'s five scope-scanning regexes, verbatim.
const DECLARES_ITALIC: &str = r#"font-style\s*[:=]\s*["']?\s*italic"#;
static ITALIC_TAG: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(&format!(r"<[^<>]*{DECLARES_ITALIC}[^<>]*>")).unwrap());
static ITALIC_CLASS_RULE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r"\.([\w-]+)[^{{}}]*\{{[^{{}}]*{DECLARES_ITALIC}[^{{}}]*\}}"
    ))
    .unwrap()
});
static CLASS_ATTR: LazyLock<Regex> = LazyLock::new(|| Regex::new(r#"class="([^"]*)""#).unwrap());
static STYLE_BLOCK: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)<style[^>]*>(.*?)</style>").unwrap());
/// `font_selection.py:140-143` (`_DECLARES_FAMILY`): an unquoted value reads
/// a character reference (`&#x27;`) as one unit, so its own `;` does not end
/// the declaration.
static DECLARES_FAMILY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r#"font-family\s*[:=]\s*("[^"]*"|'[^']*'|(?:&#?\w+;|[^;}])+)"#).unwrap()
});

/// Undo the references `svg::xml_escape_attr` and `xml::Xml` write in a
/// font-family stack (production's broader `html.unescape`).
pub(crate) fn xml_unescape_attr(s: &str) -> String {
    s.replace("&#x27;", "'")
        .replace("&apos;", "'")
        .replace("&quot;", "\"")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#10;", "\n")
        .replace("&#13;", "\r")
        .replace("&#9;", "\t")
        .replace("&amp;", "&")
}

/// `font_selection.py::_declared_family` — the family named in a scope
/// already known to declare italic.
fn declared_family(scope: &str) -> Option<String> {
    let family = DECLARES_FAMILY.captures(scope)?;
    Some(registry_family(&xml_unescape_attr(family.get(1)?.as_str())))
}

/// `font_selection.py::italic_families_in` — families whose italic is
/// declared next to them, scope by scope: a start tag carrying both
/// `font-style: italic` and a `font-family`, or a CSS class rule that
/// declares italic AND is worn by some `class="..."` attribute in the
/// document. This is the "Declared italic" half of production's own routes to
/// italic (`font_selection.py`'s module doc); the "Consumed italic" half
/// (markdown emphasis painted as an inline `<tspan>` with no co-located family)
/// arrives as `PaintedItalics` and is unioned in by `embed_used_fonts`.
fn italic_families_in(painted_markup: &str) -> HashSet<String> {
    if !painted_markup.contains("italic") {
        return HashSet::new();
    }
    let mut families: HashSet<String> = HashSet::new();
    for tag in ITALIC_TAG.find_iter(painted_markup) {
        if let Some(f) = declared_family(tag.as_str()) {
            families.insert(f);
        }
    }
    let stylesheets = STYLE_BLOCK
        .captures_iter(painted_markup)
        .map(|c| c.get(1).map(|m| m.as_str()).unwrap_or(""))
        .collect::<Vec<_>>()
        .join("\n");
    let rules: Vec<_> = ITALIC_CLASS_RULE.captures_iter(&stylesheets).collect();
    if !rules.is_empty() {
        let worn: HashSet<&str> = CLASS_ATTR
            .captures_iter(painted_markup)
            .flat_map(|c| c.get(1).map(|m| m.as_str()).unwrap_or("").split_whitespace())
            .collect();
        for rule in rules {
            let class_name = rule.get(1).expect("group 1 is the rule's own class capture").as_str();
            if worn.contains(class_name) {
                if let Some(f) = declared_family(rule.get(0).expect("match 0 always exists").as_str()) {
                    families.insert(f);
                }
            }
        }
    }
    families
}

/// `font_selection.py::board_font_faces` — the served faces `painted_markup`
/// needs, in registry order: a face joins the set when its family is named
/// in the markup, and an italic row additionally needs that family to have
/// gone italic somewhere.
fn board_font_faces<'a>(
    registry: &'a [FontRegistryEntry],
    painted_markup: &str,
    italic: &HashSet<String>,
) -> Vec<&'a FontRegistryEntry> {
    registry
        .iter()
        .filter(|f| f.web_file.is_some())
        .filter(|f| painted_markup.contains(f.family.as_str()))
        .filter(|f| f.style != "italic" || italic.contains(&f.family))
        .collect()
}

/// `fonts.py::_embedded_src` — the `src` value for a face carried inline: a
/// `data:` URI of its own web bytes, read directly from `fonts_dir` by the
/// registry's own filename (never through `FontBook`, which loads only
/// `.ttf` measure files, not the `.woff2` web files this embeds).
fn embedded_src(fonts_dir: &str, web_file: &str) -> Result<String, String> {
    let bytes = std::fs::read(std::path::Path::new(fonts_dir).join(web_file))
        .map_err(|e| format!("embed-fonts: reading {fonts_dir}/{web_file}: {e}"))?;
    let mime = if web_file.ends_with(".woff2") {
        "font/woff2"
    } else {
        "font/ttf"
    };
    Ok(format!(
        "url('data:{mime};base64,{}')",
        BASE64.encode(bytes)
    ))
}

/// `fonts.py::_css_format` — container, plus `-variations` for a variable
/// face (`weight_range.is_some()`).
fn css_format(face: &FontRegistryEntry, web_file: &str) -> &'static str {
    let woff2 = web_file.ends_with(".woff2");
    match (woff2, face.weight_range.is_some()) {
        (true, true) => "woff2-variations",
        (true, false) => "woff2",
        (false, true) => "truetype-variations",
        (false, false) => "truetype",
    }
}

/// `fonts.py::render_embedded_font_face_css` (via its own shared
/// `_font_face_css`) — one `@font-face` block per face, in the SAME order
/// `board_font_faces` returns them: the `src`, the weight range, the style,
/// `font-display: swap`, and the `unicode-range` list when the face
/// declares one.
fn render_embedded_font_face_css(
    fonts_dir: &str,
    faces: &[&FontRegistryEntry],
) -> Result<String, String> {
    let mut blocks = Vec::with_capacity(faces.len());
    for face in faces {
        // `board_font_faces` already filtered to `web_file.is_some()`.
        let web_file = face
            .web_file
            .as_deref()
            .expect("board_font_faces only returns served faces");
        let mut lines = vec![
            "@font-face {".to_string(),
            format!("  font-family: '{}';", face.family),
            format!(
                "  src: {} format('{}');",
                embedded_src(fonts_dir, web_file)?,
                css_format(face, web_file)
            ),
        ];
        if let Some((lo, hi)) = face.weight_range {
            lines.push(format!("  font-weight: {lo} {hi};"));
        }
        if face.style != "normal" {
            lines.push(format!("  font-style: {};", face.style));
        } else if face.weight_range.is_some() {
            lines.push("  font-style: normal;".to_string());
        }
        lines.push("  font-display: swap;".to_string());
        if !face.unicode_range.is_empty() {
            lines.push(format!(
                "  unicode-range:\n    {};",
                face.unicode_range.join(",\n    ")
            ));
        }
        lines.push("}".to_string());
        blocks.push(lines.join("\n"));
    }
    Ok(blocks.join("\n") + "\n")
}

/// `--embed-fonts`: swap the composed board SVG's own URL-mode
/// `@font-face` roster (`url_roster`, the exact `defs_styles_block` slice
/// `board_sweep.py`'s `font_face_css` also computed it from) for a
/// standalone `@font-face` block naming only the families this board
/// actually painted with, as base64 `data:` URIs — production's own
/// `board_font_face_css(embed=True)` REPLACES the URL roster, it never
/// grows the block additively.
///
/// `painted_markup` (`board::render_board`'s second return value) is the
/// face-SELECTION scan input — content only, never `svg` itself: `svg`
/// still carries the URL roster `painted_markup` deliberately excludes
/// (`boards.py:1265`'s own `content_items` scope), and scanning it would
/// falsely credit every family the roster merely OFFERS as "painted."
///
/// `painted_italics` is what the paint sites recorded through
/// `record_painted_italics`; a family whose italic was only consumed, never
/// declared beside its family in the markup, gets its italic font from there.
pub fn embed_used_fonts(
    svg: String,
    font_registry: &[FontRegistryEntry],
    url_roster: &str,
    painted_markup: &str,
    painted_italics: &PaintedItalics,
    fonts_dir: Option<&str>,
) -> Result<String, String> {
    let Some(fonts_dir) = fonts_dir else {
        return Err("embed-fonts: no fonts_dir to embed font bytes from".to_string());
    };
    if url_roster.is_empty() {
        return Err(
            "embed-fonts: the board's own URL font-face roster is empty; nothing to replace"
                .to_string(),
        );
    }
    let occurrences = svg.matches(url_roster).count();
    if occurrences != 1 {
        return Err(format!(
            "embed-fonts: expected exactly one occurrence of the URL font-face roster \
             in the composed svg, found {occurrences}"
        ));
    }
    let mut italic = italic_families_in(painted_markup);
    italic.extend(painted_italics.borrow().iter().cloned());
    let faces = board_font_faces(font_registry, painted_markup, &italic);
    let css = render_embedded_font_face_css(fonts_dir, &faces)?;
    Ok(svg.replacen(url_roster, &css, 1))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn registry() -> Vec<FontRegistryEntry> {
        vec![
            FontRegistryEntry {
                family: "Inter Variable".to_string(),
                style: "normal".to_string(),
                web_file: Some("InterVariable.woff2".to_string()),
                weight_range: Some((100, 900)),
                unicode_range: vec![],
            },
            FontRegistryEntry {
                family: "Inter Variable".to_string(),
                style: "italic".to_string(),
                web_file: Some("InterVariable-Italic.woff2".to_string()),
                weight_range: Some((100, 900)),
                unicode_range: vec![],
            },
            FontRegistryEntry {
                family: "Noto Emoji".to_string(),
                style: "normal".to_string(),
                web_file: Some("NotoEmoji-Regular.woff2".to_string()),
                weight_range: None,
                unicode_range: vec!["U+0023".to_string(), "U+002A".to_string()],
            },
            FontRegistryEntry {
                family: "dbt Serif Medium".to_string(),
                style: "normal".to_string(),
                web_file: None,
                weight_range: None,
                unicode_range: vec![],
            },
        ]
    }

    #[test]
    fn board_font_faces_excludes_measure_only_and_unpainted_families() {
        let reg = registry();
        let faces = board_font_faces(&reg, "font-family=\"Inter Variable\"", &HashSet::new());
        assert_eq!(faces.len(), 1);
        assert_eq!(faces[0].family, "Inter Variable");
        assert_eq!(faces[0].style, "normal");
    }

    #[test]
    fn board_font_faces_admits_italic_only_when_the_family_went_italic() {
        let reg = registry();
        let markup = "font-family=\"Inter Variable\"";
        let none = board_font_faces(&reg, markup, &HashSet::new());
        assert_eq!(none.len(), 1, "italic row excluded with no italic family");

        let mut italic = HashSet::new();
        italic.insert("Inter Variable".to_string());
        let both = board_font_faces(&reg, markup, &italic);
        assert_eq!(both.len(), 2, "both faces admitted once italic is recorded");
    }

    #[test]
    fn italic_families_in_pairs_a_declared_italic_tag_with_its_family() {
        let markup = r#"<text font-family="Source Serif 4" font-style="italic">hi</text>"#;
        let families = italic_families_in(markup);
        assert!(families.contains("Source Serif 4"), "{families:?}");
    }

    #[test]
    fn italic_families_in_only_admits_a_worn_css_class_rule() {
        let unworn = r#"<style>.md-em{font-family:'Inter Variable';font-style:italic}</style><text class="other">x</text>"#;
        assert!(italic_families_in(unworn).is_empty());

        let worn = r#"<style>.md-em{font-family:'Inter Variable';font-style:italic}</style><tspan class="md-em">x</tspan>"#;
        let families = italic_families_in(worn);
        assert!(families.contains("Inter Variable"), "{families:?}");
    }

    #[test]
    fn italic_families_in_reads_an_entity_escaped_family_in_a_css_rule() {
        let worn = r#"<style>.md-bq{font-family: &#x27;Inter Variable&#x27;, sans-serif; font-style: italic}</style><text class="md-bq">x</text>"#;
        let families = italic_families_in(worn);
        assert!(families.contains("Inter Variable"), "{families:?}");
    }

    #[test]
    fn italic_families_in_reads_a_family_escaped_with_named_apostrophes() {
        let tag = r#"<text font-family="&apos;Inter Variable&apos;, sans-serif" font-style="italic">x</text>"#;
        let families = italic_families_in(tag);
        assert!(families.contains("Inter Variable"), "{families:?}");
    }

    fn used(labels: &[&'static str]) -> HashSet<&'static str> {
        labels.iter().copied().collect()
    }

    #[test]
    fn record_painted_italics_keeps_only_italic_against_the_stack_primary() {
        let sink = PaintedItalics::default();
        let stack = "'Source Serif 4', 'Noto Emoji', Georgia, serif";
        record_painted_italics(Some(&sink), stack, &used(&["regular", "bold", "mono"]));
        assert!(
            sink.borrow().is_empty(),
            "roman, bold and mono are never recorded"
        );

        record_painted_italics(Some(&sink), stack, &used(&["regular", "italic"]));
        record_painted_italics(
            Some(&sink),
            "Inter Variable, sans-serif",
            &used(&["bold_italic"]),
        );
        assert_eq!(
            *sink.borrow(),
            HashSet::from(["Source Serif 4".to_string(), "Inter Variable".to_string()])
        );
    }

    #[test]
    fn record_painted_italics_without_a_sink_is_a_measure_only_render() {
        record_painted_italics(None, "Inter Variable", &used(&["italic"]));
    }

    #[test]
    fn render_embedded_font_face_css_declares_weight_range_and_display_swap() {
        let entry = FontRegistryEntry {
            family: "Inter Variable".to_string(),
            style: "normal".to_string(),
            web_file: Some("InterVariable.ttf".to_string()),
            weight_range: Some((100, 900)),
            unicode_range: vec![],
        };
        let tmp = std::env::temp_dir().join(format!(
            "chart-renderer-embed-fonts-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        std::fs::write(tmp.join("InterVariable.ttf"), b"not a real font, just bytes").unwrap();
        let css = render_embedded_font_face_css(&tmp.to_string_lossy(), &[&entry]).unwrap();
        assert!(css.contains("font-weight: 100 900;"));
        assert!(css.contains("font-display: swap;"));
        assert!(css.contains("format('truetype-variations')"));
        assert!(!css.contains("/static/fonts"));
        std::fs::remove_dir_all(&tmp).ok();
    }

    #[test]
    fn embed_used_fonts_replaces_not_appends_the_url_roster() {
        let reg = vec![FontRegistryEntry {
            family: "Inter Variable".to_string(),
            style: "normal".to_string(),
            web_file: Some("InterVariable.ttf".to_string()),
            weight_range: None,
            unicode_range: vec![],
        }];
        let tmp = std::env::temp_dir().join(format!(
            "chart-renderer-embed-fonts-test-replace-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        std::fs::write(tmp.join("InterVariable.ttf"), b"bytes").unwrap();
        let url_roster = "@font-face { font-family: 'Inter Variable'; src: url('/static/fonts/InterVariable.woff2'); }\n";
        let painted_markup = "<text font-family=\"Inter Variable\">x</text>";
        let svg = format!(
            "<svg><defs><style>{url_roster}</style></defs>{painted_markup}</svg>"
        );
        let out = embed_used_fonts(
            svg,
            &reg,
            url_roster,
            painted_markup,
            &PaintedItalics::default(),
            Some(&tmp.to_string_lossy()),
        )
        .unwrap();
        assert!(
            !out.contains("/static/fonts"),
            "URL roster must be replaced, not kept: {out}"
        );
        assert!(out.contains("data:font/ttf;base64,"));
        std::fs::remove_dir_all(&tmp).ok();
    }

    /// MEDIUM (a), R2 review round: face selection must scan `painted_markup`
    /// (production's `content_items` join), never `svg` — the roster names
    /// every REGISTERED family regardless of what actually painted, so
    /// scanning `svg` credits a family that never appears in the board's own
    /// content as "used," embedding a face nothing needs.
    #[test]
    fn embed_used_fonts_ignores_a_family_only_present_in_the_url_roster() {
        let reg = vec![
            FontRegistryEntry {
                family: "Inter Variable".to_string(),
                style: "normal".to_string(),
                web_file: Some("InterVariable.ttf".to_string()),
                weight_range: None,
                unicode_range: vec![],
            },
            FontRegistryEntry {
                family: "Source Code Pro".to_string(),
                style: "normal".to_string(),
                web_file: Some("SourceCodePro-Regular.ttf".to_string()),
                weight_range: None,
                unicode_range: vec![],
            },
        ];
        let tmp = std::env::temp_dir().join(format!(
            "chart-renderer-embed-fonts-test-scope-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        std::fs::write(tmp.join("InterVariable.ttf"), b"bytes").unwrap();
        std::fs::write(tmp.join("SourceCodePro-Regular.ttf"), b"bytes").unwrap();
        // The roster names BOTH families (every registered face is always
        // offered, unconditionally) — only "Inter Variable" is actually
        // painted.
        let url_roster = "@font-face { font-family: 'Inter Variable'; src: url('/static/fonts/InterVariable.woff2'); }\n\
                           @font-face { font-family: 'Source Code Pro'; src: url('/static/fonts/SourceCodePro-Regular.woff2'); }\n";
        let painted_markup = "<text font-family=\"Inter Variable\">x</text>";
        let svg = format!("<svg><defs><style>{url_roster}</style></defs>{painted_markup}</svg>");
        let out = embed_used_fonts(
            svg,
            &reg,
            url_roster,
            painted_markup,
            &PaintedItalics::default(),
            Some(&tmp.to_string_lossy()),
        )
        .unwrap();
        assert!(out.contains("Inter Variable"), "{out}");
        assert!(!out.contains("Source Code Pro"), "{out}");
    }

    #[test]
    fn embed_used_fonts_refuses_without_a_fonts_dir() {
        let err = embed_used_fonts(
            "<svg></svg>".to_string(),
            &[],
            "x",
            "",
            &PaintedItalics::default(),
            None,
        )
        .unwrap_err();
        assert!(err.contains("fonts_dir"), "{err}");
    }
}

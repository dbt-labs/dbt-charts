//! HIGH 4: pre-rasterization font normalization for `png`/`pdf` — a
//! byte-parity port of `render/font_support.py`'s
//! `normalize_svg_font_families_for_vl_convert`/
//! `normalize_svg_font_weights_for_vl_convert`. resvg (used both by
//! production's vl-convert and this crate's own `run_png`/`run_pdf`)
//! cannot bind a variable font's `wght` axis from a numeric `font-weight`
//! attribute — every weight below its synthetic-bold threshold (~600)
//! collapses to Regular, and even above that threshold it applies a fixed
//! embolden pass rather than the font's real SemiBold cut. The browser has
//! no such limitation, so only the resvg-bound copy needs this rewrite;
//! the composed SVG itself (`board.rs`'s own output, and every board a
//! browser renders) is untouched.

use crate::embed_fonts::xml_unescape_attr;
use crate::svg::xml_escape_attr;
use regex::{Captures, Regex};
use std::collections::HashMap;
use std::sync::LazyLock;

const INTER_FAMILY: &str = "Inter";
const INTER_VARIABLE_FAMILY: &str = "Inter Variable";

static FONT_FAMILY_ATTR_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(r#"(font-family\s*=\s*["']){INTER_FAMILY}(["'])"#)).unwrap()
});
static FONT_FAMILY_STYLE_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r#"(font-family\s*:\s*["']?){INTER_FAMILY}(["']?\s*[;"])"#
    ))
    .unwrap()
});
static CSS_VAR_STYLE_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r#"(--dbt-font-family\s*:\s*["']?){INTER_FAMILY}(["']?\s*;)"#
    ))
    .unwrap()
});

/// `fonts.py::WEIGHT_FACE_ALIASES` — a static engine constant (unlike the
/// embed-fonts registry's own DERIVED emoji unicode-range, this table is
/// hand-authored and stable), mirrored as a literal the same way other
/// small engine constants already are in this crate (`spark_bar.rs`'s
/// own title-height estimates). `(base_family, weight,
/// target_family)`, in the SAME base-family-major order production's dict
/// iterates.
const WEIGHT_FACE_ALIASES: &[(&str, &str, &str)] = &[
    ("Source Serif 4", "500", "dbt Serif Medium"),
    ("Source Serif 4", "600", "dbt Serif SemiBold"),
    ("dbt Sans Tabular", "500", "dbt Sans Tabular Medium"),
    ("dbt Sans Tabular", "600", "dbt Sans Tabular SemiBold"),
    ("Inter Variable", "500", "Inter Variable Medium"),
    ("Inter Variable", "600", "Inter Variable SemiBold"),
];

/// `{w for by_weight in WEIGHT_FACE_ALIASES.values() for w in by_weight}`.
const COVERED_WEIGHTS: &[&str] = &["500", "600"];

static TAG_PATTERN: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<[a-zA-Z][^>]*>").unwrap());
static FONT_FAMILY_VALUE_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"(font-family\s*=\s*")([^"]*)(")"#).unwrap());
static FONT_WEIGHT_ATTR: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"font-weight\s*=\s*"(\d+)""#).unwrap());
/// `font_support.py:76` (`_COVERED_WEIGHT_PATTERN`) — the cheap gate: no covered
/// weight spelled as an attribute or as inline style means nothing to rewrite.
static COVERED_WEIGHT_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(&format!(
        r#"font-weight\s*[=:]\s*"?(?:{})\b"#,
        COVERED_WEIGHTS.join("|")
    ))
    .unwrap()
});
/// `font_support.py:79-85` — the inline-weight rewrite's patterns.
static STYLE_FONT_WEIGHT_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"font-weight\s*:\s*(\d+)").unwrap());
static CLASS_FAMILY_RULE_PATTERN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\.([\w-]+)\s*\{[^}]*?font-family\s*:\s*((?:&#?\w+;|[^;}])+)").unwrap()
});
static CLASS_ATTR_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"class\s*=\s*"([^"]*)""#).unwrap());
static TEXT_ELEMENT_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?s)(<text\b[^>]*>)(.*?)(</text>)").unwrap());
static TSPAN_OPEN_PATTERN: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"<tspan\b[^>]*>").unwrap());

/// `font_support.py:88` (`_weight_aliased_family`) — `family` with its base
/// family swapped for the static face registered at `weight`, if there is one.
fn weight_aliased_family(family: &str, weight: &str) -> Option<String> {
    WEIGHT_FACE_ALIASES
        .iter()
        .find(|(base_family, aliased_weight, _)| {
            *aliased_weight == weight && family.contains(base_family)
        })
        .map(|(base_family, _, target_family)| family.replace(base_family, target_family))
}

/// `font_support.py:96` (`_rewrite_weight_face_in_tag`) — scoped per
/// start-tag (not a blanket string substitution) because the rewrite is
/// conditional on a second, co-occurring `font-weight` attribute.
fn rewrite_weight_face_in_tag(tag: &str) -> String {
    let Some(weight_match) = FONT_WEIGHT_ATTR.captures(tag) else {
        return tag.to_string();
    };
    FONT_FAMILY_VALUE_PATTERN
        .replace_all(tag, |caps: &Captures| {
            match weight_aliased_family(&caps[2], &weight_match[1]) {
                Some(aliased) => format!("{}{}{}", &caps[1], aliased, &caps[3]),
                None => caps[0].to_string(),
            }
        })
        .into_owned()
}

/// `font_support.py:110` (`_rewrite_inline_weight_tspans`) — mdsvg paints bold
/// as `<tspan style="font-weight: W">` and takes the family from a `<style>`
/// class rule on the enclosing `<text>`, so the weight and the family never
/// share a tag. The text element's family (its attribute, else the first of its
/// classes with a rule) is aliased for the tspan's weight and written onto the
/// tspan as an attribute.
fn rewrite_inline_weight_tspans(svg: &str) -> String {
    let class_families: HashMap<&str, &str> = CLASS_FAMILY_RULE_PATTERN
        .captures_iter(svg)
        .map(|rule| (rule.get(1).unwrap().as_str(), rule.get(2).unwrap().as_str().trim()))
        .collect();

    let text_family = |open_tag: &'_ str| -> Option<String> {
        if let Some(attribute) = FONT_FAMILY_VALUE_PATTERN.captures(open_tag) {
            return Some(attribute[2].to_string());
        }
        let class_attr = CLASS_ATTR_PATTERN.captures(open_tag)?;
        class_attr[1]
            .split_whitespace()
            .find_map(|class| class_families.get(class))
            .map(|family| family.to_string())
    };

    TEXT_ELEMENT_PATTERN
        .replace_all(svg, |text: &Captures| {
            let Some(family) = text_family(&text[1]) else {
                return text[0].to_string();
            };
            let body = TSPAN_OPEN_PATTERN.replace_all(&text[2], |tspan: &Captures| {
                let tag = &tspan[0];
                let Some(weight_match) = STYLE_FONT_WEIGHT_PATTERN.captures(tag) else {
                    return tag.to_string();
                };
                match weight_aliased_family(&family, &weight_match[1]) {
                    Some(aliased) => format!(
                        r#"<tspan font-family="{}"{}"#,
                        xml_escape_attr(&xml_unescape_attr(&aliased)),
                        &tag["<tspan".len()..]
                    ),
                    None => tag.to_string(),
                }
            });
            format!("{}{body}{}", &text[1], &text[3])
        })
        .into_owned()
}

/// `font_support.py:155` (`normalize_svg_font_weights_for_vl_convert`) —
/// rewrite a vendored family + cascaded weight to its dedicated static face.
fn normalize_svg_font_weights(svg: &str) -> String {
    if !COVERED_WEIGHT_PATTERN.is_match(svg) {
        return svg.to_string();
    }
    TAG_PATTERN
        .replace_all(&rewrite_inline_weight_tspans(svg), |caps: &Captures| {
            rewrite_weight_face_in_tag(&caps[0])
        })
        .into_owned()
}

/// `font_support.py::normalize_svg_font_families_for_vl_convert` — map the
/// public `Inter` family name to its internal vendored name (`Inter
/// Variable`), which resvg rasterizes reliably; `dbt Sans Tabular`, the dbt
/// serif oldstyle faces, and `Source Serif 4` need no alias rewrite, their
/// public names already match the vendored files' internal ones. The
/// weight rewrite applies regardless of which family is present.
pub fn normalize_svg_font_families_for_rasterization(svg: &str) -> String {
    let mut normalized = svg.to_string();
    if svg.contains(INTER_FAMILY) {
        let sub = format!("${{1}}{INTER_VARIABLE_FAMILY}${{2}}");
        normalized = FONT_FAMILY_ATTR_PATTERN.replace_all(&normalized, sub.as_str()).into_owned();
        normalized = FONT_FAMILY_STYLE_PATTERN.replace_all(&normalized, sub.as_str()).into_owned();
        normalized = CSS_VAR_STYLE_PATTERN.replace_all(&normalized, sub.as_str()).into_owned();
    }
    normalize_svg_font_weights(&normalized)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn public_inter_attr_rewrites_to_the_internal_variable_family() {
        let svg = r#"<text font-family="Inter">a</text>"#;
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert_eq!(out, r#"<text font-family="Inter Variable">a</text>"#);
    }

    #[test]
    fn inter_variable_is_untouched_the_word_inter_is_a_prefix_not_a_match() {
        let svg = r#"<text font-family="Inter Variable">a</text>"#;
        assert_eq!(normalize_svg_font_families_for_rasterization(svg), svg);
    }

    #[test]
    fn css_style_and_custom_property_forms_also_rewrite() {
        let svg = r#"<text style="font-family: Inter;">a</text><g style="--dbt-font-family: Inter;">"#;
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert!(out.contains("font-family: Inter Variable;"));
        assert!(out.contains("--dbt-font-family: Inter Variable;"));
    }

    /// HIGH 4's own demonstrated bug: a `font-weight="600"` run on `Inter
    /// Variable` must instance `Inter Variable SemiBold`, or resvg
    /// collapses it to Regular.
    #[test]
    fn a_600_weight_inter_variable_run_instances_the_semibold_face() {
        let svg = r#"<text font-family="Inter Variable" font-weight="600">brand</text>"#;
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert!(
            out.contains(r#"font-family="Inter Variable SemiBold""#),
            "{out}"
        );
    }

    #[test]
    fn a_500_weight_dbt_sans_tabular_run_instances_the_medium_face() {
        let svg = r#"<text font-family="dbt Sans Tabular" font-weight="500">1,234</text>"#;
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert!(
            out.contains(r#"font-family="dbt Sans Tabular Medium""#),
            "{out}"
        );
    }

    #[test]
    fn source_serif_4_600_weight_instances_the_semibold_face() {
        let svg = r#"<text font-family="Source Serif 4" font-weight="600">Title</text>"#;
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert!(
            out.contains(r#"font-family="dbt Serif SemiBold""#),
            "{out}"
        );
    }

    #[test]
    fn an_uncovered_weight_passes_through_unchanged() {
        let svg = r#"<text font-family="Inter Variable" font-weight="400">body</text>"#;
        assert_eq!(normalize_svg_font_families_for_rasterization(svg), svg);
    }

    /// `font_support.py::_rewrite_inline_weight_tspans`: mdsvg paints bold as
    /// `<tspan style="font-weight: W">` and the family lives in a `<style>`
    /// class rule on the enclosing `<text>`; the tspan gains the static face.
    #[test]
    fn an_inline_weight_tspan_takes_the_static_face_of_its_texts_class_family() {
        let svg = concat!(
            "<style>.c { font-family: &#x27;Source Serif 4&#x27;, serif; fill: #222; }</style>",
            r#"<text class="a c"><tspan style="font-weight: 500">T</tspan> x "#,
            r#"<tspan style="font-weight: 400">u</tspan></text>"#,
        );
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert_eq!(
            out,
            concat!(
                "<style>.c { font-family: &#x27;Source Serif 4&#x27;, serif; fill: #222; }</style>",
                r#"<text class="a c"><tspan font-family="&#x27;dbt Serif Medium&#x27;, serif" "#,
                r#"style="font-weight: 500">T</tspan> x "#,
                r#"<tspan style="font-weight: 400">u</tspan></text>"#,
            )
        );
    }

    #[test]
    fn the_text_elements_family_attribute_is_read_before_its_class_rule() {
        let svg = concat!(
            "<style>.c { font-family: Georgia; }</style>",
            r#"<text class="c" font-family="Inter Variable">"#,
            r#"<tspan style="font-weight: 600">T</tspan></text>"#,
        );
        let out = normalize_svg_font_families_for_rasterization(svg);
        assert!(
            out.contains(
                r#"<tspan font-family="Inter Variable SemiBold" style="font-weight: 600">"#
            ),
            "{out}"
        );
    }

    #[test]
    fn each_text_element_aliases_its_own_tspans_by_its_own_family() {
        let svg = concat!(
            r#"<text font-family="Inter Variable"><tspan style="font-weight:500">a</tspan>"#,
            r#"<tspan style="font-weight: 600">b</tspan></text>"#,
            r#"<text font-family="Georgia"><tspan style="font-weight: 600">c</tspan></text>"#,
        );
        assert_eq!(
            normalize_svg_font_families_for_rasterization(svg),
            concat!(
                r#"<text font-family="Inter Variable">"#,
                r#"<tspan font-family="Inter Variable Medium" style="font-weight:500">a</tspan>"#,
                r#"<tspan font-family="Inter Variable SemiBold" style="font-weight: 600">b</tspan>"#,
                r#"</text>"#,
                r#"<text font-family="Georgia"><tspan style="font-weight: 600">c</tspan></text>"#,
            )
        );
    }

    #[test]
    fn an_inline_weight_with_no_alias_or_no_text_family_is_left_alone() {
        for svg in [
            r#"<text font-family="Inter Variable"><tspan style="font-weight: 700">T</tspan></text>"#,
            r#"<text font-family="Georgia"><tspan style="font-weight: 600">T</tspan></text>"#,
            r#"<text><tspan style="font-weight: 600">T</tspan></text>"#,
        ] {
            assert_eq!(normalize_svg_font_families_for_rasterization(svg), svg);
        }
    }

    /// Production prepends its attribute without looking for one already on the
    /// tspan, so a tspan that carries a family gets a second `font-family`.
    #[test]
    fn a_tspan_already_carrying_a_family_gains_a_second_one_as_production_does() {
        let svg = concat!(
            r#"<text font-family="Inter Variable">"#,
            r#"<tspan font-family="Georgia" style="font-weight: 600">T</tspan></text>"#,
        );
        assert_eq!(
            normalize_svg_font_families_for_rasterization(svg),
            concat!(
                r#"<text font-family="Inter Variable"><tspan font-family="Inter Variable SemiBold" "#,
                r#"font-family="Georgia" style="font-weight: 600">T</tspan></text>"#,
            )
        );
    }

    #[test]
    fn a_covered_weight_on_an_unregistered_family_passes_through_unchanged() {
        let svg = r#"<text font-family="Georgia" font-weight="600">body</text>"#;
        assert_eq!(normalize_svg_font_families_for_rasterization(svg), svg);
    }
}

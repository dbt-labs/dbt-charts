//! The last step of a board's markup (`boards.py::_dedupe_repeated_style_rules`): collapse
//! byte-identical markdown CSS rule lines repeated across the `<style>` blocks of one `<svg>`.
//!
//! A markdown renderer scopes every rule as `.md-<hash>-<name>`, the hash a function of its own
//! style, so two renderers of the same style print the same lines and the later ones are safe to
//! drop. A rule line in another `<svg>` (a nested board's) is another scope and stays: a chart cut
//! out on its own takes the blocks of its enclosing `<svg>` with it.

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::{Captures, Regex};

/// `_STYLE_BLOCK_RE`: a block with the newline before it, if any, and the lines inside.
static STYLE_BLOCK: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?s)(\n?)  <style>\n(.*?)\n  </style>").expect("a valid pattern"));
/// `_SVG_TAG_RE`.
static SVG_TAG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"<svg\b|</svg>").expect("a valid pattern"));
/// `_DEDUPE_ELIGIBLE_RULE_RE`.
static ELIGIBLE_RULE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^\s*\.md-[0-9a-f]+-[a-z]+\s*\{").expect("a valid pattern"));

/// `_enclosing_svg_key`: the offset of the `<svg>` that most tightly encloses `pos`, -1 outside any.
fn enclosing_svg(svg: &str, pos: usize) -> isize {
    let mut open: Vec<usize> = Vec::new();
    for tag in SVG_TAG.find_iter(&svg[..pos]) {
        if tag.as_str() == "</svg>" {
            open.pop();
        } else {
            open.push(tag.start());
        }
    }
    open.last().map_or(-1, |at| *at as isize)
}

/// `svg` with each eligible rule line kept at its first occurrence per enclosing `<svg>`. A block
/// left with no line vanishes with the newline before it.
pub(crate) fn dedupe_repeated_style_rules(svg: &str) -> String {
    let mut seen: HashSet<(isize, String)> = HashSet::new();
    STYLE_BLOCK
        .replace_all(svg, |block: &Captures| {
            let whole = block.get(0).expect("group 0 is the match");
            let scope = enclosing_svg(svg, whole.start());
            let kept: Vec<&str> = block[2]
                .split('\n')
                .filter(|line| !ELIGIBLE_RULE.is_match(line) || seen.insert((scope, (*line).to_string())))
                .collect();
            if kept.is_empty() {
                String::new()
            } else {
                format!("{}  <style>\n{}\n  </style>", &block[1], kept.join("\n"))
            }
        })
        .into_owned()
}

#[cfg(test)]
mod tests {
    use super::dedupe_repeated_style_rules;

    const RULE: &str = "    .md-abc123-text { fill: red; }";

    fn block(rule: &str) -> String {
        format!("  <style>\n{rule}\n  </style>")
    }

    #[test]
    fn a_repeated_rule_line_in_one_svg_is_dropped() {
        let svg = format!("<svg>\n{}\n<g>\n{}\n</g>\n</svg>", block(RULE), block(RULE));
        let out = dedupe_repeated_style_rules(&svg);
        assert_eq!(out.matches(".md-abc123-text").count(), 1, "{out}");
    }

    #[test]
    fn a_repeated_rule_line_in_another_svg_stays() {
        let svg = format!("<svg>\n{}\n<svg>\n{}\n</svg>\n</svg>", block(RULE), block(RULE));
        let out = dedupe_repeated_style_rules(&svg);
        assert_eq!(out.matches(".md-abc123-text").count(), 2, "{out}");
    }

    #[test]
    fn a_block_with_no_line_left_vanishes_with_the_newline_before_it() {
        let svg = format!("<svg>\n{}\n\n{}\n</svg>", block(RULE), block(RULE));
        assert_eq!(dedupe_repeated_style_rules(&svg), format!("<svg>\n{}\n\n</svg>", block(RULE)));
    }

    #[test]
    fn a_block_keeps_the_lines_that_are_not_repeated() {
        let other = "    .md-abc123-code { fill: blue; }";
        let svg = format!("<svg>{}  <style>\n{RULE}\n{other}\n  </style></svg>", block(RULE));
        let out = dedupe_repeated_style_rules(&svg);
        assert_eq!(out, format!("<svg>{}  <style>\n{other}\n  </style></svg>", block(RULE)));
    }

    #[test]
    fn a_line_that_is_not_a_markdown_rule_is_never_dropped() {
        let line = "    body { margin: 0; }";
        let svg = format!("<svg>\n{}\n{}\n</svg>", block(line), block(line));
        assert_eq!(dedupe_repeated_style_rules(&svg).matches("body { margin: 0; }").count(), 2);
    }
}

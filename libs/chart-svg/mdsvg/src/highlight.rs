//! Transliterated from `libs/markdown-svg/src/mdsvg/highlight.py`: fenced code
//! colored per Pygments token.
//!
//! Python asks Pygments for a lexer by the fence's alias and for a style by
//! the theme name. Here the alias table and the corpus styles are data
//! (`pygments_data.rs`, generated from Pygments 2.21.0), and the lexers are
//! ports: YAML (`pygments_yaml.rs`) and the `RegexLexer`s SQL, Python and
//! Bash (`pygments_lexer.rs`). An alias Pygments resolves to a lexer that is
//! not ported, and a theme that is not ported, refuse by name, since
//! production would have colored the block; an alias Pygments does not know
//! is `Ok(None)`, the plain block, as in Python.

use std::collections::HashSet;

use crate::pygments_data::{PygmentsStyle, LEXER_ALIASES, STANDARD_TYPES, STYLES};
use crate::pygments_lexer::{self, Span};
use crate::pygments_yaml;

/// One `(text, color)` token; `color` is `"#rrggbb"`, or `""` for the
/// block's default fill (`highlight.py::CodeSegment`).
pub type CodeSegment = (String, String);

/// `#rrggbb` -> `rrggbb`, `#rgb` -> `rrggbb` (`StyleMeta.colorformat`,
/// `pygments/style.py:60-70`). `gen_pygments_data.py` admits only these.
fn color_format(word: &str) -> String {
    let hex = word.strip_prefix('#').expect("style colors are hex");
    match hex.len() {
        6 => hex.to_string(),
        3 => hex.chars().flat_map(|c| [c, c]).collect(),
        _ => unreachable!("gen_pygments_data.py admits #rgb and #rrggbb"),
    }
}

/// `StyleMeta.__new__`'s color slot for `token` (`pygments/style.py:72-118`):
/// a token starts from its parent's color (`noinherit` restarts from
/// `Token`'s) and the last color word of its own definition wins.
fn style_color(defs: &[(&str, &str)], token: &str) -> String {
    let own = defs
        .iter()
        .find(|(t, _)| *t == token)
        .map_or("", |(_, d)| *d);
    let words: Vec<&str> = own.split_whitespace().collect();
    let mut color = if words.contains(&"noinherit") && token != "Token" {
        style_color(defs, "Token")
    } else {
        token
            .rsplit_once('.')
            .map_or(String::new(), |(parent, _)| style_color(defs, parent))
    };
    for word in words {
        let is_flag = matches!(
            word,
            "noinherit"
                | "bold"
                | "nobold"
                | "italic"
                | "noitalic"
                | "underline"
                | "nounderline"
                | "roman"
                | "sans"
                | "mono"
        );
        if !is_flag && !word.starts_with("bg:") && !word.starts_with("border:") {
            color = color_format(word);
        }
    }
    color
}

/// `highlight.py::_token_color`: the style's color for `token`, walking to
/// the parent type on a miss. A style holds a token type when it is
/// `Token`, a `STANDARD_TYPES` key, one of its own keys, or an ancestor of
/// those (`_styles` is filled through `ttype.split()`).
fn token_color(style: &PygmentsStyle, known: &HashSet<String>, token: &str) -> String {
    let mut ttype = token;
    loop {
        if known.contains(ttype) {
            return style_color(style.definitions, ttype);
        }
        ttype = ttype.rsplit_once('.').expect("Token is always known").0;
    }
}

fn known_types(style: &PygmentsStyle) -> HashSet<String> {
    let mut known = HashSet::new();
    let keys = STANDARD_TYPES
        .iter()
        .chain(style.definitions.iter().map(|(t, _)| t));
    for key in keys {
        let mut path = String::new();
        for part in key.split('.') {
            if !path.is_empty() {
                path.push('.');
            }
            path.push_str(part);
            known.insert(path.clone());
        }
    }
    known
}

/// `Lexer._preprocess_lexer_input` with `stripall`, `stripnl`, `ensurenl`
/// off and `tabsize` 0 (`pygments/lexer.py:235-251`): BOM and newline
/// normalization only.
fn preprocess(code: &str) -> String {
    let code = code.strip_prefix('\u{feff}').unwrap_or(code);
    code.replace("\r\n", "\n").replace('\r', "\n")
}

/// Tokenize `code` and return per-source-line colored segments
/// (`highlight.py::highlight_code`). `Ok(None)` when Pygments knows no
/// lexer by `language`: render the plain block.
pub fn highlight_code(
    code: &str,
    language: &str,
    theme: &str,
) -> Result<Option<Vec<Vec<CodeSegment>>>, String> {
    let alias = language.to_lowercase();
    let Ok(at) = LEXER_ALIASES.binary_search_by_key(&alias.as_str(), |(a, _)| a) else {
        return Ok(None);
    };
    let class = LEXER_ALIASES[at].1;
    let port = match (class, pygments_lexer::lexer(class)) {
        ("YamlLexer", _) => None,
        (_, Some(lexer)) => Some(lexer),
        _ => {
            return Err(format!(
                "mdsvg-rs: fenced code highlighting for language `{language}` not ported"
            ))
        }
    };
    let Some(style) = STYLES.iter().find(|s| s.name == theme) else {
        return Err(format!(
            "mdsvg-rs: fenced code highlighting theme `{theme}` not ported"
        ));
    };

    let text = preprocess(code);
    let known = known_types(style);
    let mut lines: Vec<Vec<CodeSegment>> = vec![Vec::new()];
    let spans: Vec<Span> = match port {
        Some(lexer) => lexer.tokenize(&text),
        None => pygments_yaml::tokenize(&text),
    };
    for (token, start, end) in spans {
        let color = token_color(style, &known, token);
        let fill = if color.is_empty() {
            String::new()
        } else {
            format!("#{color}")
        };
        for (i, part) in text[start..end].split('\n').enumerate() {
            if i > 0 {
                lines.push(Vec::new());
            }
            if !part.is_empty() {
                lines
                    .last_mut()
                    .expect("lines is never empty")
                    .push((part.to_string(), fill.clone()));
            }
        }
    }
    Ok(Some(lines))
}

#[cfg(test)]
mod tests {
    use super::style_color;

    /// `StyleMeta` results for a style with `noinherit`, `#rgb` colors and
    /// color-less flag words, which neither corpus theme uses; the expected
    /// values are `pygments.style.Style.style_for_token(t)["color"]` of the
    /// same definitions.
    #[test]
    fn style_color_follows_stylemeta() {
        let defs = [
            ("Token", "#abc"),
            ("Token.Name", "#123456 bold"),
            ("Token.Name.Tag", "noinherit"),
            ("Token.Name.Tag.Sub", "bg:#ffffff"),
            ("Token.Comment", "italic #FFF"),
            ("Token.Comment.Single", "nobold #010203 border:#000"),
        ];
        let want = [
            ("Token", "aabbcc"),
            ("Token.Name", "123456"),
            ("Token.Name.Tag", "aabbcc"),
            ("Token.Name.Tag.Sub", "aabbcc"),
            ("Token.Comment", "FFFFFF"),
            ("Token.Comment.Single", "010203"),
            ("Token.Keyword", "aabbcc"),
        ];
        for (token, color) in want {
            assert_eq!(style_color(&defs, token), color, "{token}");
        }
    }
}

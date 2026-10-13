//! Utility functions for mdsvg.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/utils.py`.

use crate::pygments_re::is_py_space;

/// Escape text for safe inclusion in XML/SVG, byte-exact under strict XML
/// attribute-value normalization. Mirrors Python's `escape_xml`:
/// `html.escape(text, quote=True)` (`&`, `<`, `>`, `"`, `'`), then `\n`, `\r`
/// and `\t` as character references.
pub fn escape_xml(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#x27;"),
            '\n' => out.push_str("&#10;"),
            '\r' => out.push_str("&#13;"),
            '\t' => out.push_str("&#9;"),
            _ => out.push(ch),
        }
    }
    out
}

/// Escape text for inclusion in SVG text elements: XML-escape, then collapse
/// runs of 2+ spaces down to one (preserving single spaces).
pub fn escape_svg_text(text: &str) -> String {
    let escaped = escape_xml(text);
    let mut out = String::with_capacity(escaped.len());
    let mut chars = escaped.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch == ' ' {
            out.push(' ');
            while chars.peek() == Some(&' ') {
                chars.next();
            }
        } else {
            out.push(ch);
        }
    }
    out
}

/// Collapse multiple whitespace runs to a single space and strip edges.
/// Mirrors Python's `" ".join(text.split())`.
pub fn normalize_whitespace(text: &str) -> String {
    text.split(is_py_space)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// Split text into lines on `\n`, `\r\n`, or `\r`, without the line-ending
/// characters. Mirrors Python's
/// `text.replace("\r\n", "\n").replace("\r", "\n").split("\n")`.
pub fn split_lines(text: &str) -> Vec<String> {
    text.replace("\r\n", "\n")
        .replace('\r', "\n")
        .split('\n')
        .map(|s| s.to_string())
        .collect()
}

/// Format a number for SVG attribute output: an integral value prints with
/// no decimal point; otherwise fixed to `precision` places with trailing
/// zeros (and a trailing bare `.`) stripped.
pub fn format_number(n: f64, precision: usize) -> String {
    if n == n.trunc() {
        return format!("{}", n as i64);
    }
    let formatted = format!("{n:.precision$}");
    let trimmed = formatted.trim_end_matches('0').trim_end_matches('.');
    trimmed.to_string()
}

/// `format_number` at the Python default precision (2).
pub fn format_number2(n: f64) -> String {
    format_number(n, 2)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_whitespace_splits_on_every_python_space() {
        assert_eq!(
            normalize_whitespace(" a\x1cb\x1d\x1d c\u{a0}d\x1f"),
            "a b c d"
        );
    }

    #[test]
    fn test_escape_xml() {
        assert_eq!(escape_xml("<a>&\"'"), "&lt;a&gt;&amp;&quot;&#x27;");
    }

    #[test]
    fn test_escape_svg_text_collapses_spaces() {
        assert_eq!(escape_svg_text("a   b"), "a b");
        assert_eq!(escape_svg_text("a b"), "a b");
    }

    #[test]
    fn test_normalize_whitespace() {
        assert_eq!(normalize_whitespace("  a   b\tc\n"), "a b c");
    }

    #[test]
    fn test_split_lines() {
        assert_eq!(split_lines("a\r\nb\rc\nd"), vec!["a", "b", "c", "d"]);
    }

    #[test]
    fn test_format_number() {
        assert_eq!(format_number2(4.0), "4");
        assert_eq!(format_number2(4.5), "4.5");
        assert_eq!(format_number2(4.10), "4.1");
        assert_eq!(format_number2(4.125), "4.12");
    }
}

//! Utility functions for mdsvg.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/utils.py`.

/// Collapse runs of 2+ spaces to one (single spaces kept): an SVG `<text>`
/// renders them as one anyway, and layout measured them as one.
pub fn collapse_spaces(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.chars().peekable();
    while let Some(ch) = chars.next() {
        out.push(ch);
        if ch == ' ' {
            while chars.peek() == Some(&' ') {
                chars.next();
            }
        }
    }
    out
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
    fn collapse_spaces_keeps_single_spaces() {
        assert_eq!(collapse_spaces("a   b"), "a b");
        assert_eq!(collapse_spaces("a b"), "a b");
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

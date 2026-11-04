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
}

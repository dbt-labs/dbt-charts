//! Python `str` semantics the ports share: what `str.isspace()` and
//! `" ".join(s.split())` mean for non-ASCII input.

/// Python's `str.isspace()`: `\s` in a `str` pattern and the set `str.strip()`
/// removes. Unicode `White_Space` plus the separators `\x1c`-`\x1f`, which
/// `char::is_whitespace` leaves out. The one definition every crate reads.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Collapse multiple whitespace runs to a single space and strip edges.
/// Mirrors Python's `" ".join(text.split())`.
pub fn normalize_whitespace(text: &str) -> String {
    text.split(is_py_space)
        .filter(|word| !word.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Every code point `str.isspace()` accepts on CPython 3.13: the ASCII
    /// controls and space, `\x85`, `\xa0`, and the Unicode `Zs`/`Zl`/`Zp` characters.
    #[test]
    fn space_is_exactly_what_pythons_isspace_accepts() {
        let python_spaces: Vec<char> = "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \u{85}\u{a0}\u{1680}\
             \u{2000}\u{2001}\u{2002}\u{2003}\u{2004}\u{2005}\u{2006}\u{2007}\u{2008}\u{2009}\u{200a}\
             \u{2028}\u{2029}\u{202f}\u{205f}\u{3000}"
            .chars()
            .collect();
        assert_eq!(python_spaces.len(), 29);
        let accepted: Vec<char> = (0..=char::MAX as u32)
            .filter_map(char::from_u32)
            .filter(|&c| is_py_space(c))
            .collect();
        assert_eq!(accepted, python_spaces);
    }

    #[test]
    fn normalize_whitespace_splits_on_every_python_space() {
        assert_eq!(
            normalize_whitespace(" a\x1cb\x1d\x1d c\u{a0}d\x1f"),
            "a b c d"
        );
    }

    #[test]
    fn test_normalize_whitespace() {
        assert_eq!(normalize_whitespace("  a   b\tc\n"), "a b c");
    }
}

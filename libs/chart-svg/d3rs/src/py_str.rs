//! Python `str` semantics the ports share: what `str.isspace()` and
//! `" ".join(s.split())` mean for non-ASCII input.

/// Python's `str.isspace()`: `\s` in a `str` pattern and the set `str.strip()`
/// removes. Unicode `White_Space` plus the separators `\x1c`-`\x1f`, which
/// `char::is_whitespace` leaves out. The one definition every crate reads.
pub fn is_py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python's `\w` for one character of a `str` pattern: `str.isalnum()` or `_`, over the Unicode database
/// the table was generated from.
pub fn is_py_word(c: char) -> bool {
    crate::py_word::PY_WORD
        .binary_search_by(|&(lo, hi)| {
            if c < lo {
                std::cmp::Ordering::Greater
            } else if c > hi {
                std::cmp::Ordering::Less
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// `DividerStyle._space_separated`: a dash pattern stripped of Python's
/// whitespace set, its `[ ,]+` separators joined by single spaces. The one
/// reading `compile` and `board-model` share, for input the `Dasharray`
/// pattern admits.
pub fn dash_pattern_separated(value: &str) -> String {
    value
        .trim_matches(is_py_space)
        .split([' ', ','])
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
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

/// `json.dumps(text)` with its defaults: double-quoted, `ensure_ascii` (every code point outside
/// ` `..`~` a `\uXXXX` escape in lowercase hex, an astral one as a surrogate pair), and the short
/// escapes for `"`, `\`, `\n`, `\r`, `\t`, `\b` and `\f`.
pub fn json_string(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod json_string_tests {
    use super::json_string;

    #[test]
    fn it_escapes_as_json_dumps_does() {
        assert_eq!(json_string("a\"b\\c\n\r\t\u{8}\u{c}"), r#""a\"b\\c\n\r\t\b\f""#);
        assert_eq!(json_string("\u{1}\u{7f}\u{fc}"), r#""\u0001\u007f\u00fc""#);
        assert_eq!(json_string("\u{1f600}"), r#""\ud83d\ude00""#);
    }
}

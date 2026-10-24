//! CSS-context escaping for authored values written into a CSS declaration.
//!
//! Transliterated from `libs/markdown-svg/src/mdsvg/css.py`. A font-family
//! stack is escaped, never refused (`css_font_family_stack`); every other
//! authored string a `Style` carries is refused when it holds a character that
//! can close the declaration or open a rule (`is_css_breaking`,
//! `Style::validate_css`).

use once_cell::sync::Lazy;
use regex::Regex;

use crate::unicode_unassigned::is_unassigned;
use d3rs::py_str::is_py_space;

/// `css.py:9` `CSS_BREAKING_CHARS_RE` = `[<>;{}"'\\*\x00-\x1f\x7f]`: not a
/// color validator, only the characters that break out of a declaration or a
/// `<style>` block, so `var()`/`oklch()`/`color-mix()` still pass.
pub fn is_css_breaking(ch: char) -> bool {
    matches!(
        ch,
        '<' | '>' | ';' | '{' | '}' | '"' | '\'' | '\\' | '*' | '\x00'..='\x1f' | '\x7f'
    )
}

/// `css.py:14-29` `_CSS_GENERIC_FONT_FAMILIES`: generic keywords stay unquoted,
/// since a quoted `"sans-serif"` names a literal font.
const CSS_GENERIC_FONT_FAMILIES: [&str; 13] = [
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
    "ui-rounded",
    "math",
    "emoji",
    "fangsong",
];

/// One member of `[\w -]` (`css.py:12`): Python's `\w` on `str` is
/// `isalnum() or "_"`, i.e. the Unicode letter and number categories, as the
/// interpreter's own Unicode data assigns them (`is_unassigned`).
fn is_family_body_char(ch: char) -> bool {
    static LETTER_OR_NUMBER: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"^[\p{L}\p{N}]$").expect("static pattern"));
    matches!(ch, ' ' | '-' | '_')
        || (!is_unassigned(ch) && LETTER_OR_NUMBER.is_match(ch.encode_utf8(&mut [0; 4])))
}

/// `css.py:12` `_SAFE_FAMILY_ENTRY_RE.fullmatch(entry)`, where the pattern is
/// `\s*(["']?)[\w -]+\1\s*`: a name of word characters, spaces and hyphens,
/// optionally wrapped in one matching pair of quotes, with whitespace around.
/// Every non-whitespace character must sit in the body (or be the one quote
/// pair around it); an all-whitespace entry matches only through a body
/// character, the space.
fn is_safe_family_entry(entry: &str) -> bool {
    let chars: Vec<char> = entry.chars().collect();
    let Some(first) = chars.iter().position(|&c| !is_py_space(c)) else {
        return chars.iter().any(|&c| is_family_body_char(c));
    };
    let last = chars
        .iter()
        .rposition(|&c| !is_py_space(c))
        .expect("first exists, so last does");
    let quote = chars[first];
    if quote == '"' || quote == '\'' {
        return last > first + 1
            && chars[last] == quote
            && chars[first + 1..last].iter().all(|&c| is_family_body_char(c));
    }
    chars[first..=last].iter().all(|&c| is_family_body_char(c))
}

/// `css.py:32-37` `escape_css_string`: `value` as a quoted CSS string, every
/// character outside `[A-Za-z0-9 _-]` (`_CSS_STRING_SAFE_RE`, `css.py:11`)
/// written as a lowercase-hex escape followed by the terminating space.
pub fn escape_css_string(value: &str) -> String {
    let mut out = String::with_capacity(value.len() + 2);
    out.push('"');
    for ch in value.chars() {
        if ch.is_ascii_alphanumeric() || matches!(ch, ' ' | '_' | '-') {
            out.push(ch);
        } else {
            out.push_str(&format!("\\{:x} ", ch as u32));
        }
    }
    out.push('"');
    out
}

/// `css.py:40-52` `css_font_family_stack`: a stack whose every comma-separated
/// entry is a plain name is returned unchanged; otherwise each family is
/// stripped of whitespace and surrounding quotes, an empty one is dropped, a
/// generic keyword is emitted bare and lowercase, and any other name becomes
/// an escaped CSS string.
pub fn css_font_family_stack(stack: &str) -> String {
    if stack.split(',').all(is_safe_family_entry) {
        return stack.to_string();
    }
    stack
        .split(',')
        .filter_map(|raw| {
            let name = raw
                .trim_matches(is_py_space)
                .trim_matches(|c| c == '\'' || c == '"');
            if name.is_empty() {
                return None;
            }
            let lowered = name.to_lowercase();
            Some(if CSS_GENERIC_FONT_FAMILIES.contains(&lowered.as_str()) {
                lowered
            } else {
                escape_css_string(name)
            })
        })
        .collect::<Vec<_>>()
        .join(", ")
}

/// Python's `str.isprintable()` for one character: the space, or a character in
/// a letter, mark, number, punctuation or symbol category. Every other category
/// (`Cc`, `Cf`, `Cs`, `Co`, `Cn`, `Zl`, `Zp`, `Zs`) is non-printable; `Cn` is
/// the interpreter's own assignment (`is_unassigned`), not the regex crate's.
fn is_py_printable(ch: char) -> bool {
    static PRINTABLE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"^[\p{L}\p{M}\p{N}\p{P}\p{S}]$").expect("static pattern"));
    ch == ' ' || (!is_unassigned(ch) && PRINTABLE.is_match(ch.encode_utf8(&mut [0; 4])))
}

/// Python's `repr(str)`, for the `{value!r}` in `style.py:290`'s message: a
/// single-quoted literal (double-quoted when the value holds `'` and no `"`),
/// with the quote, `\\`, `\t`, `\n`, `\r` escaped and every non-printable
/// character written as `\xNN`, `\uNNNN` or `\UNNNNNNNN`.
pub fn python_repr(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for ch in value.chars() {
        match ch {
            c if c == quote || c == '\\' => {
                out.push('\\');
                out.push(c);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            c if is_py_printable(c) => out.push(c),
            c if (c as u32) < 0x100 => out.push_str(&format!("\\x{:02x}", c as u32)),
            c if (c as u32) < 0x10000 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push_str(&format!("\\U{:08x}", c as u32)),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    // Every expected string below is `mdsvg.css`'s own output (Python 3.13).

    #[test]
    fn plain_stack_is_unchanged() {
        for stack in [
            "'Source Serif 4', Georgia, serif",
            "ui-monospace, 'SF Mono', Menlo, Consolas, monospace",
            "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
            "Fira Code, monospace",
            "\"Q\" , Mono",
            "  'x' ,\t'y'  ",
            "'Café', serif",
            "Ünï-cøde_1, 日本語",
            " , ",
            "\u{212a}",
        ] {
            assert_eq!(css_font_family_stack(stack), stack);
        }
    }

    #[test]
    fn unsafe_family_becomes_a_hex_escaped_string_with_the_trailing_space() {
        assert_eq!(css_font_family_stack("Georgia; x"), r#""Georgia\3b  x""#);
        assert_eq!(
            css_font_family_stack("x;} svg{background:url(https://evil)} .a{"),
            r#""x\3b \7d  svg\7b background\3a url\28 https\3a \2f \2f evil\29 \7d  \2e a\7b ""#
        );
        assert_eq!(css_font_family_stack("x\\y"), r#""x\5c y""#);
        assert_eq!(css_font_family_stack("a\nb"), r#""a\a b""#);
        assert_eq!(css_font_family_stack("'a\"b'"), r#""a\22 b""#);
        assert_eq!(css_font_family_stack("'x'y'"), r#""x\27 y""#);
        assert_eq!(css_font_family_stack("'x"), r#""x""#);
    }

    #[test]
    fn generic_keyword_stays_bare_and_lowercase() {
        assert_eq!(
            css_font_family_stack("'a;b', SERIF, <x>"),
            r#""a\3b b", serif, "\3c x\3e ""#
        );
        assert_eq!(
            css_font_family_stack("Sans-Serif, 'MONOSPACE;'"),
            r#"sans-serif, "MONOSPACE\3b ""#
        );
        assert_eq!(
            css_font_family_stack("😀 emoji, EMOJI"),
            r#""\1f600  emoji", emoji"#
        );
    }

    #[test]
    fn non_ascii_whitespace_and_name_characters_escape_per_codepoint() {
        assert_eq!(css_font_family_stack("A\u{a0}B, 'C D'"), r#""A\a0 B", "C D""#);
    }

    #[test]
    fn empty_families_are_dropped() {
        assert_eq!(css_font_family_stack(""), "");
        assert_eq!(css_font_family_stack("\t"), "");
        assert_eq!(css_font_family_stack(" ,, "), "");
    }

    #[test]
    fn escape_css_string_keeps_only_the_safe_class() {
        assert_eq!(escape_css_string("a b_c-D9"), "\"a b_c-D9\"");
        assert_eq!(escape_css_string("é\"\\<"), r#""\e9 \22 \5c \3c ""#);
        assert_eq!(escape_css_string("😀"), r#""\1f600 ""#);
        assert_eq!(escape_css_string(""), "\"\"");
    }

    #[test]
    fn css_breaking_set_is_the_declaration_and_style_block_breakers() {
        for ch in "<>;{}\"'\\*\x00\x1f\x7f".chars() {
            assert!(is_css_breaking(ch), "{ch:?}");
        }
        for ch in "#(),.:%/ -_aZ9é\u{80}\u{a0}".chars() {
            assert!(!is_css_breaking(ch), "{ch:?}");
        }
    }

    #[test]
    fn python_repr_matches_python() {
        assert_eq!(python_repr("red;}"), "'red;}'");
        assert_eq!(python_repr("it's"), "\"it's\"");
        assert_eq!(python_repr("say \"x\""), "'say \"x\"'");
        assert_eq!(python_repr("it's \"q\""), "'it\\'s \"q\"'");
        assert_eq!(python_repr("a\\b"), "'a\\\\b'");
        assert_eq!(python_repr("tab\t nl\n cr\r"), "'tab\\t nl\\n cr\\r'");
        assert_eq!(python_repr("\x7f\x00\x1f"), "'\\x7f\\x00\\x1f'");
        assert_eq!(python_repr("é😀"), "'é😀'");
        assert_eq!(
            python_repr("\u{a0}\u{ad}\u{2028}\u{200b}\u{e000}\u{10ffff}"),
            "'\\xa0\\xad\\u2028\\u200b\\ue000\\U0010ffff'"
        );
    }
}

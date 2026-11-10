//! Python's `slugify` (`normalize/dispatch.py`): `re.sub(r"[^\w\s-]", "", text.lower().strip())`,
//! then runs of whitespace and `-` become `_`, then `_` is stripped from both ends.
//!
//! `\w` and `\s` are Python's `str` predicates, not Rust's: `isalnum()` is the letter, number
//! and decimal categories, and `isspace()` adds the four ASCII separators U+001C..U+001F.
//! `is_space` agrees with Python on every code point. `is_word` follows Python's categories at
//! this crate's Unicode version (17, Python 3.14 has 16): it also takes code points Python still
//! leaves unassigned, so it can only over-refuse a name, never accept one Python refuses.

use unicode_properties::{GeneralCategory as C, UnicodeGeneralCategory};

pub(crate) fn is_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

pub(crate) fn is_word(c: char) -> bool {
    c == '_'
        || matches!(
            c.general_category(),
            C::UppercaseLetter
                | C::LowercaseLetter
                | C::TitlecaseLetter
                | C::ModifierLetter
                | C::OtherLetter
                | C::DecimalNumber
                | C::LetterNumber
                | C::OtherNumber
        )
}

pub fn slugify(text: &str) -> String {
    let lowered = text.to_lowercase();
    let trimmed = lowered.trim_matches(is_space);
    let mut out = String::new();
    let mut pending = false;
    for c in trimmed.chars() {
        if is_space(c) || c == '-' {
            pending = true;
        } else if is_word(c) {
            if pending {
                out.push('_');
                pending = false;
            }
            out.push(c);
        }
    }
    if pending {
        out.push('_');
    }
    out.trim_matches('_').to_string()
}

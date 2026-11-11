//! The emoji family a board's `font.emoji` mode puts in every font stack it draws
//! (`_EMOJI_MODE_TO_FAMILY`, `insert_emoji_family`): a law of the text a stack paints, so each
//! reader of a family applies it at the read (`with_emoji`), not a pass over the style tree.
//! The board's `font.emoji` mode puts its family after a stack's primary font.

use board_model::style::board::EmojiMode;

/// The bundled emoji family `mode` draws with; `system-default` and `disabled` add none.
pub fn emoji_family(mode: &EmojiMode) -> Option<&'static str> {
    match mode {
        EmojiMode::Monochrome => Some("Noto Emoji"),
        EmojiMode::SystemDefault | EmojiMode::Disabled => None,
    }
}

/// `family` with `emoji` quoted after its primary font: a stack that already names it is kept, and
/// a stack of one font gets it appended. Commas are read as separators, never as part of a quoted name.
pub fn insert_emoji_family(family: &str, emoji: &str) -> String {
    if family.contains(emoji) {
        return family.to_string();
    }
    match family.split_once(',') {
        None => format!("{family}, '{emoji}'"),
        Some((primary, rest)) => format!("{primary}, '{emoji}',{rest}"),
    }
}

/// `family` as a stack drawn under `mode`.
pub fn with_emoji(family: &str, mode: &EmojiMode) -> String {
    emoji_family(mode).map_or_else(|| family.to_string(), |emoji| insert_emoji_family(family, emoji))
}

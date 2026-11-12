//! The narrowest a text column can be: the widest word it cannot break.

use crate::Measure;

/// Longer tokens are opaque IDs or base64 chunks that would force a floor no
/// column could reach.
const MAX_TOKEN_CHARS: usize = 40;

/// Whether a token may set a floor: not a URL, not longer than
/// [`MAX_TOKEN_CHARS`].
fn sets_floor(token: &str) -> bool {
    !token.starts_with("http://")
        && !token.starts_with("https://")
        && token.chars().count() <= MAX_TOKEN_CHARS
}

/// The widest whitespace token of `text` that [`sets_floor`] admits, and its width under `width`;
/// `(0.0, "")` when none qualifies. The measurer is the caller's: a table measures raw advances.
pub fn widest_word_with(text: &str, mut width: impl FnMut(&str) -> Result<f64, String>) -> Result<(f64, &str), String> {
    let mut widest = (0.0_f64, "");
    for token in text.split_whitespace().filter(|t| sets_floor(t)) {
        let token_width = width(token)?;
        if token_width > widest.0 {
            widest = (token_width, token);
        }
    }
    Ok(widest)
}

impl Measure<'_> {
    /// [`widest_word_with`] under the book's measure.
    pub fn widest_word<'t>(&self, text: &'t str, family: &str, size: f64) -> Result<(f64, &'t str), String> {
        widest_word_with(text, |token| self.fonts.measure(token, family, size))
    }

    /// The width of [`Self::widest_word`].
    pub fn widest_token(&self, text: &str, family: &str, size: f64) -> Result<f64, String> {
        Ok(self.widest_word(text, family, size)?.0)
    }
}

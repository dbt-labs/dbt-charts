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

impl Measure<'_> {
    /// The widest whitespace token of `text` that [`sets_floor`] admits.
    pub fn widest_token(&self, text: &str, family: &str, size: f64) -> Result<f64, String> {
        let mut widest = 0.0_f64;
        for token in text.split_whitespace().filter(|t| sets_floor(t)) {
            widest = widest.max(self.fonts.measure(token, family, size)?);
        }
        Ok(widest)
    }
}

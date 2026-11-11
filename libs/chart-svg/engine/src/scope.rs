//! The language and time locale a board's words and dates print in (`regional_scope`), opened for the
//! whole render.
//!
//! Both come from the board's `regional:`; a defined time locale is refused.

use board_model::board::Board;
use board_model::regional::TimeLocaleChoice;
use d3rs::time_format::Locale;
use diagnostic::{diag, refuse, Diagnostic};
use display_text::language::{language_of_time_locale, with_language, Language};

pub(crate) struct Scope {
    language: Language,
    locale: Locale,
}

/// The tag of the time locale `board` names; a board with none is en-US.
pub(crate) fn time_tag(board: &Board) -> Result<&str, Diagnostic> {
    match board.regional.as_ref().and_then(|r| r.locale.as_ref()).map(|l| &l.time) {
        None => Ok("en-US"),
        Some(TimeLocaleChoice::Name(name)) => Ok(name.as_str()),
        Some(TimeLocaleChoice::Definition(_)) => {
            Err(Diagnostic::refused(refuse!("a defined time locale is not supported")))
        }
    }
}

/// The scope `board`'s `regional:` names; a board with none prints in en-US.
pub(crate) fn of(board: &Board) -> Result<Scope, Diagnostic> {
    let tag = time_tag(board)?;
    let language = board.regional.as_ref().and_then(|r| r.language.as_ref()).map(|l| l.as_str());
    let code = language.unwrap_or_else(|| language_of_time_locale(Some(tag)));
    let definition = display_text::locale::time_locale(tag).map_err(diag::err_internal)?;
    Ok(Scope {
        language: Language::new(code).map_err(diag::err_internal)?,
        locale: serde_json::from_str(definition).map_err(|e| diag::err_internal(e.to_string()))?,
    })
}

impl Scope {
    pub(crate) fn within<T>(&self, render: impl FnOnce() -> T) -> T {
        with_language(self.language, || bind::time_locale::with_time_locale(&self.locale, render))
    }
}

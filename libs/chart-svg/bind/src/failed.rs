//! A failed chart's error card, as text: what the card says, before any width decides how it wraps.

use diagnostic::{codes, refuse, Diagnostic, Level};
use serde_json::{json, Value};

use crate::model::Failure;

/// The site production links error codes to when the host sets none (`_docs_site.py::DEFAULT_DOCS_SITE_URL`).
pub const DEFAULT_DOCS_SITE: &str = "https://docs.dbtcharts.com";

/// The words of a failed chart's card.
#[derive(Debug, Clone, PartialEq)]
pub struct FailedText {
    pub code: String,
    /// The code's own anchor on the error reference page; `http`, `https`, `mailto` or scheme-less.
    pub href: String,
    /// The diagnostic's message, with the query it ran appended when the message does not end in it.
    pub message: String,
    pub hint: Option<String>,
    /// The closing link's words, in the board language.
    pub link_text: String,
}

impl FailedText {
    /// The card's text for `failure`. `docs_site` is the resolved docs site URL (production reads
    /// `DCT_DOCS_URL` for it; bind reads no environment), without a trailing slash.
    ///
    /// The hint is the diagnostic's own: set where it was raised, else written by the code's
    /// generator. A code whose generator is not ported (`diagnostic::hints::PORTED`) and that
    /// carries no hint is refused: Python would write one, and a card without it would not be
    /// production's.
    pub fn of(failure: &Failure, docs_site: &str) -> Result<Self, Diagnostic> {
        let diagnostic = &failure.diagnostic;
        let code = diagnostic.registered().ok_or_else(|| {
            Diagnostic::from(format!("failed card: {} is not a registered code", diagnostic.code))
        })?;
        let hint = diagnostic.hint();
        if let Some(generator) = code.hint.filter(|_| hint.is_none() && !diagnostic::hints::PORTED.contains(&code.id)) {
            return Err(refuse!("the hint of {} (written by {generator}) is not supported", code.id).into());
        }
        let message = diagnostic.message().map_err(|e| Diagnostic::from(e.to_string()))?;
        let page = match code.level {
            Level::Error => "errors",
            Level::Warning => "warnings",
        };
        let href = format!("{docs_site}/reference/{page}/#{}", code.id.to_lowercase());
        checked_href(&href)?;
        Ok(FailedText {
            code: code.id.to_string(),
            href,
            message: display_message(message, query_of(diagnostic)?),
            hint: hint.map(str::to_string),
            link_text: display_text::language::tr("placard.about_this_error", None, &[])?,
        })
    }
}

/// The query a failure ran: its `query_name` field (`diagnostic.py::build_diagnostic`).
fn query_of(diagnostic: &Diagnostic) -> Result<Option<&str>, Diagnostic> {
    match diagnostic.params.get("query_name") {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(query)) => Ok(Some(query)),
        Some(other) => Err(format!("failed card: query_name {other} is not text").into()),
    }
}

/// `diagnostic.py::display_message`: the query a failure ran, appended unless the message already
/// ends in it; no query, or an empty one, leaves the message as it is.
pub fn display_message(message: String, query: Option<&str>) -> String {
    match query.filter(|query| !query.is_empty()) {
        Some(query) => {
            let suffix = format!(" (query: {query})");
            if message.ends_with(&suffix) {
                message
            } else {
                message + &suffix
            }
        }
        None => message,
    }
}

/// `svg_utils.checked_href` (svg_utils.py:80-95): `href` has no scheme or an `http`, `https` or
/// `mailto` one. A browser reads the scheme after dropping leading C0 controls and spaces and every
/// tab, LF and CR, case-folded (`links.py:34`, `[a-z][a-z0-9+.\-]*` before a `:`).
pub fn checked_href(href: &str) -> Result<(), Diagnostic> {
    let normalized: String = href
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\n' | '\r'))
        .collect::<String>()
        .to_lowercase();
    let mut chars = normalized.char_indices();
    if !chars.next().is_some_and(|(_, c)| c.is_ascii_lowercase()) {
        return Ok(());
    }
    let end = chars
        .find(|&(_, c)| !(c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '+' | '.' | '-')))
        .map_or(normalized.len(), |(i, _)| i);
    let scheme = &normalized[..end];
    if !normalized[end..].starts_with(':') || matches!(scheme, "http" | "https" | "mailto") {
        return Ok(());
    }
    Err(Diagnostic::new(
        &codes::ERR_LINK_SCHEME_UNSAFE_AT_RENDER,
        [("href", json!(href)), ("scheme", json!(scheme))],
    ))
}

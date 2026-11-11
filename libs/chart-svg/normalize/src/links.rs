//! `validate/links.py`: an authored `link:` or `header_link:` has a board path, http, https or
//! mailto scheme, and a template whose literal prefix leaves the scheme to row data is anchored.

use crate::chart_checks::Patch;
use crate::compile::CompileError;
use crate::host_facts::link_slots;
use diagnostic::{diag, Diagnostic};

/// `links.py::link_scheme`: the scheme a browser navigates `href` with, `None` when relative.
/// WHATWG parsing strips leading C0 controls and spaces and removes tab, LF and CR anywhere
/// before the scheme is read, and folds case.
fn link_scheme(href: &str) -> Option<String> {
    let normalized: String = href
        .trim_start_matches(|c: char| c <= ' ')
        .chars()
        .filter(|c| !matches!(c, '\t' | '\n' | '\r'))
        .collect::<String>()
        .to_lowercase();
    let mut chars = normalized.char_indices();
    if !chars.next().is_some_and(|(_, c)| c.is_ascii_lowercase()) {
        return None;
    }
    let end = chars
        .find(|&(_, c)| {
            !(c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '+' | '.' | '-'))
        })
        .map_or(normalized.len(), |(i, _)| i);
    normalized[end..]
        .starts_with(':')
        .then(|| normalized[..end].to_string())
}

/// A scheme holds none of `/?#`, so a prefix with one cannot be completed into a scheme by data.
const ANCHOR_CHARS: [char; 3] = ['/', '?', '#'];

/// `exempt`: the render re-checks the resolved value whatever the prefix holds (a table's links).
fn check(link: &str, path: &str, exempt: bool) -> Option<Diagnostic> {
    let prefix = link.split("{{").next().unwrap_or(link);
    let scheme = link_scheme(prefix);
    if let Some(scheme) = scheme
        .as_deref()
        .filter(|s| !matches!(*s, "http" | "https" | "mailto"))
    {
        return Some(diag::err_link_scheme_unsafe(link, path, scheme));
    }
    let anchored = scheme.is_some() || prefix.contains(ANCHOR_CHARS);
    (link.contains("{{") && !anchored && !exempt)
        .then(|| diag::err_link_scheme_unanchored(link, path))
}

/// The first unsafe or unanchored link of the board's slots (`host_facts::link_slots`).
pub fn link_error(
    patch: &Patch,
    public: &dyn Fn(&str) -> Result<String, CompileError>,
) -> Result<Option<Diagnostic>, CompileError> {
    let mut ids = std::collections::BTreeMap::new();
    for key in patch.body.charts.keys() {
        ids.insert(key.clone(), public(key)?);
    }
    let slots = link_slots(patch, &|key| ids.get(key).cloned().unwrap_or_default());
    Ok(slots
        .iter()
        .find_map(|(path, link, exempt)| check(link, path, *exempt)))
}

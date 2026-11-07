//! The built-in themes as board patches: each `defaults/themes/<name>.yaml` compiled in, parsed as
//! a board patch, its names-only `extends` chain folded lowest first. A theme is package data,
//! so a file that fails to parse is a build defect the tests catch, not a runtime state.

use crate::board::BoardBody;
use model_core::{parse_patch, Leaf, Tri};
use std::collections::BTreeMap;
use std::fmt;
use std::sync::OnceLock;

include!(concat!(env!("OUT_DIR"), "/theme_sources.rs"));

type Patch = <BoardBody as Leaf>::Patch;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ThemeError {
    /// `entry` names no theme; `from` is the theme whose `extends` wrote it, `None` for a direct lookup.
    Unknown { from: Option<String>, entry: String },
    /// The chain returns to a theme it is already folding.
    Circular(String),
    /// The theme file is not a valid board patch.
    Invalid { theme: String, first: String },
}

impl fmt::Display for ThemeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unknown {
                from: Some(t),
                entry,
            } => {
                write!(f, "theme {t:?} extends {entry:?}, which is no theme")
            }
            Self::Unknown { from: None, entry } => write!(f, "{entry:?} is no built-in theme"),
            Self::Circular(t) => write!(f, "Circular extends: {t}.yaml is already in the chain"),
            Self::Invalid { theme, first } => write!(f, "theme {theme:?} is invalid: {first}"),
        }
    }
}

impl std::error::Error for ThemeError {}

/// Whether `name` is a built-in theme stem (`_base` and the diagnostics themes included).
pub fn is_built_in(name: &str) -> bool {
    THEME_SOURCES.iter().any(|(n, _)| *n == name)
}

/// `name` folded over `sources` (`(name, yaml text)`): its `extends` entries low to high, then its
/// own keys. The folded patch carries no `extends`.
pub fn fold_theme(sources: &[(&str, &str)], name: &str) -> Result<Patch, ThemeError> {
    fold_in(sources, name, &mut Vec::new())
}

fn fold_in(
    sources: &[(&str, &str)],
    name: &str,
    chain: &mut Vec<String>,
) -> Result<Patch, ThemeError> {
    if chain.iter().any(|c| c == name) {
        return Err(ThemeError::Circular(name.to_string()));
    }
    let invalid = |first: String| ThemeError::Invalid {
        theme: name.to_string(),
        first,
    };
    let Some((_, text)) = sources.iter().find(|(n, _)| *n == name) else {
        return Err(ThemeError::Unknown {
            from: chain.last().cloned(),
            entry: name.to_string(),
        });
    };
    let node = yaml::parse_node(text).map_err(|d| {
        invalid(
            d.iter()
                .map(|d| d.code.to_string())
                .collect::<Vec<_>>()
                .join(", "),
        )
    })?;
    let parsed = parse_patch::<BoardBody>(&node, None);
    if let Some(e) = parsed.errors.first() {
        return Err(invalid(format!("{} at {:?}", e.diagnostic.code, e.path())));
    }
    let mut own = parsed.twin;
    let entries = match std::mem::take(&mut own.extends) {
        Tri::Value(e) => e.0,
        Tri::Unset | Tri::Null => Vec::new(),
    };
    chain.push(name.to_string());
    let mut folded = Patch::default();
    for entry in &entries {
        folded.merge(fold_in(sources, entry, chain)?);
    }
    chain.pop();
    folded.merge(own);
    Ok(folded)
}

/// The built-in theme `name` folded; computed for every theme on first use.
pub fn built_in_theme(name: &str) -> Result<&'static Patch, ThemeError> {
    static ALL: OnceLock<BTreeMap<&'static str, Result<Patch, ThemeError>>> = OnceLock::new();
    let all = ALL.get_or_init(|| {
        THEME_SOURCES
            .iter()
            .map(|(n, _)| (*n, fold_theme(THEME_SOURCES, n)))
            .collect()
    });
    match all.get(name) {
        Some(Ok(p)) => Ok(p),
        Some(Err(e)) => Err(e.clone()),
        None => Err(ThemeError::Unknown {
            from: None,
            entry: name.to_string(),
        }),
    }
}

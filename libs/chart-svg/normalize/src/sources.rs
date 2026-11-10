//! The `source:` of a SQL query, as Python's `normalize_query` judges it: a path-like source (a
//! `/`, or a `.csv` / `.json` / `.parquet` suffix) is an inline file source, resolved against the
//! project; any other is the name of a source the project declares.
//!
//! Python skips the name check while the project declares no sources (the execute-time resolver
//! is then the only backstop), but registers each inline file source into the same registry as it
//! goes, so a name met after one is judged against a registry that is no longer empty. The order
//! Python meets queries in is not modeled here: the check refuses a name whenever the board holds
//! any inline file source, which never accepts a board Python refuses. Python also resolves a
//! given inline file source once and skips its later uses, so a use that cannot resolve (a layer's
//! SQL text has no project location) is accepted there when another query registered the source
//! first; every use is checked here, which refuses that board too.
//!
//! An inline file source holding Jinja is refused whole: Python renders dbt Jinja over the path
//! (`env_var`, undefined names, an absolute or escaping result all fail there) and the engine
//! renders no Jinja.

use crate::files::ProjectFiles;
use crate::hoist::HoistError;
use board_model::query::QueryAuthored;
use diagnostic::{diag, py_str_repr};
use model_core::Tri;
use project::ProjectPath;
use std::collections::BTreeSet;

/// The names of the host's project source registry, as Python's compile reads it: the `sources:`
/// entries of `dbt_charts.yml`, or, for a dbt project that declares none, the one entry its
/// `profile:` gives (so a dbt project with a profile is never `empty`). A project with neither
/// says so ([`SourceRegistry::empty`]): the name check is then off, as in Python. The profile's
/// entry is a name to check against only; a SQL query naming no source still has no default to
/// take (`ERR-SOURCE-REQUIRED`).
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct SourceRegistry(BTreeSet<String>);

impl SourceRegistry {
    /// A project that declares no sources.
    pub const fn empty() -> Self {
        Self(BTreeSet::new())
    }

    /// A project that declares these sources (none is [`SourceRegistry::empty`]).
    pub fn declared<S: Into<String>>(names: impl IntoIterator<Item = S>) -> Self {
        Self(names.into_iter().map(Into::into).collect())
    }
}

/// A SQL query's source, with the directory its path-like form is resolved from.
#[derive(Debug, Clone)]
pub(crate) struct SourceUse {
    /// The query's name, for the message.
    pub(crate) query: String,
    pub(crate) source: String,
    /// The directory of the file the query is compiled in (`""` is the project root); `None` for
    /// a query Python normalizes with no project location, which no inline file source resolves
    /// from.
    pub(crate) anchor: Option<String>,
}

impl SourceUse {
    /// The use a query makes of its source, when it is a SQL query that names one.
    pub(crate) fn of(
        key: &str,
        query: &Option<QueryAuthored>,
        anchor: Option<&str>,
    ) -> Option<Self> {
        match query {
            Some(QueryAuthored::Sql(q)) => match &q.base.source {
                Tri::Value(source) => Some(Self {
                    query: key.to_string(),
                    source: source.as_str().to_string(),
                    anchor: anchor.map(str::to_string),
                }),
                _ => None,
            },
            _ => None,
        }
    }
}

/// Python's `is_file_source_ref`.
fn is_file_source_ref(source: &str) -> bool {
    let lower = source.to_lowercase();
    source.contains('/')
        || [".csv", ".json", ".parquet"]
            .iter()
            .any(|s| lower.ends_with(s))
}

/// The suffix and stem of the last component of `PurePosixPath(source)`: empty and `.` components
/// are dropped, and a suffix is the part from the last dot of a name that neither starts nor ends
/// with it.
fn stem_and_suffix(source: &str) -> (&str, &str) {
    let name = source
        .rsplit('/')
        .find(|c| !c.is_empty() && *c != ".")
        .unwrap_or("");
    match name.rfind('.') {
        Some(i) if i > 0 && i + 1 < name.len() => name.split_at(i),
        _ => (name, ""),
    }
}

/// `^[A-Za-z_][A-Za-z0-9_]*$` as Python's `re` reads it, whose `$` also matches before a final
/// newline.
fn is_identifier(stem: &str) -> bool {
    let stem = stem.strip_suffix('\n').unwrap_or(stem);
    let mut chars = stem.chars();
    chars
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// Python's `resolve_file_source` and the config built from it: the file is looked for from the
/// query's directory and the project root (both holding it is ambiguous; neither is not an
/// error, execute reports a missing file per chart), and its stem names its table.
fn resolve_file_source(
    usage: &SourceUse,
    anchor: &str,
    files: &dyn ProjectFiles,
) -> Result<(), HoistError> {
    let unresolved = |why: String| {
        HoistError::UnresolvedReference(Box::new(diag::err_unresolved_reference(
            usage.source.as_str(),
            format!(" in query {} \u{2014} {why}", py_str_repr(&usage.query)),
        )))
    };
    if ["{{", "{%", "{#"].iter().any(|t| usage.source.contains(t)) {
        return Err(unresolved(
            "Jinja in an inline file source is not rendered here".to_string(),
        ));
    }
    let (stem, suffix) = stem_and_suffix(&usage.source);
    if ![".csv", ".json", ".parquet"].contains(&suffix.to_lowercase().as_str()) {
        return Err(unresolved(
            "an inline file source needs one of ['.csv', '.json', '.parquet']".to_string(),
        ));
    }
    let mut candidates: Vec<(&str, ProjectPath)> = Vec::new();
    let mut escape = None;
    for (label, dir) in [("board directory", anchor), ("project root", "")] {
        match ProjectPath::anchored(dir, &usage.source) {
            Ok(p) if !candidates.iter().any(|(_, c)| *c == p) => candidates.push((label, p)),
            Ok(_) => {}
            Err(e) => escape = Some(e),
        }
    }
    if let (true, Some(e)) = (candidates.is_empty(), escape) {
        return Err(unresolved(e.to_string()));
    }
    let existing: Vec<String> = candidates
        .iter()
        .filter(|(_, p)| files.exists(p))
        .map(|(label, p)| format!("{} ({label})", py_str_repr(p.as_str())))
        .collect();
    if existing.len() > 1 {
        return Err(HoistError::AmbiguousFile(Box::new(
            diag::err_file_source_ambiguous(
                usage.query.as_str(),
                usage.source.as_str(),
                existing.join(" and "),
            ),
        )));
    }
    match is_identifier(stem) {
        true => Ok(()),
        false => Err(HoistError::Message(format!(
            "Query {}: inline file source {} is invalid: files key {} is not a valid SQL identifier",
            py_str_repr(&usage.query),
            py_str_repr(&usage.source),
            py_str_repr(stem)
        ))),
    }
}

/// Refuses what Python's `normalize_query` refuses of these sources: an inline file source that
/// does not resolve, and (once the project declares sources, or an inline file source is in use)
/// a name no source declares.
pub(crate) fn check_sources(
    uses: &[SourceUse],
    registry: &SourceRegistry,
    files: &dyn ProjectFiles,
) -> Result<(), HoistError> {
    let mut filed = false;
    // A registry name that reads as a path is the registry's, never resolved (Python skips a ref
    // its registry already holds).
    let inline = |u: &&SourceUse| is_file_source_ref(&u.source) && !registry.0.contains(&u.source);
    for usage in uses.iter().filter(inline) {
        let unlocated = HoistError::UnresolvedReference(Box::new(diag::err_unresolved_reference(
            usage.source.as_str(),
            format!(
                " in query {} \u{2014} a board with no project location",
                py_str_repr(&usage.query)
            ),
        )));
        resolve_file_source(usage, usage.anchor.as_deref().ok_or(unlocated)?, files)?;
        filed = true;
    }
    if registry.0.is_empty() && !filed {
        return Ok(());
    }
    match uses
        .iter()
        .filter(|u| !inline(u))
        .find(|u| !registry.0.contains(&u.source))
    {
        None => Ok(()),
        Some(usage) => Err(HoistError::SourceNotFound(Box::new(
            diag::err_source_not_found(
                usage.query.as_str(),
                usage.source.as_str(),
                registry.0.iter().map(String::as_str).collect::<Vec<_>>(),
                "",
            ),
        ))),
    }
}

//! The project seam: the engine reads no disk and no env, a host supplies files synchronously
//! (decision D-11). An async host prefetches the closure [`refs`] names into a [`MemoryFiles`].

use board_model::board::theme::is_built_in;
use project::ProjectPath;
use std::collections::BTreeMap;
use yaml::{Kind, Value};

pub trait ProjectFiles {
    /// The text of a file, `None` when it does not exist (or is not UTF-8).
    fn read(&self, path: &ProjectPath) -> Option<String>;
    /// The files directly under `dir` (`""` is the project root).
    fn list(&self, dir: &str) -> Vec<ProjectPath>;
    /// Whether anything is at `path`, as Python's `Project.exists` says: a file of any content (a
    /// data file such as `d.parquet` is not UTF-8 text, so `read` cannot answer for it) or a
    /// directory. A host on a case-insensitive file system answers case-insensitively, as Python
    /// does there; a host that prefetches must too, or `D.csv` beside `sub/d.csv` is missed as
    /// ambiguous. Symlinks are followed: a dangling link is absent, a link to a directory is
    /// present.
    fn exists(&self, path: &ProjectPath) -> bool;
}

/// An in-memory project: what a host prefetches into, and what tests build.
#[derive(Debug, Default, Clone)]
pub struct MemoryFiles(BTreeMap<ProjectPath, String>);

impl MemoryFiles {
    pub fn insert(&mut self, path: ProjectPath, text: String) {
        self.0.insert(path, text);
    }
}

impl ProjectFiles for MemoryFiles {
    fn read(&self, path: &ProjectPath) -> Option<String> {
        self.0.get(path).cloned()
    }
    fn exists(&self, path: &ProjectPath) -> bool {
        let below = format!("{path}/");
        self.0.contains_key(path) || self.0.keys().any(|p| p.as_str().starts_with(&below))
    }
    fn list(&self, dir: &str) -> Vec<ProjectPath> {
        let prefix = if dir.is_empty() {
            String::new()
        } else {
            format!("{}/", dir.trim_end_matches('/'))
        };
        self.0
            .keys()
            .filter(|p| {
                p.as_str()
                    .strip_prefix(&prefix)
                    .is_some_and(|rest| !rest.contains('/'))
            })
            .cloned()
            .collect()
    }
}

/// A file a board's text names, for a host to fetch before compiling.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Ref {
    /// An `extends:` entry that is not a built-in theme: a relative path or a board name.
    Extends(String),
}

/// The refs of a board's text, in authored order. Text that does not parse names none: compiling
/// it reports why.
pub fn refs(text: &str) -> Vec<Ref> {
    let Ok(node) = yaml::parse_node(text) else {
        return Vec::new();
    };
    let Kind::Map(map) = &node.kind else {
        return Vec::new();
    };
    let Some(entry) = map.get("extends") else {
        return Vec::new();
    };
    let names: Vec<&str> = match &entry.value.kind {
        Kind::Scalar(Value::Str(s)) => vec![s.as_str()],
        Kind::Seq(items) => items
            .iter()
            .filter_map(|n| match &n.kind {
                Kind::Scalar(Value::Str(s)) => Some(s.as_str()),
                _ => None,
            })
            .collect(),
        _ => Vec::new(),
    };
    names
        .into_iter()
        .filter(|n| is_path_ref(n) || !is_built_in(n))
        .map(|n| Ref::Extends(n.to_string()))
        .collect()
}

/// `merge.is_path_ref`: a `/` or a `.yaml` / `.yml` suffix.
pub fn is_path_ref(entry: &str) -> bool {
    entry.contains('/') || entry.ends_with(".yaml") || entry.ends_with(".yml")
}

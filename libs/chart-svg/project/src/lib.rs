//! The project seam's vocabulary (spec § 2, § 9): [`ProjectPath`], a POSIX
//! project-relative file path.
//!
//! The newtype is built only through `TryFrom`, which applies the rules of
//! Python's `assert_relpath` (`core/project.py`) with the project root as anchor:
//! the same input is accepted or refused here and there, with the same message.
//! Anchoring a ref at a board's directory and `ProjectFiles` come with the fold.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

use diagnostic::py_str_repr;
use std::fmt;

/// Why a string is not a [`ProjectPath`]; `Display` is Python's message.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathError {
    /// Empty or only whitespace.
    Empty(String),
    /// Absolute under POSIX or Windows rules, or spelled with a backslash.
    NotRelative(String),
    /// Leaves the project root once `.` and `..` segments collapse.
    Escapes(String),
    /// Collapses to the project root itself: a directory, not a file (Python
    /// admits it, since the same function anchors directories).
    Root(String),
}

impl fmt::Display for PathError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Empty(r) => write!(f, "file ref must not be empty, got {}", py_str_repr(r)),
            Self::NotRelative(r) => write!(f, "file ref must be relative, got {}", py_str_repr(r)),
            Self::Escapes(r) => write!(
                f,
                "file ref {} would escape above the project root",
                py_str_repr(r)
            ),
            Self::Root(r) => write!(
                f,
                "file ref {} names the project root, not a file",
                py_str_repr(r)
            ),
        }
    }
}

impl std::error::Error for PathError {}

/// A normalized, project-relative POSIX file path: relative, no `.` or empty
/// segment, no `..` segment that climbs out of the project root, and not the
/// root itself (a path to a file).
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ProjectPath(String);

impl ProjectPath {
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// The last segment, e.g. `report.yml` for `charts/sub/report.yml`.
    pub fn file_name(&self) -> &str {
        self.0.rsplit('/').next().unwrap_or(&self.0)
    }
}

/// Python's `is_absolute_any_os`: a leading `/`, or a Windows drive (any
/// character before the second-position `:`, as `PureWindowsPath` reads it).
fn is_absolute_any_os(s: &str) -> bool {
    s.starts_with('/') || s.chars().nth(1) == Some(':')
}

impl ProjectPath {
    /// `assert_relpath(path, anchor)`: `path` joined onto the project-relative directory `anchor`
    /// (`""` or `"."` is the root) and normalized. `path` itself must be relative, so an absolute
    /// ref is refused rather than anchored.
    pub fn anchored(anchor: &str, path: &str) -> Result<Self, PathError> {
        if path.trim().is_empty() {
            return Err(PathError::Empty(path.to_string()));
        }
        if path.contains('\\') || is_absolute_any_os(path) {
            return Err(PathError::NotRelative(path.to_string()));
        }
        // `posixpath.normpath` on a relative path.
        let mut parts: Vec<&str> = Vec::new();
        let dir = if anchor == "." { "" } else { anchor };
        for seg in dir
            .split('/')
            .chain(path.split('/'))
            .filter(|s| !s.is_empty() && *s != ".")
        {
            if seg == ".." && parts.last().is_some_and(|p| *p != "..") {
                parts.pop();
            } else {
                parts.push(seg);
            }
        }
        let joined = parts.join("/");
        // Python's test is `startswith("..")`: a first segment such as `..x` is refused too.
        if joined.starts_with("..") {
            return Err(PathError::Escapes(path.to_string()));
        }
        if joined.is_empty() {
            return Err(PathError::Root(path.to_string()));
        }
        Ok(Self(joined))
    }
}

impl TryFrom<&str> for ProjectPath {
    type Error = PathError;

    fn try_from(path: &str) -> Result<Self, PathError> {
        Self::anchored("", path)
    }
}

impl TryFrom<String> for ProjectPath {
    type Error = PathError;
    fn try_from(path: String) -> Result<Self, PathError> {
        Self::try_from(path.as_str())
    }
}

impl AsRef<str> for ProjectPath {
    fn as_ref(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ProjectPath {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

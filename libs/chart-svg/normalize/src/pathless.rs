//! A board compiled from its text alone, with no project location (Python's `compile(yaml)` with no
//! `base_dir`, as opposed to `compile_file`): no `meta.yml` cascade, no relative reference, and the
//! host's defaults for everything else.
//!
//! The compile is of a private board name that only this module's reader serves, and the files
//! report themselves [`ProjectFiles::located`] false: findings name no file or range, and no query
//! anchors an inline file source. Every other file the project holds is out of reach: a `meta.yml` is not read (so
//! nothing cascades), the project's `dbt_charts.yml` is (the host's own configuration, not a
//! reference), so are the `templates/<name>.svg.j2` a custom chart names (Python reads them from the
//! project root), and a read of anything else is a reference the board has no location to resolve.
//! Python refuses that as `a board with no project location`, so a refusal of a reference after
//! such a read is that one, naming the reference as the author wrote it.

use crate::compile::{
    compile_or_refuse, wire_located, CompileError, CompiledBoard, HostInputs, Refusal,
};
use crate::files::{MemoryFiles, ProjectFiles, ReadError};
use crate::hoist::HoistError;
use crate::FoldError;
use diagnostic::diag;
use project::ProjectPath;
use std::cell::RefCell;

const NO_LOCATION: &str = " \u{2014} a board with no project location";

/// The name the board text compiles under; it is never a file of the project.
const BOARD: &str = ".pathless.yml";

struct Pathless<'a> {
    inner: &'a dyn ProjectFiles,
    text: &'a str,
    board: ProjectPath,
    /// The directory the board's relative references resolve from, when the host names one: the
    /// project then answers every read but a `meta.yml`, since the board sits at no file.
    anchor: Option<String>,
    /// Files the board reached for that a pathless board cannot have, in the order asked.
    unreachable: RefCell<Vec<String>>,
}

impl Pathless<'_> {
    fn is_meta(path: &ProjectPath) -> bool {
        matches!(
            path.as_str().rsplit('/').next(),
            Some("meta.yml" | "meta.yaml")
        )
    }

    fn is_config(path: &ProjectPath) -> bool {
        path.as_str() == "dbt_charts.yml"
    }

    /// A custom chart's `template:` file, `templates/<name>.svg.j2` with `name` possibly in a
    /// subfolder: Python reads it from the project root, whatever the board's own location, so a
    /// pathless board reads it too (and nothing else of `templates/`).
    fn is_template(path: &ProjectPath) -> bool {
        path.as_str()
            .strip_prefix("templates/")
            .is_some_and(|name| name.ends_with(".svg.j2"))
    }

    /// Whether `path` is out of reach, noting it when it is a reference rather than the cascade.
    fn out_of_reach(&self, path: &ProjectPath) -> bool {
        if *path == self.board || Self::is_config(path) || Self::is_template(path) {
            return false;
        }
        if self.anchor.is_some() {
            return Self::is_meta(path);
        }
        if !Self::is_meta(path) {
            self.unreachable
                .borrow_mut()
                .push(path.as_str().to_string());
        }
        true
    }
}

impl ProjectFiles for Pathless<'_> {
    fn read(&self, path: &ProjectPath) -> Result<Option<String>, ReadError> {
        if *path == self.board {
            return Ok(Some(self.text.to_string()));
        }
        if self.out_of_reach(path) {
            return Ok(None);
        }
        self.inner.read(path)
    }

    fn list(&self, dir: &str) -> Result<Vec<ProjectPath>, ReadError> {
        if dir == "templates" {
            self.inner.list(dir)
        } else {
            Ok(Vec::new())
        }
    }

    fn exists(&self, path: &ProjectPath) -> Result<bool, ReadError> {
        if *path == self.board {
            return Ok(true);
        }
        if self.out_of_reach(path) {
            return Ok(false);
        }
        self.inner.exists(path)
    }

    fn located(&self) -> bool {
        false
    }

    fn anchor(&self) -> Option<&str> {
        self.anchor.as_deref()
    }
}

fn view<'a>(
    inner: &'a dyn ProjectFiles,
    text: &'a str,
    anchor: Option<&str>,
) -> Result<(Pathless<'a>, ProjectPath), CompileError> {
    let name = anchor.map_or_else(|| BOARD.to_string(), |dir| format!("{dir}/{BOARD}"));
    let board = ProjectPath::try_from(name.as_str())
        .map_err(|e| CompileError::Invalid(vec![diag::err_internal(format!("{name}: {e}"))]))?;
    let files = Pathless {
        inner,
        text,
        board: board.clone(),
        anchor: anchor.map(String::from),
        unreachable: RefCell::default(),
    };
    Ok((files, board))
}

/// `text` compiled as a board with no project location, against `files` for the project's own
/// configuration only, or, with an `anchor` directory, for every file but a `meta.yml` (the board
/// has no cascade, but its relative references resolve from `anchor`); a refusal keeps the
/// migration notices that preceded it.
pub fn compile_text_or_refuse(
    files: &dyn ProjectFiles,
    text: &str,
    host: &HostInputs,
    anchor: Option<&str>,
) -> Result<CompiledBoard, Box<Refusal>> {
    let (pathless, board) = view(files, text, anchor).map_err(|error| {
        Box::new(Refusal {
            error,
            warnings: Vec::new(),
        })
    })?;
    compile_or_refuse(&pathless, &board, host).map_err(|mut refusal| {
        // Only a refusal of a reference the board tried to read is that reference having no
        // location to resolve from; any other refusal stands.
        let reference = match &refusal.error {
            CompileError::Hoist(HoistError::UnresolvedReference(d)) => d.params.get("ref"),
            CompileError::Fold(FoldError::Diagnostic(d)) if d.code == "ERR-EXTENDS-UNRESOLVED" => {
                d.params.get("entry")
            }
            _ => None,
        };
        if let (Some(name), false) = (
            reference.and_then(serde_json::Value::as_str),
            pathless.unreachable.borrow().is_empty(),
        ) {
            refusal.error = CompileError::Hoist(HoistError::UnresolvedReference(Box::new(
                diag::err_unresolved_reference(name, NO_LOCATION),
            )));
        }
        refusal
    })
}

/// The refusal of a [`compile_text_or_refuse`] in the wire shape, `(diagnostics, warnings)`, its
/// ranges located in `text`.
pub fn wire_refusal(
    refusal: &Refusal,
    text: &str,
    today: migrate::Date,
) -> Result<(Vec<serde_json::Value>, Vec<serde_json::Value>), CompileError> {
    let only_the_board = MemoryFiles::default();
    let (files, board) = view(&only_the_board, text, None)?;
    Ok((
        refusal.error.wire_diagnostics(&files, &board, today),
        wire_located(refusal.warnings.clone(), &files, &board, today),
    ))
}

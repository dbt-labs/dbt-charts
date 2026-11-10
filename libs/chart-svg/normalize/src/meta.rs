//! A board's merged patch: the `meta.yml` chain root to leaf, the board's `extends:` chain, then
//! the board's own fields (`merged_patch` in the board-compile lane).

use crate::extends::{parent, split_own, Cx, FoldError, Patch};
use crate::files::ProjectFiles;
pub use crate::merge_warnings::{MergeWarning, RestatedError};
use migrate::Date;
use project::ProjectPath;

/// The fold of one board, with what the compile lane needs beside the patch.
#[derive(Debug, Clone, PartialEq)]
pub struct Merged {
    pub patch: Patch,
    /// The built-in theme names met in the metas, then the board's `extends:`, in the order met;
    /// the last is the effective theme.
    pub themes: Vec<String>,
    /// Each file's own patch, low to high: a meta's `extends:` fragments before the meta, the
    /// board's fragments last. The board's own patch is not among them.
    pub layers: Vec<(ProjectPath, Patch)>,
    pub notices: Vec<crate::extends::Notice>,
    /// The layout and heading-margin warnings of the fold, in Python's order.
    pub warnings: Vec<MergeWarning>,
    own_cascaded: Patch,
    own_extends: Vec<String>,
    meta_themes: usize,
}

impl Merged {
    /// WARN-RESTATED-STYLE for the board's own file: its theme and its cascaded keys that the
    /// theme and the inherited layers already supply. `default_theme` is the host's.
    pub fn restated(&self, default_theme: &str) -> Result<Vec<MergeWarning>, RestatedError> {
        crate::merge_warnings::restated(
            &self.own_cascaded,
            &self.own_extends,
            &self.layers,
            &self.themes,
            self.meta_themes,
            default_theme,
        )
    }
}

const PACKAGES: &str = "dbt_packages";
const HOST_CHARTS: &str = "charts";

fn meta_in(files: &dyn ProjectFiles, dir: &str) -> Option<(ProjectPath, String)> {
    ["meta.yml", "meta.yaml"].into_iter().find_map(|name| {
        let at = ProjectPath::anchored(dir, name).ok()?;
        files.read(&at).map(|text| (at, text))
    })
}

/// `find_meta_files`: the `meta.yml` (else `meta.yaml`) of each directory from `boards_root` down to
/// the board's, root first, each with its text. A package board (under `dbt_packages/`) also gets
/// the host's `charts/meta.yml`, above the metas the walk found outside the package and below the
/// ones inside it. A board directory outside `boards_root` is an error, as in Python.
pub fn find_meta_files(
    board_dir: &str,
    boards_root: &str,
    files: &dyn ProjectFiles,
) -> Result<Vec<(ProjectPath, String)>, FoldError> {
    let root = if boards_root == "." { "" } else { boards_root };
    let under = root.is_empty() || board_dir == root || board_dir.starts_with(&format!("{root}/"));
    if !under {
        return Err(FoldError::NotUnderRoot {
            dir: board_dir.to_string(),
            root: boards_root.to_string(),
        });
    }
    let mut found = Vec::new();
    let mut dir = board_dir;
    loop {
        if let Some(meta) = meta_in(files, dir) {
            found.push(meta);
        }
        if dir == root || dir.is_empty() {
            break;
        }
        dir = dir.rsplit_once('/').map_or("", |(up, _)| up);
    }
    found.reverse();
    if board_dir.split('/').next() == Some(PACKAGES) {
        let host_layer = found
            .iter()
            .filter(|(p, _)| p.as_str().split('/').next() != Some(PACKAGES))
            .count();
        if let Some(host) = meta_in(files, HOST_CHARTS) {
            found.insert(host_layer, host);
        }
    }
    Ok(found)
}

/// `merged_patch`: `board` (the board file's parsed patch) folded over its metas and its
/// `extends:` chain. `boards_root` bounds the meta walk and anchors a plain board name (`""` or
/// `"."` is the project root).
pub fn merged_patch(
    board: Patch,
    board_path: &ProjectPath,
    boards_root: &str,
    files: &dyn ProjectFiles,
    today: Date,
) -> Result<Merged, FoldError> {
    let root = if boards_root == "." { "" } else { boards_root };
    let mut cx = Cx::new(files, root, today);
    let mut base = Patch::default();
    for (meta, text) in find_meta_files(parent(board_path), root, files)? {
        let (own, entries) = cx.parse(&meta, &text, true)?;
        let mut folded = cx.entries(&entries, &meta, parent(&meta), &mut Vec::new())?;
        cx.out.layers.push((meta, own.clone()));
        folded.merge(own);
        base.merge(folded);
    }
    let meta_themes = cx.out.themes.len();
    let (own, entries) = split_own(board);
    let own_cascaded = crate::merge_warnings::cascaded(&own);
    let mut patch = cx.entries(&entries, board_path, parent(board_path), &mut Vec::new())?;
    let warnings = crate::merge_warnings::of_fold(&cx.out.layers, &own);
    patch.merge(own);
    base.merge(patch);
    let out = cx.out;
    Ok(Merged {
        patch: base,
        themes: out.themes,
        layers: out.layers,
        notices: out.notices,
        warnings,
        own_cascaded,
        own_extends: entries,
        meta_themes,
    })
}

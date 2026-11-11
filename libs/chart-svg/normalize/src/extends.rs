//! The `extends:` resolver: `merge_extends` in its board-compile lane. A built-in theme entry is
//! recorded, not folded (the effective theme is applied once, below everything); a path, a board
//! name or a retired theme name loads a fragment, whose own `extends:` resolves from its directory.
//! A retired theme name is redirected unless a project board shadows it, a fragment's relative
//! paths anchor at its own directory, and every fragment goes migrate, then parse; `id`,
//! `aliases` and `_schema_version` are not inherited. `tests/test_normalize_extends_fixture.py`
//! is its oracle.

use crate::files::{is_path_ref, ProjectFiles};
use board_model::board::theme::{is_built_in, BASE_THEME};
use board_model::board::Board;
use board_model::names::{ThemeName, WarningCode};
use diagnostic::{codes, diag, py_str_repr, Diagnostic, Level};
use migrate::{read_board, Date, THEME_RENAMES};
use model_core::{parse_patch, Leaf, Tri};
use project::ProjectPath;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;
use yaml::{Kind, Node, Value};

pub type Patch = <Board as Leaf>::Patch;

/// A migration or rename applied in memory, and the file whose YAML was stale.
#[derive(Debug, Clone, PartialEq)]
pub struct Notice {
    pub file: ProjectPath,
    pub message: String,
}

/// The fold of one board's `extends:` entries.
#[derive(Debug, Clone, PartialEq)]
pub struct Folded {
    /// The entries folded lowest priority first.
    pub patch: Patch,
    /// The built-in theme names met, in the order met.
    pub themes: Vec<String>,
    /// Each fragment's own patch, a fragment's `extends:` before it.
    pub layers: Vec<(ProjectPath, Patch)>,
    /// Author-facing notices (a retired theme name resolved in memory).
    pub notices: Vec<Notice>,
    /// The `lint:` directives of the `meta.yml` chain met.
    pub lint: MetaLint,
}

/// The lint suppressions of a `meta.yml` chain (`resolve_meta_lint`): the union of every meta's
/// `lint.ignore`, and of its `lint.ignore_queries` per query name. A leaf adds codes; it never
/// removes one.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct MetaLint {
    pub ignore: BTreeSet<String>,
    pub ignore_queries: BTreeMap<String, BTreeSet<String>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FoldError {
    /// A registered diagnostic: an unresolvable entry, a missing file.
    Diagnostic(Diagnostic),
    /// The chain returns to a fragment it is already folding.
    Circular(ProjectPath),
    /// The board's directory is not under the boards root that bounds the meta walk.
    NotUnderRoot { dir: String, root: String },
    /// A fragment that does not parse as YAML, or is not a board patch.
    Invalid {
        fragment: ProjectPath,
        first: String,
    },
}

impl FoldError {
    pub(crate) fn diagnostics(&self) -> Vec<Diagnostic> {
        vec![match self {
            Self::Diagnostic(d) => d.clone(),
            Self::Circular(p) => diag::err_extends_cycle(p.to_string()),
            Self::NotUnderRoot { dir, root } => {
                diag::err_meta_outside_boards_root(dir.as_str(), root.as_str())
            }
            Self::Invalid { fragment, first } => {
                diag::err_extends_invalid(fragment.to_string(), first.as_str())
            }
        }]
    }
}

impl fmt::Display for FoldError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Diagnostic(d) => f.write_str(&d.message().unwrap_or_else(|_| d.code.to_string())),
            Self::Circular(_) | Self::NotUnderRoot { .. } | Self::Invalid { .. } => {
                self.diagnostics().iter().try_for_each(|d| match d.message() {
                    Ok(text) => f.write_str(&text),
                    Err(_) => f.write_str(d.code),
                })
            }
        }
    }
}

impl std::error::Error for FoldError {}

pub(crate) struct Cx<'a> {
    files: &'a dyn ProjectFiles,
    /// Where a plain board name is looked up (`""` is the project root).
    boards_root: &'a str,
    today: Date,
    pub(crate) out: Folded,
}

/// Fold `entries`, the `extends:` of the board file `board`, over `files`.
pub fn fold_extends(
    entries: &[String],
    board: &ProjectPath,
    boards_root: &str,
    files: &dyn ProjectFiles,
    today: Date,
) -> Result<Folded, FoldError> {
    let mut cx = Cx::new(files, boards_root, today);
    cx.out.patch = cx.entries(entries, board, parent(board), &mut Vec::new())?;
    Ok(cx.out)
}

impl<'a> Cx<'a> {
    pub(crate) fn new(files: &'a dyn ProjectFiles, boards_root: &'a str, today: Date) -> Self {
        Self {
            files,
            boards_root,
            today,
            out: Folded {
                patch: Patch::default(),
                themes: Vec::new(),
                layers: Vec::new(),
                notices: Vec::new(),
                lint: MetaLint::default(),
            },
        }
    }

    pub(crate) fn entries(
        &mut self,
        entries: &[String],
        file: &ProjectPath,
        dir: &str,
        chain: &mut Vec<ProjectPath>,
    ) -> Result<Patch, FoldError> {
        let mut acc = Patch::default();
        for entry in entries {
            acc.merge(self.entry(entry, file, dir, chain)?);
        }
        Ok(acc)
    }

    fn entry(
        &mut self,
        entry: &str,
        file: &ProjectPath,
        dir: &str,
        chain: &mut Vec<ProjectPath>,
    ) -> Result<Patch, FoldError> {
        if !is_path_ref(entry) {
            if let Some((_, to)) = THEME_RENAMES.iter().find(|(from, _)| *from == entry) {
                if self.named_board(entry).is_none() {
                    self.out.notices.push(Notice {
                        file: file.clone(),
                        message: format!(
                            "dbt charts resolved retired `extends:` theme name {} to {} in memory. \
                             `dct migrate` does not rewrite this position \u{2014} update the YAML by hand.",
                            py_str_repr(entry),
                            py_str_repr(to)
                        ),
                    });
                    return self.entry(to, file, dir, chain);
                }
            }
            if entry == BASE_THEME {
                return Err(FoldError::Diagnostic(diag::err_unknown_theme(
                    entry,
                    ThemeName::VALUES.to_vec(),
                )));
            }
            if is_built_in(entry) {
                self.out.themes.push(entry.to_string());
                return Ok(Patch::default());
            }
        }
        let (path, text) = self.fragment(entry, dir)?;
        if chain.contains(&path) {
            return Err(FoldError::Circular(path));
        }
        let (own, inner) = self.parse(&path, &text, false)?;
        chain.push(path.clone());
        let folded = self.entries(&inner, &path, parent(&path), chain);
        chain.pop();
        let mut patch = folded?;
        self.out.layers.push((path, own.clone()));
        patch.merge(own);
        Ok(patch)
    }

    /// The board a plain name names at the boards root: `<name>.yaml`, else `<name>.yml`.
    fn named_board(&self, name: &str) -> Option<(ProjectPath, String)> {
        ["yaml", "yml"].into_iter().find_map(|ext| {
            let path =
                ProjectPath::try_from(join(self.boards_root, &format!("{name}.{ext}")).as_str())
                    .ok()?;
            self.files.read(&path).map(|t| (path, t))
        })
    }

    fn fragment(&self, entry: &str, dir: &str) -> Result<(ProjectPath, String), FoldError> {
        if is_path_ref(entry) {
            let anchor = if dir.is_empty() { "." } else { dir };
            let unresolved = |why: String| {
                FoldError::Diagnostic(diag::err_unresolved_reference(
                    entry,
                    format!(" in {} \u{2014} {why}", py_str_repr(anchor)),
                ))
            };
            let path = ProjectPath::anchored(dir, entry).map_err(|e| unresolved(e.to_string()))?;
            return match self.files.read(&path) {
                Some(text) => Ok((path, text)),
                None => Err(unresolved(format!(
                    "{} does not exist",
                    py_str_repr(path.as_str())
                ))),
            };
        }
        self.named_board(entry).ok_or_else(|| {
            FoldError::Diagnostic(diag::err_extends_unresolved(
                entry,
                ThemeName::VALUES.to_vec(),
            ))
        })
    }

    /// `_parse_meta_dict`'s `lint:` block: validated as Python does, then unioned into the chain's.
    fn read_lint(&mut self, path: &ProjectPath, lint: &Node) -> Result<(), FoldError> {
        let refuse = |message: String| FoldError::Diagnostic(diag::err_internal(message));
        let json = lint.to_json();
        if json.is_null()
            || json == false
            || json == 0
            || json == ""
            || json == serde_json::json!([])
        {
            return Ok(());
        }
        let Some(block) = json.as_object() else {
            let kind = match &json {
                serde_json::Value::Array(_) => "list",
                serde_json::Value::String(_) => "str",
                serde_json::Value::Bool(_) => "bool",
                serde_json::Value::Number(n) if n.is_f64() => "float",
                _ => "int",
            };
            return Err(refuse(format!(
                "{path}: 'lint' must be a mapping, got {kind}"
            )));
        };
        let codes = |source: String, value: Option<&serde_json::Value>| {
            let Some(value) = value else {
                return Ok(BTreeSet::new());
            };
            let listed = value
                .as_array()
                .map(|a| a.iter().map(|c| c.as_str()).collect::<Option<Vec<_>>>())
                .unwrap_or(None)
                .ok_or_else(|| {
                    refuse(format!("{path}: {source} must be a list of warning codes"))
                })?;
            listed
                .into_iter()
                .map(|code| {
                    suppressible(code)
                        .map(|()| code.to_string())
                        .map_err(|why| refuse(format!("{path}: {source}: {why}")))
                })
                .collect()
        };
        let ignore = codes("lint.ignore".to_string(), block.get("ignore"))?;
        let mut per_query = BTreeMap::new();
        if let Some(queries) = block.get("ignore_queries") {
            let queries = queries.as_object().ok_or_else(|| {
                refuse(format!(
                    "{path}: lint.ignore_queries must be a mapping of query name to codes"
                ))
            })?;
            for (name, list) in queries {
                let source = format!("lint.ignore_queries[{}]", py_str_repr(name));
                per_query.insert(name.clone(), codes(source, Some(list))?);
            }
        }
        let chain = &mut self.out.lint;
        chain.ignore.extend(ignore);
        for (name, added) in per_query {
            chain.ignore_queries.entry(name).or_default().extend(added);
        }
        Ok(())
    }

    /// A fragment's own patch (no `extends`, no `id`) and its `extends:` entries.
    /// `meta` drops the `lint:` directive first, as `load_meta_file` does: it is policy, not board
    /// content.
    pub(crate) fn parse(
        &mut self,
        path: &ProjectPath,
        text: &str,
        meta: bool,
    ) -> Result<(Patch, Vec<String>), FoldError> {
        let invalid = |first: String| FoldError::Invalid {
            fragment: path.clone(),
            first,
        };
        let node = match yaml::parse_node(text) {
            Ok(n) => n,
            Err(d) if d.iter().all(|d| d.code == "ERR-EMPTY-YAML-DOCUMENT") => {
                return Ok((Patch::default(), Vec::new()));
            }
            Err(d) => {
                return Err(invalid(
                    d.iter().map(|d| d.code).collect::<Vec<_>>().join(", "),
                ))
            }
        };
        let mut node = node;
        if meta {
            if let Kind::Map(m) = &mut node.kind {
                if let Some(lint) = m.shift_remove("lint") {
                    self.read_lint(path, &lint.value)?;
                }
            }
        }
        let migrated = read_board(node, self.today);
        self.out
            .notices
            .extend(migrated.notices.into_iter().map(|message| Notice {
                file: path.clone(),
                message,
            }));
        let mut node = migrated.node;
        anchor_images(&mut node, parent(path)).map_err(FoldError::Diagnostic)?;
        crate::shape::refuse(&node, true).map_err(|d| invalid(d.code.to_string()))?;
        crate::names::refuse(&node, true).map_err(|d| invalid(d.code.to_string()))?;
        let parsed = parse_patch::<Board>(&node, None);
        if let Some(e) = parsed.errors.first() {
            return Err(invalid(format!("{} at {:?}", e.diagnostic.code, e.path())));
        }
        let (own, entries) = split_own(parsed.twin);
        Ok((own, entries))
    }
}

/// A board or fragment patch without what is not inherited (`id`, `aliases`, the schema version,
/// `extends`, `theme`), and its `extends:` entries.
pub(crate) fn split_own(mut own: Patch) -> (Patch, Vec<String>) {
    let mut entries = match std::mem::take(&mut own.body.extends) {
        Tri::Value(e) => e.0,
        Tri::Unset | Tri::Null => Vec::new(),
    };
    // `theme: x` is `extends: x`; setting both is refused when the file is read.
    if let Tri::Value(theme) = std::mem::take(&mut own.body.theme) {
        entries.push(theme.as_str().to_string());
    }
    own.body.id = Default::default();
    own.aliases = Default::default();
    own._schema_version = Default::default();
    (own, entries)
}

pub(crate) fn parent(path: &ProjectPath) -> &str {
    path.as_str().rsplit_once('/').map_or("", |(dir, _)| dir)
}

fn join(dir: &str, rel: &str) -> String {
    if dir.is_empty() || dir == "." {
        rel.to_string()
    } else {
        format!("{dir}/{rel}")
    }
}

const IMAGE_SUFFIXES: [&str; 7] = [".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"];

/// `file_refs.anchor_images`: respell, in place, each relative project image in the markdown of
/// `node` (a fragment written in `dir`) from `charts/`, so it names the same file wherever the
/// markdown is inherited to.
fn anchor_images(node: &mut Node, dir: &str) -> Result<(), Diagnostic> {
    match &mut node.kind {
        Kind::Map(m) => {
            if matches!(
                m.get("ref").map(|e| &e.value.kind),
                Some(Kind::Scalar(Value::Str(_)))
            ) {
                return Ok(());
            }
            let keys: &[&str] = match m.get("type").map(|e| &e.value.kind) {
                None => &["title", "text"],
                Some(Kind::Scalar(Value::Str(t))) if t == "callout" => &["title", "message"],
                Some(_) => &[],
            };
            for (k, e) in m.iter_mut() {
                match &mut e.value.kind {
                    Kind::Scalar(Value::Str(s)) if keys.contains(&k.as_str()) => {
                        *s = anchored(s, dir)?;
                    }
                    _ => anchor_images(&mut e.value, dir)?,
                }
            }
        }
        Kind::Seq(items) => {
            for item in items {
                anchor_images(item, dir)?;
            }
        }
        Kind::Scalar(_) => {}
    }
    Ok(())
}

/// A project image destination: not a remote origin or `data:` URI, and not built by a template.
fn written_image_refs(markdown: &str) -> Vec<String> {
    let mut seen = Vec::new();
    for url in mdsvg::types::image_urls(&mdsvg::parse(markdown)) {
        let lower = url.to_lowercase();
        let remote = ["http://", "https://", "//", "data:"]
            .iter()
            .any(|p| lower.starts_with(p));
        if !remote && !url.contains("{{") && !url.contains("{%") && !seen.iter().any(|s| s == url) {
            seen.push(url.to_string());
        }
    }
    seen
}

fn anchored(markdown: &str, dir: &str) -> Result<String, Diagnostic> {
    let mut out = markdown.to_string();
    for r in written_image_refs(markdown) {
        if r.starts_with('/') {
            continue;
        }
        let anchor = if dir.is_empty() { "." } else { dir };
        let fail = |why: String| {
            diag::err_unresolved_reference(
                r.as_str(),
                format!(" in {} \u{2014} {why}", py_str_repr(anchor)),
            )
        };
        let name = r
            .trim_end_matches('/')
            .rsplit('/')
            .next()
            .unwrap_or_default();
        let suffix = match name.rsplit_once('.') {
            Some((stem, ext)) if !stem.is_empty() && !ext.is_empty() => {
                format!(".{}", ext.to_lowercase())
            }
            _ => String::new(),
        };
        if !IMAGE_SUFFIXES.contains(&suffix.as_str()) {
            let listed: Vec<String> = IMAGE_SUFFIXES.iter().map(|s| py_str_repr(s)).collect();
            return Err(fail(format!(
                "unsupported image suffix; use one of [{}]",
                listed.join(", ")
            )));
        }
        let path = ProjectPath::anchored(dir, &r).map_err(|e| fail(e.to_string()))?;
        let Some(rest) = path.as_str().strip_prefix("charts/") else {
            return Err(fail(format!(
                "{} is outside 'charts', so markdown inheriting it cannot name it",
                py_str_repr(path.as_str())
            )));
        };
        // Only where the destination ends at whitespace or `)`, as the parse found it.
        let from = format!("]({r}");
        let to = format!("](/{rest}");
        let mut res = String::new();
        let mut tail = out.as_str();
        while let Some(at) = tail.find(&from) {
            let after = &tail[at + from.len()..];
            res.push_str(&tail[..at]);
            let hit = after
                .chars()
                .next()
                .is_some_and(|c| c.is_whitespace() || c == ')');
            res.push_str(if hit { &to } else { &from });
            tail = after;
        }
        res.push_str(tail);
        out = res;
    }
    Ok(out)
}

/// `validate_suppression_codes` for one code: a registered warning code (a retired one stays
/// accepted), else the reason it cannot be named.
fn suppressible(code: &str) -> Result<(), String> {
    if WarningCode::VALUES.contains(&code) {
        return Ok(());
    }
    if codes::lookup(code).is_some_and(|c| c.level == Level::Error) {
        return Err(format!(
            "cannot suppress {} \u{2014} it is an error code, not a warning code.",
            py_str_repr(code)
        ));
    }
    let registered: Vec<&str> = codes::ALL
        .iter()
        .filter(|c| c.level == Level::Warning)
        .map(|c| c.id)
        .collect();
    Err(format!(
        "unknown warning code {}. Registered warning codes: {}",
        py_str_repr(code),
        registered.join(", ")
    ))
}

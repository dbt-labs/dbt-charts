//! `compile`: a board file to a [`Structure`] (the passes before style) and, once the style seam
//! exists, a [`CompiledBoard`] (spec § 11, phase 5, slice 5A).
//!
//! The passes: read, migrate, `shape::refuse` (each file's own `tabs:` and `grid:` as authored,
//! before the fold), parse (`FromNode`), the meta and `extends:` fold, and the `LayoutFacts` over
//! the merged root. `Structure` is the model-derive `Patch<Board>` (ids filled by `fill_ids` from
//! `LayoutFacts::board_ids`, which Python's `board.id` checks) plus the themes, notices, facts
//! and the files read, not a second board type; `hoist_charts` then moves what is inline into its
//! maps.
//!
//! [`compile`] finishes with the root style: the effective theme's (the host's `default_theme`
//! when none is named) under the board's own, which `family_reaches_recipe` first lays its pie over
//! its donut and its bar over its funnel, with its color tokens bound to the theme under the board's `palettes:` and
//! `roles:` (`Resolved::base_in`; `CompiledBoard.style`).
//!
//! `tests/test_engine_compile_live.py` is the live gate: `_engine.compile` (the host's sqlglot
//! analyzer, a filesystem reader, a project declaring a source per dialect) against `compile_file`
//! on a fixed sample of the boards `tests/fixtures/accepted_boards.txt` lists (a static list of the
//! boards whose full build matched Python's compile), plus synthetic SQL boards for each dialect
//! and the parse-error and mutating paths: the same refusal codes, the same ported diagnostics in
//! order, the same public ids. `ENGINE_GATE_FULL=1` runs the whole corpus (about a minute) and
//! prints the open boards.

use crate::cache::QueryPolicy;
use crate::chart_checks::chart_checks;
use crate::deps::dependencies;
use crate::dialect_layer_checks::{dialect_mixed, layer_color_tokens};
use crate::extends::{FoldError, MetaLint, Notice, Patch as LayerPatch};
use crate::external::{External, Imports};
use crate::files::{ProjectFiles, ReadError};
use crate::fill::{self, fill_nested, fill_queries, FillError};
use crate::focus::focus;
use crate::format_checks::format_checks;
use crate::hoist::{check_layers, hoist_charts, HoistError};
use crate::ids::fill_ids;
use crate::index::{BoardIndex, FactsError, LayoutFacts};
use crate::lint::lint;
use crate::meta::merged_patch;
use crate::query_lints::query_lints;
use crate::scope::{cascade_diagnostics, install, nested_styles, ScopeError};
use crate::sources::{SourceRegistry, SourceUse};
use crate::sql::{analyze_queries, QueryFacts};
use board_model::board::theme::{built_in_theme, is_built_in, BASE_THEME};
use board_model::board::{Board, HtmlPolicy};
use board_model::cache::CacheLayer;
use board_model::cascade;
use board_model::chart::ChartAuthored;
use board_model::ids::{ChartId, ItemId, QueryId};
use board_model::names::ThemeName;
use board_model::nested::family_reaches_recipe;
use board_model::sql_analysis::SqlAnalysis;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use indexmap::IndexMap;
use migrate::{read_board, Date};
use model_core::{parse_patch, CascadeError, Leaf, Resolved, Tri};
use project::ProjectPath;
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

pub(crate) type Patch = <Board as Leaf>::Patch;

/// A project that remembers which files were asked for: the closure a host caches by and an async
/// host prefetches.
struct Recording<'a> {
    inner: &'a dyn ProjectFiles,
    read: RefCell<BTreeSet<ProjectPath>>,
}

impl ProjectFiles for Recording<'_> {
    fn read(&self, path: &ProjectPath) -> Result<Option<String>, ReadError> {
        self.read.borrow_mut().insert(path.clone());
        self.inner.read(path)
    }
    fn list(&self, dir: &str) -> Result<Vec<ProjectPath>, ReadError> {
        self.inner.list(dir)
    }
    fn exists(&self, path: &ProjectPath) -> Result<bool, ReadError> {
        self.read.borrow_mut().insert(path.clone());
        self.inner.exists(path)
    }
    fn located(&self) -> bool {
        self.inner.located()
    }
    fn anchor(&self) -> Option<&str> {
        self.inner.anchor()
    }
}

/// The board after the passes that need no style.
#[derive(Debug, Clone, PartialEq)]
pub struct Structure {
    /// The board folded over its `meta.yml` chain and `extends:`.
    pub patch: Patch,
    /// The built-in themes met, in order; the last is the effective theme.
    pub themes: Vec<String>,
    pub layout: LayoutFacts,
    /// What the SQL seam said about each SQL query (values and schema queries have none).
    pub queries: BTreeMap<QueryId, QueryFacts>,
    /// What each query of the root's map resolves to, by key.
    pub policies: BTreeMap<String, QueryPolicy>,
    /// Every non-fatal finding, in [`crate::locate::canonical_order`]. An error never lands here:
    /// it refuses the board.
    pub diagnostics: Vec<Diagnostic>,
    pub index: BoardIndex,
    /// Where migration wrote into the board file (key paths).
    pub(crate) migrated: Vec<Vec<String>>,
    /// The variables each chart and declared variable depends on, before any `chart_focus`.
    pub deps: crate::deps::Dependencies,
    /// The board's own file: the one whose queries a finding names bare (a board with no project
    /// location holds its virtual one).
    pub file: ProjectPath,
    /// The board file the diagnostics' ranges index into.
    pub source: crate::locate::Source,
    /// The host's project source declarations, as passed to compile.
    pub project_sources: BTreeMap<String, serde_json::Value>,
    /// The file sources the queries name inline (`source: data/x.csv`), by the ref as authored.
    pub inline_sources: BTreeMap<String, serde_json::Value>,

    /// Findings only `dct validate` reports; compile accepts the board ([`chart_checks`]).
    pub validate_only: Vec<Diagnostic>,
    /// The variables a `variables:` ref brought in from another file.
    pub imported_variables: BTreeSet<String>,
    /// Every file read to get here.
    pub files: BTreeSet<ProjectPath>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum CompileError {
    /// The board file is not in the project.
    Missing(ProjectPath),
    /// The text is not YAML the engine reads.
    Syntax(Vec<Diagnostic>),
    /// The YAML is not a board.
    Invalid(Vec<Diagnostic>),
    Fold(FoldError),
    Facts(FactsError),
    Hoist(HoistError),
    Fill(FillError),
    /// An authoring lint, which Python refuses the board for. `context` leads the message when
    /// the code's own template does not name the query.
    Sql {
        context: String,
        diagnostic: Box<Diagnostic>,
    },
    /// A statement outside the read-only (`sql`) or setup (`setup_sql`) allowlist, worded as
    /// Python's `MutatingSqlError` words it.
    MutatingSql {
        query: String,
        setup: bool,
        node_kind: String,
        fragment_preview: String,
    },
}

/// A syntax error names the file it is in as `source`, as Python's compile does for the text it
/// was given.
pub(crate) fn named_source(mut found: Vec<Diagnostic>, name: &str) -> Vec<Diagnostic> {
    for d in &mut found {
        if d.params.get("source").and_then(serde_json::Value::as_str) == Some(yaml::UNNAMED_SOURCE)
        {
            d.params.insert("source", name.into());
        }
    }
    found
}

impl Structure {
    /// The non-fatal findings in Python's wire shape, ranges resolved against the board file.
    pub fn wire_diagnostics(&self) -> Vec<serde_json::Value> {
        let source = &self.source;
        self.diagnostics
            .iter()
            .map(|d| crate::locate::wire(d, source))
            .collect()
    }
}

/// `diagnostics` in the wire shape a warning has: a `path` resolved to a line/column `range` in
/// the board file at `path`, read from its own text. With no board text to read, no range.
pub fn wire_located(
    mut diagnostics: Vec<Diagnostic>,
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    today: Date,
) -> Vec<serde_json::Value> {
    // A read failure leaves the finding without a range.
    let text = files.read(path).ok().flatten().unwrap_or_default();
    let node = match yaml::parse_node(&text) {
        Ok(node) => {
            let node = read_board(node, today).node;
            crate::locate::stamp(&mut diagnostics, &node, &text);
            node
        }
        Err(_) => yaml::Node {
            span: migrate::SYNTHETIC,
            kind: yaml::Kind::Map(Default::default()),
        },
    };
    let source = crate::locate::Source {
        file: files.located().then(|| path.clone()),
        text,
        node,
    };
    crate::locate::canonical_order(&mut diagnostics);
    diagnostics
        .iter()
        .map(|d| crate::locate::wire(d, &source))
        .collect()
}

impl Structure {
    /// [`Self::validate_only`] in Python's wire shape, as [`Self::wire_diagnostics`] does.
    pub fn wire_validate_only(&self) -> Vec<serde_json::Value> {
        self.validate_only
            .iter()
            .map(|d| crate::locate::wire(d, &self.source))
            .collect()
    }
}

impl CompileError {
    /// A refusal no registered code classifies, as Python reports one it raises without a code:
    /// `ERR-INTERNAL` carrying the text.
    pub fn internal(message: impl Into<String>) -> Self {
        Self::Invalid(vec![diag::err_internal(message.into())])
    }

    /// The refusal's diagnostics in the wire shape a warning has: a `path` resolved to a
    /// line/column `range` in the board file, from the file's own text. A refusal with no board
    /// text to read carries no range. A span a parse took from a file the board imports indexes
    /// that file, so only a syntax error in the board itself keeps its own range; every other
    /// range is dropped and located again by its path, against the board.
    pub fn wire_diagnostics(
        &self,
        files: &dyn ProjectFiles,
        path: &ProjectPath,
        today: Date,
    ) -> Vec<serde_json::Value> {
        let mut diagnostics = self.diagnostics();
        if !matches!(self, Self::Syntax(_)) {
            for d in &mut diagnostics {
                d.range = None;
                d.related.iter_mut().for_each(|r| r.range = None);
            }
        }
        // A fragment's finding sits in the fragment's own file.
        let path = match self {
            Self::Fold(FoldError::Invalid { fragment, .. }) => fragment,
            _ => path,
        };
        wire_located(diagnostics, files, path, today)
    }

    /// The registered diagnostics of the refusal; never empty (a refusal with no code of its own
    /// is `ERR-INTERNAL`), so a host never receives a refusal it cannot report.
    pub fn diagnostics(&self) -> Vec<Diagnostic> {
        match self {
            Self::Missing(p) => vec![diag::err_file_not_found(p.to_string())],
            Self::Syntax(d) | Self::Invalid(d) => d.clone(),
            Self::Sql { diagnostic, .. } => vec![(**diagnostic).clone()],
            Self::Fold(e) => e.diagnostics(),
            Self::Facts(e) => e.diagnostics(),
            Self::Hoist(e) => e.diagnostics(),
            Self::Fill(e) => e.diagnostics(),
            Self::MutatingSql {
                node_kind,
                fragment_preview,
                ..
            } => vec![diag::err_mutating_sql(
                node_kind.as_str(),
                fragment_preview.as_str(),
            )],
        }
    }
}

impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Missing(p) => write!(f, "{p} does not exist"),
            Self::Syntax(d) | Self::Invalid(d) => {
                let each: Vec<String> = d
                    .iter()
                    .map(|d| d.message().unwrap_or_else(|_| d.code.to_string()))
                    .collect();
                write!(f, "{}", each.join("; "))
            }
            Self::Fold(e) => e.fmt(f),
            Self::Facts(e) => e.fmt(f),
            Self::Hoist(e) => e.fmt(f),
            Self::Fill(e) => e.fmt(f),
            Self::Sql {
                context,
                diagnostic,
            } => match diagnostic.message() {
                Ok(m) => write!(f, "{context}{m}"),
                Err(_) => write!(f, "{context}{}", diagnostic.code),
            },
            Self::MutatingSql {
                query,
                setup,
                node_kind,
                fragment_preview,
            } => {
                let (label, policy, body) = match setup {
                    true => (
                        " setup_sql",
                        "setup_sql",
                        "  Allowed: CREATE TEMP FUNCTION / TEMP TABLE / TEMP VIEW (any dialect) and CREATE [OR REPLACE] MACRO (DuckDB).",
                    ),
                    false => (
                        "",
                        "read-only SQL",
                        "  Allowed: SELECT, WITH, UNION, INTERSECT, EXCEPT, DESCRIBE, SHOW, EXPLAIN of an allowed inner statement.",
                    ),
                };
                write!(
                    f,
                    "Query '{query}'{label}: dct refuses to execute SQL outside the {policy} allowlist.\n\n  Statement: {node_kind}\n  Preview:   {fragment_preview}\n\n{body}\n  See dbt_charts/core/compile/sql_guard.py for the full allowlist."
                )
            }
        }
    }
}

impl std::error::Error for CompileError {}

impl From<FoldError> for CompileError {
    fn from(e: FoldError) -> Self {
        Self::Fold(e)
    }
}

impl From<FillError> for CompileError {
    fn from(e: FillError) -> Self {
        Self::Fill(e)
    }
}

impl From<HoistError> for CompileError {
    fn from(e: HoistError) -> Self {
        Self::Hoist(e)
    }
}

impl From<crate::files::ReadError> for CompileError {
    fn from(e: crate::files::ReadError) -> Self {
        Self::Invalid(vec![e.diagnostic()])
    }
}

impl From<FactsError> for CompileError {
    fn from(e: FactsError) -> Self {
        Self::Facts(e)
    }
}

/// What the host supplies to a compile, beside the board's files and path.
#[derive(Clone, Copy)]
pub struct HostInputs<'a> {
    /// Where a plain board name in `extends:` is looked up (`"."` is the project root).
    pub boards_root: &'a str,
    pub today: Date,
    /// The built-in theme a board uses when neither it nor a `meta.yml` names one; the host reads
    /// it from its configuration. Only [`compile`] reads it.
    pub default_theme: &'a str,
    /// The tier the host pins in its environment (Cloud's `DCT_HTML_POLICY_CEILING`); it wins
    /// outright, and only without it does the project's `dbt_charts.yml` set the ceiling.
    pub html_policy_ceiling: Option<HtmlPolicy>,
    pub sql: &'a dyn SqlAnalysis,
    /// The host's project source registry, the dbt profile's implicit entry included
    /// ([`SourceRegistry::empty`] for a project with neither). A SQL query naming no source takes
    /// the board's own default, else the registry's implicit one.
    pub sources: &'a SourceRegistry,
    /// The project's `cache:` block, the root of every query's cache cascade; the host reads it
    /// from `dbt_charts.yml` (over its shipped default) and has no silent default to supply.
    pub cache_root: &'a CacheLayer,
    /// The `cache:` layer each declared source authors, by source name.
    pub source_caches: &'a BTreeMap<String, CacheLayer>,
    /// The user named the board file directly, not by walking a directory. Only a markdown board
    /// reads it: a named file with a chart fence is a board without a `board:` key.
    pub named: bool,
}

/// A board folded over its `meta.yml` chain and `extends:` and hoisted: what every later pass starts from.
struct Folded<'a> {
    files: Recording<'a>,
    patch: Patch,
    /// The board file's own `id:`, which the fold drops.
    own_id: Option<board_model::ids::BoardId>,
    notices: Vec<String>,
    merge_notices: Vec<Notice>,
    /// WARN-RESTATED-STYLE of the board's own file (Python's merge-time restated findings).
    restated: Vec<Diagnostic>,
    themes: Vec<String>,
    lint: MetaLint,
    /// The effective theme: the last one the fold met, else the host's default.
    theme: String,
    own_format_names: BTreeSet<String>,
    /// The restated `input` and `data_type` of the board file's own variables: a variable a
    /// `meta.yml` or extends layer brings is not one this file can edit.
    own_variables: Vec<Diagnostic>,
    /// Variables a `variables:` ref brought in: another file's to judge, not this one's.
    imported_variables: BTreeSet<String>,
    index: BoardIndex,
    /// Where migration wrote into the document: restated checks judge only what the user wrote.
    migrated: Vec<Vec<String>>,
    /// The board file's text and its node as migration left it, which findings' ranges index into.
    text: String,
    as_written: yaml::Node,
    /// The placed boards that are long prose, by position.
    prose_imports: BTreeSet<ItemId>,
    inline_sources: BTreeMap<String, serde_json::Value>,
}

/// What search and listings read of a board: its index and the files read to get it. It stops
/// before the SQL seam, the lints and the style, none of which the index depends on, so a board
/// whose SQL does not parse or is not a select is still indexed.
pub fn compile_index(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
) -> Result<(BoardIndex, BTreeSet<ProjectPath>), CompileError> {
    let folded = fold(files, path, host, false, &mut Vec::new())?;
    Ok((folded.index, folded.files.read.into_inner()))
}

fn fold<'a>(
    files: &'a dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
    restated: bool,
    gathered: &mut Vec<Diagnostic>,
) -> Result<Folded<'a>, CompileError> {
    let HostInputs {
        boards_root,
        today,
        sources,
        named,
        ..
    } = *host;
    let files = Recording {
        inner: files,
        read: RefCell::default(),
    };
    let text = files
        .read(path)?
        .ok_or_else(|| CompileError::Missing(path.clone()))?;
    let markdown = crate::markdown::is_markdown(path);
    let node = if markdown {
        crate::markdown::read(&text, path, named)
    } else {
        yaml::parse_node(&text)
    }
    .map_err(|found| match files.located() {
        true => CompileError::Syntax(named_source(found, path.as_str())),
        false => CompileError::Syntax(found),
    })?;
    let migrated = read_board(node, today);
    gathered.extend(migrated.notices.iter().map(|message| {
        schema_migrated(
            &Notice {
                file: path.clone(),
                message: message.clone(),
            },
            files.located(),
        )
    }));
    let migrated_paths = migrated_paths(&migrated.edits);
    let mut node = migrated.node;
    let as_written = node.clone();
    let mut imports = Imports::default();
    External {
        files: &files,
        today,
    }
    .resolve_refs(&mut node, path, &mut imports)?;
    crate::shape::refuse(&node, true).map_err(|d| CompileError::Invalid(vec![d]))?;
    crate::names::refuse(&node, true).map_err(|d| CompileError::Invalid(vec![d]))?;
    let broken = crate::constraints::refuse(&node);
    let retired = crate::retired::RetiredKeys::of(&node);
    let parsed = parse_patch::<Board>(&node, Some(&retired));
    // A model's own rules wait for its fields to parse: Python asks them after validation.
    let parse_paths: Vec<String> = parsed.errors.iter().map(|e| e.path()).collect();
    let mut invalid: Vec<Diagnostic> = parsed
        .errors
        .into_iter()
        .map(|e| crate::extra_field::special(e.into_diagnostic(), &as_written))
        .collect();
    invalid.extend(broken.into_iter().filter(|d| {
        let at = d.path.as_ref().map(ToString::to_string).unwrap_or_default();
        // Python rewrites `theme:` before it validates a field, so that refusal always stands.
        d.code == "ERR-THEME-AND-EXTENDS"
            || !parse_paths
                .iter()
                .any(|q| q == &at || at.is_empty() || q.starts_with(&format!("{at}.")))
    }));
    refuse_rect_strokes(&node, &mut invalid);
    if !invalid.is_empty() {
        return Err(CompileError::Invalid(invalid));
    }
    // The fold drops a board's `id:` (a template's is not inherited), but the board file's own
    // names the root, and the hidden variables built on it.
    let own_id = parsed.twin.body.id.clone();
    let own_variables = crate::restated_variables::variables(&parsed.twin, &imports.variables);
    let own_format_names = crate::board_warnings::own_format_names(&parsed.twin);
    let merged = merged_patch(parsed.twin, path, boards_root, &files, today)?;
    gathered.extend(merged.notices.iter().map(|n| schema_migrated(n, files.located())));
    check_layers(&merged.layers)?;
    if merged.layers.iter().any(|(_, l)| layer_sets_rect_stroke(l)) {
        return Err(CompileError::Invalid(rect_stroke_refusal()));
    }
    let theme = merged
        .themes
        .last()
        .map_or(host.default_theme, String::as_str)
        .to_string();
    // The compile report carries the board's own file's restatements; an index has no use for them.
    let restated = if restated {
        merged
            .restated(host.default_theme)
            .map_err(|e| CompileError::Invalid(vec![diag::err_internal(e.to_string())]))?
            .into_iter()
            .map(|w| w.diagnostic)
            .filter(|d| !written_by_migration(d, &migrated_paths))
            .collect()
    } else {
        Vec::new()
    };
    let mut patch = merged.patch;
    if markdown {
        crate::markdown::with_document_style(&mut patch);
    }
    if !crate::hoist::has_content!(patch.body) {
        return Err(CompileError::Invalid(vec![diag::err_validation_field(
            "",
            crate::hoist::NO_CONTENT,
        )]));
    }
    if let Some(layout) = &mut patch.body.layout {
        let external = External {
            files: &files,
            today,
        };
        crate::board_files::inline(
            layout,
            &ItemId::root(),
            &mut vec![path.clone()],
            &external,
            &mut imports,
        )?;
    }
    let own_queries: BTreeSet<String> = patch.body.queries.keys().cloned().collect();
    let own_charts: BTreeSet<String> = patch.body.charts.keys().cloned().collect();
    let (mut index, inline_sources) =
        hoist_charts(&mut patch, path, &files, today, &imports, sources)?;
    index.facts = crate::search::board_facts(&patch, path, &own_queries, &own_charts);
    Ok(Folded {
        files,
        patch,
        own_id,
        notices: migrated.notices,
        merge_notices: merged.notices,
        restated,
        themes: merged.themes,
        lint: merged.lint,
        theme,
        own_format_names,
        own_variables,
        imported_variables: imports.variables,
        index,
        migrated: migrated_paths,
        text,
        as_written,
        prose_imports: imports.prose,
        inline_sources,
    })
}

/// The board at `path`, through the passes that need no style.
pub fn compile_structure(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
) -> Result<Structure, CompileError> {
    let mut structure = structure_found(files, path, host, &mut Vec::new())?;
    crate::locate::canonical_order(&mut structure.diagnostics);
    Ok(structure)
}

fn structure_found(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
    gathered: &mut Vec<Diagnostic>,
) -> Result<Structure, CompileError> {
    let HostInputs {
        html_policy_ceiling,
        sources,
        sql,
        today,
        cache_root,
        source_caches,
        ..
    } = *host;
    let Folded {
        files,
        mut patch,
        own_id,
        notices,
        merge_notices,
        restated,
        themes,
        lint: meta_lint,
        theme,
        own_format_names,
        own_variables,
        imported_variables,
        index,
        migrated,
        text,
        as_written,
        prose_imports,
        inline_sources,
    } = fold(files, path, host, true, gathered)?;
    fill_queries(&mut patch)?;
    let policies = crate::cache::policies(&patch, &index, cache_root, source_caches)?;
    let (queries, analysis) = analyze_queries(&patch, path, sql)?;
    dialect_mixed(&patch)?;
    let mut diagnostics = own_variables;
    diagnostics.extend(analysis);
    let ceiling = match html_policy_ceiling {
        Some(pinned) => pinned,
        None => fill::html_policy_ceiling(&files)?,
    };
    let requested = patch.body.html_policy;
    fill_nested(&mut patch, ceiling)?;
    diagnostics.extend(
        requested
            .zip(patch.body.html_policy)
            .and_then(|(req, eff)| {
                crate::board_warnings::html_policy_capped(req, eff, html_policy_ceiling.is_some())
            }),
    );
    patch.body.id = own_id;
    let mut layout = LayoutFacts::of_patch(&patch, path, &files, today)?;
    layout.prose_imports.extend(prose_imports);
    let deps = dependencies(&patch, path, &queries, &index);
    if let Some(cycle) = deps.cycle() {
        return Err(CompileError::Invalid(vec![diag::err_variable_cycle(
            cycle.join(" \u{2192} "),
        )]));
    }
    let declared: BTreeSet<String> = deps.variables.keys().cloned().collect();
    let unknown = crate::undeclared_variables::check(&patch, &queries, &index, &declared, &layout)?;
    if !unknown.is_empty() {
        return Err(CompileError::Invalid(unknown));
    }
    let focused = matches!(patch.chart_focus, Tri::Value(_));
    let facts = crate::board_warnings::Facts {
        theme: &theme,
        public_ids: &index.public_ids,
        own_format_names,
        text_queries: &index.text_queries,
    };
    diagnostics.extend(lint(
        &patch,
        path,
        &layout.charts,
        &layout.prose_imports,
        focused,
        &queries,
        &facts,
    ));
    diagnostics.extend(source_zone_warnings(&patch, sources));
    if focus(&mut patch, &index, &deps)? {
        // The focused board is rows of one chart, so its facts are read again; the root keeps the
        // id and heading level the board as written gave it, which Python stored before narrowing.
        let root = ItemId::root();
        let (id, level) = (
            layout.board_ids.remove(&root),
            layout.structural_title_level.remove(&root),
        );
        layout = LayoutFacts::of_patch(&patch, path, &files, today)?;
        layout.board_ids.extend(id.map(|id| (root.clone(), id)));
        layout
            .structural_title_level
            .extend(level.map(|level| (root, level)));
    }
    let public = |key: &str| {
        let (_, public) = index.public_ids.iter().find(|(id, _)| id.as_str() == key)?;
        Some(public.clone())
    };
    if let Some(found) = crate::palette_checks::palette_checks(&patch, &public) {
        return Err(CompileError::Invalid(vec![found]));
    }
    let found = chart_checks(&patch, &|key| public_id(&index, key))?;
    if !found.errors.is_empty() {
        return Err(CompileError::Invalid(found.errors));
    }
    diagnostics.extend(found.warnings);
    fill_ids(&mut patch, &layout)?;
    crate::custom::check(&patch.body.charts, &index.public_ids, &files)?;
    let query_findings = query_lints(&patch, path, &layout.charts, &queries, &meta_lint);
    let mut diagnostics: Vec<Diagnostic> = notices
        .into_iter()
        .map(|message| Notice {
            file: path.clone(),
            message,
        })
        .chain(merge_notices)
        .map(|n| schema_migrated(&n, files.located()))
        .chain(restated)
        .chain(diagnostics)
        .chain(query_findings)
        .collect();
    if crate::markdown::is_markdown(path) {
        let untitled = !matches!(&patch.body.title, Tri::Value(t) if !t.as_str().is_empty());
        crate::markdown::without_section_heading_warnings(&mut diagnostics, untitled);
    }
    crate::locate::stamp(&mut diagnostics, &as_written, &text);
    let located = files.located();
    let read = files.read.into_inner();
    Ok(Structure {
        patch,
        themes,
        layout,
        queries,
        policies,
        diagnostics,
        index,
        deps,
        file: path.clone(),
        source: crate::locate::Source {
            file: located.then(|| path.clone()),
            text,
            node: as_written,
        },
        project_sources: sources.declarations().clone(),
        inline_sources,
        validate_only: found.validate_only,
        migrated,
        imported_variables,
        files: read,
    })
}

/// The paths whose value migration produced: a written value, or a moved one that landed
/// different from what the author typed. A rename or identity respelling keeps the author's value.
fn migrated_paths(edits: &[migrate::Edit]) -> Vec<Vec<String>> {
    use migrate::{Edit, Seg};
    let segs = |path: &[Seg]| -> Vec<String> {
        path.iter()
            .map(|s| match s {
                Seg::Key(k) => k.clone(),
                Seg::Index(i) => i.to_string(),
            })
            .collect()
    };
    let mut out: Vec<Vec<String>> = edits
        .iter()
        .filter_map(|e| match e {
            Edit::Move {
                to, before, after, ..
            } if after.as_ref() != Some(before) => Some(segs(to)),
            Edit::Write { path, .. } => Some(segs(path)),
            _ => None,
        })
        .collect();
    // The `theme:` key is the board's `extends:` entry as the fold reads it.
    if out.iter().any(|p| p.as_slice() == ["theme"]) {
        out.push(vec!["extends".to_string()]);
    }
    out
}

/// A restated finding at or under a value migration produced: the author never typed it, so it
/// is not theirs to delete.
pub(crate) fn written_by_migration(d: &Diagnostic, migrated: &[Vec<String>]) -> bool {
    d.path
        .as_ref()
        .is_some_and(|p| migrated.iter().any(|m| p.0.starts_with(m)))
}

/// Python's rect stroke patch lacks `cap` and `join`, which the theme-filled stroke carries, so it
/// refuses a global `style.charts.marks.rect.stroke` with an unknown `cap` and `join`. Rust refuses
/// the same until Python's patch is fixed.
fn refuse_rect_strokes(node: &yaml::Node, errors: &mut Vec<Diagnostic>) {
    let yaml::Kind::Map(map) = &node.kind else {
        if let yaml::Kind::Seq(items) = &node.kind {
            items.iter().for_each(|n| refuse_rect_strokes(n, errors));
        }
        return;
    };
    for (key, entry) in map {
        if key == "style" && global_rect_stroke(&entry.value) {
            errors.extend(rect_stroke_refusal());
        }
        refuse_rect_strokes(&entry.value, errors);
    }
}

fn rect_stroke_refusal() -> Vec<Diagnostic> {
    ["cap", "join"].map(diag::err_extra_field).into()
}

fn layer_sets_rect_stroke(layer: &LayerPatch) -> bool {
    let Tri::Value(style) = &layer.body.style else {
        return false;
    };
    matches!(style.charts.marks.rect.stroke, Tri::Value(_))
}

fn global_rect_stroke(style: &yaml::Node) -> bool {
    ["charts", "marks", "rect", "stroke"]
        .iter()
        .try_fold(style, |node, key| match &node.kind {
            yaml::Kind::Map(m) => m.get(*key).map(|e| &e.value),
            _ => None,
        })
        .is_some()
}

/// The finished board.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledBoard {
    pub structure: Structure,
    /// The root board's style: the effective theme's under the board's own, with every color
    /// token and palette name resolved, the named fonts expanded and every inherited leaf filled.
    /// `style.total` is the value; the layers it came from stay for a nested scope to apply to.
    pub style: Resolved<Style>,
    /// Every nested board's and tab's style, by its position (`ItemId` printed form): the parent's
    /// authored style crossing in as `nested::in_force` lets it, over the same theme.
    pub nested: BTreeMap<String, Resolved<Style>>,
    /// The per-placement family style, keyed by position like `LayoutFacts.chart_facts` (Python
    /// compiles a copy of a chart per place): the chart's own `style:` folded, tokens resolved,
    /// over the scope the placement sits in. A chart that authored none holds its scope's style.
    pub placed: BTreeMap<String, Resolved<Style>>,
    /// The own tier of each placement, keyed like `placed`: the chart's own `style:` as a board-style
    /// layer with its color tokens resolved against the scope it sits in (empty for a chart that
    /// authored none). For render cascades that rank the chart's own above its family's and the
    /// board's, which the folded `placed` total cannot tell apart.
    pub placed_own: BTreeMap<String, <Style as Leaf>::Patch>,
    /// Each defined chart's style at the root scope, by the name it is defined under: what the
    /// built [`Board`] holds in the chart's `style`.
    pub charts: BTreeMap<String, Resolved<Style>>,
    /// The charts whose own `style:` the cascade refuses (an unknown font name or color token), each
    /// with its diagnostic: Python compiles such a board and the render fails only that chart. A
    /// failed chart holds its scope's style in `placed`, `placed_own` and `charts`, so every other
    /// reader sees a whole board; the host hands these to render as the chart's error.
    pub chart_failures: IndexMap<ChartId, Diagnostic>,
}

impl CompiledBoard {
    /// The total [`Board`]: the folded patch with every constant fill run and the root's resolved
    /// style in place of its authored layers. An `Err` names each leaf the passes before it left
    /// unset, by authored path.
    pub fn board(&self) -> Result<Board, model_core::Unset> {
        let mut patch = self.structure.patch.clone();
        patch.body.style = Tri::Value(self.style.inherited.clone());
        if let Some(layout) = &mut patch.body.layout {
            install(layout, &self.nested);
        }
        for (name, chart) in &mut patch.body.charts {
            if let (Some(chart), Some(scope)) = (chart, self.charts.get(name)) {
                chart.adopt_style(&scope.inherited);
            }
        }
        Board::try_from(patch)
    }
}

/// A migration notice, prefixed with its file unless the board has no project location.
fn schema_migrated(n: &Notice, located: bool) -> Diagnostic {
    match located {
        true => diag::warn_schema_migrated(format!("{}: {}", n.file, n.message)),
        false => diag::warn_schema_migrated(n.message.clone()),
    }
}

/// A refusal and the non-fatal findings gathered before it, in the order they were found.
#[derive(Debug, Clone, PartialEq)]
pub struct Refusal {
    pub error: CompileError,
    pub warnings: Vec<Diagnostic>,
}

/// [`compile`], keeping the migration notices a refusal was preceded by.
pub fn compile_or_refuse(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
) -> Result<CompiledBoard, Box<Refusal>> {
    let mut warnings = Vec::new();
    compile_found(files, path, host, &mut warnings)
        .map_err(|error| Box::new(Refusal { error, warnings }))
}

/// A board file to a [`CompiledBoard`].
pub fn compile(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
) -> Result<CompiledBoard, CompileError> {
    compile_found(files, path, host, &mut Vec::new())
}

fn compile_found(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
    gathered: &mut Vec<Diagnostic>,
) -> Result<CompiledBoard, CompileError> {
    let default_theme = host.default_theme;
    if default_theme == BASE_THEME || !is_built_in(default_theme) {
        return Err(CompileError::Fold(FoldError::Diagnostic(
            diag::err_invalid_default_theme(
                "the default theme",
                default_theme,
                ThemeName::VALUES.to_vec(),
            ),
        )));
    }
    let mut structure = structure_found(files, path, host, gathered)?;
    let style = root_style(&structure, default_theme)?;
    let theme = structure
        .themes
        .last()
        .map_or(default_theme, String::as_str);
    let root_own = match &structure.patch.body.style {
        Tri::Value(own) => Some((**own).clone()),
        Tri::Unset | Tri::Null => None,
    };
    let scopes = nested_styles(
        structure.patch.body.layout.as_ref(),
        root_own.as_ref(),
        theme,
    )
    .map_err(|e| match e {
        ScopeError::Theme(d) => CompileError::Fold(FoldError::Diagnostic(*d)),
        ScopeError::Invalid(d) => CompileError::Invalid(d),
    })?;
    let nested = scopes.styles;
    if let Some(found) = crate::palette_checks::role_checks(&style, &nested) {
        return Err(CompileError::Invalid(vec![found]));
    }
    let mut restated = crate::restated::tree(&structure, &style, &nested, &scopes.restated)?;
    restated.retain(|d| !written_by_migration(d, &structure.migrated));
    structure.diagnostics.extend(restated);
    crate::locate::stamp(
        &mut structure.diagnostics,
        &structure.source.node,
        &structure.source.text,
    );
    crate::locate::canonical_order(&mut structure.diagnostics);
    let mut chart_failures = IndexMap::new();
    let (placed, placed_own) = placed_styles(&structure, &style, &nested, &mut chart_failures)?;
    let merged = crate::constraints::refuse_merged(
        &structure,
        |pos| {
            innermost(nested.keys(), pos)
                .and_then(|key| nested.get(key))
                .unwrap_or(&style)
        },
        &placed_own,
    );
    if !merged.is_empty() {
        return Err(CompileError::Invalid(merged));
    }
    let owner = |name: &str| {
        let (pos, _) = structure
            .layout
            .charts
            .iter()
            .find(|(_, n)| n.as_str() == name)?;
        innermost(nested.keys(), pos.as_str()).cloned()
    };
    format_checks(
        &structure.patch,
        &style,
        &nested,
        &owner,
        &scopes.authored,
        &structure.index.query_boards,
        &|key| public_id(&structure.index, key),
    )?;
    let charts = chart_styles(&structure, &style, &mut chart_failures)?;
    let layer_tokens = layer_color_tokens(&structure, &placed)?;
    structure.validate_only.extend(layer_tokens);
    // Validate reports the error as the cascade raised it, without the render's attribution param.
    structure.validate_only.extend(chart_failures.values().cloned().map(|mut d| {
        d.params.remove("chart_id");
        d
    }));
    Ok(CompiledBoard {
        structure,
        style,
        nested,
        placed,
        placed_own,
        charts,
        chart_failures,
    })
}

/// The name a chart goes by wherever a user reads it (`BoardIndex::public_ids` holds every chart).
pub(crate) fn public_id(index: &BoardIndex, key: &str) -> Result<String, CompileError> {
    index
        .public_ids
        .iter()
        .find(|(id, _)| id.as_str() == key)
        .map(|(_, public)| public.clone())
        .ok_or_else(|| {
            CompileError::Invalid(vec![diag::err_internal(format!(
                "chart {key:?} has no public id"
            ))])
        })
}

/// A chart's own `style:` folded over `scope`: the scope itself when the chart authored none. A
/// color token no palette resolves is `ERR-COLOR-TOKEN` (`validate/chart_color_tokens.py`; Python
/// reports it from `dct validate` only, the engine refuses it here, before render).
fn fold_chart(
    name: &str,
    index: &BoardIndex,
    chart: &ChartAuthored,
    scope: &Resolved<Style>,
) -> Result<Resolved<Style>, CompileError> {
    let Some(layer) = chart.style_overlay() else {
        return Ok(scope.clone());
    };
    let refuse = |e: CascadeError| match public_id(index, name) {
        Ok(id) => CompileError::Invalid(cascade_diagnostics(&format!("chart '{id}'"), &e)),
        Err(internal) => internal,
    };
    scope.resolved_layer(&layer).map_err(refuse)?;
    scope.apply(&layer).map_err(refuse)
}

/// [`fold_chart`], where a refusal that lies in the chart's own style (an unknown font name or color
/// token, the two Python's render fails one chart for) is recorded in `failures` and the chart
/// takes its scope's style instead. Any other refusal still refuses the board.
fn fold_or_fail(
    name: &str,
    index: &BoardIndex,
    chart: &ChartAuthored,
    scope: &Resolved<Style>,
    failures: &mut IndexMap<ChartId, Diagnostic>,
) -> Result<Resolved<Style>, CompileError> {
    match fold_chart(name, index, chart, scope) {
        Err(CompileError::Invalid(found)) if chart_scoped(&found) => {
            fail_chart(name, index, found, failures)?;
            Ok(scope.clone())
        }
        folded => folded,
    }
}

fn chart_scoped(found: &[Diagnostic]) -> bool {
    !found.is_empty()
        && found
            .iter()
            .all(|d| matches!(d.code, "ERR-FONT-UNKNOWN" | "ERR-COLOR-TOKEN"))
}

/// Records the chart's first failure; a chart placed twice fails with the first placement's.
fn fail_chart(
    name: &str,
    index: &BoardIndex,
    found: Vec<Diagnostic>,
    failures: &mut IndexMap<ChartId, Diagnostic>,
) -> Result<(), CompileError> {
    let id = ChartId::try_from(name.to_string())
        .map_err(|e| CompileError::internal(format!("chart {name:?}: {e}")))?;
    if let Some(first) = found.into_iter().next() {
        let public = public_id(index, name)?;
        failures
            .entry(id)
            .or_insert_with(|| first.with_sink_param("chart_id", serde_json::json!(public)));
    }
    Ok(())
}

/// The innermost nested scope holding the position `pos`, among the scopes' positions `keys`.
fn innermost<'a>(keys: impl Iterator<Item = &'a String>, pos: &str) -> Option<&'a String> {
    keys.filter(|key| {
        pos.strip_prefix(key.as_str())
            .is_some_and(|r| r.starts_with('.'))
    })
    .max_by_key(|key| key.len())
}

/// Each placed chart's style and its own tier: the chart's own `style:` over the scope it sits in,
/// the innermost nested board or tab that holds its position, else the root.
#[allow(clippy::type_complexity)]
fn placed_styles(
    structure: &Structure,
    root: &Resolved<Style>,
    nested: &BTreeMap<String, Resolved<Style>>,
    failures: &mut IndexMap<ChartId, Diagnostic>,
) -> Result<
    (
        BTreeMap<String, Resolved<Style>>,
        BTreeMap<String, <Style as Leaf>::Patch>,
    ),
    CompileError,
> {
    let (mut placed, mut own) = (BTreeMap::new(), BTreeMap::new());
    for (pos, name) in &structure.layout.charts {
        let Some(Some(chart)) = structure.patch.body.charts.get(name) else {
            continue;
        };
        let scope = innermost(nested.keys(), pos.as_str()).map_or(root, |key| &nested[key]);
        placed.insert(
            pos.as_str().to_string(),
            fold_or_fail(name, &structure.index, chart, scope, failures)?,
        );
        let failed = failures.keys().any(|id| id.as_str() == name);
        let layer = match chart.style_overlay() {
            Some(_) if failed => Default::default(),
            Some(layer) => scope.resolved_layer(&layer).map_err(|e| {
                CompileError::Invalid(cascade_diagnostics(&format!("charts.{name}.style"), &e))
            })?,
            None => Default::default(),
        };
        own.insert(pos.as_str().to_string(), layer);
    }
    Ok((placed, own))
}

/// Each defined chart's style at the root scope.
fn chart_styles(
    structure: &Structure,
    root: &Resolved<Style>,
    failures: &mut IndexMap<ChartId, Diagnostic>,
) -> Result<BTreeMap<String, Resolved<Style>>, CompileError> {
    let mut out = BTreeMap::new();
    for (name, chart) in &structure.patch.body.charts {
        if let Some(chart) = chart {
            out.insert(
                name.clone(),
                fold_or_fail(name, &structure.index, chart, root, failures)?,
            );
        }
    }
    Ok(out)
}

/// The root's resolved style: the effective theme's style with the board's folded style over it,
/// through the generated cascade. Its color tokens bind to the theme under the board's own
/// `palettes:` and `roles:` (so `single_series[N]` indexes the theme's list, never the board's).
fn root_style(structure: &Structure, default_theme: &str) -> Result<Resolved<Style>, CompileError> {
    let theme = structure
        .themes
        .last()
        .map_or(default_theme, String::as_str);
    let built_in = built_in_theme(theme).map_err(|e| CompileError::internal(e.to_string()))?;
    let Tri::Value(base) = &built_in.style else {
        return Err(CompileError::internal(format!(
            "theme {theme} has no style"
        )));
    };
    let mut folded = (**base).clone();
    let mut context = folded.clone();
    if let Tri::Value(own) = &structure.patch.body.style {
        let mut own = (**own).clone();
        family_reaches_recipe(&mut own);
        context.palettes.extend(own.palettes.clone());
        context.roles.extend(own.roles.clone());
        board_model::palette::adopt_board_palette(&mut context, &own);
        folded.merge(own);
    }
    Resolved::base_in(&cascade::TABLES, folded, &context)
        .map_err(|e| CompileError::Invalid(cascade_diagnostics("style", &e)))
}

/// The warnings for a source that cannot honor the board's `regional.time_zone`.
fn source_zone_warnings(patch: &Patch, sources: &SourceRegistry) -> Vec<Diagnostic> {
    let zone = match &patch.regional {
        Tri::Value(r) => match &r.time_zone {
            Some(z) => z.as_str(),
            None => "UTC",
        },
        _ => "UTC",
    };
    let uses: Vec<SourceUse> = patch
        .body
        .queries
        .iter()
        .filter_map(|(key, q)| SourceUse::of(key, q, None))
        .collect();
    crate::time_zone::check(zone, &uses, sources)
}

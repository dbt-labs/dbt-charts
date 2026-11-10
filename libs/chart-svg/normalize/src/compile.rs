//! `compile`: a board file to a [`Structure`] (the passes before style) and, once the style seam
//! exists, a [`CompiledBoard`] (spec § 11, phase 5, slice 5A).
//!
//! The passes so far: read, migrate, parse (`FromNode`), the meta and `extends:` fold, the facts
//! of the layout. `Structure` is the model-derive patch form the fold already produces, not a
//! second board type; later passes fill its ids and hoist what is inline into its maps. The style
//! step needs the resolved `Style`, which waits for color tokens, so [`compile`] refuses there by
//! name and builds no stub.

use crate::deps::dependencies;
use crate::extends::{FoldError, Notice, Patch as LayerPatch};
use crate::external::{External, Imports};
use crate::files::ProjectFiles;
use crate::fill::{self, fill_nested, fill_queries, FillError};
use crate::focus::focus;
use crate::hoist::{check_layers, hoist_charts, HoistError};
use crate::ids::fill_ids;
use crate::index::{BoardIndex, FactsError, LayoutFacts};
use crate::lint::lint;
use crate::meta::merged_patch;
use crate::scope::{install, nested_styles};
use crate::sources::SourceRegistry;
use crate::sql::{analyze_queries, record_missing_columns, QueryFacts};
use board_model::board::theme::{built_in_theme, is_built_in, BASE_THEME};
use board_model::board::{Board, HtmlPolicy};
use board_model::cascade;
use board_model::chart::ChartAuthored;
use board_model::ids::{ItemId, QueryId};
use board_model::names::ThemeName;
use board_model::nested::pie_reaches_donut;
use board_model::sql_analysis::SqlAnalysis;
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic};
use migrate::{read_board, Date};
use model_core::{parse_patch, Leaf, Resolved, Tri};
use project::ProjectPath;
use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

type Patch = <Board as Leaf>::Patch;

/// A project that remembers which files were asked for: the closure a host caches by and an async
/// host prefetches.
struct Recording<'a> {
    inner: &'a dyn ProjectFiles,
    read: RefCell<BTreeSet<ProjectPath>>,
}

impl ProjectFiles for Recording<'_> {
    fn read(&self, path: &ProjectPath) -> Option<String> {
        self.read.borrow_mut().insert(path.clone());
        self.inner.read(path)
    }
    fn list(&self, dir: &str) -> Vec<ProjectPath> {
        self.inner.list(dir)
    }
    fn exists(&self, path: &ProjectPath) -> bool {
        self.read.borrow_mut().insert(path.clone());
        self.inner.exists(path)
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
    /// Every non-fatal finding, in Python's order: a `WARN-SCHEMA-MIGRATED` per migration applied
    /// in memory or retired theme name, then the rest (a parse error on a query the author named).
    /// An error never lands here: it refuses the board.
    pub diagnostics: Vec<Diagnostic>,
    pub index: BoardIndex,
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
    /// The analyzer did not answer every request of the compile.
    SqlAnswers {
        asked: usize,
        answered: usize,
    },
    /// The SQL analyzer refused a query, naming why.
    SqlRefused {
        query: String,
        why: String,
    },
    /// A built-in theme that cannot be loaded.
    Theme(String),
    /// The cascade refused the board's style (an unknown font or color, an unset leaf).
    Style(String),
    /// A feature the engine does not model yet, named.
    Refused(&'static str),
}

impl CompileError {
    /// The registered diagnostics the refusal carries; empty for one with no code.
    pub fn diagnostics(&self) -> Vec<Diagnostic> {
        match self {
            Self::Syntax(d) | Self::Invalid(d) => d.clone(),
            Self::Sql { diagnostic, .. } => vec![(**diagnostic).clone()],
            Self::Fold(e) => e.diagnostics(),
            Self::Hoist(e) => e.diagnostics(),
            _ => Vec::new(),
        }
    }
}

impl fmt::Display for CompileError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Missing(p) => write!(f, "{p} does not exist"),
            Self::Syntax(d) | Self::Invalid(d) => {
                let codes: Vec<&str> = d.iter().map(|d| d.code).collect();
                write!(f, "{}", codes.join(", "))
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
            Self::SqlAnswers { asked, answered } => {
                write!(
                    f,
                    "the SQL analyzer answered {answered} of {asked} requests"
                )
            }
            Self::SqlRefused { query, why } => write!(f, "query '{query}': {why}"),
            Self::Theme(why) | Self::Style(why) => write!(f, "{why}"),
            Self::Refused(what) => write!(f, "{what}"),
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
    /// ([`SourceRegistry::empty`] for a project with neither); a host default source (that
    /// implicit one) is not modeled, so a SQL query naming no source is refused.
    pub sources: &'a SourceRegistry,
}

/// The board at `path`, through the passes that need no style.
pub fn compile_structure(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
) -> Result<Structure, CompileError> {
    let HostInputs {
        boards_root,
        today,
        html_policy_ceiling,
        sql,
        sources,
        ..
    } = *host;
    let files = Recording {
        inner: files,
        read: RefCell::default(),
    };
    let text = files
        .read(path)
        .ok_or_else(|| CompileError::Missing(path.clone()))?;
    let node = yaml::parse_node(&text).map_err(CompileError::Syntax)?;
    let migrated = read_board(node, today);
    let mut node = migrated.node;
    let mut imports = Imports::default();
    External {
        files: &files,
        today,
    }
    .resolve_refs(&mut node, path, &mut imports)?;
    crate::shape::refuse(&node, true).map_err(|d| CompileError::Invalid(vec![d]))?;
    crate::names::refuse(&node, true).map_err(|d| CompileError::Invalid(vec![d]))?;
    let parsed = parse_patch::<Board>(&node, None);
    let mut invalid: Vec<Diagnostic> = parsed.errors.into_iter().map(|e| e.diagnostic).collect();
    refuse_rect_strokes(&node, &mut invalid);
    if !invalid.is_empty() {
        return Err(CompileError::Invalid(invalid));
    }
    // The fold drops a board's `id:` (a template's is not inherited), but the board file's own
    // names the root, and the hidden variables built on it.
    let own_id = parsed.twin.body.id.clone();
    let merged = merged_patch(parsed.twin, path, boards_root, &files, today)?;
    check_layers(&merged.layers)?;
    if merged.layers.iter().any(|(_, l)| layer_sets_rect_stroke(l)) {
        return Err(CompileError::Invalid(rect_stroke_refusal()));
    }
    let mut patch = merged.patch;
    if !crate::hoist::has_content!(patch.body) {
        return Err(CompileError::Invalid(vec![diag::err_validation_field(
            "",
            crate::hoist::NO_CONTENT,
        )]));
    }
    let index = hoist_charts(&mut patch, path, &files, today, &imports, sources)?;
    fill_queries(&mut patch)?;
    let (queries, mut diagnostics) = analyze_queries(&patch, path, sql)?;
    let ceiling = match html_policy_ceiling {
        Some(pinned) => pinned,
        None => fill::html_policy_ceiling(&files)?,
    };
    fill_nested(&mut patch, ceiling)?;
    patch.body.id = own_id;
    let mut layout = LayoutFacts::of_patch(&patch, path, &files, today)?;
    let deps = dependencies(&patch, path, &queries);
    if deps.cycle().is_some() {
        return Err(CompileError::Refused(
            "variables depend on each other in a cycle",
        ));
    }
    let focused = matches!(patch.chart_focus, Tri::Value(_));
    diagnostics.extend(lint(&patch, path, &layout.charts, focused, &queries));
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
    record_missing_columns(
        &mut patch,
        &queries,
        &layout.charts,
        &mut layout.chart_facts,
    );
    fill_ids(&mut patch, &layout)?;
    if patch
        .body
        .charts
        .values()
        .any(|c| matches!(c, Some(ChartAuthored::Custom(_))))
    {
        return Err(CompileError::Refused(
            "custom chart templates are not loaded yet",
        ));
    }
    let diagnostics: Vec<Diagnostic> = migrated
        .notices
        .into_iter()
        .map(|message| Notice {
            file: path.clone(),
            message,
        })
        .chain(merged.notices)
        .map(|n| diag::warn_schema_migrated(format!("{}: {}", n.file, n.message)))
        .chain(diagnostics)
        .collect();
    let read = files.read.into_inner();
    Ok(Structure {
        patch,
        themes: merged.themes,
        layout,
        queries,
        diagnostics,
        index,
        files: read,
    })
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

/// A board file to a [`CompiledBoard`].
pub fn compile(
    files: &dyn ProjectFiles,
    path: &ProjectPath,
    host: &HostInputs,
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
    let structure = compile_structure(files, path, host)?;
    let style = root_style(&structure, default_theme)?;
    let theme = structure
        .themes
        .last()
        .map_or(default_theme, String::as_str);
    let root_own = match &structure.patch.body.style {
        Tri::Value(own) => Some((**own).clone()),
        Tri::Unset | Tri::Null => None,
    };
    let nested = nested_styles(
        structure.patch.body.layout.as_ref(),
        root_own.as_ref(),
        theme,
    )
    .map_err(|e| CompileError::Style(e.to_string()))?;
    let (placed, placed_own) = placed_styles(&structure, &style, &nested)?;
    let charts = chart_styles(&structure, &style)?;
    Ok(CompiledBoard {
        structure,
        style,
        nested,
        placed,
        placed_own,
        charts,
    })
}

/// A chart's own `style:` folded over `scope`: the scope itself when the chart authored none.
fn fold_chart(
    name: &str,
    chart: &ChartAuthored,
    scope: &Resolved<Style>,
) -> Result<Resolved<Style>, CompileError> {
    let overlay = chart.style_overlay();
    match overlay {
        Some(layer) => scope
            .apply(&layer)
            .map_err(|e| CompileError::Style(format!("charts.{name}.style: {e}"))),
        None => Ok(scope.clone()),
    }
}

/// Each placed chart's style and its own tier: the chart's own `style:` over the scope it sits in,
/// the innermost nested board or tab that holds its position, else the root.
#[allow(clippy::type_complexity)]
fn placed_styles(
    structure: &Structure,
    root: &Resolved<Style>,
    nested: &BTreeMap<String, Resolved<Style>>,
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
        let scope = nested
            .iter()
            .filter(|(key, _)| {
                pos.as_str()
                    .strip_prefix(key.as_str())
                    .is_some_and(|r| r.starts_with('.'))
            })
            .max_by_key(|(key, _)| key.len())
            .map_or(root, |(_, scope)| scope);
        placed.insert(pos.as_str().to_string(), fold_chart(name, chart, scope)?);
        let layer = match chart.style_overlay() {
            Some(layer) => scope
                .resolved_layer(&layer)
                .map_err(|e| CompileError::Style(format!("charts.{name}.style: {e}")))?,
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
) -> Result<BTreeMap<String, Resolved<Style>>, CompileError> {
    let mut out = BTreeMap::new();
    for (name, chart) in &structure.patch.body.charts {
        if let Some(chart) = chart {
            out.insert(name.clone(), fold_chart(name, chart, root)?);
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
    let built_in = built_in_theme(theme).map_err(|e| CompileError::Theme(e.to_string()))?;
    let Tri::Value(base) = &built_in.style else {
        return Err(CompileError::Theme(format!("theme {theme} has no style")));
    };
    let mut folded = (**base).clone();
    let mut context = folded.clone();
    if let Tri::Value(own) = &structure.patch.body.style {
        let mut own = (**own).clone();
        pie_reaches_donut(&mut own);
        context.palettes.extend(own.palettes.clone());
        context.roles.extend(own.roles.clone());
        folded.merge(own);
    }
    Resolved::base_in(&cascade::TABLES, folded, &context)
        .map_err(|e| CompileError::Style(e.to_string()))
}

//! `BoardIndex`, first slice: the layout facts `bind` needs, read from the authored board before
//! ids are filled (whether an `id:` was written decides a variable's name).
//!
//! Names are Python's, exactly (`normalize/layout.py`). A board's Python id is the root's
//! `id` / slugified title / `untitled_board`, then `{parent}_{row|col|grid}{n}` for an item and
//! `{parent}_tab{n}` for a tab pane, whatever `id:` a nested board writes. The facts are keyed by
//! tree position, the YAML path of the board (`@`, `@rows[1]`, `@rows[1].tabs.items[0]`).

use crate::files::ProjectFiles;
use crate::rhythm;
use crate::slug::slugify;
use crate::variables::{default_of, Var, VariableFacts};
use board_model::board::layout::{
    LayoutAuthored, LayoutItemAuthored, NestedBoardAuthored, TabLayoutAuthored,
};
use board_model::board::{Board, BoardBodyAuthored};
use board_model::facet::DisplayText;
use board_model::ids::{BoardId, ChartId, ItemId};
use board_model::variable::{OptionsQuery, VariableAuthored};
use diagnostic::{diag, Diagnostic};
use migrate::{read_board, Date};
use model_core::{parse, Leaf, Tri};
use project::ProjectPath;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

/// What the board's charts carry that no single chart can know: a fact of the tree.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ChartFacts {
    /// The place of a single-series chart in the rhythm of the theme's single-series palette
    /// (render reads `palette[slot % len]`): charts that paint one ink, numbered in document
    /// order across the root's whole tree. `None` for a chart that does not paint one ink; it
    /// takes no slot and does not move the others. A chart authored with its own ink still
    /// holds its slot.
    pub rhythm_slot: Option<u32>,
    /// The columns the chart's channels name that its query's output columns do not list under any
    /// letter case (`str.lower()`, as the dbt and snowpark backends fold result columns; duckdb, sqlite
    /// and the driver backends keep the engine's case), so a column is recorded only when it is
    /// missing on every backend. Recorded
    /// only where the query's columns are known, for the families `rhythm::cartesian` reads; bind
    /// and validate decide what a missing column means.
    pub missing_columns: Vec<MissingColumn>,
}

/// A channel naming a column its query does not select.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissingColumn {
    /// `x`, `y` or `color`.
    pub channel: &'static str,
    pub column: String,
}

/// What users see of the board's charts, derived from the board and kept out of the spine.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BoardIndex {
    /// The id a chart goes by wherever a user reads it (SVG `data-chart-id`, `dct describe`,
    /// a diagnostic's `chart_id`, search; those readers take it from here as they move onto the
    /// spine), by the chart's [`ChartId`] (a name, or a position for an
    /// inline chart): a named chart keeps its name, an inline chart's is slugged from its title
    /// (a KPI's label), made unique within its board, else its item id ([`crate::public_id`]).
    pub public_ids: BTreeMap<ChartId, String>,
    /// The charts declared below the root board or in an imported file, by their key: Python
    /// resolves what they read against the board or file that declares them.
    pub declared_below: BTreeSet<String>,
}

/// A tabs layout: the variable that selects a tab, each tab's slug and the one open on load.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TabsFacts {
    pub variable: String,
    pub slugs: Vec<String>,
    pub default_tab: usize,
}

/// A collapsible nested board: the variable that holds its state and whether it starts open.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DetailsFacts {
    pub variable: String,
    pub expanded: bool,
}

/// Layout facts by board position: `tabs` by the board that owns the tabs layout, `details` by
/// the nested board that carries `details:`.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct LayoutFacts {
    pub tabs: BTreeMap<ItemId, TabsFacts>,
    pub details: BTreeMap<ItemId, DetailsFacts>,
    /// The heading level of every board's title by the board's position (the root, each nested or
    /// imported board, each tab pane): the count of titled ancestors plus its own, as Python's
    /// `parent_level + (1 if title else 0)`. A text-only or empty tab pane sits one level below
    /// its tabs board, and a pane with a layout of its own at its level. It is structural: an
    /// integer `style.title.level` overrides it, and layout applies that from the board's resolved
    /// style (`style.title.level.unwrap_or(structural_title_level)`).
    pub structural_title_level: BTreeMap<ItemId, u32>,
    /// The variables each board declares that its variables bar shows (`visible`, the default), in
    /// declaration order, by the declaring board's position. A board with none has no entry. The
    /// tab and details variables Python generates are hidden and live in `tabs` and `details`.
    pub variables: BTreeMap<ItemId, Vec<VariableFacts>>,
    /// The id Python gives every board by its position: the root's `id` / slugified title /
    /// `untitled_board`, then `{parent}_{row|col|grid}{n}` for an item and `{parent}_tab{n}` for a
    /// tab pane, whatever `id:` a nested board writes. Imported boards are included.
    pub board_ids: BTreeMap<ItemId, String>,
    /// The chart each chart item places, by position: the name it was authored under, or the id
    /// hoisting gave an inline chart (run after hoisting, every chart item is a name).
    pub charts: BTreeMap<ItemId, String>,
    /// The facts of each chart a layout item places, by position: Python compiles a copy of a chart
    /// per place, so a chart placed twice has two. Read from the merged root
    /// ([`Self::of_patch`]), which holds the chart definitions; empty from [`Self::of`].
    pub chart_facts: BTreeMap<ItemId, ChartFacts>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FactsError {
    /// A tab title with no letter, digit or underscore.
    EmptySlug {
        title: String,
    },
    DuplicateSlug {
        slug: String,
    },
    UnknownDefault {
        default: String,
        slug: String,
    },
    /// A variable default the index cannot read as a value.
    Variable {
        name: String,
        why: String,
    },
    /// A nested or imported board that names its own theme or `extends:`: the style it resolves
    /// to is not ported.
    NestedTheme {
        pos: ItemId,
    },
    /// A board the layout walk did not give an id: the facts and the board disagree on its place.
    NoBoardId {
        pos: ItemId,
    },
    /// An import the index cannot follow yet, or cannot read.
    Import {
        path: String,
        why: String,
    },
    /// A layout item id that is not `row<n>`, `col<n>` or `grid<n>`.
    ItemId(String),
    /// A variable name declared in two boards of the tree (`ERR-DUPLICATE-VARIABLE-NAME`).
    DuplicateVariable(Box<Diagnostic>),
}

impl fmt::Display for FactsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptySlug { title } => write!(
                f,
                "Tab title '{title}' produces an empty slug. Tab titles must contain at least one alphanumeric character."
            ),
            Self::DuplicateSlug { slug } => write!(
                f,
                "Duplicate tab slug '{slug}'. Tab titles must produce unique slugs (after lowercasing and replacing spaces with _)."
            ),
            Self::UnknownDefault { default, slug } => {
                write!(f, "tabs.default '{default}' (slug '{slug}') names no tab")
            }
            Self::Variable { name, why } => write!(f, "variable {name}: {why}"),
            Self::NestedTheme { pos } => write!(
                f,
                "the board at {pos} sets its own theme or extends, which is not ported yet"
            ),
            Self::NoBoardId { pos } => write!(f, "the board at {pos} has no id in the layout facts"),
            Self::Import { path, why } => write!(f, "board import {path}: {why}"),
            Self::ItemId(id) => write!(f, "layout item id '{id}' is not row<n>, col<n> or grid<n>"),
            Self::DuplicateVariable(d) => match d.message() {
                Ok(text) => f.write_str(&text),
                Err(_) => f.write_str(d.code),
            },
        }
    }
}

impl std::error::Error for FactsError {}

type Authored = <Board as Leaf>::Authored;

type Patch = <Board as Leaf>::Patch;

impl LayoutFacts {
    /// The facts of `board`, the authored root of `file` with its `extends:` already merged in; `files` serves the boards it imports.
    pub fn of(
        board: &Authored,
        file: &ProjectPath,
        files: &dyn ProjectFiles,
        today: Date,
    ) -> Result<Self, FactsError> {
        let body = &board.body;
        let vars = body.variables.iter().map(|(n, v)| (n, v.visible, v.into()));
        let (facts, _) = Self::root(
            &body.id,
            &body.title,
            vars.collect(),
            &body.layout,
            file,
            files,
            today,
        )?;
        Ok(facts)
    }

    /// The facts of the merged root, the form [`crate::merged_patch`] returns.
    pub fn of_patch(
        board: &Patch,
        file: &ProjectPath,
        files: &dyn ProjectFiles,
        today: Date,
    ) -> Result<Self, FactsError> {
        let body = &board.body;
        let vars = body.variables.iter().map(|(n, v)| (n, v.visible, v.into()));
        let (mut facts, order) = Self::root(
            &body.id,
            &body.title,
            vars.collect(),
            &body.layout,
            file,
            files,
            today,
        )?;
        facts.chart_facts = rhythm::allocate(&order, &|name| {
            body.charts
                .get(name)
                .and_then(Option::as_ref)
                .is_some_and(rhythm::single_ink)
        });
        Ok(facts)
    }

    fn root(
        id: &Option<BoardId>,
        title: &Tri<DisplayText>,
        vars: Vec<(&String, Option<bool>, Var<'_>)>,
        layout: &Option<LayoutAuthored>,
        file: &ProjectPath,
        files: &dyn ProjectFiles,
        today: Date,
    ) -> Result<(Self, Vec<Placement>), FactsError> {
        let mut w = Walk {
            files,
            today,
            facts: Self::default(),
            importing: vec![file.clone()],
            order: Vec::new(),
            prefix: Some(String::new()),
            declared: BTreeMap::new(),
            duplicate: None,
        };
        let id = match (id, title) {
            (Some(id), _) => id.as_str().to_string(),
            (None, Tri::Value(t)) => match slugify(t.as_str()) {
                s if s.is_empty() => "untitled_board".to_string(),
                s => s,
            },
            _ => "untitled_board".to_string(),
        };
        let shell = Shell {
            titled: titled(title),
            vars,
            layout: layout.as_ref(),
        };
        w.shell(shell, &id, parent_dir(file), &ItemId::root(), 0)?;
        match w.duplicate {
            Some(d) => Err(FactsError::DuplicateVariable(Box::new(d))),
            None => Ok((w.facts, w.order)),
        }
    }
}

/// What the walk reads of a board, whichever form the board was read in.
struct Shell<'a> {
    titled: bool,
    vars: Vec<(&'a String, Option<bool>, Var<'a>)>,
    layout: Option<&'a LayoutAuthored>,
}

fn titled(title: &Tri<DisplayText>) -> bool {
    matches!(title, Tri::Value(t) if !t.as_str().is_empty())
}

impl<'a> Shell<'a> {
    fn of(body: &'a BoardBodyAuthored) -> Self {
        Shell {
            titled: titled(&body.title),
            vars: body
                .variables
                .iter()
                .map(|(n, v)| (n, v.visible, v.into()))
                .collect(),
            layout: body.layout.as_ref(),
        }
    }
}

/// A layout item that names a file rather than a chart of the board.
pub(crate) fn is_board_file(name: &str) -> bool {
    [".yml", ".yaml", ".md"]
        .iter()
        .any(|ext| name.ends_with(ext))
}

/// A chart placement: the id of the board that holds it, its place, the chart's name.
type Placement = (String, ItemId, String);

struct Walk<'a> {
    files: &'a dyn ProjectFiles,
    today: Date,
    facts: LayoutFacts,
    importing: Vec<ProjectPath>,
    /// The chart placements as (owning board's id, place, chart name), in the order the walk met
    /// them.
    order: Vec<Placement>,
    /// Where the board being walked sits in its file's authored tree (`rows.0.tabs.items.1`, empty
    /// at the root), `None` under an imported board, whose keys have no path in this file.
    prefix: Option<String>,
    /// Where each variable name of the tree was first declared (Python's `declared_at`).
    declared: BTreeMap<String, String>,
    /// The first name declared twice. Python builds its variable registry after every board of the
    /// tree compiled, so it is reported once the walk found nothing else wrong.
    duplicate: Option<Diagnostic>,
}

/// `segment` under the board's authored path, `None` where the board has none.
fn under(prefix: &Option<String>, segment: &str) -> Option<String> {
    prefix.as_ref().map(|p| match p.is_empty() {
        true => segment.to_string(),
        false => format!("{p}.{segment}"),
    })
}

/// The authored path of the layout item `item_id` (`row2`, `col0`, `grid1`) of a board at `prefix`.
fn item_path(prefix: &Option<String>, item_id: &str) -> Result<Option<String>, FactsError> {
    let unknown = || FactsError::ItemId(item_id.to_string());
    let (kind, n) = item_id
        .split_at_checked(
            item_id
                .find(|c: char| c.is_ascii_digit())
                .ok_or_else(unknown)?,
        )
        .ok_or_else(unknown)?;
    let key = match kind {
        "grid" => "grid.items",
        "row" => "rows",
        "col" => "cols",
        _ => return Err(unknown()),
    };
    Ok(under(prefix, &format!("{key}.{n}")))
}

/// The hidden checkbox variable a nested board with `details:` holds its state in.
fn details_variable(nested: &NestedBoardAuthored, item_id: &str) -> Option<String> {
    let body = &nested.body;
    matches!(body.details, Tri::Value(_)).then(|| match &body.id {
        Some(own) => own.as_str().to_string(),
        None => format!("_details_{item_id}"),
    })
}

fn item_details_variable(item: &LayoutItemAuthored, item_id: &str) -> Option<String> {
    match item {
        LayoutItemAuthored::Board(nested) => details_variable(nested, item_id),
        _ => None,
    }
}

/// The hidden select variable that holds the open tab of a tabs layout of the board `id`.
fn tab_variable(t: &TabLayoutAuthored, id: &str) -> String {
    match &t.id {
        Tri::Value(v) if !v.is_empty() => v.clone(),
        _ => format!("_tab_{id}"),
    }
}

/// The hidden variables Python generates for the layout of board `id`: the tab selector, or the
/// state of each direct child with `details:`.
fn layout_variables(layout: Option<&LayoutAuthored>, id: &str) -> Vec<String> {
    match layout {
        Some(LayoutAuthored::Rows(items)) => items
            .iter()
            .enumerate()
            .filter_map(|(i, item)| item_details_variable(item, &format!("row{i}")))
            .collect(),
        Some(LayoutAuthored::Cols(items)) => items
            .iter()
            .enumerate()
            .filter_map(|(i, item)| item_details_variable(item, &format!("col{i}")))
            .collect(),
        Some(LayoutAuthored::Grid(g)) => g
            .items
            .iter()
            .flatten()
            .enumerate()
            .filter_map(|(i, cell)| item_details_variable(cell.item.as_ref()?, &format!("grid{i}")))
            .collect(),
        Some(LayoutAuthored::Tabs(t)) if t.items.iter().flatten().next().is_some() => {
            vec![tab_variable(t, id)]
        }
        _ => Vec::new(),
    }
}

fn parent_dir(path: &ProjectPath) -> &str {
    path.as_str().rsplit_once('/').map_or("", |(d, _)| d)
}

impl Walk<'_> {
    fn body(
        &mut self,
        body: &BoardBodyAuthored,
        id: &str,
        dir: &str,
        pos: &ItemId,
        parent_level: u32,
    ) -> Result<(), FactsError> {
        if !matches!(body.theme, Tri::Unset) || !matches!(body.extends, Tri::Unset) {
            return Err(FactsError::NestedTheme { pos: pos.clone() });
        }
        if !body.charts.is_empty() {
            self.imported_charts_refused()?;
        }
        self.imported_source_refused(body)?;
        if !body.queries.is_empty() {
            self.imported_queries_refused()?;
        }
        if body.variables.values().any(|v| {
            matches!(&v.options, Tri::Value(o) if matches!(o.query, Tri::Value(OptionsQuery::Sql(_))))
        }) {
            self.imported_option_queries_refused()?;
        }
        self.shell(Shell::of(body), id, dir, pos, parent_level)
    }

    /// Queries an imported board declares join the root's registry in Python, which is then
    /// checked for composition cycles; this pass does not gather them yet.
    fn imported_queries_refused(&self) -> Result<(), FactsError> {
        match self.importing.last() {
            Some(file) if self.importing.len() > 1 => Err(FactsError::Import {
                path: file.as_str().to_string(),
                why: "an imported board that declares queries is not ported yet".to_string(),
            }),
            _ => Ok(()),
        }
    }

    /// A variable's SQL options query becomes a query of the root's registry in Python, checked
    /// with the others for composition cycles; this pass does not gather it yet.
    fn imported_option_queries_refused(&self) -> Result<(), FactsError> {
        match self.importing.last() {
            Some(file) if self.importing.len() > 1 => Err(FactsError::Import {
                path: file.as_str().to_string(),
                why: "an imported board that declares a variable with an SQL options query is \
                      not ported yet"
                    .to_string(),
            }),
            _ => Ok(()),
        }
    }

    /// Charts an imported board declares would join the root's map under its own registry rules
    /// (a duplicate name, its own default rows), which this pass does not do yet.
    fn imported_charts_refused(&self) -> Result<(), FactsError> {
        match self.importing.last() {
            Some(file) if self.importing.len() > 1 => Err(FactsError::Import {
                path: file.as_str().to_string(),
                why: "an imported board that declares charts is not ported yet".to_string(),
            }),
            _ => Ok(()),
        }
    }

    /// A board file a layout imports is normalized by Python as a nested board with its own source
    /// and directory, over its own queries and variables, and every declared chart is judged
    /// against it; none of that is ported, so a source, a query or a query-backed variable in it
    /// is refused rather than left unjudged.
    fn imported_source_refused(&self, body: &BoardBodyAuthored) -> Result<(), FactsError> {
        let reads_query = |v: &VariableAuthored| {
            let options = match &v.options {
                Tri::Value(o) => !matches!(o.query, Tri::Unset) || !matches!(o.column, Tri::Unset),
                _ => false,
            };
            options || !matches!(v.query, Tri::Unset) || !matches!(v.column, Tri::Unset)
        };
        match self.importing.last() {
            Some(file)
                if self.importing.len() > 1
                    && (!matches!(body.source, Tri::Unset)
                        || !body.queries.is_empty()
                        || body.variables.values().any(reads_query)) =>
            {
                Err(FactsError::Import {
                    path: file.as_str().to_string(),
                    why:
                        "an imported board that sets a source or declares queries is not ported yet"
                            .to_string(),
                })
            }
            _ => Ok(()),
        }
    }

    /// Records the variables of the board being walked: the ones it declares, then the hidden ones
    /// its layout generates, which replace a declared one of the same name (Python's
    /// `local_variables.update`). A name met before in the tree is the first duplicate.
    fn declare(&mut self, authored: &[&String], hidden: &[String]) {
        let mut names: Vec<&str> = authored.iter().map(|n| n.as_str()).collect();
        for name in hidden {
            if !names.contains(&name.as_str()) {
                names.push(name);
            }
        }
        for name in names {
            let location = match &self.prefix {
                None => "an imported board".to_string(),
                Some(p) if p.is_empty() => format!("variables.{name}"),
                Some(p) => format!("{p}.variables.{name}"),
            };
            match self.declared.get(name) {
                None => {
                    self.declared.insert(name.to_string(), location);
                }
                Some(first) if self.duplicate.is_none() => {
                    self.duplicate = Some(diag::err_duplicate_variable_name(
                        name,
                        first.as_str(),
                        location.as_str(),
                    ));
                }
                Some(_) => {}
            }
        }
    }

    fn shell(
        &mut self,
        shell: Shell<'_>,
        id: &str,
        dir: &str,
        pos: &ItemId,
        parent_level: u32,
    ) -> Result<(), FactsError> {
        self.facts.board_ids.insert(pos.clone(), id.to_string());
        let level = parent_level + u32::from(shell.titled);
        self.facts.structural_title_level.insert(pos.clone(), level);
        let authored: Vec<&String> = shell.vars.iter().map(|(name, _, _)| *name).collect();
        self.declare(&authored, &layout_variables(shell.layout, id));
        let mut shown = Vec::new();
        for (name, visible, var) in shell.vars {
            if visible.unwrap_or(true) {
                let default = default_of(var).map_err(|why| FactsError::Variable {
                    name: name.clone(),
                    why,
                })?;
                shown.push(VariableFacts {
                    name: name.clone(),
                    default,
                });
            }
        }
        if !shown.is_empty() {
            self.facts.variables.insert(pos.clone(), shown);
        }
        match shell.layout {
            Some(l) => self.layout(l, id, dir, pos, level),
            None => Ok(()),
        }
    }

    fn layout(
        &mut self,
        layout: &LayoutAuthored,
        id: &str,
        dir: &str,
        pos: &ItemId,
        level: u32,
    ) -> Result<(), FactsError> {
        match layout {
            LayoutAuthored::Rows(items) => {
                self.items(items, "row", id, dir, &pos.key("rows"), level)
            }
            LayoutAuthored::Cols(items) => {
                self.items(items, "col", id, dir, &pos.key("cols"), level)
            }
            LayoutAuthored::Grid(g) => {
                let base = pos.key("grid").key("items");
                for (i, cell) in g.items.iter().flatten().enumerate() {
                    if let Some(item) = &cell.item {
                        let p = base.index(i).key("item");
                        self.item(item, &format!("grid{i}"), id, dir, &p, level)?;
                    }
                }
                Ok(())
            }
            LayoutAuthored::Tabs(t) => self.tabs(t, id, dir, pos, level),
        }
    }

    fn items(
        &mut self,
        items: &[LayoutItemAuthored],
        prefix: &str,
        id: &str,
        dir: &str,
        base: &ItemId,
        level: u32,
    ) -> Result<(), FactsError> {
        for (i, item) in items.iter().enumerate() {
            self.item(
                item,
                &format!("{prefix}{i}"),
                id,
                dir,
                &base.index(i),
                level,
            )?;
        }
        Ok(())
    }

    /// One layout item; `item_id` is its position-local id (`row0`, `grid2`).
    fn item(
        &mut self,
        item: &LayoutItemAuthored,
        item_id: &str,
        parent: &str,
        dir: &str,
        pos: &ItemId,
        level: u32,
    ) -> Result<(), FactsError> {
        let id = format!("{parent}_{item_id}");
        match item {
            LayoutItemAuthored::Chart(name) => {
                let name = name.as_str();
                if is_board_file(name) {
                    self.import(name, &id, dir, pos, level)?;
                } else {
                    self.facts.charts.insert(pos.clone(), name.to_string());
                    self.order
                        .push((parent.to_string(), pos.clone(), name.to_string()));
                }
                Ok(())
            }
            LayoutItemAuthored::Board(nested) => self.nested(nested, item_id, &id, dir, pos, level),
            LayoutItemAuthored::InlineChart(_) => self.imported_charts_refused(),
        }
    }

    fn nested(
        &mut self,
        nested: &NestedBoardAuthored,
        item_id: &str,
        id: &str,
        dir: &str,
        pos: &ItemId,
        parent_level: u32,
    ) -> Result<(), FactsError> {
        let body = &nested.body;
        if let (Tri::Value(d), Some(variable)) = (&body.details, details_variable(nested, item_id))
        {
            // `expanded` fills `false` (BoardDetails) when unwritten.
            let expanded = d.expanded.unwrap_or(false);
            self.facts
                .details
                .insert(pos.clone(), DetailsFacts { variable, expanded });
        }
        let inner = item_path(&self.prefix, item_id)?;
        let outer = std::mem::replace(&mut self.prefix, inner);
        let walked = self.body(body, id, dir, pos, parent_level);
        self.prefix = outer;
        walked
    }

    fn tabs(
        &mut self,
        t: &TabLayoutAuthored,
        id: &str,
        dir: &str,
        pos: &ItemId,
        level: u32,
    ) -> Result<(), FactsError> {
        let base = pos.key("tabs").key("items");
        let mut slugs = Vec::new();
        for (i, tab) in t.items.iter().flatten().enumerate() {
            let title = tab.title.as_ref().map_or("", |t| t.as_str());
            let pane = base.index(i);
            match &tab.layout {
                Some(l) => {
                    self.facts
                        .structural_title_level
                        .insert(pane.clone(), level);
                    let pane_id = format!("{id}_tab{i}");
                    self.facts.board_ids.insert(pane.clone(), pane_id.clone());
                    let inner = under(&self.prefix, &format!("tabs.items.{i}"));
                    let outer = std::mem::replace(&mut self.prefix, inner);
                    self.declare(&[], &layout_variables(Some(l), &pane_id));
                    let walked = self.layout(l, &pane_id, dir, &pane, level);
                    self.prefix = outer;
                    walked?;
                }
                None => {
                    self.facts
                        .board_ids
                        .insert(pane.clone(), format!("{id}_tab{i}"));
                    self.facts.structural_title_level.insert(pane, level + 1);
                }
            }
            let slug = slugify(title);
            if slug.is_empty() {
                return Err(FactsError::EmptySlug {
                    title: title.to_string(),
                });
            }
            slugs.push(slug);
        }
        for (i, s) in slugs.iter().enumerate() {
            if slugs.iter().take(i).any(|p| p == s) {
                return Err(FactsError::DuplicateSlug { slug: s.clone() });
            }
        }
        let default_tab = match &t.default {
            Tri::Value(d) if !d.as_str().is_empty() => {
                let slug = slugify(d.as_str());
                slugs
                    .iter()
                    .position(|s| *s == slug)
                    .ok_or_else(|| FactsError::UnknownDefault {
                        default: d.as_str().to_string(),
                        slug,
                    })?
            }
            _ => 0,
        };
        self.facts.tabs.insert(
            pos.clone(),
            TabsFacts {
                variable: tab_variable(t, id),
                slugs,
                default_tab,
            },
        );
        Ok(())
    }

    fn import(
        &mut self,
        name: &str,
        id: &str,
        dir: &str,
        pos: &ItemId,
        parent_level: u32,
    ) -> Result<(), FactsError> {
        let fail = |why: String| FactsError::Import {
            path: name.to_string(),
            why,
        };
        if name.contains("{{") || name.contains("{%") {
            return Err(fail("a templated import path is not ported yet".into()));
        }
        if name.ends_with(".md") {
            return Err(fail("a markdown board is not ported yet".into()));
        }
        if matches!(
            ProjectPath::try_from(name),
            Err(project::PathError::NotRelative(_) | project::PathError::Empty(_))
        ) {
            return Err(fail("an import path must be relative".into()));
        }
        let joined = if dir.is_empty() {
            name.to_string()
        } else {
            format!("{dir}/{name}")
        };
        let path = ProjectPath::try_from(joined.as_str()).map_err(|e| fail(e.to_string()))?;
        if self.importing.contains(&path) {
            return Err(fail(format!("{path} imports itself")));
        }
        let text = self
            .files
            .read(&path)
            .ok_or_else(|| fail(format!("{path} does not exist")))?;
        let node = yaml::parse_node(&text)
            .map_err(|d| fail(d.iter().map(|d| d.code).collect::<Vec<_>>().join(", ")))?;
        let node = read_board(node, self.today).node;
        crate::shape::refuse(&node, false).map_err(|d| fail(d.code.to_string()))?;
        crate::names::refuse(&node, false).map_err(|d| fail(d.code.to_string()))?;
        let parsed = parse::<Board>(&node, None);
        if let Some(e) = parsed.errors.first() {
            return Err(fail(format!("{} at {:?}", e.diagnostic.code, e.path())));
        }
        if !crate::hoist::has_content!(parsed.twin.body) {
            return Err(fail(crate::hoist::NO_CONTENT.into()));
        }
        self.importing.push(path.clone());
        let outer = self.prefix.take();
        let r = self.body(&parsed.twin.body, id, parent_dir(&path), pos, parent_level);
        self.prefix = outer;
        self.importing.pop();
        r
    }
}

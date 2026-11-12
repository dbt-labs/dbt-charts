//! The hoist pass: an inline chart in a layout moves into `Board.charts` under the id of its place
//! (`@rows[1]`, spec D-05) and the layout item becomes that chart's name. Ids derive from tree
//! position, never from the chart's title, so a title edit moves no id. A board with no layout is
//! rows of its own charts; `LayoutFacts::charts` places each, checked against Python's chart
//! type per place.
//!
//! After the move each chart's and each layer's `query:` name resolves to its `QueryId`
//! (`ChartAuthored::query_mut`, `layer_queries_mut`; `ERR-UNKNOWN-QUERY` for an undeclared name,
//! SQL text becomes an inline query), and a variable's SQL-text `options.query`
//! (`OptionsQuery::Sql`, told from a name at parse) moves into `queries`. A chart's `file.yml#name`
//! reads that file's `queries:` and hoists under the key `<resolved file>#<name>`, `QueryId`'s
//! printed form.
//!
//! Charts land in the root's map whatever board they sit in, as Python's one chart registry does:
//! the `charts:` a nested board declares are lifted there too, and a name two boards declare is an
//! error (`ERR-DUPLICATE-CHART-NAME`). `visible` stays on the chart; moving it onto the placement
//! is the final build step's job.
//!
//! A variable's `options.query` that is SQL text rather than a query name (Python's
//! `promote_inline_option_queries`) moves into `queries` too: `@variables.<name>.options.query`
//! below the board that declares the variable, and the variable keeps that name.
//!
//! Every SQL query that names no source takes the `source:` of the board it belongs to (Python's
//! `default_source`, an empty one reading as unset): a named query, a variable's option query and a
//! chart's inline query in the layout take their own board's, a declared chart's inline query the
//! board that places the chart (refused by name when two boards with different sources place it).
//! A query one board takes from another file is stamped as it is read (`external`). A declared
//! chart's inline query needs the root's source whatever board places it (Python registers every
//! declared chart at the root first), and a variable bound to a `table.column` runs on its own
//! board's. A SQL query still naming no source once everything is stamped is refused by name
//! (`ERR-SOURCE-REQUIRED`): a host's default source is not modeled, so there is none to fall to.
//! Every source a SQL query ends up with is then judged by `sources` (an inline file source that
//! does not resolve, a name no `sources:` entry declares), except a variable's option query, which
//! Python builds without the check.
//!
//! An inline query in a chart moves into `Board.queries` under the id of its place and the chart
//! holds that id (`QueryId`: the board's file and the name): `@rows[1].query` for an inline chart,
//! `@charts.<name>.query` for a declared one (names are unique, so the nested board a chart was
//! declared in does not matter).

use crate::compose::refuse_cycles;
use crate::extends::FoldError;
use crate::external::{source_required, External, Imports};
use crate::files::ProjectFiles;
use crate::index::BoardIndex;
use crate::public_id::inline_id;
use crate::sources::{check_sources, SourceRegistry, SourceUse};
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::board::Board;
use board_model::cache::CacheLayer;
use board_model::chart::{Chart, ChartAuthored, ChartQuery, LayerQuery};
use board_model::ids::{ChartId, ChartRef, ItemId, Step};
use board_model::ids::{QueryId, QueryName};
use board_model::query::{
    literal_text, Incremental, Query, QueryAuthored, QueryBaseAuthored, SourceName,
};
use board_model::variable::{OptionsQuery, VariableAuthored, VariablePatch, VariableQuery};
use diagnostic::{diag, py_str_repr, Diagnostic};
use migrate::Date;
use model_core::parse;
use model_core::Leaf;
use model_core::Tri;
use project::ProjectPath;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

type Patch = <Board as Leaf>::Patch;

/// Why charts could not be gathered into the root's map.
#[derive(Debug, Clone, PartialEq)]
pub enum HoistError {
    /// A chart name two boards declare (`ERR-DUPLICATE-CHART-NAME`).
    DuplicateChart(Box<Diagnostic>),
    /// A `charts:` key that cannot name a chart.
    BadChartName(String),
    /// A hoisted query's id that an authored `queries:` key already holds.
    DuplicateQuery(String),
    /// A chart names a query no board declares and that is not SQL (`ERR-UNKNOWN-QUERY`).
    UnknownQuery(Box<Diagnostic>),
    /// A layer names a query nothing declares (`ERR-UNRESOLVED-REFERENCE` in Python, which names
    /// the chart and the layer).
    UnresolvedReference(Box<Diagnostic>),
    /// A refusal with a registered code (Python's own code where Python refuses too): a query
    /// reference that does not resolve, a board-level `source:` that is no name, an inline file
    /// source that is invalid.
    Refused(Box<Diagnostic>),
    /// Another file's text that is not YAML the engine reads (`ERR-YAML-SYNTAX` and its kin).
    File(Vec<Diagnostic>),
    /// Something Python compiles and the engine does not yet, named.
    NotYet(String),
    /// A failure Python raises as a plain `CompilationError`, with no code: a source name that is
    /// no identifier, a query that does not read.
    Message(String),
    /// A field Python's validation refuses (`ERR-VALIDATION-FIELD`): a variable's column binding
    /// that is no `table.column`.
    FieldInvalid(Box<Diagnostic>),
    /// A SQL query that names no source and has no default to take one from
    /// (`ERR-SOURCE-REQUIRED`).
    SourceRequired(Box<Diagnostic>),
    /// A source name no `sources:` entry declares (`ERR-SOURCE-NOT-FOUND`).
    SourceNotFound(Box<Diagnostic>),
    /// An inline file source both the query's directory and the project root hold
    /// (`ERR-FILE-SOURCE-AMBIGUOUS`).
    AmbiguousFile(Box<Diagnostic>),
    /// The `meta.yml` chain above an imported file does not fold (Python's `ERR-VALIDATION-FIELD`
    /// for a `source:` that is not a string).
    Meta(Box<FoldError>),
    /// A declared constraint of a query model that the evaluator cannot read (a model bug, not
    /// something Python compiles that the engine does not).
    Constraint(String),
    /// A cycle: queries composing each other (`ERR-JINJA-ERROR`, Python's `_detect_circular`), or
    /// board files placing each other (`ERR-BOARD-IMPORT-CYCLE`).
    Cycle(Box<Diagnostic>),
    /// A query id the file's name cannot carry (a `#` ends the file in a query id).
    QueryId(String),
}

impl From<crate::files::ReadError> for HoistError {
    fn from(e: crate::files::ReadError) -> Self {
        Self::refused(e.diagnostic())
    }
}

impl HoistError {
    pub(crate) fn refused(d: Diagnostic) -> Self {
        Self::Refused(Box::new(d))
    }

    /// The registered diagnostics; an error Python raises without a code is `ERR-INTERNAL`.
    pub(crate) fn diagnostics(&self) -> Vec<Diagnostic> {
        match self {
            Self::DuplicateChart(d)
            | Self::UnknownQuery(d)
            | Self::UnresolvedReference(d)
            | Self::FieldInvalid(d)
            | Self::SourceRequired(d)
            | Self::SourceNotFound(d)
            | Self::AmbiguousFile(d)
            | Self::Refused(d)
            | Self::Cycle(d) => vec![(**d).clone()],
            Self::File(d) => d.clone(),
            Self::Meta(e) => e.diagnostics(),
            Self::BadChartName(_)
            | Self::DuplicateQuery(_)
            | Self::NotYet(_)
            | Self::Message(_)
            | Self::Constraint(_)
            | Self::QueryId(_) => vec![diag::err_internal(self.to_string())],
        }
    }
}

impl fmt::Display for HoistError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BadChartName(n) => write!(f, "'{n}' cannot name a chart"),
            Self::DuplicateQuery(n) => write!(f, "query name '{n}' is already declared"),
            Self::DuplicateChart(d)
            | Self::UnknownQuery(d)
            | Self::UnresolvedReference(d)
            | Self::SourceRequired(d)
            | Self::SourceNotFound(d)
            | Self::AmbiguousFile(d)
            | Self::Refused(d)
            | Self::Cycle(d)
            | Self::FieldInvalid(d) => match d.message() {
                Ok(text) => f.write_str(&text),
                Err(_) => f.write_str(d.code),
            },
            Self::File(d) => match d.first().map(Diagnostic::message) {
                Some(Ok(text)) => f.write_str(&text),
                _ => f.write_str(d.first().map_or("", |d| d.code)),
            },
            Self::Meta(e) => e.fmt(f),
            Self::Message(why) | Self::QueryId(why) | Self::NotYet(why) | Self::Constraint(why) => {
                f.write_str(why)
            }
        }
    }
}

impl std::error::Error for HoistError {}

/// Moves every inline chart of `patch`'s layout, and the charts its nested boards declare, into
/// `patch.body.charts`. `imports` names the charts a ref brought in from another file, whose
/// query names are that file's. Returns the public id of every chart of the root's map
/// ([`BoardIndex`]), and the inline file sources its queries name, by the ref as authored.
pub fn hoist_charts(
    patch: &mut Patch,
    file: &ProjectPath,
    files: &dyn ProjectFiles,
    today: Date,
    imports: &Imports,
    sources: &SourceRegistry,
) -> Result<(BoardIndex, BTreeMap<String, Value>), HoistError> {
    let implicit = sources
        .implicit()
        .map(SourceName::try_from)
        .transpose()
        .map_err(HoistError::Message)?;
    let root_scope = scope(&patch.body.source, implicit.as_ref())?;
    let mut found = Found {
        file,
        scope: root_scope.clone(),
        scopes: Scopes {
            root: root_scope.clone(),
            ..Scopes::default()
        },
        pending: Vec::new(),
        declared: Vec::new(),
        declared_in: BTreeMap::new(),
        charts: Vec::new(),
        queries: Vec::new(),
        names: declared_names(patch),
        used: BTreeSet::new(),
        public: BTreeMap::new(),
        synthetic: BTreeSet::new(),
        below: BTreeSet::new(),
        variable_boards: BTreeMap::new(),
        board: ItemId::root(),
        scope_of_board: BoardScope::default().under(&patch.body.cache, &patch.body.incremental, &[])?,
        boards: BTreeMap::new(),
        query_owners: BTreeMap::new(),
    };
    found
        .boards
        .insert(ItemId::root(), found.scope_of_board.clone());
    found.query_owners.extend(
        patch
            .body
            .queries
            .keys()
            .map(|key| (key.clone(), ItemId::root())),
    );
    authored_names(patch.body.charts.keys())?;
    if patch.body.layout.is_none() {
        patch.body.layout = Some(default_rows(patch.body.charts.keys())?);
    }
    found.used = found.names.clone();
    if let Some(l) = &mut patch.body.layout {
        layout(l, &mut Vec::new(), &mut found)?;
    }
    let mut root_vars = patch.body.variables.iter_mut().map(|(n, v)| {
        let options = match &v.options {
            Tri::Value(o) => Some((&o.column, &o.query)),
            _ => None,
        };
        let bound = bound_column(&v.column, options);
        let (options_slot, query_slot) = patch_query_slots(v);
        (n, bound, options_slot, query_slot)
    });
    hoist_variable_queries(&mut root_vars, &[], &mut found)?;
    let mut declared_at: BTreeMap<String, String> = patch
        .body
        .charts
        .keys()
        .map(|n| (n.clone(), format!("charts.{n}")))
        .collect();
    for (name, at, chart) in found.charts {
        if let Some(first) = declared_at.insert(name.clone(), at.clone()) {
            return Err(HoistError::DuplicateChart(Box::new(
                diag::err_duplicate_chart_name(name.as_str(), first, at),
            )));
        }
        patch.body.charts.insert(name, chart);
    }
    // A layout item names a chart of the board; one spelled as a chart ref is no chart (Python
    // resolves refs only under `charts:`), and an undeclared plain name is none either.
    if let Some((missing, _, at)) = found.scopes.placed.iter().find(|(n, ..)| {
        !patch.body.charts.contains_key(n.as_str())
            && (!is_special(n) || ChartRef::try_from(n.as_str()).is_ok())
    }) {
        return Err(HoistError::UnresolvedReference(Box::new(
            diag::err_unresolved_reference(missing.as_str(), " in layout item")
                .with_sink_param("available", serde_json::json!(found.names))
                .at_path(diagnostic::ModelPath(
                    at.split('.').map(str::to_string).collect(),
                )),
        )));
    }
    let query_boards = found.declared_in;
    for (name, query) in found.declared {
        if patch.body.queries.insert(name.clone(), query).is_some() {
            return Err(HoistError::DuplicateQuery(name));
        }
    }
    // Declared charts (the root's and the lifted ones); the inline ones are done.
    for (name, chart) in patch.body.charts.iter_mut() {
        if let Some(chart) = chart {
            let steps = [Step::Key("charts"), Step::Key(name), Step::Key("query")];
            if let Some((key, query)) = hoist_query(chart, &steps, file)? {
                found.pending.push(Pending {
                    chart: name.clone(),
                    board: ItemId::root(),
                    key,
                    query,
                });
            }
        }
    }
    let mut defaulted = Vec::new();
    for pending in found.pending {
        let mut query = pending.query;
        if takes_default(&query) {
            stamp(&mut query, found.scopes.query(&pending.chart)?.as_ref());
            defaulted.push(pending.key.clone());
        }
        found
            .query_owners
            .insert(pending.key.clone(), pending.board);
        found.queries.push((pending.key, query));
    }
    for (name, query) in found.queries {
        if patch.body.queries.insert(name.clone(), query).is_some() {
            return Err(HoistError::DuplicateQuery(name));
        }
    }
    let mut elsewhere = resolve_names(
        patch,
        file,
        &External { files, today },
        imports,
        &found.scopes,
    )?;
    elsewhere.gathered.extend(imports.queries.iter().cloned());
    for (key, board) in std::mem::take(&mut elsewhere.owners) {
        found.query_owners.entry(key).or_insert(board);
    }
    refuse_cycles(file, &patch.body.queries, &elsewhere.scoped)?;
    patch
        .body
        .queries
        .values_mut()
        .for_each(|q| stamp(q, root_scope.as_ref()));
    for (name, query) in &patch.body.queries {
        if let Some(query) = query {
            check_query(name, query, elsewhere.gathered.contains(name))?;
        }
    }
    // Python refuses every SQL query that still names no source (`ERR-SOURCE-REQUIRED`); there is
    // no host default here to supply one.
    if let Some((name, _)) = patch.body.queries.iter().find(|(_, q)| takes_default(q)) {
        return Err(source_required(name));
    }
    let mut uses = elsewhere.uses;
    // A board with no project location anchors nothing: an inline file source is then refused.
    let root_dir = files
        .located()
        .then(|| file.as_str().rsplit_once('/').map_or("", |(d, _)| d));
    for (name, query) in &patch.body.queries {
        // A variable's option query is built without the source check; a query another file
        // declares was checked against that file.
        if found.synthetic.contains(name) || elsewhere.read.contains(name) {
            continue;
        }
        let anchor = root_dir.filter(|_| !elsewhere.layers.contains(name));
        uses.extend(SourceUse::of(name, query, anchor));
    }
    // Python normalizes every declared chart once per board of the tree, each time with that
    // board's source, so a declared chart's inline query is judged against all of them; an
    // imported chart's layer SQL too (it has no project location).
    let per_board = defaulted
        .iter()
        .chain(&elsewhere.declared_sql)
        .map(|key| (key, root_dir))
        .chain(elsewhere.imported_layers.iter().map(|key| (key, None)));
    for (key, anchor) in per_board {
        for source in found.scopes.root.iter().chain(&found.scopes.boards) {
            uses.push(SourceUse {
                query: key.clone(),
                source: source.as_str().to_string(),
                anchor: anchor.map(str::to_string),
            });
        }
    }
    let inline = check_sources(&uses, sources, files)?;
    let mut public_ids = std::mem::take(&mut found.public);
    for name in patch.body.charts.keys().filter(|k| !k.starts_with('@')) {
        let id =
            ChartId::try_from(name.as_str()).map_err(|_| HoistError::BadChartName(name.clone()))?;
        public_ids.insert(id, name.clone());
    }
    let mut declared_below = std::mem::take(&mut found.below);
    declared_below.extend(
        patch
            .body
            .charts
            .keys()
            .filter(|name| imports.charts.contains_key(name.as_str()))
            .cloned(),
    );
    Ok((
        BoardIndex {
            public_ids,
            declared_below,
            chart_boards: std::mem::take(&mut found.scopes.board_of),
            variable_boards: std::mem::take(&mut found.variable_boards),
            boards: found.boards,
            query_owners: found.query_owners,
            query_boards,
            text_queries: elsewhere.text_queries,
            ..BoardIndex::default()
        },
        inline,
    ))
}

/// The shape rules of Python's authored query models: a values query has no connection slot and
/// takes `rows` or `columns` with `values` (`QueryAuthored::violation`). Python reads a query
/// another file declares through the normalized model alone, which states no shape for a values
/// query and refuses a `source` key even when it is null; a board's own `source: null` reads as
/// absent.
fn check_shape(name: &str, query: &QueryAuthored, external: bool) -> Result<(), HoistError> {
    if let QueryAuthored::Values(q) = query {
        let written = match external {
            true => !matches!(q.base.source, Tri::Unset),
            false => matches!(q.base.source, Tri::Value(_)),
        };
        if written {
            return Err(shape(format!(
                "Query '{name}': source: Extra inputs are not permitted. A values query has no connection; remove `source:`."
            )));
        }
    }
    refuse_shape(name, query, !external)
}

fn shape(message: String) -> HoistError {
    HoistError::refused(diag::err_query_shape_invalid(message))
}

fn refuse_shape(name: &str, query: &QueryAuthored, authored: bool) -> Result<(), HoistError> {
    match query
        .violation(name, authored)
        .map_err(|e| HoistError::Constraint(e.to_string()))?
    {
        Some(message) => Err(shape(message)),
        None => Ok(()),
    }
}

/// Every query a patch authors: its `queries:`, the inline query of each chart it declares or
/// places in a layout, and the same in the boards nested in its layout.
fn authored_queries(patch: &Patch) -> Vec<(String, QueryAuthored)> {
    macro_rules! body {
        ($body:expr, $out:expr) => {{
            let body = $body;
            for (name, query) in &body.queries {
                if let Some(query) = query {
                    $out.push((name.clone(), query.clone()));
                }
            }
            for (name, chart) in body
                .charts
                .iter_mut()
                .filter_map(|(n, c)| Some((n, c.as_mut()?)))
            {
                inline(name, chart, $out);
            }
            if let Some(l) = &mut body.layout {
                items(l, $out);
            }
        }};
    }
    fn inline(name: &str, chart: &mut ChartAuthored, out: &mut Vec<(String, QueryAuthored)>) {
        if let Some(Tri::Value(ChartQuery::Inline(query))) = chart.query_mut().map(|q| &*q) {
            let query: <Query as Leaf>::Patch = (**query).clone().into();
            out.extend(query.map(|q| (name.to_string(), q)));
        }
    }
    fn items(l: &mut LayoutAuthored, out: &mut Vec<(String, QueryAuthored)>) {
        let item = |it: &mut LayoutItemAuthored, out: &mut Vec<(String, QueryAuthored)>| match it {
            LayoutItemAuthored::InlineChart(chart) => inline("@inline", chart, out),
            LayoutItemAuthored::Board(nested) => body!(&mut nested.body, out),
            LayoutItemAuthored::Chart(_) => {}
        };
        match l {
            LayoutAuthored::Rows(list) | LayoutAuthored::Cols(list) => {
                list.iter_mut().for_each(|it| item(it, out))
            }
            LayoutAuthored::Grid(g) => g
                .items
                .iter_mut()
                .flatten()
                .filter_map(|cell| cell.item.as_mut())
                .for_each(|it| item(it, out)),
            LayoutAuthored::Tabs(t) => {
                for tab in t.items.iter_mut().flatten() {
                    if let Some(inner) = &mut tab.layout {
                        items(inner, out);
                    }
                }
            }
        }
    }
    let mut patch = patch.clone();
    let mut out = Vec::new();
    body!(&mut patch.body, &mut out);
    out
}

/// Python parses every layer (a `meta.yml`, an `extends:` template) as an authored board on its
/// own, so a values query anywhere in one must have a shape even when a higher layer replaces it
/// (its `source` is refused only once the query is normalized, so not here).
pub fn check_layers(layers: &[(ProjectPath, Patch)]) -> Result<(), HoistError> {
    for (_, layer) in layers {
        for (name, query) in authored_queries(layer) {
            if matches!(query, QueryAuthored::Values(_)) {
                refuse_shape(&name, &query, true)?;
            }
        }
    }
    Ok(())
}

/// What Python's query models and `normalize_query` refuse of a query once its layers are
/// folded and its default source stamped: the type's declared constraints (`QueryAuthored::violation`),
/// and the field rules the constraints do not state. A schema query's `source` is a literal
/// string like its other fields (the shared `SourceName` takes Jinja, which a SQL query's
/// source may carry), and Python's field validators run before the model's constraints, so a
/// templated `source` is the error whatever else is unmet. A values query has no connection
/// slot, and a watermark column is for SQL queries only (`incremental: false` opts out and is
/// fine anywhere). A query of another file is read by Python through the normalized model alone,
/// which states no shape for a values query.
fn check_query(name: &str, query: &QueryAuthored, external: bool) -> Result<(), HoistError> {
    if let QueryAuthored::Schema(q) = query {
        if let Tri::Value(source) = &q.base.source {
            literal_text(source.as_str())
                .map_err(|m| shape(format!("Query '{name}': source: Value error, {m}")))?;
        }
    }
    check_shape(name, query, external)?;
    let not_sql = || HoistError::refused(diag::err_incremental_not_sql(name));
    let watermark =
        |b: &QueryBaseAuthored| matches!(b.incremental, Tri::Value(Incremental::Column(_)));
    match query {
        QueryAuthored::Values(q) if watermark(&q.base) => Err(not_sql()),
        QueryAuthored::Schema(q) if watermark(&q.base) => Err(not_sql()),
        QueryAuthored::Sql(q) => match &q.setup_sql {
            Tri::Value(s) if s.contains("{{ queries.") => {
                Err(HoistError::refused(diag::err_setup_sql_composes_query(name)))
            }
            _ => Ok(()),
        },
        _ => Ok(()),
    }
}

/// The source a board's queries take: its own `source:` (an empty one reads as unset), else the
/// enclosing board's.
fn scope(
    source: &Tri<String>,
    outer: Option<&SourceName>,
) -> Result<Option<SourceName>, HoistError> {
    match source {
        Tri::Value(s) if !s.is_empty() => SourceName::try_from(s.as_str())
            .map(Some)
            .map_err(HoistError::Message),
        _ => Ok(outer.cloned()),
    }
}

/// A SQL query that names no source: the only kind a default source can fill, and the only kind
/// Python refuses when none is left (a values query has no connection slot; a schema query takes
/// no default, `check_query`).
fn takes_default(query: &Option<QueryAuthored>) -> bool {
    matches!(query, Some(QueryAuthored::Sql(q)) if matches!(q.base.source, Tri::Unset | Tri::Null))
}

/// `source` into a SQL query that names none (a values or schema query takes no default).
fn stamp(query: &mut Option<QueryAuthored>, source: Option<&SourceName>) {
    if let (Some(QueryAuthored::Sql(q)), Some(source)) = (query, source) {
        if matches!(q.base.source, Tri::Unset | Tri::Null) {
            q.base.source = Tri::Value(source.clone());
        }
    }
}

/// Python's `looks_like_sql`: a chart's `query:` string that is no query name but starts like a
/// statement is an inline SQL query.
fn looks_like_sql(text: &str) -> bool {
    let head = text.trim_start();
    [
        "SELECT", "WITH", "PRAGMA", "INSERT", "UPDATE", "DELETE", "CREATE",
    ]
    .iter()
    .any(|kw| {
        head.get(..kw.len())
            .is_some_and(|w| w.eq_ignore_ascii_case(kw))
            && !head
                .get(kw.len()..)
                .and_then(|r| r.chars().next())
                .is_some_and(|c| c.is_alphanumeric() || c == '_')
    })
}

/// Every query gathered from another file belongs to the board of the chart that reached it,
/// the composed ones included: a query only composed by a chart has no chart of its own.
fn own_gathered(
    owners: &mut BTreeMap<String, ItemId>,
    gathered: &[(String, <Query as Leaf>::Patch)],
    owner: &ItemId,
) {
    for (key, _) in gathered {
        owners.entry(key.clone()).or_insert_with(|| owner.clone());
    }
}

/// A chart's `query: name` (after an optional `queries.` prefix) becomes the id of the query of
/// that name; SQL text becomes an inline query; anything else is an unknown query. The names of a
/// chart a ref brought in are its own file's (`imports`), not this board's.
fn resolve_names(
    patch: &mut Patch,
    file: &ProjectPath,
    external: &External,
    imports: &Imports,
    scopes: &Scopes,
) -> Result<Elsewhere, HoistError> {
    let mut uses = Vec::new();
    let mut layers = BTreeSet::new();
    let mut declared_sql = Vec::new();
    let mut imported_layers = Vec::new();
    let known: std::collections::BTreeSet<String> = patch.body.queries.keys().cloned().collect();
    let mut sql = Vec::new();
    let mut ext = Vec::new();
    let mut imported = Vec::new();
    let mut owners = BTreeMap::new();
    let mut text_queries = BTreeMap::new();
    for (chart_name, chart) in patch.body.charts.iter_mut() {
        let Some(chart) = chart else { continue };
        let owner = scopes
            .board_of
            .get(chart_name)
            .cloned()
            .unwrap_or_else(ItemId::root);
        let origin = imports.charts.get(chart_name);
        if let Some(origin) = origin {
            let read = external.imported_text_queries(origin, &mut imported, &mut uses)?;
            own_gathered(&mut owners, &imported, &owner);
            if !read.is_empty() {
                text_queries.insert(chart_name.clone(), read);
            }
        }
        if let Some(slot) = chart.query_mut() {
            // Python resolves no chart-level `{ref: ..}`: it looks the ref up as a query name.
            if let Tri::Value(ChartQuery::Ref(r)) = &*slot {
                let context = format!(" in chart '{chart_name}'");
                return Err(HoistError::UnresolvedReference(Box::new(
                    diag::err_unresolved_reference(
                        r.r#ref.as_ref().map_or("", |p| AsRef::<str>::as_ref(p)),
                        context,
                    ),
                )));
            }
            if let Tri::Value(ChartQuery::Name(text)) = &*slot {
                let steps = [
                    Step::Key("charts"),
                    Step::Key(chart_name),
                    Step::Key("query"),
                ];
                let id = match origin.filter(|_| is_lexical(text)) {
                    Some(origin) => {
                        let id = external.imported_query(text, origin, &mut imported, &mut uses)?;
                        own_gathered(&mut owners, &imported, &owner);
                        id
                    }
                    None => {
                        // A chart's name may carry the `queries.` prefix; a layer's may not.
                        let name = text.strip_prefix("queries.").unwrap_or(text).to_string();
                        if name.contains('#') {
                            let id = external
                                .query(&name, chart_name, file, &known, &mut ext, &mut uses)?;
                            own_gathered(&mut owners, &ext, &owner);
                            id
                        } else {
                            let unknown = || {
                                HoistError::UnknownQuery(Box::new(diag::err_unknown_query(
                                    chart_name.as_str(),
                                    name.as_str(),
                                    known.iter().cloned().collect::<Vec<String>>(),
                                )))
                            };
                            let source = scopes.query_if_sql(chart_name, &name)?;
                            let id =
                                resolve(&name, &steps, &known, file, &mut sql, source, unknown)?;
                            if id.name().is_inline() {
                                owners
                                    .entry(id.name().as_str().to_string())
                                    .or_insert_with(|| owner.clone());
                                if !scopes.inline.contains_key(chart_name) {
                                    declared_sql.push(id.name().as_str().to_string());
                                }
                            }
                            id
                        }
                    }
                };
                *slot = Tri::Value(ChartQuery::Hoisted(id));
            }
        }
        for (i, slot) in chart.layer_queries_mut().into_iter().enumerate() {
            let Tri::Value(LayerQuery::Name(name)) = &*slot else {
                continue;
            };
            let id = match origin.filter(|_| is_lexical(name)) {
                Some(origin) => {
                    let id = external.imported_query(name, origin, &mut imported, &mut uses)?;
                    own_gathered(&mut owners, &imported, &owner);
                    id
                }
                None => {
                    let steps = [
                        Step::Key("charts"),
                        Step::Key(chart_name),
                        Step::Key("layers"),
                        Step::Index(i),
                        Step::Key("query"),
                    ];
                    let unknown = || {
                        HoistError::UnresolvedReference(Box::new(diag::err_unresolved_reference(
                            name.as_str(),
                            format!(" in chart '{chart_name}', layer {i}"),
                        )))
                    };
                    let source = scopes.layer(chart_name);
                    let id = resolve(name, &steps, &known, file, &mut sql, source, unknown)?;
                    // Python normalizes a layer's SQL text with no project location.
                    if id.name().is_inline() {
                        owners
                            .entry(id.name().as_str().to_string())
                            .or_insert_with(|| owner.clone());
                        layers.insert(id.name().as_str().to_string());
                        if origin.is_some() {
                            imported_layers.push(id.name().as_str().to_string());
                        }
                    }
                    id
                }
            };
            *slot = Tri::Value(LayerQuery::Hoisted(id));
        }
    }
    for (name, query) in sql {
        if patch.body.queries.insert(name.clone(), query).is_some() {
            return Err(HoistError::DuplicateQuery(name));
        }
    }
    // Another file's queries are named by their file, so two charts reaching one agree on it; a
    // file read through a chart's `#` ref and through an import takes different default sources.
    let read: BTreeSet<String> = ext
        .iter()
        .chain(&imported)
        .map(|(k, _)| k.clone())
        .collect();
    let mut gathered = std::collections::BTreeSet::new();
    let scoped = imported.iter().map(|(name, _)| name.clone()).collect();
    for (name, query) in ext.into_iter().chain(imported) {
        match patch.body.queries.get(&name) {
            Some(held) if *held == query => {}
            Some(_) => return Err(HoistError::DuplicateQuery(name)),
            None => {
                gathered.insert(name.clone());
                patch.body.queries.insert(name, query);
            }
        }
    }
    Ok(Elsewhere {
        owners,
        uses,
        read,
        layers,
        declared_sql,
        imported_layers,
        scoped,
        gathered,
        text_queries,
    })
}

/// What `resolve_names` leaves for the source check: the sources of the queries it read from
/// other files (checked where their directory is known), which of the board's own queries those
/// are, and which are a layer's SQL text.
struct Elsewhere {
    /// The board of each query this pass made from a chart's text or another file.
    owners: BTreeMap<String, ItemId>,
    uses: Vec<SourceUse>,
    read: BTreeSet<String>,
    layers: BTreeSet<String>,
    /// The inline SQL text of declared charts, which took their board's source.
    declared_sql: Vec<String>,
    /// The inline SQL text of an imported chart's layers: Python reads the chart's definition
    /// afresh for every board, so each promotes it under that board's source.
    imported_layers: Vec<String>,
    /// The queries an imported chart's file declares, which are that file's own scope.
    scoped: BTreeSet<String>,
    /// The other files' queries this board newly took in, plus its imports' own.
    gathered: BTreeSet<String>,
    /// The query each `queries.<name>` an imported chart's display text reads stands for, by the
    /// chart's key.
    text_queries: BTreeMap<String, BTreeMap<String, QueryId>>,
}

/// Python's `_is_lexically_scoped_query_name`: an imported chart's `query:` that is read in the
/// chart's own file is any that is neither SQL text nor a `<file>.queries.<name>` ref.
fn is_lexical(text: &str) -> bool {
    let text = text.trim();
    !text.contains(".queries.") && !looks_like_sql(text)
}

/// The id a query `name` stands for: the declared query of that name, a new inline query if the
/// text is SQL (named for `steps`), else the caller's error.
fn resolve(
    name: &str,
    steps: &[Step],
    known: &std::collections::BTreeSet<String>,
    file: &ProjectPath,
    sql: &mut Vec<(String, <Query as Leaf>::Patch)>,
    source: Option<SourceName>,
    unknown: impl FnOnce() -> HoistError,
) -> Result<QueryId, HoistError> {
    if known.contains(name) {
        let query = QueryName::try_from(name).map_err(HoistError::QueryId)?;
        return QueryId::new(file.clone(), query).map_err(HoistError::QueryId);
    }
    if looks_like_sql(name) {
        let id = QueryId::at(file.clone(), steps).map_err(HoistError::QueryId)?;
        let mut query = sql_query(name)?;
        stamp(&mut query, source.as_ref());
        sql.push((id.name().as_str().to_string(), query));
        return Ok(id);
    }
    Err(unknown())
}

/// `{sql: text}` as the query it reads to.
fn sql_query(text: &str) -> Result<<Query as Leaf>::Patch, HoistError> {
    let span = yaml::Span {
        start: 0,
        end: 0,
        line: 1,
        column: 1,
    };
    let leaf = |s: &str| yaml::Node {
        span,
        kind: yaml::Kind::Scalar(yaml::Value::Str(s.to_string())),
    };
    let mut map = indexmap::IndexMap::new();
    map.insert(
        "sql".to_string(),
        yaml::Entry::string_key(span, "sql", leaf(text)),
    );
    let node = yaml::Node {
        span,
        kind: yaml::Kind::Map(map),
    };
    let parsed = parse::<Query>(&node, None);
    match parsed.errors.first() {
        Some(e) => Err(HoistError::QueryId(format!(
            "SQL text did not read as a query: {}",
            e.diagnostic.code
        ))),
        None => Ok(parsed.twin),
    }
}

/// A layout name that is no chart of the board: a partial (`_x`) or a file (`a.yml`, `dir/b`),
/// which normalization resolves (Python's `_is_special_reference`).
fn is_special(name: &str) -> bool {
    name.starts_with('_') || name.contains(['.', '/'])
}

/// What the walk gathers: charts to add to the root's map, and the queries hoisted out of them.
struct Found<'f> {
    /// Every chart name any board declares: Python's one registry, which each board's ids are
    /// made unique against.
    names: BTreeSet<String>,
    /// The chart ids taken in the board the walk is in: the registry's names and the inline ids
    /// given so far in this board (Python's `used_chart_ids`); a nested board or tab pane starts
    /// over from the names.
    used: BTreeSet<String>,
    /// The public id of each inline chart, by its position id.
    public: BTreeMap<ChartId, String>,
    /// The charts a nested board declares.
    below: BTreeSet<String>,
    /// The nested board that declares each variable of one.
    variable_boards: BTreeMap<String, ItemId>,
    file: &'f ProjectPath,
    /// The source of the board the walk is in.
    scope: Option<SourceName>,
    scopes: Scopes,
    /// Declared charts' inline queries, stamped once every placement is known.
    pending: Vec<Pending>,
    /// Queries nested boards declare under their own `queries:`, by authored name.
    declared: Vec<(String, <Query as Leaf>::Patch)>,
    /// The position of the nested board that declared each of `declared`, by query name.
    declared_in: BTreeMap<String, String>,
    /// Each lifted chart with the authoring path of its declaration.
    charts: Vec<(String, String, <Chart as Leaf>::Patch)>,
    queries: Vec<(String, <Query as Leaf>::Patch)>,
    /// The names of the queries hoisted from a variable's `options.query` text.
    synthetic: BTreeSet<String>,
    /// The board the walk is in, and its cache and incremental scopes folded down from the root.
    board: ItemId,
    scope_of_board: BoardScope,
    boards: BTreeMap<ItemId, BoardScope>,
    /// The board each query of the root's map belongs to.
    query_owners: BTreeMap<String, ItemId>,
}

impl Found<'_> {
    fn own_query(&mut self, key: &str) {
        self.query_owners
            .insert(key.to_string(), self.board.clone());
    }
}

/// A board's cache and incremental settings as its queries inherit them: its own over every
/// enclosing board's (Python's `_build_env` folds `merge_cache_layers(board_cache, board.cache)`
/// down the nesting, and a nested board's `incremental:` replaces its parent's, `false` included).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BoardScope {
    pub cache: CacheLayer,
    pub incremental: Option<Incremental>,
}

impl BoardScope {
    /// This scope under a board that authors `cache` and `incremental`.
    fn under(
        &self,
        cache: &Tri<CacheLayer>,
        incremental: &Tri<Incremental>,
        at: &[String],
    ) -> Result<Self, HoistError> {
        let mut folded = self.clone();
        if let Tri::Value(layer) = cache {
            folded.cache.over(layer.clone());
        }
        if folded.cache.is_refused() {
            let path = [at, &["cache".to_string(), "prefetch".to_string()]].concat();
            return Err(HoistError::refused(
                diag::err_cache_prefetch_needs_cache("board").at_path(diagnostic::ModelPath(path)),
            ));
        }
        if let Tri::Value(i) = incremental {
            folded.incremental = Some(i.clone());
        }
        Ok(folded)
    }
}

/// A declared chart's inline query, hoisted but not yet stamped.
struct Pending {
    chart: String,
    board: ItemId,
    key: String,
    query: <Query as Leaf>::Patch,
}

/// Which board's source each chart's SQL text takes, known once the walk has placed every chart.
#[derive(Default)]
struct Scopes {
    root: Option<SourceName>,
    /// The source of every nested board that has one (its own, else the enclosing board's).
    boards: Vec<SourceName>,
    /// Where each declared chart is placed: its name and the source of the placing board.
    /// Each placement also names the item's position in the layout (`rows.0`).
    placed: Vec<(String, Option<SourceName>, String)>,
    /// The source of the board that declares a chart, for a chart nothing places.
    declared_in: std::collections::BTreeMap<String, Option<SourceName>>,
    /// Each layout chart's own board source.
    inline: std::collections::BTreeMap<String, Option<SourceName>>,
    /// The board each layout chart, and each chart a nested board declares, belongs to; a chart
    /// the root declares is not listed.
    board_of: BTreeMap<String, ItemId>,
}

impl Scopes {
    /// The source a chart's own SQL takes: its board's if written in a layout, else the board that
    /// places it (refused by name when boards with different sources do). Python registers a
    /// declared chart at the root before any placement, so it needs the root's source whatever
    /// board declares or places it.
    fn query(&self, chart: &str) -> Result<Option<SourceName>, HoistError> {
        if let Some(source) = self.inline.get(chart) {
            return Ok(source.clone());
        }
        if self.root.is_none() {
            return Err(source_required(&format!("_inline_query_{chart}")));
        }
        let mut places = self
            .placed
            .iter()
            .filter(|(name, ..)| name == chart)
            .map(|(_, source, _)| source);
        let declared = self.declared_in.get(chart).unwrap_or(&self.root);
        let source = places.next().unwrap_or(declared);
        if places.any(|other| other != source) {
            return Err(HoistError::refused(diag::err_chart_sources_ambiguous(chart)));
        }
        Ok(source.clone())
    }

    /// `query` for SQL text only: a query name must not trip the placement refusal.
    fn query_if_sql(&self, chart: &str, text: &str) -> Result<Option<SourceName>, HoistError> {
        if looks_like_sql(text) {
            self.query(chart)
        } else {
            Ok(None)
        }
    }

    /// A layer's SQL: a layout chart's own board source; a declared chart's takes the root's, as
    /// Python promotes it when the root board registers the chart.
    fn layer(&self, chart: &str) -> Option<SourceName> {
        self.inline.get(chart).unwrap_or(&self.root).clone()
    }
}

/// Swaps a chart's inline query for the id of the query hoisted from `steps`, and returns it.
fn hoist_query(
    chart: &mut ChartAuthored,
    steps: &[Step],
    file: &ProjectPath,
) -> Result<Option<(String, <Query as Leaf>::Patch)>, HoistError> {
    let Some(slot) = chart.query_mut() else {
        return Ok(None);
    };
    if !matches!(slot, Tri::Value(ChartQuery::Inline(_))) {
        return Ok(None);
    }
    let id = QueryId::at(file.clone(), steps).map_err(HoistError::QueryId)?;
    if let Tri::Value(ChartQuery::Inline(query)) =
        std::mem::replace(slot, Tri::Value(ChartQuery::Hoisted(id.clone())))
    {
        return Ok(Some((id.name().as_str().to_string(), (*query).into())));
    }
    Ok(None)
}

/// The chart names the root and every nested board declare.
fn declared_names(patch: &Patch) -> BTreeSet<String> {
    fn walk(l: &LayoutAuthored, out: &mut BTreeSet<String>) {
        let items: Vec<&LayoutItemAuthored> = match l {
            LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => items.iter().collect(),
            LayoutAuthored::Grid(g) => g
                .items
                .iter()
                .flatten()
                .filter_map(|c| c.item.as_ref())
                .collect(),
            LayoutAuthored::Tabs(t) => {
                t.items
                    .iter()
                    .flatten()
                    .filter_map(|t| t.layout.as_ref())
                    .for_each(|l| walk(l, out));
                return;
            }
        };
        for it in items {
            if let LayoutItemAuthored::Board(b) = it {
                out.extend(b.body.charts.keys().cloned());
                if let Some(l) = &b.body.layout {
                    walk(l, out);
                }
            }
        }
    }
    let mut out: BTreeSet<String> = patch.body.charts.keys().cloned().collect();
    if let Some(l) = &patch.body.layout {
        walk(l, &mut out);
    }
    out
}

/// Every `charts:` key a board authors names a chart (hoisted inline ids are added later).
fn authored_names<'k>(mut names: impl Iterator<Item = &'k String>) -> Result<(), HoistError> {
    names.try_for_each(|k| {
        ChartId::try_from(k.as_str())
            .map(drop)
            .map_err(|_| HoistError::BadChartName(k.clone()))
    })
}

/// A board that writes no layout is its own charts, in authored order, as rows (none, for a board
/// with no charts: Python's empty `rows`).
fn default_rows<'k>(names: impl Iterator<Item = &'k String>) -> Result<LayoutAuthored, HoistError> {
    let items = names
        .map(|k| {
            ChartId::try_from(k.as_str())
                .map(LayoutItemAuthored::Chart)
                .map_err(|_| HoistError::BadChartName(k.clone()))
        })
        .collect::<Result<Vec<_>, _>>()?;
    Ok(LayoutAuthored::Rows(items))
}

fn layout<'a>(
    l: &'a mut LayoutAuthored,
    at: &mut Vec<Step<'a>>,
    found: &mut Found<'_>,
) -> Result<(), HoistError> {
    match l {
        LayoutAuthored::Rows(items) => list("rows", "row", items, at, found),
        LayoutAuthored::Cols(items) => list("cols", "col", items, at, found),
        LayoutAuthored::Grid(g) => {
            at.push(Step::Key("grid"));
            at.push(Step::Key("items"));
            for (i, cell) in g.items.iter_mut().flatten().enumerate() {
                if let Some(it) = &mut cell.item {
                    at.push(Step::Index(i));
                    at.push(Step::Key("item"));
                    item(it, &format!("grid{i}"), at, found)?;
                    at.truncate(at.len() - 2);
                }
            }
            at.truncate(at.len() - 2);
            Ok(())
        }
        LayoutAuthored::Tabs(t) => {
            at.push(Step::Key("tabs"));
            at.push(Step::Key("items"));
            for (i, tab) in t.items.iter_mut().flatten().enumerate() {
                if let Some(l) = &mut tab.layout {
                    at.push(Step::Index(i));
                    let outer = std::mem::replace(&mut found.used, found.names.clone());
                    let walked = layout(l, at, found);
                    found.used = outer;
                    walked?;
                    at.pop();
                }
            }
            at.truncate(at.len() - 2);
            Ok(())
        }
    }
}

fn list<'a>(
    key: &'a str,
    prefix: &str,
    items: &'a mut [LayoutItemAuthored],
    at: &mut Vec<Step<'a>>,
    found: &mut Found<'_>,
) -> Result<(), HoistError> {
    at.push(Step::Key(key));
    for (i, it) in items.iter_mut().enumerate() {
        at.push(Step::Index(i));
        item(it, &format!("{prefix}{i}"), at, found)?;
        at.pop();
    }
    at.pop();
    Ok(())
}

/// Python's at-least-one rule on a board, as pydantic words it.
pub const NO_CONTENT: &str =
    "Value error, Board must have at least one layout type, text, title, or chart";

/// Python's at-least-one rule: a layout, a text, a non-empty title or a chart. A macro because the
/// root's body is a patch and a nested board's is authored, with the same fields.
macro_rules! has_content {
    ($body:expr) => {{
        let body = &$body;
        body.layout.is_some()
            || matches!(body.text, Tri::Value(_))
            || !body.charts.is_empty()
            || matches!(&body.title, Tri::Value(t) if !t.as_str().is_empty())
    }};
}
pub(crate) use has_content;

/// A step as pydantic spells it in a `loc`.
fn step_loc(step: &Step) -> String {
    match step {
        Step::Key(k) => (*k).to_string(),
        Step::Index(i) => i.to_string(),
    }
}

fn item<'a>(
    it: &'a mut LayoutItemAuthored,
    fallback: &str,
    at: &mut Vec<Step<'a>>,
    found: &mut Found<'_>,
) -> Result<(), HoistError> {
    match it {
        LayoutItemAuthored::InlineChart(_) => {
            let id = ChartId::at(at);
            if let LayoutItemAuthored::InlineChart(chart) =
                std::mem::replace(it, LayoutItemAuthored::Chart(id.clone()))
            {
                let mut chart = *chart;
                let text = match chart.id_text() {
                    Tri::Value(t) => Some(t.as_str()),
                    _ => None,
                };
                let public = inline_id(text, fallback, &mut found.used);
                found.public.insert(id.clone(), public);
                at.push(Step::Key("query"));
                if let Some((key, mut query)) = hoist_query(&mut chart, at, found.file)? {
                    stamp(&mut query, found.scope.as_ref());
                    found.own_query(&key);
                    found.queries.push((key, query));
                }
                at.pop();
                found
                    .scopes
                    .inline
                    .insert(id.as_str().to_string(), found.scope.clone());
                found
                    .scopes
                    .board_of
                    .insert(id.as_str().to_string(), found.board.clone());
                found
                    .charts
                    .push((id.as_str().to_string(), String::new(), chart.into()));
            }
        }
        LayoutItemAuthored::Board(nested) => {
            if !has_content!(nested.body) {
                return Err(HoistError::FieldInvalid(Box::new(
                    diag::err_validation_field(
                        at.iter().map(step_loc).collect::<Vec<_>>().join("."),
                        NO_CONTENT,
                    ),
                )));
            }
            authored_names(nested.body.charts.keys())?;
            if nested.body.layout.is_none() {
                nested.body.layout = Some(default_rows(nested.body.charts.keys())?);
            }
            let inner = scope(&nested.body.source, found.scope.as_ref())?;
            found.scopes.boards.extend(inner.clone());
            let outer = std::mem::replace(&mut found.scope, inner);
            let board = ItemId::at(at);
            let folded = found
                .scope_of_board
                .under(
                    &nested.body.cache,
                    &nested.body.incremental,
                    &at.iter().map(step_loc).collect::<Vec<_>>(),
                )?;
            found.boards.insert(board.clone(), folded.clone());
            let outer_board = std::mem::replace(&mut found.board, board);
            let outer_board_scope = std::mem::replace(&mut found.scope_of_board, folded);
            let outer_used = std::mem::replace(&mut found.used, found.names.clone());
            for (name, chart) in nested.body.charts.iter_mut() {
                if let Some(chart) = chart {
                    let steps = [Step::Key("charts"), Step::Key(name), Step::Key("query")];
                    if let Some((key, query)) = hoist_query(chart, &steps, found.file)? {
                        found.pending.push(Pending {
                            chart: name.clone(),
                            board: found.board.clone(),
                            key,
                            query,
                        });
                    }
                }
            }
            found.below.extend(nested.body.charts.keys().cloned());
            for name in nested.body.charts.keys() {
                found
                    .scopes
                    .board_of
                    .insert(name.clone(), found.board.clone());
                found
                    .scopes
                    .declared_in
                    .insert(name.clone(), found.scope.clone());
            }
            let board_at: String = at.iter().map(step_loc).collect::<Vec<_>>().join(".");
            found
                .charts
                .extend(std::mem::take(&mut nested.body.charts).into_iter().map(
                    |(name, chart)| {
                        let at = format!("{board_at}.charts.{name}");
                        (name, at, chart)
                    },
                ));
            for (name, mut query) in std::mem::take(&mut nested.body.queries) {
                stamp(&mut query, found.scope.as_ref());
                found.own_query(&name);
                found.declared_in.insert(
                    name.clone(),
                    board_model::ids::ItemId::at(at).as_str().to_string(),
                );
                found.declared.push((name, query));
            }
            found.variable_boards.extend(
                nested
                    .body
                    .variables
                    .keys()
                    .map(|name| (name.clone(), found.board.clone())),
            );
            let mut vars = nested.body.variables.iter_mut().map(|(n, v)| {
                let options = match &v.options {
                    Tri::Value(o) => Some((&o.column, &o.query)),
                    _ => None,
                };
                let bound = bound_column(&v.column, options);
                let (options_slot, query_slot) = authored_query_slots(v);
                (n, bound, options_slot, query_slot)
            });
            hoist_variable_queries(&mut vars, at, found)?;
            if let Some(l) = &mut nested.body.layout {
                layout(l, at, found)?;
            }
            found.scope = outer;
            found.board = outer_board;
            found.scope_of_board = outer_board_scope;
            found.used = outer_used;
        }
        LayoutItemAuthored::Chart(id) => found.scopes.placed.push((
            id.as_str().to_string(),
            found.scope.clone(),
            at.iter().map(step_loc).collect::<Vec<_>>().join("."),
        )),
    }
    Ok(())
}

/// The `options.query` slot of a merged variable (if it declares options) and its `query` slot.
fn patch_query_slots(
    var: &mut VariablePatch,
) -> (Option<&mut Tri<OptionsQuery>>, &mut Tri<VariableQuery>) {
    let options = match &mut var.options {
        Tri::Value(options) => Some(&mut options.query),
        _ => None,
    };
    (options, &mut var.query)
}

/// The same two slots of an authored variable.
fn authored_query_slots(
    var: &mut VariableAuthored,
) -> (Option<&mut Tri<OptionsQuery>>, &mut Tri<VariableQuery>) {
    let options = match &mut var.options {
        Tri::Value(options) => Some(&mut options.query),
        _ => None,
    };
    (options, &mut var.query)
}

/// One variable to hoist from: its name, the `table.column` it binds (with the authored field it
/// is read from), its `options.query` slot if it declares options, and its `query` slot.
type VariableSlot<'a> = (
    &'a String,
    Option<(&'static str, String)>,
    Option<&'a mut Tri<OptionsQuery>>,
    &'a mut Tri<VariableQuery>,
);

/// A variable whose `options.query` is SQL text rather than a query name, or whose `query` is an
/// inline query, gets that query of its own, named for the place, and keeps that name. `below` is
/// the position of the board that declares the variables.
fn hoist_variable_queries<'a, 'f>(
    vars: &mut dyn Iterator<Item = VariableSlot<'a>>,
    below: &[Step<'a>],
    found: &mut Found<'f>,
) -> Result<(), HoistError> {
    for (var_name, bound, slot, query_slot) in vars {
        if let Some((field, column)) = &bound {
            check_column_binding(var_name, (field, column), found.scope.is_some())?;
        }
        let place = |tail: &'static [&'static str]| {
            let mut steps: Vec<Step> = below.to_vec();
            steps.extend([Step::Key("variables"), Step::Key(var_name)]);
            steps.extend(tail.iter().map(|k| Step::Key(k)));
            QueryName::at(&steps)
        };
        if let Some(slot) = slot {
            if let Tri::Value(OptionsQuery::Sql(sql)) = &*slot {
                let name = place(&["options", "query"]);
                let mut query = sql_query(sql)?;
                stamp(&mut query, found.scope.as_ref());
                found.synthetic.insert(name.as_str().to_string());
                found.own_query(name.as_str());
                found.queries.push((name.as_str().to_string(), query));
                *slot = Tri::Value(OptionsQuery::Name(name));
            }
        }
        if let Tri::Value(VariableQuery::Inline(inline)) = &*query_slot {
            let name = place(&["query"]);
            let mut query: <Query as Leaf>::Patch = (**inline).clone().into();
            stamp(&mut query, found.scope.as_ref());
            found.own_query(name.as_str());
            found.queries.push((name.as_str().to_string(), query));
            *query_slot = Tri::Value(VariableQuery::Name(name));
        }
    }
    Ok(())
}

/// The `table.column` a variable binds its options to (Python's `promote_column_option_queries`),
/// with the authored field it is read from: `options.column` when no `options.query` is set, else
/// the variable's own `column`.
fn bound_column(
    column: &Tri<String>,
    options: Option<(&Tri<String>, &Tri<OptionsQuery>)>,
) -> Option<(&'static str, String)> {
    let set = |t: &Tri<String>| match t {
        Tri::Value(c) if !c.is_empty() => Some(c.clone()),
        _ => None,
    };
    match options {
        Some((c, Tri::Unset | Tri::Null)) if set(c).is_some() => {
            set(c).map(|c| ("options.column", c))
        }
        _ => set(column).map(|c| ("column", c)),
    }
}

/// Python turns a bound column into a SQL query of the board's default source, and refuses a
/// reference that is no `table.column` of bare identifiers, or a board with no source to run it on.
fn check_column_binding(
    var: &str,
    (field, column): (&str, &str),
    has_source: bool,
) -> Result<(), HoistError> {
    let at = format!("variables.{var}.{field}");
    let Some((table, name)) = column.rsplit_once('.') else {
        return Err(HoistError::FieldInvalid(Box::new(
            diag::err_validation_field(
                at,
                format!(
                    "column reference {} must use 'table.column' dot notation (e.g. 'regions.name')",
                    py_str_repr(column)
                ),
            ),
        )));
    };
    let ident = |s: &str| {
        let mut chars = s.chars();
        chars
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
            && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
    };
    if !table.split('.').all(ident) {
        return Err(HoistError::refused(diag::err_variable_table_invalid(at, table)));
    }
    if !ident(name) {
        return Err(HoistError::refused(diag::err_variable_column_invalid(at, name)));
    }
    match has_source {
        true => Ok(()),
        false => Err(source_required(&format!("_var_options_{var}"))),
    }
}

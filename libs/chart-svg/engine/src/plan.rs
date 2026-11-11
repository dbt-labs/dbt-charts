//! `plan` (spec § 14): what a run of a compiled board reads and binds, as versioned JSON, so
//! execute needs neither the board nor its model. The queries are the ones a placed chart, layer,
//! variable or visibility probe reads, with every query they embed; the values are what
//! [`normalize::values::resolve`] makes of the caller's, dated on the clock this call is given.
//!
//! `plan(&CompiledBoard, given, now_epoch, time_zone, all_queries) -> Result<String, Diagnostic>`
//! returns the wire JSON `{version, board_id, time_zone, queries, variables: {query, render},
//! sources: {project, inline}}` that execute reads instead of the board. A query is planned if a placed chart or layer, a
//! variable (`query`, `options.query`, `enabled` probe), a `visible` probe or a `{{ queries.X }}` in
//! board, tab or chart display text reads it (text that does not parse reads none); `all_queries`
//! also plans every unplaced SQL query, which a warehouse check runs, and the caller states it. Every
//! entry carries its authored `formats`. A SQL query's entry carries `sql`, `setup_sql`, `dialect`,
//! its `variable_dependencies` (its own and those of every query it embeds) and its own
//! `incremental` watermark. The tab and details variables are added at their defaults.
//!
//! `sources.project` is the declarations the host passed to compile (`HostInputs.sources`, name
//! to declaration JSON, carried unread); `sources.inline` is the file sources its queries name
//! (`source: data/x.csv`), keyed by the ref as authored and shaped `{type, files: {stem: relpath}}`.
//! The first use of a ref decides its entry, and a ref the project declares is in `project` only.
//! Which of the two a name resolves to is execute's resolver's call, not the plan's.

use board_model::board::layout::{Layout, LayoutItem};
use board_model::board::value::Visible;
use board_model::board::{items, Board, BoardBody};
use board_model::ids::{ItemId, QueryId};
use board_model::query::{Incremental, Query};
use board_model::variable::{Enabled, OptionsQuery, VariableQuery};
use board_model::variable_value::{Atom, VariableValue};
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::{IndexMap, IndexSet};
use model_core::Spell;
use normalize::values::{resolve, Clock, Declared};
use normalize::{CompiledBoard, QueryFacts};
use serde::Serialize;
use serde_json::{Map, Value};
use std::collections::{BTreeMap, BTreeSet};
use template::Kind;

/// The wire version; a reader refuses another.
const VERSION: u32 = 1;

/// A field always on the wire, null when absent.
#[cfg(feature = "schema")]
fn nullable_text(_: &mut schemars::SchemaGenerator) -> schemars::Schema {
    schemars::json_schema!({"type": ["string", "null"]})
}

/// The wire document; `dbt_charts.core.execute.query_plan.QueryPlan` mirrors it field for field.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct Plan<'a> {
    pub version: u32,
    pub board_id: &'a str,
    pub time_zone: &'a str,
    pub queries: IndexMap<String, PlannedQuery<'a>>,
    pub variables: PlanVariables,
    pub sources: PlanSources<'a>,
}

/// `QueryPlan.variables`: the values by variable name, as `VariableValue::to_json` spells them.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlanVariables {
    pub query: Map<String, Value>,
    pub render: Map<String, Value>,
}

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlanSources<'a> {
    pub project: &'a BTreeMap<String, Value>,
    pub inline: &'a BTreeMap<String, Value>,
}

/// One planned query: the fields every kind carries, then the kind's own.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlannedQuery<'a> {
    #[serde(rename = "type")]
    pub kind: QueryKind,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub source: Option<String>,
    #[cfg_attr(feature = "schema", schemars(required))]
    pub cache: Option<Value>,
    pub formats: BTreeMap<String, &'a str>,
    pub depends_on: Vec<String>,
    #[serde(flatten)]
    pub own: PlannedOwn<'a>,
}

#[derive(Serialize, Clone, Copy)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
#[serde(rename_all = "lowercase")]
pub enum QueryKind {
    Sql,
    Values,
    Schema,
}

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
#[serde(untagged)]
pub enum PlannedOwn<'a> {
    Sql(PlannedSql<'a>),
    Data { spec: Value },
}

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlannedSql<'a> {
    pub sql: &'a str,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub setup_sql: Option<&'a str>,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub dialect: Option<String>,
    pub variable_dependencies: BTreeSet<String>,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub incremental: Option<&'a str>,
}

/// The plan of `compiled` for a run at `now_epoch` (seconds), dated in the board's `time_zone`,
/// with the variables the caller set as JSON. The clock is this call's own: a [`CompiledBoard`]
/// held across midnight must not date a preset on the day it was compiled. `all_queries` also
/// plans the SQL queries nothing places or reads, which a warehouse check runs.
pub fn plan(
    compiled: &CompiledBoard,
    given: &IndexMap<String, Value>,
    now_epoch: i64,
    time_zone: &str,
    all_queries: bool,
) -> Result<String, Diagnostic> {
    let board = compiled
        .board()
        .map_err(|e| diag::err_internal(format!("{e:?}")))?;
    let mut declared = Vec::new();
    let mut names = IndexSet::new();
    let mut walk = Walk {
        compiled,
        board: &board,
        declared: &mut declared,
        wanted: &mut names,
    };
    walk.body(&board.body, &ItemId::root())?;
    if all_queries {
        let sql = board
            .body
            .queries
            .iter()
            .filter(|(_, q)| matches!(q, Query::Sql(_)))
            .map(|(key, _)| key.clone());
        names.extend(sql);
    }
    let queries = queries(compiled, &board, names)?;

    let clock = clock(&board, now_epoch, time_zone)?;
    let generated = generated(compiled)?;
    let (set, own): (IndexMap<_, _>, IndexMap<_, _>) = given
        .clone()
        .into_iter()
        .partition(|(name, _)| !generated.contains_key(name));
    let mut resolved = resolve(&declared, &set, &clock)?;
    for (name, default) in generated {
        let value = match own.get(&name) {
            None => default,
            Some(Value::String(s)) => VariableValue::One(Atom::Text(s.clone())),
            Some(Value::Bool(b)) => VariableValue::One(Atom::Bool(*b)),
            Some(other) => {
                return Err(diag::err_variable_value_invalid(
                    name.as_str(),
                    format!("{other} is not a text or a flag"),
                ))
            }
        };
        resolved.query.insert(name.clone(), value.clone());
        resolved.render.insert(name, value);
    }
    let wire = |values: &IndexMap<String, VariableValue>| -> Map<String, Value> {
        values
            .iter()
            .map(|(k, v)| (k.clone(), v.to_json()))
            .collect()
    };
    let plan = Plan {
        version: VERSION,
        board_id: board.body.id.as_str(),
        time_zone,
        queries,
        variables: PlanVariables {
            query: wire(&resolved.query),
            render: wire(&resolved.render),
        },
        sources: PlanSources {
            project: &compiled.structure.project_sources,
            inline: &compiled.structure.inline_sources,
        },
    };
    serde_json::to_string(&plan).map_err(|e| diag::err_internal(e.to_string()))
}

/// The board's day, and how its weeks and fiscal years run (Python's `Regional.week_start`
/// and `fiscal_year_start_month`).
fn clock(board: &Board, now_epoch: i64, time_zone: &str) -> Result<Clock, Diagnostic> {
    let tag = crate::scope::time_tag(board)?;
    let regional = board.regional.as_ref();
    let week_start = display_text::locale::week_start(tag).map_err(diag::err_internal)?;
    let fiscal = regional
        .and_then(|r| r.fiscal_year_start_month.spell().as_u64())
        .unwrap_or(1);
    Clock::at(now_epoch, time_zone, fiscal as u32, week_start as u32).map_err(diag::err_internal)
}

/// The variables normalize generates for tabs and details, at their defaults, in tree order.
fn generated(compiled: &CompiledBoard) -> Result<IndexMap<String, VariableValue>, Diagnostic> {
    let layout = &compiled.structure.layout;
    let mut all = Vec::new();
    for (at, tabs) in &layout.tabs {
        let slug = tabs
            .slugs
            .get(tabs.default_tab)
            .ok_or_else(|| diag::err_internal(format!("no default tab at {}", at.as_str())))?;
        all.push((
            at,
            tabs.variable.as_str(),
            VariableValue::One(Atom::Text(slug.clone())),
        ));
    }
    for (at, d) in &layout.details {
        all.push((
            at,
            d.variable.as_str(),
            VariableValue::One(Atom::Bool(d.expanded)),
        ));
    }
    all.sort_by_key(|(at, ..)| at.as_str());
    Ok(all
        .into_iter()
        .map(|(_, name, value)| (name.to_string(), value))
        .collect())
}

/// The variables declared on every board, and the queries the layout reads, in layout order.
struct Walk<'a> {
    compiled: &'a CompiledBoard,
    board: &'a Board,
    declared: &'a mut Vec<Declared>,
    wanted: &'a mut IndexSet<String>,
}

/// The key a query holds in `Board.queries`: its name, or `<file>#<name>` from another file.
fn key_of(board: &Board, id: &QueryId) -> String {
    let printed = id.to_string();
    if board.body.queries.contains_key(&printed) {
        printed
    } else {
        id.name().as_str().to_string()
    }
}

impl Walk<'_> {
    fn want_name(&mut self, written: &str) {
        self.wanted.insert(
            written
                .strip_prefix("queries.")
                .unwrap_or(written)
                .to_string(),
        );
    }

    /// The queries a display text reads as `queries.<name>`. Text that does not parse reads none:
    /// bind refuses it at its own chart.
    fn text(&mut self, text: &str, kind: Kind) {
        if text.contains("queries") {
            if let Ok(info) = template::parse(text, kind) {
                self.wanted.extend(info.queries);
            }
        }
    }

    /// The titles and text of the tabs of `layout`, nested tabs included.
    fn tabs(&mut self, layout: &Layout) {
        if let Layout::Tabs(tabs) = layout {
            for tab in &tabs.items {
                self.text(tab.title.as_str(), Kind::Label);
                if let Some(text) = &tab.text {
                    self.text(text.as_str(), Kind::Markdown);
                }
                self.tabs(&tab.layout);
            }
        }
    }

    fn visible(&mut self, visible: Option<&Visible>) {
        if let Some(Visible::Probe(probe)) = visible {
            self.want_name(&probe.query);
        }
    }

    fn body(&mut self, body: &BoardBody, at: &ItemId) -> Result<(), Diagnostic> {
        for text in [
            body.title.as_ref().map(|t| t.as_str()),
            body.text.as_ref().map(|t| t.as_str()),
        ]
        .into_iter()
        .flatten()
        {
            self.text(text, Kind::Markdown);
        }
        for (name, var) in &body.variables {
            self.declared
                .push(Declared::of(name, var).map_err(diag::err_internal)?);
            if let Some(VariableQuery::Name(q)) = &var.query {
                self.want_name(q.as_str());
            }
            if let Some(OptionsQuery::Name(q)) = var.options.as_ref().and_then(|o| o.query.as_ref())
            {
                self.want_name(q.as_str());
            }
            if let Some(Enabled::Probe(probe)) = &var.enabled {
                self.want_name(&probe.query);
            }
        }
        self.tabs(&body.layout);
        for placement in items(body, at) {
            match placement.item {
                LayoutItem::Chart { id, visible } => {
                    self.visible(visible.as_ref());
                    let chart = self
                        .compiled
                        .structure
                        .patch
                        .body
                        .charts
                        .get(id.as_str())
                        .and_then(Option::as_ref)
                        .ok_or_else(|| {
                            diag::err_internal(format!("the placed chart {id} is not in the board"))
                        })?;
                    for text in chart.display_texts() {
                        self.text(text, Kind::Label);
                    }
                    for query in chart.hoisted_queries() {
                        let key = key_of(self.board, &query);
                        self.wanted.insert(key);
                    }
                }
                LayoutItem::Board(nested) => {
                    self.visible(nested.visible.as_ref());
                    self.body(&nested.body, &placement.id)?;
                }
            }
        }
        Ok(())
    }
}

/// Each wanted query and every query it embeds, in the order met.
fn queries<'a>(
    compiled: &'a CompiledBoard,
    board: &'a Board,
    mut wanted: IndexSet<String>,
) -> Result<IndexMap<String, PlannedQuery<'a>>, Diagnostic> {
    let ids: BTreeMap<String, (&QueryId, &QueryFacts)> = compiled
        .structure
        .queries
        .iter()
        .map(|(id, facts)| (key_of(board, id), (id, facts)))
        .collect();
    let mut out = IndexMap::new();
    let mut at = 0;
    while let Some(key) = wanted.get_index(at).cloned() {
        at += 1;
        let query = board.body.queries.get(&key).ok_or_else(|| {
            diag::err_internal(format!(
                "the plan reads the query {key}, which the board does not hold"
            ))
        })?;
        let mut embeds = Vec::new();
        if matches!(query, Query::Sql(_)) {
            let (id, facts) = ids
                .get(&key)
                .copied()
                .ok_or_else(|| diag::err_internal(format!("the SQL query {key} has no facts")))?;
            for name in &facts.embeds {
                let embedded = normalize::sql::query_id(name, id.file())
                    .map_err(|e| diag::err_internal(format!("{e:?}")))?;
                if !compiled.structure.queries.contains_key(&embedded) {
                    return Err(Diagnostic::refused(refuse!(
                        "the query {key} embedding {embedded}, a query compile does not hoist, is not supported"
                    )));
                }
                embeds.push(key_of(board, &embedded));
            }
        }
        wanted.extend(embeds.iter().cloned());
        let (base, kind, own) = match query {
            Query::Sql(q) => {
                let (id, _) = ids.get(&key).copied().ok_or_else(|| {
                    diag::err_internal(format!("the SQL query {key} has no facts"))
                })?;
                let own = PlannedOwn::Sql(PlannedSql {
                    sql: &q.sql,
                    setup_sql: q.setup_sql.as_deref(),
                    dialect: q.dialect.as_ref().map(spelled_text).transpose()?,
                    variable_dependencies: normalize::deps::query_variables(
                        id,
                        &compiled.structure.queries,
                    ),
                    incremental: match &q.base.incremental {
                        Some(Incremental::Column(column)) => Some(column.as_str()),
                        Some(Incremental::Off) | None => None,
                    },
                });
                (&q.base, QueryKind::Sql, own)
            }
            Query::Values(q) => (
                &q.base,
                QueryKind::Values,
                PlannedOwn::Data {
                    spec: authored(compiled, &key)?,
                },
            ),
            Query::Schema(q) => (
                &q.base,
                QueryKind::Schema,
                PlannedOwn::Data {
                    spec: authored(compiled, &key)?,
                },
            ),
        };
        let planned = PlannedQuery {
            kind,
            source: base.source.as_ref().map(spelled_text).transpose()?,
            cache: base.cache.as_ref().map(Spell::spell),
            formats: base
                .formats
                .iter()
                .map(|(c, f)| (c.clone(), f.as_str()))
                .collect(),
            depends_on: embeds,
            own,
        };
        out.insert(key, planned);
    }
    Ok(out)
}

/// A leaf the contract types as text, as it spells.
fn spelled_text(leaf: &impl Spell) -> Result<String, Diagnostic> {
    match leaf.spell() {
        Value::String(text) => Ok(text),
        other => Err(diag::err_internal(format!("{other} is not text"))),
    }
}

/// A values or schema query as authored: the rows or the coordinates execute reads.
fn authored(compiled: &CompiledBoard, key: &str) -> Result<Value, Diagnostic> {
    let query = compiled
        .structure
        .patch
        .body
        .queries
        .get(key)
        .and_then(Option::as_ref);
    query
        .map(Spell::spell)
        .ok_or_else(|| diag::err_internal(format!("the query {key} is not in the folded board")))
}

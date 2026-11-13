//! `plan` (spec § 14): what a run of a compiled board reads and binds, as versioned JSON, so
//! execute needs neither the board nor its model. The queries are the ones a placed chart, layer,
//! variable or visibility probe reads, with every query they embed; the values are what
//! [`normalize::values::resolve`] makes of the caller's, dated on the clock this call is given.
//!
//! `plan(&CompiledBoard, given, builtins, now_epoch, all_queries, active_tab_only, strict,
//! only_charts) -> Result<String, Diagnostic>` returns the wire JSON `{version, board_id, time_zone, today,
//! week_start, fiscal_year_start_month, prefetch, chart_queries, variable_queries, queries, variables: {query, render}, sources:
//! {project, inline}}` that execute reads instead of the board.
//!
//! The clock is the board's own: `regional.time_zone` (UTC when the board has no `regional`)
//! dates `now_epoch`. A caller cannot know the zone before it has compiled, and execute compiles
//! here and in Python at once, so the zone is read here and echoed in the header beside the rest
//! of the board clock: `today` (the civil date, `YYYY-MM-DD`, that dates presets), `week_start`
//! (0 = Sunday) and `fiscal_year_start_month`.
//!
//! A query is planned if a placed chart or layer, a variable (`query`, `options.query`, `enabled`
//! probe), a `visible` probe or a `{{ queries.X }}` in board, tab or chart display text reads it
//! (text that does not parse reads none). `all_queries` also plans every unplaced SQL query, which
//! a warehouse check runs. `active_tab_only` plans only what the tabs layouts show, as Python's
//! `scope_tabs` does: each tabs layout shows the tab its variable names in `given`, else its
//! default (an empty value is unset; a value naming no tab is `ERR-TAB-UNKNOWN`); the other tabs
//! are hidden with everything under them, nested tabs and boards included, their queries,
//! variable queries and tab titles or text included, though the variables they declare still
//! resolve. A details board is never hidden, expanded or not. `prefetch` then lists the queries
//! only hidden tabs read whose resolved cache policy has `prefetch` on, in the order met; it is
//! empty when `active_tab_only` is false. `all_queries` and `active_tab_only` do not combine (one
//! asks for more than the board reads, the other for less) and the call is an internal error (a caller bug).
//!
//! The engine names queries by what it hoists them to (an inline query is keyed by position, a
//! named one by its name), not by the synthetic names Python gives them, so the plan says which
//! query each chart and variable reads, by the keys of `queries`. `chart_queries` is
//! `{public chart id: {query, layers}}` for every chart the plan shows (a chart placed twice
//! appears once): `query` is the chart's own query key, `null` for a chart that reads none (a
//! callout), and `layers` has one entry per layer, in order, the layer's own query key or `null`
//! when it draws the chart's. `variable_queries` is `{variable name: {query, options, enabled}}`
//! for every declared variable the plan shows, nested boards' included (a name is unique across
//! the board): the keys of its `query:`, its `options.query:` and its `enabled:` probe, each
//! `null` when it has none. Both cover only what the plan plans: a chart or variable on a hidden
//! tab under `active_tab_only`, or not focused by `only_charts`, is absent.
//!
//! Every entry carries its authored `formats` and its `cache`, the resolved policy
//! `{enabled, ttl, prefetch}` (project root, source, board, query: `Structure.policies`). A SQL
//! query's entry carries `sql`, `setup_sql`, `dialect`, its `variable_dependencies` (its own and
//! those of every query it embeds) and its effective `incremental` watermark (its own, else its
//! board's). The tab and details variables are added at their defaults.
//!
//! `only_charts` (`None` plans the whole board) is Python's `focus_on_chart` for each named chart,
//! by the id users see (`Chart.id`): the plan reads the named charts alone, as if they were the
//! board's whole layout, so it holds their queries (layers and display text included) and what
//! those embed, the board's own `text:` queries (its title is dropped), and only the variables the
//! charts depend on, with the queries those read. Variables of the other charts, the tab and
//! details variables and everything a `visible` probe reads are not on the focused board, so they
//! are not in the plan. A chart on a hidden tab is planned as any other. `active_tab_only` then
//! has nothing to scope (the focused board has no tabs) and `prefetch` is empty. A chart id the
//! board does not have is `ERR-UNKNOWN-CHART`, never an empty plan. A chart a nested board or
//! another file declares is planned like any other (names are board-global). A variable a nested
//! board declares is planned when a named chart depends on it, and the chart widens through the
//! variables of its own board alone, as `focus_on_chart` does: a variable of another board is
//! planned by name and what it reads is not followed. (`focus_on_item` keeps a nested board's
//! variables wholesale, so for a markdown fence the plan omits the variables no chart reads.)
//! `only_charts` does not combine with `all_queries` and may not be empty (callers' bugs,
//! `ERR-INTERNAL` naming what to pass).
//!
//! `strict` mirrors Python's `strict_variables`: when true a name in `given` the board does not
//! declare is `ERR-UNKNOWN-VARIABLE-OVERRIDE`; when false it is ignored. `builtins` (name to JSON
//! value, such as the directory-navigation variables a host supplies) are echoed verbatim, not
//! kind-tagged, into both `variables` views with no declaration check; a name that is also a
//! declared or generated variable is `ERR-BUILTIN-SHADOWS-VARIABLE`.
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
use board_model::query::Query;
use board_model::variable::{Enabled, OptionsQuery, Variable, VariableQuery};
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

/// The query keys a placed chart reads.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct ChartQueries {
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub query: Option<String>,
    pub layers: Vec<Option<String>>,
}

/// The query keys a declared variable reads.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct VariableQueries {
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub query: Option<String>,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub options: Option<String>,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub enabled: Option<String>,
}

/// The wire document; `dbt_charts.core.execute.query_plan.QueryPlan` mirrors it field for field.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct Plan<'a> {
    pub version: u32,
    pub board_id: &'a str,
    pub time_zone: &'a str,
    pub today: String,
    pub week_start: u32,
    pub fiscal_year_start_month: u32,
    pub prefetch: Vec<String>,
    pub chart_queries: IndexMap<String, ChartQueries>,
    pub variable_queries: IndexMap<String, VariableQueries>,
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
    /// The preset id of each daterange variable whose value is a preset (its default or the
    /// caller's), by name: what render publishes and links by. Fixed dates have no entry.
    pub presets: IndexMap<String, String>,
}

#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlanSources<'a> {
    #[cfg_attr(
        feature = "schema",
        schemars(with = "BTreeMap<String, BTreeMap<String, Value>>")
    )]
    pub project: &'a BTreeMap<String, Value>,
    #[cfg_attr(
        feature = "schema",
        schemars(with = "BTreeMap<String, BTreeMap<String, Value>>")
    )]
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
    pub cache: PlannedCache<'a>,
    pub formats: BTreeMap<String, &'a str>,
    pub depends_on: Vec<String>,
    #[serde(flatten)]
    pub own: PlannedOwn<'a>,
}

/// A query's cache policy resolved through project root, source, board and query.
#[derive(Serialize)]
#[cfg_attr(feature = "schema", derive(schemars::JsonSchema))]
pub struct PlannedCache<'a> {
    pub enabled: bool,
    #[cfg_attr(feature = "schema", schemars(required, schema_with = "nullable_text"))]
    pub ttl: Option<&'a str>,
    pub prefetch: bool,
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

/// The plan of `compiled` for a run at `now_epoch` (seconds), dated in the board's own time zone,
/// with the variables the caller set as JSON. The clock is this call's own: a [`CompiledBoard`]
/// held across midnight must not date a preset on the day it was compiled.
pub fn plan(
    compiled: &CompiledBoard,
    given: &IndexMap<String, Value>,
    builtins: &IndexMap<String, Value>,
    now_epoch: i64,
    all_queries: bool,
    active_tab_only: bool,
    strict: bool,
    only_charts: Option<&[String]>,
) -> Result<String, Diagnostic> {
    if all_queries && only_charts.is_some() {
        return Err(diag::err_internal(
            "plan was called with both all_queries and only_charts: all_queries plans queries no chart reads and only_charts plans only the named charts. Pass one of them",
        ));
    }
    if only_charts.is_some_and(<[String]>::is_empty) {
        return Err(diag::err_internal(
            "plan was called with an empty only_charts, which would plan nothing. Pass None for the whole board, or name at least one chart",
        ));
    }
    if all_queries && active_tab_only {
        return Err(diag::err_internal(
            "plan was called with both all_queries and active_tab_only: all_queries plans queries no tab shows and active_tab_only plans only the open tabs. Pass one of them",
        ));
    }
    let board = compiled
        .board()
        .map_err(|e| diag::err_internal(format!("{e:?}")))?;
    // A focused board is the named charts alone, so it has no tabs or details to generate.
    let generated = match only_charts {
        None => generated(compiled)?,
        Some(_) => IndexMap::new(),
    };
    let open = (active_tab_only && only_charts.is_none())
        .then(|| open_tabs(compiled, given))
        .transpose()?;
    let mut declared = Vec::new();
    let mut walk = Walk {
        compiled,
        board: &board,
        declared: &mut declared,
        all: IndexSet::new(),
        open: IndexSet::new(),
        live: true,
        chart_queries: IndexMap::new(),
        variable_queries: IndexMap::new(),
        choice: open.as_ref(),
    };
    match only_charts {
        None => walk.body(&board.body, &ItemId::root())?,
        Some(ids) => walk.focused(&board.body, ids)?,
    }
    let (all, open_names) = (walk.all, walk.open);
    let (chart_queries, variable_queries) = (walk.chart_queries, walk.variable_queries);
    let scoped = active_tab_only && only_charts.is_none();
    let mut names = if scoped { open_names } else { all.clone() };
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
    let prefetch = if scoped {
        hidden_prefetch(compiled, &board, all, &queries)?
    } else {
        Vec::new()
    };

    let clock = clock(&board, now_epoch)?;
    let (set, own): (IndexMap<_, _>, IndexMap<_, _>) = given
        .clone()
        .into_iter()
        .partition(|(name, _)| !generated.contains_key(name));
    let set: IndexMap<_, _> = if strict {
        set
    } else {
        set.into_iter()
            .filter(|(name, _)| declared.iter().any(|d| d.name() == *name))
            .collect()
    };
    let mut resolved = resolve(&declared, &set, &clock, strict)?;
    for name in builtins.keys() {
        if let Some(origin) = origin_of(compiled, &declared, name) {
            return Err(diag::err_builtin_shadows_variable(name.as_str(), origin));
        }
    }
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
            .chain(builtins.iter().map(|(k, v)| (k.clone(), v.clone())))
            .collect()
    };
    let (year, month, day) = clock.today;
    let time_zone = zone(&board);
    let plan = Plan {
        version: VERSION,
        board_id: board.body.id.as_str(),
        time_zone: &time_zone,
        today: format!("{year:04}-{month:02}-{day:02}"),
        week_start: clock.week_start,
        fiscal_year_start_month: clock.fiscal_start_month,
        prefetch,
        chart_queries,
        variable_queries,
        queries,
        variables: PlanVariables {
            query: wire(&resolved.query),
            render: wire(&resolved.render),
            presets: resolved.presets,
        },
        sources: PlanSources {
            project: &compiled.structure.project_sources,
            inline: &compiled.structure.inline_sources,
        },
    };
    serde_json::to_string(&plan).map_err(|e| diag::err_internal(e.to_string()))
}

/// How the board comes by a variable named `name`, for a message; `None` when it has none.
fn origin_of(compiled: &CompiledBoard, declared: &[Declared], name: &str) -> Option<String> {
    let layout = &compiled.structure.layout;
    if declared.iter().any(|d| d.name() == name) {
        return Some("the board declares under `variables:`".to_string());
    }
    if let Some((at, _)) = layout.tabs.iter().find(|(_, t)| t.variable == name) {
        return Some(format!(
            "the tabs layout at {at} generates to hold its open tab, from its `id:` or else the board's id"
        ));
    }
    let (at, _) = layout.details.iter().find(|(_, d)| d.variable == name)?;
    Some(format!(
        "the details board at {at} generates to hold whether it is open, from its `id:` or else its place in the layout"
    ))
}

/// The board's IANA time zone: its `regional.time_zone`, UTC when it has no `regional`.
fn zone(board: &Board) -> String {
    crate::scope::zone_name(board.regional.as_ref()).to_string()
}

/// The tab each tabs layout shows, by the position of the board or pane that owns it: the tab its
/// variable names in `given`, else its default (`resolve_active_tab_index`). Only null, `false` and `""` are unset; any other value that names no
/// tab, a number, list or object included, is `ERR-TAB-UNKNOWN`: malformed input is refused, never
/// quietly replaced by the default.
fn open_tabs(
    compiled: &CompiledBoard,
    given: &IndexMap<String, Value>,
) -> Result<BTreeMap<ItemId, usize>, Diagnostic> {
    let mut open = BTreeMap::new();
    for (at, tabs) in &compiled.structure.layout.tabs {
        let index = match given.get(&tabs.variable) {
            None | Some(Value::Null) | Some(Value::Bool(false)) => tabs.default_tab,
            Some(Value::String(s)) if s.is_empty() => tabs.default_tab,
            Some(value) => {
                let text = value
                    .as_str()
                    .map_or_else(|| value.to_string(), str::to_string);
                tabs.slugs.iter().position(|s| *s == text).ok_or_else(|| {
                    diag::err_tab_unknown(tabs.variable.as_str(), text, tabs.slugs.clone())
                })?
            }
        };
        open.insert(at.clone(), index);
    }
    Ok(open)
}

/// The queries only hidden tabs read (`all` less `planned`, each with what it embeds) whose
/// resolved policy prefetches, in the order met.
fn hidden_prefetch(
    compiled: &CompiledBoard,
    board: &Board,
    all: IndexSet<String>,
    planned: &IndexMap<String, PlannedQuery<'_>>,
) -> Result<Vec<String>, Diagnostic> {
    let every = queries(compiled, board, all)?;
    let mut hidden = Vec::new();
    for key in every.keys().filter(|k| !planned.contains_key(*k)) {
        let policy =
            compiled.structure.policies.get(key).ok_or_else(|| {
                diag::err_internal(format!("the query {key} has no resolved policy"))
            })?;
        if policy.cache.prefetch {
            hidden.push(key.clone());
        }
    }
    Ok(hidden)
}

/// The board's day, and how its weeks and fiscal years run (Python's `Regional.week_start`
/// and `fiscal_year_start_month`).
fn clock(board: &Board, now_epoch: i64) -> Result<Clock, Diagnostic> {
    let tag = crate::scope::time_tag(board.regional.as_ref())?;
    let regional = board.regional.as_ref();
    let week_start = display_text::locale::week_start(tag).map_err(diag::err_internal)?;
    let fiscal = regional
        .and_then(|r| r.fiscal_year_start_month.spell().as_u64())
        .unwrap_or(1);
    Clock::at(now_epoch, &zone(board), fiscal as u32, week_start as u32).map_err(diag::err_internal)
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
    /// Every query the board reads, hidden tabs included.
    all: IndexSet<String>,
    /// The queries read from where the open tabs show, which `all` is a superset of.
    open: IndexSet<String>,
    /// Whether the part of the board being walked is shown.
    live: bool,
    /// Each shown chart's queries by public id, and each shown variable's by name.
    chart_queries: IndexMap<String, ChartQueries>,
    variable_queries: IndexMap<String, VariableQueries>,
    /// The tab each tabs layout shows, when scoping; every tab is shown otherwise.
    choice: Option<&'a BTreeMap<ItemId, usize>>,
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
    fn want(&mut self, key: String) {
        if self.live {
            self.open.insert(key.clone());
        }
        self.all.insert(key);
    }

    /// Reads the query written as `written` (`queries.` prefix optional); its plan key.
    fn want_name(&mut self, written: &str) -> String {
        let key = written
            .strip_prefix("queries.")
            .unwrap_or(written)
            .to_string();
        self.want(key.clone());
        key
    }

    /// Whether the tabs layout owned by `at` shows its tab `index`.
    fn shows(&self, at: &ItemId, index: usize) -> Result<bool, Diagnostic> {
        let Some(choice) = self.choice else {
            return Ok(true);
        };
        let open = choice
            .get(at)
            .ok_or_else(|| diag::err_internal(format!("no open tab at {}", at.as_str())))?;
        Ok(*open == index)
    }

    /// The queries a display text reads as `queries.<name>`. Text that does not parse reads none:
    /// bind refuses it at its own chart.
    fn text(&mut self, text: &str, kind: Kind) {
        if text.contains("queries") {
            if let Ok(info) = template::parse(text, kind) {
                for query in info.queries {
                    self.want(query);
                }
            }
        }
    }

    /// The titles and text of the tabs of `layout` (owned by `at`), nested tabs included; a
    /// hidden tab's are read by no one.
    fn tabs(&mut self, layout: &Layout, at: &ItemId) -> Result<(), Diagnostic> {
        if let Layout::Tabs(tabs) = layout {
            let outer = self.live;
            for (i, tab) in tabs.items.iter().enumerate() {
                self.live = outer && self.shows(at, i)?;
                self.text(tab.title.as_str(), Kind::Label);
                if let Some(text) = &tab.text {
                    self.text(text.as_str(), Kind::Markdown);
                }
                self.tabs(&tab.layout, &at.key("tabs").key("items").index(i))?;
            }
            self.live = outer;
        }
        Ok(())
    }

    fn visible(&mut self, visible: Option<&Visible>) {
        if let Some(Visible::Probe(probe)) = visible {
            self.want_name(&probe.query);
        }
    }

    /// Declares `var` and reads its queries: `query`, `options.query` and an `enabled` probe.
    fn variable(&mut self, name: &str, var: &Variable) -> Result<(), Diagnostic> {
        self.declared
            .push(Declared::of(name, var).map_err(diag::err_internal)?);
        let query = match &var.query {
            Some(VariableQuery::Name(q)) => Some(self.want_name(q.as_str())),
            _ => None,
        };
        let options = match var.options.as_ref().and_then(|o| o.query.as_ref()) {
            Some(OptionsQuery::Name(q)) => Some(self.want_name(q.as_str())),
            _ => None,
        };
        let enabled = match &var.enabled {
            Some(Enabled::Probe(probe)) => Some(self.want_name(&probe.query)),
            _ => None,
        };
        if self.live {
            self.variable_queries.insert(
                name.to_string(),
                VariableQueries {
                    query,
                    options,
                    enabled,
                },
            );
        }
        Ok(())
    }

    /// The queries chart `id` reads: its display text, its own and its layers'.
    fn chart(&mut self, id: &str) -> Result<(), Diagnostic> {
        let chart = self
            .compiled
            .structure
            .patch
            .body
            .charts
            .get(id)
            .and_then(Option::as_ref)
            .ok_or_else(|| {
                diag::err_internal(format!("the placed chart {id} is not in the board"))
            })?;
        match self.compiled.structure.index.text_queries.get(id) {
            // An imported chart's text reads the queries of the file that declares it.
            Some(read) => {
                for query in read.values() {
                    let key = key_of(self.board, query);
                    self.want(key);
                }
            }
            None => {
                for text in chart.display_texts() {
                    self.text(text, Kind::Label);
                }
            }
        }
        let (own, layers) = chart.query_slots();
        let mut key = |id: Option<QueryId>| {
            id.map(|id| {
                let key = key_of(self.board, &id);
                self.want(key.clone());
                key
            })
        };
        let (own, layers) = (
            key(own),
            layers.into_iter().map(&mut key).collect::<Vec<_>>(),
        );
        if self.live {
            let public = self
                .compiled
                .structure
                .index
                .public_ids
                .iter()
                .find(|(chart, _)| chart.as_str() == id)
                .map(|(_, public)| public.clone())
                .ok_or_else(|| diag::err_internal(format!("the chart {id} has no public id")))?;
            self.chart_queries
                .insert(public, ChartQueries { query: own, layers });
        }
        Ok(())
    }

    /// The board `body` focused on the charts `ids` name (Python's `focus_on_chart`): the charts
    /// alone in the layout, the board's text but not its title, and only the variables the charts
    /// depend on. Names are board-global, so a chart placed more than once is read once.
    fn focused(&mut self, body: &BoardBody, ids: &[String]) -> Result<(), Diagnostic> {
        let structure = &self.compiled.structure;
        let mut keys = Vec::new();
        for id in ids {
            let key = structure
                .index
                .public_ids
                .iter()
                .find(|(_, public)| *public == id)
                .map(|(key, _)| key.as_str())
                .filter(|key| matches!(structure.patch.body.charts.get(*key), Some(Some(_))));
            let Some(key) = key else {
                let mut available: Vec<&str> = structure
                    .index
                    .public_ids
                    .values()
                    .map(String::as_str)
                    .collect();
                available.sort_unstable();
                return Err(diag::err_unknown_chart(id.as_str(), available));
            };
            keys.push(key);
        }
        let mut needed = std::collections::BTreeSet::new();
        for key in &keys {
            let deps = structure.deps.charts.get(*key).ok_or_else(|| {
                diag::err_internal(format!("the chart {key} has no variable dependencies"))
            })?;
            needed.extend(deps.iter().cloned());
        }
        if let Some(text) = &body.text {
            self.text(text.as_str(), Kind::Markdown);
        }
        self.needed_variables(body, &ItemId::root(), &needed)?;
        for key in keys {
            self.chart(key)?;
        }
        Ok(())
    }

    /// The variables of `body` and of the boards nested in it that `needed` names: names are
    /// board-global, and Python's focus keeps a nested board's variables with the board.
    fn needed_variables(
        &mut self,
        body: &BoardBody,
        at: &ItemId,
        needed: &std::collections::BTreeSet<String>,
    ) -> Result<(), Diagnostic> {
        for (name, var) in &body.variables {
            if needed.contains(name) {
                self.variable(name, var)?;
            }
        }
        for placement in items(body, at) {
            if let LayoutItem::Board(nested) = placement.item {
                self.needed_variables(&nested.body, &placement.id, needed)?;
            }
        }
        Ok(())
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
            self.variable(name, var)?;
        }
        self.tabs(&body.layout, at)?;
        let outer = self.live;
        for placement in items(body, at) {
            let mut pane_at = at.clone();
            let mut shown = outer;
            for &index in &placement.pane {
                shown = shown && self.shows(&pane_at, index)?;
                pane_at = pane_at.key("tabs").key("items").index(index);
            }
            self.live = shown;
            match placement.item {
                LayoutItem::Chart { id, visible } => {
                    self.visible(visible.as_ref());
                    self.chart(id.as_str())?;
                }
                LayoutItem::Board(nested) => {
                    self.visible(nested.visible.as_ref());
                    self.body(&nested.body, &placement.id)?;
                }
            }
        }
        self.live = outer;
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
        let policy =
            compiled.structure.policies.get(&key).ok_or_else(|| {
                diag::err_internal(format!("the query {key} has no resolved policy"))
            })?;
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
                    incremental: policy.incremental.as_deref(),
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
            cache: PlannedCache {
                enabled: policy.cache.enabled,
                ttl: policy.cache.ttl.as_deref(),
                prefetch: policy.cache.prefetch,
            },
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

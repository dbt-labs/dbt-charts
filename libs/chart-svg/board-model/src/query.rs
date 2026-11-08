//! `query/authored.py` and `query/normalized.py`: one [`Query`] per concept.
//!
//! Python keeps an authored union (`type`) and a normalized one (`query_type`); the
//! spine has one tagged enum, `type: sql | values | schema`. The tag is inferred from
//! the keys when absent (`rows`, `values` or `columns` make a values query, else SQL);
//! a schema query is never inferred.
//!
//! - [`QueryBase`] is `_BaseQueryFields`, shared by every variant through `#[flatten]`.
//! - A query's `source` is a reference by name ([`SourceName`]); the warehouse source
//!   configs (`models/source.py`) belong to `Config` and stay out (D-03). The authored
//!   inline-dict arm of `source` dissolves into a type error with a hint.
//! - Declared columns: a query declares a column's *format* in `formats`, and a values query
//!   its columns by name. Python has no authored per-column type: a column's type is the
//!   source's (execute) or read off a values literal (`declared_schema`), so the type is
//!   `column-types`' and `SqlAnalysis`'s, not a spine field.
//! - The compact values query (`columns` + `values`) is sugar for `rows`. The derive's
//!   `accept` is a leaf attribute and cannot rewrite two sibling keys into a third, so
//!   [`ValuesQuery`] keeps the compact keys beside `rows` and the constraints that make
//!   the shapes exclusive; normalize's `fill` of `rows` consumes them.
//! - Left off the spine: `variable_dependencies`, `parse_error` (derived, `BoardIndex` and
//!   `SqlAnalysis`), `lenient_variables` (execute's working state), `limit` (never authored),
//!   `source_description` (derived), and the resolved `cache` policy; the authored `cache:` layer
//!   is [`crate::cache`]'s.
//!
//! Dissolved validators: `_validate_incremental` is [`Incremental`]'s read;
//! `SchemaQuery._no_jinja` is [`LiteralText`]; the schema query's `Requires` rules are
//! `requires` constraints; `normalize_query_value` is the query union's tag inference
//! plus the board's `QueryOrRef` accept arm (bare SQL string, `{ref}`), not ported here.

use crate::cache::CacheLayer;
use crate::facet::{facet, DisplayText, Facet, FormatName};
use crate::names::WarningCode;
use crate::string_enum;
use indexmap::IndexMap;
use model_core::{Cx, FromNode, Keywords, Leaf, Merge, Node, ReadScalar, Scalar, TypeExpr};
use model_derive::Model;
use serde_json::Value;

string_enum!(
    /// Every warehouse dialect, one spelling each (the dialect registry's names minus its aliases).
    QueryDialect {
        Athena = "athena", Bigquery = "bigquery", Clickhouse = "clickhouse",
        Databricks = "databricks", Duckdb = "duckdb", Fabric = "fabric",
        Lakecompute = "lakecompute", Mysql = "mysql", Postgres = "postgres",
        Presto = "presto", Redshift = "redshift", Snowflake = "snowflake",
        Spark = "spark", Sqlite = "sqlite", Sqlserver = "sqlserver", Trino = "trino",
    }
);

facet!(
    /// The name of a configured source, resolved against `dbt_charts.yml` by normalize.
    SourceName = "source_name",
    |v| Ok(v.to_string())
);

fn literal_text(v: &str) -> Result<String, String> {
    if ["{{", "{%", "{#"].iter().any(|t| v.contains(t)) {
        return Err("schema fields must be literal strings; Jinja templates ({{ }}, {% %}, {# #}) are not supported.".into());
    }
    Ok(v.to_string())
}

facet!(
    /// A string with no Jinja in it: a schema query's coordinates are never templated.
    LiteralText = "literal_text",
    literal_text
);

/// A literal value of inline data: any JSON value.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell(pub Value);

impl ReadScalar for Cell {
    fn read(node: &Node, _: &mut Cx) -> Option<Self> {
        Some(Self(node.to_json()))
    }
}

impl FromNode for Cell {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for Cell {
    type Whole = Cell;
    type Authored = Option<Cell>;
    type Patch = Option<Cell>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Scalar(Scalar::Json);
}
model_core::build_leaf!(Cell);

/// The watermark of an incremental refresh: a column, or `false` to opt out of the board's.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Incremental {
    /// `incremental: false`: this query refreshes in full whatever the board says.
    Off,
    /// The monotonic column to fetch past.
    Column(String),
}

const NON_BLANK: Keywords = Keywords::pattern(r"\S");

impl ReadScalar for Incremental {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        match node.to_json() {
            Value::Bool(false) => Some(Self::Off),
            Value::Bool(true) => {
                cx.invalid(node.span, "incremental: true is not valid — give the watermark column name instead, e.g. incremental: updated_at");
                None
            }
            Value::String(s) if s.chars().any(|c| !c.is_whitespace()) => Some(Self::Column(s)),
            _ => {
                cx.invalid(node.span, "incremental: give the watermark column name, or omit the field (or set false) to disable incremental refresh");
                None
            }
        }
    }
}

impl FromNode for Incremental {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for Incremental {
    type Whole = Incremental;
    type Authored = Option<Incremental>;
    type Patch = Option<Incremental>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Either(
        &TypeExpr::Keyed(&NON_BLANK, &TypeExpr::Scalar(Scalar::Str)),
        &TypeExpr::Scalar(Scalar::Bool),
    );
}
model_core::build_leaf!(Incremental);

/// Fields shared by every query type.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct QueryBase {
    /// Source name reference, or an inline file path (e.g. `./data/sales.csv`). File paths are detected by `/` or data file extension. An inline connection-bearing mapping (`{type: postgres, ...}`) is rejected; reference a named source instead.
    #[model(absent = "the board's default source")]
    pub source: Option<SourceName>,
    /// What this query returns; handed to tooling with its results, never drawn.
    #[model(absent = "no note")]
    pub notes: Option<DisplayText>,
    /// Diagnostic codes to suppress for this query (e.g., ['WARN-MISSING-JOIN-PREDICATE', 'WARN-REAGGREGATION']).
    #[model(absent = "suppresses nothing")]
    pub ignore: Option<Vec<WarningCode>>,
    /// Cache policy override for this query, e.g. cache: 5m: refines the policy inherited from the source and project scopes; cache: false opts out of result caching entirely. Queries with cache: false cannot be used as {{ queries.X.cache }} upstream references.
    #[model(absent = "the policy inherited from the source and project scopes")]
    pub cache: Option<CacheLayer>,
    /// A column's format, set once on its query, only where the default would misread (currency, percent, a unit); counts and plain numbers need none. Every chart showing the column follows it; a chart sets its own only to differ. A d3 spec is rejected. No entry leaves the column on the default for its kind.
    pub formats: IndexMap<String, FormatName>,
    /// Column used as the monotonic watermark for incremental refresh: the executor fetches only rows after the prior watermark and merges them with the cached result. `incremental: false` opts this query out of a board's incremental setting. Inherits from the board-level incremental setting when omitted.
    #[model(absent = "the board's incremental setting, else a full refresh")]
    pub incremental: Option<Incremental>,
}

/// Raw SQL query: the default query type.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct SqlQuery {
    #[flatten]
    pub base: QueryBase,
    /// The SQL to run, as Jinja2. `{{ queries.base }}` inlines another query of the same source as a subquery already aliased to its name (add no `AS`); a bare `FROM base` fails. Variables go in through `filter()`, `filter_date_range()` or `{{ name }}`.
    pub sql: String,
    /// Non-nestable SQL preamble executed before the main query (e.g., CREATE TEMP FUNCTION).
    #[model(absent = "no preamble")]
    pub setup_sql: Option<String>,
    /// Warehouse dialect the SQL is written in, translated to the source's.
    #[model(absent = "the source's own dialect")]
    pub dialect: Option<QueryDialect>,
}

/// One inline row: column name to literal, in authored order.
#[derive(Debug, Clone, PartialEq)]
pub struct Row(pub IndexMap<String, Cell>);

impl ReadScalar for Row {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Value::Object(cells) = node.to_json() else {
            cx.invalid(node.span, "an inline row is a mapping of column to value");
            return None;
        };
        Some(Self(cells.into_iter().map(|(k, v)| (k, Cell(v))).collect()))
    }
}

impl FromNode for Row {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

impl Leaf for Row {
    type Whole = Row;
    type Authored = Option<Row>;
    type Patch = Option<Row>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Map(&TypeExpr::Scalar(Scalar::Json));
}
model_core::build_leaf!(Row);

/// Inline data: rows as mappings, or `columns` plus positional `values`.
#[derive(Debug, Clone, PartialEq, Model)]
// One constraint names one rule, so the same kind repeats.
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(at_least_one_of(rows, values)),
    constraint(at_most_one_of(rows, values)),
    constraint(at_most_one_of(rows, columns)),
    constraint(requires(columns, when(set(values)))),
    constraint(requires(values, when(set(columns))))
)]
pub struct ValuesQuery {
    #[flatten]
    pub base: QueryBase,
    /// Inline data rows (list of row dicts). Use the compact columns-and-values form instead when row values are positional.
    #[model(fill = rows_from_values)]
    pub rows: Vec<Row>,
    /// Column names for the compact syntax. Every compact values query also requires values.
    #[model(absent = "the rows' own keys, in first-seen order")]
    pub columns: Option<Vec<String>>,
    /// Inline row-oriented data (list of lists) for the compact syntax. Every compact values query also requires columns.
    #[model(absent = "authored as rows")]
    pub values: Option<Vec<Vec<Cell>>>,
}

crate::style::axis::non_empty_list!(
    /// The keys a schema query projects its rows to: at least one.
    Fields,
    LiteralText
);

/// dbt source schema query.
#[derive(Debug, Clone, PartialEq, Model)]
#[allow(clippy::duplicated_attributes)]
#[model(
    constraint(requires(source, when(set(schema)))),
    constraint(requires(schema, when(set(table)))),
    constraint(requires(table, when(set(column))))
)]
pub struct SchemaQuery {
    #[flatten]
    pub base: QueryBase,
    /// Which schema to inspect; lists every schema in the source if omitted (YAML key: schema).
    #[model(absent = "every schema in the source")]
    pub schema: Option<LiteralText>,
    /// Which table to inspect; lists the schema's tables if omitted.
    #[model(absent = "every table in the schema")]
    pub table: Option<LiteralText>,
    /// Which column to profile; profiles every column in the table if omitted.
    #[model(absent = "every column in the table")]
    pub column: Option<LiteralText>,
    /// Project the result rows to exactly these keys, in this order: the schema-query counterpart of a SQL SELECT list. A projected key a row lacks yields null. Omit to return every key each row carries.
    #[model(absent = "every key each row carries")]
    pub fields: Option<Fields>,
}

/// The query type an author left out, from the keys present.
fn infer_type(present: &[&str]) -> Option<&'static str> {
    if present.contains(&"schema") {
        return None;
    }
    let values = ["rows", "values", "columns"]
        .iter()
        .any(|k| present.contains(k));
    Some(if values { "values" } else { "sql" })
}

/// A query: raw SQL, inline values, or a dbt schema lookup.
#[derive(Debug, Clone, PartialEq, Model)]
#[model(tag = "type", fill = infer_type)]
pub enum Query {
    Sql(SqlQuery),
    Values(ValuesQuery),
    Schema(SchemaQuery),
}

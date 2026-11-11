//! What the SQL seam hands back for one query (spec § 16, D-18), as plain data, and the trait a
//! host implements to produce it.
//!
//! Compile hands a host-injected [`SqlAnalysis`] (first the Python host over sqlglot, later a
//! Rust one) each SQL query's [`SqlRequest`] and gets a [`SqlOutcome`] back. This module holds no
//! parser and reads no SQL. Normalize puts the pieces where they belong (the parse error and the
//! output columns into the query's facts, the lints and the guard's verdict into refusals, the
//! lineage where `bind` reads it); a fixture carries an `Analysis` by value so `bind` can be built
//! without a SQL parser.
//!
//! Every position is SQL-local: relative to the query's own text, the coordinates a board-file
//! host offsets into the file.

use crate::facet::FormatName;
use crate::query::{QueryDialect, SourceName};

/// One SQL query as the host sees it: exactly the authored text, Jinja intact. The host builds the
/// skeleton, and resolves the source to a dialect (profiles are host configuration).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SqlRequest<'a> {
    pub sql: &'a str,
    /// Analyzed separately, under the source's dialect only.
    pub setup_sql: Option<&'a str>,
    pub source: Option<&'a SourceName>,
    /// The query's authored `dialect:`. It describes `sql` only and wins over the source's.
    pub dialect: Option<QueryDialect>,
}

/// What a host says about one request.
#[derive(Debug, Clone, PartialEq)]
pub enum SqlOutcome {
    Analyzed(Analysis),
    /// Python's `query.parse_error`: only when no fatal finding applies (no fatal lint on `sql` or
    /// `setup_sql`, no mutating guard on `sql`, no mutating `setup_sql`) and the dialect resolved
    /// and the parser gave a position. A host that cannot decide all of that returns `Analyzed`.
    ParseError(ParseError),
    /// This host does not analyze SQL; compile refuses the board, naming why.
    Refused(String),
}

/// The SQL seam. Pure in the request: positions are SQL-local and nothing else is read.
pub trait SqlAnalysis {
    fn analyze(&self, request: &SqlRequest) -> SqlOutcome;

    /// Every request of one compile, so a host with a round-trip cost pays it once.
    fn analyze_all(&self, requests: &[SqlRequest]) -> Vec<SqlOutcome> {
        requests.iter().map(|r| self.analyze(r)).collect()
    }
}

/// The analyzer of a caller that has none: every SQL query is refused by name.
pub struct Refused;

impl SqlAnalysis for Refused {
    fn analyze(&self, _: &SqlRequest) -> SqlOutcome {
        SqlOutcome::Refused("this host has no SQL analyzer".to_string())
    }
}

/// A Jinja span of the template, replaced by a stand-in so the rest parses as SQL.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placeholder {
    /// The stand-in token written into the skeleton.
    pub token: String,
    /// The template source it replaced, e.g. `filter('region', region)`.
    pub source: String,
    /// Byte range of the replaced source in the template.
    pub start: usize,
    pub end: usize,
}

/// The template with every Jinja construct replaced by a placeholder, with both branches
/// of each `{% if %}` kept (Python's `build_skeleton(all_branches=True)`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Skeleton {
    pub sql: String,
    pub placeholders: Vec<Placeholder>,
}

/// A guard's verdict on a skeleton: `select_only` for `sql`, the setup allowlist (temporary
/// functions, tables and views; DuckDB macros) for `setup_sql`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Guard {
    /// Within the allowlist.
    Allowed,
    /// A statement the allowlist refuses, or DML hidden in a CTE or subquery. Execute re-checks
    /// the rendered SQL and stays the authoritative guard.
    Mutating {
        node_kind: String,
        fragment_preview: String,
    },
    /// The skeleton could not be parsed, or the dialect gave no position worth showing; the
    /// guard defers to execute.
    Undetermined,
}

/// An authoring mistake `sql_authoring_lint` finds. Every one refuses the board.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Lint {
    /// A literal backslash-n from single-quoted YAML that is the cause of a parse failure.
    LiteralNewlines,
    /// `date '{{ var }}'` on a dialect that binds the variable as a parameter.
    DateLiteralVariable {
        /// Lowercased: `date`, `time` or `timestamp`.
        keyword: String,
        variable: String,
    },
}

/// Where a SQL parse error sits, in SQL-local coordinates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ErrorPosition {
    pub line: usize,
    pub start_col: usize,
    /// End-exclusive.
    pub end_col: usize,
    /// The line as the parser saw it; proves the offset into the board file is safe.
    pub line_text: String,
}

/// A compile-time parse failure worth showing the author: the dialect was resolvable and the
/// parser gave a concrete position. Never "not yet checked".
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub message: String,
    pub position: ErrorPosition,
}

/// A relation a query reads: `catalog.schema.name`, as written.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Relation {
    pub catalog: Option<String>,
    pub schema: Option<String>,
    pub name: String,
}

/// One input column an output column is computed from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ColumnSource {
    pub relation: Relation,
    pub column: String,
}

/// One output column of the query's final SELECT.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OutputColumn {
    pub name: String,
    /// The columns it reads, through scopes and CTEs down to relations (lineage).
    pub sources: Vec<ColumnSource>,
    /// The format lineage carries from the base column's `meta.format`, only by a rule that
    /// preserves it; none when inputs disagree or an expression is unmodeled.
    pub format: Option<FormatName>,
}

/// An alias the query's FROM clauses bind, and the relation it names.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeAlias {
    pub alias: String,
    pub relation: Relation,
}

/// A structural finding about the query's SQL that needs the host's parse tree: the lints of
/// `core/inspect/query_validator.py`, as plain data. Compile maps each onto its query warning.
#[derive(Debug, Clone, PartialEq)]
pub struct QueryFinding {
    pub kind: FindingKind,
    /// `error`, `warning` or `info`.
    pub severity: String,
    pub message: String,
    pub detail: Option<String>,
    pub recommendation: Option<String>,
    pub confidence: Option<f64>,
    pub evidence: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FindingKind {
    MissingJoinPredicate,
    Reaggregation,
    /// A projection that turns a date into text; reported only when a chart uses `column` as its x.
    DateAsText {
        column: String,
    },
}

/// What the host says about a `setup_sql`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SetupAnalysis {
    pub guard: Guard,
    pub lints: Vec<Lint>,
}

/// Everything the SQL seam says about one query it could analyze.
#[derive(Debug, Clone, PartialEq)]
pub struct Analysis {
    pub skeleton: Skeleton,
    pub select_only: Guard,
    pub lints: Vec<Lint>,
    /// The structural findings of the query's `sql`, in the order the lints run: join predicates,
    /// re-aggregation, dates as text. Empty when the SQL has no single SELECT to read.
    pub findings: Vec<QueryFinding>,
    /// `Some` exactly when the request had a `setup_sql`.
    pub setup: Option<SetupAnalysis>,
    /// The projections the host could name; empty when the query is not a single SELECT-family
    /// statement. A projection it cannot name (`*`, an unaliased aggregate, a Jinja expression)
    /// is dropped, not guessed at.
    pub outputs: Vec<OutputColumn>,
    /// Whether `outputs` names every projection of every branch (the design panel's `complete`).
    /// When false, `outputs` is a partial list and absence from it proves nothing.
    pub outputs_complete: bool,
    /// The aliases in scope at the top of the query.
    pub scope: Vec<ScopeAlias>,
}

//! What the SQL seam hands back for one query (spec § 16, D-18), as plain data.
//!
//! Compile asks a host-injected `SqlAnalysis` (first the Python host over sqlglot, later a
//! Rust one) to analyze a query's SQL and gets an [`Analysis`] back. This module is the
//! result only: no parser, no trait object, nothing that reads SQL. Normalize puts the
//! pieces where they belong (`parse_error` and the output columns into `BoardIndex`, the
//! lints into the diagnostics, the lineage where `bind` reads it); a fixture carries an
//! `Analysis` by value so `bind` can be built without a SQL parser.
//!
//! Every position is SQL-local: relative to the query's own text, the coordinates
//! `stamp_diagnostics` offsets into the board file.

use crate::facet::FormatName;
use diagnostic::Diagnostic;

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

/// The select-only guard's verdict on the skeleton.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SelectOnly {
    /// Read-only: a SELECT-family statement, or a BigQuery temp-function script ending in one.
    Select,
    /// A statement that writes, or DML hidden in a CTE or subquery. Execute re-checks the
    /// rendered SQL and stays the authoritative guard.
    Mutating {
        node_kind: String,
        fragment_preview: String,
    },
    /// The skeleton could not be parsed; the guard defers to execute.
    Undetermined,
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

/// Everything the SQL seam says about one query.
#[derive(Debug, Clone, PartialEq)]
pub struct Analysis {
    pub skeleton: Skeleton,
    pub select_only: SelectOnly,
    /// Set when the skeleton failed to parse and the failure is worth reporting.
    pub parse_error: Option<ParseError>,
    /// Authoring lints (`sql_authoring_lint`), already as diagnostics.
    pub lints: Vec<Diagnostic>,
    /// Empty when the query is not a single SELECT-family statement.
    pub outputs: Vec<OutputColumn>,
    /// The aliases in scope at the top of the query.
    pub scope: Vec<ScopeAlias>,
}

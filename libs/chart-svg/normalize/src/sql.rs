//! The SQL guard of the query pass (`normalize/queries.py`): each SQL query goes to the injected
//! [`SqlAnalysis`] once, and what comes back is applied as Python applies it. An authoring lint or
//! a mutating statement refuses the board; an unparseable body the host could place stays a
//! warning on the query and compile goes on. Variable dependencies are read here from the Jinja
//! parse, not from the host.

use crate::compile::CompileError;
use crate::hoist::HoistError;
use crate::index::{ChartFacts, MissingColumn};
use crate::rhythm;
use board_model::chart::{ChartQuery, Columns};
use board_model::ids::{ItemId, QueryId};
use board_model::query::QueryAuthored;
use board_model::sql_analysis::{
    Analysis, Guard, Lint, OutputColumn, ParseError, SqlAnalysis, SqlOutcome, SqlRequest,
};
use diagnostic::{diag, Diagnostic, ModelPath};
use model_core::Tri;
use project::ProjectPath;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use template::Kind;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

/// What the host said about a query it did not refuse.
#[derive(Debug, Clone, PartialEq)]
pub enum SqlFacts {
    Analyzed(Analysis),
    /// Python's `query.parse_error`.
    ParseError(ParseError),
}

/// The facts of one SQL query. Values and schema queries have none.
#[derive(Debug, Clone, PartialEq)]
pub struct QueryFacts {
    pub sql: SqlFacts,
    /// The variables its `sql` and `setup_sql` read (`query.variable_dependencies`).
    pub variable_deps: BTreeSet<String>,
    /// The queries its `sql` and `setup_sql` embed as `queries.<name>`, by the name written.
    pub embeds: BTreeSet<String>,
}

impl QueryFacts {
    /// The columns of the query's final SELECT, only when the host named every projection.
    /// A partial list (`select *, x`) or none (a parse error, not one SELECT-family statement)
    /// answers `None`: absence from it proves nothing.
    pub fn complete_output_columns(&self) -> Option<&[OutputColumn]> {
        match &self.sql {
            SqlFacts::Analyzed(a) if a.outputs_complete && !a.outputs.is_empty() => {
                Some(&a.outputs)
            }
            SqlFacts::Analyzed(_) | SqlFacts::ParseError(_) => None,
        }
    }
}

/// Python's `extract_sql_variable_dependencies`. A template the parser rejects, or one it cannot
/// read the dependencies of, is refused by name: an empty set would be a wrong fact that looks
/// right.
fn variable_deps(
    query: &str,
    sql: &str,
    setup_sql: Option<&str>,
) -> Result<(BTreeSet<String>, BTreeSet<String>), CompileError> {
    let (mut deps, mut embeds) = (BTreeSet::new(), BTreeSet::new());
    for text in std::iter::once(sql).chain(setup_sql.filter(|s| !s.is_empty())) {
        if !text.contains("{{") && !text.contains("{%") {
            continue;
        }
        let info = template::parse(text, Kind::Sql).map_err(|errors| CompileError::SqlRefused {
            query: query.to_string(),
            why: format!(
                "variable dependencies not read: the template does not parse ({})",
                errors
                    .iter()
                    .map(|e| e.message.as_str())
                    .collect::<Vec<_>>()
                    .join("; ")
            ),
        })?;
        if let Some(why) = info.unanalyzed {
            return Err(CompileError::SqlRefused {
                query: query.to_string(),
                why: format!("variable dependencies not read: {why}"),
            });
        }
        deps.extend(info.variables);
        embeds.extend(info.queries);
    }
    Ok((deps, embeds))
}

fn lint_error(name: &str, field: &str, lint: &Lint) -> Diagnostic {
    match lint {
        Lint::LiteralNewlines => diag::err_sql_literal_newlines(name, field),
        Lint::DateLiteralVariable { keyword, variable } => {
            diag::err_sql_date_literal_variable(name, field, keyword.as_str(), variable.as_str())
        }
    }
}

fn mutating(name: &str, setup: bool, guard: &Guard) -> Option<CompileError> {
    let Guard::Mutating {
        node_kind,
        fragment_preview,
    } = guard
    else {
        return None;
    };
    Some(CompileError::MutatingSql {
        query: name.to_string(),
        setup,
        node_kind: node_kind.clone(),
        fragment_preview: fragment_preview.clone(),
    })
}

/// The first fatal finding, in the order Python raises: the newline lints (`sql`, then
/// `setup_sql`), the date-literal lints, the `sql` guard, the `setup_sql` guard.
fn fatal(name: &str, a: &Analysis) -> Option<CompileError> {
    let setup = a.setup.as_ref();
    let is_newlines = |l: &&Lint| matches!(l, Lint::LiteralNewlines);
    let is_date = |l: &&Lint| matches!(l, Lint::DateLiteralVariable { .. });
    for keep in [is_newlines, is_date] {
        let found = a
            .lints
            .iter()
            .filter(keep)
            .map(|l| ("sql", l))
            .chain(setup.into_iter().flat_map(|s| {
                s.lints
                    .iter()
                    .filter(keep)
                    .map(|l| ("setup_sql", l))
                    .collect::<Vec<_>>()
            }))
            .next();
        if let Some((field, lint)) = found {
            return Some(CompileError::Sql {
                context: String::new(),
                diagnostic: Box::new(lint_error(name, field, lint)),
            });
        }
    }
    mutating(name, false, &a.select_only)
        .or_else(|| setup.and_then(|s| mutating(name, true, &s.guard)))
}

fn parse_warning(name: &str, e: &ParseError) -> Diagnostic {
    diag::warn_parse_error(name, e.message.as_str())
        .at_path(ModelPath(vec![
            "queries".to_string(),
            name.to_string(),
            "sql".to_string(),
        ]))
        .with_sink_param("sql_line", Value::from(e.position.line))
        .with_sink_param("sql_start_col", Value::from(e.position.start_col))
        .with_sink_param("sql_end_col", Value::from(e.position.end_col))
        .with_sink_param("sql_line_text", Value::from(e.position.line_text.as_str()))
}

/// The query id a map key names: `<file>#<name>` for another file's query, else a query of `file`.
pub(crate) fn query_id(key: &str, file: &ProjectPath) -> Result<QueryId, CompileError> {
    let text = match key.contains('#') {
        true => key.to_string(),
        false => format!("{file}#{key}"),
    };
    QueryId::try_from(text).map_err(|e| CompileError::Hoist(HoistError::QueryId(e)))
}

/// Every SQL query of the merged root through `analysis`: their facts by id, and the warnings.
/// Only a query the author named under `queries:` earns a parse warning; one hoisted out of a
/// chart or a variable has no `queries.<name>` node to point at.
pub fn analyze_queries(
    patch: &Patch,
    file: &ProjectPath,
    analysis: &dyn SqlAnalysis,
) -> Result<(BTreeMap<QueryId, QueryFacts>, Vec<Diagnostic>), CompileError> {
    let mut asked = Vec::new();
    for (key, query) in &patch.body.queries {
        let Some(QueryAuthored::Sql(q)) = query else {
            continue;
        };
        let Some(sql) = q.sql.as_deref() else {
            continue;
        };
        asked.push((
            key.as_str(),
            SqlRequest {
                sql,
                setup_sql: match &q.setup_sql {
                    Tri::Value(s) if !s.is_empty() => Some(s.as_str()),
                    _ => None,
                },
                source: match &q.base.source {
                    Tri::Value(s) => Some(s),
                    Tri::Unset | Tri::Null => None,
                },
                dialect: match &q.dialect {
                    Tri::Value(d) => Some(*d),
                    Tri::Unset | Tri::Null => None,
                },
            },
        ));
    }
    let requests: Vec<SqlRequest> = asked.iter().map(|(_, r)| *r).collect();
    let outcomes = analysis.analyze_all(&requests);
    if outcomes.len() != asked.len() {
        return Err(CompileError::SqlAnswers {
            asked: asked.len(),
            answered: outcomes.len(),
        });
    }
    let mut facts = BTreeMap::new();
    let mut warnings = Vec::new();
    for ((key, request), outcome) in asked.into_iter().zip(outcomes) {
        let sql = match outcome {
            SqlOutcome::Refused(why) => {
                return Err(CompileError::SqlRefused {
                    query: key.to_string(),
                    why,
                })
            }
            SqlOutcome::Analyzed(a) => {
                if let Some(e) = fatal(key, &a) {
                    return Err(e);
                }
                SqlFacts::Analyzed(a)
            }
            SqlOutcome::ParseError(e) => {
                if !key.starts_with('@') && !key.contains('#') {
                    warnings.push(parse_warning(key, &e));
                }
                SqlFacts::ParseError(e)
            }
        };
        let (variable_deps, embeds) = variable_deps(key, request.sql, request.setup_sql)?;
        facts.insert(
            query_id(key, file)?,
            QueryFacts {
                sql,
                variable_deps,
                embeds,
            },
        );
    }
    Ok((facts, warnings))
}

/// Fills `missing_columns` of each placed chart whose query's output columns are all known.
pub fn record_missing_columns(
    patch: &mut Patch,
    queries: &BTreeMap<QueryId, QueryFacts>,
    charts: &BTreeMap<ItemId, String>,
    facts: &mut BTreeMap<ItemId, ChartFacts>,
) {
    for (pos, name) in charts {
        let Some(Some(chart)) = patch.body.charts.get_mut(name) else {
            continue;
        };
        let Some(Tri::Value(ChartQuery::Hoisted(id))) = chart.query_mut().map(|q| &*q) else {
            continue;
        };
        let Some(known) = queries
            .get(id)
            .and_then(QueryFacts::complete_output_columns)
        else {
            continue;
        };
        let Some(c) = rhythm::cartesian(chart) else {
            continue;
        };
        let mut named: Vec<(&'static str, &str)> = Vec::new();
        if let Tri::Value(x) = &c.x {
            named.push(("x", x.as_str()));
        }
        match &c.y {
            Tri::Value(Columns::One(y)) => named.push(("y", y.as_str())),
            Tri::Value(Columns::Many(ys)) => named.extend(ys.iter().map(|y| ("y", y.as_str()))),
            Tri::Unset | Tri::Null => {}
        }
        if let Tri::Value(color) = &c.color {
            named.push(("color", color.as_str()));
        }
        let missing: Vec<MissingColumn> = named
            .into_iter()
            .filter(|(_, column)| {
                let column = column.to_lowercase();
                !known.iter().any(|o| o.name.to_lowercase() == column)
            })
            .map(|(channel, column)| MissingColumn {
                channel,
                column: column.to_string(),
            })
            .collect();
        if let Some(f) = facts.get_mut(pos) {
            f.missing_columns = missing;
        }
    }
}

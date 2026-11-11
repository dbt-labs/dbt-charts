//! What search and listings read of a board, from the folded patch: the root's heading and notes,
//! its tags, and the queries and charts it declares itself (a nested board's and the inline ones
//! hoisting names by position are not the root's to list).

/// The version of the index's JSON shape; the host refuses another.
pub const INDEX_VERSION: u32 = 1;

use board_model::chart::{ChartAuthored, ChartQuery};
use board_model::query::QueryAuthored;
use model_core::Tri;
use project::ProjectPath;
use std::collections::BTreeSet;

type Patch = <board_model::board::Board as model_core::Leaf>::Patch;

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct QueryFact {
    pub name: String,
    /// The SQL text of a SQL query; none for any other kind.
    pub sql: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ChartFact {
    pub id: String,
    /// The chart's `title:`; empty when it has none.
    pub title: String,
    /// The name `dct query` takes for what the chart reads. An inline query is named for its
    /// chart (`_inline_query_<chart>`); one another file holds keeps its `<file>#<name>`.
    pub query: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct BoardFacts {
    pub title: Option<String>,
    pub notes: Option<String>,
    pub tags: Vec<String>,
    pub queries: Vec<QueryFact>,
    pub charts: Vec<ChartFact>,
}

fn text(slot: &Tri<board_model::facet::DisplayText>) -> Option<String> {
    match slot {
        Tri::Value(t) if !t.as_str().is_empty() => Some(t.as_str().to_string()),
        _ => None,
    }
}

fn chart_query(chart: &ChartAuthored, id: &str, file: &ProjectPath) -> Option<String> {
    let query = match chart.clone().query_mut()? {
        Tri::Value(ChartQuery::Hoisted(id)) => id.clone(),
        _ => return None,
    };
    let name = query.name().to_string();
    Some(if name.starts_with('@') {
        format!("_inline_query_{id}")
    } else if query.file() == file {
        name
    } else {
        // Spelled from the board's own directory, as a user writes the ref.
        let from = relative(file.as_str(), query.file().as_str());
        format!("{from}#{name}")
    })
}

/// `target` as a path from the directory of `file`: `lib.yml`, `sub/lib.yml`, `../lib.yml`.
fn relative(file: &str, target: &str) -> String {
    let dir: Vec<&str> = file
        .rsplit_once('/')
        .map_or(vec![], |(d, _)| d.split('/').collect());
    let to: Vec<&str> = target.split('/').collect();
    let common = dir.iter().zip(&to).take_while(|(a, b)| a == b).count();
    let ups = std::iter::repeat_n("..", dir.len() - common);
    ups.chain(to.iter().skip(common).copied())
        .collect::<Vec<_>>()
        .join("/")
}

/// The facts of the root board `file`. `own_queries` and `own_charts` are the names its merged
/// patch declares before hoisting, which adds a nested board's and other files' definitions.
pub fn board_facts(
    patch: &Patch,
    file: &ProjectPath,
    own_queries: &BTreeSet<String>,
    own_charts: &BTreeSet<String>,
) -> BoardFacts {
    let body = &patch.body;
    let queries = body
        .queries
        .iter()
        .filter(|(name, _)| own_queries.contains(*name))
        .map(|(name, q)| QueryFact {
            name: name.clone(),
            sql: match q {
                Some(QueryAuthored::Sql(q)) => q.sql.clone().filter(|s| !s.is_empty()),
                _ => None,
            },
        })
        .collect();
    let charts = body
        .charts
        .iter()
        .filter(|(id, _)| own_charts.contains(*id))
        .filter_map(|(id, c)| Some((id, c.as_ref()?)))
        .map(|(id, chart)| ChartFact {
            id: id.clone(),
            title: chart.title().unwrap_or_default().to_string(),
            query: chart_query(chart, id, file),
        })
        .collect();
    BoardFacts {
        title: text(&body.title),
        notes: text(&body.notes),
        tags: patch
            .tags
            .iter()
            .flatten()
            .map(|t| t.to_lowercase())
            .collect(),
        queries,
        charts,
    }
}

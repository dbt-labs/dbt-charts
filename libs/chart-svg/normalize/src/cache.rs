//! What each query's cache policy and incremental watermark resolve to: Python's
//! `normalize_query` cascade, project root, source, board, query, with the board scope the
//! query's owning board gives ([`BoardIndex::query_owners`]).

use crate::compile::{CompileError, Patch};
use crate::index::BoardIndex;
use board_model::cache::{resolve, CacheLayer, CachePolicy, CacheRefusal};
use board_model::query::{Incremental, QueryAuthored};
use diagnostic::{diag, ModelPath};
use model_core::Tri;
use std::collections::BTreeMap;

/// A query as execute runs it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QueryPolicy {
    pub cache: CachePolicy,
    /// The watermark column: the query's own, else its board's; SQL queries only.
    pub incremental: Option<String>,
}

/// The policy of every query of `patch`, by key.
pub fn policies(
    patch: &Patch,
    index: &BoardIndex,
    root: &CacheLayer,
    sources: &BTreeMap<String, CacheLayer>,
) -> Result<BTreeMap<String, QueryPolicy>, CompileError> {
    let mut out = BTreeMap::new();
    for (key, query) in &patch.body.queries {
        let Some(query) = query else { continue };
        let board = index.query_owners.get(key).ok_or_else(|| {
            CompileError::internal(format!("the query {key} has no owning board"))
        })?;
        let scope = index.boards.get(board).ok_or_else(|| {
            CompileError::internal(format!(
                "the query {key} belongs to the board {board}, which has no scope"
            ))
        })?;
        let (source, own_cache, own_incremental, is_sql) = match query {
            QueryAuthored::Sql(q) => (
                Some(&q.base.source),
                &q.base.cache,
                &q.base.incremental,
                true,
            ),
            QueryAuthored::Schema(q) => (
                Some(&q.base.source),
                &q.base.cache,
                &q.base.incremental,
                false,
            ),
            QueryAuthored::Values(q) => (None, &q.base.cache, &q.base.incremental, false),
        };
        let source_layer = match source {
            Some(Tri::Value(name)) => sources.get(name.as_str()),
            _ => None,
        };
        let own = match own_cache {
            Tri::Value(layer) => layer.clone(),
            _ => CacheLayer::default(),
        };
        let mut layers = vec![("project", root)];
        layers.extend(source_layer.map(|l| ("source", l)));
        layers.push(("board", &scope.cache));
        layers.push(("query", &own));
        let cache = resolve(&layers.iter().map(|(_, l)| *l).collect::<Vec<_>>())
            .map_err(|why| refusal(key, &layers, why))?;
        let incremental = match (is_sql, own_incremental) {
            (false, _) => None,
            (true, Tri::Value(i)) => Some(i),
            (true, _) => scope.incremental.as_ref(),
        }
        .and_then(|i| match i {
            Incremental::Column(c) => Some(c.clone()),
            Incremental::Off => None,
        });
        out.insert(key.clone(), QueryPolicy { cache, incremental });
    }
    Ok(out)
}

/// An author's refusal for a prefetch on caching turned off; the located path only when the
/// query's own layer wrote it: the owning board's key may sit in a nested board or an import.
fn refusal(key: &str, layers: &[(&str, &CacheLayer)], why: CacheRefusal) -> CompileError {
    match why {
        CacheRefusal::PrefetchNeedsCache { layer } => {
            let Some(&(scope, _)) = layers.get(layer) else {
                return CompileError::internal(format!(
                    "Query '{key}': the cache refusal names layer {layer}, past the cascade"
                ));
            };
            let refusal = diag::err_cache_prefetch_needs_cache(scope);
            CompileError::Invalid(vec![match scope {
                "query" => refusal.at_path(ModelPath(
                    ["queries", key, "cache", "prefetch"].map(str::to_string).to_vec(),
                )),
                _ => refusal,
            }])
        }
        // The cascade root is the project config, whose validation requires it to say whether to
        // cache, so no board reaches this.
        CacheRefusal::EnabledUnresolved => CompileError::internal(format!(
            "Query '{key}': cache cascade did not resolve 'enabled' — the cascade root (the cache: value in the project config) must set it."
        )),
    }
}

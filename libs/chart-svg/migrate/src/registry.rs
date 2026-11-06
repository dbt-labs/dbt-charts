//! Declarations indexed by the version they leave.

use std::collections::HashMap;
use std::sync::OnceLock;

use crate::catalog::{entries, grammar};
use crate::decl::{ConditionalMove, Declarations, Deletion, Grammars, MapKeyDeletion, Move};

#[derive(Default)]
pub struct Registry {
    pub all: Declarations,
    moves: HashMap<&'static str, Vec<usize>>,
    deletions: HashMap<&'static str, Vec<usize>>,
    conditional: HashMap<&'static str, Vec<usize>>,
    map_keys: HashMap<&'static str, Vec<usize>>,
}

fn index<T>(items: &[T], source: impl Fn(&T) -> &'static str) -> HashMap<&'static str, Vec<usize>> {
    let mut map: HashMap<&'static str, Vec<usize>> = HashMap::new();
    for (i, item) in items.iter().enumerate() {
        map.entry(source(item)).or_default().push(i);
    }
    map
}

impl Registry {
    /// Every registered version module's declarations, resolved against the
    /// frozen grammars of its boundary. A module whose boundary is not in the
    /// manifest is a build defect and contributes nothing.
    pub fn build() -> Registry {
        let mut all = Declarations::default();
        for entry in entries() {
            let Some(module) = crate::MODULES.iter().find(|(v, _)| *v == entry.version) else {
                continue;
            };
            let Some(source) = entry.predecessor.as_deref() else {
                continue;
            };
            let (Some(source_schema), Some(target_schema)) =
                (grammar(source), grammar(&entry.version))
            else {
                continue;
            };
            let g = Grammars {
                source: static_str(source),
                target: static_str(&entry.version),
                source_schema,
                target_schema,
            };
            let d = (module.1)(&g);
            all.moves.extend(d.moves);
            all.deletions.extend(d.deletions);
            all.conditional_moves.extend(d.conditional_moves);
            all.map_key_deletions.extend(d.map_key_deletions);
            all.retired_tokens.extend(d.retired_tokens);
            all.retired_role_keys.extend(d.retired_role_keys);
        }
        Registry {
            moves: index(&all.moves, |m| m.source),
            deletions: index(&all.deletions, |m| m.source),
            conditional: index(&all.conditional_moves, |m| m.source),
            map_keys: index(&all.map_key_deletions, |m| m.source),
            all,
        }
    }

    /// The process-wide registry: package data, identical for every caller.
    pub fn get() -> &'static Registry {
        static CELL: OnceLock<Registry> = OnceLock::new();
        CELL.get_or_init(Registry::build)
    }

    pub fn moves_from(&self, v: &str) -> Vec<&Move> {
        pick(&self.moves, v, &self.all.moves)
    }
    pub fn deletions_from(&self, v: &str) -> Vec<&Deletion> {
        pick(&self.deletions, v, &self.all.deletions)
    }
    pub fn conditional_moves_from(&self, v: &str) -> Vec<&ConditionalMove> {
        pick(&self.conditional, v, &self.all.conditional_moves)
    }
    pub fn map_key_deletions_from(&self, v: &str) -> Vec<&MapKeyDeletion> {
        pick(&self.map_keys, v, &self.all.map_key_deletions)
    }
}

fn pick<'a, T>(index: &HashMap<&'static str, Vec<usize>>, v: &str, items: &'a [T]) -> Vec<&'a T> {
    index
        .get(v)
        .into_iter()
        .flatten()
        .filter_map(|i| items.get(*i))
        .collect()
}

/// A manifest version as a `'static` string, for declarations to carry.
fn static_str(s: &str) -> &'static str {
    entries()
        .iter()
        .find(|e| e.version == s)
        .map_or("", |e| e.version.as_str())
}

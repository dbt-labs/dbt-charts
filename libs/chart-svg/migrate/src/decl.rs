//! The declaration kinds a version module hands the engine.

use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap};
use std::sync::Arc;

use crate::fields::Fields;
use crate::schema::Schema;

pub type KeyPath = Vec<String>;

pub fn path(parts: &[&str]) -> KeyPath {
    parts.iter().map(|p| p.to_string()).collect()
}

/// What a value map may map a scalar to.
#[derive(Debug, Clone, PartialEq)]
pub enum Mapped {
    Str(String),
    Bool(bool),
    Int(i64),
}

/// A scalar's identity as Python's `dict` sees it: `true` is `1`, `1` is `1.0`.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum ScalarKey {
    Str(String),
    Num(u64),
}

impl ScalarKey {
    pub fn of(value: &Json) -> Option<ScalarKey> {
        let num = |f: f64| ScalarKey::Num(if f == 0.0 { 0 } else { f.to_bits() });
        match value {
            Json::String(s) => Some(ScalarKey::Str(s.clone())),
            Json::Bool(b) => Some(num(f64::from(u8::from(*b)))),
            Json::Number(n) => n.as_f64().map(num),
            _ => None,
        }
    }
}

pub type ValueMap = HashMap<ScalarKey, Mapped>;

/// A string value to the leaves it expands to.
pub type Expansion = HashMap<String, Vec<(&'static str, Mapped)>>;

pub fn value_map(entries: &[(&str, &str)]) -> ValueMap {
    entries
        .iter()
        .map(|(k, v)| {
            (
                ScalarKey::Str((*k).to_string()),
                Mapped::Str((*v).to_string()),
            )
        })
        .collect()
}

/// `true` and `false` as value-map keys.
pub fn bool_key(b: bool) -> ScalarKey {
    ScalarKey::of(&Json::Bool(b)).unwrap_or(ScalarKey::Num(0))
}

/// What a `rewrite` returns: the rebuilt value, `Drop` to strike the source
/// and leave nothing, or `Refuse` with the message of a shape it cannot migrate.
#[derive(Debug, Clone, PartialEq)]
pub enum Rewritten {
    Value(Json),
    Drop,
    Refuse(String),
}

pub type Rewrite = Arc<dyn Fn(&Json) -> Rewritten + Send + Sync>;

/// What a document-aware rewrite makes of one value.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Rewrote {
    /// The new value; `None` strikes the source and leaves nothing.
    pub value: Option<Json>,
    /// Reported only when the move fires.
    pub notice: Option<String>,
    /// Values owed positions no move fires at, written after the move, each
    /// creating the mappings it passes through; `None` strikes the key and the
    /// mappings it empties.
    pub writes: Vec<(KeyPath, Option<Json>)>,
}

/// A rewrite whose successor depends on the rest of the document: it reads the
/// document as its boundary began (every rewrite in the boundary reads the same
/// one), the concrete source path, and the value there. `Err` refuses the
/// document with that message.
pub type DocumentRewrite =
    Arc<dyn Fn(&Json, &[String], &Json) -> Result<Rewrote, String> + Send + Sync>;

/// One adjacent structural rename or move. `old_path == new_path` is a
/// value-only remap on a key no grammar ever drops.
#[derive(Clone)]
pub struct Move {
    pub source: &'static str,
    pub target: &'static str,
    pub old_path: KeyPath,
    pub new_path: KeyPath,
    /// Total, lossless value mapping; an unmapped value raises.
    pub value_map: Option<Arc<ValueMap>>,
    /// A string that became a mapping under the same key: leaf name to value.
    pub expansion: Option<Arc<Expansion>>,
    pub drop_notes: Arc<HashMap<&'static str, &'static str>>,
    /// Where a board holds another board; the move follows these in the document.
    pub sub_board_prefixes: Arc<Vec<KeyPath>>,
    pub rewrite: Option<Rewrite>,
    pub rewrite_note: Option<&'static str>,
    /// Owns an occupied destination: the "both fields exist" refusal does not
    /// apply, so it refuses or replaces the value there. An identity-path one
    /// never marks a document old; it runs only beside a key that does.
    pub rewrite_in_document: Option<DocumentRewrite>,
    /// Fires only inside charts of this `type:`; `""` only outside any chart.
    pub chart_type: Option<String>,
    /// Tails that must be absent beside the source key.
    pub conflicts_with: Vec<KeyPath>,
    /// The destination mapping may already exist: the (rewritten) value merges
    /// into it key by key, recursively. A leaf both hold refuses, so nothing the
    /// author wrote is overridden.
    pub merge: bool,
}

impl Move {
    /// An identity-path move with a document-aware rewrite: live in both
    /// grammars, so recognition never reads it as evidence.
    pub fn rewrites_in_document_in_place(&self) -> bool {
        self.old_path == self.new_path && self.rewrite_in_document.is_some()
    }

    pub fn new(source: &'static str, target: &'static str, old: KeyPath, new: KeyPath) -> Move {
        Move {
            source,
            target,
            old_path: old,
            new_path: new,
            value_map: None,
            expansion: None,
            drop_notes: Arc::default(),
            sub_board_prefixes: Arc::default(),
            rewrite: None,
            rewrite_note: None,
            rewrite_in_document: None,
            chart_type: None,
            conflicts_with: Vec::new(),
            merge: false,
        }
    }
}

/// A path that existed in the source grammar and not in the target.
#[derive(Debug, Clone)]
pub struct Deletion {
    pub source: &'static str,
    pub target: &'static str,
    pub path: KeyPath,
    pub reason: Option<String>,
    /// Scopes the deletion to one family of the chart union.
    pub chart_type: Option<String>,
}

impl Deletion {
    pub fn new(source: &'static str, target: &'static str, parts: &[&str]) -> Deletion {
        Deletion {
            source,
            target,
            path: path(parts),
            reason: None,
            chart_type: None,
        }
    }
}

/// Strikes a closed set of literal keys from a declared open-map field.
#[derive(Debug, Clone)]
pub struct MapKeyDeletion {
    pub source: &'static str,
    pub target: &'static str,
    pub container_path: KeyPath,
    /// Struck key to its author-facing message, in report order.
    pub messages: Vec<(String, String)>,
}

/// A relocation that fires only when the destination's parent mapping exists;
/// otherwise the value is dropped with `drop_warning` (`{chart}` names the chart).
#[derive(Debug, Clone)]
pub struct ConditionalMove {
    pub source: &'static str,
    pub target: &'static str,
    pub chart_type: &'static str,
    pub old_tail: KeyPath,
    pub new_tail: KeyPath,
    pub sibling_tail: KeyPath,
    pub drop_warning: String,
}

#[derive(Default, Clone)]
pub struct Declarations {
    pub moves: Vec<Move>,
    pub deletions: Vec<Deletion>,
    pub conditional_moves: Vec<ConditionalMove>,
    pub map_key_deletions: Vec<MapKeyDeletion>,
    /// Retired token spellings a version retires (identity-move value maps),
    /// consulted before the currency check so a spelling no grammar can reject is still found.
    pub retired_tokens: Vec<Arc<ValueMap>>,
    /// Retired `style.palettes` role keys, for the same pre-check.
    pub retired_role_keys: Vec<String>,
}

/// The grammars one version module resolves its declarations against.
pub struct Grammars {
    pub source: &'static str,
    pub target: &'static str,
    pub source_schema: &'static Schema,
    pub target_schema: &'static Schema,
}

impl Grammars {
    /// Tail-only renames as moves: each `(new_tail, old_tail)` pair is found at
    /// every position of the target field tree, substituting the old tail and
    /// keeping the shared prefix. Candidates absent from either grammar drop.
    /// Pairs run in order, so a leaf rename declared before its container's
    /// finds the container still empty.
    pub fn suffix_rename_moves(
        &self,
        renames: &[(&[&str], &[&str])],
        value_map: Option<Arc<ValueMap>>,
        chart_type: Option<&str>,
    ) -> Vec<Move> {
        let tails: BTreeSet<KeyPath> = renames.iter().map(|(n, _)| path(n)).collect();
        let mut fields = Fields::new(self.target_schema);
        let paths = fields.paths(&tails);
        let nesting = Arc::new(fields.board_nesting_prefixes());
        let mut seen = BTreeSet::new();
        let mut out = Vec::new();
        for (new_tail, old_tail) in renames {
            let width = new_tail.len();
            for p in &paths {
                if p.len() < width || !p.ends_with(&path(new_tail)) {
                    continue;
                }
                let head = p.get(..p.len() - width).unwrap_or_default();
                let old_path = [head, &path(old_tail)[..]].concat();
                if !seen.insert((old_path.clone(), p.clone())) {
                    continue;
                }
                if self.source_schema.path_exists(&old_path, false)
                    && self.target_schema.path_exists(p, false)
                {
                    let mut m = Move::new(self.source, self.target, old_path, p.clone());
                    m.value_map = value_map.clone();
                    m.sub_board_prefixes = nesting.clone();
                    m.chart_type = chart_type.map(str::to_string);
                    out.push(m);
                }
            }
        }
        out
    }

    /// Identity-path moves respelling `value_map`'s keys wherever a color token
    /// or palette name is authored. Field paths come from the target field
    /// tree; only the leaf names are declared.
    pub fn token_respell_moves(
        &self,
        value_map: &Arc<ValueMap>,
        scalar_tails: &[&str],
        list_tails: &[&str],
        open_map_values: &[&[&str]],
    ) -> Vec<Move> {
        let tails: BTreeSet<KeyPath> = scalar_tails
            .iter()
            .chain(list_tails)
            .map(|t| path(&[t]))
            .collect();
        let mut fields = Fields::new(self.target_schema);
        let mut out = Vec::new();
        for p in fields.paths(&tails) {
            let Some(last) = p.last() else { continue };
            if !tails.contains(&vec![last.clone()]) || !self.source_schema.path_exists(&p, false) {
                continue;
            }
            out.push(self.identity(&p, value_map));
            let starred = [&p[..], &["*".to_string()][..]].concat();
            if list_tails.contains(&last.as_str())
                && self.source_schema.path_exists(&starred, false)
                && self.target_schema.path_exists(&starred, false)
            {
                out.push(self.identity(&starred, value_map));
            }
        }
        out.extend(
            open_map_values
                .iter()
                .map(|p| self.identity(&path(p), value_map)),
        );
        out
    }

    pub fn identity(&self, p: &[String], value_map: &Arc<ValueMap>) -> Move {
        let mut m = Move::new(self.source, self.target, p.to_vec(), p.to_vec());
        m.value_map = Some(value_map.clone());
        m
    }

    /// The prefixes at which a board holds a board, for a hand-built move.
    pub fn nesting_prefixes(&self) -> Arc<Vec<KeyPath>> {
        Arc::new(Fields::new(self.target_schema).board_nesting_prefixes())
    }
}

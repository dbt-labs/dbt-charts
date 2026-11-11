//! Grammar migrations over a spanned [`yaml::Node`].
//!
//! ```text
//! Node -> recognize against frozen schemas -> declared moves, deletions -> Node + edits
//! ```
//!
//! [`migrate`] is the whole API. Recognition reads the document's shape, not a
//! version marker; declarations resolve against the schema files only
//! (`../../dbt-charts/.../data/schemas/yaml`), never a live model, so this
//! crate cannot see `board-model`. The DEV entry's file, `dev.json`, is the
//! *current* grammar. Each grammar boundary is one version module (`v0_4_0.rs`, ...) and
//! one line in [`MODULES`]; its value rewrites are named Rust functions.
//!
//! `migrate(node, today) -> Migrated` returns the node, an [`Outcome`], Python's
//! notice texts, and every applied move or deletion as a span-addressed
//! [`Edit`]. Schemas are embedded (one copy, read from
//! `dbt-charts/src/dbt_charts/data/schemas/yaml`), and a reader for the JSON
//! Schema subset they use lives in `validate.rs`. The declaration kinds are
//! `Move` (a value rewrite, or a document-aware one that reads the whole
//! document and owes notices and writes elsewhere), `Deletion`,
//! `MapKeyDeletion` and `ConditionalMove`, with their gates. The crate is
//! panic-free (clippy-denied). `dbt-charts/tests/core/compile/test_migrate_text_fixtures.py`
//! pins the bytes `dct migrate` writes from its edits.
//!
//! Every move and deletion that applies is recorded as a span-addressed
//! [`Edit`], for a text rewrite to replay. [`plan_text`] is that entry for
//! `dct migrate`: it walks only what a text writer can express (never a
//! `ConditionalMove`), up to a target version, and returns the document the
//! rewrite must equal plus the edits in applied order. The writer is the
//! contract's one consumer: a `Move` edit carries `from`, `to`, the value
//! `before` and `after` (absent when dropped), whether the declaration has a
//! rewrite, a value map or an expansion, and the declared path for its refusal
//! texts; a `Delete` edit carries the shallowest struck path. [`retired_keys`]
//! reports, without changing anything, where a declaration would fire.
//!
//! Never prune a version module or a declaration: its keys would read as
//! unknown again, and `ERR-RETIRED-FIELD` is the fallback past the support
//! window.

#![cfg_attr(
    not(test),
    deny(
        clippy::unwrap_used,
        clippy::expect_used,
        clippy::panic,
        clippy::indexing_slicing
    )
)]

mod apply;
mod catalog;
mod decl;
mod doc;
mod engine;
mod fields;
mod locate;
mod pyrepr;
mod registry;
mod schema;
mod shorthand;
mod validate;

#[cfg(test)]
mod tests;

mod v0_10_0;
mod v0_11_0;
pub use v0_11_0::respell_retired_variant;
mod v0_12_0;
mod v0_4_0;
mod v0_5_0;
mod v0_6_0;
mod v0_7_0;
mod v0_9_0;

pub use catalog::{parse_date, Date};
pub use decl::{
    path, value_map, ConditionalMove, Declarations, Deletion, DocumentRewrite, Grammars, KeyPath,
    MapKeyDeletion, Mapped, Move, Rewrite, Rewritten, Rewrote, ScalarKey, ValueMap,
};
pub use doc::{Edit, Seg, SYNTHETIC};
pub use engine::{
    migrate, plan_text, retired_keys, Migrated, Outcome, RetiredKey, TextError, TextPlan,
};
pub use registry::Registry;
pub use shorthand::read_board;
pub use v0_6_0::THEME_RENAMES;

type Module = fn(&Grammars) -> Declarations;

/// The version modules, keyed by the version each one migrates *to*. A new
/// boundary is one file and one line here.
pub(crate) const MODULES: &[(&str, Module)] = &[
    ("0.4.0", v0_4_0::declarations),
    ("0.5.0", v0_5_0::declarations),
    ("0.6.0", v0_6_0::declarations),
    ("0.7.0", v0_7_0::declarations),
    ("0.9.0", v0_9_0::declarations),
    ("0.10.0", v0_10_0::declarations),
    ("0.11.0", v0_11_0::declarations),
    ("0.12.0", v0_12_0::declarations),
];

/// The versions with a registered module, for the sweeps.
pub fn module_versions() -> Vec<&'static str> {
    MODULES.iter().map(|(v, _)| *v).collect()
}

//! The project fold (spec § 9). Today: the project seam ([`ProjectFiles`]), [`refs`], and the
//! `extends:` resolver ([`extends::fold_extends`]); the meta walk and the nested-board scope join
//! them in this crate.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

pub mod compile;
mod compose;
mod daterange;
pub mod deps;
pub mod extends;
mod external;
pub mod files;
pub mod fill;
#[cfg(feature = "fixtures")]
pub mod fixtures;
pub mod focus;
pub mod hoist;
pub mod ids;
pub mod index;
pub mod lint;
mod merge_warnings;
pub mod meta;
pub mod names;
pub mod public_id;
mod rhythm;
pub mod scope;
mod shape;
pub mod slug;
pub mod sources;
pub mod sql;
pub mod tree;
pub mod values;
pub mod variables;

pub use compile::{compile, compile_structure, CompileError, CompiledBoard, HostInputs, Structure};
pub use extends::{fold_extends, FoldError, Folded, Notice};
pub use files::{refs, MemoryFiles, ProjectFiles, Ref};
pub use hoist::{hoist_charts, HoistError};
pub use ids::fill_ids;
pub use index::{
    BoardIndex, ChartFacts, DetailsFacts, FactsError, LayoutFacts, MissingColumn, TabsFacts,
};
pub use meta::{find_meta_files, merged_patch, MergeWarning, Merged, RestatedError};
pub use slug::slugify;
pub use sources::SourceRegistry;
pub use sql::{QueryFacts, SqlFacts};
pub use variables::{FactsDefault, VariableFacts};

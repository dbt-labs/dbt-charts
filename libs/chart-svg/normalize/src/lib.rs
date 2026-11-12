//! The project fold (spec § 9): the project seam ([`ProjectFiles`], [`refs`]), the `extends:`
//! resolver ([`extends::fold_extends`]), the meta walk ([`meta::merged_patch`]), the layout facts
//! ([`index::LayoutFacts`]), the hoist, and the nested-board style scope, chained by
//! [`compile::compile_structure`] and [`compile::compile`]. Each pass has a Python oracle fixture
//! named in its module or its test.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod board_files;
mod board_warnings;
pub mod cache;
pub mod chart_checks;
pub mod compile;
mod compose;
pub mod constraints;
mod custom;
mod daterange;
pub mod deps;
mod dialect_layer_checks;
pub mod extends;
mod external;
mod extra_field;
pub mod files;
pub mod fill;
#[cfg(feature = "fixtures")]
pub mod fixtures;
pub mod focus;
pub mod format_checks;
pub mod hoist;
pub mod host_facts;
pub mod ids;
pub mod index;
pub mod label_anchors;
pub mod links;
pub mod lint;
pub mod locate;
mod markdown;
mod merge_warnings;
pub mod meta;
pub mod names;
mod palette_checks;
pub mod pathless;
pub mod public_id;
mod query_lints;
mod restated;
mod restated_variables;
mod undeclared_variables;
mod retired;
mod rhythm;
pub mod scope;
pub mod search;
mod shape;
pub mod slug;
pub mod sources;
pub mod sql;
pub mod time_zone;
pub mod tree;
pub mod values;
pub mod values_kinds;
pub mod variables;

pub use compile::{
    compile, compile_index, compile_or_refuse, compile_structure, wire_located, CompileError,
    CompiledBoard, HostInputs, Refusal, Structure,
};
pub use extends::{fold_extends, FoldError, Folded, MetaLint, Notice};
pub use files::{refs, MemoryFiles, ProjectFiles, ReadError, Ref};
pub use hoist::{hoist_charts, BoardScope, HoistError};
pub use ids::fill_ids;
pub use index::{BoardIndex, ChartFacts, DetailsFacts, FactsError, LayoutFacts, TabsFacts};
pub use locate::Source;
pub use meta::{find_meta_files, merged_patch, MergeWarning, Merged, RestatedError};
pub use pathless::{compile_text_or_refuse, wire_refusal as pathless_wire_refusal};
pub use search::{board_facts, BoardFacts, ChartFact, QueryFact, INDEX_VERSION};
pub use slug::slugify;
pub use sources::SourceRegistry;
pub use sql::{QueryFacts, SqlFacts};
pub use time_zone::SourceZone;
pub use variables::{FactsDefault, VariableFacts};

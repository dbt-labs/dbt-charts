//! The runtime of the typed spine (spec §§ 3-5, 10), the foundation under
//! `board-model`. No dbt Charts concepts (the one exception is `inherit::emit`'s
//! named-font and color passes), not published, and `#![deny]` on
//! `unwrap`/`expect`/`panic`/indexing.
//!
//! A hand-written type `T` derives [`Model`] (crate `model-derive`) and gets two
//! twins, `Authored<T>` and `Patch<T>`, plus a `ModelInfo` (the field table).
//!
//! ```text
//! spine field                       twin slot
//! T            (leaf)               Option<T>        unset | value
//! Option<T>    (absent = "...")     Tri<T>           unset | null | value
//! Option<Inner> (absent, a Model)  Tri<Patch<Inner>>   deep-merged when both layers hold a value
//! Inner        (a Model)            Patch<Inner>     recursed, never optional
//! IndexMap<String, V>               IndexMap<String, Patch<V>>
//! enum (keys / tagged)              Option<EnumAuthored>   replaced wholesale
//! ```
//!
//! [`Slot::merge`] is the one merge: unset keeps lower, null sets the absent
//! state, a value applies the field's [`Merge`] strategy (`Override` replaces,
//! `Deep` recurses, `ByKey` merges per map key). [`first_set`] is the
//! specificity walk: the first set layer wins, null wins as absent, unset falls
//! through.
//!
//! [`FromNode`] reads a spanned `yaml::Node` into a twin and collects every error
//! ([`Cx`], [`ParseError`]); an unknown key is always an error, and the
//! [`SourceMap`] (model path to span) comes back beside the twin ([`Parse`]).
//! Errors are built through `diagnostic::diag`'s constructors, and an unknown key's
//! hint comes from the field table.
//!
//! [`Build`] (`T::try_from(patch)`) builds the total value, naming every unset
//! leaf by authored path; a hand-written [`Leaf`] is followed by `build_leaf!`.
//!
//! [`schema`] builds the schema IR from the field tables, renders JSON Schema
//! (`json_schema(view, extensions)`, shaped like Python's renderer) and answers
//! `at(path)`. A type becomes a `TypeExpr` through `Leaf::EXPR`;
//! facet newtypes declare themselves with `facet!`.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod build;
pub mod cascade;
mod from_node;
pub mod inherit;
mod law;
mod leaf;
mod parse;
pub mod schema;
mod table;
mod tri;

pub use build::{
    build_defaulted, build_defaulted_authored, build_field, build_field_authored, build_nullable,
    build_nullable_authored, build_omitted, build_omitted_authored, build_payload, build_whole,
    join, or_fill, Build, Unset,
};
pub use cascade::{CascadeError, Csr, Resolved, Tables};
pub use from_node::{
    map_node, parse, parse_patch, read_accept, read_enum, read_keys, read_list, read_map,
    read_null, read_nullable, read_nullable_patch, read_or_empty, read_patch_tri, read_tag,
    scalar_node, shorthand, FromMap, FromNode, InferTag, ReadScalar, Shorthand,
};
pub use inherit::{
    fill_literal, FillDefault, FillFrom, FillTry, InheritError, Refused, TokenBlock, TokenListSlot,
    TokenSlot,
};
pub use law::{first_violation, satisfied, Keyed, State, Unanswerable};
pub use leaf::{
    merge_map_deep, merge_nullable, merge_patch_slot, spell_leaves, tri_drop_nulls, tri_to_authored,
    tri_to_patch, DropNulls, Empty, Fold, Leaf, LeafValues, Lift, SetLeaves, Slot, Spell,
    ToAuthored, ToPatch,
};
pub use parse::{
    nearest, render_path, Cx, Hint, MapRead, Parse, ParseError, Retired, Retirement, Seg,
    SourceMap, Walk,
};
pub use serde_json;
pub use table::{
    Attrs, Bounds, Condition, Constraint, ConstraintKind, Field, InEngine, Keywords, Merge,
    ModelInfo, ModelKind, Scalar, TypeExpr, Value,
};
pub use tri::{first_set, Engine, Tri};
pub use yaml::{Entry, Kind, Node, Span, Value as ScalarValue};

/// A spine type with derived twins and a field table.
pub trait Model: Leaf {
    fn info() -> &'static ModelInfo;
}

/// The authored twin of `T`: parse output, unset, null and value distinct.
pub type Authored<T> = <T as Leaf>::Authored;
/// The patch twin of `T`: a mergeable layer (`meta.yml`, themes, `extends`).
pub type Patch<T> = <T as Leaf>::Patch;

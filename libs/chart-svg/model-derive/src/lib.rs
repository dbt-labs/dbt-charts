//! The derive macro of the typed spine's foundation (no dbt Charts concepts, not
//! published, `#![deny]` on `unwrap`/`expect`/`panic`/indexing).
//!
//! `#[derive(Model)]`: from one hand-written spine type, generate
//! `<T>Authored` and `<T>Patch` (named by `model_core::Authored<T>` /
//! `Patch<T>`), the `FieldTable` ([`model_core::ModelInfo`]) and
//! `Patch::merge`. Leaf rules and attributes: `model-core` and spec §§ 4-5.
//!
//! ```text
//! #[derive(Model)]
//! struct Font {
//!     /// Point size.
//!     size: f64,
//!     #[model(absent = "no stroke")]
//!     stroke: Option<String>,
//!     #[model(merge = override)]
//!     stops: Vec<f64>,
//! }
//! ```
//!
//! Field attributes live in one closed `#[model(..)]` set (`attrs.rs`; `trybuild`
//! cases in `tests/ui/` pin what is refused): `absent = ".."`, `omit = ".."`,
//! `default_empty`,
//! `merge = override | deep | by_key`, `inherit = ".."`, `inherit_slot = ".."`
//! with `links(leaf = "path", ..)`, `link_at(to = "a.b", from = "path")` and
//! `fill_at(to = "a.b", value = <literal>)` (a link or fill for a node below the field at this
//! mount only: one shared type mounted at different tiers), `local`, `nested = drop`, `fill = fn` or `fill = <literal>` (a constant the schema states as `default`),
//! `accept = Type`, `directive`, `keys`, `arm_pattern`, `nullable`, `role = ..`; a bare `#[flatten]`
//! composes a mixin. A struct or enum takes `#[model(constraint(kind(key, ..)))]`
//! with kind `requires | forbids | value_in | items_match | range | unique |
//! at_least_one_of | at_most_one_of | not_null`, and optional `when(set(k), is_in(k, ..), ..)`,
//! `nonempty(..)`, `auto(..)`, `pattern = ".."`, `gt/ge/lt/le = n`; an enum takes
//! `tag = "type"` to read its variants by a tag key. A struct takes `shorthand = "key"`: a bare
//! string where the struct is expected reads as `{key: string}`, and the schema offers it as a
//! second `anyOf` arm, and `complete` keeps its keys required of an authored layer even where an
//! `inherit_slot` mounts it. A variant's key is its snake-case name.
//! `nullable` on a plain model field reads a null as the empty mapping, for a field Python types
//! `X | None` whose `None` authors nothing (a chart's `style:`); the schema offers `null`.
//! `absent` on `Option<Inner>` with a Model `Inner` types the slot `Tri<Patch<Inner>>`: null is the
//! absent state and two values merge deep.
//! `absent` on `Option<Patch<Inner>>` is the same for a sparse overlay: it builds to `None` for unset
//! and null, `Some(patch)` for a value.
//! `omit` on `Option<T>` (a scalar or a model, not a map) is a field that may be left out and builds `None`, but
//! whose twins are `T`'s own, so an authored `null` is refused as for any plain field; `absent` is
//! the field whose `null` is a value. `default_empty` on a map, a list or a model builds empty when unset; a model must build from an empty patch (`Schema::of` refuses one with a key nothing fills), and a `Patch` slot is its own empty overlay.
//! Anything else is a compile error. All are recorded in the field table;
//! `keys`, `flatten`, `accept`, `shorthand`, `tag` and `fill = fn` (an absent tag, inferred from the keys
//! present) also drive the generated `FromNode`; the rest wait for the passes that read them.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

mod attrs;
mod expand;

use proc_macro::TokenStream;

#[proc_macro_derive(Model, attributes(model, flatten))]
pub fn derive_model(input: TokenStream) -> TokenStream {
    match syn::parse(input).and_then(|i| expand::expand(&i)) {
        Ok(t) => t.into(),
        Err(e) => e.to_compile_error().into(),
    }
}

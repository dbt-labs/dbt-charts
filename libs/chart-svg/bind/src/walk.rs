//! The declared priority-list walk: a slot's rungs, highest first, each a style layer
//! that may set the leaf asked for; the walk answers with the first rung that does. A
//! slot declares its rungs and asks through this one walk, so no slot carries a copy of
//! the rule.
//!
//! The rule is `model_core::first_set`: a set rung wins, an explicit `null` wins as the
//! stated absent state (`None`), and an unset rung falls through. A rung that is a total
//! value ends the list: it never falls through.

use model_core::{first_set, Leaf, Patch, Tri};

/// A declared priority list, highest first.
pub struct Ladder<R>(Vec<R>);

impl<R> Ladder<R> {
    pub fn new(rungs: Vec<R>) -> Self {
        Self(rungs)
    }

    /// The first rung's leaf. A dict-valued field walks as a container of leaves: ask
    /// for each leaf, not for the dict.
    pub fn walk<T>(&self, leaf: impl Fn(&R) -> Tri<&T>) -> Option<&T> {
        first_set(self.0.iter().map(leaf))
    }

    /// The first rung's answer to `find`, in priority order.
    pub fn find_map<T>(&self, find: impl Fn(&R) -> Option<T>) -> Option<T> {
        self.0.iter().find_map(find)
    }
}

/// One rung of a slot whose layers share a model `M`: a tier's sparse patch, or the total
/// that ends the list.
pub enum Rung<'a, M: Leaf> {
    Patch(&'a Patch<M>),
    Total(&'a M),
}

impl<'a, M: Leaf> Rung<'a, M> {
    /// The rungs `patches` (highest first, an absent tier skipped) over the `total`.
    pub fn ladder(
        patches: impl IntoIterator<Item = Option<&'a Patch<M>>>,
        total: &'a M,
    ) -> Ladder<Self> {
        let rungs = patches.into_iter().flatten().map(Rung::Patch);
        Ladder::new(rungs.chain([Rung::Total(total)]).collect())
    }
}

impl<M: Leaf> Ladder<Rung<'_, M>> {
    /// A leaf of the slot: `patch` reads it off a tier's patch, `total` off the total.
    pub fn leaf<T>(
        &self,
        patch: impl Fn(&Patch<M>) -> Tri<&T>,
        total: impl Fn(&M) -> Tri<&T>,
    ) -> Option<&T> {
        self.walk(|rung| match rung {
            Rung::Patch(p) => patch(p),
            Rung::Total(t) => total(t),
        })
    }
}

/// A patch's leaf that is a plain `Option`: unset is `None`.
pub fn sparse<T>(leaf: &Option<T>) -> Tri<&T> {
    leaf.as_ref().map_or(Tri::Unset, Tri::Value)
}

/// A total value's leaf that is a plain `Option`: `None` is its stated absent state.
pub fn absent<T>(leaf: &Option<T>) -> Tri<&T> {
    leaf.as_ref().map_or(Tri::Null, Tri::Value)
}

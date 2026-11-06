//! The declared priority-list walk: a slot's rungs, highest first, each a
//! style layer that may set the leaf asked for; the walk answers with the first rung
//! that does. A slot declares its rungs and asks through this one walk, so no slot
//! carries a copy of the rule.

/// A declared priority list of style layers, highest first.
pub struct Ladder<R>(Vec<R>);

impl<R> Ladder<R> {
    pub fn new(rungs: Vec<R>) -> Self {
        Self(rungs)
    }

    /// The first rung's leaf. A dict-valued field walks as a container of leaves: ask
    /// for each leaf, not for the dict.
    pub fn walk<T>(&self, leaf: impl Fn(&R) -> Option<T>) -> Option<T> {
        self.0.iter().find_map(leaf)
    }
}

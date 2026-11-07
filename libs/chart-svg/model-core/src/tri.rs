/// A value that can be unset (nothing authored), null (the absent state), or set.
#[derive(Debug, Clone, PartialEq)]
pub enum Tri<T> {
    Unset,
    Null,
    Value(T),
}

// A derive would require `T: Default`; no slot's value type has to be.
#[allow(clippy::derivable_impls)]
impl<T> Default for Tri<T> {
    fn default() -> Self {
        Tri::Unset
    }
}

impl<T> Tri<T> {
    pub fn as_ref(&self) -> Tri<&T> {
        match self {
            Tri::Unset => Tri::Unset,
            Tri::Null => Tri::Null,
            Tri::Value(v) => Tri::Value(v),
        }
    }
}

/// An engine-decided value: a named rule the engine resolves, or a value.
/// A rule's spelling is a contract.
#[derive(Debug, Clone, PartialEq)]
pub enum Engine<T, R> {
    Rule(R),
    Value(T),
}

/// The specificity walk for one leaf: layers most specific first. The first
/// set layer wins; null wins as absent (`None`); unset falls through. Callers
/// end the list with a total layer.
pub fn first_set<'a, T: 'a>(layers: impl IntoIterator<Item = Tri<&'a T>>) -> Option<&'a T> {
    for layer in layers {
        match layer {
            Tri::Unset => continue,
            Tri::Null => return None,
            Tri::Value(v) => return Some(v),
        }
    }
    None
}

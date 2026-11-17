use crate::{Empty, Leaf, Lift, Tri};
use indexmap::IndexMap;
use std::fmt;

/// Leaves a patch left unset when the total value was built from it: an engine
/// bug (the base layer is total), reported with every authored path.
#[derive(Debug, Clone, PartialEq)]
pub struct Unset {
    pub paths: Vec<String>,
}

impl fmt::Display for Unset {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "fields left unset: {}", self.paths.join(", "))
    }
}

impl std::error::Error for Unset {}

/// A spine type built from its patch once the fold and the inherit pass are done.
/// A model's impl is derived (`TryFrom<Patch<T>>` on the model is the same call);
/// every leaf (`Whole = Self`, `Patch = Option<Self>`) is built here.
pub trait Build: Leaf + Sized {
    /// `at` is the authored path of the slot, [`At::ROOT`] at the root.
    fn build(slot: Self::Patch, at: &At<'_>) -> Result<Self, Unset>;
    /// The same from the parse output, for an enum's payload or a `merge = override` field.
    fn build_authored(slot: Self::Authored, at: &At<'_>) -> Result<Self, Unset>;
}

/// `Build` for a leaf type: `Whole = Self` and both twins are `Option<Self>`. Every hand-written
/// `Leaf` impl is followed by one of these; `facet!` and the scalars do it themselves.
#[macro_export]
macro_rules! build_leaf {
    ($($t:ty),+ $(,)?) => {$(
        impl $crate::Build for $t {
            fn build(slot: Option<$t>, at: &$crate::At<'_>) -> Result<$t, $crate::Unset> {
                slot.ok_or_else(|| $crate::Unset { paths: vec![at.to_string()] })
            }
            fn build_authored(slot: Option<$t>, at: &$crate::At<'_>) -> Result<$t, $crate::Unset> {
                slot.ok_or_else(|| $crate::Unset { paths: vec![at.to_string()] })
            }
        }
    )+};
}

impl<V: Build> Build for Vec<V>
where
    V::Authored: Lift<Value = V::Whole>,
{
    fn build(slot: Self::Patch, at: &At<'_>) -> Result<Self, Unset> {
        Self::build_authored(slot, at)
    }
    fn build_authored(slot: Self::Authored, at: &At<'_>) -> Result<Self, Unset> {
        let items = slot.ok_or_else(|| Unset {
            paths: vec![at.to_string()],
        })?;
        let mut out = Vec::new();
        let mut paths = Vec::new();
        for (i, w) in items.into_iter().enumerate() {
            match build_payload::<V>(w, &at.child(&i.to_string())) {
                Ok(v) => out.push(v),
                Err(e) => paths.extend(e.paths),
            }
        }
        if paths.is_empty() {
            Ok(out)
        } else {
            Err(Unset { paths })
        }
    }
}

impl<V: Build> Build for IndexMap<String, V> {
    fn build(slot: Self::Patch, at: &At<'_>) -> Result<Self, Unset> {
        let mut out = IndexMap::new();
        let mut paths = Vec::new();
        for (k, v) in slot {
            match V::build(v, &at.child(&k)) {
                Ok(v) => {
                    out.insert(k, v);
                }
                Err(e) => paths.extend(e.paths),
            }
        }
        if paths.is_empty() {
            Ok(out)
        } else {
            Err(Unset { paths })
        }
    }
    fn build_authored(slot: Self::Authored, at: &At<'_>) -> Result<Self, Unset> {
        let mut out = IndexMap::new();
        let mut paths = Vec::new();
        for (k, v) in slot {
            match V::build_authored(v, &at.child(&k)) {
                Ok(v) => {
                    out.insert(k, v);
                }
                Err(e) => paths.extend(e.paths),
            }
        }
        if paths.is_empty() {
            Ok(out)
        } else {
            Err(Unset { paths })
        }
    }
}

impl<T: Build> Build for Box<T> {
    fn build(slot: Self::Patch, at: &At<'_>) -> Result<Self, Unset> {
        T::build(*slot, at).map(Box::new)
    }
    fn build_authored(slot: Self::Authored, at: &At<'_>) -> Result<Self, Unset> {
        T::build_authored(*slot, at).map(Box::new)
    }
}

/// A field's authored path, extended as a build descends and rendered only for the leaves it names
/// as unset: a build visits every field of a style, and almost none is unset.
#[derive(Clone, Copy)]
pub struct At<'a> {
    parent: Option<&'a At<'a>>,
    key: &'a str,
}

impl At<'static> {
    pub const ROOT: At<'static> = At { parent: None, key: "" };
}

impl<'a> At<'a> {
    /// A path already written out in full.
    pub fn of(path: &'a str) -> At<'a> {
        At {
            parent: None,
            key: path,
        }
    }

    /// The path of the field `key` below this one.
    pub fn child<'b>(&'b self, key: &'b str) -> At<'b>
    where
        'a: 'b,
    {
        At {
            parent: Some(self),
            key,
        }
    }
}

impl fmt::Display for At<'_> {
    /// As [`join`] folds the keys from the root.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let mut keys = Vec::new();
        let mut at = Some(self);
        while let Some(step) = at {
            keys.push(step.key);
            at = step.parent;
        }
        let path = keys.iter().rev().fold(String::new(), |path, key| join(&path, key));
        f.write_str(&path)
    }
}

/// The path of the field `key` below `at`.
pub fn join(at: &str, key: &str) -> String {
    if at.is_empty() {
        return key.to_string();
    }
    // Built for every field of every style a compile builds, so no formatting machinery.
    let mut path = String::with_capacity(at.len() + 1 + key.len());
    path.push_str(at);
    path.push('.');
    path.push_str(key);
    path
}

/// Build one field; an unset leaf is recorded in `unset` and the field is `None`.
pub fn build_field<T: Build>(slot: T::Patch, at: &At<'_>, unset: &mut Vec<String>) -> Option<T> {
    match T::build(slot, at) {
        Ok(v) => Some(v),
        Err(e) => {
            unset.extend(e.paths);
            None
        }
    }
}

/// A field with a declared `fill`: when the build found the slot itself unset (and nothing deeper),
/// the fill is the value, as the model says a total value holds where nothing set it. A slot that is
/// unset because something inside it is stays named by its paths.
pub fn or_fill<T>(
    built: Option<T>,
    paths: Vec<String>,
    at: &At<'_>,
    unset: &mut Vec<String>,
    fill: impl FnOnce() -> Option<T>,
) -> Option<T> {
    if built.is_some() {
        return built;
    }
    if let [only] = paths.as_slice() {
        if *only == at.to_string() {
            if let Some(v) = fill() {
                return Some(v);
            }
        }
    }
    unset.extend(paths);
    None
}

/// Build an `Option<T>` field with `absent`: null is `None`, a value is built. Unset is `None` too:
/// the layers never set it, and `absent` names what that means.
pub fn build_nullable<T: Build>(
    slot: Tri<<T::Patch as Lift>::Value>,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<Option<T>>
where
    T::Patch: Lift,
{
    match slot {
        Tri::Unset | Tri::Null => Some(None),
        Tri::Value(v) => build_field::<T>(<T::Patch as Lift>::put(v), at, unset).map(Some),
    }
}

/// Build an `omit` field: unset is `None`, anything set is built as for a plain field.
pub fn build_omitted<T: Build>(
    slot: T::Patch,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<Option<T>>
where
    T::Patch: Empty,
{
    if slot.is_empty() {
        return Some(None);
    }
    build_field::<T>(slot, at, unset).map(Some)
}

/// [`build_omitted`] from the parse output.
pub fn build_omitted_authored<T: Build>(
    slot: T::Authored,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<Option<T>>
where
    T::Authored: Empty,
{
    if slot.is_empty() {
        return Some(None);
    }
    build_field_authored::<T>(slot, at, unset).map(Some)
}

/// Build a `default_empty` field: unset is the empty value.
pub fn build_defaulted<T: Build + Default>(
    slot: T::Patch,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<T>
where
    T::Patch: Empty,
{
    if slot.is_empty() {
        return Some(T::default());
    }
    build_field::<T>(slot, at, unset)
}

/// [`build_defaulted`] from the parse output.
pub fn build_defaulted_authored<T: Build + Default>(
    slot: T::Authored,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<T>
where
    T::Authored: Empty,
{
    if slot.is_empty() {
        return Some(T::default());
    }
    build_field_authored::<T>(slot, at, unset)
}

/// Build one field from the parse output, as [`build_field`] from a patch.
pub fn build_field_authored<T: Build>(
    slot: T::Authored,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<T> {
    match T::build_authored(slot, at) {
        Ok(v) => Some(v),
        Err(e) => {
            unset.extend(e.paths);
            None
        }
    }
}

/// [`build_nullable`] from the parse output.
pub fn build_nullable_authored<T: Build>(
    slot: Tri<<T::Authored as Lift>::Value>,
    at: &At<'_>,
    unset: &mut Vec<String>,
) -> Option<Option<T>> {
    match slot {
        Tri::Unset | Tri::Null => Some(None),
        Tri::Value(v) => {
            build_field_authored::<T>(<T::Authored as Lift>::put(v), at, unset).map(Some)
        }
    }
}

/// A `merge = override` field: the slot holds the whole value as parsed.
pub fn build_whole<T: Build>(slot: Option<T::Whole>, at: &At<'_>, unset: &mut Vec<String>) -> Option<T>
where
    T::Authored: Lift<Value = T::Whole>,
{
    match slot {
        Some(w) => build_field_authored::<T>(<T::Authored as Lift>::put(w), at, unset),
        None => {
            unset.push(at.to_string());
            None
        }
    }
}

/// An enum payload as parsed.
pub fn build_payload<T: Build>(w: T::Whole, at: &At<'_>) -> Result<T, Unset>
where
    T::Authored: Lift<Value = T::Whole>,
{
    T::build_authored(<T::Authored as Lift>::put(w), at)
}

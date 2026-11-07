//! `FromNode`: a spanned [`Node`] to a twin, collecting every error.
//!
//! A bad field yields an error in the [`Cx`] and its slot stays unset; the
//! siblings are still read. The caller checks [`Parse::errors`].

use crate::parse::{Hint, MapRead, Seg, Walk};
use crate::{Cx, Engine, Leaf, Lift, Parse, Retired, Tri};
use indexmap::IndexMap;
use yaml::{Kind, Node, Value};

/// A leaf value read from one scalar or list node.
pub trait ReadScalar: Sized {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self>;
}

/// A spine type that reads from a [`Node`]. Derived by `Model` for every
/// struct and enum; implemented here for scalars and containers.
pub trait FromNode: Leaf {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored;
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch;
    /// The whole value (`Leaf::Whole`), as an enum payload holds it.
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole>;
    /// Every key a map of this type can hold.
    fn keys() -> Vec<String> {
        Vec::new()
    }
    /// Visit the model tree below this type (see [`Walk`]).
    fn walk(_: &mut Walk) {}
}

/// A type read from a map it shares with its parent (`#[flatten]`, a `keys`
/// enum, a tagged union's payload): it claims its own keys and leaves the rest.
pub trait FromMap: FromNode {
    fn authored_in(m: &mut MapRead, cx: &mut Cx) -> Self::Authored;
    fn patch_in(m: &mut MapRead, cx: &mut Cx) -> Self::Patch;
}

/// Parse `node` as `T`'s authored twin.
pub fn parse<T: FromNode>(node: &Node, retired: Option<&dyn Retired>) -> Parse<T::Authored> {
    let mut cx = Cx::new::<T>(retired);
    let twin = T::authored(node, &mut cx);
    cx.finish(twin)
}

/// Parse `node` as `T`'s patch twin (`meta.yml`, a theme, `extends`).
pub fn parse_patch<T: FromNode>(node: &Node, retired: Option<&dyn Retired>) -> Parse<T::Patch> {
    let mut cx = Cx::new::<T>(retired);
    let twin = T::patch(node, &mut cx);
    cx.finish(twin)
}

/// The shared body of a derived `authored` / `patch`: the node must be a map;
/// `read` claims keys; what it leaves unclaimed is an error.
pub fn read_map<R: Default>(
    node: &Node,
    cx: &mut Cx,
    keys: Vec<String>,
    read: impl FnOnce(&mut MapRead, &mut Cx) -> R,
) -> R {
    let Some(mut m) = MapRead::new(node) else {
        cx.not_a_map(node, &keys);
        return R::default();
    };
    let out = read(&mut m, cx);
    cx.unknown_keys(&m);
    out
}

/// A bare string read as the map `{key: string}`, for a struct that declares a `shorthand`;
/// `None` for any other node, which is read as it stands.
pub fn shorthand(node: &Node, key: &str) -> Option<Node> {
    let Kind::Scalar(Value::Str(_)) = &node.kind else {
        return None;
    };
    let mut map = IndexMap::new();
    map.insert(
        key.to_string(),
        yaml::Entry {
            key_span: node.span,
            key_kind: yaml::KeyKind::Str,
            key_text: key.to_string(),
            value: node.clone(),
        },
    );
    Some(Node {
        span: node.span,
        kind: Kind::Map(map),
    })
}

/// What a `shorthand_with` function made of a node: a bare string widened into the map it stands
/// for, a node to read as it stands, or a string it refused (reported already).
pub enum Shorthand {
    Keep,
    Refused,
    Widened(Node),
}

/// The map node `{key: value, ..}` a shorthand string stands for, every span the string's own.
pub fn map_node(span: yaml::Span, entries: Vec<(&str, Node)>) -> Node {
    let map = entries
        .into_iter()
        .map(|(k, value)| {
            let entry = yaml::Entry {
                key_span: span,
                key_kind: yaml::KeyKind::Str,
                key_text: k.to_string(),
                value,
            };
            (k.to_string(), entry)
        })
        .collect();
    Node {
        span,
        kind: Kind::Map(map),
    }
}

pub fn scalar_node(span: yaml::Span, value: Value) -> Node {
    Node {
        span,
        kind: Kind::Scalar(value),
    }
}

/// Infers a union's tag from the keys present when the tag is absent.
pub type InferTag = fn(&[&str]) -> Option<&'static str>;

/// A union's tag: its index in `valid`. An absent tag is inferred by `infer`
/// from the keys present, else an error.
pub fn read_tag(
    m: &mut MapRead,
    cx: &mut Cx,
    key: &str,
    valid: &[&'static str],
    infer: Option<InferTag>,
) -> Option<usize> {
    let read = cx.field(m, key, |node, cx| match &node.kind {
        Kind::Scalar(Value::Str(s)) => {
            let found = valid.iter().position(|v| v == s);
            if found.is_none() {
                cx.unknown_tag(node, s, valid);
            }
            found
        }
        _ => {
            cx.mismatch(node, "a string");
            None
        }
    });
    if let Some(found) = read {
        return found;
    }
    let present: Vec<&str> = m.unclaimed().collect();
    if let Some(i) = infer
        .and_then(|f| f(&present))
        .and_then(|tag| valid.iter().position(|v| *v == tag))
    {
        return Some(i);
    }
    let hint = Hint::Tags {
        valid: valid.iter().map(|v| v.to_string()).collect(),
        nearest: None,
    };
    cx.missing(m.node.span, key, hint);
    None
}

/// A `keys` enum: the variant is spelled by which of `keys` is present.
/// Returns the index of the one present; more than one is an error.
pub fn read_keys(m: &mut MapRead, cx: &mut Cx, keys: &[&str]) -> Option<usize> {
    for k in keys {
        m.ask(k);
    }
    let present: Vec<usize> = (0..keys.len())
        .filter(|&i| keys.get(i).is_some_and(|k| m.has(k)))
        .collect();
    match present.as_slice() {
        [one] => Some(*one),
        [] => None,
        _ => {
            cx.exclusive(m, keys);
            None
        }
    }
}

/// The payload of a unit variant spelled as a `keys` key: it holds null.
pub fn read_null(node: &Node, cx: &mut Cx) -> Option<()> {
    if matches!(node.kind, Kind::Scalar(Value::Null)) {
        return Some(());
    }
    cx.mismatch(node, "null");
    None
}

/// A `nullable` field: null is the empty mapping (Python's `X | None`, `None` authoring nothing),
/// else the model as written.
pub fn read_or_empty<T>(node: &Node, cx: &mut Cx, read: fn(&Node, &mut Cx) -> T) -> T {
    if matches!(node.kind, Kind::Scalar(Value::Null)) {
        let empty = Node {
            span: node.span,
            kind: Kind::Map(Default::default()),
        };
        return read(&empty, cx);
    }
    read(node, cx)
}

/// An `Option<T>` field with `absent`, authored form: null is the absent state,
/// else `T` as written (a leaf's value, or a model's mapping).
pub fn read_nullable<T: FromNode>(node: &Node, cx: &mut Cx) -> Tri<<T::Authored as Lift>::Value>
where
    T::Authored: Lift,
{
    if matches!(node.kind, Kind::Scalar(Value::Null)) {
        return Tri::Null;
    }
    T::authored(node, cx).lift().map_or(Tri::Unset, Tri::Value)
}

/// [`read_nullable`] for a patch layer.
pub fn read_nullable_patch<T: FromNode>(node: &Node, cx: &mut Cx) -> Tri<<T::Patch as Lift>::Value>
where
    T::Patch: Lift,
{
    if matches!(node.kind, Kind::Scalar(Value::Null)) {
        return Tri::Null;
    }
    T::patch(node, cx).lift().map_or(Tri::Unset, Tri::Value)
}

/// A `Patch<T>` slot: null wins over the less specific slot, else `T`'s patch twin.
pub fn read_patch_tri<T: FromNode>(node: &Node, cx: &mut Cx) -> Tri<T::Patch> {
    if matches!(node.kind, Kind::Scalar(Value::Null)) {
        return Tri::Null;
    }
    Tri::Value(T::patch(node, cx))
}

/// An enum of unit variants: a string naming one of `valid`. Its index.
pub fn read_enum(node: &Node, cx: &mut Cx, valid: &[&'static str]) -> Option<usize> {
    let Kind::Scalar(Value::Str(s)) = &node.kind else {
        cx.mismatch(node, "a string");
        return None;
    };
    let found = valid.iter().position(|v| v == s);
    if found.is_none() {
        cx.unknown_tag(node, s, valid);
    }
    found
}

/// An `accept = Alt` leaf: `F` as written, else `Alt` converted by `TryFrom`.
/// When both fail the error is `F`'s, unless `Alt` read and its conversion failed.
pub fn read_accept<F, Alt>(node: &Node, cx: &mut Cx) -> Option<F>
where
    F: ReadScalar + TryFrom<Alt>,
    <F as TryFrom<Alt>>::Error: std::fmt::Display,
    Alt: ReadScalar,
{
    let primary = match cx.attempt(|cx| F::read(node, cx)) {
        Ok(Some(v)) => return Some(v),
        Ok(None) => Vec::new(),
        Err(errs) => errs,
    };
    let alt = match cx.attempt(|cx| Alt::read(node, cx)) {
        Ok(Some(a)) => a,
        _ => {
            for e in primary {
                cx.push(e);
            }
            return None;
        }
    };
    match F::try_from(alt) {
        Ok(v) => Some(v),
        Err(e) => {
            cx.invalid(node.span, e.to_string());
            None
        }
    }
}

fn number(node: &Node) -> Option<&serde_json::Number> {
    match &node.kind {
        Kind::Scalar(Value::Number(n)) => Some(n),
        _ => None,
    }
}

impl ReadScalar for bool {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Kind::Scalar(Value::Bool(b)) = &node.kind {
            return Some(*b);
        }
        cx.mismatch(node, "a valid boolean");
        None
    }
}

impl ReadScalar for String {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Kind::Scalar(Value::Str(s)) = &node.kind {
            return Some(s.clone());
        }
        cx.mismatch(node, "a valid string");
        None
    }
}

impl ReadScalar for f64 {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Some(f) = number(node).and_then(serde_json::Number::as_f64) {
            return Some(f);
        }
        cx.mismatch(node, "a valid number");
        None
    }
}

impl ReadScalar for f32 {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let f = f64::read(node, cx)?;
        if f.is_finite() && f.abs() > f64::from(f32::MAX) {
            cx.mismatch(node, "a valid number in f32 range");
            return None;
        }
        Some(f as f32)
    }
}

/// An integer; a float with no fraction counts.
fn integer(node: &Node) -> Option<i128> {
    let n = number(node)?;
    if let Some(i) = n.as_i64() {
        return Some(i128::from(i));
    }
    if let Some(u) = n.as_u64() {
        return Some(i128::from(u));
    }
    let f = n.as_f64()?;
    (f.fract() == 0.0 && f.abs() < 9.0e15).then_some(f as i128)
}

macro_rules! read_int {
    ($($t:ty),*) => {$(
        impl ReadScalar for $t {
            fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
                if let Some(v) = integer(node).and_then(|i| <$t>::try_from(i).ok()) {
                    return Some(v);
                }
                cx.mismatch(node, "a valid integer");
                None
            }
        }
    )*};
}
read_int!(i32, i64, u32, u64, usize);

impl<T: ReadScalar, R: ReadScalar> ReadScalar for Engine<T, R> {
    /// A rule is tried first; a value that is no rule is read as a value.
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if let Ok(Some(r)) = cx.attempt(|cx| R::read(node, cx)) {
            return Some(Engine::Rule(r));
        }
        T::read(node, cx).map(Engine::Value)
    }
}

/// A scalar-shaped spine type: `Authored` and `Patch` are `Option<Self>`.
macro_rules! leaf_from_node {
    ($($t:ty),*) => {$(
        impl FromNode for $t {
            fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
                <$t as ReadScalar>::read(node, cx)
            }
            fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
                <$t as ReadScalar>::read(node, cx)
            }
            fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
                <$t as ReadScalar>::read(node, cx)
            }
        }
    )*};
}
leaf_from_node!(bool, i32, i64, u32, u64, usize, f32, f64, String);

impl<T: FromNode> FromNode for Vec<T> {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::whole(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::whole(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        let Kind::Seq(items) = &node.kind else {
            cx.mismatch(node, "a valid list");
            return None;
        };
        let mut out = Vec::with_capacity(items.len());
        let mut ok = true;
        for (i, item) in items.iter().enumerate() {
            match cx.at(Seg::Index(i), item, |cx| T::whole(item, cx)) {
                Some(v) => out.push(v),
                None => ok = false,
            }
        }
        ok.then_some(out)
    }
    fn walk(w: &mut Walk) {
        w.field("*", T::walk);
    }
}

impl<T: FromNode> FromNode for Box<T> {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Box::new(T::authored(node, cx))
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Box::new(T::patch(node, cx))
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        T::whole(node, cx).map(Box::new)
    }
    fn keys() -> Vec<String> {
        T::keys()
    }
    fn walk(w: &mut Walk) {
        T::walk(w);
    }
}

impl<T: ReadScalar + Leaf, R: ReadScalar + Leaf> FromNode for Engine<T, R> {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        Self::read(node, cx)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        Self::read(node, cx)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        Self::read(node, cx)
    }
}

/// A map: each entry is read by `each`, in authored order.
fn read_entries<R>(
    node: &Node,
    cx: &mut Cx,
    each: impl Fn(&Node, &mut Cx) -> R,
) -> IndexMap<String, R> {
    let Kind::Map(map) = &node.kind else {
        cx.mismatch(node, "a valid dictionary");
        return IndexMap::new();
    };
    map.iter()
        .map(|(k, e)| {
            let v = cx.entry(k, e, |cx| each(&e.value, cx));
            (k.clone(), v)
        })
        .collect()
}

impl<V: FromNode> FromNode for IndexMap<String, V> {
    fn authored(node: &Node, cx: &mut Cx) -> Self::Authored {
        read_entries(node, cx, V::authored)
    }
    fn patch(node: &Node, cx: &mut Cx) -> Self::Patch {
        read_entries(node, cx, V::patch)
    }
    fn whole(node: &Node, cx: &mut Cx) -> Option<Self::Whole> {
        let Kind::Map(map) = &node.kind else {
            cx.mismatch(node, "a valid dictionary");
            return None;
        };
        let mut out = IndexMap::new();
        for (k, e) in map {
            if let Some(v) = cx.entry(k, e, |cx| V::whole(&e.value, cx)) {
                out.insert(k.clone(), v);
            }
        }
        Some(out)
    }
    fn walk(w: &mut Walk) {
        w.field("*", V::walk);
    }
}

/// Implement `ReadScalar` and `FromNode` for a facet newtype (declared with
/// `facet!`) that reads as `Raw` and is built by `TryFrom<Raw>`; a failed
/// conversion is a type error at the node.
#[macro_export]
macro_rules! from_scalar {
    ($t:ty, $raw:ty) => {
        impl $crate::ReadScalar for $t {
            fn read(node: &$crate::Node, cx: &mut $crate::Cx) -> Option<Self> {
                let raw = <$raw as $crate::ReadScalar>::read(node, cx)?;
                match <$t as TryFrom<$raw>>::try_from(raw) {
                    Ok(v) => Some(v),
                    Err(e) => {
                        cx.invalid(node.span, e.to_string());
                        None
                    }
                }
            }
        }
        impl $crate::FromNode for $t {
            fn authored(node: &$crate::Node, cx: &mut $crate::Cx) -> Self::Authored {
                <$t as $crate::ReadScalar>::read(node, cx)
            }
            fn patch(node: &$crate::Node, cx: &mut $crate::Cx) -> Self::Patch {
                <$t as $crate::ReadScalar>::read(node, cx)
            }
            fn whole(node: &$crate::Node, cx: &mut $crate::Cx) -> Option<Self::Whole> {
                <$t as $crate::ReadScalar>::read(node, cx)
            }
        }
    };
}

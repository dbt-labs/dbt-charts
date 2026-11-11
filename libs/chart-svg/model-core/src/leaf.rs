use crate::{Engine, Merge, Scalar, Tri, TypeExpr};
use indexmap::IndexMap;
use serde_json::Value;

/// A slot nothing was written to: unset, or a patch whose every slot is. A merge skips an empty
/// upper slot, so merging a small patch costs its own size, not the tree's.
pub trait Empty {
    fn is_empty(&self) -> bool;
}

impl<T> Empty for Option<T> {
    fn is_empty(&self) -> bool {
        self.is_none()
    }
}

impl<T> Empty for Tri<T> {
    fn is_empty(&self) -> bool {
        matches!(self, Tri::Unset)
    }
}

impl<V> Empty for IndexMap<String, V> {
    fn is_empty(&self) -> bool {
        IndexMap::is_empty(self)
    }
}

/// The leaves a patch sets, by authored path (`charts.font.size`): the dirty set an incremental
/// cascade starts from. An absent field set to `null` is a set leaf; a map is one leaf per key.
pub trait SetLeaves {
    fn set_leaves(&self, at: &str, f: &mut dyn FnMut(&str));
}

impl<T> SetLeaves for Option<T> {
    fn set_leaves(&self, at: &str, f: &mut dyn FnMut(&str)) {
        if self.is_some() {
            f(at);
        }
    }
}

impl<V> SetLeaves for IndexMap<String, V> {
    fn set_leaves(&self, at: &str, f: &mut dyn FnMut(&str)) {
        for k in self.keys() {
            f(&crate::join(at, k));
        }
    }
}

/// A leaf value as the author wrote it, the JSON Python's `json.dumps` of the authored YAML shows:
/// an enum is its key, a facet its string, a list its items.
pub trait Spell {
    fn spell(&self) -> Value;
}

macro_rules! spell_scalar {
    ($($t:ty),*) => {$(
        impl Spell for $t {
            fn spell(&self) -> Value {
                Value::from(*self)
            }
        }
    )*};
}
spell_scalar!(bool, i32, i64, u32, u64, usize, f64);

impl Spell for f32 {
    fn spell(&self) -> Value {
        Value::from(f64::from(*self))
    }
}

impl Spell for String {
    fn spell(&self) -> Value {
        Value::from(self.as_str())
    }
}

impl<T: Spell> Spell for Vec<T> {
    fn spell(&self) -> Value {
        self.iter().map(Spell::spell).collect()
    }
}

impl<T: Spell, const N: usize> Spell for [T; N] {
    fn spell(&self) -> Value {
        self.iter().map(Spell::spell).collect()
    }
}

impl<T: Spell> Spell for Box<T> {
    fn spell(&self) -> Value {
        (**self).spell()
    }
}

/// A patch as the nested object of its set leaves.
pub fn spell_leaves(patch: &impl LeafValues) -> Value {
    let mut all: Vec<(String, Value)> = Vec::new();
    patch.leaf_values("", &mut |p, v| all.push((p.to_string(), v.spell())));
    let mut root = serde_json::Map::new();
    for (path, v) in &all {
        // A model reports its own path beside its leaves: the deeper leaves speak for it.
        let below = format!("{path}.");
        if all.iter().any(|(q, _)| q.starts_with(&below)) {
            continue;
        }
        insert(&mut root, path, v.clone());
    }
    Value::Object(root)
}

fn insert(at: &mut serde_json::Map<String, Value>, path: &str, v: Value) {
    match path.split_once('.') {
        None => {
            at.insert(path.to_string(), v);
        }
        Some((head, rest)) => {
            let below = at
                .entry(head)
                .or_insert_with(|| Value::Object(Default::default()));
            if let Value::Object(map) = below {
                insert(map, rest, v);
            }
        }
    }
}

/// The set leaves of a patch with their values, by authored path: [`SetLeaves`] for a caller that
/// compares them. A model's own path is not reported, only its leaves'.
pub trait LeafValues {
    fn leaf_values(&self, at: &str, f: &mut dyn FnMut(&str, &dyn Spell));
}

impl<T: Spell> LeafValues for Option<T> {
    fn leaf_values(&self, at: &str, f: &mut dyn FnMut(&str, &dyn Spell)) {
        if let Some(v) = self {
            f(at, v);
        }
    }
}

impl<V: LeafValues> LeafValues for IndexMap<String, V> {
    fn leaf_values(&self, at: &str, f: &mut dyn FnMut(&str, &dyn Spell)) {
        for (k, v) in self {
            v.leaf_values(&crate::join(at, k), f);
        }
    }
}

impl<S: LeafValues> LeafValues for Box<S> {
    fn leaf_values(&self, at: &str, f: &mut dyn FnMut(&str, &dyn Spell)) {
        (**self).leaf_values(at, f);
    }
}

/// A layer slot that merges an upper layer into itself.
pub trait Slot {
    fn merge(&mut self, upper: Self);
}

impl<T> Slot for Option<T> {
    /// Override: a set upper replaces.
    fn merge(&mut self, upper: Self) {
        if upper.is_some() {
            *self = upper;
        }
    }
}

impl<T> Slot for Tri<T> {
    /// Unset keeps lower; null and value replace.
    fn merge(&mut self, upper: Self) {
        if !matches!(upper, Tri::Unset) {
            *self = upper;
        }
    }
}

impl<S: Slot> Slot for IndexMap<String, S> {
    /// By key: a key in both is replaced whole by the upper entry, a new key is added.
    fn merge(&mut self, upper: Self) {
        for (k, v) in upper {
            self.insert(k, v);
        }
    }
}

/// A map field marked `merge = deep`: a key in both merges its entries field by field.
pub fn merge_map_deep<S: Slot>(lower: &mut IndexMap<String, S>, upper: IndexMap<String, S>) {
    for (k, v) in upper {
        match lower.get_mut(&k) {
            Some(entry) => entry.merge(v),
            None => {
                lower.insert(k, v);
            }
        }
    }
}

/// The value inside a twin: `T` for a leaf's `Option<T>`, the twin itself for a model's.
/// It types the `Tri` slot of an `Option<T>` field with `absent`, whether `T` is a leaf or a model.
pub trait Lift {
    type Value;
    fn lift(self) -> Option<Self::Value>;
    /// The set slot holding `v`.
    fn put(v: Self::Value) -> Self;
    /// The leaves a set value holds: itself for a leaf, its set leaves for a model.
    fn leaves(v: &Self::Value, at: &str, f: &mut dyn FnMut(&str));
    /// [`Lift::leaves`] with the values of a model's leaves; a leaf's own value is the caller's.
    fn values(_: &Self::Value, _: &str, _: &mut dyn FnMut(&str, &dyn Spell)) {}
}

impl<T> Lift for Option<T> {
    type Value = T;
    fn lift(self) -> Option<T> {
        self
    }
    fn put(v: T) -> Self {
        Some(v)
    }
    fn leaves(_: &T, at: &str, f: &mut dyn FnMut(&str)) {
        f(at);
    }
}

impl<V: Lift> Lift for IndexMap<String, V>
where
    Self: SetLeaves,
{
    type Value = Self;
    fn lift(self) -> Option<Self> {
        Some(self)
    }
    fn put(v: Self) -> Self {
        v
    }
    fn leaves(v: &Self, at: &str, f: &mut dyn FnMut(&str)) {
        v.set_leaves(at, f);
    }
}

/// How two set values of a [`Lift`] twin combine: a leaf's replaces, a model's patch merges deep.
pub trait Fold: Lift {
    fn fold(lower: &mut Self::Value, upper: Self::Value);
}

impl<T> Fold for Option<T> {
    fn fold(lower: &mut T, upper: T) {
        *lower = upper;
    }
}

/// Merge an `Option<T>` field with `absent`: unset keeps lower, null sets the
/// absent state, a value applies `T`'s strategy (replace for a leaf, deep for a model).
pub fn merge_nullable<T: Leaf>(
    lower: &mut Tri<<T::Patch as Lift>::Value>,
    upper: Tri<<T::Patch as Lift>::Value>,
) where
    T::Patch: Fold,
{
    match (&mut *lower, upper) {
        (_, Tri::Unset) => {}
        (Tri::Value(l), Tri::Value(u)) => <T::Patch as Fold>::fold(l, u),
        (_, upper) => *lower = upper,
    }
}

/// Merge a `Patch<T>` slot: unset keeps lower, null replaces, and a value merges deep into a
/// lower value, so two layers that each author part of an overlay both survive.
pub fn merge_patch_slot<P: Slot>(lower: &mut Tri<P>, upper: Tri<P>) {
    match (&mut *lower, upper) {
        (_, Tri::Unset) => {}
        (Tri::Value(l), Tri::Value(u)) => l.merge(u),
        (_, upper) => *lower = upper,
    }
}

/// How a spine type appears as a field of a twin. Derived by `Model`;
/// implemented here for scalars and containers.
pub trait Leaf {
    /// The whole value in authored form; what `merge = override` replaces with.
    type Whole;
    type Authored: Default + Lift;
    type Patch: Slot + Default + Lift + Empty + SetLeaves;
    /// The strategy used when a field does not declare one.
    const MERGE: Merge;
    /// The type as written, for the field table.
    const EXPR: TypeExpr;
}

macro_rules! scalar {
    ($($t:ty => $s:ident),*) => {$(
        impl Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: Merge = Merge::Override;
            const EXPR: TypeExpr = TypeExpr::Scalar(Scalar::$s);
        }
    )*};
}
crate::build_leaf!(bool, i32, i64, u32, u64, usize, f32, f64, String);
scalar!(bool => Bool, i32 => Int, i64 => Int, u32 => Int, u64 => Int, usize => Int, f32 => Float, f64 => Float, String => Str);

impl<T: Leaf> Leaf for Vec<T> {
    type Whole = Vec<T::Whole>;
    type Authored = Option<Vec<T::Whole>>;
    type Patch = Option<Vec<T::Whole>>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::List(&T::EXPR);
}

impl<T: Leaf, R: Leaf> Leaf for Engine<T, R> {
    type Whole = Engine<T, R>;
    type Authored = Option<Engine<T, R>>;
    type Patch = Option<Engine<T, R>>;
    const MERGE: Merge = Merge::Override;
    const EXPR: TypeExpr = TypeExpr::Engine(&T::EXPR, &R::EXPR);
}

impl<T: Leaf, R: Leaf> crate::Build for Engine<T, R> {
    fn build(slot: Self::Patch, at: &str) -> Result<Self, crate::Unset> {
        slot.ok_or_else(|| crate::Unset {
            paths: vec![at.to_string()],
        })
    }
    fn build_authored(slot: Self::Authored, at: &str) -> Result<Self, crate::Unset> {
        slot.ok_or_else(|| crate::Unset {
            paths: vec![at.to_string()],
        })
    }
}

impl<V: Leaf> Leaf for IndexMap<String, V> {
    type Whole = IndexMap<String, V::Whole>;
    type Authored = IndexMap<String, V::Authored>;
    type Patch = IndexMap<String, V::Patch>;
    const MERGE: Merge = Merge::ByKey;
    const EXPR: TypeExpr = TypeExpr::Map(&V::EXPR);
}

/// A newtype with a facet name over a scalar (`Color`, `Url`, ...): a leaf
/// whose schema carries `x-dbt-facet`. `from_scalar!` lets `FromNode` read it.
#[macro_export]
macro_rules! facet {
    ($t:ty, $name:literal, $base:ident) => {
        impl $crate::Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: $crate::Merge = $crate::Merge::Override;
            const EXPR: $crate::TypeExpr = $crate::TypeExpr::Facet($name, $crate::Scalar::$base);
        }
        $crate::build_leaf!($t);
    };
    ($t:ty, $name:literal, $base:ident, $keywords:expr) => {
        impl $crate::Leaf for $t {
            type Whole = $t;
            type Authored = Option<$t>;
            type Patch = Option<$t>;
            const MERGE: $crate::Merge = $crate::Merge::Override;
            const EXPR: $crate::TypeExpr = $crate::TypeExpr::Keyed(
                &$keywords,
                &$crate::TypeExpr::Facet($name, $crate::Scalar::$base),
            );
        }
        $crate::build_leaf!($t);
    };
}

/// A boxed model: the twins are boxed too, so a deep model (`Style`, tens of KiB per twin) can be a
/// field of a shallow one without the shallow one's stack frames paying for it.
impl<T: Leaf> Leaf for Box<T> {
    type Whole = Box<T::Whole>;
    type Authored = Box<T::Authored>;
    type Patch = Box<T::Patch>;
    const MERGE: Merge = T::MERGE;
    const EXPR: TypeExpr = T::EXPR;
}

impl<S: Slot> Slot for Box<S> {
    fn merge(&mut self, upper: Self) {
        (**self).merge(*upper);
    }
}

impl<E: Empty> Empty for Box<E> {
    fn is_empty(&self) -> bool {
        (**self).is_empty()
    }
}

impl<S: SetLeaves> SetLeaves for Box<S> {
    fn set_leaves(&self, at: &str, f: &mut dyn FnMut(&str)) {
        (**self).set_leaves(at, f);
    }
}

impl<L: Lift> Lift for Box<L> {
    type Value = Box<L::Value>;
    fn lift(self) -> Option<Self::Value> {
        (*self).lift().map(Box::new)
    }
    fn put(v: Self::Value) -> Self {
        Box::new(L::put(*v))
    }
    fn leaves(v: &Self::Value, at: &str, f: &mut dyn FnMut(&str)) {
        L::leaves(v, at, f);
    }
    fn values(v: &Self::Value, at: &str, f: &mut dyn FnMut(&str, &dyn Spell)) {
        L::values(v, at, f);
    }
}

impl<F: Fold> Fold for Box<F> {
    fn fold(lower: &mut Self::Value, upper: Self::Value) {
        F::fold(lower, *upper);
    }
}

/// An authored twin read as the patch twin of the same model: the one conversion between the
/// parse output of a whole board and a mergeable layer. A model's impl is generated from its field
/// table, so a field whose twins cannot convert fails to compile; the whole-leaf types, whose twins
/// are the same `Option<T>`, convert as themselves.
pub trait ToPatch {
    type Out;
    fn to_patch(self) -> Self::Out;
}

impl<T> ToPatch for Option<T> {
    type Out = Option<T>;
    fn to_patch(self) -> Self::Out {
        self
    }
}

impl ToPatch for () {
    type Out = ();
    fn to_patch(self) -> Self::Out {}
}

impl<A: ToPatch> ToPatch for Box<A> {
    type Out = Box<A::Out>;
    fn to_patch(self) -> Self::Out {
        Box::new((*self).to_patch())
    }
}

impl<A: ToPatch> ToPatch for IndexMap<String, A> {
    type Out = IndexMap<String, A::Out>;
    fn to_patch(self) -> Self::Out {
        self.into_iter().map(|(k, v)| (k, v.to_patch())).collect()
    }
}

/// A nullable field's slot (`Tri` of the value inside a twin) converted: a value goes through its
/// twin's conversion, unset and null stay.
pub fn tri_to_patch<A>(slot: crate::Tri<A::Value>) -> crate::Tri<<A::Out as Lift>::Value>
where
    A: Lift + ToPatch,
    A::Out: Lift,
{
    match slot {
        crate::Tri::Unset => crate::Tri::Unset,
        crate::Tri::Null => crate::Tri::Null,
        crate::Tri::Value(v) => match A::put(v).to_patch().lift() {
            Some(v) => crate::Tri::Value(v),
            None => crate::Tri::Unset,
        },
    }
}

/// A patch twin read as the authored twin of the same model: the mirror of [`ToPatch`]. Normalize
/// folds a scope's style as patches, but a board's charts live in `Patch<Board>` as authored values
/// (a tagged-enum model's patch twin is `Option<Authored>`), and the board is built from them, so a
/// folded style has to be written back into a chart's authored `style` before `Board::try_from`
/// reads it. Generated from the field table like [`ToPatch`], and round-tripped against it.
pub trait ToAuthored {
    type Out;
    fn to_authored(self) -> Self::Out;
}

impl<T> ToAuthored for Option<T> {
    type Out = Option<T>;
    fn to_authored(self) -> Self::Out {
        self
    }
}

impl ToAuthored for () {
    type Out = ();
    fn to_authored(self) -> Self::Out {}
}

impl<P: ToAuthored> ToAuthored for Box<P> {
    type Out = Box<P::Out>;
    fn to_authored(self) -> Self::Out {
        Box::new((*self).to_authored())
    }
}

impl<P: ToAuthored> ToAuthored for IndexMap<String, P> {
    type Out = IndexMap<String, P::Out>;
    fn to_authored(self) -> Self::Out {
        self.into_iter()
            .map(|(k, v)| (k, v.to_authored()))
            .collect()
    }
}

/// [`tri_to_patch`], the other way.
pub fn tri_to_authored<P>(slot: crate::Tri<P::Value>) -> crate::Tri<<P::Out as Lift>::Value>
where
    P: Lift + ToAuthored,
    P::Out: Lift,
{
    match slot {
        crate::Tri::Unset => crate::Tri::Unset,
        crate::Tri::Null => crate::Tri::Null,
        crate::Tri::Value(v) => match P::put(v).to_authored().lift() {
            Some(v) => crate::Tri::Value(v),
            None => crate::Tri::Unset,
        },
    }
}

/// A patch with its explicit nulls dropped to unset: an overlay that must not clear what a lower
/// tier set (a merge lets an upper null replace the lower value, as an explicit set-to-absent).
/// Generated from the field table for every struct model.
pub trait DropNulls {
    fn drop_nulls(&mut self);
}

impl DropNulls for () {
    fn drop_nulls(&mut self) {}
}

impl<T> DropNulls for Option<T> {
    fn drop_nulls(&mut self) {}
}

impl<P: DropNulls> DropNulls for Box<P> {
    fn drop_nulls(&mut self) {
        (**self).drop_nulls();
    }
}

impl<P: DropNulls> DropNulls for IndexMap<String, P> {
    fn drop_nulls(&mut self) {
        self.values_mut().for_each(DropNulls::drop_nulls);
    }
}

impl<X: DropNulls> DropNulls for crate::Tri<X> {
    fn drop_nulls(&mut self) {
        match self {
            crate::Tri::Null => *self = crate::Tri::Unset,
            crate::Tri::Value(x) => x.drop_nulls(),
            crate::Tri::Unset => {}
        }
    }
}

/// [`DropNulls`] for a nullable field's slot, whose value is the inside of a twin.
pub fn tri_drop_nulls<P>(slot: &mut crate::Tri<P::Value>)
where
    P: Lift + DropNulls,
{
    match std::mem::replace(slot, crate::Tri::Unset) {
        crate::Tri::Unset | crate::Tri::Null => {}
        crate::Tri::Value(v) => {
            let mut twin = P::put(v);
            twin.drop_nulls();
            if let Some(v) = twin.lift() {
                *slot = crate::Tri::Value(v);
            }
        }
    }
}

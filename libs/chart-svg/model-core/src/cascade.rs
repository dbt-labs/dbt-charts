//! The cascade of a resolved style, whole and incremental (spec § 10, D-32).
//!
//! A [`Resolved`] holds the folded patch (what the layers set), the inherited patch (every leaf
//! filled) and the total value. [`Resolved::apply`] resolves a patch over it by running only the
//! links whose source changed and rebuilding only the values a changed leaf sits in; the result is
//! the one a full recompute gives. The tables come from `inherit::emit`; nothing here knows a model.
//!
//! `Resolved::base` first resolves every color token and categorical palette name in the folded
//! patch (`Tables.resolve_colors`: a color leaf is a facet named `color`, `theme.background`
//! stays for the board's canvas; a gradient's or scale's palette keeps its name, with a role
//! replaced, and its colors resolve; a board's `palettes:`/`roles:` rebind the theme's tokens, as
//! `_theme_base` loads the theme under them) and then expands the named fonts over the folded
//! patch (`Tables.expand`: the `style.fonts` registry folds its own `extends` chain, then each
//! slot that names a font, a sparse `Patch` slot included, takes the fields the entry sets under
//! its own and drops the name; an unknown name is a `Refused`), keeping the unexpanded layers as
//! `Resolved.raw`. `apply` starts again from `raw` when an upper layer touches `fonts`, an
//! `extends`, or a sparse slot that holds a font (`Tables.opaque_fonts`). Both finish the
//! inherited patch with the generated `fill` (each `fill` literal, once the inherit has run,
//! where its container is set and the leaf is not; `Tables.fill`).

use crate::{Build, Empty, Leaf, Lift, Refused, SetLeaves, Slot, Tri, Unset};
use std::fmt;

/// Lists of node or link ids per node, flat: the items of node `n` are `items[start[n]..start[n + 1]]`.
pub struct Csr {
    pub start: &'static [u32],
    pub items: &'static [u16],
}

impl Csr {
    pub fn get(&self, id: u16) -> &'static [u16] {
        let i = usize::from(id);
        match (self.start.get(i), self.start.get(i + 1)) {
            (Some(&a), Some(&b)) => self.items.get(a as usize..b as usize).unwrap_or(&[]),
            _ => &[],
        }
    }
}

/// A link: its source node, its target node and the function that runs it.
pub type Link<T> = (u16, u16, fn(&mut <T as Leaf>::Patch, &mut Vec<Refused>));

/// Called with the authored path of each leaf a fill set.
pub type Filled<'a> = &'a mut dyn FnMut(&str);

/// The color pass: the patch to resolve and the patch whose palette bindings and roles apply.
pub type ResolveColors<T> =
    fn(&mut <T as Leaf>::Patch, &<T as Leaf>::Patch) -> Result<(), Vec<Refused>>;

/// One generated cascade for a root model `T`.
pub struct Tables<T: Leaf + 'static> {
    /// Every field of the tree by authored path below the root, sorted; a node's id is its index.
    pub paths: &'static [&'static str],
    /// Each node's parent node, `NONE` at the top.
    pub parent: &'static [u16],
    /// The node whose value is rebuilt when this node changes: the node itself, or the outermost
    /// absent container it sits in.
    pub owner: &'static [u16],
    /// The links whose source is the node.
    pub by_from: Csr,
    /// The links whose target is the node.
    pub by_to: Csr,
    /// The nodes directly below the node.
    pub children: Csr,
    /// `(source, target, run)` in topological order, `run` being the full pass's own link.
    pub links: &'static [Link<T>],
    /// Set a node of the inherited patch to the folded patch's value (unset where it has none).
    pub copy: fn(u16, &T::Patch, &mut T::Patch),
    /// Whether the folded patch leaves a node unset.
    pub unset: fn(u16, &T::Patch) -> bool,
    /// Rebuild a node's value in the total from the inherited patch.
    pub rebuild: fn(u16, &mut T, &T::Patch, &mut Vec<String>),
    /// The full inherit pass: the reference the incremental one must equal.
    pub inherit: fn(&mut T::Patch) -> Result<(), Vec<Refused>>,
    /// Set each leaf's `fill` literal where the inherited patch has its container and not the leaf,
    /// reporting the authored path of each.
    pub fill: fn(&mut T::Patch, Filled<'_>) -> Result<(), Vec<Refused>>,
    /// Expand the named fonts of a folded patch: each slot that `extends` one takes the fields the
    /// font sets under its own, and the name is dropped.
    pub expand: fn(&mut T::Patch) -> Result<(), Vec<Refused>>,
    /// Sparse slots that hold a font slot: setting one may bring an `extends` the set paths do not show.
    pub opaque_fonts: &'static [&'static str],
    /// Replace each color token in the first patch with the color it names, in the palette and
    /// role context of the second.
    pub resolve_colors: ResolveColors<T>,
}

/// No parent.
pub const NONE: u16 = u16::MAX;

#[derive(Debug, Clone, PartialEq)]
pub enum CascadeError {
    /// A converted link's source value was refused by its target.
    Refused(Vec<Refused>),
    /// The total value could not be built.
    Unset(Unset),
}

impl fmt::Display for CascadeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Refused(r) => {
                let each: Vec<String> = r.iter().map(|r| format!("{}: {}", r.at, r.why)).collect();
                write!(f, "{}", each.join("; "))
            }
            Self::Unset(u) => u.fmt(f),
        }
    }
}

impl std::error::Error for CascadeError {}

/// A resolved style with the patches it came from. Boxed: a style patch is tens of kilobytes.
pub struct Resolved<T: Leaf + 'static> {
    tables: &'static Tables<T>,
    /// What the cascade's layers set; inherit's record of which leaves were set anywhere.
    pub folded: Box<T::Patch>,
    /// The layers as merged, named fonts unexpanded: what a font change has to start again from.
    pub raw: Box<T::Patch>,
    /// `folded` with every inherited leaf filled.
    pub inherited: Box<T::Patch>,
    pub total: Box<T>,
}

/// A set of node ids.
struct Bits(Vec<u64>);

impl Bits {
    fn new(n: usize) -> Self {
        Self(vec![0; n.div_ceil(64)])
    }
    fn has(&self, id: u16) -> bool {
        let i = usize::from(id);
        self.0.get(i / 64).is_some_and(|w| w & (1 << (i % 64)) != 0)
    }
    /// Add `id`; whether it was new.
    fn add(&mut self, id: u16) -> bool {
        let i = usize::from(id);
        match self.0.get_mut(i / 64) {
            Some(w) => {
                let new = *w & (1 << (i % 64)) == 0;
                *w |= 1 << (i % 64);
                new
            }
            None => false,
        }
    }
}

/// The node a set path names, or the nearest one above it (a map key names its map).
fn node_of(paths: &[&str], path: &str) -> Option<u16> {
    let mut p = path;
    loop {
        if let Ok(i) = paths.binary_search(&p) {
            return u16::try_from(i).ok();
        }
        p = p.rsplit_once('.')?.0;
    }
}

/// One incremental pass: which nodes changed, which were reset to the folded patch's value, and the
/// links still to run, lowest (topologically first) first.
struct Pass<'t, T: Leaf + 'static> {
    t: &'t Tables<T>,
    dirty: Bits,
    reset: Bits,
    queued: Bits,
    todo: std::collections::BinaryHeap<std::cmp::Reverse<u16>>,
}

impl<'t, T: Leaf + 'static> Pass<'t, T> {
    fn queue(&mut self, links: &[u16]) {
        for &l in links {
            if self.queued.add(l) {
                self.todo.push(std::cmp::Reverse(l));
            }
        }
    }

    /// `id` and every container above it changed: the links reading them may have to run.
    fn changed(&mut self, id: u16) {
        let mut at = id;
        while at != NONE && self.dirty.add(at) {
            self.queue(self.t.by_from.get(at));
            at = self.t.parent.get(usize::from(at)).copied().unwrap_or(NONE);
        }
    }

    /// `id` was reset to the folded patch's value: every link filling a node below it must run
    /// again, and every link reading one (a container nulled or replaced changes its leaves).
    fn reset(&mut self, id: u16) {
        self.reset.add(id);
        let mut stack = vec![id];
        while let Some(n) = stack.pop() {
            self.queue(self.t.by_to.get(n));
            if n != id && self.dirty.add(n) {
                self.queue(self.t.by_from.get(n));
            }
            stack.extend_from_slice(self.t.children.get(n));
        }
    }

    fn under_reset(&self, id: u16) -> bool {
        let mut at = id;
        while at != NONE {
            if self.reset.has(at) {
                return true;
            }
            at = self.t.parent.get(usize::from(at)).copied().unwrap_or(NONE);
        }
        false
    }
}

impl<T: Leaf + 'static> Clone for Resolved<T>
where
    T: Clone,
    T::Patch: Clone,
{
    fn clone(&self) -> Self {
        Self {
            tables: self.tables,
            folded: self.folded.clone(),
            raw: self.raw.clone(),
            inherited: self.inherited.clone(),
            total: self.total.clone(),
        }
    }
}

impl<T: Leaf + 'static> PartialEq for Resolved<T>
where
    T: PartialEq,
    T::Patch: PartialEq,
{
    fn eq(&self, other: &Self) -> bool {
        self.folded == other.folded
            && self.raw == other.raw
            && self.inherited == other.inherited
            && self.total == other.total
    }
}

impl<T: Leaf + 'static> fmt::Debug for Resolved<T>
where
    T: fmt::Debug,
    T::Patch: fmt::Debug,
{
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Resolved")
            .field("folded", &self.folded)
            .field("inherited", &self.inherited)
            .field("total", &self.total)
            .finish()
    }
}

impl<T: Leaf + 'static> Resolved<T>
where
    T: Build + Clone,
    T::Patch: Clone + Slot + SetLeaves + Empty,
{
    /// Resolve a folded patch from scratch: the full pass. Its color tokens resolve in the context
    /// of the folded patch itself.
    pub fn base(tables: &'static Tables<T>, folded: T::Patch) -> Result<Self, CascadeError> {
        let with = folded.clone();
        Self::base_in(tables, folded, &with)
    }

    /// As [`Resolved::base`], with the color tokens resolved in the palette and role context of
    /// `with`: a host whose tokens bind to a layer other than the folded whole names it.
    pub fn base_in(
        tables: &'static Tables<T>,
        folded: T::Patch,
        with: &T::Patch,
    ) -> Result<Self, CascadeError> {
        let raw = Box::new(folded.clone());
        let mut folded = Box::new(folded);
        (tables.resolve_colors)(&mut folded, with).map_err(CascadeError::Refused)?;
        (tables.expand)(&mut folded).map_err(CascadeError::Refused)?;
        let mut inherited = folded.clone();
        (tables.inherit)(&mut inherited).map_err(CascadeError::Refused)?;
        (tables.fill)(&mut inherited, &mut |_| {}).map_err(CascadeError::Refused)?;
        let total = T::build((*inherited).clone(), "").map_err(CascadeError::Unset)?;
        Ok(Self {
            tables,
            folded,
            raw,
            inherited,
            total: Box::new(total),
        })
    }

    /// `upper` with its color tokens resolved as [`Resolved::apply`] resolves them: against this
    /// style's layers under `upper`. The layer on its own, for a host that ranks it above the tiers
    /// below it. Color tokens only: named fonts (`extends`) are not expanded here, as `apply`
    /// expands them on its way to the total.
    pub fn resolved_layer(&self, upper: &T::Patch) -> Result<T::Patch, CascadeError> {
        let mut raw = self.raw.clone();
        (*raw).merge(upper.clone());
        self.resolve_tokens(&raw, upper)
    }

    /// The one law: `upper`'s color tokens resolve against `raw`, the layers with `upper` over them.
    fn resolve_tokens(&self, raw: &T::Patch, upper: &T::Patch) -> Result<T::Patch, CascadeError> {
        let mut upper = upper.clone();
        (self.tables.resolve_colors)(&mut upper, raw).map_err(CascadeError::Refused)?;
        Ok(upper)
    }

    /// Resolve `upper` over this style: what a full recompute of `folded` merged with `upper`
    /// gives, at the cost of what `upper` changes.
    pub fn apply(&self, upper: &T::Patch) -> Result<Self, CascadeError> {
        let t = self.tables;
        // A font registry entry or a slot's `extends` changes what the slots took: start again.
        let mut renames = false;
        upper.set_leaves("", &mut |path| {
            renames |= path == "palettes"
                || path.starts_with("palettes.")
                || path == "roles"
                || path.starts_with("roles.")
                || path == "fonts"
                || path.starts_with("fonts.")
                || path.ends_with(".extends")
                || t.opaque_fonts.iter().any(|o| {
                    path == *o || path.strip_prefix(o).is_some_and(|r| r.starts_with('.'))
                });
        });
        if renames {
            let mut raw = self.raw.clone();
            (*raw).merge(upper.clone());
            return Self::base(t, *raw);
        }
        let mut raw = self.raw.clone();
        (*raw).merge(upper.clone());
        let upper = self.resolve_tokens(&raw, upper)?;
        let mut folded = self.folded.clone();
        (*folded).merge(upper.clone());
        let mut inherited = self.inherited.clone();
        let mut total = self.total.clone();

        let n = t.paths.len();
        let mut pass = Pass::<T> {
            t,
            dirty: Bits::new(n),
            reset: Bits::new(n),
            queued: Bits::new(t.links.len()),
            todo: std::collections::BinaryHeap::new(),
        };
        let mut set = Vec::new();
        upper.set_leaves("", &mut |path| {
            if let Some(id) = node_of(t.paths, path) {
                set.push(id);
            }
        });
        for id in set {
            if !pass.reset.has(id) {
                (t.copy)(id, &folded, &mut inherited);
                pass.reset(id);
                pass.changed(id);
            }
        }
        let mut refused = Vec::new();
        while let Some(std::cmp::Reverse(l)) = pass.todo.pop() {
            let Some(&(from, to, run)) = t.links.get(usize::from(l)) else {
                continue;
            };
            if (pass.dirty.has(from) || pass.under_reset(to)) && (t.unset)(to, &folded) {
                (t.copy)(to, &folded, &mut inherited);
                run(&mut inherited, &mut refused);
                pass.reset(to);
                pass.changed(to);
            }
        }
        if !refused.is_empty() {
            return Err(CascadeError::Refused(refused));
        }
        let mut filled = Vec::new();
        (t.fill)(&mut inherited, &mut |path| filled.push(path.to_string()))
            .map_err(CascadeError::Refused)?;
        for id in filled.iter().filter_map(|path| node_of(t.paths, path)) {
            pass.changed(id);
        }
        let mut unset = Vec::new();
        let mut rebuilt = Bits::new(n);
        for id in 0..n {
            let id = u16::try_from(id).unwrap_or(NONE);
            if !pass.dirty.has(id) {
                continue;
            }
            let owner = t.owner.get(usize::from(id)).copied().unwrap_or(NONE);
            if owner != NONE && rebuilt.add(owner) {
                (t.rebuild)(owner, &mut total, &inherited, &mut unset);
            }
        }
        if !unset.is_empty() {
            return Err(CascadeError::Unset(Unset { paths: unset }));
        }
        Ok(Self {
            tables: t,
            folded,
            raw,
            inherited,
            total,
        })
    }
}

/// Rebuild an `Option<T>` field with `absent` from its slot.
pub fn rebuild_nullable<T: Build>(
    slot: &Tri<<T::Patch as Lift>::Value>,
    at: &str,
    out: &mut Option<T>,
    unset: &mut Vec<String>,
) where
    T::Patch: Lift,
    <T::Patch as Lift>::Value: Clone,
{
    if let Some(v) = crate::build_nullable::<T>(slot.clone(), at, unset) {
        *out = v;
    }
}

/// Rebuild a `Patch<_>` field: unset is the empty patch, `null` has no total value.
pub fn rebuild_slot<P: Clone + Default>(
    slot: &Tri<P>,
    at: &str,
    out: &mut P,
    unset: &mut Vec<String>,
) {
    match slot {
        Tri::Value(v) => *out = v.clone(),
        Tri::Unset => *out = P::default(),
        Tri::Null => unset.push(at.to_string()),
    }
}

/// Rebuild an `Option<Patch<_>>` field with `absent`: unset and `null` are the absent state.
pub fn rebuild_optional_slot<P: Clone>(
    slot: &Tri<P>,
    _at: &str,
    out: &mut Option<P>,
    _unset: &mut Vec<String>,
) {
    *out = match slot {
        Tri::Value(v) => Some(v.clone()),
        Tri::Unset | Tri::Null => None,
    };
}

/// Rebuild an `omit` field from its slot: unset is `None`.
pub fn rebuild_omitted<T: Build>(
    slot: &T::Patch,
    at: &str,
    out: &mut Option<T>,
    unset: &mut Vec<String>,
) where
    T::Patch: Clone + crate::Empty,
{
    if slot.is_empty() {
        *out = None;
    } else if let Some(v) = crate::build_field::<T>(slot.clone(), at, unset) {
        *out = Some(v);
    }
}

/// Rebuild a `default_empty` field from its slot: unset is the empty value.
pub fn rebuild_defaulted<T: Build + Default>(
    slot: &T::Patch,
    at: &str,
    out: &mut T,
    unset: &mut Vec<String>,
) where
    T::Patch: Clone + crate::Empty,
{
    if slot.is_empty() {
        *out = T::default();
    } else if let Some(v) = crate::build_field::<T>(slot.clone(), at, unset) {
        *out = v;
    }
}

/// Rebuild a field from its slot.
pub fn rebuild_field<T: Build>(slot: &T::Patch, at: &str, out: &mut T, unset: &mut Vec<String>)
where
    T::Patch: Clone,
{
    if let Some(v) = crate::build_field::<T>(slot.clone(), at, unset) {
        *out = v;
    }
}

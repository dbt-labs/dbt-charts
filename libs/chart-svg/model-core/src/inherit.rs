//! Inherit as straight-line code (spec § 10, D-12). [`emit`] reads the field
//! tables below a root model and writes one function that fills every unset
//! leaf of the root's patch from its parent, parents first. The output is
//! committed and drift-tested; the compiler type-checks every link, so a link
//! between leaves of different types does not build. There is no runtime graph.
//!
//! Inputs, per field of a model reachable from the root:
//! - `inherit = "path"`: an unset leaf takes `path`.
//! - `inherit_slot = "path"` with `links(leaf = "path")`: every leaf of the
//!   field mirrors the same relative leaf under `path`; a linked leaf takes its
//!   own path instead. A slot nested in a slot applies at the outer slot's
//!   derived path.
//! - `local`: outside the enclosing slot's mirroring.
//! - an absent nested model (`Option<Inner>` with `absent`, D-23): an unset
//!   container mirrors its source as a whole, then leaf by leaf.
//!
//! Paths are authored keys from the root model's name (`Style.charts.font`);
//! `#[flatten]` fields add no segment.

use crate::{Engine, Field, ModelInfo, ModelKind, Tri, TypeExpr};
use indexmap::{IndexMap, IndexSet};
use std::fmt::Write;

/// A slot that takes its parent's value while it is unset.
pub trait FillFrom<S = Self> {
    fn fill_from(&mut self, from: &S);
}

impl<T: Clone> FillFrom for Option<T> {
    fn fill_from(&mut self, from: &Option<T>) {
        if self.is_none() {
            self.clone_from(from);
        }
    }
}

impl<T: Clone> FillFrom for Tri<T> {
    /// Unset takes the parent's state, null included: an absent parent is absent here.
    fn fill_from(&mut self, from: &Tri<T>) {
        if matches!(self, Tri::Unset) {
            self.clone_from(from);
        }
    }
}

impl<T: Clone> FillFrom<T> for Tri<T> {
    /// A model that is always set fills an absent one whole; its leaves fill the set one's.
    fn fill_from(&mut self, from: &T) {
        if matches!(self, Tri::Unset) {
            *self = Tri::Value(from.clone());
        }
    }
}

impl<T: Clone, R: Clone> FillFrom<Option<T>> for Option<Engine<T, R>> {
    /// A value fills an engine-decided slot as its own value.
    fn fill_from(&mut self, from: &Option<T>) {
        if let (None, Some(v)) = (&*self, from) {
            *self = Some(Engine::Value(v.clone()));
        }
    }
}

impl<T: Clone, R: Clone> FillFrom<Option<Engine<T, R>>> for Option<T> {
    /// Only a value fills a total leaf: a rule is the engine's to decide later, so the leaf stays
    /// unset and the build names it.
    fn fill_from(&mut self, from: &Option<Engine<T, R>>) {
        if let (None, Some(Engine::Value(v))) = (&*self, from) {
            *self = Some(v.clone());
        }
    }
}

impl<T: Clone> FillFrom<Option<T>> for Tri<T> {
    fn fill_from(&mut self, from: &Option<T>) {
        if let (Tri::Unset, Some(v)) = (&*self, from) {
            *self = Tri::Value(v.clone());
        }
    }
}

/// A leaf slot that takes its `fill` literal when nothing set it: `Some(true)` when it did,
/// `Some(false)` when the slot was set already, `None` when its type refuses the literal.
pub trait FillDefault {
    fn fill_default(&mut self, v: crate::Value) -> Option<bool>;
}

/// `v` read as a `T`, as a theme would write it. A literal its type refuses is `None`: the Style
/// tree's generated `fill` reports it, and anywhere else the build leaves the slot unset.
pub fn fill_literal<T: crate::FromNode<Patch = Option<T>>>(v: crate::Value) -> Option<T> {
    let value = match v {
        crate::Value::Null => crate::ScalarValue::Null,
        crate::Value::Bool(b) => crate::ScalarValue::Bool(b),
        crate::Value::Int(i) => crate::ScalarValue::Number(i.into()),
        crate::Value::Float(f) => crate::ScalarValue::Number(serde_json::Number::from_f64(f)?),
        crate::Value::Str(s) => crate::ScalarValue::Str(s.to_string()),
    };
    let span = crate::Span {
        start: 0,
        end: 0,
        line: 1,
        column: 1,
    };
    let node = crate::Node {
        span,
        kind: crate::Kind::Scalar(value),
    };
    T::patch(&node, &mut crate::Cx::new::<T>(None))
}

impl<T: crate::FromNode<Patch = Option<T>>> FillDefault for Option<T> {
    fn fill_default(&mut self, v: crate::Value) -> Option<bool> {
        if self.is_some() {
            return Some(false);
        }
        *self = fill_literal::<T>(v);
        self.is_some().then_some(true)
    }
}

impl<T: crate::FromNode<Patch = Option<T>>> FillDefault for Tri<T> {
    /// Unset takes the literal; an authored null stays null.
    fn fill_default(&mut self, v: crate::Value) -> Option<bool> {
        if !matches!(self, Tri::Unset) {
            return Some(false);
        }
        *self = Tri::Value(fill_literal::<T>(v)?);
        Some(true)
    }
}

/// A list of colors; a different trait from [`TokenSlot`] only because `Option<Vec<C>>` overlaps
/// `Option<C>` for the compiler.
pub trait TokenListSlot {
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String>;
}

/// A color slot whose tokens (`dbt-grays.8`, `chrome[11]`) become the colors they name. The
/// resolver answers `None` for a string that is not a token, which stays as written.
pub trait TokenSlot {
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String>;
}

fn resolve_one<C>(
    c: &mut C,
    f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
) -> Result<(), String>
where
    C: AsRef<str> + TryFrom<String>,
    C::Error: std::fmt::Display,
{
    if let Some(resolved) = f(c.as_ref())? {
        *c = C::try_from(resolved).map_err(|e| e.to_string())?;
    }
    Ok(())
}

impl<C: AsRef<str> + TryFrom<String>> TokenSlot for Option<C>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        self.as_mut().map_or(Ok(()), |c| resolve_one(c, f))
    }
}

/// A map of color leaves (a category's value-to-color assignments): each value is a token slot.
impl<C: AsRef<str> + TryFrom<String>> TokenSlot for indexmap::IndexMap<String, Option<C>>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        self.values_mut()
            .try_for_each(|c| c.as_mut().map_or(Ok(()), |c| resolve_one(c, f)))
    }
}

impl<C: AsRef<str> + TryFrom<String>> TokenSlot for Tri<C>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        match self {
            Tri::Value(c) => resolve_one(c, f),
            _ => Ok(()),
        }
    }
}

impl<C: AsRef<str> + TryFrom<String>, R> TokenSlot for Option<Engine<C, R>>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        match self {
            Some(Engine::Value(c)) => resolve_one(c, f),
            _ => Ok(()),
        }
    }
}

impl<C: AsRef<str> + TryFrom<String>, R> TokenSlot for Tri<Engine<C, R>>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        match self {
            Tri::Value(Engine::Value(c)) => resolve_one(c, f),
            _ => Ok(()),
        }
    }
}

impl<C: AsRef<str> + TryFrom<String>> TokenListSlot for Option<Vec<C>>
where
    C::Error: std::fmt::Display,
{
    fn resolve_tokens(
        &mut self,
        f: &mut dyn FnMut(&str) -> Result<Option<String>, String>,
    ) -> Result<(), String> {
        for c in self.iter_mut().flatten() {
            resolve_one(c, f)?;
        }
        Ok(())
    }
}

/// A slot that takes its parent's value through a conversion, where the two leaves differ in type
/// (`T: TryFrom<S>`). A value the target refuses is an error naming the reason.
pub trait FillTry<S> {
    fn fill_try(&mut self, from: &S) -> Result<(), String>;
}

impl<S: Clone, T: TryFrom<S>> FillTry<Option<S>> for Option<T>
where
    T::Error: std::fmt::Display,
{
    fn fill_try(&mut self, from: &Option<S>) -> Result<(), String> {
        if let (None, Some(v)) = (&*self, from) {
            *self = Some(T::try_from(v.clone()).map_err(|e| e.to_string())?);
        }
        Ok(())
    }
}

impl<S: Clone, T: TryFrom<S>> FillTry<Tri<S>> for Option<T>
where
    T::Error: std::fmt::Display,
{
    /// A named font's absent-able field fills a total slot's leaf: only a value fills it.
    fn fill_try(&mut self, from: &Tri<S>) -> Result<(), String> {
        if let (None, Tri::Value(v)) = (&*self, from) {
            *self = Some(T::try_from(v.clone()).map_err(|e| e.to_string())?);
        }
        Ok(())
    }
}

impl<S: Clone, T: TryFrom<S>> FillTry<Tri<S>> for Tri<T>
where
    T::Error: std::fmt::Display,
{
    fn fill_try(&mut self, from: &Tri<S>) -> Result<(), String> {
        if matches!(self, Tri::Unset) {
            match from {
                Tri::Unset => {}
                Tri::Null => *self = Tri::Null,
                Tri::Value(v) => {
                    *self = Tri::Value(T::try_from(v.clone()).map_err(|e| e.to_string())?)
                }
            }
        }
        Ok(())
    }
}

/// A converted link whose source value the target refused.
#[derive(Debug, Clone, PartialEq)]
pub struct Refused {
    /// The authored path of the target.
    pub at: &'static str,
    pub why: String,
}

/// Why no inherit function can be written.
#[derive(Debug, Clone, PartialEq)]
pub enum InheritError {
    /// A link names a path the model tree does not have.
    UnknownPath { at: String, path: String },
    /// A link joins a container to a leaf.
    KindMismatch { at: String, path: String },
    /// The links form a cycle through these paths.
    Cycle(Vec<String>),
    /// A converted target is not an inherit target.
    UnknownConvert(String),
    /// `links(leaf = ..)` names a leaf the slot's model does not have.
    UnknownLink { at: String, leaf: String },
    /// A mirrored leaf has no source and is not listed as one that may have none.
    NoSource { at: String, path: String },
    /// A listed no-source target has a source after all, or is not mirrored.
    UnknownOrphan(String),
}

impl std::fmt::Display for InheritError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownPath { at, path } => {
                write!(f, "{at} inherits {path}, which is not in the model")
            }
            Self::KindMismatch { at, path } => write!(
                f,
                "{at} inherits {path}: a container and a leaf do not link"
            ),
            Self::Cycle(p) => write!(f, "inherit cycle: {}", p.join(" -> ")),
            Self::UnknownConvert(p) => write!(f, "{p} is converted but has no inherit link"),
            Self::UnknownLink { at, leaf } => {
                write!(f, "{at} links {leaf}, which its model does not have")
            }
            Self::NoSource { at, path } => {
                write!(f, "{at} mirrors {path}, which is not in the model")
            }
            Self::UnknownOrphan(p) => write!(f, "{p} is listed as having no source but has one"),
        }
    }
}

impl std::error::Error for InheritError {}

/// One step of a Rust path to a field: the field, and whether it is a `Tri` container.
#[derive(Debug, Clone)]
struct Step {
    field: String,
    tri: bool,
    /// A map of models: the walk goes through every value.
    map: bool,
    /// The authored path up to and including this step.
    logical: String,
}

/// How a node's value is built into the total.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Kind {
    /// A leaf or a model: `Build`.
    Plain,
    /// An `Option<T>` with `absent`, a leaf or a model.
    Nullable,
    /// A `Patch<_>` slot.
    Slot,
    /// An `Option<Patch<_>>` slot with `absent`: unset and `null` rebuild to `None`.
    OptionalSlot,
    /// An `Option<leaf>` with `omit`: unset rebuilds to `None`.
    Omitted,
    /// A map or list with `default_empty`: unset rebuilds empty.
    Defaulted,
}

#[derive(Debug, Clone)]
struct Node {
    access: Vec<Step>,
    /// An absent nested model: a `Tri` holding a patch.
    container: bool,
    kind: Kind,
    /// A model that is not absent: its leaves are the nodes that change.
    is_struct: bool,
    /// The leaf's `fill` literal: what a total value holds where nothing set it.
    fill: Option<crate::Value>,
}

struct Ctx<'a> {
    /// The slot being mirrored: where it sits and the path it mirrors.
    slot: Option<(&'a [String], &'a [String])>,
    links: &'static [(&'static str, &'static str)],
    skip: bool,
}

struct Mount {
    at: String,
    links: &'static [(&'static str, &'static str)],
    fills: &'static [(&'static str, crate::Value)],
}

struct MapFill {
    path: String,
    access: Vec<Step>,
    value: crate::Value,
}

#[derive(Default)]
struct Plan {
    nodes: IndexMap<String, Node>,
    graph: IndexMap<String, String>,
    /// Targets of the graph that a slot mirrors (not named by `inherit` or `links`).
    mirrored: IndexSet<String>,
    /// Slot sources that must exist as a model path, with the link that names them.
    slot_sources: Vec<(String, String)>,
    /// Models with a named-font `extends` beside the font fields: where each sits and which of the
    /// registry's fields it shares.
    font_slots: Vec<FontSlot>,
    /// The registry entries' own shared fields (the model named `NamedFont`).
    registry_fields: Vec<&'static str>,
    /// Sparse slots a font slot sits in or at: an upper layer that sets one may carry an `extends`
    /// the set paths cannot show.
    opaque_fonts: IndexSet<String>,
    /// Every leaf that holds a color (a facet named `color`), maps of models walked through.
    color_sites: Vec<ColorSite>,
    /// Every `fill` leaf below a map of models: the entries are not nodes, so each is filled where
    /// it stands.
    map_fills: Vec<MapFill>,
    /// `link_at` / `fill_at` below a field: one shared type mounted at different tiers.
    mounts: Vec<Mount>,
    /// The `palette` and `single_series_palette` of a categorical color style: a palette name
    /// (or a theme role) becomes its stops.
    palette_sites: Vec<PaletteSite>,
    errors: Vec<InheritError>,
}

/// The fields a named font (`style.fonts.<name>`) can give a slot that extends it.
const FONT_FIELDS: [&str; 8] = [
    "family",
    "color",
    "size",
    "weight",
    "style",
    "decoration",
    "case",
    "line_height",
];

struct PaletteSite {
    path: String,
    access: Vec<Step>,
    /// A gradient's or a scale's palette: the name is baked later, so only a role is replaced.
    name_only: bool,
}

struct ColorSite {
    path: String,
    access: Vec<Step>,
    list: bool,
    /// A field that may hold a query column's name instead of a color.
    column: bool,
}

fn is_column_ref(ty: &TypeExpr) -> bool {
    match ty {
        TypeExpr::Facet(name, _) => *name == "color_or_column",
        TypeExpr::Keyed(_, inner)
        | TypeExpr::Option(inner)
        | TypeExpr::Patch(inner)
        | TypeExpr::List(inner)
        | TypeExpr::Engine(inner, _) => is_column_ref(inner),
        _ => false,
    }
}

fn is_list(ty: &TypeExpr) -> bool {
    match ty {
        TypeExpr::List(_) => true,
        TypeExpr::Keyed(_, inner) | TypeExpr::Option(inner) | TypeExpr::Patch(inner) => {
            is_list(inner)
        }
        _ => false,
    }
}

/// Whether a leaf type holds a color: the `color` facet, bare or under `Option`, `Patch`,
/// `Engine`, `List` or a keyword wrapper.
fn is_color_leaf(ty: &TypeExpr) -> bool {
    match ty {
        TypeExpr::Facet(name, _) => {
            matches!(*name, "color" | "color_or_column" | "category_color")
        }
        TypeExpr::Keyed(_, inner)
        | TypeExpr::Option(inner)
        | TypeExpr::Patch(inner)
        | TypeExpr::List(inner)
        | TypeExpr::Engine(inner, _) => is_color_leaf(inner),
        TypeExpr::Either(a, b) => is_color_leaf(a) || is_color_leaf(b),
        _ => false,
    }
}

struct FontSlot {
    path: String,
    access: Vec<Step>,
    fields: Vec<&'static str>,
}

fn model_of(f: &Field) -> Option<(&'static ModelInfo, bool)> {
    let (ty, tri) = match f.ty {
        TypeExpr::Option(inner) if f.attrs.absent.is_some() || f.attrs.omit.is_some() => {
            (*inner, true)
        }
        ty => (ty, false),
    };
    match ty {
        TypeExpr::Ref(_, info) if info().kind == ModelKind::Struct => Some((info(), tri)),
        _ => None,
    }
}

fn has_field(info: &'static ModelInfo, name: &str) -> bool {
    info.fields
        .iter()
        .any(|f| match (f.attrs.flatten, model_of(f)) {
            (true, Some((inner, _))) => has_field(inner, name),
            _ => f.name == name,
        })
}

fn dotted(p: &[String]) -> String {
    p.join(".")
}

/// `Style.charts.axis_x` as the authored path `charts.axis_x`.
fn below_path(p: &str) -> String {
    p.split_once('.').map_or(p, |(_, rest)| rest).to_string()
}

/// Every model below `info`, a sparse `Patch` slot included, that names a font with `extends`.
fn font_walk(
    info: &'static ModelInfo,
    logical: &mut Vec<String>,
    access: &mut Vec<Step>,
    opaque: Option<&str>,
    seen: &mut Vec<&'static str>,
    plan: &mut Plan,
) {
    if seen.contains(&info.name) {
        return;
    }
    seen.push(info.name);
    let has = |n: &str| info.fields.iter().any(|f| f.name == n);
    if has("extends") && has("family") {
        let shared: Vec<&'static str> = FONT_FIELDS.into_iter().filter(|n| has(n)).collect();
        if info.name == "NamedFont" {
            plan.registry_fields = shared;
        } else {
            if let Some(o) = opaque {
                plan.opaque_fonts.insert(below_path(o));
            }
            plan.font_slots.push(FontSlot {
                path: dotted(logical),
                access: access.clone(),
                fields: shared,
            });
        }
    }
    for f in info.fields {
        let patch = matches!(
            f.ty,
            TypeExpr::Patch(_) | TypeExpr::Option(TypeExpr::Patch(_))
        );
        let (ty, tri) = match f.ty {
            TypeExpr::Option(inner) if f.attrs.absent.is_some() || f.attrs.omit.is_some() => {
                (*inner, true)
            }
            ty => (ty, false),
        };
        let (ty, tri) = match ty {
            TypeExpr::Patch(inner) => (*inner, true),
            TypeExpr::Option(TypeExpr::Patch(inner)) => (*inner, true),
            ty => (ty, tri),
        };
        let under_scale = logical.iter().any(|s| s == "gradient" || s == "scale");
        if f.name == "palette" && under_scale
            || info.name == "CategoricalColorStyle"
                && matches!(f.name, "palette" | "single_series_palette")
        {
            let mut at = access.clone();
            at.push(Step {
                field: f.name.to_string(),
                tri: false,
                map: false,
                logical: String::new(),
            });
            logical.push(f.name.to_string());
            plan.palette_sites.push(PaletteSite {
                path: dotted(logical),
                access: at,
                name_only: info.name != "CategoricalColorStyle",
            });
            logical.pop();
            continue;
        }
        if let Some(value) = f.attrs.fill_value {
            if access.iter().any(|s| s.map) {
                let mut at = access.clone();
                at.push(Step {
                    field: f.name.to_string(),
                    tri: false,
                    map: false,
                    logical: String::new(),
                });
                logical.push(f.name.to_string());
                plan.map_fills.push(MapFill {
                    path: dotted(logical),
                    access: at,
                    value,
                });
                logical.pop();
                continue;
            }
        }
        let color_map = matches!(ty, TypeExpr::Map(inner) if is_color_leaf(inner));
        if is_color_leaf(f.ty) || color_map {
            let mut at = access.clone();
            at.push(Step {
                field: f.name.to_string(),
                tri: false,
                map: false,
                logical: String::new(),
            });
            logical.push(f.name.to_string());
            plan.color_sites.push(ColorSite {
                path: dotted(logical),
                access: at,
                list: is_list(f.ty),
                column: is_column_ref(f.ty),
            });
            logical.pop();
            continue;
        }
        if let TypeExpr::Map(TypeExpr::Ref(_, entry)) = ty {
            let entry = entry();
            if entry.name == "NamedFont" {
                let has = |n: &str| entry.fields.iter().any(|f| f.name == n);
                plan.registry_fields = FONT_FIELDS.into_iter().filter(|n| has(n)).collect();
            }
            if entry.kind == ModelKind::Struct {
                let slots_before = plan.font_slots.len();
                logical.push(format!("{}.*", f.name));
                access.push(Step {
                    field: f.name.to_string(),
                    tri: false,
                    map: true,
                    logical: dotted(logical),
                });
                font_walk(entry, logical, access, opaque, seen, plan);
                access.pop();
                logical.pop();
                if plan.font_slots.len() > slots_before && entry.name != "NamedFont" {
                    // A layer sets such a map by its entries' keys, which show no `extends`.
                    logical.push(f.name.to_string());
                    plan.opaque_fonts.insert(below_path(&dotted(logical)));
                    logical.pop();
                }
            }
            continue;
        }
        let TypeExpr::Ref(_, inner) = ty else {
            continue;
        };
        let inner = inner();
        if inner.kind != ModelKind::Struct {
            continue;
        }
        if f.attrs.flatten {
            access.push(Step {
                field: f.name.to_string(),
                tri: false,
                map: false,
                logical: dotted(logical),
            });
            font_walk(inner, logical, access, opaque, seen, plan);
        } else {
            logical.push(f.name.to_string());
            access.push(Step {
                field: f.name.to_string(),
                tri,
                map: false,
                logical: dotted(logical),
            });
            let here = dotted(logical);
            let inside = opaque.map(str::to_string).or_else(|| patch.then_some(here));
            font_walk(inner, logical, access, inside.as_deref(), seen, plan);
            logical.pop();
        }
        access.pop();
    }
    seen.pop();
}

fn walk(
    info: &'static ModelInfo,
    logical: &mut Vec<String>,
    access: &mut Vec<Step>,
    ctx: &Ctx,
    seen: &mut Vec<&'static str>,
    plan: &mut Plan,
) {
    if seen.contains(&info.name) {
        return;
    }
    seen.push(info.name);
    for f in info.fields {
        let nested = model_of(f);
        if f.attrs.flatten {
            if let Some((inner, _)) = nested {
                access.push(Step {
                    field: f.name.to_string(),
                    tri: false,
                    map: false,
                    logical: dotted(logical),
                });
                walk(inner, logical, access, ctx, seen, plan);
                access.pop();
            }
            continue;
        }
        logical.push(f.name.to_string());
        let here = dotted(logical);
        access.push(Step {
            field: f.name.to_string(),
            tri: nested.is_some_and(|n| n.1),
            map: false,
            logical: here.clone(),
        });
        let kind = match f.ty {
            TypeExpr::Patch(_) => Kind::Slot,
            TypeExpr::Option(TypeExpr::Patch(_)) if f.attrs.absent.is_some() => Kind::OptionalSlot,
            TypeExpr::Option(_) if f.attrs.absent.is_some() => Kind::Nullable,
            TypeExpr::Option(_) if f.attrs.omit.is_some() => Kind::Omitted,
            _ if f.attrs.default_empty => Kind::Defaulted,
            _ => Kind::Plain,
        };
        plan.nodes.insert(
            here.clone(),
            Node {
                access: access.clone(),
                container: nested.is_some_and(|n| n.1),
                kind,
                is_struct: nested.is_some_and(|n| !n.1),
                fill: f.attrs.fill_value,
            },
        );
        if !f.attrs.links_at.is_empty() || !f.attrs.fills_at.is_empty() {
            plan.mounts.push(Mount {
                at: here.clone(),
                links: f.attrs.links_at,
                fills: f.attrs.fills_at,
            });
        }
        match nested {
            Some((inner, tri)) => {
                let mirrored = |slot: (&[String], &[String])| {
                    let mut to = slot.1.to_vec();
                    to.extend(logical.iter().skip(slot.0.len()).cloned());
                    dotted(&to)
                };
                if f.attrs.local {
                    let sub = Ctx {
                        slot: None,
                        links: &[],
                        skip: true,
                    };
                    walk(inner, logical, access, &sub, seen, plan);
                } else if let (Some(from), false) = (f.attrs.inherit_slot, ctx.skip) {
                    if let Some(slot) = ctx.slot {
                        if tri {
                            plan.mirrored.insert(here.clone());
                            plan.graph.insert(here.clone(), mirrored(slot));
                        }
                        walk(inner, logical, access, ctx, seen, plan);
                    } else {
                        let source: Vec<String> = from.split('.').map(str::to_string).collect();
                        if tri {
                            plan.graph.insert(here.clone(), from.to_string());
                        }
                        plan.slot_sources.push((here.clone(), from.to_string()));
                        for (leaf, _) in f.attrs.links {
                            if !has_field(inner, leaf) {
                                plan.errors.push(InheritError::UnknownLink {
                                    at: here.clone(),
                                    leaf: leaf.to_string(),
                                });
                            }
                        }
                        let at = logical.clone();
                        let sub = Ctx {
                            slot: Some((&at, &source)),
                            links: f.attrs.links,
                            skip: false,
                        };
                        walk(inner, logical, access, &sub, seen, plan);
                    }
                } else {
                    if let (true, Some(slot), false) = (tri, ctx.slot, ctx.skip) {
                        plan.mirrored.insert(here.clone());
                        plan.graph.insert(here.clone(), mirrored(slot));
                    }
                    walk(inner, logical, access, ctx, seen, plan);
                }
            }
            None => {
                if let Some((_, to)) = ctx.links.iter().find(|(leaf, _)| *leaf == f.name) {
                    plan.graph.insert(here, to.to_string());
                } else if let (Some(slot), false, false) = (ctx.slot, f.attrs.local, ctx.skip) {
                    let mut from = slot.1.to_vec();
                    from.extend(logical.iter().skip(slot.0.len()).cloned());
                    plan.mirrored.insert(here.clone());
                    plan.graph.insert(here, dotted(&from));
                } else if let Some(from) = f.attrs.inherit {
                    if from != here {
                        plan.graph.insert(here, from.to_string());
                    }
                }
            }
        }
        access.pop();
        logical.pop();
    }
    seen.pop();
}

/// The Rust expression for `step`s below `root`, with the `if let` lines that unwrap each
/// `Tri` container on the way (all but a container that is itself the end of the path).
fn access(
    root: &str,
    steps: &[Step],
    own_container: bool,
    mutable: bool,
    tag: &str,
    opens: &mut Vec<String>,
) -> String {
    let mut expr = root.to_string();
    let borrow = if mutable { "&mut " } else { "&" };
    let last = steps.len().saturating_sub(1);
    for (i, s) in steps.iter().enumerate() {
        let _ = write!(expr, ".{}", ident(&s.field));
        if s.map {
            let var = format!("{tag}{}", opens.len());
            let values = if mutable { "values_mut" } else { "values" };
            opens.push(format!("for {var} in {expr}.{values}() {{"));
            expr = var;
        } else if s.tri && !(i == last && own_container) {
            let var = format!("{tag}{}", opens.len());
            opens.push(format!(
                "if let ::model_core::Tri::Value({var}) = {borrow}{expr} {{"
            ));
            expr = var;
        }
    }
    expr
}

fn ident(name: &str) -> String {
    const KEYWORDS: &[&str] = &[
        "as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn",
        "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref",
        "return", "self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use",
        "where", "while", "async", "await", "dyn", "abstract", "become", "box", "do", "final",
        "macro", "override", "priv", "typeof", "unsized", "virtual", "yield", "try",
    ];
    if KEYWORDS.contains(&name) {
        format!("r#{name}")
    } else {
        name.to_string()
    }
}

/// The `link_at` and `fill_at` of each mount: the same links an `inherit_slot` states, but for a
/// leaf or container below a field that is not a slot, because the type is shared by mounts at
/// other tiers that must not take them. A container takes its source whole and then leaf by leaf.
fn apply_mounts(plan: &mut Plan) {
    for mount in std::mem::take(&mut plan.mounts) {
        for (rel, from) in mount.links {
            let at = format!("{}.{rel}", mount.at);
            let Some(node) = plan.nodes.get(&at) else {
                plan.errors.push(InheritError::UnknownPath {
                    at: mount.at.clone(),
                    path: at,
                });
                continue;
            };
            if node.container {
                plan.slot_sources.push((at.clone(), from.to_string()));
            }
            plan.graph.insert(at.clone(), from.to_string());
            if node.container {
                let below = format!("{at}.");
                let kids: Vec<String> = plan
                    .nodes
                    .iter()
                    .filter(|(k, n)| k.starts_with(&below) && !n.is_struct)
                    .map(|(k, _)| k.clone())
                    .collect();
                for kid in kids {
                    let source = format!("{from}.{}", &kid[below.len()..]);
                    plan.graph.entry(kid.clone()).or_insert(source);
                    plan.mirrored.insert(kid);
                }
            }
        }
        for (rel, value) in mount.fills {
            let at = format!("{}.{rel}", mount.at);
            match plan.nodes.get_mut(&at) {
                Some(node) => node.fill = Some(*value),
                None => plan.errors.push(InheritError::UnknownPath {
                    at: mount.at.clone(),
                    path: at,
                }),
            }
        }
    }
}

/// Parents before children: a node follows its source, and every container above it or
/// above its source that is itself filled (so the container holds a value when read or written).
fn order(plan: &Plan) -> Result<Vec<String>, InheritError> {
    fn visit(
        n: &str,
        plan: &Plan,
        done: &mut IndexSet<String>,
        stack: &mut Vec<String>,
    ) -> Result<(), InheritError> {
        if done.contains(n) {
            return Ok(());
        }
        if stack.iter().any(|s| s == n) {
            let mut cycle = stack.clone();
            cycle.push(n.to_string());
            return Err(InheritError::Cycle(cycle));
        }
        stack.push(n.to_string());
        let mut deps: Vec<String> = Vec::new();
        for p in [Some(n), plan.graph.get(n).map(String::as_str)]
            .into_iter()
            .flatten()
        {
            let mut prefix = String::new();
            let segs: Vec<&str> = p.split('.').collect();
            for (i, seg) in segs.iter().enumerate() {
                if !prefix.is_empty() {
                    prefix.push('.');
                }
                prefix.push_str(seg);
                if i + 1 < segs.len() && plan.graph.contains_key(&prefix) {
                    deps.push(prefix.clone());
                }
            }
        }
        if let Some(from) = plan.graph.get(n) {
            deps.push(from.clone());
        }
        for d in deps {
            if d != n {
                visit(&d, plan, done, stack)?;
            }
        }
        stack.pop();
        if plan.graph.contains_key(n) {
            done.insert(n.to_string());
        }
        Ok(())
    }
    let mut done = IndexSet::new();
    for n in plan.graph.keys() {
        visit(n, plan, &mut done, &mut Vec::new())?;
    }
    Ok(done.into_iter().collect())
}

/// The inherit function for `root`'s patch, as Rust source. `patch_ty` is the root patch type
/// and `total_ty` the root model, as the generated file names them (`super::StylePatch`, `super::Style`). `convert` lists the `(target, reason)` links whose
/// two leaves are different types: they fill through `TryFrom` ([`FillTry`]) and are named in the output.
pub fn emit(
    root: &'static ModelInfo,
    patch_ty: &str,
    total_ty: &str,
    convert: &[(&str, &str)],
    no_source: &[&str],
    colors: Option<&str>,
) -> Result<String, InheritError> {
    let mut plan = Plan::default();
    let mut logical = vec![root.name.to_string()];
    let ctx = Ctx {
        slot: None,
        links: &[],
        skip: false,
    };
    walk(
        root,
        &mut logical,
        &mut Vec::new(),
        &ctx,
        &mut Vec::new(),
        &mut plan,
    );
    font_walk(
        root,
        &mut vec![root.name.to_string()],
        &mut Vec::new(),
        None,
        &mut Vec::new(),
        &mut plan,
    );
    apply_mounts(&mut plan);
    if let Some(e) = plan.errors.first() {
        return Err(e.clone());
    }
    let nodes = &plan.nodes;
    let exists =
        |p: &str| nodes.contains_key(p) || nodes.keys().any(|k| k.starts_with(&format!("{p}.")));
    for (at, from) in &plan.slot_sources {
        if !exists(from) {
            return Err(InheritError::UnknownPath {
                at: at.clone(),
                path: from.clone(),
            });
        }
    }
    // A leaf a slot mirrors but its source lacks has nothing to take (a model folded from two
    // Python classes can hold more leaves than the slot it sits in); it is listed in the output.
    for (target, _) in convert {
        if !plan.graph.contains_key(*target) {
            return Err(InheritError::UnknownConvert(target.to_string()));
        }
    }
    let mut orphans = Vec::new();
    let mut unlisted = None;
    plan.graph.retain(|to, from| {
        let orphan = plan.mirrored.contains(to) && !nodes.contains_key(from);
        if orphan {
            if !no_source.contains(&to.as_str()) {
                unlisted.get_or_insert_with(|| InheritError::NoSource {
                    at: to.clone(),
                    path: from.clone(),
                });
            }
            orphans.push((to.clone(), from.clone()));
        }
        !orphan
    });
    if let Some(e) = unlisted {
        return Err(e);
    }
    if let Some(stale) = no_source
        .iter()
        .find(|t| !orphans.iter().any(|(o, _)| o == *t))
    {
        return Err(InheritError::UnknownOrphan(stale.to_string()));
    }
    for (to, from) in &plan.graph {
        let (Some(t), Some(s)) = (nodes.get(to), nodes.get(from)) else {
            return Err(InheritError::UnknownPath {
                at: to.clone(),
                path: from.clone(),
            });
        };
        // An absent model may take a model that is always set (`Tri` from a plain struct).
        if t.container != s.container && !(t.container && s.is_struct) {
            return Err(InheritError::KindMismatch {
                at: to.clone(),
                path: from.clone(),
            });
        }
    }
    let mut out = String::new();
    let _ = writeln!(
        out,
        "//! Generated by `model_core::inherit::emit` from the field tables of `{}`; do not edit.",
        root.name
    );
    let _ = writeln!(out, "//! Parents first; the compiler checks every link.");
    if !orphans.is_empty() {
        let _ = writeln!(
            out,
            "//!\n//! Mirrored leaves whose slot source has no such leaf (nothing to take):"
        );
        for (to, from) in &orphans {
            let _ = writeln!(out, "//! - {to} <- {from}");
        }
    }
    if !convert.is_empty() {
        let _ = writeln!(
            out,
            "//!\n//! Converted through `TryFrom` (the leaves differ in type):"
        );
        for (t, why) in convert {
            let _ = writeln!(out, "//! - {t}: {why}");
        }
    }
    out.push('\n');
    let _ = writeln!(out, "#![allow(clippy::all)]\n");
    let import = if convert.is_empty() {
        "FillFrom"
    } else {
        "{FillFrom, FillTry}"
    };
    let _ = writeln!(out, "use ::model_core::{import};\n");
    // One function per link, so the stack a call needs does not grow with the style tree: a
    // single function holding every link's bindings outgrows a thread's stack in a debug build.
    let mut calls = String::new();
    let mut links = Vec::new();
    for (n, to) in order(&plan)?.into_iter().enumerate() {
        let (Some(from), Some(t)) = (plan.graph.get(&to), nodes.get(&to)) else {
            continue;
        };
        let Some(s) = nodes.get(from) else { continue };
        let mut opens = Vec::new();
        let target = access("style", &t.access, t.container, true, "t", &mut opens);
        let source = access("style", &s.access, s.container, false, "s", &mut opens);
        let fallible = convert.iter().any(|(t, _)| *t == to);
        let refused = if fallible { "refused" } else { "_refused" };
        let _ = writeln!(
            out,
            "// {to} <- {from}\n#[rustfmt::skip]\nfn link_{n}(style: &mut {patch_ty}, {refused}: &mut Vec<::model_core::Refused>) {{"
        );
        for (i, o) in opens.iter().enumerate() {
            let _ = writeln!(out, "{}{o}", "    ".repeat(i + 1));
        }
        let pad = "    ".repeat(opens.len() + 1);
        if fallible {
            let _ = writeln!(
                out,
                "{pad}if let Err(why) = {target}.fill_try(&{source}) {{ refused.push(::model_core::Refused {{ at: \"{to}\", why }}); }}"
            );
        } else {
            let _ = writeln!(out, "{pad}{target}.fill_from(&{source});");
        }
        for i in (0..opens.len()).rev() {
            let _ = writeln!(out, "{}}}", "    ".repeat(i + 1));
        }
        let _ = writeln!(out, "}}\n");
        let _ = writeln!(calls, "    link_{n}(style, &mut refused);");
        links.push((from.clone(), to.clone(), n));
    }
    // Named fonts: the registry folds its own `extends` chain, then each slot that names a font
    // takes the fields the entry sets under its own. Runs on the folded patch before the inherit.
    // One closure per slot, in `expand`, over a snapshot of the folded registry: a slot inside a map
    // of models is expanded value by value.
    let mut expands = String::new();
    for (i, slot) in plan.font_slots.iter().enumerate() {
        let mut write = Vec::new();
        let at = access("style", &slot.access, false, true, "t", &mut write);
        let p = below(root.name, &slot.path);
        let _ = writeln!(
            expands,
            "    let expand_{i} = |style: &mut {patch_ty}, refused: &mut Vec<::model_core::Refused>| {{"
        );
        for (j, o) in write.iter().enumerate() {
            let _ = writeln!(expands, "{}{o}", "        ".repeat(j + 1));
        }
        let pad = "        ".repeat(write.len() + 1);
        let _ = writeln!(
            expands,
            "{pad}let named = if let ::model_core::Tri::Value(n) = &{at}.extends {{ Some(n.clone()) }} else {{ None }};\n{pad}if let Some(name) = named {{\n{pad}    match fonts.get(&name) {{\n{pad}        Some(reg) => {{\n{pad}            {at}.extends = ::model_core::Tri::Unset;"
        );
        for f in &slot.fields {
            let f = ident(f);
            let _ = writeln!(
                expands,
                "{pad}            if let Err(why) = ::model_core::FillTry::fill_try(&mut {at}.{f}, &reg.{f}) {{ refused.push(::model_core::Refused {{ at: \"{p}.{f}\", why }}); }}"
            );
        }
        let _ = writeln!(
            expands,
            "{pad}        }}\n{pad}        None => {{\n{pad}            let known = fonts.keys().cloned().collect::<Vec<_>>().join(\", \");\n{pad}            refused.push(::model_core::Refused {{ at: \"{p}\", why: format!(\"Unknown font '{{name}}'. Known fonts: {{known}}.\") }});\n{pad}        }}\n{pad}    }}\n{pad}}}"
        );
        for j in (0..write.len()).rev() {
            let _ = writeln!(expands, "{}}}", "        ".repeat(j + 1));
        }
        let _ = writeln!(expands, "    }};\n    expand_{i}(style, &mut refused);");
    }
    if !expands.is_empty() {
        expands.insert_str(0, "    let fonts = style.fonts.clone();\n");
    }
    // Color tokens: every color leaf goes through the hook's resolver, in a block of its own.
    match colors {
        None => {
            let _ = writeln!(
                out,
                "pub fn resolve_colors(_: &mut {patch_ty}, _: &{patch_ty}) -> Result<(), Vec<::model_core::Refused>> {{\n    Ok(())\n}}\n"
            );
        }
        Some(hook) => {
            let mut body = String::new();
            for site in &plan.color_sites {
                let mut opens = Vec::new();
                let at = access("style", &site.access, true, true, "c", &mut opens);
                let p = below(root.name, &site.path);
                let tr = if site.list {
                    "TokenListSlot"
                } else {
                    "TokenSlot"
                };
                let resolver = if site.column {
                    "resolve_column"
                } else if p.split('.').any(|seg| seg == "annotations") {
                    "resolve_annotation"
                } else {
                    "resolve"
                };
                let _ = writeln!(body, "    {{");
                for (j, o) in opens.iter().enumerate() {
                    let _ = writeln!(body, "{}{o}", "        ".repeat(j + 1));
                }
                let pad = "        ".repeat(opens.len() + 1);
                let _ = writeln!(
                    body,
                    "{pad}if let Err(why) = ::model_core::{tr}::resolve_tokens(&mut {at}, &mut |s| {hook}::{resolver}(&ctx, s)) {{ refused.push(::model_core::Refused {{ at: \"{p}\", why }}); }}"
                );
                for j in (0..opens.len()).rev() {
                    let _ = writeln!(body, "{}}}", "        ".repeat(j + 1));
                }
                let _ = writeln!(body, "    }}");
            }
            for site in &plan.palette_sites {
                let mut opens = Vec::new();
                let at = access("style", &site.access, true, true, "c", &mut opens);
                let p = below(root.name, &site.path);
                let leaf = if site.name_only {
                    "palette_name_leaf"
                } else {
                    "palette_leaf"
                };
                let _ = writeln!(body, "    {{");
                for (j, o) in opens.iter().enumerate() {
                    let _ = writeln!(body, "{}{o}", "        ".repeat(j + 1));
                }
                let pad = "        ".repeat(opens.len() + 1);
                let _ = writeln!(
                    body,
                    "{pad}if let Err(why) = {hook}::{leaf}(&ctx, &mut {at}) {{ refused.push(::model_core::Refused {{ at: \"{p}\", why }}); }}"
                );
                for j in (0..opens.len()).rev() {
                    let _ = writeln!(body, "{}}}", "        ".repeat(j + 1));
                }
                let _ = writeln!(body, "    }}");
            }
            let _ = writeln!(
                out,
                "#[rustfmt::skip]\npub fn resolve_colors(style: &mut {patch_ty}, with: &{patch_ty}) -> Result<(), Vec<::model_core::Refused>> {{\n    let ctx = {hook}::ctx(with);\n    let mut refused = Vec::new();\n{body}    if refused.is_empty() {{\n        Ok(())\n    }} else {{\n        Err(refused)\n    }}\n}}\n"
            );
        }
    }
    let registry = if plan.registry_fields.is_empty() {
        String::new()
    } else {
        let fills: String = plan
            .registry_fields
            .iter()
            .map(|f| {
                let f = ident(f);
                format!("            entry.{f}.fill_from(&parent.{f});\n")
            })
            .collect();
        format!(
            "    let names: Vec<String> = style.fonts.keys().cloned().collect();\n    for _ in 0..=names.len() {{\n        for name in &names {{\n            let Some(::model_core::Tri::Value(from)) = style.fonts.get(name).map(|e| e.extends.clone()) else {{ continue }};\n            let Some(parent) = style.fonts.get(&from).cloned() else {{\n                let known = names.join(\", \");\n                refused.push(::model_core::Refused {{ at: \"fonts\", why: format!(\"Unknown font '{{from}}' at style.fonts.{{name}}. Known fonts: {{known}}.\") }});\n                if let Some(entry) = style.fonts.get_mut(name) {{ entry.extends = ::model_core::Tri::Unset; }}\n                continue;\n            }};\n            if matches!(parent.extends, ::model_core::Tri::Value(_)) {{ continue }}\n            if let Some(entry) = style.fonts.get_mut(name) {{\n{fills}                entry.extends = ::model_core::Tri::Unset;\n            }}\n        }}\n    }}\n    if style.fonts.values().any(|e| matches!(e.extends, ::model_core::Tri::Value(_))) {{\n        refused.push(::model_core::Refused {{ at: \"fonts\", why: \"Fonts extend in a cycle at style.fonts.\".to_string() }});\n    }}\n"
        )
    };
    let opaque: Vec<String> = plan.opaque_fonts.iter().map(|p| format!("{p:?}")).collect();
    let _ = writeln!(
        out,
        "pub static OPAQUE_FONTS: [&str; {}] = [{}];\n",
        opaque.len(),
        opaque.join(", ")
    );
    if expands.is_empty() && registry.is_empty() {
        let _ = writeln!(
            out,
            "pub fn expand(_: &mut {patch_ty}) -> Result<(), Vec<::model_core::Refused>> {{\n    Ok(())\n}}\n"
        );
    } else {
        let _ = writeln!(
        out,
        "#[rustfmt::skip]\npub fn expand(style: &mut {patch_ty}) -> Result<(), Vec<::model_core::Refused>> {{\n    let mut refused = Vec::new();\n{registry}{expands}    if refused.is_empty() {{\n        Ok(())\n    }} else {{\n        Err(refused)\n    }}\n}}\n"
    );
    }
    let mut fills = String::new();
    for (i, (path, n)) in nodes.iter().enumerate() {
        let Some(v) = n.fill else { continue };
        let mut opens = Vec::new();
        let target = access("style", &n.access, n.container, true, "t", &mut opens);
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\nfn fill_{i}(style: &mut {patch_ty}, filled: &mut dyn FnMut(&str), refused: &mut Vec<::model_core::Refused>) {{"
        );
        for (j, o) in opens.iter().enumerate() {
            let _ = writeln!(out, "{}{o}", "    ".repeat(j + 1));
        }
        let pad = "    ".repeat(opens.len() + 1);
        let _ = writeln!(
            out,
            "{pad}match ::model_core::FillDefault::fill_default(&mut {target}, ::model_core::Value::{v:?}) {{ Some(true) => filled(\"{p}\"), Some(false) => {{}}, None => refused.push(::model_core::Refused {{ at: \"{p}\", why: \"the fill literal is refused by its type\".to_string() }}) }}",
            p = below(root.name, path)
        );
        for j in (0..opens.len()).rev() {
            let _ = writeln!(out, "{}}}", "    ".repeat(j + 1));
        }
        let _ = writeln!(out, "}}\n");
        let _ = writeln!(fills, "    fill_{i}(style, filled, &mut refused);");
    }
    for (j, site) in plan.map_fills.iter().enumerate() {
        let mut opens = Vec::new();
        let target = access("style", &site.access, false, true, "t", &mut opens);
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\nfn fill_map_{j}(style: &mut {patch_ty}, filled: &mut dyn FnMut(&str), refused: &mut Vec<::model_core::Refused>) {{"
        );
        for (k, o) in opens.iter().enumerate() {
            let _ = writeln!(out, "{}{o}", "    ".repeat(k + 1));
        }
        let pad = "    ".repeat(opens.len() + 1);
        let _ = writeln!(
            out,
            "{pad}match ::model_core::FillDefault::fill_default(&mut {target}, ::model_core::Value::{v:?}) {{ Some(true) => filled(\"{p}\"), Some(false) => {{}}, None => refused.push(::model_core::Refused {{ at: \"{p}\", why: \"the fill literal is refused by its type\".to_string() }}) }}",
            v = site.value,
            p = below(root.name, &site.path)
        );
        for k in (0..opens.len()).rev() {
            let _ = writeln!(out, "{}}}", "    ".repeat(k + 1));
        }
        let _ = writeln!(out, "}}\n");
        let _ = writeln!(fills, "    fill_map_{j}(style, filled, &mut refused);");
    }
    if fills.is_empty() {
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\npub fn fill(_: &mut {patch_ty}, _: &mut dyn FnMut(&str)) -> Result<(), Vec<::model_core::Refused>> {{\n    Ok(())\n}}\n"
        );
    } else {
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\npub fn fill(style: &mut {patch_ty}, filled: &mut dyn FnMut(&str)) -> Result<(), Vec<::model_core::Refused>> {{\n    let mut refused = Vec::new();\n{fills}    if refused.is_empty() {{\n        Ok(())\n    }} else {{\n        Err(refused)\n    }}\n}}\n"
        );
    }
    let _ = writeln!(
        out,
        "pub fn inherit(style: &mut {patch_ty}) -> Result<(), Vec<::model_core::Refused>> {{\n    let mut refused = Vec::new();\n{calls}    if refused.is_empty() {{\n        Ok(())\n    }} else {{\n        Err(refused)\n    }}\n}}\n"
    );
    tables(&mut out, root, &plan, &links, patch_ty, total_ty);
    Ok(out)
}

/// The authored path below the root: `Style.charts.font` is `charts.font`.
fn below(root: &str, path: &str) -> String {
    path.strip_prefix(root)
        .and_then(|p| p.strip_prefix('.'))
        .unwrap_or(path)
        .to_string()
}

/// `i` or `f` assigned from the other side along `steps`; `from` is `None` once an absent
/// container on the folded side is missing, and the leaf is then reset to unset.
fn copy_body(steps: &[Step], to: &str, from: Option<&str>, depth: usize, out: &mut String) {
    let Some((step, rest)) = steps.split_first() else {
        return;
    };
    let field = ident(&step.field);
    let target = format!("{to}.{field}");
    let source = from.map(|f| format!("{f}.{field}"));
    let pad = "    ".repeat(depth + 1);
    if rest.is_empty() {
        let value = match &source {
            Some(s) => format!("{s}.clone()"),
            None => "Default::default()".to_string(),
        };
        let _ = writeln!(out, "{pad}{target} = {value};");
    } else if step.tri {
        let _ = writeln!(
            out,
            "{pad}if let ::model_core::Tri::Value(ti{depth}) = &mut {target} {{"
        );
        let inner = format!("ti{depth}");
        match &source {
            Some(s) => {
                let _ = writeln!(
                    out,
                    "{pad}    if let ::model_core::Tri::Value(sf{depth}) = &{s} {{"
                );
                copy_body(rest, &inner, Some(&format!("sf{depth}")), depth + 2, out);
                let _ = writeln!(out, "{pad}    }} else {{");
                copy_body(rest, &inner, None, depth + 2, out);
                let _ = writeln!(out, "{pad}    }}");
            }
            None => copy_body(rest, &inner, None, depth + 1, out),
        }
        let _ = writeln!(out, "{pad}}}");
    } else {
        copy_body(rest, &target, source.as_deref(), depth, out);
    }
}

fn unset_expr(steps: &[Step], from: &str, depth: usize) -> String {
    let Some((step, rest)) = steps.split_first() else {
        return "true".to_string();
    };
    let source = format!("{from}.{}", ident(&step.field));
    if rest.is_empty() {
        format!("::model_core::Empty::is_empty(&{source})")
    } else if step.tri {
        format!(
            "match &{source} {{ ::model_core::Tri::Value(s{depth}) => {}, _ => true }}",
            unset_expr(rest, &format!("s{depth}"), depth + 1)
        )
    } else {
        unset_expr(rest, &source, depth)
    }
}

/// The cascade tables: node ids by sorted authored path, the link table, and the per-node
/// functions the incremental pass calls. Every function is small and takes one node.
fn tables(
    out: &mut String,
    root: &ModelInfo,
    plan: &Plan,
    links: &[(String, String, usize)],
    patch_ty: &str,
    total_ty: &str,
) {
    let mut ids: Vec<(String, &Node)> = plan
        .nodes
        .iter()
        .map(|(p, n)| (below(root.name, p), n))
        .collect();
    ids.sort_by(|a, b| a.0.cmp(&b.0));
    let id_of = |logical: &str| -> usize {
        let key = below(root.name, logical);
        ids.binary_search_by(|(p, _)| p.as_str().cmp(&key))
            .unwrap_or(0)
    };
    let parent_of = |path: &str| -> String {
        match path.rsplit_once('.') {
            Some((p, _)) => p.to_string(),
            None => String::new(),
        }
    };
    let mut paths = String::new();
    let mut parents = String::new();
    let mut owners = String::new();
    let mut copies = String::new();
    let mut unsets = String::new();
    let mut rebuilds = String::new();
    for (id, (path, node)) in ids.iter().enumerate() {
        let _ = writeln!(paths, "    \"{path}\",");
        let parent = parent_of(path);
        let pid = if parent.is_empty() {
            "::model_core::cascade::NONE".to_string()
        } else {
            ids.binary_search_by(|(p, _)| p.as_str().cmp(&parent))
                .map_or("::model_core::cascade::NONE".to_string(), |i| i.to_string())
        };
        let _ = writeln!(parents, "    {pid},");
        // The value that is rebuilt: the outermost absent container this node sits in, else the node.
        let outer = node
            .access
            .iter()
            .find(|s| s.tri)
            .map(|s| s.logical.clone());
        let owner = match (&outer, node.is_struct) {
            (Some(l), _) => Some(id_of(l)),
            (None, true) => None,
            (None, false) => Some(id),
        };
        let _ = writeln!(
            owners,
            "    {},",
            owner.map_or("::model_core::cascade::NONE".to_string(), |o| o.to_string())
        );
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\nfn copy_{id}(f: &{patch_ty}, i: &mut {patch_ty}) {{"
        );
        copy_body(&node.access, "i", Some("f"), 0, out);
        let _ = writeln!(out, "}}\n");
        let _ = writeln!(
            out,
            "#[rustfmt::skip]\nfn unset_{id}(f: &{patch_ty}) -> bool {{\n    {}\n}}\n",
            unset_expr(&node.access, "f", 0)
        );
        let _ = writeln!(copies, "        {id} => copy_{id}(f, i),");
        let _ = writeln!(unsets, "        {id} => unset_{id}(f),");
        if owner == Some(id) {
            let fields: Vec<String> = node.access.iter().map(|s| ident(&s.field)).collect();
            let slot = format!("i.{}", fields.join("."));
            let target = format!("total.{}", fields.join("."));
            let call = match node.kind {
                Kind::Plain => "rebuild_field",
                Kind::Nullable => "rebuild_nullable",
                Kind::Slot => "rebuild_slot",
                Kind::OptionalSlot => "rebuild_optional_slot",
                Kind::Omitted => "rebuild_omitted",
                Kind::Defaulted => "rebuild_defaulted",
            };
            let _ = writeln!(
                out,
                "#[rustfmt::skip]\nfn rebuild_{id}(total: &mut {total_ty}, i: &{patch_ty}, unset: &mut Vec<String>) {{\n    ::model_core::cascade::{call}(&{slot}, \"{path}\", &mut {target}, unset);\n}}\n"
            );
            let _ = writeln!(rebuilds, "        {id} => rebuild_{id}(total, i, unset),");
        }
    }
    let n = ids.len();
    let _ = writeln!(out, "static PATHS: [&str; {n}] = [\n{paths}];\n");
    let _ = writeln!(out, "static PARENT: [u16; {n}] = [\n{parents}];\n");
    let _ = writeln!(out, "static OWNER: [u16; {n}] = [\n{owners}];\n");
    let mut rows = String::new();
    for (from, to, k) in links {
        let _ = writeln!(rows, "    ({}, {}, link_{k}),", id_of(from), id_of(to));
    }
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\ntype Link = (u16, u16, fn(&mut {patch_ty}, &mut Vec<::model_core::Refused>));\n\nstatic LINKS: [Link; {}] = [\n{rows}];\n",
        links.len()
    );
    let mut by_from: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut by_to: Vec<Vec<usize>> = vec![Vec::new(); n];
    let mut children: Vec<Vec<usize>> = vec![Vec::new(); n];
    for (k, (from, to, _)) in links.iter().enumerate() {
        by_from
            .get_mut(id_of(from))
            .into_iter()
            .for_each(|v| v.push(k));
        by_to.get_mut(id_of(to)).into_iter().for_each(|v| v.push(k));
    }
    for (id, (path, _)) in ids.iter().enumerate() {
        if let Ok(p) = ids.binary_search_by(|(q, _)| q.as_str().cmp(&parent_of(path))) {
            children.get_mut(p).into_iter().for_each(|v| v.push(id));
        }
    }
    csr(out, "BY_FROM", &by_from);
    csr(out, "BY_TO", &by_to);
    csr(out, "CHILDREN", &children);
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\nfn copy(id: u16, f: &{patch_ty}, i: &mut {patch_ty}) {{\n    match id {{\n{copies}        _ => {{}}\n    }}\n}}\n"
    );
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\nfn unset(id: u16, f: &{patch_ty}) -> bool {{\n    match id {{\n{unsets}        _ => true,\n    }}\n}}\n"
    );
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\nfn rebuild(id: u16, total: &mut {total_ty}, i: &{patch_ty}, unset: &mut Vec<String>) {{\n    match id {{\n{rebuilds}        _ => {{}}\n    }}\n}}\n"
    );
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\npub static TABLES: ::model_core::Tables<{total_ty}> = ::model_core::Tables {{\n    paths: &PATHS,\n    parent: &PARENT,\n    by_from: ::model_core::cascade::Csr {{ start: &BY_FROM_START, items: &BY_FROM_ITEMS }},\n    by_to: ::model_core::cascade::Csr {{ start: &BY_TO_START, items: &BY_TO_ITEMS }},\n    children: ::model_core::cascade::Csr {{ start: &CHILDREN_START, items: &CHILDREN_ITEMS }},\n    owner: &OWNER,\n    links: &LINKS,\n    copy,\n    unset,\n    rebuild,\n    inherit,\n    fill,\n    expand,\n    opaque_fonts: &OPAQUE_FONTS,\n    resolve_colors,\n}};"
    );
}

/// A list of ids per node as two flat arrays (`<name>_START`, `<name>_ITEMS`).
fn csr(out: &mut String, name: &str, lists: &[Vec<usize>]) {
    let mut start = 0;
    let mut starts = vec![0usize];
    let mut items = Vec::new();
    for l in lists {
        items.extend(l.iter().map(ToString::to_string));
        start += l.len();
        starts.push(start);
    }
    let _ = writeln!(
        out,
        "#[rustfmt::skip]\nstatic {name}_START: [u32; {}] = [{}];\n#[rustfmt::skip]\nstatic {name}_ITEMS: [u16; {}] = [{}];\n",
        starts.len(),
        starts.iter().map(ToString::to_string).collect::<Vec<_>>().join(", "),
        items.len(),
        items.join(", ")
    );
}

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
}

#[derive(Debug, Clone)]
struct Node {
    access: Vec<Step>,
    /// An absent nested model: a `Tri` holding a patch.
    container: bool,
    kind: Kind,
    /// A model that is not absent: its leaves are the nodes that change.
    is_struct: bool,
}

struct Ctx<'a> {
    /// The slot being mirrored: where it sits and the path it mirrors.
    slot: Option<(&'a [String], &'a [String])>,
    links: &'static [(&'static str, &'static str)],
    skip: bool,
}

#[derive(Default)]
struct Plan {
    nodes: IndexMap<String, Node>,
    graph: IndexMap<String, String>,
    /// Targets of the graph that a slot mirrors (not named by `inherit` or `links`).
    mirrored: IndexSet<String>,
    /// Slot sources that must exist as a model path, with the link that names them.
    slot_sources: Vec<(String, String)>,
    errors: Vec<InheritError>,
}

fn model_of(f: &Field) -> Option<(&'static ModelInfo, bool)> {
    let (ty, tri) = match f.ty {
        TypeExpr::Option(inner) if f.attrs.absent.is_some() => (*inner, true),
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
            logical: here.clone(),
        });
        let kind = match f.ty {
            TypeExpr::Patch(_) => Kind::Slot,
            TypeExpr::Option(TypeExpr::Patch(_)) if f.attrs.absent.is_some() => Kind::OptionalSlot,
            TypeExpr::Option(_) if f.attrs.absent.is_some() => Kind::Nullable,
            _ => Kind::Plain,
        };
        plan.nodes.insert(
            here.clone(),
            Node {
                access: access.clone(),
                container: nested.is_some_and(|n| n.1),
                kind,
                is_struct: nested.is_some_and(|n| !n.1),
            },
        );
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
        if s.tri && !(i == last && own_container) {
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
        if t.container != s.container {
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
        "#[rustfmt::skip]\npub static TABLES: ::model_core::Tables<{total_ty}> = ::model_core::Tables {{\n    paths: &PATHS,\n    parent: &PARENT,\n    by_from: ::model_core::cascade::Csr {{ start: &BY_FROM_START, items: &BY_FROM_ITEMS }},\n    by_to: ::model_core::cascade::Csr {{ start: &BY_TO_START, items: &BY_TO_ITEMS }},\n    children: ::model_core::cascade::Csr {{ start: &CHILDREN_START, items: &CHILDREN_ITEMS }},\n    owner: &OWNER,\n    links: &LINKS,\n    copy,\n    unset,\n    rebuild,\n    inherit,\n}};"
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

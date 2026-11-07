//! The schema IR: models of fields, each a type expression, built from the
//! field tables ([`ModelInfo`]) a `#[derive(Model)]` type carries (spec § 13).
//!
//! The IR mirrors the spine's type expressions ([`Ty`]) and keeps each field's
//! attributes, the model's constraints and doc text. [`View::Authored`] and
//! [`View::Patch`] are computed from it as the twins are: an authored document
//! requires what the spine cannot fill, a patch layer requires nothing.
//! [`Schema::json_schema`] renders JSON Schema; [`Schema::at`] answers which
//! keys exist at a path and of what type.

mod at;
mod build;
mod json;
pub mod theme;

pub use at::{At, AtError};
pub use build::SchemaError;

use crate::{Attrs, Constraint, Keywords, Merge, Scalar};
use indexmap::IndexMap;
use std::collections::BTreeSet;

/// A type expression with models resolved by name.
#[derive(Debug, Clone, PartialEq)]
pub enum Ty {
    Scalar(Scalar),
    Facet(&'static str, Scalar),
    /// A struct or tagged enum, by name (a key of [`Schema::models`]).
    Ref(String),
    List(Box<Ty>),
    Map(Box<Ty>),
    /// `Option<T>` with the meaning of absent.
    Option(Box<Ty>, &'static str),
    /// A unit-only enum: its spellings.
    Enum(Vec<&'static str>),
    /// `Engine<T, R>`: the value type and the rule spellings.
    Engine(Box<Ty>, Vec<&'static str>),
    /// An enum field whose variants are sibling keys.
    Keys(Vec<SchemaField>),
    /// The type, and the type its `accept` also takes.
    Accept(Box<Ty>, Box<Ty>),
    Patch(Box<Ty>),
    /// A fixed-length array of positional types.
    Tuple(Vec<Ty>),
    /// A facet or unit with the keywords its declaration carries.
    Keyed(&'static Keywords, Box<Ty>),
    Unit,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SchemaField {
    /// The authored key.
    pub key: &'static str,
    pub doc: &'static str,
    pub ty: Ty,
    pub merge: Merge,
    pub attrs: &'static Attrs,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Body {
    Struct {
        fields: Vec<SchemaField>,
        constraints: &'static [Constraint],
        /// The key a bare string stands for.
        shorthand: Option<&'static str>,
        /// The pattern of the bare string the model also reads from.
        string_pattern: Option<&'static str>,
        /// Required of an authored layer even under a slot.
        complete: bool,
    },
    /// Each variant selected by the value of `tag`.
    Tagged {
        tag: &'static str,
        /// The tag as a field: an enum of the variant spellings.
        tag_field: SchemaField,
        variants: Vec<(&'static str, Ty)>,
    },
}

#[derive(Debug, Clone, PartialEq)]
pub struct SchemaModel {
    pub name: &'static str,
    pub doc: &'static str,
    pub body: Body,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Schema {
    pub root: String,
    root_ty: Ty,
    /// Structs and tagged enums, root first, then in discovery order.
    pub models: IndexMap<String, SchemaModel>,
}

/// The shape of the document the schema describes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    /// Parse input: required where the spine cannot fill the field.
    Authored,
    /// A layer (`meta.yml`, theme, `extends`): nothing is required.
    Patch,
}

/// A key valid in a model: a field, a variant of a `keys` field, or a field
/// of a flattened mixin.
#[derive(Debug, Clone, Copy)]
pub struct Prop<'a> {
    pub key: &'static str,
    pub doc: &'static str,
    pub ty: &'a Ty,
    pub field: &'a SchemaField,
    /// A variant of a `keys` field.
    pub keyed: bool,
}

/// The variants of one `keys` field: at most one is set.
#[derive(Debug, Clone)]
pub struct KeyGroup {
    pub keys: Vec<&'static str>,
    pub optional: bool,
}

impl Ty {
    /// The type under `Option` / `Patch` wrappers.
    pub(crate) fn bare(&self) -> &Ty {
        match self {
            Ty::Option(t, _) | Ty::Patch(t) | Ty::Keyed(_, t) => t.bare(),
            t => t,
        }
    }
}

/// A struct with its flattened mixins inlined.
#[derive(Debug, Default)]
pub struct Flat<'a> {
    pub props: Vec<Prop<'a>>,
    pub groups: Vec<KeyGroup>,
    pub constraints: Vec<&'static Constraint>,
}

impl Ty {
    fn refs<'a>(&'a self, out: &mut Vec<&'a str>) {
        match self {
            Ty::Ref(n) => out.push(n),
            Ty::List(t)
            | Ty::Map(t)
            | Ty::Option(t, _)
            | Ty::Patch(t)
            | Ty::Keyed(_, t)
            | Ty::Engine(t, _) => t.refs(out),
            Ty::Accept(a, b) => {
                a.refs(out);
                b.refs(out);
            }
            Ty::Tuple(v) => v.iter().for_each(|t| t.refs(out)),
            Ty::Keys(v) => v.iter().for_each(|f| f.ty.refs(out)),
            _ => {}
        }
    }
}

impl Schema {
    pub fn model(&self, name: &str) -> Option<&SchemaModel> {
        self.models.get(name)
    }

    /// Every key valid in a struct: flattened mixins inlined (with their
    /// constraints) and each `keys` field expanded to its variants.
    pub fn flat<'a>(&'a self, model: &'a SchemaModel) -> Flat<'a> {
        let mut flat = Flat::default();
        self.collect(model, &mut flat);
        flat
    }

    /// Models used as a field type, tagged variant or map/list element: the
    /// ones that get a `$defs` entry. A mixin only ever flattened has none.
    pub fn referenced(&self) -> Vec<&str> {
        let mut out = vec![self.root.as_str()];
        for m in self.models.values() {
            match &m.body {
                Body::Struct { fields, .. } => fields
                    .iter()
                    .filter(|f| !f.attrs.flatten)
                    .for_each(|f| f.ty.refs(&mut out)),
                Body::Tagged { variants, .. } => {
                    variants.iter().for_each(|(_, t)| t.refs(&mut out))
                }
            }
        }
        out
    }

    /// Every use of a model as a field type: `(owner, model, cascade)`, `cascade` when the field
    /// is an `inherit_slot` or a `Patch` (also under `Option`), whose leaves another layer fills.
    pub fn mounts(&self) -> BTreeSet<(String, String, bool)> {
        let mut out = BTreeSet::new();
        for m in self.models.values() {
            let Body::Struct { fields, .. } = &m.body else {
                continue;
            };
            for f in fields {
                let cascade = f.attrs.inherit_slot.is_some()
                    || matches!(&f.ty, Ty::Patch(_))
                    || matches!(&f.ty, Ty::Option(t, _) if matches!(**t, Ty::Patch(_)));
                let mut refs = Vec::new();
                f.ty.refs(&mut refs);
                out.extend(
                    refs.into_iter()
                        .map(|n| (m.name.to_string(), n.to_string(), cascade)),
                );
            }
        }
        out
    }

    /// The models every use of which is cascade-filled: under an `inherit_slot` or `Patch`
    /// field, or inside a model that is itself so used. A slot's parent fills their leaves, so
    /// an authored layer never has to say them; a model with a use of its own is not one.
    pub fn mirrored(&self) -> BTreeSet<String> {
        mirrored_in(&self.mounts())
    }

    fn collect<'a>(&'a self, model: &'a SchemaModel, flat: &mut Flat<'a>) {
        let Body::Struct {
            fields,
            constraints,
            ..
        } = &model.body
        else {
            return;
        };
        flat.constraints.extend(constraints.iter());
        for f in fields {
            match (f.attrs.flatten, f.ty.bare()) {
                (true, Ty::Ref(n)) => {
                    if let Some(m) = self.models.get(n) {
                        self.collect(m, flat);
                    }
                }
                (_, Ty::Keys(variants)) => {
                    flat.groups.push(KeyGroup {
                        keys: variants.iter().map(|v| v.key).collect(),
                        optional: matches!(f.ty, Ty::Option(..)),
                    });
                    flat.props.extend(variants.iter().map(|v| Prop {
                        key: v.key,
                        doc: v.doc,
                        ty: &v.ty,
                        field: f,
                        keyed: true,
                    }));
                }
                _ => flat.props.push(Prop {
                    key: f.key,
                    doc: f.doc,
                    ty: &f.ty,
                    field: f,
                    keyed: false,
                }),
            }
        }
    }
}

/// [`Schema::mirrored`] over the mounts of several roots at once.
pub fn mirrored_in(mounts: &BTreeSet<(String, String, bool)>) -> BTreeSet<String> {
    let mut out: BTreeSet<String> = BTreeSet::new();
    loop {
        let before = out.len();
        let models: BTreeSet<&String> = mounts.iter().map(|(_, m, _)| m).collect();
        for m in models {
            let filled = mounts
                .iter()
                .filter(|(_, child, _)| child == m)
                .all(|(owner, _, cascade)| *cascade || out.contains(owner));
            if filled {
                out.insert(m.clone());
            }
        }
        if out.len() == before {
            return out;
        }
    }
}

use super::at::Step;
use super::{Body, Schema, SchemaField, SchemaModel, Ty};
use crate::{Attrs, Condition, Field, Merge, Model, ModelInfo, ModelKind, TypeExpr};
use indexmap::IndexMap;

/// Why a field table does not make a schema.
#[derive(Debug, Clone, PartialEq)]
pub enum SchemaError {
    /// Two different models share a name.
    NameClash(&'static str),
    /// An enum with data variants used where no `keys` field or `tag` reads it.
    UntaggedVariants {
        model: &'static str,
        field: &'static str,
    },
    /// `keys` on a field that is not a data enum.
    KeysNotVariants {
        model: &'static str,
        field: &'static str,
    },
    /// The rule type of an `Engine` is not a unit-only enum.
    RulesNotEnum {
        model: &'static str,
        field: &'static str,
    },
    /// A constraint names a key the model does not carry.
    UnknownConstraintKey { model: &'static str, key: String },
    /// A tagged enum variant does not hold a struct or tagged model.
    TaggedVariant {
        model: &'static str,
        variant: &'static str,
    },
    /// `#[flatten]` on something other than a struct.
    BadFlatten {
        model: &'static str,
        field: &'static str,
    },
    /// The root is not a struct.
    RootNotStruct(&'static str),
}

impl Schema {
    /// The schema of `T` and every model it reaches.
    pub fn of<T: Model>() -> Result<Schema, SchemaError> {
        let root = T::info();
        if root.kind != ModelKind::Struct {
            return Err(SchemaError::RootNotStruct(root.name));
        }
        let mut infos: IndexMap<&'static str, &'static ModelInfo> = IndexMap::new();
        infos.insert(root.name, root);
        let mut models = IndexMap::new();
        let mut next = 0;
        while let Some((_, info)) = infos.get_index(next) {
            let info: &'static ModelInfo = info;
            next += 1;
            let model = lower_model(info, &mut infos)?;
            models.insert(info.name.to_string(), model);
        }
        let schema = Schema {
            root: root.name.to_string(),
            root_ty: Ty::Ref(root.name.to_string()),
            models,
        };
        schema.check_flatten()?;
        schema.check_constraints()?;
        Ok(schema)
    }

    fn check_flatten(&self) -> Result<(), SchemaError> {
        for m in self.models.values() {
            let Body::Struct { fields, .. } = &m.body else {
                continue;
            };
            for f in fields.iter().filter(|f| f.attrs.flatten) {
                let ok = matches!(&f.ty, Ty::Ref(n)
                    if self.models.get(n).is_some_and(|t| matches!(t.body, Body::Struct { .. })));
                if !ok {
                    return Err(SchemaError::BadFlatten {
                        model: m.name,
                        field: f.key,
                    });
                }
            }
        }
        Ok(())
    }

    fn check_constraints(&self) -> Result<(), SchemaError> {
        for m in self.models.values() {
            let Body::Struct { constraints, .. } = &m.body else {
                continue;
            };
            for c in *constraints {
                let keys = c
                    .fields
                    .iter()
                    .copied()
                    .chain(c.when.iter().map(|w| match w {
                        Condition::IsSet(k) | Condition::IsList(k) => *k,
                        Condition::ValueIn { key, .. } | Condition::IsCall { key, .. } => key,
                    }));
                for k in keys {
                    if !self.key_exists(m, k) {
                        return Err(SchemaError::UnknownConstraintKey {
                            model: m.name,
                            key: k.to_string(),
                        });
                    }
                }
            }
        }
        Ok(())
    }

    /// Whether a (dotted) authored key resolves from `model`, by the same
    /// steps `at` takes.
    fn key_exists(&self, model: &SchemaModel, key: &str) -> bool {
        let start = Ty::Ref(model.name.to_string());
        let mut cur = &start;
        for seg in key.split('.') {
            // `spanners[*]` is the list, then any one of its items.
            let (name, items) = match seg.strip_suffix("[*]") {
                Some(name) => (name, true),
                None => (seg, false),
            };
            match self.step(cur, name) {
                Step::Found(t) => cur = t,
                _ => return false,
            }
            if items {
                match self.step(cur, "0") {
                    Step::Found(t) => cur = t,
                    _ => return false,
                }
            }
        }
        true
    }
}

static NO_ATTRS: Attrs = Attrs {
    absent: None,
    inherit: None,
    inherit_slot: None,
    links: &[],
    local: false,
    nested_drop: false,
    nullable: false,
    fill: None,
    fill_value: None,
    accept: None,
    directive: false,
    keys: false,
    flatten: false,
    role: None,
};

fn lower_model(
    info: &'static ModelInfo,
    infos: &mut IndexMap<&'static str, &'static ModelInfo>,
) -> Result<SchemaModel, SchemaError> {
    let body = match info.kind {
        ModelKind::Struct => Body::Struct {
            fields: info
                .fields
                .iter()
                .map(|f| lower_field(info, f, infos))
                .collect::<Result<_, _>>()?,
            constraints: info.constraints,
            shorthand: info.shorthand,
            string_pattern: info.string_pattern,
            complete: info.complete,
        },
        ModelKind::Tagged(tag) => {
            let mut variants = Vec::new();
            for v in info.fields {
                let ty = lower(info, v, v.ty, infos)?;
                if !matches!(ty, Ty::Ref(_)) {
                    return Err(SchemaError::TaggedVariant {
                        model: info.name,
                        variant: v.name,
                    });
                }
                variants.push((v.name, ty));
            }
            let mut spellings: Vec<&'static str> = variants.iter().map(|(t, _)| *t).collect();
            spellings.sort_unstable();
            let tag_field = SchemaField {
                key: tag,
                doc: "",
                ty: Ty::Enum(spellings),
                merge: Merge::Override,
                attrs: &NO_ATTRS,
            };
            Body::Tagged {
                tag,
                tag_field,
                variants,
            }
        }
        // Only structs and tagged enums enter the work list; enums inline where used.
        ModelKind::Enum | ModelKind::Variants => return Err(SchemaError::RootNotStruct(info.name)),
    };
    Ok(SchemaModel {
        name: info.name,
        doc: info.doc,
        body,
    })
}

fn lower_field(
    owner: &'static ModelInfo,
    f: &'static Field,
    infos: &mut IndexMap<&'static str, &'static ModelInfo>,
) -> Result<SchemaField, SchemaError> {
    let mut ty = lower(owner, f, f.ty, infos)?;
    if let Some(also) = f.attrs.accept {
        let also = lower(owner, f, also, infos)?;
        ty = match ty {
            // Keep Option outermost: the absent state is not an alternative spelling.
            Ty::Option(inner, a) => Ty::Option(Box::new(Ty::Accept(inner, Box::new(also))), a),
            t => Ty::Accept(Box::new(t), Box::new(also)),
        };
    }
    Ok(field(f, ty))
}

fn field(f: &'static Field, ty: Ty) -> SchemaField {
    SchemaField {
        key: f.name,
        doc: f.doc,
        ty,
        merge: f.merge,
        attrs: &f.attrs,
    }
}

fn lower(
    owner: &'static ModelInfo,
    f: &'static Field,
    e: &'static TypeExpr,
    infos: &mut IndexMap<&'static str, &'static ModelInfo>,
) -> Result<Ty, SchemaError> {
    Ok(match e {
        TypeExpr::Scalar(s) => Ty::Scalar(*s),
        TypeExpr::Facet(n, s) => Ty::Facet(n, *s),
        TypeExpr::Keyed(k, i) => Ty::Keyed(k, Box::new(lower(owner, f, i, infos)?)),
        TypeExpr::OneOf(names) => Ty::Enum(names.to_vec()),
        TypeExpr::Either(a, b) => Ty::Accept(
            Box::new(lower(owner, f, a, infos)?),
            Box::new(lower(owner, f, b, infos)?),
        ),
        TypeExpr::Tuple(items) => Ty::Tuple(
            items
                .iter()
                .map(|t| lower(owner, f, t, infos))
                .collect::<Result<_, _>>()?,
        ),
        TypeExpr::Unit => Ty::Unit,
        TypeExpr::List(i) => Ty::List(Box::new(lower(owner, f, i, infos)?)),
        TypeExpr::Map(i) => Ty::Map(Box::new(lower(owner, f, i, infos)?)),
        TypeExpr::Patch(i) => Ty::Patch(Box::new(lower(owner, f, i, infos)?)),
        TypeExpr::Option(i) => Ty::Option(
            Box::new(lower(owner, f, i, infos)?),
            f.attrs.absent.unwrap_or(""),
        ),
        TypeExpr::Engine(v, r) => {
            let rules = match lower(owner, f, r, infos)? {
                Ty::Enum(r) => r,
                _ => {
                    return Err(SchemaError::RulesNotEnum {
                        model: owner.name,
                        field: f.name,
                    })
                }
            };
            Ty::Engine(Box::new(lower(owner, f, v, infos)?), rules)
        }
        TypeExpr::Ref(name, info) => {
            let info = info();
            match info.kind {
                ModelKind::Enum => Ty::Enum(info.fields.iter().map(|v| v.name).collect()),
                ModelKind::Variants if f.attrs.keys => Ty::Keys(
                    info.fields
                        .iter()
                        .map(|v| lower_field(info, v, infos))
                        .collect::<Result<_, _>>()?,
                ),
                ModelKind::Variants => {
                    return Err(SchemaError::UntaggedVariants {
                        model: owner.name,
                        field: f.name,
                    })
                }
                ModelKind::Struct | ModelKind::Tagged(_) => {
                    if f.attrs.keys {
                        return Err(SchemaError::KeysNotVariants {
                            model: owner.name,
                            field: f.name,
                        });
                    }
                    match infos.get(name) {
                        Some(seen) if **seen != *info => {
                            return Err(SchemaError::NameClash(info.name))
                        }
                        Some(_) => {}
                        None => {
                            infos.insert(info.name, info);
                        }
                    }
                    Ty::Ref(name.to_string())
                }
            }
        }
    })
}

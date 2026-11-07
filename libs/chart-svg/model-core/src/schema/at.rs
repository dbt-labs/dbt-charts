use super::{Body, Prop, Schema, SchemaModel, Ty};

/// What lives at a path: its type, and the keys valid inside it.
#[derive(Debug, Clone)]
pub struct At<'a> {
    pub ty: &'a Ty,
    /// The keys of the model at the path; empty for a scalar, list or map.
    pub keys: Vec<Prop<'a>>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AtError {
    /// `key` (segment number `at`) is not valid there; `valid` are the keys that are.
    UnknownKey {
        at: usize,
        key: String,
        valid: Vec<&'static str>,
    },
    /// A segment tried to go inside a leaf.
    NotAContainer { at: usize, key: String },
}

pub(super) enum Step<'a> {
    Found(&'a Ty),
    Unknown(Vec<&'static str>),
    Leaf,
}

impl Schema {
    /// The type at an authored path and the keys valid there. A map accepts
    /// any key, a list an index. Through a tagged enum a key resolves in the
    /// first variant that has it, and the keys at a tagged enum are the union
    /// of its variants'.
    pub fn at<'a>(&'a self, path: &[&str]) -> Result<At<'a>, AtError> {
        let mut cur: &'a Ty = &self.root_ty;
        for (at, seg) in path.iter().enumerate() {
            cur = match self.step(cur, seg) {
                Step::Found(t) => t,
                Step::Unknown(valid) => {
                    return Err(AtError::UnknownKey {
                        at,
                        key: (*seg).to_string(),
                        valid,
                    })
                }
                Step::Leaf => {
                    return Err(AtError::NotAContainer {
                        at,
                        key: (*seg).to_string(),
                    })
                }
            };
        }
        Ok(At {
            ty: cur,
            keys: self.keys_of(cur),
        })
    }

    fn keys_of<'a>(&'a self, ty: &'a Ty) -> Vec<Prop<'a>> {
        match ty.bare() {
            Ty::Ref(n) => self
                .models
                .get(n)
                .map(|m| self.model_keys(m))
                .unwrap_or_default(),
            Ty::Accept(a, _) => self.keys_of(a),
            Ty::Engine(v, _) => self.keys_of(v),
            _ => Vec::new(),
        }
    }

    fn model_keys<'a>(&'a self, m: &'a SchemaModel) -> Vec<Prop<'a>> {
        match &m.body {
            Body::Struct { .. } => self.flat(m).props,
            Body::Tagged {
                tag_field,
                variants,
                ..
            } => {
                let mut out: Vec<Prop<'a>> = vec![Prop {
                    key: tag_field.key,
                    doc: tag_field.doc,
                    ty: &tag_field.ty,
                    field: tag_field,
                    keyed: false,
                }];
                for (_, vty) in variants {
                    for p in self.keys_of(vty) {
                        if !out.iter().any(|o| o.key == p.key) {
                            out.push(p);
                        }
                    }
                }
                out
            }
        }
    }

    pub(super) fn step<'a>(&'a self, ty: &'a Ty, seg: &str) -> Step<'a> {
        match ty {
            Ty::Option(i, _) | Ty::Patch(i) => self.step(i, seg),
            Ty::Engine(v, _) => self.step(v, seg),
            Ty::Accept(a, b) => match self.step(a, seg) {
                Step::Found(t) => Step::Found(t),
                _ => self.step(b, seg),
            },
            Ty::Map(i) => Step::Found(i),
            Ty::List(i) if seg.parse::<usize>().is_ok() => Step::Found(i),
            Ty::Ref(_) => {
                let keys = self.keys_of(ty);
                match keys.iter().find(|p| p.key == seg) {
                    Some(p) => Step::Found(p.ty),
                    None => Step::Unknown(keys.iter().map(|p| p.key).collect()),
                }
            }
            _ => Step::Leaf,
        }
    }
}

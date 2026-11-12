use super::{Body, Flat, Schema, Ty};
use yaml::{Kind, Node};

/// Every struct-valued node of an authored document, with the struct's flat field table.
pub struct Visit<'a, 'n> {
    /// The authored path of the node: keys and list indexes, dot-joined.
    pub path: String,
    pub model: &'a str,
    pub flat: Flat<'a>,
    pub node: &'n Node,
}

impl Schema {
    /// Walk `node`, a document of the root model, by the schema's types and visit each struct it
    /// holds, outermost first. A node the schema does not type that way (a scalar where a struct
    /// is, a key no field names) is left to the parse that refuses it.
    pub fn structs<'a, 'n>(&'a self, node: &'n Node) -> Vec<Visit<'a, 'n>> {
        let mut out = Vec::new();
        self.descend(&self.root_ty, node, "", &mut out);
        out
    }

    fn descend<'a, 'n>(
        &'a self,
        ty: &'a Ty,
        node: &'n Node,
        at: &str,
        out: &mut Vec<Visit<'a, 'n>>,
    ) {
        match (ty, &node.kind) {
            (Ty::Option(t, _) | Ty::Patch(t) | Ty::Keyed(_, t) | Ty::Engine(t, _), _) => {
                self.descend(t, node, at, out);
            }
            (Ty::Accept(a, b), _) => {
                let first = if self.fits(a, node) { a } else { b };
                self.descend(first, node, at, out);
            }
            (Ty::List(item), Kind::Seq(items)) => {
                for (i, n) in items.iter().enumerate() {
                    self.descend(item, n, &join(at, &i.to_string()), out);
                }
            }
            (Ty::Map(item), Kind::Map(entries)) => {
                for (k, e) in entries {
                    self.descend(item, &e.value, &join(at, k), out);
                }
            }
            (Ty::Ref(name), Kind::Map(entries)) => {
                let Some(model) = self.models.get(name) else {
                    return;
                };
                match &model.body {
                    Body::Struct { .. } => {
                        let flat = self.flat(model);
                        let props: Vec<_> = flat
                            .props
                            .iter()
                            .filter_map(|p| entries.get(p.key).map(|e| (p.ty, p.key, &e.value)))
                            .collect();
                        out.push(Visit {
                            path: at.to_string(),
                            model: model.name,
                            flat,
                            node,
                        });
                        for (ty, key, child) in props {
                            self.descend(ty, child, &join(at, key), out);
                        }
                    }
                    Body::Tagged { tag, variants, .. } => {
                        let Some(Kind::Scalar(yaml::Value::Str(name))) =
                            entries.get(*tag).map(|e| &e.value.kind)
                        else {
                            return;
                        };
                        if let Some((_, variant)) = variants.iter().find(|(n, _)| n == name) {
                            self.descend(variant, node, at, out);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl Schema {
    /// A struct's node as authored JSON, with each bare string a model reads as its shorthand
    /// written out (`font: heading` is `{extends: heading}`), so a rule's dotted key reaches it.
    pub fn authored_json(&self, model: &str, node: &Node) -> serde_json::Value {
        self.expand(&Ty::Ref(model.to_string()), node)
    }

    fn expand(&self, ty: &Ty, node: &Node) -> serde_json::Value {
        match (ty.bare(), &node.kind) {
            (Ty::Accept(a, b), _) => self.expand(if self.fits(a, node) { a } else { b }, node),
            (Ty::Engine(t, _), _) => self.expand(t, node),
            (Ty::List(item), Kind::Seq(items)) => {
                items.iter().map(|n| self.expand(item, n)).collect()
            }
            (Ty::Map(item), Kind::Map(entries)) => entries
                .iter()
                .map(|(k, e)| (k.clone(), self.expand(item, &e.value)))
                .collect::<serde_json::Map<_, _>>()
                .into(),
            (Ty::Ref(name), Kind::Scalar(yaml::Value::Str(text))) => {
                match self.models.get(name).map(|m| &m.body) {
                    Some(Body::Struct {
                        shorthand: Some(key),
                        ..
                    }) => serde_json::json!({ *key: text }),
                    _ => node.to_json(),
                }
            }
            (Ty::Ref(name), Kind::Map(entries)) => {
                let Some(model) = self.models.get(name) else {
                    return node.to_json();
                };
                let variant = match &model.body {
                    Body::Tagged { tag, variants, .. } => {
                        match entries.get(*tag).map(|e| &e.value.kind) {
                            Some(Kind::Scalar(yaml::Value::Str(t))) => {
                                variants.iter().find(|(n, _)| n == t).map(|(_, v)| v)
                            }
                            _ => None,
                        }
                    }
                    Body::Struct { .. } => None,
                };
                if let Some(variant) = variant {
                    return self.expand(variant, node);
                }
                let flat = self.flat(model);
                entries
                    .iter()
                    .map(|(k, e)| {
                        let value = match flat.props.iter().find(|p| p.key == k) {
                            Some(p) => self.expand(p.ty, &e.value),
                            None => e.value.to_json(),
                        };
                        (k.clone(), value)
                    })
                    .collect::<serde_json::Map<_, _>>()
                    .into()
            }
            _ => node.to_json(),
        }
    }

    /// Whether a type reads this node: the arm `accept` takes it by. A struct reads a mapping
    /// whose every key it names, a tagged enum one whose tag it knows, and a map any mapping.
    fn fits(&self, ty: &Ty, node: &Node) -> bool {
        match (ty.bare(), &node.kind) {
            (Ty::Ref(name), Kind::Map(entries)) => match self.models.get(name) {
                Some(model) => match &model.body {
                    Body::Struct { .. } => {
                        let known = self.flat(model);
                        entries
                            .keys()
                            .all(|k| known.props.iter().any(|p| p.key == k))
                    }
                    Body::Tagged { tag, variants, .. } => matches!(
                        entries.get(*tag).map(|e| &e.value.kind),
                        Some(Kind::Scalar(yaml::Value::Str(t))) if variants.iter().any(|(n, _)| n == t)
                    ),
                },
                None => false,
            },
            (Ty::Map(_) | Ty::Engine(..), Kind::Map(_)) => true,
            (Ty::List(_) | Ty::Tuple(_), Kind::Seq(_)) => true,
            (Ty::Accept(a, b), _) => self.fits(a, node) || self.fits(b, node),
            (Ty::Ref(_) | Ty::List(_) | Ty::Map(_), _) => false,
            (_, Kind::Scalar(_)) => true,
            _ => false,
        }
    }
}

fn join(at: &str, key: &str) -> String {
    if at.is_empty() {
        key.to_string()
    } else {
        format!("{at}.{key}")
    }
}

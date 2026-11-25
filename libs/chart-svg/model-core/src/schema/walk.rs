use super::{Body, Flat, Prop, Schema, SchemaModel, Ty};
use std::collections::HashMap;
use std::rc::Rc;
use yaml::{Kind, Node};

/// Every struct-valued node of an authored document, with the struct's flat field table.
pub struct Visit<'a, 'n> {
    /// The authored path of the node: keys and list indexes, dot-joined.
    pub path: String,
    /// Whether a list holds the node, at any depth.
    pub listed: bool,
    pub model: &'a str,
    /// Shared by every visit of the same model in one walk.
    pub flat: Rc<Flat<'a>>,
    pub node: &'n Node,
}

impl Schema {
    /// Walk `node`, a document of the root model, by the schema's types and visit each struct it
    /// holds, outermost first. A node the schema does not type that way (a scalar where a struct
    /// is, a key no field names) is left to the parse that refuses it.
    pub fn structs<'a, 'n>(&'a self, node: &'n Node) -> Vec<Visit<'a, 'n>> {
        self.structs_where(node, &|_, _| true)
    }

    /// [`Self::structs`], entering a struct's field only where `enter(model, prop)` holds. Each
    /// model's flat table is built once per walk.
    pub fn structs_where<'a, 'n>(
        &'a self,
        node: &'n Node,
        enter: &dyn Fn(&str, &Prop<'a>) -> bool,
    ) -> Vec<Visit<'a, 'n>> {
        let mut walk = Walk {
            schema: self,
            enter,
            flats: HashMap::new(),
            out: Vec::new(),
        };
        walk.descend(&self.root_ty, node, "", false);
        walk.out
    }
}

struct Walk<'a, 'n, 'e> {
    schema: &'a Schema,
    enter: &'e dyn Fn(&str, &Prop<'a>) -> bool,
    flats: HashMap<&'a str, Rc<Flat<'a>>>,
    out: Vec<Visit<'a, 'n>>,
}

impl<'a, 'n> Walk<'a, 'n, '_> {
    fn flat(&mut self, model: &'a SchemaModel) -> Rc<Flat<'a>> {
        let schema = self.schema;
        self.flats
            .entry(model.name)
            .or_insert_with(|| Rc::new(schema.flat(model)))
            .clone()
    }

    fn descend(&mut self, ty: &'a Ty, node: &'n Node, at: &str, listed: bool) {
        match (ty, &node.kind) {
            (Ty::Option(t, _) | Ty::Patch(t) | Ty::Keyed(_, t) | Ty::Engine(t, _), _) => {
                self.descend(t, node, at, listed);
            }
            (Ty::Accept(a, b), _) => {
                let first = if self.schema.fits_by(a, node, &mut |m| self.flat(m)) {
                    a
                } else {
                    b
                };
                self.descend(first, node, at, listed);
            }
            (Ty::List(item), Kind::Seq(items)) => {
                for (i, n) in items.iter().enumerate() {
                    self.descend(item, n, &join(at, &i.to_string()), true);
                }
            }
            (Ty::Map(item), Kind::Map(entries)) => {
                for (k, e) in entries {
                    self.descend(item, &e.value, &join(at, k), listed);
                }
            }
            (Ty::Ref(name), Kind::Map(entries)) => {
                let Some(model) = self.schema.models.get(name) else {
                    return;
                };
                match &model.body {
                    Body::Struct { .. } => {
                        let flat = self.flat(model);
                        self.out.push(Visit {
                            path: at.to_string(),
                            listed,
                            model: model.name,
                            flat: flat.clone(),
                            node,
                        });
                        for p in &flat.props {
                            if let Some(e) = entries.get(p.key) {
                                if (self.enter)(model.name, p) {
                                    self.descend(p.ty, &e.value, &join(at, p.key), listed);
                                }
                            }
                        }
                    }
                    Body::Tagged { tag, variants, .. } => {
                        let Some(Kind::Scalar(yaml::Value::Str(name))) =
                            entries.get(*tag).map(|e| &e.value.kind)
                        else {
                            return;
                        };
                        if let Some((_, variant)) = variants.iter().find(|(n, _)| n == name) {
                            self.descend(variant, node, at, listed);
                        }
                    }
                }
            }
            _ => {}
        }
    }
}

impl Schema {
    /// A struct's node as authored JSON, with each bare string or list a model reads as its
    /// shorthand written out (`font: heading` is `{extends: heading}`, `domain: [0, 1]` is
    /// `{min: 0, max: 1}`), so a rule's dotted key reaches it.
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
            // A `[min, max]` shorthand names the struct's fields in order; a null item is unset.
            (Ty::Ref(name), Kind::Seq(items)) => match self.models.get(name).map(|m| &m.body) {
                Some(Body::Struct {
                    shorthand_items: Some(_),
                    fields,
                    ..
                }) => fields
                    .iter()
                    .zip(items)
                    .filter(|(_, n)| !matches!(n.kind, Kind::Scalar(yaml::Value::Null)))
                    .map(|(f, n)| (f.key.to_string(), n.to_json()))
                    .collect::<serde_json::Map<_, _>>()
                    .into(),
                _ => node.to_json(),
            },
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
        self.fits_by(ty, node, &mut |m| Rc::new(self.flat(m)))
    }

    /// [`Self::fits`], reading a struct's flat table through `flat`.
    fn fits_by<'a>(
        &'a self,
        ty: &Ty,
        node: &Node,
        flat: &mut dyn FnMut(&'a SchemaModel) -> Rc<Flat<'a>>,
    ) -> bool {
        match (ty.bare(), &node.kind) {
            (Ty::Ref(name), Kind::Map(entries)) => match self.models.get(name) {
                Some(model) => match &model.body {
                    Body::Struct { .. } => {
                        let known = flat(model);
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
            (Ty::Accept(a, b), _) => self.fits_by(a, node, flat) || self.fits_by(b, node, flat),
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

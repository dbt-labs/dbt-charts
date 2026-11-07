//! JSON Schema 2020-12, shaped like the Python renderer's
//! (`compile/schema/renderers/json_schema.py`) so the two compare exactly.
//! Cross-field rules carry `x-dbt-charts-cross-field-rule`, as there.
//! `extensions` adds the `x-dbt-*` annotations Python's schema does not carry:
//! `x-dbt-facet`, `x-dbt-absent`, `x-dbt-inherit`, `x-dbt-inherit-slot`,
//! `x-dbt-links`, `x-dbt-local`, `x-dbt-nested-drop`, `x-dbt-role`.

use super::{Body, Prop, Schema, SchemaField, SchemaModel, Ty, View};
use crate::{Bounds, Condition, Constraint, ConstraintKind, InEngine, Keywords, Scalar, Value};
use serde_json::{json, Map, Value as J};
use std::collections::BTreeSet;

/// The annotation marking an `allOf` entry as a cross-field rule.
pub const CROSS_FIELD_RULE: &str = "x-dbt-charts-cross-field-rule";

const EMPTY: [&str; 0] = [];

/// The keywords a facet or unit declares.
const KEYWORDS: [&str; 7] = [
    "minItems",
    "maxItems",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
];

impl Schema {
    /// The JSON Schema of the root with every other model under `$defs`.
    pub fn json_schema(&self, view: View, extensions: bool) -> J {
        self.json_schema_in(view, extensions, &self.mirrored())
    }

    /// [`Schema::json_schema`] for a schema that is part of a larger one: `mirrored` names the
    /// models every use of which, across the larger schema, is cascade-filled (see
    /// [`Schema::mirrored`]); a model's schema alone cannot see its parents.
    pub fn json_schema_in(&self, view: View, extensions: bool, mirrored: &BTreeSet<String>) -> J {
        let e = Emit {
            schema: self,
            view,
            ext: extensions,
            mirrored,
        };
        let mut out = Map::new();
        out.insert(
            "$schema".into(),
            json!("https://json-schema.org/draft/2020-12/schema"),
        );
        out.insert("title".into(), json!(self.root));
        let mut defs = Map::new();
        let refs = self.referenced();
        for (name, m) in &self.models {
            if *name != self.root && !refs.contains(&name.as_str()) {
                continue;
            }
            let def = e.model(m);
            if *name == self.root {
                if let J::Object(root) = def {
                    out.extend(
                        [
                            "description",
                            "type",
                            "properties",
                            "additionalProperties",
                            "required",
                            "allOf",
                        ]
                        .iter()
                        .filter_map(|k| root.get(*k).map(|v| ((*k).to_string(), v.clone()))),
                    );
                }
            } else {
                defs.insert(name.clone(), def);
            }
        }
        let mut ordered = out;
        if !defs.is_empty() {
            ordered.insert("$defs".into(), J::Object(defs));
        }
        J::Object(ordered)
    }
}

struct Emit<'a> {
    schema: &'a Schema,
    view: View,
    ext: bool,
    mirrored: &'a BTreeSet<String>,
}

/// A number as JSON writes it: `0`, not `0.0`, so a bound equals the one Python emits.
fn num(v: f64) -> J {
    if v.is_finite() && v.fract() == 0.0 && v.abs() < 9e15 {
        json!(v as i64)
    } else {
        json!(v)
    }
}

/// The keywords of a facet or unit: a pattern and the bounds, as one object. `rule` is whether
/// an `Engine` takes the `auto` the facet would refuse.
fn keywords(k: &Keywords, rule: bool) -> Map<String, J> {
    let mut o = bound_map(&bounds(&k.bounds));
    let pattern = match (rule, k.in_engine) {
        (true, InEngine::Drop) => None,
        (true, InEngine::Pattern(p)) => Some(p),
        _ => k.pattern,
    };
    if let Some(p) = pattern {
        o.insert("pattern".into(), json!(p));
    }
    if let Some(n) = k.min_items {
        o.insert("minItems".into(), json!(n));
    }
    if let Some(n) = k.max_items {
        o.insert("maxItems".into(), json!(n));
    }
    o
}

/// Whether the property is one scalar type, alone or beside `null`: the only shape a closed set
/// can stand in for without losing an arm.
fn one_scalar(prop: &Map<String, J>) -> bool {
    let typed = |a: &J| a.get("type").is_some_and(|t| t != "null");
    match prop.get("anyOf") {
        Some(J::Array(arms)) => arms.iter().filter(|a| typed(a)).count() == 1,
        _ => prop.get("type").is_some(),
    }
}

/// The keywords a field's type declares, moved from its arms to the property: pydantic writes
/// `Field(pattern=..)` and `Field(ge=..)` beside the `anyOf` of `X | None` or `X | "auto"`, not
/// inside one arm. Arms are built from one facet or unit each, so a keyword is not stated twice.
/// A tuple's own length stays in its arm.
fn hoist(j: J) -> J {
    let J::Object(mut o) = j else { return j };
    let Some(J::Array(arms)) = o.get_mut("anyOf") else {
        return J::Object(o);
    };
    let mut up = Map::new();
    for arm in arms.iter_mut() {
        if let J::Object(a) = arm {
            // A tuple states its own length, as a pydantic `tuple` does.
            if a.contains_key("prefixItems") {
                continue;
            }
            for k in KEYWORDS {
                if let Some(v) = a.remove(k) {
                    up.insert(k.to_string(), v);
                }
            }
        }
    }
    o.extend(up);
    J::Object(o)
}

fn scalar(s: Scalar) -> J {
    match s {
        Scalar::Bool => json!({"type": "boolean"}),
        Scalar::Int => json!({"type": "integer"}),
        Scalar::Float => json!({"type": "number"}),
        Scalar::Str => json!({"type": "string"}),
        Scalar::Json => json!({}),
    }
}

fn value(v: &Value) -> J {
    match v {
        Value::Null => J::Null,
        Value::Bool(b) => json!(b),
        Value::Int(i) => json!(i),
        Value::Float(f) => json!(f),
        Value::Str(s) => json!(s),
    }
}

/// `anyOf` of the arms, flattening nested `anyOf`s and dropping repeats. An arm that is an
/// `anyOf` with keywords beside it hands them to its own arms, where [`hoist`] finds them.
fn any_of(arms: Vec<J>) -> J {
    let mut flat: Vec<J> = Vec::new();
    for a in arms {
        let inner = match &a {
            J::Object(o)
                if o.contains_key("anyOf")
                    && o.keys()
                        .all(|k| k == "anyOf" || KEYWORDS.contains(&k.as_str())) =>
            {
                let mut inner = o
                    .get("anyOf")
                    .and_then(J::as_array)
                    .cloned()
                    .unwrap_or_default();
                for arm in inner.iter_mut() {
                    if let J::Object(x) = arm {
                        for (k, v) in o.iter().filter(|(k, _)| *k != "anyOf") {
                            x.entry(k.clone()).or_insert(v.clone());
                        }
                    }
                }
                Some(inner)
            }
            _ => None,
        };
        for arm in inner.unwrap_or_else(|| vec![a]) {
            if !flat.contains(&arm) {
                flat.push(arm);
            }
        }
    }
    match flat.len() {
        1 => flat.into_iter().next().unwrap_or(J::Null),
        _ => json!({"anyOf": flat}),
    }
}

/// Apply `schema` to the value at dotted `key`; each level is typed `object`.
fn at(key: &str, schema: J, required: bool) -> J {
    let mut schema = schema;
    for part in key.split('.').rev() {
        let mut o = Map::new();
        o.insert("type".into(), json!("object"));
        o.insert("properties".into(), json!({ part: schema }));
        if required {
            o.insert("required".into(), json!([part]));
        }
        schema = J::Object(o);
    }
    schema
}

fn is_set(key: &str, nonempty: bool, auto: bool) -> J {
    let mut unset = if nonempty {
        json!({"enum": [null, "", [], {}]})
    } else {
        json!({"type": "null"})
    };
    if auto {
        unset = json!({"anyOf": [unset, {"const": "auto"}]});
    }
    at(key, json!({"not": unset}), true)
}

fn condition(c: &Condition) -> J {
    match c {
        Condition::IsSet(k) => is_set(k, false, false),
        Condition::IsList(k) => at(k, json!({"type": "array"}), true),
        Condition::ValueIn {
            key,
            values,
            negate,
        } => {
            let vals: Vec<J> = values.iter().map(value).collect();
            let mut m = at(key, json!({"enum": vals}), true);
            if values.contains(&Value::Null) {
                m = json!({"anyOf": [m, {"not": is_set(key, false, false)}]});
            }
            if *negate {
                json!({"not": m})
            } else {
                m
            }
        }
        Condition::IsCall { key, names, negate } => {
            let call = at(
                key,
                json!({"anyOf": [
                    {"type": "string",
                     "pattern": format!(r"^\s*(?:{})\s*\(\s*\)\s*$", names.join("|"))},
                    at("fn", json!({"enum": names}), true),
                ]}),
                true,
            );
            if *negate {
                json!({"not": call})
            } else {
                call
            }
        }
    }
}

fn when(conds: &[Condition], then: J) -> J {
    if conds.is_empty() {
        return then;
    }
    let all: Vec<J> = conds.iter().map(condition).collect();
    json!({"if": {"allOf": all}, "then": then})
}

fn bound_map(b: &J) -> Map<String, J> {
    match b {
        J::Object(o) => o.clone(),
        _ => Map::new(),
    }
}

fn bounds(b: &Bounds) -> J {
    let mut o = Map::new();
    for (k, v) in [
        ("exclusiveMinimum", b.gt),
        ("minimum", b.ge),
        ("exclusiveMaximum", b.lt),
        ("maximum", b.le),
    ] {
        if let Some(v) = v {
            o.insert(k.into(), num(v));
        }
    }
    J::Object(o)
}

/// The rule as Python's renderer states it; `None` where JSON Schema cannot say it.
fn constraint(c: &Constraint) -> Option<J> {
    let set = |k: &str| is_set(k, c.nonempty.contains(&k), false);
    let key = c.fields.first().copied().unwrap_or_default();
    Some(match c.kind {
        ConstraintKind::AtLeastOneOf => {
            json!({"anyOf": c.fields.iter().map(|k| set(k)).collect::<Vec<_>>()})
        }
        ConstraintKind::AtMostOneOf => at_most_one(c.fields, c.nonempty, c.auto),
        ConstraintKind::Requires => when(
            c.when,
            json!({"allOf": c.fields.iter().map(|k| set(k)).collect::<Vec<_>>()}),
        ),
        ConstraintKind::Forbids => when(
            c.when,
            json!({"not": {"anyOf": c.fields.iter().map(|k| set(k)).collect::<Vec<_>>()}}),
        ),
        ConstraintKind::ItemsMatch => when(
            c.when,
            at(
                key,
                json!({"items": {"type": "string", "pattern": c.pattern}}),
                false,
            ),
        ),
        ConstraintKind::ValueIn => {
            let vals: Vec<J> = c.values.iter().map(value).collect();
            when(c.when, at(key, json!({"enum": vals}), false))
        }
        // A bound on a number, or on each number of a list: numeric keywords
        // ignore a non-number, and `items` ignores a non-list.
        ConstraintKind::Range => {
            let b = bounds(&c.bounds);
            let mut with_items = b.clone();
            if let J::Object(o) = &mut with_items {
                o.insert("items".into(), b);
            }
            when(c.when, at(key, with_items, false))
        }
        // Uniqueness across several lists is not a JSON Schema statement.
        ConstraintKind::Unique if c.fields.len() == 1 => {
            at(key, json!({"uniqueItems": true}), false)
        }
        ConstraintKind::Unique => return None,
    })
}

fn at_most_one(keys: &[&str], nonempty: &[&str], auto: &[&str]) -> J {
    let mut pairs = Vec::new();
    for (i, a) in keys.iter().enumerate() {
        for b in keys.iter().skip(i + 1) {
            pairs.push(json!({"allOf": [
                is_set(a, nonempty.contains(a), auto.contains(a)),
                is_set(b, nonempty.contains(b), auto.contains(b)),
            ]}));
        }
    }
    json!({"not": {"anyOf": pairs}})
}

fn rule(mut body: J) -> J {
    let mut o = Map::new();
    o.insert(CROSS_FIELD_RULE.into(), json!(true));
    if let J::Object(b) = &mut body {
        o.append(b);
    }
    J::Object(o)
}

impl Emit<'_> {
    /// A model by reference: `$ref`, and for a model that reads from a bare string, that string
    /// as a second arm.
    fn reference(&self, name: &str) -> J {
        let to = self.bare_reference(name);
        match self.schema.models.get(name).map(|m| &m.body) {
            Some(Body::Struct {
                shorthand: Some(_), ..
            }) => any_of(vec![to, json!({"type": "string"})]),
            // Python's string shorthand belongs to its patch tier and its total models take the mapping; the
            // gate compares the Authored view with those, so the arm is stated in the Patch view only.
            // The parser reads the string in both.
            Some(Body::Struct {
                string_pattern: Some(p),
                ..
            }) if self.view == View::Patch => {
                any_of(vec![json!({"type": "string", "pattern": p}), to])
            }
            _ => to,
        }
    }

    fn bare_reference(&self, name: &str) -> J {
        if name == self.schema.root {
            json!({"$ref": "#"})
        } else {
            json!({"$ref": format!("#/$defs/{name}")})
        }
    }

    fn ty(&self, ty: &Ty) -> J {
        self.ty_in(ty, false)
    }

    /// `rule`: an `Engine` around the type takes `auto` before the type sees it.
    fn ty_in(&self, ty: &Ty, rule: bool) -> J {
        match ty {
            Ty::Scalar(s) => scalar(*s),
            Ty::Facet(name, s) => {
                let mut j = scalar(*s);
                if let (true, J::Object(o)) = (self.ext, &mut j) {
                    o.insert("x-dbt-facet".into(), json!(name));
                }
                j
            }
            Ty::Ref(n) => self.reference(n),
            Ty::List(i) => match self.ty(i) {
                J::Object(o) if o.is_empty() => json!({"type": "array"}),
                items => json!({"type": "array", "items": items}),
            },
            Ty::Map(i) => json!({"type": "object", "additionalProperties": self.ty(i)}),
            Ty::Option(i, _) => any_of(vec![self.ty(i), json!({"type": "null"})]),
            Ty::Enum(v) => json!({"enum": v}),
            // A closed set of values beside `auto` is one enum, as pydantic merges the literals.
            Ty::Engine(v, rules) => match &**v {
                Ty::Enum(values) => {
                    let mut all: Vec<&str> = rules.clone();
                    all.extend(values.iter());
                    json!({"enum": all})
                }
                v => any_of(vec![json!({"enum": rules}), self.ty_in(v, true)]),
            },
            // An explicit union spells its own alternatives, a bare string among them.
            Ty::Accept(a, b) => {
                let first = match &**a {
                    Ty::Ref(n) => self.bare_reference(n),
                    a => self.ty(a),
                };
                any_of(vec![first, self.ty(b)])
            }
            Ty::Patch(i) => self.ty(i),
            Ty::Keyed(k, i) => {
                let mut j = self.ty(i);
                if let J::Object(o) = &mut j {
                    o.extend(keywords(k, rule));
                }
                j
            }
            Ty::Tuple(items) => json!({
                "type": "array",
                "prefixItems": items.iter().map(|t| self.ty(t)).collect::<Vec<_>>(),
                "minItems": items.len(),
                "maxItems": items.len(),
            }),
            Ty::Unit => json!({"type": "null"}),
            // A `keys` field is expanded into its variants by the struct.
            Ty::Keys(_) => json!({}),
        }
    }

    /// A property: its type, the description with the inherit note, extensions.
    fn prop(&self, key_ty: &Ty, f: &SchemaField, doc: &str) -> J {
        let a = f.attrs;
        let mut j = hoist(if a.nullable {
            any_of(vec![self.ty(key_ty), json!({"type": "null"})])
        } else {
            self.ty(key_ty)
        });
        let note = match (a.inherit_slot, a.inherit) {
            (Some(p), _) => format!("Unset fields fall back to `{p}`."),
            (None, Some(p)) => format!("Falls back to `{p}`."),
            _ => String::new(),
        };
        let desc = [doc, note.as_str()]
            .iter()
            .filter(|s| !s.is_empty())
            .copied()
            .collect::<Vec<_>>()
            .join(" ");
        let J::Object(o) = &mut j else { return j };
        if !desc.is_empty() {
            o.insert("description".into(), json!(desc));
        }
        if let Some(v) = &a.fill_value {
            o.insert("default".into(), value(v));
        }
        if self.ext {
            if let Some(m) = a.absent {
                o.insert("x-dbt-absent".into(), json!(m));
            }
            if let Some(p) = a.inherit {
                o.insert("x-dbt-inherit".into(), json!(p));
            }
            if let Some(p) = a.inherit_slot {
                o.insert("x-dbt-inherit-slot".into(), json!(p));
            }
            if !a.links.is_empty() {
                let links: Map<String, J> = a
                    .links
                    .iter()
                    .map(|(l, p)| ((*l).to_string(), json!(p)))
                    .collect();
                o.insert("x-dbt-links".into(), J::Object(links));
            }
            if a.local {
                o.insert("x-dbt-local".into(), json!(true));
            }
            if a.nested_drop {
                o.insert("x-dbt-nested-drop".into(), json!(true));
            }
            if let Some(r) = a.role {
                o.insert("x-dbt-role".into(), json!(r));
            }
        }
        j
    }

    /// Whether an authored document must say `p`: the spine cannot fill it from a fallback,
    /// a `fill` or the slot it mirrors.
    fn needs(&self, owner: &SchemaModel, p: &Prop) -> bool {
        let a = p.field.attrs;
        !(p.keyed
            || matches!(p.field.ty, Ty::Option(..))
            || a.fill.is_some()
            || a.fill_value.is_some()
            || a.directive
            || a.inherit.is_some()
            || a.inherit_slot.is_some()
            || (self.mirrored.contains(owner.name)
                && !matches!(owner.body, Body::Struct { complete: true, .. })))
    }

    fn model(&self, m: &SchemaModel) -> J {
        match &m.body {
            Body::Struct { .. } => self.strukt(m),
            Body::Tagged { tag, variants, .. } => self.tagged(m, tag, variants),
        }
    }

    fn strukt(&self, m: &SchemaModel) -> J {
        let flat = self.schema.flat(m);
        let (props, groups, constraints) = (flat.props, flat.groups, flat.constraints);
        let mut properties = Map::new();
        let mut required = Vec::new();
        for p in &props {
            properties.insert(p.key.into(), self.prop(p.ty, p.field, p.doc));
            if self.view == View::Authored && self.needs(m, p) {
                required.push(p.key);
            }
        }
        // A variant model declares the tag that selects it, as Python's variants do.
        let selected = self.schema.models.values().find_map(|t| match &t.body {
            Body::Tagged { tag, variants, .. } => {
                let tags: Vec<&str> = variants
                    .iter()
                    .filter(|(_, ty)| matches!(ty, Ty::Ref(n) if n == m.name))
                    .map(|(t, _)| *t)
                    .collect();
                (!tags.is_empty()).then_some((*tag, tags))
            }
            _ => None,
        });
        if let Some((tag, mut tags)) = selected {
            if !properties.contains_key(tag) {
                tags.sort_unstable();
                properties.insert(tag.into(), json!({"enum": tags}));
                if self.view == View::Authored {
                    required.push(tag);
                }
            }
        }
        let mut o = Map::new();
        o.insert("type".into(), json!("object"));
        o.insert("properties".into(), J::Object(properties));
        if !m.doc.is_empty() {
            o.insert("description".into(), json!(m.doc));
        }
        if !required.is_empty() {
            o.insert("required".into(), json!(required));
        }
        o.insert("additionalProperties".into(), json!(false));
        // A bound on one field is that property's own keyword, as `Field(ge=..)` writes it.
        let mut left = Vec::new();
        for c in constraints {
            let key = c.fields.first().copied().unwrap_or_default();
            let alone = c.when.is_empty() && c.fields.len() == 1;
            let bound = c.kind == ConstraintKind::Range && alone;
            match o.get_mut("properties").and_then(|p| p.get_mut(key)) {
                // A closed set of values on one field is that property's own `enum`, as a
                // `Literal` writes it; the enum states the type.
                Some(J::Object(prop))
                    if c.kind == ConstraintKind::ValueIn && alone && one_scalar(prop) =>
                {
                    let vals: Vec<J> = c.values.iter().map(value).collect();
                    match prop.get_mut("anyOf") {
                        Some(J::Array(arms)) => {
                            let kept = arms
                                .iter_mut()
                                .find(|a| a.get("type").is_some_and(|t| t != "null"));
                            if let Some(arm) = kept {
                                *arm = json!({"enum": vals});
                            }
                        }
                        _ => {
                            prop.remove("type");
                            prop.insert("enum".into(), J::Array(vals));
                        }
                    }
                }
                Some(J::Object(prop)) if bound => {
                    let b = bounds(&c.bounds);
                    // Numeric keywords ignore an array, so a list's bound goes on its items,
                    // in the array arm of an `anyOf` when the property is optional.
                    let on_items = |p: &mut Map<String, J>| {
                        let list = p.get("type") == Some(&json!("array"));
                        match p.get_mut("items") {
                            Some(J::Object(items)) if list => {
                                items.extend(bound_map(&b));
                                true
                            }
                            _ => false,
                        }
                    };
                    let placed = match prop.get_mut("anyOf") {
                        Some(J::Array(arms)) => arms.iter_mut().fold(false, |hit, a| match a {
                            J::Object(a) => on_items(a) || hit,
                            _ => hit,
                        }),
                        _ => on_items(prop),
                    };
                    if !placed {
                        prop.extend(bound_map(&b));
                    }
                }
                _ => left.push(c),
            }
        }
        let mut rules: Vec<J> = left.into_iter().filter_map(constraint).map(rule).collect();
        for g in &groups {
            rules.push(rule(at_most_one(&g.keys, &EMPTY, &EMPTY)));
            if self.view == View::Authored && !g.optional {
                let any: Vec<J> = g.keys.iter().map(|k| is_set(k, false, false)).collect();
                rules.push(rule(json!({"anyOf": any})));
            }
        }
        if !rules.is_empty() {
            o.insert("allOf".into(), J::Array(rules));
        }
        J::Object(o)
    }

    fn tagged(&self, m: &SchemaModel, tag: &str, variants: &[(&str, Ty)]) -> J {
        let mut all_tags: Vec<&str> = variants.iter().map(|(t, _)| *t).collect();
        all_tags.sort_unstable();
        // Variants selecting the same model share one branch.
        let mut groups: Vec<(&Ty, Vec<&str>)> = Vec::new();
        for (t, ty) in variants {
            match groups.iter_mut().find(|(g, _)| g == &ty) {
                Some((_, tags)) => tags.push(t),
                None => groups.push((ty, vec![t])),
            }
        }
        let branches: Vec<J> = groups
            .into_iter()
            .map(|(ty, mut tags)| {
                tags.sort_unstable();
                json!({
                    "if": {"properties": {tag: {"enum": tags}}, "required": [tag]},
                    "then": self.ty(ty),
                })
            })
            .collect();
        let one_of = format!("One of: {}.", all_tags.join(", "));
        let mut o = Map::new();
        o.insert("type".into(), json!("object"));
        o.insert(
            "properties".into(),
            json!({tag: {"enum": all_tags, "description": one_of}}),
        );
        o.insert("required".into(), json!([tag]));
        o.insert("allOf".into(), J::Array(branches));
        if !m.doc.is_empty() {
            o.insert("description".into(), json!(m.doc));
        }
        J::Object(o)
    }
}

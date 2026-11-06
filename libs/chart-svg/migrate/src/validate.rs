//! The JSON Schema subset the frozen grammar files use, evaluated over a [`Node`].
//!
//! Keywords: `type`, `enum`, `const`, `properties`, `required`,
//! `additionalProperties`, `items`/`prefixItems`, `anyOf`, `allOf`, `not`,
//! `if`/`then`, `$ref`, `pattern`, and the numeric/size bounds. Anything else is
//! an annotation. Format is not asserted. A schema declaring draft-07 reads as
//! jsonschema's Draft7 does: `$ref` overrides its siblings and `prefixItems` is
//! unknown.

use fancy_regex::Regex;
use serde_json::Value as Json;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use yaml::{Kind, Node, Value};

use crate::pyrepr::{py_repr as repr_str, repr_json};

/// Whether `node` is valid under `schema`, with `$ref` resolved against `root`.
pub fn is_valid(root: &Json, schema: &Json, node: &Node) -> bool {
    let legacy = root
        .get("$schema")
        .and_then(Json::as_str)
        .is_some_and(|s| s.contains("draft-07"));
    check(root, legacy, schema, node)
}

fn check(root: &Json, legacy: bool, schema: &Json, node: &Node) -> bool {
    let Some(s) = schema.as_object() else {
        return schema.as_bool().unwrap_or(true);
    };
    if let Some(r) = s.get("$ref").and_then(Json::as_str) {
        if !resolve(root, r).is_some_and(|t| check(root, legacy, t, node)) {
            return false;
        }
        if legacy {
            return true;
        }
    }
    let sub = |v: &Json| check(root, legacy, v, node);
    if let Some(t) = s.get("type") {
        let ok = match t {
            Json::String(t) => type_is(t, node),
            Json::Array(ts) => ts.iter().filter_map(Json::as_str).any(|t| type_is(t, node)),
            _ => true,
        };
        if !ok {
            return false;
        }
    }
    if let Some(Json::Array(options)) = s.get("enum") {
        if !options.iter().any(|o| equals(o, node)) {
            return false;
        }
    }
    if let Some(c) = s.get("const") {
        if !equals(c, node) {
            return false;
        }
    }
    if let Some(Json::Array(all)) = s.get("allOf") {
        if !all.iter().all(sub) {
            return false;
        }
    }
    if let Some(Json::Array(any)) = s.get("anyOf") {
        if !any.iter().any(sub) {
            return false;
        }
    }
    if let Some(not) = s.get("not") {
        if sub(not) {
            return false;
        }
    }
    if let Some(cond) = s.get("if") {
        let branch = if sub(cond) {
            s.get("then")
        } else {
            s.get("else")
        };
        if let Some(b) = branch {
            if !sub(b) {
                return false;
            }
        }
    }
    match &node.kind {
        Kind::Map(map) => object(root, legacy, s, map),
        Kind::Seq(items) => array(root, legacy, s, items),
        Kind::Scalar(Value::Str(text)) => string(s, text),
        Kind::Scalar(Value::Number(n)) => number(s, n.as_f64().unwrap_or(f64::NAN)),
        Kind::Scalar(_) => true,
    }
}

fn object(
    root: &Json,
    legacy: bool,
    s: &serde_json::Map<String, Json>,
    map: &indexmap::IndexMap<String, yaml::Entry>,
) -> bool {
    if let Some(Json::Array(required)) = s.get("required") {
        if required
            .iter()
            .filter_map(Json::as_str)
            .any(|k| !map.contains_key(k))
        {
            return false;
        }
    }
    if let Some(min) = s.get("minProperties").and_then(Json::as_u64) {
        if (map.len() as u64) < min {
            return false;
        }
    }
    let properties = s.get("properties").and_then(Json::as_object);
    let additional = s.get("additionalProperties");
    for (key, entry) in map {
        let declared = properties.and_then(|p| p.get(key));
        match declared {
            Some(p) => {
                if !check(root, legacy, p, &entry.value) {
                    return false;
                }
            }
            None => match additional {
                Some(Json::Bool(false)) => return false,
                Some(a @ Json::Object(_)) if !check(root, legacy, a, &entry.value) => return false,
                _ => {}
            },
        }
    }
    true
}

fn array(root: &Json, legacy: bool, s: &serde_json::Map<String, Json>, items: &[Node]) -> bool {
    let len = items.len() as u64;
    if s.get("minItems")
        .and_then(Json::as_u64)
        .is_some_and(|m| len < m)
        || s.get("maxItems")
            .and_then(Json::as_u64)
            .is_some_and(|m| len > m)
    {
        return false;
    }
    let prefix = match (legacy, s.get("prefixItems")) {
        (false, Some(Json::Array(p))) => p.as_slice(),
        _ => &[],
    };
    for (item, schema) in items.iter().zip(prefix) {
        if !check(root, legacy, schema, item) {
            return false;
        }
    }
    match s.get("items") {
        Some(schema @ Json::Object(_)) => items
            .iter()
            .skip(prefix.len())
            .all(|i| check(root, legacy, schema, i)),
        _ => true,
    }
}

fn string(s: &serde_json::Map<String, Json>, text: &str) -> bool {
    let chars = text.chars().count() as u64;
    if s.get("minLength")
        .and_then(Json::as_u64)
        .is_some_and(|m| chars < m)
        || s.get("maxLength")
            .and_then(Json::as_u64)
            .is_some_and(|m| chars > m)
    {
        return false;
    }
    match s.get("pattern").and_then(Json::as_str) {
        Some(p) => pattern(p).is_some_and(|re| re.is_match(text).unwrap_or(false)),
        None => true,
    }
}

fn number(s: &serde_json::Map<String, Json>, n: f64) -> bool {
    let bound = |k: &str| s.get(k).and_then(Json::as_f64);
    bound("minimum").is_none_or(|m| n >= m)
        && bound("maximum").is_none_or(|m| n <= m)
        && bound("exclusiveMinimum").is_none_or(|m| n > m)
        && bound("exclusiveMaximum").is_none_or(|m| n < m)
}

fn type_is(name: &str, node: &Node) -> bool {
    match (&node.kind, name) {
        (Kind::Map(_), "object") | (Kind::Seq(_), "array") => true,
        (Kind::Scalar(Value::Null), "null") | (Kind::Scalar(Value::Bool(_)), "boolean") => true,
        (Kind::Scalar(Value::Str(_)), "string") => true,
        (Kind::Scalar(Value::Number(_)), "number") => true,
        (Kind::Scalar(Value::Number(n)), "integer") => {
            n.is_i64() || n.is_u64() || n.as_f64().is_some_and(|f| f.fract() == 0.0)
        }
        _ => false,
    }
}

/// jsonschema's equality: `true` is not `1`, and `1` equals `1.0`.
pub(crate) fn equals(expected: &Json, node: &Node) -> bool {
    match (expected, &node.kind) {
        (Json::Null, Kind::Scalar(Value::Null)) => true,
        (Json::Bool(a), Kind::Scalar(Value::Bool(b))) => a == b,
        (Json::Number(a), Kind::Scalar(Value::Number(b))) => a.as_f64() == b.as_f64(),
        (Json::String(a), Kind::Scalar(Value::Str(b))) => a == b,
        (Json::Array(a), Kind::Seq(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| equals(x, y))
        }
        (Json::Object(a), Kind::Map(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(k, x)| b.get(k).is_some_and(|e| equals(x, &e.value)))
        }
        _ => false,
    }
}

/// `#` or `#/a/b` into `root`, with `~0`/`~1` unescaped.
pub fn resolve<'a>(root: &'a Json, reference: &str) -> Option<&'a Json> {
    if reference == "#" {
        return Some(root);
    }
    let mut target = root;
    for part in reference.strip_prefix("#/")?.split('/') {
        target = target.get(part.replace("~1", "/").replace("~0", "~"))?;
    }
    Some(target)
}

fn pattern(source: &str) -> Option<&'static Regex> {
    static CACHE: OnceLock<Mutex<HashMap<String, Option<&'static Regex>>>> = OnceLock::new();
    let mut cache = CACHE.get_or_init(Default::default).lock().ok()?;
    if let Some(found) = cache.get(source) {
        return *found;
    }
    let compiled = Regex::new(&python_dollar(source))
        .ok()
        .map(|r| &*Box::leak(Box::new(r)));
    cache.insert(source.to_string(), compiled);
    compiled
}

/// The first error jsonschema's `iter_errors` yields for `node`, as the
/// notice phrases it: the located path, then the detail. Keywords are visited
/// in the schema's own key order, depth first. Composite failures name the
/// keyword and not the offending value (a dumped node is an unplaced blob).
pub fn first_rejection(root: &Json, node: &Node) -> Option<String> {
    let (path, validator, message) = first_error(root, root, node, &mut Vec::new())?;
    let detail = match validator {
        "anyOf" | "oneOf" | "allOf" => format!("does not match any accepted {validator} form"),
        _ => message,
    };
    Some(if path.is_empty() {
        detail
    } else {
        format!("{}: {detail}", path.join("."))
    })
}

type Found = (Vec<String>, &'static str, String);

fn first_error(root: &Json, schema: &Json, node: &Node, at: &mut Vec<String>) -> Option<Found> {
    let s = schema.as_object()?;
    let here = |at: &Vec<String>, v: &'static str, m: String| Some((at.clone(), v, m));
    let value = || node.to_json();
    let ok = |sub: &Json| check(root, false, sub, node);
    // The frozen files are key-sorted, which puts `additionalProperties` ahead of
    // `properties`; production validates its live schema, where the declared
    // fields come first.
    let (extras, rest): (Vec<_>, Vec<_>) =
        s.iter().partition(|(k, _)| *k == "additionalProperties");
    for (keyword, arg) in rest.into_iter().chain(extras) {
        match keyword.as_str() {
            "$ref" => {
                if let Some(t) = arg.as_str().and_then(|r| resolve(root, r)) {
                    if let Some(f) = first_error(root, t, node, at) {
                        return Some(f);
                    }
                }
            }
            "type" => {
                let types: Vec<&str> = match arg {
                    Json::String(t) => vec![t],
                    Json::Array(ts) => ts.iter().filter_map(Json::as_str).collect(),
                    _ => Vec::new(),
                };
                if !types.is_empty() && !types.iter().any(|t| type_is(t, node)) {
                    let reprs: Vec<String> = types.iter().map(|t| repr_str(t)).collect();
                    return here(
                        at,
                        "type",
                        format!(
                            "{} is not of type {}",
                            repr_json(&value()),
                            reprs.join(", ")
                        ),
                    );
                }
            }
            "enum"
                if arg
                    .as_array()
                    .is_some_and(|e| !e.iter().any(|o| equals(o, node))) =>
            {
                return here(
                    at,
                    "enum",
                    format!("{} is not one of {}", repr_json(&value()), repr_json(arg)),
                );
            }
            "const" if !equals(arg, node) => {
                return here(at, "const", format!("{} was expected", repr_json(arg)));
            }
            "allOf" => {
                for sub in arg.as_array().into_iter().flatten() {
                    if let Some(f) = first_error(root, sub, node, at) {
                        return Some(f);
                    }
                }
            }
            "anyOf" | "oneOf" if arg.as_array().is_some_and(|a| !a.iter().any(ok)) => {
                let v = if keyword == "anyOf" { "anyOf" } else { "oneOf" };
                return here(at, v, String::new());
            }
            "not" if ok(arg) => {
                return here(
                    at,
                    "not",
                    format!(
                        "{} should not be valid under {}",
                        repr_json(&value()),
                        repr_json(arg)
                    ),
                );
            }
            "if" => {
                let branch = if ok(arg) {
                    s.get("then")
                } else {
                    s.get("else")
                };
                if let Some(b) = branch {
                    if let Some(f) = first_error(root, b, node, at) {
                        return Some(f);
                    }
                }
            }
            "properties" => {
                if let (Kind::Map(m), Some(props)) = (&node.kind, arg.as_object()) {
                    for (k, sub) in props {
                        if let Some(e) = m.get(k) {
                            at.push(k.clone());
                            let f = first_error(root, sub, &e.value, at);
                            at.pop();
                            if f.is_some() {
                                return f;
                            }
                        }
                    }
                }
            }
            "required" => {
                if let Kind::Map(m) = &node.kind {
                    if let Some(missing) = arg
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Json::as_str)
                        .find(|k| !m.contains_key(*k))
                    {
                        return here(
                            at,
                            "required",
                            format!("{} is a required property", repr_str(missing)),
                        );
                    }
                }
            }
            "additionalProperties" => {
                if let Kind::Map(m) = &node.kind {
                    let props = s.get("properties").and_then(Json::as_object);
                    let mut extras: Vec<&String> = m
                        .keys()
                        .filter(|k| !props.is_some_and(|p| p.contains_key(*k)))
                        .collect();
                    if arg.is_object() {
                        for k in extras {
                            at.push(k.clone());
                            let f = m.get(k).and_then(|e| first_error(root, arg, &e.value, at));
                            at.pop();
                            if f.is_some() {
                                return f;
                            }
                        }
                    } else if arg == &Json::Bool(false) && !extras.is_empty() {
                        extras.sort();
                        let verb = if extras.len() == 1 { "was" } else { "were" };
                        let joined = extras
                            .iter()
                            .map(|e| repr_str(e))
                            .collect::<Vec<_>>()
                            .join(", ");
                        return here(at, "additionalProperties", format!("Additional properties are not allowed ({joined} {verb} unexpected)"));
                    }
                }
            }
            "items" => {
                if let (Kind::Seq(items), true) = (&node.kind, arg.is_object()) {
                    let skip = s
                        .get("prefixItems")
                        .and_then(Json::as_array)
                        .map_or(0, Vec::len);
                    for (i, item) in items.iter().enumerate().skip(skip) {
                        at.push(i.to_string());
                        let f = first_error(root, arg, item, at);
                        at.pop();
                        if f.is_some() {
                            return f;
                        }
                    }
                }
            }
            "prefixItems" => {
                if let (Kind::Seq(items), Some(subs)) = (&node.kind, arg.as_array()) {
                    for (i, (item, sub)) in items.iter().zip(subs).enumerate() {
                        at.push(i.to_string());
                        let f = first_error(root, sub, item, at);
                        at.pop();
                        if f.is_some() {
                            return f;
                        }
                    }
                }
            }
            other => {
                if let Some(f) = bound_error(other, arg, node, at) {
                    return Some(f);
                }
            }
        }
    }
    None
}

/// Size, range and pattern keywords: the leaf checks with their jsonschema text.
fn bound_error(keyword: &str, arg: &Json, node: &Node, at: &[String]) -> Option<Found> {
    let v = repr_json(&node.to_json());
    let fail = |name: &'static str, text: String| Some((at.to_vec(), name, text));
    let limit = repr_json(arg);
    match (&node.kind, keyword) {
        (Kind::Seq(items), "minItems") if (items.len() as u64) < arg.as_u64()? => fail(
            "minItems",
            format!(
                "{v} {}",
                if arg.as_u64() == Some(1) {
                    "should be non-empty"
                } else {
                    "is too short"
                }
            ),
        ),
        (Kind::Seq(items), "maxItems") if (items.len() as u64) > arg.as_u64()? => {
            fail("maxItems", format!("{v} is too long"))
        }
        (Kind::Map(m), "minProperties") if (m.len() as u64) < arg.as_u64()? => fail(
            "minProperties",
            format!(
                "{v} {}",
                if arg.as_u64() == Some(1) {
                    "should be non-empty"
                } else {
                    "does not have enough properties"
                }
            ),
        ),
        (Kind::Scalar(Value::Str(t)), "minLength")
            if (t.chars().count() as u64) < arg.as_u64()? =>
        {
            fail(
                "minLength",
                format!(
                    "{v} {}",
                    if arg.as_u64() == Some(1) {
                        "should be non-empty"
                    } else {
                        "is too short"
                    }
                ),
            )
        }
        (Kind::Scalar(Value::Str(t)), "pattern") => {
            let p = arg.as_str()?;
            (!pattern(p).is_some_and(|re| re.is_match(t).unwrap_or(false))).then(|| {
                (
                    at.to_vec(),
                    "pattern",
                    format!("{v} does not match {}", limit),
                )
            })
        }
        (Kind::Scalar(Value::Number(n)), k) => {
            let (x, b) = (n.as_f64()?, arg.as_f64()?);
            match k {
                "minimum" if x < b => fail(
                    "minimum",
                    format!("{v} is less than the minimum of {limit}"),
                ),
                "maximum" if x > b => fail(
                    "maximum",
                    format!("{v} is greater than the maximum of {limit}"),
                ),
                "exclusiveMinimum" if x <= b => fail(
                    "exclusiveMinimum",
                    format!("{v} is less than or equal to the minimum of {limit}"),
                ),
                "exclusiveMaximum" if x >= b => fail(
                    "exclusiveMaximum",
                    format!("{v} is greater than or equal to the maximum of {limit}"),
                ),
                _ => None,
            }
        }
        _ => None,
    }
}

/// Python's `$` also matches before a trailing newline: a bare `$` that ends a
/// pattern or a group or an alternative becomes `(?=\n?\z)`.
fn python_dollar(source: &str) -> String {
    let chars: Vec<char> = source.chars().collect();
    let mut out = String::new();
    let mut escaped = false;
    let mut class = false;
    for (i, c) in chars.iter().enumerate() {
        match (*c, escaped, class) {
            ('$', false, false) if chars.get(i + 1).is_none_or(|n| matches!(n, ')' | '|')) => {
                out.push_str("(?=\\n?\\z)");
            }
            _ => out.push(*c),
        }
        class = match (*c, escaped) {
            ('[', false) => true,
            (']', false) => false,
            _ => class,
        };
        escaped = *c == '\\' && !escaped;
    }
    out
}

#[cfg(test)]
pub(crate) fn pattern_compiles(source: &str) -> bool {
    Regex::new(&python_dollar(source)).is_ok()
}

//! A model's declared [`Constraint`]s, evaluated over a value's keys. The JSON Schema states the
//! same table (`schema`); this reads it for a value already built from several layers, so a rule
//! is written once. All nine kinds are answered over a [`Doc`], the authored JSON of a value; a
//! typed view that implements [`Keyed`] with `state` alone answers the kinds that read whether a
//! key is set, and the rest are [`Unanswerable`] for it.

use crate::table::{Condition, Constraint, ConstraintKind, Value};
use serde_json::Value as Json;
use std::fmt;

/// A key of an authored value: not written, written as `null`, or given a value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Unset,
    Null,
    Set,
}

/// The keys of an authored value, by the names its constraints use.
pub trait Keyed {
    /// `None` when the value has no such key.
    fn state(&self, key: &str) -> Option<State>;

    /// The JSON the key holds. `None` when the key is not set, or when the view reads no values
    /// (a rule that needs one is then [`Unanswerable`]).
    fn value(&self, _key: &str) -> Option<Json> {
        None
    }
}

/// A constraint the view cannot answer: it reads more than whether a key is set.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unanswerable(pub String);

impl fmt::Display for Unanswerable {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for Unanswerable {}

/// The authored JSON of a value. A key is a dotted path through objects (a non-object has no
/// keys); `a[*].b` reaches `b` of every item of the list `a`.
#[derive(Debug, Clone, Copy)]
pub struct Doc<'a>(pub &'a Json);

fn step<'a>(at: &'a Json, part: &str) -> Option<&'a Json> {
    at.as_object()?.get(part)
}

impl Doc<'_> {
    /// Every value the path reaches: one for a plain path, one per list item at each `[*]`.
    pub fn reach(&self, key: &str) -> Vec<&Json> {
        let mut at = vec![self.0];
        for part in key.split('.') {
            let (name, each) = match part.strip_suffix("[*]") {
                Some(name) => (name, true),
                None => (part, false),
            };
            at = at.into_iter().filter_map(|v| step(v, name)).collect();
            if each {
                at = at
                    .into_iter()
                    .filter_map(Json::as_array)
                    .flatten()
                    .collect();
            }
        }
        at
    }
}

impl Keyed for Doc<'_> {
    fn state(&self, key: &str) -> Option<State> {
        Some(match self.reach(key).first() {
            None => State::Unset,
            Some(Json::Null) => State::Null,
            Some(_) => State::Set,
        })
    }

    fn value(&self, key: &str) -> Option<Json> {
        let found = self.reach(key);
        if key.contains("[*]") {
            return Some(Json::Array(found.into_iter().cloned().collect()));
        }
        found.first().map(|v| (*v).clone())
    }
}

fn state(view: &dyn Keyed, key: &str) -> Result<State, Unanswerable> {
    view.state(key)
        .ok_or_else(|| Unanswerable(format!("the value has no key `{key}`")))
}

fn value(view: &dyn Keyed, key: &str) -> Result<Option<Json>, Unanswerable> {
    if state(view, key)? == State::Unset {
        return Ok(None);
    }
    view.value(key)
        .map(Some)
        .ok_or_else(|| Unanswerable(format!("the view reads no value for `{key}`")))
}

fn nonempty(v: &Json) -> bool {
    match v {
        Json::String(s) => !s.is_empty(),
        Json::Array(a) => !a.is_empty(),
        Json::Object(o) => !o.is_empty(),
        _ => true,
    }
}

/// Whether the key counts as set under the constraint's modifiers: written, not `null`, not
/// `auto` where the engine decides it, and not empty where the rule says it must not be.
fn is_set(c: &Constraint, view: &dyn Keyed, key: &str) -> Result<bool, Unanswerable> {
    if state(view, key)? != State::Set {
        return Ok(false);
    }
    let (empty, auto) = (c.nonempty.contains(&key), c.auto.contains(&key));
    if !empty && !auto {
        return Ok(true);
    }
    let v = value(view, key)?.unwrap_or(Json::Null);
    Ok(!(auto && v == "auto") && (!empty || nonempty(&v)))
}

fn same(v: &Json, want: &Value) -> bool {
    match (v, want) {
        (Json::Null, Value::Null) => true,
        (Json::Bool(a), Value::Bool(b)) => a == b,
        (Json::String(a), Value::Str(b)) => a == b,
        (Json::Number(a), Value::Int(b)) => a.as_f64() == Some(*b as f64),
        (Json::Number(a), Value::Float(b)) => a.as_f64() == Some(*b),
        _ => false,
    }
}

fn is_call(v: &Json, names: &[&str]) -> bool {
    match v {
        Json::String(s) => s
            .trim()
            .strip_suffix(')')
            .and_then(|s| s.trim_end().strip_suffix('('))
            .is_some_and(|name| names.contains(&name.trim())),
        Json::Object(o) => o
            .get("fn")
            .and_then(Json::as_str)
            .is_some_and(|f| names.contains(&f)),
        _ => false,
    }
}

fn holds(when: &[Condition], view: &dyn Keyed) -> Result<bool, Unanswerable> {
    for c in when {
        let met = match c {
            Condition::IsSet(key) => state(view, key)? == State::Set,
            Condition::ValueIn {
                key,
                values,
                negate,
            } => {
                let v = value(view, key)?.unwrap_or(Json::Null);
                values.iter().any(|w| same(&v, w)) != *negate
            }
            Condition::IsCall { key, names, negate } => {
                let v = value(view, key)?.unwrap_or(Json::Null);
                is_call(&v, names) != *negate
            }
            Condition::IsList(key) => matches!(value(view, key)?, Some(Json::Array(_))),
        };
        if !met {
            return Ok(false);
        }
    }
    Ok(true)
}

fn count_set(c: &Constraint, view: &dyn Keyed) -> Result<usize, Unanswerable> {
    let mut n = 0;
    for key in c.fields {
        n += usize::from(is_set(c, view, key)?);
    }
    Ok(n)
}

fn in_bounds(n: f64, b: &crate::table::Bounds) -> bool {
    b.gt.is_none_or(|x| n > x)
        && b.ge.is_none_or(|x| n >= x)
        && b.lt.is_none_or(|x| n < x)
        && b.le.is_none_or(|x| n <= x)
}

fn first_field(c: &Constraint) -> &'static str {
    c.fields.first().copied().unwrap_or_default()
}

fn range(c: &Constraint, view: &dyn Keyed) -> Result<bool, Unanswerable> {
    let v = value(view, first_field(c))?.unwrap_or(Json::Null);
    let numbers = match &v {
        Json::Array(items) => items.iter().filter_map(Json::as_f64).collect(),
        one => one.as_f64().into_iter().collect::<Vec<_>>(),
    };
    Ok(numbers.into_iter().all(|n| in_bounds(n, &c.bounds)))
}

fn compiled(c: &Constraint) -> Result<regex::Regex, Unanswerable> {
    let pattern = c
        .pattern
        .ok_or_else(|| Unanswerable("`items_match` names no pattern".to_string()))?;
    regex::Regex::new(&format!(r"\A(?:{pattern})\z"))
        .map_err(|e| Unanswerable(format!("pattern `{pattern}` does not compile: {e}")))
}

/// The first item of an `items_match` list that is no string fully matching its pattern.
pub fn bad_item(c: &Constraint, view: &dyn Keyed) -> Result<Option<Json>, Unanswerable> {
    let Some(Json::Array(items)) = value(view, first_field(c))? else {
        return Ok(None);
    };
    let re = compiled(c)?;
    Ok(items
        .into_iter()
        .find(|i| !i.as_str().is_some_and(|s| re.is_match(s))))
}

/// A value two lists of a `unique` constraint share, and the two that hold it. A list reached by
/// `[*]` is named by its path and item index.
#[derive(Debug, Clone, PartialEq)]
pub struct Clash {
    pub value: Json,
    pub first: String,
    pub second: String,
}

/// The first value a `unique` constraint's lists repeat, in the order they are named.
pub fn clash(c: &Constraint, view: &dyn Keyed) -> Result<Option<Clash>, Unanswerable> {
    let mut seen: Vec<(Json, String)> = Vec::new();
    for key in c.fields {
        let Some(Json::Array(items)) = value(view, key)? else {
            continue;
        };
        // A `[*]` path yields one list per item: its items are the values.
        let lists = match key.contains("[*]") {
            true => items
                .into_iter()
                .enumerate()
                .filter_map(|(i, l)| match l {
                    Json::Array(inner) => Some((
                        format!("{}[{i}]", key.split("[*]").next().unwrap_or_default()),
                        inner,
                    )),
                    _ => None,
                })
                .collect(),
            false => vec![((*key).to_string(), items)],
        };
        for (owner, inner) in lists {
            for item in inner {
                if let Some((_, first)) = seen.iter().find(|(v, _)| *v == item) {
                    return Ok(Some(Clash {
                        value: item,
                        first: first.clone(),
                        second: owner,
                    }));
                }
                seen.push((item, owner.clone()));
            }
        }
    }
    Ok(None)
}

/// The constraint's keys that are set, in the order it names them.
pub fn set_keys<'a>(c: &'a Constraint, view: &dyn Keyed) -> Result<Vec<&'a str>, Unanswerable> {
    let mut out = Vec::new();
    for key in c.fields {
        if is_set(c, view, key)? {
            out.push(*key);
        }
    }
    Ok(out)
}

/// Whether `c` holds over `view`. A kind that reads a value (`value_in`, `range`, ..), or a
/// `nonempty` / `auto` modifier, is [`Unanswerable`] for a view that reads no values: a rule is
/// never skipped.
pub fn satisfied(c: &Constraint, view: &dyn Keyed) -> Result<bool, Unanswerable> {
    if !holds(c.when, view)? {
        return Ok(true);
    }
    Ok(match c.kind {
        ConstraintKind::Requires => count_set(c, view)? == c.fields.len(),
        ConstraintKind::Forbids => count_set(c, view)? == 0,
        ConstraintKind::AtLeastOneOf => count_set(c, view)? >= 1,
        ConstraintKind::AtMostOneOf => count_set(c, view)? <= 1,
        ConstraintKind::NotNull => {
            let mut ok = true;
            for key in c.fields {
                ok &= state(view, key)? != State::Null;
            }
            ok
        }
        ConstraintKind::ValueIn => match value(view, first_field(c))? {
            None | Some(Json::Null) => true,
            Some(v) => c.values.iter().any(|w| same(&v, w)),
        },
        ConstraintKind::Range => range(c, view)?,
        ConstraintKind::ItemsMatch => bad_item(c, view)?.is_none(),
        ConstraintKind::Unique => clash(c, view)?.is_none(),
    })
}

/// The first constraint of `constraints`, in declaration order, that `view` breaks.
pub fn first_violation<'a>(
    constraints: &'a [Constraint],
    view: &dyn Keyed,
) -> Result<Option<&'a Constraint>, Unanswerable> {
    for c in constraints {
        if !satisfied(c, view)? {
            return Ok(Some(c));
        }
    }
    Ok(None)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use crate::table::{Bounds, Value};

    struct Keys(&'static [(&'static str, State)]);

    impl Keyed for Keys {
        fn state(&self, key: &str) -> Option<State> {
            self.0.iter().find(|(k, _)| *k == key).map(|(_, s)| *s)
        }
    }

    const fn rule(kind: ConstraintKind, fields: &'static [&'static str]) -> Constraint {
        Constraint {
            kind,
            fields,
            when: &[],
            nonempty: &[],
            auto: &[],
            values: &[],
            pattern: None,
            bounds: Bounds::NONE,
            message: None,
            code: None,
        }
    }

    #[test]
    fn a_null_key_is_not_set_and_a_rule_under_an_unmet_when_holds() {
        let view = Keys(&[("a", State::Null), ("b", State::Set), ("c", State::Unset)]);
        let at_least = rule(ConstraintKind::AtLeastOneOf, &["a", "c"]);
        let at_most = rule(ConstraintKind::AtMostOneOf, &["a", "b"]);
        let requires = Constraint {
            when: &[Condition::IsSet("c")],
            ..rule(ConstraintKind::Requires, &["a"])
        };
        assert_eq!(satisfied(&at_least, &view), Ok(false));
        assert_eq!(satisfied(&at_most, &view), Ok(true));
        assert_eq!(satisfied(&requires, &view), Ok(true));
        let rules = [at_most, at_least];
        let first = first_violation(&rules, &view).map(|c| c.map(|c| c.kind));
        assert_eq!(first, Ok(Some(ConstraintKind::AtLeastOneOf)));
    }

    fn doc(json: &str) -> Doc<'static> {
        let v: &'static serde_json::Value =
            Box::leak(Box::new(serde_json::from_str(json).unwrap()));
        Doc(v)
    }

    const fn with_when(c: Constraint, when: &'static [Condition]) -> Constraint {
        Constraint { when, ..c }
    }

    #[test]
    fn requires_and_forbids_read_a_document_with_dotted_keys_and_nonempty() {
        let requires = rule(ConstraintKind::Requires, &["font.size"]);
        assert_eq!(
            satisfied(&requires, &doc(r#"{"font": {"size": 1}}"#)),
            Ok(true)
        );
        assert_eq!(satisfied(&requires, &doc(r#"{"font": {}}"#)), Ok(false));
        assert_eq!(satisfied(&requires, &doc(r#"{"font": null}"#)), Ok(false));
        let nonempty = Constraint {
            nonempty: &["a"],
            ..rule(ConstraintKind::Forbids, &["a"])
        };
        assert_eq!(satisfied(&nonempty, &doc(r#"{"a": []}"#)), Ok(true));
        assert_eq!(satisfied(&nonempty, &doc(r#"{"a": [1]}"#)), Ok(false));
        assert_eq!(satisfied(&nonempty, &doc(r#"{"a": ""}"#)), Ok(true));
    }

    #[test]
    fn at_most_one_of_counts_auto_as_unset() {
        let c = Constraint {
            auto: &["count"],
            ..rule(ConstraintKind::AtMostOneOf, &["time_unit", "count"])
        };
        assert_eq!(
            satisfied(&c, &doc(r#"{"time_unit": "day", "count": "auto"}"#)),
            Ok(true)
        );
        assert_eq!(
            satisfied(&c, &doc(r#"{"time_unit": "day", "count": 3}"#)),
            Ok(false)
        );
    }

    #[test]
    fn not_null_refuses_a_written_null_and_accepts_an_omitted_key() {
        let c = rule(ConstraintKind::NotNull, &["y_start"]);
        assert_eq!(satisfied(&c, &doc("{}")), Ok(true));
        assert_eq!(satisfied(&c, &doc(r#"{"y_start": null}"#)), Ok(false));
        assert_eq!(satisfied(&c, &doc(r#"{"y_start": "a"}"#)), Ok(true));
    }

    #[test]
    fn value_in_reads_the_value_and_ignores_an_unset_key() {
        const VALUES: &[Value] = &[Value::Int(500), Value::Int(600)];
        let c = Constraint {
            values: VALUES,
            ..rule(ConstraintKind::ValueIn, &["brand_weight"])
        };
        assert_eq!(satisfied(&c, &doc("{}")), Ok(true));
        assert_eq!(satisfied(&c, &doc(r#"{"brand_weight": 600}"#)), Ok(true));
        assert_eq!(satisfied(&c, &doc(r#"{"brand_weight": 700}"#)), Ok(false));
    }

    #[test]
    fn range_bounds_a_number_or_each_number_of_a_list() {
        let c = Constraint {
            bounds: Bounds {
                gt: Some(0.0),
                ge: None,
                lt: None,
                le: Some(1.0),
            },
            ..rule(ConstraintKind::Range, &["w"])
        };
        assert_eq!(satisfied(&c, &doc(r#"{"w": 1}"#)), Ok(true));
        assert_eq!(satisfied(&c, &doc(r#"{"w": 0}"#)), Ok(false));
        assert_eq!(satisfied(&c, &doc(r#"{"w": 1.5}"#)), Ok(false));
        assert_eq!(satisfied(&c, &doc(r#"{"w": [0.5, 2]}"#)), Ok(false));
        assert_eq!(satisfied(&c, &doc(r#"{"w": "a variable"}"#)), Ok(true));
        assert_eq!(satisfied(&c, &doc("{}")), Ok(true));
    }

    #[test]
    fn items_match_checks_each_string_of_a_list_under_its_conditions() {
        const WHEN: &[Condition] = &[Condition::ValueIn {
            key: "type",
            values: &[Value::Str("temporal")],
            negate: false,
        }];
        let c = Constraint {
            pattern: Some("^[0-9]{4}$"),
            ..with_when(rule(ConstraintKind::ItemsMatch, &["domain"]), WHEN)
        };
        assert_eq!(
            satisfied(&c, &doc(r#"{"type": "temporal", "domain": ["2020"]}"#)),
            Ok(true)
        );
        assert_eq!(
            satisfied(&c, &doc(r#"{"type": "temporal", "domain": ["2020", "x"]}"#)),
            Ok(false)
        );
        assert_eq!(
            satisfied(&c, &doc(r#"{"type": "temporal", "domain": [2020]}"#)),
            Ok(false)
        );
        assert_eq!(
            satisfied(&c, &doc(r#"{"type": "linear", "domain": ["x"]}"#)),
            Ok(true)
        );
        assert_eq!(satisfied(&c, &doc(r#"{"type": "temporal"}"#)), Ok(true));
    }

    #[test]
    fn unique_reads_the_union_of_the_named_lists() {
        let c = rule(ConstraintKind::Unique, &["rows", "columns"]);
        assert_eq!(
            satisfied(&c, &doc(r#"{"rows": ["a"], "columns": ["b"]}"#)),
            Ok(true)
        );
        assert_eq!(
            satisfied(&c, &doc(r#"{"rows": ["a"], "columns": ["a"]}"#)),
            Ok(false)
        );
        assert_eq!(satisfied(&c, &doc(r#"{"rows": ["a", "a"]}"#)), Ok(false));
        let spanners = rule(ConstraintKind::Unique, &["spanners[*].columns"]);
        let ok = doc(r#"{"spanners": [{"columns": ["a"]}, {"columns": ["b"]}]}"#);
        let dup = doc(r#"{"spanners": [{"columns": ["a"]}, {"columns": ["a"]}]}"#);
        assert_eq!(satisfied(&spanners, &ok), Ok(true));
        assert_eq!(satisfied(&spanners, &dup), Ok(false));
    }

    #[test]
    fn the_offending_item_and_the_shared_value_are_named() {
        let items = Constraint {
            pattern: Some("^[0-9]{4}$"),
            ..rule(ConstraintKind::ItemsMatch, &["domain"])
        };
        let bad = bad_item(&items, &doc(r#"{"domain": ["2020", 7, "x"]}"#));
        assert_eq!(bad, Ok(Some(serde_json::json!(7))));
        let c = rule(ConstraintKind::Unique, &["rows", "columns"]);
        let found = clash(&c, &doc(r#"{"rows": ["a"], "columns": ["b", "a"]}"#));
        let want = Clash {
            value: "a".into(),
            first: "rows".into(),
            second: "columns".into(),
        };
        assert_eq!(found, Ok(Some(want)));
        let spanners = rule(ConstraintKind::Unique, &["spanners[*].columns"]);
        let dup = doc(r#"{"spanners": [{"columns": ["a"]}, {"columns": ["a"]}]}"#);
        let found = clash(&spanners, &dup).unwrap().unwrap();
        assert_eq!(
            (found.first.as_str(), found.second.as_str()),
            ("spanners[0]", "spanners[1]")
        );
        let two = rule(ConstraintKind::AtMostOneOf, &["a", "b", "c"]);
        assert_eq!(
            set_keys(&two, &doc(r#"{"a": 1, "c": 2}"#)),
            Ok(vec!["a", "c"])
        );
    }

    #[test]
    fn conditions_read_values_calls_and_lists() {
        const WHEN: &[Condition] = &[
            Condition::IsCall {
                key: "x",
                names: &["min", "max"],
                negate: true,
            },
            Condition::IsList("y"),
            Condition::ValueIn {
                key: "kind",
                values: &[Value::Null],
                negate: false,
            },
        ];
        let c = with_when(rule(ConstraintKind::Forbids, &["enclose"]), WHEN);
        let base = |x: &str| doc(&format!(r#"{{"x": {x}, "y": [1], "enclose": "rect"}}"#));
        assert_eq!(satisfied(&c, &base(r#""min()""#)), Ok(true));
        assert_eq!(satisfied(&c, &base(r#"{"fn": "max"}"#)), Ok(true));
        assert_eq!(satisfied(&c, &base(r#""a""#)), Ok(false));
        assert_eq!(
            satisfied(&c, &doc(r#"{"x": "a", "y": 1, "enclose": 1}"#)),
            Ok(true)
        );
    }

    #[test]
    fn a_view_that_cannot_read_values_is_unanswerable_for_a_kind_that_reads_them() {
        let view = Keys(&[("a", State::Set)]);
        assert!(satisfied(&rule(ConstraintKind::Range, &["a"]), &view).is_err());
        let nonempty = Constraint {
            nonempty: &["a"],
            ..rule(ConstraintKind::Requires, &["a"])
        };
        assert!(satisfied(&nonempty, &view).is_err());
        assert!(satisfied(&rule(ConstraintKind::Requires, &["missing"]), &view).is_err());
    }
}

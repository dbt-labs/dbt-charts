//! The constraints pass: every `constraint` a model declares, checked on a board file as its
//! author wrote it, before the fold. A file's own mappings are what Python validates one at a
//! time, so a rule that reads two keys sees both only when one mapping holds both.
//!
//! The walk is the schema's: each struct a document holds is visited with its flat constraint
//! table (`Schema::structs`), and `model_core::law` answers each rule over the struct's authored
//! JSON. A violation is `ERR-VALIDATION-FIELD`, as Python's:
//! - a bound or closed set on one field (`range`, `value_in`) is that field's, worded as the
//!   validation library words it; a model's other fields are checked beside it, all of them;
//! - every other rule is the model's own, carries the message its declaration states, and only
//!   the first a model breaks is reported, after its fields all passed.
//!
//! Some rules are left to a later stage. A query mapping is `hoist`'s, which names the query in
//! its message. Two more wait because a lone file may leave out what another file says:
//! what a `COMPLETED_BY_BUILD` model requires is what the completed style must hold (Python asks
//! it of the style it builds, never of one layer; the build refuses a leaf no layer set), and a
//! board's own `at_least_one_of` layout, text, title or charts is asked of the merged board
//! (`hoist`).
//!
//! A `MERGED_ONLY` model's rules are asked by [`refuse_merged`] instead, of each placement's
//! merged style: a theme may set `type: log` while a chart sets only `log.base`. A violation sits
//! at the key of the last layer that set one of the rule's keys: the chart's own `style:`, else
//! the board's `style`. A nested board's or tab's own layer is reported at the board's `style`.

use crate::compile::Structure;
use board_model::style::style::Style;
use diagnostic::diag::{err_internal, err_theme_and_extends, err_validation_field};
use diagnostic::{py_repr, Diagnostic, ModelPath};
use model_core::schema::Schema;
use model_core::{
    bad_item, clash, satisfied, set_keys, Condition, Constraint, ConstraintKind, Doc, Unanswerable,
    Value as Scalar,
};
use model_core::{spell_leaves, Leaf, Resolved};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::OnceLock;
use yaml::Node;

struct Spine {
    schema: Schema,
}

const COMPLETED_BY_BUILD: [&str; 3] = ["FooterStyle", "TimestampStyle", "SparkEmptyStyle"];

fn spine() -> Result<&'static Spine, String> {
    static SPINE: OnceLock<Result<Spine, String>> = OnceLock::new();
    SPINE
        .get_or_init(|| {
            let schema = Schema::of::<board_model::board::Board>().map_err(|e| format!("{e:?}"))?;
            Ok(Spine { schema })
        })
        .as_ref()
        .map_err(Clone::clone)
}

/// A constraint whose violation Python reports under a code of its own: the board's `theme:`
/// beside `extends:`, which Python rewrites before it validates.
fn is_theme_and_extends(c: &Constraint) -> bool {
    c.kind == ConstraintKind::AtMostOneOf && c.fields == ["theme", "extends"]
}

/// A query mapping is judged by `hoist` (`QueryAuthored::violation`), which names the query.
const QUERY_MODELS: [&str; 3] = ["SqlQuery", "ValuesQuery", "SchemaQuery"];

/// Style models whose rules Python asks of the merged value, never of one layer: a theme may set
/// `type: log` while a chart sets only `log.base`. Only a domain's date pattern is per layer.
const MERGED_ONLY: [&str; 3] = [
    "ScaleContinuousStyle",
    "DimensionTicksStyle",
    "AxisMirrorStyle",
];

fn later(model: &str, c: &Constraint) -> bool {
    if QUERY_MODELS.contains(&model) {
        return true;
    }
    if MERGED_ONLY.contains(&model) && c.kind != ConstraintKind::ItemsMatch {
        return true;
    }
    match c.kind {
        ConstraintKind::Requires => COMPLETED_BY_BUILD.contains(&model),
        ConstraintKind::AtLeastOneOf => c.fields.contains(&"rows") && c.fields.contains(&"charts"),
        _ => false,
    }
}

/// Whether a violation is the field's own: a bound or closed set that states no message.
pub fn is_field_rule(c: &Constraint) -> bool {
    matches!(c.kind, ConstraintKind::Range | ConstraintKind::ValueIn) && c.message.is_none()
}

/// Whether `c` reads its model's one field and says so itself: `requires(f, nonempty(f))`.
fn is_required_field(c: &Constraint) -> bool {
    c.kind == ConstraintKind::Requires
        && c.when.is_empty()
        && c.fields.len() == 1
        && c.nonempty == c.fields
        && c.message.is_none()
}

/// Whether the constraint words its own violation (or the pass words it by a rule above).
pub fn states_itself(model: &str, c: &Constraint) -> bool {
    c.message.is_some()
        || c.code.is_some()
        || is_field_rule(c)
        || is_required_field(c)
        || is_theme_and_extends(c)
        || later(model, c)
}

/// Every refusal in `node`, a board file's own document, outermost model first.
pub(crate) fn refuse(node: &Node) -> Vec<Diagnostic> {
    let spine = match spine() {
        Ok(spine) => spine,
        Err(e) => {
            return vec![err_internal(format!(
                "the model schema does not build: {e}"
            ))]
        }
    };
    // `(path, cross_field, diagnostics)` per struct, outermost first.
    let mut found: Vec<(String, bool, Vec<Diagnostic>)> = Vec::new();
    for visit in spine.schema.structs(node) {
        if visit.flat.constraints.is_empty() {
            continue;
        }
        let json = spine.schema.authored_json(visit.model, visit.node);
        let doc = Doc(&json);
        let rules: Vec<&Constraint> = visit
            .flat
            .constraints
            .iter()
            .copied()
            .filter(|c| !later(visit.model, c))
            .collect();
        match check(&visit.path, &rules, &doc) {
            Ok((cross, d)) => found.push((visit.path, cross, d)),
            Err(e) => found.push((
                visit.path,
                false,
                vec![err_internal(format!(
                    "model `{}` declares a constraint the engine cannot read: {e}",
                    visit.model
                ))],
            )),
        }
    }
    // Python rewrites `theme:` before it validates anything, so that refusal stands alone.
    if let Some((_, _, d)) = found
        .iter()
        .find(|(at, _, d)| at.is_empty() && d.iter().any(|x| x.code == "ERR-THEME-AND-EXTENDS"))
    {
        return d.clone();
    }
    // Python asks a model's own rules only after all its fields, children included, passed.
    let below = |at: &str, other: &str| match at.is_empty() {
        true => !other.is_empty(),
        false => other.starts_with(&format!("{at}.")),
    };
    let broken: Vec<&str> = found
        .iter()
        .filter(|(_, _, d)| !d.is_empty())
        .map(|(p, _, _)| p.as_str())
        .collect();
    let skipped: Vec<bool> = found
        .iter()
        .map(|(at, cross, _)| *cross && broken.iter().any(|b| below(at, b)))
        .collect();
    found
        .into_iter()
        .zip(skipped)
        .filter(|(_, skip)| !skip)
        .flat_map(|((_, _, d), _)| d)
        .collect()
}

/// The keys a rule reads: the ones it names, then the ones its `when` conditions read.
fn rule_keys(c: &Constraint) -> Vec<&'static str> {
    let mut keys: Vec<&'static str> = c.fields.to_vec();
    for w in c.when {
        keys.push(match w {
            Condition::IsSet(key) | Condition::IsList(key) => key,
            Condition::ValueIn { key, .. } | Condition::IsCall { key, .. } => key,
        });
    }
    keys
}

/// `upper` over `base`, key by key through objects.
fn overlay(base: &mut Value, upper: Value) {
    match (base, upper) {
        (Value::Object(b), Value::Object(u)) => {
            for (k, v) in u {
                match b.get_mut(&k) {
                    Some(at) => overlay(at, v),
                    None => {
                        b.insert(k, v);
                    }
                }
            }
        }
        (b, u) if !u.is_null() => *b = u,
        _ => {}
    }
}

fn at<'a>(json: &'a Value, path: &[&str]) -> Option<&'a Value> {
    path.iter().try_fold(json, |at, key| at.get(key))
}

/// Whether `json` holds a value at the dotted `path`.
fn holds(json: &Value, path: &str) -> bool {
    !Doc(json).reach(path).is_empty()
}

/// One rung of an axis's cascade: what it sets, and where it is authored.
struct Rung {
    set: Value,
    authored: String,
}

/// The rungs of one axis in Python's order (`resolved_axis_style`, layers 1-6 and 8-10): the
/// scope's `charts.axis`, `charts.<axis>` and `charts.<t>`, then the family's three, then the
/// chart's own three. `t` is the channel's slot (`axis_quantitative` or `axis_band`), or none on
/// an axis that has none (temporal).
fn rungs(
    scope: &Value,
    own: &Value,
    (family, own_slot, name): (&str, &str, &str),
    axis: &str,
    t: Option<&str>,
) -> Vec<Rung> {
    let slots: Vec<&str> = ["axis", axis].into_iter().chain(t).collect();
    let mut out = Vec::new();
    for slot in &slots {
        out.push((at(scope, &["charts", slot]), format!("style.charts.{slot}")));
    }
    for slot in &slots {
        out.push((
            at(scope, &["charts", family, slot]),
            format!("style.charts.{family}.{slot}"),
        ));
    }
    for slot in &slots {
        out.push((
            at(own, &["charts", own_slot, slot]),
            format!("charts.{name}.style.{slot}"),
        ));
    }
    out.into_iter()
        .filter_map(|(set, authored)| {
            set.map(|set| Rung {
                set: set.clone(),
                authored,
            })
        })
        .collect()
}

/// A `MERGED_ONLY` rule a merged axis breaks: the struct it sits in (by path), its place in that
/// struct's table, the rule and the struct's authored JSON.
type Broken = (String, usize, &'static Constraint, Value);

/// Every `MERGED_ONLY` rule a struct of `rungs`' merged `axis` breaks.
fn broken(spine: &'static Spine, axis: &str, rungs: &[Rung]) -> Result<Vec<Broken>, Diagnostic> {
    let mut merged = Value::Null;
    for rung in rungs {
        overlay(&mut merged, rung.set.clone());
    }
    let node = migrate::from_json(
        &json!({ "style": { "charts": { axis: merged } } }),
        migrate::SYNTHETIC,
    );
    let mut out = Vec::new();
    for visit in spine.schema.structs(&node) {
        if !MERGED_ONLY.contains(&visit.model) {
            continue;
        }
        let json = spine.schema.authored_json(visit.model, visit.node);
        let doc = Doc(&json);
        for (i, c) in visit.flat.constraints.iter().copied().enumerate() {
            if c.kind == ConstraintKind::ItemsMatch {
                continue;
            }
            match satisfied(c, &doc) {
                Ok(true) => {}
                Ok(false) => {
                    out.push((visit.path.clone(), i, c, json.clone()));
                }
                Err(e) => {
                    return Err(err_internal(format!(
                        "model `{}` declares a constraint the engine cannot read: {e}",
                        visit.model
                    )))
                }
            }
        }
    }
    Ok(out)
}

/// Every `MERGED_ONLY` rule a placement's merged axis style breaks, at the authored key of the
/// last rung that set one of the rule's keys. The channel type picks the `T` rung and normalize
/// has no data, so a rule is reported only when it breaks with `axis_quantitative`, with
/// `axis_band` and with neither: no valid board is refused. `scope_of` is the style a chart's own
/// `style:` folds over; `placed_own` is that own layer.
pub(crate) fn refuse_merged<'a>(
    structure: &Structure,
    scope_of: impl Fn(&str) -> &'a Resolved<Style>,
    placed_own: &BTreeMap<String, <Style as Leaf>::Patch>,
) -> Vec<Diagnostic> {
    let spine = match spine() {
        Ok(spine) => spine,
        Err(e) => {
            return vec![err_internal(format!(
                "the model schema does not build: {e}"
            ))]
        }
    };
    let mut out: Vec<Diagnostic> = Vec::new();
    for (pos, name) in &structure.layout.charts {
        let Some(own) = placed_own.get(pos.as_str()) else {
            continue;
        };
        let Some(Some(chart)) = structure.patch.body.charts.get(name) else {
            continue;
        };
        let Some(own_slot) = chart.family_slot() else {
            continue;
        };
        let family = match chart.family() {
            "histogram" => "histogram",
            _ => own_slot,
        };
        let scope = spell_leaves(&*scope_of(pos.as_str()).folded);
        let own = spell_leaves(own);
        for axis in ["axis_x", "axis_y"] {
            let ladder = |t| rungs(&scope, &own, (family, own_slot, name), axis, t);
            let plain = ladder(None);
            let others = [ladder(Some("axis_quantitative")), ladder(Some("axis_band"))];
            let found = broken(spine, axis, &plain).and_then(|plain_broken| {
                let rest = others
                    .iter()
                    .map(|r| broken(spine, axis, r))
                    .collect::<Result<Vec<_>, _>>()?;
                let mut common: Vec<Broken> = plain_broken
                    .into_iter()
                    .filter(|(path, i, ..)| {
                        rest.iter()
                            .all(|b| b.iter().any(|(p, j, ..)| p == path && j == i))
                    })
                    .collect();
                // One refusal per struct, as Python raises one: the first rule all three break.
                common.dedup_by(|b, a| a.0 == b.0);
                Ok(common)
            });
            let found = match found {
                Ok(found) => found,
                Err(d) => {
                    out.push(d);
                    continue;
                }
            };
            let prefix = format!("style.charts.{axis}.");
            for (path, _, c, json) in found {
                let Some(inner) = path.strip_prefix(&prefix) else {
                    out.push(err_internal(format!(
                        "style `{path}` is not under `{prefix}`"
                    )));
                    continue;
                };
                let keys = rule_keys(c);
                let Some((key, authored)) = plain.iter().rev().find_map(|r| {
                    let key = keys
                        .iter()
                        .copied()
                        .find(|k| holds(&r.set, &join(inner, k)))?;
                    Some((key, join(&r.authored, inner)))
                }) else {
                    continue;
                };
                let doc = Doc(&json);
                let d = match is_field_rule(c) {
                    true => field_error(c, &authored, &doc),
                    false => match model_error(c, &join(&authored, key), &doc) {
                        Ok(d) => d,
                        Err(e) => err_internal(format!("{e}")),
                    },
                };
                if !out
                    .iter()
                    .any(|o| o.path == d.path && o.message().ok() == d.message().ok())
                {
                    out.push(d);
                }
            }
        }
    }
    out
}

fn check(
    at: &str,
    rules: &[&Constraint],
    doc: &Doc,
) -> Result<(bool, Vec<Diagnostic>), Unanswerable> {
    let mut fields = Vec::new();
    for c in rules
        .iter()
        .filter(|c| is_field_rule(c) || is_required_field(c))
    {
        if !satisfied(c, doc)? {
            fields.push(field_error(c, at, doc));
        }
    }
    if !fields.is_empty() {
        return Ok((false, fields));
    }
    for c in rules
        .iter()
        .filter(|c| !is_field_rule(c) && !is_required_field(c))
    {
        if !satisfied(c, doc)? {
            return Ok((true, vec![model_error(c, at, doc)?]));
        }
    }
    Ok((false, Vec::new()))
}

fn join(at: &str, key: &str) -> String {
    match at.is_empty() {
        true => key.to_string(),
        false => format!("{at}.{key}"),
    }
}

fn located(d: Diagnostic, path: &str) -> Diagnostic {
    d.at_path(ModelPath(path.split('.').map(str::to_string).collect()))
}

fn field_error(c: &Constraint, at: &str, doc: &Doc) -> Diagnostic {
    let key = c.fields.first().copied().unwrap_or_default();
    let path = join(at, key);
    let text = match c.kind {
        ConstraintKind::Requires => match doc.reach(key).first() {
            Some(Value::Array(items)) if items.is_empty() => {
                "List should have at least 1 item after validation, not 0".to_string()
            }
            _ => "Field required".to_string(),
        },
        ConstraintKind::ValueIn => format!("Input should be {}", one_of(c.values)),
        _ => bound_text(c, doc.reach(key)),
    };
    located(err_validation_field(path.clone(), text), &path)
}

fn number(x: f64) -> String {
    match x.fract() == 0.0 && x.abs() < 1e15 {
        true => format!("{}", x as i64),
        false => format!("{x}"),
    }
}

/// The validation library's wording of the first bound a number breaks.
fn bound_text(c: &Constraint, found: Vec<&Value>) -> String {
    let b = &c.bounds;
    let values: Vec<f64> = match found.first() {
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_f64).collect(),
        Some(one) => one.as_f64().into_iter().collect(),
        None => Vec::new(),
    };
    let checks = [
        (b.gt, "greater than"),
        (b.ge, "greater than or equal to"),
        (b.lt, "less than"),
        (b.le, "less than or equal to"),
    ];
    for (bound, words) in checks {
        let Some(x) = bound else { continue };
        let broken = values.iter().any(|n| match words {
            "greater than" => *n <= x,
            "greater than or equal to" => *n < x,
            "less than" => *n >= x,
            _ => *n > x,
        });
        if broken {
            return format!("Input should be {words} {}", number(x));
        }
    }
    "Input is out of range".to_string()
}

/// `a, b or c`, each value as Python prints it.
fn one_of(values: &[Scalar]) -> String {
    let shown: Vec<String> = values
        .iter()
        .map(|v| match v {
            Scalar::Null => "None".to_string(),
            Scalar::Bool(b) => py_repr(&Value::Bool(*b)),
            Scalar::Int(i) => i.to_string(),
            Scalar::Float(f) => number(*f),
            Scalar::Str(s) => py_repr(&Value::from(*s)),
        })
        .collect();
    match shown.split_last() {
        Some((last, [])) => last.clone(),
        Some((last, rest)) => format!("{} or {last}", rest.join(", ")),
        None => String::new(),
    }
}

fn model_error(c: &Constraint, at: &str, doc: &Doc) -> Result<Diagnostic, Unanswerable> {
    if is_theme_and_extends(c) {
        return Ok(theme_and_extends(doc));
    }
    if let Some(id) = c.code {
        let code = diagnostic::codes::lookup(id)
            .ok_or_else(|| Unanswerable(format!("`{id}` is not a registered code")))?;
        return Ok(located(Diagnostic::new(code, []), at));
    }
    let template = c.message.ok_or_else(|| {
        Unanswerable(format!(
            "`{:?}({})` states no message",
            c.kind,
            c.fields.join(", ")
        ))
    })?;
    let text = render(template, c, doc)?;
    let d = err_validation_field(at.to_string(), format!("Value error, {text}"));
    Ok(match at.is_empty() {
        true => d,
        false => located(d, at),
    })
}

fn theme_and_extends(doc: &Doc) -> Diagnostic {
    let theme = doc
        .reach("theme")
        .first()
        .map_or(String::new(), |v| text(v));
    let extends = doc.reach("extends");
    let mut entries = vec![theme.clone()];
    match extends.first() {
        Some(Value::Array(items)) => entries.extend(items.iter().map(text)),
        Some(one) => entries.push(text(one)),
        None => {}
    }
    err_theme_and_extends(theme, format!("[{}]", entries.join(", ")))
}

/// `str(value)` as Python prints it.
fn text(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(py_repr_deep)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => py_repr_deep(other),
    }
}

fn py_repr_deep(v: &Value) -> String {
    match v {
        Value::Array(items) => format!(
            "[{}]",
            items
                .iter()
                .map(py_repr_deep)
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{}: {}", py_repr(&Value::from(k.as_str())), py_repr_deep(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => py_repr(other),
    }
}

/// `str.format` over the rule's message: `{key}` is the value at that key of the model, `{key!r}`
/// its `repr`; `{set}`, `{item}`, `{dup}`, `{first}` and `{second}` are what the rule found.
fn render(template: &str, c: &Constraint, doc: &Doc) -> Result<String, Unanswerable> {
    let mut out = String::new();
    let mut chars = template.chars().peekable();
    while let Some(ch) = chars.next() {
        match (ch, chars.peek()) {
            ('{', Some('{')) | ('}', Some('}')) => {
                out.push(ch);
                chars.next();
            }
            ('{', _) => {
                let mut field = String::new();
                for f in chars.by_ref() {
                    if f == '}' {
                        break;
                    }
                    field.push(f);
                }
                let (name, repr) = match field.strip_suffix("!r") {
                    Some(name) => (name, true),
                    None => (field.as_str(), false),
                };
                let value = placeholder(name, c, doc)?;
                out.push_str(&match repr {
                    true => py_repr_deep(&value),
                    false => text(&value),
                });
            }
            _ => out.push(ch),
        }
    }
    Ok(out)
}

fn placeholder(name: &str, c: &Constraint, doc: &Doc) -> Result<Value, Unanswerable> {
    Ok(match name {
        "set" => Value::Array(set_keys(c, doc)?.into_iter().map(Value::from).collect()),
        "item" => bad_item(c, doc)?.unwrap_or(Value::Null),
        "dup" | "first" | "second" => match clash(c, doc)? {
            Some(found) => match name {
                "dup" => found.value,
                "first" => Value::from(found.first),
                _ => Value::from(found.second),
            },
            None => Value::Null,
        },
        key => doc.reach(key).first().map_or(Value::Null, |v| (*v).clone()),
    })
}

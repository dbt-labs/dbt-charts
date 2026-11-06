//! Board documents across the host boundary: a Python mapping in, the migrated
//! mapping and its notices out. Conversion only; recognition and rewriting are
//! the `migrate` crate's.

use diagnostic::{codes, Diagnostic};
use indexmap::IndexMap;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString};
use yaml::{Entry, KeyKind, Kind, Node, Value};

fn node(kind: Kind) -> Node {
    Node { span: migrate::SYNTHETIC, kind }
}

fn at(path: &[String]) -> String {
    if path.is_empty() { "the root".to_string() } else { path.join(".") }
}

/// A value the engine cannot read reaching it is a caller bug: migration runs on
/// the raw authored document, before any merge or typing.
fn unreadable(py: Python<'_>, caller: &str, path: &[String], what: &str) -> PyErr {
    let message = format!(
        "{caller} handed the migration engine {what} at {}; migration runs on the raw authored document",
        at(path)
    );
    crate::coded_error(py, &Diagnostic::new(&codes::ERR_INTERNAL, [("message", message.into())]))
}

/// The refusal the `yaml` crate gives what it does not carry: a number JSON
/// cannot hold, and (as user input PyYAML loads, not a caller bug) a null or
/// tuple key, `!!binary`, `!!set`, text that is not valid Unicode.
fn refused(py: Python<'_>, path: &[String], what: &str) -> PyErr {
    crate::coded_error(py, &Diagnostic::refused(format!("{what} at {} is not supported", at(path))))
}

/// A mapping key as the engine keeps it: JSON's text and the kind it had.
fn key_of(py: Python<'_>, k: &Bound<'_, PyAny>, path: &[String]) -> PyResult<(String, KeyKind)> {
    if let Ok(s) = k.cast::<PyString>() {
        let text = s
            .to_str()
            .map_err(|_| refused(py, path, "text that is not valid Unicode"))?;
        return Ok((text.to_string(), KeyKind::Str));
    }
    if k.is_none() {
        return Ok(("null".to_string(), KeyKind::Null));
    }
    if let Ok(b) = k.cast::<PyBool>() {
        return Ok((b.is_true().to_string(), KeyKind::Bool));
    }
    if k.cast::<PyInt>().is_ok() {
        return Ok((k.str()?.extract()?, KeyKind::Int));
    }
    if k.cast::<PyFloat>().is_ok() {
        return Ok((k.repr()?.extract()?, KeyKind::Float));
    }
    Err(refused(py, path, "a mapping key that is neither text nor a number"))
}

/// The Python key a `Node` map key was built from.
fn key_to_py<'py>(py: Python<'py>, kind: KeyKind, text: &str) -> PyResult<Bound<'py, PyAny>> {
    Ok(match kind {
        KeyKind::Str => text.into_pyobject(py)?.into_any(),
        KeyKind::Null => py.None().into_bound(py),
        KeyKind::Bool => PyBool::new(py, text == "true").to_owned().into_any(),
        KeyKind::Int => py.get_type::<PyInt>().call1((text,))?,
        KeyKind::Float => py.get_type::<PyFloat>().call1((text,))?,
    })
}

/// A Python value as a spanless `Node`. A date or datetime is a timestamp scalar
/// so it survives the round trip as the same Python type.
fn to_node<'py>(
    obj: &Bound<'py, PyAny>,
    path: &mut Vec<String>,
    caller: &str,
) -> PyResult<Node> {
    let py = obj.py();
    let scalar = |v| Ok(node(Kind::Scalar(v)));
    if obj.is_none() {
        return scalar(Value::Null);
    }
    if let Ok(b) = obj.cast::<PyBool>() {
        return scalar(Value::Bool(b.is_true()));
    }
    if obj.cast::<PyInt>().is_ok() {
        let n = if let Ok(i) = obj.extract::<i64>() {
            serde_json::Number::from(i)
        } else if let Ok(u) = obj.extract::<u64>() {
            serde_json::Number::from(u)
        } else {
            return Err(refused(py, path, "an integer beyond 64 bits"));
        };
        return scalar(Value::Number(n));
    }
    if obj.cast::<PyFloat>().is_ok() {
        let f = obj.extract::<f64>()?;
        return match serde_json::Number::from_f64(f) {
            Some(n) => scalar(Value::Number(n)),
            None => Err(refused(py, path, "a number JSON cannot hold (`.inf` or `.nan`)")),
        };
    }
    if let Ok(s) = obj.cast::<PyString>() {
        return match s.to_str() {
            Ok(text) => scalar(Value::Str(text.to_string())),
            Err(_) => Err(refused(py, path, "text that is not valid Unicode")),
        };
    }
    if obj.is_instance(&py.import("datetime")?.getattr("date")?)? {
        let iso: String = obj.call_method0("isoformat")?.extract()?;
        return scalar(Value::Timestamp(iso));
    }
    if let Ok(d) = obj.cast::<PyDict>() {
        let mut map = IndexMap::new();
        for (k, v) in d.iter() {
            let (key, kind) = key_of(py, &k, path)?;
            path.push(key.clone());
            let value = to_node(&v, path, caller)?;
            path.pop();
            let entry = Entry {
                key_span: migrate::SYNTHETIC,
                key_kind: kind,
                key_text: key.clone(),
                value,
            };
            if map.insert(key.clone(), entry).is_some() {
                return Err(refused(py, path, &format!("two mapping keys that are one key in JSON text ({key})")));
            }
        }
        return Ok(node(Kind::Map(map)));
    }
    if let Ok(l) = obj.cast::<PyList>() {
        let mut items = Vec::new();
        for (i, item) in l.iter().enumerate() {
            path.push(i.to_string());
            items.push(to_node(&item, path, caller)?);
            path.pop();
        }
        return Ok(node(Kind::Seq(items)));
    }
    let kind = obj.get_type().name()?.to_string();
    if matches!(kind.as_str(), "bytes" | "set" | "frozenset" | "tuple") {
        return Err(refused(py, path, &format!("a {kind} value")));
    }
    Err(unreadable(py, caller, path, &format!("a {kind}")))
}

fn to_py<'py>(py: Python<'py>, n: &Node) -> PyResult<Bound<'py, PyAny>> {
    Ok(match &n.kind {
        Kind::Scalar(Value::Null) => py.None().into_bound(py),
        Kind::Scalar(Value::Bool(b)) => PyBool::new(py, *b).to_owned().into_any(),
        Kind::Scalar(Value::Number(num)) => {
            if let Some(i) = num.as_i64() {
                i.into_pyobject(py)?.into_any()
            } else if let Some(u) = num.as_u64() {
                u.into_pyobject(py)?.into_any()
            } else {
                num.as_f64()
                    .ok_or_else(|| PyValueError::new_err("a number the engine returned is not a finite float"))?
                    .into_pyobject(py)?
                    .into_any()
            }
        }
        Kind::Scalar(Value::Str(s)) => s.into_pyobject(py)?.into_any(),
        Kind::Scalar(Value::Timestamp(iso)) => {
            let cls = if iso.contains('T') { "datetime" } else { "date" };
            py.import("datetime")?.getattr(cls)?.call_method1("fromisoformat", (iso,))?
        }
        Kind::Seq(items) => {
            let out = PyList::empty(py);
            for item in items {
                out.append(to_py(py, item)?)?;
            }
            out.into_any()
        }
        Kind::Map(m) => {
            let out = PyDict::new(py);
            for (k, e) in m {
                out.set_item(key_to_py(py, e.key_kind, k)?, to_py(py, &e.value)?)?;
            }
            out.into_any()
        }
    })
}

/// `migrate_mapping(document, today, caller)` -> `(document, notices, outcome)`.
///
/// `today` is `YYYY-MM-DD`, the clock the six-month window reads; `caller`
/// names the Python function handing the document over, for the
/// `ERR-INTERNAL` a value the engine cannot read raises (a model instance); `.inf`/`.nan` and integers past 64 bits raise
/// `ERR-ENGINE-REFUSED`, as the `yaml` crate refuses them. A document the engine
/// found current comes back as the same object; a migrated one as a new
/// mapping. `outcome` is `current`, `migrated`, `unrecognized` or `kept`. The
/// GIL is released while migrating.
#[pyfunction]
fn migrate_mapping<'py>(
    py: Python<'py>,
    document: &Bound<'py, PyAny>,
    today: &str,
    caller: &str,
) -> PyResult<(Bound<'py, PyAny>, Vec<String>, &'static str)> {
    let today = migrate::parse_date(today).ok_or_else(|| PyValueError::new_err("today must be YYYY-MM-DD"))?;
    let root = to_node(document, &mut Vec::new(), caller)?;
    let migrated = py.detach(|| migrate::migrate(root, today));
    let outcome = match migrated.outcome {
        migrate::Outcome::Current => "current",
        migrate::Outcome::Migrated => "migrated",
        migrate::Outcome::Unrecognized => "unrecognized",
        migrate::Outcome::Kept { .. } => "kept",
    };
    let document = if outcome == "current" { document.clone() } else { to_py(py, &migrated.node)? };
    Ok((document, migrated.notices, outcome))
}

fn json_to_py<'py>(py: Python<'py>, value: &serde_json::Value) -> PyResult<Bound<'py, PyAny>> {
    to_py(py, &migrate_json_node(value))
}

fn migrate_json_node(value: &serde_json::Value) -> Node {
    let kind = match value {
        serde_json::Value::Null => Kind::Scalar(Value::Null),
        serde_json::Value::Bool(b) => Kind::Scalar(Value::Bool(*b)),
        serde_json::Value::Number(n) => Kind::Scalar(Value::Number(n.clone())),
        serde_json::Value::String(s) => Kind::Scalar(Value::Str(s.clone())),
        serde_json::Value::Array(a) => Kind::Seq(a.iter().map(migrate_json_node).collect()),
        serde_json::Value::Object(o) => Kind::Map(
            o.iter()
                .map(|(k, v)| (k.clone(), Entry::string_key(migrate::SYNTHETIC, k, migrate_json_node(v))))
                .collect(),
        ),
    };
    node(kind)
}

fn seg_text(seg: &migrate::Seg) -> String {
    match seg {
        migrate::Seg::Index(i) => i.to_string(),
        migrate::Seg::Key(k) => k.clone(),
    }
}

fn edit_to_py<'py>(py: Python<'py>, edit: &migrate::Edit) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    match edit {
        migrate::Edit::Move { from, to, before, after, rewrite, value_map, expansion, declared, .. } => {
            out.set_item("kind", "move")?;
            out.set_item("item", matches!(from.last(), Some(migrate::Seg::Index(_))))?;
            out.set_item("from", from.iter().map(seg_text).collect::<Vec<_>>())?;
            out.set_item("to", to.iter().map(seg_text).collect::<Vec<_>>())?;
            out.set_item("before", json_to_py(py, before)?)?;
            match after {
                Some(a) => out.set_item("after", json_to_py(py, a)?)?,
                None => out.set_item("dropped", true)?,
            }
            out.set_item("rewrite", *rewrite)?;
            out.set_item("value_map", *value_map)?;
            out.set_item("expansion", *expansion)?;
            out.set_item("declared", declared)?;
        }
        migrate::Edit::Delete { path, .. } => {
            out.set_item("kind", "delete")?;
            let parts = PyList::empty(py);
            for seg in path {
                match seg {
                    migrate::Seg::Index(i) => parts.append(*i)?,
                    other => parts.append(seg_text(other))?,
                }
            }
            out.set_item("path", parts)?;
        }
    }
    Ok(out)
}

/// `plan_text_migration(document, stop_target, caller)` -> `("current",)`,
/// `("planned", staged, edits, notices)` or `(error_kind, message)` with
/// `error_kind` one of `unsupported`, `incomplete`, `conflict`, `migration`.
/// What a `dct migrate` text writer replays: each edit a dict (`kind` `move`
/// with `from`, `to`, `before`, `after` (absent when `dropped`), `rewrite`,
/// `value_map`, `expansion`, `item` (the value sat in a list), `declared`; or
/// `delete` with `path`, a list position an int).
#[pyfunction]
fn plan_text_migration<'py>(
    py: Python<'py>,
    document: &Bound<'py, PyAny>,
    stop_target: Option<&str>,
    caller: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let root = to_node(document, &mut Vec::new(), caller)?;
    let plan = py.detach(|| migrate::plan_text(&root, stop_target));
    let tuple = match plan {
        Ok(migrate::TextPlan::Current) => ("current",).into_pyobject(py)?.into_any(),
        Ok(migrate::TextPlan::Planned { node, edits, notices }) => {
            let edits = edits.iter().map(|e| edit_to_py(py, e)).collect::<PyResult<Vec<_>>>()?;
            ("planned", to_py(py, &node)?, edits, notices).into_pyobject(py)?.into_any()
        }
        Err(e) => {
            let (kind, message) = match e {
                migrate::TextError::Unsupported(m) => ("unsupported", m),
                migrate::TextError::Incomplete(m) => ("incomplete", m),
                migrate::TextError::Conflict(m) => ("conflict", m),
                migrate::TextError::Migration(m) => ("migration", m),
            };
            (kind, message).into_pyobject(py)?.into_any()
        }
    };
    Ok(tuple)
}

/// `retired_keys(document, caller)` -> `[(path, retired_in, successor, reason)]`:
/// every key a declared migration would act on in the raw document, by
/// concrete path (list positions as digit strings), newest grammar first.
#[pyfunction]
#[allow(clippy::type_complexity)]
fn retired_keys(
    py: Python<'_>,
    document: &Bound<'_, PyAny>,
    caller: &str,
) -> PyResult<Vec<(Vec<String>, &'static str, Option<Vec<String>>, Option<String>)>> {
    let root = to_node(document, &mut Vec::new(), caller)?;
    let found = py.detach(|| migrate::retired_keys(&root));
    found
        .into_iter()
        .map(|k| Ok((k.path, k.retired_in, k.successor, k.reason)))
        .collect()
}

/// Retired theme names and the theme each now means.
#[pyfunction]
fn retired_theme_renames() -> Vec<(&'static str, &'static str)> {
    migrate::THEME_RENAMES.to_vec()
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(migrate_mapping, m)?)?;
    m.add_function(wrap_pyfunction!(plan_text_migration, m)?)?;
    m.add_function(wrap_pyfunction!(retired_keys, m)?)?;
    m.add_function(wrap_pyfunction!(retired_theme_renames, m)?)?;
    Ok(())
}

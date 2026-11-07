//! Display text: a board's titles and prose and a chart's title, subtitle and
//! message, rendered as production's `resolve_display_text` renders them (spec
//! § 5.1 step 4). Undefined names print empty and chain; every value prints as
//! Python's `str` prints it; `queries.<name>` reads `.rows`, `.one` and `.columns`
//! of the results the text references; fenced code passes through untouched.
//!
//! A query-result access error must reach the author, but a minijinja lookup cannot
//! fail, so the first error a lookup or print raises is kept beside the render and
//! returned in place of its output, which is what production's raise amounts to.

use std::fmt;
use std::sync::{Arc, Mutex};

use column_types::{ColumnType, Dataset};
use d3rs::py_float::float_repr;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use minijinja::value::{Enumerator, Object, ObjectRepr, Value, ValueKind};
use minijinja::{Error, ErrorKind, UndefinedBehavior};
use serde_json::Value as Json;
use template::environment::{environment, Kind};

/// The first error the render raised, in place of its output.
#[derive(Debug, Default)]
struct Raised(Mutex<Option<Diagnostic>>);

impl Raised {
    fn raise(&self, diagnostic: Diagnostic) {
        if let Ok(mut first) = self.0.lock() {
            first.get_or_insert(diagnostic);
        }
    }

    fn take(&self) -> Option<Diagnostic> {
        self.0.lock().ok().and_then(|mut first| first.take())
    }
}

/// Renders `text` against the board's `variables` and the results of the queries it
/// references, keyed by the name the text reads them under.
pub fn render_text(
    text: &str,
    variables: &IndexMap<String, Json>,
    queries: &IndexMap<String, Dataset>,
) -> Result<String, Diagnostic> {
    let blocks = template::mask::fenced_blocks(text);
    let mut masked = String::with_capacity(text.len());
    let mut at = 0;
    for (i, block) in blocks.iter().enumerate() {
        masked.push_str(&text[at..block.start]);
        masked.push_str(&fence_token(i));
        at = block.end;
    }
    masked.push_str(&text[at..]);
    if !masked.contains("{{") && !masked.contains("{%") {
        return Ok(text.to_string());
    }
    let info = template::parse(&masked, Kind::Text).map_err(|errors| {
        let message = errors.first().map_or_else(String::new, |e| e.message.clone());
        Diagnostic::refused(refuse!("display text that does not parse is not supported: {message}"))
    })?;
    if let Some(construct) = info.unanalyzed {
        return Err(Diagnostic::refused(refuse!("display text with {construct} is not supported")));
    }
    if info.arithmetic {
        return Err(Diagnostic::refused(refuse!("arithmetic in display text is not supported")));
    }
    // `~` prints its operands through minijinja, not Python's `str`.
    if info.concat {
        return Err(Diagnostic::refused(refuse!("`~` in display text is not supported")));
    }
    if info.ordering {
        return Err(Diagnostic::refused(refuse!("an ordering comparison in display text is not supported")));
    }
    let mut context = IndexMap::new();
    for (name, value) in variables {
        if !info.variables.contains(name) {
            continue;
        }
        context.insert(name.clone(), variable(name, value)?);
    }
    let raised = Arc::new(Raised::default());
    let mut results = IndexMap::new();
    for (name, dataset) in queries {
        results.insert(name.clone(), Arc::new(QueryResult::new(name, dataset, &raised)?));
    }
    context.insert("queries".to_string(), Value::from_object(QueryResults { results, raised: raised.clone() }));

    let mut env = environment(Kind::Text).map_err(engine)?;
    env.set_undefined_behavior(UndefinedBehavior::Chainable);
    let sink = raised.clone();
    env.set_formatter(move |out, _state, value| {
        if let Some(text) = python_str(value, &sink) {
            out.write_str(&text).map_err(|e| Error::new(ErrorKind::WriteFailure, e.to_string()))?;
        }
        Ok(())
    });
    for name in ["first", "last", "min", "max"] {
        let sink = raised.clone();
        env.add_filter(name, move |state: &minijinja::State, value: Value| -> Result<Value, Error> {
            if value.len() == Some(0) {
                sink.raise(diag::err_text_empty_sequence(format!("No {name} item, sequence was empty.")));
                return Ok(Value::UNDEFINED);
            }
            builtin(state, name, &value)
        });
    }
    // `string` and `join` print values, so they print them as the formatter does.
    let sink = raised.clone();
    env.add_filter("string", move |value: Value| python_str(&value, &sink).map_or(Value::UNDEFINED, Value::from));
    let sink = raised.clone();
    env.add_filter("join", move |value: Value, separator: Option<String>| -> Result<Value, Error> {
        let mut parts = Vec::new();
        for item in value.try_iter()? {
            match python_str(&item, &sink) {
                Some(text) => parts.push(text),
                None => return Ok(Value::UNDEFINED),
            }
        }
        Ok(Value::from(parts.join(separator.as_deref().unwrap_or_default())))
    });
    let sink = raised.clone();
    env.add_filter("random", move |_: Value| {
        sink.raise(Diagnostic::refused(refuse!("the `random` filter in display text is not supported")));
        Value::UNDEFINED
    });
    let sink = raised.clone();
    env.add_filter("format", move |_: Value, _: minijinja::value::Rest<Value>| {
        sink.raise(Diagnostic::refused(refuse!("the `format` filter in display text is not supported")));
        Value::UNDEFINED
    });

    let rendered = env.render_str(&masked, Value::from_iter(context));
    if let Some(error) = raised.take() {
        return Err(error);
    }
    let mut out = rendered.map_err(engine)?;
    for (i, block) in blocks.iter().enumerate() {
        out = out.replace(&fence_token(i), &text[block.clone()]);
    }
    Ok(out)
}

fn fence_token(i: usize) -> String {
    format!("__DCT_MARKDOWN_FENCE_{i}__")
}

/// A minijinja error: an invalid operation production raises as `ERR-TEXT-TYPE-ERROR`,
/// anything else refused by name.
fn engine(error: Error) -> Diagnostic {
    match error.kind() {
        ErrorKind::InvalidOperation => {
            diag::err_text_type_error(error.detail().map_or_else(|| error.to_string(), str::to_string))
        }
        _ => Diagnostic::refused(refuse!("display text that minijinja cannot render is not supported: {error}")),
    }
}

fn builtin(state: &minijinja::State, name: &str, value: &Value) -> Result<Value, Error> {
    match name {
        "first" => minijinja::filters::first(value),
        "last" => minijinja::filters::last(value.clone()),
        "min" => minijinja::filters::min(state, value.clone()),
        _ => minijinja::filters::max(state, value.clone()),
    }
}

/// A variable's value as the text reads it. A list or mapping is a multiselect, a date
/// range or a structured value, which needs the typed variable value.
fn variable(name: &str, value: &Json) -> Result<Value, Diagnostic> {
    match value {
        Json::Array(_) | Json::Object(_) => Err(Diagnostic::refused(refuse!(
            "the list or mapping variable {name:?} in display text is not supported"
        ))),
        _ => Ok(json_scalar(value)),
    }
}

fn json_scalar(value: &Json) -> Value {
    match value {
        Json::Null => Value::from(()),
        Json::Bool(b) => Value::from(*b),
        Json::Number(n) => match n.as_i64() {
            Some(i) => Value::from(i),
            None => n.as_f64().map_or(Value::UNDEFINED, Value::from),
        },
        Json::String(s) => Value::from(s.as_str()),
        Json::Array(_) | Json::Object(_) => Value::UNDEFINED,
    }
}

/// What Python's `str` prints for `value`, or `None` when the print raised.
fn python_str(value: &Value, raised: &Raised) -> Option<String> {
    match value.kind() {
        ValueKind::Undefined => Some(String::new()),
        ValueKind::None => Some("None".to_string()),
        ValueKind::Bool => Some(if value.is_true() { "True" } else { "False" }.to_string()),
        ValueKind::Number if !value.is_integer() => f64::try_from(value.clone()).ok().map(float_repr),
        ValueKind::Number | ValueKind::String => Some(value.to_string()),
        ValueKind::Map if value.as_object().is_none_or(|o| o.downcast_ref::<QueryRow>().is_none() && o.downcast_ref::<QueryResult>().is_none()) => {
            raised.raise(diag::err_text_mapping_value("mapping"));
            None
        }
        _ => match value.as_object() {
            Some(object) => {
                if let Some(cell) = object.downcast_ref::<Cell>() {
                    if cell.text.is_none() {
                        raised.raise(Diagnostic::refused(refuse!(
                            "printing a bytes, JSON, list or struct cell in display text is not supported"
                        )));
                    }
                    return cell.text.clone();
                }
                if let Some(row) = object.downcast_ref::<QueryRow>() {
                    raised.raise(diag::err_text_query_access(row.query.as_str(), "cannot print a row directly", "row.<col>"));
                    return None;
                }
                if let Some(result) = object.downcast_ref::<QueryResult>() {
                    raised.raise(diag::err_text_query_access(
                        result.name.as_str(),
                        "cannot be printed directly",
                        ".one.<col>, .rows or .columns",
                    ));
                    return None;
                }
                raised.raise(Diagnostic::refused(refuse!("printing a list in display text is not supported")));
                None
            }
            None => {
                raised.raise(Diagnostic::refused(refuse!("printing a list in display text is not supported")));
                None
            }
        },
    }
}

/// `queries` in board text: names resolve only to the results the text references.
#[derive(Debug)]
struct QueryResults {
    results: IndexMap<String, Arc<QueryResult>>,
    raised: Arc<Raised>,
}

impl Object for QueryResults {
    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        let name = key.as_str()?;
        if name.starts_with("__") {
            return None;
        }
        if name.starts_with('_') {
            self.raised.raise(Diagnostic::refused(refuse!("an underscore name on `queries` is not supported")));
            return None;
        }
        match self.results.get(name) {
            Some(result) => Some(Value::from_dyn_object(result.clone())),
            None => {
                self.raised.raise(diag::err_text_query_access(
                    name,
                    "is not a query this text references",
                    "queries.<name> with a query the board defines",
                ));
                None
            }
        }
    }
}

/// A query's rows as templates read them: `.rows`, `.one`, `.columns`.
#[derive(Debug)]
struct QueryResult {
    name: String,
    rows: Vec<Value>,
    columns: Vec<Value>,
    raised: Arc<Raised>,
}

impl QueryResult {
    /// Every cell decodes by its column's declared type; an undeclared column is an
    /// engine error, as the dataset contract declares every column.
    fn new(name: &str, dataset: &Dataset, raised: &Arc<Raised>) -> Result<Self, Diagnostic> {
        let mut rows = Vec::with_capacity(dataset.rows.len());
        for row in &dataset.rows {
            let mut cells = IndexMap::new();
            for (column, cell) in row {
                let declared = dataset.schema.get(column).map_err(|e| diag::err_internal(format!("query {name:?}: {e}")))?;
                cells.insert(column.clone(), cell_value(cell, declared));
            }
            rows.push(Value::from_object(QueryRow { query: name.to_string(), cells, raised: raised.clone() }));
        }
        // Columns in the first row's order, as production lists them; none without rows.
        let columns = dataset.rows.first().map_or_else(Vec::new, |row| row.keys().map(|c| Value::from(c.as_str())).collect());
        Ok(Self { name: name.to_string(), rows, columns, raised: raised.clone() })
    }
}

impl Object for QueryResult {
    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        let attribute = key.as_str()?;
        match attribute {
            "rows" => Some(items(&self.name, "row", &self.rows, format!("returned {} rows", self.rows.len()), &self.raised)),
            "columns" => Some(items(&self.name, "column", &self.columns, format!("{} columns", self.columns.len()), &self.raised)),
            "one" if self.rows.len() == 1 => self.rows.first().cloned(),
            "one" => {
                self.raised.raise(diag::err_text_query_row_count(self.name.as_str(), self.rows.len()));
                None
            }
            _ if attribute.starts_with("__") => None,
            _ if attribute.starts_with('_') => {
                self.raised.raise(Diagnostic::refused(refuse!("an underscore attribute of a query result is not supported")));
                None
            }
            _ => {
                self.raised.raise(diag::err_text_query_access(
                    self.name.as_str(),
                    format!("has no attribute '{attribute}'"),
                    format!(".one.{attribute}, .rows or .columns"),
                ));
                None
            }
        }
    }
}

fn items(query: &str, noun: &'static str, items: &[Value], extent: String, raised: &Arc<Raised>) -> Value {
    Value::from_object(QueryItems { query: query.to_string(), noun, items: items.to_vec(), extent, raised: raised.clone() })
}

/// A query's rows or column names: an index it does not have raises.
#[derive(Debug)]
struct QueryItems {
    query: String,
    noun: &'static str,
    items: Vec<Value>,
    extent: String,
    raised: Arc<Raised>,
}

impl Object for QueryItems {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Seq
    }

    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        let index = key.as_i64()?;
        let len = i64::try_from(self.items.len()).ok()?;
        let at = if index < 0 { index + len } else { index };
        match usize::try_from(at).ok().and_then(|at| self.items.get(at)) {
            Some(item) => Some(item.clone()),
            None => {
                self.raised.raise(diag::err_text_query_index(self.query.as_str(), self.noun, index, self.extent.as_str()));
                None
            }
        }
    }

    fn enumerate(self: &Arc<Self>) -> Enumerator {
        Enumerator::Seq(self.items.len())
    }
}

/// One result row: cells reached by column name only.
#[derive(Debug)]
struct QueryRow {
    query: String,
    cells: IndexMap<String, Value>,
    raised: Arc<Raised>,
}

impl Object for QueryRow {
    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        let column = key.as_str()?;
        if column.starts_with("__") {
            return None;
        }
        match self.cells.get(column) {
            Some(cell) => Some(cell.clone()),
            None => {
                let available: Vec<&str> = self.cells.keys().map(String::as_str).collect();
                self.raised.raise(diag::err_text_query_unknown_column(self.query.as_str(), column, available));
                None
            }
        }
    }

    fn enumerate(self: &Arc<Self>) -> Enumerator {
        Enumerator::NonEnumerable
    }
}

/// A cell Python holds as a `Decimal`, `date`, `datetime` or `time`: it prints as
/// `str` prints it and offers nothing else.
#[derive(Debug)]
struct Cell {
    /// `None` for a cell whose printing is not modeled.
    text: Option<String>,
}

impl Object for Cell {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn render(self: &Arc<Self>, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.text.as_deref().unwrap_or_default())
    }
}

/// A cell as the text reads it, by its declared type: the value `result_codec`
/// restores in production. A bytes, JSON, list or struct cell is not modeled.
fn cell_value(cell: &Json, declared: &ColumnType) -> Value {
    let text = |t: String| Value::from_object(Cell { text: Some(t) });
    match (cell, declared) {
        (Json::Null, _) => Value::from(()),
        (Json::String(s), ColumnType::Decimal | ColumnType::Date | ColumnType::Time) => text(s.clone()),
        // `str(datetime)` puts a space where the ISO text has `T`.
        (Json::String(s), ColumnType::Datetime | ColumnType::DatetimeTz) => text(s.replacen('T', " ", 1)),
        (Json::Number(n), ColumnType::Float) => n.as_f64().map_or(Value::UNDEFINED, Value::from),
        (_, ColumnType::Bytes | ColumnType::Json | ColumnType::List(_) | ColumnType::Struct(_)) => {
            Value::from_object(Cell { text: None })
        }
        (value, _) => json_scalar(value),
    }
}

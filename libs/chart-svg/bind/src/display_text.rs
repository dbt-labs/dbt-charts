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

use crate::formats::locale::active_number_locale;
use crate::formats::{
    column_format, format_value, is_predefined_name, is_time_format, resolve_format, Alias, AuthoredFormat, Formats, Kind as FormatKind, Notation, Tiers,
    TEXT_NAMES,
};
use crate::table_dates;
use d3rs::py_float::float_repr;
use diagnostic::{diag, refuse, Diagnostic};
use indexmap::IndexMap;
use minijinja::value::{Enumerator, Object, ObjectRepr, Value, ValueKind};
use minijinja::{Error, ErrorKind, UndefinedBehavior};
use serde_json::Value as Json;
use board_model::variable_value::{Atom, VariableValue};
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
    variables: &IndexMap<String, VariableValue>,
    queries: &IndexMap<String, Dataset>,
    formats: &IndexMap<String, Alias>,
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
    if info.ordering_chained {
        return Err(Diagnostic::refused(refuse!("a chained ordering comparison in display text is not supported")));
    }
    let source = with_orderings_as_calls(&masked, &info.orderings)?;
    let mut context = IndexMap::new();
    for (name, value) in variables {
        if !info.variables.contains(name) {
            continue;
        }
        context.insert(name.clone(), variable(value));
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
        if let Some(text) = printed(value, &sink) {
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
    let aliases = Arc::new(formats.clone());
    env.add_filter("format", move |value: Value, args: minijinja::value::Rest<Value>| {
        match format_filter(&value, &args, Formats::new(Some(&aliases))) {
            Ok(text) => Value::from(text),
            Err(diagnostic) => {
                sink.raise(diagnostic);
                Value::UNDEFINED
            }
        }
    });
    let sink = raised.clone();
    env.add_function(ORDER, move |left: Value, op: String, right: Value| match order(&left, &op, &right) {
        Ok(holds) => holds,
        Err(diagnostic) => {
            sink.raise(diagnostic);
            false
        }
    });

    let rendered = env.render_str(&source, Value::from_iter(context));
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

/// A variable's value as the text reads it: the Python value production's Jinja holds.
fn variable(value: &VariableValue) -> Value {
    let slot = |slot: &Option<Atom>| slot.as_ref().map_or(Value::from(()), atom);
    match value {
        VariableValue::Unset => Value::from(()),
        VariableValue::One(one) => atom(one),
        VariableValue::Multiselect(members) => list(ListKind::Multiselect, members.iter().map(slot).collect()),
        VariableValue::Daterange { start, end } => list(ListKind::Daterange, vec![slot(start), slot(end)]),
    }
}

fn atom(atom: &Atom) -> Value {
    match atom {
        Atom::Text(text) => Value::from(text.as_str()),
        Atom::Int(int) => Value::from(*int),
        Atom::Float(float) => Value::from(*float),
        Atom::Bool(flag) => Value::from(*flag),
        // A variable's `date` is the same Python `date` a date cell restores to.
        Atom::Date(iso) => Value::from_object(Cell { text: Some(iso.clone()), kind: CellKind::Temporal(iso.clone(), ColumnType::Date) }),
    }
}

fn list(kind: ListKind, items: Vec<Value>) -> Value {
    Value::from_object(VariableList { kind, items })
}

#[derive(Debug, Clone, Copy)]
enum ListKind {
    /// `MultiselectValue`: `{{ v }}` prints it as a natural list.
    Multiselect,
    /// `DaterangeValue`: `{{ v }}` raises; its ends are indexable.
    Daterange,
}

/// A multiselect or daterange variable: a Python list subclass whose whole print
/// (`jinja._print_text_value`) differs from a plain list's.
#[derive(Debug)]
struct VariableList {
    kind: ListKind,
    items: Vec<Value>,
}

impl Object for VariableList {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Seq
    }

    fn get_value(self: &Arc<Self>, key: &Value) -> Option<Value> {
        self.items.get(key.as_usize()?).cloned()
    }

    fn enumerate(self: &Arc<Self>) -> Enumerator {
        Enumerator::Seq(self.items.len())
    }
}

/// What `{{ value }}` prints: `jinja._print_text_value` finalizes each expression, then
/// Python's `str` prints what is left. A multiselect reads as "A and B" or "A, B, and C".
fn printed(value: &Value, raised: &Raised) -> Option<String> {
    let Some(list) = value.downcast_object_ref::<VariableList>() else {
        return python_str(value, raised);
    };
    match list.kind {
        ListKind::Daterange => {
            raised.raise(diag::err_text_mapping_value("date range"));
            None
        }
        ListKind::Multiselect => {
            let names: Option<Vec<String>> = list.items.iter().map(|item| python_str(item, raised)).collect();
            let names = names?;
            Some(match names.as_slice() {
                [.., last] if names.len() > 2 => format!("{}, and {last}", names[..names.len() - 1].join(", ")),
                _ => names.join(" and "),
            })
        }
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
                if object.downcast_ref::<VariableList>().is_some() {
                    raised.raise(Diagnostic::refused(refuse!(
                        "converting a multiselect or daterange variable to a string in display text is not supported"
                    )));
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
    /// What Python's `str` prints; `None` for a cell whose printing is not modeled.
    text: Option<String>,
    kind: CellKind,
}

/// What a cell holds beyond its printed text, as `format` reads it.
#[derive(Debug)]
enum CellKind {
    /// A `Decimal`, as its wire text.
    Decimal(String),
    /// A `date` or `datetime`, as its wire text and declared type.
    Temporal(String, ColumnType),
    /// A `time`, or a cell whose printing is not modeled.
    Other,
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
    let cell_of = |text: Option<String>, kind: CellKind| Value::from_object(Cell { text, kind });
    match (cell, declared) {
        (Json::Null, _) => Value::from(()),
        (Json::String(s), ColumnType::Decimal) => cell_of(Some(s.clone()), CellKind::Decimal(s.clone())),
        (Json::String(s), ColumnType::Date) => cell_of(Some(s.clone()), CellKind::Temporal(s.clone(), declared.clone())),
        // `str(datetime)` puts a space where the ISO text has `T`.
        (Json::String(s), ColumnType::Datetime | ColumnType::DatetimeTz) => {
            cell_of(Some(s.replacen('T', " ", 1)), CellKind::Temporal(s.clone(), declared.clone()))
        }
        (Json::String(s), ColumnType::Time) => cell_of(Some(s.clone()), CellKind::Other),
        (Json::Number(n), ColumnType::Float) => n.as_f64().map_or(Value::UNDEFINED, Value::from),
        (_, ColumnType::Bytes | ColumnType::Json | ColumnType::List(_) | ColumnType::Struct(_)) => {
            cell_of(None, CellKind::Other)
        }
        (value, _) => json_scalar(value),
    }
}

/// The function an ordering comparison is rewritten to call.
const ORDER: &str = "__dct_order";

/// `source` with each `left <op> right` rewritten to `__dct_order((left), "op", (right))`,
/// so the comparison runs Python's rules ([`order`]) rather than minijinja's, which
/// orders values of any two kinds without complaint.
fn with_orderings_as_calls(source: &str, sites: &[template::OrderingSite]) -> Result<String, Diagnostic> {
    let span = |(start, end): (u32, u32)| source.get(start as usize..end as usize);
    let mut out = String::with_capacity(source.len());
    let mut at = 0;
    let mut sites: Vec<_> = sites.iter().collect();
    sites.sort_by_key(|site| site.left.0);
    for site in sites {
        let (start, end) = (site.left.0 as usize, site.right.1 as usize);
        let (Some(left), Some(right)) = (span(site.left), span(site.right)) else {
            return Err(diag::err_internal("an ordering comparison's span is outside its text"));
        };
        if start < at {
            return Err(Diagnostic::refused(refuse!("an ordering comparison inside another in display text is not supported")));
        }
        // Only the operator and whitespace may sit between the operands, and each
        // operand's text must balance: a parenthesized operand is not rewritten.
        let between = source.get(site.left.1 as usize..site.right.0 as usize).unwrap_or_default();
        if between.trim() != site.op || !balanced(left) || !balanced(right) {
            return Err(Diagnostic::refused(refuse!("a parenthesized ordering operand in display text is not supported")));
        }
        out.push_str(&source[at..start]);
        out.push_str(&format!("{ORDER}(({left}), {:?}, ({right}))", site.op));
        at = end;
    }
    out.push_str(&source[at..]);
    Ok(out)
}

fn balanced(text: &str) -> bool {
    let mut depth = 0i32;
    for c in text.chars() {
        depth += match c {
            '(' | '[' | '{' => 1,
            ')' | ']' | '}' => -1,
            _ => 0,
        };
        if depth < 0 {
            return false;
        }
    }
    depth == 0
}

/// Python's ordering of two values. Numbers (a bool among them) order by value and
/// strings by code point (NaN is in no order); `None` on either side is
/// `ERR-TEXT-NULL-COMPARISON` and any other pair `ERR-TEXT-TYPE-ERROR`, as production
/// words a `TypeError`. Lists, which Python orders item by item, are refused by name.
fn order(left: &Value, op: &str, right: &Value) -> Result<bool, Diagnostic> {
    use std::cmp::Ordering::*;
    let modeled = |v: &Value| match v.kind() {
        ValueKind::Undefined => Err(Diagnostic::refused(refuse!("ordering an undefined name in display text is not supported"))),
        _ if v.downcast_object_ref::<Cell>().is_some() => {
            Err(Diagnostic::refused(refuse!("ordering a decimal, date or time cell in display text is not supported")))
        }
        ValueKind::Seq | ValueKind::Iterable => {
            Err(Diagnostic::refused(refuse!("ordering a list in display text is not supported")))
        }
        _ => Ok(()),
    };
    modeled(left)?;
    modeled(right)?;
    if left.is_none() || right.is_none() {
        return Err(diag::err_text_null_comparison());
    }
    let numeric = |v: &Value| matches!(v.kind(), ValueKind::Number | ValueKind::Bool);
    let ordering = match (left.as_str(), right.as_str()) {
        (Some(a), Some(b)) => a.cmp(b),
        _ if numeric(left) && numeric(right) => {
            let number = |v: &Value| match v.kind() {
                ValueKind::Bool => Value::from(i64::from(v.is_true())),
                _ => v.clone(),
            };
            let nan = |v: &Value| f64::try_from(v.clone()).is_ok_and(f64::is_nan);
            if nan(left) || nan(right) {
                return Ok(false);
            }
            number(left).partial_cmp(&number(right)).unwrap_or(Equal)
        }
        _ => return Err(diag::err_text_type_error(format!("'{op}' between {} and {}", left.kind(), right.kind()))),
    };
    Ok(match op {
        "<" => ordering == Less,
        "<=" => ordering != Greater,
        ">" => ordering == Greater,
        _ => ordering != Less,
    })
}

/// The `format` filter (`display_text._text_format_filter`): a number or date through
/// one spec, as a table cell prints it, in the narrative register unless an alias
/// names its own notation. Anything else is `ERR-TEXT-FORMAT-USAGE`.
fn format_filter(value: &Value, args: &[Value], formats: Formats<'_>) -> Result<String, Diagnostic> {
    let usage = |problem: String| Err(diag::err_text_format_usage(problem));
    let [spec, extra @ ..] = args else {
        return usage("no spec".to_string());
    };
    if spec.is_none() {
        return usage("no spec".to_string());
    }
    if !extra.is_empty() {
        return usage(format!("{} extra argument(s)", extra.len()));
    }
    let Some(spec) = spec.as_str().filter(|spec| is_format_spec(spec, formats)) else {
        return usage(format!("spec {}", python_repr(spec)?));
    };
    let cell = match cell_of(value)? {
        Some(cell) => cell,
        None => return usage(format!("value of type '{}' is not a number or date", python_type(value))),
    };
    if let CellText::Text(text) = &cell {
        if !is_temporal_text(text) && python_float(text)?.is_none() {
            return usage(format!("value {} is not a number or date", diagnostic::py_str_repr(text)));
        }
    }
    let narrative = if formats.alias_notation(spec).is_some() {
        AuthoredFormat::named(spec)
    } else {
        AuthoredFormat { notation: Some(Notation::Narrative), ..AuthoredFormat::named(spec) }
    };
    cell_text(&cell, &narrative, formats)
}

/// A value as `format_table_cell_value` reads it; `None` for one `format` refuses.
enum CellText {
    Null,
    Number(f64),
    Decimal(String),
    Temporal(String, ColumnType),
    Text(String),
}

fn cell_of(value: &Value) -> Result<Option<CellText>, Diagnostic> {
    if let Some(cell) = value.downcast_object_ref::<Cell>() {
        return Ok(match &cell.kind {
            CellKind::Decimal(wire) => Some(CellText::Decimal(wire.clone())),
            CellKind::Temporal(wire, declared) => Some(CellText::Temporal(wire.clone(), declared.clone())),
            CellKind::Other => None,
        });
    }
    Ok(match value.kind() {
        ValueKind::None => Some(CellText::Null),
        ValueKind::Number => Some(CellText::Number(f64::try_from(value.clone()).map_err(engine)?)),
        ValueKind::String => value.as_str().map(|s| CellText::Text(s.to_string())),
        _ => None,
    })
}

/// Python's `repr` of a scalar argument; any other value is named by its kind.
fn python_repr(value: &Value) -> Result<String, Diagnostic> {
    match value.kind() {
        ValueKind::String => Ok(diagnostic::py_str_repr(value.as_str().unwrap_or_default())),
        ValueKind::Bool => Ok(if value.is_true() { "True" } else { "False" }.to_string()),
        ValueKind::Number if value.is_integer() => Ok(value.to_string()),
        ValueKind::Number => Ok(float_repr(f64::try_from(value.clone()).map_err(engine)?)),
        kind => Ok(kind.to_string()),
    }
}

/// `type(value).__name__` for a value `format` takes as no number or date, where
/// the value maps to one Python type; any other (a bytes, JSON, list or struct cell,
/// an undefined name) is named by its minijinja kind, a wording production lacks.
fn python_type(value: &Value) -> String {
    if let Some(cell) = value.downcast_object_ref::<Cell>() {
        return if cell.text.is_some() { "time" } else { "cell" }.to_string();
    }
    if let Some(list) = value.downcast_object_ref::<VariableList>() {
        return match list.kind {
            ListKind::Multiselect => "MultiselectValue",
            ListKind::Daterange => "DaterangeValue",
        }
        .to_string();
    }
    match value.kind() {
        ValueKind::Bool => "bool".to_string(),
        ValueKind::Seq => "list".to_string(),
        ValueKind::Map => "dict".to_string(),
        kind => kind.to_string(),
    }
}

/// `_is_format_spec`: an alias the board defines, a predefined name, a time spec with
/// only the directives the painter knows, or a d3 spec.
fn is_format_spec(spec: &str, formats: Formats<'_>) -> bool {
    if formats.defines(spec) {
        return true;
    }
    let Ok(resolved) = resolve_format(&AuthoredFormat::named(spec), formats) else {
        return false;
    };
    if resolved.is_empty() {
        return false;
    }
    if is_predefined_name(&resolved) {
        return true;
    }
    if is_time_format(&resolved) {
        return table_dates::validate_spec(&resolved).is_ok();
    }
    d3rs::format::parse_parts(&resolved).is_ok()
}

/// `is_temporal_value` over text: an ISO date or timestamp on a real calendar day.
fn is_temporal_text(text: &str) -> bool {
    text.len() >= 10 && matches!(table_dates::parse_temporal(text.trim(), &ColumnType::String), Ok(Some(_)))
}

/// Number text as production's `_is_number_text` reads it (recorded probes),
/// `None` where it is no number: ASCII whitespace (`\t\n\v\f\r` and space)
/// stripped, PEP 515 underscores between digits; `\x1c`-`\x1f` are no whitespace.
/// Production also reads Unicode digits and spaces, which `str::parse` does not,
/// so text holding one is refused by name; any other non-ASCII character makes
/// text no number in both.
pub(crate) fn python_float(text: &str) -> Result<Option<f64>, Diagnostic> {
    if text.chars().any(|c| !c.is_ascii() && (c.is_numeric() || c.is_whitespace())) {
        return Err(Diagnostic::refused(refuse!("number text with a non-ASCII digit or space is not supported")));
    }
    let trimmed = text.trim_matches(|c: char| c.is_ascii_whitespace() || c == '\x0b');
    let bytes = trimmed.as_bytes();
    let digit = |i: Option<usize>| i.and_then(|i| bytes.get(i)).is_some_and(u8::is_ascii_digit);
    let mut plain = String::with_capacity(trimmed.len());
    for (i, c) in trimmed.char_indices() {
        if c != '_' {
            plain.push(c);
        } else if !(digit(i.checked_sub(1)) && digit(Some(i + 1))) {
            return Ok(None);
        }
    }
    Ok(plain.parse().ok())
}

const NULL_DISPLAY: &str = "\u{2014}";

/// `format_table_cell_value` for the values text can hand it.
fn cell_text(cell: &CellText, format: &AuthoredFormat, formats: Formats<'_>) -> Result<String, Diagnostic> {
    let temporal = match cell {
        CellText::Null => return Ok(NULL_DISPLAY.to_string()),
        CellText::Text(text) if text.trim().is_empty() => return Ok(NULL_DISPLAY.to_string()),
        CellText::Temporal(wire, declared) => Some((wire.as_str(), declared.clone())),
        CellText::Text(text) if is_temporal_text(text) => Some((text.as_str(), ColumnType::String)),
        _ => None,
    };
    if let Some((wire, declared)) = temporal {
        return temporal_text(wire, &declared, format, formats);
    }
    let resolved = resolve_format(format, formats)?;
    let number = match cell {
        CellText::Number(value) => *value,
        CellText::Decimal(wire) => python_float(wire)?.ok_or_else(|| diag::err_internal(format!("decimal cell {wire:?}")))?,
        CellText::Text(text) => match python_float(text)? {
            Some(value) if !(column_types::is_date_like(text) && is_time_format(&resolved)) => value,
            _ => return Ok(text.clone()),
        },
        CellText::Null | CellText::Temporal(..) => return Err(diag::err_internal("a temporal or null cell reached the number path")),
    };
    if TEXT_NAMES.contains(&resolved.as_str()) {
        return Err(diag::err_table_format_kind_mismatch(
            resolved.as_str(),
            format!("`{resolved}` formats text, but this column holds numbers."),
        ));
    }
    if is_time_format(&resolved) {
        return Err(diag::err_table_format_kind_mismatch(
            resolved,
            table_dates::NUMERIC_REMEDY,
        ));
    }
    format_value(number, format, formats, &active_number_locale())
}

fn temporal_text(wire: &str, declared: &ColumnType, format: &AuthoredFormat, formats: Formats<'_>) -> Result<String, Diagnostic> {
    let dated = column_format(FormatKind::Time, Tiers { own: Some(format), ..Tiers::default() }, formats, None, None, &[])?;
    if !is_time_format(&dated.spec) {
        return Err(diag::err_table_format_kind_mismatch(dated.spec, table_dates::STRFTIME_REMEDY));
    }
    let parsed = table_dates::parse_temporal(wire.trim(), declared);
    match table_dates::paint_cell(parsed, &dated.spec, &dated.prefix, &dated.suffix) {
        Ok(Some(text)) => Ok(text),
        Ok(None) => Err(diag::err_internal(format!("temporal cell {wire:?} holds no date"))),
        Err(table_dates::TemporalError::Invalid(reason)) => {
            Err(diag::err_table_temporal_format_invalid(wire, dated.spec.as_str(), reason))
        }
        Err(table_dates::TemporalError::Unported(why)) => {
            Err(Diagnostic::refused(refuse!("a timestamp or strftime directive the painter does not reach is not supported: {why}")))
        }
    }
}

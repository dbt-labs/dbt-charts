//! A board's variable values for one run: what a caller set (a URL, `--var`, an API binding)
//! against what each variable declares, as Python's execute path (`merge_board_variables`) and
//! render path (`_render_variables`) end with. A value is read as JSON, merged over the
//! defaults, checked against the static options, coerced to its input's type for queries and
//! narrowed for render; a daterange preset is dated on the [`Clock`] the caller passes, which
//! compile never reads (`plan` supplies its own).
//!
//! Python's dynamic value is [`Val`]. Where it holds what no [`VariableValue`] can (a mapping, a
//! nested list), resolution refuses it; an integer past `i64` reads as a float, and a digit
//! outside ASCII is not a digit.

use crate::daterange::{self, GRAMMAR};
use board_model::variable::{DefaultValue, InputType, Member, StaticOptions, Variable};
use board_model::variable_value::{Atom, VariableValue};
use diagnostic::{diag, py_repr, py_str_repr, Diagnostic};
use indexmap::IndexMap;
use jiff::civil::{Date as Day, ISOWeekDate, Weekday};
use model_core::Engine;
use serde_json::Value;

/// The board's date and how its weeks and fiscal years run. `week_start` counts from Sunday.
/// It is `plan`'s own, from [`Clock::at`]; compile's `HostInputs.today` never reaches it.
#[derive(Debug, Clone, Copy)]
pub struct Clock {
    pub today: migrate::Date,
    pub fiscal_start_month: u32,
    pub week_start: u32,
}

impl Clock {
    /// The date at `epoch_seconds` in `time_zone`: the board's day, which is not UTC's.
    pub fn at(
        epoch_seconds: i64,
        time_zone: &str,
        fiscal_start_month: u32,
        week_start: u32,
    ) -> Result<Self, String> {
        let at = jiff::Timestamp::from_second(epoch_seconds)
            .and_then(|t| t.in_tz(time_zone))
            .map_err(|e| e.to_string())?;
        let day = at.date();
        let (year, month, day) = (i32::from(day.year()), day.month(), day.day());
        Ok(Self {
            today: (year, month.unsigned_abs().into(), day.unsigned_abs().into()),
            fiscal_start_month,
            week_start,
        })
    }
}

/// A value as Python holds it.
#[derive(Debug, Clone, PartialEq)]
enum Val {
    Null,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    /// A `datetime.date`, as ISO.
    Date(String),
    List(Vec<Val>),
    Map(Value),
}

impl Val {
    fn from_json(v: &Value) -> Self {
        match v {
            Value::Null => Self::Null,
            Value::Bool(b) => Self::Bool(*b),
            Value::Number(n) => n
                .as_i64()
                .map(Self::Int)
                .unwrap_or_else(|| Self::Float(n.as_f64().unwrap_or(f64::NAN))),
            Value::String(s) => Self::Str(s.clone()),
            Value::Array(items) => Self::List(items.iter().map(Self::from_json).collect()),
            Value::Object(_) => Self::Map(v.clone()),
        }
    }

    fn repr(&self) -> String {
        match self {
            Self::Null => "None".into(),
            Self::Bool(true) => "True".into(),
            Self::Bool(false) => "False".into(),
            Self::Int(i) => i.to_string(),
            Self::Float(f) if f.is_nan() => "nan".into(),
            Self::Float(f) if f.is_infinite() => (if *f > 0.0 { "inf" } else { "-inf" }).into(),
            Self::Float(f) => py_repr(&Value::from(*f)),
            Self::Str(s) => py_str_repr(s),
            Self::Date(iso) => {
                let n = |r: std::ops::Range<usize>| iso[r].parse::<u32>().unwrap_or(0);
                format!("datetime.date({}, {}, {})", n(0..4), n(5..7), n(8..10))
            }
            Self::List(items) => {
                let items: Vec<String> = items.iter().map(Self::repr).collect();
                format!("[{}]", items.join(", "))
            }
            Self::Map(v) => repr_json(v),
        }
    }

    /// `str(value)`.
    fn text(&self) -> String {
        match self {
            Self::Str(s) | Self::Date(s) => s.clone(),
            other => other.repr(),
        }
    }

    fn type_name(&self) -> &'static str {
        match self {
            Self::Null => "NoneType",
            Self::Bool(_) => "bool",
            Self::Int(_) => "int",
            Self::Float(_) => "float",
            Self::Str(_) => "str",
            Self::Date(_) => "date",
            Self::List(_) => "list",
            Self::Map(_) => "dict",
        }
    }

    /// `variable_value_is_absent`: no value, a blank string or an empty list.
    fn is_absent(&self) -> bool {
        match self {
            Self::Null => true,
            Self::Str(s) => s.trim().is_empty(),
            Self::List(items) => items.is_empty(),
            _ => false,
        }
    }

    fn atom(&self) -> Result<Option<Atom>, String> {
        Ok(Some(match self {
            Self::Null => return Ok(None),
            Self::Bool(b) => Atom::Bool(*b),
            Self::Int(i) => Atom::Int(*i),
            Self::Float(f) => Atom::Float(*f),
            Self::Str(s) => Atom::Text(s.clone()),
            Self::Date(d) => Atom::Date(d.clone()),
            Self::List(_) | Self::Map(_) => return Err(self.type_name().to_string()),
        }))
    }
}

/// `repr()` of a parsed JSON mapping, whose members `py_repr` leaves in JSON.
fn repr_json(v: &Value) -> String {
    match v {
        Value::Object(map) => {
            let pairs: Vec<String> = map
                .iter()
                .map(|(k, v)| format!("{}: {}", py_str_repr(k), repr_json(v)))
                .collect();
            format!("{{{}}}", pairs.join(", "))
        }
        Value::Array(items) => {
            let items: Vec<String> = items.iter().map(repr_json).collect();
            format!("[{}]", items.join(", "))
        }
        other => py_repr(other),
    }
}

/// One declared variable, as the board's registry holds it.
#[derive(Debug, Clone)]
pub struct Declared {
    name: String,
    input: InputType,
    member: Member,
    /// `options.static` as strings: the options a value is checked against.
    options: Vec<String>,
    default: Option<Val>,
}

fn number_text(f: f64) -> String {
    if f.fract() == 0.0 && f.abs() < 1e15 {
        format!("{f:.0}")
    } else {
        py_repr(&Value::from(f))
    }
}

fn default_val(v: &DefaultValue) -> Result<Option<Val>, String> {
    Ok(Some(match v {
        DefaultValue::Null => return Ok(None),
        DefaultValue::Bool(b) => Val::Bool(*b),
        DefaultValue::Text(s) => Val::Str(s.clone()),
        DefaultValue::Date(s) => Val::Date(s.clone()),
        DefaultValue::DateTime(s) => return Err(format!("{s} is a datetime, not a date")),
        DefaultValue::Number(n) => match n.as_i64() {
            Some(i) => Val::Int(i),
            None => Val::Float(n.as_f64().ok_or("a number out of range")?),
        },
        DefaultValue::List(items) => Val::List(
            items
                .iter()
                .map(|i| default_val(i).map(|v| v.unwrap_or(Val::Null)))
                .collect::<Result<_, _>>()?,
        ),
        DefaultValue::Map(_) => return Err("a mapping default".into()),
    }))
}

/// The control `input: auto` settles on (`detect_variable_input_type`).
fn detect(var: &Variable) -> InputType {
    let options = var.options.as_ref();
    let has_options = options.is_some_and(|o| {
        o.query.is_some()
            || match &o.r#static {
                Some(StaticOptions::Strings(s)) => !s.is_empty(),
                Some(StaticOptions::Numbers(n)) => !n.is_empty(),
                None => false,
            }
    });
    board_model::variable::InputSignals {
        list_default: matches!(var.default, Some(DefaultValue::List(_))),
        slider_field: var.min.is_some() || var.max.is_some() || var.step.is_some(),
        bool_default: matches!(var.default, Some(DefaultValue::Bool(_))),
        has_options,
        has_column: var.column.as_deref().is_some_and(|c| !c.is_empty()),
    }
    .detect()
}

impl Declared {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn of(name: &str, var: &Variable) -> Result<Self, String> {
        let input = match &var.input {
            Engine::Value(input) => *input,
            Engine::Rule(_) => detect(var),
        };
        let statics = var.options.as_ref().and_then(|o| o.r#static.as_ref());
        let member = Member::of(var.data_type, statics);
        let options = match statics {
            Some(StaticOptions::Strings(s)) => s.clone(),
            Some(StaticOptions::Numbers(n)) => n.iter().map(|f| number_text(*f)).collect(),
            None => Vec::new(),
        };
        let default = match &var.default {
            Some(v) => default_val(v).map_err(|e| format!("variable {name}: {e}"))?,
            None => None,
        };
        Ok(Self {
            name: name.to_string(),
            input,
            member,
            options,
            default,
        })
    }

    fn is(&self, input: InputType) -> bool {
        self.input == input
    }
}

fn invalid(d: &Declared, problem: impl Into<String>) -> Diagnostic {
    diag::err_variable_value_invalid(d.name.as_str(), problem.into())
}

// Python's `int()` and `float()` take `_` between digits.
fn without_underscores(text: &str) -> Option<String> {
    let chars: Vec<char> = text.chars().collect();
    let mut out = String::with_capacity(text.len());
    for (i, c) in chars.iter().enumerate() {
        if *c == '_' {
            let digit = |j: Option<usize>| {
                j.and_then(|j| chars.get(j))
                    .is_some_and(char::is_ascii_digit)
            };
            if !(digit(i.checked_sub(1)) && digit(Some(i + 1))) {
                return None;
            }
        } else {
            out.push(*c);
        }
    }
    Some(out)
}

fn py_float(text: &str) -> Option<f64> {
    without_underscores(text.trim())?.parse().ok()
}

fn py_int(text: &str) -> Option<i64> {
    let clean = without_underscores(text.trim())?;
    let digits = clean.strip_prefix(['+', '-']).unwrap_or(&clean);
    (!digits.is_empty() && digits.bytes().all(|b| b.is_ascii_digit()))
        .then(|| clean.parse().ok())?
}

/// `date.fromisoformat`: `YYYY-MM-DD`, `YYYYMMDD` and ISO week dates (`2024-W10-5`, `2024W105`).
fn py_date(text: &str) -> Option<Day> {
    let text = text.trim();
    let digits = |s: &str| {
        s.bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| s.parse::<i16>().ok())?
    };
    let year = digits(text.get(..4)?).filter(|y| *y >= 1)?;
    let extended = text.get(4..5) == Some("-");
    let rest = text.get(if extended { 5.. } else { 4.. })?;
    let dash = |s: &str, at: usize| (s.get(at..=at) == Some("-")) == extended;
    if let Some(w) = rest.strip_prefix('W') {
        let (week, day) = match w.len() {
            2 => (w, "1"),
            4 if extended && dash(w, 2) => (w.get(..2)?, w.get(3..)?),
            3 if !extended => (w.get(..2)?, w.get(2..)?),
            _ => return None,
        };
        let weekday = Weekday::from_monday_one_offset(digits(day)?.try_into().ok()?).ok()?;
        return ISOWeekDate::new(year, digits(week)?.try_into().ok()?, weekday)
            .ok()
            .map(|w| w.date());
    }
    let (month, day) = match rest.len() {
        5 if extended && dash(rest, 2) => (rest.get(..2)?, rest.get(3..)?),
        4 if !extended => (rest.get(..2)?, rest.get(2..)?),
        _ => return None,
    };
    Day::new(
        year,
        digits(month)?.try_into().ok()?,
        digits(day)?.try_into().ok()?,
    )
    .ok()
}

fn coerce_date(d: &Declared, v: &Val) -> Result<Val, Diagnostic> {
    match v {
        Val::Date(_) => Ok(v.clone()),
        Val::Str(s) => py_date(s)
            .map(|day| Val::Date(day.to_string()))
            .ok_or_else(|| {
                invalid(
                    d,
                    format!(
                        "date value must be an ISO date (YYYY-MM-DD), got {}",
                        v.repr()
                    ),
                )
            }),
        other => Err(invalid(
            d,
            format!("date value must be a string, got {}", other.type_name()),
        )),
    }
}

fn finite(d: &Declared, f: f64) -> Result<Val, Diagnostic> {
    if f.is_finite() {
        return Ok(Val::Float(f));
    }
    Err(invalid(
        d,
        format!("number value must be finite, got {}", Val::Float(f).repr()),
    ))
}

fn coerce_number(d: &Declared, v: &Val) -> Result<Val, Diagnostic> {
    match v {
        Val::Bool(_) => Err(invalid(d, "number value must be numeric, got a boolean")),
        Val::Int(_) => Ok(v.clone()),
        Val::Float(f) => finite(d, *f),
        Val::Str(s) => {
            let text = s.trim();
            if let Some(i) = py_int(text) {
                return Ok(Val::Int(i));
            }
            match py_float(text) {
                Some(f) => finite(d, f),
                None => Err(invalid(
                    d,
                    format!("number value must be numeric, got {}", py_str_repr(text)),
                )),
            }
        }
        other => Err(invalid(
            d,
            format!("number value must be numeric, got {}", other.type_name()),
        )),
    }
}

fn coerce_bool(d: &Declared, v: &Val) -> Result<Val, Diagnostic> {
    let text = match v {
        Val::Bool(_) => return Ok(v.clone()),
        Val::Str(s) => s.trim().to_lowercase(),
        _ => String::new(),
    };
    match text.as_str() {
        "true" | "1" | "yes" | "on" => Ok(Val::Bool(true)),
        "false" | "0" | "no" | "off" => Ok(Val::Bool(false)),
        _ => Err(invalid(
            d,
            format!("checkbox value must be a boolean, got {}", v.repr()),
        )),
    }
}

fn coerce_member(d: &Declared, v: &Val) -> Result<Val, Diagnostic> {
    match d.member {
        Member::Number => coerce_number(d, v),
        Member::Date => coerce_date(d, v),
        Member::Boolean => coerce_bool(d, v),
        Member::Text => Ok(v.clone()),
    }
}

/// A multiselect's value as a list: unset is `[]`, a scalar is its one member.
fn members(v: &Val) -> Vec<Val> {
    match v {
        Val::List(items) => items.clone(),
        Val::Null => Vec::new(),
        Val::Str(s) if s.trim().is_empty() => Vec::new(),
        other => vec![other.clone()],
    }
}

fn require_parsed_list(d: &Declared, v: &Val) -> Result<(), Diagnostic> {
    match v {
        Val::Str(s) if s.trim_start().starts_with('[') => Err(invalid(
            d,
            format!("multiselect value must be a JSON list, got {}", v.repr()),
        )),
        _ => Ok(()),
    }
}

fn require_preset(d: &Declared, name: &str) -> Result<daterange::Preset, Diagnostic> {
    daterange::parse(name).ok_or_else(|| {
        invalid(
            d,
            format!(
                "daterange value must be [start, end] or a preset ({GRAMMAR}), got {}",
                py_str_repr(name)
            ),
        )
    })
}

/// A preset dated on the clock, as ISO days with no start for `before_previous`.
fn window(d: &Declared, name: &str, clock: &Clock) -> Result<(Option<String>, String), Diagnostic> {
    let preset = require_preset(d, name)?;
    let (y, m, day) = clock.today;
    let today = i16::try_from(y)
        .ok()
        .and_then(|y| Day::new(y, m.try_into().ok()?, day.try_into().ok()?).ok())
        .ok_or_else(|| invalid(d, format!("the board's date {y}-{m}-{day} is not a date")))?;
    let (start, end) = preset
        .window(
            today,
            clock.fiscal_start_month.into(),
            clock.week_start.into(),
        )
        .map_err(|e| invalid(d, format!("preset {name} is out of range: {e}")))?;
    Ok((start.map(|s| s.to_string()), end.to_string()))
}

/// `coerce_variable_values` then `resolve_daterange_presets`: the value queries bind.
fn for_queries(d: &Declared, v: &Val, clock: &Clock) -> Result<Val, Diagnostic> {
    if d.is(InputType::Multiselect) {
        require_parsed_list(d, v)?;
        let coerced: Result<Vec<Val>, _> = members(v).iter().map(|m| coerce_member(d, m)).collect();
        return coerced.map(Val::List);
    }
    let scalar = matches!(
        d.input,
        InputType::Date
            | InputType::Datepicker
            | InputType::Number
            | InputType::Slider
            | InputType::Range
            | InputType::Checkbox
            | InputType::Select
            | InputType::Radio
    );
    match (&d.input, v) {
        (_, Val::Null) => Ok(Val::Null),
        (_, Val::Str(s)) if s.trim().is_empty() && scalar => Ok(Val::Null),
        (InputType::Select | InputType::Radio, _) => coerce_member(d, v),
        (InputType::Date | InputType::Datepicker, _) => coerce_date(d, v),
        (InputType::Daterange, _) if v.is_absent() => Ok(Val::Null),
        (InputType::Daterange, Val::Str(name)) => {
            let (start, end) = window(d, name, clock)?;
            Ok(Val::List(vec![
                start.map_or(Val::Null, Val::Date),
                Val::Date(end),
            ]))
        }
        (InputType::Daterange, Val::List(ends)) if ends.len() == 2 => ends
            .iter()
            .map(|e| match e {
                Val::Null => Ok(Val::Null),
                Val::Str(s) if s.trim().is_empty() => Ok(Val::Null),
                other => coerce_date(d, other),
            })
            .collect::<Result<_, _>>()
            .map(Val::List),
        (InputType::Daterange, _) => Err(invalid(
            d,
            format!("daterange value must be [start, end], got {}", v.repr()),
        )),
        (InputType::Number | InputType::Slider | InputType::Range, _) => coerce_number(d, v),
        (InputType::Checkbox, _) => coerce_bool(d, v),
        _ => Ok(v.clone()),
    }
}

/// `normalize_render_values` for one variable: a multiselect as a list, a daterange preset as
/// its ISO ends (a blank start for `before_previous`), the rest as set.
fn for_render(d: &Declared, v: &Val, clock: &Clock) -> Result<Val, Diagnostic> {
    match v {
        _ if d.is(InputType::Multiselect) => Ok(Val::List(members(v))),
        Val::Str(name) if d.is(InputType::Daterange) && !v.is_absent() => {
            let (start, end) = window(d, name, clock)?;
            Ok(Val::List(vec![
                Val::Str(start.unwrap_or_default()),
                Val::Str(end),
            ]))
        }
        _ => Ok(v.clone()),
    }
}

fn held(d: &Declared, v: &Val) -> Result<VariableValue, Diagnostic> {
    let cannot = |what: &str| invalid(d, format!("a {what} cannot be held as a variable value"));
    let slot = |v: &Val| v.atom().map_err(|what| cannot(&what));
    Ok(match v {
        Val::List(ends) if d.is(InputType::Daterange) && ends.is_empty() => VariableValue::Unset,
        Val::List(ends) if d.is(InputType::Daterange) => match ends.as_slice() {
            [start, end] => VariableValue::Daterange {
                start: slot(start)?,
                end: slot(end)?,
            },
            _ => return Err(cannot("daterange of other than two ends")),
        },
        Val::List(items) => {
            VariableValue::Multiselect(items.iter().map(slot).collect::<Result<_, _>>()?)
        }
        other => slot(other)?.map_or(VariableValue::Unset, VariableValue::One),
    })
}

/// The `options` of a choice value of `d`, or none to check against.
fn check_options(d: &Declared, given: &Val) -> Result<(), Diagnostic> {
    let choice = matches!(
        d.input,
        InputType::Select | InputType::Radio | InputType::Multiselect
    );
    if !choice || given.is_absent() || d.options.is_empty() {
        return Ok(());
    }
    let multiselect = d.is(InputType::Multiselect);
    if multiselect {
        require_parsed_list(d, given)?;
    }
    let listed = if multiselect {
        members(given)
    } else {
        vec![given.clone()]
    };
    for member in listed {
        let text = member.text();
        let known = d.options.contains(&text)
            || py_float(&text).is_some_and(|n| {
                d.options
                    .iter()
                    .any(|o| py_float(o).is_some_and(|f| f == n))
            });
        if !known {
            return Err(diag::err_variable_value_not_in_options(
                d.name.as_str(),
                value_of(&member),
                d.options.as_slice(),
            ));
        }
    }
    Ok(())
}

fn value_of(v: &Val) -> Value {
    match v {
        Val::Null => Value::Null,
        Val::Bool(b) => Value::from(*b),
        Val::Int(i) => Value::from(*i),
        Val::Float(f) => Value::from(*f),
        Val::Str(s) | Val::Date(s) => Value::from(s.as_str()),
        Val::List(items) => Value::Array(items.iter().map(value_of).collect()),
        Val::Map(m) => m.clone(),
    }
}

/// `values` as queries bind them and as render holds them, by variable name in declaration order.
#[derive(Debug, Clone, PartialEq)]
pub struct Resolved {
    pub query: IndexMap<String, VariableValue>,
    pub render: IndexMap<String, VariableValue>,
    /// The daterange variables whose value is a preset, by name, to the preset as written: what
    /// render publishes and links by, where `query` and `render` hold the dates it names.
    pub presets: IndexMap<String, String>,
}

/// The host's `presets` against the board's declarations: each names a declared daterange variable
/// and a preset the grammar knows. A host that passes anything else has a bug, so this is an
/// error, never an ignored entry.
pub fn check_presets<'a>(
    declared: impl IntoIterator<Item = (&'a str, &'a Variable)>,
    presets: &IndexMap<String, String>,
) -> Result<(), String> {
    let declared: Vec<_> = declared.into_iter().collect();
    for (name, id) in presets {
        let Some((_, var)) = declared.iter().find(|(n, _)| n == name) else {
            return Err(format!("presets: {name} is not a declared variable"));
        };
        if !Declared::of(name, var)?.is(InputType::Daterange) {
            return Err(format!("presets: {name} is not a daterange variable"));
        }
        if daterange::parse(id).is_none() {
            return Err(format!(
                "presets: {name}: {} is not a preset ({GRAMMAR})",
                py_str_repr(id)
            ));
        }
    }
    Ok(())
}

/// What `given` (JSON, in the order the caller set it) makes of each declared variable, or the
/// first refusal: a name the board does not declare, a value outside the static options (only
/// when `strict`: a browser's query string may carry any value), a value its input cannot read.
pub fn resolve(
    declared: &[Declared],
    given: &IndexMap<String, Value>,
    clock: &Clock,
    strict: bool,
) -> Result<Resolved, Diagnostic> {
    let by_name = |name: &str| declared.iter().find(|d| d.name == name);
    let mut set: IndexMap<&str, Val> = IndexMap::new();
    for (name, raw) in given {
        if by_name(name).is_none() {
            let mut available: Vec<&str> = declared.iter().map(|d| d.name.as_str()).collect();
            available.sort_unstable();
            return Err(diag::err_unknown_variable_override(
                name.as_str(),
                available,
            ));
        }
        // A string a URL carries for a list or a mapping is its JSON.
        let val = match raw {
            Value::String(s) if s.starts_with(['[', '{']) => {
                serde_json::from_str(s).map_or_else(|_| Val::Str(s.clone()), |v| Val::from_json(&v))
            }
            other => Val::from_json(other),
        };
        set.insert(name.as_str(), val);
    }
    if strict {
        for (name, val) in &set {
            if let Some(d) = by_name(name) {
                check_options(d, val)?;
            }
        }
    }
    let merged: Vec<(&Declared, Val)> = declared
        .iter()
        .map(|d| {
            let val = set.get(d.name.as_str()).cloned();
            (d, val.or_else(|| d.default.clone()).unwrap_or(Val::Null))
        })
        .collect();
    let mut out = Resolved {
        query: IndexMap::new(),
        render: IndexMap::new(),
        presets: IndexMap::new(),
    };
    for (d, v) in &merged {
        if let Val::Str(name) = v {
            if d.is(InputType::Daterange) && !v.is_absent() {
                out.presets.insert(d.name.clone(), name.clone());
            }
        }
    }
    for (d, v) in &merged {
        out.query
            .insert(d.name.clone(), held(d, &for_queries(d, v, clock)?)?);
    }
    for (d, v) in &merged {
        out.render
            .insert(d.name.clone(), held(d, &for_render(d, v, clock)?)?);
    }
    Ok(out)
}

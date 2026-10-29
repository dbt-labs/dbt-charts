//! Vega expressions of an authored `axis.labels.expr`, evaluated the way Vega's generated JavaScript runs them.
//!
//! The grammar is vega-expression's (ECMAScript literals, member access, calls into Vega's function table,
//! every operator but assignment); the values are JavaScript's (`undefined`, arrays, objects, `Date`, `RegExp`),
//! with their coercions; the function table is vega-expression's and vega-functions'. A tick evaluates with
//! `datum.value`, `datum.label` and `datum.index` (`i / (n - 1 || 1)`), and paints `(line + '').trim()` for each
//! line of the result: `null` and `undefined` paint nothing, an array paints one line per element.
//!
//! Three failures exist. [`EvalError::Unknown`]: Vega's compiler rejects the expression (production reports
//! `ERR-INTERNAL`). [`EvalError::Throws`]: the generated code throws while painting (a bar paints no marks,
//! `ERR-CHART-PAINTED-NO-MARKS`; the other families refuse, see [`throw_diagnostic`]). [`EvalError::Unmodeled`]: the expression reads state of
//! the view Vega evaluates it in that this engine does not carry (`item.bounds`, `item.mark.items`, an axis no
//! family laid out); the view it does carry is [`view`].

#[cfg(test)]
mod corpus_tests;
#[cfg(test)]
mod tests;
mod bound;
pub(crate) mod colors;
mod date;
mod funcs;
mod geo;
mod jsre;
mod parse;
mod scales;
mod val;
pub mod view;

use parse::{Node, Parser};
pub use val::{EvalError, Text, Val};
use val::{js_rem, throws, to_int32, Res};

/// One axis tick as Vega's `AxisTicks` builds it.
#[derive(Debug, Clone)]
pub struct Tick<'a> {
    /// `datum.value`.
    pub value: Val,
    /// `datum.label`: the axis's own text for the value.
    pub label: &'a str,
    /// `datum.index`: the tick's position in the tick list, `i / (n - 1 || 1)`.
    pub index: f64,
}

impl<'a> Tick<'a> {
    /// The `i`th of `n` ticks.
    pub fn at(i: usize, n: usize, value: Val, label: &'a str) -> Self {
        Tick { value, label, index: i as f64 / (n.saturating_sub(1).max(1)) as f64 }
    }
}

/// A parsed label expression.
#[derive(Debug, Clone)]
pub struct Expr {
    node: Node,
    /// The axis (the scale's name) whose labels it labels, for the `item` props that axis's layout sets.
    axis: &'static str,
}

impl Expr {
    /// Parses `src` as Vega's compiler does.
    pub fn parse(src: &str) -> Res<Self> {
        Parser::parse(src).map(|node| Expr { node, axis: "" })
    }

    /// The expression of the axis `axis` (`"x"` or `"y"`).
    pub fn on(self, axis: &'static str) -> Self {
        Expr { axis, ..self }
    }

    /// Whether the expression reads `datum.label`, which costs the caller a format to compute.
    pub fn uses_label(&self) -> bool {
        self.node.reads_label()
    }

    /// The lines of text one tick paints.
    pub fn eval_lines(&self, tick: &Tick) -> Res<Vec<String>> {
        let _evaluation = val::Evaluation::begin();
        let datum = Val::tuple(vec![
            ("index".into(), Val::Num(tick.index)),
            ("value".into(), tick.value.clone()),
            ("label".into(), Val::string(tick.label.to_string())?),
        ]);
        let reads_item = self.node.reads_item();
        if reads_item {
            match view::item_begin(self.axis, &tick.value) {
                Err(EvalError::Unmodeled(_)) if view::discovering() => return Ok(vec![String::new()]),
                other => other?,
            }
        }
        let evaluated = self.node.eval(&datum);
        if reads_item {
            view::item_end(self.axis, &tick.value, evaluated.as_ref().ok());
        }
        let out = match evaluated {
            // The view is still being collected: what a read of it throws is not yet what Vega's would.
            Err(EvalError::Throws(_) | EvalError::Unmodeled(_)) if view::discovering() => return Ok(vec![String::new()]),
            other => other?,
        };
        let line = |v: &Val| -> Res<String> {
            Ok(match v {
                Val::Undefined | Val::Null => String::new(),
                other => val::trim_js(&other.to_text()?).to_string(),
            })
        };
        match out {
            Val::Array(items) if items.len() > 1 => items.iter().map(line).collect(),
            Val::Array(items) => Ok(vec![items.first().map_or_else(|| Ok(String::new()), line)?]),
            other => Ok(vec![line(&other)?]),
        }
    }

    /// The text one tick paints: its lines joined by [`measure::LINE_SEP`], which a painter measures by the
    /// widest line and the SVG finishes as a `tspan` each.
    pub fn eval(&self, tick: &Tick) -> Res<String> {
        let sep = measure::LINE_SEP.to_string();
        // A line holding the separator itself paints the noncharacter beside it, which looks the same.
        Ok(self.eval_lines(tick)?.iter().map(|l| l.replace(measure::LINE_SEP, "\u{FDD1}")).collect::<Vec<_>>().join(&sep))
    }

    /// The text a band axis paints for its band `raw`, which is both `datum.value` and the default `datum.label`.
    pub fn rewrite_band(&self, raw: &str, index: usize, count: usize) -> Result<String, String> {
        let value = Val::string(raw.to_string()).map_err(|e| describe(&e, &format!("the band {raw:?}")))?;
        self.eval(&Tick::at(index, count, value, raw)).map_err(|e| describe(&e, &format!("the band {raw:?}")))
    }
}

/// What each band of a discrete axis paints: the band itself, or an authored `labels.expr`'s rewrite of it
/// (`labelExpr` runs on `datum.value`, the raw band) at the scale `axis` names.
pub fn band_texts(axis: &'static str, domain: &[String], labels: &crate::common::AxisLabels) -> Result<Vec<String>, String> {
    let Some(src) = labels.expr.as_deref() else { return domain.iter().map(|band| labels.cased(band)).collect() };
    let expr = Expr::parse(src).map(|e| e.on(axis)).map_err(|e| describe(&e, "the axis"))?;
    domain.iter().enumerate().map(|(i, band)| labels.cased(&expr.rewrite_band(band, i, domain.len())?)).collect()
}

const THROWS_MARK: &str = "labels.expr throws on ";
const COMPILER_MARK: &str = "labels.expr fails Vega's compiler: ";

/// A human reading of a failure, naming where it happened. [`recode`] turns the first two back into the
/// diagnostics production raises for them.
pub fn describe(e: &EvalError, at: &str) -> String {
    match e {
        EvalError::Throws(what) => format!("{THROWS_MARK}{at}: {what}"),
        EvalError::Unknown(what) => format!("{COMPILER_MARK}{what}"),
        EvalError::Unmodeled(refusal) => refusal.clone(),
    }
}

/// vl-convert's failure to convert: `what` is the message of a plain `Error`, or of the named class (`RangeError: ...`).
fn conversion_failed(what: &str) -> crate::diagnostic::Diagnostic {
    let named = what.split_once(": ").is_some_and(|(class, _)| class.ends_with("Error") && class.chars().all(|c| c.is_ascii_alphabetic()));
    let error = if named { what.to_string() } else { format!("Error: {what}") };
    crate::diagnostic::Diagnostic::new(
        &crate::codes::ERR_INTERNAL,
        [("message", serde_json::Value::String(format!("Vega-Lite to SVG conversion failed: {error}")))],
    )
}

/// What a chart becomes when its label expression throws: a bar paints no marks (`ERR-CHART-PAINTED-NO-MARKS`),
/// while Vega draws a line, area or scatter chart half-built (or fails inside its scenegraph), which is not drawn here.
pub(crate) fn throw_diagnostic(payload: &crate::Payload) -> crate::diagnostic::Diagnostic {
    match payload.resolved.get("chart_type").and_then(serde_json::Value::as_str) {
        Some(family @ ("line" | "area" | "scatter")) => {
            crate::diagnostic::Diagnostic::refused(crate::refuse!("{family}: a labels.expr that throws on an axis label is not supported"))
        }
        _ => payload.painted_no_marks(),
    }
}

/// The diagnostic production raises for a refusal that carries a [`describe`]d failure: a throw while painting
/// leaves the chart with no marks, a rejected expression fails vl-convert's conversion.
pub(crate) fn recode(d: crate::diagnostic::Diagnostic, payload: &crate::Payload) -> crate::diagnostic::Diagnostic {
    let text = d.to_string();
    if text.contains(THROWS_MARK) {
        throw_diagnostic(payload)
    } else if let Some(at) = text.find(COMPILER_MARK) {
        conversion_failed(&text[at + COMPILER_MARK.len()..])
    } else {
        d
    }
}

/// Every axis `labels.expr` the chart authors.
fn axis_exprs(resolved: &serde_json::Value) -> Vec<String> {
    use serde_json::Value;
    fn walk(v: &Value, in_axis: bool, found: &mut Vec<String>) {
        match v {
            Value::Object(map) => {
                for (key, child) in map {
                    if in_axis && key == "labels" {
                        if let Some(Value::String(src)) = child.get("expr") {
                            found.push(src.clone());
                        }
                    }
                    walk(child, matches!(key.as_str(), "axis_x" | "axis_y"), found);
                }
            }
            Value::Array(items) => items.iter().for_each(|c| walk(c, in_axis, found)),
            _ => {}
        }
    }
    let mut sources = Vec::new();
    walk(resolved, false, &mut sources);
    sources
}

/// Compiles every axis `labels.expr` the chart authors, the way Vega's compiler does before it draws anything: an
/// expression it rejects fails the conversion whether or not this engine would have evaluated it.
pub(crate) fn validate(resolved: &serde_json::Value) -> Result<(), crate::diagnostic::Diagnostic> {
    for src in axis_exprs(resolved) {
        if let Err(EvalError::Unknown(what)) = Expr::parse(&src) {
            return Err(conversion_failed(&what));
        }
    }
    Ok(())
}

/// What an authored axis `labels.expr` reads of the chart it labels.
#[derive(Debug, Default, Clone, Copy)]
pub(crate) struct Needs {
    /// The plot's scales or label geometry, which only the final layout knows: such a chart is painted pass by
    /// pass ([`view::render_passes`]).
    pub scales: bool,
    /// The chart's datasets.
    pub data: bool,
}

pub(crate) fn needs(resolved: &serde_json::Value) -> Needs {
    let mut found = Needs::default();
    for src in axis_exprs(resolved) {
        if let Ok(expr) = Expr::parse(&src) {
            found.scales |= expr.node.reads_view();
            found.data |= expr.node.reads_data();
        }
    }
    found
}

/// A value tick's text under the authored expression `src`: `datum.value` the number, `datum.label` its own text.
pub fn speak_value(scale: &'static str, src: &str, value: f64, label: &str, index: usize, count: usize) -> Result<String, String> {
    let expr = Expr::parse(src).map(|e| e.on(scale)).map_err(|e| describe(&e, "the axis"))?;
    expr.eval(&Tick::at(index, count, Val::Num(value), label)).map_err(|e| describe(&e, &format!("the tick {value}")))
}

impl Val {
    /// A `datum` tuple: an object Vega's dataflow minted.
    pub fn tuple(fields: Vec<(String, Val)>) -> Val {
        let mut fields = fields;
        fields.push((TUPLE_ID.to_string(), Val::Bool(true)));
        Val::Object(fields)
    }

    fn property(&self, key: &Val) -> Res<Val> {
        let key = match key.to_primitive(val::Hint::String) {
            Val::Num(n) => val::num_to_str(n),
            other => other.to_js_string(),
        };
        let own = match self {
            Val::Undefined | Val::Null => return throws(format!("Cannot read properties of {} (reading '{key}')", self.to_js_string())),
            Val::Str(s) => {
                let units: Vec<u16> = s.encode_utf16().collect();
                if key == "length" {
                    return Ok(Val::Num(units.len() as f64));
                }
                index_of(&key, units.len()).map(|i| Val::string(String::from_utf16_lossy(&units[i..=i]))).transpose()?
            }
            Val::Array(items) => {
                if key == "length" {
                    return Ok(Val::Num(items.len() as f64));
                }
                index_of(&key, items.len()).map(|i| items[i].clone())
            }
            Val::Object(fields) => {
                if key == TUPLE_ID {
                    return Ok(Val::Undefined);
                }
                fields.iter().rev().find(|(k, _)| *k == key).map(|(_, v)| v.clone())
            }
            Val::Regex(source, flags) => match key.as_str() {
                "source" => Some(Val::string(val::regex_source(source))?),
                "flags" => Some(Val::string(val::sorted_flags(flags))?),
                "global" => Some(Val::Bool(flags.contains('g'))),
                "ignoreCase" => Some(Val::Bool(flags.contains('i'))),
                "multiline" => Some(Val::Bool(flags.contains('m'))),
                "dotAll" => Some(Val::Bool(flags.contains('s'))),
                "sticky" => Some(Val::Bool(flags.contains('y'))),
                "unicode" => Some(Val::Bool(flags.contains('u'))),
                "hasIndices" => Some(Val::Bool(flags.contains('d'))),
                "lastIndex" => Some(Val::Num(0.0)),
                _ => None,
            },
            Val::Color(c) => c.channel(&key).map(Val::Num),
            Val::Bool(_) | Val::Num(_) | Val::Date(_) | Val::Func(_) => None,
        };
        Ok(own.or_else(|| native_method(self, &key)).unwrap_or(Val::Undefined))
    }
}

/// The built-in methods a value inherits: reading one (it cannot be called from an expression) gives the function,
/// which prints as `function name() { [native code] }`.
fn native_method(v: &Val, key: &str) -> Option<Val> {
    const OBJECT: &[&str] = &[
        "__defineGetter__", "__defineSetter__", "hasOwnProperty", "__lookupGetter__", "__lookupSetter__", "isPrototypeOf",
        "propertyIsEnumerable", "toString", "valueOf", "toLocaleString",
    ];
    const NUMBER: &[&str] = &["toExponential", "toFixed", "toPrecision", "toString", "valueOf", "toLocaleString"];
    const STRING: &[&str] = &[
        "at", "charAt", "charCodeAt", "codePointAt", "concat", "endsWith", "includes", "indexOf", "isWellFormed", "lastIndexOf",
        "localeCompare", "match", "matchAll", "normalize", "padEnd", "padStart", "repeat", "replace", "replaceAll", "search", "slice",
        "split", "startsWith", "substr", "substring", "toLocaleLowerCase", "toLocaleUpperCase", "toLowerCase", "toString", "toUpperCase",
        "toWellFormed", "trim", "trimEnd", "trimStart", "valueOf", "anchor", "big", "blink", "bold", "fixed", "fontcolor", "fontsize",
        "italics", "link", "small", "strike", "sub", "sup",
    ];
    const ARRAY: &[&str] = &[
        "at", "concat", "copyWithin", "entries", "every", "fill", "filter", "find", "findIndex", "findLast", "findLastIndex", "flat",
        "flatMap", "forEach", "includes", "indexOf", "join", "keys", "lastIndexOf", "map", "pop", "push", "reduce", "reduceRight",
        "reverse", "shift", "slice", "some", "sort", "splice", "toLocaleString", "toReversed", "toSorted", "toSpliced", "toString",
        "unshift", "values", "with",
    ];
    const DATE: &[&str] = &[
        "getDate", "getDay", "getFullYear", "getHours", "getMilliseconds", "getMinutes", "getMonth", "getSeconds", "getTime",
        "getTimezoneOffset", "getUTCDate", "getUTCDay", "getUTCFullYear", "getUTCHours", "getUTCMilliseconds", "getUTCMinutes",
        "getUTCMonth", "getUTCSeconds", "getYear", "setDate", "setFullYear", "setHours", "setMilliseconds", "setMinutes", "setMonth",
        "setSeconds", "setTime", "setUTCDate", "setUTCFullYear", "setUTCHours", "setUTCMilliseconds", "setUTCMinutes", "setUTCMonth",
        "setUTCSeconds", "setYear", "toDateString", "toISOString", "toJSON", "toLocaleDateString", "toLocaleString",
        "toLocaleTimeString", "toString", "toTimeString", "toUTCString", "valueOf", "toTemporalInstant",
    ];
    let (methods, constructor): (&[&str], &str) = match v {
        Val::Num(_) => (NUMBER, "Number"),
        Val::Bool(_) => (&["toString", "valueOf"], "Boolean"),
        Val::Str(_) => (STRING, "String"),
        Val::Array(_) => (ARRAY, "Array"),
        Val::Date(_) => (DATE, "Date"),
        Val::Regex(..) => (&["compile", "exec", "test", "toString"], "RegExp"),
        Val::Func(_) => (&["apply", "bind", "call", "toString"], "Function"),
        Val::Object(_) | Val::Color(_) => (&[], "Object"),
        Val::Undefined | Val::Null => return None,
    };
    fn alias(name: &str) -> &str {
        match name {
            "trimLeft" => "trimStart",
            "trimRight" => "trimEnd",
            "toGMTString" => "toUTCString",
            other => other,
        }
    }
    let key = if matches!(v, Val::Str(_) | Val::Date(_)) { alias(key) } else { key };
    let known = key == "constructor" || methods.contains(&key) || OBJECT.contains(&key);
    // `Date.prototype.toTemporalInstant` is not in every engine; the others are in every one this renders with.
    (known && key != "toTemporalInstant").then(|| {
        Val::Func(if key == "constructor" { format!("function {constructor}() {{ [native code] }}") } else { format!("function {key}() {{ [native code] }}") })
    })
}

/// The scenegraph item of a tick's label.
fn item_object(datum: &Val) -> Val {
    let mut fields = vec![
        ("datum".into(), datum.clone()),
        (
            "mark".into(),
            Val::Object(vec![
                ("marktype".into(), Val::Str("text".into())),
                ("role".into(), Val::Str("axis-label".into())),
                ("interactive".into(), Val::Bool(false)),
                ("clip".into(), Val::Bool(false)),
                ("zindex".into(), Val::Num(0.0)),
            ]),
        ),
    ];
    fields.extend(view::item_props());
    Val::Object(fields)
}

const TUPLE_ID: &str = "\u{0}vega_id";

/// A canonical array index (`"0"`, `"12"`, not `"01"` or `"1.0"`) within `len`.
fn index_of(key: &str, len: usize) -> Option<usize> {
    let i: usize = key.parse().ok()?;
    (i.to_string() == key && i < len).then_some(i)
}

impl Node {
    fn reads_data(&self) -> bool {
        match self {
            Node::Lit(_) | Node::Num(..) | Node::Datum | Node::Event | Node::Ident(_) | Node::Item => false,
            Node::Array(items) => items.iter().any(Node::reads_data),
            Node::Object(props) => props.iter().any(|(_, v)| v.reads_data()),
            Node::Member(base, key) => base.reads_data() || key.reads_data(),
            Node::Binary(_, a, b) | Node::Logical(_, a, b) => a.reads_data() || b.reads_data(),
            Node::Call(name, args) => funcs::reads_data(name) || args.iter().any(Node::reads_data),
            Node::Unary(_, a) => a.reads_data(),
            Node::Cond(a, b, c) => a.reads_data() || b.reads_data() || c.reads_data(),
        }
    }

    fn reads_view(&self) -> bool {
        match self {
            Node::Item => true,
            Node::Lit(_) | Node::Num(..) | Node::Datum | Node::Event | Node::Ident(_) => false,
            Node::Array(items) => items.iter().any(Node::reads_view),
            Node::Object(props) => props.iter().any(|(_, v)| v.reads_view()),
            Node::Member(base, key) => base.reads_view() || key.reads_view(),
            Node::Binary(_, a, b) | Node::Logical(_, a, b) => a.reads_view() || b.reads_view(),
            Node::Call(name, args) => funcs::reads_scales(name) || args.iter().any(Node::reads_view),
            Node::Unary(_, a) => a.reads_view(),
            Node::Cond(a, b, c) => a.reads_view() || b.reads_view() || c.reads_view(),
        }
    }

    /// Whether it reads a prop of the label item beyond its `datum` and `mark`.
    fn reads_item(&self) -> bool {
        match self {
            Node::Lit(_) | Node::Num(..) | Node::Datum | Node::Event | Node::Ident(_) | Node::Item => false,
            Node::Array(items) => items.iter().any(Node::reads_item),
            Node::Object(props) => props.iter().any(|(_, v)| v.reads_item()),
            Node::Member(base, key) => match (&**base, &**key) {
                (Node::Item, Node::Lit(Val::Str(k))) => k != "datum" && k != "mark",
                (Node::Item, _) => true,
                _ => base.reads_item() || key.reads_item(),
            },
            Node::Binary(_, a, b) | Node::Logical(_, a, b) => a.reads_item() || b.reads_item(),
            Node::Call(_, args) => args.iter().any(Node::reads_item),
            Node::Unary(_, a) => a.reads_item(),
            Node::Cond(a, b, c) => a.reads_item() || b.reads_item() || c.reads_item(),
        }
    }

    fn reads_label(&self) -> bool {
        match self {
            Node::Datum | Node::Item => true,
            Node::Lit(_) | Node::Num(..) | Node::Event | Node::Ident(_) => false,
            Node::Array(items) => items.iter().any(Node::reads_label),
            Node::Object(props) => props.iter().any(|(_, v)| v.reads_label()),
            Node::Member(base, key) => match (&**base, &**key) {
                (Node::Datum, Node::Lit(Val::Str(k))) if k != "label" => false,
                _ => base.reads_label() || key.reads_label(),
            },
            Node::Binary(_, a, b) | Node::Logical(_, a, b) => a.reads_label() || b.reads_label(),
            Node::Call(_, args) => args.iter().any(Node::reads_label),
            Node::Unary(_, a) => a.reads_label(),
            Node::Cond(a, b, c) => a.reads_label() || b.reads_label() || c.reads_label(),
        }
    }

    /// The value of the node, charged to the evaluation's memory budget.
    fn eval(&self, datum: &Val) -> Res<Val> {
        let value = self.eval_node(datum)?;
        val::charge(value.weight())?;
        Ok(value)
    }

    fn eval_node(&self, datum: &Val) -> Res<Val> {
        Ok(match self {
            Node::Lit(v) => v.clone(),
            Node::Num(n, _) => Val::Num(*n),
            Node::Ident(name) => return Err(EvalError::Unknown(format!("Unrecognized signal name: \"{name}\""))),
            Node::Datum => datum.clone(),
            Node::Item => item_object(datum),
            Node::Event => return throws("event is not defined"),
            Node::Array(items) => Val::Array(items.iter().map(|n| n.eval(datum)).collect::<Res<_>>()?),
            Node::Object(props) => Val::Object(props.iter().map(|(k, n)| Ok((k.clone(), n.eval(datum)?))).collect::<Res<_>>()?),
            Node::Member(base, key) => {
                if let Some(path) = base.item_path() {
                    let name = key.eval(datum)?.to_js_string();
                    let ok = match path.as_str() {
                        "item" => name != "bounds",
                        "item.mark" => !matches!(name.as_str(), "items" | "bounds" | "group"),
                        _ => true,
                    };
                    if !ok {
                        return Err(EvalError::Unmodeled(crate::refuse!("labels.expr reading {path}.{name} is not supported")));
                    }
                }
                base.eval(datum)?.property(&key.eval(datum)?)?
            }
            Node::Call(name, args) => {
                let vals = args.iter().map(|a| a.eval(datum)).collect::<Res<Vec<_>>>()?;
                funcs::call(name, &vals, datum)?
            }
            Node::Unary(op, inner) => {
                let v = inner.eval(datum)?;
                match *op {
                    "!" => Val::Bool(!v.truthy()),
                    "-" => Val::Num(-v.to_number()),
                    "+" => Val::Num(v.to_number()),
                    _ => Val::Num(f64::from(!to_int32(v.to_number()))),
                }
            }
            Node::Logical(op, l, r) => {
                let left = l.eval(datum)?;
                if (*op == "&&") == left.truthy() { r.eval(datum)? } else { left }
            }
            Node::Cond(c, a, b) => {
                if c.eval(datum)?.truthy() { a.eval(datum)? } else { b.eval(datum)? }
            }
            Node::Binary(op, l, r) => {
                let (lv, rv) = (l.eval(datum)?, r.eval(datum)?);
                let same_object = lv.is_object() && rv.is_object() && l.ref_path().is_some() && l.ref_path() == r.ref_path();
                match (*op, same_object) {
                    ("==" | "===", true) => Val::Bool(true),
                    ("!=" | "!==", true) => Val::Bool(false),
                    _ => binary(op, lv, rv)?,
                }
            }
        })
    }

    /// The tick object this expression reads, if it reads one: two reads of it are one JavaScript object.
    fn ref_path(&self) -> Option<String> {
        match self {
            Node::Datum => Some("datum".into()),
            Node::Item => Some("item".into()),
            Node::Member(base, key) => match (base.ref_path()?.as_str(), &**key) {
                ("datum", Node::Lit(Val::Str(k))) if k == "value" => Some("datum.value".into()),
                ("item", Node::Lit(Val::Str(k))) if k == "datum" => Some("datum".into()),
                ("item", Node::Lit(Val::Str(k))) if k == "mark" => Some("item.mark".into()),
                _ => None,
            },
            _ => None,
        }
    }

    /// `item` / `item.mark`, the scenegraph objects an expression can walk into.
    fn item_path(&self) -> Option<String> {
        match self {
            Node::Item => Some("item".into()),
            Node::Member(base, key) => match (base.item_path()?.as_str(), &**key) {
                ("item", Node::Lit(Val::Str(k))) if k == "mark" => Some("item.mark".into()),
                _ => None,
            },
            _ => None,
        }
    }
}

fn binary(op: &str, l: Val, r: Val) -> Res<Val> {
    let num = |f: fn(f64, f64) -> f64| Val::Num(f(l.to_number(), r.to_number()));
    Ok(match op {
        "+" => l.add(&r)?,
        "-" => num(|a, b| a - b),
        "*" => num(|a, b| a * b),
        "/" => num(|a, b| a / b),
        "%" => num(js_rem),
        "==" => Val::Bool(l.loose_eq(&r)),
        "!=" => Val::Bool(!l.loose_eq(&r)),
        "===" => Val::Bool(l.strict_eq(&r)),
        "!==" => Val::Bool(!l.strict_eq(&r)),
        "<" => Val::Bool(l.less_than(&r) == Some(true)),
        ">" => Val::Bool(r.less_than(&l) == Some(true)),
        "<=" => Val::Bool(r.less_than(&l) == Some(false)),
        ">=" => Val::Bool(l.less_than(&r) == Some(false)),
        "&" => Val::Num(f64::from(to_int32(l.to_number()) & to_int32(r.to_number()))),
        "|" => Val::Num(f64::from(to_int32(l.to_number()) | to_int32(r.to_number()))),
        "^" => Val::Num(f64::from(to_int32(l.to_number()) ^ to_int32(r.to_number()))),
        "<<" => Val::Num(f64::from(to_int32(l.to_number()).wrapping_shl(to_int32(r.to_number()) as u32 & 31))),
        ">>" => Val::Num(f64::from(to_int32(l.to_number()) >> (to_int32(r.to_number()) as u32 & 31))),
        ">>>" => Val::Num(f64::from((to_int32(l.to_number()) as u32) >> (to_int32(r.to_number()) as u32 & 31))),
        "in" => match &r {
            Val::Array(items) => {
                let key = l.to_js_string();
                Val::Bool(key == "length" || index_of(&key, items.len()).is_some())
            }
            Val::Object(fields) => {
                let key = l.to_js_string();
                Val::Bool(key != TUPLE_ID && fields.iter().any(|(k, _)| *k == key))
            }
            Val::Date(_) | Val::Regex(..) | Val::Func(_) => Val::Bool(false),
            Val::Color(c) => Val::Bool(c.channel(&l.to_js_string()).is_some()),
            other => return throws(format!("Cannot use 'in' operator to search for '{}' in {}", l.to_js_string(), other.to_js_string())),
        },
        _ => return throws("Right-hand side of 'instanceof' is not callable"),
    })
}

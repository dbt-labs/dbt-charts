//! The expression functions that read the view they run in: the scales (`scale()` and its family), the chart's
//! source dataset (`data()`, `indata()`, the tree and selection readers), and the helpers over them.

use super::scales::{same_key, Scale};
use super::val::{throws, EvalError, Res, Val};
use super::view;

const PATH_SHAPE: &str = "function(e){return e?it(e,n=n||at(t)):t}";
const GEO_SHAPE: &str = "function(t){return o?o.path.context(t)(n):\"\"}";

fn arg(vals: &[Val], i: usize) -> &Val {
    vals.get(i).unwrap_or(&Val::Undefined)
}

/// `getScale(name, (group || this).context)`: a truthy `group` is read as a scenegraph group, whose `.context`
/// a plain value does not have, so naming a scale through it reads `.scales` off `undefined`. `Ok(None)` is a
/// scale that is not there (or a `name` that is no registered scale).
fn lookup<R>(vals: &[Val], name_at: usize, group_at: usize, read: impl FnOnce(&mut Scale) -> Res<R>) -> Res<Option<R>> {
    if let Val::Str(_) = arg(vals, name_at) {
        if arg(vals, group_at).truthy() {
            return throws("Cannot read properties of undefined (reading 'scales')");
        }
    }
    match arg(vals, name_at) {
        Val::Str(name) => registered(name, read),
        _ => Ok(None),
    }
}

/// The scales Vega-Lite names after a channel. A chart has the ones its encodings use; this renderer registers the
/// position scales a family lays out, so a read of another is neither the scale nor a scale that is not there.
const CHANNEL_SCALES: [&str; 14] = [
    "x", "y", "color", "fill", "stroke", "opacity", "fillOpacity", "strokeOpacity", "strokeWidth", "strokeDash", "size", "shape", "xOffset", "yOffset",
];

/// Reads the registered scale `name`; `None` where no scale of that name exists, and a refusal where Vega-Lite
/// would have one this renderer does not register.
fn registered<R>(name: &str, read: impl FnOnce(&mut Scale) -> Res<R>) -> Res<Option<R>> {
    match view::with_scale(name, read) {
        Some(out) => out.map(Some),
        None if CHANNEL_SCALES.contains(&name) => Err(unregistered_channel(name)),
        None => Ok(None),
    }
}

fn unregistered_channel(name: &str) -> EvalError {
    EvalError::Unmodeled(crate::refuse!("labels.expr reading the {name:?} scale, which this renderer does not register for the chart, is not supported"))
}

fn with_named(vals: &[Val], name_at: usize, group_at: usize, missing: Val, read: impl FnOnce(&mut Scale) -> Res<Val>) -> Res<Val> {
    Ok(lookup(vals, name_at, group_at, read)?.unwrap_or(missing))
}

/// An internal helper's scale reference (`_scale('x', v)` is `_['%x'](v)`): a scale that is not there is a
/// `TypeError`.
fn with_referenced<R>(vals: &[Val], read: impl FnOnce(&mut Scale) -> R) -> Res<R> {
    match arg(vals, 0) {
        Val::Str(name) => match view::with_scale(name, read) {
            Some(out) => Ok(out),
            None if CHANNEL_SCALES.contains(&&**name) => Err(unregistered_channel(name)),
            None => throws(format!("_[\"%{name}\"] is not a function")),
        },
        other => throws(format!("_[\"%{}\"] is not a function", other.to_js_string())),
    }
}

/// A dataset Vega-Lite's compiler mints, whose contents this engine does not carry.
fn derived_dataset(name: &str) -> EvalError {
    EvalError::Unmodeled(crate::refuse!("labels.expr reading the dataset {name:?}, which Vega-Lite derives from the chart, is not supported"))
}

/// The tuples of the dataset `name`: `[]` for one that does not exist, an error for one Vega-Lite's compiler mints
/// whose contents this engine does not carry.
fn tuples(name: &str) -> Res<Vec<Val>> {
    match view::dataset(name) {
        Some(rows) => Ok(rows),
        None if view::is_compiled_name(name) => {
            Err(derived_dataset(name))
        }
        None => Ok(Vec::new()),
    }
}

/// vega-util's `array`.
fn array_of(v: &Val) -> Vec<Val> {
    match v {
        Val::Undefined | Val::Null => Vec::new(),
        Val::Array(items) => items.clone(),
        other => vec![other.clone()],
    }
}

fn pair(v: &Val, i: usize) -> Val {
    match v {
        Val::Array(items) => items.get(i).cloned().unwrap_or(Val::Undefined),
        Val::Str(s) => s.chars().nth(i).map_or(Val::Undefined, |c| Val::Str(c.into())),
        _ => Val::Undefined,
    }
}

/// `[x, y] = point`: a point that is not iterable is a `TypeError`.
fn destructure(point: &Val) -> Res<(Val, Val)> {
    match point {
        Val::Array(_) | Val::Str(_) => Ok((pair(point, 0), pair(point, 1))),
        Val::Undefined | Val::Null => throws(format!("{} is not iterable", point.to_js_string())),
        other => throws(format!("{} {} is not iterable (cannot read property Symbol(Symbol.iterator))", js_type_name(other), other.to_js_string())),
    }
}

fn js_type_name(v: &Val) -> &'static str {
    match v {
        Val::Num(_) => "number",
        Val::Bool(_) => "boolean",
        _ => "object",
    }
}

fn lasso_append(vals: &[Val]) -> Val {
    let lasso = array_of(arg(vals, 0));
    let (x, y) = (arg(vals, 1), arg(vals, 2));
    let min_dist = match arg(vals, 3) {
        Val::Undefined => 5.0,
        other => other.to_number(),
    };
    let far = lasso.last().is_none_or(|last| {
        let (lx, ly) = (pair(last, 0).to_number() - x.to_number(), pair(last, 1).to_number() - y.to_number());
        super::funcs::hypot(&[Val::Num(lx), Val::Num(ly)]) > min_dist
    });
    if far {
        let mut out = lasso;
        out.push(Val::Array(vec![x.clone(), y.clone()]));
        Val::Array(out)
    } else {
        Val::Array(lasso)
    }
}

fn lasso_path(vals: &[Val]) -> Res<Val> {
    let source = arg(vals, 0);
    let len = match source {
        Val::Array(items) => items.len(),
        Val::Str(s) => s.encode_utf16().count(),
        _ => 0,
    };
    let mut path = String::new();
    for (i, point) in array_of(source).iter().enumerate() {
        let (x, y) = destructure(point)?;
        if i == 0 {
            path += &format!("M {},{} ", x.to_js_string(), y.to_js_string());
        } else if i == len.wrapping_sub(1) {
            path += " Z";
        } else {
            path += &format!("L {},{} ", x.to_js_string(), y.to_js_string());
        }
    }
    Val::string(path)
}

/// `gradient(scale, p0, p1, count)`.
fn gradient(vals: &[Val]) -> Res<Val> {
    let missing = || throws("Cannot read properties of undefined (reading 'domain')");
    let built = lookup(vals, 0, 4, |s| {
        let domain = match s.domain() {
            Val::Array(d) => d,
            _ => Vec::new(),
        };
        let (min, max) = (domain.first().cloned().unwrap_or(Val::Undefined), domain.last().cloned().unwrap_or(Val::Undefined));
        let span = max.to_number() - min.to_number();
        let continuous = s.ticks(1.0)?.is_some();
        let (mut stops, fraction, color): (Vec<Val>, Box<dyn Fn(&Val) -> Val>, Scale) = if span == 0.0 || span.is_nan() {
            if !continuous {
                return throws("scale.interpolate is not a function");
            }
            // A zero-span domain is replaced by `[0, 1]` over the same range.
            let flat = Scale::new(s.kind(), vec![Val::Num(0.0), Val::Num(1.0)], match s.range() {
                Val::Array(r) => r,
                _ => Vec::new(),
            });
            (vec![min.clone(), max.clone()], Box::new(|v: &Val| v.clone()), flat)
        } else {
            let (lo, hi) = (min.to_number(), max.to_number());
            let fraction = s.fraction(lo, hi);
            (domain.clone(), Box::new(move |v: &Val| fraction.clone().call(v)), s.clone())
        };
        let count = match arg(vals, 3).to_number() {
            n if n.is_nan() || n == 0.0 => 15.0,
            n => n,
        };
        if let Some(ticks) = color.ticks(count)? {
            stops = ticks;
            if !min.strict_eq(stops.first().unwrap_or(&Val::Undefined)) {
                stops.insert(0, min.clone());
            }
            if !max.strict_eq(stops.last().unwrap_or(&Val::Undefined)) {
                stops.push(max.clone());
            }
        }
        let color = color;
        let stops: Vec<Val> =
            stops.iter().map(|at| Val::Object(vec![("offset".into(), fraction(at)), ("color".into(), color.call(at))])).collect();
        let point = |p: &Val, i: usize, default: f64| if p.truthy() { pair(p, i) } else { Val::Num(default) };
        Ok(Val::Object(vec![
            ("gradient".into(), Val::Str("linear".into())),
            ("x1".into(), point(arg(vals, 1), 0, 0.0)),
            ("y1".into(), point(arg(vals, 1), 1, 0.0)),
            ("x2".into(), point(arg(vals, 2), 0, 1.0)),
            ("y2".into(), point(arg(vals, 2), 1, 0.0)),
            ("stops".into(), Val::Array(stops)),
            ("stop".into(), Val::Func("function(t,e){return n.push({offset:t,color:e}),r}".into())),
        ]))
    })?;
    match built {
        Some(gradient) => Ok(gradient),
        None => missing(),
    }
}

/// Calls the view-bound function `name`; `None` if it is not one of this module's.
pub fn call(name: &str, vals: &[Val]) -> Option<Res<Val>> {
    Some(match name {
        "scale" => with_named(vals, 0, 2, Val::Undefined, |s| Ok(s.call(arg(vals, 1)))),
        "domain" => with_named(vals, 0, 1, Val::Array(vec![]), |s| Ok(s.domain())),
        "range" => with_named(vals, 0, 1, Val::Array(vec![]), |s| Ok(s.range())),
        "bandwidth" => with_named(vals, 0, 1, Val::Num(0.0), |s| Ok(Val::Num(s.bandwidth()))),
        "invert" => with_named(vals, 0, 2, Val::Undefined, |s| s.invert(arg(vals, 1))),
        "copy" => with_named(vals, 0, 1, Val::Undefined, |s| Ok(Val::Func(s.copy_source()))),
        "gradient" => gradient(vals),
        "_scale" => with_referenced(vals, |s| s.call(arg(vals, 1))),
        "_range" => with_referenced(vals, |s| s.range()),
        "_bandwidth" => match arg(vals, 0) {
            Val::Str(name) => registered(name, |s| Ok(s.bandwidth())).map(|found| Val::Num(found.unwrap_or(0.0))),
            _ => Ok(Val::Num(0.0)),
        },
        "data" => tuples(&arg(vals, 0).to_js_string()).map(Val::Array),
        "indata" => indata(vals),
        "treePath" | "treeAncestors" => tree(vals),
        "lassoAppend" => Ok(lasso_append(vals)),
        "lassoPath" => lasso_path(vals),
        "pathShape" => Ok(Val::Func(PATH_SHAPE.into())),
        "geoShape" => lookup(vals, 0, 2, |_| Ok(())).map(|_| Val::Func(GEO_SHAPE.into())),
        "geoScale" => projection(vals, 1, "scale").map(|()| Val::Undefined),
        "geoArea" | "geoBounds" | "geoCentroid" => {
            let method = name.trim_start_matches("geo").to_lowercase();
            super::geo::call(name, vals, projection(vals, 2, &method))
        }
        "encode" => encode(vals),
        "setdata" => setdata(vals),
        "modify" => modify(vals),
        "intersect" => intersect(vals),
        "intersectLasso" => intersect_lasso(vals),
        "vlSelectionTest" => selection_test(vals),
        "vlSelectionIdTest" => selection_id_test(vals),
        "vlSelectionResolve" => selection_resolve(vals),
        "vlSelectionTuples" => selection_tuples(vals),
        _ => return None,
    })
}

/// `getScale(projection, (group || this).context)` over the projections a chart can name: none. A name that is a
/// scale finds one that has no projection's `path` or `scale()`; a name that is no scale finds nothing.
fn projection(vals: &[Val], group_at: usize, member: &str) -> Res<()> {
    if let Some(found) = lookup(vals, 0, group_at, |_| Ok(()))? {
        let _ = found;
        return throws(match member {
            "scale" => "p.scale is not a function".to_string(),
            m => format!("Cannot read properties of undefined (reading '{m}')"),
        });
    }
    Ok(())
}

fn indata(vals: &[Val]) -> Res<Val> {
    let (name, field, value) = (arg(vals, 0).to_js_string(), arg(vals, 1), arg(vals, 2));
    let rows = match view::dataset(&name) {
        Some(rows) => rows,
        None if view::is_compiled_name(&name) => {
            return Err(derived_dataset(&name));
        }
        None => return Err(EvalError::Unknown(format!("Undefined data set name: {name:?}"))),
    };
    let path = super::funcs::field_path(field)?;
    let mut count = 0;
    for row in &rows {
        let mut at = row.clone();
        for step in &path {
            at = at.property(&Val::string(step.clone())?)?;
        }
        if same_key(&at, value) {
            count += 1;
        }
    }
    Ok(if count == 0 { Val::Undefined } else { Val::Num(count as f64) })
}

/// `treePath` / `treeAncestors`: a dataset of rows has no tree, so no node is found.
fn tree(vals: &[Val]) -> Res<Val> {
    tuples(&arg(vals, 0).to_js_string())?;
    Ok(Val::Undefined)
}

/// `encode(item, name, retval)`: re-encodes the item's property, which paints what it painted; it answers `retval`
/// or the item. Something that is not an item has no `mark` to read.
fn encode(vals: &[Val]) -> Res<Val> {
    let item = arg(vals, 0);
    if item.truthy() {
        let is_item = matches!(item, Val::Object(fields) if fields.iter().any(|(k, _)| k == "mark"));
        if !is_item {
            return throws("Cannot read properties of undefined (reading 'source')");
        }
    }
    Ok(match arg(vals, 2) {
        Val::Undefined => item.clone(),
        retval => retval.clone(),
    })
}

/// `setdata(name, tuples)` pulses a dataset's input while the dataflow runs: Vega reports it as running already.
fn setdata(vals: &[Val]) -> Res<Val> {
    let name = arg(vals, 0).to_js_string();
    tuples(&name)?;
    throws("Dataflow already running. Use runAsync() to chain invocations.")
}

/// `modify(name, insert, remove, toggle, modify, values)` edits a dataset the chart draws from, whatever it
/// edits: that re-runs the dataflow its own labels are part of, which never settles.
fn modify(vals: &[Val]) -> Res<Val> {
    let name = arg(vals, 0).to_js_string();
    tuples(&name)?;
    if view::dataset(&name).is_none() {
        return throws("Cannot read properties of undefined (reading 'input')");
    }
    Err(EvalError::Unknown("RangeError: Maximum call stack size exceeded".into()))
}

/// The rows of the selection store `name`: the chart has none of Vega-Lite's own (no selection is authored), so
/// the one dataset it can name is its source, whose rows carry no selection structure.
fn store(name: &Val) -> Res<Vec<Val>> {
    let name = name.to_js_string();
    match view::dataset(&name) {
        Some(rows) => Ok(rows),
        None if view::is_compiled_name(&name) => {
            Err(derived_dataset(&name))
        }
        None => Err(EvalError::Unknown(format!("Undefined data set name: {name:?}"))),
    }
}

/// `vlSelectionTest(name, datum, op)`: a tuple of the store is tested against its `fields`, which a row of the
/// source does not have.
fn selection_test(vals: &[Val]) -> Res<Val> {
    let rows = store(arg(vals, 0))?;
    if rows.is_empty() {
        return Ok(Val::Num(0.0));
    }
    throws("Cannot read properties of undefined (reading 'length')")
}

/// `vlSelectionIdTest(name, datum, op)`: no row of the source carries a selection id.
fn selection_id_test(vals: &[Val]) -> Res<Val> {
    store(arg(vals, 0))?;
    match arg(vals, 1) {
        Val::Undefined | Val::Null => throws(format!("Cannot read properties of {} (reading '_vgsid_')", arg(vals, 1).to_js_string())),
        _ => Ok(Val::Bool(false)),
    }
}

/// `vlSelectionResolve(name, op)`: an empty store resolves to nothing; the rows of the source resolve their
/// missing ids into a `Set`, which this engine has no value for.
fn selection_resolve(vals: &[Val]) -> Res<Val> {
    if store(arg(vals, 0))?.is_empty() {
        return Ok(Val::Object(Vec::new()));
    }
    Err(EvalError::Unmodeled(crate::refuse!("labels.expr resolving a selection over the chart's source rows is not supported")))
}

/// `vlSelectionTuples(items, base)`: one selection tuple per scenegraph item, over `base`.
fn selection_tuples(vals: &[Val]) -> Res<Val> {
    let Val::Array(items) = arg(vals, 0) else {
        return throws("First argument to selectionTuples must be an array.");
    };
    let base = arg(vals, 1);
    if !base.is_object() {
        return throws("Second argument to selectionTuples must be an object.");
    }
    let fields = base.property(&Val::Str("fields".into()))?;
    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let datum = item.property(&Val::Str("datum".into()))?;
        let mut tuple: Vec<(String, Val)> = if fields.truthy() {
            let Val::Array(defs) = &fields else {
                return throws(format!("base.fields.map is not a function"));
            };
            let mut values = Vec::with_capacity(defs.len());
            for def in defs {
                if !matches!(def, Val::Object(_)) {
                    return throws(format!("Cannot create property 'getter' on {} '{}'", js_type_name(def), def.to_js_string()));
                }
                let path = super::funcs::field_path(&def.property(&Val::Str("field".into()))?)?;
                let mut at = datum.clone();
                for step in &path {
                    at = at.property(&Val::string(step.clone())?)?;
                }
                values.push(at);
            }
            vec![("values".to_string(), Val::Array(values))]
        } else {
            vec![("_vgsid_".to_string(), datum.property(&Val::Str("_vgsid_".into()))?)]
        };
        if let Val::Object(own) = base {
            for (key, value) in own {
                match tuple.iter_mut().find(|(k, _)| k == key) {
                    Some(slot) => slot.1 = value.clone(),
                    None => tuple.push((key.clone(), value.clone())),
                }
            }
        }
        out.push(Val::Object(tuple));
    }
    Ok(Val::Array(out))
}

/// Whether `scene` is a scenegraph mark or group the generated `intersect` can walk.
fn is_scene(scene: &Val) -> bool {
    matches!(scene, Val::Object(fields) if fields.iter().any(|(k, _)| k == "marktype" || k == "items"))
}

/// The scenegraph items an `intersect` walks: the marks the chart has drawn.
fn marks_drawn() -> EvalError {
    EvalError::Unmodeled(crate::refuse!("labels.expr intersecting the marks the chart has drawn is not supported"))
}

/// `intersect(box, opt, group)`: the scenegraph items inside the box.
fn intersect(vals: &[Val]) -> Res<Val> {
    let b = arg(vals, 0);
    if !b.truthy() {
        return Ok(Val::Array(Vec::new()));
    }
    destructure(b)?;
    let group = arg(vals, 2);
    if group.truthy() && !is_scene(group) {
        return throws("Intersect scene must be mark node or group item.");
    }
    Err(marks_drawn())
}

/// `intersectLasso(markname, lasso, unit)`: the scenegraph items inside the lasso.
fn intersect_lasso(vals: &[Val]) -> Res<Val> {
    let unit = arg(vals, 2);
    for key in ["x", "y", "mark"] {
        unit.property(&Val::Str(key.into())).map_err(|_| EvalError::Throws(format!("Cannot destructure property 'x' of 'e' as it is {}.", unit.to_js_string())))?;
    }
    let mark = unit.property(&Val::Str("mark".into()))?;
    if !is_scene(&mark) {
        return throws("Intersect scene must be mark node or group item.");
    }
    Err(marks_drawn())
}

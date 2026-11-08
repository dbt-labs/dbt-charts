//! The rest of a custom chart's `d3` namespace: the sankey layout, the stack,
//! symbols, `format`, the color interpolators and the categorical schemes.

use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;

use d3rs::color::{format_rgb, interpolate_hcl_color, interpolate_rgb_color, parse_color, Color, Rgb};
use d3rs::format::spec_width;
use d3rs::sankey::{Align, Sankey};
use d3rs::shape::link_horizontal;
use d3rs::stack::{Offset, Order};
use d3rs::symbol::SymbolType;
use ::layout::scene_path::scene_path;
use d3rs::vega_schemes;
use minijinja::value::{Object, ObjectRepr, Rest, Value, ValueKind};
use minijinja::{Error, State};

use super::custom::invalid;
use super::custom_d3::{bind, extent, finite, finite_or, floats, items, non_negative_or, pair, text, text_or};

/// The most relaxation passes one call may run: d3's default is 6, and a
/// template's instruction budget does not count the native loop.
const MAX_ITERATIONS: f64 = 1000.0;

/// d3-scale-chromatic's categorical schemes, which Vega carries unchanged.
const SCHEMES: [(&str, &[Rgb]); 9] = [
    ("scheme_category10", &vega_schemes::CATEGORY10),
    ("scheme_accent", &vega_schemes::ACCENT),
    ("scheme_dark2", &vega_schemes::DARK2),
    ("scheme_paired", &vega_schemes::PAIRED),
    ("scheme_pastel1", &vega_schemes::PASTEL1),
    ("scheme_pastel2", &vega_schemes::PASTEL2),
    ("scheme_set1", &vega_schemes::SET1),
    ("scheme_set2", &vega_schemes::SET2),
    ("scheme_set3", &vega_schemes::SET3),
];

pub(super) fn register(m: &mut BTreeMap<&'static str, Value>) {
    m.insert("sankey", Value::from_function(|args: Rest<Value>| sankey(&args)));
    m.insert("sankey_link_horizontal", Value::from_function(|args: Rest<Value>| sankey_link(&args)));
    m.insert("stack", Value::from_function(|args: Rest<Value>| stack(&args)));
    m.insert("symbol", Value::from_function(|args: Rest<Value>| symbol(&args)));
    m.insert("format", Value::from_function(|args: Rest<Value>| format(&args)));
    m.insert("interpolate_rgb", Value::from_function(|args: Rest<Value>| interpolate("interpolate_rgb", &args)));
    m.insert("interpolate_hcl", Value::from_function(|args: Rest<Value>| interpolate("interpolate_hcl", &args)));
    for (name, scheme) in SCHEMES {
        let hexes: Vec<String> = scheme.iter().map(|&rgb| d3rs::color::format_hex(d3rs::color::Color::opaque(rgb))).collect();
        m.insert(name, Value::from(hexes));
    }
}

// ── Sankey ──────────────────────────────────────────────────────────────────

/// `d3.sankey(links, nodes=none, size=none, extent=none, node_width=24,
/// node_padding=8, align="justify", iterations=6)`: d3-sankey's layout over
/// links of `{source, target, value}`, the nodes named by the links in order
/// of first appearance unless `nodes` lists them.
fn sankey(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.sankey()";
    let [links, nodes, size, box_arg, node_width, node_padding, align, iterations] = bind(
        SITE,
        ["links", "nodes", "size", "extent", "node_width", "node_padding", "align", "iterations"],
        1,
        args,
    )?;
    let mut names: Vec<Value> = Vec::new();
    let mut index: HashMap<Value, usize> = HashMap::new();
    fn intern(names: &mut Vec<Value>, index: &mut HashMap<Value, usize>, name: Value) -> usize {
        *index.entry(name.clone()).or_insert_with(|| {
            names.push(name);
            names.len() - 1
        })
    }
    let listed = match nodes {
        Some(nodes) if !nodes.is_none() => {
            for (i, name) in items(SITE, "nodes", &nodes)?.into_iter().enumerate() {
                if !matches!(name.kind(), ValueKind::String | ValueKind::Number) {
                    return Err(invalid(std::format!("{SITE}: nodes[{i}] must be a string or number, got {name:?}")));
                }
                if intern(&mut names, &mut index, name.clone()) != i {
                    return Err(invalid(std::format!("{SITE}: nodes[{i}] {name:?} is listed twice")));
                }
            }
            true
        }
        _ => false,
    };
    let mut edges: Vec<(usize, usize, f64)> = Vec::new();
    for (i, link) in items(SITE, "links", &links.expect("required"))?.iter().enumerate() {
        if link.kind() != ValueKind::Map {
            return Err(invalid(std::format!("{SITE}: links[{i}] must be a mapping with source, target and value, got {link:?}")));
        }
        let mut end = |key: &str| -> Result<usize, Error> {
            let name = link.get_attr(key)?;
            if name.is_undefined() || name.is_none() {
                return Err(invalid(std::format!("{SITE}: links[{i}] has no {key}")));
            }
            if !matches!(name.kind(), ValueKind::String | ValueKind::Number) {
                return Err(invalid(std::format!("{SITE}: links[{i}].{key} must be a string or number, got {name:?}")));
            }
            if listed && !index.contains_key(&name) {
                return Err(invalid(std::format!("{SITE}: links[{i}].{key} {name:?} is not in nodes")));
            }
            Ok(intern(&mut names, &mut index, name))
        };
        let source = end("source")?;
        let target = end("target")?;
        let value = link.get_attr("value")?;
        let value = finite(SITE, &std::format!("links[{i}].value"), &value)?;
        edges.push((source, target, value));
    }
    let box_ = match (size, box_arg) {
        (Some(size), Some(corners)) if !size.is_none() && !corners.is_none() => {
            return Err(invalid(std::format!("{SITE}: size and extent both set the box; give one")));
        }
        (Some(size), _) if !size.is_none() => [[0.0, 0.0], pair(SITE, "size", &size)?],
        (_, Some(corners)) if !corners.is_none() => extent(SITE, "extent", &corners)?,
        _ => [[0.0, 0.0], [1.0, 1.0]],
    };
    let align = match text_or(SITE, "align", align.as_ref(), "justify")?.as_str() {
        "justify" => Align::Justify,
        "left" => Align::Left,
        "right" => Align::Right,
        "center" => Align::Center,
        other => return Err(invalid(std::format!("{SITE}: unknown align {other:?}; use justify, left, right or center"))),
    };
    let iterations = finite_or(SITE, "iterations", iterations.as_ref(), 6.0)?;
    if iterations.fract() != 0.0 || !(0.0..=MAX_ITERATIONS).contains(&iterations) {
        return Err(invalid(std::format!(
            "{SITE}: iterations must be a whole number from 0 to {MAX_ITERATIONS}, got {iterations}"
        )));
    }
    let layout = Sankey {
        x0: box_[0][0],
        y0: box_[0][1],
        x1: box_[1][0],
        y1: box_[1][1],
        node_width: non_negative_or(SITE, "node_width", node_width.as_ref(), 24.0)?,
        node_padding: non_negative_or(SITE, "node_padding", node_padding.as_ref(), 8.0)?,
        align,
        iterations: iterations as usize,
    };
    if names.is_empty() {
        return Err(invalid(std::format!("{SITE}: links must not be empty")));
    }
    let graph = layout.layout(names.len(), &edges, None).map_err(|e| invalid(std::format!("{SITE}: {e}")))?;
    // d3 scales the columns by the smallest finite ratio, so only every column summing to 0 leaves
    // NaN rows; an overflowing box or node width leaves non-finite columns.
    if graph.nodes.iter().any(|n| ![n.x0, n.x1, n.y0, n.y1].iter().all(|v| v.is_finite())) {
        return Err(invalid(std::format!(
            "{SITE}: the layout is not finite (every column's values sum to 0, or the box overflows)"
        )));
    }
    let indices = |links: &[usize]| Value::from(links.iter().map(|&l| Value::from(l)).collect::<Vec<Value>>());
    let nodes: Vec<Value> = graph
        .nodes
        .iter()
        .zip(&names)
        .enumerate()
        .map(|(i, (n, name))| {
            Value::from(BTreeMap::from([
                ("name", name.clone()),
                ("index", Value::from(i)),
                ("value", Value::from(n.value)),
                ("depth", Value::from(n.depth)),
                ("height", Value::from(n.height)),
                ("layer", Value::from(n.layer)),
                ("x0", Value::from(n.x0)),
                ("x1", Value::from(n.x1)),
                ("y0", Value::from(n.y0)),
                ("y1", Value::from(n.y1)),
                ("source_links", indices(&n.source_links)),
                ("target_links", indices(&n.target_links)),
            ]))
        })
        .collect();
    let links: Vec<Value> = graph
        .links
        .iter()
        .enumerate()
        .map(|(i, l)| {
            Value::from(BTreeMap::from([
                ("index", Value::from(i)),
                ("source", nodes[l.source].clone()),
                ("target", nodes[l.target].clone()),
                ("value", Value::from(l.value)),
                ("width", Value::from(l.width)),
                ("y0", Value::from(l.y0)),
                ("y1", Value::from(l.y1)),
            ]))
        })
        .collect();
    Ok(Value::from(BTreeMap::from([("nodes", Value::from(nodes)), ("links", Value::from(links))])))
}

/// `d3.sankey_link_horizontal(link)`: d3-sankey's `sankeyLinkHorizontal()`
/// over d3-shape 3's `linkHorizontal`: the `d` of the
/// link's centerline, stroked at `link.width`.
fn sankey_link(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.sankey_link_horizontal()";
    let [link] = bind(SITE, ["link"], 1, args)?.map(|v| v.expect("required"));
    let read = |path: &[&str]| -> Result<f64, Error> {
        let mut v = link.clone();
        for key in path {
            v = v.get_attr(key)?;
            if v.is_undefined() {
                return Err(invalid(std::format!("{SITE}: link has no {}", path.join("."))));
            }
        }
        finite(SITE, &std::format!("link.{}", path.join(".")), &v)
    };
    let path = link_horizontal(
        (read(&["source", "x1"])?, read(&["y0"])?),
        (read(&["target", "x0"])?, read(&["y1"])?),
    );
    Ok(Value::from(print::path_d(&scene_path(&path))))
}

// ── Stack ───────────────────────────────────────────────────────────────────

/// `d3.stack(series, order="none", offset="none")`: d3-shape's `stack()` over
/// `series[i][j]`, the value of series `i` at point `j` (`none` for no datum).
/// Returns `{series, index}`: `series[i][j]` is `[y0, y1]` (`none` where there
/// is no datum) and `index[i]` is the series' place in the stacking order.
fn stack(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.stack()";
    let [series, order, offset] = bind(SITE, ["series", "order", "offset"], 1, args)?;
    let rows = items(SITE, "series", &series.expect("required"))?;
    if rows.is_empty() {
        return Err(invalid(std::format!("{SITE}: series must not be empty")));
    }
    let mut values: Vec<Vec<f64>> = Vec::with_capacity(rows.len());
    for (i, row) in rows.iter().enumerate() {
        let points = items(SITE, &std::format!("series[{i}]"), row)?;
        if i > 0 && points.len() != values[0].len() {
            return Err(invalid(std::format!(
                "{SITE}: series[{i}] has {} points but series[0] has {}",
                points.len(),
                values[0].len()
            )));
        }
        let points = points.iter().enumerate().map(|(j, v)| {
            if v.is_none() { Ok(f64::NAN) } else { finite(SITE, &std::format!("series[{i}][{j}]"), v) }
        });
        values.push(points.collect::<Result<_, Error>>()?);
    }
    let order = match text_or(SITE, "order", order.as_ref(), "none")?.as_str() {
        "none" => Order::None,
        "ascending" => Order::Ascending,
        "descending" => Order::Descending,
        "inside_out" => Order::InsideOut,
        "reverse" => Order::Reverse,
        "appearance" => Order::Appearance,
        other => {
            return Err(invalid(std::format!(
                "{SITE}: unknown order {other:?}; use none, ascending, descending, inside_out, reverse or appearance"
            )))
        }
    };
    let offset = match text_or(SITE, "offset", offset.as_ref(), "none")?.as_str() {
        "none" => Offset::None,
        "expand" => Offset::Expand,
        "diverging" => Offset::Diverging,
        "silhouette" => Offset::Silhouette,
        "wiggle" => Offset::Wiggle,
        other => {
            return Err(invalid(std::format!(
                "{SITE}: unknown offset {other:?}; use none, expand, diverging, silhouette or wiggle"
            )))
        }
    };
    let stacked = d3rs::stack::stack(&values, order, offset);
    let mut series = Vec::with_capacity(stacked.series.len());
    for (i, row) in stacked.series.iter().enumerate() {
        let mut spans = Vec::with_capacity(row.len());
        for (j, span) in row.iter().enumerate() {
            let finite_span = span.iter().all(|v| v.is_finite());
            // `none` means no datum, so a datum that went non-finite is an error, not a `none`.
            if !finite_span && values[i][j].is_finite() {
                return Err(invalid(std::format!("{SITE}: series[{i}][{j}] overflows")));
            }
            spans.push(if finite_span { floats(span) } else { Value::from(()) });
        }
        series.push(Value::from(spans));
    }
    let index: Vec<Value> = stacked.index.into_iter().map(Value::from).collect();
    Ok(Value::from(BTreeMap::from([("series", Value::from(series)), ("index", Value::from(index))])))
}

// ── Symbols ─────────────────────────────────────────────────────────────────

fn symbol(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.symbol()";
    let [kind, size] = bind(SITE, ["type", "size"], 0, args)?;
    let name = text_or(SITE, "type", kind.as_ref(), "circle")?;
    let kind = SymbolType::from_name(&name).ok_or_else(|| {
        invalid(std::format!(
            "{SITE}: unknown type {name:?}; use circle, cross, diamond, square, star, triangle, wye \
             (filled) or plus, times, triangle2, asterisk, square2, diamond2 (stroked)"
        ))
    })?;
    Ok(Value::from(print::path_d(&scene_path(&kind.draw(non_negative_or(SITE, "size", size.as_ref(), 64.0)?)))))
}

// ── Format ──────────────────────────────────────────────────────────────────

/// `d3.format(spec)`: a function printing a number through the board's
/// active number locale.
fn format(args: &[Value]) -> Result<Value, Error> {
    const SITE: &str = "d3.format()";
    let [spec] = bind(SITE, ["spec"], 1, args)?.map(|v| v.expect("required"));
    let spec = text(SITE, "spec", &spec)?;
    spec_width(&spec).map_err(|e| invalid(std::format!("{SITE}: {e}")))?;
    Ok(Value::from_object(Formatter { spec }))
}

#[derive(Debug)]
struct Formatter {
    spec: String,
}

impl Object for Formatter {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let site = std::format!("d3.format({:?})()", self.spec);
        let [value] = bind(&site, ["value"], 1, args)?.map(|v| v.expect("required"));
        let value = finite(&site, "value", &value)?;
        ::bind::formats::locale::d3_format(&self.spec, value)
            .map(Value::from)
            .map_err(|e| invalid(std::format!("{site}: {e}")))
    }
}

// ── Color ───────────────────────────────────────────────────────────────────

/// `d3.interpolate_rgb(a, b)` / `d3.interpolate_hcl(a, b)`: a function of `t`.
fn interpolate(name: &'static str, args: &[Value]) -> Result<Value, Error> {
    let site = std::format!("d3.{name}()");
    let [a, b] = bind(&site, ["a", "b"], 2, args)?.map(|v| v.expect("required"));
    let color = |which: &str, v: &Value| -> Result<Color, Error> {
        let spelled = text(&site, which, v)?;
        parse_color(&spelled).ok_or_else(|| invalid(std::format!("{site}: {which} is not a color d3 reads, got {spelled:?}")))
    };
    Ok(Value::from_object(Interpolator { name, a: color("a", &a)?, b: color("b", &b)? }))
}

struct Interpolator {
    name: &'static str,
    a: Color,
    b: Color,
}

impl std::fmt::Debug for Interpolator {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "d3.{}()", self.name)
    }
}

impl Object for Interpolator {
    fn repr(self: &Arc<Self>) -> ObjectRepr {
        ObjectRepr::Plain
    }

    fn call(self: &Arc<Self>, _state: &State<'_, '_>, args: &[Value]) -> Result<Value, Error> {
        let site = std::format!("d3.{}()()", self.name);
        let [t] = bind(&site, ["t"], 1, args)?.map(|v| v.expect("required"));
        let t = finite(&site, "t", &t)?;
        let mixed = match self.name {
            "interpolate_rgb" => interpolate_rgb_color(self.a, self.b, t),
            _ => interpolate_hcl_color(self.a, self.b, t),
        };
        Ok(Value::from(format_rgb(mixed)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_scheme_prints_hex() {
        for (name, scheme) in SCHEMES {
            assert!(!scheme.is_empty(), "{name}");
        }
        let mut m = BTreeMap::new();
        register(&mut m);
        let list: Vec<Value> = m["scheme_category10"].try_iter().unwrap().collect();
        assert_eq!(list[0].as_str(), Some("#1f77b4"));
        assert_eq!(list.len(), 10);
    }
}

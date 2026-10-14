//! Sankey family: ResolvedSankeyChart payload → SVG. Native SVG with no
//! Vega-Lite oracle — VL has no sankey mark. This module validates the edge
//! list, picks a layout engine, and paints nodes, ribbons, and labels.
//!
//! Every row is one flow, and a node is its name. Three modes:
//! - default: the flows must form a DAG, laid out by `d3rs::sankey` (d3-sankey);
//!   a cycle refuses, naming it.
//! - `step`: a node is `(step, name)` and a row at step `s` runs from column
//!   `s` to `s + 1`, so a revisited state is a new node and cycles cannot occur.
//! - `loopbacks`: cycles and self-loops are drawn as return flows, laid out by
//!   `d3rs::sankey_circular` (Semiotic's sankey-plus) and fit to the plot
//!   with one uniform scale, so every band keeps its proportional width.
//!
//! Labels sit right of their node; the last column's run into a right margin
//! reserved for the widest of them. Unsupported → clear `Err`: a subtitle,
//! `step` with `loopbacks`, and a label that would overlap another label or
//! a node.

use std::collections::HashMap;
use std::fmt::Write as _;

use serde::Deserialize;
use serde_json::Value;

use d3rs::sankey::{ribbon_area, Align, Sankey};
use d3rs::sankey_circular::SankeyCircular;

use crate::common::{emit_title, Font, TitleStyle};
use crate::contract::{aria_attr, mark_group_close, mark_group_open, ItemRole, MUTED, ROLE_HEADER};
use crate::frame::ChartFrame;
use crate::svg::{num, xml_escape, xml_escape_attr};
use crate::text::FontBook;
use crate::Payload;

#[derive(Deserialize)]
struct Resolved {
    source: String,
    target: String,
    value: String,
    #[serde(default)]
    step: Option<String>,
    /// Draw cycles as return flows instead of refusing them.
    loopbacks: bool,
    /// The color channel's column, if bound: `source` or `target` colors
    /// nodes by name and each ribbon by that end's node. Unbound, every mark
    /// takes `style.single_series_fill`.
    color: Option<String>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    title: Option<String>,
    title_style: Option<TitleStyle>,
    #[serde(default, deserialize_with = "crate::common::de_nonempty_string")]
    subtitle: Option<String>,
    background: String,
    palette: Vec<String>,
    /// d3-format spec for node labels and tooltips.
    value_format: String,
    /// The tooltip's name for `value` (resolve's display title).
    value_title: String,
    style: SankeyStyle,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SankeyStyle {
    node_align: String,
    node_width: f64,
    node_padding: f64,
    iterations: usize,
    single_series_fill: String,
    link_opacity: f64,
    /// Floor on a ribbon's drawn width, so a tiny flow stays visible.
    min_link_width: f64,
    /// Space between nested loop bands, and a loop's innermost corner radius.
    loop_gap: f64,
    loop_radius: f64,
    label: Font,
    value_label: Font,
    label_gap: f64,
    /// `name` or `name_value`.
    label_content: String,
}

struct Flow {
    source: usize,
    target: usize,
    value: f64,
}

struct NodeKey {
    name: String,
    step: Option<i64>,
}

/// Engine-neutral geometry: what painting needs from either layout.
struct Laid {
    /// `[x0, x1, y0, y1]` and column per node.
    nodes: Vec<([f64; 4], i64)>,
    /// Per flow, in row order: filled band outline, is a loop.
    links: Vec<(String, bool)>,
}

pub fn render(payload: &Payload, fonts: &FontBook) -> Result<String, String> {
    let r: Resolved = serde_json::from_value(payload.resolved.clone())
        .map_err(|e| format!("sankey: resolved payload: {e}"))?;
    if r.subtitle.is_some() {
        return Err("sankey: subtitle not supported".into());
    }
    if r.loopbacks && r.step.is_some() {
        return Err("sankey: `step` and `loopbacks` both set; a stepped flow cannot loop".into());
    }
    let st = &r.style;
    let align = match st.node_align.as_str() {
        "justify" => Align::Justify,
        "left" => Align::Left,
        "right" => Align::Right,
        "center" => Align::Center,
        other => return Err(format!("sankey: node_align `{other}` not one of justify/left/right/center")),
    };
    let by_target = match r.color.as_deref() {
        None => None,
        Some(c) if c == r.source => Some(false),
        Some(c) if c == r.target => Some(true),
        Some(c) => {
            return Err(format!(
                "sankey: color `{c}` is neither the source nor the target column; coloring flows \
                 by another column needs a legend, which sankey does not draw yet"
            ))
        }
    };
    let show_value = match st.label_content.as_str() {
        "name" => false,
        "name_value" => true,
        other => return Err(format!("sankey: label_content `{other}` not one of name/name_value")),
    };
    if r.palette.is_empty() {
        return Err("sankey: palette is empty".into());
    }
    let label_size = st.label.size.ok_or("sankey: style.label.font size missing")?;

    let (nodes, flows) = build_graph(&r, &payload.rows)?;
    if r.step.is_none() && !r.loopbacks {
        if let Some(cycle) = find_cycle(nodes.len(), &flows) {
            let path: Vec<&str> = cycle.iter().map(|&i| nodes[i].name.as_str()).collect();
            return Err(format!(
                "sankey: flows form a cycle ({}). Assign each flow a stage with a `step` column \
                 so a revisited state becomes a new node, or set `loopbacks` to draw it as a \
                 return flow.",
                path.join(" → ")
            ));
        }
    }

    let pad = payload.padding.unwrap_or(crate::Padding { left: 0.0, right: 0.0, top: 0.0, bottom: 0.0 });
    let mut out = String::new();
    let title_h = emit_title(&mut out, r.title.as_deref(), r.title_style.as_ref(), pad.left, pad.top);
    // Half a label line above the top node and below the bottom one keeps
    // every centered label inside the canvas.
    let half = label_size / 2.0;
    let (x0, x1) = (pad.left, payload.width - pad.right);
    let (y0, y1) = (pad.top + title_h + half, payload.height - pad.bottom - half);
    let edge_list: Vec<(usize, usize, f64)> = flows.iter().map(|f| (f.source, f.target, f.value)).collect();
    let hw = |width: f64| width.max(st.min_link_width) / 2.0;
    let lay = |x1: f64| -> Result<Laid, String> {
    Ok(if r.loopbacks {
        let ids: Vec<String> = nodes.iter().map(|n| n.name.clone()).collect();
        let layout = SankeyCircular {
            x0,
            y0,
            x1,
            y1,
            node_width: st.node_width,
            node_padding: st.node_padding,
            align,
            iterations: st.iterations,
            circular_gap: st.loop_gap,
            base_radius: st.loop_radius,
            ..SankeyCircular::default()
        };
        let mut g = layout.layout(&ids, &edge_list).map_err(|e| match e.strip_prefix("sort of ") {
            Some(_) => "sankey: with `loopbacks`, the layout handles at most 63 items in any one \
                        group it orders (a column's nodes, a node's links on one side, or the \
                        loops routed over the top or under the bottom)"
                .to_string(),
            None => format!("sankey: layout: {e}"),
        })?;
        g.fit(x0, y0, x1, y1);
        Laid {
            nodes: g.nodes.iter().map(|n| ([n.x0, n.x1, n.y0, n.y1], n.column)).collect(),
            links: (0..g.links.len()).map(|l| (g.link_area(l, hw(g.links[l].width), num), g.links[l].circular)).collect(),
        }
    } else {
        let fixed_layers: Option<Vec<usize>> = r.step.as_ref().map(|_| {
            let min = nodes.iter().filter_map(|n| n.step).min().expect("step mode has steps");
            nodes.iter().map(|n| (n.step.expect("step mode") - min) as usize).collect()
        });
        let layout = Sankey {
            x0,
            x1,
            y0,
            y1,
            node_width: st.node_width,
            node_padding: st.node_padding,
            align,
            iterations: st.iterations,
        };
        let g = layout.layout(nodes.len(), &edge_list, fixed_layers.as_deref()).map_err(|e| {
            match (e.strip_prefix("column "), r.step.as_ref()) {
                (Some(rest), Some(step)) => format!("sankey: `{step}` leaves a column empty ({rest})"),
                _ => format!("sankey: layout: {e}"),
            }
        })?;
        Laid {
            nodes: g.nodes.iter().map(|n| ([n.x0, n.x1, n.y0, n.y1], n.layer as i64)).collect(),
            links: g
                .links
                .iter()
                .map(|l| {
                    let (sx, tx) = (g.nodes[l.source].x1, g.nodes[l.target].x0);
                    (ribbon_area(sx, l.y0, tx, l.y1, hw(l.width), num), false)
                })
                .collect(),
        }
    })
    };

    let fmt = |v: f64| {
        crate::number_locale::d3_format(&r.value_format, v)
            .map_err(|e| format!("sankey: value_format {:?}: {e}", r.value_format))
    };
    let mut inflow = vec![0.0; nodes.len()];
    let mut outflow = vec![0.0; nodes.len()];
    for f in &flows {
        outflow[f.source] += f.value;
        inflow[f.target] += f.value;
    }
    let lf = &st.label;
    let vf = &st.value_label;
    let mut label_w = Vec::with_capacity(nodes.len());
    for (i, n) in nodes.iter().enumerate() {
        let mut w = measure(fonts, &n.name, lf, label_size)?;
        if show_value {
            w += measure(fonts, &format!(" {}", fmt(inflow[i].max(outflow[i]))?), vf, vf.size_or(label_size))?;
        }
        label_w.push(w);
    }
    // Columns do not depend on the horizontal extent, so a first layout names
    // the last column and a second one leaves its labels their margin.
    let first = lay(x1)?;
    let last_column = first.nodes.iter().map(|n| n.1).max().unwrap_or(0);
    let margin = first
        .nodes
        .iter()
        .zip(&label_w)
        .filter(|(n, _)| n.1 == last_column)
        .map(|(_, w)| st.label_gap + w)
        .fold(0.0, f64::max);
    let laid = lay(x1 - margin)?;
    check_label_room(&laid, &nodes, &label_w, st.label_gap, label_size, x1)?;

    let colors = match by_target {
        Some(_) => node_colors(&nodes, &r.palette),
        None => vec![st.single_series_fill.clone(); nodes.len()],
    };

    mark_group_open(&mut out, "path", "sankey-links");
    for (li, (f, (d, looped))) in flows.iter().zip(&laid.links).enumerate() {
        let (s, t) = (&nodes[f.source].name, &nodes[f.target].name);
        let share = crate::number_locale::d3_format(".0%", f.value / outflow[f.source])
            .map_err(|e| format!("sankey: share format: {e}"))?;
        let label = format!(
            "{ROLE_HEADER}{s} → {t}; {}: {}; {MUTED}Share of {s}: {share}",
            r.value_title,
            fmt(f.value)?
        );
        let color = &colors[if by_target == Some(true) { f.target } else { f.source }];
        let _ = write!(
            out,
            "<path d=\"{d}\" fill=\"{}\" fill-opacity=\"{}\" data-link=\"{li}\"{}{}/>",
            xml_escape_attr(color),
            num(st.link_opacity),
            if *looped { " data-loop=\"true\"" } else { "" },
            aria_attr(Some(&label), ItemRole::PathMark),
        );
    }
    mark_group_close(&mut out);

    mark_group_open(&mut out, "rect", "sankey-nodes");
    for (ni, ([nx0, nx1, ny0, ny1], _)) in laid.nodes.iter().enumerate() {
        let (has_in, has_out) = (inflow[ni] > 0.0, outflow[ni] > 0.0);
        let mut rows = vec![format!("{ROLE_HEADER}{}", nodes[ni].name)];
        if has_in {
            rows.push(format!("In: {}", fmt(inflow[ni])?));
        }
        if has_out {
            rows.push(format!("Out: {}", fmt(outflow[ni])?));
        }
        if has_in && has_out && inflow[ni] != outflow[ni] {
            rows.push(format!("{MUTED}Difference: {}", fmt(outflow[ni] - inflow[ni])?));
        }
        let _ = write!(
            out,
            "<rect x=\"{}\" y=\"{}\" width=\"{}\" height=\"{}\" fill=\"{}\" data-node=\"{ni}\"{}/>",
            num(*nx0),
            num(*ny0),
            num(nx1 - nx0),
            num(ny1 - ny0),
            xml_escape_attr(&colors[ni]),
            aria_attr(Some(&rows.join("; ")), ItemRole::RectMark),
        );
    }
    mark_group_close(&mut out);

    let _ = write!(out, "<g class=\"sankey-labels\" pointer-events=\"none\">");
    for (ni, ([_, nx1, ny0, ny1], _)) in laid.nodes.iter().enumerate() {
        let (x, y) = (nx1 + st.label_gap, (ny0 + ny1) / 2.0);
        let _ = write!(
            out,
            "<text x=\"{}\" y=\"{}\" dy=\"0.35em\" text-anchor=\"start\" font-family=\"{}\" \
             font-size=\"{}\" font-weight=\"{}\" fill=\"{}\">{}",
            num(x),
            num(y),
            xml_escape_attr(lf.family_or("sans-serif")),
            num(label_size),
            xml_escape_attr(&lf.weight_str()),
            xml_escape_attr(lf.color.as_deref().ok_or("sankey: style.label.font color missing")?),
            xml_escape(&nodes[ni].name),
        );
        if show_value {
            let _ = write!(
                out,
                "<tspan font-family=\"{}\" font-size=\"{}\" font-weight=\"{}\" fill=\"{}\"> {}</tspan>",
                xml_escape_attr(vf.family_or("sans-serif")),
                num(vf.size_or(label_size)),
                xml_escape_attr(&vf.weight_str()),
                xml_escape_attr(vf.color.as_deref().ok_or("sankey: style.value_label.font color missing")?),
                xml_escape(&fmt(inflow[ni].max(outflow[ni]))?),
            );
        }
        out.push_str("</text>");
    }
    out.push_str("</g>");

    let mut svg = String::new();
    ChartFrame::new(payload.width, payload.height, (pad.left, pad.right, pad.top, pad.bottom), &r.background, num)
        .open(&mut svg);
    Ok(svg + &out + "</svg>")
}

/// A label's advance at its painted weight.
fn measure(fonts: &FontBook, text: &str, font: &Font, size: f64) -> Result<f64, String> {
    let weight: f32 = font
        .weight_str()
        .parse()
        .map_err(|_| format!("sankey: label weight {:?} is not numeric", font.weight_str()))?;
    fonts
        .measure_at_weight(text, font.family_or("sans-serif"), size, Some(weight))
        .map_err(|e| format!("sankey: measuring label {text:?}: {e}"))
}

/// Every label is one line right of its node. No label may cross the plot's
/// right edge, or touch another label or any node but its own — two such boxes intersecting means ink
/// printed over ink.
fn check_label_room(
    laid: &Laid,
    nodes: &[NodeKey],
    label_w: &[f64],
    gap: f64,
    label_size: f64,
    x_max: f64,
) -> Result<(), String> {
    let label_box = |i: usize| {
        let [_, x1, y0, y1] = laid.nodes[i].0;
        let cy = (y0 + y1) / 2.0;
        [x1 + gap, x1 + gap + label_w[i], cy - label_size / 2.0, cy + label_size / 2.0]
    };
    let hit = |a: [f64; 4], b: [f64; 4]| a[0] < b[1] && b[0] < a[1] && a[2] < b[3] && b[2] < a[3];
    for i in 0..nodes.len() {
        let li = label_box(i);
        if li[1] > x_max {
            return Err(format!(
                "sankey: the label for `{}` runs {}px past the chart's right edge; give the \
                 chart more room, or shorten or remove labels",
                nodes[i].name,
                num(li[1] - x_max)
            ));
        }
        for j in 0..nodes.len() {
            if i == j {
                continue;
            }
            let [nx0, nx1, ny0, ny1] = laid.nodes[j].0;
            let what = if hit(li, [nx0, nx1, ny0, ny1]) {
                "node"
            } else if j > i && hit(li, label_box(j)) {
                "label"
            } else {
                continue;
            };
            return Err(format!(
                "sankey: the label for `{}` would overlap the {what} `{}`; give the chart more \
                 room, or shorten or remove labels",
                nodes[i].name, nodes[j].name
            ));
        }
    }
    Ok(())
}

/// Validate every row and intern nodes in first-appearance order (a row's
/// source, then its target) — the order d3 lays each column out from.
fn build_graph(
    r: &Resolved,
    rows: &[serde_json::Map<String, Value>],
) -> Result<(Vec<NodeKey>, Vec<Flow>), String> {
    if rows.is_empty() {
        return Err("sankey: query returned no rows".into());
    }
    let mut nodes: Vec<NodeKey> = Vec::new();
    let mut index: HashMap<(Option<i64>, String), usize> = HashMap::new();
    let mut seen: HashMap<(usize, usize), usize> = HashMap::new();
    let mut flows = Vec::with_capacity(rows.len());
    let mut intern = |name: String, step: Option<i64>, nodes: &mut Vec<NodeKey>| {
        *index.entry((step, name.clone())).or_insert_with(|| {
            nodes.push(NodeKey { name, step });
            nodes.len() - 1
        })
    };
    for (i, row) in rows.iter().enumerate() {
        let source = name_cell(row, &r.source, i)?;
        let target = name_cell(row, &r.target, i)?;
        let value = match row.get(&r.value) {
            None => return Err(format!("sankey: row {i} has no `{}` column", r.value)),
            Some(Value::Number(n)) => n.as_f64().expect("serde_json number converts"),
            Some(other) => return Err(format!("sankey: row {i} `{}` is {other}, not a number", r.value)),
        };
        if !(value.is_finite() && value > 0.0) {
            return Err(format!(
                "sankey: row {i} ({source} → {target}) has `{}` {value}; every flow must be positive",
                r.value
            ));
        }
        let step = match &r.step {
            None => None,
            Some(f) => match row.get(f) {
                Some(Value::Number(n)) if n.as_i64().is_some() => n.as_i64(),
                None => return Err(format!("sankey: row {i} has no `{f}` column")),
                Some(other) => return Err(format!("sankey: row {i} `{f}` is {other}, not an integer")),
            },
        };
        if step.is_none() && !r.loopbacks && source == target {
            return Err(format!("sankey: row {i} flows from `{source}` to itself"));
        }
        let s = intern(source.clone(), step, &mut nodes);
        let t = intern(target.clone(), step.map(|k| k + 1), &mut nodes);
        if let Some(first) = seen.insert((s, t), i) {
            return Err(format!(
                "sankey: rows {first} and {i} both flow {source} → {target}{}; aggregate them in \
                 the query (`group by`)",
                step.map(|k| format!(" at step {k}")).unwrap_or_default()
            ));
        }
        flows.push(Flow { source: s, target: t, value });
    }
    Ok((nodes, flows))
}

fn name_cell(row: &serde_json::Map<String, Value>, field: &str, i: usize) -> Result<String, String> {
    match row.get(field) {
        Some(Value::String(s)) if !s.is_empty() => Ok(s.clone()),
        None => Err(format!("sankey: row {i} has no `{field}` column")),
        Some(other) => Err(format!("sankey: row {i} `{field}` is {other}, not a node name")),
    }
}

/// The first cycle a depth-first walk meets, as node indices with the start
/// repeated at the end (`A → B → A`).
fn find_cycle(n: usize, flows: &[Flow]) -> Option<Vec<usize>> {
    let mut adj = vec![Vec::new(); n];
    for f in flows {
        adj[f.source].push(f.target);
    }
    // 0 unvisited, 1 on the stack, 2 done.
    let mut state = vec![0u8; n];
    let mut stack: Vec<usize> = Vec::new();
    fn visit(v: usize, adj: &[Vec<usize>], state: &mut [u8], stack: &mut Vec<usize>) -> Option<Vec<usize>> {
        state[v] = 1;
        stack.push(v);
        for &w in &adj[v] {
            if state[w] == 1 {
                let start = stack.iter().position(|&x| x == w).expect("on stack");
                let mut cycle = stack[start..].to_vec();
                cycle.push(w);
                return Some(cycle);
            }
            if state[w] == 0 {
                if let Some(c) = visit(w, adj, state, stack) {
                    return Some(c);
                }
            }
        }
        stack.pop();
        state[v] = 2;
        None
    }
    (0..n).find_map(|v| if state[v] == 0 { visit(v, &adj, &mut state, &mut stack) } else { None })
}

/// Under a bound color channel, one color per node name, so a name keeps its
/// color in every column:
/// palette slots by first appearance, cycling like pie's positional fill.
fn node_colors(nodes: &[NodeKey], palette: &[String]) -> Vec<String> {
    let mut slots: HashMap<&str, usize> = HashMap::new();
    nodes
        .iter()
        .map(|n| {
            let next = slots.len();
            let slot = *slots.entry(n.name.as_str()).or_insert(next);
            palette[slot % palette.len()].clone()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn payload(rows: Value, step: Option<&str>) -> Payload {
        build(rows, step, false, None)
    }

    fn with_loopbacks(rows: Value, step: Option<&str>, loopbacks: bool) -> Payload {
        build(rows, step, loopbacks, None)
    }

    fn build(rows: Value, step: Option<&str>, loopbacks: bool, color: Option<&str>) -> Payload {
        serde_json::from_value(json!({
            "resolved": {
                "chart_type": "sankey",
                "source": "from_stage",
                "target": "to_stage",
                "value": "users",
                "step": step,
                "loopbacks": loopbacks,
                "color": color,
                "title": "Signup flow",
                "background": "#ffffff",
                "palette": ["#3164a3", "#779bc9", "#ad9c7f", "#c46b4f"],
                "value_format": ",d",
                "value_title": "Users",
                "style": {
                    "node_align": "justify",
                    "node_width": 12.0,
                    "node_padding": 14.0,
                    "iterations": 6,
                    "single_series_fill": "#23467f",
                    "link_opacity": 0.45,
                    "min_link_width": 1.0,
                    "loop_gap": 2.0,
                    "loop_radius": 10.0,
                    "label": {"family": "Inter", "size": 11.0, "color": "#1a1a1a", "weight": 500},
                    "value_label": {"family": "Inter", "size": 11.0, "color": "#6b6b6b", "weight": 400},
                    "label_gap": 6.0,
                    "label_content": "name_value",
                },
            },
            "rows": rows,
            "width": 600.0,
            "height": 360.0,
            "padding": {"left": 8.0, "right": 8.0, "top": 8.0, "bottom": 8.0},
        }))
        .unwrap()
    }

    fn funnel() -> Value {
        json!([
            {"from_stage": "Visit", "to_stage": "Signup", "users": 400},
            {"from_stage": "Visit", "to_stage": "Bounced", "users": 600},
            {"from_stage": "Signup", "to_stage": "Activated", "users": 250},
            {"from_stage": "Signup", "to_stage": "Churned", "users": 150},
        ])
    }

    fn fonts() -> FontBook {
        let dir = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(dir).expect("test fonts load")
    }

    fn render_ok(p: &Payload) -> String {
        render(p, &fonts()).unwrap()
    }

    #[test]
    fn a_funnel_paints_one_ribbon_per_row_and_one_rect_per_node() {
        let svg = render_ok(&payload(funnel(), None));
        assert_eq!(svg.matches("data-link=").count(), 4);
        assert_eq!(svg.matches("data-node=").count(), 5);
        assert!(svg.contains("⁡Visit → Signup; Users: 400; \u{2060}Share of Visit: 40%"), "{svg}");
        assert!(svg.contains(">Visit<tspan"), "{svg}");
        assert!(svg.contains("> 1,000</tspan>"), "{svg}");
    }

    #[test]
    fn the_dispatcher_stamps_the_view_root() {
        let svg = crate::render(&payload(funnel(), None), &fonts()).unwrap();
        assert!(svg[..80].contains(" class=\"marks\""), "{}", &svg[..80]);
    }

    #[test]
    fn a_node_keeps_its_color_in_every_column() {
        let rows = json!([
            {"from_stage": "Free", "to_stage": "Free", "users": 70, "step": 0},
            {"from_stage": "Free", "to_stage": "Pro", "users": 30, "step": 0},
            {"from_stage": "Pro", "to_stage": "Pro", "users": 40, "step": 0},
        ]);
        let svg = render_ok(&build(rows, Some("step"), false, Some("from_stage")));
        // Free@0, Free@1, Pro@1, Pro@0 → Free and Pro each keep one color.
        let fills: Vec<&str> = svg
            .split("<rect x=")
            .skip(1)
            .map(|s| s.split("fill=\"").nth(1).unwrap().split('"').next().unwrap())
            .collect();
        assert_eq!(fills, ["#3164a3", "#3164a3", "#779bc9", "#779bc9"]);
    }

    #[test]
    fn unbound_color_paints_every_mark_one_ink() {
        let svg = render_ok(&payload(funnel(), None));
        let marks = svg.matches("fill=\"#23467f\"").count();
        assert_eq!(marks, 4 + 5, "every ribbon and node: {svg}");
    }

    #[test]
    fn color_by_target_paints_ribbons_by_their_target() {
        let svg = render_ok(&build(funnel(), None, false, Some("to_stage")));
        let ribbon = svg.split("<path").find(|p| p.contains("Visit → Bounced")).unwrap();
        let bounced = svg.split("<rect").find(|p| p.contains("\u{2061}Bounced;")).unwrap();
        let fill = |t: &str| t.split("fill=\"").nth(1).unwrap().split('"').next().unwrap().to_string();
        assert_eq!(fill(ribbon), fill(bounced));
    }

    #[test]
    fn color_by_another_column_refuses() {
        let err = render(&build(funnel(), None, false, Some("users")), &fonts()).unwrap_err();
        assert!(err.contains("color `users` is neither"), "{err}");
    }

    /// A left-aligned sink lands mid-chart, where no margin is reserved; its
    /// long label would run off the canvas.
    #[test]
    fn a_label_past_the_right_edge_refuses() {
        let long = "An extremely long destination name that cannot possibly fit anywhere";
        let rows = json!([
            {"from_stage": "A", "to_stage": long, "users": 900},
            {"from_stage": "A", "to_stage": "C", "users": 100},
            {"from_stage": "C", "to_stage": "D", "users": 100},
        ]);
        let mut p = payload(rows, None);
        p.resolved["style"]["node_align"] = json!("left");
        let err = render(&p, &fonts()).unwrap_err();
        assert!(err.contains("past the chart's right edge"), "{err}");
    }

    /// The chart paints its own resolved background, never the board's
    /// (`docs/laws-shared.md#background`).
    #[test]
    fn paints_the_charts_own_background() {
        let mut p = payload(funnel(), None);
        p.resolved["background"] = json!("#f5f0e6");
        p.background = Some("#ffffff".into());
        let svg = render_ok(&p);
        assert!(svg.contains("fill=\"#f5f0e6\"/>"), "{}", &svg[..200]);
    }

    #[test]
    fn labels_carry_no_halo() {
        let svg = render_ok(&payload(funnel(), None));
        assert!(!svg.contains("paint-order") && !svg.contains("stroke="), "{svg}");
    }

    #[test]
    fn last_column_labels_get_a_right_margin() {
        let svg = render_ok(&payload(funnel(), None));
        let x_of = |name: &str| -> f64 {
            let t = svg.split("<text").find(|t| t.contains(&format!(">{name}<"))).unwrap();
            t.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap()
        };
        // "Bounced 600" at 11px Inter is ~60px; it starts right of its node
        // and still ends inside the 600px canvas less the 8px padding.
        assert!(x_of("Bounced") < 592.0 - 55.0, "{}", x_of("Bounced"));
    }

    #[test]
    fn a_cycle_refuses_naming_it() {
        let rows = json!([
            {"from_stage": "Cart", "to_stage": "Checkout", "users": 5},
            {"from_stage": "Checkout", "to_stage": "Cart", "users": 2},
        ]);
        let err = render(&payload(rows, None), &fonts()).unwrap_err();
        assert!(err.contains("(Cart → Checkout → Cart)"), "{err}");
        assert!(err.contains("`step`") && err.contains("`loopbacks`"), "{err}");
    }

    fn tickets() -> Value {
        json!([
            {"from_stage": "New", "to_stage": "Triage", "users": 100},
            {"from_stage": "Triage", "to_stage": "In progress", "users": 80},
            {"from_stage": "Triage", "to_stage": "Won't fix", "users": 20},
            {"from_stage": "In progress", "to_stage": "Review", "users": 95},
            {"from_stage": "Review", "to_stage": "In progress", "users": 30},
            {"from_stage": "Review", "to_stage": "Done", "users": 65},
        ])
    }

    #[test]
    fn loopbacks_draw_a_cycle_as_a_return_flow() {
        let svg = render_ok(&with_loopbacks(tickets(), None, true));
        assert_eq!(svg.matches("data-link=").count(), 6);
        assert_eq!(svg.matches("data-loop=\"true\"").count(), 1, "{svg}");
        let lp = svg.split("<path").find(|p| p.contains("data-loop")).unwrap();
        assert!(lp.contains("Review → In progress"), "{lp}");
        assert!(lp.contains("A"), "a loop routes with arcs: {lp}");
    }

    #[test]
    fn a_looped_chart_stays_inside_its_canvas() {
        let p = with_loopbacks(tickets(), None, true);
        let svg = render_ok(&p);
        let coords: Vec<f64> = svg
            .split("<rect x=\"")
            .skip(1)
            .flat_map(|s| {
                let x: f64 = s.split('"').next().unwrap().parse().unwrap();
                let y: f64 = s.split("y=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                let h: f64 = s.split("height=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                [x, y, y + h]
            })
            .collect();
        assert!(coords.iter().all(|&c| (0.0..=600.0).contains(&c)), "{coords:?}");
    }

    #[test]
    fn loopbacks_allow_a_self_loop() {
        let rows = json!([
            {"from_stage": "A", "to_stage": "B", "users": 10},
            {"from_stage": "B", "to_stage": "B", "users": 4},
            {"from_stage": "B", "to_stage": "C", "users": 8},
        ]);
        let svg = render_ok(&with_loopbacks(rows, None, true));
        assert_eq!(svg.matches("data-loop=\"true\"").count(), 1, "{svg}");
    }

    #[test]
    fn step_with_loopbacks_refuses() {
        let err = render(&with_loopbacks(funnel(), Some("step"), true), &fonts()).unwrap_err();
        assert!(err.contains("`step` and `loopbacks`"), "{err}");
    }

    #[test]
    fn the_same_cycle_unrolls_with_step() {
        let rows = json!([
            {"from_stage": "Cart", "to_stage": "Checkout", "users": 5, "step": 1},
            {"from_stage": "Checkout", "to_stage": "Cart", "users": 2, "step": 2},
        ]);
        let svg = render_ok(&payload(rows, Some("step")));
        assert_eq!(svg.matches("data-node=").count(), 3);
    }

    #[test]
    fn bad_rows_refuse_by_name() {
        let cases = [
            (json!([{"from_stage": "A", "to_stage": "B", "users": 0}]), "every flow must be positive"),
            (json!([{"from_stage": "A", "to_stage": "B", "users": -3}]), "every flow must be positive"),
            (json!([{"from_stage": "A", "to_stage": "B", "users": null}]), "not a number"),
            (json!([{"from_stage": "A", "to_stage": "A", "users": 1}]), "to itself"),
            (json!([{"from_stage": null, "to_stage": "B", "users": 1}]), "not a node name"),
            (json!([{"to_stage": "B", "users": 1}]), "no `from_stage` column"),
            (
                json!([
                    {"from_stage": "A", "to_stage": "B", "users": 1},
                    {"from_stage": "A", "to_stage": "B", "users": 2},
                ]),
                "rows 0 and 1 both flow A → B",
            ),
            (json!([]), "no rows"),
        ];
        for (rows, want) in cases {
            let err = render(&payload(rows.clone(), None), &fonts()).unwrap_err();
            assert!(err.contains(want), "{rows}: {err}");
        }
    }

    #[test]
    fn a_step_gap_refuses() {
        let rows = json!([
            {"from_stage": "A", "to_stage": "B", "users": 1, "step": 0},
            {"from_stage": "C", "to_stage": "D", "users": 1, "step": 3},
        ]);
        let err = render(&payload(rows, Some("step")), &fonts()).unwrap_err();
        assert!(err.contains("`step` leaves a column empty"), "{err}");
    }

    #[test]
    fn a_crowded_column_refuses_instead_of_overlapping_labels() {
        let rows: Vec<Value> = (0..40)
            .map(|i| json!({"from_stage": "All", "to_stage": format!("Leaf {i}"), "users": 1}))
            .collect();
        let err = render(&payload(Value::Array(rows), None), &fonts()).unwrap_err();
        assert!(err.contains("would overlap"), "{err}");
    }

    #[test]
    fn an_unknown_style_key_refuses() {
        let mut p = payload(funnel(), None);
        p.resolved["style"]["node_sort"] = json!("query");
        let err = render(&p, &fonts()).unwrap_err();
        assert!(err.contains("unknown field `node_sort`"), "{err}");
    }
}

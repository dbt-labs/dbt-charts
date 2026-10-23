//! Input production rejects with a registered ERR code, decided from the
//! resolved payload before any cartesian family models it. Each check names
//! the production raise site it ports; Rust carries the code and params only.

use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap};

use serde::Deserialize;
use serde_json::{json, Map, Value};

use crate::codes;
use crate::diagnostic::{py_repr, Diagnostic};
use crate::families::multiples::{self, PartitionAxis};
use crate::families::table::coerce_numeric_cell;
use crate::families::temporal::{infer_vega_type, VegaType};
use crate::Payload;

type Row = Map<String, Value>;

#[derive(Deserialize)]
struct Channel {
    mode: String,
    data_field: Option<String>,
}

#[derive(Deserialize)]
struct Sort {
    by: String,
}

#[derive(Deserialize, Default)]
struct LabelSlot {
    field: Option<String>,
    visible: Option<bool>,
    position: Option<String>,
}

#[derive(Deserialize, Default)]
struct MarkStyle {
    labels: Option<LabelSlot>,
    curve: Option<String>,
}

#[derive(Deserialize)]
struct LayerAxis {
    position: Option<String>,
}

#[derive(Deserialize)]
struct Layer {
    #[serde(rename = "type")]
    kind: String,
    x: Option<String>,
    y: Option<Value>,
    color: Option<String>,
    query_name: Option<String>,
    axis_y: Option<LayerAxis>,
    bar_mark: Option<MarkStyle>,
    line_mark: Option<MarkStyle>,
    point_mark: Option<MarkStyle>,
    area_mark: Option<MarkStyle>,
}

impl Layer {
    /// `_layer_label_slots`: the label slots this layer's own marks carry.
    fn label_slots(&self) -> Vec<&LabelSlot> {
        let marks = match self.kind.as_str() {
            "bar" => [self.bar_mark.as_ref(), None],
            "line" => [self.line_mark.as_ref(), self.point_mark.as_ref()],
            "area" => [self.line_mark.as_ref(), None],
            _ => [self.point_mark.as_ref(), None],
        };
        marks.into_iter().flatten().filter_map(|m| m.labels.as_ref()).collect()
    }
}

#[derive(Deserialize, Default)]
struct EndpointLabels {
    visible: bool,
}

#[derive(Deserialize, Default)]
struct LabelFont {
    case: Option<String>,
}

#[derive(Deserialize, Default)]
struct AxisLabels {
    align: Option<String>,
    expr: Option<Value>,
    format: Option<Value>,
    font: Option<LabelFont>,
}

#[derive(Deserialize, Default)]
struct AxisY {
    mirror: Option<Value>,
    position: Option<String>,
    #[serde(default)]
    tick_values: Vec<f64>,
    labels: Option<AxisLabels>,
}

#[derive(Deserialize, Default)]
struct LegendPosition {
    align: Option<String>,
    #[serde(default)]
    overlay: bool,
}

#[derive(Deserialize, Default)]
struct Legend {
    #[serde(default)]
    visible: bool,
    position: Option<LegendPosition>,
}

#[derive(Deserialize, Default)]
struct Style {
    #[serde(default)]
    endpoint_labels: EndpointLabels,
    #[serde(default)]
    axis_y: AxisY,
    mark: Option<MarkStyle>,
    line_mark: Option<MarkStyle>,
    point_mark: Option<MarkStyle>,
}

#[derive(Deserialize)]
struct Multiples {
    rows: Option<String>,
    columns: Option<String>,
}

/// The slice of a resolved cartesian chart the checks read.
#[derive(Deserialize)]
struct Facts {
    id: String,
    chart_type: String,
    query_name: Option<String>,
    x: Option<String>,
    y: Option<Value>,
    color: Option<String>,
    link: Option<String>,
    orientation: Option<String>,
    stack: Option<String>,
    y_start: Option<String>,
    sort: Option<Sort>,
    multiples: Option<Multiples>,
    support_table: Option<Value>,
    #[serde(default)]
    layers: Vec<Layer>,
    #[serde(default)]
    wide_measures: Vec<String>,
    #[serde(default)]
    resolved_channels: BTreeMap<String, Channel>,
    #[serde(default)]
    panel_axes: Vec<PartitionAxis>,
    #[serde(default)]
    style: Style,
    #[serde(default)]
    legend: Legend,
}

#[derive(PartialEq)]
enum Rail {
    Right,
    Top,
}

impl Facts {
    fn chart_id(&self) -> &str {
        &self.id
    }

    /// The id the duplicate-row and pre-aggregated-histogram codes print
    /// (`chart.id or "unknown"`).
    fn chart_id_or_unknown(&self) -> &str {
        if self.id.is_empty() {
            "unknown"
        } else {
            &self.id
        }
    }

    /// `str.title()` of a chart type: `bar` is `Bar`.
    fn title(&self) -> String {
        let mut chars = self.chart_type.chars();
        chars.next().map(|c| c.to_uppercase().chain(chars).collect()).unwrap_or_default()
    }

    /// `endpoint_rail_layout`: which endpoint-label rail this chart draws.
    fn rail(&self) -> Option<Rail> {
        if !matches!(self.chart_type.as_str(), "bar" | "line" | "area") || !self.style.endpoint_labels.visible {
            return None;
        }
        let color = self.resolved_channels.get("color");
        let series = color.is_some_and(|c| c.mode == "series");
        if self.chart_type == "bar" && self.orientation.as_deref() == Some("horizontal") {
            return (series && !matches!(self.stack.as_deref(), None | Some("none"))).then_some(Rail::Top);
        }
        if series {
            return Some(Rail::Right);
        }
        let layered = self.x.is_some() && self.y.as_ref().is_some_and(Value::is_string);
        (color.is_none() && layered && !self.layers.is_empty() && self.layers.iter().all(|l| l.color.is_none()))
            .then_some(Rail::Right)
    }

    fn mirrored(&self) -> bool {
        !matches!(self.style.axis_y.mirror, None | Some(Value::Null) | Some(Value::Bool(false)))
    }

    /// `effective_color_field`: the series column, unless the color is a literal.
    fn color_field(&self) -> Option<&str> {
        self.resolved_channels.get("color").filter(|c| c.mode != "literal")?.data_field.as_deref().filter(|f| !f.is_empty())
    }
}

/// Position of a row in the query, carried through the panel split.
const ROW_INDEX: &str = "__row_index";

/// Raises the first ERR production raises for this chart, in production's order.
pub fn check(payload: &Payload, chart_type: &str) -> Result<(), Diagnostic> {
    if !matches!(chart_type, "bar" | "line" | "area" | "scatter" | "heatmap" | "histogram") {
        return Ok(());
    }
    let facts = Facts::deserialize(&payload.resolved).map_err(|e| format!("{chart_type} payload: {e}"))?;
    if let (Some(template), "bar" | "line" | "area" | "scatter") = (&facts.link, chart_type) {
        let mut missing = None;
        // The walk only finds the first placeholder naming a channel the chart does not encode.
        let _ = crate::href::resolve_channel_href(template, |channel| {
            let encoded = match channel {
                "x" => facts.x.is_some(),
                "y" => facts.y.as_ref().is_some_and(|y| !y.is_null()),
                "color" => facts.color.is_some(),
                // `theta` is a pie channel; these families never encode it.
                _ => false,
            };
            if encoded {
                return Ok(String::new());
            }
            missing = Some(channel.to_string());
            Err(String::new())
        });
        if let Some(channel) = missing {
            return Err(Diagnostic::new(
                &codes::ERR_LINK_CHANNEL_NOT_ENCODED,
                [("chart_id", json!(facts.chart_id())), ("channel", json!(channel))],
            ));
        }
    }
    if facts.x.is_none() && !payload.resolved.get("support_table").is_none_or(Value::is_null) {
        return Err(Diagnostic::new(&codes::ERR_SUPPORT_TABLE_NO_X_ENCODING, []));
    }
    // Faceted against zero rows at resolve, rendered with rows: no panel axes to split by. Rendered
    // against zero rows too, it is the empty facet.
    if facts.multiples.is_some() && facts.panel_axes.is_empty() && !payload.rows.is_empty() {
        return Err(Diagnostic::new(&codes::ERR_MULTIPLES_RESOLVED_WITHOUT_DATA, [("chart_id", json!(facts.chart_id()))]));
    }
    if matches!(chart_type, "bar" | "line" | "area") {
        // Bar rewrites a labeled or year-shaped x over the whole column, then
        // re-slices by each row's raw panel membership (`restripe`: the x can
        // itself be the multiples field), and a panel's rows carry no partition column.
        let checked = match (chart_type, facts.x.as_deref()) {
            ("bar", Some(x)) => super::temporal::normalize_labeled_temporal(&payload.rows, x)?,
            _ => Cow::Borrowed(payload.rows.as_slice()),
        };
        if facts.panel_axes.is_empty() {
            duplicate_rows(&facts, &checked)?;
        } else {
            let indexed: Vec<Row> = payload
                .rows
                .iter()
                .enumerate()
                .map(|(i, row)| {
                    let mut row = row.clone();
                    row.insert(ROW_INDEX.to_string(), json!(i));
                    row
                })
                .collect();
            for panel in multiples::regroup(&facts.panel_axes, &indexed)? {
                let rows: Vec<Row> = panel
                    .rows
                    .iter()
                    .filter_map(|row| row.get(ROW_INDEX).and_then(Value::as_u64))
                    .map(|i| {
                        let mut row = checked[i as usize].clone();
                        for axis in &facts.panel_axes {
                            row.remove(&axis.field);
                        }
                        row
                    })
                    .collect();
                duplicate_rows(&facts, &rows)?;
            }
        }
        null_series(&facts, &payload.rows)?;
    }
    if chart_type == "histogram" {
        histogram(&facts, &payload.rows)?;
    }
    if matches!(chart_type, "bar" | "line" | "area" | "scatter" | "heatmap") {
        super::cartesian_x_errors::check(&payload.resolved, &payload.rows)?;
    }
    layers(&facts, payload)?;
    if matches!(chart_type, "bar" | "line" | "area") {
        endpoint_rail(&facts, &payload.rows)?;
    }
    value_labels(&facts, payload)?;
    mirror(&facts, &payload.rows)?;
    multiples_conflicts(&facts, payload)?;
    legend_align(&facts)
}

/// `validation.py::_plot_key_fields`: the columns one row per key must hold.
fn plot_key_fields(facts: &Facts, rows: &[Row]) -> Vec<String> {
    let color = if facts.wide_measures.is_empty() { facts.color_field() } else { facts.color.as_deref() };
    let y_fields: Vec<&str> = if facts.wide_measures.is_empty() {
        facts.y.as_ref().and_then(Value::as_str).into_iter().collect()
    } else {
        facts.wide_measures.iter().map(String::as_str).collect()
    };
    let Some(x_field) = facts.x.as_deref().filter(|x| !x.is_empty()) else { return Vec::new() };
    if y_fields.is_empty() {
        return Vec::new();
    }
    let y_types: Vec<VegaType> = y_fields.iter().map(|f| infer_vega_type(rows, f)).collect();
    let mut fields = if facts.chart_type == "bar" {
        let x_type = infer_vega_type(rows, x_field);
        let all_quantitative = y_types.iter().all(|t| *t == VegaType::Quantitative);
        if x_type != VegaType::Quantitative && all_quantitative {
            vec![x_field.to_string()]
        } else if y_fields.len() == 1 && matches!(y_types[0], VegaType::Nominal | VegaType::Ordinal) && x_type == VegaType::Quantitative {
            vec![y_fields[0].to_string()]
        } else {
            return Vec::new();
        }
    } else {
        if y_types.iter().any(|t| *t != VegaType::Quantitative) {
            return Vec::new();
        }
        vec![x_field.to_string()]
    };
    fields.extend(color.map(str::to_string));
    fields
}

/// `validation.py::validate_preaggregated_data`, for one panel's rows.
fn duplicate_rows(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    let fields = plot_key_fields(facts, rows);
    if fields.is_empty() {
        return Ok(());
    }
    // Counter semantics: keys in first-seen order, each previewed by its first row.
    let mut counts: HashMap<Vec<crate::common::PyKey>, usize> = HashMap::new();
    let mut first_seen: Vec<(Vec<crate::common::PyKey>, Vec<&Value>)> = Vec::new();
    for row in rows {
        let key: Vec<&Value> = fields.iter().map(|f| row.get(f).unwrap_or(&Value::Null)).collect();
        let identity: Vec<crate::common::PyKey> = key.iter().map(|v| crate::common::py_scalar_key(v)).collect();
        let count = counts.entry(identity.clone()).or_insert(0);
        *count += 1;
        if *count == 1 {
            first_seen.push((identity, key));
        }
    }
    let duplicated: Vec<&Vec<&Value>> = first_seen.iter().filter(|(id, _)| counts[id] > 1).map(|(_, key)| key).collect();
    if duplicated.is_empty() {
        return Ok(());
    }
    let preview: Vec<String> = duplicated
        .iter()
        .take(3)
        .map(|key| fields.iter().zip(key.iter()).map(|(f, v)| format!("{f}={}", py_repr(v))).collect::<Vec<_>>().join(", "))
        .collect();
    Err(Diagnostic::new(
        &codes::ERR_BAR_DUPLICATE_ROWS,
        [
            ("chart_id", json!(facts.chart_id_or_unknown())),
            ("chart_type", json!(facts.title())),
            ("duplicate_preview", json!(preview.join("; "))),
            ("field_list", json!(fields.join(", "))),
        ],
    ))
}

/// `validation.py::validate_color_series`: a NULL in a categorical series column.
fn null_series(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    let Some(channel) = facts.resolved_channels.get("color").filter(|c| c.mode == "series") else { return Ok(()) };
    if channel.data_field.as_deref().is_none_or(str::is_empty) {
        return Ok(());
    }
    let wide = !facts.wide_measures.is_empty();
    let Some(field) = (if wide { facts.color.as_deref() } else { channel.data_field.as_deref() }) else { return Ok(()) };
    if !wide && !matches!(infer_vega_type(rows, field), VegaType::Nominal | VegaType::Ordinal) {
        return Ok(());
    }
    let null_rows = rows.iter().filter(|row| row.get(field).is_none_or(Value::is_null)).count();
    if null_rows == 0 {
        return Ok(());
    }
    Err(Diagnostic::new(
        &codes::ERR_COLOR_NULL_SERIES,
        [
            ("chart_id", json!(facts.chart_id())),
            ("chart_type", json!(facts.title())),
            ("color_field", json!(field)),
            ("null_rows", json!(null_rows)),
        ],
    ))
}

/// `endpoint_labels.py::EndpointLabelFeature.apply`: the rail anchors on a
/// cumulative stack, so a center stack, a negative segment or a sort column
/// with no numbers has no rail to draw.
fn endpoint_rail(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    let Some(rail) = facts.rail() else { return Ok(()) };
    if !facts.resolved_channels.get("color").is_some_and(|c| c.mode == "series") {
        return Ok(());
    }
    let (Some(_), Some(y)) = (facts.x.as_deref(), facts.y.as_ref().and_then(Value::as_str)) else { return Ok(()) };
    let is_stacked = rail == Rail::Top || (facts.chart_type != "line" && !matches!(facts.stack.as_deref(), None | Some("none")));
    if !is_stacked {
        return Ok(());
    }
    // Only a bar's rail predicts the order its sorted axis draws.
    if let Some(sort) = facts.sort.as_ref().filter(|_| facts.chart_type == "bar") {
        if !rows.iter().any(|row| coerce_numeric_cell(row.get(&sort.by)).is_some()) {
            return Err(Diagnostic::new(
                &codes::ERR_ENDPOINT_LABELS_UNORDERABLE_SORT,
                [("chart_id", json!(facts.chart_id())), ("sort_by", json!(sort.by))],
            ));
        }
    }
    if rail == Rail::Top && facts.stack.as_deref() == Some("center") {
        return Err(Diagnostic::new(&codes::ERR_ENDPOINT_LABELS_CENTER_STACK, [("chart_id", json!(facts.chart_id()))]));
    }
    let measures: Vec<&str> = if facts.wide_measures.is_empty() { vec![y] } else { facts.wide_measures.iter().map(String::as_str).collect() };
    let negative = rows.iter().any(|row| {
        measures.iter().any(|m| row.get(*m).is_some_and(|v| !v.is_null()) && coerce_numeric_cell(row.get(*m)).is_some_and(|n| n < 0.0))
    });
    if negative {
        return Err(Diagnostic::new(&codes::ERR_ENDPOINT_LABELS_NEGATIVE_STACK, [("chart_id", json!(facts.chart_id()))]));
    }
    Ok(())
}

/// `mirror_axis.py::MirrorAxisFeature.apply`.
fn mirror(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    if !facts.mirrored() {
        return Ok(());
    }
    let chart_id = || json!(facts.chart_id());
    // Layers mirror when they share the base's one y scale: a layer pinning its own y
    // axis has a scale of its own, and a horizontal bar measures along x.
    let horizontal = facts.chart_type == "bar" && facts.orientation.as_deref() == Some("horizontal");
    let own_scale = |layer: &Layer| layer.axis_y.as_ref().is_some_and(|axis| axis.position.is_some());
    if !facts.layers.is_empty() && (horizontal || facts.layers.iter().any(own_scale)) {
        return Err(Diagnostic::new(&codes::ERR_MIRROR_LAYERS, [("chart_id", chart_id())]));
    }
    // A multi-measure heatmap folds its list `y` into sublayers: no shared y encoding.
    if let Some(Value::Array(y)) = &facts.y {
        let names: Vec<&str> = y.iter().filter_map(Value::as_str).collect();
        return Err(Diagnostic::new(&codes::ERR_MIRROR_MULTI_SERIES, [("chart_id", chart_id()), ("y", json!(names.join(", ")))]));
    }
    // No y anywhere (a histogram's count aside) leaves nothing to reflect.
    if facts.y.as_ref().is_none_or(Value::is_null) && facts.chart_type != "histogram" {
        return Ok(());
    }
    if !facts.wide_measures.is_empty() {
        return Err(Diagnostic::new(
            &codes::ERR_MIRROR_MULTI_SERIES,
            [("chart_id", chart_id()), ("y", json!(facts.wide_measures.join(", ")))],
        ));
    }
    if facts.rail().is_some() {
        let code = if facts.multiples.is_some() { &codes::ERR_MULTIPLES_ENDPOINT_LABELS } else { &codes::ERR_MIRROR_ENDPOINT_LABELS };
        return Err(Diagnostic::new(code, [("chart_id", chart_id())]));
    }
    mirror_format(facts, rows)?;
    unmeasurable_align(facts)
}

/// The `mirror.format` gate: the ghost relabels the shared y scale, which
/// is categorical on a horizontal bar, a heatmap and a dot plot, so a number
/// format can only read numeric-looking categories.
fn mirror_format(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    let Some(format) = facts.style.axis_y.mirror.as_ref().and_then(|m| m.get("format")).filter(|f| !f.is_null()) else {
        return Ok(());
    };
    let (field, vl_type) = match facts.chart_type.as_str() {
        "bar" if facts.orientation.as_deref() == Some("horizontal") => {
            let Some(x) = facts.x.as_deref() else { return Ok(()) };
            (x, infer_vega_type(rows, x))
        }
        "heatmap" => {
            let Some(y) = facts.y.as_ref().and_then(Value::as_str) else { return Ok(()) };
            (y, VegaType::Nominal)
        }
        "scatter" => {
            let Some(y) = facts.y.as_ref().and_then(Value::as_str) else { return Ok(()) };
            (y, infer_vega_type(rows, y))
        }
        _ => return Ok(()),
    };
    let slot: crate::common::FormatSlot =
        Deserialize::deserialize(format).map_err(|e| format!("{}: axis_y.mirror.format is not a ResolvedFormat: {e}", facts.chart_type))?;
    let move_it_to = match facts.chart_type.as_str() {
        "heatmap" => super::cartesian_x_errors::HEATMAP_FORMAT_REMEDY,
        "scatter" => "Remove axis_y.mirror.format; to format the measure instead, author it on style.axis_x.labels.format.",
        _ => "Remove axis_y.mirror.format; to format the measure instead, author it on style.axis_y.labels.format.",
    };
    let remedy = format!(
        "axis_y.mirror.format relabels the shared y-scale on the opposite edge, and that scale is categorical here \u{2014} it can't carry a number format. {move_it_to}"
    );
    let categorical = matches!(vl_type, VegaType::Nominal | VegaType::Ordinal);
    super::cartesian_x_errors::gate_label_format(
        Some(&slot),
        "axis_y.mirror.format",
        field,
        vl_type,
        rows,
        categorical.then_some(remedy.as_str()),
    )
}

/// The ghost axis sits on the edge opposite the primary. A label alignment
/// authored for that edge's own side grows its labels back over the plot: it
/// needs the ghost's labels measured, which an `expr`-painted, case-folded or
/// unformatted axis cannot do, nor one with neither a baked ladder nor a
/// measure field to estimate from (a histogram's count axis).
fn unmeasurable_align(facts: &Facts) -> Result<(), Diagnostic> {
    let axis_y = &facts.style.axis_y;
    let (Some(labels), Some(mirror)) = (&axis_y.labels, &axis_y.mirror) else { return Ok(()) };
    let opposite = if axis_y.position.as_deref() == Some("right") { "left" } else { "right" };
    if labels.align.as_deref() != Some(opposite) {
        return Ok(());
    }
    let set = |key: &str| mirror.get(key).is_some_and(|v| !v.is_null());
    let cased = labels.font.as_ref().and_then(|f| f.case.as_deref()).is_some_and(|c| matches!(c, "upper" | "lower"));
    // A heatmap's y is its nominal band axis: no numeric value to estimate from.
    let has_source = !axis_y.tick_values.is_empty()
        || (facts.chart_type != "heatmap" && facts.y.as_ref().is_some_and(Value::is_string))
        || facts.layers.iter().any(|l| l.y.as_ref().is_some_and(Value::is_string));
    let measurable = if set("expr") {
        false
    } else if set("format") {
        has_source
    } else {
        has_source
            && labels.expr.as_ref().is_none_or(Value::is_null)
            && !cased
            && labels.format.as_ref().is_some_and(|f| !f.is_null())
    };
    if measurable {
        return Ok(());
    }
    Err(Diagnostic::new(
        &codes::ERR_MIRROR_UNMEASURABLE_ALIGN,
        [("align", json!(opposite)), ("chart_id", json!(facts.chart_id())), ("opposite", json!(opposite))],
    ))
}

/// `facet.py::FacetFeature.apply`.
fn multiples_conflicts(facts: &Facts, payload: &Payload) -> Result<(), Diagnostic> {
    let Some(multiples) = &facts.multiples else { return Ok(()) };
    let chart_id = || json!(facts.chart_id());
    if facts.rail().is_some() {
        return Err(Diagnostic::new(&codes::ERR_MULTIPLES_ENDPOINT_LABELS, [("chart_id", chart_id())]));
    }
    if facts.support_table.as_ref().is_some_and(|t| !t.is_null()) {
        return Err(Diagnostic::new(&codes::ERR_MULTIPLES_SUPPORT_TABLE, [("chart_id", chart_id())]));
    }
    let partition: Vec<&str> = [multiples.rows.as_deref(), multiples.columns.as_deref()].into_iter().flatten().collect();
    for layer in &facts.layers {
        let Some(name) = layer.query_name.as_deref().filter(|n| Some(*n) != facts.query_name.as_deref()) else { continue };
        let Some(first) = payload.datasets.as_ref().and_then(|d| d.get(name)).and_then(|rows| rows.first()) else { continue };
        let offending: Vec<&str> = partition.iter().copied().filter(|f| first.contains_key(*f)).collect();
        if !offending.is_empty() {
            return Err(Diagnostic::new(
                &codes::ERR_MULTIPLES_LAYER_PARTITION,
                [("chart_id", chart_id()), ("fields", json!(offending.join(", ")))],
            ));
        }
    }
    Ok(())
}

/// `validation.py::_forms_gapless_integer_run`: whole numbers that cover every
/// integer between their min and max exactly once, the shape `GROUP BY` makes.
fn gapless_integer_run(values: &[Option<&Value>]) -> bool {
    if values.len() < 5 {
        return false;
    }
    let mut whole = std::collections::BTreeSet::new();
    for value in values {
        let Some(Value::Number(n)) = value else { return false };
        let Some(f) = n.as_f64().filter(|f| f.fract() == 0.0) else { return false };
        if !whole.insert(f as i64) {
            return false;
        }
    }
    whole.last().zip(whole.first()).is_some_and(|(max, min)| max - min + 1 == whole.len() as i64)
}

/// `bar.py::_emit_histogram` and `validation.py::validate_raw_rows_for_histogram`.
fn histogram(facts: &Facts, rows: &[Row]) -> Result<(), Diagnostic> {
    let non_numeric = |field: &str, vl_type: &str| {
        Diagnostic::new(
            &codes::ERR_HISTOGRAM_NON_NUMERIC,
            [("chart_id", json!(facts.id)), ("field", json!(field)), ("vl_type", json!(vl_type))],
        )
    };
    let Some(x) = facts.x.as_deref() else { return Err(non_numeric("<none>", "missing")) };
    let x_type = infer_vega_type(rows, x);
    if x_type != VegaType::Quantitative {
        return Err(non_numeric(x, x_type.name()));
    }
    let color = facts.color_field();
    let numeric_extra: Vec<&String> = rows[0]
        .keys()
        .filter(|column| column.as_str() != x && Some(column.as_str()) != color)
        .filter(|column| infer_vega_type(rows, column) == VegaType::Quantitative)
        .collect();
    let x_values: Vec<Option<&Value>> = rows.iter().map(|row| row.get(x)).collect();
    if numeric_extra.is_empty() || !gapless_integer_run(&x_values) {
        return Ok(());
    }
    let count_fields: Vec<String> = numeric_extra.iter().map(|c| py_repr(&json!(c))).collect();
    Err(Diagnostic::new(
        &codes::ERR_HISTOGRAM_PREAGGREGATED,
        [("chart_id", json!(facts.chart_id_or_unknown())), ("count_fields", json!(count_fields.join(", "))), ("field", json!(x))],
    ))
}

/// `value_labels.py::ValueLabelFeature.apply`: a `labels.field` must name a
/// column of the rows it labels, and a stacked or span bar has no
/// `middle_aligned` position.
fn value_labels(facts: &Facts, payload: &Payload) -> Result<(), Diagnostic> {
    let style = &facts.style;
    let chart_slots: Vec<&LabelSlot> = match facts.chart_type.as_str() {
        "bar" | "histogram" => vec![style.mark.as_ref()],
        "line" => vec![style.line_mark.as_ref(), style.point_mark.as_ref()],
        "area" => vec![style.line_mark.as_ref()],
        "scatter" => vec![style.point_mark.as_ref()],
        _ => return Ok(()),
    }
    .into_iter()
    .flatten()
    .filter_map(|m| m.labels.as_ref())
    .collect();
    let missing = |field: &str, source: String, rows: &[Row]| {
        let first = rows.first()?;
        (!first.contains_key(field)).then(|| {
            let mut available: Vec<&str> = first.keys().map(String::as_str).collect();
            available.sort_unstable();
            Diagnostic::new(
                &codes::ERR_LABELS_FIELD_NOT_FOUND,
                [
                    ("available", json!(available.join(", "))),
                    ("chart_id", json!(facts.id)),
                    ("field", json!(field)),
                    ("source", json!(source)),
                ],
            )
        })
    };
    for field in chart_slots.iter().filter_map(|slot| slot.field.as_deref()) {
        if let Some(d) = missing(field, "the chart".into(), &payload.rows) {
            return Err(d);
        }
    }
    for (index, layer) in facts.layers.iter().enumerate() {
        let rows = if layer.query_name == facts.query_name {
            Some(payload.rows.as_slice())
        } else {
            layer.query_name.as_deref().and_then(|n| payload.datasets.as_ref()?.get(n)).map(Vec::as_slice)
        };
        let source = format!("overlay layer {index} ({}, query={})", layer.kind, py_repr(&json!(layer.query_name)));
        for field in layer.label_slots().into_iter().filter_map(|slot| slot.field.as_deref()) {
            if let Some(d) = rows.and_then(|rows| missing(field, source.clone(), rows)) {
                return Err(d);
            }
        }
    }
    if facts.chart_type != "bar" || !facts.wide_measures.is_empty() || !facts.y.as_ref().is_some_and(Value::is_string) {
        return Ok(());
    }
    let Some(labels) = style.mark.as_ref().and_then(|m| m.labels.as_ref()).filter(|l| l.visible == Some(true)) else {
        return Ok(());
    };
    let position = labels.position.as_deref().unwrap_or("above");
    if position != "middle_aligned" {
        return Ok(());
    }
    if facts.y_start.is_some() {
        return Err(Diagnostic::new(&codes::ERR_SPAN_MIDDLE_ALIGNED_LABELS, []));
    }
    if !matches!(facts.stack.as_deref(), None | Some("none")) {
        return Err(Diagnostic::new(&codes::ERR_STACKED_MIDDLE_ALIGNED_LABELS, []));
    }
    Ok(())
}

/// `_overlay.py`: a layer's pinned y-axis side and a band-aware step curve
/// both need a vertical base, an area layer needs a vertical or unsplit base,
/// and a band-aware step is a single-series idiom.
fn layers(facts: &Facts, payload: &Payload) -> Result<(), Diagnostic> {
    let chart_id = || json!(facts.chart_id());
    let step_curve = |layer: &Layer| {
        let curve = match layer.kind.as_str() {
            "line" => layer.line_mark.as_ref(),
            "area" => layer.area_mark.as_ref(),
            _ => None,
        }
        .and_then(|m| m.curve.as_deref());
        curve == Some("step") && layer.y.as_ref().is_some_and(|y| !y.is_null())
    };
    for (index, layer) in facts.layers.iter().enumerate() {
        let Some(color) = layer.color.as_deref() else { continue };
        let own_rows = if layer.query_name.is_none() || layer.query_name == facts.query_name {
            Some(&payload.rows)
        } else {
            layer.query_name.as_deref().and_then(|name| payload.datasets.as_ref()?.get(name))
        };
        if own_rows.is_some_and(|rows| rows.iter().all(|row| row.get(color).map_or(true, Value::is_null))) {
            return Err(Diagnostic::new(
                &codes::ERR_LAYER_COLOR_ALL_NULL,
                [("chart_id", chart_id()), ("layer_index", json!(index)), ("color_field", json!(color))],
            ));
        }
    }
    if facts.orientation.as_deref() == Some("horizontal") {
        // The pinned side is read ahead of the per-layer step transform.
        let pinned = |l: &Layer| l.axis_y.as_ref().and_then(|a| a.position.as_deref()).is_some_and(|p| matches!(p, "left" | "right"));
        if facts.layers.iter().any(pinned) {
            return Err(Diagnostic::new(&codes::ERR_LAYER_AXIS_POSITION_ORIENTATION, [("chart_id", chart_id())]));
        }
        // Each layer's own branch raises in authored order; an area layer's split-base check precedes its step.
        let split_base = !facts.wide_measures.is_empty()
            || facts.color.as_deref().is_some_and(|c| matches!(infer_vega_type(&payload.rows, c), VegaType::Nominal | VegaType::Ordinal));
        for layer in &facts.layers {
            if layer.kind == "area" && split_base {
                return Err(Diagnostic::new(&codes::ERR_LAYER_AREA_SPLIT_HORIZONTAL_BASE, [("chart_id", chart_id())]));
            }
            if step_curve(layer) {
                return Err(Diagnostic::new(&codes::ERR_LAYER_STEP_ORIENTATION, [("chart_id", chart_id())]));
            }
        }
        return Ok(());
    }
    layer_x(facts, payload)?;
    // `apply_step_band` rejects a data-driven color field; it only runs on a band x.
    let colored_step = |l: &Layer| step_curve(l) && l.color.is_some();
    if facts.layers.iter().any(colored_step) && super::cartesian_x_errors::x_is_band(&payload.resolved, &payload.rows)? {
        return Err(Diagnostic::new(&codes::ERR_STEP_BAND_MULTI_SERIES, [("chart_id", chart_id())]));
    }
    Ok(())
}

/// `_overlay.py::_resolve_layer_x_encoding`: a layer authoring its own x column
/// shares the base's x scale, so its type must be one that scale takes.
fn layer_x(facts: &Facts, payload: &Payload) -> Result<(), Diagnostic> {
    let Some(base_field) = facts.x.as_deref().filter(|_| facts.layers.iter().any(|l| l.x.is_some())) else { return Ok(()) };
    let Some(base_type) = super::cartesian_x_errors::base_x_type(&payload.resolved, &payload.rows)? else { return Ok(()) };
    let base_rows = super::temporal::normalize_labeled_temporal(&payload.rows, base_field)?;
    let mut base_values = base_rows.iter().filter_map(|row| row.get(base_field)).filter(|v| !v.is_null()).peekable();
    // Only a date-like base takes a temporal layer.
    let base_date_shaped =
        base_values.peek().is_some() && base_values.all(|v| v.as_str().is_some_and(super::temporal::is_date_like_string));
    for layer in facts.layers.iter().filter(|l| l.y.as_ref().is_some_and(|y| !y.is_null())) {
        let Some(x_field) = layer.x.as_deref() else { continue };
        let own_rows;
        let rows: &[Row] = if layer.query_name == facts.query_name {
            &payload.rows
        } else {
            let dataset = super::layers::resolve_layer_rows(payload, layer.query_name.as_deref(), facts.query_name.as_deref(), &facts.chart_type)?;
            own_rows = super::temporal::normalize_labeled_temporal(dataset, x_field)?;
            &own_rows
        };
        let layer_type = infer_vega_type(rows, x_field);
        let code = if base_type == VegaType::Quantitative && rows.first().is_some_and(|row| row.contains_key(x_field)) && layer_type != VegaType::Quantitative {
            &codes::ERR_LAYER_X_NOT_QUANTITATIVE
        } else if !matches!(base_type, VegaType::Nominal | VegaType::Ordinal)
            || matches!(layer_type, VegaType::Nominal | VegaType::Ordinal)
            || (layer_type == VegaType::Temporal && base_date_shaped)
        {
            continue;
        } else {
            &codes::ERR_LAYER_X_DOMAIN_MISMATCH
        };
        return Err(Diagnostic::new(
            code,
            [
                ("base_field", json!(base_field)),
                ("base_type", json!(base_type.name())),
                ("chart_id", json!(facts.chart_id())),
                ("layer_type", json!(layer_type.name())),
                ("x_field", json!(x_field)),
            ],
        ));
    }
    Ok(())
}

/// `vega_lite.py::_stamp_legend_align_sentinel`: a legend slid along its edge
/// needs one plot to slide along; small multiples and endpoint-label rails
/// compose several.
fn legend_align(facts: &Facts) -> Result<(), Diagnostic> {
    let Some(position) = facts.legend.position.as_ref() else { return Ok(()) };
    let slides = match position.align.as_deref() {
        Some("center") => true,
        Some("end") => !position.overlay,
        _ => false,
    };
    let emits_legend = facts.resolved_channels.values().any(|c| c.mode != "literal") || !facts.wide_measures.is_empty();
    if !(facts.legend.visible && slides && emits_legend) || (facts.multiples.is_none() && facts.rail().is_none()) {
        return Ok(());
    }
    Err(Diagnostic::new(
        &codes::ERR_LEGEND_ALIGN_UNSUPPORTED,
        [
            ("chart_id", json!(facts.chart_id())),
            ("reason", json!("small multiples and endpoint-label rails have no single plot to align in")),
        ],
    ))
}

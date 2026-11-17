//! What a chart draws when it has nothing to plot: zero rows, or rows whose plotted measure is
//! NULL throughout. The card keeps its canvas, title and subtitle; the plot box holds one quiet
//! "No results" centered in the legend label face (the same face in every family), and under it
//! the board filters that reach the chart, in the subtitle's face. No axes, ticks, gridlines or
//! legends: without a domain they say nothing. `docs/laws-empty.md`.

use serde::Deserialize;
use serde_json::Value;

use crate::common::{de_nonempty_string, Font, TitleStyle};
use crate::contract::stamp_view_root;
use crate::frame::{ChartFrame, FrameTitle};
use crate::Payload;
use diagnostic::Diagnostic;
use measure::FontBook;
use scene::{Attrs, Node, Role};

/// Between the headline and the context line under it.
const LINE_GAP: f64 = 6.0;

/// The one wording of an empty result.
pub(crate) fn headline() -> Result<String, Diagnostic> {
    Ok(display_text::language::tr("chart.no_results", None, &[])?)
}

/// The filters that reach this chart and are set, as `Label: value`, joined by ` · `. `None` when
/// no filter is active.
pub(crate) fn context_line(payload: &Payload) -> Option<String> {
    if payload.empty_filters.is_empty() {
        return None;
    }
    let filters: Vec<String> = payload.empty_filters.iter().map(|(label, value)| format!("{label}: {value}")).collect();
    Some(filters.join(" \u{b7} "))
}

/// A KPI's line under its dash: the headline, then the filters.
pub(crate) fn kpi_line(payload: &Payload) -> Result<String, Diagnostic> {
    let headline = headline()?;
    Ok(match context_line(payload) {
        Some(filters) => format!("{headline} \u{b7} {filters}"),
        None => headline,
    })
}

fn has_value(rows: &[serde_json::Map<String, Value>], columns: &[String]) -> bool {
    rows.iter().any(|row| columns.iter().any(|column| row.get(column).is_some_and(|cell| !cell.is_null())))
}

fn names(field: Option<&Value>) -> Vec<String> {
    match field {
        Some(Value::String(name)) => vec![name.clone()],
        Some(Value::Array(items)) => items.iter().filter_map(Value::as_str).map(str::to_string).collect(),
        _ => Vec::new(),
    }
}

/// The columns a family plots: what a value in any of them puts a mark on the card.
fn measure_columns(resolved: &Value, chart_type: &str) -> Vec<String> {
    let wide = names(resolved.get("wide_measures"));
    match chart_type {
        "bar" | "line" | "area" | "scatter" if !wide.is_empty() => wide,
        "bar" | "line" | "area" | "scatter" => names(resolved.get("y")),
        "heatmap" => names(resolved.pointer("/resolved_channels/color/data_field")),
        "histogram" => names(resolved.get("x")),
        "pie" => names(resolved.get("theta")),
        "point_map" => names(resolved.get("latitude")),
        _ => Vec::new(),
    }
}

/// A chart with no row, or no value in its plotted column (a layer's included), has nothing to
/// draw. A plotted column the rows lack is not "nothing": it is an authoring error the family
/// refuses by name, so it never reaches the quiet card.
pub(crate) fn nothing_to_plot(payload: &Payload, chart_type: &str) -> Result<bool, Diagnostic> {
    if payload.is_empty_result() {
        return Ok(true);
    }
    let columns = measure_columns(&payload.resolved, chart_type);
    if columns.is_empty() || has_value(&payload.rows, &columns) {
        return Ok(false);
    }
    if columns.iter().any(|column| !payload.rows.is_empty() && payload.rows.iter().all(|row| !row.contains_key(column))) {
        return Ok(false);
    }
    let base_query = payload.resolved.get("query_name").and_then(Value::as_str);
    for layer in payload.resolved.get("layers").and_then(Value::as_array).into_iter().flatten() {
        let layer_query = layer.get("query_name").and_then(Value::as_str);
        let rows = super::layers::resolve_layer_rows(payload, layer_query, base_query, chart_type)?;
        if has_value(rows, &names(layer.get("y"))) {
            return Ok(false);
        }
    }
    Ok(true)
}

#[derive(Deserialize)]
struct Resolved {
    id: String,
    #[serde(default, deserialize_with = "de_nonempty_string")]
    title: Option<String>,
    #[serde(default, deserialize_with = "de_nonempty_string")]
    subtitle: Option<String>,
    title_style: Option<TitleStyle>,
    background: String,
    style: Style,
    /// Every family carries the board's legend style; its label face sets the headline, so an
    /// empty pie reads the same as an empty bar.
    legend: Legend,
}

#[derive(Deserialize)]
struct Style {
    #[serde(default)]
    title_font: Option<Font>,
}

#[derive(Deserialize)]
struct Legend {
    label: Label,
}

#[derive(Deserialize)]
struct Label {
    font: Font,
}

fn painted<'a>(family: &str, what: &str, font: &'a Font) -> Result<(&'a str, f64, &'a str), String> {
    let name = font.family.as_deref().ok_or_else(|| format!("{family}: {what}.family absent (theme required)"))?;
    let size = font.size.ok_or_else(|| format!("{family}: {what}.size absent (theme required)"))?;
    let color = font.color.as_deref().ok_or_else(|| format!("{family}: {what}.color absent (theme required)"))?;
    Ok((name, size, color))
}

fn text_node(text: &str, font: &Font, (family, size, color): (&str, f64, &str), center: (f64, f64), key: &'static str) -> Node {
    let attrs = Attrs::new()
        .vega("font-family", family)
        .num("font-size", size)
        .quick_some("font-style", font.style.as_deref())
        .set("font-weight", font.weight_str())
        .set("fill", color)
        .set("text-anchor", "middle")
        .set("data-authored-kind", key);
    Node::text(Role::Label, (center.0, center.1 + measure::baseline_shift("middle", size)), attrs, text)
}

/// The card of a chart with nothing to plot, printed and stamped as the view root.
pub(crate) fn render(chart_type: &str, payload: &Payload, fonts: &FontBook) -> Result<String, Diagnostic> {
    let r: Resolved = Deserialize::deserialize(&payload.resolved).map_err(|e| format!("{chart_type} payload: {e}"))?;
    let frame = ChartFrame::of(payload, &r.background);
    let title = FrameTitle::new(r.title.as_deref(), r.subtitle.as_deref(), r.style.title_font.as_ref(), r.title_style.as_ref(), frame.avail_w(), fonts)?;
    title.warn_truncated(&r.id);
    let m = frame.fit(&title, |_, _, _| {});
    let (left, top) = frame.origin(&m);
    let center = (left + m.plot_w / 2.0, top + m.plot_h / 2.0);

    let face = &r.legend.label.font;
    let ink = painted(chart_type, "legend.label.font", face)?;
    let mut body = title.nodes(m.title_pos, (left, top));
    body.push(text_node(&headline()?, face, ink, center, "no_results"));
    if let Some(line) = context_line(payload) {
        let subtitle = r.title_style.as_ref().and_then(|t| t.subtitle.as_ref()).map(|s| &s.font);
        let sub_face = subtitle.ok_or_else(|| format!("{chart_type}: title_style.subtitle absent (theme required)"))?;
        let sub_ink = painted(chart_type, "title_style.subtitle.font", sub_face)?;
        let line = crate::common::truncate_label(&line, m.plot_w, sub_ink.0, sub_ink.1, fonts, false)?;
        let at = (center.0, center.1 + ink.1 / 2.0 + LINE_GAP + sub_ink.1 / 2.0);
        body.push(text_node(&line, sub_face, sub_ink, at, "no_results_filters"));
    }
    Ok(stamp_view_root(print::print(&[frame.svg(body)]))?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn layered(layer_query: &str, layer_rows: Value) -> Payload {
        let mut payload: Payload = serde_json::from_str(include_str!("../../../fixtures/empty/bar_titled.json")).expect("fixture");
        payload.rows = serde_json::from_value::<Vec<serde_json::Map<String, Value>>>(json!([{"category": "a", "amount": null}])).unwrap().into();
        payload.resolved["layers"] = json!([{"query_name": layer_query, "y": "amount"}]);
        payload.datasets = Some([("other".to_string(), serde_json::from_value::<Vec<_>>(layer_rows).unwrap().into())].into());
        payload
    }

    /// A layer with values keeps the chart drawing even when the base's measure is all NULL.
    #[test]
    fn a_layer_with_values_is_something_to_plot() {
        let payload = layered("other", json!([{"category": "a", "amount": 3.0}]));
        assert!(!nothing_to_plot(&payload, "bar").unwrap());
        let nulls = layered("other", json!([{"category": "a", "amount": null}]));
        assert!(nothing_to_plot(&nulls, "bar").unwrap());
    }

    /// A layer reading a dataset the payload lacks is refused, not read as the base's rows.
    #[test]
    fn a_layer_over_a_missing_dataset_is_refused() {
        let payload = layered("gone", json!([]));
        let err = nothing_to_plot(&payload, "bar").unwrap_err().to_string();
        assert!(err.contains("gone"), "{err}");
    }
}

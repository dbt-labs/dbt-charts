//! Binds every `*.json` case under the directory argument and exits non-zero on
//! any disagreement with Python, or when no case ran; run by `bind_sweep.py`. A case
//! is `{board, datasets, expected, charts}`: `expected` is `{"scales": [...]}` or
//! `{"error": {"code": ..., "params": {...}}}` (the category color plan), and `charts`
//! holds what Python decides per placed chart (channels, channel types, axis formats).

use bind::axis::{AxisLadder, AxisName};
use bind::channel_type::{classify, ChannelType};
use bind::channels::{Channels, Mode};
use bind::formats::AuthoredFormat;
use bind::{bind_board, identity, BoundBoard};
use diagnostic::Diagnostic;
use column_types::QueryDatasets;
use board_types::{Board, BoardChartStylesValue, BoardChartTiers, LayoutItemChart};
use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;

#[derive(Deserialize)]
struct Case {
    board: Board,
    datasets: QueryDatasets,
    expected: Expected,
    charts: IndexMap<String, ChartOracle>,
}

/// What Python decides for one placed chart.
#[derive(Deserialize)]
struct ChartOracle {
    channels: ChannelsOracle,
    types: IndexMap<String, TypeOracle>,
    axes: Vec<AxisOracle>,
    probes: Vec<ProbeOracle>,
}

/// The cascade with formats planted in known layers: `sets` are `[layer, slot, spec]`.
#[derive(Deserialize)]
struct ProbeOracle {
    axis: String,
    channel: String,
    column: Option<String>,
    temporal_band: bool,
    sets: Vec<(String, String, String)>,
    format: Option<BTreeMap<String, Value>>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum ChannelsOracle {
    Bound(IndexMap<String, ChannelOracle>),
    Refused(String),
}

#[derive(Deserialize, Debug)]
struct ChannelOracle {
    mode: String,
    data_field: String,
    quantitative_data: bool,
    categorical: bool,
}

#[derive(Deserialize)]
struct TypeOracle {
    field: String,
    dimension: bool,
    #[serde(rename = "type")]
    type_: String,
}

#[derive(Deserialize)]
struct AxisOracle {
    axis: String,
    channel: String,
    column: Option<String>,
    temporal_band: bool,
    format: Option<BTreeMap<String, Value>>,
    visible: Option<bool>,
    band_position: Option<f64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
enum Expected {
    Scales(Vec<Scale>),
    Error(ErrorWant),
}

#[derive(Deserialize)]
struct ErrorWant {
    code: String,
    params: BTreeMap<String, Value>,
}

#[derive(Deserialize, PartialEq, Debug)]
struct Scale {
    field: String,
    slots: IndexMap<String, usize>,
    overrides: IndexMap<String, String>,
    unseen_pins: IndexMap<String, String>,
}

/// Every chart the board tree places, with the board whose style it cascades from.
fn placed<'a>(board: &'a Board, out: &mut Vec<(&'a Board, &'a LayoutItemChart)>) {
    for item in &board.layout.items {
        if let Some(chart) = &item.chart {
            out.push((board, chart));
        }
        if let Some(nested) = &item.board {
            placed(nested, out);
        }
    }
}

fn channel_type(name: &str) -> ChannelType {
    match name {
        "quantitative" => ChannelType::Quantitative,
        "nominal" => ChannelType::Nominal,
        "temporal" => ChannelType::Temporal,
        other => panic!("unknown channel type {other}"),
    }
}

/// An authored format as its set fields, the way Python dumps it.
fn format_wire(format: Option<AuthoredFormat>) -> Option<BTreeMap<String, Value>> {
    let format = format?;
    let mut wire = BTreeMap::new();
    let mut put = |key: &str, value: Option<String>| {
        if let Some(value) = value {
            wire.insert(key.to_string(), Value::String(value));
        }
    };
    put("spec", format.spec);
    put("prefix", format.prefix);
    put("suffix", format.suffix);
    put("notation", format.notation.map(|v| v.to_string()));
    put("sign_placement", format.sign_placement.map(|v| v.to_string()));
    put("repeat", format.repeat.map(|v| v.to_string()));
    Some(wire)
}

#[derive(Default)]
struct Tally {
    channels: usize,
    refused: usize,
    /// Gradient channels among the equal ones.
    gradients: usize,
    types: usize,
    type_refusals: usize,
    axes: usize,
    probes: usize,
}

/// Why a channel refused, as both sides spell it: a code (hyphens or underscores) or the
/// ValueError's wording.
fn refusal_kind(message: &str) -> Option<&'static str> {
    const KINDS: [&str; 6] = [
        "ERR_MULTI_Y_LAYERS_CONFLICT",
        "ERR_MULTI_Y_COLOR_CONFLICT",
        "ERR_WIDE_MEASURE_NAME_CONTAINS_SEPARATOR",
        "LOOKS LIKE A LITERAL COLOR",
        "NOT FOUND",
        "REQUIRES CHART.COLOR",
    ];
    let message = message.to_uppercase().replace('-', "_");
    KINDS.into_iter().find(|kind| message.contains(&kind.replace('-', "_")))
}

fn compare_channels(want: &ChannelsOracle, got: Option<&Result<Channels, Diagnostic>>, tally: &mut Tally) -> Result<(), String> {
    match (want, got) {
        (ChannelsOracle::Refused(why), Some(Err(d))) => {
            let (theirs, mine) = (refusal_kind(why), refusal_kind(&d.to_string()));
            if theirs.is_none() || theirs != mine {
                return Err(format!("python refused ({why}), rust refused differently: {d}"));
            }
            tally.refused += 1;
            Ok(())
        }
        (ChannelsOracle::Refused(why), Some(Ok(_))) => Err(format!("python refused ({why}), rust bound")),
        (ChannelsOracle::Refused(_), None) => Ok(()),
        (ChannelsOracle::Bound(_), Some(Err(d))) => Err(format!("python bound, rust refused: {d}")),
        (ChannelsOracle::Bound(want), got) => {
            tally.channels += 1;
            tally.gradients += want.values().filter(|c| c.mode == "gradient").count();
            let got = match got {
                Some(Ok(channels)) => channels,
                _ if want.is_empty() => return Ok(()),
                _ => return Err("python bound channels, rust has no dataset".into()),
            };
            for (key, mine) in [("color", &got.color), ("background", &got.background)] {
                let theirs = want.get(key);
                let same = match (mine, theirs) {
                    (None, None) => true,
                    (Some(m), Some(t)) => {
                        let mode = match m.mode {
                            Mode::Series => "series",
                            Mode::Gradient(_) => "gradient",
                        };
                        (&m.data_field, mode, m.quantitative_data, m.categorical)
                            == (&t.data_field, t.mode.as_str(), t.quantitative_data, t.categorical)
                    }
                    _ => false,
                };
                if !same {
                    return Err(format!("{key}: python {theirs:?}, rust {mine:?}"));
                }
            }
            Ok(())
        }
    }
}

fn compare_chart(
    id: &str,
    board: &Board,
    chart: &LayoutItemChart,
    want: &ChartOracle,
    data: &QueryDatasets,
    bound: &BoundBoard,
    tally: &mut Tally,
) -> Result<(), String> {
    compare_channels(&want.channels, bound.channels.get(id), tally).map_err(|e| format!("{id} channels: {e}"))?;
    let dataset = identity(chart).1.and_then(|query| data.get(query));
    for (name, t) in &want.types {
        let dataset = dataset.ok_or_else(|| format!("{id} {name}: no dataset"))?;
        match classify(&dataset.schema, &dataset.rows, Some(&t.field), t.dimension) {
            Ok(got) if got == channel_type(&t.type_) => tally.types += 1,
            Ok(got) => return Err(format!("{id} {name} ({}): python {}, rust {got:?}", t.field, t.type_)),
            Err(_) => tally.type_refusals += 1,
        }
    }
    if want.axes.is_empty() {
        return Ok(());
    }
    let style = board.chart_styles.get(id).ok_or_else(|| format!("{id}: no chart style"))?;
    for a in &want.axes {
        let axis = if a.axis == "axis_x" { AxisName::X } else { AxisName::Y };
        let column = a.column.as_deref().map(AuthoredFormat::named);
        let ladder = AxisLadder::new(axis, channel_type(&a.channel), style, &board.chart_tiers, column.as_ref(), a.temporal_band)
            .map_err(|e| format!("{id} {}/{}: {e}", a.axis, a.channel))?;
        let got = (format_wire(ladder.labels_format()), ladder.labels_visible(), ladder.band_position());
        if got != (a.format.clone(), a.visible, a.band_position) {
            return Err(format!(
                "{id} {} {} column={:?} temporal_band={}: python {:?}, rust {got:?}",
                a.axis, a.channel, a.column, a.temporal_band, (&a.format, a.visible, a.band_position)
            ));
        }
        tally.axes += 1;
    }
    for probe in &want.probes {
        compare_probe(id, board, style, probe)?;
        tally.probes += 1;
    }
    Ok(())
}

/// Plants each probe's formats in the chart's style and the board's tiers, as the
/// seam would carry them, and reads the axis format back.
fn compare_probe(id: &str, board: &Board, style: &BoardChartStylesValue, probe: &ProbeOracle) -> Result<(), String> {
    let mut style_json = serde_json::to_value(style).expect("style serializes");
    let mut tiers_json = serde_json::to_value(&board.chart_tiers).expect("tiers serialize");
    for (layer, slot, spec) in &probe.sets {
        // A family carries only the slots it can paint (a heatmap has no `axis_quantitative`).
        if layer == "family" && slot == "axis_quantitative" && style_json["type"] == "heatmap" {
            continue;
        }
        let target = match layer.as_str() {
            "chart" => &mut style_json[slot.as_str()],
            "family" => &mut style_json["family"][slot.as_str()],
            "board" => &mut tiers_json[slot.as_str()],
            other => panic!("unknown layer {other}"),
        };
        if target.is_null() {
            *target = serde_json::json!({});
        }
        if target["labels"].is_null() {
            target["labels"] = serde_json::json!({});
        }
        target["labels"]["format"] = Value::String(spec.clone());
    }
    let style: BoardChartStylesValue = serde_json::from_value(style_json).map_err(|e| format!("{id} probe style: {e}"))?;
    let tiers: BoardChartTiers = serde_json::from_value(tiers_json).map_err(|e| format!("{id} probe tiers: {e}"))?;
    let axis = if probe.axis == "axis_x" { AxisName::X } else { AxisName::Y };
    let column = probe.column.as_deref().map(AuthoredFormat::named);
    let ladder = AxisLadder::new(axis, channel_type(&probe.channel), &style, &tiers, column.as_ref(), probe.temporal_band)
        .map_err(|e| format!("{id} probe: {e}"))?;
    let got = format_wire(ladder.labels_format());
    if got != probe.format {
        return Err(format!(
            "{id} {} {} column={:?} temporal_band={} sets={:?}: python {:?}, rust {got:?}",
            probe.axis, probe.channel, probe.column, probe.temporal_band, probe.sets, probe.format
        ));
    }
    Ok(())
}

fn ordered<V: Clone>(map: &IndexMap<String, V>) -> Vec<(String, V)> {
    map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
}

fn main() {
    std::thread::Builder::new()
        .stack_size(512 << 20)
        .spawn(run)
        .expect("spawn")
        .join()
        .expect("worker");
}

fn run() {
    let dir = std::env::args().nth(1).expect("usage: check_plan <dir>");
    let mut paths: Vec<_> = std::fs::read_dir(&dir)
        .expect("read cases dir")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    let (mut failed, mut bound, mut errored, mut skipped) = (0, 0, 0, 0);
    let mut tally = Tally::default();
    for path in &paths {
        let text = std::fs::read_to_string(path).expect("read case");
        let case: Case = match serde_json::from_str(&text) {
            Ok(case) => case,
            Err(e) => {
                failed += 1;
                eprintln!("{}: {e}", path.display());
                continue;
            }
        };
        let got = bind_board(&case.board, &case.datasets);
        if let Ok(bound) = &got {
            let mut charts = Vec::new();
            placed(&case.board, &mut charts);
            for (board, chart) in charts {
                let id = identity(chart).0;
                let Some(want) = case.charts.get(id) else { continue };
                if let Err(why) = compare_chart(id, board, chart, want, &case.datasets, bound, &mut tally) {
                    failed += 1;
                    eprintln!("{}: {why}", path.display());
                }
            }
        }
        let verdict = match (case.expected, got) {
            (Expected::Scales(want), Ok(got)) => {
                bound += usize::from(!want.is_empty());
                skipped += usize::from(want.is_empty());
                let got: Vec<_> = got
                    .category_colors
                    .iter()
                    .map(|s| (&s.field, ordered(&s.slots), ordered(&s.overrides), ordered(&s.unseen_pins)))
                    .collect();
                let want: Vec<_> = want
                    .iter()
                    .map(|s| (&s.field, ordered(&s.slots), ordered(&s.overrides), ordered(&s.unseen_pins)))
                    .collect();
                (got == want).then_some(()).ok_or(format!("want {want:?}, got {got:?}"))
            }
            (Expected::Error(want), Err(d)) => {
                errored += 1;
                let params: BTreeMap<String, Value> =
                    d.params.iter().map(|(k, v)| (k.to_string(), v.clone())).collect();
                (d.code == want.code && params == want.params).then_some(()).ok_or(format!(
                    "want error {} {:?}, got {} {:?}",
                    want.code, want.params, d.code, params
                ))
            }
            (Expected::Scales(_), Err(d)) => Err(format!("want scales, got error {d}")),
            (Expected::Error(want), Ok(_)) => Err(format!("want error {}, got a plan", want.code)),
        };
        if let Err(why) = verdict {
            failed += 1;
            eprintln!("{}: {why}", path.display());
        }
    }
    println!(
        "{} cases: {bound} plan, {errored} error, {skipped} skip, {failed} failed",
        paths.len()
    );
    println!(
        "charts: {} channel sets equal ({} gradient, {} refused by both); \
         {} channel types equal ({} refused by name); {} axis decisions equal; {} planted-format probes equal",
        tally.channels, tally.gradients, tally.refused, tally.types, tally.type_refusals, tally.axes, tally.probes
    );
    std::process::exit(i32::from(failed > 0 || paths.is_empty()));
}

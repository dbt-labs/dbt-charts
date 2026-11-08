//! Replays every `*.json` case under the directory argument and exits non-zero on any
//! disagreement with Python, or when no case ran; run by `bind_sweep.py`. A case is
//! `{board, datasets, plan, charts}`: `board` names the corpus board, `plan` holds the category
//! color plan Python makes and what it plans from (pins, palette, each chart's color field), and
//! `charts` holds, per placed chart and for a cartesian family, the channel type Python gives its
//! `x`/`y` fields over the datasets and its recorded style walks (`chart_svg_dev/_bind_walks.py`,
//! replayed by `tests/oracle/walks.rs`). Each chart's bound channels, also recorded, are compared
//! by engine's `check_channels` example, over the `Board` Rust normalize compiles.

use bind::category_colors::{CategoryColorScale, Pins};
use bind::channel_type::{classify, ChannelType};
use bind::{seat, Encoding};
use column_types::QueryDatasets;
use indexmap::IndexMap;
use serde::Deserialize;
use serde_json::Value;
use std::collections::BTreeMap;

// Shared with the unit tests, which use more of it.
#[allow(dead_code)]
#[path = "../tests/oracle/walks.rs"]
mod walks;

#[derive(Deserialize)]
struct Case {
    datasets: QueryDatasets,
    plan: Plan,
    charts: IndexMap<String, ChartOracle>,
}

/// The category color plan Python makes, and the inputs it makes it from.
#[derive(Deserialize)]
struct Plan {
    pins: Pins,
    palette: Vec<String>,
    encodings: Vec<EncodingWant>,
    expected: Expected,
}

#[derive(Deserialize)]
struct EncodingWant {
    query: String,
    field: String,
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

fn ordered<V: Clone>(map: &IndexMap<String, V>) -> Vec<(String, V)> {
    map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
}

type Seated<'a> = Vec<(
    &'a String,
    Vec<(String, usize)>,
    Vec<(String, String)>,
    Vec<(String, String)>,
)>;

fn rows_of(scales: &[Scale]) -> Seated<'_> {
    scales
        .iter()
        .map(|s| {
            (
                &s.field,
                ordered(&s.slots),
                ordered(&s.overrides),
                ordered(&s.unseen_pins),
            )
        })
        .collect()
}

/// Seats the plan's encodings and compares with what Python planned.
fn check_plan(case: &Case) -> Result<bool, String> {
    let plan = &case.plan;
    let encodings: Vec<Encoding> = plan
        .encodings
        .iter()
        .filter_map(|e| {
            Some(Encoding {
                dataset: case.datasets.get(&e.query)?,
                field: &e.field,
            })
        })
        .collect();
    let got = seat(&encodings, &plan.pins, &plan.palette);
    match (&plan.expected, got) {
        (Expected::Scales(want), Ok(got)) => {
            let got: Vec<Scale> = got.iter().map(scale).collect();
            if rows_of(want) != rows_of(&got) {
                return Err(format!("want {:?}, got {:?}", rows_of(want), rows_of(&got)));
            }
            Ok(!want.is_empty())
        }
        (Expected::Error(want), Err(d)) => {
            let params: BTreeMap<String, Value> = d
                .params
                .iter()
                .map(|(k, v)| (k.to_string(), v.clone()))
                .collect();
            if d.code == want.code && params == want.params {
                return Ok(false);
            }
            Err(format!(
                "want error {} {:?}, got {} {:?}",
                want.code, want.params, d.code, params
            ))
        }
        (Expected::Scales(_), Err(d)) => Err(format!("want scales, got error {d}")),
        (Expected::Error(want), Ok(_)) => Err(format!("want error {}, got a plan", want.code)),
    }
}

fn scale(s: &CategoryColorScale) -> Scale {
    Scale {
        field: s.field.clone(),
        slots: s.slots.clone(),
        overrides: s.overrides.clone(),
        unseen_pins: s.unseen_pins.clone(),
    }
}

/// What Python decides for one placed chart.
#[derive(Deserialize)]
struct ChartOracle {
    query: Option<String>,
    #[serde(default)]
    types: IndexMap<String, TypeOracle>,
    walk: Option<walks::Case>,
}

#[derive(Deserialize)]
struct TypeOracle {
    field: String,
    dimension: bool,
    #[serde(rename = "type")]
    type_: String,
}

fn channel_type(name: &str) -> ChannelType {
    match name {
        "quantitative" => ChannelType::Quantitative,
        "nominal" => ChannelType::Nominal,
        "temporal" => ChannelType::Temporal,
        other => panic!("unknown channel type {other}"),
    }
}

fn main() {
    let dir = std::env::args().nth(1).expect("usage: check_plan <dir>");
    let mut paths: Vec<_> = std::fs::read_dir(&dir)
        .expect("read cases dir")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    let (mut failed, mut types, mut type_refusals, mut plans) = (0, 0, 0, 0);
    let mut tally = walks::Tally::default();
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
        match check_plan(&case) {
            Ok(true) => plans += 1,
            Ok(false) => {}
            Err(why) => {
                failed += 1;
                eprintln!("{}: {why}", path.display());
            }
        }
        for (id, want) in &case.charts {
            let dataset = want
                .query
                .as_deref()
                .and_then(|query| case.datasets.get(query));
            for (name, t) in &want.types {
                let Some(dataset) = dataset else {
                    failed += 1;
                    eprintln!("{}: {id} {name}: no dataset", path.display());
                    continue;
                };
                match classify(&dataset.schema, &dataset.rows, Some(&t.field), t.dimension) {
                    Ok(got) if got == channel_type(&t.type_) => types += 1,
                    Ok(got) => {
                        failed += 1;
                        eprintln!(
                            "{}: {id} {name} ({}): python {}, rust {got:?}",
                            path.display(),
                            t.field,
                            t.type_
                        );
                    }
                    Err(_) => type_refusals += 1,
                }
            }
            if let Some(Err(why)) = want.walk.as_ref().map(|walk| walks::check(walk, &mut tally)) {
                failed += 1;
                eprintln!("{}: {id}: {why}", path.display());
            }
        }
    }
    println!("{} cases: {plans} plan, {failed} failed", paths.len());
    println!(
        "charts: {types} channel types equal ({type_refusals} refused by name); {} axis decisions equal; \
         {} planted-format probes equal; {} planted legend/title probes equal; {} layer-mark probes equal",
        tally.axes, tally.axis_probes, tally.leaf_probes, tally.mark_probes
    );
    std::process::exit(i32::from(failed > 0 || paths.is_empty()));
}

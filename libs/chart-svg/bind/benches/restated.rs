//! The restated-key check over one bar chart's axes, with the tiers and ladders it shares with the
//! family bind built per call: a chart that authors no axis key (the common case, gated out before
//! any walk) and one that authors eight leaves.
//! `cargo bench -p bind --bench restated`

use criterion::{criterion_group, criterion_main, Criterion};
use serde_json::json;
use std::hint::black_box;

use bind::channel_type::ChannelType;
use bind::style::axis::{AxisLadder, AxisName};
use bind::style::Tiers;
use board_model::style::style::Style;
use model_core::Patch;

#[path = "../tests/it/support.rs"]
#[allow(dead_code)]
mod support;

fn own(axes: serde_json::Value) -> Patch<Style> {
    let text = json!({"charts": {"bar": axes}}).to_string();
    model_core::parse_patch::<Style>(&yaml::parse_node(&text).expect("a node"), None).twin
}

fn bench(c: &mut Criterion) {
    let family = *board_model::fixtures::resolved_style("clarity").expect("clarity");
    let chart = support::bar(&family.charts, support::fields(&["y"], None), Vec::new());
    let none = own(json!({}));
    let eight = own(json!({
        "axis_y": {"labels": {"visible": true}, "grid": {"visible": true}, "ticks": {"count": 5}},
        "axis_x": {"labels": {"visible": true}, "band_position": 0.5},
        "axis": {"labels": {"format": "number"}, "grid": {"visible": true}},
    }));
    for (name, own) in [("no_own_axis_keys", &none), ("eight_own_leaves", &eight)] {
        c.bench_function(&format!("restated/{name}"), |b| {
            b.iter(|| {
                let tiers = Tiers::of(black_box(&chart), own, &family.charts).expect("tiers");
                let x = AxisLadder::new(AxisName::X, ChannelType::Nominal, &tiers, None, false).expect("x");
                let y = AxisLadder::new(AxisName::Y, ChannelType::Quantitative, &tiers, None, false).expect("y");
                bind::restated::axes(&tiers, [&x, &y], "c")
            })
        });
    }
}

criterion_group!(benches, bench);
criterion_main!(benches);

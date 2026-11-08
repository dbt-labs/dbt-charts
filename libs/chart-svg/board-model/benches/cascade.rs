//! Resolving a nested style over its parent: `Resolved::apply` against folding and recomputing the
//! whole tree. The tree is the test-only generated `Style` root (`tests/it/inherit`).
//! `cargo bench -p board-model --bench cascade`

use criterion::{criterion_group, criterion_main, Criterion};
use model_core::Resolved;
use serde_json::{json, Value};
use std::hint::black_box;

use board_model::cascade as generated;
#[path = "../tests/it/inherit/root.rs"]
mod root;

/// What `tests/it/inherit/mod.rs` provides to the shared test helpers.
pub(crate) fn patch(
    v: &Value,
    refused: &mut std::collections::BTreeSet<String>,
) -> Result<Box<StylePatch>, String> {
    let _ = refused;
    let node = yaml::parse_node(&v.to_string()).map_err(|e| format!("{e:?}"))?;
    Ok(Box::new(model_core::parse_patch::<Style>(&node, None).twin))
}
use root::{Style, StylePatch};

#[path = "../tests/it/inherit/incremental.rs"]
#[allow(dead_code)]
mod incremental;
use incremental::{cases, complete, parse};

fn case(name: &str) -> Value {
    cases().into_iter().find(|c| c["name"] == name).unwrap()
}

fn full(base: &StylePatch, upper: &StylePatch) -> Resolved<Style> {
    let mut folded = base.clone();
    folded.merge(upper.clone());
    Resolved::base(&generated::TABLES, folded).unwrap()
}

fn bench(c: &mut Criterion) {
    let theme_case = case("theme/clarity");
    let theme = parse(&complete(&theme_case["layers"][0]));
    let parent = Resolved::base(&generated::TABLES, (*theme).clone()).unwrap();
    let uppers = [
        ("one leaf", parse(&json!({"accent": "#ff0000"}))),
        (
            "nested board",
            parse(&case("corpus/jaffle_games/charts/_dbt-dark.yml")["layers"][1]),
        ),
        ("full theme", parse(&theme_case["expected"])),
    ];
    for (name, upper) in &uppers {
        let mut g = c.benchmark_group(*name);
        g.bench_function("apply", |b| {
            b.iter(|| black_box(parent.apply(upper)).is_ok())
        });
        g.bench_function("recompute", |b| b.iter(|| black_box(full(&theme, upper))));
        g.finish();
    }
}

criterion_group!(benches, bench);
criterion_main!(benches);

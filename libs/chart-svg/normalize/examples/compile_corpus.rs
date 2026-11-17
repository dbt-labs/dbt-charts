//! Per-board `normalize::compile` time over the accepted corpus, warm and in one process.
//!
//! `cargo run --release -p normalize --example compile_corpus [-- <passes> [<name fragment>]]`
//! prints the median, p95 and max over every accepted board and the slowest few; a name fragment
//! runs only the boards containing it, for a profiler. The SQL seam is opaque and no project file
//! exists, so this is the pure-Rust cost. `libs/chart-svg/bench_compile.py` times the same boards
//! through `_engine.compile` next to Python's `compile_file`.

use std::collections::BTreeMap;
use std::time::Instant;

use normalize::fixtures::host_inputs;
use normalize::{compile_or_refuse, MemoryFiles};
use project::ProjectPath;

fn ms(sorted: &[f64], q: f64) -> f64 {
    sorted[((sorted.len() - 1) as f64 * q).round() as usize]
}

fn main() {
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let corpus: BTreeMap<String, String> = serde_json::from_str(
        &std::fs::read_to_string(root.join("../board-model/tests/fixtures/corpus.json")).unwrap(),
    )
    .unwrap();
    let accepted =
        std::fs::read_to_string(root.join("tests/fixtures/accepted_boards.txt")).unwrap();
    let passes: usize = std::env::args()
        .nth(1)
        .and_then(|n| n.parse().ok())
        .unwrap_or(5);
    let only = std::env::args().nth(2);
    let path = ProjectPath::try_from("b.yaml").unwrap();
    let host = host_inputs();
    let boards: Vec<(&str, &String)> = accepted
        .lines()
        .filter_map(|l| l.split('\t').nth(1))
        .filter(|name| only.as_deref().is_none_or(|o| name.contains(o)))
        .filter_map(|name| corpus.get(name).map(|text| (name, text)))
        .collect();
    // The first compile of a process builds the lazily initialized tables (migration registry, built-in themes); time warm calls only.
    let mut files = MemoryFiles::default();
    files.insert(path.clone(), boards[0].1.clone());
    let _ = compile_or_refuse(&files, &path, &host);
    let mut best: Vec<(f64, &str)> = boards.iter().map(|(n, _)| (f64::MAX, *n)).collect();
    let mut total: Vec<(f64, &str)> = boards.iter().map(|(n, _)| (f64::MAX, *n)).collect();
    for _ in 0..passes {
        for (i, (_, text)) in boards.iter().enumerate() {
            let mut files = MemoryFiles::default();
            files.insert(path.clone(), (*text).clone());
            let start = Instant::now();
            let compiled = std::hint::black_box(normalize::compile(&files, &path, &host));
            best[i].0 = best[i].0.min(start.elapsed().as_secs_f64() * 1e3);
            if let Ok(compiled) = compiled {
                let start = Instant::now();
                let _ = std::hint::black_box(compiled.board());
                total[i].0 = total[i].0.min(start.elapsed().as_secs_f64() * 1e3);
            }
        }
    }
    let mut total: Vec<(f64, &str)> = total.into_iter().filter(|t| t.0 != f64::MAX).collect();
    total.sort_by(|a, b| a.0.total_cmp(&b.0));
    let slowest_boards: Vec<(f64, &str)> = total.iter().rev().take(5).copied().collect();
    let total: Vec<f64> = total.iter().map(|t| t.0).collect();
    best.sort_by(|a, b| a.0.total_cmp(&b.0));
    let times: Vec<f64> = best.iter().map(|b| b.0).collect();
    println!("{} boards, best of {passes} passes", times.len());
    println!(
        "median {:.2} ms  p95 {:.2} ms  max {:.2} ms  sum {:.0} ms",
        ms(&times, 0.5),
        ms(&times, 0.95),
        ms(&times, 1.0),
        times.iter().sum::<f64>()
    );
    if !total.is_empty() {
        println!(
            "board() of the {} that compile: median {:.2} ms  p95 {:.2} ms  max {:.2} ms  sum {:.0} ms",
            total.len(),
            ms(&total, 0.5),
            ms(&total, 0.95),
            ms(&total, 1.0),
            total.iter().sum::<f64>()
        );
    }
    for (t, n) in &slowest_boards {
        println!("  slowest board() {t:7.2} ms  {n}");
    }
    for (t, n) in best.iter().rev().take(8) {
        println!("  slowest {t:8.2} ms  {n}");
    }
    for (t, n) in best.iter().take(3) {
        println!("  fastest {t:8.3} ms  {n}");
    }
}

//! Compiling a board of table charts whose own style restates the board's tier for the type: the
//! restated pass reads each scope's family slot once, however many charts sit in it. Compared with
//! the same board where no chart restates (the pass finds nothing to flag) and with one where no
//! chart authors a style at all (the pass is gated out before any lookup).
//! `board_*` boards author their own `style:` beside none, for the board-level check.
//! `*_axis_format` boards put the same y-axis label format against the query's `formats:` entry.
//! `cargo bench -p normalize --bench restated`

use criterion::{criterion_group, criterion_main, Criterion};
use normalize::fixtures::host_inputs;
use normalize::{compile, MemoryFiles};
use project::ProjectPath;
use std::fmt::Write;
use std::hint::black_box;

fn board(charts: usize, style: &str) -> String {
    let mut text = String::from(
        "source: db\nstyle:\n  charts:\n    table:\n      pagination:\n        page_rows: 7\n        enabled: true\nqueries:\n  sales:\n    sql: select 1 as order_id\ncharts:\n",
    );
    let mut rows = String::from("rows:\n");
    for i in 0..charts {
        let _ = write!(text, "  c{i}:\n    type: table\n    query: sales\n{style}");
        let _ = writeln!(rows, "  - c{i}");
    }
    text + &rows
}

fn bench(c: &mut Criterion) {
    let path = ProjectPath::try_from("b.yaml").expect("a path");
    let host = host_inputs();
    let restating = "    style:\n      pagination:\n        page_rows: 7\n        enabled: true\n";
    let different = "    style:\n      pagination:\n        page_rows: 8\n        enabled: false\n";
    for (name, style) in [
        ("every_chart_restates", restating),
        ("every_chart_overrides", different),
        ("no_chart_style", ""),
    ] {
        for charts in [10, 100] {
            let mut files = MemoryFiles::default();
            files.insert(path.clone(), board(charts, style));
            c.bench_function(&format!("compile/{name}/{charts}_charts"), |b| {
                b.iter(|| compile(black_box(&files), &path, &host).expect("compiles"))
            });
        }
    }
}

/// A board whose own file authors cascaded style: the board-level check looks each own key up in
/// the theme's leaves (read once per process) and the `meta.yml` layers.
fn bench_board_level(c: &mut Criterion) {
    let path = ProjectPath::try_from("b.yaml").expect("a path");
    let host = host_inputs();
    for (name, style) in [
        (
            "board_restates_theme",
            "style:\n  frame:\n    card_gap: 8.0\n",
        ),
        (
            "board_overrides_theme",
            "style:\n  frame:\n    card_gap: 9.0\n",
        ),
        ("board_authors_no_style", ""),
    ] {
        let mut files = MemoryFiles::default();
        files.insert(
            path.clone(),
            format!("source: db\n{style}queries:\n  sales:\n    sql: select 1 as order_id\ncharts:\n  c:\n    type: table\n    query: sales\nrows:\n  - c\n"),
        );
        c.bench_function(&format!("compile/{name}"), |b| {
            b.iter(|| compile(black_box(&files), &path, &host).expect("compiles"))
        });
    }
}

/// Bar charts whose y-axis label format restates, overrides or leaves to the query's `formats:`
/// entry: the check reads each chart's own slot against the entry, resolved once per chart.
fn bench_query_formats(c: &mut Criterion) {
    let path = ProjectPath::try_from("b.yaml").expect("a path");
    let host = host_inputs();
    for (name, style) in [
        (
            "every_axis_format_restates",
            "    style:\n      axis_y:\n        labels:\n          format: integer\n",
        ),
        (
            "every_axis_format_overrides",
            "    style:\n      axis_y:\n        labels:\n          format: percent\n",
        ),
        ("no_axis_format", ""),
    ] {
        for charts in [10, 100] {
            let mut text = String::from(
                "source: db\nqueries:\n  q:\n    sql: select 1 as a, 2 as b\n    formats:\n      b: integer\ncharts:\n",
            );
            let mut rows = String::from("rows:\n");
            for i in 0..charts {
                let _ = write!(
                    text,
                    "  c{i}:\n    type: bar\n    query: q\n    x: a\n    y: b\n{style}"
                );
                let _ = writeln!(rows, "  - c{i}");
            }
            let mut files = MemoryFiles::default();
            files.insert(path.clone(), text + &rows);
            c.bench_function(&format!("compile/{name}/{charts}_charts"), |b| {
                b.iter(|| compile(black_box(&files), &path, &host).expect("compiles"))
            });
        }
    }
}

criterion_group!(benches, bench, bench_board_level, bench_query_formats);
criterion_main!(benches);

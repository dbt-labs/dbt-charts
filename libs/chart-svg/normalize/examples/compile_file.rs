//! Compiles one board file repeatedly and prints the best time: `cargo run --release -p normalize
//! --example compile_file -- <file> [<passes>]`. For a profiler on a board of your own.

use std::time::Instant;

use normalize::fixtures::host_inputs;
use normalize::{compile, compile_or_refuse, MemoryFiles};
use project::ProjectPath;

fn main() {
    let mut args = std::env::args().skip(1);
    let file = args.next().expect("a board file");
    let passes: usize = args.next().and_then(|n| n.parse().ok()).unwrap_or(20);
    let text = std::fs::read_to_string(&file).expect("readable");
    let path = ProjectPath::try_from("b.yaml").unwrap();
    let host = host_inputs();
    let mut files = MemoryFiles::default();
    files.insert(path.clone(), text);
    // The first compile of a process builds the lazily initialized tables; it goes through the
    // other entry point so a profiler collecting `compile` sees warm calls only.
    println!(
        "compiles: {}",
        compile_or_refuse(&files, &path, &host).is_ok()
    );
    let mut best = f64::MAX;
    for _ in 0..passes {
        let start = Instant::now();
        let _ = std::hint::black_box(compile(&files, &path, &host));
        best = best.min(start.elapsed().as_secs_f64() * 1e3);
    }
    println!("{file}: best of {passes} = {best:.2} ms");
}

//! Deserializes every `*.json` under the directory argument into `Board` and
//! exits non-zero on the first failure; run by `board_types_sweep.py`.

use board_types::Board;

fn main() {
    // The generated types' untagged unions deserialize through large frames; a
    // board overflows the default 8 MiB main stack.
    let worker = std::thread::Builder::new()
        .stack_size(512 << 20)
        .spawn(run)
        .expect("spawn");
    worker.join().expect("worker");
}

fn run() {
    let dir = std::env::args().nth(1).expect("usage: check_corpus <dir>");
    let mut paths: Vec<_> = std::fs::read_dir(&dir)
        .expect("read corpus dir")
        .map(|e| e.expect("dir entry").path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    paths.sort();
    let mut failed = 0;
    for path in &paths {
        let text = std::fs::read_to_string(path).expect("read board json");
        if let Err(e) = serde_json::from_str::<Board>(&text) {
            failed += 1;
            eprintln!("{}: {e}", path.display());
        }
    }
    println!("{} boards, {failed} failed", paths.len());
    std::process::exit(i32::from(failed > 0));
}

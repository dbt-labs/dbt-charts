//! Indexes `locale/{number,time}/<tag>.json` (written by `locale_gen/gen_locales.mjs`)
//! into `$OUT_DIR/locales.rs`: each folder's tags in sorted order and its files'
//! text, so a locale exists exactly when its file does.

use std::fmt::Write;
use std::path::Path;
use std::{env, fs};

fn index(kind: &str, konst: &str) -> String {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("locale")
        .join(kind);
    println!("cargo:rerun-if-changed={}", dir.display());
    let mut tags: Vec<String> = fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("{}: {e}", dir.display()))
        .map(|entry| entry.unwrap().file_name().into_string().unwrap())
        .filter_map(|name| name.strip_suffix(".json").map(str::to_string))
        .collect();
    tags.sort();
    let mut out = format!("pub const {konst}: [&str; {}] = [\n", tags.len());
    for tag in &tags {
        writeln!(out, "    {tag:?},").unwrap();
    }
    write!(
        out,
        "];\nconst {konst}_SOURCES: [&str; {}] = [\n",
        tags.len()
    )
    .unwrap();
    for tag in &tags {
        let path = dir.join(format!("{tag}.json"));
        writeln!(out, "    include_str!({:?}),", path.display().to_string()).unwrap();
    }
    out + "];\n"
}

fn main() {
    let out = index("number", "NUMBER_LOCALES") + &index("time", "TIME_LOCALES");
    fs::write(
        Path::new(&env::var("OUT_DIR").unwrap()).join("locales.rs"),
        out,
    )
    .unwrap();
}

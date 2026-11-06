//! The laws of the one date parser, scanned over the engine's sources: `bind::time_axis` holds the only
//! date reader, and outside it only the readers of text no axis holds call the parser, and only the readers of
//! a domain no chart axis holds read one into dates; tests read through `bind::time_axis::testing`.

    /// Every source of the engine's two crates that parse or order data, as `(path, text)`: this crate's
/// `src` and `bind`'s, the latter prefixed `bind/`; `bind`'s `time_axis.rs` is the parser itself and this file only names it.
fn sources() -> Vec<(std::path::PathBuf, String)> {
    let manifest = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
    let mut out = Vec::new();
    for (root, prefix) in [(manifest.join("src"), ""), (manifest.join("../bind/src"), "bind")] {
        let mut stack = vec![root.clone()];
        while let Some(dir) = stack.pop() {
            for entry in std::fs::read_dir(&dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|e| e == "rs") && path.file_name().is_some_and(|name| name != "time_axis.rs" && name != "time_axis_laws.rs") {
                    let text = std::fs::read_to_string(&path).unwrap();
                    out.push((std::path::Path::new(prefix).join(path.strip_prefix(&root).unwrap()), text));
                }
            }
        }
    }
    out
}

/// The one parser: nothing else in the engine defines a date reader.
#[test]
fn this_module_holds_the_only_date_parser() {
    let mut offenders = Vec::new();
    for (path, text) in sources() {
        for needle in ["fn date_text_day(", "fn parse_iso_date("] {
            if text.contains(needle) {
                offenders.push(format!("{}: {needle}", path.display()));
            }
        }
    }
    assert!(offenders.is_empty(), "{offenders:?}");
}

/// Outside this module only the readers of text no axis holds call the parser, and only the readers of a
/// domain no chart axis holds read one into dates; tests read through [`testing`].
#[test]
fn only_readers_of_text_no_axis_holds_call_the_parser() {
    const PARSERS: [&str; 2] = ["date_text_day", "parse_iso_date"];
    const PARSER_READERS: [(&str, &str); 9] = [
        ("annotation/model.rs", "days"),
        ("bind/x_type.rs", "parse_date"),
        ("families/scatter_axis.rs", "authored_day_domain"),
        ("families/scatter/layout.rs", "fit"),
        ("variables.rs", "full_iso_date"),
        ("families/bar/vertical.rs", "leading_dates"),
        ("families/temporal.rs", "iso_format_day"),
        ("families/table/bind.rs", "iso_day"),
        ("families/custom_d3_time.rs", "instant"),
    ];
    const DOMAIN_READS: [&str; 2] = ["Dates::read", "TimeAxis::over"];
    const DOMAIN_READERS: [(&str, &str); 10] = [
        ("families/bar/bind.rs", "rekey_to_enclosing_buckets"),
        ("families/line/bind.rs", "rekey_to_enclosing_buckets"),
        ("families/bar/horizontal.rs", "render_horizontal_panel"),
        ("families/scatter/bind.rs", "bind"),
        ("bind/x_type.rs", "scaffold_rows"),
        ("bind/temporal.rs", "has_labeled_bucket"),
        ("families/axis_caption.rs", "x_axis_caption"),
        ("families/heatmap.rs", "of"),
        ("families/heatmap/layout.rs", "y_date_labels"),
        ("families/heatmap_facet.rs", "dates"),
    ];
    let calls = |code: &str, names: &[&str]| {
        names.iter().any(|name| {
            code.match_indices(name).any(|(i, _)| {
                let before = code[..i].chars().next_back().is_some_and(|c| c.is_alphanumeric() || c == '_');
                let after = &code[i + name.len()..];
                !before && (after.starts_with('(') || after.starts_with(')') || after.starts_with(',') || after.starts_with(';'))
            })
        })
    };
    let mut offenders = Vec::new();
    for (path, text) in sources() {
        let path = path.to_string_lossy().replace('\\', "/");
        // The functions open around a line: name, the brace depth it starts at, whether its body opened.
        let mut open: Vec<(&str, i64, bool)> = Vec::new();
        let mut depth = 0_i64;
        let mut within_use = false;
        for (n, line) in text.lines().enumerate() {
            let code = line.split("//").next().unwrap_or_default();
            let item = code.trim_start();
            let item = item.strip_prefix("pub").map_or(item, |rest| match rest.strip_prefix('(') {
                Some(scoped) => scoped.split_once(')').map_or(rest, |(_, after)| after),
                None => rest,
            });
            within_use |= item.trim_start().starts_with("use ");
            if within_use {
                // A renamed reader hides its calls from the scan.
                let renames = PARSERS.iter().chain(&["Dates", "TimeAxis"]).any(|name| {
                    code.match_indices(&format!("{name} as ")).any(|(i, _)| {
                        !code[..i].chars().next_back().is_some_and(|c| c.is_alphanumeric() || c == '_')
                    })
                });
                if renames {
                    offenders.push(format!("{path}:{} renames a date reader", n + 1));
                }
                within_use = !code.trim_end().ends_with(';');
                continue;
            }
            if let Some(at) = code.find("fn ") {
                let name = code[at + 3..].split(|c: char| !c.is_alphanumeric() && c != '_').next().unwrap_or_default();
                if !name.is_empty() {
                    open.push((name, depth, false));
                }
            }
            let within = open.last().map_or("", |f| f.0);
            let site = (path.as_str(), within);
            if calls(code, &PARSERS) && !PARSER_READERS.contains(&site) || calls(code, &DOMAIN_READS) && !DOMAIN_READERS.contains(&site) {
                offenders.push(format!("{path}:{} in {within}", n + 1));
            }
            depth += code.matches('{').count() as i64 - code.matches('}').count() as i64;
            while let Some(f) = open.last_mut() {
                f.2 |= depth > f.1 || code.contains('{');
                let ended = if f.2 { depth <= f.1 } else { code.trim_end().ends_with(';') };
                if !ended {
                    break;
                }
                open.pop();
            }
        }
    }
    assert!(offenders.is_empty(), "{offenders:#?}");
}

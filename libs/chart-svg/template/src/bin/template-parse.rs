//! Sweep helper: reads `[{id, kind, source}]` on stdin, writes
//! `[{id, ok, diagnostics | info}]` on stdout.

use std::io::{Read, Write};

use serde::{Deserialize, Serialize};
use template::{Diagnostic, Kind, TemplateInfo};

#[derive(Deserialize)]
struct Case {
    id: String,
    kind: Kind,
    source: String,
}

#[derive(Serialize)]
struct Outcome {
    id: String,
    ok: bool,
    diagnostics: Vec<Diagnostic>,
    info: Option<TemplateInfo>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input)?;
    let cases: Vec<Case> = serde_json::from_str(&input)?;
    let outcomes: Vec<Outcome> = cases
        .into_iter()
        .map(|c| match template::parse(&c.source, c.kind) {
            Ok(info) => Outcome {
                id: c.id,
                ok: true,
                diagnostics: vec![],
                info: Some(info),
            },
            Err(diagnostics) => Outcome {
                id: c.id,
                ok: false,
                diagnostics,
                info: None,
            },
        })
        .collect();
    std::io::stdout().write_all(serde_json::to_string(&outcomes)?.as_bytes())?;
    Ok(())
}

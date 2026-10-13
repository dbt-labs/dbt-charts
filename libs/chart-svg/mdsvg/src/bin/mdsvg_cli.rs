//! Harness CLI for the mdsvg Rust port.
//!
//! Reads one JSON request from stdin, renders or measures it against the
//! Rust port, and writes the result to stdout — so
//! `chart_svg_dev/mdsvg_diff.py` can build the exact production `Style`/
//! `FontSlots` in Python, hand them to this binary verbatim, and diff the
//! bytes against Python mdsvg's own output for the same input.
//!
//! Request shape (JSON):
//! ```json
//! {
//!   "mode": "render" | "measure" | "max_content_width",
//!   "markdown": "# Title",
//!   "width": 400.0,
//!   "padding": 0.0,
//!   "fetch_image_sizes": true,
//!   "style": { ...Style fields, any subset... },
//!   "fonts": { "regular": {"path": "...", "weight": null, "font_number": 0}, ... }
//! }
//! ```
//!
//! `render` writes the SVG string to stdout; `measure` writes
//! `{"width": ..., "height": ...}`; `max_content_width` writes
//! `{"max_content_width": ...}`. A refused/unported block kind, or any
//! other renderer error, is reported as `{"error": "..."}` on stdout with
//! exit code 1 — never a silent partial render.
//!
//! `mdsvg-cli east-asian-dump` (no stdin request) is a separate probe mode —
//! see [`dump_east_asian_ranges`]. `mdsvg-cli highlight` is another: see
//! [`highlight_batch`]; so is `mdsvg-cli css`: see [`css_batch`].

use std::io::{Read, Write};

use mdsvg::fonts::FontSlots;
use mdsvg::style::Style;

#[derive(serde::Deserialize)]
struct CliRequest {
    mode: String,
    markdown: String,
    width: f64,
    #[serde(default)]
    padding: f64,
    #[serde(default)]
    style: Style,
    fonts: FontSlots,
    /// Python's constructor default; board prose sends `false`.
    #[serde(default = "default_fetch_image_sizes")]
    fetch_image_sizes: bool,
}

fn default_fetch_image_sizes() -> bool {
    true
}

/// Print `{"error": msg}` (no trailing newline, matching every success
/// path) and exit(1). `std::process::exit` skips destructors — including
/// the flush a normal `main` return gets from the runtime — so a bare
/// `print!` right before it can lose the write entirely; flushing by hand
/// here is what makes that safe.
fn fail(msg: impl Into<String>) -> ! {
    print!("{}", serde_json::json!({"error": msg.into()}));
    std::io::stdout().flush().expect("mdsvg-cli: failed to flush stdout");
    std::process::exit(1);
}

/// `mdsvg-cli east-asian-dump` — prints `{"wide": [[lo, hi], ...], "zero_mark": [cp, ...]}`,
/// the compiled `fonts::is_wide_east_asian`/`is_zero_advance_wide_mark`
/// predicates' answer over every codepoint 0x0-0x10FFFF, coalesced into
/// ranges. `chart_svg_dev/gen_east_asian_width.py`'s own test computes the
/// same predicate straight from `unicodedata` and diffs against this —
/// exercising the actual compiled/wired functions, not just the generated
/// source file's text.
fn dump_east_asian_ranges() -> ! {
    let mut wide_ranges: Vec<(u32, u32)> = Vec::new();
    let mut wide_start: Option<u32> = None;
    let mut zero_marks: Vec<u32> = Vec::new();
    for cp in 0u32..=0x10FFFF {
        let Some(ch) = char::from_u32(cp) else {
            if let Some(start) = wide_start.take() {
                wide_ranges.push((start, cp - 1));
            }
            continue;
        };
        if mdsvg::fonts::is_wide_east_asian(ch) {
            if wide_start.is_none() {
                wide_start = Some(cp);
            }
        } else if let Some(start) = wide_start.take() {
            wide_ranges.push((start, cp - 1));
        }
        if mdsvg::fonts::is_zero_advance_wide_mark(cp) {
            zero_marks.push(cp);
        }
    }
    if let Some(start) = wide_start {
        wide_ranges.push((start, 0x10FFFF));
    }
    print!(
        "{}",
        serde_json::json!({"wide": wide_ranges, "zero_mark": zero_marks})
    );
    std::io::stdout().flush().expect("mdsvg-cli: failed to flush stdout");
    std::process::exit(0);
}

#[derive(serde::Deserialize)]
struct HighlightRequest {
    code: String,
    language: String,
    theme: String,
}

/// `mdsvg-cli highlight` — reads a JSON array of `{code, language, theme}`
/// from stdin and prints one result per request: `{"lines": [[[text,
/// color], ...], ...]}` (`highlight_code`'s segments), `{"plain": true}` for
/// an alias Pygments does not know, or `{"error": msg}` for a refusal.
/// `libs/chart-svg/tests/test_mdsvg_highlight.py` diffs the segments against
/// Python's `highlight_code` over a corpus of YAML documents.
fn highlight_batch() -> ! {
    let mut input = String::new();
    std::io::stdin()
        .read_to_string(&mut input)
        .expect("mdsvg-cli: failed to read stdin");
    let requests: Vec<HighlightRequest> = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(e) => fail(format!("bad request: {e}")),
    };
    let results: Vec<serde_json::Value> = requests
        .iter()
        .map(
            |r| match mdsvg::highlight::highlight_code(&r.code, &r.language, &r.theme) {
                Ok(Some(lines)) => serde_json::json!({"lines": lines}),
                Ok(None) => serde_json::json!({"plain": true}),
                Err(e) => serde_json::json!({"error": e}),
            },
        )
        .collect();
    print!("{}", serde_json::Value::Array(results));
    std::io::stdout()
        .flush()
        .expect("mdsvg-cli: failed to flush stdout");
    std::process::exit(0);
}

/// `mdsvg-cli css` — reads a JSON array of strings from stdin and prints, for
/// each, `{"stack": css_font_family_stack(s), "style_error": <detail or null>}`:
/// the string as a font-family stack, and as `Style.text_color`
/// (`Style::validate_css`'s refusal text). `libs/chart-svg/tests/
/// test_mdsvg_css.py` diffs both against `mdsvg.css` and `mdsvg.Style` over
/// every Unicode scalar value.
fn css_batch() -> ! {
    let mut input = String::new();
    std::io::stdin()
        .read_to_string(&mut input)
        .expect("mdsvg-cli: failed to read stdin");
    let values: Vec<String> = match serde_json::from_str(&input) {
        Ok(v) => v,
        Err(e) => fail(format!("bad request: {e}")),
    };
    let results: Vec<serde_json::Value> = values
        .into_iter()
        .map(|value| {
            let style = Style {
                text_color: value.clone(),
                ..Style::default()
            };
            serde_json::json!({
                "stack": mdsvg::css::css_font_family_stack(&value),
                "style_error": style.validate_css().err(),
            })
        })
        .collect();
    print!("{}", serde_json::Value::Array(results));
    std::io::stdout()
        .flush()
        .expect("mdsvg-cli: failed to flush stdout");
    std::process::exit(0);
}

fn main() {
    match std::env::args().nth(1).as_deref() {
        Some("css") => css_batch(),
        Some("east-asian-dump") => dump_east_asian_ranges(),
        Some("highlight") => highlight_batch(),
        _ => {}
    }

    let mut input = String::new();
    std::io::stdin()
        .read_to_string(&mut input)
        .expect("mdsvg-cli: failed to read stdin");

    let request: CliRequest = match serde_json::from_str(&input) {
        Ok(r) => r,
        Err(e) => fail(format!("bad request: {e}")),
    };

    let blocks = mdsvg::parser::parse(&request.markdown);
    let renderer = match mdsvg::renderer::SVGRenderer::new(request.style, request.fonts) {
        Ok(r) => r.with_fetch_image_sizes(request.fetch_image_sizes),
        Err(e) => fail(e),
    };

    match request.mode.as_str() {
        "render" => match renderer.render(&blocks, request.width, request.padding) {
            Ok(svg) => print!("{svg}"),
            Err(e) => fail(e),
        },
        "measure" => match renderer.measure(&blocks, request.width, request.padding) {
            Ok(size) => print!(
                "{}",
                serde_json::json!({"width": size.width, "height": size.height})
            ),
            Err(e) => fail(e),
        },
        "max_content_width" => match renderer.max_content_width(&blocks) {
            Ok(width) => print!("{}", serde_json::json!({"max_content_width": width})),
            Err(e) => fail(e),
        },
        other => fail(format!("unknown mode {other:?}")),
    }
}

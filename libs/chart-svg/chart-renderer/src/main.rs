//! chart-renderer CLI — spike binary.
//!
//!   chart-renderer payload.json out.svg [--bench N] [--bench-parse N]
//!
//! The payload envelope (see lib.rs) names the chart family via
//! `resolved.chart_type`; rendering dispatches to the family module.

use std::time::Instant;

use chart_renderer::families::table::width::{measure_natural_width, MeasurePayload};
use chart_renderer::{board, render_fitted, render_measured, text::FontBook, Payload};

/// Resolve a payload-relative `fonts_dir` against the directory holding
/// `payload_path`, so a committed fixture's `fonts_dir` stays
/// machine-portable — `None` when the payload carries no `fonts_dir` at
/// all. Shared by `load_fonts` (measurement) and `--embed-fonts` (raw
/// `.woff2` bytes, item d), which both need the SAME resolved directory.
fn resolve_fonts_dir(fonts_dir: &Option<String>, payload_path: &str) -> Option<std::path::PathBuf> {
    let d = fonts_dir.as_ref()?;
    let base = std::path::Path::new(payload_path)
        .parent()
        .unwrap_or_else(|| std::path::Path::new("."));
    Some(if std::path::Path::new(d).is_absolute() {
        std::path::PathBuf::from(d)
    } else {
        base.join(d)
    })
}

/// The same directory as a string, for the payload a custom chart's child
/// process reads `fonts_dir` from (`sandbox`): the child has no payload
/// path to resolve a relative one against.
fn resolved_fonts_dir(fonts_dir: &Option<String>, payload_path: &str) -> Option<String> {
    resolve_fonts_dir(fonts_dir, payload_path).map(|p| p.to_string_lossy().into_owned())
}

/// `FontBook::empty()` when `fonts_dir` is absent.
fn load_fonts(fonts_dir: &Option<String>, payload_path: &str) -> FontBook {
    match resolve_fonts_dir(fonts_dir, payload_path) {
        Some(resolved) => match FontBook::load_dir(&resolved.to_string_lossy()) {
            Ok(fb) => fb,
            Err(e) => {
                eprintln!("error: {e}");
                std::process::exit(1);
            }
        },
        None => FontBook::empty(),
    }
}

/// `chart-renderer board <payload|-> <out|-> [--report path] [--embed-fonts]`
/// — item (a) of the R1 engine contract. `-` for the payload arg reads it from stdin; `-`
/// for the out arg writes the SVG to stdout (never mixed with the status
/// line the file-path form still prints, so a stdout consumer sees only
/// SVG bytes). The render report — the per-chart status list (item b:
/// `rendered`/`refused` per chart id) plus the board-level box list this
/// binary always wrote as `<out>.boxes.json` before this PR — goes to
/// `--report <path>` when given, else to stderr as one JSON line when
/// `out` is `-` (stdout is spoken for), else to `<out>.boxes.json` as
/// before: the file-path form's on-disk contract is unchanged, only its
/// content gained the `chart_statuses` key (`board_sweep.py`, the one
/// production consumer of that file, reads `items`/`container` only).
///
/// A board-level failure (bad payload, a board law this composer does not
/// model — tabs, an unrecognized layout, etc.) still exits non-zero with
/// `render error: <message>` on stderr; a single chart's own refusal no
/// longer reaches this level at all (`board::render_chart_item` catches it
/// and paints an error card, item b).
fn run_board(args: &[String]) {
    if args.len() < 2 {
        eprintln!("usage: chart-renderer board <payload.json|-> <out.svg|-> [--report path] [--embed-fonts]");
        std::process::exit(2);
    }
    let payload_arg = args[0].as_str();
    let out_arg = args[1].as_str();
    let report_path = flag_value(args, "--report");

    let raw = if payload_arg == "-" {
        let mut buf = String::new();
        std::io::Read::read_to_string(&mut std::io::stdin(), &mut buf)
            .expect("read board payload from stdin");
        buf
    } else {
        std::fs::read_to_string(payload_arg).expect("read board payload")
    };
    // A malformed/drifted payload (a producer out of step with `sizing::
    // ChartDefaultsDoc` — R3-H1: `engine_payload.py` once omitted
    // `histogram`) must surface as a NAMED refusal, the same `render
    // error:` shape every other board-level law violation uses — never a
    // raw Rust panic, which the cutover plan's error mapping cannot read
    // as anything but ERR_INTERNAL.
    let mut payload: board::BoardPayload = serde_json::from_str(&raw).unwrap_or_else(|e| {
        eprintln!("render error: board payload does not match the engine contract: {e}");
        std::process::exit(1);
    });
    // Stdin carries no filesystem location to resolve a relative fonts_dir
    // against — resolve it against the working directory instead, same as
    // an omitted fonts_dir already falls through to via `Path::new(".")`.
    let fonts_base = if payload_arg == "-" { "." } else { payload_arg };
    let fonts = load_fonts(&payload.fonts_dir, fonts_base);
    payload.fonts_dir = resolved_fonts_dir(&payload.fonts_dir, fonts_base);

    // Computed once here (board::render_board computes its own copy
    // internally — see size_board's doc) purely so board_sweep.py can diff
    // Rust's own boxes against production's SIZED board without reading
    // them back out of the composed SVG.
    let (boxes, container, overrides, _fresh_render) =
        board::size_board(&payload, &fonts).unwrap_or_else(|e| {
            eprintln!("render error: {e}");
            std::process::exit(1);
        });
    let items =
        board::size_board_items_json(&payload, &fonts, &boxes, &overrides).unwrap_or_else(|e| {
            eprintln!("render error: {e}");
            std::process::exit(1);
        });

    // The derived row title shift of every drawn layout item beside the one the
    // payload carries, so a harness can prove the derivation against production's.
    let title_shifts: Vec<serde_json::Value> = board::derive_title_shifts(&payload, &fonts, &items)
        .unwrap_or_else(|e| {
            eprintln!("render error: {e}");
            std::process::exit(1);
        })
        .iter()
        .map(|s| {
            serde_json::json!({
                "path": s.pointer(),
                "supplied": {"title_dy": s.supplied.title_dy, "body_dy": s.supplied.body_dy},
                "derived": {"title_dy": s.derived.title_dy, "body_dy": s.derived.body_dy},
            })
        })
        .collect();

    let (svg, painted_markup) = board::render_board(&payload, &fonts).unwrap_or_else(|e| {
        eprintln!("render error: {e}");
        std::process::exit(1);
    });
    // Item (d): --embed-fonts REPLACES the composed SVG's own URL-mode
    // @font-face roster with a standalone one carrying only the families
    // this board actually painted with, as base64 data: URIs — no external
    // font references left in the output (HIGH 3). Face SELECTION scans
    // `painted_markup` (content only), never `svg` (which still carries the
    // URL roster itself, a false-positive source — R2 review round). The
    // italics markdown emphasis consumed while painting were recorded on the
    // payload by `render_board` and join the declared ones.
    let svg = if args.iter().any(|a| a == "--embed-fonts") {
        let embed_fonts_dir = resolve_fonts_dir(&payload.fonts_dir, fonts_base);
        chart_renderer::embed_fonts::embed_used_fonts(
            svg,
            &payload.font_registry,
            &payload.font_face_css,
            &painted_markup,
            &payload.painted_italics,
            embed_fonts_dir
                .as_deref()
                .map(|p| p.to_string_lossy().into_owned())
                .as_deref(),
        )
        .unwrap_or_else(|e| {
            eprintln!("render error: {e}");
            std::process::exit(1);
        })
    } else {
        svg
    };

    let report = serde_json::json!({
        "items": items,
        "container": {
            "width": container.width,
            "height": container.height,
            "content_width": container.content_width,
            "content_height": container.content_height,
        },
        "chart_statuses": payload.report.borrow().clone(),
        "title_shifts": title_shifts,
    });
    let report_str = serde_json::to_string(&report).unwrap();

    if out_arg == "-" {
        use std::io::Write;
        // render_board's return has no trailing newline; production's own
        // render_board_svg doesn't add one either, so stdout must not.
        std::io::stdout()
            .write_all(svg.as_bytes())
            .expect("write svg to stdout");
    } else {
        std::fs::write(out_arg, &svg).expect("write svg");
    }

    match report_path {
        Some(path) => std::fs::write(path, &report_str).expect("write report"),
        None if out_arg == "-" => eprintln!("{report_str}"),
        None => {
            let boxes_path = std::path::Path::new(out_arg).with_extension("boxes.json");
            std::fs::write(&boxes_path, &report_str).expect("write report sidecar");
        }
    }

    if out_arg != "-" {
        println!("wrote {out_arg} ({} bytes)", svg.len());
    }
}

/// `chart-renderer case <case-value> <text>` — prints `case::apply_case`'s
/// result, for `chart_svg_dev/case_probe.py` (run as a pytest via
/// `tests/test_case_probe.py`) to diff against real
/// `dbt_charts.core.text.case.apply_case`.
fn run_case(args: &[String]) {
    if args.len() < 2 {
        eprintln!("usage: chart-renderer case case-value text");
        std::process::exit(2);
    }
    match chart_renderer::case::apply_case(&args[1], &args[0]) {
        Ok(result) => println!("{result}"),
        Err(e) => {
            eprintln!("error: {e}");
            std::process::exit(1);
        }
    }
}

/// Read a `--flag value` pair out of a raw arg slice — the same shape
/// `run_board`'s own `--report` lookup and `main`'s `--bench`/`--bench-parse`
/// use, pulled out once now that `png`/`pdf` need a second flag
/// (`--fonts-dir`) beside `png`'s own `--scale`.
fn flag_value<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .map(String::as_str)
}

/// `svg.data` (stdin when `svg_arg == "-"`, else the file's bytes) — the
/// same `-`-means-stdin convention `run_board` established for item (a).
fn read_svg_input(svg_arg: &str) -> Vec<u8> {
    if svg_arg == "-" {
        let mut buf = Vec::new();
        std::io::Read::read_to_end(&mut std::io::stdin(), &mut buf).expect("read svg from stdin");
        buf
    } else {
        std::fs::read(svg_arg).expect("read svg file")
    }
}

/// `render/converters/png.py`/`pdf.py` both call `normalize_svg_font_
/// families_for_vl_convert(svg)` before handing the SVG to resvg (HIGH 4):
/// resvg cannot bind a variable font's `wght` axis from a numeric
/// `font-weight` attribute, so a `500`/`600` run rasterizes with the wrong
/// face without this rewrite. `run_png`/`run_pdf` use the same resvg, so
/// they need the same rewrite — never a silent divergence between what a
/// browser paints and what a static export does. Refuses (never silently
/// rasterizes mojibake) when the SVG bytes are not UTF-8, which real board
/// output never produces.
fn normalize_for_rasterization(svg_bytes: Vec<u8>) -> Vec<u8> {
    let svg_text = String::from_utf8(svg_bytes).unwrap_or_else(|e| {
        eprintln!("render error: svg input is not valid utf-8: {e}");
        std::process::exit(1);
    });
    chart_renderer::font_normalize::normalize_svg_font_families_for_rasterization(&svg_text)
        .into_bytes()
}

/// Write `bytes` to `out_arg` (stdout when `out_arg == "-"`, else the file)
/// — the same `-`-means-stdout convention `run_board` established.
fn write_output(out_arg: &str, bytes: &[u8]) {
    if out_arg == "-" {
        std::io::Write::write_all(&mut std::io::stdout(), bytes).expect("write to stdout");
    } else {
        std::fs::write(out_arg, bytes).expect("write output file");
    }
}

/// Load `--fonts-dir`'s TTFs into a fresh `fontdb::Database` for `usvg` to
/// shape text with — the same font FILES `text::FontBook::load_dir` loads
/// for the port's own measurement, into the font-matching structure `usvg`/
/// `resvg`/`svg2pdf` need instead (their own text layout, not this crate's).
///
/// HIGH 5: an omitted `--fonts-dir`, or one that resolves no faces, REFUSES
/// rather than handing `usvg` an empty database — an empty database has no
/// face to shape any `<text>` with, so `usvg`/`resvg` paint every glyph
/// invisible and still exit 0, a silent wrong result (a real `png`/`pdf` of
/// a chart board always carries text) rather than a loud failure. An
/// earlier version of this function's own doc comment claimed this
/// refusal without the code actually raising it.
fn build_fontdb(fonts_dir: Option<&str>) -> Result<usvg::fontdb::Database, String> {
    let Some(dir) = fonts_dir else {
        return Err(
            "png/pdf refuses to render with no --fonts-dir: an empty font database has \
             no face to shape any <text> with, which would silently paint every glyph \
             invisible instead of failing loudly"
                .to_string(),
        );
    };
    let mut db = usvg::fontdb::Database::new();
    db.load_fonts_dir(dir);
    if db.is_empty() {
        return Err(format!("png/pdf: --fonts-dir {dir:?} resolved no font faces"));
    }
    // Production rasterizes through vl-convert, which resolves the CSS generic
    // families (`monospace`, `serif`, `sans-serif`) onto real faces itself —
    // `init_font_db()`/`setup_default_fonts()` in vl-convert-rs's `text.rs`.
    // The vendored fonts dir above never claims those generic names, so
    // without this step `usvg` leaves them unresolved and drops the glyphs.
    // Port vl-convert's own algorithm: load the OS's fonts too, then run its
    // exact first-present-wins candidate cascades (no cursive/fantasy — it
    // doesn't set those either).
    db.load_system_fonts();
    set_default_generic_families(&mut db);
    Ok(db)
}

/// Transliteration of vl-convert-rs `text.rs::setup_default_fonts` (v1.9.0).
///
/// Deliberately incomplete relative to vl-convert's own `init_font_db`
/// (`text.rs:50-65`), which `load_font_data`s four vendored Liberation Sans
/// style files *before* running this cascade, "so that there is always a
/// fallback" for the sans-serif default even with zero system fonts — this
/// port relies entirely on `load_system_fonts()` (R3 review round, MEDIUM
/// 1). Left unvendored: the shipped images install no font package, so in them
/// the generic cascade finds nothing on EITHER side — parity holds
/// (the badge simply doesn't paint), and the gap only bites a host with
/// system fonts installed that still lacks Liberation Sans specifically.
fn set_default_generic_families(db: &mut usvg::fontdb::Database) {
    let families: std::collections::HashSet<String> = db
        .faces()
        .flat_map(|face| face.families.iter().map(|(fam, _lang)| fam.clone()))
        .collect();

    for family in ["Arial", "Helvetica", "Liberation Sans"] {
        if families.contains(family) {
            db.set_sans_serif_family(family);
            break;
        }
    }
    for family in ["Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"] {
        if families.contains(family) {
            db.set_monospace_family(family);
            break;
        }
    }
    for family in ["Times New Roman", "Times", "Liberation Serif", "DejaVu Serif"] {
        if families.contains(family) {
            db.set_serif_family(family);
            break;
        }
    }
}

/// `chart-renderer png <svg.svg|-> <out.png|-> [--scale N] [--fonts-dir DIR]`
/// — item (c). Parses the SVG with `usvg`, rasterizes with `resvg` at
/// `--scale` (default 1), and writes a PNG. `--fonts-dir` should be the same
/// directory `chart-renderer board`/the bare per-chart form load fonts from
/// (`get_fonts_dir()` in production) — `usvg` re-shapes every `<text>` run
/// from the SVG's own `font-family`/`font-size` attributes rather than
/// trusting the positions the SVG already baked in, so a missing or
/// mismatched font directory changes what text renders, not just whether
/// it's pixel-identical to the SVG source.
fn run_png(args: &[String]) {
    if args.len() < 2 {
        eprintln!(
            "usage: chart-renderer png <svg.svg|-> <out.png|-> [--scale N] [--fonts-dir DIR]"
        );
        std::process::exit(2);
    }
    let svg_arg = args[0].as_str();
    let out_arg = args[1].as_str();
    let scale: f32 = flag_value(args, "--scale")
        .map(|v| {
            v.parse().unwrap_or_else(|_| {
                eprintln!("error: --scale must be a number, got {v:?}");
                std::process::exit(2);
            })
        })
        .unwrap_or(1.0);
    let fonts_dir = flag_value(args, "--fonts-dir");

    let svg_data = normalize_for_rasterization(read_svg_input(svg_arg));
    let mut opt = usvg::Options::default();
    *opt.fontdb_mut() = build_fontdb(fonts_dir).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });
    let tree = usvg::Tree::from_data(&svg_data, &opt).unwrap_or_else(|e| {
        eprintln!("render error: {e}");
        std::process::exit(1);
    });

    let size = tree.size();
    let (px_w, px_h) = png_pixel_dimensions(size.width(), size.height(), scale);
    let mut pixmap = resvg::tiny_skia::Pixmap::new(px_w, px_h).unwrap_or_else(|| {
        eprintln!("render error: png dimensions {px_w}x{px_h} not representable");
        std::process::exit(1);
    });
    let transform = resvg::tiny_skia::Transform::from_scale(scale, scale);
    resvg::render(&tree, transform, &mut pixmap.as_mut());
    let png_bytes = pixmap.encode_png().expect("encode png");
    write_output(out_arg, &png_bytes);
}

/// MEDIUM: production's own PNG dimension law (`vlc.svg_to_png`, resvg
/// underneath) FLOORS `width * scale`/`height * scale` — empirically pinned
/// against the real `vl-convert` binary (`0 0 1200 571.15` at scale 1 comes
/// back `1200x571`, and `571.6`/`571.9` both still floor to `571`; every
/// tested fraction floors, none round). `ceil` (this port's own earlier
/// law) or `round` (the fixed test's own earlier, equally wrong,
/// assumption) both diverge from that on a fractional viewBox.
fn png_pixel_dimensions(width: f32, height: f32, scale: f32) -> (u32, u32) {
    let px_w = (width * scale).floor().max(1.0) as u32;
    let px_h = (height * scale).floor().max(1.0) as u32;
    (px_w, px_h)
}

/// `chart-renderer pdf <svg.svg|-> <out.pdf|-> [--fonts-dir DIR]` — item
/// (c). Same font-loading reasoning as `run_png`; `svg2pdf` embeds real
/// selectable text (`ConversionOptions::embed_text`, on by default) rather
/// than rasterizing it, so a missing font directory produces missing/wrong
/// glyphs, never a silent fallback.
fn run_pdf(args: &[String]) {
    if args.len() < 2 {
        eprintln!("usage: chart-renderer pdf <svg.svg|-> <out.pdf|-> [--fonts-dir DIR]");
        std::process::exit(2);
    }
    let svg_arg = args[0].as_str();
    let out_arg = args[1].as_str();
    let fonts_dir = flag_value(args, "--fonts-dir");

    let svg_data = normalize_for_rasterization(read_svg_input(svg_arg));
    let mut opt = usvg::Options::default();
    *opt.fontdb_mut() = build_fontdb(fonts_dir).unwrap_or_else(|e| {
        eprintln!("error: {e}");
        std::process::exit(1);
    });
    let tree = usvg::Tree::from_data(&svg_data, &opt).unwrap_or_else(|e| {
        eprintln!("render error: {e}");
        std::process::exit(1);
    });

    let pdf_bytes = svg2pdf::to_pdf(
        &tree,
        svg2pdf::ConversionOptions::default(),
        svg2pdf::PageOptions::default(),
    )
    .unwrap_or_else(|e| {
        eprintln!("render error: {e}");
        std::process::exit(1);
    });
    write_output(out_arg, &pdf_bytes);
}

/// `chart-renderer capitalize-dump` — prints the compiled `titlecase_
/// first_char` exception table as `{"<codepoint>": "<mapped>", ...}`, for
/// `chart_svg_dev/gen_titlecase_first_char.py`'s own probe to diff against
/// a fresh `str.capitalize()` computation — exercises the wired table, not
/// just the generated source file's text.
fn run_capitalize_dump() {
    let entries: Vec<_> = chart_renderer::titlecase_first_char::TITLECASE_FIRST_CHAR_EXCEPTIONS
        .iter()
        .map(|&(cp, s)| (cp.to_string(), s))
        .collect();
    print!("{}", serde_json::json!(entries.into_iter().collect::<std::collections::BTreeMap<_, _>>()));
}

/// `chart-renderer table-width <payload|->` — the width production's
/// `measure_table_natural_width` gives one table, as `{"natural_width": N}`
/// (`null` with nothing to hug). The payload is the table's resolved chart and
/// its rows (`MeasurePayload`); a table the engine does not draw refuses with
/// `render error: <message>` and a non-zero exit, like every other command.
fn run_table_width(args: &[String]) {
    if args.is_empty() {
        eprintln!("usage: chart-renderer table-width <payload.json|->");
        std::process::exit(2);
    }
    let raw = if args[0] == "-" {
        let mut buf = String::new();
        std::io::Read::read_to_string(&mut std::io::stdin(), &mut buf)
            .expect("read table-width payload from stdin");
        buf
    } else {
        std::fs::read_to_string(&args[0]).expect("read table-width payload")
    };
    let payload: MeasurePayload = serde_json::from_str(&raw).unwrap_or_else(|e| {
        eprintln!("render error: table-width payload does not match the engine contract: {e}");
        std::process::exit(1);
    });
    let fonts_base = if args[0] == "-" { "." } else { args[0].as_str() };
    let fonts = load_fonts(&payload.fonts_dir, fonts_base);
    let natural = measure_natural_width(&payload, &fonts).unwrap_or_else(|e| {
        eprintln!("render error: {e}");
        std::process::exit(1);
    });
    println!("{}", serde_json::json!({ "natural_width": natural }));
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 2 && args[1] == "custom-child" {
        chart_renderer::families::custom::child_main();
        return;
    }
    let own = std::env::current_exe().expect("the engine's own path");
    chart_renderer::sandbox::configure(chart_renderer::sandbox::Runner::Spawn(vec![
        own.to_string_lossy().into_owned(),
        "custom-child".to_string(),
    ]));
    if args.len() >= 2 && args[1] == "table-width" {
        run_table_width(&args[2..]);
        return;
    }
    if args.len() >= 2 && args[1] == "board" {
        run_board(&args[2..]);
        return;
    }
    if args.len() >= 2 && args[1] == "capitalize-dump" {
        run_capitalize_dump();
        return;
    }
    if args.len() >= 2 && args[1] == "case" {
        run_case(&args[2..]);
        return;
    }
    if args.len() >= 2 && args[1] == "png" {
        run_png(&args[2..]);
        return;
    }
    if args.len() >= 2 && args[1] == "pdf" {
        run_pdf(&args[2..]);
        return;
    }
    if args.len() < 3 {
        eprintln!(
            "usage: chart-renderer payload.json out.svg [--bench N] [--bench-parse N] \
             [--dump-layout <path|->] [--diagnostics <path>]"
        );
        eprintln!(
            "       chart-renderer board <payload|-> <out|-> [--report path] [--embed-fonts]"
        );
        eprintln!("       chart-renderer png <svg|-> <out|-> [--scale N] [--fonts-dir DIR]");
        eprintln!("       chart-renderer pdf <svg|-> <out|-> [--fonts-dir DIR]");
        eprintln!("       chart-renderer measure font-path size text");
        eprintln!("       chart-renderer table-width <payload|->");
        std::process::exit(2);
    }
    let raw = std::fs::read_to_string(&args[1]).expect("read payload");
    let mut payload: Payload = serde_json::from_str(&raw).expect("parse payload");
    let fonts = load_fonts(&payload.fonts_dir, &args[1]);
    payload.fonts_dir = resolved_fonts_dir(&payload.fonts_dir, &args[1]);
    let diagnostics_path = flag_value(&args, "--diagnostics");
    let write_diagnostics = |doc: serde_json::Value| {
        if let Some(path) = diagnostics_path {
            std::fs::write(path, doc.to_string()).expect("write diagnostics");
        }
    };
    let output = render_measured(&payload, &fonts).unwrap_or_else(|e| {
        write_diagnostics(serde_json::json!({ "error": e }));
        eprintln!("render error: {e}");
        std::process::exit(1);
    });
    write_diagnostics(serde_json::json!({ "warnings": output.warnings, "natural_height": output.natural_height }));
    let svg = output.svg;
    std::fs::write(&args[2], format!("{svg}\n")).expect("write svg");
    let dump_layout = flag_value(&args, "--dump-layout");
    // A dump to stdout keeps stdout pure JSON; the human line moves aside.
    if dump_layout == Some("-") {
        eprintln!("wrote {} ({} bytes)", args[2], svg.len());
    } else {
        println!("wrote {} ({} bytes)", args[2], svg.len());
    }
    if let Some(path) = dump_layout {
        let layout = chart_renderer::render_layout(&payload, &fonts).unwrap_or_else(|e| {
            eprintln!("layout dump error: {e}");
            std::process::exit(1);
        });
        let json = serde_json::to_string_pretty(&layout).expect("layout serializes");
        write_output(path, format!("{json}\n").as_bytes());
    }

    let flag_n =
        |name: &str| -> Option<usize> { flag_value(&args, name).and_then(|v| v.parse().ok()) };

    if let Some(n) = flag_n("--bench") {
        let _ = render_fitted(&payload, &fonts);
        let mut times = Vec::with_capacity(n);
        for _ in 0..n {
            let t0 = Instant::now();
            let out = render_fitted(&payload, &fonts).unwrap();
            times.push(t0.elapsed().as_secs_f64() * 1000.0);
            std::hint::black_box(out);
        }
        report("render only", &mut times);
    }
    if let Some(n) = flag_n("--bench-parse") {
        let mut times = Vec::with_capacity(n);
        for _ in 0..n {
            let t0 = Instant::now();
            let parsed: Payload = serde_json::from_str(&raw).unwrap();
            let out = render_fitted(&parsed, &fonts).unwrap();
            times.push(t0.elapsed().as_secs_f64() * 1000.0);
            std::hint::black_box(out);
        }
        report("parse + render", &mut times);
    }
}

fn report(label: &str, times: &mut [f64]) {
    times.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = times.len();
    let mean: f64 = times.iter().sum::<f64>() / n as f64;
    println!(
        "{label} x{n}: median {:.4}ms  mean {mean:.4}ms  min {:.4}ms",
        times[n / 2],
        times[0]
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flag_value_finds_the_argument_right_after_the_flag_name() {
        let args: Vec<String> = ["board", "in.json", "out.svg", "--report", "r.json"]
            .map(String::from)
            .to_vec();
        assert_eq!(flag_value(&args, "--report"), Some("r.json"));
        assert_eq!(flag_value(&args, "--scale"), None);
    }

    #[test]
    fn flag_value_at_the_end_with_no_value_is_none() {
        let args: Vec<String> = ["board", "in.json", "out.svg", "--report"]
            .map(String::from)
            .to_vec();
        assert_eq!(flag_value(&args, "--report"), None);
    }

    /// Item (c): PNG dimensions are the SVG's own `viewBox` scaled by
    /// `--scale` — pinned directly against `usvg`/`resvg` rather than only
    /// through the CLI (the Python subprocess contract test,
    /// `tests/test_png_pdf_cli.py`, covers the CLI's own arg/flag wiring).
    #[test]
    fn png_dimensions_are_the_viewbox_scaled_by_the_scale_flag() {
        let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><rect width="100" height="50" fill="red"/></svg>"#;
        let opt = usvg::Options::default();
        let tree = usvg::Tree::from_data(svg.as_bytes(), &opt).unwrap();
        let size = tree.size();
        let scale = 2.0_f32;
        let (px_w, px_h) = png_pixel_dimensions(size.width(), size.height(), scale);
        assert_eq!((px_w, px_h), (200, 100));
        let mut pixmap = resvg::tiny_skia::Pixmap::new(px_w, px_h).unwrap();
        resvg::render(
            &tree,
            resvg::tiny_skia::Transform::from_scale(scale, scale),
            &mut pixmap.as_mut(),
        );
        assert_eq!(pixmap.width(), 200);
        assert_eq!(pixmap.height(), 100);
    }

    /// MEDIUM regression: production's own PNG dimension law FLOORS a
    /// fractional `viewBox` height rather than ceiling it — pinned against
    /// `vl_convert.svg_to_png`'s real, empirically-observed behavior
    /// (`0 0 1200 571.15` at scale 1 comes back `1200x571`, never `572`).
    /// This case is chosen specifically because it DISCRIMINATES: `floor`
    /// gives 571, `ceil` gives 572, and `round`/`round-half-up` also give
    /// 571 here but would give 572 for `571.6` (a fraction this port's own
    /// earlier, wrong test assumption — `round(vb * scale)` — never tested
    /// against production for).
    #[test]
    fn png_dimensions_floor_a_fractional_viewbox_height_like_production() {
        assert_eq!(png_pixel_dimensions(1200.0, 571.15, 1.0), (1200, 571));
        assert_eq!(png_pixel_dimensions(1200.0, 571.6, 1.0), (1200, 571));
        assert_eq!(png_pixel_dimensions(300.6, 100.0, 2.0), (601, 200));
    }

    #[test]
    fn pdf_bytes_start_with_the_pdf_magic_number() {
        let svg = r#"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><rect width="100" height="50" fill="red"/></svg>"#;
        let opt = usvg::Options::default();
        let tree = usvg::Tree::from_data(svg.as_bytes(), &opt).unwrap();
        let pdf = svg2pdf::to_pdf(
            &tree,
            svg2pdf::ConversionOptions::default(),
            svg2pdf::PageOptions::default(),
        )
        .unwrap();
        assert!(pdf.starts_with(b"%PDF"), "{:?}", &pdf[..pdf.len().min(16)]);
    }

    /// HIGH 5: no `--fonts-dir` refuses by name rather than handing `usvg`
    /// an empty database (which would paint every glyph invisible and
    /// still exit 0).
    #[test]
    fn build_fontdb_refuses_with_no_fonts_dir() {
        let err = build_fontdb(None).unwrap_err();
        assert!(err.contains("--fonts-dir"), "{err}");
    }

    /// R2-H1: `usvg`'s generic CSS families (`monospace`/`serif`/
    /// `sans-serif`) must resolve onto a real face, not usvg's own
    /// unresolvable defaults (`"Courier New"` etc., absent from the
    /// vendored fonts dir) — production rasterizes through vl-convert,
    /// which runs this exact cascade (`setup_default_fonts` in
    /// vl-convert-rs's `text.rs`). Paints the same text once with
    /// `font-family="monospace"` and once with the family
    /// `Database::family_name` says that generic resolved to; the two
    /// must ink identically pixel-for-pixel, and neither may be blank.
    #[test]
    fn build_fontdb_resolves_generic_families_to_a_real_face() {
        let fonts_dir = concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../../src/dbt_charts/core/render/fonts"
        );
        let db = build_fontdb(Some(fonts_dir)).unwrap();
        let resolved = db.family_name(&usvg::fontdb::Family::Monospace).to_string();

        let ink = |family: &str| -> u32 {
            let svg = format!(
                r#"<svg xmlns="http://www.w3.org/2000/svg" width="300" height="60">
                     <rect width="300" height="60" fill="white"/>
                     <text x="5" y="35" font-family="{family}" font-size="28" fill="black">Hello World</text>
                   </svg>"#
            );
            let mut opt = usvg::Options::default();
            opt.fontdb = std::sync::Arc::new(db.clone());
            let tree = usvg::Tree::from_data(svg.as_bytes(), &opt).unwrap();
            let mut pixmap = resvg::tiny_skia::Pixmap::new(300, 60).unwrap();
            resvg::render(&tree, resvg::tiny_skia::Transform::identity(), &mut pixmap.as_mut());
            pixmap.data().chunks(4).filter(|px| px[3] > 0).count() as u32
        };

        let generic_ink = ink("monospace");
        let named_ink = ink(&resolved);
        assert!(generic_ink > 0, "font-family=\"monospace\" painted nothing");
        assert_eq!(generic_ink, named_ink);
    }

    /// HIGH 5: a `--fonts-dir` that resolves no faces (a typo'd or empty
    /// directory) refuses the same way an omitted flag does — never a
    /// silently textless render.
    #[test]
    fn build_fontdb_refuses_when_the_directory_has_no_font_files() {
        let tmp = std::env::temp_dir().join(format!(
            "chart-renderer-build-fontdb-test-{}",
            std::process::id()
        ));
        std::fs::create_dir_all(&tmp).unwrap();
        let err = build_fontdb(Some(&tmp.to_string_lossy())).unwrap_err();
        assert!(err.contains("no font faces"), "{err}");
        std::fs::remove_dir_all(&tmp).ok();
    }
}

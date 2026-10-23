//! Board title band — transliteration of `boards.py`'s `_render_title_svg`/
//! `_tagged_authoring_block` plus `render/sizing.py`'s `get_title_height`/
//! `title_line_box`.
//!
//! The title's own mdsvg `Style`/`FontSlots` are NOT re-derived here:
//! `markdown_style.py::title_renderer`/`get_compact_style` resolve dozens of
//! theme fields (font-selection logic identical in kind to
//! `board_font_face_css`, the documented seam `board.rs`'s module doc already
//! cites for `defs_styles_block`) — `board_sweep.py` calls the real
//! production function and forwards the exact `Style`/`FontSlots` it built,
//! serialized with `chart_svg_dev/mdsvg_diff.py`'s own `_style_to_json`/
//! `_fonts_to_json` helpers (see `docs/laws-board.md`'s title mapping table).
//! What IS ported here is everything downstream of that Style: Jinja
//! variable substitution, the case transform, markdown assembly, and the
//! authoring-tag wrapper.

use mdsvg::fonts::FontSlots;
use mdsvg::style::Style;
use mdsvg::types::{
    AnyBlock, Document, Heading, OrderedList, Paragraph, Span, SpanType, UnorderedList,
};
use serde_json::Value;

use crate::board::{padded_authoring_content, px};
use crate::embed_fonts::{record_painted_italics, PaintedItalics};
use crate::svg::num_full;
use crate::Padding;

/// The port of `boards.py::_resolve_jinja` (which delegates to
/// `compile/template/jinja.py::resolve_jinja_template(..., strict=False)`,
/// a real Jinja2 environment), restricted to the subset the board title/text
/// corpus and docs actually author: bare `{{ identifier }}` substitution,
/// `{{ identifier or 'literal' }}`, `{% if identifier %}...[{% else %}...]
/// {% endif %}`, and `{% raw %}...{% endraw %}`. Anything else — a filter, a
/// non-identifier `if` condition, `{% for %}`, nested `{% if %}` — needs a
/// real Jinja evaluator this composer doesn't have, and refuses by name
/// rather than approximating. An unresolved/falsy identifier stringifies via
/// `jinja_value_str`, matching `resolve_jinja_template(..., strict=False)`'s
/// own lenient-`Undefined` behavior (see `_jinja_truthy`/`jinja_value_str`).
pub fn resolve_jinja(template: &str, variables: &serde_json::Map<String, Value>) -> Result<String, String> {
    if !template.contains("{{") && !template.contains("{%") {
        return Ok(template.to_string());
    }
    // stop_tags is empty at the top level, so parse_jinja_nodes always
    // consumes the whole template (Ok((_, "", None))) or refuses.
    // Jinja2's `keep_trailing_newline=False` (the real environment's
    // default) strips exactly one trailing newline from the template
    // SOURCE before rendering -- never from a variable's value -- and the
    // no-tag passthrough above never reaches this line, matching
    // `resolve_jinja_template`'s own identical short-circuit before Jinja
    // ever sees the template.
    let template = template.strip_suffix('\n').unwrap_or(template);
    let (nodes, _rest, _terminator) = parse_jinja_nodes(template, &[])?;
    render_jinja_nodes(&nodes, variables)
}

/// One parsed unit of a `resolve_jinja` template.
enum JinjaNode<'a> {
    Text(&'a str),
    Var(JinjaExpr<'a>),
    If { cond: &'a str, then_branch: Vec<JinjaNode<'a>>, else_branch: Option<Vec<JinjaNode<'a>>> },
    Raw(&'a str),
}

/// The two `{{ ... }}` expression shapes this subset models.
enum JinjaExpr<'a> {
    Ident(&'a str),
    OrLiteral(&'a str, String),
}

/// Parses `rest` into a flat node list, stopping at end-of-input or at the
/// first `{% TAG %}` whose bare name is in `stop_tags` (that tag is
/// consumed and returned as the terminator, not included in `nodes`) —
/// the same shape `{% if %}` needs to find its own `else`/`endif` and hand
/// control back to its caller. A `{%`/`{{` with no matching close, or a tag
/// name outside `if`/`else`/`endif`/`raw`/`endraw`/`stop_tags`, refuses by
/// name.
fn parse_jinja_nodes<'a>(
    mut rest: &'a str,
    stop_tags: &[&str],
) -> Result<(Vec<JinjaNode<'a>>, &'a str, Option<&'a str>), String> {
    let mut nodes = Vec::new();
    loop {
        let var_pos = rest.find("{{");
        let tag_pos = rest.find("{%");
        let next_is_var = match (var_pos, tag_pos) {
            (Some(v), Some(t)) => v < t,
            (Some(_), None) => true,
            (None, Some(_)) => false,
            (None, None) => {
                if !rest.is_empty() {
                    nodes.push(JinjaNode::Text(rest));
                }
                return Ok((nodes, "", None));
            }
        };

        if next_is_var {
            let start = var_pos.expect("checked above");
            if start > 0 {
                nodes.push(JinjaNode::Text(&rest[..start]));
            }
            let after = &rest[start + 2..];
            let end = after
                .find("}}")
                .ok_or_else(|| "board title jinja expression unterminated".to_string())?;
            let expr_src = after[..end].trim();
            nodes.push(JinjaNode::Var(parse_jinja_expr(expr_src)?));
            rest = &after[end + 2..];
            continue;
        }

        let start = tag_pos.expect("checked above");
        if start > 0 {
            nodes.push(JinjaNode::Text(&rest[..start]));
        }
        let after = &rest[start + 2..];
        let end = after
            .find("%}")
            .ok_or_else(|| "board title jinja tag unterminated".to_string())?;
        let tag_src = after[..end].trim();
        rest = &after[end + 2..];
        let tag_name = tag_src.split_whitespace().next().unwrap_or("");

        if stop_tags.contains(&tag_name) {
            return Ok((nodes, rest, Some(tag_name)));
        }

        match tag_name {
            "if" => {
                let cond = tag_src["if".len()..].trim();
                let is_identifier = !cond.is_empty()
                    && cond.chars().next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
                    && cond.chars().all(|c| c.is_ascii_alphanumeric() || c == '_');
                if !is_identifier {
                    return Err(format!("board title jinja {{% if %}} condition not modeled: {{% {tag_src} %}}"));
                }
                let (then_branch, rest_after_then, term) = parse_jinja_nodes(rest, &["else", "endif"])?;
                rest = rest_after_then;
                let else_branch = match term {
                    Some("else") => {
                        let (else_nodes, rest_after_else, term2) = parse_jinja_nodes(rest, &["endif"])?;
                        if term2 != Some("endif") {
                            return Err("board title jinja {% if %} block missing {% endif %}".to_string());
                        }
                        rest = rest_after_else;
                        Some(else_nodes)
                    }
                    Some("endif") => None,
                    _ => return Err("board title jinja {% if %} block missing {% endif %}".to_string()),
                };
                nodes.push(JinjaNode::If { cond, then_branch, else_branch });
            }
            "raw" => {
                let (raw_content, rest_after_raw) = extract_raw_block(rest)?;
                nodes.push(JinjaNode::Raw(raw_content));
                rest = rest_after_raw;
            }
            other => return Err(format!("board title jinja {{% %}} block not modeled: {{% {other} %}}")),
        }
    }
}

/// Scans for the literal `{% endraw %}` tag (any internal whitespace),
/// treating everything up to it as un-parsed literal content — Jinja's
/// `{% raw %}` block does not recurse into nested tags.
fn extract_raw_block(rest: &str) -> Result<(&str, &str), String> {
    let mut search_from = 0usize;
    loop {
        let Some(tag_start) = rest[search_from..].find("{%") else {
            return Err("board title jinja {% raw %} block missing {% endraw %}".to_string());
        };
        let tag_start = search_from + tag_start;
        let Some(tag_end) = rest[tag_start + 2..].find("%}") else {
            return Err("board title jinja {% raw %} block missing {% endraw %}".to_string());
        };
        let tag_end = tag_start + 2 + tag_end;
        let tag_src = rest[tag_start + 2..tag_end].trim();
        if tag_src == "endraw" {
            return Ok((&rest[..tag_start], &rest[tag_end + 2..]));
        }
        search_from = tag_end + 2;
    }
}

/// Parses a trimmed `{{ ... }}` expression body into one of this subset's
/// two shapes, refusing by name for anything else (a filter, an `and`/`not`,
/// a numeric or attribute expression, …).
fn parse_jinja_expr(expr: &str) -> Result<JinjaExpr<'_>, String> {
    let is_identifier = |s: &str| {
        !s.is_empty()
            && s.chars().next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
            && s.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
    };
    if is_identifier(expr) {
        return Ok(JinjaExpr::Ident(expr));
    }
    if let Some((left, right)) = expr.split_once(" or ") {
        let left = left.trim();
        let right = right.trim();
        let quote = right.chars().next();
        if is_identifier(left) && matches!(quote, Some('\'') | Some('"')) {
            let q = quote.expect("matched above");
            if right.len() >= 2 && right.ends_with(q) {
                let literal = &right[1..right.len() - 1];
                return Ok(JinjaExpr::OrLiteral(left, literal.to_string()));
            }
        }
    }
    Err(format!("board title jinja expression not modeled: {{{{ {expr} }}}}"))
}

/// Jinja/Python truthiness on a resolved variable value: missing (`None`
/// here doubles as `LenientUndefined`) / `null` / `false` / `0` / `""` are
/// falsy; every other string/number/`true` is truthy. Anything beyond a
/// scalar (a list, a mapping) is not a shape any title/text ladder rung
/// authors and refuses by name rather than guessing.
fn jinja_truthy(v: Option<&Value>) -> Result<bool, String> {
    match v {
        None | Some(Value::Null) => Ok(false),
        Some(Value::Bool(b)) => Ok(*b),
        Some(Value::String(s)) => Ok(!s.is_empty()),
        Some(Value::Number(n)) => Ok(n.as_f64().is_some_and(|f| f != 0.0)),
        Some(other) => Err(format!("board title jinja variable of type {other:?} not modeled")),
    }
}

fn render_jinja_nodes(nodes: &[JinjaNode], variables: &serde_json::Map<String, Value>) -> Result<String, String> {
    let mut out = String::new();
    for node in nodes {
        match node {
            JinjaNode::Text(s) => out.push_str(s),
            JinjaNode::Raw(s) => out.push_str(s),
            JinjaNode::Var(JinjaExpr::Ident(name)) => {
                out.push_str(&jinja_value_str(variables.get(*name))?);
            }
            JinjaNode::Var(JinjaExpr::OrLiteral(name, literal)) => {
                let value = variables.get(*name);
                if jinja_truthy(value)? {
                    out.push_str(&jinja_value_str(value)?);
                } else {
                    out.push_str(literal);
                }
            }
            JinjaNode::If { cond, then_branch, else_branch } => {
                if jinja_truthy(variables.get(*cond))? {
                    out.push_str(&render_jinja_nodes(then_branch, variables)?);
                } else if let Some(else_branch) = else_branch {
                    out.push_str(&render_jinja_nodes(else_branch, variables)?);
                }
            }
        }
    }
    Ok(out)
}

/// Python's bare `str(value)` on a Jinja-substituted value: a string is
/// itself, a bool prints `True`/`False`, a whole number prints bare, an
/// undefined variable is the empty string. Anything else (a list, a mapping)
/// is not a shape any board title ladder rung authors and refuses by name
/// rather than guessing at `str()`'s output.
fn jinja_value_str(v: Option<&Value>) -> Result<String, String> {
    match v {
        None | Some(Value::Null) => Ok(String::new()),
        Some(Value::String(s)) => Ok(s.clone()),
        Some(Value::Bool(b)) => Ok(if *b { "True".to_string() } else { "False".to_string() }),
        Some(Value::Number(n)) => match n.as_i64() {
            Some(i) => Ok(i.to_string()),
            None => Ok(n.as_f64().map(|f| format!("{f}")).unwrap_or_default()),
        },
        Some(other) => Err(format!("board title jinja variable of type {other:?} not modeled")),
    }
}

/// `markdown_style.py::title_blocks`: the title's Jinja resolved, formatted as
/// the `# ` heading (the level/size baked into `style` already — see the
/// module doc), parsed, and the case applied to the parsed words.
fn resolved_title_blocks(
    title: &str,
    variables: &serde_json::Map<String, Value>,
    title_case: Option<&str>,
) -> Result<Document, String> {
    let mut blocks = mdsvg::parser::parse(&format!("# {}", resolve_jinja(title, variables)?));
    if let Some(case) = title_case {
        case_blocks(&mut blocks, case)?;
    }
    Ok(blocks)
}

/// `markdown_blocks.py::map_span_runs` with `markdown_style.py::_case_words`
/// as the run function: every run of spans (paragraph, heading, list item,
/// table cell, at any depth) is cased as one string (`apply_case_to_runs`).
/// Code spans and images keep their text: code is literal, and an image's
/// alt is not a word.
fn case_blocks(blocks: &mut [AnyBlock], case: &str) -> Result<(), String> {
    for block in blocks {
        match block {
            AnyBlock::Paragraph(Paragraph { spans }) | AnyBlock::Heading(Heading { spans, .. }) => {
                case_words(spans, case)?
            }
            AnyBlock::Blockquote(quote) => case_blocks(&mut quote.blocks, case)?,
            AnyBlock::UnorderedList(UnorderedList { items })
            | AnyBlock::OrderedList(OrderedList { items, .. }) => {
                for item in items {
                    case_words(&mut item.spans, case)?;
                    case_blocks(&mut item.children, case)?;
                }
            }
            AnyBlock::Table(table) => {
                for row in std::iter::once(&mut table.header).chain(&mut table.rows) {
                    for cell in &mut row.cells {
                        case_words(&mut cell.spans, case)?;
                    }
                }
            }
            AnyBlock::CodeBlock(_)
            | AnyBlock::HorizontalRule(_)
            | AnyBlock::ImageBlock(_)
            | AnyBlock::RawHtmlBlock(_) => {}
        }
    }
    Ok(())
}

fn case_words(spans: &mut [Span], case: &str) -> Result<(), String> {
    let mut words: Vec<&mut Span> = spans
        .iter_mut()
        .filter(|s| !matches!(s.span_type, SpanType::Code | SpanType::Image))
        .collect();
    let cased = display_text::case::apply_case_to_runs(
        &words.iter().map(|s| s.text.as_str()).collect::<Vec<_>>(),
        case,
    )?;
    for (span, text) in words.iter_mut().zip(cased) {
        span.text = text;
    }
    Ok(())
}

/// `svg_utils.render_title` (via `_render_title_svg`): render the board
/// title as an mdsvg heading. The renderer is driven directly rather than
/// through mdsvg's one-shot `render` so the fonts this title reached can be
/// read back off it: a title carries markdown, so it can be the only thing on a
/// board that paints italic. `sink` is the paint site's own
/// (`embed_fonts::record_painted_italics`); the title always paints in
/// `style.font_family` (`markdown_style.py::title_renderer` hands that family in).
pub fn render_title_svg(
    title: &str,
    variables: &serde_json::Map<String, Value>,
    title_case: Option<&str>,
    width: f64,
    style: Style,
    fonts: FontSlots,
    sink: Option<&PaintedItalics>,
) -> Result<String, String> {
    let blocks = resolved_title_blocks(title, variables, title_case)?;
    let family = style.font_family.clone();
    let renderer = mdsvg::renderer::SVGRenderer::new(style, fonts)?;
    let svg = renderer.render(&blocks, width, 0.0)?;
    record_painted_italics(sink, &family, &renderer.used_fonts());
    Ok(svg)
}

/// `boards.py::_paint_title_svg_fill`: force every `<text>` — and any
/// existing `style="...fill:..."` run inside it, since mdsvg paints a
/// code/link span in its own color even inside a heading — to `color`.
/// Inline `style="fill: ..."` is the only override that beats the `.md-
/// heading` class rule's own fill (CSS specificity), so a bare `fill="..."`
/// presentation attribute would be silently swallowed.
pub fn paint_title_svg_fill(svg: &str, color: &str) -> String {
    let color_escaped = print::xml_escape_attr(color);
    inject_missing_style(&rewrite_existing_fill(svg, &color_escaped), &color_escaped)
}

/// Pass 2 first (matching production's order is irrelevant — the two passes
/// never touch the same bytes on one `<text>` element): rewrite the value of
/// any `fill` declaration already inside a `style="..."` attribute.
fn rewrite_existing_fill(svg: &str, color_escaped: &str) -> String {
    let mut out = String::with_capacity(svg.len());
    let mut rest = svg;
    loop {
        let Some(rel_start) = rest.find(r#"style=""#) else {
            out.push_str(rest);
            break;
        };
        let value_start = rel_start + r#"style=""#.len();
        out.push_str(&rest[..value_start]);
        let Some(rel_end) = rest[value_start..].find('"') else {
            out.push_str(&rest[value_start..]);
            break;
        };
        let value = &rest[value_start..value_start + rel_end];
        out.push_str(&rewrite_fill_in_value(value, color_escaped));
        rest = &rest[value_start + rel_end..];
    }
    out
}

/// Replace the value of a `fill:` declaration inside one `style="..."`
/// attribute's content, preserving every other declaration. No `fill:`
/// present is a no-op (matching the production regex's own non-match case).
fn rewrite_fill_in_value(value: &str, color_escaped: &str) -> String {
    let Some(fill_at) = value.find("fill") else {
        return value.to_string();
    };
    let after_fill = &value[fill_at + "fill".len()..];
    let trimmed = after_fill.trim_start();
    let Some(after_colon) = trimmed.strip_prefix(':') else {
        return value.to_string();
    };
    let value_start = after_colon.trim_start();
    let leading_ws = after_colon.len() - value_start.len();
    let decl_end = after_fill.len() - trimmed.len() + 1 + leading_ws;
    let old_value_end = value_start.find(';').unwrap_or(value_start.len());
    format!(
        "{}{}{}",
        &value[..fill_at + "fill".len() + decl_end],
        color_escaped,
        &value_start[old_value_end..]
    )
}

/// Pass 1: inject `style="fill: {color}"` on every `<text` tag that does not
/// already carry a `style=` attribute anywhere before its closing `>`.
fn inject_missing_style(svg: &str, color_escaped: &str) -> String {
    let mut out = String::with_capacity(svg.len());
    let mut rest = svg;
    loop {
        let Some(idx) = rest.find("<text") else {
            out.push_str(rest);
            break;
        };
        out.push_str(&rest[..idx]);
        let tag_end = rest[idx..].find('>').map_or(rest.len(), |e| idx + e);
        let tag = &rest[idx..tag_end];
        if tag.contains("style=") {
            out.push_str("<text");
        } else {
            out.push_str(r#"<text style="fill: "#);
            out.push_str(color_escaped);
            out.push('"');
        }
        rest = &rest[idx + "<text".len()..];
    }
    out
}

/// `render/sizing.py::get_title_height`: mdsvg's own `measure()` on the same
/// markdown `render_title_svg` draws, at the same width and style.
pub fn get_title_height(
    title: &str,
    variables: &serde_json::Map<String, Value>,
    title_case: Option<&str>,
    width: f64,
    style: Style,
    fonts: FontSlots,
) -> Result<f64, String> {
    let blocks = resolved_title_blocks(title, variables, title_case)?;
    Ok(mdsvg::renderer::SVGRenderer::new(style, fonts)?.measure(&blocks, width, 0.0)?.height)
}

/// `markdown_style.py::title_max_content_width`: the width the title needs to
/// draw on one line, asked of the renderer and the markdown that paint it
/// (`resolved_title_blocks`, same style and fonts as `render_title_svg`).
pub fn title_max_content_width(
    title: &str,
    variables: &serde_json::Map<String, Value>,
    title_case: Option<&str>,
    style: Style,
    fonts: FontSlots,
) -> Result<f64, String> {
    let blocks = resolved_title_blocks(title, variables, title_case)?;
    mdsvg::renderer::SVGRenderer::new(style, fonts)?.max_content_width(&blocks)
}

/// `render/sizing.py::title_line_box`: the `(top, height)` of the title
/// text's own span inside a block of `block_height` — the level is always 1
/// (`board_title_markdown` always emits an h1; `title_renderer` bakes the
/// board's real level into the style's own h1 fields).
pub fn title_line_box(style: Style, fonts: FontSlots, block_height: f64) -> Result<(f64, f64), String> {
    Ok(mdsvg::renderer::SVGRenderer::new(style, fonts)?.heading_line_box(1, block_height))
}

/// `render/sizing.py::title_baseline_offset`: how far below a title block's
/// top edge its first baseline sits — asked of the renderer the title was
/// actually drawn with, on the blocks it draws at `width`
/// (`SVGRenderer::first_baseline`), so an inline image taller than the text
/// moves the baseline down as it does the drawn title.
pub fn title_baseline_offset(
    title: &str,
    variables: &serde_json::Map<String, Value>,
    title_case: Option<&str>,
    width: f64,
    style: Style,
    fonts: FontSlots,
) -> Result<f64, String> {
    let blocks = resolved_title_blocks(title, variables, title_case)?;
    mdsvg::renderer::SVGRenderer::new(style, fonts)?.first_baseline(&blocks, width)
}

/// `svg_utils.authored_attrs`: the `data-authored-path`/`data-authored-kind`
/// pair every authoring-tagged block carries.
pub fn authored_attrs(path: &str, kind: &str) -> String {
    format!(
        r#" data-authored-path="{}" data-authored-kind="{}""#,
        print::xml_escape_attr(path),
        print::xml_escape_attr(kind),
    )
}

/// `boards.py::_tagged_authoring_block`: a tagged authoring group whose own
/// box contains `content`'s padding — see the Python docstring for why
/// `content_x`/`content_y` and the group's translate are equal and opposite.
/// `attrs` already carries its own leading space (`authored_attrs`'s shape).
#[allow(clippy::too_many_arguments)]
pub fn tagged_authoring_block(
    content: &str,
    attrs: &str,
    content_x: f64,
    content_y: f64,
    content_width: f64,
    content_height: f64,
    pad_left: f64,
    pad_right: f64,
    top_padding: f64,
    line_box: Option<(f64, f64)>,
) -> String {
    let pad = Padding { left: pad_left, right: pad_right, top: top_padding, bottom: 0.0 };
    format!(
        r#"<g transform="translate({}, {})"{attrs}>{}</g>"#,
        num_full(px(content_x - pad.left)),
        num_full(px(content_y - pad.top)),
        padded_authoring_content(content, content_width, content_height, &pad, line_box),
    )
}

/// `render/sizing.py::compute_board_content_box`'s own return shape.
pub struct BoardContentBox {
    pub content_top: f64,
    pub non_layout_height: f64,
    pub gap_before_layout: f64,
}

/// `render/sizing.py::compute_board_content_box`. `variables_height` is 0
/// whenever `inline_band_height > 0` (the caller's own responsibility, same
/// as Python's docstring: "When > 0, title_height and variables_height are
/// 0") — this function does not special-case that itself.
#[allow(clippy::too_many_arguments)]
pub fn compute_board_content_box(
    title_height: f64,
    text_height: f64,
    variables_height: f64,
    inline_band_height: f64,
    gap: f64,
    has_layout_items: bool,
    card_padding: f64,
) -> BoardContentBox {
    let mut height = 0.0;
    let mut has_prev = false;
    let mut prev_is_title = false;
    if inline_band_height > 0.0 {
        height += inline_band_height;
        has_prev = true;
        prev_is_title = false;
    } else if title_height > 0.0 {
        height += title_height;
        has_prev = true;
        prev_is_title = true;
    }
    if text_height > 0.0 {
        if has_prev {
            // title -> text: 0 gap (heading_margin_bottom_px IS the rhythm).
            height += if prev_is_title { 0.0 } else { gap };
        }
        height += text_height;
        has_prev = true;
    }
    if variables_height > 0.0 {
        if has_prev {
            height += gap;
        }
        height += variables_height;
        has_prev = true;
    }
    let content_top = if has_prev { card_padding } else { 0.0 };
    let gap_before_layout = if has_layout_items && has_prev { gap } else { 0.0 };
    BoardContentBox {
        content_top,
        non_layout_height: height + content_top,
        gap_before_layout,
    }
}

/// The title's mdsvg `Style`/`FontSlots` exactly as `board_sweep.py` forwards
/// them (`title_renderer`, default theme, level 1), with the font paths
/// rebased onto this checkout's shipped fonts.
#[cfg(test)]
pub(crate) fn title_style_and_fonts() -> (Style, FontSlots) {
    let fonts_dir = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../../../src/dbt_charts/core/render/fonts"
    );
    let mut doc: Value =
        serde_json::from_str(include_str!("../../fixtures/title_inline_title_style.json"))
            .expect("fixture parses");
    for slot in doc["title_fonts"].as_object_mut().unwrap().values_mut() {
        if let Some(path) = slot.get_mut("path") {
            *path = Value::String(format!("{fonts_dir}/{}", path.as_str().unwrap()));
        }
    }
    (
        serde::Deserialize::deserialize(&doc["title_style"]).expect("style deserializes"),
        serde::Deserialize::deserialize(&doc["title_fonts"]).expect("fonts deserialize"),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vars(pairs: &[(&str, Value)]) -> serde_json::Map<String, Value> {
        pairs.iter().map(|(k, v)| (k.to_string(), v.clone())).collect()
    }

    /// The text a rendered title paints, run by run.
    fn painted_runs(title: &str, case: &str) -> Vec<String> {
        let (style, fonts) = title_style_and_fonts();
        let svg = render_title_svg(title, &vars(&[]), Some(case), 2000.0, style, fonts, None)
            .unwrap();
        let run = regex::Regex::new(r"<(?:tspan|text)[^>]*>([^<]+)</").unwrap();
        run.captures_iter(&svg).map(|c| c[1].to_string()).collect()
    }

    // Expected runs are production's: `map_span_runs(parse_markdown("# " + title),
    // _case_words)` (`markdown_style.py::title_blocks`) drawn by `title_renderer`
    // at width 2000; the list, quote and table texts are the same call's spans.

    #[test]
    fn title_case_leaves_code_and_images_literal_and_cases_the_rest_as_one_run() {
        let title = "v1.2 `rev usd` of [the docs here](https://example.com/a.b) \
            ![logo](data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=){width=20 height=20} summary";
        assert_eq!(
            painted_runs(title, "title"),
            ["v1.2 ", "rev usd", " of ", "the Docs Here", " ", "Summary"]
        );
    }

    #[test]
    fn title_case_treats_a_word_split_by_emphasis_as_one_word() {
        assert_eq!(
            painted_runs("re**venue** by *mon*th and **bold** the end", "title"),
            ["Re", "venue", " by ", "Mon", "th and ", "Bold", " the End"]
        );
    }

    #[test]
    fn title_case_reaches_lists_quotes_and_tables() {
        let blocks = resolved_title_blocks(
            "a **b**\n\n- item one `x`\n- two\n\n> quoted words\n\n\
             | h one | h two |\n|---|---|\n| cell a | cell b |",
            &vars(&[]),
            Some("title"),
        )
        .unwrap();
        let debug = format!("{blocks:?}");
        let texts = ["A ", "B", "Item One ", "x", "Two", "Quoted Words", "H One", "Cell B"];
        for text in texts {
            assert!(debug.contains(&format!("text: {text:?}")), "{text:?} in {debug}");
        }
    }

    #[test]
    fn a_case_that_changes_the_length_cases_each_run_on_its_own() {
        assert_eq!(
            painted_runs("stra\u{df}e **x** `\u{df}`", "upper"),
            ["STRASSE ", "X", " ", "\u{df}"]
        );
    }

    #[test]
    fn resolve_jinja_passes_plain_text_through() {
        assert_eq!(resolve_jinja("Sales Overview", &vars(&[])).unwrap(), "Sales Overview");
    }

    #[test]
    fn resolve_jinja_substitutes_a_bare_variable() {
        let variables = vars(&[("region", Value::String("APAC".to_string()))]);
        assert_eq!(resolve_jinja("Sales in {{ region }}", &variables).unwrap(), "Sales in APAC");
    }

    #[test]
    fn resolve_jinja_strips_one_trailing_newline_when_rendered() {
        // MEDIUM 3 (review round 1): matches Jinja2's `keep_trailing_
        // newline=False` default, which only fires once the template
        // actually renders through Jinja -- a tag-free template short-
        // circuits before this and keeps its own trailing newline.
        let variables = vars(&[("x", Value::String("A".to_string()))]);
        assert_eq!(resolve_jinja("Hello {{ x }}\n", &variables).unwrap(), "Hello A");
        assert_eq!(resolve_jinja("Hello\n", &vars(&[])).unwrap(), "Hello\n");
        // The strip is on the template source, not the rendered output: a
        // variable's own trailing newline survives, and a template whose
        // last character is a tag keeps the newline before it.
        let nl = vars(&[("x", Value::String("d\n".to_string()))]);
        assert_eq!(resolve_jinja("abc{{ x }}", &nl).unwrap(), "abcd\n");
        let cond = vars(&[("x", Value::Bool(true))]);
        assert_eq!(
            resolve_jinja("a\n{% if x %}b\n{% endif %}", &cond).unwrap(),
            "a\nb\n"
        );
    }

    #[test]
    fn resolve_jinja_undefined_variable_renders_empty() {
        assert_eq!(resolve_jinja("Sales in {{ region }}", &vars(&[])).unwrap(), "Sales in ");
    }

    #[test]
    fn resolve_jinja_refuses_a_filter() {
        let variables = vars(&[("region", Value::String("apac".to_string()))]);
        assert!(resolve_jinja("{{ region | upper }}", &variables).is_err());
    }

    #[test]
    fn resolve_jinja_if_block_renders_the_true_branch() {
        let variables = vars(&[("x", Value::Bool(true))]);
        assert_eq!(resolve_jinja("{% if x %}Y{% endif %}", &variables).unwrap(), "Y");
    }

    #[test]
    fn resolve_jinja_if_block_renders_nothing_when_falsy_with_no_else() {
        assert_eq!(resolve_jinja("{% if x %}Y{% endif %}", &vars(&[])).unwrap(), "");
    }

    #[test]
    fn resolve_jinja_if_else_block_picks_the_right_branch() {
        let template = "{% if region %}{{ region }}{% else %}Unknown{% endif %}";
        assert_eq!(
            resolve_jinja(template, &vars(&[("region", Value::String("West".to_string()))])).unwrap(),
            "West"
        );
        assert_eq!(resolve_jinja(template, &vars(&[])).unwrap(), "Unknown");
    }

    #[test]
    fn resolve_jinja_if_block_surrounding_text_is_untouched() {
        let variables = vars(&[("region", Value::String("West".to_string()))]);
        assert_eq!(
            resolve_jinja("{% if region %}{{ region }} Region {% endif %}Sales", &variables).unwrap(),
            "West Region Sales"
        );
        assert_eq!(
            resolve_jinja("{% if region %}{{ region }} Region {% endif %}Sales", &vars(&[])).unwrap(),
            "Sales"
        );
    }

    #[test]
    fn resolve_jinja_or_literal_falls_back_when_undefined() {
        assert_eq!(
            resolve_jinja("{{ segment or 'All segments' }}", &vars(&[])).unwrap(),
            "All segments"
        );
    }

    #[test]
    fn resolve_jinja_or_literal_falls_back_when_empty_string() {
        let variables = vars(&[("segment", Value::String(String::new()))]);
        assert_eq!(
            resolve_jinja("{{ segment or 'All segments' }}", &variables).unwrap(),
            "All segments"
        );
    }

    #[test]
    fn resolve_jinja_or_literal_keeps_a_truthy_value() {
        let variables = vars(&[("segment", Value::String("Enterprise".to_string()))]);
        assert_eq!(
            resolve_jinja("{{ segment or 'All segments' }}", &variables).unwrap(),
            "Enterprise"
        );
    }

    #[test]
    fn resolve_jinja_raw_block_passes_content_through_verbatim() {
        assert_eq!(
            resolve_jinja("{% raw %}literal {{ x }}{% endraw %}", &vars(&[])).unwrap(),
            "literal {{ x }}"
        );
    }

    #[test]
    fn resolve_jinja_refuses_a_for_loop() {
        assert!(resolve_jinja("{% for x in y %}{{ x }}{% endfor %}", &vars(&[])).is_err());
    }

    #[test]
    fn resolve_jinja_refuses_an_if_with_a_non_identifier_condition() {
        assert!(resolve_jinja("{% if x == 1 %}Y{% endif %}", &vars(&[])).is_err());
    }

    #[test]
    fn resolve_jinja_refuses_an_or_expression_with_a_non_identifier_left_side() {
        assert!(resolve_jinja("{{ 1foo or 'y' }}", &vars(&[])).is_err());
    }

    #[test]
    fn resolve_jinja_refuses_a_non_identifier_expression() {
        assert!(resolve_jinja("{{ 1foo }}", &vars(&[])).is_err());
    }

    #[test]
    fn resolve_jinja_stringifies_numbers_and_bools_like_python() {
        let variables = vars(&[
            ("n", serde_json::json!(5)),
            ("f", serde_json::json!(5.5)),
            ("b", Value::Bool(true)),
        ]);
        assert_eq!(resolve_jinja("{{ n }}", &variables).unwrap(), "5");
        assert_eq!(resolve_jinja("{{ f }}", &variables).unwrap(), "5.5");
        assert_eq!(resolve_jinja("{{ b }}", &variables).unwrap(), "True");
    }

    #[test]
    fn paint_title_svg_fill_injects_style_on_a_bare_text_element() {
        let svg = r#"<text x="0" y="10">hello</text>"#;
        assert_eq!(
            paint_title_svg_fill(svg, "#111"),
            r#"<text style="fill: #111" x="0" y="10">hello</text>"#
        );
    }

    #[test]
    fn paint_title_svg_fill_leaves_a_styled_run_with_no_fill_untouched() {
        let svg = r#"<text style="font-weight: 500">hello</text>"#;
        assert_eq!(paint_title_svg_fill(svg, "#111"), svg);
    }

    #[test]
    fn paint_title_svg_fill_overrides_an_existing_fill_in_a_mixed_style_run() {
        let svg = r#"<tspan style="fill: #be185d; font-family: mono">code</tspan>"#;
        assert_eq!(
            paint_title_svg_fill(svg, "#222"),
            r#"<tspan style="fill: #222; font-family: mono">code</tspan>"#
        );
    }

    #[test]
    fn compute_board_content_box_degenerates_with_no_title_or_text() {
        let box_ = compute_board_content_box(0.0, 0.0, 0.0, 0.0, 8.0, true, 16.0);
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (0.0, 0.0, 0.0));
    }

    #[test]
    fn compute_board_content_box_reserves_padding_and_gap_for_a_title() {
        let box_ = compute_board_content_box(40.0, 0.0, 0.0, 0.0, 8.0, true, 16.0);
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (16.0, 56.0, 8.0));
    }

    #[test]
    fn compute_board_content_box_no_gap_before_layout_without_layout_items() {
        let box_ = compute_board_content_box(40.0, 0.0, 0.0, 0.0, 8.0, false, 16.0);
        assert_eq!(box_.gap_before_layout, 0.0);
    }

    #[test]
    fn compute_board_content_box_title_to_text_is_a_zero_gap() {
        let box_ = compute_board_content_box(40.0, 20.0, 0.0, 0.0, 8.0, true, 16.0);
        // 40 (title) + 0 gap + 20 (text) + 16 (card_padding) = 76.
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (16.0, 76.0, 8.0));
    }

    #[test]
    fn compute_board_content_box_text_only_takes_the_normal_gap_to_layout() {
        let box_ = compute_board_content_box(0.0, 20.0, 0.0, 0.0, 8.0, true, 16.0);
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (16.0, 36.0, 8.0));
    }

    #[test]
    fn compute_board_content_box_adds_variables_after_text_with_a_normal_gap() {
        let box_ = compute_board_content_box(40.0, 20.0, 30.0, 0.0, 8.0, true, 16.0);
        // 40 (title) + 0 gap + 20 (text) + 8 gap + 30 (variables) + 16 (card_padding).
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (16.0, 114.0, 8.0));
    }

    #[test]
    fn compute_board_content_box_inline_band_replaces_title_and_variables() {
        let box_ = compute_board_content_box(0.0, 20.0, 0.0, 50.0, 8.0, true, 16.0);
        // 50 (inline band) + 8 gap (band -> text is a normal gap, not the
        // title-to-text zero gap) + 20 (text) + 16 (card_padding).
        assert_eq!((box_.content_top, box_.non_layout_height, box_.gap_before_layout), (16.0, 94.0, 8.0));
    }
}

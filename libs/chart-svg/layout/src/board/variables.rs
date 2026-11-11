//! The root's variables band: where each control sits and the markup that draws it. Production's laws,
//! cited per item: `render/variables_layout.py` (`lay_out_variables`, `_input_width`, `ornament_width`,
//! `ornament_gap`), `render/variables_strip.py` (`render_variables_strip_svg`, `_draw_control`,
//! `_draw_field`, `_draw_reset`, the glyphs) and `render/sizing.py` (`standalone_variables_band`).
//! What each control is and says is `bind::variables`'.
//!
//! A strip text is measured against Inter Variable whatever the board's font (`_text_width` reads
//! `get_font_measurer()` with no family), while a baseline centers on the font it is painted in
//! (`centered_baseline_offset`).
//!
//! [`standalone`] is the band's width and packing edge; [`band`] places the controls by
//! `lay_out_variables`'s rows and draws them as `variables_strip.py` does; [`inline`] is the band beside
//! the title under `style.variables.position: title-inline`: `resolve_title_variables_inline_widths`'
//! column split, `should_use_title_inline_band`'s fit (at most two rows, none wider than the column;
//! else `None` and the strip stacks under the title via [`standalone`]) and
//! `compute_title_variables_inline_baseline_layout`'s shared baseline.

use bind::font_stack::with_emoji;
use bind::variables::{Bounds, Control, Controls, Ornament, Sizing, Widget};
use board_model::facet::FontWeightValue;
use board_model::style::style::Style;
use board_model::style::variables::{VariablesPosition, VariablesStyle};
use d3rs::py_float::float_repr;
use diagnostic::{diag, Diagnostic};
use display_text::language::tr;
use measure::shaped::{Font, FontBook};
use scene::{Attrs, Chars, Child, Element, Escape, Node};

/// `WRAP_EPSILON`: a row that overflows by sub-pixel slop keeps its control.
const WRAP_EPSILON: f64 = 0.5;

/// `MIN_CONTROLS_FOR_RESET`: a lone filter is reset by clearing it.
const MIN_CONTROLS_FOR_RESET: usize = 2;

/// The edge a strip packs against.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Start,
    End,
}

impl Align {
    fn name(self) -> &'static str {
        match self {
            Align::Start => "start",
            Align::End => "end",
        }
    }
}

/// `standalone_variables_band`: the width and packing edge of a band on its own line. An untitled
/// board under `title-inline` keeps the edge its controls have beside a title; a titled one ran out
/// of room beside it (`inline`), so its strip flows from the leading edge under the title.
pub fn standalone(
    titled: bool,
    content_width: f64,
    card_padding: f64,
    style: &Style,
) -> (f64, Align) {
    if !titled && style.variables.position == VariablesPosition::TitleInline {
        ((content_width - 2.0 * card_padding).max(0.0), Align::End)
    } else {
        ((content_width - card_padding).max(0.0), Align::Start)
    }
}

/// `_MIN_TITLE_INLINE_TITLE_COLUMN_PX`: the title column never collapses below this.
const MIN_TITLE_COLUMN: f64 = 48.0;

/// `_TITLE_INLINE_TITLE_MAX_INNER_RATIO`: the title never takes more of the band than this.
const TITLE_MAX_INNER_RATIO: f64 = 0.8;

/// `_TITLE_INLINE_VARIABLE_MAX_ROWS`: the most rows the controls take beside a title.
const INLINE_MAX_ROWS: u32 = 2;

/// `resolve_title_variables_inline_widths`: `(title_w, vars_w)` of an `inner` wide band whose title
/// asks `one_line_width` (`title_max_content_width`) for one line.
fn inline_widths(inner: f64, one_line_width: f64, vars: &VariablesStyle) -> (f64, f64) {
    // A little breathing room, so the measured width does not wrap on sub-pixel jitter.
    let natural = if one_line_width > 0.0 {
        one_line_width + 8.0
    } else {
        MIN_TITLE_COLUMN
    };
    let title = natural
        .min(inner * TITLE_MAX_INNER_RATIO)
        .max(MIN_TITLE_COLUMN);
    let title = if vars.title_inline_title_max_width > 0.0 {
        title.min(vars.title_inline_title_max_width)
    } else {
        title
    };
    (title, (inner - title - vars.gap).max(1.0))
}

/// A title and its controls on one band: where each column sits and the strip.
pub struct Inline {
    /// The title column's width.
    pub title_width: f64,
    /// How far the title moves down so its first baseline is the controls' label baseline.
    pub title_dy: f64,
    /// The strip's offset from the title column's left edge.
    pub strip_dx: f64,
    pub strip_dy: f64,
    pub strip: Node,
    /// The band's height, with the theme's bottom pad.
    pub height: f64,
}

/// What `inline` reads of a drawn title.
pub struct Headline {
    /// The reserved height, at least the theme's minimum.
    pub height: f64,
    /// `title_baseline_offset`.
    pub baseline: f64,
    /// `title_max_content_width`.
    pub one_line_width: f64,
}

/// `should_use_title_inline_band` and `_render_title_variables_inline_band` for the root: the band
/// of a title and its controls when `position: title-inline` and the controls fit the column beside
/// the title in two rows, none wider than it; `None` when they do not, and the controls stack under
/// the title (`standalone`). `inner` is the content width less the card padding on both sides.
pub fn inline(
    controls: &Controls,
    title: &Headline,
    inner: f64,
    card_padding: f64,
    style: &Style,
    book: &FontBook,
) -> Result<Option<Inline>, Diagnostic> {
    let vars = &style.variables;
    if vars.position != VariablesPosition::TitleInline {
        return Ok(None);
    }
    let inner = inner.max(1.0);
    let (title_width, strip_width) = inline_widths(inner, title.one_line_width, vars);
    let layout = lay_out(&controls.list, strip_width, style, book)?;
    let placed = layout
        .boxes
        .iter()
        .map(|b| b.x + b.width)
        .chain(layout.reset.iter().map(|r| r.x + r.width));
    // Rows alone is no fit test: the first control of a row is placed whatever its width.
    if layout.rows > INLINE_MAX_ROWS || placed.fold(0.0, f64::max) > strip_width + WRAP_EPSILON {
        return Ok(None);
    }
    let (strip, strip_height) = band(controls, strip_width, Align::End, style, book)?;
    // `compute_title_variables_inline_baseline_layout`: both columns shift so the deeper baseline is
    // the shared one, and neither rises above the band's top.
    let strip_baseline = strip_height / 2.0
        + book
            .centered_baseline_offset(vars.font.family.as_str(), vars.font.size)
            .map_err(diag::err_internal)?;
    let target = title.baseline.max(strip_baseline);
    let (title_dy, strip_dy) = (target - title.baseline, target - strip_baseline);
    Ok(Some(Inline {
        title_width,
        title_dy,
        strip_dx: card_padding + title_width + vars.gap,
        strip_dy,
        strip,
        height: (title_dy + title.height).max(strip_dy + strip_height)
            + vars.title_inline_band_bottom_pad,
    }))
}

struct Placed {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    label_width: f64,
    label: String,
}

struct Reset {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

struct Layout {
    boxes: Vec<Placed>,
    reset: Option<Reset>,
    rows: u32,
    height: f64,
}

fn text_width(book: &FontBook, text: &str, size: f64) -> Result<f64, Diagnostic> {
    if text.is_empty() {
        return Ok(0.0);
    }
    let font = Font {
        family: "Inter Variable",
        size,
        weight: 400.0,
        italic: false,
    };
    book.raw_advance(text, &font).map_err(diag::err_internal)
}

fn ornament_width(widget: Widget, font_size: f64) -> f64 {
    match widget.ornament() {
        Ornament::Arrow => font_size,
        Ornament::Calendar => font_size * 0.85,
        Ornament::None => 0.0,
    }
}

/// `ornament_gap`: half an em between a value and its glyph.
fn ornament_gap(widget: Widget, font_size: f64) -> f64 {
    if ornament_width(widget, font_size) > 0.0 {
        font_size * 0.5
    } else {
        0.0
    }
}

/// `_input_width`: measured when content-driven, the theme's as a floor otherwise.
fn input_width(
    control: &Control,
    style: &Style,
    font_size: f64,
    book: &FontBook,
) -> Result<f64, Diagnostic> {
    let input = &style.variables.input;
    let (widths, padding) = (&input.widths, &input.padding);
    let sizing = control.widget.sizing();
    if sizing == Sizing::Checkbox {
        return Ok(widths.checkbox);
    }
    let value = text_width(book, &control.value, font_size)?;
    let boxed = value.max(font_size) + padding.left + padding.right;
    Ok(match sizing {
        Sizing::Slider => widths.range + style.variables.control_gap + value.max(widths.slider_value_min),
        Sizing::Content => boxed,
        Sizing::Number => widths.number.max(boxed),
        Sizing::Text | Sizing::Checkbox => widths.text.max(boxed),
    })
}

fn label_text(label: &str) -> Result<String, Diagnostic> {
    tr("controls.field_label", None, &[("label", label)]).map_err(diag::err_internal)
}

/// `lay_out_variables`: each control in document order into rows no wider than `width`, and the reset
/// item after the last when there are two or more.
fn lay_out(
    controls: &[Control],
    width: f64,
    style: &Style,
    book: &FontBook,
) -> Result<Layout, Diagnostic> {
    let vars = &style.variables;
    let (gap, row_height, font_size) = (vars.gap, vars.container_height, vars.font.size);
    let available = width.max(1.0);
    let (mut boxes, mut x, mut row) = (Vec::new(), 0.0, 0_u32);
    for control in controls {
        let label = label_text(&control.label)?;
        let label_width = text_width(book, &label, font_size)?;
        let control_width = label_width
            + vars.control_gap
            + input_width(control, style, font_size, book)?
            + ornament_gap(control.widget, font_size)
            + ornament_width(control.widget, font_size);
        // The first control of a row is placed wherever it lands: an overrun is visible, a dropped filter is not.
        if !boxes.is_empty() && x + control_width > available + WRAP_EPSILON {
            row += 1;
            x = 0.0;
        }
        boxes.push(Placed {
            x,
            y: f64::from(row) * (row_height + gap),
            width: control_width,
            height: row_height,
            label_width,
            label,
        });
        x += control_width + gap;
    }
    let reset = if controls.len() >= MIN_CONTROLS_FOR_RESET {
        let padding = &vars.input.padding;
        let width =
            text_width(book, &word("controls.reset")?, font_size)? + padding.left + padding.right;
        if x + width > available + WRAP_EPSILON {
            row += 1;
            x = 0.0;
        }
        Some(Reset {
            x,
            y: f64::from(row) * (row_height + gap),
            width,
            height: row_height,
        })
    } else {
        None
    };
    let rows = f64::from(row + 1);
    Ok(Layout {
        boxes,
        reset,
        rows: row + 1,
        height: rows * row_height + (rows - 1.0) * gap,
    })
}

fn word(id: &str) -> Result<String, Diagnostic> {
    tr(id, None, &[]).map_err(diag::err_internal)
}

/// `_row_offsets`: how far each row shifts to pack against the end edge, keyed by row `y`; never negative.
fn row_offset(layout: &Layout, y: f64, width: f64, align: Align) -> f64 {
    if align == Align::Start {
        return 0.0;
    }
    let ends = layout
        .boxes
        .iter()
        .map(|b| (b.y, b.x + b.width))
        .chain(layout.reset.iter().map(|r| (r.y, r.x + r.width)));
    // The last box of a row decides, as production's dict overwrites.
    let end = ends
        .filter(|(row, _)| *row == y)
        .last()
        .map_or(0.0, |(_, end)| end);
    (width - end).max(0.0)
}

/// `svg_utils.px`: a coordinate snapped to a whole pixel, ties to even.
fn px(v: f64) -> String {
    format!("{}", v.round_ties_even() as i64)
}

/// `_number_attr`: an authored bound exactly, never `:g`'s six digits.
fn number_attr(v: f64) -> String {
    if v.fract() == 0.0 {
        format!("{}", v as i64)
    } else {
        float_repr(v)
    }
}

fn weight_css(weight: &FontWeightValue) -> String {
    match weight {
        FontWeightValue::Number(n) => number_attr(*n),
        FontWeightValue::Name(name) => name.clone(),
    }
}

fn el(tag: &str, attrs: Attrs, children: Vec<Element>) -> Element {
    Element {
        tag: tag.to_string(),
        attrs,
        children: children.into_iter().map(Child::Element).collect(),
    }
}

fn text_el(attrs: Attrs, content: &str) -> Element {
    Element {
        tag: "text".to_string(),
        attrs,
        children: vec![Child::Text(Chars::new(content, Escape::Attr))],
    }
}

/// What the painter reads off the style once.
struct Ink<'a> {
    style: &'a Style,
    book: &'a FontBook,
    font_size: f64,
    label_family: String,
    value_family: String,
}

impl Ink<'_> {
    fn vars(&self) -> &VariablesStyle {
        &self.style.variables
    }

    /// `centered_baseline_offset` of a font stack, the stack as the cascade holds it.
    fn centered(&self, family: &str) -> Result<f64, Diagnostic> {
        self.book
            .centered_baseline_offset(family, self.font_size)
            .map_err(diag::err_internal)
    }
}

/// The band: the `<g data-dbt-variables>` group and its height. `controls` is not empty.
pub fn band(
    controls: &Controls,
    width: f64,
    align: Align,
    style: &Style,
    book: &FontBook,
) -> Result<(Node, f64), Diagnostic> {
    let vars = &style.variables;
    let layout = lay_out(&controls.list, width, style, book)?;
    let ink = Ink {
        style,
        book,
        font_size: vars.font.size,
        label_family: with_emoji(vars.label.font.family.as_str(), &style.font.emoji),
        value_family: with_emoji(vars.value.font.family.as_str(), &style.font.emoji),
    };
    let mut drawn = Vec::new();
    for (control, placed) in controls.list.iter().zip(&layout.boxes) {
        drawn.push(draw_control(
            &ink,
            control,
            placed,
            row_offset(&layout, placed.y, width, align),
        )?);
    }
    if let Some(reset) = &layout.reset {
        drawn.push(draw_reset(
            &ink,
            reset,
            row_offset(&layout, reset.y, width, align),
            controls.reset_enabled,
        )?);
    }
    // The anchor sits outside the control group, transparent: only fields carry a background.
    let anchor = el(
        "rect",
        Attrs::new()
            .set("data-dbt-variables-box", "true")
            .set("data-dbt-align", align.name())
            .set("class", "dbt-pointer-inert")
            .set("x", "0")
            .set("y", "0")
            .set("width", px(width))
            .set("height", px(layout.height))
            .set("fill", "none"),
        vec![],
    );
    let group = el(
        "g",
        Attrs::new().set("data-dbt-variables-static", "true"),
        drawn,
    );
    let attrs = Attrs::new().set("data-dbt-variables", "true").set(
        "style",
        format!(
            "--dbt-variable-field-border: {}",
            vars.border.color.as_str()
        ),
    );
    Ok((
        Node::element(el("g", attrs, vec![anchor, group])),
        layout.height,
    ))
}

fn draw_control(
    ink: &Ink,
    control: &Control,
    placed: &Placed,
    dx: f64,
) -> Result<Element, Diagnostic> {
    let vars = ink.vars();
    let x = placed.x + dx;
    let field_x = x + placed.label_width + vars.control_gap;
    let field_width = (placed.width - placed.label_width - vars.control_gap).max(0.0);
    let field_height = vars.input.height;
    let field_y = placed.y + (placed.height - field_height) / 2.0;
    let baseline =
        placed.y + placed.height / 2.0 + ink.centered(vars.label.font.family.as_str())?;
    let label = text_el(
        Attrs::new()
            .set("data-authored-kind", "label")
            .set("x", px(x))
            .set("y", px(baseline))
            .set("font-size", px(ink.font_size))
            .set("font-weight", weight_css(&vars.label.font.weight))
            .set("font-family", ink.label_family.as_str())
            .set("fill", vars.label.font.color.as_str()),
        &placed.label,
    );
    let mut parts = vec![label];
    parts.extend(draw_field(
        ink,
        control,
        field_x,
        field_y,
        field_width,
        field_height,
    )?);

    let name = control.name.as_str();
    let attrs = Attrs::new()
        .set("data-dbt-variable", name)
        .set("data-authored-path", format!("variables.{name}"))
        .set("data-authored-kind", "variable")
        .set("data-dbt-input", control.widget.name())
        .set_if(
            control.widget == Widget::Checkbox,
            "data-dbt-checked",
            if control.checked { "true" } else { "false" },
        );
    let attrs = attrs.set("data-dbt-value", control.committed.as_str());
    let mut attrs = match control.bounds {
        Bounds::None => attrs,
        Bounds::Slider { min, max, step, .. } => attrs
            .set("data-dbt-min", number_attr(min))
            .set("data-dbt-max", number_attr(max))
            .set("data-dbt-step", number_attr(step)),
        Bounds::Authored { min, max, step } => [("min", min), ("max", max), ("step", step)]
            .into_iter()
            .filter_map(|(bound, value)| value.map(|v| (bound, v)))
            .fold(attrs, |attrs, (bound, v)| {
                attrs.set(format!("data-dbt-{bound}"), number_attr(v))
            }),
    };
    if let Some(hint) = &control.placeholder {
        attrs = attrs.set("data-dbt-placeholder", hint.as_str());
    }
    if let Some(unset) = &control.unset {
        attrs = attrs.set(
            "data-dbt-can-unset",
            if unset.can { "true" } else { "false" },
        );
        if unset.can {
            attrs = attrs.set("data-dbt-unset-label", unset.label.as_str());
        }
    }
    attrs = attrs
        .set("data-dbt-x", px(x))
        .set("data-dbt-y", px(placed.y))
        .set("data-dbt-width", px(placed.width))
        .set("data-dbt-height", px(placed.height));
    Ok(el("g", attrs, parts))
}

/// `_draw_reset`: hidden at rest, drawn so a static export keeps the gap a live board fills; dimmed
/// in the placeholder ink while every filter it would reset is at its default.
fn draw_reset(ink: &Ink, reset: &Reset, dx: f64, enabled: bool) -> Result<Element, Diagnostic> {
    let vars = ink.vars();
    let x = reset.x + dx;
    let color = if enabled {
        &vars.value.font.color
    } else {
        &vars.placeholder.font.color
    };
    let field_y = reset.y + (reset.height - vars.input.height) / 2.0;
    let baseline = reset.y + reset.height / 2.0 + ink.centered(vars.value.font.family.as_str())?;
    let rect = el(
        "rect",
        Attrs::new()
            .set("x", px(x))
            .set("y", px(field_y))
            .set("width", px(reset.width))
            .set("height", px(vars.input.height))
            .set("rx", px(vars.input.border.radius))
            .set("fill", vars.input.background.as_str())
            .set("stroke", "none"),
        vec![],
    );
    let text = text_el(
        Attrs::new()
            .set("x", px(x + vars.input.padding.left))
            .set("y", px(baseline))
            .set("font-size", px(ink.font_size))
            .set("font-family", ink.value_family.as_str())
            .set("fill", color.as_str()),
        &word("controls.reset")?,
    );
    let attrs = Attrs::new()
        .set("data-dbt-reset", "true")
        .set_if(!enabled, "data-dbt-enabled", "false")
        .set("opacity", "0");
    let mut group = el("g", attrs, vec![rect]);
    group.children.push(Child::Element(text));
    Ok(group)
}

/// `_draw_field`: a checkbox and a slider are shapes, the rest are fields.
fn draw_field(
    ink: &Ink,
    control: &Control,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<Vec<Element>, Diagnostic> {
    match control.widget.sizing() {
        Sizing::Checkbox => Ok(draw_checkbox(ink, control, x, y)),
        Sizing::Slider => draw_slider(ink, control, x, y, height),
        _ => draw_text_field(ink, control, x, y, width, height),
    }
}

/// A rounded field showing the committed value (or, muted, the hint standing in for one), plus its
/// type ornament.
fn draw_text_field(
    ink: &Ink,
    control: &Control,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<Vec<Element>, Diagnostic> {
    let vars = ink.vars();
    let baseline = y + height / 2.0 + ink.centered(vars.value.font.family.as_str())?;
    let color = if control.hint {
        &vars.placeholder.font.color
    } else {
        &vars.value.font.color
    };
    let mut parts = vec![
        el(
            "rect",
            Attrs::new()
                .set("data-dbt-field", "input")
                .set("x", px(x))
                .set("y", px(y))
                .set("width", px(width))
                .set("height", px(height))
                .set("rx", px(vars.input.border.radius))
                .set("fill", vars.input.background.as_str())
                .set("stroke", "none"),
            vec![],
        ),
        text_el(
            Attrs::new()
                .set("x", px(x + vars.input.padding.left))
                .set("y", px(baseline))
                .set("font-size", px(ink.font_size))
                .set("font-family", ink.value_family.as_str())
                .set("fill", color.as_str()),
            &control.value,
        ),
    ];
    let mid = y + height / 2.0;
    match control.widget.ornament() {
        Ornament::Arrow => parts.push(draw_arrow(ink, x + width, mid)),
        Ornament::Calendar => parts.push(draw_calendar(ink, x + width, mid)),
        Ornament::None => {}
    }
    Ok(parts)
}

/// `_draw_checkbox`: the one field that keeps its outline at rest, since the box is the value.
fn draw_checkbox(ink: &Ink, control: &Control, x: f64, y: f64) -> Vec<Element> {
    let vars = ink.vars();
    let size = vars.input.widths.checkbox;
    let top = y + (vars.input.height - size) / 2.0;
    let mut parts = vec![el(
        "rect",
        Attrs::new()
            .set("data-dbt-field", "checkbox")
            .set("data-dbt-ornament", "checkbox")
            .set("x", px(x))
            .set("y", px(top))
            .set("width", px(size))
            .set("height", px(size))
            .set("rx", px(vars.input.border.radius))
            .set("fill", vars.input.background.as_str())
            .set("stroke", vars.border.color.as_str())
            .set("stroke-width", "1"),
        vec![],
    )];
    if control.checked {
        let d = format!(
            "M{} {} L{} {} L{} {}",
            px(x + size * 0.25),
            px(top + size * 0.5),
            px(x + size * 0.45),
            px(top + size * 0.7),
            px(x + size * 0.75),
            px(top + size * 0.3)
        );
        parts.push(el(
            "path",
            Attrs::new()
                .set("d", d)
                .set("fill", "none")
                .set("stroke", vars.value.font.color.as_str())
                .set("stroke-width", "1.5")
                .set("stroke-linecap", "round")
                .set("stroke-linejoin", "round"),
            vec![],
        ));
    }
    parts
}

/// `_draw_slider`: a track with a thumb at the committed value, and the value beside it.
fn draw_slider(
    ink: &Ink,
    control: &Control,
    x: f64,
    y: f64,
    height: f64,
) -> Result<Vec<Element>, Diagnostic> {
    let vars = ink.vars();
    let Bounds::Slider { fraction, .. } = control.bounds else {
        return Err(diag::err_internal("a slider carries its bounds"));
    };
    let track = vars.input.widths.range;
    let mid = y + height / 2.0;
    let thumb = x + track * fraction;
    let baseline = mid + ink.centered(vars.value.font.family.as_str())?;
    Ok(vec![
        el(
            "rect",
            Attrs::new()
                .set("data-dbt-field", "slider")
                .set("data-dbt-ornament", "slider")
                .set("x", px(x))
                .set("y", px(mid - 2.0))
                .set("width", px(track))
                .set("height", "4")
                .set("rx", "2")
                .set("fill", vars.input.background.as_str())
                .set("stroke", "none"),
            vec![],
        ),
        el(
            "circle",
            Attrs::new()
                .set("data-dbt-ornament", "slider-thumb")
                .set("cx", px(thumb))
                .set("cy", px(mid))
                .set("r", "6")
                .set("fill", vars.value.font.color.as_str()),
            vec![],
        ),
        text_el(
            Attrs::new()
                .set("x", px(x + track + vars.control_gap))
                .set("y", px(baseline))
                .set("font-size", px(ink.font_size))
                .set("font-family", ink.value_family.as_str())
                .set("fill", vars.value.font.color.as_str()),
            &control.value,
        ),
    ])
}

/// `_draw_arrow`: the chevron that marks a dropdown, in its reserved width, hidden at rest.
fn draw_arrow(ink: &Ink, right: f64, mid: f64) -> Element {
    let vars = ink.vars();
    let size = ornament_width(Widget::Select, ink.font_size);
    let x = right - vars.input.padding.right - size;
    let drop = size * 0.32;
    let d = format!(
        "M{} {} L{} {} L{} {}",
        px(x),
        px(mid - drop / 2.0),
        px(x + size / 2.0),
        px(mid + drop / 2.0),
        px(x + size),
        px(mid - drop / 2.0)
    );
    el(
        "path",
        Attrs::new()
            .set("data-dbt-ornament", "arrow")
            .set("opacity", "0")
            .set("d", d)
            .set("fill", "none")
            .set("stroke", vars.placeholder.font.color.as_str())
            .set("stroke-width", "1.5")
            .set("stroke-linecap", "round")
            .set("stroke-linejoin", "round"),
        vec![],
    )
}

/// `_draw_calendar`: the glyph that marks a date picker, hidden at rest.
fn draw_calendar(ink: &Ink, right: f64, mid: f64) -> Element {
    let vars = ink.vars();
    let size = ornament_width(Widget::Date, ink.font_size);
    let x = right - vars.input.padding.right - size;
    let top = mid - size / 2.0;
    let frame = el(
        "rect",
        Attrs::new()
            .set("x", px(x))
            .set("y", px(top + size * 0.12))
            .set("width", px(size))
            .set("height", px(size * 0.88))
            .set("rx", px(size * 0.15)),
        vec![],
    );
    let line = el(
        "line",
        Attrs::new()
            .set("x1", px(x))
            .set("y1", px(top + size * 0.4))
            .set("x2", px(x + size))
            .set("y2", px(top + size * 0.4)),
        vec![],
    );
    el(
        "g",
        Attrs::new()
            .set("data-dbt-ornament", "calendar")
            .set("opacity", "0")
            .set("fill", "none")
            .set("stroke", vars.placeholder.font.color.as_str())
            .set("stroke-width", "1.25"),
        vec![frame, line],
    )
}

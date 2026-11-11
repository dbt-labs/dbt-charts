//! The board's root `<svg>`: its identity attributes and the theme facts the hover runtime reads off
//! it (`boards.py::_render_board_svg`, `chart_interactivity.py::hover_runtime_attributes`).

use board_model::facet::FontWeightValue;
use d3rs::py_float::float_repr;
use diagnostic::Diagnostic;
use layout::board::assemble::StylePatch;
use scene::{Attrs, Chars, Child, Element, Escape, Node, Role, Shape};

use layout::board::assemble::leaf;

/// The root's attributes, the hover facts and the page title included.
pub(crate) fn root_attrs(style: &StylePatch, family: &str, page_title: &str, rendered_at: &jiff::Timestamp) -> Result<Attrs, Diagnostic> {
    let background = leaf(style.background.as_ref(), "style.background")?.as_str();
    Ok(Attrs::new()
        .set("preserveAspectRatio", "xMinYMin meet")
        .set("style", "display: block;")
        .set("data-rendered-at", rendered_at.strftime("%Y-%m-%dT%H:%M:%SZ").to_string())
        .set("data-dbt-page-title", page_title)
        .set("data-dbt-font-family", family)
        .set("data-dbt-tooltip-style", tooltip_json(style)?)
        .set("data-dbt-hover-emphasis", hover_json(style)?)
        .set("data-dbt-page-background", background)
        .set("aria-label", page_title))
}

/// `defs > style`: the page's stylesheet.
pub(crate) fn defs(stylesheet: &str) -> Node {
    let style = Element {
        tag: "style".to_string(),
        attrs: Attrs::new().set("type", "text/css"),
        children: vec![Child::Text(Chars::new(stylesheet, Escape::Attr))],
    };
    Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![Node::element(style)]))
}

/// Python's `json.dumps` of an object: `", "` and `": "` separators; each value is already text.
fn object(entries: &[(&str, String)]) -> String {
    let body: Vec<String> = entries.iter().map(|(k, v)| format!("\"{k}\": {v}")).collect();
    format!("{{{}}}", body.join(", "))
}

fn text(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            c if (' '..='~').contains(&c) => out.push(c),
            c => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out + "\""
}

fn num(v: f64) -> String {
    float_repr(v)
}

fn weight(w: &FontWeightValue) -> String {
    match w {
        FontWeightValue::Name(name) => text(name),
        FontWeightValue::Number(n) => num(*n),
    }
}

/// `_build_tooltip_style_dict`.
fn tooltip_json(style: &StylePatch) -> Result<String, Diagnostic> {
    let t = &style.charts.tooltip;
    let p = &t.padding;
    let slot = |slot: &board_model::style::axis::TooltipSlotStylePatch, name: &str| -> Result<String, Diagnostic> {
        let font = &slot.font;
        Ok(object(&[(
            "font",
            object(&[
                ("color", text(leaf(font.color.as_ref(), &format!("style.charts.tooltip.{name}.font.color"))?.as_str())),
                ("weight", weight(leaf(font.weight.as_ref(), &format!("style.charts.tooltip.{name}.font.weight"))?)),
            ]),
        )]))
    };
    Ok(object(&[
        ("background", text(leaf(t.background.as_ref(), "style.charts.tooltip.background")?.as_str())),
        ("lineHeight", num(leaf(t.line_height, "style.charts.tooltip.line_height")?)),
        ("maxWidth", num(leaf(t.max_width, "style.charts.tooltip.max_width")?)),
        ("gap", num(leaf(t.gap, "style.charts.tooltip.gap")?)),
        ("font", object(&[("size", num(leaf(t.font.size, "style.charts.tooltip.font.size")?))])),
        (
            "padding",
            object(&[
                ("top", num(leaf(p.top, "style.charts.tooltip.padding.top")?)),
                ("bottom", num(leaf(p.bottom, "style.charts.tooltip.padding.bottom")?)),
                ("left", num(leaf(p.left, "style.charts.tooltip.padding.left")?)),
                ("right", num(leaf(p.right, "style.charts.tooltip.padding.right")?)),
            ]),
        ),
        ("label", slot(&t.label, "label")?),
        ("value", slot(&t.value, "value")?),
        (
            "border",
            object(&[
                ("color", text(leaf(t.border.color.as_ref(), "style.charts.tooltip.border.color")?.as_str())),
                ("width", num(leaf(t.border.width, "style.charts.tooltip.border.width")?)),
                ("radius", num(leaf(t.border.radius, "style.charts.tooltip.border.radius")?)),
            ]),
        ),
        ("shadow", object(&[("visible", leaf(t.shadow.visible, "style.charts.tooltip.shadow.visible")?.to_string())])),
        (
            "swatch",
            object(&[
                ("size", num(leaf(t.swatch.size, "style.charts.tooltip.swatch.size")?)),
                ("radius", num(leaf(t.swatch.radius, "style.charts.tooltip.swatch.radius")?)),
            ]),
        ),
        ("activeMarker", text(leaf(t.active_marker, "style.charts.tooltip.active_marker")?.as_str())),
        ("maxRows", leaf(t.max_rows, "style.charts.tooltip.max_rows")?.to_string()),
        ("groupLimit", leaf(t.group_limit, "style.charts.tooltip.group_limit")?.to_string()),
    ]))
}

/// `_build_hover_emphasis_dict`.
fn hover_json(style: &StylePatch) -> Result<String, Diagnostic> {
    let h = &style.charts.hover_emphasis;
    Ok(object(&[
        ("visible", leaf(h.visible, "style.charts.hover_emphasis.visible")?.to_string()),
        ("recedeContrast", num(leaf(h.recede_contrast, "style.charts.hover_emphasis.recede_contrast")?)),
        ("dropLineColor", text(leaf(h.drop_line_color.as_ref(), "style.charts.hover_emphasis.drop_line_color")?.as_str())),
        ("dropLineWidth", num(leaf(h.drop_line_width, "style.charts.hover_emphasis.drop_line_width")?)),
    ]))
}

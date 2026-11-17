//! A chart card's chrome, as `chart/rendering.py::_wrap_rendered_chart_svg` lays it around the card's
//! content: the board's drop shadow under the card (`svg_utils.shadow_rect`), the content clipped to
//! the card's corner radius (`round_clipped`), and the card's border over it (`border_rect`).

use board_model::primitives::{BorderStyle, BoxShadow};
use d3rs::py_float::{float_repr, format_g};
use diagnostic::{diag, Diagnostic};
use scene::{Attrs, Node, Role, Shape};

/// The board's shadow under one card: the shadow, the canvas the card's box is filled with (so the
/// shadow falls outside the box and the card's own paint covers the rest), and the box's radius.
pub struct Shadow<'a> {
    pub shadow: &'a BoxShadow,
    pub fill: &'a str,
    pub radius: f64,
}

/// `primitives.py::parse_box_shadow`: `<x> <y> [<blur>] <color>`, with blur 0 when omitted. The
/// facet already matched `BOX_SHADOW_PATTERN`, and no color it names starts with a digit.
fn parts(shadow: &BoxShadow) -> Result<(f64, f64, f64, &str), Diagnostic> {
    let length = |token: &str| token.trim_end_matches("px").parse::<f64>().ok();
    let malformed = || diag::err_internal(format!("box_shadow {:?} is not `<x> <y> [<blur>] <color>`", shadow.as_str()));
    let mut tokens = shadow.as_str().splitn(3, ' ');
    let (dx, dy, rest) = match (tokens.next().and_then(length), tokens.next().and_then(length), tokens.next()) {
        (Some(dx), Some(dy), Some(rest)) => (dx, dy, rest),
        _ => return Err(malformed()),
    };
    match rest.split_once(' ').and_then(|(blur, color)| Some((length(blur)?, color))) {
        Some((blur, color)) => Ok((dx, dy, blur, color)),
        None => Ok((dx, dy, 0.0, rest)),
    }
}

/// `svg_utils.format_svg_numeric`: an integer bare, else the float's repr.
fn numeric(value: f64) -> String {
    if value.fract() == 0.0 && value.abs() < 1e12 {
        format!("{}", value as i64)
    } else {
        float_repr(value)
    }
}

/// `svg_utils.border_dash_attrs`: a dashed border's pattern, its cap and offset, only with a dash
/// array.
pub fn dash_attrs(attrs: Attrs, dash_array: Option<&[f64]>, line_cap: Option<&str>, dash_offset: Option<f64>) -> Attrs {
    let Some(dashes) = dash_array else { return attrs };
    let attrs = attrs.set("stroke-dasharray", dashes.iter().map(|v| format_g(*v)).collect::<Vec<_>>().join(","));
    let attrs = match line_cap {
        Some(cap) => attrs.set("stroke-linecap", cap),
        None => attrs,
    };
    match dash_offset {
        Some(offset) => attrs.set("stroke-dashoffset", format_g(offset)),
        None => attrs,
    }
}

/// `svg_utils.border_rect`: a stroke-only rect tracing a `width` x `height` box, inset by half the
/// stroke so the border stays inside the box; nothing when it strokes nothing.
pub fn border_rect(width: f64, height: f64, border: &BorderStyle, class: &str) -> Option<Node> {
    let stroke = border.width;
    if stroke <= 0.0 || border.color.as_str().is_empty() {
        return None;
    }
    let inset = stroke / 2.0;
    let attrs = Attrs::new().set("class", class).set("fill", "none").set("stroke", border.color.as_str()).num("stroke-width", stroke);
    let attrs = dash_attrs(attrs, border.dash_array.as_deref(), border.line_cap.as_ref().map(|cap| cap.as_str()), border.dash_offset);
    let (w, h, rx) = ((width - stroke).max(0.0), (height - stroke).max(0.0), (border.radius - inset).max(0.0));
    Some(Node::new(Role::Chrome, attrs, Shape::Rect { x: inset, y: inset, w, h, rx }))
}

/// The card's `content` in its chrome, in a `width` x `height` box: the shadow first, the content
/// clipped to `radius` when it is above zero, the border last.
pub fn card_layers(content: Vec<Node>, (width, height): (f64, f64), radius: f64, border: Option<&BorderStyle>, shadow: Option<Shadow>) -> Result<Vec<Node>, Diagnostic> {
    let mut nodes = Vec::new();
    if let Some(Shadow { shadow, fill, radius }) = shadow {
        let (dx, dy, blur, color) = parts(shadow)?;
        let filter = format!("filter: drop-shadow({}px {}px {}px {color})", format_g(dx), format_g(dy), format_g(blur));
        let attrs = Attrs::new().set("class", "dbt-card-shadow").set("fill", fill).set("style", filter);
        nodes.push(Node::new(Role::Chrome, attrs, Shape::Rect { x: 0.0, y: 0.0, w: width, h: height, rx: radius }));
    }
    if radius > 0.0 {
        // The clip id is the geometry itself: two cards sharing an id share a shape.
        let id = format!("dbt-card-clip-{}-{}-{}", numeric(width), numeric(height), numeric(radius));
        let rect = Node::new(Role::Chrome, Attrs::new(), Shape::Rect { x: 0.0, y: 0.0, w: width, h: height, rx: radius });
        nodes.push(Node::new(Role::Chrome, Attrs::new().set("id", id.as_str()), Shape::ClipPath(vec![rect])));
        nodes.push(Node::group(Role::Chrome, Attrs::new().set("clip-path", format!("url(#{id})")), content));
    } else {
        nodes.extend(content);
    }
    nodes.extend(border.and_then(|border| border_rect(width, height, border, "dbt-card-border")));
    Ok(nodes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shadow(text: &str) -> BoxShadow {
        BoxShadow::try_from(text).unwrap()
    }

    #[test]
    fn a_shadow_reads_its_offsets_blur_and_color() {
        assert_eq!(parts(&shadow("0 2px 8px #00000040")).unwrap(), (0.0, 2.0, 8.0, "#00000040"));
        assert_eq!(parts(&shadow("1 -2 rgba(0, 0, 0, 0.2)")).unwrap(), (1.0, -2.0, 0.0, "rgba(0, 0, 0, 0.2)"));
        assert_eq!(parts(&shadow("0.5px 1.5px 3 black")).unwrap(), (0.5, 1.5, 3.0, "black"));
    }
}

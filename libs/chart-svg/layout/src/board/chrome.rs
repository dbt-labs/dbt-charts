//! The chrome of tabs and details boards: the tab strip and the details summary bar, as typed laws
//! over the total [`Style`], each cited to its production source (`dbt_charts/core/render/layouts.py`
//! `render_tabs_layout`, `render_details_summary`; `render/sizing.py` `tab_*`; `chart-renderer/src/board.rs`
//! `render_details_item`).
//!
//! A left tab rail and a dashed border are refused by name: no corpus board draws either through this
//! pipeline yet.

use bind::font_stack::with_emoji;
use board_model::board::TabPosition;
use board_model::style::style::Style;
use diagnostic::{diag, refuse, Diagnostic};
use measure::shaped::{Font, FontBook};
use scene::{Attrs, Node, PathData, Role, Run, Shape};

/// One tab of the strip: where it sits and the label it draws.
#[derive(Debug, Clone, PartialEq)]
pub struct TabCell {
    pub x: f64,
    pub width: f64,
    /// The label as drawn: the title in the tab case, cut with "…" to fit `max_width`.
    pub shown: String,
    /// The label before it was cut, when it was; the cell's tooltip.
    pub full: Option<String>,
}

/// The tab strip of one tabs layout, top position.
#[derive(Debug, Clone, PartialEq)]
pub struct TabStrip {
    /// `frame.card_padding`: the bar sits this far below its slot's top, like card ink.
    pub inset: f64,
    pub cells: Vec<TabCell>,
}

impl TabStrip {
    /// `sizing.py::tab_content_offset` for `top`: the pane starts below the inset bar.
    pub fn pane_top(&self, style: &Style) -> f64 {
        self.inset + style.layout.tabs.bar_height
    }
}

/// A label's text, which must be plain: a template is resolved by display text, which this chrome does
/// not run.
pub fn plain<'a>(
    text: &'a board_model::facet::DisplayText,
    what: &str,
) -> Result<&'a str, Diagnostic> {
    let text = text.as_str();
    if text.contains("{{") || text.contains("{%") {
        return Err(Diagnostic::refused(refuse!(
            "{what} with template syntax is not supported"
        )));
    }
    Ok(text)
}

fn refuse_dashed(
    border: &board_model::primitives::BorderStyle,
    what: &str,
) -> Result<(), Diagnostic> {
    if border.dash_array.is_some() {
        return Err(Diagnostic::refused(refuse!(
            "a dashed {what} border is not supported"
        )));
    }
    Ok(())
}

/// `sizing.py::tab_label_measurer`: a label's advance at the active weight, so a tab does not resize when
/// the selection moves.
fn measure(book: &FontBook, style: &Style, text: &str) -> Result<f64, Diagnostic> {
    let tabs = &style.layout.tabs;
    let weight = measure::fonts::css_weight_axis(&tabs.active_weight)
        .map_err(diag::err_internal)?
        .ok_or_else(|| Diagnostic::refused(refuse!("a tab weight that names no font weight is not supported")))?;
    let font = Font {
        family: tabs.font.family.as_str(),
        size: tabs.font.size,
        weight,
        italic: false,
    };
    book.weighted_advance(text, &font)
        .map_err(diag::err_internal)
}

/// `layouts.py::_fit_label`: `label`, or its longest prefix that fits `room` once "…" is appended.
fn fit_label(
    label: &str,
    room: f64,
    width: &dyn Fn(&str) -> Result<f64, Diagnostic>,
) -> Result<String, Diagnostic> {
    if width(label)? <= room {
        return Ok(label.to_string());
    }
    let chars: Vec<char> = label.chars().collect();
    let mut keep = chars.len();
    while keep > 0
        && width(&format!(
            "{}…",
            chars[..keep].iter().collect::<String>().trim_end()
        ))? > room
    {
        keep -= 1;
    }
    Ok(format!(
        "{}…",
        chars[..keep].iter().collect::<String>().trim_end()
    ))
}

/// The strip of tabs titled `titles`: `tab_labels` (the tab case applied), `tab_cell_width` (label plus
/// padding, whole pixels, within `min_width` and `max_width`) and `_fit_label` for each.
pub fn tab_strip(
    style: &Style,
    position: TabPosition,
    titles: &[&str],
    book: &FontBook,
) -> Result<TabStrip, Diagnostic> {
    if position != TabPosition::Top {
        return Err(Diagnostic::refused(refuse!(
            "a tab rail beside the pane is not supported"
        )));
    }
    let tabs = &style.layout.tabs;
    refuse_dashed(&tabs.border, "tab strip")?;
    let room = tabs.max_width - 2.0 * tabs.padding_x;
    let width_of = |text: &str| measure(book, style, text);
    let mut x = 0.0;
    let mut cells = Vec::new();
    for title in titles {
        let label = display_text::case::apply_case(title, tabs.font.case.as_str())
            .map_err(diag::err_internal)?;
        let shown = fit_label(&label, room, &width_of)?;
        let width = (width_of(&label)? + 2.0 * tabs.padding_x)
            .ceil()
            .max(tabs.min_width)
            .min(tabs.max_width);
        cells.push(TabCell {
            x,
            width,
            full: (shown != label).then_some(label),
            shown,
        });
        x += width;
    }
    Ok(TabStrip {
        inset: style.frame.card_padding,
        cells,
    })
}

fn box_rect(class: &str, (x, y, w, h): (f64, f64, f64, f64)) -> Node {
    Node::rect(
        Role::Chrome,
        (x, y, w, h),
        Attrs::new().set("class", class).set("fill", "transparent"),
    )
}

/// `render_tabs_layout`'s bar: a group holding the outline of the active tab joined to the baseline, then
/// a cell per tab (inactive ones link to `hrefs[i]`, a tab with an `authored` path carries it), the group
/// placed `inset` below the slot. `width` is the layout's width.
pub fn draw_tab_strip(
    style: &Style,
    strip: &TabStrip,
    active: usize,
    hrefs: &[String],
    authored: &[String],
    width: f64,
) -> Result<Node, Diagnostic> {
    let tabs = &style.layout.tabs;
    let family = with_emoji(tabs.font.family.as_str(), &style.font.emoji);
    let ink = |color: &board_model::facet::Color| color.as_str().to_string();
    let (active_ink, inactive_ink) = (
        ink(&style.title.font.color),
        ink(&style.variables.font.color),
    );
    let stroke = tabs.border.width;
    let bar = tabs.bar_height;
    let end = strip.cells.last().map_or(0.0, |c| c.x + c.width);
    let cell = &strip.cells[active];
    let (baseline, top, left, right) = (
        bar - stroke / 2.0,
        stroke / 2.0,
        cell.x + stroke / 2.0,
        cell.x + cell.width - stroke / 2.0,
    );
    let radius = tabs
        .border
        .radius
        .min((right - left) / 2.0)
        .min(baseline - top);
    let rule = PathData::new()
        .move_to(0.0, baseline)
        .line_to(left, baseline)
        .line_to(left, top + radius)
        .arc_to(radius, true, left + radius, top)
        .line_to(right - radius, top)
        .arc_to(radius, true, right, top + radius)
        .line_to(right, baseline)
        .line_to(width.max(end), baseline);
    let mut parts = vec![Node::path(
        Role::Chrome,
        rule,
        Attrs::new()
            .set("fill", "none")
            .set("stroke", tabs.border.color.as_str())
            .num("stroke-width", stroke)
            .set("stroke-linejoin", "round"),
    )];
    for (i, cell) in strip.cells.iter().enumerate() {
        let is_active = i == active;
        let weight = if is_active {
            &tabs.active_weight
        } else {
            &tabs.inactive_weight
        };
        let runs = vec![Run::Chars(scene::Chars::new(
            cell.shown.as_str(),
            scene::Escape::Attr,
        ))];
        let text_attrs = Attrs::new()
            .set("text-anchor", "middle")
            .set("font-family", family.as_str())
            .num("font-size", tabs.font.size)
            .set(
                "fill",
                if is_active {
                    active_ink.as_str()
                } else {
                    inactive_ink.as_str()
                },
            )
            .set("font-weight", weight.as_str());
        let text = Node::new(
            Role::Label,
            text_attrs,
            Shape::Text {
                at: (cell.width / 2.0, bar / 2.0 + tabs.title_baseline_offset),
                bounds: None,
                runs,
            },
        );
        // A cut label's words ride in a `<title>` ahead of the boxes.
        let tooltip = cell.full.iter().map(|full| {
            let title = scene::Element {
                tag: "title".to_string(),
                attrs: Attrs::new(),
                children: vec![scene::Child::Text(scene::Chars::new(
                    full.as_str(),
                    scene::Escape::Attr,
                ))],
            };
            Node::element(title)
        });
        let children: Vec<Node> = tooltip
            .chain([
                box_rect("dbt-box-outer", (0.0, 0.0, cell.width, bar)),
                box_rect("dbt-box-inner", (0.0, 0.0, cell.width, bar)),
                text,
            ])
            .collect();
        let tab_attrs = |attrs: Attrs| {
            attrs
                .set("role", "tab")
                .set("aria-selected", is_active.to_string())
                .extend(authored_attrs(&authored[i]))
        };
        let tab = if is_active {
            Node::group(Role::Chrome, tab_attrs(Attrs::new()), children)
        } else {
            Node::new(
                Role::Chrome,
                tab_attrs(Attrs::new().set("href", hrefs[i].as_str())),
                Shape::Link(children),
            )
        };
        parts.push(tab.translate(cell.x, 0.0));
    }
    Ok(Node::group(
        Role::Chrome,
        Attrs::new(),
        vec![Node::group(
            Role::Chrome,
            Attrs::new().set("role", "tablist"),
            parts,
        )],
    )
    .translate(0.0, strip.inset))
}

/// `svg_utils.authored_attrs` for a tab's title: the pane's path and its kind.
fn authored_attrs(path: &str) -> Attrs {
    Attrs::new()
        .set("data-authored-path", path)
        .set("data-authored-kind", "title")
}

/// `layouts.py::render_details_summary`'s height: `layout.details.summary_height`.
pub fn summary_height(style: &Style) -> Result<f64, Diagnostic> {
    refuse_dashed(&style.layout.details.border, "details")?;
    Ok(style.layout.details.summary_height)
}

/// `render_details_summary`: the bar's rect, the disclosure arrow and the label in one link to `href`.
/// The collapsed bar is filled with the table's row stripe, the expanded one with its header
/// background, and neither (`none`) when the theme sets none.
pub fn draw_details_bar(
    style: &Style,
    label: &str,
    expanded: bool,
    href: &str,
    width: f64,
) -> Node {
    let details = &style.layout.details;
    let table = &style.charts.table;
    let fill = if expanded {
        table.header.background.as_ref()
    } else {
        table
            .row
            .stripe
            .as_ref()
            .and_then(|stripe| stripe.color.as_ref())
    };
    let ink = style.title.font.color.as_str();
    let (height, base) = (
        details.summary_height,
        details.summary_height / 2.0 + details.text_baseline_offset,
    );
    let rect = Node::new(
        Role::Chrome,
        Attrs::new()
            .set("fill", fill.map_or("none", |c| c.as_str()))
            .set("stroke", style.border.color.as_str())
            .num("stroke-width", details.border.width)
            .set("class", "dbt-details-toggle"),
        Shape::Rect {
            x: 0.0,
            y: 0.0,
            w: width,
            h: height,
            rx: details.border.radius,
        },
    );
    let arrow = Node::text(
        Role::Label,
        (details.arrow.x, base),
        Attrs::new()
            .num("font-size", details.arrow.font.size)
            .set("fill", ink),
        if expanded { "\u{25bc}" } else { "\u{25b6}" },
    );
    let text = Node::text(
        Role::Label,
        (details.label_x, base),
        Attrs::new()
            .num("font-size", details.font.size)
            .set("fill", ink)
            .set("font-weight", "500"),
        label,
    );
    Node::new(
        Role::Chrome,
        Attrs::new().set("href", href),
        Shape::Link(vec![rect, arrow, text]),
    )
}

/// The border around an expanded section, as tall as its summary and content.
pub fn draw_details_border(style: &Style, width: f64, height: f64) -> Node {
    let details = &style.layout.details;
    Node::new(
        Role::Chrome,
        Attrs::new()
            .set("fill", "none")
            .set("stroke", style.border.color.as_str())
            .num("stroke-width", details.border.width),
        Shape::Rect {
            x: 0.0,
            y: 0.0,
            w: width,
            h: height,
            rx: details.border.radius,
        },
    )
}

/// `layouts.py::_build_toggle_url`: the board's variables as a query string with `name` set to `value`,
/// an existing parameter updated in place and a new one appended last.
pub fn toggle_url(variables: &[(String, String)], name: &str, value: &str) -> String {
    let mut params: Vec<(&str, &str)> = variables
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect();
    match params.iter_mut().find(|(k, _)| *k == name) {
        Some(param) => param.1 = value,
        None => params.push((name, value)),
    }
    let query: Vec<String> = params
        .iter()
        .map(|(k, v)| format!("{}={}", url_encode(k), url_encode(v)))
        .collect();
    format!("?{}", query.join("&"))
}

/// `urllib.parse.urlencode`'s quoting: letters, digits and `-_.~` stay, a space is `+`, the rest `%XX`.
fn url_encode(text: &str) -> String {
    text.bytes()
        .map(|byte| match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                (byte as char).to_string()
            }
            b' ' => "+".to_string(),
            _ => format!("%{byte:02X}"),
        })
        .collect()
}

//! Front-end SVG contract: role-mark groups, structured-tooltip aria-labels,
//! authored-kind stamps, namespaced ids -- the hooks `chart_interactivity.js`
//! / `converters/chart.py` read off a rendered chart that the pixel sweep
//! cannot see. Ports `dbt_charts.core.render.chart.emitters._tooltip` (role
//! markers, `build_structured_tooltip_expr`'s row order) and
//! `render/converters/chart.py` (`safe_svg_id`, `svg_utils.authored_kind_attr`).
//! See `docs/laws-shared.md#front-end-contract`.

use display_text::language::tr;
use crate::families::axis_caption::AxisCaption;
use crate::common::FormatSlot;
use scene::{Attrs, Node};
use print::{print, xml_escape_attr};
use std::fmt::Write as _;

/// `_tooltip.py:56-73`'s exact codepoints -- `chart_interactivity.js`
/// mirrors these; keep the two in sync.
pub const ROLE_HEADER: char = '\u{2061}'; // FUNCTION APPLICATION
pub const ROLE_HEADER_SWATCHED: char = '\u{2064}'; // INVISIBLE PLUS -- header IS the color dim (pie)
pub const ROLE_SERIES: char = '\u{2062}'; // INVISIBLE TIMES
pub const ROLE_TOTAL: char = '\u{2063}'; // INVISIBLE SEPARATOR
pub const ROLE_ORDER: char = '\u{200C}'; // ZERO WIDTH NON-JOINER
/// Prefixes a row the author added with the chart's `tooltip:` -- a
/// `label: value` row that follows the mark's own value rows.
pub const ROLE_EXTRA: char = '\u{200D}'; // ZERO WIDTH JOINER
/// The one part of an imputed stack slot's label that is not identity: the slot
/// names its x and series but has no datum to report.
pub const ROLE_IMPUTED: char = '\u{034F}'; // COMBINING GRAPHEME JOINER
/// Stands for a `;` inside an authored value, which the runtime splits rows on;
/// `parseAriaLabel` turns it back.
pub const SEMICOLON_ESCAPE: char = '\u{E000}'; // PRIVATE USE AREA
/// Emphasis modifier, orthogonal to the role markers above -- prefixes a
/// value/total row that renders at the low-contrast label color rather
/// than the loud value color (`_tooltip.py`'s `MUTED`). Pie's own `%`
/// row leads (loud, unprefixed); its raw value and grand-total rows are
/// context, so both carry this prefix.
pub const MUTED: char = '\u{2060}'; // WORD JOINER
/// One authored `tooltip:` row.
pub fn extra_row(label: &str, value: &str) -> String {
    format!("{ROLE_EXTRA}{label}: {value}")
}

/// Brackets a value row's swatch color: `SWATCH #hex SWATCH` leads its title.
pub const SWATCH: char = '\u{200B}'; // ZERO WIDTH SPACE

/// Open a Vega mark-set's `.role-mark` group -- `chart_interactivity.js`'s
/// `isDataMark` classifies an element by its NEAREST ancestor carrying
/// `role-mark`/`role-axis`/`role-legend`/`role-title`, so wrapping the
/// datum here is what makes its own `aria-label` readable as a data mark
/// (`chart_svg_dev/contract.py::_group_role`). `kind` is Vega's own mark
/// tag ("rect", "rule"); `name` is cosmetic -- the extractor never reads
/// the group's third class token.
pub fn mark_group_open(out: &mut String, kind: &str, name: &str) -> Result<(), String> {
    out.push_str(&open_tag(mark_group(kind, name, Vec::new())?));
    Ok(())
}

/// The `.role-mark` group of [`mark_group_open`], holding `children`.
pub fn mark_group(kind: &str, name: &str, children: Vec<Node>) -> Result<Node, String> {
    Ok(Node::group(mark_group_attrs(kind, name, false)?, children))
}

pub fn value_label_group(name: &str, children: Vec<Node>) -> Result<Node, String> {
    Ok(Node::group(mark_group_attrs("text", name, true)?, children))
}

fn mark_group_attrs(kind: &str, name: &str, value_label: bool) -> Result<Attrs, String> {
    let container = tr(&format!("aria.container_{kind}"), None, &[])?;
    Ok(Attrs::new()
        .set("class", format!("mark-{kind} role-mark {name}"))
        .set_if(value_label, "data-dbt-value-label", "true")
        .set("role", "graphics-object")
        .set("aria-roledescription", container))
}

/// The opening tags of a childless `group`, for a caller that writes the children as markup
/// itself and closes with [`mark_group_close`].
fn open_tag(group: Node) -> String {
    let printed = print(&[group]);
    printed.strip_suffix("</g>").expect("a childless group prints its end tag").to_string()
}

pub fn mark_group_close(out: &mut String) {
    out.push_str("</g>");
}

/// Which of Vega's two axis channels a group describes. `chart_interactivity.js`
/// finds an axis by its group's `data-dct-role` (`x-axis` / `y-axis`); the
/// `aria-label` beside it is a label, never a key.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum AxisChannel {
    X,
    Y,
}

/// Open the labeled axis group Vega draws around an axis's ticks, labels and
/// domain: `.role-axis` carrying Vega's description of the axis (`caption`),
/// which Vega writes in English whatever the board's language,
/// whose first child `<g>` is the axis's own coordinate frame. The runtime reads
/// that frame's origin as the x-axis baseline (`panelXAxis`, the drop line's
/// foot) and the group's rendered box as the y-axis's vertical band
/// (`yAxisBands`).
///
/// This engine draws axis items in plot-card coordinates, so the frame is
/// opened at `origin` and the items sit in a second `<g>` that undoes the
/// translate: the frame origin is the real axis origin and no item moves.
/// Close with [`axis_group_close`].
pub fn axis_group_open(
    out: &mut String,
    channel: AxisChannel,
    caption: &AxisCaption,
    origin: (f64, f64),
) -> Result<(), String> {
    let label = caption.describe(channel)?;
    let frame = axis_frame(channel, &label, origin, caption.bare, Vec::new());
    let printed = print(&[frame]);
    out.push_str(printed.strip_suffix("</g></g></g>").expect("a childless axis frame prints three end tags"));
    Ok(())
}

/// [`axis_group_open`]'s group around `children`; a bare axis (Vega-Lite drops it) is its
/// `children` alone.
pub fn axis_group(
    channel: AxisChannel,
    caption: &AxisCaption,
    origin: (f64, f64),
    children: Vec<Node>,
) -> Result<Vec<Node>, String> {
    let label = caption.describe(channel)?;
    if caption.bare {
        return Ok(children);
    }
    Ok(vec![axis_frame(channel, &label, origin, false, children)])
}

/// `.role-axis` around the axis's coordinate frame and the second `<g>` that undoes its
/// translate; `mark_bare` stamps the opener a string-building caller later unwraps.
fn axis_frame(channel: AxisChannel, label: &str, (ox, oy): (f64, f64), mark_bare: bool, children: Vec<Node>) -> Node {
    let key = match channel {
        AxisChannel::X => "x-axis",
        AxisChannel::Y => "y-axis",
    };
    let attrs = Attrs::new()
        .set("class", "mark-group role-axis")
        .set_if(mark_bare, BARE_AXIS_ATTR.0, BARE_AXIS_ATTR.1)
        .set("data-dct-role", key)
        .set("role", "graphics-symbol")
        .set("aria-roledescription", "axis")
        .set("aria-label", label);
    let undo = Node::group(Attrs::new().set("transform", format!("translate({:.3},{:.3})", -ox, -oy)), children);
    let frame = Node::group(Attrs::new().set("transform", format!("translate({ox:.3},{oy:.3})")), vec![undo]);
    Node::group(attrs, vec![frame])
}

/// Marks the opener of an axis group Vega-Lite drops; [`axis_group_close`]
/// unwraps it.
const BARE_AXIS_ATTR: (&str, &str) = ("data-dct-bare", "1");

/// The group Vega draws around an axis's gridlines, holding `children`.
pub fn grid_group(children: Vec<Node>) -> Node {
    Node::group(Attrs::new().set("class", "mark-rule role-axis-grid").set("aria-hidden", "true"), children)
}

pub fn axis_group_close(out: &mut String) {
    let opener = out.rfind("<g class=\"mark-group role-axis\"").expect("an axis group is open");
    if !out[opener..].starts_with(&format!("<g class=\"mark-group role-axis\" {}=\"{}\"", BARE_AXIS_ATTR.0, BARE_AXIS_ATTR.1)) {
        out.push_str("</g></g></g>");
        return;
    }
    // The wrapper is three tags, none with a `>` inside.
    let mut end = opener;
    for _ in 0..3 {
        end += out[end..].find('>').expect("an axis opener has three tags") + 1;
    }
    out.replace_range(opener..end, "");
}

/// The authored label an axis title edits: the key a click-to-edit writes back.
/// An axis carries one only when the chart's channels pin it unambiguously (a
/// facet's panels do not, and a dual axis's secondary title never does).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LabelKey {
    X,
    Y,
}

/// The attributes of the group Vega draws around an axis title, stamped with the authored
/// key it edits (`converters/chart.py::_stamp_axis_title_kinds`).
pub fn axis_title_attrs(key: Option<LabelKey>) -> Attrs {
    let attrs = Attrs::new().set("class", "mark-text role-axis-title").set("pointer-events", "none");
    match key {
        Some(LabelKey::X) => attrs.set("data-authored-kind", "x_label"),
        Some(LabelKey::Y) => attrs.set("data-authored-kind", "y_label"),
        None => attrs,
    }
}

/// Vega's symbol-legend group (`role-legend`): the thumbnail strips it and
/// the hover runtime skips a cursor inside it. It holds the title and the
/// entries group.
///
/// `aria` is the legend's own label, set where the entries' names are ambiguous.
pub fn legend_attrs(aria: Option<&str>) -> Attrs {
    let attrs = Attrs::new().set("class", "mark-group role-legend");
    match aria {
        Some(label) => attrs.set("role", "graphics-symbol").set("aria-roledescription", "legend").set("aria-label", label),
        None => attrs,
    }
}

/// A legend's title group (`role-legend-title`).
pub fn legend_title_attrs() -> Attrs {
    Attrs::new().set("class", "mark-text role-legend-title").set("pointer-events", "none")
}

/// The one group holding every legend entry (`role-legend-entry`).
pub fn legend_entries_attrs() -> Attrs {
    Attrs::new().set("class", "mark-group role-legend-entry")
}

/// One entry's swatch group (`role-legend-symbol`).
pub fn legend_symbol_attrs() -> Attrs {
    Attrs::new().set("class", "mark-symbol role-legend-symbol").set("pointer-events", "none")
}

const LEGEND_LABEL_CLASS: &str = "mark-text role-legend-label";

/// The group Vega draws around legend label text (`role-legend-label`): the
/// runtime reads a legend's labels from it. A gradient legend has one for all
/// its tick labels, a symbol legend one per entry. `series_key` is the
/// `data-dbt-series` join key (`_stamp_series_keys`), `None` when none is baked.
pub fn legend_label_attrs(series_key: Option<&str>) -> Attrs {
    let attrs = Attrs::new().set("class", LEGEND_LABEL_CLASS).set("pointer-events", "none");
    match series_key {
        Some(key) => attrs.set("data-dbt-series", key),
        None => attrs,
    }
}

/// Open the group Vega draws around a facet header (`role-title-text`). The
/// authored-kind stamp is the converter's regex over every such group, so a
/// header carries the chart-title kind exactly as the chart's own title does.
/// Close with [`mark_group_close`].
pub fn facet_header_open(out: &mut String, header: &str) -> Result<(), String> {
    let _ = write!(
        out,
        "<g class=\"mark-text role-title-text\"{kind} role=\"graphics-symbol\" \
         aria-roledescription=\"{title}\" aria-label=\"{label}\" pointer-events=\"none\">",
        kind = authored_kind_attr("title"),
        title = xml_escape_attr(&tr("aria.title", None, &[])?),
        label = xml_escape_attr(&tr("aria.title_text", None, &[("header", header)])?),
    );
    Ok(())
}

/// Whether `svg` announces nothing but axes: no mark carries an `aria-label`.
#[cfg(test)]
pub(crate) fn has_no_mark_labels(svg: &str) -> bool {
    let mut rest = svg;
    while let Some(at) = rest.find("aria-label=\"") {
        let label = &rest[at + "aria-label=\"".len()..];
        if !(label.starts_with("X-axis") || label.starts_with("Y-axis")) {
            return false;
        }
        rest = &label[label.find('"').expect("an attribute value closes")..];
    }
    true
}

/// The group Vega draws around a connected series' path (`kind` is `line`
/// or `area`): `chart_interactivity.js` tells a line/area chart, which restates
/// a datum as a drop line and a dot, from a scatter by finding a `.mark-line`
/// or `.mark-area` group. The path carries no description, so the group is
/// bare, as Vega leaves it.
pub fn connected_group(kind: &str, name: &str, children: Vec<Node>) -> Node {
    Node::group(Attrs::new().set("class", format!("mark-{kind} role-mark {name}")), children)
}

/// The `.role-mark` group of a mark set declared `aria: false`. Vega
/// writes `aria-hidden="true"` on that group and no `role`/`aria-label` on
/// its items, so a screen reader skips the whole set; the runtime still
/// resolves a hit on one of its items to the nearest labeled mark, which is
/// why the group is kept rather than dropped.
pub fn hidden_mark_group(kind: &str, name: &str, children: Vec<Node>) -> Node {
    Node::group(
        Attrs::new().set("class", format!("mark-{kind} role-mark {name}")).set("aria-hidden", "true"),
        children,
    )
}

/// Close with [`mark_group_close`].
pub fn hidden_mark_group_open(out: &mut String, kind: &str, name: &str) {
    out.push_str(&open_tag(hidden_mark_group(kind, name, Vec::new())));
}

/// What a screen reader is told about one mark.
pub enum Reads<'a> {
    /// No description wired (an unlabeled mark is bare in production too).
    Bare,
    /// `aria: false`: the mark's group is hidden from the accessibility tree.
    Hidden,
    /// Announced by this `aria-label`.
    Label(&'a str),
}

/// One mark as its own `.role-mark` group (`mark_group` / `hidden_mark_group`)
/// around the node `item` builds from the attributes it carries for `reads` (the
/// label, or nothing); a bare mark is its item alone.
pub fn item_group(
    kind: &str,
    name: &str,
    role: ItemRole,
    reads: Reads,
    item: impl FnOnce(Attrs) -> Node,
) -> Result<Node, String> {
    match reads {
        Reads::Bare => Ok(item(Attrs::new())),
        Reads::Hidden => Ok(hidden_mark_group(kind, name, vec![item(Attrs::new())])),
        Reads::Label(label) => mark_group(kind, name, vec![item(aria_attrs(Some(label), role)?)]),
    }
}

/// Mark a Vega-family chart's root `<svg>` as the view root Vega writes
/// (`class="marks"`): `chart_interactivity.js`'s `markerOverlay` appends its
/// hover markers to `chartEl.querySelector('svg.marks')` and draws none when
/// it finds no host. The class sits right after the `xmlns` attribute, where
/// Vega's own root carries it. A root it cannot place it on is an `Err`, not
/// a chart that quietly loses its hover markers.
pub fn stamp_view_root(svg: String) -> Result<String, String> {
    const ROOT: &str = "<svg xmlns=\"http://www.w3.org/2000/svg\"";
    let rest = svg
        .strip_prefix(ROOT)
        .ok_or("chart svg root does not open `<svg xmlns=\"…\"`: cannot stamp class=\"marks\"")?;
    let root_tag = &rest[..rest.find('>').ok_or("chart svg root tag is unterminated")?];
    if root_tag.contains(" class=") {
        return Err("chart svg root already carries a class: cannot stamp class=\"marks\"".into());
    }
    Ok(format!("{ROOT} class=\"marks\"{rest}"))
}

/// The role of a mark's items. `aria-roledescription` carries Vega's
/// description (a label); `data-dct-role` carries the machine key. The runtime
/// reads the key: `chart_interactivity.js`'s `RECEDE_ROLES` (`bar`, `arc`,
/// `rect`) decide which marks recede on hover, and `point` at `opacity="0"`
/// marks a line/area/scatter hover target -- so a wrong key here silently
/// turns the hover treatment off.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ItemRole {
    /// A bar (`mark: bar`); Vega-Lite names it explicitly.
    Bar,
    /// A pie/donut slice (`mark: arc`), Vega's default `<type> mark`.
    ArcMark,
    /// A heatmap cell (`mark: rect`), Vega's default `<type> mark`.
    RectMark,
    /// A point/circle symbol; Vega-Lite names it explicitly.
    Point,
    RuleMark,
    TextMark,
    /// A sankey ribbon, Vega's default `<type> mark` naming.
    PathMark,
    /// An authored annotation (rule, band, note or trend).
    Annotation,
}

impl ItemRole {
    /// `(catalog id of the aria-roledescription, data-dct-role)`.
    fn description_id_and_key(self) -> (&'static str, &'static str) {
        match self {
            ItemRole::Bar => ("aria.role_bar", "bar"),
            ItemRole::ArcMark => ("aria.role_arc", "arc"),
            ItemRole::RectMark => ("aria.role_rect", "rect"),
            ItemRole::Point => ("aria.role_point", "point"),
            ItemRole::RuleMark => ("aria.role_rule", "rule"),
            ItemRole::TextMark => ("aria.role_text", "text"),
            ItemRole::PathMark => ("aria.role_path", "path"),
            ItemRole::Annotation => ("aria.role_annotation", "annotation"),
        }
    }
}

/// The attributes a datum's first drawn element carries: its `aria-label`
/// plus the `role="graphics-symbol"` / `aria-roledescription` pair Vega
/// stamps on every item that has a description. Empty when the shape wires
/// no label (an unlabeled mark is bare in production too, never an
/// approximation).
pub fn aria_attr(label: Option<&str>, role: ItemRole) -> Result<String, String> {
    Ok(print::attributes(&aria_attrs(label, role)?))
}

/// [`aria_attr`] as attributes.
pub fn aria_attrs(label: Option<&str>, role: ItemRole) -> Result<Attrs, String> {
    let Some(label) = label else { return Ok(Attrs::new()) };
    let (description_id, key) = role.description_id_and_key();
    Ok(Attrs::new()
        .set("data-dct-role", key)
        .set("aria-label", label)
        .set("role", "graphics-symbol")
        .set("aria-roledescription", tr(description_id, None, &[])?))
}

/// The attributes a support-table cell carries; a cell nobody described stays bare.
pub fn cell_label_attrs(label: &str) -> Result<Attrs, String> {
    aria_attrs((!label.is_empty()).then_some(label), ItemRole::TextMark)
}

/// The attributes a series-label rail text carries: Vega's default
/// description of a text mark bound to a nominal field, the raw `field: value`
/// pair (never the truncated display text).
pub fn series_label_attrs(color_field: &str, series: &str) -> Result<Attrs, String> {
    aria_attrs(Some(&format!("{color_field}: {series}")), ItemRole::TextMark)
}

/// One cartesian datum's structured-tooltip content -- shared by bar,
/// line, and area (`features/structured_tooltip.py::_cartesian_roles`/
/// `_stack_group_total` assembled through `emitters/_tooltip.py::
/// build_structured_tooltip_expr`'s fixed row order: header, series, order,
/// values, total). A bare (unlabeled) row carries no title -- confirmed
/// against the corpus (`⁡Jan; ⁢EMEA; Revenue: 90,000`: the header/series
/// rows show only their value, never `x: Jan`) -- so `header`/`series` here
/// are plain display strings, not `TooltipField`s. The struct name predates
/// line/area landing here too; kept rather than rippling a rename through
/// `bar.rs`'s many call sites for a purely cosmetic gain.
pub struct BarTooltip<'a> {
    /// Chart.x's own display value -- nominal category text, or a
    /// `temporal::tooltip_header_date` formatted string.
    pub header: &'a str,
    /// The bound color field's raw value, when a series row is promoted
    /// (`series_row_promoted`: `color_domain.len() > 1`).
    pub series: Option<&'a str>,
    /// 0-based rank into the color scale's own display domain
    /// (`_series_order_role`) -- `Some` only alongside `series`. For a
    /// grouped (non-stacked) bar, only when a board-planned category-color
    /// scale binds this field (`emitters/bar.py:1066-1102`); a stacked bar
    /// always ranks (`emitters/bar.py:1103-1119`); line/area rank whenever
    /// the series row promotes (`emitters/line.py`'s `spatial_color_scale`
    /// bakes an explicit `scale.domain` -- `legend_domain`'s last-value
    /// order, the dash palette's insertion order, or, on a stacked area, the
    /// reversed baseline order -- unconditionally once a nominal color
    /// channel with a palette binds).
    pub order: Option<i64>,
    pub value_title: &'a str,
    pub value_format: &'a FormatSlot,
    /// Share-of-total (`%`) row (`normalize` stacks only) -- a fraction, rendered
    /// at the fixed `.0%` tooltip convention (`_PERCENT_TOOLTIP_FORMAT`).
    pub percent: Option<f64>,
    /// Stacked group-total footer (`("Σ", sum)`), `zero` and
    /// `normalize` stacks alike.
    pub total: Option<(&'a str, f64)>,
    /// The authored `tooltip:` rows ([`extra_row`]), after the value row and
    /// before the Σ footer.
    pub extra: &'a [String],
}

/// Build one datum's aria-label. `d3rs::format` mirrors Vega's own
/// `format()` expression call -- a format spec it rejects is a real,
/// user-authored shape this port cannot reproduce, so it refuses by
/// propagating the `Err` rather than drawing an approximate label.
pub fn bar_aria_label(t: &BarTooltip, value: f64) -> Result<String, String> {
    let value_s = t.value_format.paint(value).map_err(|e| format!("bar: tooltip value {e}"))?;
    bar_aria_label_text(t, &value_s)
}

/// The description of a datum Vega imputes into a sparse stack: it names the
/// slot (so the hover band covers it and the runtime finds its x) and marks it
/// imputed, which the runtime leaves out of the card.
pub fn imputed_aria_label(header: &str, series: &str, order: Option<i64>) -> String {
    let mut parts = identity_parts(header, Some(series), order);
    parts.push(ROLE_IMPUTED.to_string());
    parts.join("; ")
}

fn identity_parts(header: &str, series: Option<&str>, order: Option<i64>) -> Vec<String> {
    let mut parts = vec![format!("{ROLE_HEADER}{header}")];
    if let Some(series) = series {
        parts.push(format!("{ROLE_SERIES}{series}"));
    }
    if let Some(rank) = order {
        parts.push(format!("{ROLE_ORDER}{rank}"));
    }
    parts
}

fn bar_aria_label_text(t: &BarTooltip, value_s: &str) -> Result<String, String> {
    let mut parts = identity_parts(t.header, t.series, t.order);
    if let Some(pct) = t.percent {
        let pct_s = crate::number_locale::d3_format(".0%", pct)
            .map_err(|e| format!("bar: tooltip percent format: {e}"))?;
        parts.push(format!("%: {pct_s}"));
    }
    parts.push(format!("{}: {value_s}", t.value_title));
    parts.extend(t.extra.iter().cloned());
    if let Some((total_title, total_value)) = t.total {
        let total_s = t.value_format.paint(total_value).map_err(|e| format!("bar: tooltip total {e}"))?;
        parts.push(format!("{ROLE_TOTAL}{total_title}: {total_s}"));
    }
    Ok(parts.join("; "))
}

/// One datum of a mirrored facet's ghost layer.
pub struct GhostRule<'a> {
    pub aria: Option<&'a str>,
    pub x: f64,
    pub y: f64,
    pub stroke: &'a str,
    /// The datum's link, which wraps its rule like any other mark of the datum.
    pub(crate) anchor: Option<crate::href::Anchor<'a>>,
}

/// How far a ghost rule runs from its datum.
#[derive(Clone, Copy)]
pub enum GhostRun {
    /// A column chart's rule drops to the plot floor, this far down from the top.
    Down(f64),
    /// A horizontal bar's rule runs back to the plot's left edge.
    ToLeftEdge,
    /// A heatmap cell's rule runs back to the plot's origin.
    ToOrigin,
}

/// [`mirror_ghost_layer`]'s group.
pub fn ghost_layer(rules: &[GhostRule], run: GhostRun) -> Result<Node, String> {
    let lines = rules
        .iter()
        .map(|rule| {
            let (x2, y2) = match run {
                GhostRun::Down(floor) => (0.0, floor - rule.y),
                GhostRun::ToLeftEdge => (-rule.x, 0.0),
                GhostRun::ToOrigin => (-rule.x, -rule.y),
            };
            let line = Node::Line(
                Attrs::new()
                    .set("transform", format!("translate({:.3},{:.3})", rule.x, rule.y))
                    .set("x2", format!("{x2:.3}"))
                    .set("y2", format!("{y2:.3}"))
                    .set("stroke", rule.stroke)
                    .set("opacity", "0")
                    .extend(aria_attrs(rule.aria, ItemRole::RuleMark)?),
            );
            Ok(crate::href::linked(rule.anchor, line))
        })
        .collect::<Result<_, String>>()?;
    mark_group("rule", "ghost_marks", lines)
}

/// Vega-Lite's own description of a mark: one `title: value` row per described
/// channel, in encoding order, joined by `; `.
pub fn native_label(rows: &[(&str, String)]) -> String {
    rows.iter().map(|(title, value)| format!("{title}: {value}")).collect::<Vec<_>>().join("; ")
}

/// One value row of a ranged bar's (`y_start`) tooltip. `muted` rows render
/// at the low-contrast label color (`MUTED`), and a row with an empty title
/// prints as a bare `: value` (`_tooltip.py::_row_expr`).
pub struct SpanRow {
    pub title: String,
    pub value: String,
    pub muted: bool,
    /// The mark color the row is keyed to, when a layer shares the block.
    pub swatch: Option<String>,
}

/// A ranged bar's structured-tooltip content: header, its series and order
/// rows when the bar is color-split, then its value rows — the start, the end
/// and their difference (`structured_tooltip.py::_span_values`,
/// `_tooltip.py::span_tooltip_rows`; the row order is
/// `build_structured_tooltip_expr`'s).
pub fn span_aria_label(header: &str, series: Option<&str>, order: Option<i64>, rows: &[SpanRow]) -> String {
    let mut parts = vec![format!("{ROLE_HEADER}{header}")];
    parts.extend(series.map(|series| format!("{ROLE_SERIES}{series}")));
    parts.extend(order.map(|rank| format!("{ROLE_ORDER}{rank}")));
    parts.extend(rows.iter().map(|row| {
        let marker = if row.muted { MUTED.to_string() } else { String::new() };
        let swatch = row.swatch.as_ref().map(|c| format!("{SWATCH}{c}{SWATCH}")).unwrap_or_default();
        format!("{marker}{swatch}{}: {}", row.title, row.value)
    }));
    parts.join("; ")
}

/// Pie's own structured-tooltip content for one slice --
/// `features/structured_tooltip.py::_pie_roles`, ported. Unlike bar's
/// cartesian roles the header IS the color dimension (`ROLE_HEADER_
/// SWATCHED`, never plain `ROLE_HEADER`) and the `Σ` footer is
/// UNCONDITIONAL -- present regardless of whether the chart's own
/// center-total display (`chart.total.visible`) is configured, confirmed
/// against the corpus: `charts/sector-charts_0/revenue_share_hero` bakes
/// `total: {visible: true, ...}` and `charts/sector-charts_1/revenue_
/// share_minimal` has no `total` config at all, yet both emit the
/// identical `Σ: 443,023` tooltip row -- the tooltip's own total is a
/// separate, always-on computation (`_augment_pie_data`'s `PIE_TOTAL_
/// FIELD`), not gated by the center-text display.
pub struct PieTooltip<'a> {
    /// Raw identity value, `None` when the chart authors no identity
    /// field -- a bare row like bar's header/series (the swatched marker
    /// alone carries the "this is the series" meaning; no title ever
    /// joins it, same "value only" law as `BarTooltip::header`/`series`).
    pub header: Option<&'a str>,
    /// This slice's share of `total` (`v / total`, already computed by
    /// the caller from the same `thetas`/`total` the wheel geometry uses
    /// -- never re-derived here).
    pub share: f64,
    pub value_title: &'a str,
    pub value_format: &'a FormatSlot,
    pub value: f64,
    pub total: f64,
    /// The authored `tooltip:` rows, after the value row and before the Σ footer.
    pub extra: &'a [String],
}

/// Build one slice's aria-label. The `%` row always renders at the fixed
/// `.0%` tooltip convention (`_PERCENT_TOOLTIP_FORMAT`), never the
/// chart's own `tooltip_format` -- same fixed convention as bar's
/// normalized-stack `%` row.
pub fn pie_aria_label(t: &PieTooltip) -> Result<String, String> {
    let mut parts = Vec::new();
    if let Some(header) = t.header {
        parts.push(format!("{ROLE_HEADER_SWATCHED}{header}"));
    }
    let share_s = crate::number_locale::d3_format(".0%", t.share)
        .map_err(|e| format!("pie: tooltip share format: {e}"))?;
    parts.push(format!("%: {share_s}"));
    let value_s = t.value_format.paint(t.value).map_err(|e| format!("pie: tooltip value {e}"))?;
    parts.push(format!("{MUTED}{}: {value_s}", t.value_title));
    parts.extend(t.extra.iter().cloned());
    let total_s = t.value_format.paint(t.total).map_err(|e| format!("pie: tooltip total {e}"))?;
    parts.push(format!("{MUTED}{ROLE_TOTAL}Σ: {total_s}"));
    Ok(parts.join("; "))
}

/// One of scatter's two peer value rows -- `_value_tooltip_field`'s
/// per-field type inference lets y or x independently be a measure or a
/// category (a dot plot's nominal y), unlike bar/pie's fixed measure-only
/// rows.
pub enum ScatterValue<'a> {
    Quantitative(f64, &'a FormatSlot),
    Nominal(&'a str),
}

/// Scatter's own structured-tooltip content -- `features/structured_
/// tooltip.py::_scatter_roles`, ported. y (dependent) leads, x
/// (independent) follows -- neither axis alone identifies a point. A bound
/// color channel promotes its raw value to a swatched header (mirrors
/// pie's `header_is_swatched`); a plain (uncolored) scatter carries no
/// header row at all.
pub struct ScatterTooltip<'a> {
    pub header: Option<&'a str>,
    pub y_title: &'a str,
    pub y_value: ScatterValue<'a>,
    pub x_title: &'a str,
    pub x_value: ScatterValue<'a>,
    /// The authored `tooltip:` rows, after the x row.
    pub extra: &'a [String],
}

fn scatter_value_row(title: &str, value: &ScatterValue) -> Result<String, String> {
    let rendered = match value {
        ScatterValue::Quantitative(v, fmt) => fmt.paint(*v).map_err(|e| format!("scatter: tooltip value {e}"))?,
        ScatterValue::Nominal(s) => (*s).to_string(),
    };
    Ok(format!("{title}: {rendered}"))
}

/// A dot plot's structured-tooltip content -- `features/structured_
/// tooltip.py::_dot_plot_roles`, ported. A scatter with one category axis,
/// one measure axis and a color split reads like a grouped bar: the category
/// heads the bubble as a bare `ROLE_HEADER` row, the series follows as a bare
/// `ROLE_SERIES` row, and the measure is the one titled value row
/// (`CHART_AXES_LUT["dot_plot"]`).
pub struct DotPlotTooltip<'a> {
    pub category: &'a str,
    pub series: &'a str,
    /// The series' rank in the color scale's baked domain -- `Some` only when
    /// a board-planned category-color scale binds the color field.
    pub order: Option<i64>,
    pub value_title: &'a str,
    pub value: f64,
    pub value_format: &'a FormatSlot,
    /// The authored `tooltip:` rows, after the value row.
    pub extra: &'a [String],
}

pub fn dot_plot_aria_label(t: &DotPlotTooltip) -> Result<String, String> {
    let value = scatter_value_row(t.value_title, &ScatterValue::Quantitative(t.value, t.value_format))?;
    let order = t.order.map_or_else(String::new, |rank| format!("{ROLE_ORDER}{rank}; "));
    let extra: String = t.extra.iter().map(|row| format!("; {row}")).collect();
    Ok(format!("{ROLE_HEADER}{}; {ROLE_SERIES}{}; {order}{value}{extra}", t.category, t.series))
}

pub fn scatter_aria_label(t: &ScatterTooltip) -> Result<String, String> {
    let mut parts = Vec::new();
    if let Some(header) = t.header {
        parts.push(format!("{ROLE_HEADER_SWATCHED}{header}"));
    }
    parts.push(scatter_value_row(t.y_title, &t.y_value)?);
    parts.push(scatter_value_row(t.x_title, &t.x_value)?);
    parts.extend(t.extra.iter().cloned());
    Ok(parts.join("; "))
}

/// Heatmap's own structured-tooltip content -- `features/structured_
/// tooltip.py::_heatmap_roles`, ported. Heatmap's two independents (x, y)
/// TOGETHER identify the cell, so both header as bare `ROLE_HEADER` rows
/// (never swatched -- color IS the dependent here, not an identity
/// dimension); the color-encoded value is the sole dependent row, and
/// there is no series/order/total role at all.
pub struct HeatmapTooltip<'a> {
    pub x: &'a str,
    pub y: &'a str,
    pub value_title: &'a str,
    pub value_format: &'a FormatSlot,
    pub value: HeatmapValue<'a>,
}

/// The color-encoded value of a heatmap cell: a measure prints through the
/// tooltip format, a category prints its own name.
#[derive(Clone, Copy)]
pub enum HeatmapValue<'a> {
    Number(f64),
    Category(&'a str),
}

impl HeatmapValue<'_> {
    pub fn text(&self, format: &FormatSlot) -> Result<String, String> {
        match self {
            Self::Number(n) => format.paint(*n),
            Self::Category(name) => Ok((*name).to_string()),
        }
    }
}

pub fn heatmap_aria_label(t: &HeatmapTooltip) -> Result<String, String> {
    let value_s = t.value.text(t.value_format).map_err(|e| format!("heatmap: tooltip value {e}"))?;
    Ok(format!("{ROLE_HEADER}{}; {ROLE_HEADER}{}; {}: {value_s}", t.x, t.y, t.value_title))
}

/// Structured-tooltip header text for one x/category value --
/// `emitters/_tooltip.py::header_tooltip_field`'s temporal branch, ported.
/// The raw category string when the field types nominal; otherwise
/// `temporal::tooltip_header_date`'s formatted text -- the FULL bucket when
/// `detected_tu` names one, else its own continuous-date fallback
/// (`time_unit == ""`, `"%-d %b %Y"`). `is_standard_date` is production's
/// STRICT `all_temporal` gate (`x_values_are_standard_dates`): a field
/// matching it types "temporal" UNCONDITIONALLY, even with no detected
/// grain, distinct from a looser date-like ("ordinal") field, which only
/// formats when `detected_tu` is `Some`. A board's redefinition of the detected
/// grain's name (`grain_format`, `ResolvedAxisStyle.grain_formats`) replaces
/// that vocabulary with its own spec. Shared by every cartesian family whose
/// header is chart.x regardless of orientation (bar) or series count
/// (line/area).
pub fn tooltip_header_text(
    cat: &str,
    detected_tu: Option<crate::families::temporal::TimeUnit>,
    is_standard_date: bool,
    grain_format: Option<crate::families::temporal::AuthoredTimeFormat>,
) -> Result<String, String> {
    if detected_tu.is_none() && !is_standard_date {
        return Ok(cat.to_string());
    }
    let (y, m, d) = crate::time_axis::parse_iso_date(cat)
        .ok_or_else(|| format!("tooltip header: unparsed date {cat:?}"))?;
    match grain_format {
        Some(format) => Ok(format.text(y, m, d)),
        None => crate::families::temporal::tooltip_header_date(y, m, d, detected_tu),
    }
}

/// 0-based rank of `series` in `order_domain` -- `_series_order_role`'s
/// Rust mirror of `series_order_expression`'s own
/// `toString(...) === v ? i : ... : -1` fallback. Shared by every cartesian
/// family's ORDER role (bar, line, area).
pub fn tooltip_order_rank(order_domain: &[String], series: &str) -> i64 {
    order_domain.iter().position(|s| s == series).map_or(-1, |i| i as i64)
}

/// The `data-authored-kind` a *leaf* text run carries -- `svg_utils.py`'s
/// `authored_kind_attr`, verbatim.
pub fn authored_kind_attr(kind: &str) -> String {
    print::attributes(&Attrs::new().set("data-authored-kind", kind))
}

/// The chart-id tail of the id-namespace suffix every vl-convert-minted id
/// carries in production -- `converters/chart.py::safe_svg_id`, ported.
/// Production puts a content token ahead of it; this classification only
/// reads the tail, so it holds for both renderers. FuncIRI-unsafe
/// characters replaced with `_`, plus a short digest suffix when sanitizing
/// was lossy (many-to-one, so two chart ids colliding only after sanitizing
/// must not share a suffix). A chart id already FuncIRI-safe is untouched
/// (every id in the golden corpus).
pub fn safe_svg_id(chart_id: &str) -> String {
    let safe: String = chart_id
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() || c == '_' || c == '-' { c } else { '_' })
        .collect();
    if safe == chart_id {
        return safe;
    }
    use md5::{Digest, Md5};
    let digest = Md5::digest(chart_id.as_bytes());
    let mut hex = String::with_capacity(8);
    for byte in digest.iter().take(4) {
        let _ = write!(hex, "{byte:02x}");
    }
    format!("{safe}-{hex}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mark_group_wraps_a_datum_as_the_reference_does() {
        let mut svg = String::new();
        mark_group_open(&mut svg, "rect", "layer_0_marks").unwrap();
        svg.push_str("<path aria-label=\"x\"/>");
        mark_group_close(&mut svg);
        assert_eq!(
            svg,
            "<g class=\"mark-rect role-mark layer_0_marks\" role=\"graphics-object\" \
             aria-roledescription=\"rect mark container\"><path aria-label=\"x\"/></g>"
        );
    }

    #[test]
    fn a_hidden_mark_set_is_aria_hidden_with_bare_items() {
        let svg = print(&[
            item_group("rule", "layer_1_marks", ItemRole::RuleMark, Reads::Hidden, |aria| Node::Line(aria)).unwrap()
        ]);
        assert_eq!(
            svg,
            "<g class=\"mark-rule role-mark layer_1_marks\" aria-hidden=\"true\"><line/></g>"
        );
    }

    #[test]
    fn item_group_announces_a_label_and_leaves_a_bare_mark_ungrouped() {
        let item = |aria: Attrs| Node::Line(aria);
        let announced =
            print(&[item_group("rule", "layer_1_marks", ItemRole::RuleMark, Reads::Label("v: 1"), item).unwrap()]);
        assert_eq!(
            announced,
            "<g class=\"mark-rule role-mark layer_1_marks\" role=\"graphics-object\" \
             aria-roledescription=\"rule mark container\"><line data-dct-role=\"rule\" \
             aria-label=\"v: 1\" role=\"graphics-symbol\" aria-roledescription=\"rule mark\"/></g>"
        );
        let bare = print(&[item_group("rule", "layer_1_marks", ItemRole::RuleMark, Reads::Bare, item).unwrap()]);
        assert_eq!(bare, "<line/>");
    }

    #[test]
    fn value_label_group_carries_the_stamp_between_class_and_role() {
        let svg = print(&[value_label_group("layer_2_marks", Vec::new()).unwrap()]);
        assert_eq!(
            svg,
            "<g class=\"mark-text role-mark layer_2_marks\" data-dbt-value-label=\"true\" \
             role=\"graphics-object\" aria-roledescription=\"text mark container\"></g>"
        );
    }

    #[test]
    fn the_view_root_takes_the_marks_class_after_xmlns() {
        let root = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"5\"><g/></svg>";
        assert_eq!(
            stamp_view_root(root.to_string()).unwrap(),
            "<svg xmlns=\"http://www.w3.org/2000/svg\" class=\"marks\" width=\"10\" height=\"5\"><g/></svg>"
        );
    }

    #[test]
    fn a_root_that_cannot_take_the_marks_class_is_an_error() {
        assert!(stamp_view_root("<g/>".into()).is_err());
        let classed = "<svg xmlns=\"http://www.w3.org/2000/svg\" class=\"x\"></svg>";
        assert!(stamp_view_root(classed.into()).is_err());
    }

    #[test]
    fn aria_attr_stamps_role_and_roledescription_beside_the_label() {
        assert_eq!(
            aria_attr(Some("a: 1"), ItemRole::Bar).unwrap(),
            " data-dct-role=\"bar\" aria-label=\"a: 1\" role=\"graphics-symbol\" aria-roledescription=\"bar\""
        );
        assert_eq!(aria_attr(None, ItemRole::Bar).unwrap(), "");
    }

    /// The description strings Vega/Vega-Lite write on the reference corpus's
    /// marks, and the machine key `chart_interactivity.js` matches instead.
    #[test]
    fn item_roles_spell_the_description_and_the_key() {
        for (role, description, key) in [
            (ItemRole::Bar, "bar", "bar"),
            (ItemRole::ArcMark, "arc mark", "arc"),
            (ItemRole::RectMark, "rect mark", "rect"),
            (ItemRole::Point, "point", "point"),
            (ItemRole::RuleMark, "rule mark", "rule"),
            (ItemRole::TextMark, "text mark", "text"),
            (ItemRole::PathMark, "path mark", "path"),
        ] {
            assert_eq!(
                aria_attr(Some("x"), role).unwrap(),
                format!(
                    " data-dct-role=\"{key}\" aria-label=\"x\" role=\"graphics-symbol\" \
                     aria-roledescription=\"{description}\""
                ),
                "{role:?}"
            );
        }
    }

    /// `waterfall`'s rows: a color-split bar's series and order lead its values.
    #[test]
    fn a_colored_span_label_leads_its_values_with_series_and_order() {
        let row = |title: &str, value: &str| SpanRow { title: title.into(), value: value.into(), muted: false, swatch: None };
        assert_eq!(
            span_aria_label("Churn", Some("Decrease"), Some(0), &[row("Δ", "\u{2212}$22")]),
            "\u{2061}Churn; \u{2062}Decrease; \u{200c}0; Δ: \u{2212}$22"
        );
    }

    /// `floating_bar`'s and `gantt`'s rows, copied from the reference SVGs:
    /// a muted row leads with the word joiner and an empty title.
    #[test]
    fn a_span_label_joins_the_header_and_its_rows() {
        let row = |title: &str, value: &str, muted| SpanRow { title: title.into(), value: value.into(), muted, swatch: None };
        assert_eq!(
            span_aria_label(
                "Scope",
                None,
                None,
                &[
                    row("start date", "5 Jan 2026", false),
                    row("Duration", "2.6 weeks", false),
                    row("", "18 days", true),
                ]
            ),
            "\u{2061}Scope; start date: 5 Jan 2026; Duration: 2.6 weeks; \u{2060}: 18 days"
        );
    }

    // Every string below is copied verbatim from a live retired per-chart sweep dump
    // (`the retired per-chart sweep --group ... --dump-svgs`), never fitted by hand.

    /// `charts/bar-charts_0/product_revenue_columns`: single-series, no
    /// `y_label` (default axis title, lowercase), tooltip_format `,.2~f`.
    #[test]
    fn single_series_bar_matches_the_corpus() {
        let t = BarTooltip {
            header: "Widget B",
            series: None,
            order: None,
            value_title: "revenue",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: None,
            extra: &[],
        };
        assert_eq!(bar_aria_label(&t, 75404.0).unwrap(), "⁡Widget B; revenue: 75,404");
    }

    /// `charts/bar-charts_2/ranked_products`: authored `y_label: Revenue`,
    /// a currency tooltip_format.
    #[test]
    fn currency_format_matches_the_corpus() {
        let t = BarTooltip {
            header: "Widget B",
            series: None,
            order: None,
            value_title: "Revenue",
            value_format: &FormatSlot::from("$,.0f"),
            percent: None,
            total: None,
            extra: &[],
        };
        assert_eq!(bar_aria_label(&t, 75404.0).unwrap(), "⁡Widget B; Revenue: $75,404");
    }

    /// `charts/color-channels_0/channel_revenue`: grouped bar (`stack:
    /// none`), a temporal (`yearmonth`) x, no board category-color scale
    /// bound to the color field -- no order role.
    #[test]
    fn grouped_bar_with_temporal_header_and_no_order_matches_the_corpus() {
        let t = BarTooltip {
            header: "Jan 2026",
            series: Some("Organic"),
            order: None,
            value_title: "revenue",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: None,
            extra: &[],
        };
        assert_eq!(
            bar_aria_label(&t, 42000.0).unwrap(),
            "⁡Jan 2026; ⁢Organic; revenue: 42,000"
        );
    }

    /// `charts/bar-charts_7/region_revenue_by_category_vertical` (vertical,
    /// `stack: zero`): always ranks its series and always carries a
    /// group-total footer. Asserts the FIRST (`Electronics`) and LAST
    /// (`Tools`) series, not the middle (`Accessories`, rank 1) -- a
    /// 3-element reversal leaves the middle rank fixed, so a middle-only
    /// assertion cannot catch `tooltip_order_domain` ranking over the
    /// wrong (paint, not display) domain. See `bar.rs`'s
    /// `tooltip_order_domain_tests` for the domain-level law.
    #[test]
    fn vertical_stacked_bar_ranks_over_the_reversed_display_domain() {
        let accessories = BarTooltip {
            header: "East",
            series: Some("Accessories"),
            order: Some(1),
            value_title: "revenue",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: Some(("Σ", 120588.0)),
            extra: &[],
        };
        assert_eq!(
            bar_aria_label(&accessories, 47368.0).unwrap(),
            "⁡East; ⁢Accessories; ‌1; revenue: 47,368; ⁣Σ: 120,588"
        );
        let electronics = BarTooltip { series: Some("Electronics"), order: Some(2), ..accessories };
        assert_eq!(
            bar_aria_label(&electronics, 58396.0).unwrap(),
            "⁡East; ⁢Electronics; ‌2; revenue: 58,396; ⁣Σ: 120,588"
        );
        let tools = BarTooltip { series: Some("Tools"), order: Some(0), ..accessories };
        assert_eq!(
            bar_aria_label(&tools, 14824.0).unwrap(),
            "⁡East; ⁢Tools; ‌0; revenue: 14,824; ⁣Σ: 120,588"
        );
    }

    /// `charts/bar-charts_8/region_revenue_by_category` (horizontal, same
    /// `stack_order` as the vertical case above): ranks over the
    /// UNREVERSED domain -- `Electronics` (the paint order's own first
    /// entry) ranks first here, `Tools` last, the opposite of the vertical
    /// chart above despite identical series sums.
    #[test]
    fn horizontal_stacked_bar_ranks_over_the_unreversed_paint_domain() {
        let electronics = BarTooltip {
            header: "East",
            series: Some("Electronics"),
            order: Some(0),
            value_title: "revenue",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: Some(("Σ", 120588.0)),
            extra: &[],
        };
        assert_eq!(
            bar_aria_label(&electronics, 58396.0).unwrap(),
            "⁡East; ⁢Electronics; ‌0; revenue: 58,396; ⁣Σ: 120,588"
        );
        let tools = BarTooltip { series: Some("Tools"), order: Some(2), ..electronics };
        assert_eq!(
            bar_aria_label(&tools, 14824.0).unwrap(),
            "⁡East; ⁢Tools; ‌2; revenue: 14,824; ⁣Σ: 120,588"
        );
    }

    /// `playground/stacked-bar-stress-test/horizontal_100`: `stack:
    /// normalize` prepends a `%` row (fixed `.0%`, never the chart's
    /// own tooltip_format) ahead of the raw value.
    #[test]
    fn normalized_stack_share_row_matches_the_corpus() {
        let t = BarTooltip {
            header: "high",
            series: Some("new"),
            order: Some(0),
            value_title: "ticket count",
            value_format: &FormatSlot::from(",.2~f"),
            percent: Some(47.0 / 69.0),
            total: Some(("Σ", 69.0)),
            extra: &[],
        };
        assert_eq!(
            bar_aria_label(&t, 47.0).unwrap(),
            "⁡high; ⁢new; ‌0; %: 68%; ticket count: 47; ⁣Σ: 69"
        );
    }

    /// A format spec `d3rs::format` rejects refuses BY NAME -- the
    /// module's headline law (never an approximate label). No corpus
    /// row hits this; a bad tooltip_format is a genuinely unsupported
    /// shape, not a silently-mangled one.
    #[test]
    fn an_unsupported_format_spec_refuses_rather_than_mislabel() {
        let t = BarTooltip {
            header: "Jan",
            series: None,
            order: None,
            value_title: "revenue",
            value_format: &FormatSlot::from("not-a-real-spec"),
            percent: None,
            total: None,
            extra: &[],
        };
        assert!(bar_aria_label(&t, 100.0).is_err());
    }

    /// `playground/time-unit-label-cadence-matrix/month_third_width_bar`:
    /// a temporal x header built from `temporal::tooltip_header_date`.
    #[test]
    fn temporal_header_matches_the_corpus() {
        let _en = crate::time_locale::en_us_scope();
        let header = crate::families::temporal::tooltip_header_date(
            2024,
            2,
            1,
            Some(crate::families::temporal::TimeUnit::YearMonth),
        ).unwrap();
        let t = BarTooltip {
            header: &header,
            series: None,
            order: None,
            value_title: "value",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: None,
            extra: &[],
        };
        assert_eq!(bar_aria_label(&t, 60.0).unwrap(), "⁡Feb 2024; value: 60");
    }

    // ── tooltip_header_text ──────────────────────────────────────────────

    /// A bucketed grain formats through it regardless of `is_standard_date`.
    #[test]
    fn tooltip_header_text_formats_a_detected_bucket() {
        let _en = crate::time_locale::en_us_scope();
        use crate::families::temporal::TimeUnit;
        assert_eq!(
            tooltip_header_text("2024-02-01", Some(TimeUnit::YearMonth), false, None).unwrap(),
            "Feb 2024"
        );
    }

    /// Oracle: production's tooltip aria-label for a bar, line and area over
    /// `2024-02-01` under `style.formats.date_month: "%b '%y"` is
    /// `Feb '24; revenue: 150`: the board's spec replaces the grain vocabulary,
    /// at the detected grain only.
    #[test]
    fn tooltip_header_text_paints_a_redefined_grain() {
        let _locale = crate::time_locale::en_us_scope();
        use crate::families::temporal::{AuthoredTimeFormat, TimeUnit};
        let spec = AuthoredTimeFormat::parse("test", Some("%b '%y")).unwrap();
        assert_eq!(
            tooltip_header_text("2024-02-01", Some(TimeUnit::YearMonth), false, spec).unwrap(),
            "Feb '24"
        );
        assert_eq!(tooltip_header_text("Enterprise", None, false, spec).unwrap(), "Enterprise");
    }

    /// `playground/chart-lab-import-review/area_review`: a 28-day cadence
    /// -- every value is a STANDARD date shape (`YYYY-MM-DD`), but
    /// `detect_time_unit` finds no bucketed grain from it. Production's
    /// `infer_vega_type_from_data` still types this field "temporal"
    /// (`all_temporal`, unconditional on a detected bucket), so the header
    /// formats through the continuous-date fallback ("27 Apr 2025"),
    /// never the raw ISO string.
    #[test]
    fn tooltip_header_text_formats_a_standard_date_with_no_detected_bucket() {
        let _en = crate::time_locale::en_us_scope();
        assert_eq!(tooltip_header_text("2025-04-27", None, true, None).unwrap(), "27 Apr 2025");
        // An offset timestamp names its instant's day in the board zone: on a UTC board, March 11.
        assert_eq!(tooltip_header_text("2024-03-10T23:00:00-05:00", None, true, None).unwrap(), "11 Mar 2024");
        let _zone = crate::time_zone::zone_scope("America/New_York");
        assert_eq!(tooltip_header_text("2024-03-02T03:00:00Z", None, true, None).unwrap(), "1 Mar 2024");
        assert_eq!(tooltip_header_text("2024-03-02T03:00:00", None, true, None).unwrap(), "2 Mar 2024");
    }

    /// Neither a detected bucket nor a standard-date shape (a plain
    /// nominal category, or the looser "date-like" ordinal shape
    /// `detect_time_unit` failed to bucket) -- the raw string passes
    /// through unformatted.
    #[test]
    fn tooltip_header_text_passes_through_a_plain_category() {
        assert_eq!(tooltip_header_text("Enterprise", None, false, None).unwrap(), "Enterprise");
    }

    // ── PieTooltip / pie_aria_label ─────────────────────────────────────

    /// `charts/sector-charts_0/revenue_share_hero`: `total.visible: true`
    /// (a center-total display IS configured) -- the tooltip's own `Σ`
    /// row is unaffected either way, see `PieTooltip`'s doc comment.
    #[test]
    fn pie_slice_with_a_center_total_display_matches_the_corpus() {
        let t = PieTooltip {
            header: Some("Electronics"),
            share: 197_199.0 / 443_023.0,
            value_title: "Revenue",
            value_format: &FormatSlot::from(",.2~f"),
            value: 197_199.0,
            total: 443_023.0,
            extra: &[],
        };
        assert_eq!(
            pie_aria_label(&t).unwrap(),
            "⁤Electronics; %: 45%; \u{2060}Revenue: 197,199; \u{2060}⁣Σ: 443,023"
        );
    }

    /// `charts/sector-charts_1/revenue_share_minimal`: same rows, NO
    /// `total` config at all -- the identical `Σ` tooltip row still
    /// renders, proving the row is unconditional.
    #[test]
    fn pie_slice_with_no_center_total_display_still_carries_the_total_row() {
        let t = PieTooltip {
            header: Some("Tools"),
            share: 93_320.0 / 443_023.0,
            value_title: "Revenue",
            value_format: &FormatSlot::from(",.2~f"),
            value: 93_320.0,
            total: 443_023.0,
            extra: &[],
        };
        assert_eq!(
            pie_aria_label(&t).unwrap(),
            "⁤Tools; %: 21%; \u{2060}Revenue: 93,320; \u{2060}⁣Σ: 443,023"
        );
    }

    /// `charts/sector-charts_10/by_product`: a different theta field
    /// ("units") title-cases through `slug_to_display` to "Units", not
    /// "Units Sold" -- the raw field name, never a resolved axis label.
    #[test]
    fn pie_slice_with_a_different_theta_field_matches_the_corpus() {
        let t = PieTooltip {
            header: Some("Gadget X"),
            share: 1_786.0 / 14_849.0,
            value_title: "Units",
            value_format: &FormatSlot::from(",.2~f"),
            value: 1_786.0,
            total: 14_849.0,
            extra: &[],
        };
        assert_eq!(
            pie_aria_label(&t).unwrap(),
            "⁤Gadget X; %: 12%; \u{2060}Units: 1,786; \u{2060}⁣Σ: 14,849"
        );
    }

    /// A pie with no identity field authored carries no header row at
    /// all -- not an empty swatched marker, no `ROLE_HEADER_SWATCHED`
    /// token at all. No corpus row hits this (every sector-charts pie
    /// authors an identity field); this exercises the `None` branch.
    #[test]
    fn pie_slice_with_no_identity_field_omits_the_header_row() {
        let t = PieTooltip {
            header: None,
            share: 0.5,
            value_title: "Revenue",
            value_format: &FormatSlot::from(",.2~f"),
            value: 50.0,
            total: 100.0,
            extra: &[],
        };
        assert_eq!(
            pie_aria_label(&t).unwrap(),
            "%: 50%; \u{2060}Revenue: 50; \u{2060}⁣Σ: 100"
        );
    }

    /// Same headline law as bar: a format spec `d3rs::format` rejects
    /// refuses BY NAME rather than drawing an approximate label. No
    /// corpus row hits this.
    #[test]
    fn pie_unsupported_format_spec_refuses_rather_than_mislabel() {
        let t = PieTooltip {
            header: Some("Electronics"),
            share: 0.45,
            value_title: "Revenue",
            value_format: &FormatSlot::from("not-a-real-spec"),
            value: 197_199.0,
            total: 443_023.0,
            extra: &[],
        };
        assert!(pie_aria_label(&t).is_err());
    }

    // ── ScatterTooltip / scatter_aria_label ──────────────────────────────

    /// `charts/point-charts_0/spend_vs_pipeline`: no color channel -- no
    /// header row, both peer rows quantitative.
    #[test]
    fn scatter_uncolored_pair_matches_the_corpus() {
        let t = ScatterTooltip {
            header: None,
            y_title: "pipeline value",
            y_value: ScatterValue::Quantitative(140.0, &FormatSlot::from(",.2~f")),
            x_title: "ad spend",
            x_value: ScatterValue::Quantitative(18.0, &FormatSlot::from(",.2~f")),
            extra: &[],
        };
        assert_eq!(scatter_aria_label(&t).unwrap(), "pipeline value: 140; ad spend: 18");
    }

    /// `charts/point-charts_2/team_response_dots`: a dot plot -- y ("Team")
    /// is nominal, not quantitative; its own row still carries a title
    /// (unlike bar/pie's bare header/series rows) since scatter's value
    /// rows always go through `_row_expr`, never `_bare_row_expr`.
    #[test]
    fn scatter_dot_plot_nominal_y_matches_the_corpus() {
        let t = ScatterTooltip {
            header: None,
            y_title: "Team",
            y_value: ScatterValue::Nominal("Escalations"),
            x_title: "Hours",
            x_value: ScatterValue::Quantitative(5.3, &FormatSlot::from(",.2~f")),
            extra: &[],
        };
        assert_eq!(scatter_aria_label(&t).unwrap(), "Team: Escalations; Hours: 5.3");
    }

    /// `charts/point-charts_3/experiment_scatter`: a bound color channel
    /// promotes to a swatched header, raw value only (bare row, same "no
    /// title" law as bar/pie).
    #[test]
    fn scatter_colored_pair_matches_the_corpus() {
        let t = ScatterTooltip {
            header: Some("Baseline"),
            y_title: "lift",
            y_value: ScatterValue::Quantitative(0.10, &FormatSlot::from(".0%")),
            x_title: "confidence",
            x_value: ScatterValue::Quantitative(0.68, &FormatSlot::from(".0%")),
            extra: &[],
        };
        assert_eq!(scatter_aria_label(&t).unwrap(), "⁤Baseline; lift: 10%; confidence: 68%");
    }

    /// Same headline law as bar/pie: an unsupported format spec refuses BY
    /// NAME. No corpus row hits this.
    #[test]
    fn scatter_unsupported_format_spec_refuses_rather_than_mislabel() {
        let t = ScatterTooltip {
            header: None,
            y_title: "pipeline value",
            y_value: ScatterValue::Quantitative(140.0, &FormatSlot::from("not-a-real-spec")),
            x_title: "ad spend",
            x_value: ScatterValue::Quantitative(18.0, &FormatSlot::from(",.2~f")),
            extra: &[],
        };
        assert!(scatter_aria_label(&t).is_err());
    }

    /// `charts/extensibility_1/dot_plot`: category header, then the bare
    /// series row, then the titled measure -- not the swatched-series-first
    /// order a colored scatter carries.
    #[test]
    fn dot_plot_category_heads_and_series_follows() {
        let t = DotPlotTooltip {
            category: "Region E",
            series: "2019",
            order: None,
            value_title: "value",
            value: 59.1,
            value_format: &FormatSlot::from(",.2~f"),
            extra: &[],
        };
        assert_eq!(dot_plot_aria_label(&t).unwrap(), "\u{2061}Region E; \u{2062}2019; value: 59.1");
        let ranked = DotPlotTooltip { order: Some(1), ..t };
        assert_eq!(
            dot_plot_aria_label(&ranked).unwrap(),
            "\u{2061}Region E; \u{2062}2019; \u{200C}1; value: 59.1"
        );
    }

    // ── HeatmapTooltip / heatmap_aria_label ──────────────────────────────

    /// `charts/more-types_0/heatmap_example`: both x and y bare `ROLE_
    /// HEADER` rows (never swatched -- color is the dependent, not an
    /// identity), one labeled value row.
    #[test]
    fn heatmap_cell_matches_the_corpus() {
        let t = HeatmapTooltip {
            x: "East",
            y: "Accessories",
            value_title: "Revenue",
            value_format: &FormatSlot::from(",.2~f"),
            value: HeatmapValue::Number(47_368.0),
        };
        assert_eq!(
            heatmap_aria_label(&t).unwrap(),
            "⁡East; ⁡Accessories; Revenue: 47,368"
        );
    }

    /// Same headline law as bar/pie/scatter: an unsupported format spec
    /// refuses BY NAME. No corpus row hits this.
    #[test]
    fn heatmap_unsupported_format_spec_refuses_rather_than_mislabel() {
        let t = HeatmapTooltip {
            x: "East",
            y: "Accessories",
            value_title: "Revenue",
            value_format: &FormatSlot::from("not-a-real-spec"),
            value: HeatmapValue::Number(47_368.0),
        };
        assert!(heatmap_aria_label(&t).is_err());
    }

    // ── authored `tooltip:` rows ────────────────────────────────────────

    fn extras() -> Vec<String> {
        vec![extra_row("Approved", "yes"), extra_row("% of budget", "25.0%")]
    }

    /// The authored rows follow the mark's own value rows and precede the Σ footer.
    #[test]
    fn bar_extras_follow_the_value_row_and_precede_the_total() {
        let extra = extras();
        let t = BarTooltip {
            header: "East",
            series: Some("Tools"),
            order: Some(0),
            value_title: "revenue",
            value_format: &FormatSlot::from(",.2~f"),
            percent: None,
            total: Some(("Σ", 120588.0)),
            extra: &extra,
        };
        assert_eq!(
            bar_aria_label(&t, 14824.0).unwrap(),
            "⁡East; ⁢Tools; ‌0; revenue: 14,824; \u{200D}Approved: yes; \u{200D}% of budget: 25.0%; ⁣Σ: 120,588"
        );
    }

    #[test]
    fn pie_extras_follow_the_value_row_and_precede_the_total() {
        let extra = extras();
        let t = PieTooltip {
            header: Some("Tools"),
            share: 0.5,
            value_title: "Revenue",
            value_format: &FormatSlot::from(",.2~f"),
            value: 50.0,
            total: 100.0,
            extra: &extra,
        };
        assert_eq!(
            pie_aria_label(&t).unwrap(),
            "⁤Tools; %: 50%; \u{2060}Revenue: 50; \u{200D}Approved: yes; \u{200D}% of budget: 25.0%; \u{2060}⁣Σ: 100"
        );
    }

    #[test]
    fn scatter_extras_follow_the_x_row() {
        let extra = extras();
        let t = ScatterTooltip {
            header: None,
            y_title: "pipeline value",
            y_value: ScatterValue::Quantitative(140.0, &FormatSlot::from(",.2~f")),
            x_title: "ad spend",
            x_value: ScatterValue::Quantitative(18.0, &FormatSlot::from(",.2~f")),
            extra: &extra,
        };
        assert_eq!(
            scatter_aria_label(&t).unwrap(),
            "pipeline value: 140; ad spend: 18; \u{200D}Approved: yes; \u{200D}% of budget: 25.0%"
        );
    }

    #[test]
    fn dot_plot_extras_follow_the_value_row() {
        let extra = extras();
        let t = DotPlotTooltip {
            category: "Region E",
            series: "2019",
            order: None,
            value_title: "value",
            value: 59.1,
            value_format: &FormatSlot::from(",.2~f"),
            extra: &extra,
        };
        assert_eq!(
            dot_plot_aria_label(&t).unwrap(),
            "\u{2061}Region E; \u{2062}2019; value: 59.1; \u{200D}Approved: yes; \u{200D}% of budget: 25.0%"
        );
    }

    /// A (category, series) slot a sparse stack lacks a row for is not a value:
    /// it keeps its identity so the hover band covers the slot, and reports nothing.
    #[test]
    fn an_imputed_stack_datum_reports_no_value() {
        assert_eq!(imputed_aria_label("Feb 2024", "APAC", Some(2)), "⁡Feb 2024; ⁢APAC; ‌2; \u{34F}");
    }

    // ── data-dct-role machine keys ──────────────────────────────────────

    #[test]
    fn axis_groups_carry_a_machine_role_key() {
        let mut out = String::new();
        axis_group_open(&mut out, AxisChannel::X, &empty_caption(false), (0.0, 0.0)).unwrap();
        axis_group_open(&mut out, AxisChannel::Y, &empty_caption(false), (0.0, 0.0)).unwrap();
        assert_eq!(out.matches(" data-dct-role=\"x-axis\"").count(), 1);
        assert_eq!(out.matches(" data-dct-role=\"y-axis\"").count(), 1);
    }

    fn empty_caption(bare: bool) -> AxisCaption {
        AxisCaption {
            title: None,
            scale: crate::families::axis_caption::CaptionScale::Discrete { values: Vec::new(), format: None },
            bare,
        }
    }

    #[test]
    fn a_bare_axis_group_unwraps_and_keeps_what_it_held() {
        let mut out = String::from("<g>");
        axis_group_open(&mut out, AxisChannel::X, &empty_caption(true), (1.0, 2.0)).unwrap();
        out.push_str("<text>t</text>");
        axis_group_close(&mut out);
        assert_eq!(out, "<g><text>t</text>");
    }

    #[test]
    fn an_axis_group_describes_its_scale() {
        let mut out = String::new();
        axis_group_open(&mut out, AxisChannel::Y, &empty_caption(false), (0.0, 0.0)).unwrap();
        assert!(out.contains(" aria-label=\"Y-axis for a discrete scale with 0 values: \""), "{out}");
    }

    #[test]
    fn the_group_builders_print_what_the_open_and_close_pairs_write() {
        let mut open = String::new();
        mark_group_open(&mut open, "rect", "marks").unwrap();
        open.push_str("<rect/>");
        mark_group_close(&mut open);
        let built = mark_group("rect", "marks", vec![Node::Rect(Attrs::new())]).unwrap();
        assert_eq!(print(&[built]), open);

        let (origin, mut open) = ((2.5, -4.0), String::new());
        axis_group_open(&mut open, AxisChannel::X, &empty_caption(false), origin).unwrap();
        open.push_str("<rect/>");
        axis_group_close(&mut open);
        let built = axis_group(AxisChannel::X, &empty_caption(false), origin, vec![Node::Rect(Attrs::new())]).unwrap();
        assert_eq!(print(&built), open);

        let mut bare = String::new();
        axis_group_open(&mut bare, AxisChannel::Y, &empty_caption(true), origin).unwrap();
        bare.push_str("<rect/>");
        axis_group_close(&mut bare);
        let built = axis_group(AxisChannel::Y, &empty_caption(true), origin, vec![Node::Rect(Attrs::new())]).unwrap();
        assert_eq!(print(&built), bare);
        assert_eq!(bare, "<rect/>");

        assert_eq!(
            print(&[Node::group(axis_title_attrs(Some(LabelKey::Y)), vec![])]),
            "<g class=\"mark-text role-axis-title\" pointer-events=\"none\" data-authored-kind=\"y_label\"></g>"
        );
    }

    #[test]
    fn aria_attrs_are_the_attribute_run_aria_attr_writes() {
        assert_eq!(
            aria_attr(Some("a \"b\""), ItemRole::RectMark).unwrap(),
            print::attributes(&aria_attrs(Some("a \"b\""), ItemRole::RectMark).unwrap())
        );
        assert_eq!(aria_attr(None, ItemRole::Bar).unwrap(), "");
    }

    #[test]
    fn a_ghost_layer_holds_one_invisible_rule_per_datum() {
        let rules = [GhostRule { aria: Some("a: 1"), x: 10.0, y: 20.0, stroke: "#abc", anchor: None }];
        let out = print(&[ghost_layer(&rules, GhostRun::ToOrigin).unwrap()]);
        assert!(
            out.contains("<line transform=\"translate(10.000,20.000)\" x2=\"-10.000\" y2=\"-20.000\" stroke=\"#abc\" opacity=\"0\" data-dct-role=\"rule\" aria-label=\"a: 1\""),
            "{out}"
        );
    }

    // ── authored_kind_attr / safe_svg_id ────────────────────────────────

    #[test]
    fn authored_kind_attr_matches_svg_utils_py() {
        assert_eq!(authored_kind_attr("title"), " data-authored-kind=\"title\"");
        assert_eq!(authored_kind_attr("subtitle"), " data-authored-kind=\"subtitle\"");
    }

    /// Mirrors `test_namespace_svg_ids_disambiguates_chart_ids_that_
    /// sanitize_to_the_same_token` / `test_namespace_svg_ids_sanitizes_
    /// chart_id_for_funciri_safety` (test_chart_converter.py).
    #[test]
    fn safe_svg_id_matches_production() {
        assert_eq!(safe_svg_id("a_b"), "a_b", "an already-safe chart id is untouched");
        assert_eq!(safe_svg_id("a b"), "a_b-0cc9cd4d");
        assert_ne!(
            safe_svg_id("a b"),
            safe_svg_id("a_b"),
            "colliding-after-sanitize ids must not share a suffix"
        );
        assert_eq!(safe_svg_id("a)b"), "a_b-52f450cd");
    }
}

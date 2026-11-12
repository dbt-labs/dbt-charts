//! The front-end SVG contract's builders (D-34) every family's painter shares: the `.role-mark` group a
//! mark set sits in ([`mark_group`], [`value_label_group`]), and the `aria-label` / `data-dct-role`
//! attributes of a mark's items ([`ItemRole`], [`aria_attrs`]). chart-renderer imports them.
//! `chart_interactivity.js` reads both off a rendered chart. See `docs/laws-shared.md#front-end-contract`.

use d3rs::format::{format, parse_parts, vega_tick_format, Locale};
use display_text::language::tr;
use scene::{Attrs, Node, Role};

/// A Vega mark-set's `.role-mark` group, holding `children`: `chart_interactivity.js`'s
/// `isDataMark` classifies an element by its NEAREST ancestor carrying
/// `role-mark`/`role-axis`/`role-legend`/`role-title`, so wrapping the datum here is what makes
/// its own `aria-label` readable as a data mark (`chart_svg_dev/contract.py::_group_role`).
/// `kind` is Vega's own mark tag ("rect", "rule"); `name` is cosmetic -- the extractor never
/// reads the group's third class token.
pub fn mark_group(kind: &str, name: &str, children: Vec<Node>) -> Result<Node, String> {
    Ok(Node::group(Role::Chrome, mark_group_attrs(kind, name, false)?, children))
}

/// A mark set hidden from the accessibility tree: its marks carry no description, so a hit on one
/// resolves to the nearest labeled mark.
pub fn hidden_mark_group(kind: &str, name: &str, children: Vec<Node>) -> Node {
    Node::group(Role::Chrome, Attrs::new().set("class", mark_class(kind, name)).set("aria-hidden", "true"), children)
}

/// The `.role-mark` group of a connected mark (`line`, `area`). The path carries no description, so
/// the group is bare, as Vega leaves it (`chart-renderer`'s `contract::connected_group`).
pub fn connected_group(kind: &str, name: &str, children: Vec<Node>) -> Node {
    Node::group(Role::Chrome, Attrs::new().set("class", mark_class(kind, name)), children)
}

pub fn value_label_group(name: &str, children: Vec<Node>) -> Result<Node, String> {
    Ok(Node::group(Role::Chrome, mark_group_attrs("text", name, true)?, children))
}

fn mark_class(kind: &str, name: &str) -> String {
    format!("mark-{kind} role-mark {name}")
}

fn mark_group_attrs(kind: &str, name: &str, value_label: bool) -> Result<Attrs, String> {
    let container = tr(&format!("aria.container_{kind}"), None, &[])?;
    Ok(Attrs::new()
        .set("class", mark_class(kind, name))
        .set_if(value_label, "data-dbt-value-label", "true")
        .set("role", "graphics-object")
        .set("aria-roledescription", container))
}

/// `.role-axis` around the axis's coordinate frame, `key` its `data-dct-role` (`x-axis` or
/// `y-axis`), and the second `<g>` that undoes the frame's translate: items are drawn in plot
/// coordinates, and the runtime reads the frame's origin as the axis baseline.
pub fn axis_frame(key: &str, label: &str, (ox, oy): (f64, f64), children: Vec<Node>) -> Node {
    let attrs = Attrs::new()
        .set("class", "mark-group role-axis")
        .set("data-dct-role", key)
        .set("role", "graphics-symbol")
        .set("aria-roledescription", "axis")
        .set("aria-label", label);
    let undo = Node::group(Role::Axis, Attrs::new(), children).translate(-ox, -oy);
    let frame = Node::group(Role::Axis, Attrs::new(), vec![undo]).translate(ox, oy);
    Node::group(Role::Axis, attrs, vec![frame])
}

/// The group Vega draws around an axis's gridlines, holding `children`.
pub fn grid_group(children: Vec<Node>) -> Node {
    Node::group(Role::Grid, Attrs::new().set("class", "mark-rule role-axis-grid").set("aria-hidden", "true"), children)
}

/// The attributes of the group Vega draws around an axis title, stamped with the authored key it
/// edits (`x_label` or `y_label`) when the chart's channels pin it unambiguously
/// (`converters/chart.py::_stamp_axis_title_kinds`).
pub fn axis_title_attrs(authored: Option<&str>) -> Attrs {
    let attrs = Attrs::new().set("class", "mark-text role-axis-title").set("pointer-events", "none");
    match authored {
        Some(kind) => attrs.set("data-authored-kind", kind),
        None => attrs,
    }
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

/// The attributes a datum's first drawn element carries: its `aria-label` plus the
/// `role="graphics-symbol"` / `aria-roledescription` pair Vega stamps on every item that has a
/// description. Empty when the shape wires no label (an unlabeled mark is bare in production
/// too, never an approximation).
pub fn aria_attrs(label: Option<&str>, role: ItemRole) -> Result<Attrs, String> {
    let Some(label) = label else { return Ok(Attrs::new()) };
    let (description_id, key) = role.description_id_and_key();
    Ok(Attrs::new()
        .set("data-dct-role", key)
        .set("aria-label", label)
        .set("role", "graphics-symbol")
        .set("aria-roledescription", tr(description_id, None, &[])?))
}

#[cfg(test)]
mod tests {
    use super::*;
    use print::print;

    #[test]
    fn value_label_group_carries_the_stamp_between_class_and_role() {
        let svg = print(&[value_label_group("layer_2_marks", Vec::new()).unwrap()]);
        assert_eq!(
            svg,
            "<g class=\"mark-text role-mark layer_2_marks\" data-dbt-value-label=\"true\" \
             role=\"graphics-object\" aria-roledescription=\"text mark container\"></g>"
        );
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
                print::attributes(&aria_attrs(Some("x"), role).unwrap()),
                format!(
                    " data-dct-role=\"{key}\" aria-label=\"x\" role=\"graphics-symbol\" \
                     aria-roledescription=\"{description}\""
                ),
                "{role:?}"
            );
        }
    }
}

/// Vega's description of an axis (`axisCaption`), in English whatever the board's language:
/// `channel` is `X` or `Y`, `kind` the scale's type and `domain` what the scale holds.
pub fn axis_description(channel: &str, title: Option<&str>, kind: &str, domain: &str) -> String {
    let titled = title.map(|t| format!(" titled '{t}'")).unwrap_or_default();
    format!("{channel}-axis{titled} for a {kind} scale with {domain}")
}

/// A continuous domain's description: its two ends, already printed.
pub fn span_description(from: &str, to: &str) -> String {
    format!("values from {from} to {to}")
}

/// A value of a linear scale over `domain` as its description prints it: a spec with a precision
/// prints as written; otherwise the precision follows the step of five ticks, as vega-format's
/// `formatSpan` derives it.
pub fn linear_value_text(locale: &Locale, spec: Option<&str>, domain: (f64, f64), value: f64) -> Result<String, String> {
    if let Some(spec) = spec.filter(|spec| parse_parts(spec).is_ok_and(|parts| parts.precision.is_some())) {
        return format(locale, spec, value);
    }
    vega_tick_format(locale, spec, domain.0, domain.1, 5, value)
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

/// One entry's swatch group (`role-legend-symbol`); a `linked` entry takes the pointer so its
/// `<a>` receives the click.
pub fn legend_symbol_attrs(linked: bool) -> Attrs {
    Attrs::new().set("class", "mark-symbol role-legend-symbol").set_if(!linked, "pointer-events", "none")
}

const LEGEND_LABEL_CLASS: &str = "mark-text role-legend-label";

/// The series a legend entry, a direct label, or a line or area's own ink belongs to.
pub const SERIES_KEY: &str = "data-dbt-series";

/// The group Vega draws around legend label text (`role-legend-label`): the
/// runtime reads a legend's labels from it. A gradient legend has one for all
/// its tick labels, a symbol legend one per entry. `series_key` is the
/// `data-dbt-series` join key (`_stamp_series_keys`), `None` when none is baked.
pub fn legend_label_attrs(series_key: Option<&str>, linked: bool) -> Attrs {
    let attrs = Attrs::new().set("class", LEGEND_LABEL_CLASS).set_if(!linked, "pointer-events", "none");
    match series_key {
        Some(key) => attrs.set(SERIES_KEY, key),
        None => attrs,
    }
}

//! The front-end SVG contract's builders every family's painter shares: the `.role-mark` group a
//! mark set sits in, and the `aria-label` / `data-dct-role` attributes of a mark's items.
//! `chart_interactivity.js` reads both off a rendered chart. See `docs/laws-shared.md#front-end-contract`.

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

//! Python's `validate/palettes.py` and `validate/roles.py`: a palette field a board or chart
//! `style:` leaves as a name no shipped palette answers to (a theme role included, which only the
//! theme's own `palettes:` may use), and a `style.roles` address no palette satisfies.

use crate::scope::own_patch;
use board_model::board::layout::{LayoutAuthored, LayoutItemAuthored};
use board_model::cascade;
use board_model::chart::kpi::KpiBackground;
use board_model::chart::ChartAuthored;
use board_model::palette::hook::{python_refusal, unknown_name, unusable_name};
use board_model::palette::{color_from_theme, ThemeColors};
use board_model::primitives::{PaletteChoice, SeriesInk};
use board_model::style::style::Style;
use diagnostic::{diag, Diagnostic, ModelPath};
use model_core::{Leaf, Resolved, Tri};
use std::collections::BTreeMap;

type StylePatch = <Style as Leaf>::Patch;

/// Where a palette field was authored: Python validates a typed style at parse and a raw KPI
/// `background:` mapping only when the palette checks walk it.
#[derive(Clone, Copy, PartialEq)]
enum Site {
    Board,
    Chart,
    Raw,
}

/// The diagnostic for a palette refusal the color pass worded: `ERR-PALETTE-UNKNOWN` for a name no
/// palette answers to, `ERR-PALETTE-UNUSABLE` for one that cannot serve as written, each at its
/// authored field path. A typed gradient or scale palette, or a board's categorical one, that
/// Python's model refuses at parse is the code Python gives it. Any other refusal is not a
/// palette field's.
fn palette_fault(why: &str, field_path: String, site: Site) -> Option<Diagnostic> {
    let at = ModelPath(field_path.split('.').map(str::to_string).collect());
    let authored = unknown_name(why).or_else(|| unusable_name(why).map(|(a, _)| a));
    if let (Some(authored), true) = (authored, site != Site::Raw) {
        let slot = [".gradient.palette", ".scale.palette"]
            .iter()
            .find_map(|s| field_path.strip_suffix(".palette").filter(|_| field_path.ends_with(s)));
        if let Some(parent) = slot {
            if let Some(reason) = python_refusal(authored, true) {
                let at = ModelPath(parent.split('.').map(str::to_string).collect());
                return Some(
                    diag::err_validation_field(parent, format!("Value error, {reason}")).at_path(at),
                );
            }
        }
        let categorical = field_path.ends_with(".categorical.palette")
            || field_path.ends_with(".categorical.single_series_palette");
        if let (true, Site::Board) = (categorical, site) {
            if let Some(reason) = python_refusal(authored, false) {
                return Some(diag::err_palette_style_invalid(reason));
            }
        }
    }
    let found = if let Some(name) = unknown_name(why) {
        diag::err_palette_unknown(name, name, field_path)
    } else {
        let (authored, reason) = unusable_name(why)?;
        diag::err_palette_unusable(authored, field_path, reason)
    };
    Some(found.at_path(at))
}

/// Every palette field of `style` that names no shipped palette or cannot be used as written.
/// The color pass runs over an empty palette context, so a theme role is as unknown as a typo.
fn faults(style: &StylePatch, site: Site, path: impl Fn(&str) -> String) -> Vec<Diagnostic> {
    let mut style = style.clone();
    let Err(refused) = (cascade::TABLES.resolve_colors)(&mut style, &StylePatch::default()) else {
        return Vec::new();
    };
    refused
        .iter()
        .filter_map(|r| palette_fault(&r.why, path(r.at), site))
        .collect()
}

/// A KPI `background:` channel is a raw mapping no typed pass walks: every `palette` or
/// `single_series_palette` string in it is a name Python resolves, a `gradient` or `scale` subtree
/// keeping the name for a later bake.
fn background_fault(node: &serde_json::Value, name_only: bool, path: &str) -> Option<Diagnostic> {
    use serde_json::Value;
    match node {
        Value::Object(map) => map.iter().find_map(|(key, value)| {
            let here = format!("{path}.{key}");
            match value {
                Value::String(name) if key == "palette" || key == "single_series_palette" => {
                    let why = if name_only {
                        board_model::palette::hook::name_only_check(name)
                    } else {
                        board_model::palette::hook::name_check(name)
                    }
                    .err()?;
                    palette_fault(&why, here, Site::Raw)
                }
                _ => background_fault(
                    value,
                    name_only || key == "gradient" || key == "scale",
                    &here,
                ),
            }
        }),
        Value::Array(items) => items
            .iter()
            .find_map(|item| background_fault(item, name_only, path)),
        _ => None,
    }
}

/// Nested boards first, as Python recurses before it checks a board's own style.
fn nested(layout: &LayoutAuthored, out: &mut Vec<StylePatch>) {
    let items: Vec<&LayoutItemAuthored> = match layout {
        LayoutAuthored::Rows(items) | LayoutAuthored::Cols(items) => items.iter().collect(),
        LayoutAuthored::Grid(g) => g
            .items
            .iter()
            .flatten()
            .filter_map(|c| c.item.as_ref())
            .collect(),
        LayoutAuthored::Tabs(t) => {
            for tab in t.items.iter().flatten() {
                out.extend(own_patch(&tab.style));
                if let Some(inner) = &tab.layout {
                    nested(inner, out);
                }
            }
            return;
        }
    };
    for item in items {
        if let LayoutItemAuthored::Board(b) = item {
            if let Some(inner) = &b.body.layout {
                nested(inner, out);
            }
            out.extend(own_patch(&b.body.style));
        }
    }
}

type Patch = <board_model::board::Board as Leaf>::Patch;

/// The first palette field no shipped palette answers to, over every nested board's style, the
/// board's, then each chart's own (`public_id` names a chart by the key its patch holds it under).
pub fn palette_checks(
    patch: &Patch,
    public_id: &dyn Fn(&str) -> Option<String>,
) -> Option<Diagnostic> {
    let mut scopes = Vec::new();
    if let Some(layout) = &patch.body.layout {
        nested(layout, &mut scopes);
    }
    if let Tri::Value(own) = &patch.body.style {
        scopes.push((**own).clone());
    }
    let board_path = |at: &str| format!("style.{at}");
    let mut found: Vec<Diagnostic> = scopes
        .iter()
        .flat_map(|s| faults(s, Site::Board, board_path))
        .collect();
    for (key, chart) in &patch.body.charts {
        let (Some(chart), Some(id)) = (chart.as_ref(), public_id(key.as_str())) else {
            continue;
        };
        let styled = chart.style_overlay().map(|layer| {
            faults(&layer, Site::Chart, |at| {
                let below = at.strip_prefix("charts.").unwrap_or(at);
                let below = below.split_once('.').map_or(below, |(_, rest)| rest);
                format!("charts.{id}.style.{below}")
            })
        });
        found.extend(styled.unwrap_or_default());
        if let ChartAuthored::Kpi(k) = chart {
            if let Tri::Value(KpiBackground::Channel(spec)) = &k.background {
                found.extend(background_fault(spec, false, &format!("charts.{id}.background")));
            }
        }
    }
    // Python's models refuse these while the style parses, before any palette walk.
    let at_parse = |d: &&Diagnostic| {
        d.code == "ERR-VALIDATION-FIELD" || d.code == "ERR-PALETTE-STYLE-INVALID"
    };
    found
        .iter()
        .find(at_parse)
        .or_else(|| found.first())
        .cloned()
}

/// The first `style.roles` address of a scope that resolves to no color, as `ERR-COLOR-TOKEN`.
fn role_error(style: &Style) -> Option<Diagnostic> {
    let palettes = style
        .palettes
        .iter()
        .map(|(k, v)| (k.clone(), v.as_str().to_string()))
        .collect();
    let single: Option<Vec<String>> = match style
        .charts
        .color
        .categorical
        .as_ref()
        .map(|c| &c.single_series_palette)
    {
        Some(SeriesInk(PaletteChoice::Colors(c))) => {
            Some(c.iter().map(|c| c.as_str().to_string()).collect())
        }
        _ => None,
    };
    let theme = ThemeColors {
        palettes: &palettes,
        roles: &style.roles,
        single_series: single.as_deref(),
    };
    style.roles.iter().find_map(|(name, address)| {
        let why = color_from_theme(name, &theme).err()?;
        Some(
            diag::err_color_token(format!("style.roles.{name} ({address})"), why.0).at_path(
                ModelPath(vec!["style".into(), "roles".into(), name.clone()]),
            ),
        )
    })
}

/// Every board's role table, nested scopes before the root.
pub fn role_checks(
    root: &Resolved<Style>,
    nested: &BTreeMap<String, Resolved<Style>>,
) -> Option<Diagnostic> {
    nested
        .values()
        .chain(std::iter::once(root))
        .find_map(|s| role_error(&s.total))
}

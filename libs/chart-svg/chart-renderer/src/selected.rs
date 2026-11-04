//! Sticky emphasis for click-to-filter. A chart whose `link:` is `?name={{ ch }}`
//! sets the board variable `name`; once that variable holds a value, the marks
//! keyed by that value (an area's series, or a category on a single-series bar or a pie)
//! keep their ink and every other mark recedes: its fill, outline, label, and its
//! series' legend entry and direct label (`recede_of_series`) are
//! mixed toward the chart canvas to the theme's recede contrast, as the hover runtime
//! mixes them (`color-mix(in srgb, paint share, canvas)`, share solved per color), but baked into the SVG
//! so a PNG or PDF carries it. A receded mark's anchor is stamped
//! `data-dbt-receded`, which the hover runtime reads to put hover on top.
//!
//! A mark is selected iff its own href is the link law applied to the variable's value, so
//! the guard, the value and every mark read one law. A link keyed by anything else (a multi-series
//! bar's category) selects nothing. Lines and scatter points never recede: too thin to read statically.

use std::cell::Cell;

use serde_json::Value;

use crate::families::legend::{LegendAction, LegendEntry};
use crate::legend_view::LegendView;
use crate::href::{js_string, Anchor};
use crate::Payload;
use d3rs::color::{byte, format_hex, parse_color, Color};
use dbt_charts_color::{recede_share, wcag_contrast};

/// `?name={{ channel }}` and nothing else: the one link shape that sets exactly one variable.
pub(crate) fn setter(template: &str) -> Option<(&str, &str)> {
    let (name, rest) = template.strip_prefix('?')?.split_once('=')?;
    let channel = rest.strip_prefix("{{")?.strip_suffix("}}")?.trim();
    let named = !name.is_empty() && !name.contains(['=', '&', '#', '{', '}']);
    (named && matches!(channel, "x" | "y" | "color" | "theta")).then_some((name, channel))
}

/// How far a receded mark's ink moves toward the canvas: until its WCAG contrast against it
/// is `contrast`, so a light and a dark canvas get the same separation.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Recede {
    canvas: (f64, f64, f64),
    contrast: f64,
}

impl Recede {
    /// `paint` mixed toward the canvas as a solid hex; `none`, a `url()` pattern and a
    /// translucent color have no solid ink to mix and pass through.
    pub(crate) fn paint(self, paint: &str) -> String {
        match parse_color(paint) {
            Some(color) if color.a == 1.0 => {
                let (r, g, b) = self.canvas;
                let hex = |(r, g, b): (f64, f64, f64)| format_hex(Color { r, g, b, a: 1.0 });
                let share = recede_share(&hex((color.r, color.g, color.b)), &hex(self.canvas), self.contrast)
                    .expect("both colors are #rrggbb");
                let mix = |mark: f64, ground: f64| share * mark + (1.0 - share) * ground;
                format_hex(Color { r: mix(color.r, r), g: mix(color.g, g), b: mix(color.b, b), a: 1.0 })
            }
            _ => paint.to_string(),
        }
    }
}

impl Recede {
    /// `paint` at the contrast halfway between its own against the canvas and the receded one.
    pub(crate) fn halfway(self, paint: &str) -> String {
        let hex = |(r, g, b): (f64, f64, f64)| format!("#{:02x}{:02x}{:02x}", byte(r), byte(g), byte(b));
        match parse_color(paint) {
            Some(color) if color.a == 1.0 => {
                let own = wcag_contrast(&hex((color.r, color.g, color.b)), &hex(self.canvas)).expect("both colors are #rrggbb");
                Recede { contrast: (own + self.contrast) / 2.0, ..self }.paint(paint)
            }
            _ => paint.to_string(),
        }
    }
}

impl Recede {
    /// The recede the theme's hover emphasis asks for over the chart's canvas; `None` when the
    /// theme resolved no visible emphasis.
    pub(crate) fn of(payload: &Payload) -> Result<Option<Self>, String> {
        let Some(emphasis) = payload.board_style.as_ref().and_then(|s| s.hover_emphasis.as_ref()).filter(|e| e.visible) else {
            return Ok(None);
        };
        let canvas = crate::common::de_canvas(payload.resolved.get("canvas").cloned().unwrap_or(Value::Null))
            .map_err(|e| e.to_string())?;
        let channels = d3rs::color::parse_hex(&canvas).expect("`de_canvas` accepts only #rrggbb");
        let canvas = (f64::from(channels.0), f64::from(channels.1), f64::from(channels.2));
        Ok(Some(Recede { canvas, contrast: emphasis.recede_contrast }))
    }

    /// `attrs` with `fill` and `stroke` receded, each paint it had kept as `data-dbt-rest-{name}`.
    pub(crate) fn recede_attrs(self, attrs: scene::Attrs) -> scene::Attrs {
        ["fill", "stroke"].into_iter().fold(attrs.clone(), |receded, name| match attrs.get(name) {
            Some(rest) => receded.map_values(&[name], |paint| self.paint(paint)).set(format!("data-dbt-rest-{name}"), rest),
            None => receded,
        })
    }
}

/// The variable value a chart's marks are keyed by, if its link sets one that is held.
#[derive(Clone, Debug, Default)]
pub(crate) struct Selection {
    active: Option<Active>,
    series_link: Option<SeriesLink>,
    /// What a legend click does, when it sets a view param or a declared variable's options are listed
    /// whole; it then replaces the `series_link`'s hrefs.
    view: Option<LegendView>,
}

/// A link of exactly `?name={{ color }}`: every legend entry sets `name` to its series, as the
/// series' marks do, and the entry the held value names clears it.
#[derive(Clone, Debug)]
struct SeriesLink {
    link: String,
    /// The href the held value's own entry would carry, `None` when nothing is held.
    held: Option<String>,
}

/// What a color legend entry links to: its series, or nothing once its series is the held one.
#[derive(Clone, Copy, Debug)]
pub struct LegendLink<'a> {
    link: &'a str,
    key: &'a str,
    clears: bool,
}

impl LegendLink<'_> {
    pub(crate) fn href(&self) -> String {
        if self.clears {
            let (name, _) = setter(self.link).expect("a series link sets one variable");
            return format!("?{name}=");
        }
        series_href(self.link, self.key)
    }
}

fn series_href(link: &str, series: &str) -> String {
    crate::href::resolve_channel_href(link, |_| Ok(series.to_string())).expect("the channel value is infallible")
}

/// The variable `name`'s held value as a link writes it, `None` when it holds nothing.
fn held(payload: &Payload, name: &str) -> Result<Option<String>, String> {
    Ok(match payload.variables.get(name) {
        Some(Value::String(value)) if !value.is_empty() => Some(value.clone()),
        Some(value @ (Value::Number(_) | Value::Bool(_))) => Some(js_string(Some(value))?),
        _ => None,
    })
}

impl SeriesLink {
    fn of(payload: &Payload, link: Option<&str>, keys: &[(&str, &str)]) -> Result<Option<Self>, String> {
        let Some(link) = link else { return Ok(None) };
        let Some((name, "color")) = setter(link) else { return Ok(None) };
        if !keys.iter().any(|(key, _)| *key == "color") {
            return Ok(None);
        }
        let held = held(payload, name)?.map(|value| series_href(link, &value));
        Ok(Some(SeriesLink { link: link.to_string(), held }))
    }
}

#[derive(Clone, Debug)]
struct Active {
    /// The chart's link template and the channel its one placeholder names.
    link: String,
    channel: String,
    /// The href a mark carries when it is the one the variable names: the link law
    /// applied to the variable's own value, so a mark is selected iff its own href is this.
    href: String,
    recede: Recede,
}

impl Selection {
    /// The selection `payload` holds. Whether a drawn mark carries it is learned from the marks
    /// themselves ([`settled`]), so a facet panel with none of the selected data recedes like the rest. `keys` pairs each channel whose
    /// marks may be selected with the data field it reads; the link must set its variable from one.
    pub(crate) fn of(
        payload: &Payload,
        link: Option<&str>,
        keys: &[(&str, &str)],
    ) -> Result<Self, String> {
        let series_link = SeriesLink::of(payload, link, keys)?;
        Ok(Selection { active: Self::active(payload, link, keys)?, series_link, view: None })
    }

    /// A chart whose marks never recede (a line): its legend entries still set the variable.
    pub(crate) fn linking(payload: &Payload, link: Option<&str>, keys: &[(&str, &str)]) -> Result<Self, String> {
        Ok(Selection { active: None, series_link: SeriesLink::of(payload, link, keys)?, view: None })
    }

    /// This selection with the legend view that hides series, if the chart has one.
    pub(crate) fn viewing(self, view: Option<LegendView>) -> Self {
        Selection { view, ..self }
    }

    fn active(payload: &Payload, link: Option<&str>, keys: &[(&str, &str)]) -> Result<Option<Active>, String> {
        let none = Ok(None);
        let Some((name, channel)) = link.and_then(setter) else { return none };
        if !keys.iter().any(|(key, _)| *key == channel) || PROBE.get().off || crate::board::is_magnitude_colored(&payload.resolved) {
            return none;
        }
        let Some(value) = held(payload, name)? else { return none };
        let Some(recede) = Recede::of(payload)? else { return none };
        let href = crate::href::resolve_channel_href(link.expect("a setter link"), |_| Ok(value.clone()))?;
        PROBE.set(Probe { active: true, ..PROBE.get() });
        Ok(Some(Active { link: link.expect("a setter link").to_string(), channel: channel.to_string(), href, recede }))
    }

    /// `anchor` stamped receded unless it is the selected mark's.
    pub(crate) fn mark<'a>(&self, anchor: Option<Anchor<'a>>) -> Option<Anchor<'a>> {
        let anchor = anchor?;
        Some(Anchor { recede: self.recede_of(Some(anchor.url)), ..anchor })
    }

    /// How a mark linking to `href` recedes, `None` when it is the selected one, unlinked, or nothing is selected.
    pub(crate) fn recede_of(&self, href: Option<&str>) -> Option<Recede> {
        let active = self.active.as_ref()?;
        let recede = Self::recede_href(active, href?);
        if recede.is_none() {
            PROBE.set(Probe { matched: true, ..PROBE.get() });
        }
        recede
    }

    fn recede_href(active: &Active, href: &str) -> Option<Recede> {
        (href != active.href).then_some(active.recede)
    }

    /// How the legend entry or direct label naming `series` recedes: as the series' own marks do,
    /// by the same link law. Only a link keyed by color names a series. Whether a mark carries the
    /// selection is the marks' to say, so this never settles it.
    pub(crate) fn recede_of_series(&self, series: &str) -> Option<Recede> {
        let active = self.active.as_ref().filter(|active| active.channel == "color")?;
        Self::recede_href(active, &series_href(&active.link, series))
    }

    /// `entries`, each receded and linked as the series `keys` names it: the raw value its marks
    /// link by. An entry with no key (a layer, a y title, a "…N entries" fold) is no series: it
    /// never recedes and links nowhere.
    pub(crate) fn legend<'a>(&'a self, entries: Vec<LegendEntry<'a>>, keys: &[Option<&'a str>]) -> Vec<LegendEntry<'a>> {
        assert_eq!(entries.len(), keys.len(), "one key per legend entry");
        if let Some(view) = &self.view {
            return view.entries(entries, keys);
        }
        entries
            .into_iter()
            .zip(keys)
            .map(|(entry, key)| LegendEntry {
                recede: key.and_then(|key| self.recede_of_series(key)),
                action: key.zip(self.series_link.as_ref()).map(|(key, series)| {
                    LegendAction::Link(LegendLink {
                        link: &series.link,
                        key,
                        clears: series.held.as_deref() == Some(series_href(&series.link, key).as_str()),
                    })
                }),
                ..entry
            })
            .collect()
    }
}

/// Per entry, the link key of the series whose painted `label` it carries, `None` when no series
/// paints that label. `series` pairs each series' painted label with its link key. Run it on the
/// whole labels, before any is cut to `labelLimit`.
pub(crate) fn series_keys<'s>(entries: &[LegendEntry], series: &[(String, &'s str)]) -> Vec<Option<&'s str>> {
    entries.iter().map(|entry| series.iter().find(|(label, _)| entry.label == label).map(|(_, key)| *key)).collect()
}

/// `paint` as a mark that recedes by `recede` draws it.
pub(crate) fn ink(recede: Option<Recede>, paint: &str) -> String {
    recede.map_or_else(|| paint.to_string(), |recede| recede.paint(paint))
}

/// What the marks of the chart being drawn have said about its selection.
#[derive(Clone, Copy, Default)]
struct Probe {
    /// A selection was built.
    active: bool,
    /// A drawn mark carries the selected value.
    matched: bool,
    /// Draw as if no variable were held.
    off: bool,
}

thread_local! {
    static PROBE: Cell<Probe> = const { Cell::new(Probe { active: false, matched: false, off: false }) };
}

/// `draw`, then once more with no selection if it built one that no drawn mark carries (a stale
/// value, or a mark the family never draws): the chart is then as authored, never washed out.
/// The marks are the one source, so every panel of a facet answers together.
pub(crate) fn settled<T, E>(draw: impl Fn() -> Result<T, E>) -> Result<T, E> {
    let outer = PROBE.replace(Probe::default());
    let mut drawn = draw();
    let probe = PROBE.get();
    if drawn.is_ok() && probe.active && !probe.matched {
        PROBE.set(Probe { off: true, ..Probe::default() });
        drawn = draw();
    }
    PROBE.set(outer);
    drawn
}

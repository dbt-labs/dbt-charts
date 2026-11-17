//! A geoshape's base map: its decoded features and the neutral fill and stroke they draw with, over
//! the geo source the host sent (`RunResult.geo`). `chart-renderer/src/families/geo/geoshape.rs`
//! (`bind`, without its `bind_choropleth`) and `compile/resolve/chart/geo.py` (`_resolve_geoshape`,
//! `_resolve_geo_projection`) decide the same facts; a choropleth (a `lookup`, `value` or `color`
//! channel), an inline source, a projection taken from the source registry, a conditional style or
//! anything else this slice does not model is refused by name.
//!
//! The chart's source is the name its `geo:` (or `geo_source:`) holds; the host sends each one's
//! descriptor and document keyed by it, and a source it did not send is refused naming the source.

use board_model::chart::{GeoSource, GeoshapeChart};
use diagnostic::{refuse, Diagnostic};
use model_core::{Engine, Tri};

use crate::bound::{Failure, Family, GeoStroke, Geoshape, StrokeInk};
use crate::derived::Placed;
use crate::families::cartesian::plain_text;
use crate::geo_source::{decode, GeoSource as Reader};
use crate::run::RunResult;

/// A chart field, style or board fact that changes what the map says and is not modeled.
fn unmodeled(what: &str) -> Diagnostic {
    Diagnostic::refused(refuse!("a geoshape with {what} is not supported"))
}

/// The source name a chart's `geo:` or `geo_source:` holds.
fn source_name(chart: &GeoshapeChart) -> Result<&str, Diagnostic> {
    match (&chart.geo.geo, &chart.geo.geo_source) {
        (Some(GeoSource::Name(name)), None) | (None, Some(name)) => Ok(name),
        (Some(GeoSource::Name(_)), Some(_)) => Err(unmodeled("both `geo:` and `geo_source:`")),
        (Some(GeoSource::Inline(_)), _) => Err(unmodeled("an inline `geo:` source")),
        (None, None) => Err(unmodeled("no `geo:` or `geo_source:`")),
    }
}

/// Binds `chart`, a base map with no data encoding, at the placement `placed`.
pub fn bind_geoshape(chart: &GeoshapeChart, run: &RunResult, placed: &Placed) -> Result<Result<Family, Failure>, Diagnostic> {
    let style = &placed.style.charts.geoshape;
    let geo = &chart.geo;
    let checks = [
        (geo.lookup.is_some(), "a `lookup` column"),
        (geo.value.is_some(), "a `value` column"),
        (geo.color.is_some(), "a `color` column"),
        (geo.shared.base.link.is_some(), "a `link`"),
        (!geo.labels.field_labels.is_empty(), "`field_labels`"),
        (geo.height.is_some() || geo.width.is_some(), "a `height` or `width`"),
        (geo.shared.subtitle.as_ref().is_some_and(|t| !t.as_str().is_empty()) && geo.shared.title.as_ref().is_none_or(|t| t.as_str().is_empty()), "a `subtitle` but no `title`"),
        (!chart.style.conditional.is_empty() || !style.conditional.is_empty(), "a conditional style"),
        (placed.style.charts.legend.base.visible == Engine::Value(true), "a visible legend"),
    ];
    if let Some((_, what)) = checks.iter().find(|(set, _)| *set) {
        return Err(unmodeled(what));
    }
    let name = source_name(chart)?;
    let input = run
        .geo
        .get(name)
        .ok_or_else(|| Diagnostic::refused(refuse!("a geoshape over a geo source the host did not send is not supported")).with_sink_param("geo_source", serde_json::json!(name)))?;
    let projection = match &geo.projection {
        Some(projection) => projection.clone(),
        None => return Err(unmodeled("a projection taken from the geo source's registry entry")),
    };
    let reader = Reader { role: "geoshape", name: &input.source_name, url: &input.url, format_type: &input.format_type, feature: input.feature.as_deref(), property: input.property.as_deref() };
    let documents = crate::geo_source::Geometries::from([(input.url.clone(), input.document.clone())]);
    let decoded = decode(&reader, &documents)?;
    let mark = &style.marks.geoshape;
    let stroke = match &mark.stroke {
        Some(s) => match (&s.color, &s.width) {
            (Engine::Value(color), Engine::Value(width)) => {
                let ink = if color.as_str() == "theme.background" { StrokeInk::BoardBackground } else { StrokeInk::Color(color.as_str().to_string()) };
                Some(GeoStroke { ink, width: width.get() })
            }
            _ => return Err(unmodeled("a stroke color or width that is not a plain value")),
        },
        None => None,
    };
    let scope = placed.scope();
    let canvas = placed.canvas(&style.geo.base.background.clone().map_or(Tri::Unset, Tri::Value), scope)?;
    let background = style.geo.base.background.as_ref().map_or(placed.style.charts.background.as_str(), |c| c.as_str()).to_string();
    let title = plain_text(geo.shared.title.as_ref())?;
    let subtitle = plain_text(geo.shared.subtitle.as_ref())?;
    Ok(Ok(Family::Geoshape(Geoshape {
        canvas,
        background,
        title,
        subtitle,
        fill: mark.fill.as_str().to_string(),
        stroke,
        features: decoded,
        projection,
    })))
}

//! Geoshape family (`type: map` / `type: geoshape`) — production's own
//! choropleth emitter (`emitters/geo.py`) never computes a projected path
//! itself: it assembles a `mark: "geoshape"` spec with a `projection: {type,
//! params}` dict (`_geo_projection`) and hands the TopoJSON/GeoJSON geometry
//! straight to Vega-Lite. The actual projection math — d3-geo's
//! `albersUsa`/`mercator`/`equalEarth`/etc, adaptive-resolution path
//! rendering, graticule and mesh assembly — runs inside Vega (via
//! vl-convert) at render time, not in Python. There is no production
//! computation to port; porting d3-geo's own projection/path machinery from
//! scratch would be an approximation this renderer never signs up for (see
//! `libs/chart-svg/AGENTS.md`'s "never approximate" rule). Refuses by name
//! rather than rendering a placeholder or a wrong map.

use crate::text::FontBook;
use crate::Payload;

/// The message is user-readable on purpose: the cutover's error-card path
/// (R1) shows this string verbatim on the board, so it reads as a plain
/// sentence rather than an internal diagnostic.
pub fn render(_payload: &Payload, _fonts: &FontBook) -> Result<String, String> {
    Err("geoshape: projection is computed by Vega at render time, not ported".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_fonts() -> FontBook {
        let manifest = env!("CARGO_MANIFEST_DIR");
        let dir = format!("{manifest}/../../../src/dbt_charts/core/render/fonts");
        FontBook::load_dir(&dir).expect("test fonts load")
    }

    #[test]
    fn refuses_by_name() {
        let json = serde_json::json!({
            "resolved": {"chart_type": "geoshape"},
            "rows": [],
            "width": 600.0,
            "height": 400.0,
            "background": null,
            "fonts_dir": null,
            "padding": null
        });
        let payload: Payload = serde_json::from_value(json).unwrap();
        let err = render(&payload, &test_fonts()).expect_err("geoshape refuses");
        assert!(err.contains("geoshape"), "{err}");
        assert!(err.contains("Vega"), "{err}");
    }
}

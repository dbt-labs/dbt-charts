//! d3rs — spike-scoped Rust ports of the d3 functions dbt charts' pie family
//! needs. One module per d3 module, ported from the upstream source, grown
//! only as chart families demand functions, plus `py_float`, the
//! numeric-parity primitive every port shares.

pub mod arc;
pub mod color;
pub mod css_colors;
pub mod format;
pub mod geo;
pub mod geojson;
pub mod js_math;
pub mod js_sort;
pub mod native;
pub mod numeral_pad;
pub mod path;
pub mod pie;
pub mod radial_fit;
pub mod predefined_formats;
pub mod py_float;
pub mod py_str;
pub mod sankey;
pub mod sankey_circular;
pub mod scale;
pub mod shape;
pub mod ticks;
pub mod time;
pub mod topojson;
pub mod vega_schemes;

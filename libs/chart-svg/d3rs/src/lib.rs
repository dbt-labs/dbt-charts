//! d3rs — spike-scoped Rust ports of the d3 functions dbt charts' pie family
//! needs. One module per d3 module, ported from the upstream source, grown
//! only as chart families demand functions, plus `py_float`, the
//! numeric-parity primitive every port shares.

#![cfg_attr(not(test), deny(clippy::unwrap_used, clippy::expect_used, clippy::panic, clippy::unreachable, clippy::indexing_slicing))]

pub mod invariant;

pub mod arc;
pub mod array;
pub mod bin;
pub mod calendar;
pub mod color;
pub mod css_colors;
pub mod format;
pub mod format_spec;
pub mod geo;
pub mod geojson;
pub mod js_math;
pub mod js_sort;
pub mod measure_axis;
pub mod native;
pub mod numeral_pad;
pub mod numeral_scale;
pub mod path;
pub mod pie;
pub mod radial_fit;
pub mod predefined_formats;
pub mod py_float;
pub mod py_str;
pub mod sankey;
pub mod sankey_circular;
pub mod scale;
pub mod sequential;
pub mod shape;
pub mod stack;
pub mod symbol;
pub mod ticks;
pub mod time;
pub mod time_format;
pub mod topojson;
pub mod vega_schemes;
pub mod vega_symbol;

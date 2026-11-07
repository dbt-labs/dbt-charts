//! One module per Python file under `models/style/theme/` (see the table in the crate docs).

pub mod annotations;
pub mod area;
pub mod axis;
pub mod bar;
pub mod board;
pub mod callout;
pub mod category_colors;
pub mod chart_base;
pub mod charts;
pub mod endpoint_labels;
pub mod font;
pub mod geoshape;
pub mod heatmap;
pub mod histogram;
pub mod kpi;
pub mod layout;
pub mod legend;
pub mod line;
pub mod marks;
pub mod page;
pub mod pie;
pub mod point_map;
pub mod scatter;
pub mod spark_bar;
// `style/theme/style.py` keeps its name, as every row of the mapping table does.
#[allow(clippy::module_inception)]
pub mod style;
pub mod table;
pub mod variables;

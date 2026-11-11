//! The cartesian frame every bar, line, area, scatter, histogram and heatmap lays out through: the plot box and its negotiation, the value axis on any side,
//! and the band and time axes.

pub mod band_axis;
pub mod bins;
pub mod cartesian;
pub mod empty;
pub mod label;
pub mod legend;
pub mod negotiate;
pub mod points;
pub mod tear;
pub mod time_axis;
pub mod time_cadence;
pub mod value_axis;

pub use cartesian::{draw, plan, plan_at, Env, Heading, PlotPlan};

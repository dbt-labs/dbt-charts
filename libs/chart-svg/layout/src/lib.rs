//! The layout stage: every pixel decision of the render (rust-render-bind-size-print spec § 6).
//!
//! It reads bind's active time zone and locale scopes. Laws: `docs/laws-labels-expr.md`.

pub mod board;
pub mod contract;
pub mod families;
pub mod family;
pub mod geom;
pub mod knockout;
pub mod labels_expr;
pub(crate) mod legacy;
pub mod markdown_style;
pub mod plot;
pub mod scene_path;
pub mod text_ink;
pub mod tier;
pub mod time_ladder;
pub mod title;
pub mod zoom;

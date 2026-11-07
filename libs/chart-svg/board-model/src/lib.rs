//! The typed spine's hand-written board model, on `model-core` and `model-derive`.
//!
//! This crate holds the shared primitives every style module builds on, and the
//! style modules ported so far. Nothing here parses YAML: `TryFrom` on a
//! newtype is the validation `FromNode` calls per scalar.
//!
//! - [`facet`]: one newtype per Python facet, validated as Python validates it;
//!   `Facet::FACET` names the facet for the schema emitter.
//! - [`ids`]: `BoardId`, `ChartId`, `QueryId`, `TemplateRef` and the cross-file ref types; render builds these in fixtures.
//! - [`board`]: the root `Board`, the `BoardBody` every board holds, `Layout` and its items.
//! - `fixtures` (cargo feature): boards built through `try_from`, for tests before normalize exists.
//! - [`primitives`]: the box and line types of `primitives.py` the style modules share.
//! - [`unit`]: `Px`, `Fraction` and `Ratio`, the units more than one style model shares.
//! - [`rule`]: the rule enum behind `Engine<T, R>` fields.
//! - [`string_enum!`]: a closed string set (Python `Literal[...]`) as a Rust enum.
//! - [`query`], [`variable`], [`sql_analysis`]: the structure phase's query side.
//! - `fixtures` (feature `fixtures`): builders for render's mock board values and for tests.
//! - [`style`]: one module per Python file under
//!   `dbt-charts/src/dbt_charts/core/compile/models/style/theme/`.
//!
//! A module is added when its Python file is ported.
//!
//! | Python file (`core/compile/models/...`) | Rust module |
//! |---|---|
//! | `models/refs.py` (`VariableRef`, `QueryRef`, `ChartRef`), the id newtypes | `ids` |
//! | `board/authored.py`, `board/normalized.py` (`Board`, `Layout`, `LayoutItem` as authored meaning), `board/patch.py` | `board`, `board::layout`, `board::value` |
//! | `chart/authored/{__init__,_base,_type,_tooltip}.py`, `chart/authored/callout.py` | `chart`, `chart::tooltip`, `chart::callout` |
//! | `chart/authored/_annotation.py`, `_annotations.py` | `chart::annotation` |
//! | `chart/authored/_layer.py` | `chart::layer` |
//! | `chart/authored/_conditional_formatting.py` | `chart::conditional_formatting` |
//! | `chart/authored/_support_table.py` | `chart::support_table` |
//! | `chart/authored/_base.py` (the mixins past `_BaseChartFields` / `_SharedChartFields`; its column facets) | `chart` (mixins), `chart::columns` |
//! | `vega_lite/contracts.py` (`Projection`, `ProjectionType`) | `chart::projection` |
//! | `style/theme/legend.py` | `style::legend` |
//! | `primitives.py` (`FontStyle`, `SingleLineFontStyle`, `SymbolShape`, `NamedFont` only) | `style::font` |
//! | `primitives.py` (`Curve`, `LineCap`, `StrokeStyle`, `FontColorStrokeStyle`, `BorderStyle`, `CornerStyle`, `DividerStyle`, `SpacingValues`, `TextBaseline`, `Dasharray`, `ToneLiteral` as `Tone`, `ScaleTargetConfig`, `StaticGradientColorStyle`, `ColorStyle`, `CategoricalColorStyle`, `GlyphText`, `FormatConfig`, `Link`, `BoxShadow`) | `primitives` |
//! | `style/theme/_chart_base.py` | `style::chart_base` |
//! | `style/authored/_base.py` (`EndpointLabelsConfig` only) | `style::endpoint_labels` |
//! | `style/theme/annotations.py` | `style::annotations` |
//! | `style/theme/area.py` | `style::area` |
//! | `style/theme/axis.py` | `style::axis` |
//! | `style/theme/bar.py` | `style::bar` |
//! | `style/theme/board.py` | `style::board` |
//! | `style/theme/callout.py` | `style::callout` |
//! | `style/theme/category_colors.py` | `style::category_colors` |
//! | `style/theme/charts.py` | `style::charts` |
//! | `style/theme/geoshape.py` | `style::geoshape` |
//! | `style/theme/heatmap.py` | `style::heatmap` |
//! | `style/theme/histogram.py` | `style::histogram` |
//! | `style/theme/kpi.py` | `style::kpi` |
//! | `style/theme/layout.py` | `style::layout` |
//! | `style/theme/line.py` | `style::line` |
//! | `style/theme/marks.py` | `style::marks` |
//! | `style/theme/page.py` | `style::page` |
//! | `style/theme/pie.py` | `style::pie` |
//! | `style/theme/point_map.py` | `style::point_map` |
//! | `style/theme/scatter.py` | `style::scatter` |
//! | `style/theme/spark_bar.py` | `style::spark_bar` |
//! | `style/theme/style.py`, `style/theme/__init__.py` (re-exports only) | `style::style` |
//! | `style/theme/table.py`, `style/authored/table.py` (`PaginationConfig`, `TableColumnDefaultsConfig`, `TableColumnConfig` and what they hold), `primitives.py` (`ColumnFontStyle`) | `style::table` |
//! | `style/theme/variables.py` | `style::variables` |
//! | `models/query/authored.py`, `models/query/normalized.py` | `query` |
//! | `models/variable/authored.py` | `variable` |
//! | `sqlglot` seam results (`sql_guard.py`, `column_lineage.py`; D-18): no Python model | `sql_analysis` |
//!
//! Porting rules:
//! - A Python subclass is `#[flatten]` composition; a private base such as
//!   `_LegendStyleBase` becomes a model of its own.
//! - `X | None` with `Absent("..")` is `Option<X>` with `absent = ".."`. A bare
//!   `X | None` whose `None` is a documented state still needs a meaning.
//! - `AutoUnset`, or a `Literal["auto"]` arm in the union, is `Engine<X, Auto>`.
//! - A bound shared by more than one model is a unit newtype; a one-off bound
//!   (`ge=1`, `gt=0`) stays a plain number with a `range` constraint.
//! - A `*StylePatch | None` overlay slot is `Option<Patch<T>>` with `absent` and `local`: `null`
//!   clears the layers below it, and unset and `null` both build to `None`. A non-optional overlay
//!   (`charts.axis_x`, ...) is `Patch<T>`, and `null` on it is refused when the value is built.
//! - `InheritSlot(from_path)` is `inherit_slot = "path"`, copied verbatim.

#![deny(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing
)]

pub mod board;
pub mod chart;
pub mod convert;
pub mod facet;
#[cfg(feature = "fixtures")]
pub mod fixtures;
pub mod ids;
pub mod names;
pub mod primitives;
pub mod query;
pub mod rule;
pub mod sql_analysis;
pub mod style;
pub mod unit;
pub mod variable;

/// Declares a closed string set: the enum, its authored spelling, a
/// `TryFrom<&str>` that refuses anything else, and its `Model` (a unit-only
/// enum whose fields are the spellings, so a hyphenated spelling such as
/// `triangle-up` is expressible). The enum is a spine leaf replaced wholesale.
#[macro_export]
macro_rules! string_enum {
    ($(#[doc = $doc:literal])* $name:ident { $($variant:ident = $s:literal),+ $(,)? }) => {
        $(#[doc = $doc])*
        #[derive(Debug, Clone, Copy, PartialEq, Eq)]
        pub enum $name { $($variant),+ }

        impl $name {
            /// The spellings an author may write, in declaration order.
            pub const VALUES: &'static [&'static str] = &[$($s),+];
            const ALL: &'static [$name] = &[$($name::$variant),+];

            pub fn as_str(self) -> &'static str {
                match self { $(Self::$variant => $s),+ }
            }
        }

        impl TryFrom<&str> for $name {
            type Error = String;
            fn try_from(v: &str) -> Result<Self, Self::Error> {
                match v {
                    $($s => Ok(Self::$variant),)+
                    _ => Err($crate::facet::one_of(Self::VALUES)),
                }
            }
        }

        impl ::model_core::ReadScalar for $name {
            fn read(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<Self> {
                let at = ::model_core::read_enum(node, cx, Self::VALUES)?;
                Self::ALL.get(at).copied()
            }
        }

        impl ::model_core::FromNode for $name {
            fn authored(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Self::Authored {
                <$name as ::model_core::ReadScalar>::read(node, cx)
            }
            fn patch(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Self::Patch {
                <$name as ::model_core::ReadScalar>::read(node, cx)
            }
            fn whole(node: &::model_core::Node, cx: &mut ::model_core::Cx) -> Option<Self::Whole> {
                <$name as ::model_core::ReadScalar>::read(node, cx)
            }
        }

        impl ::model_core::Leaf for $name {
            type Whole = $name;
            type Authored = Option<$name>;
            type Patch = Option<$name>;
            const MERGE: ::model_core::Merge = ::model_core::Merge::Override;
            const EXPR: ::model_core::TypeExpr = ::model_core::TypeExpr::Ref(
                stringify!($name),
                <$name as ::model_core::Model>::info,
            );
        }
        ::model_core::build_leaf!($name);

        impl ::model_core::Model for $name {
            fn info() -> &'static ::model_core::ModelInfo {
                const INFO: ::model_core::ModelInfo = ::model_core::ModelInfo {
                    name: stringify!($name),
                    doc: $crate::doc(concat!($($doc, "\n"),*)),
                    kind: ::model_core::ModelKind::Enum,
                    fields: &[$(::model_core::Field {
                        name: $s,
                        ty: &::model_core::TypeExpr::Unit,
                        doc: "",
                        merge: ::model_core::Merge::Override,
                        attrs: ::model_core::Attrs {
                            absent: None,
                            inherit: None,
                            inherit_slot: None,
                            links: &[],
                            local: false,
                            nested_drop: false,
                            nullable: false,
                            fill: None,
                            fill_value: None,
                            accept: None,
                            directive: false,
                            keys: false,
                            flatten: false,
                            role: None,
                        },
                    }),+],
                    constraints: &[],
                    shorthand: None,
                    string_pattern: None,
                    complete: false,
                };
                &INFO
            }
        }
    };
}

/// A doc comment's text without its leading space and trailing newline.
pub const fn doc(text: &'static str) -> &'static str {
    text.trim_ascii()
}

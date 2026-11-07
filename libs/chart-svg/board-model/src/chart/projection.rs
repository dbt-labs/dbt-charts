//! `vega_lite/contracts.py` `Projection` and `ProjectionType`: a map projection, spelled as Vega
//! spells it (`clipAngle`), so the authored object is the one Vega receives. Closed: an unknown
//! type or key is an authoring error.

#![allow(non_snake_case)]

use super::columns::LonLat;
use crate::string_enum;
use model_core::{Cx, FromNode, Kind, Leaf, Node, ReadScalar};
use model_derive::Model;

string_enum!(
    /// Every projection Vega registers.
    ProjectionType {
        Mercator = "mercator",
        TransverseMercator = "transverseMercator",
        Equirectangular = "equirectangular",
        EqualEarth = "equalEarth",
        NaturalEarth1 = "naturalEarth1",
        Mollweide = "mollweide",
        AzimuthalEqualArea = "azimuthalEqualArea",
        AzimuthalEquidistant = "azimuthalEquidistant",
        Gnomonic = "gnomonic",
        Orthographic = "orthographic",
        Stereographic = "stereographic",
        Albers = "albers",
        AlbersUsa = "albersUsa",
        ConicConformal = "conicConformal",
        ConicEqualArea = "conicEqualArea",
        ConicEquidistant = "conicEquidistant",
        Identity = "identity",
    }
);

/// `[lambda, phi]` or `[lambda, phi, gamma]`.
#[derive(Debug, Clone, PartialEq)]
pub struct Rotation(pub Vec<f64>);

impl ReadScalar for Rotation {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let items = <Vec<f64> as FromNode>::whole(node, cx)?;
        if !(2..=3).contains(&items.len()) {
            cx.mismatch(node, "a list of 2 or 3 numbers");
            return None;
        }
        Some(Self(items))
    }
}
union_leaf!(
    Rotation,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Tuple(&[&FLOAT, &FLOAT]),
        &::model_core::TypeExpr::Tuple(&[&FLOAT, &FLOAT, &FLOAT]),
    )
);

/// `[[x0, y0], [x1, y1]]` in pixels.
#[derive(Debug, Clone, PartialEq)]
pub struct ClipExtent(pub [LonLat; 2]);

impl ReadScalar for ClipExtent {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        let Kind::Seq(items) = &node.kind else {
            cx.mismatch(node, "a list of two [x, y] pairs");
            return None;
        };
        let [a, b] = items.as_slice() else {
            cx.mismatch(node, "a list of two [x, y] pairs");
            return None;
        };
        let (a, b) = (LonLat::read(a, cx), LonLat::read(b, cx));
        Some(Self([a?, b?]))
    }
}
union_leaf!(
    ClipExtent,
    ::model_core::TypeExpr::Tuple(&[
        &<LonLat as ::model_core::Leaf>::EXPR,
        &<LonLat as ::model_core::Leaf>::EXPR,
    ])
);

const FLOAT: ::model_core::TypeExpr = ::model_core::TypeExpr::Scalar(::model_core::Scalar::Float);

/// A map projection: a Vega projection type plus its typed parameters.
#[derive(Debug, Clone, PartialEq, Model)]
pub struct Projection {
    /// Projection name.
    pub r#type: ProjectionType,
    /// Map center as [longitude, latitude].
    #[model(absent = "the projection's own center")]
    pub center: Option<LonLat>,
    /// Rotation as [lambda, phi] or [lambda, phi, gamma].
    #[model(absent = "no rotation")]
    pub rotate: Option<Rotation>,
    /// Projection scale factor.
    #[model(absent = "fit to the chart")]
    pub scale: Option<f64>,
    /// Pixel offset of the projection center, as [x, y].
    #[model(absent = "centered in the chart")]
    pub translate: Option<LonLat>,
    /// Standard parallels for conic projections.
    #[model(absent = "the projection's own parallels")]
    pub parallels: Option<LonLat>,
    /// Clip angle in degrees from the projection center.
    #[model(absent = "no angular clip")]
    pub clipAngle: Option<f64>,
    /// Clip rectangle as [[x0, y0], [x1, y1]] in pixels.
    #[model(absent = "no clip rectangle")]
    pub clipExtent: Option<ClipExtent>,
    /// Adaptive resampling threshold in pixels.
    #[model(absent = "the projection's own precision")]
    pub precision: Option<f64>,
    /// Mirror the projected x axis.
    #[model(absent = "not mirrored")]
    pub reflectX: Option<bool>,
    /// Mirror the projected y axis.
    #[model(absent = "not mirrored")]
    pub reflectY: Option<bool>,
    /// Radius in pixels for point geometries.
    #[model(absent = "the mark's own radius")]
    pub pointRadius: Option<f64>,
}

/// A `projection:` value: a projection name, or the object with parameters.
#[derive(Debug, Clone, PartialEq)]
pub enum ProjectionChoice {
    Name(ProjectionType),
    Block(Box<<Projection as Leaf>::Whole>),
}

impl ReadScalar for ProjectionChoice {
    fn read(node: &Node, cx: &mut Cx) -> Option<Self> {
        if matches!(node.kind, Kind::Map(_)) {
            return <Projection as FromNode>::whole(node, cx).map(|p| Self::Block(Box::new(p)));
        }
        ProjectionType::read(node, cx).map(Self::Name)
    }
}
union_leaf!(
    ProjectionChoice,
    ::model_core::TypeExpr::Either(
        &::model_core::TypeExpr::Ref("Projection", <Projection as ::model_core::Model>::info),
        &<ProjectionType as Leaf>::EXPR,
    )
);

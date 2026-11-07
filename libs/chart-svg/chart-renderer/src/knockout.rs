//! A mark halo that cuts what is under the mark instead of painting over it.
//!
//! A mark outlined in the canvas color separates itself from its neighbors and cuts the
//! gridlines, but painted over a background annotation it leaves a canvas-colored rim. The halo
//! splits in two: an undercoat of the outlines, painted before the annotations (it cuts the grid
//! and the annotations tint over it), and a mask on the marks that leaves each outline's stroke
//! open, so the rim and the seams between neighbors show whatever is beneath. The mask keeps the
//! stroke's paint order: a mark covers the halos painted before it and is cut by its own and later
//! ones. It covers at full strength over its whole shape, so a translucent mark hides an earlier
//! halo beneath it that a stroke would have let show faintly.

use scene::{Attrs, Bounds, Node, Role, Shape};

/// The attributes that shape a stroke's corners and ends: an outline cut at the same width but a
/// different join would not match the halo it replaces.
const STROKE_SHAPE: [&str; 3] = ["stroke-linejoin", "stroke-linecap", "stroke-miterlimit"];

/// `paint` with `from`'s stroke-shape attributes.
fn stroke_shape(from: &Attrs, paint: Attrs) -> Attrs {
    STROKE_SHAPE
        .iter()
        .fold(paint, |paint, name| paint.quick_some(name, from.get(name)))
}

/// The marks a halo touches, as painted: the `undercoat` goes in before the background
/// annotations, `marks` where the marks were.
pub struct Knocked {
    pub undercoat: Vec<Node>,
    pub marks: Vec<Node>,
}

/// `marks` with the `canvas`-colored stroke of every shape taken off and moved into an
/// undercoat and the mask `name` (within the chart's id `scope`) over `region` (the plot box) and any mark past it. A shape
/// drawn in its own ink (`data-dbt-ink`) keeps its stroke. Marks with no canvas stroke come back
/// unchanged. A caller whose marks paint over nothing but the canvas (pie slices, map regions)
/// discards the undercoat.
pub fn knock_out(
    marks: Vec<Node>,
    canvas: &str,
    name: &str,
    scope: &str,
    region: (f64, f64, f64, f64),
) -> Knocked {
    let mut found = Found {
        canvas,
        cuts: Vec::new(),
        clips: Vec::new(),
    };
    let marks = found.strip(marks);
    cut(marks, canvas, (name, scope), region, found.cuts)
}

/// `marks` unchanged but for the mask `name` and an undercoat, both cut along `outline` (a
/// node holding its geometry and stroke shape) at `width`: for marks that keep their own canvas strokes where
/// they meet, and knock out only along an edge they share with nothing, such as a stack's
/// envelope.
pub fn knock_out_along(
    marks: Vec<Node>,
    canvas: &str,
    name: &str,
    scope: &str,
    region: (f64, f64, f64, f64),
    outline: Node,
    width: f64,
) -> Knocked {
    cut(marks, canvas, (name, scope), region, vec![Cut::Outline(outline, width)])
}

/// What the mask paints for one shape, in paint order.
enum Cut {
    /// A filled shape's geometry: it covers the halos painted before it.
    Covers(Node),
    /// A canvas stroke's geometry and stroke shape, and its width: it cuts what is painted
    /// before it, its own shape included.
    Outline(Node, f64),
}

impl Cut {
    fn node(&self) -> &Node {
        match self {
            Cut::Covers(node) | Cut::Outline(node, _) => node,
        }
    }
}

/// `nodes` clipped by `clip` (a `url(#id)`). A masked group carries the clip itself: under a
/// clipped ancestor, resvg sizes the group by everything it holds and paints nothing when that
/// runs far past the clip (a basemap's world). Runs of the other nodes share a clipped group,
/// and a mask definition is left as it is.
pub fn clip(nodes: Vec<Node>, clip: &str) -> Vec<Node> {
    let mut out = Vec::new();
    let mut run = Vec::new();
    for mut node in nodes {
        let masked = node.attrs.get("mask").is_some();
        if masked || matches!(node.shape, Shape::Mask { .. }) {
            if !run.is_empty() {
                out.push(Node::group(Role::Chrome, Attrs::new().set("clip-path", clip), std::mem::take(&mut run)));
            }
            if masked {
                node.attrs = node.attrs.set("clip-path", clip);
            }
            out.push(node);
        } else {
            run.push(node);
        }
    }
    if !run.is_empty() {
        out.push(Node::group(Role::Chrome, Attrs::new().set("clip-path", clip), run));
    }
    out
}

fn cut(
    marks: Vec<Node>,
    canvas: &str,
    (name, scope): (&str, &str),
    region: (f64, f64, f64, f64),
    cuts: Vec<Cut>,
) -> Knocked {
    let outlines: Vec<(&Node, f64)> = cuts
        .iter()
        .filter_map(|cut| match cut {
            Cut::Outline(node, width) => Some((node, *width)),
            Cut::Covers(_) => None,
        })
        .collect();
    if outlines.is_empty() {
        return Knocked {
            undercoat: Vec::new(),
            marks,
        };
    }
    let width = outlines.iter().map(|(_, width)| *width).fold(0.0, f64::max);
    // A mark may paint past the region (an unclipped layer bar): the mask covers it too.
    let (x, y, w, h) = region;
    let covered = cuts
        .iter()
        .filter_map(|cut| cut.node().bounds().map(|b| b.transformed(&cut.node().affine())))
        .fold(Bounds { x1: x, y1: y, x2: x + w, y2: y + h }, Bounds::union);
    let region = (
        covered.x1 - width,
        covered.y1 - width,
        covered.width() + 2.0 * width,
        covered.height() + 2.0 * width,
    );
    let mask_id = &named_for(name, scope, region, &cuts);
    let painted = |shape: &Node, paint: Attrs| {
        let mut node = Node::new(Role::Chrome, paint, shape.shape.clone());
        node.transform.clone_from(&shape.transform);
        node
    };
    let outline = |shape: &Node, width: f64, paint: Attrs| {
        painted(shape, stroke_shape(&shape.attrs, paint.num("stroke-width", width)))
    };
    // An unfilled mark's halo is only its rim: its undercoat leaves the inside open too.
    let undercoat = outlines
        .iter()
        .map(|&(shape, width)| {
            let fill = if shape.attrs.get("fill") == Some("none") {
                "none"
            } else {
                canvas
            };
            outline(shape, width, Attrs::new().set("fill", fill).set("stroke", canvas))
        })
        .collect();
    let mut holes = vec![Node::rect(
        Role::Chrome,
        region,
        Attrs::new().set("fill", "#fff"),
    )];
    holes.extend(cuts.iter().map(|cut| match cut {
        Cut::Covers(shape) => painted(shape, Attrs::new().set("fill", "#fff")),
        Cut::Outline(shape, width) => {
            outline(shape, *width, Attrs::new().set("fill", "none").set("stroke", "#000"))
        }
    }));
    let mask = Node::new(
        Role::Chrome,
        Attrs::new().set("id", mask_id),
        Shape::Mask {
            region,
            children: holes,
        },
    );
    let masked = Node::group(
        Role::Chrome,
        Attrs::new().set("mask", format!("url(#{mask_id})")),
        marks,
    );
    Knocked {
        undercoat: vec![Node::group(
            Role::Chrome,
            Attrs::new()
                .set("class", "knockout-undercoat")
                .set("aria-hidden", "true"),
            undercoat,
        )],
        marks: vec![mask, masked],
    }
}

/// The mask's id: `name`, a digest of the region and outlines it cuts, then the chart's `scope`,
/// which every id a chart defines ends with. A board composed outside the
/// engine renders every placement of a chart with the same ids, and `url(#id)` resolves to the
/// first definition in the document: placements of different sizes must not share one mask, while
/// like placements share a like one harmlessly.
fn named_for(name: &str, scope: &str, region: (f64, f64, f64, f64), cuts: &[Cut]) -> String {
    use sha2::{Digest, Sha256};
    let mut digest = Sha256::new();
    let (x, y, w, h) = region;
    for value in [x, y, w, h] {
        digest.update(value.to_bits().to_le_bytes());
    }
    for cut in cuts {
        let node = cut.node();
        let bounds = node.bounds().map(|b| b.transformed(&node.affine()));
        let corners = bounds.into_iter().flat_map(|b| [b.x1, b.y1, b.x2, b.y2]);
        let width = match cut {
            Cut::Covers(_) => -1.0,
            Cut::Outline(_, width) => *width,
        };
        for value in corners.chain([width]) {
            digest.update(value.to_bits().to_le_bytes());
        }
    }
    let digest = digest.finalize();
    format!("{name}-{:02x}{:02x}{:02x}{:02x}-{scope}", digest[0], digest[1], digest[2], digest[3])
}

struct Found<'a> {
    canvas: &'a str,
    /// The mask's paints, in paint order.
    cuts: Vec<Cut>,
    /// The clip ids of stripped marks: a clip only trims a stroke, so it goes with it.
    clips: Vec<String>,
}

impl Found<'_> {
    fn strip(&mut self, nodes: Vec<Node>) -> Vec<Node> {
        nodes
            .into_iter()
            .filter_map(|node| self.strip_node(node))
            .collect()
    }

    fn strip_node(&mut self, mut node: Node) -> Option<Node> {
        let shape = matches!(node.shape, Shape::Path(_) | Shape::Rect { .. });
        let drawn_in_canvas = |attrs: &Attrs| {
            attrs
                .get_num("stroke-width")
                .is_some_and(|width| width > 0.0)
                && attrs.get("data-dbt-ink").is_none()
                && attrs
                    .get("data-dbt-rest-stroke")
                    .or(attrs.get("stroke"))
                    .is_some_and(|stroke| stroke.eq_ignore_ascii_case(self.canvas))
        };
        match node.shape {
            Shape::Group(children) => node.shape = Shape::Group(self.strip(children)),
            Shape::Link(children) => node.shape = Shape::Link(self.strip(children)),
            Shape::ClipPath(_)
                if node
                    .attrs
                    .get("id")
                    .is_some_and(|id| self.clips.iter().any(|clip| clip == id)) =>
            {
                return None
            }
            _ if shape && !drawn_in_canvas(&node.attrs) => {
                if node.attrs.get("fill") != Some("none") {
                    self.cuts.push(Cut::Covers(Node {
                        transform: node.transform.clone(),
                        ..Node::new(Role::Chrome, Attrs::new(), node.shape.clone())
                    }));
                }
            }
            _ if shape => {
                let width = node.attrs.get_num("stroke-width").expect("drawn in the canvas color");
                let unfilled = node.attrs.get("fill") == Some("none");
                if !unfilled {
                    self.cuts.push(Cut::Covers(Node {
                        transform: node.transform.clone(),
                        ..Node::new(Role::Chrome, Attrs::new(), node.shape.clone())
                    }));
                }
                // A second stroke pass (a bar's outline-only pass, a pattern's edge): the same
                // outline with no fill. The undercoat and the mask each take it again, so an
                // antialiased edge pixel is covered once per pass.
                let overlay = node.role == Role::Chrome && unfilled;
                let outline = Node::new(
                    Role::Chrome,
                    stroke_shape(
                        &node.attrs,
                        if unfilled {
                            Attrs::new().set("fill", "none")
                        } else {
                            Attrs::new()
                        },
                    ),
                    node.shape.clone(),
                );
                self.cuts.push(Cut::Outline(
                    Node {
                        transform: node.transform.clone(),
                        ..outline
                    },
                    width,
                ));
                if overlay {
                    return None;
                }
                if let Some(clip) = node
                    .attrs
                    .get("clip-path")
                    .and_then(|c| c.strip_prefix("url(#"))
                    .and_then(|c| c.strip_suffix(')'))
                {
                    self.clips.push(clip.to_string());
                }
                node.attrs = node.attrs.without(&[
                    "stroke",
                    "stroke-width",
                    "data-dbt-rest-stroke",
                    "clip-path",
                ]);
            }
            _ => {}
        }
        Some(node)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mask_id(knocked: &Knocked) -> String {
        knocked.marks[0].attrs.get("id").expect("a mask has an id").to_string()
    }

    fn bar(h: f64) -> Node {
        Node::rect(
            Role::Chrome,
            (10.0, 0.0, 20.0, h),
            Attrs::new().set("fill", "#336699").set("stroke", "#ffffff").num("stroke-width", 1.0),
        )
    }

    /// A board can place one chart twice with the same ids, so a mask is named for what it cuts
    /// too: placements of different sizes never share a mask, and like ones share a like mask.
    /// Like every id a chart defines, it ends with the chart's.
    #[test]
    fn a_mask_is_named_for_its_chart_and_what_it_cuts() {
        let at = |h| mask_id(&knock_out(vec![bar(h)], "#ffffff", "ko0", "c", (0.0, 0.0, 100.0, 100.0)));
        assert!(at(40.0).starts_with("ko0-") && at(40.0).ends_with("-c"), "every id ends with the chart's: {}", at(40.0));
        assert_ne!(at(40.0), at(60.0));
        assert_eq!(at(40.0), at(40.0));
        let masked = knock_out(vec![bar(40.0)], "#ffffff", "ko0", "c", (0.0, 0.0, 100.0, 100.0));
        assert_eq!(masked.marks[1].attrs.get("mask"), Some(format!("url(#{})", mask_id(&masked)).as_str()));
    }

    /// The mask keeps paint order: each mark's shape restores it over the outlines painted before
    /// it, then its own outline cuts, so a later mark overlapping an earlier one covers the
    /// earlier halo.
    #[test]
    fn a_later_mark_covers_an_earlier_marks_cut() {
        let at = |x: f64| {
            Node::rect(
                Role::Chrome,
                (x, 0.0, 20.0, 40.0),
                Attrs::new().set("fill", "#336699").set("stroke", "#ffffff").num("stroke-width", 1.0),
            )
        };
        let knocked = knock_out(vec![at(10.0), at(25.0)], "#ffffff", "ko0", "m", (0.0, 0.0, 100.0, 100.0));
        let Shape::Mask { children, .. } = &knocked.marks[0].shape else { panic!("a mask first") };
        let paints: Vec<(Option<&str>, Option<&str>, f64)> = children
            .iter()
            .map(|c| {
                let Shape::Rect { x, .. } = c.shape else { panic!("rects") };
                (c.attrs.get("fill"), c.attrs.get("stroke"), x)
            })
            .collect();
        assert_eq!(
            paints[1..],
            [
                (Some("#fff"), None, 10.0),
                (Some("none"), Some("#000"), 10.0),
                (Some("#fff"), None, 25.0),
                (Some("none"), Some("#000"), 25.0),
            ]
        );
    }

    /// A mark that paints past the plot, as an unclipped layer bar does, stays inside the mask.
    #[test]
    fn the_mask_covers_a_mark_past_the_plot() {
        let bar = Node::rect(
            Role::Chrome,
            (10.0, -40.0, 20.0, 90.0),
            Attrs::new().set("fill", "#336699").set("stroke", "#ffffff").num("stroke-width", 1.0),
        );
        let knocked = knock_out(vec![bar], "#ffffff", "ko0", "m", (0.0, 0.0, 100.0, 50.0));
        let Shape::Mask { region, .. } = &knocked.marks[0].shape else { panic!("a mask first") };
        assert_eq!(*region, (-1.0, -41.0, 102.0, 92.0));
    }
}

use super::*;

fn mark(layer: usize) -> Role {
    Role::Mark { layer, series: None, datum: None }
}

fn rect(role: Role, x: f64, y: f64, w: f64, h: f64) -> Node {
    Node::rect(role, (x, y, w, h), Attrs::new())
}

fn assert_box(got: Bounds, want: (f64, f64, f64, f64)) {
    let close = |a: f64, b: f64| (a - b).abs() < 1e-9;
    assert!(
        close(got.x1, want.0) && close(got.y1, want.1) && close(got.x2, want.2) && close(got.y2, want.3),
        "{got:?} != {want:?}"
    );
}

#[test]
fn a_translated_group_moves_the_marks_inside_it() {
    let tree = vec![Node::group(Role::Chrome, Attrs::new(), vec![rect(mark(0), 10.0, 20.0, 30.0, 5.0)]).translate(100.0, 200.0)];
    let found = marks(&tree);
    assert_eq!(found.len(), 1);
    assert_box(found[0].bounds, (110.0, 220.0, 140.0, 225.0));
}

#[test]
fn the_frame_is_the_slice_given_not_the_root() {
    let inner = Node::group(Role::Chrome, Attrs::new(), vec![rect(mark(0), 0.0, 0.0, 10.0, 10.0)]).translate(5.0, 5.0);
    let outer = Node::group(Role::Chrome, Attrs::new(), vec![inner]).translate(1000.0, 1000.0);
    let found = marks(outer.children());
    assert_box(found[0].bounds, (5.0, 5.0, 15.0, 15.0));
}

#[test]
fn a_rotated_group_boxes_the_rotated_corners() {
    let group = Node::group(Role::Chrome, Attrs::new(), vec![rect(mark(0), 10.0, 0.0, 20.0, 5.0)])
        .with_transform(Transform::Rotate { deg: 90.0, about: None });
    let found = marks(std::slice::from_ref(&group));
    assert_box(found[0].bounds, (-5.0, 10.0, 0.0, 30.0));
}

#[test]
fn rotation_about_a_point_composes_after_translation() {
    let node = rect(mark(0), 0.0, 0.0, 10.0, 10.0)
        .translate(100.0, 0.0)
        .with_transform(Transform::Rotate { deg: 180.0, about: Some((5.0, 5.0)) });
    // rotate 180 about (5,5) keeps the square; translate shifts it.
    let found = marks(std::slice::from_ref(&node));
    assert_box(found[0].bounds, (100.0, 0.0, 110.0, 10.0));
}

#[test]
fn marks_carry_their_role_fields_and_skip_everything_else() {
    let tree = vec![
        rect(Role::Chrome, 0.0, 0.0, 100.0, 100.0),
        Node::rect(Role::Mark { layer: 2, series: Some("a".into()), datum: Some(7) }, (1.0, 2.0, 3.0, 4.0), Attrs::new()),
        Node::path(Role::Mark { layer: 0, series: None, datum: None }, PathData::new(), Attrs::new()),
    ];
    let found = marks(&tree);
    assert_eq!(found.len(), 1);
    assert_eq!((found[0].layer, found[0].series, found[0].datum), (2, Some("a"), Some(7)));
}

#[test]
fn labels_are_the_measured_boxes_of_label_text_in_the_slices_frame() {
    let measured = Bounds { x1: 1.0, y1: 2.0, x2: 5.0, y2: 6.0 };
    let label = |role| Node::text(role, (1.0, 6.0), Attrs::new(), "7").with_text_bounds(measured);
    let tree = vec![
        Node::group(Role::Chrome, Attrs::new(), vec![label(Role::Label), label(Role::Title)]).translate(10.0, 20.0),
        Node::text(Role::Label, (0.0, 0.0), Attrs::new(), "unmeasured"),
    ];
    assert_eq!(labels(&tree), [Bounds { x1: 11.0, y1: 22.0, x2: 15.0, y2: 26.0 }]);
}

fn path(data: PathData) -> Bounds {
    Node::path(Role::Chrome, data, Attrs::new()).bounds().expect("a path with points")
}

#[test]
fn a_semicircle_arc_reaches_its_apex() {
    let top = PathData::new().move_to(0.0, 0.0).arc_to(10.0, true, 20.0, 0.0);
    assert_box(path(top), (0.0, -10.0, 20.0, 0.0));
    let bottom = PathData::new().move_to(0.0, 0.0).arc_to(10.0, false, 20.0, 0.0);
    assert_box(path(bottom), (0.0, 0.0, 20.0, 10.0));
}

#[test]
fn a_quarter_arc_stays_inside_its_corner() {
    let quarter = PathData::new().move_to(0.0, 10.0).arc_to(10.0, true, 10.0, 0.0);
    assert_box(path(quarter), (0.0, 0.0, 10.0, 10.0));
}

#[test]
fn a_rotated_ellipse_arc_reaches_its_extrema() {
    // Half of a 45-degree ellipse, rx 10 ry 5: the extreme x of the full ellipse is hypot(10cos45, 5sin45).
    let half = PathData(vec![
        PathCommand::M(-7.0710678118654755, -7.0710678118654755),
        PathCommand::A { rx: 10.0, ry: 5.0, rot: 45.0, large: false, sweep: true, x: 7.0710678118654755, y: 7.0710678118654755 },
    ]);
    let b = path(half);
    let reach = ((10.0f64 * 0.5f64.sqrt()).powi(2) + (5.0f64 * 0.5f64.sqrt()).powi(2)).sqrt();
    assert!(b.x2 <= reach + 1e-9 && b.x2 > 7.07, "{b:?}");
    assert!(b.width() > 14.14 && b.height() > 14.14, "{b:?}");
}

#[test]
fn a_cubic_bulges_past_its_endpoints() {
    let curve = PathData(vec![PathCommand::M(0.0, 0.0), PathCommand::C(0.0, 10.0, 10.0, 10.0, 10.0, 0.0)]);
    assert_box(path(curve), (0.0, 0.0, 10.0, 7.5));
}

#[test]
fn a_cubic_with_an_inflection_reaches_both_extrema() {
    let curve = PathData(vec![PathCommand::M(0.0, 0.0), PathCommand::C(10.0, 10.0, -10.0, 10.0, 0.0, 0.0)]);
    let b = path(curve);
    assert!(b.x1 < -1.0 && b.x2 > 1.0, "{b:?}");
}

#[test]
fn text_with_no_measured_box_has_no_bounds() {
    assert_eq!(Node::text(Role::Label, (0.0, 0.0), Attrs::new(), "x").bounds(), None);
}

#[test]
fn walk_yields_each_node_with_its_composed_map_in_paint_order() {
    let leaf = rect(Role::Chrome, 0.0, 0.0, 1.0, 1.0).translate(1.0, 0.0);
    let tree = vec![
        Node::group(Role::Chrome, Attrs::new(), vec![leaf]).translate(10.0, 0.0),
        rect(Role::Chrome, 0.0, 0.0, 1.0, 1.0),
    ];
    let maps: Vec<f64> = walk(&tree).map(|(_, m)| m.e).collect();
    assert_eq!(maps, vec![10.0, 11.0, 0.0]);
}

#[test]
fn definitions_are_not_drawn() {
    let defs = Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![rect(Role::Chrome, 0.0, 0.0, 99.0, 99.0)]));
    let group = Node::group(Role::Chrome, Attrs::new(), vec![defs, rect(Role::Chrome, 0.0, 0.0, 1.0, 1.0)]);
    assert_box(group.bounds().unwrap(), (0.0, 0.0, 1.0, 1.0));
}

#[test]
#[should_panic(expected = "geometry")]
fn geometry_never_goes_in_attrs() {
    Node::rect(Role::Chrome, (0.0, 0.0, 1.0, 1.0), Attrs::new().set("width", "5"));
}

#[test]
#[should_panic(expected = "mark")]
fn a_mark_is_a_primitive() {
    Node::group(mark(0), Attrs::new(), Vec::new());
}

#[test]
#[should_panic(expected = "text mark")]
fn a_text_mark_needs_its_measured_box() {
    Node::text(mark(0), (0.0, 0.0), Attrs::new(), "x");
}

#[test]
#[should_panic(expected = "geometry")]
fn a_line_offset_never_goes_in_attrs() {
    Node::text(Role::Label, (0.0, 0.0), Attrs::new().num("dy", 3.0), "x");
}

#[test]
#[should_panic(expected = "geometry")]
fn a_tspan_never_carries_geometry_in_attrs() {
    let tspan = Run::Tspan { x: None, y: None, dx: None, dy: None, attrs: Attrs::new().num("dx", 1.0), runs: Vec::new() };
    Node::new(Role::Label, Attrs::new(), Shape::Text { at: (0.0, 0.0), bounds: None, runs: vec![tspan] });
}

#[test]
fn a_poly_is_a_mark_with_the_box_of_its_points() {
    let poly = Node::poly(mark(0), vec![(1.0, 5.0), (4.0, 2.0), (3.0, 9.0)], false, Attrs::new());
    assert_box(poly.bounds().unwrap(), (1.0, 2.0, 4.0, 9.0));
    assert_eq!(marks(&[poly]).len(), 1);
}

#[test]
fn walk_does_not_descend_into_what_is_not_drawn() {
    let clip = Node::new(Role::Chrome, Attrs::new(), Shape::ClipPath(vec![rect(mark(0), 0.0, 0.0, 9.0, 9.0)]));
    let defs = Node::new(Role::Chrome, Attrs::new(), Shape::Defs(vec![rect(mark(1), 0.0, 0.0, 9.0, 9.0)]));
    assert!(marks(&[clip, defs]).is_empty());
}

#[test]
fn a_scale_composes_with_a_translate_and_a_pattern_draws_no_marks() {
    let moved = Transform::Translate { x: 10.0, y: 20.0 };
    let map = compose(&[moved, Transform::Scale(2.0)]);
    assert_eq!(map.apply((1.0, 1.0)), (12.0, 22.0));
    let tile = rect(mark(0), 0.0, 0.0, 1.0, 1.0);
    let pattern = Node::new(Role::Chrome, Attrs::new(), Shape::Pattern { w: 4.0, h: 4.0, transform: Some(Transform::Rotate { deg: 45.0, about: None }), children: vec![tile] });
    assert_eq!(marks(&[pattern]).len(), 0);
}

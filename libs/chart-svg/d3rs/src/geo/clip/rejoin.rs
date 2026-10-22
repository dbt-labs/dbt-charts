//! d3-geo 3.1.1 `src/clip/rejoin.js`: a generalized polygon clipping algorithm. Given a polygon that has
//! been cut into its visible line segments, it rejoins the segments by interpolating along the clip edge.

use super::buffer::BufferPoint;
use crate::geo::math::EPSILON;
use crate::geo::point_equal::point_equal;
use crate::geo::stream::GeoStream;
use crate::js_sort::js_sort;

/// d3's `Intersection`. The nodes of both lists live in one arena and refer to each other by index; `z` is
/// the index of the subject segment a node starts or ends, absent on the clip list's twins.
struct Intersection {
    x: [f64; 2],
    z: Option<usize>,
    o: usize,
    e: bool,
    v: bool,
    n: usize,
    p: usize,
}

/// `interpolate` takes d3's `from`/`to` pair, absent for the whole clip edge. A segment of one point (a
/// rectangle buffers one for an empty ring) is skipped.
pub fn clip_rejoin<S: GeoStream>(
    mut segments: Vec<Vec<BufferPoint>>,
    compare_intersection: impl Fn([f64; 2], [f64; 2]) -> f64,
    mut start_inside: bool,
    interpolate: impl Fn(Option<([f64; 2], [f64; 2])>, i32, &mut S),
    stream: &mut S,
) -> Result<(), String> {
    let mut nodes: Vec<Intersection> = Vec::new();
    let mut subject: Vec<usize> = Vec::new();
    let mut clip: Vec<usize> = Vec::new();

    for (index, segment) in segments.iter_mut().enumerate() {
        if segment.len() <= 1 {
            continue;
        }
        let n = segment.len() - 1;

        if point_equal(segment[0].xy, segment[n].xy) {
            if segment[0].m.is_none() && segment[n].m.is_none() {
                stream.line_start();
                for p in &segment[..n] {
                    stream.point(p.xy[0], p.xy[1], None);
                }
                stream.line_end();
                continue;
            }
            // handle degenerate cases by moving the point
            segment[n].xy[0] += 2.0 * EPSILON;
        }

        let first = nodes.len();
        for (offset, (xy, z, entry)) in [
            (segment[0].xy, Some(index), true),
            (segment[0].xy, None, false),
            (segment[n].xy, Some(index), false),
            (segment[n].xy, None, true),
        ]
        .into_iter()
        .enumerate()
        {
            // Nodes come in (subject, clip) pairs that are each other's `o`.
            let other = first + (offset ^ 1);
            nodes.push(Intersection { x: xy, z, o: other, e: entry, v: false, n: 0, p: 0 });
            if z.is_some() {
                subject.push(first + offset);
            } else {
                clip.push(first + offset);
            }
        }
    }

    if subject.is_empty() {
        return Ok(());
    }

    // Without NaN the comparator is a difference of a key per point: a consistent total order, where any
    // stable sort agrees with V8's. A NaN key breaks the order, so only then is V8's algorithm ported.
    if clip.iter().any(|&i| nodes[i].x.iter().any(|v| v.is_nan())) {
        js_sort(&mut clip, |a, b| compare_intersection(nodes[a].x, nodes[b].x))?;
    } else {
        clip.sort_by(|&a, &b| compare_intersection(nodes[a].x, nodes[b].x).partial_cmp(&0.0).expect("no NaN key"));
    }
    link(&mut nodes, &subject);
    link(&mut nodes, &clip);

    for &i in &clip {
        start_inside = !start_inside;
        nodes[i].e = start_inside;
    }

    let start = subject[0];
    loop {
        // Find first unvisited intersection.
        let mut current = start;
        let mut is_subject = true;
        while nodes[current].v {
            current = nodes[current].n;
            if current == start {
                return Ok(());
            }
        }
        stream.line_start();
        loop {
            nodes[current].v = true;
            let other = nodes[current].o;
            nodes[other].v = true;
            if nodes[current].e {
                if is_subject {
                    let points = nodes[current].z.expect("a subject node has a segment");
                    for p in &segments[points] {
                        stream.point(p.xy[0], p.xy[1], None);
                    }
                } else {
                    interpolate(Some((nodes[current].x, nodes[nodes[current].n].x)), 1, stream);
                }
                current = nodes[current].n;
            } else {
                if is_subject {
                    let points = nodes[nodes[current].p].z.expect("a subject node has a segment");
                    for p in segments[points].iter().rev() {
                        stream.point(p.xy[0], p.xy[1], None);
                    }
                } else {
                    interpolate(Some((nodes[current].x, nodes[nodes[current].p].x)), -1, stream);
                }
                current = nodes[current].p;
            }
            current = nodes[current].o;
            is_subject = !is_subject;
            if nodes[current].v {
                break;
            }
        }
        stream.line_end();
    }
}

/// Links `order` into a ring (`order` is never empty: `subject` is checked and `clip` has as many nodes).
fn link(nodes: &mut [Intersection], order: &[usize]) {
    for (i, &a) in order.iter().enumerate() {
        let b = order[(i + 1) % order.len()];
        nodes[a].n = b;
        nodes[b].p = a;
    }
}

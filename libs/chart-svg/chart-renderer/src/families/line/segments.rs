//! A line or area rule restyles the segment between two matching neighbors.
//!
//! A rule is read per row, so it styles points. The line and the area are drawn between points,
//! and the law that bridges the two is the agreement of a segment's endpoints: the segment
//! between two neighboring drawn points of a series takes, key by key, the value that BOTH
//! endpoints' merged patches resolve to; a key the two disagree on, or that either leaves
//! unset, keeps the base style. So a lone matching point restyles no segment, and a run of
//! matching points restyles every segment inside it but not the one that steps out of it.

/// What the matched rules set on the stroke of the line a datum starts or ends.
#[derive(Default, Clone, PartialEq, Debug)]
pub(crate) struct LineStyle {
    pub(crate) color: Option<String>,
    pub(crate) width: Option<f64>,
    pub(crate) dasharray: Option<String>,
}

/// What the matched rules set on the fill of the area a datum starts or ends.
#[derive(Default, Clone, PartialEq, Debug)]
pub(crate) struct AreaStyle {
    pub(crate) color: Option<String>,
    pub(crate) opacity: Option<f64>,
}

/// The per-key agreement of two endpoints' styles.
pub(crate) trait Agree: Sized {
    fn agree(&self, other: &Self) -> Self;
}

fn same<T: PartialEq + Clone>(a: &Option<T>, b: &Option<T>) -> Option<T> {
    if a == b { a.clone() } else { None }
}

impl Agree for LineStyle {
    fn agree(&self, other: &Self) -> Self {
        LineStyle {
            color: same(&self.color, &other.color),
            width: same(&self.width, &other.width),
            dasharray: same(&self.dasharray, &other.dasharray),
        }
    }
}

impl Agree for AreaStyle {
    fn agree(&self, other: &Self) -> Self {
        AreaStyle { color: same(&self.color, &other.color), opacity: same(&self.opacity, &other.opacity) }
    }
}

/// The style of each segment of a run, from its points' styles in drawing order.
pub(crate) fn segment_styles<S: Agree>(points: &[S]) -> Vec<S> {
    points.windows(2).map(|pair| pair[0].agree(&pair[1])).collect()
}

/// The maximal runs of equal style among `segments`, as `(first, end, style)` over segment
/// indices: segments `first..end` share `style`, which is the points `first..=end` of the run.
pub(crate) fn style_runs<S: PartialEq + Clone>(segments: &[S]) -> Vec<(usize, usize, S)> {
    let mut runs: Vec<(usize, usize, S)> = Vec::new();
    for (k, style) in segments.iter().enumerate() {
        match runs.last_mut() {
            Some((_, end, last)) if last == style => *end = k + 1,
            _ => runs.push((k, k + 1, style.clone())),
        }
    }
    runs
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(color: Option<&str>, width: Option<f64>) -> LineStyle {
        LineStyle { color: color.map(String::from), width, dasharray: None }
    }

    #[test]
    fn a_segment_keeps_a_key_only_where_both_endpoints_resolve_the_same_value() {
        let a = line(Some("gray"), Some(3.0));
        let b = line(Some("gray"), None);
        assert_eq!(a.agree(&b), line(Some("gray"), None));
        assert_eq!(a.agree(&line(Some("red"), Some(3.0))), line(None, Some(3.0)));
        assert_eq!(a.agree(&LineStyle::default()), LineStyle::default());
    }

    #[test]
    fn agreement_is_symmetric() {
        let a = line(Some("gray"), Some(3.0));
        let b = line(Some("gray"), Some(5.0));
        assert_eq!(a.agree(&b), b.agree(&a));
    }

    #[test]
    fn an_isolated_point_restyles_no_segment() {
        let points = [LineStyle::default(), line(Some("gray"), None), LineStyle::default()];
        assert!(segment_styles(&points).iter().all(|s| *s == LineStyle::default()));
    }

    #[test]
    fn the_boundary_segment_of_a_run_keeps_the_base_style() {
        let g = line(Some("gray"), None);
        let points = [LineStyle::default(), g.clone(), g.clone(), g.clone()];
        let segments = segment_styles(&points);
        assert_eq!(segments, [LineStyle::default(), g.clone(), g]);
    }

    #[test]
    fn an_area_agrees_on_its_own_keys() {
        let a = AreaStyle { color: Some("gray".into()), opacity: Some(0.4) };
        let b = AreaStyle { color: Some("gray".into()), opacity: Some(0.5) };
        assert_eq!(a.agree(&b), AreaStyle { color: Some("gray".into()), opacity: None });
    }

    #[test]
    fn equal_neighbors_draw_as_one_run() {
        assert_eq!(style_runs(&[1, 1, 2, 2, 2, 1]), [(0, 2, 1), (2, 5, 2), (5, 6, 1)]);
        assert!(style_runs::<u8>(&[]).is_empty());
    }
}

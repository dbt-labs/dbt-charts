//! Which family a text is measured in.

/// The family text is measured in: Source Serif keeps its own file, every other
/// family measures as Inter (labels may paint in another family, e.g. dbt Sans
/// Tabular, but gutters are sized with Inter advances).
pub fn stand_in_family(family: &str) -> &str {
    if family.contains("Source Serif") {
        // The `Web` alias is a paint-time CSS font name; production measures the
        // bundled file under the authored family.
        family.strip_prefix("'Source Serif 4 Web', ").unwrap_or(family)
    } else {
        "Inter"
    }
}

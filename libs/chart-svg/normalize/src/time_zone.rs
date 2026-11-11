//! Python's `check_session_time_zones`: a warning where a source cannot honor the board's
//! `regional.time_zone`, once per named source a SQL query uses, in first-use order.
//!
//! The host supplies each source's facts ([`SourceZone`]) for the sources it parsed and whose type
//! has a dialect; a `dbt_profile` source, a file source and a malformed one (reported where it is
//! used) have none and are skipped.

use crate::sources::{SourceRegistry, SourceUse};
use diagnostic::{diag, Diagnostic};
use std::collections::BTreeSet;

/// What the check reads of one source's config.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceZone {
    /// The config's `type` (a dialect name).
    pub adapter_type: String,
    /// The zone the source authors itself (Trino's `timezone`, DuckDB's `settings.timezone`).
    pub authored_zone: Option<String>,
}

impl SourceZone {
    pub fn new(adapter_type: impl Into<String>, authored_zone: Option<&str>) -> Self {
        Self {
            adapter_type: adapter_type.into(),
            authored_zone: authored_zone.map(str::to_string),
        }
    }
}

/// Ported from `dbt_charts/core/dialects`: `time_zone_per_request` and `session_time_zone_sql`.
/// Whether the dialect runs a session in the board's zone, by its type: `Some(true)` for one that
/// sets a session statement or takes the zone per request, `Some(false)` for one with neither,
/// `None` for a type with no dialect.
fn honors_session_zone(adapter_type: &str) -> Option<bool> {
    match adapter_type {
        "postgres" | "postgresql" | "duckdb" | "snowflake" | "bigquery" | "redshift"
        | "databricks" | "spark" | "trino" | "clickhouse" | "lakecompute" => Some(true),
        "sqlite" | "mysql" | "mariadb" | "sqlserver" | "mssql" | "fabric" | "athena" | "presto" => {
            Some(false)
        }
        _ => None,
    }
}

pub(crate) fn check(zone: &str, uses: &[SourceUse], registry: &SourceRegistry) -> Vec<Diagnostic> {
    let mut seen = BTreeSet::new();
    let mut out = Vec::new();
    for usage in uses.iter().filter(|u| seen.insert(u.source.as_str())) {
        let Some(facts) = registry.zone(&usage.source) else {
            continue;
        };
        let Some(honors) = honors_session_zone(&facts.adapter_type) else {
            continue;
        };
        if zone != "UTC" && !honors {
            out.push(diag::warn_session_time_zone_unsupported(
                usage.source.as_str(),
                facts.adapter_type.as_str(),
                zone,
            ));
        }
        if let Some(own) = facts.authored_zone.as_deref().filter(|own| *own != zone) {
            out.push(diag::warn_source_time_zone_overridden(
                usage.source.as_str(),
                own,
                zone,
            ));
        }
    }
    out
}

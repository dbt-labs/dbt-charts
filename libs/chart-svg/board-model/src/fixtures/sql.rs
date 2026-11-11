//! A [`SqlAnalysis`] for tests that need no SQL parser.

use crate::sql_analysis::{
    Analysis, Guard, SetupAnalysis, Skeleton, SqlAnalysis, SqlOutcome, SqlRequest,
};

/// A host that parses nothing: the skeleton is the text, the guard is undetermined, no lints, no
/// output columns. For a test that needs a board to compile and does not care what its SQL says.
pub struct Opaque;

impl SqlAnalysis for Opaque {
    fn analyze(&self, request: &SqlRequest) -> SqlOutcome {
        SqlOutcome::Analyzed(Analysis {
            skeleton: Skeleton {
                sql: request.sql.to_string(),
                placeholders: Vec::new(),
            },
            select_only: Guard::Undetermined,
            lints: Vec::new(),
            findings: Vec::new(),
            setup: request.setup_sql.map(|_| SetupAnalysis {
                guard: Guard::Undetermined,
                lints: Vec::new(),
            }),
            outputs: Vec::new(),
            outputs_complete: false,
            scope: Vec::new(),
        })
    }
}

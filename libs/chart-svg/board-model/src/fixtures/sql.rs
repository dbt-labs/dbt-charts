//! A [`SqlAnalysis`] that replays outcomes recorded from a real host, so a test needs no SQL parser.

use crate::query::{QueryDialect, SourceName};
use crate::sql_analysis::{
    Analysis, Guard, SetupAnalysis, Skeleton, SqlAnalysis, SqlOutcome, SqlRequest,
};
use std::collections::BTreeMap;

type Key = (String, Option<String>, Option<String>, Option<&'static str>);

fn key(request: &SqlRequest) -> Key {
    (
        request.sql.to_string(),
        request.setup_sql.map(str::to_string),
        request.source.map(|s| s.as_str().to_string()),
        request.dialect.map(QueryDialect::as_str),
    )
}

/// Outcomes keyed by the whole request. A request nobody recorded is a bug in the test, so it
/// panics with the request rather than guess an outcome.
#[derive(Debug, Default)]
pub struct Recorded(BTreeMap<Key, SqlOutcome>);

impl Recorded {
    pub fn insert(&mut self, request: &SqlRequest, outcome: SqlOutcome) {
        self.0.insert(key(request), outcome);
    }

    /// The request as owned parts, for a recorder that holds owned text.
    pub fn insert_owned(
        &mut self,
        sql: &str,
        setup_sql: Option<&str>,
        source: Option<&str>,
        dialect: Option<QueryDialect>,
        outcome: SqlOutcome,
    ) -> Result<(), String> {
        let source = source.map(SourceName::try_from).transpose()?;
        self.insert(
            &SqlRequest {
                sql,
                setup_sql,
                source: source.as_ref(),
                dialect,
            },
            outcome,
        );
        Ok(())
    }
}

impl SqlAnalysis for Recorded {
    // Test support behind a feature: an unrecorded request is a bug in the fixture.
    #[allow(clippy::panic)]
    fn analyze(&self, request: &SqlRequest) -> SqlOutcome {
        match self.0.get(&key(request)) {
            Some(outcome) => outcome.clone(),
            None => panic!("no recorded outcome for {request:?}"),
        }
    }
}

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

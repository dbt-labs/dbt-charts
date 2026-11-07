//! A result's rows, shared by every payload that draws from it.

use std::ops::{Deref, DerefMut};
use std::sync::Arc;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

type Row = Map<String, Value>;

/// Cloning is a reference bump; a holder that edits its rows (a fold, a test)
/// gets its own copy on the first write.
#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(transparent)]
pub struct SharedRows(Arc<Vec<Row>>);

impl SharedRows {
    pub fn ptr_eq(&self, other: &SharedRows) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

impl Deref for SharedRows {
    type Target = Vec<Row>;
    fn deref(&self) -> &Vec<Row> {
        &self.0
    }
}

impl DerefMut for SharedRows {
    fn deref_mut(&mut self) -> &mut Vec<Row> {
        Arc::make_mut(&mut self.0)
    }
}

impl From<Vec<Row>> for SharedRows {
    fn from(rows: Vec<Row>) -> Self {
        Self(Arc::new(rows))
    }
}

impl FromIterator<Row> for SharedRows {
    fn from_iter<I: IntoIterator<Item = Row>>(rows: I) -> Self {
        Self(Arc::new(rows.into_iter().collect()))
    }
}

impl<'a> IntoIterator for &'a SharedRows {
    type Item = &'a Row;
    type IntoIter = std::slice::Iter<'a, Row>;
    fn into_iter(self) -> Self::IntoIter {
        self.0.iter()
    }
}

impl<'a> IntoIterator for &'a mut SharedRows {
    type Item = &'a mut Row;
    type IntoIter = std::slice::IterMut<'a, Row>;
    fn into_iter(self) -> Self::IntoIter {
        self.iter_mut()
    }
}

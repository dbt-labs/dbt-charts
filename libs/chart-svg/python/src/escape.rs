//! A Python exception that is not an `Exception` (`KeyboardInterrupt`, `SystemExit`) is not a
//! finding about the board: the host seams set it aside and the entry point raises it unchanged.

use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use std::sync::{Mutex, PoisonError};

#[derive(Default)]
pub struct Escaped(Mutex<Option<PyErr>>);

impl Escaped {
    /// The text a refusal carries for `e`; a `BaseException` that is no `Exception` is kept for
    /// [`Self::take`] as well, the first one winning.
    pub fn reason(&self, py: Python<'_>, e: PyErr) -> String {
        let text = e.to_string();
        if !e.is_instance_of::<PyException>(py) {
            self.0.lock().unwrap_or_else(PoisonError::into_inner).get_or_insert(e);
        }
        text
    }

    pub fn take(&self) -> Option<PyErr> {
        self.0.lock().unwrap_or_else(PoisonError::into_inner).take()
    }
}

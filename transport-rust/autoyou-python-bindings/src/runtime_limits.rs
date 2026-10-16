// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_application_core::runtime_limits::{ExpiryRow, RateLimiter, expiring_rows_to_remove};
use pyo3::{
    exceptions::{PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyDict, PyFloat, PyInt, PyModule, PyString},
};
use std::sync::Mutex;

#[pyclass(frozen, module = "autoyou_application")]
struct RuntimeRateLimiter {
    inner: Mutex<RateLimiter>,
}

#[pymethods]
impl RuntimeRateLimiter {
    #[new]
    fn new(max_requests: u64, window_seconds: f64, max_tracked_keys: u64) -> PyResult<Self> {
        Ok(Self {
            inner: Mutex::new(
                RateLimiter::new(max_requests, window_seconds, max_tracked_keys)
                    .map_err(|_| PyValueError::new_err("Invalid rate-limit bounds"))?,
            ),
        })
    }
    fn is_allowed(&self, py: Python<'_>, key: String, now_seconds: f64) -> PyResult<bool> {
        py.detach(|| {
            let mut owner = self
                .inner
                .lock()
                .map_err(|_| PyRuntimeError::new_err("Runtime admission unavailable"))?;
            Ok(owner.is_allowed(&key, now_seconds))
        })
    }
    fn tracked_key_count(&self) -> PyResult<u64> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| PyRuntimeError::new_err("Runtime admission unavailable"))?
            .tracked_key_count())
    }
}

fn number(value: &Bound<'_, PyAny>) -> Option<f64> {
    // Cache metadata is plain numeric JSON or a numeric string, never a host callback.
    if !(value.is_exact_instance_of::<PyFloat>()
        || value.is_exact_instance_of::<PyInt>()
        || value.is_exact_instance_of::<pyo3::types::PyBool>()
        || value.is_exact_instance_of::<PyString>())
    {
        return None;
    }
    value
        .py()
        .get_type::<PyFloat>()
        .call1((value,))
        .ok()?
        .extract()
        .ok()
}

#[pyfunction]
fn prune_expiring_map(
    entries: &Bound<'_, PyDict>,
    expiry_fields: Vec<String>,
    max_entries: u64,
    now_seconds: f64,
) -> PyResult<u64> {
    if !now_seconds.is_finite() {
        return Err(PyValueError::new_err("Invalid cache clock"));
    }
    let keys = entries.keys();
    let mut rows = Vec::with_capacity(keys.len());
    for key in keys.iter() {
        let value = entries.get_item(&key)?;
        let row = value.as_ref().and_then(|value| value.cast::<PyDict>().ok());
        let (mut expires_at, mut created_at) = (None, 0.0);
        if let Some(row) = row {
            for field in &expiry_fields {
                if let Some(value) = row.get_item(field)?.filter(|value| !value.is_none()) {
                    expires_at = number(&value);
                    break;
                }
            }
            created_at = row
                .get_item("created_at")?
                .as_ref()
                .and_then(number)
                .unwrap_or(0.0);
        }
        rows.push(ExpiryRow {
            expires_at,
            created_at,
        });
    }
    let selected = expiring_rows_to_remove(&rows, max_entries, now_seconds)
        .map_err(|_| PyValueError::new_err("Invalid cache limits"))?;
    // Keep the GIL through selection and removal; retained host payloads keep their identities.
    let mut removed = 0;
    for index in selected {
        let key = keys.get_item(index)?;
        if entries.contains(&key)? {
            entries.del_item(&key)?;
            removed += 1;
        }
    }
    Ok(removed)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<RuntimeRateLimiter>()?;
    module.add_function(wrap_pyfunction!(prune_expiring_map, module)?)
}

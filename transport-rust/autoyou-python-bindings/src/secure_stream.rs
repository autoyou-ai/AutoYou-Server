// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_application_core::secure_stream as core;
use pyo3::{
    exceptions::{PyOSError, PyRuntimeError, PyValueError},
    prelude::*,
    types::{PyBytes, PyModule},
};
use std::{path::Path, sync::Mutex};

fn error(error: core::StreamError) -> PyErr {
    match error {
        core::StreamError::Invalid => PyValueError::new_err("Invalid protected stream"),
        core::StreamError::Unavailable => {
            PyOSError::new_err("Protected stream storage unavailable")
        }
    }
}
fn locked() -> PyErr {
    PyRuntimeError::new_err("Protected stream owner unavailable")
}

#[pyclass(frozen, module = "autoyou_application")]
struct FernetStreamEncryptor {
    inner: Mutex<core::FernetEncryptor>,
}
#[pymethods]
impl FernetStreamEncryptor {
    #[new]
    fn new(py: Python<'_>, key: &Bound<'_, PyBytes>) -> PyResult<Self> {
        let key = key.as_bytes();
        Ok(Self {
            inner: Mutex::new(
                py.detach(|| core::FernetEncryptor::new(key))
                    .map_err(error)?,
            ),
        })
    }
    fn header<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        Ok(PyBytes::new(
            py,
            self.inner.lock().map_err(|_| locked())?.header(),
        ))
    }
    fn update<'py>(
        &self,
        py: Python<'py>,
        block: &Bound<'_, PyBytes>,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let block = block.as_bytes();
        let output = py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| locked())?
                .update(block)
                .map_err(error)
        })?;
        Ok(PyBytes::new(py, &output))
    }
    fn finish<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let output = py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| locked())?
                .finish()
                .map_err(error)
        })?;
        Ok(PyBytes::new(py, &output))
    }
}

#[pyclass(frozen, module = "autoyou_application")]
struct FernetStreamDecryptor {
    inner: Mutex<Option<core::FernetReader>>,
}
#[pymethods]
impl FernetStreamDecryptor {
    #[new]
    fn new(
        py: Python<'_>,
        key: &Bound<'_, PyBytes>,
        directory: String,
        maximum_bytes: Option<u64>,
    ) -> PyResult<Self> {
        let key = key.as_bytes();
        let owner = py
            .detach(|| core::FernetReader::new(key, Path::new(&directory), maximum_bytes))
            .map_err(error)?;
        Ok(Self {
            inner: Mutex::new(Some(owner)),
        })
    }
    fn update(&self, py: Python<'_>, block: &Bound<'_, PyBytes>) -> PyResult<()> {
        let block = block.as_bytes();
        py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| locked())?
                .as_mut()
                .ok_or_else(locked)?
                .update(block)
                .map_err(error)
        })
    }
    fn authenticate(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| locked())?
                .as_mut()
                .ok_or_else(locked)?
                .finish()
                .map_err(error)
        })
    }
    fn read_next<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyBytes>>> {
        let output = py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| locked())?
                .as_mut()
                .ok_or_else(locked)?
                .read_next()
                .map_err(error)
        })?;
        Ok(output.map(|bytes| PyBytes::new(py, &bytes)))
    }
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.inner.lock().map_err(|_| locked())?.take();
            Ok(())
        })
    }
}

#[pyfunction]
fn fernet_encrypt_bytes<'py>(
    py: Python<'py>,
    key: &Bound<'_, PyBytes>,
    data: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let (key, data) = (key.as_bytes(), data.as_bytes());
    let output = py
        .detach(|| core::encrypt_bytes(key, data))
        .map_err(error)?;
    Ok(PyBytes::new(py, &output))
}
#[pyfunction]
fn fernet_decrypt_bytes<'py>(
    py: Python<'py>,
    key: &Bound<'_, PyBytes>,
    token: &Bound<'_, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let (key, token) = (key.as_bytes(), token.as_bytes());
    let output = py
        .detach(|| core::decrypt_bytes(key, token))
        .map_err(error)?;
    Ok(PyBytes::new(py, &output))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<FernetStreamEncryptor>()?;
    module.add_class::<FernetStreamDecryptor>()?;
    module.add_function(wrap_pyfunction!(fernet_encrypt_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(fernet_decrypt_bytes, module)?)
}

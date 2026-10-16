// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use autoyou_application_core::{atomic_file, protected_file as core};
use pyo3::{
    exceptions::{PyOSError, PyPermissionError, PyValueError},
    prelude::*,
    types::{PyAny, PyBytes, PyInt, PyModule},
};
use std::{io, path::Path, sync::Mutex};

fn error(error: io::Error) -> PyErr {
    match error.kind() {
        io::ErrorKind::InvalidInput | io::ErrorKind::InvalidData => {
            PyValueError::new_err(error.to_string())
        }
        io::ErrorKind::PermissionDenied => PyPermissionError::new_err(error.to_string()),
        _ => PyOSError::new_err("Protected file storage is unavailable"),
    }
}
fn unavailable() -> io::Error {
    io::Error::other("protected file owner is unavailable")
}
fn commit_snapshot(py: Python<'_>, inner: &Mutex<Option<core::FileWriter>>) -> PyResult<()> {
    let result = py.detach(|| {
        atomic_file::retry_commit(
            || {
                inner
                    .lock()
                    .map_err(|_| unavailable())?
                    .as_mut()
                    .ok_or_else(unavailable)?
                    .commit_once()
            },
            // Python can retain a Windows file handle until collection; no native lock is held here.
            || {
                Python::attach(|py| {
                    py.import("gc")
                        .and_then(|gc| gc.call_method0("collect"))
                        .map(|_| ())
                        .map_err(|_| unavailable())
                })
            },
        )
    });
    if result.is_err() {
        if let Ok(mut owner) = inner.lock() {
            owner.take();
        }
    }
    result.map_err(error)
}

#[pyclass(frozen, module = "autoyou_application")]
struct ProtectedFileWriter {
    inner: Mutex<Option<core::FileWriter>>,
}
#[pymethods]
impl ProtectedFileWriter {
    #[new]
    #[pyo3(signature=(path, directory, key, expected_size, expected_sha256))]
    fn new(
        py: Python<'_>,
        path: String,
        directory: Option<String>,
        key: Option<&Bound<'_, PyBytes>>,
        expected_size: &Bound<'_, PyAny>,
        expected_sha256: &Bound<'_, PyBytes>,
    ) -> PyResult<Self> {
        if !expected_size.is_exact_instance_of::<PyInt>() {
            return Err(PyValueError::new_err("invalid protected stream descriptor"));
        }
        let size = expected_size
            .extract::<u64>()
            .map_err(|_| PyValueError::new_err("invalid protected stream descriptor"))?;
        let key = key.map(|key| key.as_bytes());
        let digest = expected_sha256.as_bytes();
        let owner = py
            .detach(|| {
                core::FileWriter::verified(
                    Path::new(&path),
                    directory.as_deref().map(Path::new),
                    key,
                    size,
                    digest,
                )
            })
            .map_err(error)?;
        Ok(Self {
            inner: Mutex::new(Some(owner)),
        })
    }
    fn update(&self, py: Python<'_>, block: &Bound<'_, PyAny>) -> PyResult<()> {
        let block = match block.cast::<PyBytes>() {
            Ok(block) => block,
            Err(_) => {
                self.discard(py)?;
                return Err(PyValueError::new_err(
                    "protected stream block exceeded its bound",
                ));
            }
        };
        let block = block.as_bytes();
        py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| unavailable())?
                .as_mut()
                .ok_or_else(unavailable)?
                .update(block)
        })
        .map_err(error)
    }
    fn prepare(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| unavailable())?
                .as_mut()
                .ok_or_else(unavailable)?
                .prepare()
        })
        .map_err(error)
    }
    fn commit(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.inner
                .lock()
                .map_err(|_| unavailable())?
                .as_mut()
                .ok_or_else(unavailable)?
                .commit()
        })
        .map_err(error)
    }
    fn discard(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| {
            self.inner.lock().map_err(|_| unavailable())?.take();
            Ok(())
        })
        .map_err(error)
    }
}

#[pyfunction]
fn atomic_write_file(py: Python<'_>, path: String, data: &Bound<'_, PyBytes>) -> PyResult<()> {
    let data = data.as_bytes();
    let writer = py
        .detach(|| core::prepare_bytes(Path::new(&path), data))
        .map_err(error)?;
    commit_snapshot(py, &Mutex::new(Some(writer)))
}
#[pyfunction]
fn write_plain_file(py: Python<'_>, path: String, data: &Bound<'_, PyBytes>) -> PyResult<()> {
    let data = data.as_bytes();
    let writer = py
        .detach(|| core::prepare_plain(Path::new(&path), data))
        .map_err(error)?;
    commit_snapshot(py, &Mutex::new(Some(writer)))
}
#[pyfunction]
fn write_sealed_file(
    py: Python<'_>,
    path: String,
    key: &Bound<'_, PyBytes>,
    data: &Bound<'_, PyBytes>,
    sqlite: bool,
) -> PyResult<()> {
    let key = key.as_bytes();
    let data = data.as_bytes();
    let writer = py
        .detach(|| core::prepare_sealed(Path::new(&path), key, data, sqlite))
        .map_err(error)?;
    commit_snapshot(py, &Mutex::new(Some(writer)))
}
#[pyfunction]
fn normalise_sqlite_snapshot<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyBytes>,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = data.as_bytes();
    if !core::sqlite_needs_normalisation(bytes) {
        return Ok(data.clone());
    }
    PyBytes::new_with(py, bytes.len(), |output| {
        output.copy_from_slice(bytes);
        core::normalise_sqlite_header(output);
        Ok(())
    })
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<ProtectedFileWriter>()?;
    module.add_function(wrap_pyfunction!(atomic_write_file, module)?)?;
    module.add_function(wrap_pyfunction!(write_plain_file, module)?)?;
    module.add_function(wrap_pyfunction!(write_sealed_file, module)?)?;
    module.add_function(wrap_pyfunction!(normalise_sqlite_snapshot, module)?)
}

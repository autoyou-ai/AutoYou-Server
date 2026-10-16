// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_application_core::protected_file as core;
use std::{
    io,
    path::Path,
    sync::{Arc, Mutex},
};
use zeroize::Zeroizing;

fn error(error: io::Error) -> BindingError {
    match error.kind() {
        io::ErrorKind::InvalidInput | io::ErrorKind::InvalidData => BindingError::InvalidInput,
        _ => BindingError::StorageUnavailable,
    }
}

#[derive(uniffi::Object)]
pub struct ProtectedFileWriter {
    inner: Mutex<Option<core::FileWriter>>,
}
#[uniffi::export]
impl ProtectedFileWriter {
    #[uniffi::constructor]
    pub fn new(
        path: String,
        directory: Option<String>,
        key: Option<Vec<u8>>,
        expected_size: u64,
        expected_sha256: Vec<u8>,
    ) -> Result<Arc<Self>, BindingError> {
        let key = key.map(Zeroizing::new);
        let owner = core::FileWriter::verified(
            Path::new(&path),
            directory.as_deref().map(Path::new),
            key.as_ref().map(|key| key.as_slice()),
            expected_size,
            &expected_sha256,
        )
        .map_err(error)?;
        Ok(Arc::new(Self {
            inner: Mutex::new(Some(owner)),
        }))
    }
    pub fn update(&self, block: Vec<u8>) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .as_mut()
            .ok_or(BindingError::StorageUnavailable)?
            .update(&block)
            .map_err(error)
    }
    pub fn prepare(&self) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .as_mut()
            .ok_or(BindingError::StorageUnavailable)?
            .prepare()
            .map_err(error)
    }
    pub fn commit(&self) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .as_mut()
            .ok_or(BindingError::StorageUnavailable)?
            .commit()
            .map_err(error)
    }
    pub fn discard(&self) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .take();
        Ok(())
    }
}
#[uniffi::export]
pub fn atomic_write_file(path: String, data: Vec<u8>) -> Result<(), BindingError> {
    core::prepare_bytes(Path::new(&path), &data)
        .and_then(|mut owner| owner.commit())
        .map_err(error)
}
#[uniffi::export]
pub fn write_plain_file(path: String, data: Vec<u8>) -> Result<(), BindingError> {
    core::prepare_plain(Path::new(&path), &data)
        .and_then(|mut owner| owner.commit())
        .map_err(error)
}
#[uniffi::export]
pub fn write_sealed_file(
    path: String,
    key: Vec<u8>,
    data: Vec<u8>,
    sqlite: bool,
) -> Result<(), BindingError> {
    let key = Zeroizing::new(key);
    core::prepare_sealed(Path::new(&path), &key, &data, sqlite)
        .and_then(|mut owner| owner.commit())
        .map_err(error)
}
#[uniffi::export]
pub fn normalise_sqlite_snapshot(mut data: Vec<u8>) -> Vec<u8> {
    core::normalise_sqlite_header(&mut data);
    data
}

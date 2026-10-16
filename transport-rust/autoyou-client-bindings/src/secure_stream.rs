// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_application_core::secure_stream as core;
use std::{
    path::Path,
    sync::{Arc, Mutex},
};
use zeroize::Zeroizing;

fn error(error: core::StreamError) -> BindingError {
    match error {
        core::StreamError::Invalid => BindingError::InvalidInput,
        core::StreamError::Unavailable => BindingError::Worker,
    }
}

#[derive(uniffi::Object)]
pub struct FernetStreamEncryptor {
    inner: Mutex<core::FernetEncryptor>,
}
#[uniffi::export]
impl FernetStreamEncryptor {
    #[uniffi::constructor]
    pub fn new(key: Vec<u8>) -> Result<Arc<Self>, BindingError> {
        let key = Zeroizing::new(key);
        Ok(Arc::new(Self {
            inner: Mutex::new(core::FernetEncryptor::new(&key).map_err(error)?),
        }))
    }
    pub fn header(&self) -> Result<Vec<u8>, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .header()
            .to_vec())
    }
    pub fn update(&self, block: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .update(&block)
            .map_err(error)
    }
    pub fn finish(&self) -> Result<Vec<u8>, BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .finish()
            .map_err(error)
    }
}

#[derive(uniffi::Object)]
pub struct FernetStreamDecryptor {
    inner: Mutex<Option<core::FernetReader>>,
}
#[uniffi::export]
impl FernetStreamDecryptor {
    #[uniffi::constructor]
    pub fn new(
        key: Vec<u8>,
        directory: String,
        maximum_bytes: Option<u64>,
    ) -> Result<Arc<Self>, BindingError> {
        let key = Zeroizing::new(key);
        Ok(Arc::new(Self {
            inner: Mutex::new(Some(
                core::FernetReader::new(&key, Path::new(&directory), maximum_bytes)
                    .map_err(error)?,
            )),
        }))
    }
    pub fn update(&self, block: Vec<u8>) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .as_mut()
            .ok_or(BindingError::Closed)?
            .update(&block)
            .map_err(error)
    }
    pub fn authenticate(&self) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .as_mut()
            .ok_or(BindingError::Closed)?
            .finish()
            .map_err(error)
    }
    pub fn read_next(&self) -> Result<Option<Vec<u8>>, BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .as_mut()
            .ok_or(BindingError::Closed)?
            .read_next()
            .map_err(error)
    }
    pub fn discard(&self) -> Result<(), BindingError> {
        self.inner.lock().map_err(|_| BindingError::Worker)?.take();
        Ok(())
    }
}

#[uniffi::export]
pub fn fernet_encrypt_bytes(key: Vec<u8>, data: Vec<u8>) -> Result<Vec<u8>, BindingError> {
    core::encrypt_bytes(&Zeroizing::new(key), &data).map_err(error)
}
#[uniffi::export]
pub fn fernet_decrypt_bytes(key: Vec<u8>, token: Vec<u8>) -> Result<Vec<u8>, BindingError> {
    core::decrypt_bytes(&Zeroizing::new(key), &token).map_err(error)
}

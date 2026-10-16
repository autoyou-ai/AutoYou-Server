// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_session::protected_state::{ProtectedStateTransaction as Transaction, StateError};
use std::{path::PathBuf, sync::{Arc, Mutex}};
use zeroize::Zeroizing;

impl From<StateError> for BindingError {
    fn from(error: StateError) -> Self { match error {
        StateError::Invalid => Self::InvalidInput,
        StateError::Busy => Self::Backpressure,
        StateError::Unavailable => Self::StorageUnavailable,
        StateError::Capacity => Self::StorageCapacity,
    } }
}

#[derive(uniffi::Object)]
pub struct ProtectedStateTransaction { pub(crate) inner: Mutex<Option<Transaction>> }

#[uniffi::export]
impl ProtectedStateTransaction {
    #[uniffi::constructor]
    pub fn new(key_directory: String) -> Result<Arc<Self>, BindingError> {
        Ok(Arc::new(Self { inner: Mutex::new(Some(Transaction::begin(PathBuf::from(key_directory))?)) }))
    }
    pub fn has_state(&self) -> Result<bool, BindingError> {
        Ok(self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.as_ref().ok_or(BindingError::Closed)?.has_state())
    }
    pub fn baseline_required(&self) -> Result<bool, BindingError> {
        Ok(self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.as_ref().ok_or(BindingError::Closed)?.baseline_required())
    }
    pub fn read(&self, secret_key: Vec<u8>) -> Result<Vec<u8>, BindingError> {
        let key = Zeroizing::new(secret_key);
        let key = key.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
        Ok(self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.as_mut().ok_or(BindingError::Closed)?.read(key)?)
    }
    pub fn commit(&self, secret_key: Vec<u8>, plaintext: Vec<u8>, baseline_plaintext: Vec<u8>) -> Result<(), BindingError> {
        let key = Zeroizing::new(secret_key);
        let plaintext = Zeroizing::new(plaintext);
        let baseline = Zeroizing::new(baseline_plaintext);
        let transaction = self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.take().ok_or(BindingError::Closed)?;
        let key = key.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
        Ok(transaction.commit(key, &plaintext, &baseline)?)
    }
    // UniFFI reserves close() for the generated Kotlin AutoCloseable owner.
    pub fn abort(&self) { if let Ok(mut inner) = self.inner.lock() { inner.take(); } }
}

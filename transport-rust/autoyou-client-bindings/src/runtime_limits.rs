// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_application_core::runtime_limits as core;
use std::{
    sync::{Arc, Mutex},
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(uniffi::Object)]
pub struct RuntimeRateLimiter {
    inner: Mutex<core::RateLimiter>,
}

#[uniffi::export]
impl RuntimeRateLimiter {
    #[uniffi::constructor]
    pub fn new(
        max_requests: u64,
        window_seconds: f64,
        max_tracked_keys: u64,
    ) -> Result<Arc<Self>, BindingError> {
        Ok(Arc::new(Self {
            inner: Mutex::new(
                core::RateLimiter::new(max_requests, window_seconds, max_tracked_keys)
                    .map_err(|_| BindingError::InvalidInput)?,
            ),
        }))
    }
    pub fn is_allowed(&self, key: String, now_seconds: Option<f64>) -> Result<bool, BindingError> {
        let mut owner = self.inner.lock().map_err(|_| BindingError::Worker)?;
        let now = now_seconds.unwrap_or_else(|| {
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs_f64()
        });
        Ok(owner.is_allowed(&key, now))
    }
    pub fn tracked_key_count(&self) -> Result<u64, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::Worker)?
            .tracked_key_count())
    }
}

#[derive(uniffi::Record)]
pub struct RuntimeExpiryRow {
    pub expires_at: Option<f64>,
    pub created_at: f64,
}

#[uniffi::export]
pub fn runtime_expiring_rows_to_remove(
    rows: Vec<RuntimeExpiryRow>,
    max_entries: u64,
    now_seconds: f64,
) -> Result<Vec<u64>, BindingError> {
    let rows: Vec<_> = rows
        .iter()
        .map(|row| core::ExpiryRow {
            expires_at: row.expires_at,
            created_at: row.created_at,
        })
        .collect();
    Ok(
        core::expiring_rows_to_remove(&rows, max_entries, now_seconds)
            .map_err(|_| BindingError::InvalidInput)?
            .into_iter()
            .map(|index| index as u64)
            .collect(),
    )
}

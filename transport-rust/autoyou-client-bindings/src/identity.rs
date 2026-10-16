// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::BindingError;
use autoyou_session::identity::{self as core, Identity, IdentityIndex};
use std::sync::{Arc, Mutex};

#[derive(Clone, uniffi::Record)]
pub struct CanonicalSessionIdentity {
    pub transport: String,
    pub sender_id: String,
    pub raw_session_id: String,
    pub owner_key: String,
    pub canonical_user_id: String,
    pub canonical_session_id: String,
    pub thread_id: Option<String>,
    pub pairing_mode: String,
    pub destination_session_id: String,
}
impl From<Identity> for CanonicalSessionIdentity {
    fn from(value: Identity) -> Self {
        Self {
            transport: value.transport,
            sender_id: value.sender_id,
            raw_session_id: value.raw_session_id,
            owner_key: value.owner_key,
            canonical_user_id: value.canonical_user_id,
            canonical_session_id: value.canonical_session_id,
            thread_id: value.thread_id,
            pairing_mode: value.pairing_mode,
            destination_session_id: value.destination_session_id,
        }
    }
}

#[derive(uniffi::Object)]
pub struct SessionIdentityIndex {
    inner: Mutex<IdentityIndex>,
}
#[uniffi::export]
impl SessionIdentityIndex {
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(IdentityIndex::default()),
        })
    }
    pub fn bind(
        &self,
        transport: String,
        sender_id: String,
        raw_session_id: Option<String>,
        thread_id: Option<String>,
        pairing_mode: String,
        destination_session_id: String,
    ) -> Result<CanonicalSessionIdentity, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .bind(
                &transport,
                &sender_id,
                raw_session_id.as_deref(),
                thread_id.as_deref(),
                &pairing_mode,
                &destination_session_id,
            )
            .into())
    }
    pub fn release(
        &self,
        raw_session_id: String,
        expected_owner_key: String,
    ) -> Result<bool, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .release(&raw_session_id, &expected_owner_key))
    }
    pub fn alias(
        &self,
        existing_session_id: String,
        alias_session_id: String,
        thread_id: Option<String>,
    ) -> Result<Option<CanonicalSessionIdentity>, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .alias(
                &existing_session_id,
                &alias_session_id,
                thread_id.as_deref(),
            )
            .map(Into::into))
    }
    pub fn resolve_transport(
        &self,
        transport: String,
        sender_id: String,
        thread_id: Option<String>,
    ) -> Result<CanonicalSessionIdentity, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .resolve_transport(&transport, &sender_id, thread_id.as_deref())
            .into())
    }
    pub fn resolve_webrtc(
        &self,
        session_id: String,
        thread_id: Option<String>,
    ) -> Result<CanonicalSessionIdentity, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .resolve_webrtc(&session_id, thread_id.as_deref())
            .into())
    }
    pub fn known_owners(&self) -> Result<Vec<String>, BindingError> {
        Ok(self
            .inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .known_owners())
    }
    pub fn clear_sessions(&self) -> Result<(), BindingError> {
        self.inner
            .lock()
            .map_err(|_| BindingError::StorageUnavailable)?
            .clear_sessions();
        Ok(())
    }
}

#[uniffi::export]
pub fn identity_owner_key(transport: String, sender_id: String) -> String {
    core::owner_key(&transport, &sender_id)
}
#[uniffi::export]
pub fn identity_transport_alias(transport: String, sender_id: String) -> String {
    core::transport_alias(&transport, &sender_id)
}
#[uniffi::export]
pub fn identity_webrtc_alias(session_id: String) -> String {
    core::webrtc_alias(&session_id)
}
#[uniffi::export]
pub fn identity_guest_owner_key(session_id: String) -> String {
    core::guest_owner_key(&session_id)
}
#[uniffi::export]
pub fn identity_canonical_user_id(owner_key: String) -> String {
    core::canonical_user_id(&owner_key)
}
#[uniffi::export]
pub fn identity_canonical_session_id(owner_key: String, thread_id: Option<String>) -> String {
    core::canonical_session_id(&owner_key, thread_id.as_deref())
}

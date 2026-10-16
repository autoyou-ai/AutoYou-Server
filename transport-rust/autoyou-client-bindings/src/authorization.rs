// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::{BindingError, ProtectedStateTransaction, SharedEndpoint};
use autoyou_session::{authorization::{self, AuthorizationError as PolicyError, AuthorizationValue as Value, PreparedAuthorization},
    client::ClientGrant, protected_state::StateError};
use std::sync::{Arc, Mutex};
use zeroize::Zeroizing;

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum AuthorizationError {
    #[error("{reason}")] Denied { reason: String },
    #[error("protected authorization state is invalid")] InvalidState,
    #[error("protected authorization storage is unavailable")] Unavailable,
    #[error("protected authorization storage exceeds its bound")] Capacity,
    #[error("protected authorization transaction is closed")] Closed,
}
impl From<PolicyError> for AuthorizationError {
    fn from(value: PolicyError) -> Self { match value {
        PolicyError::Denied(reason) => Self::Denied { reason: reason.into() },
        PolicyError::InvalidState => Self::InvalidState,
        PolicyError::Storage(StateError::Capacity) => Self::Capacity,
        PolicyError::Storage(_) => Self::Unavailable,
    } }
}

#[derive(Clone, uniffi::Record)]
pub struct EndpointGrantRecord {
    pub endpoint_id: String, pub device_id: String, pub owner_key: String, pub canonical_user_id: String,
    pub conversation_key: String, pub origin_transport: String, pub origin_sender_id: String,
    pub pairing_mode: String, pub device_ownership: String, pub authorization_epoch: u64,
    pub expires_at_ms: u64, pub scopes: Vec<String>,
}
impl From<ClientGrant> for EndpointGrantRecord {
    fn from(value: ClientGrant) -> Self {
        Self { endpoint_id: value.endpoint_id, device_id: value.device_id, owner_key: value.owner_key,
            canonical_user_id: value.canonical_user_id, conversation_key: value.conversation_key,
            origin_transport: value.origin_transport, origin_sender_id: value.origin_sender_id,
            pairing_mode: value.pairing_mode, device_ownership: value.device_ownership,
            authorization_epoch: value.authorization_epoch, expires_at_ms: value.expires_at_ms, scopes: value.scopes }
    }
}
#[derive(Clone, uniffi::Record)]
pub struct AuthorityDevice { pub device_id: String, pub endpoint_id: String }
#[derive(Clone, uniffi::Record)]
pub struct GrantRevocation { pub device_id: String, pub authorization_epoch: u64 }
#[derive(Clone, uniffi::Enum)]
pub enum AuthorizationValue {
    Unit,
    Peer { grant: EndpointGrantRecord, generation: u64, core_device_id: Option<String> },
    Identity { grant: Option<EndpointGrantRecord> },
    Epoch { epoch: u64 },
    AuthorityDevice { device_id: Option<String> },
    AuthorityDevices { devices: Vec<AuthorityDevice> },
    Changed { changed: bool },
    Revocations { devices: Vec<GrantRevocation> },
    Routing { epoch: u64, expires_at_ms: u64, ticket: String, owner_id: String },
    Relays { configuration: Arc<CommittedCoreRelays> },
}
impl From<Value> for AuthorizationValue {
    fn from(value: Value) -> Self { match value {
        Value::Unit => Self::Unit,
        Value::Peer { grant, generation, core_device_id } => Self::Peer { grant: grant.into(), generation, core_device_id },
        Value::Identity { grant } => Self::Identity { grant: grant.map(Into::into) },
        Value::Epoch { epoch } => Self::Epoch { epoch },
        Value::AuthorityDevice { device_id } => Self::AuthorityDevice { device_id },
        Value::AuthorityDevices { devices } => Self::AuthorityDevices { devices: devices.into_iter()
            .map(|(device_id, endpoint_id)| AuthorityDevice { device_id, endpoint_id }).collect() },
        Value::Changed { changed } => Self::Changed { changed },
        Value::Revocations { devices } => Self::Revocations { devices: devices.into_iter()
            .map(|(device_id, authorization_epoch)| GrantRevocation { device_id, authorization_epoch }).collect() },
        Value::Routing { epoch, expires_at_ms, ticket, owner_id } => Self::Routing { epoch, expires_at_ms, ticket, owner_id },
        Value::Relays { configuration, endpoint_id } => Self::Relays { configuration: Arc::new(CommittedCoreRelays {
            inner: Mutex::new(Some((endpoint_id, configuration))) }) },
    } }
}

#[derive(uniffi::Object)]
pub struct CommittedCoreRelays { inner: Mutex<Option<(String, autoyou_session::core_routing::RelayConfiguration)>> }
#[uniffi::export]
impl CommittedCoreRelays {
    pub fn install(&self, endpoint: Arc<SharedEndpoint>, generation: u64) -> Result<(), BindingError> {
        let (expected, configuration) = self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.take().ok_or(BindingError::Closed)?;
        if endpoint.endpoint_info()?.endpoint_id != expected { return Err(BindingError::PermissionDenied); }
        Ok(endpoint.host.set_core_relay_credentials(configuration.relays, configuration.expires_at_ms, generation)?)
    }
}

fn with_key<T>(secret_key: Option<Vec<u8>>, operation: impl FnOnce(Option<&[u8; 32]>) -> Result<T, AuthorizationError>) -> Result<T, AuthorizationError> {
    let secret = secret_key.map(Zeroizing::new);
    let key = secret.as_ref().map(|key| key.as_slice().try_into().map_err(|_| AuthorizationError::Unavailable)).transpose()?;
    operation(key)
}
fn take(transaction: &ProtectedStateTransaction) -> Result<autoyou_session::protected_state::ProtectedStateTransaction, AuthorizationError> {
    transaction.inner.lock().map_err(|_| AuthorizationError::Unavailable)?.take().ok_or(AuthorizationError::Closed)
}

#[derive(uniffi::Object)]
pub struct PreparedAuthorizationCommit { inner: Mutex<Option<PreparedAuthorization>> }
impl PreparedAuthorizationCommit {
    fn new(value: PreparedAuthorization) -> Arc<Self> { Arc::new(Self { inner: Mutex::new(Some(value)) }) }
}
#[uniffi::export]
impl PreparedAuthorizationCommit {
    pub fn commit(&self, secret_key: Vec<u8>) -> Result<AuthorizationValue, BindingError> {
        let key = Zeroizing::new(secret_key);
        let transaction = self.inner.lock().map_err(|_| BindingError::StorageUnavailable)?.take().ok_or(BindingError::Closed)?;
        let key = key.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
        Ok(transaction.commit(key)?.into())
    }
    pub fn abort(&self) { if let Ok(mut inner) = self.inner.lock() { inner.take(); } }
}

#[uniffi::export]
impl ProtectedStateTransaction {
    pub fn query_authorization(&self, secret_key: Option<Vec<u8>>, command_json: Vec<u8>, now_ms: u64) -> Result<AuthorizationValue, AuthorizationError> {
        let transaction = take(self)?;
        with_key(secret_key, |key| Ok(authorization::query(transaction, key, &command_json, now_ms)?.into()))
    }
    pub fn prepare_authorization(&self, secret_key: Option<Vec<u8>>, command_json: Vec<u8>, now_ms: u64) -> Result<Arc<PreparedAuthorizationCommit>, AuthorizationError> {
        let transaction = take(self)?;
        with_key(secret_key, |key| Ok(PreparedAuthorizationCommit::new(authorization::prepare(transaction, key, &command_json, now_ms)?)))
    }
    pub fn read_client_snapshot(&self, secret_key: Option<Vec<u8>>) -> Result<Vec<u8>, AuthorizationError> {
        let mut transaction = take(self)?;
        with_key(secret_key, |key| {
            let bytes = if transaction.has_state() {
                transaction.read(key.ok_or(AuthorizationError::Unavailable)?).map_err(PolicyError::from)?
            } else { autoyou_session::client_store::empty() };
            Ok(bytes)
        })
    }
    pub fn prepare_client_compare(&self, secret_key: Option<Vec<u8>>, expected: Vec<u8>, updated: Vec<u8>, endpoint_id: Option<String>, core_device_id: Option<String>) -> Result<Arc<PreparedAuthorizationCommit>, AuthorizationError> {
        let transaction = take(self)?;
        with_key(secret_key, |key| Ok(PreparedAuthorizationCommit::new(authorization::prepare_client_compare(transaction, key, &expected, updated,
            endpoint_id.as_deref(), core_device_id.as_deref())?)))
    }
}
#[uniffi::export]
pub fn validate_endpoint_grant(grant_json: Vec<u8>) -> Result<(), AuthorizationError> {
    Ok(authorization::validate_grant(&grant_json)?)
}

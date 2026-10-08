// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Typed generated host boundary. Foreign runtimes never supply callbacks to a
//! QUIC worker or run Tokio futures on a UI/Python event loop.

use autoyou_protocol::{Envelope, Frame, Lane, Principal, SESSION_ALPN, ROOM_SESSION_ALPN, transcript_binding};
use autoyou_session::scheduler::Scheduler;
use autoyou_session::host::{EndpointHost as RustEndpointHost, EndpointPolicy, HostError, HostEvent};
use std::sync::{Arc, Mutex};
use autoyou_session::client::{ClientSession as RustClientSession, ClientContext, ClientError, Phase};

mod byte_stream;
pub use byte_stream::*;
mod file_store;
pub use file_store::*;
mod delivery_store;
pub use delivery_store::*;
mod media;
pub use media::*;
mod audio_dsp;
pub use audio_dsp::*;
mod audio_opus;
pub use audio_opus::*;

uniffi::setup_scaffolding!();

#[derive(Debug, Clone, uniffi::Enum)]
pub enum ClientPhase { Idle, Enrolling, Connecting, Authorizing, Online, ChangingPath,
    Recovering, Suspended, Revoked, NeedsPairing, UserDisconnected, Failed }

#[derive(Debug, Clone, uniffi::Record)]
pub struct ClientSnapshot {
    pub operation: u64, pub phase: ClientPhase, pub generation: u64, pub authorization_epoch: u64,
    pub retry_attempt: u32, pub next_retry_ms: Option<u64>,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct ClientConnectionContext {
    pub connection_id: u64, pub remote_endpoint: String, pub local_endpoint: String,
    pub exporter: Vec<u8>, pub protocol: String, pub initiator: bool,
}
impl ClientConnectionContext {
    fn core(self) -> Result<ClientContext, BindingError> {
        Ok(ClientContext { connection_id: self.connection_id, remote_endpoint: self.remote_endpoint,
            local_endpoint: self.local_endpoint, exporter: self.exporter.try_into().map_err(|_| BindingError::InvalidInput)?,
            protocol: self.protocol, initiator: self.initiator })
    }
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct ClientAction {
    pub outgoing: Option<Vec<u8>>, pub grant_json: Option<String>, pub generation: u64,
    pub capabilities_json: Option<String>, pub ready: bool, pub close_connection: bool,
    pub session_grant: Option<SessionGrant>,
    pub outgoing_frame: Option<TransportFrame>,
}
impl From<ClientError> for BindingError {
    fn from(error: ClientError) -> Self { match error {
        ClientError::Invalid => Self::InvalidInput, ClientError::Closed => Self::Closed,
        ClientError::Denied | ClientError::Stale => Self::PermissionDenied,
    } }
}
fn client_action(step: autoyou_session::client::Step) -> Result<ClientAction, BindingError> {
    let session_grant = step.grant.as_ref().map(|grant| session_grant(grant.principal(step.generation)));
    let outgoing_frame = step.outgoing.as_ref().map(|payload| TransportFrame { lane: Lane::Enrollment as u8,
        generation: 0, stream_id: 0, sequence: 0, payload: payload.clone() });
    Ok(ClientAction { outgoing: step.outgoing, grant_json: step.grant.map(|grant| serde_json::to_string(&grant))
        .transpose().map_err(|_| BindingError::InvalidInput)?, generation: step.generation,
        capabilities_json: step.capabilities_json, ready: step.ready, close_connection: step.close_connection, session_grant, outgoing_frame })
}

fn session_grant(grant: Principal) -> SessionGrant {
    SessionGrant { endpoint_id: grant.endpoint_id, device_id: grant.device_id, owner_id: grant.owner_id,
        conversation_id: grant.conversation_id, generation: grant.generation, authorization_epoch: grant.authorization_epoch,
        expires_at_ms: grant.expires_at_ms, scopes: grant.scopes }
}

#[derive(Clone, uniffi::Record)]
pub struct StoredClientPeer {
    pub grant_json: String, pub generation_floor: u64, pub canonical_user_id: String,
    pub device_ownership: String, pub session_grant: SessionGrant,
}

#[derive(Clone, uniffi::Record)]
pub struct ClientDialTarget { pub endpoint_id: String, pub ticket: String, pub pairing: bool }

fn grant_from_json(json: &str) -> Result<autoyou_session::client::ClientGrant, BindingError> {
    if json.len() > 16*1024 { return Err(BindingError::InvalidInput); }
    serde_json::from_str(json).map_err(|_| BindingError::InvalidInput)
}

#[uniffi::export]
pub fn empty_client_store() -> Vec<u8> { autoyou_session::client_store::empty() }

#[uniffi::export]
pub fn register_client_grant(state: Vec<u8>, grant_json: String, now_ms: u64) -> Result<Vec<u8>, BindingError> {
    Ok(autoyou_session::client_store::register(&state, grant_from_json(&grant_json)?, now_ms)?)
}

#[uniffi::export]
pub fn load_client_peer(state: Vec<u8>, endpoint_id: String, now_ms: u64) -> Result<StoredClientPeer, BindingError> {
    let peer = autoyou_session::client_store::load(&state, &endpoint_id, now_ms)?;
    Ok(StoredClientPeer { grant_json: serde_json::to_string(&peer.grant).map_err(|_| BindingError::InvalidInput)?,
        generation_floor: peer.generation_floor, canonical_user_id: peer.grant.canonical_user_id.clone(),
        device_ownership: peer.grant.device_ownership.clone(), session_grant: session_grant(peer.grant.principal(peer.generation_floor)) })
}

#[uniffi::export]
pub fn admit_client_generation(state: Vec<u8>, grant_json: String, generation: u64, now_ms: u64) -> Result<Vec<u8>, BindingError> {
    Ok(autoyou_session::client_store::admit(&state, &grant_from_json(&grant_json)?, generation, now_ms)?)
}

#[uniffi::export]
pub fn revoke_client_grant(state: Vec<u8>, device_id: String, authorization_epoch: u64) -> Result<Vec<u8>, BindingError> {
    Ok(autoyou_session::client_store::revoke(&state, &device_id, authorization_epoch)?)
}

/// One shared protocol/lifecycle owner; the enclosing adapter owns protected
/// persistence and pumps this object with native endpoint events.
#[derive(uniffi::Object)]
pub struct ClientSession { session: Mutex<RustClientSession> }

#[derive(Clone, uniffi::Record)]
pub struct AcceptorAction {
    pub application: ClientAction,
    pub protected_store: Option<Vec<u8>>,
}
fn acceptor_action(step: autoyou_session::acceptor::AcceptorStep) -> Result<AcceptorAction, BindingError> {
    Ok(AcceptorAction { application: client_action(step.application)?, protected_store: step.store })
}

#[derive(uniffi::Object)]
pub struct AcceptorSession { session: Mutex<autoyou_session::acceptor::AcceptorSession> }

#[uniffi::export]
impl AcceptorSession {
    #[uniffi::constructor]
    pub fn new(local_endpoint: String, capabilities_json: String) -> Result<Arc<Self>, BindingError> {
        if capabilities_json.len() > 8192 { return Err(BindingError::InvalidInput); }
        let capabilities = serde_json::from_str(&capabilities_json).map_err(|_| BindingError::InvalidInput)?;
        Ok(Arc::new(Self { session: Mutex::new(autoyou_session::acceptor::AcceptorSession::new(local_endpoint, capabilities)?) }))
    }
    pub fn issue_after_verified_proof(&self, grant_json: String, ticket: String, now_ms: u64) -> Result<String, BindingError> {
        if grant_json.len() > 32*1024 { return Err(BindingError::InvalidInput); }
        let grant = serde_json::from_str(&grant_json).map_err(|_| BindingError::InvalidInput)?;
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.issue(grant, ticket, now_ms)?)
    }
    pub fn connected(&self, context: ClientConnectionContext, protected_store: Vec<u8>, now_ms: u64) -> Result<AcceptorAction, BindingError> {
        acceptor_action(self.session.lock().map_err(|_| BindingError::Closed)?.connected(context.core()?, &protected_store, now_ms)?)
    }
    pub fn receive(&self, context: ClientConnectionContext, payload: Vec<u8>, protected_store: Vec<u8>, now_ms: u64) -> Result<AcceptorAction, BindingError> {
        acceptor_action(self.session.lock().map_err(|_| BindingError::Closed)?.receive(&context.core()?, &payload, &protected_store, now_ms)?)
    }
    pub fn persisted(&self, context: ClientConnectionContext, protected_store: Vec<u8>, now_ms: u64) -> Result<ClientAction, BindingError> {
        client_action(self.session.lock().map_err(|_| BindingError::Closed)?.persisted(&context.core()?, &protected_store, now_ms)?)
    }
    pub fn expire(&self, now_ms: u64) -> Result<Vec<u64>, BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.expire(now_ms))
    }
    pub fn closed(&self, connection_id: u64) -> Result<(), BindingError> {
        self.session.lock().map_err(|_| BindingError::Closed)?.closed(connection_id); Ok(())
    }
    pub fn cancel_endpoint(&self, endpoint_id: String) -> Result<Vec<u64>, BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.cancel_endpoint(&endpoint_id))
    }
}

#[uniffi::export]
impl ClientSession {
    #[uniffi::constructor]
    pub fn new(local_endpoint: String) -> Result<Arc<Self>, BindingError> {
        Ok(Arc::new(Self { session: Mutex::new(RustClientSession::new(local_endpoint)?) }))
    }
    pub fn snapshot(&self) -> Result<ClientSnapshot, BindingError> {
        let state = self.session.lock().map_err(|_| BindingError::Closed)?.snapshot();
        Ok(ClientSnapshot { operation: state.operation, generation: state.generation,
            authorization_epoch: state.authorization_epoch, retry_attempt: state.retry_attempt,
            next_retry_ms: state.next_retry_ms, phase: match state.phase {
                Phase::Idle => ClientPhase::Idle, Phase::Enrolling => ClientPhase::Enrolling,
                Phase::Connecting => ClientPhase::Connecting, Phase::Authorizing => ClientPhase::Authorizing,
                Phase::Online => ClientPhase::Online, Phase::ChangingPath => ClientPhase::ChangingPath,
                Phase::Recovering => ClientPhase::Recovering, Phase::Suspended => ClientPhase::Suspended,
                Phase::Revoked => ClientPhase::Revoked, Phase::NeedsPairing => ClientPhase::NeedsPairing,
                Phase::UserDisconnected => ClientPhase::UserDisconnected, Phase::Failed => ClientPhase::Failed,
            } })
    }
    pub fn begin_verified_pairing(&self, answer_json: String, now_ms: u64) -> Result<u64, BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.begin_verified_pairing(answer_json.as_bytes(),now_ms)?)
    }
    pub fn begin_session(&self, grant_json: String, ticket: String, generation_floor: u64, now_ms: u64) -> Result<u64, BindingError> {
        if grant_json.len() > 16*1024 { return Err(BindingError::InvalidInput); }
        let grant = serde_json::from_str(&grant_json).map_err(|_| BindingError::InvalidInput)?;
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.begin_session(grant,ticket,generation_floor,now_ms)?)
    }
    pub fn dial_target(&self) -> Result<ClientDialTarget, BindingError> {
        let session = self.session.lock().map_err(|_| BindingError::Closed)?;
        let phase = session.snapshot().phase;
        if !matches!(phase, Phase::Enrolling | Phase::Connecting) { return Err(BindingError::PermissionDenied); }
        Ok(ClientDialTarget { endpoint_id: session.peer().ok_or(BindingError::PermissionDenied)?.endpoint_id.clone(),
            ticket: session.ticket().ok_or(BindingError::PermissionDenied)?.into(), pairing: phase == Phase::Enrolling })
    }
    pub fn dial_protocol(&self) -> Result<String, BindingError> {
        let session = self.session.lock().map_err(|_| BindingError::Closed)?;
        if !matches!(session.snapshot().phase, Phase::Enrolling | Phase::Connecting) { return Err(BindingError::PermissionDenied); }
        Ok(String::from_utf8(session.dial_protocol()?.to_vec()).map_err(|_| BindingError::InvalidInput)?)
    }
    pub fn bind_dial(&self, operation: u64, connection_id: u64) -> Result<(), BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.bind_dial(operation,connection_id)?)
    }
    pub fn connected(&self, operation: u64, context: ClientConnectionContext, now_ms: u64) -> Result<ClientAction, BindingError> {
        client_action(self.session.lock().map_err(|_| BindingError::Closed)?.connected(operation,&context.core()?,now_ms)?)
    }
    pub fn receive(&self, operation: u64, context: ClientConnectionContext, payload: Vec<u8>, now_ms: u64) -> Result<ClientAction, BindingError> {
        client_action(self.session.lock().map_err(|_| BindingError::Closed)?.receive(operation,&context.core()?,&payload,now_ms)?)
    }
    pub fn pairing_persisted(&self, operation: u64) -> Result<(), BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.pairing_persisted(operation)?)
    }
    pub fn network_changed(&self, operation: u64, usable_path: bool) -> Result<(), BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.network_changed(operation,usable_path)?)
    }
    pub fn lost(&self, operation: u64, now_ms: u64, jitter_ms: u64) -> Result<(), BindingError> {
        Ok(self.session.lock().map_err(|_| BindingError::Closed)?.lost(operation,now_ms,jitter_ms)?)
    }
    pub fn suspend(&self) -> Result<(), BindingError> { self.session.lock().map_err(|_| BindingError::Closed)?.suspend(); Ok(()) }
    pub fn disconnect(&self) -> Result<(), BindingError> { self.session.lock().map_err(|_| BindingError::Closed)?.disconnect(); Ok(()) }
    pub fn revoke(&self) -> Result<(), BindingError> { self.session.lock().map_err(|_| BindingError::Closed)?.revoke(); Ok(()) }
    pub fn deny(&self) -> Result<(), BindingError> { self.session.lock().map_err(|_| BindingError::Closed)?.deny(); Ok(()) }
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct CoreInfo {
    pub api_version: u32, pub wire_version: u32, pub core_version: String,
    pub iroh_version: String, pub noq_version: String, pub uniffi_version: String,
    pub lock_sha256: String,
}

#[uniffi::export]
pub fn core_info() -> CoreInfo {
    use sha2::{Digest, Sha256};
    let hash = Sha256::digest(include_bytes!("../../Cargo.lock"));
    CoreInfo { api_version: 1, wire_version: 1, core_version: env!("CARGO_PKG_VERSION").into(),
        iroh_version: "1.3.0".into(), noq_version: "1.3.0".into(), uniffi_version: "0.32.2".into(),
        lock_sha256: hash.iter().map(|byte| format!("{byte:02x}")).collect() }
}

#[uniffi::export]
pub fn validate_envelope(payload: Vec<u8>) -> Result<Vec<u8>, BindingError> {
    Envelope::from_slice(&payload).and_then(|value| value.to_vec()).map_err(|_| BindingError::InvalidInput)
}

#[uniffi::export]
pub fn application_lane(payload: Vec<u8>) -> Result<u8, BindingError> {
    if payload.len() > autoyou_protocol::MAX_CONTROL_BYTES { return Ok(prepare_browser_message(payload)?.lane); }
    Ok(Envelope::from_slice(&payload).map_err(|_| BindingError::InvalidInput)?.lane() as u8)
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct AdmissionContext {
    pub exporter: Vec<u8>, pub initiator_endpoint: String, pub acceptor_endpoint: String,
    pub challenge: Vec<u8>, pub protocol: String, pub capabilities_json: String,
    pub generation: u64, pub authorization_epoch: u64,
}

#[uniffi::export]
pub fn admission_binding(context: AdmissionContext) -> Result<Vec<u8>, BindingError> {
    if context.exporter.len() != 32 || context.challenge.len() != 32 || context.generation == 0 ||
        ![SESSION_ALPN, ROOM_SESSION_ALPN].contains(&context.protocol.as_bytes()) || context.capabilities_json.len() > 8192 {
        return Err(BindingError::InvalidInput);
    }
    let capabilities: serde_json::Value = serde_json::from_str(&context.capabilities_json).map_err(|_| BindingError::InvalidInput)?;
    if !capabilities.is_object() { return Err(BindingError::InvalidInput); }
    let capabilities = serde_json::to_vec(&capabilities).map_err(|_| BindingError::InvalidInput)?;
    let exporter = context.exporter.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
    let challenge = context.challenge.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
    let initiator = autoyou_session::host::endpoint_bytes(&context.initiator_endpoint)?;
    let acceptor = autoyou_session::host::endpoint_bytes(&context.acceptor_endpoint)?;
    let mut protocol = context.protocol.into_bytes();
    protocol.extend_from_slice(&context.generation.to_be_bytes());
    protocol.extend_from_slice(&context.authorization_epoch.to_be_bytes());
    Ok(transcript_binding(exporter, &initiator, &acceptor, challenge, &protocol, &capabilities).to_vec())
}

#[derive(Debug, Clone, uniffi::Enum)]
pub enum EnrollmentKind { Redeem, Challenge, Confirm, Ready }
#[derive(Debug, Clone, uniffi::Record)]
pub struct EnrollmentChallenge {
    pub initiator_endpoint: String, pub acceptor_endpoint: String, pub nonce: Vec<u8>,
    pub generation: u64, pub authorization_epoch: u64, pub expires_at_ms: u64,
    pub scopes: Vec<String>, pub capabilities_json: String,
}
impl EnrollmentChallenge {
    fn core(&self) -> Result<autoyou_protocol::enrollment::Challenge, BindingError> {
        if self.capabilities_json.len() > 8192 { return Err(BindingError::InvalidInput); }
        Ok(autoyou_protocol::enrollment::Challenge { initiator_endpoint: self.initiator_endpoint.clone(),
            acceptor_endpoint: self.acceptor_endpoint.clone(), nonce: self.nonce.clone(), generation: self.generation,
            authorization_epoch: self.authorization_epoch, expires_at_ms: self.expires_at_ms,
            scopes: self.scopes.clone(), capabilities: serde_json::from_str(&self.capabilities_json).map_err(|_| BindingError::InvalidInput)? })
    }
}
#[derive(Debug, Clone, uniffi::Record)]
pub struct EnrollmentMessage {
    pub kind: EnrollmentKind, pub challenge: Option<EnrollmentChallenge>, pub binding: Option<Vec<u8>>,
}
#[uniffi::export]
pub fn encode_enrollment(message: EnrollmentMessage) -> Result<Vec<u8>, BindingError> {
    use autoyou_protocol::enrollment::{Kind, Message};
    Message { version: 1, kind: match message.kind { EnrollmentKind::Redeem => Kind::Redeem, EnrollmentKind::Challenge => Kind::Challenge,
        EnrollmentKind::Confirm => Kind::Confirm, EnrollmentKind::Ready => Kind::Ready },
        challenge: message.challenge.map(|value| value.core()).transpose()?, binding: message.binding }
        .to_vec().map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn decode_enrollment(payload: Vec<u8>) -> Result<EnrollmentMessage, BindingError> {
    use autoyou_protocol::enrollment::{Kind, Message};
    let value = Message::from_slice(&payload).map_err(|_| BindingError::InvalidInput)?;
    Ok(EnrollmentMessage { kind: match value.kind { Kind::Redeem => EnrollmentKind::Redeem, Kind::Challenge => EnrollmentKind::Challenge,
        Kind::Confirm => EnrollmentKind::Confirm, Kind::Ready => EnrollmentKind::Ready }, binding: value.binding,
        challenge: value.challenge.map(|challenge| EnrollmentChallenge { initiator_endpoint: challenge.initiator_endpoint,
            acceptor_endpoint: challenge.acceptor_endpoint, nonce: challenge.nonce, generation: challenge.generation,
            authorization_epoch: challenge.authorization_epoch, expires_at_ms: challenge.expires_at_ms,
            scopes: challenge.scopes, capabilities_json: serde_json::to_string(&challenge.capabilities).unwrap() }) })
}
#[uniffi::export]
pub fn enrollment_binding(challenge: EnrollmentChallenge, exporter: Vec<u8>, initiator_endpoint: String,
    acceptor_endpoint: String) -> Result<Vec<u8>, BindingError> {
    if exporter.len() != 32 || challenge.initiator_endpoint != initiator_endpoint || challenge.acceptor_endpoint != acceptor_endpoint {
        return Err(BindingError::PermissionDenied);
    }
    let initiator = autoyou_session::host::endpoint_bytes(&initiator_endpoint)?;
    let acceptor = autoyou_session::host::endpoint_bytes(&acceptor_endpoint)?;
    let exporter = exporter.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
    Ok(challenge.core()?.binding(exporter, &initiator, &acceptor).map_err(|_| BindingError::InvalidInput)?.to_vec())
}

#[uniffi::export]
pub fn pairing_binding(challenge: EnrollmentChallenge, exporter: Vec<u8>, initiator_endpoint: String,
    acceptor_endpoint: String) -> Result<Vec<u8>, BindingError> {
    if exporter.len() != 32 || challenge.initiator_endpoint != initiator_endpoint || challenge.acceptor_endpoint != acceptor_endpoint {
        return Err(BindingError::PermissionDenied);
    }
    let initiator = autoyou_session::host::endpoint_bytes(&initiator_endpoint)?;
    let acceptor = autoyou_session::host::endpoint_bytes(&acceptor_endpoint)?;
    let exporter = exporter.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
    Ok(challenge.core()?.pairing_binding(exporter, &initiator, &acceptor).map_err(|_| BindingError::InvalidInput)?.to_vec())
}

#[uniffi::export]
pub fn validate_endpoint_id(endpoint_id: String) -> Result<(), BindingError> {
    autoyou_session::host::endpoint_bytes(&endpoint_id)?;
    Ok(())
}

#[uniffi::export]
pub fn peer_approval_grant(protected_store: Vec<u8>, remote_endpoint: String, origin_sender: String,
                          scopes: Vec<String>, expires_at_ms: u64, now_ms: u64) -> Result<String, BindingError> {
    let grant = autoyou_session::peer::approval_grant(&protected_store, &remote_endpoint, origin_sender, scopes, expires_at_ms, now_ms)?;
    serde_json::to_string(&grant).map_err(|_| BindingError::InvalidInput)
}

#[uniffi::export]
pub fn revoke_client_peer(protected_store: Vec<u8>, remote_endpoint: String, authorization_epoch: u64) -> Result<Vec<u8>, BindingError> {
    Ok(autoyou_session::client_store::revoke_endpoint(&protected_store, &remote_endpoint, authorization_epoch)?)
}

#[uniffi::export]
pub fn validate_peer_descriptor(descriptor_json: String, kind: String) -> Result<String, BindingError> {
    Ok(autoyou_session::peer::descriptor(&descriptor_json, &kind)?)
}

#[uniffi::export]
pub fn peer_endpoint_fingerprint(endpoint_id: String) -> Result<String, BindingError> {
    Ok(autoyou_session::peer::fingerprint(&endpoint_id)?)
}

#[uniffi::export]
pub fn validate_peer_pairing(descriptor_json: String, proof_json: String, now_ms: u64) -> Result<String, BindingError> {
    Ok(autoyou_session::peer::pairing(&descriptor_json, &proof_json, now_ms)?)
}

#[uniffi::export]
pub fn endpoint_id_from_key(mut secret_key: Vec<u8>) -> Result<String, BindingError> {
    if secret_key.len()!=32 { secret_key.fill(0); return Err(BindingError::InvalidInput); }
    let key: [u8;32]=secret_key.as_slice().try_into().map_err(|_|BindingError::InvalidInput)?;
    secret_key.fill(0); Ok(autoyou_session::host::endpoint_id_from_key(key))
}

#[derive(Debug, thiserror::Error, uniffi::Error)]
pub enum BindingError {
    #[error("invalid transport input")] InvalidInput,
    #[error("bounded transport queue is full")] Backpressure,
    #[error("transport is closed")] Closed,
    #[error("transport worker failed")] Worker,
    #[error("endpoint ticket is invalid or unapproved")] InvalidTicket,
    #[error("transport session permission denied")] PermissionDenied,
    #[error("unknown transport connection")] UnknownConnection,
    #[error("transport operation timed out")] Timeout,
    #[error("protected file storage is unavailable")] StorageUnavailable,
    #[error("protected file storage capacity is exhausted")] StorageCapacity,
    #[error("file operation was deleted or expired")] FileDeleted,
    #[error("file digest did not match")] DigestMismatch,
}

impl From<HostError> for BindingError {
    fn from(error: HostError) -> Self {
        match error {
            HostError::InvalidConfig => Self::InvalidInput,
            HostError::InvalidTicket => Self::InvalidTicket,
            HostError::Backpressure => Self::Backpressure,
            HostError::Closed => Self::Closed,
            HostError::UnknownConnection => Self::UnknownConnection,
            HostError::NotAuthorized => Self::PermissionDenied,
            HostError::Worker => Self::Worker,
            HostError::Timeout => Self::Timeout,
        }
    }
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct TransportFrame {
    pub lane: u8,
    pub generation: u64,
    pub stream_id: u64,
    pub sequence: u64,
    pub payload: Vec<u8>,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct EndpointInfo { pub endpoint_id: String, pub ticket: String }

#[derive(Debug, Clone, uniffi::Record)]
pub struct TransportDiagnostics {
    pub generation: u64, pub authorization_epoch: u64,
    pub path_kind: String, pub open_paths: u32, pub rtt_ms: Option<u64>,
    pub queued_send_bytes: u64, pub held_send_bytes: u64, pub held_receive_bytes: u64,
    pub active_logical_streams: u32, pub pending_stream_receipts: u32,
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct SessionGrant {
    pub endpoint_id: String,
    pub device_id: String,
    pub owner_id: String,
    pub conversation_id: String,
    pub generation: u64,
    pub authorization_epoch: u64,
    pub expires_at_ms: u64,
    pub scopes: Vec<String>,
}

#[derive(Debug, Clone, uniffi::Enum)]
pub enum TransportEventKind { Connected, Frame, Closed, Failed }

#[derive(Debug, Clone, uniffi::Record)]
pub struct TransportEvent {
    pub kind: TransportEventKind,
    pub connection_id: u64,
    pub endpoint_id: Option<String>,
    pub initiator: bool,
    pub exporter: Vec<u8>,
    pub frame: Option<TransportFrame>,
    /// Trusted local timing for media/live input; never wire authority.
    pub media_age_us: Option<u64>,
    pub media_ttl_us: Option<u64>,
    pub error_code: Option<String>,
    pub protocol: Option<String>,
}

#[derive(uniffi::Object)]
pub struct SharedEndpoint { host: RustEndpointHost }

#[uniffi::export]
impl SharedEndpoint {
    #[uniffi::constructor]
    pub fn new(policy_json: String, mut secret_key: Vec<u8>) -> Result<Arc<Self>, BindingError> {
        if policy_json.len() > 64*1024 || secret_key.len() != 32 {
            secret_key.fill(0); return Err(BindingError::InvalidInput);
        }
        let key: [u8;32] = secret_key.as_slice().try_into().map_err(|_| BindingError::InvalidInput)?;
        secret_key.fill(0);
        let policy: EndpointPolicy = serde_json::from_str(&policy_json).map_err(|_| BindingError::InvalidInput)?;
        Ok(Arc::new(Self { host: RustEndpointHost::start(policy, key)? }))
    }
    pub fn endpoint_info(&self) -> Result<EndpointInfo, BindingError> {
        let (endpoint_id, ticket) = self.host.endpoint_info()?;
        Ok(EndpointInfo { endpoint_id, ticket })
    }
    pub fn diagnostics(&self, connection_id: u64) -> Result<TransportDiagnostics, BindingError> {
        let value = self.host.diagnostics(connection_id)?;
        Ok(TransportDiagnostics { generation: value.generation, authorization_epoch: value.authorization_epoch,
            path_kind: value.path_kind.into(), open_paths: value.open_paths, rtt_ms: value.rtt_ms,
            queued_send_bytes: value.queued_send_bytes, held_send_bytes: value.held_send_bytes,
            held_receive_bytes: value.held_receive_bytes, active_logical_streams: value.active_logical_streams,
            pending_stream_receipts: value.pending_stream_receipts })
    }
    pub fn dial(&self, ticket: String, expected_endpoint: String, pairing: bool) -> Result<u64, BindingError> {
        Ok(self.host.dial(&ticket, &expected_endpoint, pairing)?)
    }
    pub fn dial_application(&self, ticket: String, expected_endpoint: String, protocol: String) -> Result<u64, BindingError> {
        Ok(self.host.dial_application(&ticket,&expected_endpoint,protocol.as_bytes())?)
    }
    pub fn admit(&self, connection_id: u64, grant: SessionGrant) -> Result<(), BindingError> {
        self.host.admit(connection_id, Principal { endpoint_id: grant.endpoint_id,
            device_id: grant.device_id, owner_id: grant.owner_id, conversation_id: grant.conversation_id,
            generation: grant.generation, authorization_epoch: grant.authorization_epoch,
            expires_at_ms: grant.expires_at_ms, scopes: grant.scopes })?;
        Ok(())
    }
    pub fn send(&self, connection_id: u64, frame: TransportFrame, deadline_ms: Option<u64>) -> Result<(), BindingError> {
        let lane = Lane::try_from(frame.lane).map_err(|_| BindingError::InvalidInput)?;
        self.host.send(connection_id, Frame { lane, generation: frame.generation,
            stream_id: frame.stream_id, sequence: 0, payload: frame.payload }, deadline_ms)?;
        Ok(())
    }
    pub fn send_browser_message(&self, connection_id: u64, generation: u64, payload: Vec<u8>) -> Result<u64, BindingError> {
        let message = prepare_browser_message(payload)?;
        let (lane, records) = byte_stream::message_records(message)?;
        Ok(self.host.send_browser_records(connection_id, lane, generation, records)?)
    }
    pub fn send_browser_parts(&self, connection_id: u64, generation: u64, message: BrowserMessage) -> Result<u64, BindingError> {
        let (lane, records) = byte_stream::message_records(message)?;
        Ok(self.host.send_browser_records(connection_id, lane, generation, records)?)
    }
    pub fn allocate_byte_stream(&self, connection_id: u64, lane: u8) -> Result<u64, BindingError> {
        Ok(self.host.allocate_stream(connection_id, Lane::try_from(lane).map_err(|_| BindingError::InvalidInput)?)?)
    }
    pub fn acknowledge_byte_stream(&self, connection_id: u64, lane: u8, stream_id: u64, total: u64, digest: Vec<u8>) -> Result<(), BindingError> {
        Ok(self.host.acknowledge_stream(connection_id, lane, stream_id, total, digest)?)
    }
    pub fn acknowledge_byte_progress(&self,connection_id:u64,lane:u8,stream_id:u64,offset:u64)->Result<(),BindingError> {
        Ok(self.host.acknowledge_progress(connection_id,lane,stream_id,offset)?)
    }
    pub fn activate(&self, connection_id: u64) -> Result<(), BindingError> {
        Ok(self.host.activate(connection_id)?)
    }
    /// Trusted local call/consent owners supply a bounded source lease after
    /// application admission. Peer media bytes cannot call this method.
    pub fn approve_media_source(&self, connection_id: u64, lease_json: String, inbound: bool) -> Result<(), BindingError> {
        if lease_json.len() > 4096 { return Err(BindingError::InvalidInput); }
        let lease = serde_json::from_str(&lease_json).map_err(|_| BindingError::InvalidInput)?;
        Ok(self.host.approve_media_source(connection_id, lease, inbound)?)
    }
    pub fn revoke_media_source(&self, connection_id: u64, source_id: u64, inbound: bool) -> Result<(), BindingError> {
        Ok(self.host.revoke_media_source(connection_id, source_id, inbound)?)
    }
    pub fn poll(&self, maximum: u32) -> Result<Vec<TransportEvent>, BindingError> {
        Ok(self.host.poll(maximum)?.into_iter().map(|event| match event {
            HostEvent::Connected { connection_id, endpoint_id, initiator, exporter, protocol } => TransportEvent {
                kind: TransportEventKind::Connected, connection_id, endpoint_id: Some(endpoint_id),
                initiator, exporter: exporter.to_vec(), frame: None, media_age_us: None, media_ttl_us: None,
                error_code: None, protocol: Some(protocol) },
            HostEvent::Frame { connection_id, frame, received_at, expires_at, .. } => {
                let (media_age_us, media_ttl_us) = received_at.zip(expires_at).map(|(received,expires)| {
                    let age = received.elapsed();
                    let ttl = expires.saturating_duration_since(received).saturating_sub(age);
                    (Some(age.as_micros().min(u128::from(u64::MAX)) as u64),
                        Some(ttl.as_micros().min(u128::from(u64::MAX)) as u64))
                }).unwrap_or((None,None));
                TransportEvent { kind: TransportEventKind::Frame, connection_id, endpoint_id: None, initiator: false,
                    exporter: vec![], frame: Some(TransportFrame { lane: frame.lane as u8,
                        generation: frame.generation, stream_id: frame.stream_id, sequence: frame.sequence,
                        payload: frame.payload }),
                    media_age_us, media_ttl_us,
                    error_code: None, protocol: None }
            },
            HostEvent::Closed { connection_id } => TransportEvent { kind: TransportEventKind::Closed,
                connection_id, endpoint_id: None, initiator: false, exporter: vec![], frame: None,
                media_age_us: None, media_ttl_us: None, error_code: None, protocol: None },
            HostEvent::Failed { request_id, code } => TransportEvent { kind: TransportEventKind::Failed,
                connection_id: request_id, endpoint_id: None, initiator: false, exporter: vec![],
                frame: None, media_age_us: None, media_ttl_us: None, error_code: Some(code.into()), protocol: None },
        }).collect())
    }
    pub fn disconnect(&self, connection_id: u64) -> Result<(), BindingError> {
        Ok(self.host.disconnect(connection_id)?)
    }
    pub fn network_changed(&self) -> Result<(), BindingError> { Ok(self.host.network_changed()?) }
    pub fn retire_stream(&self, connection_id: u64, lane: u8, stream_id: u64) -> Result<(), BindingError> {
        Ok(self.host.retire_stream(connection_id, lane, stream_id)?)
    }
    pub fn shutdown(&self) -> Result<(), BindingError> { Ok(self.host.shutdown()?) }
}

/// Qualification object used by generated-language hermetic tests. Production
/// EndpointHost is added on the same ABI after lifecycle qualification.
#[derive(uniffi::Object)]
pub struct FrameQueue { queue: Mutex<Scheduler> }

#[uniffi::export]
impl FrameQueue {
    #[uniffi::constructor]
    pub fn new() -> Arc<Self> {
        Arc::new(Self { queue: Mutex::new(Scheduler::default()) })
    }
    pub fn send(&self, frame: TransportFrame, deadline_ms: Option<u64>) -> Result<(), BindingError> {
        let lane = Lane::try_from(frame.lane).map_err(|_| BindingError::InvalidInput)?;
        self.queue.lock().map_err(|_| BindingError::Worker)?
            .push(Frame { lane, generation: frame.generation, stream_id: frame.stream_id,
                sequence: frame.sequence, payload: frame.payload }, deadline_ms)
            .map_err(|error| match error {
                autoyou_session::scheduler::QueueError::Full => BindingError::Backpressure,
                autoyou_session::scheduler::QueueError::Closed => BindingError::Closed,
                _ => BindingError::InvalidInput,
            })
    }
    pub fn poll(&self, now_ms: u64, maximum: u32) -> Result<Vec<TransportFrame>, BindingError> {
        if maximum == 0 || maximum > 64 { return Err(BindingError::InvalidInput); }
        let mut queue = self.queue.lock().map_err(|_| BindingError::Worker)?;
        let mut result = Vec::new();
        for _ in 0..maximum {
            let Some(frame) = queue.pop(now_ms) else { break; };
            result.push(TransportFrame { stream_id: frame.stream_id, sequence: frame.sequence,
                lane: frame.lane as u8, generation: frame.generation, payload: frame.payload });
        }
        Ok(result)
    }
    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.queue.lock().map_err(|_| BindingError::Worker)?.close();
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn foreign_boundary_preserves_binary_bytes_and_typed_errors() {
        let queue = FrameQueue::new();
        queue.send(TransportFrame { stream_id: 0, sequence: 0, lane: 7, generation: 19, payload: vec![0, 255, 13, 10] }, None).unwrap();
        let frames = queue.poll(0, 1).unwrap();
        assert_eq!(frames[0].generation, 19); assert_eq!(frames[0].payload, [0, 255, 13, 10]);
        assert!(matches!(queue.send(TransportFrame { stream_id: 0, sequence: 0, lane: 99, generation: 1, payload: vec![] }, None), Err(BindingError::InvalidInput)));
        queue.shutdown().unwrap();
        assert!(matches!(queue.send(TransportFrame { stream_id: 0, sequence: 0, lane: 7, generation: 1, payload: vec![] }, None), Err(BindingError::Closed)));
    }
}

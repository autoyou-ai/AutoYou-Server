// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Typed generated host boundary. Foreign runtimes never supply callbacks to a
//! QUIC worker or run Tokio futures on a UI/Python event loop.

use autoyou_protocol::{Envelope, Frame, Lane, Principal, SESSION_ALPN, transcript_binding};
use autoyou_session::scheduler::Scheduler;
use autoyou_session::host::{EndpointHost as RustEndpointHost, EndpointPolicy, HostError, HostEvent};
use std::sync::{Arc, Mutex};

uniffi::setup_scaffolding!();

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
        context.protocol.as_bytes() != SESSION_ALPN || context.capabilities_json.len() > 8192 {
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
            held_receive_bytes: value.held_receive_bytes })
    }
    pub fn dial(&self, ticket: String, expected_endpoint: String, pairing: bool) -> Result<u64, BindingError> {
        Ok(self.host.dial(&ticket, &expected_endpoint, pairing)?)
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
    pub fn activate(&self, connection_id: u64) -> Result<(), BindingError> {
        Ok(self.host.activate(connection_id)?)
    }
    pub fn poll(&self, maximum: u32) -> Result<Vec<TransportEvent>, BindingError> {
        Ok(self.host.poll(maximum)?.into_iter().map(|event| match event {
            HostEvent::Connected { connection_id, endpoint_id, initiator, exporter, protocol } => TransportEvent {
                kind: TransportEventKind::Connected, connection_id, endpoint_id: Some(endpoint_id),
                initiator, exporter: exporter.to_vec(), frame: None, error_code: None, protocol: Some(protocol) },
            HostEvent::Frame { connection_id, frame, .. } => TransportEvent {
                kind: TransportEventKind::Frame, connection_id, endpoint_id: None, initiator: false,
                exporter: vec![], frame: Some(TransportFrame { lane: frame.lane as u8,
                    generation: frame.generation, stream_id: frame.stream_id, sequence: frame.sequence,
                    payload: frame.payload }), error_code: None, protocol: None },
            HostEvent::Closed { connection_id } => TransportEvent { kind: TransportEventKind::Closed,
                connection_id, endpoint_id: None, initiator: false, exporter: vec![], frame: None, error_code: None, protocol: None },
            HostEvent::Failed { request_id, code } => TransportEvent { kind: TransportEventKind::Failed,
                connection_id: request_id, endpoint_id: None, initiator: false, exporter: vec![],
                frame: None, error_code: Some(code.into()), protocol: None },
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
    pub fn close(&self) -> Result<(), BindingError> {
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
        queue.close().unwrap();
        assert!(matches!(queue.send(TransportFrame { stream_id: 0, sequence: 0, lane: 7, generation: 1, payload: vec![] }, None), Err(BindingError::Closed)));
    }
}

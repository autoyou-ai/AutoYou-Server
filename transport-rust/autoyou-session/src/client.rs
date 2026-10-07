// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Shared client admission and lifecycle. Host adapters provide only a reviewed
//! proof result and native TLS context, and persist returned grants before use.
//! There is one protocol owner across Python, Swift and Kotlin.

use autoyou_protocol::{Principal, PAIR_ALPN, SESSION_ALPN};
use autoyou_protocol::enrollment::{Challenge, Kind, Message};
use base64::{Engine, engine::general_purpose::URL_SAFE};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use crate::host::endpoint_bytes;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ClientError {
    #[error("invalid client protocol input")] Invalid,
    #[error("client permission rejected")] Denied,
    #[error("obsolete client operation")] Stale,
    #[error("client operation is closed")] Closed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase { Idle, Enrolling, Connecting, Authorizing, Online, ChangingPath,
    Recovering, Suspended, Revoked, NeedsPairing, UserDisconnected, Failed }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ClientGrant {
    pub endpoint_id: String, pub device_id: String, pub owner_key: String,
    pub canonical_user_id: String, pub conversation_key: String,
    pub origin_transport: String, pub origin_sender_id: String, pub pairing_mode: String,
    pub device_ownership: String, pub authorization_epoch: u64, pub expires_at_ms: u64,
    pub scopes: Vec<String>,
}
impl ClientGrant {
    pub fn principal(&self, generation: u64) -> Principal {
        Principal { endpoint_id: self.endpoint_id.clone(), device_id: self.device_id.clone(),
            owner_id: self.owner_key.clone(), conversation_id: self.conversation_key.clone(),
            generation, authorization_epoch: self.authorization_epoch, expires_at_ms: self.expires_at_ms,
            scopes: self.scopes.iter().cloned().collect() }
    }
    pub fn validate(&self, now_ms: u64) -> Result<(), ClientError> {
        endpoint_bytes(&self.endpoint_id).map_err(|_| ClientError::Invalid)?;
        self.principal(1).validate().map_err(|_| ClientError::Invalid)?;
        if self.expires_at_ms <= now_ms || !matches!(self.device_ownership.as_str(), "own" | "shared") ||
            [&self.canonical_user_id, &self.origin_transport, &self.origin_sender_id, &self.pairing_mode]
                .iter().any(|s| s.is_empty() || s.len() > 512) {
            return Err(ClientError::Denied);
        }
        let mut scopes = self.scopes.clone(); scopes.sort(); scopes.dedup();
        if scopes.len() != self.scopes.len() { return Err(ClientError::Invalid); }
        Ok(())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Answer {
    version: u8, endpoint_id: String, ticket: String, redemption: String,
    redemption_expires_in_seconds: u64, grant: ClientGrant, capabilities: Map<String, Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ClientContext {
    pub connection_id: u64, pub remote_endpoint: String, pub local_endpoint: String,
    pub exporter: [u8;32], pub protocol: String, pub initiator: bool,
}

#[derive(Debug, Clone, Default)]
pub struct Step {
    pub outgoing: Option<Vec<u8>>, pub grant: Option<ClientGrant>, pub generation: u64,
    pub capabilities_json: Option<String>, pub ready: bool, pub close_connection: bool,
}

#[derive(Debug, Clone)]
pub struct Snapshot {
    pub operation: u64, pub phase: Phase, pub generation: u64, pub authorization_epoch: u64,
    pub retry_attempt: u32, pub next_retry_ms: Option<u64>,
}

#[derive(Debug)]
struct Pending {
    context: ClientContext, digest: [u8;32], generation: u64, grant: ClientGrant,
    capabilities: Map<String, Value>,
}

pub struct ClientSession {
    local_endpoint: String, operation: u64, phase: Phase, expected_id: Option<u64>,
    peer: Option<ClientGrant>, ticket: Option<String>, secret: Option<Vec<u8>>,
    approved_capabilities: Map<String, Value>, deadline_ms: u64, pending: Option<Pending>,
    connected_context: Option<ClientContext>,
    generation: u64, retry_attempt: u32, next_retry_ms: Option<u64>,
}

impl ClientSession {
    pub fn new(local_endpoint: String) -> Result<Self, ClientError> {
        endpoint_bytes(&local_endpoint).map_err(|_| ClientError::Invalid)?;
        Ok(Self { local_endpoint, operation: 0, phase: Phase::Idle, expected_id: None,
            peer: None, ticket: None, secret: None, approved_capabilities: Map::new(),
            deadline_ms: 0, pending: None, connected_context: None, generation: 0, retry_attempt: 0, next_retry_ms: None })
    }
    pub fn snapshot(&self) -> Snapshot {
        Snapshot { operation: self.operation, phase: self.phase, generation: self.generation,
            authorization_epoch: self.peer.as_ref().map_or(0, |g| g.authorization_epoch),
            retry_attempt: self.retry_attempt, next_retry_ms: self.next_retry_ms }
    }
    pub fn peer(&self) -> Option<&ClientGrant> { self.peer.as_ref() }
    pub fn ticket(&self) -> Option<&str> { self.ticket.as_deref() }
    fn start_operation(&mut self) -> Result<u64, ClientError> {
        if matches!(self.phase, Phase::Enrolling | Phase::Connecting | Phase::Authorizing | Phase::Online | Phase::ChangingPath) {
            return Err(ClientError::Denied);
        }
        self.operation = self.operation.checked_add(1).ok_or(ClientError::Closed)?;
        self.expected_id = None; self.pending = None; self.connected_context = None; self.next_retry_ms = None;
        Ok(self.operation)
    }
    pub fn begin_verified_pairing(&mut self, json: &[u8], now_ms: u64) -> Result<u64, ClientError> {
        if json.len() > 32*1024 { return Err(ClientError::Invalid); }
        let mut answer: Answer = serde_json::from_slice(json).map_err(|_| ClientError::Invalid)?;
        let max_lifetime = if answer.grant.origin_transport == "peer" {
            crate::acceptor::AcceptorSession::peer_grant(&answer.grant, now_ms)?;
            crate::peer::INVITATION_LIFETIME_SECONDS
        } else { 60 };
        if answer.version != 1 || answer.grant.endpoint_id != self.local_endpoint ||
            answer.ticket.is_empty() || answer.ticket.len() > 16*1024 ||
            answer.redemption.len() != 44 || !(1..=max_lifetime).contains(&answer.redemption_expires_in_seconds) ||
            answer.capabilities.len() > 64 || answer.capabilities.contains_key("_grant") ||
            serde_json::to_vec(&answer.capabilities).map_err(|_| ClientError::Invalid)?.len() > 8192 {
            return Err(ClientError::Invalid);
        }
        answer.grant.validate(now_ms)?;
        endpoint_bytes(&answer.endpoint_id).map_err(|_| ClientError::Invalid)?;
        let secret = URL_SAFE.decode(answer.redemption).map_err(|_| ClientError::Invalid)?;
        if secret.len() != 32 { return Err(ClientError::Invalid); }
        let operation = self.start_operation()?;
        answer.grant.endpoint_id = answer.endpoint_id;
        self.deadline_ms = now_ms.saturating_add(answer.redemption_expires_in_seconds * 1000).min(answer.grant.expires_at_ms);
        self.peer = Some(answer.grant); self.ticket = Some(answer.ticket); self.secret = Some(secret);
        self.approved_capabilities = answer.capabilities;
        self.phase = Phase::Enrolling; self.retry_attempt = 0; self.generation = 0;
        Ok(operation)
    }
    /// A grant comes from protected storage or a completed pairing transaction.
    pub fn begin_session(&mut self, grant: ClientGrant, ticket: String, generation_floor: u64, now_ms: u64) -> Result<u64, ClientError> {
        grant.validate(now_ms)?;
        if ticket.is_empty() || ticket.len() > 16*1024 ||
            self.peer.as_ref().is_some_and(|old| old.endpoint_id == grant.endpoint_id && self.generation > generation_floor) {
            return Err(ClientError::Invalid);
        }
        if matches!(self.phase, Phase::Revoked) { return Err(ClientError::Denied); }
        let operation = self.start_operation()?;
        self.peer = Some(grant); self.ticket = Some(ticket); self.secret = None;
        self.generation = generation_floor; self.deadline_ms = now_ms.saturating_add(10_000);
        self.phase = Phase::Connecting;
        Ok(operation)
    }
    pub fn bind_dial(&mut self, operation: u64, connection_id: u64) -> Result<(), ClientError> {
        if operation != self.operation || self.expected_id.is_some() ||
            !matches!(self.phase, Phase::Enrolling | Phase::Connecting) { return Err(ClientError::Stale); }
        self.expected_id = Some(connection_id); Ok(())
    }
    fn check_context(&self, operation: u64, context: &ClientContext, now_ms: u64) -> Result<(), ClientError> {
        if operation != self.operation || self.expected_id != Some(context.connection_id) ||
            !matches!(self.phase, Phase::Enrolling | Phase::Connecting | Phase::Authorizing) { return Err(ClientError::Stale); }
        if now_ms >= self.deadline_ms || !context.initiator || context.local_endpoint != self.local_endpoint ||
            self.peer.as_ref().is_none_or(|grant| grant.endpoint_id != context.remote_endpoint || grant.expires_at_ms <= now_ms) ||
            context.protocol.as_bytes() != if self.secret.is_some() { PAIR_ALPN } else { SESSION_ALPN } {
            return Err(ClientError::Denied);
        }
        Ok(())
    }
    pub fn connected(&mut self, operation: u64, context: &ClientContext, now_ms: u64) -> Result<Step, ClientError> {
        self.check_context(operation, context, now_ms)?;
        if self.connected_context.is_some() { return Err(ClientError::Denied); }
        self.connected_context = Some(context.clone());
        if let Some(secret) = &self.secret {
            Ok(Step { outgoing: Some(Message { version: 1, kind: Kind::Redeem, challenge: None, binding: Some(secret.clone()) }
                .to_vec().map_err(|_| ClientError::Invalid)?), ..Default::default() })
        } else { self.phase = Phase::Authorizing; Ok(Step::default()) }
    }
    pub fn receive(&mut self, operation: u64, context: &ClientContext, bytes: &[u8], now_ms: u64) -> Result<Step, ClientError> {
        self.check_context(operation, context, now_ms)?;
        if self.connected_context.as_ref() != Some(context) { return Err(ClientError::Denied); }
        let message = Message::from_slice(bytes).map_err(|_| ClientError::Invalid)?;
        if message.kind == Kind::Challenge && self.pending.is_none() {
            let challenge = message.challenge.ok_or(ClientError::Invalid)?;
            let grant = self.peer.as_ref().ok_or(ClientError::Denied)?;
            self.validate_challenge(context, &challenge, grant, now_ms)?;
            let local = endpoint_bytes(&self.local_endpoint).map_err(|_| ClientError::Invalid)?;
            let remote = endpoint_bytes(&context.remote_endpoint).map_err(|_| ClientError::Invalid)?;
            let digest = if self.secret.is_some() { challenge.pairing_binding(&context.exporter, &local, &remote) }
                else { challenge.binding(&context.exporter, &local, &remote) }.map_err(|_| ClientError::Invalid)?;
            let mut admitted = grant.clone(); admitted.scopes = challenge.scopes.clone(); admitted.expires_at_ms = challenge.expires_at_ms;
            let capabilities_json = serde_json::to_string(&challenge.capabilities).map_err(|_| ClientError::Invalid)?;
            self.pending = Some(Pending { context: context.clone(), digest, generation: challenge.generation,
                grant: admitted.clone(), capabilities: challenge.capabilities });
            self.phase = Phase::Authorizing;
            Ok(Step { outgoing: Some(Message { version: 1, kind: Kind::Confirm, challenge: None, binding: Some(digest.to_vec()) }
                .to_vec().map_err(|_| ClientError::Invalid)?),
                grant: if self.secret.is_none() { Some(admitted) } else { None },
                generation: challenge.generation, capabilities_json: Some(capabilities_json), ..Default::default() })
        } else if message.kind == Kind::Ready {
            let pending = self.pending.as_ref().ok_or(ClientError::Denied)?;
            let bytes = message.binding.ok_or(ClientError::Invalid)?;
            // Fixed-length XOR avoids comparing any invitation/exporter prefix.
            if pending.context.connection_id != context.connection_id || pending.context.exporter != context.exporter ||
                bytes.iter().zip(pending.digest).fold(0u8, |diff, (a,b)| diff | (a ^ b)) != 0 { return Err(ClientError::Denied); }
            let pairing = self.secret.is_some();
            let pending = self.pending.take().ok_or(ClientError::Denied)?;
            self.phase = if pairing { Phase::NeedsPairing } else { Phase::Online };
            if !pairing { self.generation = pending.generation; self.retry_attempt = 0; }
            self.expected_id = None; self.next_retry_ms = None;
            Ok(Step { grant: Some(pending.grant), generation: pending.generation,
                capabilities_json: Some(serde_json::to_string(&pending.capabilities).map_err(|_| ClientError::Invalid)?),
                ready: !pairing, close_connection: pairing, ..Default::default() })
        } else { Err(ClientError::Denied) }
    }
    fn validate_challenge(&self, context: &ClientContext, challenge: &Challenge, grant: &ClientGrant, now_ms: u64) -> Result<(), ClientError> {
        if challenge.initiator_endpoint != context.local_endpoint || challenge.acceptor_endpoint != context.remote_endpoint ||
            challenge.authorization_epoch != grant.authorization_epoch || challenge.expires_at_ms <= now_ms ||
            challenge.expires_at_ms > grant.expires_at_ms || challenge.scopes.iter().any(|scope| !grant.scopes.contains(scope)) {
            return Err(ClientError::Denied);
        }
        if self.secret.is_some() {
            let mut capabilities = self.approved_capabilities.clone(); capabilities.insert("device_id".into(), Value::String(grant.device_id.clone()));
            let mut actual = challenge.scopes.clone(); actual.sort(); let mut expected = grant.scopes.clone(); expected.sort();
            if challenge.generation != 1 || challenge.expires_at_ms != grant.expires_at_ms || actual != expected || challenge.capabilities != capabilities {
                return Err(ClientError::Denied);
            }
        } else if challenge.generation <= self.generation { return Err(ClientError::Stale); }
        Ok(())
    }
    pub fn pairing_persisted(&mut self, operation: u64) -> Result<(), ClientError> {
        if operation != self.operation || self.phase != Phase::NeedsPairing || self.secret.is_none() || self.pending.is_some() { return Err(ClientError::Stale); }
        self.secret = None; self.phase = Phase::Idle; Ok(())
    }
    pub fn network_changed(&mut self, operation: u64, usable_path: bool) -> Result<(), ClientError> {
        if operation != self.operation { return Err(ClientError::Stale); }
        if matches!(self.phase, Phase::Online | Phase::ChangingPath) {
            self.phase = if usable_path { Phase::Online } else { Phase::ChangingPath };
        }
        Ok(())
    }
    pub fn lost(&mut self, operation: u64, now_ms: u64, jitter_ms: u64) -> Result<(), ClientError> {
        if operation != self.operation { return Err(ClientError::Stale); }
        if matches!(self.phase, Phase::UserDisconnected | Phase::Revoked | Phase::NeedsPairing | Phase::Suspended | Phase::Failed | Phase::Idle | Phase::Recovering) { return Ok(()); }
        self.expected_id = None; self.pending = None; self.connected_context = None;
        if self.secret.is_some() { self.terminate(Phase::NeedsPairing); return Ok(()); }
        if self.peer.as_ref().is_none_or(|g| g.expires_at_ms <= now_ms) { self.terminate(Phase::NeedsPairing); return Ok(()); }
        if self.retry_attempt >= 8 { self.terminate(Phase::Failed); return Ok(()); }
        let delay = (250u64 << self.retry_attempt.min(6)).min(15_000) + jitter_ms.min(250);
        self.retry_attempt += 1; self.next_retry_ms = Some(now_ms.saturating_add(delay)); self.phase = Phase::Recovering;
        Ok(())
    }
    pub fn suspend(&mut self) { self.terminate(Phase::Suspended); }
    pub fn disconnect(&mut self) { self.terminate(Phase::UserDisconnected); }
    pub fn revoke(&mut self) { self.terminate(Phase::Revoked); }
    pub fn deny(&mut self) { self.terminate(Phase::NeedsPairing); }
    fn terminate(&mut self, phase: Phase) {
        self.phase = phase; self.expected_id = None; self.pending = None; self.connected_context = None; self.secret = None; self.next_retry_ms = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn ids() -> (String,String) { (iroh::SecretKey::from_bytes(&[61;32]).public().to_string(), iroh::SecretKey::from_bytes(&[62;32]).public().to_string()) }
    fn grant(endpoint: String) -> ClientGrant { ClientGrant { endpoint_id: endpoint, device_id: "synthetic-device".into(),
        owner_key: "synthetic-owner".into(), canonical_user_id: "synthetic-user".into(), conversation_key: "synthetic-conversation".into(),
        origin_transport: "local".into(), origin_sender_id: "synthetic-install".into(), pairing_mode: "local".into(), device_ownership: "shared".into(),
        authorization_epoch: 1, expires_at_ms: 100_000, scopes: vec!["chat".into()] } }
    fn context(local: String, remote: String, pair: bool) -> ClientContext { ClientContext { connection_id: 7, local_endpoint: local,
        remote_endpoint: remote, exporter: [65;32], protocol: String::from_utf8(if pair { PAIR_ALPN } else { SESSION_ALPN }.to_vec()).unwrap(), initiator: true } }
    fn challenge(context: &ClientContext, pair: bool) -> Challenge {
        let mut capabilities = Map::new(); if pair { capabilities.insert("device_id".into(), Value::String("synthetic-device".into())); }
        Challenge { initiator_endpoint: context.local_endpoint.clone(), acceptor_endpoint: context.remote_endpoint.clone(), nonce: vec![64;32],
            generation: 1, authorization_epoch: 1, expires_at_ms: 100_000, scopes: vec!["chat".into()], capabilities }
    }
    fn encoded(challenge: Challenge) -> Vec<u8> { Message { version: 1, kind: Kind::Challenge, challenge: Some(challenge), binding: None }.to_vec().unwrap() }
    #[test]
    fn client_pairing_requires_exact_native_context_and_store_commit() {
        let (local,remote) = ids(); let ctx = context(local.clone(),remote.clone(),true);
        let answer = serde_json::json!({"version":1,"endpoint_id":remote,"ticket":"synthetic-ticket","redemption": URL_SAFE.encode([66;32]),
            "redemption_expires_in_seconds": 5,"grant":grant(local.clone()),"capabilities": {}});
        let mut session = ClientSession::new(local).unwrap(); let op = session.begin_verified_pairing(&serde_json::to_vec(&answer).unwrap(),1000).unwrap();
        session.bind_dial(op,7).unwrap();
        let mut wrong = ctx.clone(); wrong.connection_id = 8;
        assert!(session.connected(op,&wrong,1001).is_err());
        assert_eq!(Message::from_slice(&session.connected(op,&ctx,1001).unwrap().outgoing.unwrap()).unwrap().kind,Kind::Redeem);
        let mut altered = challenge(&ctx,true); altered.capabilities.insert("media".into(),Value::Bool(true));
        assert_eq!(session.receive(op,&ctx,&encoded(altered),1002).unwrap_err(),ClientError::Denied);
        let step = session.receive(op,&ctx,&encoded(challenge(&ctx,true)),1002).unwrap(); assert!(step.grant.is_none());
        let mut ready = Message::from_slice(&step.outgoing.unwrap()).unwrap(); ready.kind = Kind::Ready;
        let mut wrong = ctx.clone(); wrong.exporter[0] ^= 1;
        assert!(session.receive(op,&wrong,&ready.to_vec().unwrap(),1003).is_err());
        let result = session.receive(op,&ctx,&ready.to_vec().unwrap(),1003).unwrap();
        assert!(!result.ready && result.close_connection && result.grant.unwrap().endpoint_id == remote);
        assert_eq!(session.snapshot().phase,Phase::NeedsPairing);
        session.pairing_persisted(op).unwrap(); assert_eq!(session.snapshot().phase,Phase::Idle);
    }
    #[test]
    fn shared_client_session_fences_generation_and_preserves_live_path_change() {
        let (local,remote) = ids(); let ctx = context(local.clone(),remote.clone(),false);
        let mut session = ClientSession::new(local).unwrap(); let op = session.begin_session(grant(remote),"synthetic-ticket".into(),0,1000).unwrap();
        session.bind_dial(op,7).unwrap(); session.connected(op,&ctx,1001).unwrap();
        let step = session.receive(op,&ctx,&encoded(challenge(&ctx,false)),1002).unwrap(); assert!(step.grant.is_some());
        let mut ready = Message::from_slice(&step.outgoing.unwrap()).unwrap(); ready.kind = Kind::Ready;
        assert!(session.receive(op,&ctx,&ready.to_vec().unwrap(),1003).unwrap().ready);
        session.network_changed(op,false).unwrap(); assert_eq!(session.snapshot().phase,Phase::ChangingPath);
        session.network_changed(op,true).unwrap(); assert_eq!(session.snapshot().generation,1);
        session.lost(op,2000,999).unwrap(); assert_eq!(session.snapshot().next_retry_ms,Some(2500));
        let next = session.begin_session(session.peer().unwrap().clone(),"synthetic-ticket".into(),1,2500).unwrap();
        session.bind_dial(next,7).unwrap(); session.connected(next,&ctx,2501).unwrap();
        assert_eq!(session.receive(next,&ctx,&encoded(challenge(&ctx,false)),2502).unwrap_err(),ClientError::Stale);
        assert_eq!(session.connected(op,&ctx,2502).unwrap_err(),ClientError::Stale);
        session.disconnect(); session.lost(next,2503,0).unwrap(); assert_eq!(session.snapshot().phase,Phase::UserDisconnected);
        assert!(session.snapshot().next_retry_ms.is_none());
    }
    #[test]
    fn denial_revocation_suspension_and_expiry_never_retry_or_downgrade() {
        for terminal in [Phase::Revoked,Phase::NeedsPairing,Phase::Suspended] {
            let (local,remote) = ids(); let mut session = ClientSession::new(local).unwrap();
            let op = session.begin_session(grant(remote.clone()),"synthetic-ticket".into(),0,1000).unwrap();
            match terminal { Phase::Revoked => session.revoke(),Phase::Suspended => session.suspend(),_ => session.deny() }
            session.lost(op,1001,0).unwrap(); assert_eq!(session.snapshot().phase,terminal); assert!(session.snapshot().next_retry_ms.is_none());
            if terminal == Phase::Revoked { assert!(session.begin_session(grant(remote),"synthetic-ticket".into(),0,1002).is_err()); }
        }
        let (local,remote) = ids(); let mut session = ClientSession::new(local).unwrap();
        let op = session.begin_session(grant(remote),"synthetic-ticket".into(),0,1000).unwrap();
        session.lost(op,100_000,0).unwrap(); assert_eq!(session.snapshot().phase,Phase::NeedsPairing);
    }
}

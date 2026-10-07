// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Accepted Peer Link enrollment. Platform adapters atomically persist returned
//! store bytes before calling `persisted`; tickets never become grants.

use std::collections::BTreeMap;
use autoyou_protocol::{PAIR_ALPN, SESSION_ALPN};
use autoyou_protocol::enrollment::{Challenge, Kind, Message};
use base64::{Engine, engine::general_purpose::URL_SAFE};
use ring::rand::{SecureRandom, SystemRandom};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use crate::client::{ClientContext, ClientError, ClientGrant, Step};
use crate::{client_store, host::endpoint_bytes};

pub struct AcceptorStep { pub application: Step, pub store: Option<Vec<u8>> }
impl From<Step> for AcceptorStep {
    fn from(application: Step) -> Self { Self { application, store: None } }
}
struct Invitation { grant: ClientGrant, deadline: u64 }
#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase { WaitingRedeem, PersistGeneration, Confirm, PersistPairing }
struct Pending {
    context: ClientContext, phase: Phase, deadline: u64, grant: Option<ClientGrant>,
    generation: u64, digest: Option<[u8;32]>, challenge: Option<Vec<u8>>,
}

pub struct AcceptorSession {
    local_endpoint: String, capabilities: Map<String, Value>,
    invitations: BTreeMap<[u8;32], Invitation>, pending: BTreeMap<u64, Pending>,
}

impl AcceptorSession {
    pub fn new(local_endpoint: String, capabilities: Map<String, Value>) -> Result<Self, ClientError> {
        endpoint_bytes(&local_endpoint).map_err(|_| ClientError::Invalid)?;
        if capabilities.contains_key("_grant") || capabilities.len() > 64 ||
            serde_json::to_vec(&capabilities).map_err(|_| ClientError::Invalid)?.len() > 8192 {
            return Err(ClientError::Invalid);
        }
        Ok(Self { local_endpoint, capabilities, invitations: BTreeMap::new(), pending: BTreeMap::new() })
    }
    pub(crate) fn peer_grant(grant: &ClientGrant, now: u64) -> Result<(), ClientError> {
        grant.validate(now)?;
        if !grant.scopes.iter().any(|scope| scope == "peer") ||
            grant.scopes.iter().any(|scope| !matches!(scope.as_str(), "peer"|"chat"|"browser"|"files"|"media")) ||
            grant.origin_transport != "peer" || grant.device_ownership != "shared" {
            return Err(ClientError::Denied);
        }
        Ok(())
    }
    fn context(&self, context: &ClientContext) -> Result<(), ClientError> {
        if context.initiator || context.local_endpoint != self.local_endpoint || context.connection_id == 0 ||
            ![PAIR_ALPN, SESSION_ALPN].contains(&context.protocol.as_bytes()) ||
            context.remote_endpoint == self.local_endpoint {
            return Err(ClientError::Denied);
        }
        endpoint_bytes(&context.remote_endpoint).map_err(|_| ClientError::Invalid)?;
        Ok(())
    }
    pub fn expire(&mut self, now: u64) -> Vec<u64> {
        self.invitations.retain(|_, invitation| invitation.deadline > now);
        let expired: Vec<_> = self.pending.iter().filter_map(|(&id, pending)| (pending.deadline <= now).then_some(id)).collect();
        for id in &expired { self.pending.remove(id); }
        expired
    }
    /// Called only by the application's existing human/proof approval owner.
    pub fn issue(&mut self, grant: ClientGrant, ticket: String, now: u64) -> Result<String, ClientError> {
        Self::peer_grant(&grant, now)?;
        if grant.endpoint_id == self.local_endpoint { return Err(ClientError::Denied); }
        let descriptor = serde_json::json!({"transport":"iroh", "version":1,"type":"answer",
            "endpoint_id":self.local_endpoint,"ticket":ticket});
        crate::peer::descriptor(&descriptor.to_string(), "answer").map_err(|_| ClientError::Invalid)?;
        self.invitations.retain(|_, invitation| invitation.deadline > now);
        if self.invitations.len() >= 128 { return Err(ClientError::Denied); }
        let mut secret = [0;32];
        SystemRandom::new().fill(&mut secret).map_err(|_| ClientError::Closed)?;
        let key: [u8;32] = Sha256::digest(secret).into();
        let redemption = URL_SAFE.encode(secret);
        use zeroize::Zeroize;
        secret.zeroize();
        self.invitations.insert(key, Invitation { grant: grant.clone(), deadline: now.saturating_add(30_000).min(grant.expires_at_ms) });
        Ok(serde_json::json!({"version":1,"endpoint_id":self.local_endpoint,"ticket":ticket,
            "redemption":redemption,"redemption_expires_in_seconds":30,"grant":grant,
            "capabilities":self.capabilities}).to_string())
    }
    fn challenge(&self, context: &ClientContext, grant: &ClientGrant, generation: u64) -> Result<(Vec<u8>,[u8;32]), ClientError> {
        let mut nonce = vec![0;32];
        SystemRandom::new().fill(&mut nonce).map_err(|_| ClientError::Closed)?;
        let mut capabilities = self.capabilities.clone();
        if context.protocol.as_bytes() == PAIR_ALPN { capabilities.insert("device_id".into(), Value::String(grant.device_id.clone())); }
        let challenge = Challenge { initiator_endpoint: context.remote_endpoint.clone(), acceptor_endpoint: self.local_endpoint.clone(),
            nonce, generation, authorization_epoch: grant.authorization_epoch, expires_at_ms: grant.expires_at_ms,
            scopes: grant.scopes.clone(), capabilities };
        let remote = endpoint_bytes(&context.remote_endpoint).map_err(|_| ClientError::Invalid)?;
        let local = endpoint_bytes(&self.local_endpoint).map_err(|_| ClientError::Invalid)?;
        let digest = if context.protocol.as_bytes() == PAIR_ALPN { challenge.pairing_binding(&context.exporter, &remote, &local) }
            else { challenge.binding(&context.exporter, &remote, &local) }.map_err(|_| ClientError::Invalid)?;
        let payload = Message { version:1, kind:Kind::Challenge, challenge:Some(challenge), binding:None }
            .to_vec().map_err(|_| ClientError::Invalid)?;
        Ok((payload,digest))
    }
    pub fn connected(&mut self, context: ClientContext, store: &[u8], now: u64) -> Result<AcceptorStep, ClientError> {
        self.context(&context)?;
        self.invitations.retain(|_, invitation| invitation.deadline > now);
        if self.pending.contains_key(&context.connection_id) || self.pending.len() >= 32 { return Err(ClientError::Denied); }
        let mut pending = Pending { context, phase:Phase::WaitingRedeem, deadline:now.saturating_add(10_000),
            grant:None, generation:0, digest:None, challenge:None };
        let update = if pending.context.protocol.as_bytes() == SESSION_ALPN {
            let peer = client_store::load(store,&pending.context.remote_endpoint,now)?;
            Self::peer_grant(&peer.grant,now)?;
            let generation = peer.generation_floor.checked_add(1).ok_or(ClientError::Closed)?;
            let updated = client_store::admit(store,&peer.grant,generation,now)?;
            let (challenge,digest) = self.challenge(&pending.context,&peer.grant,generation)?;
            pending.phase = Phase::PersistGeneration; pending.grant = Some(peer.grant);
            pending.generation = generation; pending.digest = Some(digest); pending.challenge = Some(challenge);
            Some(updated)
        } else { None };
        self.pending.insert(pending.context.connection_id,pending);
        Ok(AcceptorStep { application:Step::default(),store:update })
    }
    pub fn receive(&mut self, context: &ClientContext, payload: &[u8], store: &[u8], now: u64) -> Result<AcceptorStep, ClientError> {
        self.context(context)?;
        let message = Message::from_slice(payload).map_err(|_| ClientError::Invalid)?;
        let mut pending = self.pending.remove(&context.connection_id).ok_or(ClientError::Stale)?;
        if pending.context != *context || now >= pending.deadline { return Err(ClientError::Denied); }
        if pending.phase == Phase::WaitingRedeem && message.kind == Kind::Redeem {
            let key: [u8;32] = Sha256::digest(message.binding.ok_or(ClientError::Invalid)?).into();
            // A redemption is consumed before checking who presented it.
            let invitation = self.invitations.remove(&key).ok_or(ClientError::Denied)?;
            if invitation.deadline <= now || invitation.grant.endpoint_id != context.remote_endpoint { return Err(ClientError::Denied); }
            Self::peer_grant(&invitation.grant,now)?;
            let (challenge,digest) = self.challenge(context,&invitation.grant,1)?;
            pending.phase = Phase::Confirm; pending.deadline = invitation.deadline;
            pending.grant = Some(invitation.grant); pending.generation = 1; pending.digest = Some(digest);
            self.pending.insert(context.connection_id,pending);
            return Ok(Step { outgoing:Some(challenge),..Default::default() }.into());
        }
        if pending.phase != Phase::Confirm || message.kind != Kind::Confirm { return Err(ClientError::Denied); }
        let confirmation = message.binding.ok_or(ClientError::Invalid)?;
        let digest = pending.digest.ok_or(ClientError::Stale)?;
        // Enrollment validates the fixed 32-byte length before comparison.
        if confirmation.iter().zip(digest).fold(0u8, |diff, (a,b)| diff | (a ^ b)) != 0 {
            return Err(ClientError::Denied);
        }
        let grant = pending.grant.as_ref().ok_or(ClientError::Stale)?;
        Self::peer_grant(grant,now)?;
        if context.protocol.as_bytes() == PAIR_ALPN {
            let updated = client_store::register(store,grant.clone(),now)?;
            pending.phase = Phase::PersistPairing;
            self.pending.insert(context.connection_id,pending);
            return Ok(AcceptorStep { application:Step::default(),store:Some(updated) });
        }
        let current = client_store::load(store,&context.remote_endpoint,now)?;
        if current.grant != *grant || current.generation_floor != pending.generation { return Err(ClientError::Denied); }
        Ok(self.ready(pending)?.into())
    }
    fn ready(&self, pending: Pending) -> Result<Step, ClientError> {
        let pairing = pending.context.protocol.as_bytes() == PAIR_ALPN;
        let outgoing = Message { version:1,kind:Kind::Ready,challenge:None,binding:Some(pending.digest.ok_or(ClientError::Stale)?.to_vec()) }
            .to_vec().map_err(|_| ClientError::Invalid)?;
        Ok(Step { outgoing:Some(outgoing),grant:pending.grant,generation:pending.generation,
            capabilities_json:Some(serde_json::to_string(&self.capabilities).map_err(|_| ClientError::Invalid)?),
            ready:!pairing,close_connection:pairing })
    }
    pub fn persisted(&mut self, context: &ClientContext, store: &[u8], now: u64) -> Result<Step, ClientError> {
        self.context(context)?;
        let mut pending = self.pending.remove(&context.connection_id).ok_or(ClientError::Stale)?;
        if pending.context != *context || now >= pending.deadline { return Err(ClientError::Denied); }
        let current = client_store::load(store,&context.remote_endpoint,now)?;
        if pending.grant.as_ref() != Some(&current.grant) { return Err(ClientError::Denied); }
        match pending.phase {
            Phase::PersistGeneration if current.generation_floor == pending.generation => {
                pending.phase = Phase::Confirm;
                let outgoing = pending.challenge.take();
                self.pending.insert(context.connection_id,pending);
                Ok(Step { outgoing,..Default::default() })
            },
            Phase::PersistPairing => self.ready(pending),
            _ => Err(ClientError::Denied),
        }
    }
    pub fn closed(&mut self, connection: u64) { self.pending.remove(&connection); }
    pub fn cancel_endpoint(&mut self, endpoint: &str) -> Vec<u64> {
        self.invitations.retain(|_, invitation| invitation.grant.endpoint_id != endpoint);
        let connections: Vec<_> = self.pending.iter().filter_map(|(&id, pending)| (pending.context.remote_endpoint == endpoint).then_some(id)).collect();
        for connection in &connections { self.pending.remove(connection); }
        connections
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::client::ClientSession;
    use iroh::{EndpointAddr, SecretKey};
    use iroh_tickets::endpoint::EndpointTicket;

    fn grant(endpoint: String) -> ClientGrant {
        ClientGrant { endpoint_id:endpoint,device_id:"synthetic-peer-device".into(),owner_key:"synthetic-peer-owner".into(),
            canonical_user_id:"synthetic-user".into(),conversation_key:"synthetic-conversation".into(),
            origin_transport:"peer".into(),origin_sender_id:"synthetic-peer-install".into(),pairing_mode:"manual-peer".into(),
            device_ownership:"shared".into(),authorization_epoch:1,expires_at_ms:100_000,scopes:vec!["peer".into(),"chat".into(),"media".into()] }
    }
    fn contexts(protocol: &[u8], id: u64) -> (ClientContext,ClientContext) {
        let guest = SecretKey::from_bytes(&[61;32]).public().to_string();
        let host = SecretKey::from_bytes(&[62;32]).public().to_string();
        let caller = ClientContext { connection_id:id,remote_endpoint:host.clone(),local_endpoint:guest.clone(),
            exporter:[7;32],protocol:String::from_utf8(protocol.to_vec()).unwrap(),initiator:true };
        let acceptor = ClientContext { connection_id:id,remote_endpoint:guest,local_endpoint:host,
            exporter:[7;32],protocol:caller.protocol.clone(),initiator:false };
        (caller,acceptor)
    }
    fn ticket() -> String {
        EndpointTicket::new(EndpointAddr::new(SecretKey::from_bytes(&[62;32]).public())
            .with_ip_addr("127.0.0.1:31415".parse().unwrap())).to_string()
    }
    #[test]
    fn both_sides_require_proof_and_atomic_persistence_then_recover_with_new_generation() {
        let (caller,accepted) = contexts(PAIR_ALPN,1);
        let mut host = AcceptorSession::new(accepted.local_endpoint.clone(),serde_json::json!({"transport":"iroh","peer":true}).as_object().unwrap().clone()).unwrap();
        let mut client = ClientSession::new(caller.local_endpoint.clone()).unwrap();
        let proof = host.issue(grant(caller.local_endpoint.clone()),ticket(),1000).unwrap();
        let operation = client.begin_verified_pairing(proof.as_bytes(),1000).unwrap();
        client.bind_dial(operation,1).unwrap();
        let mut store = client_store::empty();
        assert!(host.connected(accepted.clone(),&store,1000).unwrap().application.outgoing.is_none());
        let redeem = client.connected(operation,&caller,1000).unwrap().outgoing.unwrap();
        let challenge = host.receive(&accepted,&redeem,&store,1000).unwrap().application.outgoing.unwrap();
        let confirm = client.receive(operation,&caller,&challenge,1000).unwrap().outgoing.unwrap();
        let step = host.receive(&accepted,&confirm,&store,1000).unwrap();
        assert!(step.application.outgoing.is_none()); // No READY before protected storage commits.
        store = step.store.unwrap();
        let ready = host.persisted(&accepted,&store,1000).unwrap();
        assert!(ready.close_connection && !ready.ready);
        let paired = client.receive(operation,&caller,&ready.outgoing.unwrap(),1000).unwrap();
        assert!(paired.close_connection && !paired.ready);
        client.pairing_persisted(operation).unwrap();
        let paired_grant = paired.grant.unwrap();
        for generation in 1..=2 {
            let (caller,accepted) = contexts(SESSION_ALPN,generation+1);
            let operation = client.begin_session(paired_grant.clone(),ticket(),generation-1,1100).unwrap();
            client.bind_dial(operation,caller.connection_id).unwrap();
            client.connected(operation,&caller,1100).unwrap();
            let step = host.connected(accepted.clone(),&store,1100).unwrap();
            assert!(step.application.outgoing.is_none()); // Generation also commits before publication.
            store = step.store.unwrap();
            let challenge = host.persisted(&accepted,&store,1100).unwrap().outgoing.unwrap();
            let confirm = client.receive(operation,&caller,&challenge,1100).unwrap().outgoing.unwrap();
            let ready = host.receive(&accepted,&confirm,&store,1100).unwrap().application;
            assert!(ready.ready && ready.generation == generation);
            assert!(client.receive(operation,&caller,&ready.outgoing.unwrap(),1100).unwrap().ready);
            client.lost(operation,1200,0).unwrap();
        }
    }
    #[test]
    fn wrong_purpose_context_persistence_revoke_and_expiry_are_denied() {
        let (_caller,accepted) = contexts(SESSION_ALPN,1);
        let mut host = AcceptorSession::new(accepted.local_endpoint.clone(),Map::new()).unwrap();
        let grant = grant(accepted.remote_endpoint.clone());
        let original = client_store::register(&client_store::empty(),grant.clone(),1000).unwrap();
        let step = host.connected(accepted.clone(),&original,1000).unwrap();
        assert!(host.persisted(&accepted,&original,1000).is_err());
        host.closed(1);
        let step2 = host.connected(accepted.clone(),&original,1000).unwrap();
        assert_eq!(step.store,step2.store);
        let store = step2.store.unwrap();
        let mut altered = accepted.clone(); altered.exporter = [9;32];
        assert!(host.persisted(&altered,&store,1000).is_err());
        host.closed(1);
        host.connected(accepted.clone(),&original,1000).unwrap();
        let revoked = client_store::revoke(&store,&grant.device_id,2).unwrap();
        assert!(host.persisted(&accepted,&revoked,1000).is_err());
        host.closed(1);
        host.connected(accepted.clone(),&original,1000).unwrap();
        assert_eq!(host.expire(11_000),vec![1]);
        assert!(host.persisted(&accepted,&store,11_000).is_err());
        let mut reflected = accepted.clone(); reflected.initiator = true;
        assert!(host.connected(reflected,&original,1000).is_err());
        let mut widened = grant; widened.scopes.push("control".into());
        assert!(host.issue(widened,ticket(),1000).is_err());
    }
    #[test]
    fn redemption_is_single_use_and_exact_endpoint_bound() {
        let (_caller,accepted) = contexts(PAIR_ALPN,1);
        let mut host = AcceptorSession::new(accepted.local_endpoint.clone(),Map::new()).unwrap();
        let proof: Value = serde_json::from_str(&host.issue(grant(accepted.remote_endpoint.clone()),ticket(),1000).unwrap()).unwrap();
        let redemption = URL_SAFE.decode(proof["redemption"].as_str().unwrap()).unwrap();
        let redeem = Message { version:1,kind:Kind::Redeem,challenge:None,binding:Some(redemption) }.to_vec().unwrap();
        let mut other = accepted.clone(); other.remote_endpoint = SecretKey::from_bytes(&[63;32]).public().to_string();
        host.connected(other.clone(),&client_store::empty(),1000).unwrap();
        assert!(host.receive(&other,&redeem,&client_store::empty(),1000).is_err());
        host.connected(accepted.clone(),&client_store::empty(),1000).unwrap();
        assert!(host.receive(&accepted,&redeem,&client_store::empty(),1000).is_err());
    }
}

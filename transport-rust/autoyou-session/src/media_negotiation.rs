// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Strict source negotiation. Only an application's local consent owner approves
//! a call. A remote offer only requests receiving; it can never approve capture.
use std::collections::{HashMap, HashSet};
use autoyou_protocol::{Principal, ProtocolError};
use serde::{Deserialize, Serialize};
use crate::media::{SourceLease, Sources};

type Result<T> = std::result::Result<T, ProtocolError>;
const MAX_CALLS: usize = 8;
const MAX_CALL_FLOORS: usize = 128;
pub const MAX_MEDIA_CONTROL_BYTES: usize = 4096;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MediaConsent {
    pub call_id: String,
    pub local_target_id: String,
    pub remote_target_id: String,
    pub expires_at_ms: u64,
    pub send_kinds: Vec<u8>,
    pub receive_kinds: Vec<u8>,
    pub codecs: Vec<u8>,
    pub maximum_width: u16,
    pub maximum_height: u16,
    pub maximum_fps: u16,
    pub maximum_audio_channels: u8,
}
fn identifier(value: &str) -> bool {
    !value.is_empty() && value.len() <= 128 && !value.chars().any(char::is_control)
}
fn set(values: &[u8]) -> bool {
    values.len() <= 4 && values.iter().all(|value|(1..=4).contains(value)) &&
        values.iter().copied().collect::<HashSet<_>>().len() == values.len()
}
impl MediaConsent {
    fn validate(&self, principal: &Principal, now_ms: u64) -> Result<()> {
        if !identifier(&self.call_id) || !identifier(&self.local_target_id) || !identifier(&self.remote_target_id) ||
            !set(&self.send_kinds) || !set(&self.receive_kinds) || !set(&self.codecs) || self.codecs.is_empty() ||
            !matches!(self.maximum_audio_channels,1|2) || self.expires_at_ms <= now_ms ||
            self.expires_at_ms > principal.expires_at_ms {
            return Err(ProtocolError::ScopeDenied);
        }
        if self.send_kinds.iter().chain(&self.receive_kinds).any(|kind|matches!(kind,2|3)) &&
            (self.maximum_width == 0 || self.maximum_width > 8192 || self.maximum_height == 0 ||
                self.maximum_height > 8192 || !(1..=120).contains(&self.maximum_fps)) {
            return Err(ProtocolError::InvalidFrame);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceReference {
    pub call_id: String, pub lease_id: String, pub source_id: u64, pub media_generation: u64,
}
impl SourceReference {
    fn from_lease(lease: &SourceLease) -> Self {
        Self { call_id: lease.call_id.clone(), lease_id: lease.lease_id.clone(),
            source_id: lease.source_id, media_generation: lease.media_generation }
    }
    fn validate(&self) -> Result<()> {
        if !identifier(&self.call_id) || !identifier(&self.lease_id) || self.source_id == 0 || self.media_generation == 0 {
            return Err(ProtocolError::InvalidFrame);
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceFeedback {
    pub played_sequence: u64, pub lost_frames: u64, pub jitter_us: u64,
    pub buffered_frames: u16, pub keyframe_required: bool, pub quality_scale_permille: u16,
}
impl SourceFeedback {
    fn validate(&self) -> Result<()> {
        if self.jitter_us > 1_000_000 || self.buffered_frames > 64 || !(250..=1000).contains(&self.quality_scale_permille) {
            return Err(ProtocolError::InvalidFrame);
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag="action",rename_all="snake_case",deny_unknown_fields)]
enum Signal {
    Offer { lease: SourceLease },
    Accept { source: SourceReference },
    Revoke { source: SourceReference, source_sender: String },
    Feedback { source: SourceReference, feedback: SourceFeedback },
    End { call_id: String },
}
fn wire(signal: &Signal) -> Result<String> {
    let value = serde_json::to_string(signal).map_err(|_|ProtocolError::InvalidFrame)?;
    if value.len() > MAX_MEDIA_CONTROL_BYTES { return Err(ProtocolError::FrameTooLarge); }
    Ok(value)
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MediaActionKind { PrepareReceiver, ActivateSender, CloseSource, ApplyFeedback, Reply }
pub struct MediaAction {
    pub kind: MediaActionKind, pub inbound: bool, pub lease: Option<SourceLease>,
    pub reply: Option<String>, pub feedback: Option<SourceFeedback>,
}
impl MediaAction {
    fn new(kind: MediaActionKind, inbound: bool, lease: SourceLease) -> Self {
        Self { kind,inbound,lease: Some(lease),reply: None,feedback: None }
    }
}
struct SourceState { lease: SourceLease, ready: bool }
pub struct MediaNegotiation {
    principal: Principal, local_endpoint_id: String, consents: HashMap<String,MediaConsent>,
    call_floors: HashSet<String>, sources: HashMap<(bool,u64),SourceState>, permissions: Sources, closed: bool,
}
impl MediaNegotiation {
    pub fn new(principal: Principal, local_endpoint_id: String, now_ms: u64) -> Result<Self> {
        principal.validate()?;
        if !identifier(&local_endpoint_id) || local_endpoint_id == principal.endpoint_id ||
            principal.expires_at_ms <= now_ms || !principal.scopes.iter().any(|scope|scope == "media") {
            return Err(ProtocolError::ScopeDenied);
        }
        Ok(Self { principal,local_endpoint_id,consents: HashMap::new(),call_floors: HashSet::new(),
            sources: HashMap::new(),permissions: Sources::default(),closed: false })
    }
    fn open(&self, now_ms: u64) -> Result<()> {
        if self.closed || now_ms >= self.principal.expires_at_ms { return Err(ProtocolError::Revoked); }
        Ok(())
    }
    /// This method is never called from decoded signaling, bootstrap or recovery.
    pub fn approve_call(&mut self, consent: MediaConsent, now_ms: u64) -> Result<()> {
        self.open(now_ms)?; consent.validate(&self.principal,now_ms)?;
        if let Some(previous) = self.consents.get(&consent.call_id) {
            return if previous == &consent { Ok(()) } else { Err(ProtocolError::ScopeDenied) };
        }
        if self.call_floors.contains(&consent.call_id) { return Err(ProtocolError::StaleGeneration); }
        if self.consents.len() >= MAX_CALLS || self.call_floors.len() >= MAX_CALL_FLOORS { return Err(ProtocolError::FrameTooLarge); }
        self.call_floors.insert(consent.call_id.clone()); self.consents.insert(consent.call_id.clone(),consent); Ok(())
    }
    fn check_lease(&self, lease: &SourceLease, inbound: bool, now_ms: u64) -> Result<()> {
        self.open(now_ms)?; lease.validate(&self.principal,now_ms)?;
        let consent = self.consents.get(&lease.call_id).ok_or(ProtocolError::ScopeDenied)?;
        consent.validate(&self.principal,now_ms)?;
        let (participant,target,kinds) = if inbound {
            (&self.principal.endpoint_id,&consent.remote_target_id,&consent.receive_kinds)
        } else { (&self.local_endpoint_id,&consent.local_target_id,&consent.send_kinds) };
        if &lease.participant_id != participant || &lease.target_id != target || !kinds.contains(&lease.kind) ||
            !consent.codecs.contains(&lease.codec) || lease.expires_at_ms > consent.expires_at_ms ||
            lease.channels > consent.maximum_audio_channels ||
            (matches!(lease.kind,2|3) && (lease.width > consent.maximum_width || lease.height > consent.maximum_height || lease.fps > consent.maximum_fps)) {
            return Err(ProtocolError::ScopeDenied);
        }
        Ok(())
    }
    pub fn offer_source(&mut self, lease: SourceLease, now_ms: u64) -> Result<String> {
        self.check_lease(&lease,false,now_ms)?;
        self.permissions.approve(lease.clone(),false,&self.principal,now_ms)?;
        let key = (false,lease.source_id);
        if self.sources.get(&key).is_none_or(|state|state.lease != lease) {
            self.sources.insert(key,SourceState { lease: lease.clone(),ready: false });
        }
        wire(&Signal::Offer { lease })
    }
    pub fn receive(&mut self, json: &str, now_ms: u64) -> Result<Vec<MediaAction>> {
        self.open(now_ms)?;
        if json.len() > MAX_MEDIA_CONTROL_BYTES { return Err(ProtocolError::FrameTooLarge); }
        let signal: Signal = serde_json::from_str(json).map_err(|_|ProtocolError::InvalidFrame)?;
        match signal {
            Signal::Offer { lease } => {
                self.check_lease(&lease,true,now_ms)?;
                let key = (true,lease.source_id);
                if let Some(state) = self.sources.get(&key) {
                    if lease.media_generation < state.lease.media_generation { return Ok(vec![]); }
                    if state.lease == lease {
                        if !state.ready { return Ok(vec![]); }
                        let mut action = MediaAction::new(MediaActionKind::Reply,true,lease.clone());
                        action.reply = Some(wire(&Signal::Accept { source: SourceReference::from_lease(&lease) })?);
                        return Ok(vec![action]);
                    }
                }
                self.permissions.approve(lease.clone(),true,&self.principal,now_ms)?;
                self.sources.insert(key,SourceState { lease: lease.clone(),ready: false });
                Ok(vec![MediaAction::new(MediaActionKind::PrepareReceiver,true,lease)])
            },
            Signal::Accept { source } => {
                source.validate()?;
                let Some(state) = self.sources.get(&(false,source.source_id)) else { return Ok(vec![]); };
                if SourceReference::from_lease(&state.lease) != source || state.ready { return Ok(vec![]); }
                self.check_lease(&state.lease,false,now_ms)?;
                let state = self.sources.get_mut(&(false,source.source_id)).unwrap(); state.ready = true;
                Ok(vec![MediaAction::new(MediaActionKind::ActivateSender,false,state.lease.clone())])
            },
            Signal::Revoke { source,source_sender } => {
                source.validate()?;
                let inbound = if source_sender == self.local_endpoint_id { false }
                    else if source_sender == self.principal.endpoint_id { true } else { return Err(ProtocolError::ScopeDenied); };
                Ok(self.retire_reference(&source,inbound,None).into_iter().collect())
            },
            Signal::Feedback { source,feedback } => {
                source.validate()?; feedback.validate()?;
                let Some(state) = self.sources.get(&(false,source.source_id)) else { return Ok(vec![]); };
                if SourceReference::from_lease(&state.lease) != source || !state.ready { return Ok(vec![]); }
                self.check_lease(&state.lease,false,now_ms)?;
                let mut action = MediaAction::new(MediaActionKind::ApplyFeedback,false,state.lease.clone());
                action.feedback = Some(feedback); Ok(vec![action])
            },
            Signal::End { call_id } => {
                if !identifier(&call_id) { return Err(ProtocolError::InvalidFrame); }
                self.end_call_owned(&call_id,false)
            },
        }
    }
    /// A joined receiving adapter calls this only after native source approval.
    pub fn receiver_prepared(&mut self, source: SourceReference, now_ms: u64) -> Result<String> {
        source.validate()?;
        let state = self.sources.get(&(true,source.source_id)).ok_or(ProtocolError::ScopeDenied)?;
        if SourceReference::from_lease(&state.lease) != source { return Err(ProtocolError::StaleGeneration); }
        self.check_lease(&state.lease,true,now_ms)?;
        self.sources.get_mut(&(true,source.source_id)).unwrap().ready = true;
        wire(&Signal::Accept { source })
    }
    pub fn feedback(&self, source: SourceReference, feedback: SourceFeedback, now_ms: u64) -> Result<String> {
        source.validate()?; feedback.validate()?;
        let state = self.sources.get(&(true,source.source_id)).ok_or(ProtocolError::ScopeDenied)?;
        if !state.ready || SourceReference::from_lease(&state.lease) != source { return Err(ProtocolError::StaleGeneration); }
        self.check_lease(&state.lease,true,now_ms)?;
        wire(&Signal::Feedback { source,feedback })
    }
    pub fn check_source(&self, source: &SourceReference, inbound: bool, now_ms: u64) -> Result<()> {
        source.validate()?;
        let state = self.sources.get(&(inbound,source.source_id)).ok_or(ProtocolError::ScopeDenied)?;
        if SourceReference::from_lease(&state.lease) != *source { return Err(ProtocolError::StaleGeneration); }
        if !inbound && !state.ready { return Err(ProtocolError::NotAuthorized); }
        self.check_lease(&state.lease,inbound,now_ms)
    }
    fn retire_reference(&mut self, source: &SourceReference, inbound: bool, reply: Option<String>) -> Option<MediaAction> {
        let key = (inbound,source.source_id);
        if self.sources.get(&key).is_none_or(|state|SourceReference::from_lease(&state.lease) != *source) { return None; }
        self.permissions.revoke(source.source_id,inbound);
        let state = self.sources.remove(&key).unwrap();
        let mut action = MediaAction::new(MediaActionKind::CloseSource,inbound,state.lease); action.reply = reply; Some(action)
    }
    pub fn revoke_source(&mut self, source: SourceReference, inbound: bool) -> Result<Vec<MediaAction>> {
        source.validate()?;
        let source_sender = if inbound { &self.principal.endpoint_id } else { &self.local_endpoint_id };
        let reply = wire(&Signal::Revoke { source: source.clone(),source_sender: source_sender.clone() })?;
        Ok(self.retire_reference(&source,inbound,Some(reply)).into_iter().collect())
    }
    fn end_call_owned(&mut self, call_id: &str, send: bool) -> Result<Vec<MediaAction>> {
        let approved = self.consents.remove(call_id).is_some();
        let refs: Vec<_> = self.sources.iter().filter(|(_,state)|state.lease.call_id == call_id)
            .map(|((inbound,_),state)|(*inbound,SourceReference::from_lease(&state.lease))).collect();
        let mut actions: Vec<_> = refs.into_iter().filter_map(|(inbound,source)|self.retire_reference(&source,inbound,None)).collect();
        if send && (approved || !actions.is_empty()) {
            let reply = wire(&Signal::End { call_id: call_id.into() })?;
            actions.push(MediaAction { kind: MediaActionKind::Reply,inbound: false,lease: None,
                reply: Some(reply),feedback: None });
        }
        Ok(actions)
    }
    pub fn end_call(&mut self, call_id: &str) -> Result<Vec<MediaAction>> {
        if !identifier(call_id) { return Err(ProtocolError::InvalidFrame); }
        self.end_call_owned(call_id,true)
    }
    pub fn expire(&mut self, now_ms: u64) -> Vec<MediaAction> {
        let calls: Vec<_> = self.consents.iter().filter(|(_,consent)|now_ms >= consent.expires_at_ms || now_ms >= self.principal.expires_at_ms)
            .map(|(id,_)|id.clone()).collect();
        let mut actions = Vec::new();
        for id in calls { actions.extend(self.end_call_owned(&id,true).unwrap_or_default()); }
        let refs: Vec<_> = self.sources.iter().filter(|(_,state)|now_ms >= state.lease.expires_at_ms)
            .map(|((inbound,_),state)|(*inbound,SourceReference::from_lease(&state.lease))).collect();
        for (inbound,source) in refs { actions.extend(self.revoke_source(source,inbound).unwrap_or_default()); }
        actions
    }
    pub fn close(&mut self) -> Vec<MediaAction> {
        self.closed = true;
        let calls: Vec<_> = self.consents.keys().cloned().collect();
        let mut actions = Vec::new();
        for id in calls { actions.extend(self.end_call_owned(&id,false).unwrap_or_default()); }
        actions
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn principal(peer: &str) -> Principal {
        Principal { endpoint_id: peer.into(),device_id: "synthetic-device".into(),owner_id: "synthetic-owner".into(),
            conversation_id: "synthetic-conversation".into(),generation: 7,authorization_epoch: 3,
            expires_at_ms: 10_000,scopes: vec!["media".into()] }
    }
    fn consent(local: &str, remote: &str) -> MediaConsent {
        MediaConsent { call_id: "synthetic-call".into(),local_target_id: local.into(),remote_target_id: remote.into(),
            expires_at_ms: 9000,send_kinds: vec![1],receive_kinds: vec![1],codecs: vec![1,4],
            maximum_width: 0,maximum_height: 0,maximum_fps: 0,maximum_audio_channels: 1 }
    }
    fn lease() -> SourceLease {
        SourceLease { lease_id: "synthetic-lease".into(),call_id: "synthetic-call".into(),participant_id: "synthetic-a".into(),
            target_id: "synthetic-a-target".into(),source_id: 9,media_generation: 2,authorization_epoch: 3,
            expires_at_ms: 8000,kind: 1,codec: 4,sample_rate: 48000,channels: 1,width: 0,height: 0,fps: 0,
            layout: "single".into(),maximum_delay_ms: 200 }
    }
    fn pair() -> (MediaNegotiation,MediaNegotiation) {
        let mut a = MediaNegotiation::new(principal("synthetic-b"),"synthetic-a".into(),1000).unwrap();
        let mut b = MediaNegotiation::new(principal("synthetic-a"),"synthetic-b".into(),1000).unwrap();
        a.approve_call(consent("synthetic-a-target","synthetic-b-target"),1000).unwrap();
        b.approve_call(consent("synthetic-b-target","synthetic-a-target"),1000).unwrap(); (a,b)
    }
    #[test]
    fn media_negotiation_requires_local_consent_and_receiver_readiness_before_capture() {
        let (mut a,mut b) = pair();
        let offer = a.offer_source(lease(),1000).unwrap();
        assert!(a.check_source(&SourceReference::from_lease(&lease()),false,1000).is_err());
        let mut cold = MediaNegotiation::new(principal("synthetic-a"),"synthetic-b".into(),1000).unwrap();
        assert!(cold.receive(&offer,1000).is_err());
        let actions = b.receive(&offer,1000).unwrap();
        assert_eq!(actions[0].kind,MediaActionKind::PrepareReceiver); assert!(actions[0].inbound && actions[0].reply.is_none());
        assert!(b.receive(&offer,1000).unwrap().is_empty());
        let accepted = b.receiver_prepared(SourceReference::from_lease(&lease()),1000).unwrap();
        let actions = a.receive(&accepted,1000).unwrap();
        assert_eq!(actions[0].kind,MediaActionKind::ActivateSender); assert!(!actions[0].inbound);
        a.check_source(&SourceReference::from_lease(&lease()),false,1000).unwrap();
        assert!(a.receive(&accepted,1000).unwrap().is_empty());
        assert_eq!(b.receive(&offer,1000).unwrap()[0].reply.as_ref(),Some(&accepted));
    }
    #[test]
    fn media_negotiation_fences_source_target_kind_codec_epoch_and_unknown_fields() {
        let (mut a,mut b) = pair();
        for lease in [SourceLease { participant_id: "synthetic-other".into(),..lease() },
            SourceLease { target_id: "synthetic-other".into(),..lease() }, SourceLease { authorization_epoch: 4,..lease() },
            SourceLease { channels: 2,..lease() },SourceLease { call_id: "synthetic-other-call".into(),..lease() }] {
            assert!(a.offer_source(lease.clone(),1000).is_err());
            assert!(b.receive(&wire(&Signal::Offer { lease }).unwrap(),1000).is_err());
        }
        let mut blocked=consent("synthetic-a-target","synthetic-b-target"); blocked.call_id="synthetic-diagnostic-denied".into(); blocked.codecs=vec![1];
        a.approve_call(blocked,1000).unwrap();
        assert!(a.offer_source(SourceLease { call_id: "synthetic-diagnostic-denied".into(),..lease() },1000).is_err());
        let offer=a.offer_source(lease(),1000).unwrap();
        let mut value: serde_json::Value=serde_json::from_str(&offer).unwrap(); value["owner_id"]="synthetic-forged".into();
        assert!(b.receive(&value.to_string(),1000).is_err());
        value.as_object_mut().unwrap().remove("owner_id"); value["lease"]["owner_id"]="synthetic-forged".into();
        assert!(b.receive(&value.to_string(),1000).is_err());
        assert!(b.receive(&"x".repeat(MAX_MEDIA_CONTROL_BYTES+1),1000).is_err());
    }
    #[test]
    fn media_negotiation_stale_accept_or_revoke_cannot_activate_or_retire_replacement() {
        let (mut a,mut b) = pair();
        b.receive(&a.offer_source(lease(),1000).unwrap(),1000).unwrap();
        let old_accepted=b.receiver_prepared(SourceReference::from_lease(&lease()),1000).unwrap();
        let old_revoke=b.revoke_source(SourceReference::from_lease(&lease()),true).unwrap().remove(0).reply.unwrap();
        let replacement=SourceLease { lease_id: "synthetic-next-lease".into(),media_generation: 3,..lease() };
        b.receive(&a.offer_source(replacement.clone(),1000).unwrap(),1000).unwrap();
        assert!(a.receive(&old_accepted,1000).unwrap().is_empty()); assert!(a.receive(&old_revoke,1000).unwrap().is_empty());
        let accepted=b.receiver_prepared(SourceReference::from_lease(&replacement),1000).unwrap();
        assert_eq!(a.receive(&accepted,1000).unwrap()[0].kind,MediaActionKind::ActivateSender);
        assert_eq!(a.expire(8000).len(),1); assert!(a.receive(&accepted,8000).unwrap().is_empty());
    }
    #[test]
    fn media_negotiation_feedback_end_and_expiry_do_not_reacquire_capture() {
        let (mut a,mut b) = pair(); b.receive(&a.offer_source(lease(),1000).unwrap(),1000).unwrap();
        let accepted=b.receiver_prepared(SourceReference::from_lease(&lease()),1000).unwrap(); a.receive(&accepted,1000).unwrap();
        let feedback=SourceFeedback { played_sequence: 5,lost_frames: 2,jitter_us: 3000,buffered_frames: 3,
            keyframe_required: true,quality_scale_permille: 800 };
        let message=b.feedback(SourceReference::from_lease(&lease()),feedback.clone(),1000).unwrap();
        let actions=a.receive(&message,1000).unwrap(); assert_eq!(actions[0].kind,MediaActionKind::ApplyFeedback);
        assert_eq!(actions[0].feedback.as_ref(),Some(&feedback));
        assert!(b.feedback(SourceReference::from_lease(&lease()),SourceFeedback { quality_scale_permille: 1001,..feedback },1000).is_err());
        let actions=b.end_call("synthetic-call").unwrap(); assert_eq!(actions[0].kind,MediaActionKind::CloseSource);
        assert_eq!(a.receive(actions[1].reply.as_ref().unwrap(),1000).unwrap()[0].kind,MediaActionKind::CloseSource);
        assert!(a.offer_source(lease(),1000).is_err());
        assert!(a.approve_call(consent("synthetic-a-target","synthetic-b-target"),1000).is_err());
        assert!(b.close().is_empty()); assert!(b.receive(&message,1000).is_err());
    }
    #[test]
    fn media_negotiation_ends_a_call_even_before_any_source_is_offered() {
        let (mut a,mut b)=pair();
        let actions=a.end_call("synthetic-call").unwrap();
        assert_eq!(actions.len(),1); assert!(actions[0].lease.is_none());
        assert!(b.receive(actions[0].reply.as_ref().unwrap(),1000).unwrap().is_empty());
        assert!(b.approve_call(consent("synthetic-b-target","synthetic-a-target"),1000).is_err());
        assert!(a.end_call("synthetic-call").unwrap().is_empty());
    }
    #[test]
    fn media_negotiation_limits_call_tombstones_and_active_sources() {
        let (mut a,_b)=pair();
        for id in 1..MAX_CALL_FLOORS {
            let mut approval=consent("synthetic-a-target","synthetic-b-target"); approval.call_id=format!("synthetic-call-{id}");
            a.approve_call(approval.clone(),1000).unwrap(); a.end_call(&approval.call_id).unwrap();
        }
        let mut overflow=consent("synthetic-a-target","synthetic-b-target"); overflow.call_id="synthetic-overflow".into();
        assert!(a.approve_call(overflow,1000).is_err());
        for id in 1..=16 { a.offer_source(SourceLease { source_id: id,..lease() },1000).unwrap(); }
        assert!(a.offer_source(SourceLease { source_id: 17,..lease() },1000).is_err());
        assert_eq!(a.close().len(),16);
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Application-approved source leases, independent frame ordering and bounds.
//! Endpoint admission alone never approves a microphone/camera/screen source.

use std::{collections::HashMap, time::Duration};
use autoyou_protocol::{Principal, ProtocolError, media::{Codec, MediaFence, MediaHeader, MediaKind, SequenceWindow}};
use serde::{Deserialize, Serialize};
use tokio::sync::watch;

pub const MAX_ACTIVE_SOURCES: usize = 16;
pub const MAX_SOURCE_FLOORS: usize = 128;
pub const MEDIA_EXPIRED_CODE: u32 = 2;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceLease {
    pub lease_id: String,
    pub call_id: String,
    pub participant_id: String,
    pub target_id: String,
    pub source_id: u64,
    pub media_generation: u64,
    pub authorization_epoch: u64,
    pub expires_at_ms: u64,
    pub kind: u8,
    pub codec: u8,
    pub sample_rate: u32,
    pub channels: u8,
    pub width: u16,
    pub height: u16,
    pub fps: u16,
    pub layout: String,
    /// Local frame transport budget; sender timestamps are a separate clock.
    pub maximum_delay_ms: u16,
}

impl SourceLease {
    pub fn validate(&self, principal: &Principal, now_ms: u64) -> Result<(), ProtocolError> {
        principal.validate()?;
        if !principal.scopes.iter().any(|scope| scope == "media") ||
            self.authorization_epoch != principal.authorization_epoch ||
            self.expires_at_ms <= now_ms || self.expires_at_ms > principal.expires_at_ms {
            return Err(ProtocolError::Revoked);
        }
        for value in [&self.lease_id, &self.call_id, &self.participant_id, &self.target_id] {
            if value.is_empty() || value.len() > 128 || value.chars().any(char::is_control) {
                return Err(ProtocolError::InvalidFrame);
            }
        }
        if self.source_id == 0 || self.media_generation == 0 ||
            !(20..=2000).contains(&self.maximum_delay_ms) ||
            !matches!(self.layout.as_str(), "single" | "composite") {
            return Err(ProtocolError::InvalidFrame);
        }
        let fence = self.fence()?;
        if matches!(fence.kind, MediaKind::Audio | MediaKind::SystemAudio) {
            if !matches!(fence.codec, Codec::Opus | Codec::Pcm16) || self.sample_rate != 48_000 ||
                !matches!(self.channels, 1 | 2) || self.width != 0 || self.height != 0 || self.fps != 0 {
                return Err(ProtocolError::InvalidFrame);
            }
        } else if !matches!(fence.codec, Codec::H264 | Codec::Vp8) || self.sample_rate != 0 ||
            self.channels != 0 || self.width == 0 || self.width > 8192 || self.height == 0 ||
            self.height > 8192 || !(1..=120).contains(&self.fps) {
            return Err(ProtocolError::InvalidFrame);
        }
        Ok(())
    }

    fn fence(&self) -> Result<MediaFence, ProtocolError> {
        let kind = match self.kind {
            1 => MediaKind::Audio, 2 => MediaKind::Camera, 3 => MediaKind::Screen,
            4 => MediaKind::SystemAudio, _ => return Err(ProtocolError::InvalidFrame),
        };
        let codec = match self.codec {
            1 => Codec::Opus, 2 => Codec::H264, 3 => Codec::Vp8, 4 => Codec::Pcm16,
            _ => return Err(ProtocolError::InvalidFrame),
        };
        Ok(MediaFence { generation: self.media_generation, authorization_epoch: self.authorization_epoch,
            source_id: self.source_id, kind, codec })
    }

    pub fn check(&self, header: &MediaHeader, now_ms: u64) -> Result<(), ProtocolError> {
        if now_ms >= self.expires_at_ms { return Err(ProtocolError::Revoked); }
        self.fence()?.check(header)?;
        if header.channels != self.channels || header.width > self.width || header.height > self.height ||
            (matches!(header.kind, MediaKind::Audio | MediaKind::SystemAudio) && header.duration_us != 20_000) ||
            (self.fps > 0 && u64::from(header.duration_us) + 2000 < 1_000_000 / u64::from(self.fps)) {
            return Err(ProtocolError::ScopeDenied);
        }
        Ok(())
    }

    pub fn transport_budget(&self, now_ms: u64) -> Duration {
        Duration::from_millis(u64::from(self.maximum_delay_ms).min(self.expires_at_ms.saturating_sub(now_ms)))
    }
}

pub struct SourceTicket { pub lease: SourceLease, canceled: watch::Receiver<bool> }
impl SourceTicket {
    pub async fn cancelled(&mut self) {
        if !*self.canceled.borrow() { let _ = self.canceled.changed().await; }
    }
    pub fn is_cancelled(&self) -> bool { *self.canceled.borrow() }
}
struct Source { floor: u64, lease: Option<SourceLease>, sequences: SequenceWindow, canceled: watch::Sender<bool> }
#[derive(Default)]
pub struct Sources { sources: HashMap<(bool, u64), Source> }

impl Sources {
    /// Only a trusted local call/consent owner invokes this method. It is never
    /// decoded from media packets or automatically inferred from session scope.
    pub fn approve(&mut self, lease: SourceLease, inbound: bool, principal: &Principal, now_ms: u64)
        -> Result<(), ProtocolError> {
        lease.validate(principal, now_ms)?;
        let key = (inbound, lease.source_id);
        if let Some(source) = self.sources.get(&key) {
            if source.lease.as_ref() == Some(&lease) { return Ok(()); }
            if lease.media_generation <= source.floor { return Err(ProtocolError::StaleGeneration); }
        } else if self.sources.len() >= MAX_SOURCE_FLOORS { return Err(ProtocolError::FrameTooLarge); }
        if !self.sources.get(&key).is_some_and(|source| source.lease.is_some()) &&
            self.sources.values().filter(|source| source.lease.as_ref().is_some_and(|lease| lease.expires_at_ms > now_ms)).count() >= MAX_ACTIVE_SOURCES {
            return Err(ProtocolError::FrameTooLarge);
        }
        if let Some(old) = self.sources.get(&key) { old.canceled.send_replace(true); }
        let (canceled, _) = watch::channel(false);
        self.sources.insert(key, Source { floor: lease.media_generation, lease: Some(lease), sequences: SequenceWindow::default(), canceled });
        Ok(())
    }

    pub fn lease(&self, source_id: u64, inbound: bool, now_ms: u64) -> Result<&SourceLease, ProtocolError> {
        let lease = self.sources.get(&(inbound, source_id)).and_then(|source| source.lease.as_ref())
            .ok_or(ProtocolError::ScopeDenied)?;
        if now_ms >= lease.expires_at_ms { return Err(ProtocolError::Revoked); }
        Ok(lease)
    }

    pub fn check(&self, header: &MediaHeader, inbound: bool, now_ms: u64) -> Result<&SourceLease, ProtocolError> {
        let lease = self.lease(header.source_id, inbound, now_ms)?;
        lease.check(header, now_ms)?;
        Ok(lease)
    }

    pub fn ticket(&self, source_id: u64, inbound: bool, now_ms: u64) -> Result<SourceTicket, ProtocolError> {
        let lease = self.lease(source_id, inbound, now_ms)?.clone();
        Ok(SourceTicket { lease, canceled: self.sources[&(inbound, source_id)].canceled.subscribe() })
    }

    pub fn retired(&self, source_id: u64, inbound: bool, now_ms: u64) -> bool {
        self.sources.get(&(inbound, source_id)).is_some_and(|source|
            source.lease.as_ref().is_none_or(|lease| lease.expires_at_ms <= now_ms))
    }

    /// Completion of source sequence N never waits for N-1. Expired/reset
    /// streams intentionally leave gaps, and the bounded window deduplicates.
    pub fn accept(&mut self, header: &MediaHeader, inbound: bool, now_ms: u64) -> Result<bool, ProtocolError> {
        self.check(header, inbound, now_ms)?;
        Ok(self.sources.get_mut(&(inbound, header.source_id)).unwrap().sequences.accept(header.sequence))
    }

    pub fn revoke(&mut self, source_id: u64, inbound: bool) {
        if let Some(source) = self.sources.get_mut(&(inbound, source_id)) {
            source.lease = None; source.canceled.send_replace(true);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    pub fn principal() -> Principal {
        Principal { endpoint_id: "synthetic-endpoint".into(), device_id: "synthetic-device".into(), owner_id: "synthetic-owner".into(),
            conversation_id: "synthetic-conversation".into(), generation: 7, authorization_epoch: 3,
            expires_at_ms: 10_000, scopes: vec!["media".into()] }
    }
    pub fn lease() -> SourceLease {
        SourceLease { lease_id: "synthetic-lease".into(), call_id: "synthetic-call".into(), participant_id: "synthetic-participant".into(),
            target_id: "synthetic-target".into(), source_id: 9, media_generation: 2, authorization_epoch: 3,
            expires_at_ms: 9000, kind: 1, codec: 4, sample_rate: 48_000, channels: 1,
            width: 0, height: 0, fps: 0, layout: "single".into(), maximum_delay_ms: 200 }
    }
    fn frame(sequence: u64) -> MediaHeader {
        MediaHeader { kind: MediaKind::Audio, codec: Codec::Pcm16, keyframe: false,
            generation: 2, authorization_epoch: 3, source_id: 9, sequence, timestamp_us: sequence * 20_000,
            duration_us: 20_000, length: 1920, width: 0, height: 0, channels: 1 }
    }
    #[test]
    fn media_sources_need_explicit_consent_and_reject_cross_source_direction_epoch_profile() {
        let mut sources = Sources::default();
        assert!(sources.check(&frame(0), true, 1000).is_err());
        sources.approve(lease(), true, &principal(), 1000).unwrap();
        assert!(sources.check(&frame(0), false, 1000).is_err());
        assert!(sources.check(&MediaHeader { authorization_epoch: 4, ..frame(0) }, true, 1000).is_err());
        assert!(sources.check(&MediaHeader { source_id: 10, ..frame(0) }, true, 1000).is_err());
        assert!(sources.check(&MediaHeader { channels: 2, length: 3840, ..frame(0) }, true, 1000).is_err());
        assert!(sources.check(&MediaHeader { duration_us: 40_000, length: 3840, ..frame(0) }, true, 1000).is_err());
        assert!(sources.check(&frame(0), true, 9000).is_err());
    }
    #[test]
    fn media_missing_or_stalled_predecessor_does_not_block_independent_completion() {
        let mut sources = Sources::default(); sources.approve(lease(), true, &principal(), 1000).unwrap();
        assert!(sources.accept(&frame(100), true, 1000).unwrap());
        assert!(sources.accept(&frame(98), true, 1000).unwrap());
        assert!(!sources.accept(&frame(98), true, 1000).unwrap());
        assert!(!sources.accept(&frame(0), true, 1000).unwrap());
        assert!(sources.accept(&frame(102), true, 1000).unwrap());
    }
    #[test]
    fn media_revocation_requires_explicit_new_generation_and_bounded_source_floors() {
        let mut sources = Sources::default(); sources.approve(lease(), true, &principal(), 1000).unwrap();
        sources.revoke(9, true); assert!(sources.accept(&frame(0), true, 1000).is_err());
        assert!(sources.approve(lease(), true, &principal(), 1000).is_err());
        let mut replacement = lease(); replacement.media_generation += 1;
        sources.approve(replacement, true, &principal(), 1000).unwrap();
        assert!(sources.accept(&frame(0), true, 1000).is_err());
        assert!(sources.accept(&MediaHeader { generation: 3, ..frame(0) }, true, 1000).unwrap());
        for id in 1..MAX_SOURCE_FLOORS as u64 {
            let mut item = lease(); item.source_id = 100 + id;
            sources.approve(item, true, &principal(), 1000).unwrap(); sources.revoke(100 + id, true);
        }
        let mut overflow = lease(); overflow.source_id = 9999;
        assert!(sources.approve(overflow, true, &principal(), 1000).is_err());
    }
    #[test]
    fn media_source_admission_preserves_session_expiry_scope_and_active_bounds() {
        let mut sources = Sources::default();
        let mut denied = principal(); denied.scopes.clear(); assert!(sources.approve(lease(), true, &denied, 1000).is_err());
        let mut item = lease(); item.expires_at_ms = 10_001; assert!(sources.approve(item, true, &principal(), 1000).is_err());
        for id in 1..=MAX_ACTIVE_SOURCES as u64 { let mut item = lease(); item.source_id = id; sources.approve(item, true, &principal(), 1000).unwrap(); }
        let mut item = lease(); item.source_id = 100; assert!(sources.approve(item.clone(), true, &principal(), 1000).is_err());
        sources.revoke(1, true); sources.approve(item, true, &principal(), 1000).unwrap();
        assert_eq!(lease().transport_budget(8995), Duration::from_millis(5));
    }
    #[tokio::test]
    async fn media_revocation_and_replacement_wake_inflight_source_owners() {
        let mut sources = Sources::default(); sources.approve(lease(), true, &principal(), 1000).unwrap();
        let mut owned = sources.ticket(9, true, 1000).unwrap();
        sources.revoke(9, true);
        tokio::time::timeout(Duration::from_millis(50), owned.cancelled()).await.unwrap();
        assert!(owned.is_cancelled());
        let mut next = lease(); next.media_generation += 1;
        sources.approve(next.clone(), true, &principal(), 1000).unwrap();
        let mut owned = sources.ticket(9, true, 1000).unwrap(); next.media_generation += 1;
        sources.approve(next, true, &principal(), 1000).unwrap();
        tokio::time::timeout(Duration::from_millis(50), owned.cancelled()).await.unwrap();
        assert!(owned.is_cancelled());
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Shared bounded playout and feedback. Capture, codecs and device clocks stay
//! native; every source in a call/participant uses the same monotonic timeline.

use std::collections::{BTreeMap, HashMap};
use autoyou_protocol::{Principal, ProtocolError, media::MediaHeader};
use crate::media::{SourceLease, Sources};

pub const MAX_PLAYOUT_BYTES: usize = 16 * 1024 * 1024;
pub const MAX_PLAYOUT_FRAMES: usize = 128;
const RESERVED_AUDIO_FRAMES: usize = 16;
const RESERVED_AUDIO_BYTES: usize = 1024 * 1024;
const MAX_SOURCE_FRAMES: usize = 64;
type Result<T> = std::result::Result<T, ProtocolError>;
type ClockKey = (String, String);

pub struct EncodedFrame { pub header: MediaHeader, pub data: Vec<u8> }
pub struct PlayoutFrame {
    pub frame: EncodedFrame,
    pub presentation_us: u64,
    pub expires_at_us: u64,
    /// Renderer PLC only; never synthesize missing speech into STT/recording.
    pub missing_audio_frames: u8,
    pub reset_decoder: bool,
    pub rate_adjustment_ppm: i32,
}
#[derive(Debug, Clone, Default)]
pub struct Feedback {
    pub received: u64, pub played: u64, pub late: u64, pub overflow: u64,
    pub missing: u64, pub duplicates: u64, pub buffered_bytes: u64,
    pub buffered_frames: u32, pub jitter_us: u64, pub target_delay_us: u64,
    pub keyframe_required: bool, pub rate_adjustment_ppm: i32,
    pub last_played_sequence: Option<u64>,
    /// A recommendation within the native adapter's existing quality profile.
    pub quality_scale_permille: u16,
}
struct Pending { frame: EncodedFrame, queued_at_us: u64 }
struct Source {
    lease: SourceLease, queue: BTreeMap<u64, Pending>, feedback: Feedback,
    last_arrival: Option<(u64, u64, u64)>, last_played: Option<(u64, u64)>,
    geometry: Option<(u16, u16)>, device_adjustment_ppm: i32,
    quality_updated_us: u64,
}
impl Source {
    fn new(lease: SourceLease) -> Self {
        let video = lease.kind == 2 || lease.kind == 3;
        Self { feedback: Feedback { target_delay_us: 60_000.min(u64::from(lease.maximum_delay_ms)*1000),
                keyframe_required: video, quality_scale_permille: 1000, ..Feedback::default() },
            lease, queue: BTreeMap::new(), last_arrival: None, last_played: None, geometry: None,
            device_adjustment_ppm: 0, quality_updated_us: 0 }
    }
    fn clock_key(&self) -> ClockKey { (self.lease.call_id.clone(), self.lease.participant_id.clone()) }
    fn video(&self) -> bool { self.lease.kind == 2 || self.lease.kind == 3 }
    fn damaged(&mut self, now_us: u64) {
        if self.video() { self.feedback.keyframe_required = true; }
        if now_us.saturating_sub(self.quality_updated_us) >= 1_000_000 {
            self.feedback.quality_scale_permille = self.feedback.quality_scale_permille.saturating_sub(100).max(250);
            self.quality_updated_us = now_us;
        }
    }
    fn remove(&mut self, sequence: u64) -> Pending {
        let pending = self.queue.remove(&sequence).unwrap();
        self.feedback.buffered_bytes -= pending.frame.data.len() as u64;
        self.feedback.buffered_frames -= 1;
        pending
    }
}
struct Clock {
    remote_us: u64, local_us: u64, drift_ppm: i32,
    delay_us: u64,
    last_observation: (u64, u64),
}
impl Clock {
    fn new(remote_us: u64, local_us: u64) -> Self {
        Self { remote_us, local_us, drift_ppm: 0, delay_us: 60_000, last_observation: (remote_us, local_us) }
    }
    fn presentation(&self, remote_us: u64, delay_us: u64) -> u64 {
        let delta = i128::from(remote_us)-i128::from(self.remote_us);
        let local = i128::from(self.local_us)+delta*(1_000_000+i128::from(self.drift_ppm))/1_000_000+i128::from(delay_us);
        local.clamp(0, i128::from(u64::MAX)) as u64
    }
    fn observe(&mut self, remote_us: u64, local_us: u64) {
        if remote_us <= self.last_observation.0 || local_us < self.last_observation.1 { return; }
        let remote_delta = remote_us-self.last_observation.0;
        if remote_delta < 500_000 { return; }
        let local_delta = local_us-self.last_observation.1;
        // Ignore path/jitter outliers. Native playback feedback independently
        // corrects its hardware queue; this estimate alone is not device drift.
        let difference = i128::from(local_delta)-i128::from(remote_delta);
        if difference.abs() <= 2000 {
            let estimate = (difference*1_000_000/i128::from(remote_delta)).clamp(-500, 500) as i32;
            self.drift_ppm = ((7*self.drift_ppm+estimate)/8).clamp(-500, 500);
        }
        self.last_observation = (remote_us, local_us);
    }
}

pub struct Playout {
    principal: Principal, permissions: Sources, sources: HashMap<u64, Source>,
    clocks: HashMap<ClockKey, Clock>, bytes: usize, frames: usize,
    last_local_us: Option<u64>, closed: bool,
}
impl Playout {
    pub fn new(principal: Principal, now_ms: u64) -> Result<Self> {
        principal.validate()?;
        if principal.expires_at_ms <= now_ms || !principal.scopes.iter().any(|scope| scope == "media") {
            return Err(ProtocolError::Revoked);
        }
        Ok(Self { principal, permissions: Sources::default(), sources: HashMap::new(), clocks: HashMap::new(),
            bytes: 0, frames: 0, last_local_us: None, closed: false })
    }
    fn open(&self, now_ms: u64) -> Result<()> {
        if self.closed || now_ms >= self.principal.expires_at_ms { Err(ProtocolError::Revoked) } else { Ok(()) }
    }
    fn clock_tick(&mut self, local_us: u64) -> Result<()> {
        if self.last_local_us.is_some_and(|last| local_us < last) { return Err(ProtocolError::InvalidFrame); }
        self.last_local_us = Some(local_us); Ok(())
    }
    pub fn approve(&mut self, lease: SourceLease, now_ms: u64) -> Result<()> {
        self.open(now_ms)?;
        self.expire(now_ms);
        self.permissions.approve(lease.clone(), true, &self.principal, now_ms)?;
        if self.sources.get(&lease.source_id).is_some_and(|source| source.lease == lease) { return Ok(()); }
        self.remove_source(lease.source_id);
        let key = (lease.call_id.clone(),lease.participant_id.clone());
        self.sources.insert(lease.source_id, Source::new(lease)); self.refresh_delay(&key); Ok(())
    }
    fn remove_source(&mut self, source_id: u64) {
        if let Some(source) = self.sources.remove(&source_id) {
            self.bytes -= source.feedback.buffered_bytes as usize; self.frames -= source.queue.len();
            let key = source.clock_key();
            if !self.sources.values().any(|item| item.clock_key() == key) { self.clocks.remove(&key); }
            else { self.refresh_delay(&key); }
        }
    }
    pub fn revoke(&mut self, source_id: u64) {
        self.permissions.revoke(source_id, true); self.remove_source(source_id);
    }
    fn expire(&mut self, now_ms: u64) {
        let expired: Vec<_> = self.sources.iter().filter(|(_,source)| source.lease.expires_at_ms <= now_ms)
            .map(|(id,_)| *id).collect();
        for id in expired { self.revoke(id); }
    }
    fn discard(&mut self, source_id: u64, sequence: u64, overflow: bool, now_us: u64) {
        let source = self.sources.get_mut(&source_id).unwrap();
        let pending = source.remove(sequence); self.bytes -= pending.frame.data.len(); self.frames -= 1;
        if overflow { source.feedback.overflow += 1; } else { source.feedback.late += 1; }
        source.damaged(now_us);
    }
    fn refresh_delay(&mut self, key: &ClockKey) {
        let mut delay = 0; let mut maximum = 200_000;
        for source in self.sources.values().filter(|source| source.clock_key() == *key) {
            delay = delay.max(source.feedback.target_delay_us);
            maximum = maximum.min(u64::from(source.lease.maximum_delay_ms)*1000);
        }
        if let Some(clock) = self.clocks.get_mut(key) { clock.delay_us = delay.min(maximum); }
    }
    fn make_room(&mut self, source_id: u64, bytes: usize, now_us: u64) -> bool {
        let video = self.sources[&source_id].video();
        let byte_limit = MAX_PLAYOUT_BYTES-if video { RESERVED_AUDIO_BYTES } else { 0 };
        let frame_limit = MAX_PLAYOUT_FRAMES-if video { RESERVED_AUDIO_FRAMES } else { 0 };
        while self.bytes+bytes > byte_limit || self.frames >= frame_limit || self.sources[&source_id].queue.len() >= MAX_SOURCE_FRAMES {
            let candidate = if self.sources[&source_id].queue.len() >= MAX_SOURCE_FRAMES {
                self.sources[&source_id].queue.first_key_value().map(|(sequence,pending)| (source_id, *sequence, pending.queued_at_us))
            } else {
                self.sources.iter().filter(|(_,source)| source.video() || !video)
                    .filter_map(|(id,source)| source.queue.first_key_value().map(|(sequence,pending)| (*id,*sequence,pending.queued_at_us)))
                    .min_by_key(|(id,seq,at)| (!self.sources[id].video(),*at,*id,*seq))
            };
            let Some((id,sequence,_)) = candidate else { return false; };
            self.discard(id,sequence,true,now_us);
        }
        true
    }
    /// The host has already authenticated the connection. Recheck its separate
    /// generation and local source consent before a packet enters native codecs.
    pub fn push(&mut self, connection_generation: u64, frame: EncodedFrame, arrival_us: u64, now_ms: u64) -> Result<bool> {
        self.push_at(connection_generation,frame,arrival_us,arrival_us,now_ms)
    }
    /// Foreign queues retain the original trusted receive clock. The current
    /// local clock may advance while those queues wait; do not re-anchor a late
    /// first packet or mistake its older receive time for a clock rollback.
    pub fn push_at(&mut self, connection_generation: u64, frame: EncodedFrame,
        arrival_us: u64, local_us: u64, now_ms: u64) -> Result<bool> {
        self.open(now_ms)?; self.clock_tick(local_us)?; self.expire(now_ms);
        if arrival_us > local_us { return Err(ProtocolError::InvalidFrame); }
        if connection_generation != self.principal.generation { return Err(ProtocolError::StaleGeneration); }
        self.permissions.check(&frame.header,true,now_ms)?;
        autoyou_protocol::media_codec::validate(&frame.header,&frame.data)?;
        let id = frame.header.source_id;
        let source = &self.sources[&id];
        let header = &frame.header;
        if header.timestamp_us > i64::MAX as u64 || local_us > i64::MAX as u64 ||
            source.queue.range(..header.sequence).next_back().is_some_and(|(_,previous)| previous.frame.header.timestamp_us >= header.timestamp_us) ||
            header.sequence.checked_add(1).and_then(|next|source.queue.range(next..).next())
                .is_some_and(|(_,next)| next.frame.header.timestamp_us <= header.timestamp_us) {
            return Err(ProtocolError::InvalidFrame);
        }
        if !self.permissions.accept(header,true,now_ms)? { self.sources.get_mut(&id).unwrap().feedback.duplicates += 1; return Ok(false); }
        if local_us.saturating_sub(arrival_us) >= u64::from(source.lease.maximum_delay_ms)*1000 {
            let source = self.sources.get_mut(&id).unwrap();
            source.feedback.late += 1; source.damaged(local_us); return Ok(false);
        }
        let key = source.clock_key();
        let clock = self.clocks.entry(key.clone()).or_insert_with(|| Clock::new(header.timestamp_us,arrival_us));
        let presentation = clock.presentation(header.timestamp_us,source.feedback.target_delay_us);
        if presentation > arrival_us.saturating_add(u64::from(source.lease.maximum_delay_ms)*1000+200_000) {
            return Err(ProtocolError::InvalidFrame);
        }
        let source = self.sources.get_mut(&id).unwrap(); source.feedback.received += 1;
        if source.last_played.is_some_and(|(sequence,stamp)| header.sequence <= sequence || header.timestamp_us <= stamp) {
            source.feedback.late += 1; return Ok(false);
        }
        if local_us > presentation.saturating_add(if source.video() { 200_000 } else { 60_000 }) {
            source.feedback.late += 1; source.damaged(local_us); return Ok(false);
        }
        if source.last_arrival.is_none_or(|(seq,_,_)| header.sequence > seq) {
            if let Some((_,previous_stamp,previous_arrival)) = source.last_arrival {
                let transit = (i128::from(arrival_us)-i128::from(previous_arrival))-
                    (i128::from(header.timestamp_us)-i128::from(previous_stamp));
                let jitter = transit.abs().min(1_000_000) as u64;
                source.feedback.jitter_us = (15*source.feedback.jitter_us+jitter)/16;
                source.feedback.target_delay_us = (40_000+4*source.feedback.jitter_us).min(200_000)
                    .min(u64::from(source.lease.maximum_delay_ms)*1000);
            }
            source.last_arrival = Some((header.sequence,header.timestamp_us,arrival_us));
            clock.observe(header.timestamp_us,arrival_us);
        }
        self.refresh_delay(&key);
        if !self.make_room(id,frame.data.len(),arrival_us) {
            let source = self.sources.get_mut(&id).unwrap(); source.feedback.overflow += 1;
            source.damaged(arrival_us); return Ok(false);
        }
        let source = self.sources.get_mut(&id).unwrap();
        source.feedback.buffered_bytes += frame.data.len() as u64; source.feedback.buffered_frames += 1;
        self.bytes += frame.data.len(); self.frames += 1;
        source.queue.insert(header.sequence,Pending { frame, queued_at_us: arrival_us }); Ok(true)
    }
    pub fn take(&mut self, maximum: u32, local_us: u64, now_ms: u64) -> Result<Vec<PlayoutFrame>> {
        self.open(now_ms)?; self.clock_tick(local_us)?; self.expire(now_ms);
        if maximum == 0 || maximum > 16 { return Err(ProtocolError::InvalidFrame); }
        let mut output = Vec::new();
        while output.len() < maximum as usize {
            let candidate = self.sources.iter().filter_map(|(id,source)| {
                let (sequence,pending) = source.queue.first_key_value()?;
                let clock = &self.clocks[&source.clock_key()];
                let due = clock.presentation(pending.frame.header.timestamp_us,clock.delay_us);
                (due <= local_us).then_some((*id,*sequence,due))
            }).min_by_key(|(id,sequence,due)| (*due,*id,*sequence));
            let Some((id,sequence,presentation_us)) = candidate else { break; };
            let source = &self.sources[&id];
            if local_us > presentation_us.saturating_add(if source.video() { 200_000 } else { 60_000 }) ||
                local_us.saturating_sub(source.queue[&sequence].queued_at_us) > u64::from(source.lease.maximum_delay_ms)*1000 {
                self.discard(id,sequence,false,local_us); continue;
            }
            let source = self.sources.get_mut(&id).unwrap();
            let pending = source.remove(sequence); self.bytes -= pending.frame.data.len(); self.frames -= 1;
            let header = &pending.frame.header;
            let missing = source.last_played.map_or(0,|(previous,_)| sequence.saturating_sub(previous).saturating_sub(1));
            source.feedback.missing = source.feedback.missing.saturating_add(missing);
            let reset_decoder;
            if source.video() {
                let geometry = (header.width,header.height);
                if missing > 0 || source.geometry.is_some_and(|old| old != geometry) { source.feedback.keyframe_required = true; }
                reset_decoder = source.feedback.keyframe_required || source.geometry != Some(geometry);
                source.last_played = Some((sequence,header.timestamp_us));
                if source.feedback.keyframe_required && !header.keyframe {
                    source.damaged(local_us); continue;
                }
                source.feedback.keyframe_required = false; source.geometry = Some(geometry);
            } else {
                reset_decoder = source.last_played.is_none() || missing > 5;
                source.last_played = Some((sequence,header.timestamp_us));
            }
            source.feedback.played += 1;
            if missing == 0 && local_us.saturating_sub(source.quality_updated_us) >= 5_000_000 {
                source.feedback.quality_scale_permille = (source.feedback.quality_scale_permille+20).min(1000);
                source.quality_updated_us = local_us;
            }
            let clock = &self.clocks[&source.clock_key()];
            let rate_adjustment_ppm = (clock.drift_ppm+source.device_adjustment_ppm).clamp(-500,500);
            source.feedback.rate_adjustment_ppm = rate_adjustment_ppm;
            output.push(PlayoutFrame { frame: pending.frame, presentation_us,
                expires_at_us: pending.queued_at_us.saturating_add(u64::from(source.lease.maximum_delay_ms)*1000),
                missing_audio_frames: if source.video() { 0 } else { missing.min(5) as u8 }, reset_decoder, rate_adjustment_ppm });
        }
        Ok(output)
    }
    /// The audio renderer supplies its actual bounded device queue duration.
    /// A positive recommendation consumes samples faster when that queue grows.
    pub fn audio_device_feedback(&mut self, source_id: u64, queued_duration_us: u64, now_ms: u64) -> Result<()> {
        self.open(now_ms)?; self.expire(now_ms);
        let source = self.sources.get_mut(&source_id).ok_or(ProtocolError::ScopeDenied)?;
        if source.video() || queued_duration_us > 1_000_000 { return Err(ProtocolError::InvalidFrame); }
        let difference = i128::from(queued_duration_us)-i128::from(source.feedback.target_delay_us);
        let adjustment = (difference/40).clamp(-500,500) as i32;
        source.device_adjustment_ppm = ((7*source.device_adjustment_ppm+adjustment)/8).clamp(-500,500);
        Ok(())
    }
    pub fn decoder_discontinuity(&mut self, source_id: u64, now_ms: u64) -> Result<()> {
        self.open(now_ms)?; self.expire(now_ms);
        let source = self.sources.get_mut(&source_id).ok_or(ProtocolError::ScopeDenied)?;
        self.bytes -= source.feedback.buffered_bytes as usize; self.frames -= source.queue.len();
        source.feedback.overflow = source.feedback.overflow.saturating_add(source.queue.len() as u64);
        source.queue.clear(); source.feedback.buffered_bytes = 0; source.feedback.buffered_frames = 0;
        if source.video() { source.feedback.keyframe_required = true; source.geometry = None; }
        source.last_played = None;
        Ok(())
    }
    pub fn feedback(&mut self, source_id: u64, now_ms: u64) -> Result<Feedback> {
        self.open(now_ms)?; self.expire(now_ms);
        let source = self.sources.get(&source_id).ok_or(ProtocolError::ScopeDenied)?;
        let mut result = source.feedback.clone();
        result.last_played_sequence = source.last_played.map(|(sequence,_)|sequence);
        if let Some(clock) = self.clocks.get(&source.clock_key()) { result.target_delay_us = clock.delay_us; }
        result.rate_adjustment_ppm = (self.clocks.get(&source.clock_key()).map_or(0,|clock|clock.drift_ppm)+source.device_adjustment_ppm).clamp(-500,500);
        Ok(result)
    }
    pub fn buffered_bytes(&self) -> usize { self.bytes }
    pub fn close(&mut self) {
        self.closed = true; self.sources.clear(); self.clocks.clear(); self.permissions = Sources::default(); self.bytes = 0; self.frames = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use autoyou_protocol::media::{Codec, MediaKind};
    fn principal() -> Principal {
        Principal { endpoint_id: "synthetic-endpoint".into(), device_id: "synthetic-device".into(), owner_id: "synthetic-owner".into(),
            conversation_id: "synthetic-conversation".into(), generation: 7, authorization_epoch: 3,
            expires_at_ms: 100_000, scopes: vec!["media".into()] }
    }
    fn lease(id: u64, video: bool) -> SourceLease {
        SourceLease { lease_id: format!("synthetic-lease-{id}"), call_id: "synthetic-call".into(), participant_id: "synthetic-participant".into(),
            target_id: "synthetic-target".into(), source_id: id, media_generation: 2, authorization_epoch: 3,
            expires_at_ms: 90_000, kind: if video { 2 } else { 1 }, codec: if video { 3 } else { 4 },
            sample_rate: if video { 0 } else { 48_000 }, channels: if video { 0 } else { 1 },
            width: if video { 160 } else { 0 }, height: if video { 96 } else { 0 }, fps: if video { 30 } else { 0 },
            layout: "single".into(), maximum_delay_ms: 200 }
    }
    fn audio(sequence: u64) -> EncodedFrame {
        EncodedFrame { header: MediaHeader { kind: MediaKind::Audio, codec: Codec::Pcm16, keyframe: false,
            generation: 2, authorization_epoch: 3, source_id: 1, sequence, timestamp_us: sequence*20_000,
            duration_us: 20_000, length: 1920, width: 0, height: 0, channels: 1 }, data: vec![0;1920] }
    }
    fn video(sequence: u64, keyframe: bool) -> EncodedFrame {
        // Synthetic VP8 uncompressed header; no native decoding in this fixture.
        let data = if keyframe { vec![0x30,0,0,0x9d,1,0x2a,160,0,96,0,7] } else { vec![0x31,0,0,7] };
        EncodedFrame { header: MediaHeader { kind: MediaKind::Camera, codec: Codec::Vp8, keyframe,
            generation: 2, authorization_epoch: 3, source_id: 2, sequence, timestamp_us: sequence*33_333,
            duration_us: 33_333, length: data.len(), width: 160, height: 96, channels: 0 }, data }
    }
    #[test]
    fn playout_reorders_without_waiting_forever_and_reports_renderer_only_concealment() {
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(1,false),1000).unwrap();
        assert!(playout.push(7,audio(0),1_000_000,1000).unwrap());
        assert!(playout.push(7,audio(2),1_030_000,1030).unwrap());
        assert!(playout.push(7,audio(1),1_035_000,1035).unwrap());
        assert!(!playout.push(7,audio(1),1_035_000,1035).unwrap());
        let frames = playout.take(16,1_100_000,1100).unwrap();
        assert_eq!(frames.iter().map(|frame|frame.frame.header.sequence).collect::<Vec<_>>(),vec![0,1,2]);
        assert!(frames.iter().all(|frame|frame.missing_audio_frames == 0));
        playout.push(7,audio(5),1_125_000,1125).unwrap();
        let frames = playout.take(16,1_160_000,1160).unwrap(); assert_eq!(frames[0].missing_audio_frames,2);
        assert_eq!(playout.buffered_bytes(),0);
        assert!(!playout.push(7,audio(4),1_161_000,1161).unwrap());
    }
    #[test]
    fn playout_requires_video_keyframe_after_gap_or_geometry_change() {
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(2,true),1000).unwrap();
        playout.push(7,video(0,false),1_000_000,1000).unwrap();
        assert!(playout.take(16,1_060_000,1060).unwrap().is_empty());
        assert!(playout.feedback(2,1060).unwrap().keyframe_required);
        playout.push(7,video(1,true),1_061_000,1061).unwrap();
        let played = playout.take(16,1_100_000,1100).unwrap(); assert!(played[0].reset_decoder);
        playout.push(7,video(3,false),1_110_000,1110).unwrap();
        assert!(playout.take(16,1_170_000,1170).unwrap().is_empty());
        playout.push(7,video(4,true),1_171_000,1171).unwrap();
        let played = playout.take(16,1_210_000,1210).unwrap(); assert!(played[0].reset_decoder);
        let mut wrong = video(5,false); wrong.header.width = 144;
        playout.push(7,wrong,1_220_000,1220).unwrap();
        assert!(playout.take(16,1_270_000,1270).unwrap().is_empty());
    }
    #[test]
    fn playout_audio_and_video_share_monotonic_call_clock_and_fence_generations() {
        let mut playout = Playout::new(principal(),1000).unwrap();
        playout.approve(lease(1,false),1000).unwrap(); playout.approve(lease(2,true),1000).unwrap();
        playout.push(7,audio(0),1_000_000,1000).unwrap();
        playout.push(7,video(0,true),1_020_000,1020).unwrap();
        let played = playout.take(16,1_060_000,1060).unwrap();
        assert_eq!(played[0].presentation_us,played[1].presentation_us);
        assert!(playout.push(6,audio(1),1_061_000,1061).is_err());
        playout.revoke(1); assert!(playout.push(7,audio(1),1_062_000,1062).is_err());
        assert!(playout.approve(lease(1,false),1062).is_err());
        let mut replacement=lease(1,false); replacement.media_generation=3;
        playout.approve(replacement,1062).unwrap(); assert!(playout.push(7,audio(1),1_063_000,1063).is_err());
        assert!(playout.take(16,1_000_000,1063).is_err());
    }
    #[test]
    fn playout_bounds_future_timestamps_queue_pressure_expiry_and_close() {
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(1,false),1000).unwrap();
        playout.push(7,audio(0),1_000_000,1000).unwrap();
        let mut forged=audio(1); forged.header.timestamp_us=1_000_000_000;
        assert!(playout.push(7,forged,1_001_000,1001).is_err());
        for sequence in 2..120 { playout.push(7,audio(sequence),1_000_000+sequence*20_000,1000+sequence*20).unwrap(); }
        let feedback=playout.feedback(1,3400).unwrap();
        assert!(feedback.buffered_frames <= 64 && feedback.buffered_bytes <= MAX_PLAYOUT_BYTES as u64 && feedback.overflow > 0);
        assert!(playout.take(16,4_000_000,4000).unwrap().is_empty());
        assert_eq!(playout.buffered_bytes(),0);
        playout.approve(lease(2,true),4000).unwrap();
        assert!(playout.take(16,90_000_000,90_000).unwrap().is_empty());
        assert!(playout.feedback(2,90_000).is_err());
        playout.close(); assert_eq!(playout.buffered_bytes(),0); assert!(playout.take(1,90_000_001,90_000).is_err());
    }
    #[test]
    fn playout_device_drift_feedback_is_bounded_and_never_authorizes_a_source() {
        let mut playout = Playout::new(principal(),1000).unwrap();
        assert!(playout.audio_device_feedback(1,60_000,1000).is_err()); playout.approve(lease(1,false),1000).unwrap();
        for _ in 0..100 { playout.audio_device_feedback(1,500_000,1000).unwrap(); }
        assert!((1..=500).contains(&playout.feedback(1,1000).unwrap().rate_adjustment_ppm));
        for _ in 0..100 { playout.audio_device_feedback(1,0,1000).unwrap(); }
        assert!((-500..=-1).contains(&playout.feedback(1,1000).unwrap().rate_adjustment_ppm));
        assert!(playout.audio_device_feedback(1,1_000_001,1000).is_err());
    }
    #[test]
    fn playout_foreign_queue_retains_original_clock_and_expiry() {
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(1,false),1000).unwrap();
        assert!(playout.take(1,1_090_000,1090).unwrap().is_empty());
        assert!(playout.push_at(7,audio(0),1_000_000,1_100_000,1100).unwrap());
        let frames = playout.take(1,1_100_000,1100).unwrap();
        assert_eq!(frames[0].presentation_us,1_060_000);
        assert_eq!(frames[0].expires_at_us,1_200_000);
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(1,false),1000).unwrap();
        assert!(!playout.push_at(7,audio(0),1_000_000,1_200_000,1200).unwrap());
        assert_eq!(playout.buffered_bytes(),0);
        assert!(playout.push_at(7,audio(1),1_210_000,1_200_000,1200).is_err());
    }
    #[test]
    fn playout_renderer_discontinuity_drops_queued_deltas_until_keyframe() {
        let mut playout = Playout::new(principal(),1000).unwrap(); playout.approve(lease(2,true),1000).unwrap();
        playout.push(7,video(0,true),1_000_000,1000).unwrap();
        playout.decoder_discontinuity(2,1000).unwrap();
        assert_eq!(playout.buffered_bytes(),0);
        assert!(playout.feedback(2,1000).unwrap().keyframe_required);
        playout.push(7,video(1,false),1_033_333,1033).unwrap();
        assert!(playout.take(1,1_093_333,1093).unwrap().is_empty());
        playout.push(7,video(3,true),1_099_999,1099).unwrap();
        let frames = playout.take(1,1_160_000,1160).unwrap();
        assert!(frames[0].reset_decoder);
        assert_eq!(playout.buffered_bytes(),0);
    }
}

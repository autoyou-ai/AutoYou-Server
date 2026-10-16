// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Shared AYMF wire encoder/decoder for native capture and codec adapters.
use autoyou_protocol::{FrameHeader, Lane, media::{Codec, MediaHeader, MediaKind, MEDIA_HEADER_BYTES}};
use crate::{BindingError, TransportFrame};
use std::sync::{Arc, Mutex};
use autoyou_session::{media::SourceLease, playout::{EncodedFrame, Playout}};
use autoyou_session::media_negotiation::{MediaConsent,MediaNegotiation as Negotiation,
    MediaAction as NegotiationAction,MediaActionKind,SourceReference,SourceFeedback,MAX_MEDIA_CONTROL_BYTES};

#[derive(Debug, Clone, uniffi::Record)]
pub struct MediaPacket {
    pub kind: u8,
    pub codec: u8,
    pub keyframe: bool,
    pub media_generation: u64,
    pub authorization_epoch: u64,
    pub source_id: u64,
    pub sequence: u64,
    pub timestamp_us: u64,
    pub duration_us: u32,
    pub width: u16,
    pub height: u16,
    pub channels: u8,
    pub data: Vec<u8>,
}
impl MediaPacket {
    pub(crate) fn header(&self) -> Result<MediaHeader, BindingError> {
        let kind = match self.kind {
            1 => MediaKind::Audio, 2 => MediaKind::Camera, 3 => MediaKind::Screen, 4 => MediaKind::SystemAudio,
            _ => return Err(BindingError::InvalidInput),
        };
        let codec = match self.codec {
            1 => Codec::Opus, 2 => Codec::H264, 3 => Codec::Vp8, 4 => Codec::Pcm16,
            _ => return Err(BindingError::InvalidInput),
        };
        let header = MediaHeader { kind, codec, keyframe: self.keyframe, generation: self.media_generation,
            authorization_epoch: self.authorization_epoch, source_id: self.source_id, sequence: self.sequence,
            timestamp_us: self.timestamp_us, duration_us: self.duration_us, length: self.data.len(),
            width: self.width, height: self.height, channels: self.channels };
        header.validate().map_err(|_| BindingError::InvalidInput)?;
        Ok(header)
    }
}

fn from_encoded(frame: EncodedFrame) -> MediaPacket {
    let header = frame.header;
    MediaPacket { kind: header.kind as u8, codec: header.codec as u8, keyframe: header.keyframe,
        media_generation: header.generation, authorization_epoch: header.authorization_epoch,
        source_id: header.source_id, sequence: header.sequence, timestamp_us: header.timestamp_us,
        duration_us: header.duration_us, width: header.width, height: header.height, channels: header.channels, data: frame.data }
}
#[derive(Debug, Clone, uniffi::Record)]
pub struct MediaPlayoutFrame {
    pub packet: MediaPacket, pub presentation_us: u64, pub expires_at_us: u64, pub missing_audio_frames: u8,
    pub reset_decoder: bool, pub rate_adjustment_ppm: i32,
}
#[derive(Debug, Clone, uniffi::Record)]
pub struct MediaFeedback {
    pub received: u64, pub played: u64, pub late: u64, pub overflow: u64,
    pub missing: u64, pub duplicates: u64, pub buffered_bytes: u64,
    pub buffered_frames: u32, pub jitter_us: u64, pub target_delay_us: u64,
    pub keyframe_required: bool, pub rate_adjustment_ppm: i32, pub quality_scale_permille: u16,
    pub last_played_sequence: Option<u64>,
}
fn media_error(error: autoyou_protocol::ProtocolError) -> BindingError {
    use autoyou_protocol::ProtocolError::*;
    match error {
        Revoked | ScopeDenied | StaleGeneration | NotAuthorized => BindingError::PermissionDenied,
        FrameTooLarge => BindingError::Backpressure,
        _ => BindingError::InvalidInput,
    }
}
fn lease_json(value: &str) -> Result<SourceLease, BindingError> {
    if value.len() > 4096 { return Err(BindingError::InvalidInput); }
    serde_json::from_str(value).map_err(|_|BindingError::InvalidInput)
}

/// One trusted local session owns one bounded playout instance. Foreign code
/// supplies native clocks and locally approved leases, never wire authority.
#[derive(uniffi::Object)]
pub struct MediaPlayout { state: Mutex<Playout> }
#[uniffi::export]
impl MediaPlayout {
    #[uniffi::constructor]
    pub fn new(grant: crate::SessionGrant, now_ms: u64) -> Result<Arc<Self>, BindingError> {
        let principal = autoyou_protocol::Principal { endpoint_id: grant.endpoint_id, device_id: grant.device_id,
            owner_id: grant.owner_id, conversation_id: grant.conversation_id, generation: grant.generation,
            authorization_epoch: grant.authorization_epoch, expires_at_ms: grant.expires_at_ms, scopes: grant.scopes };
        Ok(Arc::new(Self { state: Mutex::new(Playout::new(principal,now_ms).map_err(media_error)?) }))
    }
    pub fn approve_source(&self, source_lease_json: String, now_ms: u64) -> Result<(), BindingError> {
        let lease = lease_json(&source_lease_json)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.approve(lease,now_ms).map_err(media_error)
    }
    pub fn revoke_source(&self, source_id: u64) -> Result<(), BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.revoke(source_id); Ok(())
    }
    pub fn push(&self, frame: TransportFrame, arrival_us: u64, local_us: u64, now_ms: u64) -> Result<bool, BindingError> {
        let generation = frame.generation;
        let packet = decode_media_packet(frame)?;
        let header = packet.header()?;
        self.state.lock().map_err(|_|BindingError::Worker)?.push_at(generation,
            EncodedFrame { header, data: packet.data },arrival_us,local_us,now_ms).map_err(media_error)
    }
    pub fn take(&self, maximum: u32, local_us: u64, now_ms: u64) -> Result<Vec<MediaPlayoutFrame>, BindingError> {
        let frames = self.state.lock().map_err(|_|BindingError::Worker)?.take(maximum,local_us,now_ms).map_err(media_error)?;
        Ok(frames.into_iter().map(|frame|MediaPlayoutFrame { packet: from_encoded(frame.frame),
            presentation_us: frame.presentation_us, expires_at_us: frame.expires_at_us, missing_audio_frames: frame.missing_audio_frames,
            reset_decoder: frame.reset_decoder, rate_adjustment_ppm: frame.rate_adjustment_ppm }).collect())
    }
    pub fn audio_device_feedback(&self, source_id: u64, queued_duration_us: u64, now_ms: u64) -> Result<(), BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.audio_device_feedback(source_id,queued_duration_us,now_ms).map_err(media_error)
    }
    pub fn decoder_discontinuity(&self, source_id: u64, now_ms: u64) -> Result<(), BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.decoder_discontinuity(source_id,now_ms).map_err(media_error)
    }
    pub fn feedback(&self, source_id: u64, now_ms: u64) -> Result<MediaFeedback, BindingError> {
        let value = self.state.lock().map_err(|_|BindingError::Worker)?.feedback(source_id,now_ms).map_err(media_error)?;
        Ok(MediaFeedback { received: value.received, played: value.played, late: value.late, overflow: value.overflow,
            missing: value.missing, duplicates: value.duplicates, buffered_bytes: value.buffered_bytes,
            buffered_frames: value.buffered_frames, jitter_us: value.jitter_us, target_delay_us: value.target_delay_us,
            keyframe_required: value.keyframe_required, rate_adjustment_ppm: value.rate_adjustment_ppm,
            quality_scale_permille: value.quality_scale_permille,last_played_sequence: value.last_played_sequence })
    }
    // `close` is reserved by the generated Kotlin AutoCloseable handle owner.
    pub fn shutdown(&self) -> Result<(), BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.close(); Ok(())
    }
}
impl Drop for MediaPlayout {
    fn drop(&mut self) { self.state.get_mut().unwrap_or_else(|error|error.into_inner()).close(); }
}

#[derive(Debug,Clone,uniffi::Enum)]
pub enum MediaNegotiationActionKind { PrepareReceiver,ActivateSender,CloseSource,ApplyFeedback,Reply }
#[derive(Debug,Clone,uniffi::Record)]
pub struct MediaNegotiationAction {
    pub kind: MediaNegotiationActionKind,pub inbound: bool,pub source_lease_json: Option<String>,
    pub reply_json: Option<String>,pub feedback_json: Option<String>,
}
fn action_record(action: NegotiationAction) -> Result<MediaNegotiationAction,BindingError> {
    let kind = match action.kind {
        MediaActionKind::PrepareReceiver => MediaNegotiationActionKind::PrepareReceiver,
        MediaActionKind::ActivateSender => MediaNegotiationActionKind::ActivateSender,
        MediaActionKind::CloseSource => MediaNegotiationActionKind::CloseSource,
        MediaActionKind::ApplyFeedback => MediaNegotiationActionKind::ApplyFeedback,
        MediaActionKind::Reply => MediaNegotiationActionKind::Reply,
    };
    Ok(MediaNegotiationAction { kind,inbound: action.inbound,
        source_lease_json: action.lease.map(|lease|serde_json::to_string(&lease)).transpose().map_err(|_|BindingError::InvalidInput)?,
        reply_json: action.reply,
        feedback_json: action.feedback.map(|feedback|serde_json::to_string(&feedback)).transpose().map_err(|_|BindingError::InvalidInput)? })
}
fn control_json(json: &str) -> Result<&str,BindingError> {
    if json.len() > MAX_MEDIA_CONTROL_BYTES { return Err(BindingError::InvalidInput); }
    Ok(json)
}
/// Local call consent and source negotiation are shared across every generated
/// language. Actions still require joined native adapter acquisition/teardown.
#[derive(uniffi::Object)]
pub struct MediaNegotiation { state: Mutex<Negotiation> }
#[uniffi::export]
impl MediaNegotiation {
    #[uniffi::constructor]
    pub fn new(grant: crate::SessionGrant,local_endpoint_id: String,now_ms: u64) -> Result<Arc<Self>,BindingError> {
        let principal = autoyou_protocol::Principal { endpoint_id: grant.endpoint_id,device_id: grant.device_id,
            owner_id: grant.owner_id,conversation_id: grant.conversation_id,generation: grant.generation,
            authorization_epoch: grant.authorization_epoch,expires_at_ms: grant.expires_at_ms,scopes: grant.scopes };
        Ok(Arc::new(Self { state: Mutex::new(Negotiation::new(principal,local_endpoint_id,now_ms).map_err(media_error)?) }))
    }
    pub fn approve_call(&self,consent_json: String,now_ms: u64) -> Result<(),BindingError> {
        let consent: MediaConsent = serde_json::from_str(control_json(&consent_json)?).map_err(|_|BindingError::InvalidInput)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.approve_call(consent,now_ms).map_err(media_error)
    }
    pub fn offer_source(&self,source_lease_json: String,now_ms: u64) -> Result<String,BindingError> {
        let lease = lease_json(&source_lease_json)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.offer_source(lease,now_ms).map_err(media_error)
    }
    pub fn receive(&self,signal_json: String,now_ms: u64) -> Result<Vec<MediaNegotiationAction>,BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.receive(&signal_json,now_ms).map_err(media_error)?
            .into_iter().map(action_record).collect()
    }
    pub fn receiver_prepared(&self,source_reference_json: String,now_ms: u64) -> Result<String,BindingError> {
        let reference: SourceReference = serde_json::from_str(control_json(&source_reference_json)?).map_err(|_|BindingError::InvalidInput)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.receiver_prepared(reference,now_ms).map_err(media_error)
    }
    pub fn feedback(&self,source_reference_json: String,feedback_json: String,now_ms: u64) -> Result<String,BindingError> {
        let reference: SourceReference = serde_json::from_str(control_json(&source_reference_json)?).map_err(|_|BindingError::InvalidInput)?;
        let feedback: SourceFeedback = serde_json::from_str(control_json(&feedback_json)?).map_err(|_|BindingError::InvalidInput)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.feedback(reference,feedback,now_ms).map_err(media_error)
    }
    pub fn revoke_source(&self,source_reference_json: String,inbound: bool) -> Result<Vec<MediaNegotiationAction>,BindingError> {
        let reference: SourceReference = serde_json::from_str(control_json(&source_reference_json)?).map_err(|_|BindingError::InvalidInput)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.revoke_source(reference,inbound).map_err(media_error)?
            .into_iter().map(action_record).collect()
    }
    pub fn check_source(&self,source_reference_json: String,inbound: bool,now_ms: u64) -> Result<(),BindingError> {
        let reference: SourceReference = serde_json::from_str(control_json(&source_reference_json)?).map_err(|_|BindingError::InvalidInput)?;
        self.state.lock().map_err(|_|BindingError::Worker)?.check_source(&reference,inbound,now_ms).map_err(media_error)
    }
    pub fn end_call(&self,call_id: String) -> Result<Vec<MediaNegotiationAction>,BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.end_call(&call_id).map_err(media_error)?
            .into_iter().map(action_record).collect()
    }
    pub fn expire(&self,now_ms: u64) -> Result<Vec<MediaNegotiationAction>,BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.expire(now_ms).into_iter().map(action_record).collect()
    }
    pub fn shutdown(&self) -> Result<Vec<MediaNegotiationAction>,BindingError> {
        self.state.lock().map_err(|_|BindingError::Worker)?.close().into_iter().map(action_record).collect()
    }
}
impl Drop for MediaNegotiation {
    fn drop(&mut self) { self.state.get_mut().unwrap_or_else(|error|error.into_inner()).close(); }
}

#[uniffi::export]
pub fn encode_media_packet(connection_generation: u64, packet: MediaPacket) -> Result<TransportFrame, BindingError> {
    if connection_generation == 0 { return Err(BindingError::InvalidInput); }
    let header = packet.header()?;
    FrameHeader { lane: Lane::Media, generation: connection_generation, stream_id: packet.source_id,
        sequence: packet.sequence, length: MEDIA_HEADER_BYTES + packet.data.len() }
        .encode().map_err(|_| BindingError::InvalidInput)?;
    let mut payload = Vec::with_capacity(MEDIA_HEADER_BYTES + packet.data.len());
    payload.extend_from_slice(&header.encode().map_err(|_| BindingError::InvalidInput)?);
    payload.extend_from_slice(&packet.data);
    Ok(TransportFrame { lane: Lane::Media as u8, generation: connection_generation,
        stream_id: packet.source_id, sequence: packet.sequence, payload })
}

#[uniffi::export]
pub fn decode_media_packet(frame: TransportFrame) -> Result<MediaPacket, BindingError> {
    if frame.lane != Lane::Media as u8 || frame.generation == 0 { return Err(BindingError::InvalidInput); }
    FrameHeader { lane: Lane::Media, generation: frame.generation, stream_id: frame.stream_id,
        sequence: frame.sequence, length: frame.payload.len() }.encode().map_err(|_| BindingError::InvalidInput)?;
    let header = MediaHeader::decode(frame.payload.get(..MEDIA_HEADER_BYTES).ok_or(BindingError::InvalidInput)?)
        .map_err(|_| BindingError::InvalidInput)?;
    if header.source_id != frame.stream_id || frame.payload.len() != MEDIA_HEADER_BYTES + header.length {
        return Err(BindingError::InvalidInput);
    }
    autoyou_protocol::media_codec::validate(&header, &frame.payload[MEDIA_HEADER_BYTES..])
        .map_err(|_| BindingError::InvalidInput)?;
    Ok(MediaPacket { kind: header.kind as u8, codec: header.codec as u8, keyframe: header.keyframe,
        media_generation: header.generation, authorization_epoch: header.authorization_epoch,
        source_id: header.source_id, sequence: header.sequence, timestamp_us: header.timestamp_us,
        duration_us: header.duration_us, width: header.width, height: header.height, channels: header.channels,
        data: frame.payload[MEDIA_HEADER_BYTES..].to_vec() })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packet() -> MediaPacket {
        MediaPacket { kind: 1, codec: 4, keyframe: false, media_generation: 2, authorization_epoch: 3,
            source_id: 9, sequence: 5, timestamp_us: 100_000, duration_us: 20_000,
            width: 0, height: 0, channels: 1, data: vec![7;1920] }
    }
    #[test]
    fn media_binding_codec_preserves_distinct_connection_and_source_generations_and_bytes() {
        let frame = encode_media_packet(7, packet()).unwrap();
        assert_eq!(frame.generation, 7); assert_eq!(frame.stream_id, 9);
        assert_eq!(&frame.payload[..4], b"AYMF");
        let parsed = decode_media_packet(frame).unwrap();
        assert_eq!(parsed.media_generation, 2); assert_eq!(parsed.sequence, 5);
        assert_eq!(parsed.data, vec![7;1920]);
        assert!(encode_media_packet(0, packet()).is_err());
        let mut forged = encode_media_packet(7, packet()).unwrap(); forged.stream_id = 10;
        assert!(decode_media_packet(forged).is_err());
        let mut truncated = encode_media_packet(7, packet()).unwrap(); truncated.payload.pop();
        assert!(decode_media_packet(truncated).is_err());
        let mut forged = packet(); forged.channels = 2;
        assert!(encode_media_packet(7, forged).is_err());
    }
}

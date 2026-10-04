// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Streaming bodies stay outside application JSON. Both the wire parser and
//! incremental completion checks are shared by every generated host binding.
//! A digest proves byte completion, never authorization or a business commit.

use crate::{Envelope, Lane, MessageType, ProtocolError};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use serde::{Deserialize, Serialize};

pub const MAGIC: [u8; 4] = *b"AYBS";
pub const HEADER_BYTES: usize = 64;
pub const MAX_METADATA_BYTES: usize = 48 * 1024;
pub const MAX_DATA_BYTES: usize = 48 * 1024;
pub const MAX_ACTIVE_STREAMS: usize = 64;
/// Existing browser WebSocket message ceiling. This is a host business boundary,
/// not permission to send large application/control JSON frames on the wire.
pub const MAX_WEBSOCKET_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_BROWSER_ENVELOPE_BYTES: usize = MAX_WEBSOCKET_BYTES * 6 + MAX_METADATA_BYTES;
// The existing transport permits a 1 GiB binary attachment. Bodies are streamed
// by their host into bounded sinks, rather than accumulated to this limit.
pub const MAX_STREAM_BYTES: u64 = 16 * 1024 * 1024 * 1024;
/// Only streamed raw HTTP uploads may omit their final size at Open. Finish
/// carries the actual bounded size and digest; no unknown-size receipt exists.
pub const UNKNOWN_TOTAL: u64 = u64::MAX;

pub fn normalize_metadata(metadata: &[u8]) -> Result<Vec<u8>, ProtocolError> {
    let mut envelope = Envelope::from_slice(metadata)?;
    if envelope.payload.get("raw_headers").is_some_and(|value| value.is_array()) {
        envelope.payload.remove("headers");
    }
    envelope.to_vec()
}

pub fn restore_header_map(envelope: &mut Envelope) {
    if let Some(rows) = envelope.payload.get("raw_headers").and_then(|value| value.as_array()) {
        let headers: serde_json::Map<String, serde_json::Value> = rows.iter().filter_map(|row| {
            let pair = row.as_array()?;
            if pair.len() != 2 { return None; }
            Some((pair[0].as_str()?.to_owned(), pair[1].as_str()?.into()))
        }).collect();
        envelope.payload.insert("headers".into(), headers.into());
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Receipt { pub lane: u8, pub stream_id: u64, pub total: u64, pub digest: Vec<u8> }
impl Receipt {
    pub fn from_envelope(envelope: &Envelope) -> Result<Self, ProtocolError> {
        if envelope.header.message_type != MessageType::TransportStreamReceipt { return Err(ProtocolError::InvalidEnvelope); }
        let receipt: Self = serde_json::from_value(serde_json::Value::Object(envelope.payload.clone()))
            .map_err(|_| ProtocolError::InvalidEnvelope)?;
        stream_lane(Lane::try_from(receipt.lane)?)?;
        if receipt.stream_id < 2 || receipt.total > MAX_STREAM_BYTES || receipt.digest.len() != 32 {
            return Err(ProtocolError::InvalidEnvelope);
        }
        Ok(receipt)
    }
}

/// Credit means the host consumed or discarded a validated upload prefix. It
/// carries no digest and does not attest to HTTP admission or mutation success.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Progress { pub lane: u8, pub stream_id: u64, pub offset: u64 }
impl Progress {
    pub fn from_envelope(envelope: &Envelope) -> Result<Self, ProtocolError> {
        if envelope.header.message_type != MessageType::TransportStreamProgress { return Err(ProtocolError::InvalidEnvelope); }
        let progress:Self=serde_json::from_value(serde_json::Value::Object(envelope.payload.clone())).map_err(|_|ProtocolError::InvalidEnvelope)?;
        if progress.lane!=Lane::Http as u8 || progress.stream_id<2 || progress.offset==0 || progress.offset>MAX_STREAM_BYTES {
            return Err(ProtocolError::InvalidEnvelope);
        }
        Ok(progress)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Kind { Open = 1, Data = 2, Finish = 3, Abort = 4 }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Content { None = 0, RawBody = 1, TextBody = 2, Base64Body = 3,
    TextData = 4, BinaryData = 5, WebSocketBinary = 6, RawFile = 7 }

impl TryFrom<u8> for Kind {
    type Error = ProtocolError;
    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value { 1 => Ok(Self::Open), 2 => Ok(Self::Data), 3 => Ok(Self::Finish),
            4 => Ok(Self::Abort), _ => Err(ProtocolError::InvalidFrame) }
    }
}
impl TryFrom<u8> for Content {
    type Error = ProtocolError;
    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value { 0 => Ok(Self::None), 1 => Ok(Self::RawBody), 2 => Ok(Self::TextBody),
            3 => Ok(Self::Base64Body), 4 => Ok(Self::TextData), 5 => Ok(Self::BinaryData),
            6 => Ok(Self::WebSocketBinary), 7 => Ok(Self::RawFile), _ => Err(ProtocolError::InvalidFrame) }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub kind: Kind, pub content: Content, pub offset: u64, pub total: u64,
    pub digest: [u8; 32], pub metadata: Vec<u8>, pub data: Vec<u8>,
}

fn stream_lane(lane: Lane) -> Result<(), ProtocolError> {
    if matches!(lane, Lane::Http | Lane::ServerEvents | Lane::WebSocket | Lane::Binary) { Ok(()) }
    else { Err(ProtocolError::UnknownLane) }
}

pub fn validate_metadata(lane: Lane, content: Content, metadata: &[u8]) -> Result<(), ProtocolError> {
    stream_lane(lane)?;
    if metadata.is_empty() || metadata.len() > MAX_METADATA_BYTES { return Err(ProtocolError::FrameTooLarge); }
    let envelope = Envelope::from_slice(metadata)?;
    if envelope.lane() != lane { return Err(ProtocolError::InvalidEnvelope); }
    if lane == Lane::Binary {
        if content != Content::RawFile { return Err(ProtocolError::InvalidEnvelope); }
        crate::binary::Descriptor::from_envelope(&envelope)?;
        return Ok(());
    }
    let permitted = match content {
        Content::None => true,
        Content::RawBody | Content::TextBody | Content::Base64Body =>
            matches!(envelope.header.message_type, MessageType::HttpRequest | MessageType::HttpResponse),
        Content::TextData => matches!(envelope.header.message_type,
            MessageType::HttpStreamData | MessageType::HttpSseEvent | MessageType::HttpWsData),
        Content::BinaryData => envelope.header.message_type == MessageType::HttpStreamData,
        Content::WebSocketBinary => envelope.header.message_type == MessageType::HttpWsData,
        Content::RawFile => false,
    };
    // The field is carried exactly once, as bytes, without a hidden JSON copy.
    if !permitted || ["body", "data", "data_b64"].iter().any(|key|
        envelope.payload.get(*key).is_some_and(|value| !value.is_null())) {
        return Err(ProtocolError::InvalidEnvelope);
    }
    if content == Content::RawBody && ["compressed", "body_base64"].iter().any(|key|
        envelope.payload.get(*key).is_some_and(|value| !value.is_null() && value.as_bool() != Some(false))) {
        return Err(ProtocolError::InvalidEnvelope);
    }
    Ok(())
}

impl Record {
    pub fn validate(&self, lane: Lane) -> Result<(), ProtocolError> {
        stream_lane(lane)?;
        if lane == Lane::Binary && (self.content != Content::RawFile || self.total > crate::binary::MAX_FILE_BYTES) {
            return Err(ProtocolError::InvalidFrame);
        }
        if self.metadata.len() > MAX_METADATA_BYTES || self.data.len() > MAX_DATA_BYTES ||
            HEADER_BYTES + self.metadata.len() + self.data.len() > lane.max_payload() ||
            (self.total > MAX_STREAM_BYTES && self.total != UNKNOWN_TOTAL) || self.offset > MAX_STREAM_BYTES || self.offset > self.total ||
            (self.total == UNKNOWN_TOTAL && (lane != Lane::Http || self.content != Content::RawBody || self.kind == Kind::Finish)) {
            return Err(ProtocolError::FrameTooLarge);
        }
        match self.kind {
            Kind::Open => {
                validate_metadata(lane, self.content, &self.metadata)?;
                if lane == Lane::Binary {
                    let file = crate::binary::Descriptor::from_envelope(&Envelope::from_slice(&self.metadata)?)?;
                    if self.total != file.total - file.offset { return Err(ProtocolError::InvalidFrame); }
                }
                if lane == Lane::WebSocket && self.total > MAX_WEBSOCKET_BYTES as u64 {
                    return Err(ProtocolError::FrameTooLarge);
                }
                if self.total == UNKNOWN_TOTAL && Envelope::from_slice(&self.metadata)?.header.message_type != MessageType::HttpRequest {
                    return Err(ProtocolError::InvalidEnvelope);
                }
                if self.offset != 0 || !self.data.is_empty() || self.digest != [0; 32] ||
                    (self.content == Content::None && self.total != 0) { return Err(ProtocolError::InvalidFrame); }
            }
            Kind::Data => {
                if lane == Lane::Binary && self.data.len() != MAX_DATA_BYTES && self.offset + self.data.len() as u64 != self.total {
                    return Err(ProtocolError::InvalidFrame);
                }
                if self.content == Content::None || !self.metadata.is_empty() || self.data.is_empty() ||
                    self.digest != [0; 32] || self.offset.checked_add(self.data.len() as u64)
                        .is_none_or(|end| end > self.total.min(MAX_STREAM_BYTES)) { return Err(ProtocolError::InvalidFrame); }
            }
            Kind::Finish | Kind::Abort => {
                if !self.metadata.is_empty() || !self.data.is_empty() ||
                    (self.kind == Kind::Finish && self.offset != self.total) ||
                    (self.kind == Kind::Abort && self.digest != [0; 32]) { return Err(ProtocolError::InvalidFrame); }
            }
        }
        Ok(())
    }

    pub fn encode(&self, lane: Lane) -> Result<Vec<u8>, ProtocolError> {
        self.validate(lane)?;
        let mut bytes = vec![0u8; HEADER_BYTES];
        bytes[..4].copy_from_slice(&MAGIC); bytes[4] = 1;
        bytes[5] = self.kind as u8; bytes[6] = self.content as u8;
        bytes[8..12].copy_from_slice(&(self.metadata.len() as u32).to_be_bytes());
        bytes[12..16].copy_from_slice(&(self.data.len() as u32).to_be_bytes());
        bytes[16..24].copy_from_slice(&self.offset.to_be_bytes());
        bytes[24..32].copy_from_slice(&self.total.to_be_bytes());
        bytes[32..64].copy_from_slice(&self.digest);
        bytes.extend_from_slice(&self.metadata); bytes.extend_from_slice(&self.data);
        Ok(bytes)
    }

    pub fn decode(lane: Lane, bytes: &[u8]) -> Result<Self, ProtocolError> {
        stream_lane(lane)?;
        if bytes.len() < HEADER_BYTES { return Err(ProtocolError::IncompleteFrame); }
        if bytes.len() > lane.max_payload() { return Err(ProtocolError::FrameTooLarge); }
        if bytes[..4] != MAGIC || bytes[4] != 1 || bytes[7] != 0 { return Err(ProtocolError::InvalidFrame); }
        let metadata_len = u32::from_be_bytes(bytes[8..12].try_into().unwrap()) as usize;
        let data_len = u32::from_be_bytes(bytes[12..16].try_into().unwrap()) as usize;
        // Reject advertised allocations before copying any peer-controlled bytes.
        if metadata_len > MAX_METADATA_BYTES || data_len > MAX_DATA_BYTES ||
            HEADER_BYTES + metadata_len + data_len != bytes.len() { return Err(ProtocolError::InvalidFrame); }
        let value = Self { kind: Kind::try_from(bytes[5])?, content: Content::try_from(bytes[6])?,
            offset: u64::from_be_bytes(bytes[16..24].try_into().unwrap()),
            total: u64::from_be_bytes(bytes[24..32].try_into().unwrap()),
            digest: bytes[32..64].try_into().unwrap(),
            metadata: bytes[64..64 + metadata_len].to_vec(), data: bytes[64 + metadata_len..].to_vec() };
        value.validate(lane)?; Ok(value)
    }
}

struct Pending { content: Content, total: u64, next: u64, digest: Sha256 }

/// Memory use depends on active stream count, never the advertised body size.
#[derive(Default)]
pub struct Receiver { pending: HashMap<(u8, u64), Pending> }
impl Receiver {
    pub fn active_count(&self) -> usize { self.pending.len() }
    pub fn accept(&mut self, lane: Lane, stream_id: u64, record: &Record) -> Result<(), ProtocolError> {
        record.validate(lane)?;
        if stream_id == 0 { return Err(ProtocolError::InvalidFrame); }
        let key = (lane as u8, stream_id);
        if record.kind == Kind::Open {
            if self.pending.contains_key(&key) || self.pending.len() >= MAX_ACTIVE_STREAMS {
                return Err(ProtocolError::FrameTooLarge);
            }
            self.pending.insert(key, Pending { content: record.content, total: record.total, next: 0, digest: Sha256::new() });
            return Ok(());
        }
        let pending = self.pending.get_mut(&key).ok_or(ProtocolError::InvalidFrame)?;
        let final_unknown = pending.total == UNKNOWN_TOTAL && matches!(record.kind, Kind::Finish | Kind::Abort) && record.total == pending.next;
        if pending.content != record.content || (pending.total != record.total && !final_unknown) || pending.next != record.offset {
            return Err(ProtocolError::InvalidFrame);
        }
        match record.kind {
            Kind::Data => { pending.next += record.data.len() as u64; pending.digest.update(&record.data); }
            Kind::Finish => {
                if pending.next != record.total || pending.digest.clone().finalize().as_slice() != record.digest {
                    return Err(ProtocolError::InvalidFrame);
                }
                self.pending.remove(&key);
            }
            Kind::Abort => { self.pending.remove(&key); }
            Kind::Open => unreachable!(),
        }
        Ok(())
    }
    pub fn clear(&mut self) { self.pending.clear(); }
}

pub struct Writer { lane: Lane, content: Content, total: u64, next: u64, digest: Sha256, finished: bool, cancelled: bool }
impl Writer {
    pub fn open(lane: Lane, content: Content, total: u64, metadata: Vec<u8>) -> Result<(Self, Record), ProtocolError> {
        let metadata = normalize_metadata(&metadata)?;
        if total == UNKNOWN_TOTAL && Envelope::from_slice(&metadata)?.header.message_type != MessageType::HttpRequest {
            return Err(ProtocolError::InvalidEnvelope);
        }
        let record = Record { kind: Kind::Open, content, offset: 0, total, digest: [0; 32], metadata, data: vec![] };
        record.validate(lane)?;
        Ok((Self { lane, content, total, next: 0, digest: Sha256::new(), finished: false, cancelled: false }, record))
    }
    pub fn data(&mut self, data: Vec<u8>) -> Result<Record, ProtocolError> {
        if self.finished { return Err(ProtocolError::InvalidFrame); }
        let record = Record { kind: Kind::Data, content: self.content, offset: self.next, total: self.total,
            digest: [0; 32], metadata: vec![], data };
        record.validate(self.lane)?;
        self.next += record.data.len() as u64; self.digest.update(&record.data); Ok(record)
    }
    pub fn finish(&mut self) -> Result<Record, ProtocolError> {
        if self.finished || (self.total != UNKNOWN_TOTAL && self.next != self.total) { return Err(ProtocolError::InvalidFrame); }
        let record = Record { kind: Kind::Finish, content: self.content, offset: self.next,
            total: if self.total == UNKNOWN_TOTAL { self.next } else { self.total },
            digest: self.digest.clone().finalize().into(), metadata: vec![], data: vec![] };
        record.validate(self.lane)?; self.finished = true; Ok(record)
    }
    pub fn abort(&mut self) -> Result<Record, ProtocolError> {
        if self.finished { return Err(ProtocolError::InvalidFrame); }
        self.cancel_at(self.next)
    }
    /// A host may have constructed a record that never entered the bounded
    /// endpoint queue. Cancel at the last successfully enqueued body offset.
    pub fn cancel_at(&mut self, queued: u64) -> Result<Record, ProtocolError> {
        if self.cancelled || queued > self.next { return Err(ProtocolError::InvalidFrame); }
        self.cancelled = true;
        self.finished = true;
        Ok(Record { kind: Kind::Abort, content: self.content, offset: queued,
            total: if self.total == UNKNOWN_TOTAL { queued } else { self.total },
            digest: [0; 32], metadata: vec![], data: vec![] })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn upload_progress_is_scoped_control_and_cannot_claim_completion() {
        let mut envelope=Envelope::from_slice(br#"{"header":{"message_id":"synthetic-credit","message_type":"transport_stream_progress","timestamp":1},"payload":{"lane":4,"stream_id":3,"offset":49152}}"#).unwrap();
        assert_eq!(envelope.lane(),Lane::Control);assert_eq!(envelope.required_scope(),Some("browser"));
        assert_eq!(Progress::from_envelope(&envelope).unwrap().offset,49152);
        assert!(Receipt::from_envelope(&envelope).is_err());
        envelope.payload.insert("digest".into(),serde_json::json!(vec![0;32]));assert!(Progress::from_envelope(&envelope).is_err());
        envelope.payload.remove("digest");
        for (field,value) in [("lane",7),("stream_id",1),("offset",0),("offset",MAX_STREAM_BYTES+1)] {
            let mut invalid=envelope.clone();invalid.payload.insert(field.into(),value.into());assert!(Progress::from_envelope(&invalid).is_err());
        }
    }
    #[test]
    fn binary_records_share_bounded_hashing_without_allowing_inline_bodies_or_unbounded_files() {
        let data = vec![17; MAX_DATA_BYTES + 3];
        let descriptor = crate::binary::Descriptor { transfer_id: "ab".repeat(16), purpose: crate::binary::Purpose::Attachment,
            filename: "synthetic.bin".into(), mime_type: "application/octet-stream".into(), total: data.len() as u64,
            offset: 0, sha256: Sha256::digest(&data).to_vec(), expires_at_ms: 1000, metadata: Default::default() };
        let mut envelope = Envelope::from_slice(&metadata()).unwrap(); envelope.header.message_type = MessageType::BinaryTransferOpen;
        envelope.payload = serde_json::to_value(&descriptor).unwrap().as_object().unwrap().clone();
        let (mut writer, open) = Writer::open(Lane::Binary, Content::RawFile, descriptor.total, envelope.to_vec().unwrap()).unwrap();
        let mut receiver = Receiver::default(); receiver.accept(Lane::Binary, 3, &open).unwrap();
        assert!(writer.data(vec![17]).is_err());
        for block in data.chunks(MAX_DATA_BYTES) { receiver.accept(Lane::Binary, 3, &writer.data(block.to_vec()).unwrap()).unwrap(); }
        let finish = writer.finish().unwrap(); receiver.accept(Lane::Binary, 3, &finish).unwrap();
        assert_eq!(finish.digest.as_slice(), descriptor.sha256); assert_eq!(receiver.active_count(), 0);
        assert!(Writer::open(Lane::Http, Content::RawFile, descriptor.total, envelope.to_vec().unwrap()).is_err());
        envelope.payload.insert("body".into(), "hidden".into());
        assert!(Writer::open(Lane::Binary, Content::RawFile, descriptor.total, envelope.to_vec().unwrap()).is_err());
    }
    #[test]
    fn binary_resume_carries_a_verified_suffix_with_matching_declared_offset() {
        let descriptor = crate::binary::Descriptor { transfer_id: "ab".repeat(16), purpose: crate::binary::Purpose::Download,
            filename: "synthetic.bin".into(), mime_type: "application/octet-stream".into(), total: 48 * 1024 + 3,
            offset: 48 * 1024, sha256: vec![9;32], expires_at_ms: 1000, metadata: Default::default() };
        let mut envelope = Envelope::from_slice(&metadata()).unwrap(); envelope.header.message_type = MessageType::BinaryTransferOpen;
        envelope.payload = serde_json::to_value(&descriptor).unwrap().as_object().unwrap().clone();
        assert!(Writer::open(Lane::Binary, Content::RawFile, descriptor.total, envelope.to_vec().unwrap()).is_err());
        let (mut writer, open) = Writer::open(Lane::Binary, Content::RawFile, 3, envelope.to_vec().unwrap()).unwrap();
        let mut receiver = Receiver::default(); receiver.accept(Lane::Binary, 3, &open).unwrap();
        receiver.accept(Lane::Binary, 3, &writer.data(vec![9;3]).unwrap()).unwrap();
        receiver.accept(Lane::Binary, 3, &writer.finish().unwrap()).unwrap(); assert_eq!(receiver.active_count(), 0);
    }
    fn metadata() -> Vec<u8> {
        br#"{"header":{"message_id":"synthetic-request","message_type":"http_request","timestamp":1},"payload":{"method":"POST","url":"/agent/synthetic/upload","raw_headers":[["cookie","a=1"],["cookie","b=2"]]}}"#.to_vec()
    }
    #[test]
    fn unknown_uploads_finish_with_actual_size_or_abort_without_replaying() {
        for abort in [false, true] {
            let (mut writer, open) = Writer::open(Lane::Http, Content::RawBody, UNKNOWN_TOTAL, metadata()).unwrap();
            let mut receiver = Receiver::default(); receiver.accept(Lane::Http, 3, &open).unwrap();
            let data = writer.data(vec![0, 255, 13, 10]).unwrap(); receiver.accept(Lane::Http, 3, &data).unwrap();
            let mut wrong = writer.finish().unwrap(); wrong.total += 1;
            assert!(receiver.accept(Lane::Http, 3, &wrong).is_err());
            let (mut writer, open) = Writer::open(Lane::Http, Content::RawBody, UNKNOWN_TOTAL, metadata()).unwrap();
            let mut receiver = Receiver::default(); receiver.accept(Lane::Http, 5, &open).unwrap();
            receiver.accept(Lane::Http, 5, &writer.data(vec![0, 255, 13, 10]).unwrap()).unwrap();
            let end = if abort { writer.abort().unwrap() } else { writer.finish().unwrap() };
            assert_eq!(end.total, 4); receiver.accept(Lane::Http, 5, &end).unwrap();
            assert_eq!(receiver.active_count(), 0);
        }
        assert!(Writer::open(Lane::WebSocket, Content::WebSocketBinary, UNKNOWN_TOTAL, metadata()).is_err());
        let response = String::from_utf8(metadata()).unwrap().replace("http_request", "http_response").into_bytes();
        assert!(Writer::open(Lane::Http, Content::RawBody, UNKNOWN_TOTAL, response).is_err());
    }
    #[test]
    fn cancelled_upload_uses_last_queued_offset_after_unwritten_data_or_finish() {
        for finish in [false, true] {
            let (mut writer, open) = Writer::open(Lane::Http, Content::RawBody, 4, metadata()).unwrap();
            let mut receiver = Receiver::default(); receiver.accept(Lane::Http, 3, &open).unwrap();
            receiver.accept(Lane::Http, 3, &writer.data(vec![1,2]).unwrap()).unwrap();
            let _never_enqueued = writer.data(vec![3,4]).unwrap();
            if finish { let _never_enqueued = writer.finish().unwrap(); }
            receiver.accept(Lane::Http, 3, &writer.cancel_at(2).unwrap()).unwrap();
            assert_eq!(receiver.active_count(), 0); assert!(writer.cancel_at(2).is_err());
        }
    }

    #[test]
    fn header_budget_does_not_duplicate_raw_pairs_in_stream_metadata() {
        let mut envelope = Envelope::from_slice(&metadata()).unwrap();
        let value = "\\\"".repeat(7000);
        envelope.payload.insert("raw_headers".into(), serde_json::json!([["x-synthetic", value]]));
        envelope.payload.insert("headers".into(), serde_json::json!({"x-synthetic": value}));
        let (_, open) = Writer::open(Lane::Http, Content::RawBody, 0, envelope.to_vec().unwrap()).unwrap();
        let mut normalized = Envelope::from_slice(&open.metadata).unwrap();
        assert!(!normalized.payload.contains_key("headers"));
        assert!(open.metadata.len() < MAX_METADATA_BYTES);
        restore_header_map(&mut normalized); assert_eq!(normalized.payload, envelope.payload);
    }
    #[test]
    fn byte_stream_roundtrip_preserves_bytes_headers_and_verified_completion() {
        let bytes = vec![0, 255, 13, 10, 194, 163];
        let (mut writer, open) = Writer::open(Lane::Http, Content::RawBody, bytes.len() as u64, metadata()).unwrap();
        let mut receiver = Receiver::default();
        for record in [open, writer.data(bytes[..3].to_vec()).unwrap(), writer.data(bytes[3..].to_vec()).unwrap(), writer.finish().unwrap()] {
            let decoded = Record::decode(Lane::Http, &record.encode(Lane::Http).unwrap()).unwrap();
            assert_eq!(decoded, record); receiver.accept(Lane::Http, 2, &decoded).unwrap();
        }
        assert_eq!(receiver.active_count(), 0);
    }
    #[test]
    fn byte_stream_rejects_truncation_offset_replay_bad_digest_and_cross_lane() {
        let (mut writer, open) = Writer::open(Lane::Http, Content::RawBody, 2, metadata()).unwrap();
        let mut receiver = Receiver::default(); receiver.accept(Lane::Http, 2, &open).unwrap();
        assert!(Record::decode(Lane::WebSocket, &open.encode(Lane::Http).unwrap()).is_err());
        assert!(writer.finish().is_err());
        let data = writer.data(vec![1, 2]).unwrap(); receiver.accept(Lane::Http, 2, &data).unwrap();
        assert!(receiver.accept(Lane::Http, 2, &data).is_err());
        let mut finish = writer.finish().unwrap(); finish.digest[0] ^= 1;
        assert!(receiver.accept(Lane::Http, 2, &finish).is_err());
        assert_eq!(receiver.active_count(), 1); receiver.clear();
        let mut bytes = open.encode(Lane::Http).unwrap(); bytes[8..12].copy_from_slice(&u32::MAX.to_be_bytes());
        assert!(Record::decode(Lane::Http, &bytes).is_err());
    }
    #[test]
    fn byte_stream_bounded_stream_flood_and_abort_without_body_allocation() {
        let (_, open) = Writer::open(Lane::Http, Content::RawBody, MAX_STREAM_BYTES, metadata()).unwrap();
        let mut receiver = Receiver::default();
        for id in 2..2 + MAX_ACTIVE_STREAMS as u64 { receiver.accept(Lane::Http, id, &open).unwrap(); }
        assert!(receiver.accept(Lane::Http, 999, &open).is_err());
        let (mut writer, _) = Writer::open(Lane::Http, Content::RawBody, MAX_STREAM_BYTES, metadata()).unwrap();
        receiver.accept(Lane::Http, 2, &writer.abort().unwrap()).unwrap();
        assert_eq!(receiver.active_count(), MAX_ACTIVE_STREAMS - 1);
        assert!(Writer::open(Lane::Http, Content::RawBody, MAX_STREAM_BYTES + 1, metadata()).is_err());
    }
    #[test]
    fn byte_stream_metadata_cannot_smuggle_a_bulk_body_or_other_scope() {
        let mut envelope: serde_json::Value = serde_json::from_slice(&metadata()).unwrap();
        envelope["payload"]["body"] = "hidden".into();
        assert!(Writer::open(Lane::Http, Content::RawBody, 0, serde_json::to_vec(&envelope).unwrap()).is_err());
        envelope["payload"].as_object_mut().unwrap().remove("body");
        envelope["header"]["message_type"] = "voice_call_control".into();
        assert!(Writer::open(Lane::Http, Content::None, 0, serde_json::to_vec(&envelope).unwrap()).is_err());
    }
    #[test]
    fn byte_stream_changed_parser_mutation_corpus_remains_bounded_and_canonical() {
        let (_, open) = Writer::open(Lane::Http, Content::RawBody, 20, metadata()).unwrap();
        let encoded = open.encode(Lane::Http).unwrap();
        for end in 0..encoded.len() {
            if let Ok(decoded) = Record::decode(Lane::Http, &encoded[..end]) {
                assert_eq!(decoded.encode(Lane::Http).unwrap(), encoded[..end]);
            }
        }
        for index in 0..encoded.len() {
            for mask in [1,16,128,255] {
                let mut mutated = encoded.clone(); mutated[index] ^= mask;
                if let Ok(decoded) = Record::decode(Lane::Http, &mutated) {
                    assert!(decoded.metadata.len() <= MAX_METADATA_BYTES && decoded.data.len() <= MAX_DATA_BYTES);
                    assert_eq!(decoded.encode(Lane::Http).unwrap(), mutated);
                }
            }
        }
    }
}

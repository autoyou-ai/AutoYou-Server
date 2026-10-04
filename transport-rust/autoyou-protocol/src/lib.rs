// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License. See ../../LICENSE.

//! One wire contract for all AutoYou hosts. Transport identities never derive
//! application authority from the legacy envelope's user/session headers.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

pub mod media;
pub mod enrollment;
pub mod byte_stream;
pub mod http_body;
pub mod binary;
pub mod delivery;

pub const PAIR_ALPN: &[u8] = b"autoyou/pair/1";
pub const SESSION_ALPN: &[u8] = b"autoyou/session/1";
pub const MEDIA_ALPN: &[u8] = b"autoyou/media/1";
pub const MAGIC: [u8; 4] = *b"AYIR";
pub const WIRE_VERSION: u8 = 1;
pub const HEADER_BYTES: usize = 36;
pub const MAX_CONTROL_BYTES: usize = 1024 * 1024;
pub const MAX_BLOCK_BYTES: usize = 64 * 1024;
pub const MAX_IDENTIFIER_BYTES: usize = 512;

#[derive(Debug, thiserror::Error, Clone, PartialEq, Eq)]
pub enum ProtocolError {
    #[error("unsupported AutoYou protocol")]
    UnsupportedVersion,
    #[error("invalid AutoYou frame")]
    InvalidFrame,
    #[error("unknown AutoYou lane")]
    UnknownLane,
    #[error("frame exceeds the negotiated bound")]
    FrameTooLarge,
    #[error("incomplete AutoYou frame")]
    IncompleteFrame,
    #[error("invalid application envelope")]
    InvalidEnvelope,
    #[error("legacy transport chunks are not Iroh application messages")]
    LegacyChunk,
    #[error("application session is not authorized")]
    NotAuthorized,
    #[error("stale application connection generation")]
    StaleGeneration,
    #[error("application grant is expired or revoked")]
    Revoked,
    #[error("operation is outside the admitted scope")]
    ScopeDenied,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[repr(u8)]
pub enum Lane {
    Enrollment = 1,
    Control = 2,
    Application = 3,
    Http = 4,
    ServerEvents = 5,
    WebSocket = 6,
    Binary = 7,
    Media = 8,
    Input = 9,
}

impl TryFrom<u8> for Lane {
    type Error = ProtocolError;
    fn try_from(value: u8) -> Result<Self, Self::Error> {
        match value {
            1 => Ok(Self::Enrollment), 2 => Ok(Self::Control),
            3 => Ok(Self::Application), 4 => Ok(Self::Http),
            5 => Ok(Self::ServerEvents), 6 => Ok(Self::WebSocket),
            7 => Ok(Self::Binary), 8 => Ok(Self::Media),
            9 => Ok(Self::Input), _ => Err(ProtocolError::UnknownLane),
        }
    }
}

impl Lane {
    pub fn max_payload(self) -> usize {
        match self {
            Self::Enrollment | Self::Input => 16 * 1024,
            Self::Media => media::MEDIA_HEADER_BYTES + media::MAX_VIDEO_FRAME_BYTES,
            Self::Binary | Self::Http | Self::WebSocket | Self::ServerEvents => MAX_BLOCK_BYTES,
            _ => MAX_CONTROL_BYTES,
        }
    }

    pub fn priority(self) -> u8 {
        match self {
            Self::Enrollment | Self::Control | Self::Input => 0,
            Self::Media => 1,
            Self::Application | Self::ServerEvents => 2,
            Self::Http | Self::WebSocket => 3,
            Self::Binary => 4,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameHeader {
    pub lane: Lane,
    pub generation: u64,
    pub stream_id: u64,
    pub sequence: u64,
    pub length: usize,
}

impl FrameHeader {
    pub fn encode(&self) -> Result<[u8; HEADER_BYTES], ProtocolError> {
        if self.length > self.lane.max_payload() {
            return Err(ProtocolError::FrameTooLarge);
        }
        let mut bytes = [0u8; HEADER_BYTES];
        bytes[..4].copy_from_slice(&MAGIC);
        bytes[4] = WIRE_VERSION;
        bytes[5] = self.lane as u8;
        // Bytes 6..8 are reserved and must remain zero in protocol 1.
        bytes[8..16].copy_from_slice(&self.generation.to_be_bytes());
        bytes[16..24].copy_from_slice(&self.stream_id.to_be_bytes());
        bytes[24..32].copy_from_slice(&self.sequence.to_be_bytes());
        bytes[32..36].copy_from_slice(&(self.length as u32).to_be_bytes());
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, ProtocolError> {
        if bytes.len() != HEADER_BYTES {
            return Err(ProtocolError::IncompleteFrame);
        }
        if bytes[..4] != MAGIC || bytes[6..8] != [0, 0] {
            return Err(ProtocolError::InvalidFrame);
        }
        if bytes[4] != WIRE_VERSION {
            return Err(ProtocolError::UnsupportedVersion);
        }
        let lane = Lane::try_from(bytes[5])?;
        let generation = u64::from_be_bytes(bytes[8..16].try_into().map_err(|_| ProtocolError::InvalidFrame)?);
        let stream_id = u64::from_be_bytes(bytes[16..24].try_into().map_err(|_| ProtocolError::InvalidFrame)?);
        let sequence = u64::from_be_bytes(bytes[24..32].try_into().map_err(|_| ProtocolError::InvalidFrame)?);
        let length = u32::from_be_bytes(bytes[32..36].try_into().map_err(|_| ProtocolError::InvalidFrame)?) as usize;
        if length > lane.max_payload() {
            return Err(ProtocolError::FrameTooLarge);
        }
        Ok(Self { lane, generation, stream_id, sequence, length })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Frame {
    pub lane: Lane,
    pub generation: u64,
    pub stream_id: u64,
    pub sequence: u64,
    pub payload: Vec<u8>,
}

impl Frame {
    pub fn encode(&self) -> Result<Vec<u8>, ProtocolError> {
        let header = FrameHeader { lane: self.lane, generation: self.generation,
            stream_id: self.stream_id, sequence: self.sequence, length: self.payload.len() }.encode()?;
        let mut bytes = Vec::with_capacity(HEADER_BYTES + self.payload.len());
        bytes.extend_from_slice(&header);
        bytes.extend_from_slice(&self.payload);
        Ok(bytes)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, ProtocolError> {
        if bytes.len() < HEADER_BYTES {
            return Err(ProtocolError::IncompleteFrame);
        }
        let header = FrameHeader::decode(&bytes[..HEADER_BYTES])?;
        if bytes.len() != HEADER_BYTES + header.length {
            return Err(ProtocolError::InvalidFrame);
        }
        Ok(Self { lane: header.lane, generation: header.generation, stream_id: header.stream_id,
            sequence: header.sequence, payload: bytes[HEADER_BYTES..].to_vec() })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageType {
    Chat, HttpRequest, HttpRequestCancel, HttpResponse,
    HttpSseStart, HttpSseEvent, HttpSseEnd,
    HttpStreamOpen, HttpStreamData, HttpStreamEnd, HttpStreamAbort,
    Ping, Pong, VoiceCallControl, RoomBridgeControl, PairingControl,
    PeerControl, RoomControl, RoomChat, RoomFederationControl, RoomFederationChat,
    Chunk, ChunkAck, Error, HttpWsUpgrade, HttpWsData, HttpWsClose, TransportStreamReceipt, TransportStreamProgress,
    BinaryTransferOpen, BinaryTransferControl, TransportTransferLimits, ApplicationDeliveryControl,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MessageHeader {
    pub message_id: String,
    pub message_type: MessageType,
    pub timestamp: f64,
    #[serde(default)]
    pub session_id: Option<String>,
    #[serde(default)]
    pub user_id: Option<String>,
    #[serde(flatten)]
    pub extensions: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope {
    pub header: MessageHeader,
    pub payload: Map<String, Value>,
    #[serde(flatten)]
    pub extensions: Map<String, Value>,
}

impl Envelope {
    pub fn lane(&self) -> Lane {
        use MessageType::*;
        match self.header.message_type {
            Chat | RoomChat | RoomFederationChat => Lane::Application,
            HttpRequest | HttpResponse | HttpStreamOpen | HttpStreamData | HttpStreamEnd => Lane::Http,
            HttpSseStart | HttpSseEvent | HttpSseEnd => Lane::ServerEvents,
            HttpWsUpgrade | HttpWsData | HttpWsClose => Lane::WebSocket,
            BinaryTransferOpen => Lane::Binary,
            _ => Lane::Control,
        }
    }
    pub fn required_scope(&self) -> Option<&'static str> {
        use MessageType::*;
        match self.header.message_type {
            Chat | RoomChat | RoomFederationChat | ApplicationDeliveryControl => Some("chat"),
            HttpRequest | HttpResponse | HttpRequestCancel | HttpStreamOpen | HttpStreamData |
                HttpStreamEnd | HttpStreamAbort | HttpSseStart | HttpSseEvent | HttpSseEnd |
                  HttpWsUpgrade | HttpWsData | HttpWsClose => Some("browser"),
            TransportStreamReceipt => if self.payload.get("lane").and_then(Value::as_u64) == Some(Lane::Binary as u64) { Some("files") } else { Some("browser") },
            TransportStreamProgress => Some("browser"),
            BinaryTransferOpen | BinaryTransferControl | TransportTransferLimits => Some("files"),
            VoiceCallControl => Some("media"), PairingControl => Some("pairing"),
            PeerControl => Some("peer"), RoomBridgeControl | RoomControl => Some("room"),
            RoomFederationControl => Some("room_federation"), _ => None,
        }
    }
    pub fn from_slice(bytes: &[u8]) -> Result<Self, ProtocolError> {
        if bytes.len() > MAX_CONTROL_BYTES {
            return Err(ProtocolError::FrameTooLarge);
        }
        let result: Self = serde_json::from_slice(bytes).map_err(|_| ProtocolError::InvalidEnvelope)?;
        result.validate()?;
        Ok(result)
    }

    pub fn validate(&self) -> Result<(), ProtocolError> {
        let header = &self.header;
        if header.message_id.is_empty() || header.message_id.len() > MAX_IDENTIFIER_BYTES ||
            !header.timestamp.is_finite() || header.timestamp < 0.0 ||
            header.session_id.as_ref().is_some_and(|s| s.len() > MAX_IDENTIFIER_BYTES) ||
            header.user_id.as_ref().is_some_and(|s| s.len() > MAX_IDENTIFIER_BYTES) {
            return Err(ProtocolError::InvalidEnvelope);
        }
        if matches!(header.message_type, MessageType::Chunk | MessageType::ChunkAck) ||
            self.extensions.get("chunk_info").is_some_and(|value| !value.is_null()) {
            return Err(ProtocolError::LegacyChunk);
        }
        Ok(())
    }

    pub fn to_vec(&self) -> Result<Vec<u8>, ProtocolError> {
        self.validate()?;
        let bytes = serde_json::to_vec(self).map_err(|_| ProtocolError::InvalidEnvelope)?;
        if bytes.len() > MAX_CONTROL_BYTES {
            return Err(ProtocolError::FrameTooLarge);
        }
        Ok(bytes)
    }
}

/// Values supplied by the trusted host's existing enrollment/authorization
/// service, never by untrusted application headers.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Principal {
    pub endpoint_id: String,
    pub device_id: String,
    pub owner_id: String,
    pub conversation_id: String,
    pub generation: u64,
    pub authorization_epoch: u64,
    pub expires_at_ms: u64,
    pub scopes: Vec<String>,
}

impl Principal {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.generation == 0 || self.expires_at_ms == 0 ||
            [&self.endpoint_id, &self.device_id, &self.owner_id, &self.conversation_id]
                .iter().any(|s| s.is_empty() || s.len() > MAX_IDENTIFIER_BYTES) ||
            self.scopes.len() > 32 || self.scopes.iter().any(|s| s.is_empty() || s.len() > 64) {
            return Err(ProtocolError::InvalidEnvelope);
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Default)]
pub enum Admission {
    #[default]
    AwaitingProof,
    Admitted(Principal),
    Revoked,
}

impl Admission {
    pub fn admit(&mut self, principal: Principal, authenticated_endpoint: &str) -> Result<(), ProtocolError> {
        if matches!(self, Self::Revoked) {
            return Err(ProtocolError::Revoked);
        }
        principal.validate()?;
        if principal.endpoint_id != authenticated_endpoint {
            return Err(ProtocolError::NotAuthorized);
        }
        *self = Self::Admitted(principal);
        Ok(())
    }

    pub fn check(&self, frame: &FrameHeader, scope: Option<&str>, now_ms: u64) -> Result<&Principal, ProtocolError> {
        let principal = match self {
            Self::Admitted(principal) => principal,
            Self::Revoked => return Err(ProtocolError::Revoked),
            Self::AwaitingProof => return Err(ProtocolError::NotAuthorized),
        };
        if principal.expires_at_ms <= now_ms {
            return Err(ProtocolError::Revoked);
        }
        if frame.generation != principal.generation {
            return Err(ProtocolError::StaleGeneration);
        }
        if scope.is_some_and(|required| !principal.scopes.iter().any(|s| s == required)) {
            return Err(ProtocolError::ScopeDenied);
        }
        Ok(principal)
    }

    pub fn revoke(&mut self) {
        *self = Self::Revoked;
    }
}

/// Domain-separated, unambiguous binding of the application negotiation to the
/// authenticated connection exporter, endpoints and fresh enrollment challenge.
pub fn transcript_binding(
    exporter: &[u8; 32], initiator: &[u8; 32], acceptor: &[u8; 32],
    challenge: &[u8; 32], protocol: &[u8], capabilities: &[u8],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(b"AutoYou enrollment transcript v1\0");
    for bytes in [exporter.as_slice(), initiator.as_slice(), acceptor.as_slice(), challenge.as_slice(), protocol, capabilities] {
        digest.update((bytes.len() as u64).to_be_bytes());
        digest.update(bytes);
    }
    digest.finalize().into()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn principal() -> Principal {
        Principal { endpoint_id: "synthetic-endpoint".into(), device_id: "synthetic-device".into(),
            owner_id: "synthetic-owner".into(), conversation_id: "synthetic-conversation".into(),
            generation: 7, authorization_epoch: 2, expires_at_ms: 1000, scopes: vec!["chat".into()] }
    }

    #[test]
    fn golden_big_endian_frame_and_roundtrip() {
        let frame = Frame { stream_id: 0, sequence: 0, lane: Lane::Application, generation: 7, payload: b"ok".to_vec() };
        let bytes = frame.encode().unwrap();
        assert_eq!(&bytes[..8], &[65, 89, 73, 82, 1, 3, 0, 0]);
        assert_eq!(&bytes[8..16], &7u64.to_be_bytes());
        assert_eq!(&bytes[32..36], &2u32.to_be_bytes());
        assert_eq!(Frame::decode(&bytes).unwrap(), frame);
    }

    #[test]
    fn oversized_header_is_rejected_before_payload_allocation() {
        let mut bytes = FrameHeader { stream_id: 0, sequence: 0, lane: Lane::Binary, generation: 1, length: 0 }.encode().unwrap();
        bytes[32..].copy_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(FrameHeader::decode(&bytes), Err(ProtocolError::FrameTooLarge));
    }

    #[test]
    fn lane_bounds_support_one_complete_encoded_video_frame_without_unbounded_control() {
        let media = FrameHeader { lane: Lane::Media, generation: 1, stream_id: 1, sequence: 0,
            length: media::MEDIA_HEADER_BYTES + media::MAX_VIDEO_FRAME_BYTES };
        assert_eq!(FrameHeader::decode(&media.encode().unwrap()).unwrap(), media);
        assert_eq!(FrameHeader { length: media.length + 1, ..media }.encode(), Err(ProtocolError::FrameTooLarge));
        for lane in [Lane::Enrollment, Lane::Input] {
            assert_eq!(FrameHeader { lane, generation: 1, stream_id: 1, sequence: 0, length: 16*1024+1 }.encode(),
                Err(ProtocolError::FrameTooLarge));
        }
        for lane in [Lane::Binary, Lane::Http, Lane::WebSocket, Lane::ServerEvents] {
            assert_eq!(lane.max_payload(), MAX_BLOCK_BYTES);
        }
    }

    #[test]
    fn malformed_or_future_protocol_is_explicit() {
        let frame = Frame { stream_id: 0, sequence: 0, lane: Lane::Control, generation: 1, payload: vec![] };
        let mut bytes = frame.encode().unwrap();
        bytes[4] = 2;
        assert_eq!(Frame::decode(&bytes), Err(ProtocolError::UnsupportedVersion));
        bytes[4] = 1; bytes[6] = 1;
        assert_eq!(Frame::decode(&bytes), Err(ProtocolError::InvalidFrame));
        bytes[6] = 0; bytes[5] = 255;
        assert_eq!(Frame::decode(&bytes), Err(ProtocolError::UnknownLane));
    }

    #[test]
    fn trailing_or_truncated_bytes_are_not_accepted() {
        let frame = Frame { stream_id: 0, sequence: 0, lane: Lane::Application, generation: 1, payload: b"payload".to_vec() };
        let mut bytes = frame.encode().unwrap();
        assert_eq!(Frame::decode(&bytes[..35]), Err(ProtocolError::IncompleteFrame));
        bytes.push(0);
        assert_eq!(Frame::decode(&bytes), Err(ProtocolError::InvalidFrame));
    }

    #[test]
    fn legacy_envelope_keeps_extensions_but_does_not_authorize_headers() {
        let json = br#"{"header":{"message_id":"synthetic-message","message_type":"chat","timestamp":1.0,"user_id":"forged-owner","session_id":"forged-session","future":true},"payload":{"text":"hello"},"future_payload":17}"#;
        let envelope = Envelope::from_slice(json).unwrap();
        assert_eq!(envelope.header.user_id.as_deref(), Some("forged-owner"));
        assert_eq!(envelope.extensions["future_payload"], 17);
        let mut admission = Admission::default();
        admission.admit(principal(), "synthetic-endpoint").unwrap();
        let trusted = admission.check(&FrameHeader { stream_id: 0, sequence: 0, lane: Lane::Application, generation: 7, length: json.len() }, Some("chat"), 500).unwrap();
        assert_eq!(trusted.owner_id, "synthetic-owner");
        assert_eq!(trusted.conversation_id, "synthetic-conversation");
    }

    #[test]
    fn chunks_cannot_reintroduce_sctp_state_on_iroh() {
        let json = br#"{"header":{"message_id":"synthetic","message_type":"chunk","timestamp":1.0},"payload":{}}"#;
        assert_eq!(Envelope::from_slice(json).unwrap_err(), ProtocolError::LegacyChunk);
    }

    #[test]
    fn wrong_endpoint_scope_expiry_and_generation_fail_closed() {
        let mut admission = Admission::default();
        assert_eq!(admission.admit(principal(), "wrong-endpoint"), Err(ProtocolError::NotAuthorized));
        let header = FrameHeader { stream_id: 0, sequence: 0, lane: Lane::Application, generation: 7, length: 0 };
        assert!(matches!(admission.check(&header, None, 500), Err(ProtocolError::NotAuthorized)));
        admission.admit(principal(), "synthetic-endpoint").unwrap();
        assert!(matches!(admission.check(&header, Some("admin"), 500), Err(ProtocolError::ScopeDenied)));
        assert!(matches!(admission.check(&header, None, 1000), Err(ProtocolError::Revoked)));
        assert!(matches!(admission.check(&FrameHeader { generation: 6, ..header.clone() }, None, 500), Err(ProtocolError::StaleGeneration)));
        admission.revoke();
        assert_eq!(admission.admit(principal(), "synthetic-endpoint"), Err(ProtocolError::Revoked));
    }

    #[test]
    fn transcript_binds_every_component_and_avoids_concatenation_ambiguity() {
        let initial = transcript_binding(&[1;32], &[2;32], &[3;32], &[4;32], b"pair", b"chat");
        assert_ne!(initial, transcript_binding(&[9;32], &[2;32], &[3;32], &[4;32], b"pair", b"chat"));
        assert_ne!(initial, transcript_binding(&[1;32], &[3;32], &[2;32], &[4;32], b"pair", b"chat"));
        assert_ne!(initial, transcript_binding(&[1;32], &[2;32], &[3;32], &[5;32], b"pair", b"chat"));
        assert_ne!(transcript_binding(&[1;32], &[2;32], &[3;32], &[4;32], b"ab", b"c"),
            transcript_binding(&[1;32], &[2;32], &[3;32], &[4;32], b"a", b"bc"));
    }
}

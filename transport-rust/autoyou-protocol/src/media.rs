// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! One independently cancellable QUIC stream per encoded media frame.
//! Native capture/codec adapters supply bytes; this module does not capture.

use crate::ProtocolError;

pub const MEDIA_HEADER_BYTES: usize = 64;
pub const MAX_VIDEO_FRAME_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_AUDIO_FRAME_BYTES: usize = 16 * 1024;
pub const AUDIO_SAMPLE_RATE: u32 = 48_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum MediaKind { Audio = 1, Camera = 2, Screen = 3, SystemAudio = 4 }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum Codec { Opus = 1, H264 = 2, Vp8 = 3, Pcm16 = 4 }

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MediaHeader {
    pub kind: MediaKind,
    pub codec: Codec,
    pub keyframe: bool,
    pub generation: u64,
    pub authorization_epoch: u64,
    /// Negotiated source identifier, bound to call/room scope by admission.
    pub source_id: u64,
    pub sequence: u64,
    /// Sender's monotonic media clock. Never interpreted as a wall clock.
    pub timestamp_us: u64,
    pub duration_us: u32,
    pub length: usize,
    pub width: u16,
    pub height: u16,
    pub channels: u8,
}

impl MediaHeader {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.generation == 0 || self.source_id == 0 || self.duration_us == 0 || self.duration_us > 1_000_000 {
            return Err(ProtocolError::InvalidFrame);
        }
        let audio = matches!(self.kind, MediaKind::Audio | MediaKind::SystemAudio);
        if audio {
            if !matches!(self.codec, Codec::Opus | Codec::Pcm16) || !(1..=2).contains(&self.channels) ||
                self.width != 0 || self.height != 0 || self.keyframe || self.length > MAX_AUDIO_FRAME_BYTES {
                return Err(ProtocolError::InvalidFrame);
            }
            if self.codec == Codec::Pcm16 {
                let expected = u64::from(AUDIO_SAMPLE_RATE) * u64::from(self.duration_us) *
                    u64::from(self.channels) * 2;
                if expected % 1_000_000 != 0 || self.length as u64 != expected / 1_000_000 {
                    return Err(ProtocolError::InvalidFrame);
                }
            }
        } else if !matches!(self.codec, Codec::H264 | Codec::Vp8) || self.channels != 0 ||
            self.width == 0 || self.height == 0 || self.width > 8192 || self.height > 8192 ||
            self.length > MAX_VIDEO_FRAME_BYTES {
            return Err(ProtocolError::InvalidFrame);
        }
        if self.length == 0 { return Err(ProtocolError::InvalidFrame); }
        Ok(())
    }

    pub fn encode(&self) -> Result<[u8; MEDIA_HEADER_BYTES], ProtocolError> {
        self.validate()?;
        let mut out = [0u8; MEDIA_HEADER_BYTES];
        out[..4].copy_from_slice(b"AYMF");
        out[4] = 1; out[5] = self.kind as u8; out[6] = self.codec as u8;
        out[7] = u8::from(self.keyframe);
        for (offset, value) in [(8, self.generation), (16, self.authorization_epoch),
            (24, self.source_id), (32, self.sequence), (40, self.timestamp_us)] {
            out[offset..offset+8].copy_from_slice(&value.to_be_bytes());
        }
        out[48..52].copy_from_slice(&self.duration_us.to_be_bytes());
        out[52..56].copy_from_slice(&(self.length as u32).to_be_bytes());
        out[56..58].copy_from_slice(&self.width.to_be_bytes());
        out[58..60].copy_from_slice(&self.height.to_be_bytes());
        out[60] = self.channels;
        Ok(out)
    }

    pub fn decode(bytes: &[u8]) -> Result<Self, ProtocolError> {
        if bytes.len() != MEDIA_HEADER_BYTES { return Err(ProtocolError::IncompleteFrame); }
        if &bytes[..4] != b"AYMF" || bytes[4] != 1 || bytes[7] > 1 || bytes[61..] != [0, 0, 0] {
            return Err(ProtocolError::InvalidFrame);
        }
        let kind = match bytes[5] {
            1 => MediaKind::Audio, 2 => MediaKind::Camera, 3 => MediaKind::Screen,
            4 => MediaKind::SystemAudio, _ => return Err(ProtocolError::InvalidFrame),
        };
        let codec = match bytes[6] {
            1 => Codec::Opus, 2 => Codec::H264, 3 => Codec::Vp8, 4 => Codec::Pcm16,
            _ => return Err(ProtocolError::InvalidFrame),
        };
        // Slice lengths were checked above; fixed-width conversions cannot fail.
        let u64_at = |offset| u64::from_be_bytes(bytes[offset..offset+8].try_into().unwrap());
        let header = Self { kind, codec, keyframe: bytes[7] == 1,
            generation: u64_at(8), authorization_epoch: u64_at(16), source_id: u64_at(24),
            sequence: u64_at(32), timestamp_us: u64_at(40),
            duration_us: u32::from_be_bytes(bytes[48..52].try_into().unwrap()),
            length: u32::from_be_bytes(bytes[52..56].try_into().unwrap()) as usize,
            width: u16::from_be_bytes(bytes[56..58].try_into().unwrap()),
            height: u16::from_be_bytes(bytes[58..60].try_into().unwrap()), channels: bytes[60] };
        header.validate()?;
        Ok(header)
    }
}

/// Admission supplies current epoch/generation and a negotiated source. No
/// payload is allocated until this guard and the frame bound have succeeded.
#[derive(Debug, Clone)]
pub struct MediaFence {
    pub generation: u64,
    pub authorization_epoch: u64,
    pub source_id: u64,
    pub kind: MediaKind,
    pub codec: Codec,
}

impl MediaFence {
    pub fn check(&self, header: &MediaHeader) -> Result<(), ProtocolError> {
        header.validate()?;
        if header.generation != self.generation { return Err(ProtocolError::StaleGeneration); }
        if header.authorization_epoch != self.authorization_epoch { return Err(ProtocolError::Revoked); }
        if header.source_id != self.source_id || header.kind != self.kind || header.codec != self.codec {
            return Err(ProtocolError::ScopeDenied);
        }
        Ok(())
    }
}

/// A small receiver window rejects duplicate/old frames while allowing bounded
/// out-of-order completion of independent streams.
#[derive(Debug, Default)]
pub struct SequenceWindow { newest: Option<u64>, seen: u64 }
impl SequenceWindow {
    pub fn accept(&mut self, sequence: u64) -> bool {
        match self.newest {
            None => { self.newest = Some(sequence); self.seen = 1; true }
            Some(newest) if sequence > newest => {
                self.seen = if sequence - newest >= 64 { 1 } else { (self.seen << (sequence - newest)) | 1 };
                self.newest = Some(sequence); true
            }
            Some(newest) if newest - sequence < 64 => {
                let bit = 1u64 << (newest - sequence);
                if self.seen & bit != 0 { false } else { self.seen |= bit; true }
            }
            _ => false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn audio() -> MediaHeader {
        MediaHeader { kind: MediaKind::Audio, codec: Codec::Pcm16, keyframe: false,
            generation: 7, authorization_epoch: 3, source_id: 9, sequence: 1,
            timestamp_us: 20_000, duration_us: 20_000, length: 1920,
            width: 0, height: 0, channels: 1 }
    }
    #[test]
    fn fixed_media_golden_vector_and_pcm_duration() {
        let header = audio(); let bytes = header.encode().unwrap();
        assert_eq!(&bytes[..8], &[65, 89, 77, 70, 1, 1, 4, 0]);
        assert_eq!(MediaHeader::decode(&bytes).unwrap(), header);
        assert!(MediaHeader { length: 1919, ..header }.validate().is_err());
    }
    #[test]
    fn untrusted_length_and_flags_are_rejected_before_allocation() {
        let mut bytes = audio().encode().unwrap();
        bytes[52..56].copy_from_slice(&u32::MAX.to_be_bytes());
        assert!(MediaHeader::decode(&bytes).is_err());
        let mut bytes = audio().encode().unwrap(); bytes[61] = 1;
        assert!(MediaHeader::decode(&bytes).is_err());
        let mut bytes = audio().encode().unwrap(); bytes[5] = 99;
        assert!(MediaHeader::decode(&bytes).is_err());
    }
    #[test]
    fn source_epoch_codec_and_generation_are_fenced() {
        let header = audio();
        let fence = MediaFence { generation: 7, authorization_epoch: 3, source_id: 9,
            kind: MediaKind::Audio, codec: Codec::Pcm16 };
        fence.check(&header).unwrap();
        assert_eq!(fence.check(&MediaHeader { generation: 6, ..header.clone() }), Err(ProtocolError::StaleGeneration));
        assert_eq!(fence.check(&MediaHeader { authorization_epoch: 2, ..header.clone() }), Err(ProtocolError::Revoked));
        assert_eq!(fence.check(&MediaHeader { source_id: 10, ..header }), Err(ProtocolError::ScopeDenied));
    }
    #[test]
    fn sequence_window_handles_reorder_duplicates_and_integer_edges() {
        let mut window = SequenceWindow::default();
        assert!(window.accept(2)); assert!(window.accept(1)); assert!(!window.accept(1));
        assert!(window.accept(100)); assert!(!window.accept(2)); assert!(window.accept(99));
        assert!(window.accept(u64::MAX)); assert!(!window.accept(u64::MAX)); assert!(!window.accept(0));
    }
    #[test]
    fn arbitrary_short_headers_never_panic() {
        for length in 0..=128 {
            let bytes = vec![255; length];
            assert!(MediaHeader::decode(&bytes).is_err());
        }
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! File descriptors are not storage paths or authorization grants. The host
//! supplies a verified owner scope before creating or resuming a durable sink.

use crate::{Envelope, MessageType, ProtocolError};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub const MAX_FILE_BYTES: u64 = 1024 * 1024 * 1024;
pub const MAX_FILE_METADATA_BYTES: usize = 16 * 1024;
pub const MAX_RETENTION_MS: u64 = 7 * 24 * 60 * 60 * 1000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Purpose { Attachment, Download }

/// This endpoint's consumer bounds for one authenticated connection generation.
/// They are limits, never authorization to use a file capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TransferLimits { pub version: u8, pub attachment_max_bytes: u64, pub download_max_bytes: u64 }
impl Default for TransferLimits {
    fn default() -> Self { Self { version: 1, attachment_max_bytes: MAX_FILE_BYTES, download_max_bytes: MAX_FILE_BYTES } }
}
impl TransferLimits {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.version != 1 || self.attachment_max_bytes == 0 || self.attachment_max_bytes > MAX_FILE_BYTES ||
            self.download_max_bytes == 0 || self.download_max_bytes > MAX_FILE_BYTES { return Err(ProtocolError::InvalidEnvelope); }
        Ok(())
    }
    pub fn permits(&self, descriptor: &Descriptor) -> bool {
        descriptor.total <= match descriptor.purpose { Purpose::Attachment => self.attachment_max_bytes, Purpose::Download => self.download_max_bytes }
    }
    pub fn from_envelope(envelope: &Envelope) -> Result<Self, ProtocolError> {
        if envelope.header.message_type != MessageType::TransportTransferLimits { return Err(ProtocolError::InvalidEnvelope); }
        let value: Self = serde_json::from_value(Value::Object(envelope.payload.clone())).map_err(|_| ProtocolError::InvalidEnvelope)?;
        value.validate()?; Ok(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Descriptor {
    pub transfer_id: String,
    pub purpose: Purpose,
    pub filename: String,
    pub mime_type: String,
    pub total: u64,
    /// The durable prefix retained by the receiver. This connection carries
    /// only the suffix; Finish also verifies the entire assembled file digest.
    pub offset: u64,
    pub sha256: Vec<u8>,
    pub expires_at_ms: u64,
    #[serde(default)]
    pub metadata: Map<String, Value>,
}
impl Descriptor {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if !valid_id(&self.transfer_id) || self.total > MAX_FILE_BYTES || self.offset > self.total || self.sha256.len() != 32 ||
            self.expires_at_ms == 0 || self.filename.is_empty() || self.filename.len() > 255 ||
            self.filename.chars().any(|c| c.is_control() || matches!(c, '/' | '\\')) || matches!(self.filename.as_str(), "." | "..") ||
            self.mime_type.is_empty() || self.mime_type.len() > 255 || !self.mime_type.bytes().all(|b| (32..127).contains(&b)) ||
            serde_json::to_vec(&self.metadata).map_err(|_| ProtocolError::InvalidEnvelope)?.len() > MAX_FILE_METADATA_BYTES {
            return Err(ProtocolError::InvalidEnvelope);
        }
        Ok(())
    }
    pub fn from_envelope(envelope: &Envelope) -> Result<Self, ProtocolError> {
        if envelope.header.message_type != MessageType::BinaryTransferOpen { return Err(ProtocolError::InvalidEnvelope); }
        let descriptor: Self = serde_json::from_value(Value::Object(envelope.payload.clone())).map_err(|_| ProtocolError::InvalidEnvelope)?;
        descriptor.validate()?; Ok(descriptor)
    }
    pub fn same_file(&self, other: &Self) -> bool {
        let mut ours = self.clone(); let mut theirs = other.clone(); ours.offset = 0; theirs.offset = 0; ours == theirs
    }
    pub fn check_expiry(&self, now_ms: u64) -> Result<(), ProtocolError> {
        self.validate()?;
        if self.expires_at_ms <= now_ms || self.expires_at_ms - now_ms > MAX_RETENTION_MS { return Err(ProtocolError::Revoked); }
        Ok(())
    }
}
pub fn valid_id(id: &str) -> bool { id.len() == 32 && id.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StatusPhase { Pending, Committed, Deleted, Unavailable }
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure { Storage, Capacity, Digest, Expired, Cancelled, Stale }
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case", deny_unknown_fields)]
pub enum Control {
    Query { descriptor: Descriptor },
    Cancel { descriptor: Descriptor },
    Status { descriptor: Descriptor, offset: u64, prefix_sha256: Vec<u8>, phase: StatusPhase, failure: Option<Failure> },
}
impl Control {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        let descriptor = match self { Self::Query { descriptor } | Self::Cancel { descriptor } | Self::Status { descriptor, .. } => descriptor };
        descriptor.validate()?;
        if descriptor.offset != 0 { return Err(ProtocolError::InvalidEnvelope); }
        if let Self::Status { offset, prefix_sha256, phase, failure, .. } = self {
            if *offset > descriptor.total || prefix_sha256.len() != 32 ||
                (*phase == StatusPhase::Committed && (*offset != descriptor.total || *prefix_sha256 != descriptor.sha256 || failure.is_some())) ||
                (*phase == StatusPhase::Unavailable && failure.is_none()) { return Err(ProtocolError::InvalidEnvelope); }
        }
        Ok(())
    }
    pub fn from_envelope(envelope: &Envelope) -> Result<Self, ProtocolError> {
        if envelope.header.message_type != MessageType::BinaryTransferControl { return Err(ProtocolError::InvalidEnvelope); }
        let value: Self = serde_json::from_value(Value::Object(envelope.payload.clone())).map_err(|_| ProtocolError::InvalidEnvelope)?;
        value.validate()?; Ok(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn transfer_limits_are_strict_and_cannot_exceed_supported_bytes() {
        let valid = TransferLimits { attachment_max_bytes: 32 * 1024 * 1024, ..Default::default() };
        valid.validate().unwrap();
        for value in [TransferLimits { version: 2, ..valid }, TransferLimits { attachment_max_bytes: 0, ..valid },
            TransferLimits { download_max_bytes: MAX_FILE_BYTES + 1, ..valid }] { assert!(value.validate().is_err()); }
        let mut payload = serde_json::to_value(valid).unwrap(); payload["owner"] = Value::String("synthetic-owner".into());
        assert!(serde_json::from_value::<TransferLimits>(payload).is_err());
    }
    #[test]
    fn descriptors_bound_paths_hashes_sizes_and_expiry_without_conveying_roles() {
        let descriptor = Descriptor { transfer_id: "ab".repeat(16), purpose: Purpose::Attachment, filename: "synthetic.bin".into(),
            mime_type: "application/octet-stream".into(), total: MAX_FILE_BYTES, offset: 0, sha256: vec![3;32],
            expires_at_ms: 1000, metadata: Map::new() };
        descriptor.check_expiry(1).unwrap();
        for mutate in [0,1,2,3,4] {
            let mut bad = descriptor.clone();
            match mutate { 0 => bad.filename = "../synthetic".into(), 1 => bad.transfer_id = "../synthetic".into(),
                2 => bad.sha256.clear(), 3 => bad.total += 1, _ => bad.offset = bad.total + 1 }
            assert!(bad.validate().is_err());
        }
        assert!(descriptor.check_expiry(1000).is_err());
        let mut resumed = descriptor.clone(); resumed.offset = 48 * 1024;
        assert!(descriptor.same_file(&resumed)); resumed.sha256[0] ^= 1; assert!(!descriptor.same_file(&resumed));
    }
    #[test]
    fn durable_receipts_require_whole_file_completion_and_cannot_smuggle_authority_or_bodies() {
        let descriptor = Descriptor { transfer_id: "ab".repeat(16), purpose: Purpose::Attachment, filename: "synthetic.bin".into(),
            mime_type: "application/octet-stream".into(), total: 3, offset: 0, sha256: vec![3;32], expires_at_ms: 1000, metadata: Map::new() };
        let receipt = Control::Status { descriptor, offset: 3, prefix_sha256: vec![3;32], phase: StatusPhase::Committed, failure: None };
        receipt.validate().unwrap();
        for extra in ["body", "owner_key", "data", "generation"] {
            let mut value = serde_json::to_value(&receipt).unwrap(); value[extra] = "untrusted".into();
            assert!(serde_json::from_value::<Control>(value).is_err());
        }
        let mut wrong = receipt;
        if let Control::Status { prefix_sha256, .. } = &mut wrong { prefix_sha256[0] ^= 1; }
        assert!(wrong.validate().is_err());
    }
}

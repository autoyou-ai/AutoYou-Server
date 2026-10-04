// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Receipts for admission of ordinary computer prompts. They never attest to
//! completion of a model, tool, payment, or other external side effect.
use crate::{Envelope, MessageType, ProtocolError};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

pub const MAX_TTL_MS: u64 = 7 * 24 * 60 * 60 * 1000;
pub const MAX_BODY_BYTES: usize = 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Stamp { pub version: u8, pub revision: String, pub expires_at_ms: u64 }
impl Stamp {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.version != 1 || !hex(&self.revision, 32) || self.expires_at_ms == 0 { return Err(ProtocolError::InvalidEnvelope); }
        Ok(())
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status { Missing, Queued, Pending, Accepted, Uncertain, Deleted, Expired }

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case", deny_unknown_fields)]
pub enum Control {
    Sync { revision: String },
    Query { operation_id: String, digest: Vec<u8>, revision: String, expires_at_ms: u64 },
    Receipt { operation_id: String, digest: Vec<u8>, revision: String, expires_at_ms: u64, status: Status },
}
pub fn identifier(value: &str) -> bool { !value.is_empty() && value.len() <= 128 && !value.chars().any(char::is_control) }
fn hex(value: &str, size: usize) -> bool { value.len() == size && value.bytes().all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v)) }
impl Control {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        let valid = match self {
            Self::Sync { revision } => hex(revision, 32),
            Self::Query { operation_id, digest, revision, expires_at_ms } => identifier(operation_id) && digest.len() == 32 && hex(revision, 32) && *expires_at_ms > 0,
            Self::Receipt { operation_id, digest, revision, expires_at_ms, status } => identifier(operation_id) && digest.len() == 32 && hex(revision, 32) && *expires_at_ms > 0 && *status != Status::Queued,
        };
        if valid { Ok(()) } else { Err(ProtocolError::InvalidEnvelope) }
    }
    pub fn from_envelope(value: &Envelope) -> Result<Self, ProtocolError> {
        if value.header.message_type != MessageType::ApplicationDeliveryControl { return Err(ProtocolError::InvalidEnvelope); }
        let control: Self = serde_json::from_value(serde_json::Value::Object(value.payload.clone())).map_err(|_| ProtocolError::InvalidEnvelope)?;
        control.validate()?; Ok(control)
    }
}

/// Control actions and relayed/social chat are live-only. Payload bytes are
/// canonicalized here so every FFI consumer uses one operation fingerprint.
pub fn prompt_digest(envelope: &Envelope) -> Result<Vec<u8>, ProtocolError> {
    envelope.validate()?;
    if envelope.header.message_type != MessageType::Chat || !identifier(&envelope.header.message_id) ||
        !envelope.payload.get("message").is_some_and(serde_json::Value::is_string) {
        return Err(ProtocolError::InvalidEnvelope);
    }
    if envelope.payload.get("metadata").is_some_and(|v| v.as_object().is_none_or(|m|
        ["conversation_action", "room_bridge", "peer_link", "peer_relay", "room"].iter().any(|key| m.contains_key(*key)))) {
        return Err(ProtocolError::InvalidEnvelope);
    }
    if let Some(context)=envelope.payload.get("context") {
        let context=context.as_array().ok_or(ProtocolError::InvalidEnvelope)?;
        if context.len()>256 { return Err(ProtocolError::InvalidEnvelope); }
        let mut count=0;
        for item in context {
            let item=item.as_object().ok_or(ProtocolError::InvalidEnvelope)?;
            if let Some(attachments)=item.get("attachments") {
                let attachments=attachments.as_array().ok_or(ProtocolError::InvalidEnvelope)?;
                count+=attachments.len(); if count>64 {return Err(ProtocolError::InvalidEnvelope);}
                for attachment in attachments {
                    let attachment=attachment.as_object().ok_or(ProtocolError::InvalidEnvelope)?;
                    if ["data","path","local_path","localFilePath"].iter().any(|key|attachment.get(*key).is_some_and(|v|!v.is_null())) {
                        return Err(ProtocolError::InvalidEnvelope);
                    }
                    if let Some(reference)=attachment.get("file_ref") {
                        let descriptor:crate::binary::Descriptor=serde_json::from_value(reference.clone()).map_err(|_|ProtocolError::InvalidEnvelope)?;
                        descriptor.validate()?;
                    }
                }
            }
        }
    }
    let payload = serde_json::to_vec(&envelope.payload).map_err(|_| ProtocolError::InvalidEnvelope)?;
    if payload.len() > MAX_BODY_BYTES { return Err(ProtocolError::FrameTooLarge); }
    Ok(Sha256::digest([b"AutoYou-prompt-admission/1\0".as_slice(), &payload].concat()).to_vec())
}
pub fn stamp(envelope: &Envelope) -> Result<Stamp, ProtocolError> {
    let value: Stamp = serde_json::from_value(envelope.extensions.get("delivery").ok_or(ProtocolError::InvalidEnvelope)?.clone())
        .map_err(|_| ProtocolError::InvalidEnvelope)?;
    value.validate()?; Ok(value)
}

/// A prompt cannot remain retryable longer than any file capability it needs.
/// Called after prompt_digest has validated the context and descriptors.
pub fn attachment_expiry(envelope: &Envelope, expires_at_ms: u64) -> u64 {
    let mut expiry = expires_at_ms;
    if let Some(context) = envelope.payload.get("context").and_then(serde_json::Value::as_array) {
        for item in context {
            if let Some(attachments) = item.get("attachments").and_then(serde_json::Value::as_array) {
                for attachment in attachments {
                    if let Some(value) = attachment.get("file_ref").and_then(|v| v.get("expires_at_ms")).and_then(serde_json::Value::as_u64) {
                        expiry = expiry.min(value);
                    }
                }
            }
        }
    }
    expiry
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn receipt_is_typed_bounded_and_never_accepts_a_queued_claim() {
        let query = Control::Query { operation_id: "synthetic-operation".into(), digest: vec![9;32], revision: "ab".repeat(16), expires_at_ms: 1000 };
        query.validate().unwrap();
        let malformed = serde_json::json!({"event":"query","operation_id":"synthetic","digest":[9],"revision":"ab".repeat(16),"expires_at_ms":1000});
        assert!(serde_json::from_value::<Control>(malformed).unwrap().validate().is_err());
        assert!(Control::Receipt { operation_id:"synthetic".into(),digest:vec![9;32], revision:"ab".repeat(16),expires_at_ms:1000,status:Status::Queued }.validate().is_err());
        assert!(serde_json::from_value::<Control>(serde_json::json!({"event":"sync","revision":"ab".repeat(16),"owner":"forged"})).is_err());
    }
    #[test]
    fn fingerprint_ignores_connection_headers_but_live_controls_cannot_be_replayed() {
        let mut prompt = Envelope::from_slice(br#"{"header":{"message_id":"synthetic","message_type":"chat","timestamp":1},"payload":{"message":"synthetic prompt"}}"#).unwrap();
        let digest = prompt_digest(&prompt).unwrap();
        prompt.header.session_id=Some("different-generation".into()); assert_eq!(prompt_digest(&prompt).unwrap(),digest);
        prompt.payload.insert("metadata".into(),serde_json::json!({"conversation_action":"delete_server_history"}));
        assert!(prompt_digest(&prompt).is_err());
    }
}

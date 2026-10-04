// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Connection confirmation after the existing pairing owner grants an endpoint.
//! The challenge binds routing identity to this TLS exporter; it is not a grant.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use crate::{ProtocolError, PAIR_ALPN, SESSION_ALPN, transcript_binding};

pub const MAX_ENROLLMENT_BYTES: usize = 16*1024;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Challenge {
    pub initiator_endpoint: String, pub acceptor_endpoint: String,
    pub nonce: Vec<u8>, pub generation: u64, pub authorization_epoch: u64,
    pub expires_at_ms: u64, pub scopes: Vec<String>, pub capabilities: Map<String, Value>,
}
impl Challenge {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.nonce.len() != 32 || self.generation == 0 || self.expires_at_ms == 0 ||
            [&self.initiator_endpoint, &self.acceptor_endpoint].iter().any(|s| s.is_empty() || s.len() > 128) ||
            self.scopes.len() > 32 || self.scopes.iter().any(|s| s.is_empty() || s.len() > 64) ||
            self.capabilities.contains_key("_grant") || self.capabilities.len() > 64 ||
            serde_json::to_vec(&self.capabilities).map_err(|_| ProtocolError::InvalidEnvelope)?.len() > 8192 {
            return Err(ProtocolError::InvalidEnvelope);
        }
        let mut scopes = self.scopes.clone(); scopes.sort(); scopes.dedup();
        if scopes.len() != self.scopes.len() { return Err(ProtocolError::InvalidEnvelope); }
        Ok(())
    }
    pub fn binding(&self, exporter: &[u8;32], initiator: &[u8;32], acceptor: &[u8;32]) -> Result<[u8;32], ProtocolError> {
        self.binding_for_alpn(exporter, initiator, acceptor, SESSION_ALPN)
    }
    pub fn pairing_binding(&self, exporter: &[u8;32], initiator: &[u8;32], acceptor: &[u8;32]) -> Result<[u8;32], ProtocolError> {
        self.binding_for_alpn(exporter, initiator, acceptor, PAIR_ALPN)
    }
    fn binding_for_alpn(&self, exporter: &[u8;32], initiator: &[u8;32], acceptor: &[u8;32], alpn: &[u8]) -> Result<[u8;32], ProtocolError> {
        self.validate()?;
        let mut capabilities = self.capabilities.clone();
        let mut scopes = self.scopes.clone(); scopes.sort();
        capabilities.insert("_grant".into(), serde_json::json!({"scopes": scopes, "expires_at_ms": self.expires_at_ms}));
        let capabilities = serde_json::to_vec(&capabilities).map_err(|_| ProtocolError::InvalidEnvelope)?;
        let mut protocol = alpn.to_vec();
        protocol.extend_from_slice(&self.generation.to_be_bytes());
        protocol.extend_from_slice(&self.authorization_epoch.to_be_bytes());
        let nonce = self.nonce.as_slice().try_into().map_err(|_| ProtocolError::InvalidEnvelope)?;
        Ok(transcript_binding(exporter, initiator, acceptor, nonce, &protocol, &capabilities))
    }
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Kind { Redeem, Challenge, Confirm, Ready }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct Message {
    pub version: u8, pub kind: Kind,
    #[serde(default, skip_serializing_if="Option::is_none")] pub challenge: Option<Challenge>,
    #[serde(default, skip_serializing_if="Option::is_none")] pub binding: Option<Vec<u8>>,
}
impl Message {
    pub fn validate(&self) -> Result<(), ProtocolError> {
        if self.version != 1 { return Err(ProtocolError::UnsupportedVersion); }
        match self.kind {
            Kind::Challenge if self.binding.is_none() => self.challenge.as_ref().ok_or(ProtocolError::InvalidEnvelope)?.validate(),
            Kind::Redeem | Kind::Confirm | Kind::Ready if self.challenge.is_none() && self.binding.as_ref().is_some_and(|b| b.len() == 32) => Ok(()),
            _ => Err(ProtocolError::InvalidEnvelope),
        }
    }
    pub fn from_slice(bytes: &[u8]) -> Result<Self, ProtocolError> {
        if bytes.len() > MAX_ENROLLMENT_BYTES { return Err(ProtocolError::FrameTooLarge); }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| ProtocolError::InvalidEnvelope)?;
        value.validate()?; Ok(value)
    }
    pub fn to_vec(&self) -> Result<Vec<u8>, ProtocolError> {
        self.validate()?;
        let bytes = serde_json::to_vec(self).map_err(|_| ProtocolError::InvalidEnvelope)?;
        if bytes.len() > MAX_ENROLLMENT_BYTES { return Err(ProtocolError::FrameTooLarge); }
        Ok(bytes)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn challenge() -> Challenge {
        Challenge { initiator_endpoint: "synthetic-initiator".into(), acceptor_endpoint: "synthetic-acceptor".into(),
            nonce: vec![3;32], generation: 7, authorization_epoch: 2, expires_at_ms: 1000,
            scopes: vec!["chat".into()], capabilities: Map::new() }
    }
    #[test]
    fn grant_fields_and_exporter_are_bound_without_capability_ambiguity() {
        let original = challenge(); let expected = original.binding(&[1;32], &[2;32], &[3;32]).unwrap();
        let mut changed = original.clone(); changed.generation += 1;
        assert_ne!(changed.binding(&[1;32], &[2;32], &[3;32]).unwrap(), expected);
        changed = original.clone(); changed.expires_at_ms += 1;
        assert_ne!(changed.binding(&[1;32], &[2;32], &[3;32]).unwrap(), expected);
        changed = original.clone(); changed.scopes.push("control".into());
        assert_ne!(changed.binding(&[1;32], &[2;32], &[3;32]).unwrap(), expected);
        assert_ne!(original.binding(&[4;32], &[2;32], &[3;32]).unwrap(), expected);
        changed.capabilities.insert("_grant".into(), serde_json::json!({}));
        assert!(changed.validate().is_err());
    }
    #[test]
    fn only_unambiguous_versioned_enrollment_records_are_accepted() {
        let message = Message { version: 1, kind: Kind::Challenge, challenge: Some(challenge()), binding: None };
        assert_eq!(Message::from_slice(&message.to_vec().unwrap()).unwrap(), message);
        let mut ambiguous = message.clone(); ambiguous.binding = Some(vec![1;32]);
        assert!(ambiguous.to_vec().is_err());
        let mut unknown: Value = serde_json::from_slice(&message.to_vec().unwrap()).unwrap();
        unknown["owner_id"] = Value::String("forged-owner".into());
        assert!(Message::from_slice(&serde_json::to_vec(&unknown).unwrap()).is_err());
        assert!(Message::from_slice(&vec![b'x'; MAX_ENROLLMENT_BYTES+1]).is_err());
    }
    #[test]
    fn pairing_redemption_is_bounded_and_cannot_confirm_an_application_session() {
        let message = Message { version: 1, kind: Kind::Redeem, challenge: None, binding: Some(vec![7;32]) };
        assert_eq!(Message::from_slice(&message.to_vec().unwrap()).unwrap(), message);
        let mut oversized = message.clone(); oversized.binding = Some(vec![7;33]);
        assert!(oversized.to_vec().is_err());
        let challenge = challenge();
        assert_ne!(challenge.binding(&[1;32], &[2;32], &[3;32]).unwrap(),
            challenge.pairing_binding(&[1;32], &[2;32], &[3;32]).unwrap());
    }
}

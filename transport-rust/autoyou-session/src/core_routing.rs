// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
//! Core controls cloud routing, not the application's pairing grants.
use base64::{Engine, engine::general_purpose::STANDARD};
use iroh::{EndpointAddr, SecretKey, TransportAddr};
use iroh_tickets::endpoint::EndpointTicket;
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::Deserialize;
use zeroize::Zeroize;
use crate::{file_store::hex, host::{endpoint_bytes, EndpointPolicy, HostError}};

const PROOF_DOMAIN: &[u8] = b"autoyou-core-endpoint-proof-v1\n";
const RECORD_DOMAIN: &[u8] = b"autoyou-core-routing-v1\n";
const MAX_EPOCH: u64 = (1 << 53) - 1;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Challenge {
    schema_version: u8, issuer: String, owner_id: String, device_id: String,
    endpoint_id: String, epoch: u64, nonce: String, issued_at: u64, expires_at: u64,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope { schema_version: u8, key_id: String, payload: String, signature: String }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema_version: u8, issuer: String, audience: String, owner_id: String, device_id: String,
    endpoint_id: String, epoch: u64, issued_at: u64, expires_at: u64, relay_urls: Vec<String>,
}

pub struct RoutingRecord {
    pub epoch: u64, pub expires_at_ms: u64, pub ticket: String,
}

fn identifier(value: &str) -> bool {
    value.len() == 15 && value.bytes().all(|v| v.is_ascii_digit() || v.is_ascii_lowercase())
}
fn unhex<const N: usize>(value: &str) -> Result<[u8; N], HostError> {
    if value.len() != N * 2 || !value.bytes().all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v)) {
        return Err(HostError::NotAuthorized);
    }
    let mut bytes = [0; N];
    for (index, target) in bytes.iter_mut().enumerate() {
        *target = u8::from_str_radix(&value[index*2..index*2+2], 16).map_err(|_|HostError::NotAuthorized)?;
    }
    Ok(bytes)
}
fn payload(value: &str) -> Result<Vec<u8>, HostError> {
    if value.is_empty() || value.len() > 4096 { return Err(HostError::NotAuthorized); }
    let raw = STANDARD.decode(value).map_err(|_| HostError::NotAuthorized)?;
    if STANDARD.encode(&raw) != value { return Err(HostError::NotAuthorized); }
    Ok(raw)
}

pub fn sign_endpoint_proof(mut key: [u8;32], encoded: &str, issuer: &str,
    owner: &str, device: &str, now_ms: u64) -> Result<String, HostError> {
    let secret = SecretKey::from_bytes(&key); key.zeroize();
    let raw = payload(encoded)?;
    let value: Challenge = serde_json::from_slice(&raw).map_err(|_| HostError::NotAuthorized)?;
    let now = now_ms / 1000;
    if value.schema_version != 1 || value.issuer != issuer || issuer.is_empty() || issuer.len() > 2048
        || !identifier(owner) || !identifier(device) || value.owner_id != owner || value.device_id != device
        || value.endpoint_id != secret.public().to_string() || value.epoch >= MAX_EPOCH
        || unhex::<32>(&value.nonce).is_err() || value.issued_at > now
        || value.expires_at != value.issued_at.saturating_add(60) || now >= value.expires_at {
        return Err(HostError::NotAuthorized);
    }
    Ok(hex(&secret.sign(&[PROOF_DOMAIN, raw.as_slice()].concat()).to_bytes()))
}

pub fn verify_routing_record(encoded: &str, public_key: &str, issuer: &str,
    owner: &str, device: &str, endpoint: &str, minimum_epoch: u64,
    now_ms: u64, policy: &EndpointPolicy) -> Result<RoutingRecord, HostError> {
    if encoded.len() > 8192 { return Err(HostError::NotAuthorized); }
    let envelope: Envelope = serde_json::from_str(encoded).map_err(|_| HostError::NotAuthorized)?;
    if envelope.schema_version != 1 || envelope.key_id != public_key { return Err(HostError::NotAuthorized); }
    let public = unhex::<32>(public_key)?; let signature = unhex::<64>(&envelope.signature)?;
    let raw = payload(&envelope.payload)?;
    UnparsedPublicKey::new(&ED25519, public).verify(&[RECORD_DOMAIN, raw.as_slice()].concat(), &signature)
        .map_err(|_| HostError::NotAuthorized)?;
    let value: Record = serde_json::from_slice(&raw).map_err(|_| HostError::NotAuthorized)?;
    let now = now_ms / 1000;
    if value.schema_version != 1 || value.issuer != issuer || issuer.is_empty() || issuer.len() > 2048
        || value.audience != "autoyou-iroh-routing" || !identifier(owner) || !identifier(device)
        || value.owner_id != owner || value.device_id != device || value.endpoint_id != endpoint
        || value.epoch == 0 || value.epoch < minimum_epoch || value.epoch > MAX_EPOCH
        || value.issued_at > now.saturating_add(30) || now >= value.expires_at
        || value.expires_at != value.issued_at.saturating_add(120) || value.relay_urls.len() > 8 {
        return Err(HostError::NotAuthorized);
    }
    // Neither an envelope's key_id nor a new relay URL can replace pinned local policy.
    policy.validate()?;
    let endpoint = iroh::EndpointId::from_bytes(&endpoint_bytes(endpoint)?).map_err(|_| HostError::NotAuthorized)?;
    let mut addresses = Vec::new();
    for url in value.relay_urls {
        let relay: iroh::RelayUrl = url.parse().map_err(|_| HostError::InvalidTicket)?;
        if addresses.contains(&TransportAddr::Relay(relay.clone())) { return Err(HostError::InvalidTicket); }
        addresses.push(TransportAddr::Relay(relay));
    }
    let ticket = EndpointTicket::new(EndpointAddr::new(endpoint).with_addrs(addresses)).to_string();
    // Empty remote routing records are not used to discard an enrolled LAN ticket.
    policy.ticket_address(&ticket, &endpoint.to_string())?;
    Ok(RoutingRecord { epoch: value.epoch, expires_at_ms: value.expires_at.saturating_mul(1000), ticket })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::host::RelayPolicy;
    fn fixture() -> serde_json::Value {
        serde_json::from_str(include_str!("../../tests/fixtures/core-routing-v1.json")).unwrap()
    }
    #[test]
    fn core_golden_proof_is_domain_separated_and_binds_current_identity() {
        let data = fixture(); let raw = data["challenge_payload"].as_str().unwrap();
        assert_eq!(sign_endpoint_proof([17;32], raw, "https://core.example.invalid", "account00000001",
            "client000000001", 1_800_000_000_000).unwrap(), data["proof_signature"].as_str().unwrap());
        for (owner,device) in [("account00000002","client000000001"),("account00000001","client000000002")] {
            assert!(sign_endpoint_proof([17;32],raw,"https://core.example.invalid",owner,device,1_800_000_000_000).is_err());
        }
        assert!(sign_endpoint_proof([18;32],raw,"https://core.example.invalid","account00000001","client000000001",1_800_000_000_000).is_err());
        assert!(sign_endpoint_proof([17;32],raw,"https://core.example.invalid","account00000001","client000000001",1_800_000_060_000).is_err());
    }
    #[test]
    fn core_golden_record_cannot_rotate_endpoint_trust_policy_or_epoch() {
        let data = fixture(); let encoded = data["routing_envelope"].to_string();
        let public = data["core_public_key"].as_str().unwrap(); let endpoint = data["endpoint_id"].as_str().unwrap();
        let mut policy = EndpointPolicy::local();
        policy.relays.push(RelayPolicy { url:"http://127.0.0.1:32123/".into(), token:"synthetic-token".into() });
        let check = |wire:&str,key:&str,remote:&str,floor:u64,now:u64,p:&EndpointPolicy| verify_routing_record(wire,key,
            "https://core.example.invalid","account00000001","client000000001",remote,floor,now,p);
        let record = check(&encoded,public,endpoint,1,1_800_000_000_000,&policy).unwrap();
        assert_eq!(record.epoch,4);assert_eq!(record.expires_at_ms,1_800_000_120_000);
        assert!(check(&encoded,public,endpoint,5,1_800_000_000_000,&policy).is_err());
        assert!(check(&encoded,public,endpoint,1,1_800_000_120_000,&policy).is_err());
        assert!(check(&encoded,public,&SecretKey::from_bytes(&[18;32]).public().to_string(),1,1_800_000_000_000,&policy).is_err());
        assert!(check(&encoded,&SecretKey::from_bytes(&[18;32]).public().to_string(),endpoint,1,1_800_000_000_000,&policy).is_err());
        assert!(check(&encoded,public,endpoint,1,1_800_000_000_000,&EndpointPolicy::local()).is_err());
        let mut forged = data["routing_envelope"].clone();forged["signature"]=serde_json::Value::String("00".repeat(64));
        assert!(check(&forged.to_string(),public,endpoint,1,1_800_000_000_000,&policy).is_err());
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
//! Core controls cloud routing, not the application's pairing grants.
use base64::{Engine, engine::general_purpose::STANDARD};
use iroh::{EndpointAddr, SecretKey, TransportAddr};
use iroh_tickets::endpoint::EndpointTicket;
use ring::signature::{UnparsedPublicKey, ED25519};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use sha2::{Digest, Sha256};
use zeroize::Zeroize;
use crate::{file_store::hex, host::{endpoint_bytes, EndpointPolicy, HostError}};

const PROOF_DOMAIN: &[u8] = b"autoyou-core-endpoint-proof-v1\n";
const RECORD_DOMAIN: &[u8] = b"autoyou-core-routing-v1\n";
const RELAY_DOMAIN: &[u8] = b"autoyou-core-relays-v1\n";
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
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelayCredential { pub url: String, pub token: String }
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RelayRecord {
    schema_version:u8, issuer:String, audience:String, owner_id:String, device_id:String,
    endpoint_id:String, epoch:u64, issued_at:u64, expires_at:u64, relays:Vec<RelayCredential>,
}
pub struct RelayConfiguration { pub epoch:u64, pub expires_at_ms:u64, pub relays:Vec<RelayCredential> }

#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Floor { endpoint:String, epoch:u64 }
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Store { schema:u8, clock_floor_ms:u64, records:BTreeMap<String,Floor> }

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
fn verify_envelope(encoded:&str,public_key:&str,domain:&[u8],maximum:usize)->Result<Vec<u8>,HostError> {
    if encoded.len()>maximum*2 { return Err(HostError::NotAuthorized); }
    let envelope:Envelope=serde_json::from_str(encoded).map_err(|_|HostError::NotAuthorized)?;
    if envelope.schema_version!=1 || envelope.key_id!=public_key || envelope.payload.len()>maximum {
        return Err(HostError::NotAuthorized);
    }
    let public=unhex::<32>(public_key)?;let signature=unhex::<64>(&envelope.signature)?;
    let raw=STANDARD.decode(&envelope.payload).map_err(|_|HostError::NotAuthorized)?;
    if STANDARD.encode(&raw)!=envelope.payload { return Err(HostError::NotAuthorized); }
    UnparsedPublicKey::new(&ED25519,public).verify(&[domain,raw.as_slice()].concat(),&signature)
        .map_err(|_|HostError::NotAuthorized)?;
    Ok(raw)
}

pub fn empty_store()->Vec<u8> { br#"{"schema":1,"clock_floor_ms":0,"records":{}}"#.to_vec() }
fn store(bytes:&[u8],issuer:&str,public_key:&str,owner:&str,device:&str,endpoint:&str)
    ->Result<(Store,String,u64),HostError> {
    if bytes.len()>2*1024*1024 { return Err(HostError::InvalidConfig); }
    let value:Store=serde_json::from_slice(bytes).map_err(|_|HostError::InvalidConfig)?;
    if value.schema!=1 || value.records.len()>4096 || value.records.iter().any(|(key,row)|
        unhex::<32>(key).is_err() || unhex::<32>(&row.endpoint).is_err() || row.epoch==0 || row.epoch>MAX_EPOCH) {
        return Err(HostError::InvalidConfig);
    }
    let context=serde_json::to_vec(&[issuer,public_key,owner,device]).map_err(|_|HostError::InvalidConfig)?;
    let key=hex(&Sha256::digest(context));
    let epoch=if let Some(row)=value.records.get(&key) {
        if row.endpoint!=endpoint { return Err(HostError::NotAuthorized); } row.epoch
    } else { if value.records.len()>=4096 { return Err(HostError::Backpressure); } 0 };
    Ok((value,key,epoch))
}
fn save(mut value:Store,key:String,endpoint:&str,epoch:u64,now_ms:u64)->Result<Vec<u8>,HostError> {
    value.clock_floor_ms=value.clock_floor_ms.max(now_ms);
    value.records.insert(key,Floor { endpoint:endpoint.into(),epoch });
    serde_json::to_vec(&value).map_err(|_|HostError::InvalidConfig)
}

pub fn accept_routing_record(bytes:&[u8],encoded:&str,public_key:&str,issuer:&str,owner:&str,
    device:&str,endpoint:&str,now_ms:u64,policy:&EndpointPolicy)->Result<(RoutingRecord,Vec<u8>),HostError> {
    let (state,key,epoch)=store(bytes,issuer,public_key,owner,device,endpoint)?;
    let now=now_ms.max(state.clock_floor_ms);
    let record=verify_routing_record(encoded,public_key,issuer,owner,device,endpoint,epoch,now,policy)?;
    let saved=save(state,key,endpoint,record.epoch,now)?;Ok((record,saved))
}

pub fn accept_relay_configuration(bytes:&[u8],encoded:&str,public_key:&str,issuer:&str,owner:&str,
    device:&str,endpoint:&str,now_ms:u64,policy:&EndpointPolicy)->Result<(RelayConfiguration,Vec<u8>),HostError> {
    let (state,key,epoch)=store(bytes,issuer,public_key,owner,device,endpoint)?;
    let now=now_ms.max(state.clock_floor_ms);
    let record=verify_relay_configuration(encoded,public_key,issuer,owner,device,endpoint,epoch,now,policy)?;
    let saved=save(state,key,endpoint,record.epoch,now)?;Ok((record,saved))
}

pub fn verify_relay_configuration(encoded:&str,public_key:&str,issuer:&str,owner:&str,device:&str,
    endpoint:&str,minimum_epoch:u64,now_ms:u64,policy:&EndpointPolicy)->Result<RelayConfiguration,HostError> {
    let raw=verify_envelope(encoded,public_key,RELAY_DOMAIN,64*1024)?;
    let value:RelayRecord=serde_json::from_slice(&raw).map_err(|_|HostError::NotAuthorized)?;
    let now=now_ms/1000;
    if value.schema_version!=1 || value.audience!="autoyou-iroh-relays" || issuer.is_empty() || issuer.len()>2048
        || value.issuer!=issuer || !identifier(owner) || !identifier(device) || value.owner_id!=owner
        || value.device_id!=device || value.endpoint_id!=endpoint || value.epoch==0 || value.epoch<minimum_epoch
        || value.epoch>MAX_EPOCH || value.issued_at>now.saturating_add(30) || now>=value.expires_at
        || value.expires_at!=value.issued_at.saturating_add(120) || value.relays.is_empty() || value.relays.len()>8 {
        return Err(HostError::NotAuthorized);
    }
    policy.validate()?;
    let mut urls=Vec::new();
    for relay in &value.relays {
        let url:iroh::RelayUrl=relay.url.parse().map_err(|_|HostError::InvalidConfig)?;
        if relay.token.is_empty() || relay.token.len()>4096 || urls.contains(&url)
            || !policy.relays.iter().any(|approved| approved.url.parse::<iroh::RelayUrl>().ok()==Some(url.clone())) {
            return Err(HostError::InvalidConfig);
        }
        urls.push(url);
    }
    Ok(RelayConfiguration { epoch:value.epoch,expires_at_ms:value.expires_at.saturating_mul(1000),relays:value.relays })
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
    let raw=verify_envelope(encoded,public_key,RECORD_DOMAIN,4096)?;
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
    #[test]
    fn protected_core_floors_refuse_epoch_pin_clock_and_schema_rewind() {
        let data=fixture();let encoded=data["routing_envelope"].to_string();
        let key=data["core_public_key"].as_str().unwrap();let endpoint=data["endpoint_id"].as_str().unwrap();
        let mut policy=EndpointPolicy::local();policy.relays.push(RelayPolicy {
            url:"http://127.0.0.1:32123/".into(),token:"synthetic-token".into() });
        let accept=|state:&[u8],remote:&str,now:u64|accept_routing_record(state,&encoded,key,
            "https://core.example.invalid","account00000001","client000000001",remote,now,&policy);
        let (_,saved)=accept(&empty_store(),endpoint,1_800_000_100_000).unwrap();
        // A wall-clock rollback still evaluates TTL against the protected clock floor.
        let (_,again)=accept(&saved,endpoint,1_800_000_000_000).unwrap();
        let mut state:serde_json::Value=serde_json::from_slice(&again).unwrap();
        assert_eq!(state["clock_floor_ms"],1_800_000_100_000u64);
        state["clock_floor_ms"]=serde_json::json!(1_800_000_120_000u64);
        assert!(accept(&serde_json::to_vec(&state).unwrap(),endpoint,1_800_000_000_000).is_err());
        state=serde_json::from_slice(&again).unwrap();
        let record=state["records"].as_object_mut().unwrap().values_mut().next().unwrap();record["epoch"]=serde_json::json!(5);
        assert!(accept(&serde_json::to_vec(&state).unwrap(),endpoint,1_800_000_100_000).is_err());
        assert!(accept(&saved,&SecretKey::from_bytes(&[18;32]).public().to_string(),1_800_000_100_000).is_err());
        assert!(accept(b"corrupt",endpoint,1_800_000_000_000).is_err());
        assert!(accept(br#"{"schema":2,"clock_floor_ms":0,"records":{}}"#,endpoint,1_800_000_000_000).is_err());
    }
    #[test]
    fn relay_configuration_has_dedicated_domain_current_epoch_and_approved_urls() {
        let data=fixture();let encoded=data["relay_envelope"].to_string();
        let key=data["core_public_key"].as_str().unwrap();let endpoint=data["endpoint_id"].as_str().unwrap();
        let mut policy=EndpointPolicy::local();policy.relays.push(RelayPolicy {
            url:"http://127.0.0.1:32123/".into(),token:"synthetic-token".into() });
        let accept=|state:&[u8],wire:&str,owner:&str,now:u64,p:&EndpointPolicy|accept_relay_configuration(state,wire,key,
            "https://core.example.invalid",owner,"client000000001",endpoint,now,p);
        let (relay,saved)=accept(&empty_store(),&encoded,"account00000001",1_800_000_000_000,&policy).unwrap();
        assert_eq!(relay.epoch,4);assert_eq!(relay.relays.len(),1);assert!(!relay.relays[0].token.is_empty());
        assert!(accept(&saved,&data["routing_envelope"].to_string(),"account00000001",1_800_000_000_000,&policy).is_err());
        assert!(accept(&saved,&encoded,"account00000002",1_800_000_000_000,&policy).is_err());
        assert!(accept(&saved,&encoded,"account00000001",1_800_000_120_000,&policy).is_err());
        assert!(accept(&saved,&encoded,"account00000001",1_800_000_000_000,&EndpointPolicy::local()).is_err());
    }
}

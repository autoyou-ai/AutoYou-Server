// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Protected client grant format shared by all native hosts. These pure
//! transactions never touch a filesystem or credential store. A host must
//! atomically persist the returned bytes before admitting a connection.

use crate::client::{ClientError, ClientGrant};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const MAX_STORE: usize = 16 * 1024 * 1024;
const MAX_DEVICES: usize = 4096;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    grant: ClientGrant, generation: u64, revoked: bool,
    #[serde(default,skip_serializing_if="std::ops::Not::not")] core_denied:bool,
    #[serde(default,skip_serializing_if="Option::is_none")] core_device:Option<String>,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct State { schema: u8, devices: BTreeMap<String, Row> }

#[derive(Clone)]
pub struct StoredPeer { pub grant: ClientGrant, pub generation_floor: u64, pub core_device:Option<String> }

fn decode(bytes: &[u8]) -> Result<State, ClientError> {
    if bytes.len() > MAX_STORE { return Err(ClientError::Invalid); }
    let state: State = serde_json::from_slice(bytes).map_err(|_| ClientError::Invalid)?;
    if state.schema != 1 || state.devices.len() > MAX_DEVICES { return Err(ClientError::Invalid); }
    let mut endpoints = BTreeSet::new();
    for (device, row) in &state.devices {
        row.grant.validate(0)?;
        if device != &row.grant.device_id || !endpoints.insert(&row.grant.endpoint_id) ||
            row.core_device.as_ref().is_some_and(|device|!core_identifier(device)) {
            return Err(ClientError::Invalid);
        }
    }
    Ok(state)
}

fn encode(state: &State) -> Result<Vec<u8>, ClientError> {
    let bytes = serde_json::to_vec(state).map_err(|_| ClientError::Invalid)?;
    if bytes.len() > MAX_STORE { return Err(ClientError::Invalid); }
    Ok(bytes)
}

pub fn empty() -> Vec<u8> { br#"{"schema":1,"devices":{}}"#.to_vec() }

pub fn next_authorization_epoch(bytes: &[u8], device: &str) -> Result<u64, ClientError> {
    let state = decode(bytes)?;
    state.devices.get(device).map_or(Ok(1), |row| row.grant.authorization_epoch.checked_add(1).ok_or(ClientError::Denied))
}

fn same_association(a: &ClientGrant, b: &ClientGrant) -> bool {
    a.endpoint_id == b.endpoint_id && a.device_id == b.device_id && a.owner_key == b.owner_key &&
        a.canonical_user_id == b.canonical_user_id && a.conversation_key == b.conversation_key
}

pub fn register(bytes: &[u8], grant: ClientGrant, now_ms: u64) -> Result<Vec<u8>, ClientError> {
    grant.validate(now_ms)?;
    let mut state = decode(bytes)?;
    let old = state.devices.get(&grant.device_id);
    if let Some(old) = old {
        if !same_association(&grant, &old.grant) || grant.authorization_epoch < old.grant.authorization_epoch ||
            (old.revoked && grant.authorization_epoch <= old.grant.authorization_epoch) {
            return Err(ClientError::Denied);
        }
    } else if state.devices.len() >= MAX_DEVICES { return Err(ClientError::Denied); }
    if state.devices.iter().any(|(device, row)| device != &grant.device_id && row.grant.endpoint_id == grant.endpoint_id) {
        return Err(ClientError::Denied);
    }
    let generation = old.map_or(0, |row| row.generation);
    let core_device=old.and_then(|row|row.core_device.clone());
    state.devices.insert(grant.device_id.clone(), Row { grant, generation, revoked: false, core_denied:false,core_device });
    encode(&state)
}

pub fn load(bytes: &[u8], endpoint: &str, now_ms: u64) -> Result<StoredPeer, ClientError> {
    let state = decode(bytes)?;
    let row = state.devices.values().find(|row| row.grant.endpoint_id == endpoint).ok_or(ClientError::Denied)?;
    if row.revoked || row.core_denied { return Err(ClientError::Denied); }
    row.grant.validate(now_ms)?;
    Ok(StoredPeer { grant: row.grant.clone(), generation_floor: row.generation,core_device:row.core_device.clone() })
}

pub fn admit(bytes: &[u8], proposed: &ClientGrant, generation: u64, now_ms: u64) -> Result<Vec<u8>, ClientError> {
    proposed.validate(now_ms)?;
    let mut state = decode(bytes)?;
    let row = state.devices.get_mut(&proposed.device_id).ok_or(ClientError::Denied)?;
    if row.revoked || row.core_denied || !same_association(proposed, &row.grant) || generation <= row.generation ||
        proposed.authorization_epoch != row.grant.authorization_epoch || proposed.expires_at_ms > row.grant.expires_at_ms ||
        proposed.scopes.iter().any(|scope| !row.grant.scopes.contains(scope)) {
        return Err(ClientError::Denied);
    }
    row.generation = generation;
    encode(&state)
}

pub fn revoke(bytes: &[u8], device: &str, authorization_epoch: u64) -> Result<Vec<u8>, ClientError> {
    let mut state = decode(bytes)?;
    let row = state.devices.get_mut(device).ok_or(ClientError::Denied)?;
    mark_revoked(row, authorization_epoch)?;
    encode(&state)
}

pub fn deny_core_endpoint(bytes:&[u8],endpoint:&str)->Result<Vec<u8>,ClientError> {
    crate::host::endpoint_bytes(endpoint).map_err(|_| ClientError::Invalid)?;
    let mut state=decode(bytes)?;
    // Core can fence cloud use; only fresh host-verified pairing clears this local fence.
    if let Some(row)=state.devices.values_mut().find(|row|row.grant.endpoint_id==endpoint) { row.core_denied=true; }
    encode(&state)
}
fn core_identifier(device:&str)->bool {
    device.len()==15 && device.bytes().all(|value|value.is_ascii_digit() || value.is_ascii_lowercase())
}
pub fn associate_core_endpoint(bytes:&[u8],endpoint:&str,device:Option<&str>)->Result<Vec<u8>,ClientError> {
    if device.is_some_and(|value|!core_identifier(value)) { return Err(ClientError::Invalid); }
    let mut state=decode(bytes)?;
    let row=state.devices.values_mut().find(|row|row.grant.endpoint_id==endpoint).ok_or(ClientError::Denied)?;
    if row.revoked || row.core_denied || device.is_some_and(|device|row.core_device.as_ref().is_some_and(|old|old!=device)) { return Err(ClientError::Denied); }
    row.core_device=device.map(str::to_owned);encode(&state)
}

pub fn revoke_endpoint(bytes: &[u8], endpoint: &str, authorization_epoch: u64) -> Result<Vec<u8>, ClientError> {
    let mut state = decode(bytes)?;
    let row = state.devices.values_mut().find(|row| row.grant.endpoint_id == endpoint).ok_or(ClientError::Denied)?;
    mark_revoked(row, authorization_epoch)?;
    encode(&state)
}

fn mark_revoked(row: &mut Row, authorization_epoch: u64) -> Result<(), ClientError> {
    if authorization_epoch <= row.grant.authorization_epoch { return Err(ClientError::Denied); }
    row.grant.authorization_epoch = authorization_epoch;
    row.revoked = true;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn grant() -> ClientGrant {
        ClientGrant { endpoint_id: iroh::SecretKey::from_bytes(&[41;32]).public().to_string(),
            device_id: "synthetic-client".into(), owner_key: "synthetic-owner".into(),
            canonical_user_id: "synthetic-user".into(), conversation_key: "synthetic-conversation".into(),
            origin_transport: "local".into(), origin_sender_id: "synthetic-origin".into(), pairing_mode: "normal".into(),
            device_ownership: "own".into(), authorization_epoch: 3, expires_at_ms: 100_000,
            scopes: vec!["chat".into(), "http".into()] }
    }

    #[test]
    fn core_denial_survives_reopen_without_inventing_a_host_authorization_epoch() {
        let g=grant();assert_eq!(deny_core_endpoint(&empty(),&g.endpoint_id).unwrap(),empty());
        assert!(deny_core_endpoint(&empty(),"invalid").is_err());
        let saved=register(&empty(),g.clone(),10).unwrap();
        let saved=admit(&saved,&g,7,20).unwrap();
        let saved=associate_core_endpoint(&saved,&g.endpoint_id,Some("client000000001")).unwrap();
        assert!(associate_core_endpoint(&saved,&g.endpoint_id,Some("client000000002")).is_err());
        let denied=deny_core_endpoint(&saved,&g.endpoint_id).unwrap();
        assert!(load(&denied,&g.endpoint_id,30).is_err());
        assert!(admit(&denied,&g,8,30).is_err());
        assert_eq!(next_authorization_epoch(&denied,&g.device_id).unwrap(),g.authorization_epoch+1);
        let fresh=register(&denied,g.clone(),30).unwrap();
        let peer=load(&fresh,&g.endpoint_id,30).unwrap();
        assert_eq!(peer.generation_floor,7);assert_eq!(peer.grant,g);
        assert_eq!(peer.core_device.as_deref(),Some("client000000001"));
        assert!(associate_core_endpoint(&denied,&g.endpoint_id,None).is_err());
        let manual=associate_core_endpoint(&fresh,&g.endpoint_id,None).unwrap();
        assert!(load(&manual,&g.endpoint_id,30).unwrap().core_device.is_none());
        let revoked=revoke(&denied,&g.device_id,4).unwrap();assert!(register(&revoked,g,30).is_err());
    }
    #[test]
    fn persisted_generations_scope_and_association_never_rewind() {
        let g = grant(); let state = register(&empty(), g.clone(), 10).unwrap();
        let mut admitted = g.clone(); admitted.scopes = vec!["chat".into()]; admitted.expires_at_ms = 90_000;
        let saved = admit(&state, &admitted, 7, 20).unwrap();
        assert_eq!(load(&saved, &g.endpoint_id, 30).unwrap().generation_floor, 7);
        assert_eq!(admit(&saved, &admitted, 7, 30).err(), Some(ClientError::Denied));
        admitted.scopes.push("admin".into());
        assert_eq!(admit(&saved, &admitted, 8, 30).err(), Some(ClientError::Denied));
        admitted = g.clone(); admitted.owner_key = "other-synthetic-owner".into();
        assert_eq!(register(&saved, admitted, 30).err(), Some(ClientError::Denied));
        let renewed = register(&saved, g.clone(), 30).unwrap();
        assert_eq!(load(&renewed, &g.endpoint_id, 30).unwrap().generation_floor, 7);
    }

    #[test]
    fn revocation_requires_fresh_pairing_and_expired_records_remain_readable() {
        let mut g = grant(); let saved = register(&empty(), g.clone(), 10).unwrap();
        let revoked = revoke(&saved, &g.device_id, 4).unwrap();
        assert!(load(&revoked, &g.endpoint_id, 10).is_err());
        g.authorization_epoch = 4; assert!(register(&revoked, g.clone(), 10).is_err());
        g.authorization_epoch = 5;
        let repaired = register(&revoked, g.clone(), 10).unwrap();
        assert!(load(&repaired, &g.endpoint_id, 100_000).is_err());
        let expired_revoked = revoke_endpoint(&repaired, &g.endpoint_id, 6).unwrap();
        assert_eq!(next_authorization_epoch(&expired_revoked, &g.device_id).unwrap(), 7);
        assert!(revoke_endpoint(&expired_revoked, &g.endpoint_id, 6).is_err());
        g.expires_at_ms = 200_000;
        g.authorization_epoch = 7;
        assert!(register(&expired_revoked, g.clone(), 100_000).is_ok());
        g.authorization_epoch = u64::MAX;
        let exhausted = register(&expired_revoked, g.clone(), 100_000).unwrap();
        assert_eq!(next_authorization_epoch(&exhausted, &g.device_id).err(), Some(ClientError::Denied));
    }

    #[test]
    fn corrupt_protected_state_never_becomes_an_empty_database() {
        let g = grant();
        for bytes in [b"{}".as_slice(), br#"{"schema":2,"devices":{}}"#, br#"{"schema":1,"devices":{},"role":"admin"}"#] {
            assert_eq!(register(bytes, g.clone(), 10).err(), Some(ClientError::Invalid));
        }
        let mut value: serde_json::Value = serde_json::from_slice(&register(&empty(), g.clone(), 10).unwrap()).unwrap();
        let row = value["devices"][&g.device_id].clone();
        value["devices"]["another-synthetic-device"] = row;
        assert!(load(&serde_json::to_vec(&value).unwrap(), &g.endpoint_id, 10).is_err());
    }
}

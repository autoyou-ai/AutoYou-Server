// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Server grant policy. Protected state is decoded once and borrowed throughout
//! each lookup or mutation; verified pairing remains the source of authority.

use crate::client::ClientGrant;
use serde::{Deserialize, Serialize, de::{self, MapAccess, Visitor}};
use serde_json::value::RawValue;
use std::{collections::{BTreeMap, BTreeSet}, fmt};

const MAX_STATE: usize = 16 * 1024 * 1024;
const MAX_DEVICES: usize = 4096;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum GrantError {
    #[error("stored endpoint grants are invalid")] InvalidState,
    #[error("{0}")] Denied(&'static str),
}

type Result<T> = std::result::Result<T, GrantError>;

pub fn core_identifier(value: &str) -> bool {
    value.len() == 15 && value.bytes().all(|v| v.is_ascii_digit() || v.is_ascii_lowercase())
}
pub fn validate_grant(grant: &ClientGrant) -> Result<()> {
    if [&grant.endpoint_id, &grant.device_id, &grant.owner_key, &grant.canonical_user_id,
        &grant.conversation_key, &grant.origin_transport, &grant.origin_sender_id, &grant.pairing_mode]
        .iter().any(|value| value.is_empty() || value.len() > 512) ||
        !matches!(grant.device_ownership.as_str(), "own" | "shared") || grant.expires_at_ms == 0 {
        return Err(GrantError::Denied("invalid verified pairing identity"));
    }
    let scopes: BTreeSet<_> = grant.scopes.iter().collect();
    if scopes.len() > 32 || scopes.iter().any(|value| value.is_empty() || value.len() > 64) {
        return Err(GrantError::Denied("invalid verified session scope"));
    }
    Ok(())
}
fn transport_id(value: &str) -> Result<()> {
    if value.is_empty() || value.len() > 512 { return Err(GrantError::Denied("invalid verified session identity")); }
    Ok(())
}

#[derive(Serialize)]
struct Row {
    grant: ClientGrant,
    generation: u64,
    revoked: bool,
    #[serde(skip_serializing_if="Option::is_none")] core_device_id: Option<String>,
    #[serde(skip_serializing_if="Option::is_none")] core_device: Option<String>,
    #[serde(skip_serializing_if="std::ops::Not::not")] core_denied: bool,
    #[serde(flatten)] extra: BTreeMap<String, Box<RawValue>>,
}
impl<'de> Deserialize<'de> for Row {
    fn deserialize<D: de::Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        struct RowVisitor;
        impl<'de> Visitor<'de> for RowVisitor {
            type Value = Row;
            fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result { formatter.write_str("a stored grant row") }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> std::result::Result<Row, M::Error> {
                let (mut grant, mut generation, mut revoked) = (None, None, None);
                let (mut core_device_id, mut core_device, mut core_denied) = (None, None, false);
                let mut extra = BTreeMap::new();
                while let Some(key) = map.next_key::<String>()? {
                    match key.as_str() {
                        "grant" => grant = Some(map.next_value()?),
                        "generation" => generation = Some(map.next_value()?),
                        "revoked" => revoked = Some(map.next_value()?),
                        "core_device_id" => core_device_id = map.next_value()?,
                        "core_device" => core_device = map.next_value()?,
                        "core_denied" => core_denied = map.next_value()?,
                        // Preserve opaque legacy metadata, including large numbers and literal object keys.
                        _ => { extra.insert(key, map.next_value()?); }
                    }
                }
                Ok(Row { grant: grant.ok_or_else(|| de::Error::missing_field("grant"))?,
                    generation: generation.ok_or_else(|| de::Error::missing_field("generation"))?,
                    revoked: revoked.ok_or_else(|| de::Error::missing_field("revoked"))?, core_device_id, core_device, core_denied, extra })
            }
        }
        deserializer.deserialize_map(RowVisitor)
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GrantStore { schema: u8, devices: BTreeMap<String, Row> }

#[derive(Clone)]
pub struct GrantPeer { pub grant: ClientGrant, pub generation: u64, pub core_device_id: Option<String> }

impl Default for GrantStore {
    fn default() -> Self { Self { schema: 1, devices: BTreeMap::new() } }
}
impl GrantStore {
    pub fn decode(bytes: &[u8]) -> Result<Self> {
        if bytes.len() > MAX_STATE { return Err(GrantError::InvalidState); }
        let state: Self = serde_json::from_slice(bytes).map_err(|_| GrantError::InvalidState)?;
        if state.schema != 1 || state.devices.len() > MAX_DEVICES { return Err(GrantError::InvalidState); }
        let mut endpoints = BTreeSet::new();
        for (device, row) in &state.devices {
            if validate_grant(&row.grant).is_err() || device != &row.grant.device_id || !endpoints.insert(&row.grant.endpoint_id) ||
                [&row.core_device_id, &row.core_device].iter().any(|id| id.as_ref().is_some_and(|id| !core_identifier(id))) {
                return Err(GrantError::InvalidState);
            }
        }
        Ok(state)
    }
    pub fn encode(&self) -> Result<Vec<u8>> {
        let bytes = serde_json::to_vec(self).map_err(|_| GrantError::InvalidState)?;
        if bytes.len() > MAX_STATE { return Err(GrantError::InvalidState); }
        Ok(bytes)
    }
    fn row(&self, endpoint: &str) -> Option<&Row> { self.devices.values().find(|row| row.grant.endpoint_id == endpoint) }
    fn row_mut(&mut self, endpoint: &str) -> Option<&mut Row> { self.devices.values_mut().find(|row| row.grant.endpoint_id == endpoint) }
    fn active(&self, endpoint: &str, now_ms: u64) -> Result<&Row> {
        let row = self.row(endpoint).ok_or(GrantError::Denied("endpoint has no active pairing grant"))?;
        Self::check_active(row, now_ms)?;
        Ok(row)
    }
    fn check_active(row: &Row, now_ms: u64) -> Result<()> {
        if row.revoked || row.core_denied || row.grant.expires_at_ms <= now_ms {
            return Err(GrantError::Denied("endpoint has no active pairing grant"));
        }
        Ok(())
    }
    pub fn peer(&self, endpoint: &str, now_ms: u64) -> Result<GrantPeer> {
        let row = self.active(endpoint, now_ms)?;
        Ok(Self::peer_row(row))
    }
    fn peer_row(row: &Row) -> GrantPeer {
        GrantPeer { grant: row.grant.clone(), generation: row.generation,
            core_device_id: row.core_device.clone().or_else(|| row.core_device_id.clone()) }
    }
    pub fn pairing_identity(&self, endpoint: &str) -> Option<ClientGrant> { self.row(endpoint).map(|row| row.grant.clone()) }
    pub fn authorization_epoch(&self, device: &str) -> u64 { self.devices.get(device).map_or(0, |row| row.grant.authorization_epoch) }
    pub fn authority_device(&self, endpoint: &str) -> Option<String> {
        self.row(endpoint).filter(|row| !row.revoked).and_then(|row| row.core_device.clone().or_else(|| row.core_device_id.clone()))
    }
    pub fn authority_devices(&self, devices: Option<&BTreeSet<String>>) -> Vec<(String, String)> {
        self.devices.iter().filter_map(|(device, row)| {
            if row.revoked || row.core_denied || devices.is_some_and(|devices| !devices.contains(device)) { return None; }
            row.core_device.as_ref().or_else(|| row.core_device_id.as_ref().filter(|_| row.grant.origin_transport == "cloud"))
                .map(|id| (id.clone(), row.grant.endpoint_id.clone()))
        }).collect()
    }
    pub fn register(&mut self, mut grant: ClientGrant, core_device_id: Option<String>, now_ms: u64) -> Result<()> {
        validate_grant(&grant)?;
        if core_device_id.as_ref().is_some_and(|id| grant.origin_transport != "cloud" || !core_identifier(id)) {
            return Err(GrantError::Denied("invalid verified Core grant device"));
        }
        if grant.expires_at_ms <= now_ms { return Err(GrantError::Denied("pairing grant has expired")); }
        let existing = self.devices.get(&grant.device_id);
        if let Some(row) = existing {
            let old = &row.grant;
            if grant.endpoint_id != old.endpoint_id || grant.owner_key != old.owner_key ||
                grant.canonical_user_id != old.canonical_user_id || grant.conversation_key != old.conversation_key {
                return Err(GrantError::Denied("endpoint association requires authorized recovery"));
            }
            if grant.authorization_epoch < old.authorization_epoch || (row.revoked && grant.authorization_epoch <= old.authorization_epoch) {
                return Err(GrantError::Denied("stale authorization epoch"));
            }
        } else if self.devices.len() >= MAX_DEVICES { return Err(GrantError::Denied("device grant capacity has been reached")); }
        if existing.is_none() && self.devices.values().any(|row| row.grant.endpoint_id == grant.endpoint_id) {
            return Err(GrantError::Denied("endpoint already belongs to another device"));
        }
        let associated = core_device_id.or_else(|| existing.and_then(|row| row.core_device_id.clone()));
        if existing.is_some_and(|row| row.core_device_id.as_ref().is_some_and(|id| Some(id) != associated.as_ref())) {
            return Err(GrantError::Denied("Core grant device requires authorized recovery"));
        }
        let generation = existing.map_or(0, |row| row.generation);
        grant.scopes.sort(); grant.scopes.dedup();
        self.devices.insert(grant.device_id.clone(), Row { grant, generation, revoked: false, core_device_id: associated,
            core_device: None, core_denied: false, extra: BTreeMap::new() });
        Ok(())
    }
    pub fn binding(&mut self, endpoint: &str, transport: &str, now_ms: u64) -> Result<GrantPeer> {
        transport_id(transport)?;
        let row = self.row_mut(endpoint).ok_or(GrantError::Denied("endpoint has no active pairing grant"))?;
        Self::check_active(row, now_ms)?;
        row.generation = row.generation.checked_add(1).ok_or(GrantError::Denied("endpoint pairing grant requires recovery"))?;
        Ok(Self::peer_row(row))
    }
    pub fn remote_binding(&mut self, endpoint: &str, transport: &str, generation: u64, epoch: u64,
        expires_at_ms: u64, scopes: Vec<String>, now_ms: u64) -> Result<GrantPeer> {
        transport_id(transport)?;
        let row = self.row_mut(endpoint).ok_or(GrantError::Denied("endpoint has no active pairing grant"))?;
        Self::check_active(row, now_ms)?;
        if generation <= row.generation || epoch != row.grant.authorization_epoch ||
            !(now_ms < expires_at_ms && expires_at_ms <= row.grant.expires_at_ms) ||
            scopes.iter().any(|scope| !row.grant.scopes.contains(scope)) {
            return Err(GrantError::Denied("remote session is stale or outside its paired scope"));
        }
        let mut grant = row.grant.clone(); grant.expires_at_ms = expires_at_ms; grant.scopes = scopes;
        validate_grant(&grant)?;
        row.generation = generation;
        Ok(GrantPeer { grant, generation, core_device_id: None })
    }
    pub fn revoke(&mut self, device: &str, epoch: u64) -> Result<bool> {
        let Some(row) = self.devices.get_mut(device) else { return Ok(false); };
        if epoch <= row.grant.authorization_epoch { return Err(GrantError::Denied("revocation epoch must advance")); }
        row.grant.authorization_epoch = epoch; row.revoked = true; Ok(true)
    }
    pub fn revoke_origin(&mut self, origin: &str, core_device_id: Option<&str>) -> Result<Vec<(String, u64)>> {
        let mut revoked = Vec::new();
        for (device, row) in &mut self.devices {
            if row.revoked || row.grant.origin_transport != origin ||
                core_device_id.is_some_and(|id| row.core_device_id.as_deref() != Some(id)) { continue; }
            let epoch = row.grant.authorization_epoch.checked_add(1).ok_or(GrantError::Denied("authorization epoch exhausted"))?;
            row.grant.authorization_epoch = epoch; row.revoked = true; revoked.push((device.clone(), epoch));
        }
        Ok(revoked)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn grant() -> ClientGrant {
        ClientGrant { endpoint_id:"synthetic-endpoint".into(), device_id:"synthetic-device".into(),
            owner_key:"synthetic-owner".into(), canonical_user_id:"synthetic-user".into(),
            conversation_key:"synthetic-conversation".into(), origin_transport:"cloud".into(),
            origin_sender_id:"synthetic-origin".into(), pairing_mode:"pair".into(), device_ownership:"own".into(),
            authorization_epoch:2, expires_at_ms:10_000, scopes:vec!["chat".into(), "browser".into()] }
    }
    #[test]
    fn recovery_retains_association_generation_and_revocation_authority() {
        let g=grant(); let mut state=GrantStore::default();
        state.register(g.clone(),Some("client000000001".into()),1000).unwrap();
        assert_eq!(state.binding(&g.endpoint_id,"synthetic-connection",1000).unwrap().generation,1);
        let mut forged=g.clone(); forged.owner_key="synthetic-other-owner".into();
        assert!(state.register(forged,None,1000).is_err());
        assert!(state.register(g.clone(),Some("client000000002".into()),1000).is_err());
        assert!(state.remote_binding(&g.endpoint_id,"synthetic-connection",2,2,9000,vec!["admin".into()],1000).is_err());
        assert_eq!(state.peer(&g.endpoint_id,1000).unwrap().generation,1);
        state.revoke(&g.device_id,3).unwrap();
        assert!(state.peer(&g.endpoint_id,1000).is_err());
        let mut g=g; g.authorization_epoch=3;
        assert!(state.register(g.clone(),None,1000).is_err());
        g.authorization_epoch=4; state.register(g.clone(),None,1000).unwrap();
        let mut reopened=GrantStore::decode(&state.encode().unwrap()).unwrap();
        assert_eq!(reopened.binding(&g.endpoint_id,"synthetic-recovered",1000).unwrap().generation,2);
        assert_eq!(reopened.authority_device(&g.endpoint_id).as_deref(),Some("client000000001"));
    }
    #[test]
    fn legacy_metadata_is_preserved_without_numeric_or_reserved_key_conversion() {
        let g=grant(); let mut state=GrantStore::default(); state.register(g.clone(),None,1000).unwrap();
        let row=state.devices.get_mut(&g.device_id).unwrap();
        row.extra.insert("synthetic_metadata".into(),RawValue::from_string(
            r#"{"integer":1267650600228229401496703205376,"$serde_json::private::Number":"literal"}"#.into()).unwrap());
        let mut reopened=GrantStore::decode(&state.encode().unwrap()).unwrap();
        reopened.binding(&g.endpoint_id,"synthetic-connection",1000).unwrap();
        let saved=String::from_utf8(reopened.encode().unwrap()).unwrap();
        assert!(saved.contains("1267650600228229401496703205376"));
        assert!(saved.contains(r#""$serde_json::private::Number":"literal""#));
    }
    #[test]
    fn invalid_stored_authority_and_counter_exhaustion_are_never_reset() {
        let g=grant(); let mut state=GrantStore::default(); state.register(g.clone(),None,1000).unwrap();
        let row=state.devices.get_mut(&g.device_id).unwrap(); row.generation=u64::MAX;
        assert!(state.binding(&g.endpoint_id,"synthetic-overflow",1000).is_err());
        let saved=state.encode().unwrap();
        assert_eq!(GrantStore::decode(&saved).unwrap().devices[&g.device_id].generation,u64::MAX);
        for (field,value) in [("schema",serde_json::json!(true)),("generation",serde_json::json!(false)),("revoked",serde_json::json!(1))] {
            let mut bad:serde_json::Value=serde_json::from_slice(&saved).unwrap();
            if field=="schema" { bad[field]=value; } else { bad["devices"][&g.device_id][field]=value; }
            assert!(matches!(GrantStore::decode(&serde_json::to_vec(&bad).unwrap()),Err(GrantError::InvalidState)));
        }
        state.devices.get_mut(&g.device_id).unwrap().grant.authorization_epoch=u64::MAX;
        assert!(state.revoke_origin("cloud",None).is_err());
    }
}

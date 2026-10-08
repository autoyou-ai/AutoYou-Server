// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Peer/room descriptors are routing hints. Existing proof and local approval
//! establish grants; endpoint admission still verifies the connection exporter.

use crate::host::{endpoint_bytes, HostError};
use iroh::TransportAddr;
use iroh_tickets::endpoint::EndpointTicket;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use crate::client::{ClientError, ClientGrant};

/// Same ten-minute manual invitation lifetime as AutoYou's Peer Link protocol.
pub const INVITATION_LIFETIME_SECONDS: u64 = 600;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Descriptor {
    transport: String,
    #[serde(rename = "type")] kind: String,
    version: u32,
    endpoint_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")] ticket: Option<String>,
}

pub fn descriptor(json: &str, kind: &str) -> Result<String, HostError> {
    if json.len() > 16 * 1024 || !matches!(kind, "offer" | "answer") {
        return Err(HostError::InvalidConfig);
    }
    let value: Descriptor = serde_json::from_str(json).map_err(|_| HostError::InvalidConfig)?;
    if value.transport != "iroh" || value.version != 1 || value.kind != kind {
        return Err(HostError::InvalidConfig);
    }
    let endpoint = endpoint_bytes(&value.endpoint_id)?;
    match (kind, &value.ticket) {
        ("offer", None) => {},
        ("answer", Some(ticket)) if ticket.len() <= 8192 => {
            let ticket: EndpointTicket = ticket.parse().map_err(|_| HostError::InvalidTicket)?;
            let address = ticket.endpoint_addr();
            if address.id.as_bytes() != &endpoint || address.is_empty() || address.addrs.len() > 16 ||
                address.addrs.iter().any(|hint| matches!(hint, TransportAddr::Ip(socket) if socket.port() == 0)) {
                return Err(HostError::InvalidTicket);
            }
        },
        _ => return Err(HostError::InvalidTicket),
    }
    serde_json::to_string(&value).map_err(|_| HostError::InvalidConfig)
}

pub fn fingerprint(endpoint: &str) -> Result<String, HostError> {
    let bytes = endpoint_bytes(endpoint)?;
    Ok(format!("iroh-ed25519 {}", bytes.iter().map(|byte| format!("{byte:02x}")).collect::<String>()))
}

/// Identity and epoch construction for a peer already approved by its local
/// application owner. This neither issues a proof nor mutates protected state.
pub fn approval_grant(store: &[u8], remote_endpoint: &str, origin_sender: String,
                      mut scopes: Vec<String>, expires_at_ms: u64, now_ms: u64) -> Result<ClientGrant, ClientError> {
    let endpoint = endpoint_bytes(remote_endpoint).map_err(|_| ClientError::Invalid)?;
    let canonical = endpoint.iter().map(|byte| format!("{byte:02x}")).collect::<String>();
    let digest = Sha256::digest(canonical.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect::<String>();
    let device = format!("peer-device-{digest}");
    let owner = format!("peer:{digest}");
    if scopes.len() > 5 { return Err(ClientError::Invalid); }
    scopes.sort(); scopes.dedup();
    let grant = ClientGrant {
        endpoint_id: canonical, authorization_epoch: crate::client_store::next_authorization_epoch(store, &device)?,
        device_id: device, owner_key: owner.clone(), canonical_user_id: format!("user::{owner}"),
        conversation_key: format!("session::{owner}"), origin_transport: "peer".into(), origin_sender_id: origin_sender,
        pairing_mode: "manual-peer".into(), device_ownership: "shared".into(), expires_at_ms, scopes,
    };
    crate::acceptor::AcceptorSession::peer_grant(&grant, now_ms)?;
    Ok(grant)
}

fn room_realm(room_id: &str, room_epoch: &str) -> Result<String, ClientError> {
    if room_id.len() != 12 || !room_id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()) ||
        room_epoch.len() != 22 || !room_epoch.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') {
        return Err(ClientError::Invalid);
    }
    Ok(format!("room::{room_id}:{room_epoch}"))
}

/// The room-v4 proof and host membership owner approve the participant first.
/// This only constructs the same scoped identity/epoch used by desktop Lobbies.
pub fn room_approval_grant(store: &[u8], remote_endpoint: &str, origin_sender: String,
    room_id: &str, room_epoch: &str, mut scopes: Vec<String>, expires_at_ms: u64, now_ms: u64)
    -> Result<ClientGrant, ClientError> {
    let endpoint = endpoint_bytes(remote_endpoint).map_err(|_| ClientError::Invalid)?;
    let canonical = endpoint.iter().map(|b| format!("{b:02x}")).collect::<String>();
    let realm = room_realm(room_id, room_epoch)?;
    let digest = Sha256::digest(format!("{realm}:{canonical}").as_bytes()).iter()
        .map(|b| format!("{b:02x}")).collect::<String>();
    let device = format!("room-device-{digest}");
    if scopes.len() > 5 { return Err(ClientError::Invalid); }
    scopes.sort(); scopes.dedup();
    let grant = ClientGrant {
        endpoint_id: canonical, authorization_epoch: crate::client_store::next_authorization_epoch(store, &device)?,
        device_id: device, owner_key: realm.clone(), canonical_user_id: format!("user::{realm}"),
        conversation_key: realm, origin_transport: "room".into(), origin_sender_id: origin_sender,
        pairing_mode: "room-v4".into(), device_ownership: "shared".into(), expires_at_ms, scopes,
    };
    crate::acceptor::AcceptorSession::room_grant(&grant, now_ms)?;
    Ok(grant)
}

/// Validate the opaque bootstrap before a platform serializes it into an
/// encrypted Peer Link reply. The client's actor still proves and admits it.
pub fn pairing(descriptor_json: &str, proof_json: &str, now_ms: u64) -> Result<String, HostError> {
    let canonical = descriptor(descriptor_json, "answer")?;
    if proof_json.len() > 32*1024 { return Err(HostError::InvalidConfig); }
    let description: Descriptor = serde_json::from_str(&canonical).map_err(|_| HostError::InvalidConfig)?;
    let proof: serde_json::Value = serde_json::from_str(proof_json).map_err(|_| HostError::InvalidConfig)?;
    if proof["endpoint_id"].as_str() != Some(description.endpoint_id.as_str()) ||
        proof["ticket"].as_str() != description.ticket.as_deref() ||
        proof["capabilities"]["peer"].as_bool() != Some(true) ||
        proof["capabilities"]["transport"].as_str() != Some("iroh") ||
        proof["capabilities"].get("application").is_some_and(|value| value.as_str() != Some("peer")) ||
        proof["grant"]["origin_transport"].as_str() != Some("peer") ||
        proof["grant"]["device_ownership"].as_str() != Some("shared") {
        return Err(HostError::InvalidConfig);
    }
    let scopes = proof["grant"]["scopes"].as_array().ok_or(HostError::InvalidConfig)?;
    if !scopes.iter().any(|scope| scope.as_str() == Some("peer")) ||
        scopes.iter().any(|scope| !matches!(scope.as_str(),Some("peer"|"chat"|"browser"|"files"|"media"))) {
        return Err(HostError::InvalidConfig);
    }
    let local = proof["grant"]["endpoint_id"].as_str().ok_or(HostError::InvalidConfig)?;
    if local == description.endpoint_id { return Err(HostError::InvalidConfig); }
    let mut validation = crate::client::ClientSession::new(local.to_owned()).map_err(|_| HostError::InvalidConfig)?;
    validation.begin_verified_pairing(proof_json.as_bytes(),now_ms).map_err(|_| HostError::InvalidConfig)?;
    serde_json::to_string(&proof).map_err(|_| HostError::InvalidConfig)
}

/// Bind the opaque enrollment to the exact opening authenticated by room-v4.
/// The client actor subsequently verifies redemption and the TLS transcript.
pub fn room_pairing(descriptor_json: &str, proof_json: &str, room_id: &str, room_epoch: &str,
    now_ms: u64) -> Result<String, HostError> {
    let realm = room_realm(room_id, room_epoch).map_err(|_| HostError::InvalidConfig)?;
    let canonical = descriptor(descriptor_json, "answer")?;
    if proof_json.len() > 32 * 1024 { return Err(HostError::InvalidConfig); }
    let description: Descriptor = serde_json::from_str(&canonical).map_err(|_| HostError::InvalidConfig)?;
    let proof: serde_json::Value = serde_json::from_str(proof_json).map_err(|_| HostError::InvalidConfig)?;
    if proof["endpoint_id"].as_str() != Some(description.endpoint_id.as_str()) ||
        proof["ticket"].as_str() != description.ticket.as_deref() ||
        proof["capabilities"]["transport"].as_str() != Some("iroh") ||
        proof["capabilities"]["application"].as_str() != Some("room") ||
        proof["capabilities"]["wire_version"].as_u64() != Some(1) ||
        proof["capabilities"]["room_id"].as_str() != Some(room_id) ||
        proof["capabilities"]["room_epoch"].as_str() != Some(room_epoch) ||
        proof["grant"]["origin_transport"].as_str() != Some("room") ||
        proof["grant"]["owner_key"].as_str() != Some(realm.as_str()) ||
        proof["grant"]["conversation_key"].as_str() != Some(realm.as_str()) ||
        proof["grant"]["canonical_user_id"].as_str() != Some(format!("user::{realm}").as_str()) {
        return Err(HostError::InvalidConfig);
    }
    let local = proof["grant"]["endpoint_id"].as_str().ok_or(HostError::InvalidConfig)?;
    if local == description.endpoint_id { return Err(HostError::InvalidConfig); }
    let mut validation = crate::client::ClientSession::new(local.to_owned()).map_err(|_| HostError::InvalidConfig)?;
    validation.begin_verified_pairing(proof_json.as_bytes(), now_ms).map_err(|_| HostError::InvalidConfig)?;
    serde_json::to_string(&proof).map_err(|_| HostError::InvalidConfig)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn room_bootstrap_preserves_opening_identity_epoch_and_exact_role() {
        use base64::Engine;
        let host = SecretKey::from_bytes(&[71;32]).public();
        let guest = SecretKey::from_bytes(&[73;32]).public().to_string();
        let room = "synthetic001"; let epoch = "abcdefghijklmnopqrstuv";
        let scopes = vec!["room".into(), "chat".into(), "media".into()];
        let grant = room_approval_grant(&crate::client_store::empty(), &guest, "synthetic-install".into(), room, epoch, scopes.clone(), 100_000, 1000).unwrap();
        assert_eq!(grant.owner_key, format!("room::{room}:{epoch}"));
        let saved = crate::client_store::register(&crate::client_store::empty(), grant.clone(), 1000).unwrap();
        let saved = crate::client_store::admit(&saved, &grant, 9, 1000).unwrap();
        let revoked = crate::client_store::revoke(&saved, &grant.device_id, 2).unwrap();
        let renewed = room_approval_grant(&revoked, &guest, "synthetic-install".into(), room, epoch, scopes.clone(), 110_000, 1001).unwrap();
        assert_eq!(renewed.authorization_epoch, 3); assert_eq!(renewed.device_id, grant.device_id);
        let updated = crate::client_store::register(&revoked, renewed, 1001).unwrap();
        assert_eq!(crate::client_store::load(&updated, &guest, 1001).unwrap().generation_floor, 9);
        let next_opening = room_approval_grant(&updated, &guest, "synthetic-install".into(), room, "bcdefghijklmnopqrstuvw", scopes.clone(), 110_000, 1001).unwrap();
        assert_ne!(next_opening.device_id, grant.device_id);
        for (id, opening, allowed) in [("../synthetic", epoch, scopes.clone()), (room, "synthetic-invalid-epoch", scopes.clone()), (room, epoch, vec!["room".into(), "chat".into(), "control".into()]), (room, epoch, vec!["room".into()])] {
            assert!(room_approval_grant(&saved, &guest, "synthetic-install".into(), id, opening, allowed, 100_000, 1000).is_err());
        }
        let ticket = EndpointTicket::new(EndpointAddr::new(host).with_ip_addr("127.0.0.1:31415".parse().unwrap())).to_string();
        let descriptor = serde_json::json!({"transport":"iroh","type":"answer","version":1,"endpoint_id":host.to_string(),"ticket":ticket});
        let proof = serde_json::json!({"version":1,"endpoint_id":host.to_string(),"ticket":ticket,
            "redemption":base64::engine::general_purpose::URL_SAFE.encode([7;32]),"redemption_expires_in_seconds":30,
            "capabilities":{"transport":"iroh","application":"room","wire_version":1,"room_id":room,"room_epoch":epoch},
            "grant":grant});
        assert_eq!(serde_json::from_str::<serde_json::Value>(&room_pairing(&descriptor.to_string(), &proof.to_string(), room, epoch, 1000).unwrap()).unwrap(), proof);
        assert!(pairing(&descriptor.to_string(), &proof.to_string(), 1000).is_err());
        assert!(room_pairing(&descriptor.to_string(), &proof.to_string(), room, "bcdefghijklmnopqrstuvw", 1000).is_err());
        for (section, key, value) in [
            ("capabilities", "application", serde_json::json!("peer")),
            ("capabilities", "wire_version", serde_json::json!(true)),
            ("capabilities", "room_id", serde_json::json!("synthetic002")),
            ("grant", "conversation_key", serde_json::json!("room::synthetic-forged")),
            ("grant", "owner_key", serde_json::json!("synthetic-forged")),
            ("grant", "canonical_user_id", serde_json::json!("synthetic-forged")),
            ("grant", "scopes", serde_json::json!(["room","chat","browser"])),
            ("grant", "device_ownership", serde_json::json!("own")),
            ("grant", "endpoint_id", serde_json::json!(host.to_string())),
        ] {
            let mut changed = proof.clone(); changed[section][key] = value;
            assert!(room_pairing(&descriptor.to_string(), &changed.to_string(), room, epoch, 1000).is_err());
        }
        let mut changed = proof.clone(); changed["authority"] = serde_json::json!("synthetic-forged");
        assert!(room_pairing(&descriptor.to_string(), &changed.to_string(), room, epoch, 1000).is_err());
        changed = proof.clone(); changed["ticket"] = serde_json::json!("synthetic-unbound-ticket");
        assert!(room_pairing(&descriptor.to_string(), &changed.to_string(), room, epoch, 1000).is_err());
        assert!(room_pairing(&descriptor.to_string(), &proof.to_string(), room, epoch, 100_001).is_err());
    }
    #[test]
    fn approved_peer_grants_share_identity_and_preserve_revoked_epoch_and_generation() {
        let endpoint = SecretKey::from_bytes(&[63;32]).public().to_string();
        let empty = crate::client_store::empty();
        let grant = approval_grant(&empty, &endpoint, "synthetic-peer-install".into(), vec!["peer".into(), "chat".into()], 100_000, 1000).unwrap();
        assert_eq!(grant.authorization_epoch, 1); assert_eq!(grant.origin_transport, "peer");
        assert_eq!(grant.device_ownership, "shared"); assert_eq!(grant.canonical_user_id, format!("user::{}", grant.owner_key));
        let saved = crate::client_store::register(&empty, grant.clone(), 1000).unwrap();
        let saved = crate::client_store::admit(&saved, &grant, 9, 1000).unwrap();
        let revoked = crate::client_store::revoke(&saved, &grant.device_id, 2).unwrap();
        let renewed = approval_grant(&revoked, &endpoint, "synthetic-peer-install".into(), grant.scopes.clone(), 110_000, 1001).unwrap();
        assert_eq!(renewed.authorization_epoch, 3); assert_eq!(renewed.device_id, grant.device_id);
        let updated = crate::client_store::register(&revoked, renewed, 1001).unwrap();
        assert_eq!(crate::client_store::load(&updated, &endpoint, 1001).unwrap().generation_floor, 9);
        assert!(approval_grant(b"{}", &endpoint, "synthetic-peer-install".into(), grant.scopes.clone(), 100_000, 1000).is_err());
        assert!(approval_grant(&empty, &endpoint, "synthetic-peer-install".into(), vec!["peer".into(), "control".into()], 100_000, 1000).is_err());
        assert!(approval_grant(&empty, &endpoint, "synthetic-peer-install".into(), vec!["chat".into()], 100_000, 1000).is_err());
        assert!(approval_grant(&empty, &endpoint, "synthetic-peer-install".into(), grant.scopes.clone(), 999, 1000).is_err());
    }
    use iroh::{EndpointAddr, SecretKey};

    #[test]
    fn bootstrap_keeps_the_complete_proof_and_rejects_ticket_role_or_schema_changes() {
        let host = SecretKey::from_bytes(&[51;32]).public();
        let guest = SecretKey::from_bytes(&[53;32]).public().to_string();
        let ticket = EndpointTicket::new(EndpointAddr::new(host).with_ip_addr("127.0.0.1:31415".parse().unwrap())).to_string();
        let descriptor = serde_json::json!({"transport":"iroh","type":"answer","version":1,"endpoint_id":host.to_string(),"ticket":ticket});
        use base64::Engine;
        let proof = serde_json::json!({"version":1,"endpoint_id":host.to_string(),"ticket":ticket,
            "redemption":base64::engine::general_purpose::URL_SAFE.encode([7;32]),"redemption_expires_in_seconds":30,
            "capabilities":{"peer":true,"transport":"iroh","media":true},
            "grant":{"endpoint_id":guest,"device_id":"synthetic-device","owner_key":"synthetic-owner",
                "canonical_user_id":"synthetic-user","conversation_key":"synthetic-conversation","origin_transport":"peer",
                "origin_sender_id":"synthetic-install","pairing_mode":"manual-peer","device_ownership":"shared",
                "authorization_epoch":1,"expires_at_ms":100_000,"scopes":["peer","chat","media"]}});
        let canonical = pairing(&descriptor.to_string(),&proof.to_string(),1000).unwrap();
        assert_eq!(serde_json::from_str::<serde_json::Value>(&canonical).unwrap(),proof);
        for changed in [
            { let mut value = proof.clone(); value["grant"]["scopes"] = serde_json::json!(["peer","control"]); value },
            { let mut value = proof.clone(); value["ticket"] = "synthetic-unbound-ticket".into(); value },
            { let mut value = proof.clone(); value["grant"]["device_ownership"] = "own".into(); value },
            { let mut value = proof.clone(); value["capabilities"]["application"] = "room".into(); value },
            { let mut value = proof.clone(); value["grant"]["endpoint_id"] = host.to_string().into(); value },
            { let mut value = proof.clone(); value["version"] = true.into(); value },
            { let mut value = proof.clone(); value["authority"] = "synthetic-forged-role".into(); value },
        ] { assert!(pairing(&descriptor.to_string(),&changed.to_string(),1000).is_err()); }
    }

    #[test]
    fn peer_descriptors_bind_endpoint_kind_and_bounded_routing_hints() {
        let id = SecretKey::from_bytes(&[47;32]).public();
        let ticket = EndpointTicket::new(EndpointAddr::new(id).with_ip_addr("127.0.0.1:31415".parse().unwrap())).to_string();
        let mut value = serde_json::json!({"transport":"iroh","type":"answer","version":1,"endpoint_id":id.to_string(),"ticket":ticket});
        assert!(descriptor(&value.to_string(), "answer").is_ok());
        assert!(descriptor(&value.to_string(), "offer").is_err());
        value["endpoint_id"] = SecretKey::from_bytes(&[49;32]).public().to_string().into();
        assert!(descriptor(&value.to_string(), "answer").is_err());
        value["endpoint_id"] = id.to_string().into();
        value["authorization"] = "synthetic-forged-role".into();
        assert!(descriptor(&value.to_string(), "answer").is_err());
        value.as_object_mut().unwrap().remove("authorization");
        value["ticket"] = EndpointTicket::new(EndpointAddr::new(id)).to_string().into();
        assert!(descriptor(&value.to_string(), "answer").is_err());
        value["type"] = "offer".into();
        value.as_object_mut().unwrap().remove("ticket");
        assert!(descriptor(&value.to_string(), "offer").is_ok());
        assert!(fingerprint(&id.to_string()).unwrap().starts_with("iroh-ed25519 "));
        value["version"] = true.into();
        assert!(descriptor(&value.to_string(), "offer").is_err());
    }
}

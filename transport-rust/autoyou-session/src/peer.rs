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

#[cfg(test)]
mod tests {
    use super::*;
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

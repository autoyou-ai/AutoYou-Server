// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Peer/room descriptors are routing hints. Existing proof and local approval
//! establish grants; endpoint admission still verifies the connection exporter.

use crate::host::{endpoint_bytes, HostError};
use iroh::TransportAddr;
use iroh_tickets::endpoint::EndpointTicket;
use serde::{Deserialize, Serialize};

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

#[cfg(test)]
mod tests {
    use super::*;
    use iroh::{EndpointAddr, SecretKey};

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

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
use crate::{BindingError, EndpointPolicy};
use zeroize::Zeroize;

#[derive(Clone, uniffi::Record)]
pub struct CoreRoutingRecord { pub epoch: u64, pub expires_at_ms: u64, pub ticket: String }

#[uniffi::export]
pub fn sign_core_endpoint_proof(mut secret_key: Vec<u8>, payload: String, issuer: String,
    owner_id: String, device_id: String, now_ms: u64) -> Result<String, BindingError> {
    if secret_key.len()!=32 { secret_key.zeroize(); return Err(BindingError::InvalidInput); }
    let key: [u8;32]=secret_key.as_slice().try_into().map_err(|_|BindingError::InvalidInput)?;
    secret_key.zeroize();
    Ok(autoyou_session::core_routing::sign_endpoint_proof(key,&payload,&issuer,&owner_id,&device_id,now_ms)?)
}

#[uniffi::export]
pub fn verify_core_routing_record(envelope_json: String, core_public_key: String, issuer: String,
    owner_id: String, device_id: String, endpoint_id: String, minimum_epoch: u64, now_ms: u64,
    policy_json: String) -> Result<CoreRoutingRecord, BindingError> {
    if policy_json.len()>64*1024 { return Err(BindingError::InvalidInput); }
    let policy: EndpointPolicy=serde_json::from_str(&policy_json).map_err(|_|BindingError::InvalidInput)?;
    let value=autoyou_session::core_routing::verify_routing_record(&envelope_json,&core_public_key,&issuer,
        &owner_id,&device_id,&endpoint_id,minimum_epoch,now_ms,&policy)?;
    Ok(CoreRoutingRecord { epoch:value.epoch, expires_at_ms:value.expires_at_ms, ticket:value.ticket })
}

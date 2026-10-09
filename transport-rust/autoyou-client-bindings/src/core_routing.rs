// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
use crate::{BindingError, EndpointPolicy};
use zeroize::Zeroize;

#[derive(Clone, uniffi::Record)]
pub struct CoreRoutingRecord { pub epoch: u64, pub expires_at_ms: u64, pub ticket: String }
#[derive(Clone, uniffi::Record)]
pub struct StoredCoreRoutingRecord {
    pub epoch:u64, pub expires_at_ms:u64, pub ticket:String, pub protected_store:Vec<u8>,
    pub owner_id:String,
}
#[derive(Clone, uniffi::Record)]
pub struct CoreRelayConfiguration {
    pub epoch:u64, pub expires_at_ms:u64, pub credentials_json:String, pub protected_store:Vec<u8>,
}

#[uniffi::export]
pub fn empty_core_routing_store()->Vec<u8> { autoyou_session::core_routing::empty_store() }

#[uniffi::export]
pub fn accept_core_routing_record(protected_store:Vec<u8>,envelope_json:String,core_public_key:String,
    issuer:String,owner_id:String,device_id:String,endpoint_id:String,now_ms:u64,policy_json:String)
    ->Result<StoredCoreRoutingRecord,BindingError> {
    if policy_json.len()>64*1024 { return Err(BindingError::InvalidInput); }
    let policy:EndpointPolicy=serde_json::from_str(&policy_json).map_err(|_|BindingError::InvalidInput)?;
    let (value,saved)=autoyou_session::core_routing::accept_routing_record(&protected_store,&envelope_json,
        &core_public_key,&issuer,&owner_id,&device_id,&endpoint_id,now_ms,&policy)?;
    Ok(StoredCoreRoutingRecord {epoch:value.epoch,expires_at_ms:value.expires_at_ms,ticket:value.ticket,protected_store:saved,owner_id})
}

#[uniffi::export]
pub fn accept_core_device_routing_record(protected_store:Vec<u8>,envelope_json:String,core_public_key:String,
    issuer:String,device_id:String,endpoint_id:String,now_ms:u64,policy_json:String)->Result<StoredCoreRoutingRecord,BindingError> {
    if policy_json.len()>64*1024 { return Err(BindingError::InvalidInput); }
    let policy:EndpointPolicy=serde_json::from_str(&policy_json).map_err(|_|BindingError::InvalidInput)?;
    let (value,saved,owner_id)=autoyou_session::core_routing::accept_device_routing_record(&protected_store,&envelope_json,
        &core_public_key,&issuer,&device_id,&endpoint_id,now_ms,&policy)?;
    Ok(StoredCoreRoutingRecord {epoch:value.epoch,expires_at_ms:value.expires_at_ms,ticket:value.ticket,protected_store:saved,owner_id})
}

#[uniffi::export]
pub fn accept_core_relay_configuration(protected_store:Vec<u8>,envelope_json:String,core_public_key:String,
    issuer:String,owner_id:String,device_id:String,endpoint_id:String,now_ms:u64,policy_json:String)
    ->Result<CoreRelayConfiguration,BindingError> {
    if policy_json.len()>64*1024 { return Err(BindingError::InvalidInput); }
    let policy:EndpointPolicy=serde_json::from_str(&policy_json).map_err(|_|BindingError::InvalidInput)?;
    let (value,saved)=autoyou_session::core_routing::accept_relay_configuration(&protected_store,&envelope_json,
        &core_public_key,&issuer,&owner_id,&device_id,&endpoint_id,now_ms,&policy)?;
    Ok(CoreRelayConfiguration {epoch:value.epoch,expires_at_ms:value.expires_at_ms,
        credentials_json:serde_json::to_string(&value.relays).map_err(|_|BindingError::InvalidInput)?,protected_store:saved})
}

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

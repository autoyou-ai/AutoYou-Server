// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.
use crate::BindingError;
use autoyou_protocol::{Envelope, MessageHeader, MessageType, delivery::{Control,Status,prompt_digest}};
use autoyou_session::{delivery_store::{DeliveryStore,Operation},file_store::FileScope};
use std::{path::PathBuf,sync::Arc};

#[derive(Debug,Clone,uniffi::Enum)]
pub enum DeliveryPhase { Missing,Queued,Pending,Accepted,Uncertain,Deleted,Expired }
impl DeliveryPhase {
    fn core(&self)->Status { match self { Self::Missing=>Status::Missing,Self::Queued=>Status::Queued,Self::Pending=>Status::Pending,
        Self::Accepted=>Status::Accepted,Self::Uncertain=>Status::Uncertain,Self::Deleted=>Status::Deleted,Self::Expired=>Status::Expired } }
}
#[derive(Debug,Clone,uniffi::Record)]
pub struct DeliveryOperation {
    pub operation_id:String,pub digest:Vec<u8>,pub revision:String,pub expires_at_ms:u64,
    pub client_prompt_id:Option<String>,
    pub generation:u64,pub phase:DeliveryPhase,pub envelope:Option<Vec<u8>>,pub execute:bool,
}
impl From<Operation> for DeliveryOperation {
    fn from(v:Operation)->Self { Self {operation_id:v.operation_id,digest:v.digest,revision:v.revision,expires_at_ms:v.expires_at_ms,
        client_prompt_id:v.client_prompt_id,
        generation:v.generation,envelope:v.envelope,execute:v.execute,
        phase:match v.status {Status::Missing=>DeliveryPhase::Missing,Status::Queued=>DeliveryPhase::Queued,Status::Pending=>DeliveryPhase::Pending,
            Status::Accepted=>DeliveryPhase::Accepted,Status::Uncertain=>DeliveryPhase::Uncertain,Status::Deleted=>DeliveryPhase::Deleted,Status::Expired=>DeliveryPhase::Expired} } }
}
#[uniffi::export]
pub fn delivery_store_has_state(root_directory:String)->Result<bool,BindingError> { Ok(autoyou_session::file_store::has_state(PathBuf::from(root_directory))?) }
#[uniffi::export]
pub fn delivery_prompt_supported(envelope:Vec<u8>)->Result<bool,BindingError> {
    let parsed=Envelope::from_slice(&envelope).map_err(|_|BindingError::InvalidInput)?;
    Ok(prompt_digest(&parsed).is_ok())
}
#[uniffi::export]
pub fn application_delivery_control(control_json:String,message_id:String)->Result<Vec<u8>,BindingError> {
    if control_json.len()>4096 {return Err(BindingError::InvalidInput);}
    let control:Control=serde_json::from_str(&control_json).map_err(|_|BindingError::InvalidInput)?;
    control.validate().map_err(|_|BindingError::InvalidInput)?;
    Envelope {header:MessageHeader{message_id,message_type:MessageType::ApplicationDeliveryControl,timestamp:0.0,
        session_id:None,user_id:None,extensions:Default::default()},
        payload:serde_json::to_value(control).map_err(|_|BindingError::InvalidInput)?.as_object().ok_or(BindingError::InvalidInput)?.clone(),
        extensions:Default::default()}.to_vec().map_err(|_|BindingError::InvalidInput)
}
#[uniffi::export]
pub fn parse_application_delivery_control(envelope:Vec<u8>)->Result<String,BindingError> {
    let value=Control::from_envelope(&Envelope::from_slice(&envelope).map_err(|_|BindingError::InvalidInput)?).map_err(|_|BindingError::InvalidInput)?;
    serde_json::to_string(&value).map_err(|_|BindingError::InvalidInput)
}
#[derive(uniffi::Object)]
pub struct ApplicationDeliveryStore {store:DeliveryStore}
#[uniffi::export]
impl ApplicationDeliveryStore {
    #[uniffi::constructor]
    pub fn new(root_directory:String,storage_key:Vec<u8>,scope_json:String,create:bool)->Result<Arc<Self>,BindingError> {
        if scope_json.len()>16384 {return Err(BindingError::InvalidInput);}
        let scope:FileScope=serde_json::from_str(&scope_json).map_err(|_|BindingError::InvalidInput)?;
        let key:[u8;32]=storage_key.try_into().map_err(|_|BindingError::InvalidInput)?;
        Ok(Arc::new(Self{store:DeliveryStore::open(PathBuf::from(root_directory),key,scope,create)?}))
    }
    pub fn revision(&self,generation:u64)->Result<String,BindingError> {Ok(self.store.revision(generation)?)}
    pub fn align_revision(&self,revision:String,generation:u64,now_ms:u64)->Result<Vec<DeliveryOperation>,BindingError> {
        Ok(self.store.align_revision(&revision,generation,now_ms)?.into_iter().map(Into::into).collect())
    }
    pub fn reset_revision(&self,generation:u64)->Result<String,BindingError> {Ok(self.store.reset_revision(generation)?)}
    pub fn pause_revision(&self,generation:u64)->Result<String,BindingError> {Ok(self.store.pause_revision(generation)?)}
    pub fn resume_revision(&self,revision:String,generation:u64)->Result<(),BindingError> {Ok(self.store.resume_revision(&revision,generation)?)}
    pub fn queue(&self,envelope:Vec<u8>,generation:u64,now_ms:u64,expires_at_ms:u64)->Result<DeliveryOperation,BindingError> {
        Ok(self.store.queue(Envelope::from_slice(&envelope).map_err(|_|BindingError::InvalidInput)?,generation,now_ms,expires_at_ms)?.into())
    }
    pub fn begin(&self,envelope:Vec<u8>,generation:u64,now_ms:u64)->Result<DeliveryOperation,BindingError> {
        Ok(self.store.begin(&Envelope::from_slice(&envelope).map_err(|_|BindingError::InvalidInput)?,generation,now_ms)?.into())
    }
    pub fn finish(&self,operation_id:String,generation:u64,now_ms:u64,accepted:bool)->Result<DeliveryOperation,BindingError> {
        Ok(self.store.finish(&operation_id,generation,now_ms,accepted)?.into())
    }
    pub fn query(&self,operation_id:String,digest:Vec<u8>,revision:String,expires_at_ms:u64,generation:u64,now_ms:u64)->Result<DeliveryOperation,BindingError> {
        Ok(self.store.query(&operation_id,&digest,&revision,expires_at_ms,generation,now_ms)?.into())
    }
    pub fn receipt(&self,operation_id:String,digest:Vec<u8>,revision:String,expires_at_ms:u64,phase:DeliveryPhase,generation:u64,now_ms:u64)->Result<DeliveryOperation,BindingError> {
        Ok(self.store.receipt(&operation_id,&digest,&revision,expires_at_ms,phase.core(),generation,now_ms)?.into())
    }
    pub fn pending(&self,generation:u64,now_ms:u64)->Result<Vec<DeliveryOperation>,BindingError> {
        Ok(self.store.pending(generation,now_ms)?.into_iter().map(Into::into).collect())
    }
    pub fn observations(&self,generation:u64,now_ms:u64)->Result<Vec<DeliveryOperation>,BindingError> {
        Ok(self.store.observations(generation,now_ms)?.into_iter().map(Into::into).collect())
    }
    pub fn delete_pending(&self,generation:u64)->Result<(),BindingError> {Ok(self.store.delete_pending(generation)?)}
    pub fn delete_pending_history(&self,conversation_key:Option<String>)->Result<Vec<DeliveryOperation>,BindingError> {
        Ok(self.store.delete_pending_history(conversation_key.as_deref())?.into_iter().map(Into::into).collect())
    }
    pub fn delete_pending_prompts(&self,prompt_ids:Vec<String>)->Result<Vec<DeliveryOperation>,BindingError> {
        Ok(self.store.delete_pending_prompts(&prompt_ids)?.into_iter().map(Into::into).collect())
    }
    pub fn sweep(&self,now_ms:u64)->Result<u32,BindingError> {Ok(self.store.sweep(now_ms)?)}
}

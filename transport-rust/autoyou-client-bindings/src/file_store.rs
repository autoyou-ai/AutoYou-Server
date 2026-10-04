// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

use crate::{BindingError, ByteStreamKind};
use autoyou_protocol::{binary::Descriptor, Envelope, MessageHeader, MessageType};
use autoyou_session::file_store::{FileStore, FileScope, FileCheckpoint, FilePhase, FileError};
use std::{path::PathBuf, sync::{Arc, Mutex}};

impl From<FileError> for BindingError {
    fn from(error: FileError) -> Self { match error {
        FileError::Invalid => Self::InvalidInput, FileError::Stale => Self::PermissionDenied,
        FileError::Deleted => Self::FileDeleted, FileError::Unavailable => Self::StorageUnavailable,
        FileError::Capacity => Self::StorageCapacity, FileError::Digest => Self::DigestMismatch,
    } }
}
#[derive(Debug, Clone, uniffi::Enum)]
pub enum FileTransferPhase { Pending, Committed, Deleted }
#[derive(Debug, Clone, uniffi::Record)]
pub struct FileTransferCheckpoint {
    pub descriptor_json: String, pub generation: u64, pub offset: u64,
    pub prefix_sha256: Vec<u8>, pub phase: FileTransferPhase,
}
fn checkpoint(value: FileCheckpoint) -> Result<FileTransferCheckpoint, BindingError> {
    Ok(FileTransferCheckpoint { descriptor_json: serde_json::to_string(&value.descriptor).map_err(|_| BindingError::InvalidInput)?,
        generation: value.generation, offset: value.offset, prefix_sha256: value.prefix_sha256,
        phase: match value.phase { FilePhase::Pending => FileTransferPhase::Pending, FilePhase::Committed => FileTransferPhase::Committed,
            FilePhase::Deleted => FileTransferPhase::Deleted } })
}
fn descriptor(json: &str) -> Result<Descriptor, BindingError> {
    if json.len() > 32 * 1024 { return Err(BindingError::InvalidInput); }
    let value: Descriptor = serde_json::from_str(json).map_err(|_| BindingError::InvalidInput)?;
    value.validate().map_err(|_| BindingError::InvalidInput)?; Ok(value)
}
/// Read-only preflight before a host creates a new platform-protected file key.
/// Existing encrypted state requires the original key, including in test roots.
#[uniffi::export]
pub fn file_store_has_state(root_directory: String) -> Result<bool, BindingError> {
    Ok(autoyou_session::file_store::has_state(PathBuf::from(root_directory))?)
}
#[uniffi::export]
pub fn transfer_limits(limits_json: String, message_id: String) -> Result<Vec<u8>, BindingError> {
    if limits_json.len() > 1024 { return Err(BindingError::InvalidInput); }
    let value: autoyou_protocol::binary::TransferLimits = serde_json::from_str(&limits_json).map_err(|_| BindingError::InvalidInput)?;
    value.validate().map_err(|_| BindingError::InvalidInput)?;
    Envelope { header: MessageHeader { message_id, message_type: MessageType::TransportTransferLimits, timestamp: 0.0,
        session_id: None, user_id: None, extensions: Default::default() },
        payload: serde_json::to_value(value).map_err(|_| BindingError::InvalidInput)?.as_object().ok_or(BindingError::InvalidInput)?.clone(),
        extensions: Default::default() }.to_vec().map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn parse_transfer_limits(envelope: Vec<u8>) -> Result<String, BindingError> {
    let value = autoyou_protocol::binary::TransferLimits::from_envelope(&Envelope::from_slice(&envelope).map_err(|_| BindingError::InvalidInput)?)
        .map_err(|_| BindingError::InvalidInput)?;
    serde_json::to_string(&value).map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn binary_transfer_control(control_json: String, message_id: String) -> Result<Vec<u8>, BindingError> {
    if control_json.len() > 32 * 1024 { return Err(BindingError::InvalidInput); }
    let value: autoyou_protocol::binary::Control = serde_json::from_str(&control_json).map_err(|_| BindingError::InvalidInput)?;
    value.validate().map_err(|_| BindingError::InvalidInput)?;
    Envelope { header: MessageHeader { message_id, message_type: MessageType::BinaryTransferControl, timestamp: 0.0,
        session_id: None, user_id: None, extensions: Default::default() },
        payload: serde_json::to_value(value).map_err(|_| BindingError::InvalidInput)?.as_object().ok_or(BindingError::InvalidInput)?.clone(),
        extensions: Default::default() }.to_vec().map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn parse_binary_transfer_control(envelope: Vec<u8>) -> Result<String, BindingError> {
    let value = autoyou_protocol::binary::Control::from_envelope(&Envelope::from_slice(&envelope).map_err(|_| BindingError::InvalidInput)?)
        .map_err(|_| BindingError::InvalidInput)?;
    serde_json::to_string(&value).map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn binary_transfer_metadata(descriptor_json: String, message_id: String) -> Result<Vec<u8>, BindingError> {
    let value = descriptor(&descriptor_json)?;
    Envelope { header: MessageHeader { message_id, message_type: MessageType::BinaryTransferOpen, timestamp: 0.0,
        session_id: None, user_id: None, extensions: Default::default() },
        payload: serde_json::to_value(value).map_err(|_| BindingError::InvalidInput)?.as_object().ok_or(BindingError::InvalidInput)?.clone(),
        extensions: Default::default() }.to_vec().map_err(|_| BindingError::InvalidInput)
}
#[uniffi::export]
pub fn binary_transfer_descriptor(metadata: Vec<u8>) -> Result<String, BindingError> {
    let value = Descriptor::from_envelope(&Envelope::from_slice(&metadata).map_err(|_| BindingError::InvalidInput)?)
        .map_err(|_| BindingError::InvalidInput)?;
    serde_json::to_string(&value).map_err(|_| BindingError::InvalidInput)
}

/// A host supplies its platform-protected key and authoritative session scope.
/// The object is a storage boundary, not another enrollment/permission service.
#[derive(uniffi::Object)]
pub struct FileTransferStore { store: Arc<FileStore> }
#[uniffi::export]
impl FileTransferStore {
    #[uniffi::constructor]
    pub fn new(root_directory: String, storage_key: Vec<u8>, scope_json: String, create: bool) -> Result<Arc<Self>, BindingError> {
        if scope_json.len() > 16 * 1024 { return Err(BindingError::InvalidInput); }
        let scope: FileScope = serde_json::from_str(&scope_json).map_err(|_| BindingError::InvalidInput)?;
        let key: [u8;32] = storage_key.try_into().map_err(|_| BindingError::InvalidInput)?;
        Ok(Arc::new(Self { store: Arc::new(FileStore::open(PathBuf::from(root_directory), key, scope, create)?) }))
    }
    pub fn stage(&self, descriptor_json: String, generation: u64, now_ms: u64) -> Result<FileTransferCheckpoint, BindingError> {
        checkpoint(self.store.stage(descriptor(&descriptor_json)?, generation, now_ms)?)
    }
    pub fn checkpoint(&self, transfer_id: String, now_ms: u64) -> Result<Option<FileTransferCheckpoint>, BindingError> {
        self.store.checkpoint(&transfer_id, now_ms)?.map(checkpoint).transpose()
    }
    pub fn append(&self, transfer_id: String, generation: u64, offset: u64, data: Vec<u8>, now_ms: u64) -> Result<FileTransferCheckpoint, BindingError> {
        checkpoint(self.store.append(&transfer_id, generation, offset, &data, now_ms)?)
    }
    pub fn finish(&self, transfer_id: String, generation: u64, now_ms: u64) -> Result<FileTransferCheckpoint, BindingError> {
        checkpoint(self.store.finish(&transfer_id, generation, now_ms)?)
    }
    pub fn read(&self, transfer_id: String, generation: u64, offset: u64, maximum: u32, now_ms: u64) -> Result<Vec<u8>, BindingError> {
        Ok(self.store.read(&transfer_id, generation, offset, maximum as usize, now_ms)?)
    }
    pub fn delete(&self, transfer_id: String, generation: u64, now_ms: u64) -> Result<FileTransferCheckpoint, BindingError> {
        checkpoint(self.store.delete(&transfer_id, generation, now_ms)?)
    }
    pub fn cancel(&self, descriptor_json: String, generation: u64, now_ms: u64) -> Result<FileTransferCheckpoint, BindingError> {
        checkpoint(self.store.cancel(descriptor(&descriptor_json)?, generation, now_ms)?)
    }
    pub fn release(&self, transfer_id: String) -> Result<(), BindingError> { Ok(self.store.release(&transfer_id)?) }
    pub fn sweep(&self, now_ms: u64) -> Result<u32, BindingError> { Ok(self.store.sweep(now_ms)?) }
}

#[derive(Debug, Clone, uniffi::Record)]
pub struct FileByteReceipt { pub stream_id: u64, pub total: u64, pub digest: Vec<u8> }
#[derive(Debug, Clone, uniffi::Record)]
pub struct FileReceiveEvent {
    pub kind: ByteStreamKind, pub stream_id: u64, pub descriptor_json: String,
    pub durable_offset: u64, pub newly_committed: bool, pub control_json: Option<String>, pub receipt: Option<FileByteReceipt>,
}
impl From<autoyou_session::file_receiver::ReceiveError> for BindingError {
    fn from(error: autoyou_session::file_receiver::ReceiveError) -> Self {
        match error { autoyou_session::file_receiver::ReceiveError::Invalid => Self::InvalidInput,
            autoyou_session::file_receiver::ReceiveError::Closed => Self::Closed }
    }
}
/// Dispatch from an owned IO worker; storage calls may fsync. Foreign UI and
/// connection pumps must not run these calls inline or share another grant's sink.
#[derive(uniffi::Object)]
pub struct FileTransferReceiver { receiver: Mutex<autoyou_session::file_receiver::FileReceiver> }
#[uniffi::export]
impl FileTransferReceiver {
    #[uniffi::constructor]
    pub fn new(store: Arc<FileTransferStore>, generation: u64) -> Result<Arc<Self>, BindingError> {
        Ok(Arc::new(Self { receiver: Mutex::new(autoyou_session::file_receiver::FileReceiver::new(store.store.clone(), generation)?) }))
    }
    pub fn configure_limits(&self, limits_json: String) -> Result<(), BindingError> {
        if limits_json.len() > 1024 { return Err(BindingError::InvalidInput); }
        let limits = serde_json::from_str(&limits_json).map_err(|_| BindingError::InvalidInput)?;
        self.receiver.lock().map_err(|_| BindingError::Worker)?.configure_limits(limits)?; Ok(())
    }
    pub fn receive(&self, stream_id: u64, payload: Vec<u8>, now_ms: u64) -> Result<FileReceiveEvent, BindingError> {
        let event = self.receiver.lock().map_err(|_| BindingError::Worker)?.receive(stream_id, &payload, now_ms)?;
        Ok(FileReceiveEvent { kind: match event.kind {
            autoyou_protocol::byte_stream::Kind::Open => ByteStreamKind::Open,
            autoyou_protocol::byte_stream::Kind::Data => ByteStreamKind::Data,
            autoyou_protocol::byte_stream::Kind::Finish => ByteStreamKind::Finish,
            autoyou_protocol::byte_stream::Kind::Abort => ByteStreamKind::Abort },
            stream_id: event.stream_id, descriptor_json: serde_json::to_string(&event.descriptor).map_err(|_| BindingError::InvalidInput)?,
            durable_offset: event.durable_offset, newly_committed: event.newly_committed,
            control_json: event.control.map(|value| serde_json::to_string(&value)).transpose().map_err(|_| BindingError::InvalidInput)?,
            receipt: event.receipt.map(|value| FileByteReceipt { stream_id: value.stream_id, total: value.total, digest: value.digest }) })
    }
    pub fn control(&self, control_json: String, now_ms: u64) -> Result<String, BindingError> {
        if control_json.len() > 32 * 1024 { return Err(BindingError::InvalidInput); }
        let control = serde_json::from_str(&control_json).map_err(|_| BindingError::InvalidInput)?;
        let status = self.receiver.lock().map_err(|_| BindingError::Worker)?.control(control, now_ms)?;
        serde_json::to_string(&status).map_err(|_| BindingError::InvalidInput)
    }
    pub fn active_count(&self) -> Result<u32, BindingError> {
        Ok(self.receiver.lock().map_err(|_| BindingError::Worker)?.active_count() as u32)
    }
    pub fn shutdown(&self) -> Result<(), BindingError> { self.receiver.lock().map_err(|_| BindingError::Worker)?.close(); Ok(()) }
}

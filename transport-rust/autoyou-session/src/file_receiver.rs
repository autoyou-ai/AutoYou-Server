// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Bounded assembly of file records into an already scoped protected store.
//! Hosts check the live grant before dispatch and after each disk operation.
//! Byte receipts never claim that an application effect has been committed.

use crate::file_store::{FileError, FilePhase, FileStore};
use autoyou_protocol::{binary::{Control, Descriptor, Failure, StatusPhase},
    binary::TransferLimits, byte_stream::{Kind, Receipt, Receiver as ByteReceiver, Record}, Envelope, Lane};
use sha2::{Digest, Sha256};
use std::{collections::HashMap, sync::Arc};

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum ReceiveError {
    #[error("invalid file stream")] Invalid,
    #[error("file receiver is closed")] Closed,
}
pub struct Event {
    pub kind: Kind,
    pub stream_id: u64,
    pub descriptor: Descriptor,
    pub durable_offset: u64,
    pub newly_committed: bool,
    pub control: Option<Control>,
    pub receipt: Option<Receipt>,
}
struct Pending { descriptor: Descriptor, durable_offset: u64, failure: Option<Failure>, already_committed: bool }
pub struct FileReceiver {
    store: Arc<FileStore>, generation: u64, bytes: ByteReceiver,
    pending: HashMap<u64, Pending>, closed: bool, limits: TransferLimits, used: bool,
}
impl FileReceiver {
    pub fn new(store: Arc<FileStore>, generation: u64) -> Result<Self, ReceiveError> {
        if generation == 0 { return Err(ReceiveError::Invalid); }
        Ok(Self { store, generation, bytes: ByteReceiver::default(), pending: HashMap::new(), closed: false,
            limits: TransferLimits::default(), used: false })
    }
    pub fn configure_limits(&mut self, limits: TransferLimits) -> Result<(), ReceiveError> {
        if self.closed || self.used { return Err(ReceiveError::Invalid); }
        limits.validate().map_err(|_| ReceiveError::Invalid)?;
        self.limits = limits; Ok(())
    }
    fn status(&self, descriptor: &Descriptor, now_ms: u64, failure: Option<Failure>) -> Control {
        let mut canonical = descriptor.clone(); canonical.offset = 0;
        match self.store.checkpoint(&descriptor.transfer_id, now_ms) {
            Ok(Some(checkpoint)) if checkpoint.descriptor.same_file(descriptor) && checkpoint.generation <= self.generation => {
                let phase = match checkpoint.phase {
                    FilePhase::Deleted => StatusPhase::Deleted,
                    _ if failure.is_some() => StatusPhase::Unavailable,
                    FilePhase::Pending => StatusPhase::Pending,
                    FilePhase::Committed => StatusPhase::Committed,
                };
                Control::Status { descriptor: canonical, offset: checkpoint.offset,
                    prefix_sha256: checkpoint.prefix_sha256, phase, failure }
            }
            Ok(None) => Control::Status { descriptor: canonical, offset: 0,
                prefix_sha256: Sha256::digest([]).to_vec(),
                phase: if failure.is_some() { StatusPhase::Unavailable } else { StatusPhase::Pending }, failure },
            result => Control::Status { descriptor: canonical, offset: 0,
                prefix_sha256: Sha256::digest([]).to_vec(), phase: StatusPhase::Unavailable,
                failure: Some(match result { Ok(_) => Failure::Stale, Err(error) => failure_for(error) }) },
        }
    }
    pub fn control(&mut self, control: Control, now_ms: u64) -> Result<Control, ReceiveError> {
        if self.closed { return Err(ReceiveError::Closed); }
        control.validate().map_err(|_| ReceiveError::Invalid)?;
        self.used = true;
        let (descriptor, cancel) = match control {
            Control::Query { descriptor } => (descriptor, false),
            Control::Cancel { descriptor } => (descriptor, true),
            Control::Status { .. } => return Err(ReceiveError::Invalid),
        };
        if descriptor.check_expiry(now_ms).is_err() { return Ok(self.status(&descriptor, now_ms, Some(Failure::Expired))); }
        if !cancel && !self.limits.permits(&descriptor) { return Ok(self.status(&descriptor, now_ms, Some(Failure::Capacity))); }
        if !cancel {
            if let Some(pending) = self.pending.values().find(|item| item.descriptor.same_file(&descriptor)) {
                if let Some(failure) = pending.failure { return Ok(self.status(&descriptor, now_ms, Some(failure))); }
            }
            let failure = match self.store.checkpoint(&descriptor.transfer_id, now_ms) {
                Ok(Some(checkpoint)) if checkpoint.descriptor.same_file(&descriptor) && checkpoint.phase != FilePhase::Deleted => {
                    let mut resumed = descriptor.clone(); resumed.offset = checkpoint.offset;
                    // Verify the retained ciphertext and advance its generation
                    // before advertising a readable committed file after recovery.
                    if self.pending.values().any(|item| item.descriptor.transfer_id == descriptor.transfer_id) {
                        None
                    } else {
                        match self.store.stage(resumed, self.generation, now_ms) {
                            Ok(_) => self.store.release(&descriptor.transfer_id).err().map(failure_for),
                            Err(error) => Some(failure_for(error)),
                        }
                    }
                }
                Ok(_) => None, Err(error) => Some(failure_for(error)),
            };
            return Ok(self.status(&descriptor, now_ms, failure));
        }
        let failure = match self.store.cancel(descriptor.clone(), self.generation, now_ms) {
            Ok(_) => Failure::Cancelled, Err(error) => failure_for(error),
        };
        // Keep the byte parser alive: already queued records are consumed and
        // checked, but none may resurrect the durable cancellation tombstone.
        for pending in self.pending.values_mut().filter(|item| item.descriptor.same_file(&descriptor)) {
            pending.failure = Some(failure);
        }
        Ok(self.status(&descriptor, now_ms, Some(failure)))
    }
    pub fn receive(&mut self, stream_id: u64, payload: &[u8], now_ms: u64) -> Result<Event, ReceiveError> {
        if self.closed { return Err(ReceiveError::Closed); }
        let result = self.receive_checked(stream_id, payload, now_ms);
        if result.is_err() { self.close(); }
        result
    }
    fn receive_checked(&mut self, stream_id: u64, payload: &[u8], now_ms: u64) -> Result<Event, ReceiveError> {
        self.used = true;
        if stream_id < 2 { return Err(ReceiveError::Invalid); }
        let record = Record::decode(Lane::Binary, payload).map_err(|_| ReceiveError::Invalid)?;
        let descriptor = if record.kind == Kind::Open {
            let descriptor = Descriptor::from_envelope(&Envelope::from_slice(&record.metadata)
                .map_err(|_| ReceiveError::Invalid)?).map_err(|_| ReceiveError::Invalid)?;
            if self.pending.values().any(|item| item.descriptor.transfer_id == descriptor.transfer_id) {
                return Err(ReceiveError::Invalid);
            }
            Some(descriptor)
        } else { None };
        self.bytes.accept(Lane::Binary, stream_id, &record).map_err(|_| ReceiveError::Invalid)?;
        if let Some(descriptor) = descriptor {
            let mut pending = Pending { durable_offset: 0, descriptor, failure: None, already_committed: false };
            pending.failure = if !self.limits.permits(&pending.descriptor) { Some(Failure::Capacity) }
                else if pending.descriptor.check_expiry(now_ms).is_err() { Some(Failure::Expired) }
                else { match self.store.stage(pending.descriptor.clone(), self.generation, now_ms) {
                    Ok(checkpoint) => { pending.durable_offset = checkpoint.offset;
                        pending.already_committed = checkpoint.phase == FilePhase::Committed; None },
                    Err(error) => Some(failure_for(error)),
                } };
            let event = Event { kind: Kind::Open, stream_id, descriptor: pending.descriptor.clone(),
                durable_offset: pending.durable_offset, newly_committed: false,
                control: pending.failure.map(|failure| self.status(&pending.descriptor, now_ms, Some(failure))), receipt: None };
            self.pending.insert(stream_id, pending);
            return Ok(event);
        }
        let mut pending = self.pending.remove(&stream_id).ok_or(ReceiveError::Invalid)?;
        let previously_failed = pending.failure.is_some();
        if pending.failure.is_none() && pending.descriptor.check_expiry(now_ms).is_err() { pending.failure = Some(Failure::Expired); }
        match record.kind {
            Kind::Data if pending.failure.is_none() => {
                match self.store.append(&pending.descriptor.transfer_id, self.generation,
                    pending.descriptor.offset + record.offset, &record.data, now_ms) {
                    Ok(checkpoint) => pending.durable_offset = checkpoint.offset,
                    Err(error) => pending.failure = Some(failure_for(error)),
                }
            }
            Kind::Finish if pending.failure.is_none() => {
                match self.store.finish(&pending.descriptor.transfer_id, self.generation, now_ms) {
                    Ok(checkpoint) => pending.durable_offset = checkpoint.offset,
                    Err(error) => pending.failure = Some(failure_for(error)),
                }
            }
            _ => {}
        }
        let terminal = matches!(record.kind, Kind::Finish | Kind::Abort);
        let event = Event { kind: record.kind, stream_id, descriptor: pending.descriptor.clone(),
            durable_offset: pending.durable_offset,
            newly_committed: record.kind == Kind::Finish && pending.failure.is_none() && !pending.already_committed,
            control: (terminal || (!previously_failed && pending.failure.is_some())).then(|| self.status(&pending.descriptor, now_ms, pending.failure)),
            receipt: terminal.then(|| Receipt { lane: Lane::Binary as u8, stream_id,
                total: record.offset, digest: record.digest.to_vec() }) };
        if terminal { let _ = self.store.release(&pending.descriptor.transfer_id); }
        else { self.pending.insert(stream_id, pending); }
        Ok(event)
    }
    pub fn active_count(&self) -> usize { self.pending.len() }
    pub fn close(&mut self) {
        self.closed = true; self.bytes.clear();
        for pending in self.pending.drain().map(|(_, item)| item) { let _ = self.store.release(&pending.descriptor.transfer_id); }
    }
}
impl Drop for FileReceiver { fn drop(&mut self) { self.close(); } }
fn failure_for(error: FileError) -> Failure {
    match error { FileError::Invalid | FileError::Stale => Failure::Stale,
        FileError::Deleted => Failure::Cancelled, FileError::Unavailable => Failure::Storage,
        FileError::Capacity => Failure::Capacity, FileError::Digest => Failure::Digest }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::file_store::{Direction, FileScope};
    use autoyou_protocol::{binary::Purpose, byte_stream::{Content, Writer, MAX_DATA_BYTES}, MessageHeader, MessageType};
    use std::{path::PathBuf, sync::atomic::{AtomicU64, Ordering}};
    static NEXT: AtomicU64 = AtomicU64::new(1);
    fn store(name: &str) -> Arc<FileStore> {
        let root = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").expect("isolated tests only"))
            .join(format!("synthetic-receiver-{name}-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
        Arc::new(FileStore::open(root, [42;32], FileScope {
            local_endpoint: iroh::SecretKey::from_bytes(&[41;32]).public().to_string(),
            remote_endpoint: iroh::SecretKey::from_bytes(&[43;32]).public().to_string(), server_instance: "synthetic-server".into(),
            device_id: "synthetic-device".into(), owner_key: "synthetic-owner".into(), canonical_user_id: "synthetic-user".into(),
            conversation_key: "synthetic-conversation".into(), authorization_epoch: 1, direction: Direction::Incoming,
        }, true).unwrap())
    }
    fn descriptor(bytes: &[u8]) -> Descriptor {
        Descriptor { transfer_id: "ab".repeat(16), purpose: Purpose::Attachment, filename: "synthetic.bin".into(),
            mime_type: "application/octet-stream".into(), total: bytes.len() as u64, offset: 0,
            sha256: Sha256::digest(bytes).to_vec(), expires_at_ms: 10000, metadata: Default::default() }
    }
    fn writer(descriptor: &Descriptor) -> (Writer, Record) {
        let metadata = Envelope { header: MessageHeader { message_id: "synthetic-file".into(),
            message_type: MessageType::BinaryTransferOpen, timestamp: 0.0, session_id: None, user_id: None, extensions: Default::default() },
            payload: serde_json::to_value(descriptor).unwrap().as_object().unwrap().clone(), extensions: Default::default() }.to_vec().unwrap();
        Writer::open(Lane::Binary, Content::RawFile, descriptor.total - descriptor.offset, metadata).unwrap()
    }
    fn accept(receiver: &mut FileReceiver, record: &Record) -> Event { receiver.receive(3, &record.encode(Lane::Binary).unwrap(), 1000).unwrap() }
    fn phase(control: &Option<Control>) -> (StatusPhase, Option<Failure>) {
        match control.as_ref().unwrap() { Control::Status { phase, failure, .. } => (*phase, *failure), _ => panic!("status expected") }
    }
    #[test]
    fn consumer_limits_reject_before_storage_and_consume_discarded_records() {
        let store = store("consumer-limits"); let bytes = [1, 2, 3]; let mut descriptor = descriptor(&bytes);
        let limits = TransferLimits { attachment_max_bytes: 2, download_max_bytes: 4, ..Default::default() };
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); receiver.configure_limits(limits).unwrap();
        let status = receiver.control(Control::Query { descriptor: descriptor.clone() }, 1000).unwrap();
        assert!(matches!(status, Control::Status { phase: StatusPhase::Unavailable, failure: Some(Failure::Capacity), offset: 0, .. }));
        assert!(receiver.configure_limits(TransferLimits::default()).is_err());
        let (mut writer, open) = writer(&descriptor);
        assert_eq!(phase(&accept(&mut receiver, &open).control), (StatusPhase::Unavailable, Some(Failure::Capacity)));
        accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        let finished = accept(&mut receiver, &writer.finish().unwrap());
        assert!(!finished.newly_committed); assert_eq!(finished.receipt.unwrap().total, 3);
        assert!(store.checkpoint(&descriptor.transfer_id, 1000).unwrap().is_none());
        let mut receiver = FileReceiver::new(store.clone(), 2).unwrap(); receiver.configure_limits(limits).unwrap();
        descriptor.purpose = Purpose::Download;
        let (mut writer, open) = self::writer(&descriptor); accept(&mut receiver, &open);
        accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        assert!(accept(&mut receiver, &writer.finish().unwrap()).newly_committed);
    }
    #[test]
    fn verified_finish_has_separate_consumption_and_durable_commit_receipts() {
        let store = store("finish"); let bytes = vec![71;MAX_DATA_BYTES + 3]; let descriptor = descriptor(&bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
        assert!(accept(&mut receiver, &open).receipt.is_none());
        let progress = accept(&mut receiver, &writer.data(bytes[..MAX_DATA_BYTES].to_vec()).unwrap());
        assert_eq!(progress.durable_offset, MAX_DATA_BYTES as u64); assert!(progress.control.is_none());
        assert_eq!(store.read(&descriptor.transfer_id, 1, 0, 1, 1000), Err(FileError::Invalid));
        accept(&mut receiver, &writer.data(bytes[MAX_DATA_BYTES..].to_vec()).unwrap());
        let end = accept(&mut receiver, &writer.finish().unwrap());
        assert_eq!(phase(&end.control), (StatusPhase::Committed, None)); assert!(end.newly_committed);
        assert_eq!(end.receipt.unwrap().digest, descriptor.sha256);
        assert_eq!(receiver.active_count(), 0); assert_eq!(store.read(&descriptor.transfer_id, 1, MAX_DATA_BYTES as u64, 4, 1000).unwrap(), vec![71;3]);
    }
    #[test]
    fn a_committed_replay_reports_the_receipt_without_repeating_application_notification() {
        let store = store("committed-replay"); let bytes = [5,6,7]; let original = descriptor(&bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut first, open) = writer(&original);
        accept(&mut receiver, &open); accept(&mut receiver, &first.data(bytes.to_vec()).unwrap());
        assert!(accept(&mut receiver, &first.finish().unwrap()).newly_committed); receiver.close();
        let mut receiver = FileReceiver::new(store.clone(), 2).unwrap();
        let mut replay = original.clone(); replay.offset = replay.total;
        let (mut replay_writer, open) = writer(&replay); accept(&mut receiver, &open);
        let end = accept(&mut receiver, &replay_writer.finish().unwrap());
        assert_eq!(phase(&end.control), (StatusPhase::Committed, None)); assert!(!end.newly_committed);
        let mut old = FileReceiver::new(store, 1).unwrap();
        assert_eq!(phase(&Some(old.control(Control::Query { descriptor: original }, 1000).unwrap())),
            (StatusPhase::Unavailable, Some(Failure::Stale)));
    }
    #[test]
    fn committed_query_verifies_and_admits_readback_under_the_fresh_generation() {
        let store = store("query-read"); let bytes = [8,9,10]; let original = descriptor(&bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&original);
        accept(&mut receiver, &open); accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        accept(&mut receiver, &writer.finish().unwrap()); receiver.close();
        let mut receiver = FileReceiver::new(store.clone(), 2).unwrap();
        let result = receiver.control(Control::Query { descriptor: original.clone() }, 1000).unwrap();
        assert_eq!(phase(&Some(result)), (StatusPhase::Committed, None));
        assert_eq!(store.read(&original.transfer_id, 2, 0, 48, 1000).unwrap(), bytes);
        assert_eq!(store.read(&original.transfer_id, 1, 0, 48, 1000), Err(FileError::Stale));
    }
    #[test]
    fn transport_abort_keeps_the_verified_prefix_for_a_new_session_generation() {
        let store = store("resume"); let bytes = vec![23;MAX_DATA_BYTES + 4]; let original = descriptor(&bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut first, open) = writer(&original);
        accept(&mut receiver, &open); accept(&mut receiver, &first.data(bytes[..MAX_DATA_BYTES].to_vec()).unwrap());
        let aborted = accept(&mut receiver, &first.abort().unwrap());
        assert_eq!(phase(&aborted.control), (StatusPhase::Pending, None));
        assert_eq!(aborted.receipt.unwrap().total, MAX_DATA_BYTES as u64); receiver.close();
        let mut receiver = FileReceiver::new(store.clone(), 2).unwrap();
        let status = receiver.control(Control::Query { descriptor: original.clone() }, 1000).unwrap();
        assert!(matches!(status, Control::Status { offset, phase: StatusPhase::Pending, .. } if offset == MAX_DATA_BYTES as u64));
        let mut resumed = original.clone(); resumed.offset = MAX_DATA_BYTES as u64;
        let (mut suffix, open) = writer(&resumed); accept(&mut receiver, &open);
        accept(&mut receiver, &suffix.data(bytes[MAX_DATA_BYTES..].to_vec()).unwrap());
        let end = accept(&mut receiver, &suffix.finish().unwrap());
        assert_eq!(end.receipt.unwrap().total, 4); assert_eq!(phase(&end.control), (StatusPhase::Committed, None));
        assert_eq!(store.read(&original.transfer_id, 1, 0, 1, 1000), Err(FileError::Stale));
    }
    #[test]
    fn cancellation_before_open_or_mid_stream_never_resurrects_a_file() {
        for early in [true, false] {
            let store = store("cancel"); let bytes = vec![16;MAX_DATA_BYTES + 2]; let descriptor = descriptor(&bytes);
            let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
            if !early { accept(&mut receiver, &open); accept(&mut receiver, &writer.data(bytes[..MAX_DATA_BYTES].to_vec()).unwrap()); }
            let control = receiver.control(Control::Cancel { descriptor: descriptor.clone() }, 1000).unwrap();
            assert_eq!(phase(&Some(control)), (StatusPhase::Deleted, Some(Failure::Cancelled)));
            if early { assert_eq!(phase(&accept(&mut receiver, &open).control), (StatusPhase::Deleted, Some(Failure::Cancelled)));
                accept(&mut receiver, &writer.data(bytes[..MAX_DATA_BYTES].to_vec()).unwrap()); }
            accept(&mut receiver, &writer.data(bytes[MAX_DATA_BYTES..].to_vec()).unwrap());
            let end = accept(&mut receiver, &writer.finish().unwrap());
            assert!(end.receipt.is_some()); assert_eq!(phase(&end.control), (StatusPhase::Deleted, Some(Failure::Cancelled)));
            assert_eq!(store.read(&descriptor.transfer_id, 1, 0, 1, 1000), Err(FileError::Deleted));
        }
    }

    #[test]
    fn failed_disk_append_notifies_immediately_and_query_keeps_the_failure() {
        let store = store("append-failure"); let bytes = b"synthetic"; let descriptor = descriptor(bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
        accept(&mut receiver, &open);
        std::fs::create_dir(store.operation_root(&descriptor.transfer_id).unwrap().join("body.enc")).unwrap();
        let progress = accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        assert_eq!(phase(&progress.control), (StatusPhase::Unavailable, Some(Failure::Storage)));
        assert_eq!(progress.durable_offset, 0); assert!(progress.receipt.is_none());
        let status = receiver.control(Control::Query { descriptor: descriptor.clone() }, 1000).unwrap();
        assert_eq!(phase(&Some(status)), (StatusPhase::Unavailable, Some(Failure::Storage)));
        let end = accept(&mut receiver, &writer.finish().unwrap());
        assert_eq!(phase(&end.control), (StatusPhase::Unavailable, Some(Failure::Storage)));
        assert!(!end.newly_committed); assert_eq!(end.receipt.unwrap().total, bytes.len() as u64);
        assert_eq!(receiver.active_count(), 0);
    }
    #[test]
    fn whole_file_digest_failure_is_deleted_even_when_suffix_transport_digest_passes() {
        let store = store("digest"); let bytes = [1,2,3]; let mut descriptor = descriptor(&bytes); descriptor.sha256[0] ^= 1;
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
        accept(&mut receiver, &open); accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        let end = accept(&mut receiver, &writer.finish().unwrap());
        assert!(end.receipt.is_some()); assert_eq!(phase(&end.control), (StatusPhase::Deleted, Some(Failure::Digest)));
    }
    #[test]
    fn malformed_finish_closes_receiver_without_success_receipts_or_commit() {
        let store = store("malformed"); let bytes = [1,2,3]; let descriptor = descriptor(&bytes);
        let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
        accept(&mut receiver, &open); accept(&mut receiver, &writer.data(bytes.to_vec()).unwrap());
        let mut finish = writer.finish().unwrap(); finish.digest[0] ^= 1;
        assert!(matches!(receiver.receive(3, &finish.encode(Lane::Binary).unwrap(), 1000), Err(ReceiveError::Invalid)));
        assert_eq!(receiver.active_count(), 0); assert_eq!(store.checkpoint(&descriptor.transfer_id, 1000).unwrap().unwrap().phase, FilePhase::Pending);
        assert!(matches!(receiver.receive(3, &open.encode(Lane::Binary).unwrap(), 1000), Err(ReceiveError::Closed)));
    }
    #[test]
    fn expiry_and_wrong_resume_are_consumed_without_claiming_commit() {
        for expired in [false, true] {
            let store = store("denied"); let bytes = vec![12;MAX_DATA_BYTES + 1]; let mut descriptor = descriptor(&bytes);
            if !expired { descriptor.offset = MAX_DATA_BYTES as u64; }
            let mut receiver = FileReceiver::new(store.clone(), 1).unwrap(); let (mut writer, open) = writer(&descriptor);
            let now = if expired { 10000 } else { 1000 };
            let start = receiver.receive(3, &open.encode(Lane::Binary).unwrap(), now).unwrap();
            assert_eq!(phase(&start.control), (StatusPhase::Unavailable, Some(if expired { Failure::Expired } else { Failure::Stale })));
            for data in bytes[descriptor.offset as usize..].chunks(MAX_DATA_BYTES) {
                receiver.receive(3, &writer.data(data.to_vec()).unwrap().encode(Lane::Binary).unwrap(), now).unwrap();
            }
            let end = receiver.receive(3, &writer.finish().unwrap().encode(Lane::Binary).unwrap(), now).unwrap();
            assert!(end.receipt.is_some()); assert_eq!(phase(&end.control).0, StatusPhase::Unavailable);
            assert!(store.checkpoint(&descriptor.transfer_id, now).unwrap().is_none());
        }
    }
    #[test]
    fn duplicate_active_transfer_and_status_echo_are_rejected() {
        let store = store("duplicate"); let descriptor = descriptor(&[1]);
        let mut receiver = FileReceiver::new(store, 1).unwrap(); let (_, open) = writer(&descriptor);
        accept(&mut receiver, &open);
        assert!(matches!(receiver.receive(5, &open.encode(Lane::Binary).unwrap(), 1000), Err(ReceiveError::Invalid)));
        assert_eq!(receiver.active_count(), 0);
        let status = Control::Status { descriptor, offset: 0, prefix_sha256: Sha256::digest([]).to_vec(), phase: StatusPhase::Pending, failure: None };
        assert!(matches!(receiver.control(status, 1000), Err(ReceiveError::Closed)));
    }
}

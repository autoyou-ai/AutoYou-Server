// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Bounded encrypted file checkpoints and durable receipts. No path or owner is
//! taken from a peer's descriptor. Hosts supply the admitted scope and current
//! generation, and recheck their session registry around blocking disk work.

use autoyou_protocol::binary::{Descriptor, valid_id};
use autoyou_protocol::byte_stream::MAX_DATA_BYTES;
use ring::{aead, rand::{SecureRandom, SystemRandom}};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{collections::HashMap, fs::{self, File, OpenOptions}, io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf}, sync::Mutex};
use zeroize::Zeroizing;

const MAX_STATE_BYTES: usize = 32 * 1024;
const MAX_RECORDS: usize = 4096;
const MAX_ACTIVE: usize = 64;
const MAX_STORED_BYTES: u64 = 2 * 1024 * 1024 * 1024;
const WITNESS: &[u8] = b"AutoYou-file-store/1";

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum FileError {
    #[error("invalid file operation")] Invalid,
    #[error("obsolete file generation")] Stale,
    #[error("file operation was deleted or expired")] Deleted,
    #[error("protected file storage is unavailable")] Unavailable,
    #[error("file storage capacity is exhausted")] Capacity,
    #[error("file digest did not match")] Digest,
}
pub(crate) type Result<T> = std::result::Result<T, FileError>;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileScope {
    pub local_endpoint: String,
    pub remote_endpoint: String,
    pub server_instance: String,
    pub device_id: String,
    pub owner_key: String,
    pub canonical_user_id: String,
    pub conversation_key: String,
    pub authorization_epoch: u64,
    pub direction: Direction,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Direction { Incoming, Outgoing }
impl FileScope {
    pub fn validate(&self) -> Result<()> {
        for value in [&self.local_endpoint, &self.remote_endpoint] {
            crate::host::endpoint_bytes(value).map_err(|_| FileError::Invalid)?;
        }
        if [&self.server_instance, &self.device_id, &self.owner_key, &self.canonical_user_id, &self.conversation_key]
            .iter().any(|s| s.is_empty() || s.len() > 512 || s.chars().any(char::is_control)) { return Err(FileError::Invalid); }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FilePhase { Pending, Committed, Deleted }
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FileCheckpoint {
    pub descriptor: Descriptor,
    pub generation: u64,
    pub offset: u64,
    pub prefix_sha256: Vec<u8>,
    pub phase: FilePhase,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct State { schema: u8, checkpoint: FileCheckpoint, file_bytes: u64, blocks: u32 }
struct Block { file_offset: u64, plain_offset: u64, plain_length: usize }
struct Cached { digest: Sha256, offset: u64, file_bytes: u64, blocks: Vec<Block>, phase: FilePhase }

pub struct FileStore {
    pub(crate) root: PathBuf, pub(crate) scope: PathBuf, pub(crate) scope_digest: Vec<u8>, key: Zeroizing<[u8;32]>,
    cache: Mutex<HashMap<String, Cached>>,
    maximum_stored_bytes: u64,
}
impl FileStore {
    pub fn open(root: PathBuf, key: [u8;32], scope: FileScope, create: bool) -> Result<Self> {
        scope.validate()?;
        let root = storage_root(root, true)?;
        checked_directory(&root, create)?;
        let scope_bytes = serde_json::to_vec(&scope).map_err(|_| FileError::Invalid)?;
        let scope_digest = Sha256::digest([key.as_slice(), b"file-scope-v1\0", &scope_bytes].concat()).to_vec();
        let store = Self { scope: root.join(hex(&scope_digest)), root, scope_digest, key: Zeroizing::new(key), cache: Mutex::new(HashMap::new()), maximum_stored_bytes: MAX_STORED_BYTES };
        store.transaction(|this, _| {
            let witness = this.root.join("store.enc");
            if witness.exists() {
                if this.unseal(&read_bounded(&witness, 256)?, b"store")? != WITNESS { return Err(FileError::Unavailable); }
            } else {
                if !create || fs::read_dir(&this.root).map_err(|_| FileError::Unavailable)?.filter_map(std::result::Result::ok)
                    .any(|e| e.file_name() != "store.lock") { return Err(FileError::Unavailable); }
                atomic_write(&witness, &this.seal(WITNESS.to_vec(), b"store")?)?;
            }
            checked_directory(&this.scope, true)
        })?;
        Ok(store)
    }

    pub(crate) fn journal_transaction<T>(&self, operation: impl FnOnce(&Self) -> Result<T>) -> Result<T> {
        self.transaction(|store, _cache| operation(store))
    }
    fn transaction<T>(&self, operation: impl FnOnce(&Self, &mut HashMap<String, Cached>) -> Result<T>) -> Result<T> {
        let mut cache = self.cache.lock().map_err(|_| FileError::Unavailable)?;
        let path = self.root.join("store.lock"); checked_file(&path, true)?;
        let lease = open_options().read(true).write(true).create(true).truncate(false).open(path).map_err(|_| FileError::Unavailable)?;
        lease.lock().map_err(|_| FileError::Unavailable)?;
        operation(self, &mut cache)
        // Closing the handle releases its cross-process lock on every result.
    }
    fn cipher(&self) -> Result<aead::LessSafeKey> {
        Ok(aead::LessSafeKey::new(aead::UnboundKey::new(&aead::AES_256_GCM, self.key.as_slice()).map_err(|_| FileError::Unavailable)?))
    }
    pub(crate) fn seal(&self, mut bytes: Vec<u8>, aad: &[u8]) -> Result<Vec<u8>> {
        let mut nonce = [0u8;12]; SystemRandom::new().fill(&mut nonce).map_err(|_| FileError::Unavailable)?;
        self.cipher()?.seal_in_place_append_tag(aead::Nonce::assume_unique_for_key(nonce), aead::Aad::from(aad), &mut bytes)
            .map_err(|_| FileError::Unavailable)?;
        let mut result = nonce.to_vec(); result.extend(bytes); Ok(result)
    }
    pub(crate) fn unseal(&self, bytes: &[u8], aad: &[u8]) -> Result<Vec<u8>> {
        if bytes.len() < 28 { return Err(FileError::Unavailable); }
        let nonce: [u8;12] = bytes[..12].try_into().map_err(|_| FileError::Unavailable)?;
        let mut payload = bytes[12..].to_vec();
        let plain = self.cipher()?.open_in_place(aead::Nonce::assume_unique_for_key(nonce), aead::Aad::from(aad), &mut payload)
            .map_err(|_| FileError::Unavailable)?;
        Ok(plain.to_vec())
    }
    pub(crate) fn operation_root(&self, id: &str) -> Result<PathBuf> {
        if !valid_id(id) { return Err(FileError::Invalid); }
        Ok(self.scope.join(self.operation_name(id)))
    }
    pub(crate) fn operation_name(&self, id: &str) -> String { hex(&Sha256::digest([self.key.as_slice(), b"file-id-v1\0", id.as_bytes()].concat())) }
    pub(crate) fn scope_digest_for(&self, scope: &FileScope) -> Result<Vec<u8>> {
        scope.validate()?;
        let encoded=serde_json::to_vec(scope).map_err(|_|FileError::Invalid)?;
        Ok(Sha256::digest([self.key.as_slice(),b"file-scope-v1\0",&encoded].concat()).to_vec())
    }
    fn aad(&self, id: &str, purpose: &[u8]) -> Vec<u8> {
        self.aad_named(&self.operation_name(id), purpose)
    }
    pub(crate) fn aad_named(&self, name: &str, purpose: &[u8]) -> Vec<u8> {
        [b"AutoYou-file/1\0".as_slice(), &self.scope_digest, name.as_bytes(), purpose].concat()
    }
    fn load(&self, id: &str) -> Result<Option<State>> {
        let root = self.operation_root(id)?;
        if !root.exists() { return Ok(None); }
        checked_directory(&root, false)?;
        let plain = self.unseal(&read_bounded(&root.join("state.enc"), MAX_STATE_BYTES + 28)?, &self.aad(id, b"state"))?;
        let state: State = serde_json::from_slice(&plain).map_err(|_| FileError::Unavailable)?;
        let item = &state.checkpoint;
        if state.schema != 1 || item.descriptor.validate().is_err() || item.descriptor.transfer_id != id || item.generation == 0 ||
            item.offset > item.descriptor.total || item.prefix_sha256.len() != 32 || state.blocks > 21846 ||
            state.file_bytes != item.offset + u64::from(state.blocks) * 32 ||
            (item.phase == FilePhase::Committed && (item.offset != item.descriptor.total || item.prefix_sha256 != item.descriptor.sha256)) {
            return Err(FileError::Unavailable);
        }
        Ok(Some(state))
    }
    fn persist(&self, state: &State) -> Result<()> {
        let id = &state.checkpoint.descriptor.transfer_id;
        let plain = serde_json::to_vec(state).map_err(|_| FileError::Invalid)?;
        if plain.len() > MAX_STATE_BYTES { return Err(FileError::Invalid); }
        atomic_write(&self.operation_root(id)?.join("state.enc"), &self.seal(plain, &self.aad(id, b"state"))?)
    }
    fn checked<'a>(&self, state: &'a State, generation: u64, now_ms: u64) -> Result<&'a FileCheckpoint> {
        let item = &state.checkpoint;
        if generation == 0 || generation != item.generation { return Err(FileError::Stale); }
        if item.phase == FilePhase::Deleted || item.descriptor.expires_at_ms <= now_ms { return Err(FileError::Deleted); }
        Ok(item)
    }
    fn restore(&self, state: &State) -> Result<Cached> {
        let id = &state.checkpoint.descriptor.transfer_id;
        let path = self.operation_root(id)?.join("body.enc"); checked_file(&path, state.file_bytes == 0)?;
        let mut digest = Sha256::new(); let mut blocks = Vec::new(); let mut offset = 0; let mut file_offset = 0;
        if state.file_bytes > 0 {
            let mut file = File::open(&path).map_err(|_| FileError::Unavailable)?;
            if file.metadata().map_err(|_| FileError::Unavailable)?.len() < state.file_bytes { return Err(FileError::Unavailable); }
            while file_offset < state.file_bytes {
                let bytes = self.read_block(&mut file, id, offset)?;
                blocks.push(Block { file_offset, plain_offset: offset, plain_length: bytes.len() });
                digest.update(&bytes); offset += bytes.len() as u64; file_offset += bytes.len() as u64 + 32;
            }
        }
        if offset != state.checkpoint.offset || blocks.len() != state.blocks as usize ||
            digest.clone().finalize().as_slice() != state.checkpoint.prefix_sha256 { return Err(FileError::Unavailable); }
        Ok(Cached { digest, blocks, offset, file_bytes: file_offset, phase: state.checkpoint.phase })
    }
    fn read_block(&self, file: &mut File, id: &str, offset: u64) -> Result<Vec<u8>> {
        let mut size = [0u8;4]; file.read_exact(&mut size).map_err(|_| FileError::Unavailable)?;
        let length = u32::from_be_bytes(size) as usize;
        if length == 0 || length > MAX_DATA_BYTES { return Err(FileError::Unavailable); }
        let mut encrypted = vec![0; length + 28]; file.read_exact(&mut encrypted).map_err(|_| FileError::Unavailable)?;
        self.unseal(&encrypted, &self.aad(id, &[b"body".as_slice(), &offset.to_be_bytes(), &(length as u32).to_be_bytes()].concat()))
    }
    fn usage(&self) -> Result<(usize, u64)> {
        let mut records = 0; let mut bytes = 0;
        // This lock also covers other scopes sharing the storage key.
        for scope in fs::read_dir(&self.root).map_err(|_| FileError::Unavailable)? {
            let scope = scope.map_err(|_| FileError::Unavailable)?;
            if !scope.file_type().map_err(|_| FileError::Unavailable)?.is_dir() { continue; }
            checked_directory(&scope.path(), false)?;
            for operation in fs::read_dir(scope.path()).map_err(|_| FileError::Unavailable)? {
                let operation = operation.map_err(|_| FileError::Unavailable)?;
                checked_directory(&operation.path(), false)?; records += 1;
                if records > MAX_RECORDS { return Err(FileError::Capacity); }
                let body = operation.path().join("body.enc");
                if body.exists() { checked_file(&body, false)?; bytes += body.metadata().map_err(|_| FileError::Unavailable)?.len(); }
            }
        }
        Ok((records, bytes))
    }
    fn sweep_locked(&self, now_ms: u64, cache: &mut HashMap<String, Cached>) -> Result<u32> {
        let mut removed = 0;
        let mut visited = 0;
        let mut scopes = 0;
        // The installation key owns every scope in this root. Retired epochs
        // and conversations must expire even when that scope never reconnects.
        // One root lease prevents this sweep racing another scope's append.
        for scope in fs::read_dir(&self.root).map_err(|_| FileError::Unavailable)? {
            let scope = scope.map_err(|_| FileError::Unavailable)?;
            if !scope.file_type().map_err(|_| FileError::Unavailable)?.is_dir() { continue; }
            checked_directory(&scope.path(), false)?;
            scopes += 1;
            if scopes > MAX_RECORDS { return Err(FileError::Capacity); }
            let scope_name = scope.file_name().to_str().ok_or(FileError::Unavailable)?.to_owned();
            let scope_digest = parse_digest(&scope_name)?;
            for entry in fs::read_dir(scope.path()).map_err(|_| FileError::Unavailable)? {
                let entry = entry.map_err(|_| FileError::Unavailable)?;
                checked_directory(&entry.path(), false)?;
                visited += 1;
                if visited > MAX_RECORDS { return Err(FileError::Capacity); }
                let name = entry.file_name().to_str().ok_or(FileError::Unavailable)?.to_owned();
                let aad = [b"AutoYou-file/1\0".as_slice(), &scope_digest, name.as_bytes(), b"state"].concat();
                let plain = self.unseal(&read_bounded(&entry.path().join("state.enc"), MAX_STATE_BYTES + 28)?, &aad)?;
                let state: State = serde_json::from_slice(&plain).map_err(|_| FileError::Unavailable)?;
                let item = &state.checkpoint;
                let id = &item.descriptor.transfer_id;
                if state.schema != 1 || item.descriptor.validate().is_err() || self.operation_name(id) != name || item.generation == 0 ||
                    item.offset > item.descriptor.total || item.prefix_sha256.len() != 32 || state.blocks > 21846 ||
                    state.file_bytes != item.offset + u64::from(state.blocks) * 32 ||
                    (item.phase == FilePhase::Committed && (item.offset != item.descriptor.total || item.prefix_sha256 != item.descriptor.sha256)) {
                    return Err(FileError::Unavailable);
                }
                if item.descriptor.expires_at_ms <= now_ms {
                    remove_operation(&scope.path(), &entry.path())?;
                    if scope.path() == self.scope { cache.remove(id); }
                    removed += 1;
                }
            }
        }
        Ok(removed)
    }

    pub fn stage(&self, descriptor: Descriptor, generation: u64, now_ms: u64) -> Result<FileCheckpoint> {
        descriptor.check_expiry(now_ms).map_err(|_| FileError::Invalid)?;
        if generation == 0 { return Err(FileError::Stale); }
        self.transaction(|this, cache| {
            let id = &descriptor.transfer_id;
            this.sweep_locked(now_ms, cache)?;
            cache_slot(cache, id)?;
            let mut state = if let Some(mut state) = this.load(id)? {
                if !state.checkpoint.descriptor.same_file(&descriptor) || generation < state.checkpoint.generation { return Err(FileError::Stale); }
                if state.checkpoint.phase == FilePhase::Deleted || state.checkpoint.descriptor.expires_at_ms <= now_ms { return Err(FileError::Deleted); }
                if state.checkpoint.offset != descriptor.offset { return Err(FileError::Invalid); }
                state.checkpoint.generation = generation; state
            } else {
                if descriptor.offset != 0 { return Err(FileError::Invalid); }
                let (records, _) = this.usage()?;
                if records >= MAX_RECORDS { return Err(FileError::Capacity); }
                fs::create_dir(this.operation_root(id)?).map_err(|_| FileError::Unavailable)?;
                State { schema: 1, checkpoint: FileCheckpoint { descriptor: descriptor.clone(), generation, offset: 0,
                    prefix_sha256: Sha256::digest([]).to_vec(), phase: FilePhase::Pending }, file_bytes: 0, blocks: 0 }
            };
            let restored = this.restore(&state)?;
            // Uncheckpointed tail bytes left by a crashed writer cannot become
            // part of a resumed file. Only a valid encrypted state permits truncation.
            let body = this.operation_root(id)?.join("body.enc");
            if body.exists() {
                let file = OpenOptions::new().write(true).open(body).map_err(|_| FileError::Unavailable)?;
                file.set_len(state.file_bytes).map_err(|_| FileError::Unavailable)?;
                file.sync_all().map_err(|_| FileError::Unavailable)?;
            }
            state.checkpoint.descriptor.offset = 0;
            this.persist(&state)?; cache.insert(id.clone(), restored); Ok(state.checkpoint)
        })
    }
    pub fn checkpoint(&self, id: &str, now_ms: u64) -> Result<Option<FileCheckpoint>> {
        self.transaction(|this, _| {
            Ok(this.load(id)?.map(|mut state| {
                if state.checkpoint.descriptor.expires_at_ms <= now_ms { state.checkpoint.phase = FilePhase::Deleted; }
                state.checkpoint
            }))
        })
    }
    pub fn append(&self, id: &str, generation: u64, offset: u64, bytes: &[u8], now_ms: u64) -> Result<FileCheckpoint> {
        if bytes.is_empty() || bytes.len() > MAX_DATA_BYTES { return Err(FileError::Invalid); }
        self.transaction(|this, cache| {
            let mut state = this.load(id)?.ok_or(FileError::Invalid)?;
            let item = this.checked(&state, generation, now_ms)?;
            if item.phase != FilePhase::Pending || item.offset != offset || offset.checked_add(bytes.len() as u64).is_none_or(|v| v > item.descriptor.total) ||
                (bytes.len() != MAX_DATA_BYTES && offset + bytes.len() as u64 != item.descriptor.total) { return Err(FileError::Invalid); }
            let cached = cache.get_mut(id).ok_or(FileError::Stale)?;
            if cached.offset != offset || cached.file_bytes != state.file_bytes { return Err(FileError::Stale); }
            let (_, used) = this.usage()?;
            if used.checked_add(bytes.len() as u64 + 32).is_none_or(|v| v > this.maximum_stored_bytes) { return Err(FileError::Capacity); }
            let encrypted = this.seal(bytes.to_vec(), &this.aad(id, &[b"body".as_slice(), &offset.to_be_bytes(), &(bytes.len() as u32).to_be_bytes()].concat()))?;
            let path = this.operation_root(id)?.join("body.enc"); checked_file(&path, true)?;
            let mut file = open_options().create(true).truncate(false).write(true).open(path).map_err(|_| FileError::Unavailable)?;
            if file.metadata().map_err(|_| FileError::Unavailable)?.len() != state.file_bytes { return Err(FileError::Unavailable); }
            file.seek(SeekFrom::Start(state.file_bytes)).map_err(|_| FileError::Unavailable)?;
            file.write_all(&(bytes.len() as u32).to_be_bytes()).and_then(|_| file.write_all(&encrypted)).and_then(|_| file.sync_all())
                .map_err(|_| FileError::Unavailable)?;
            let mut digest = cached.digest.clone(); digest.update(bytes);
            state.checkpoint.offset += bytes.len() as u64; state.checkpoint.prefix_sha256 = digest.clone().finalize().to_vec();
            state.blocks += 1; state.file_bytes += bytes.len() as u64 + 32;
            this.persist(&state)?;
            cached.blocks.push(Block { file_offset: cached.file_bytes, plain_offset: offset, plain_length: bytes.len() });
            cached.offset = state.checkpoint.offset; cached.file_bytes = state.file_bytes; cached.digest = digest;
            Ok(state.checkpoint)
        })
    }
    pub fn finish(&self, id: &str, generation: u64, now_ms: u64) -> Result<FileCheckpoint> {
        self.transaction(|this, cache| {
            let mut state = this.load(id)?.ok_or(FileError::Invalid)?;
            let item = this.checked(&state, generation, now_ms)?;
            if item.offset != item.descriptor.total { return Err(FileError::Invalid); }
            let mut restored = this.restore(&state)?;
            if restored.digest.clone().finalize().as_slice() != item.descriptor.sha256 {
                state.checkpoint.phase = FilePhase::Deleted; this.persist(&state)?; cache.remove(id);
                let body = this.operation_root(id)?.join("body.enc");
                if body.exists() { checked_file(&body, false)?; fs::remove_file(body).map_err(|_| FileError::Unavailable)?; }
                return Err(FileError::Digest);
            }
            state.checkpoint.phase = FilePhase::Committed;
            this.persist(&state)?;
            restored.phase = FilePhase::Committed;
            if cache.len() < MAX_ACTIVE || cache.contains_key(id) { cache.insert(id.into(), restored); }
            Ok(state.checkpoint)
        })
    }
    pub fn read(&self, id: &str, generation: u64, offset: u64, maximum: usize, now_ms: u64) -> Result<Vec<u8>> {
        if maximum == 0 || maximum > MAX_DATA_BYTES { return Err(FileError::Invalid); }
        self.transaction(|this, cache| {
            let state = this.load(id)?.ok_or(FileError::Invalid)?;
            let item = this.checked(&state, generation, now_ms)?;
            if item.phase != FilePhase::Committed || offset > item.offset { return Err(FileError::Invalid); }
            if offset == item.offset { return Ok(vec![]); }
            if !cache.contains_key(id) {
                cache_slot(cache, id)?;
                cache.insert(id.into(), this.restore(&state)?);
            }
            let cached = cache.get(id).ok_or(FileError::Unavailable)?;
            let block = cached.blocks.iter().find(|block| block.plain_offset <= offset && offset < block.plain_offset + block.plain_length as u64)
                .ok_or(FileError::Unavailable)?;
            let path = this.operation_root(id)?.join("body.enc"); checked_file(&path, false)?;
            let mut file = File::open(path).map_err(|_| FileError::Unavailable)?;
            file.seek(SeekFrom::Start(block.file_offset)).map_err(|_| FileError::Unavailable)?;
            let bytes = this.read_block(&mut file, id, block.plain_offset)?;
            let start = (offset - block.plain_offset) as usize;
            Ok(bytes[start..bytes.len().min(start + maximum)].to_vec())
        })
    }
    pub fn delete(&self, id: &str, generation: u64, now_ms: u64) -> Result<FileCheckpoint> {
        self.transaction(|this, cache| {
            let mut state = this.load(id)?.ok_or(FileError::Invalid)?;
            if generation == 0 || generation < state.checkpoint.generation { return Err(FileError::Stale); }
            state.checkpoint.generation = generation; state.checkpoint.phase = FilePhase::Deleted;
            this.persist(&state)?; cache.remove(id);
            let body = this.operation_root(id)?.join("body.enc");
            if body.exists() { checked_file(&body, false)?; fs::remove_file(body).map_err(|_| FileError::Unavailable)?; }
            let _ = now_ms; Ok(state.checkpoint)
        })
    }
    pub fn cancel(&self, descriptor: Descriptor, generation: u64, now_ms: u64) -> Result<FileCheckpoint> {
        descriptor.check_expiry(now_ms).map_err(|_| FileError::Invalid)?;
        if generation == 0 || descriptor.offset != 0 { return Err(FileError::Invalid); }
        self.transaction(|this, cache| {
            let id = &descriptor.transfer_id;
            let mut state = if let Some(state) = this.load(id)? {
                if generation < state.checkpoint.generation || !state.checkpoint.descriptor.same_file(&descriptor) { return Err(FileError::Stale); }
                state
            } else {
                this.sweep_locked(now_ms, cache)?;
                if this.usage()?.0 >= MAX_RECORDS { return Err(FileError::Capacity); }
                fs::create_dir(this.operation_root(id)?).map_err(|_| FileError::Unavailable)?;
                State { schema: 1, checkpoint: FileCheckpoint { descriptor: descriptor.clone(), generation, offset: 0,
                    prefix_sha256: Sha256::digest([]).to_vec(), phase: FilePhase::Deleted }, file_bytes: 0, blocks: 0 }
            };
            state.checkpoint.phase = FilePhase::Deleted; state.checkpoint.generation = generation;
            // Persist the tombstone before deleting bytes or acknowledging the
            // cancellation, including when priority control overtakes File Open.
            this.persist(&state)?; cache.remove(id);
            let body = this.operation_root(id)?.join("body.enc");
            if body.exists() { checked_file(&body, false)?; fs::remove_file(body).map_err(|_| FileError::Unavailable)?; }
            Ok(state.checkpoint)
        })
    }
    pub fn release(&self, id: &str) -> Result<()> { self.transaction(|_, cache| { cache.remove(id); Ok(()) }) }
    pub fn sweep(&self, now_ms: u64) -> Result<u32> { self.transaction(|this, cache| this.sweep_locked(now_ms, cache)) }
}

fn storage_root(root: PathBuf, create_test_root: bool) -> Result<PathBuf> {
    // Use exactly the same isolated namespace for read-only key preflight and IO.
    let root = if let Some(test) = std::env::var_os("AUTOYOU_TEST_ROOT") {
        if test.is_empty() { return Err(FileError::Invalid); }
        let test = PathBuf::from(test);
        if create_test_root { fs::create_dir_all(&test).map_err(|_| FileError::Unavailable)?; }
        let test = if test.exists() { test.canonicalize().map_err(|_| FileError::Unavailable)? } else { test };
        test.join("file-stores").join(hex(&Sha256::digest(root.to_string_lossy().as_bytes())))
    } else { root };
    if root.as_os_str().is_empty() || !root.is_absolute() { return Err(FileError::Invalid); }
    Ok(root)
}
pub fn has_state(root: PathBuf) -> Result<bool> {
    let root = storage_root(root, false)?;
    if !root.exists() { return Ok(false); }
    checked_directory(&root, false)?;
    Ok(fs::read_dir(root).map_err(|_| FileError::Unavailable)?.next().transpose().map_err(|_| FileError::Unavailable)?.is_some())
}

fn cache_slot(cache: &mut HashMap<String, Cached>, id: &str) -> Result<()> {
    if cache.contains_key(id) || cache.len() < MAX_ACTIVE { return Ok(()); }
    // Read indexes are disposable. Eviction cannot interrupt a pending writer
    // or make the 65th history attachment permanently unreadable in this process.
    if let Some(retired) = cache.iter().find_map(|(key, value)|
        (value.phase == FilePhase::Committed).then(|| key.clone())) {
        cache.remove(&retired); Ok(())
    } else { Err(FileError::Capacity) }
}

pub(crate) fn hex(bytes: &[u8]) -> String { bytes.iter().map(|v| format!("{v:02x}")).collect() }
pub(crate) fn valid_revision(value:&str)->bool { value.len()==32 && value.bytes().all(|v|v.is_ascii_digit() || (b'a'..=b'f').contains(&v)) }
pub(crate) fn parse_digest(value: &str) -> Result<Vec<u8>> {
    if value.len() != 64 || !value.bytes().all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v)) { return Err(FileError::Unavailable); }
    (0..64).step_by(2).map(|i| u8::from_str_radix(&value[i..i+2], 16).map_err(|_| FileError::Unavailable)).collect()
}
pub(crate) fn checked_directory(path: &Path, create: bool) -> Result<()> {
    if !path.exists() && create { fs::create_dir_all(path).map_err(|_| FileError::Unavailable)?; }
    let meta = fs::symlink_metadata(path).map_err(|_| FileError::Unavailable)?;
    if !meta.is_dir() || meta.file_type().is_symlink() { return Err(FileError::Unavailable); }
    Ok(())
}
pub(crate) fn checked_file(path: &Path, may_create: bool) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_file() && !meta.file_type().is_symlink() => Ok(()),
        Err(error) if may_create && error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        _ => Err(FileError::Unavailable),
    }
}
fn open_options() -> OpenOptions {
    #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; let mut options = OpenOptions::new(); options.mode(0o600); options }
    #[cfg(not(unix))] { OpenOptions::new() }
}
pub(crate) fn read_bounded(path: &Path, maximum: usize) -> Result<Vec<u8>> {
    checked_file(path, false)?; let file = File::open(path).map_err(|_| FileError::Unavailable)?;
    if file.metadata().map_err(|_| FileError::Unavailable)?.len() > maximum as u64 { return Err(FileError::Unavailable); }
    let mut bytes = Vec::new(); file.take(maximum as u64 + 1).read_to_end(&mut bytes).map_err(|_| FileError::Unavailable)?;
    if bytes.len() > maximum { return Err(FileError::Unavailable); } Ok(bytes)
}
pub(crate) fn remove_operation(scope: &Path, operation: &Path) -> Result<()> {
    // Delete only an absolute, verified descendant and only files owned by this
    // format. Never follow a symlink or recursively delete a computed path.
    let scope = scope.canonicalize().map_err(|_| FileError::Unavailable)?;
    let resolved = operation.canonicalize().map_err(|_| FileError::Unavailable)?;
    if !resolved.starts_with(&scope) || resolved == scope { return Err(FileError::Unavailable); }
    checked_directory(operation, false)?;
    let entries: Vec<_> = fs::read_dir(operation).map_err(|_| FileError::Unavailable)?.collect::<std::io::Result<_>>()
        .map_err(|_| FileError::Unavailable)?;
    for entry in &entries {
        let name = entry.file_name().to_str().ok_or(FileError::Unavailable)?.to_owned();
        if name != "state.enc" && name != "body.enc" && !(name.starts_with("write-") && name.ends_with(".tmp") &&
            valid_id(&name[6..name.len()-4])) { return Err(FileError::Unavailable); }
        checked_file(&entry.path(), false)?;
    }
    for entry in entries { fs::remove_file(entry.path()).map_err(|_| FileError::Unavailable)?; }
    fs::remove_dir(operation).map_err(|_| FileError::Unavailable)
}
pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    checked_file(path, true)?;
    let mut nonce = [0u8;16]; SystemRandom::new().fill(&mut nonce).map_err(|_| FileError::Unavailable)?;
    let temporary = path.with_file_name(format!("write-{}.tmp", hex(&nonce)));
    let result = (|| {
        let mut file = open_options().write(true).create_new(true).open(&temporary).map_err(|_| FileError::Unavailable)?;
        file.write_all(bytes).and_then(|_| file.sync_all()).map_err(|_| FileError::Unavailable)?;
        drop(file); fs::rename(&temporary, path).map_err(|_| FileError::Unavailable)?;
        #[cfg(unix)] { File::open(path.parent().ok_or(FileError::Unavailable)?).and_then(|f| f.sync_all()).map_err(|_| FileError::Unavailable)?; }
        Ok(())
    })();
    if temporary.exists() { let _ = fs::remove_file(temporary); }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use autoyou_protocol::binary::Purpose;
    use std::sync::{Arc, atomic::{AtomicU64, Ordering}};
    static NEXT: AtomicU64 = AtomicU64::new(1);
    fn root(name: &str) -> PathBuf {
        let base = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").expect("file tests require AUTOYOU_TEST_ROOT"));
        base.join(format!("synthetic-file-{name}-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)))
    }
    fn scope() -> FileScope {
        FileScope { local_endpoint: iroh::SecretKey::from_bytes(&[91;32]).public().to_string(),
            remote_endpoint: iroh::SecretKey::from_bytes(&[92;32]).public().to_string(), server_instance: "synthetic-instance".into(),
            device_id: "synthetic-device".into(), owner_key: "synthetic-owner".into(), canonical_user_id: "synthetic-user".into(),
            conversation_key: "synthetic-conversation".into(), authorization_epoch: 1, direction: Direction::Incoming }
    }
    fn descriptor(bytes: &[u8]) -> Descriptor {
        Descriptor { transfer_id: "ab".repeat(16), purpose: Purpose::Attachment, filename: "synthetic.bin".into(),
            mime_type: "application/octet-stream".into(), total: bytes.len() as u64, offset: 0, sha256: Sha256::digest(bytes).to_vec(),
            expires_at_ms: 10_000, metadata: Default::default() }
    }
    #[test]
    fn protected_key_preflight_is_read_only_and_uses_the_same_test_namespace_as_storage() {
        let path = root("key-preflight"); assert!(!has_state(path.clone()).unwrap()); assert!(!path.exists());
        let store = FileStore::open(path.clone(), [9;32], scope(), true).unwrap();
        assert!(has_state(path.clone()).unwrap()); assert!(!path.exists());
        let witness = fs::read(store.root.join("store.enc")).unwrap();
        assert!(matches!(FileStore::open(path.clone(), [10;32], scope(), true), Err(FileError::Unavailable)));
        assert!(has_state(path).unwrap()); assert_eq!(fs::read(store.root.join("store.enc")).unwrap(), witness);
    }
    #[test]
    fn checkpoint_survives_restart_and_only_a_fresh_admitted_generation_can_resume() {
        let root = root("resume"); let bytes = vec![19; MAX_DATA_BYTES + 3]; let initial = descriptor(&bytes);
        let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        assert!(store.root.starts_with(PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").unwrap()).canonicalize().unwrap()));
        store.stage(initial.clone(), 1, 1000).unwrap();
        store.append(&initial.transfer_id, 1, 0, &bytes[..MAX_DATA_BYTES], 1000).unwrap();
        assert_eq!(store.read(&initial.transfer_id, 1, 0, 10, 1000), Err(FileError::Invalid));
        drop(store);
        let store = FileStore::open(root, [9;32], scope(), false).unwrap();
        let mut resumed = initial.clone(); resumed.offset = MAX_DATA_BYTES as u64;
        let checkpoint = store.stage(resumed, 2, 1000).unwrap();
        assert_eq!(checkpoint.prefix_sha256, Sha256::digest(&bytes[..MAX_DATA_BYTES]).to_vec());
        assert!(matches!(store.append(&initial.transfer_id, 1, MAX_DATA_BYTES as u64, &bytes[MAX_DATA_BYTES..], 1000), Err(FileError::Stale)));
        store.append(&initial.transfer_id, 2, MAX_DATA_BYTES as u64, &bytes[MAX_DATA_BYTES..], 1000).unwrap();
        assert_eq!(store.finish(&initial.transfer_id, 2, 1000).unwrap().phase, FilePhase::Committed);
        assert_eq!(store.finish(&initial.transfer_id, 2, 1000).unwrap().phase, FilePhase::Committed);
        assert_eq!(store.read(&initial.transfer_id, 2, MAX_DATA_BYTES as u64 - 1, 4, 1000).unwrap(), [19]);
        assert_eq!(store.read(&initial.transfer_id, 2, MAX_DATA_BYTES as u64, 4, 1000).unwrap(), [19;3]);
    }
    #[test]
    fn ciphertext_and_receipts_hide_bodies_and_owner_metadata() {
        let store = FileStore::open(root("private"), [9;32], scope(), true).unwrap();
        let bytes = b"synthetic-private-attachment-content"; let file = descriptor(bytes);
        store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, bytes, 1000).unwrap();
        store.finish(&file.transfer_id, 1, 1000).unwrap();
        for name in ["state.enc", "body.enc"] {
            let stored = fs::read(store.operation_root(&file.transfer_id).unwrap().join(name)).unwrap();
            for needle in [bytes.as_slice(), b"synthetic-owner", file.transfer_id.as_bytes()] {
                assert!(!stored.windows(needle.len()).any(|part| part == needle));
            }
        }
    }
    #[test]
    fn corrupt_or_newer_state_is_never_reset_or_overwritten() {
        let root = root("state-corruption"); let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        let file = descriptor(b"synthetic"); store.stage(file.clone(), 1, 1000).unwrap();
        let state = store.operation_root(&file.transfer_id).unwrap().join("state.enc");
        let corrupt = b"synthetic-corrupt-state"; fs::write(&state, corrupt).unwrap(); drop(store);
        let store = FileStore::open(root, [9;32], scope(), false).unwrap();
        assert!(matches!(store.stage(file, 2, 1000), Err(FileError::Unavailable)));
        assert_eq!(fs::read(&state).unwrap(), corrupt);
    }
    #[test]
    fn a_valid_encrypted_newer_schema_is_preserved_for_authorized_recovery() {
        let store = FileStore::open(root("newer-schema"), [9;32], scope(), true).unwrap(); let file = descriptor(b"synthetic");
        store.stage(file.clone(), 1, 1000).unwrap();
        let mut state = store.load(&file.transfer_id).unwrap().unwrap(); state.schema = 2;
        store.persist(&state).unwrap(); let path = store.operation_root(&file.transfer_id).unwrap().join("state.enc"); let saved = fs::read(&path).unwrap();
        assert!(matches!(store.stage(file, 2, 1000), Err(FileError::Unavailable)));
        assert_eq!(fs::read(path).unwrap(), saved);
    }
    #[test]
    fn encrypted_block_corruption_fails_without_claiming_a_file_commit() {
        let root = root("body-corruption"); let file = descriptor(b"synthetic");
        let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap();
        let path = store.operation_root(&file.transfer_id).unwrap().join("body.enc");
        let mut body = fs::read(&path).unwrap(); *body.last_mut().unwrap() ^= 1; fs::write(&path, &body).unwrap();
        assert!(matches!(store.finish(&file.transfer_id, 1, 1000), Err(FileError::Unavailable)));
        drop(store); let store = FileStore::open(root, [9;32], scope(), false).unwrap();
        let mut resumed = file; resumed.offset = resumed.total;
        assert!(matches!(store.stage(resumed, 2, 1000), Err(FileError::Unavailable)));
        assert_eq!(fs::read(path).unwrap(), body);
    }
    #[test]
    fn corrupted_committed_ciphertext_cannot_advertise_a_receipt_after_reconnect() {
        use crate::file_receiver::FileReceiver;
        use autoyou_protocol::binary::{Control, Failure, StatusPhase};
        let store = Arc::new(FileStore::open(root("committed-corruption"), [9;32], scope(), true).unwrap());
        let file = descriptor(b"synthetic"); store.stage(file.clone(), 1, 1000).unwrap();
        store.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap(); store.finish(&file.transfer_id, 1, 1000).unwrap();
        let operation = store.operation_root(&file.transfer_id).unwrap();
        let state = fs::read(operation.join("state.enc")).unwrap();
        let mut body = fs::read(operation.join("body.enc")).unwrap(); *body.last_mut().unwrap() ^= 1;
        fs::write(operation.join("body.enc"), &body).unwrap();
        let mut receiver = FileReceiver::new(store.clone(), 2).unwrap();
        let status = receiver.control(Control::Query { descriptor: file.clone() }, 1000).unwrap();
        assert!(matches!(status, Control::Status { phase: StatusPhase::Unavailable, failure: Some(Failure::Storage), .. }));
        assert_eq!(fs::read(operation.join("state.enc")).unwrap(), state);
        assert_eq!(fs::read(operation.join("body.enc")).unwrap(), body);
        assert_eq!(store.checkpoint(&file.transfer_id, 1000).unwrap().unwrap().generation, 1);
    }
    #[test]
    fn reading_many_committed_files_evicts_indexes_without_interrupting_a_pending_writer() {
        let store = FileStore::open(root("read-eviction"), [9;32], scope(), true).unwrap();
        let mut committed = Vec::new();
        for number in 1..=MAX_ACTIVE + 2 {
            let mut file = descriptor(&[1,2,3]); file.transfer_id = format!("{number:032x}");
            store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, &[1,2,3], 1000).unwrap();
            store.finish(&file.transfer_id, 1, 1000).unwrap(); store.release(&file.transfer_id).unwrap(); committed.push(file);
        }
        let pending_bytes = vec![17;MAX_DATA_BYTES + 3]; let mut pending = descriptor(&pending_bytes); pending.transfer_id = "cd".repeat(16);
        store.stage(pending.clone(), 1, 1000).unwrap(); store.append(&pending.transfer_id, 1, 0, &pending_bytes[..MAX_DATA_BYTES], 1000).unwrap();
        for file in committed {
            assert_eq!(store.read(&file.transfer_id, 1, 0, 48, 1000).unwrap(), [1,2,3]);
        }
        assert_eq!(store.cache.lock().unwrap().len(), MAX_ACTIVE);
        store.append(&pending.transfer_id, 1, MAX_DATA_BYTES as u64, &pending_bytes[MAX_DATA_BYTES..], 1000).unwrap();
        store.finish(&pending.transfer_id, 1, 1000).unwrap();
        assert_eq!(store.read(&pending.transfer_id, 1, MAX_DATA_BYTES as u64, 48, 1000).unwrap(), [17;3]);
    }
    #[test]
    fn cancellation_and_bad_whole_digest_leave_tombstones_that_cannot_resurrect() {
        let store = FileStore::open(root("delete"), [9;32], scope(), true).unwrap();
        let file = descriptor(b"synthetic"); store.stage(file.clone(), 1, 1000).unwrap();
        store.delete(&file.transfer_id, 1, 1000).unwrap();
        assert!(matches!(store.stage(file.clone(), 2, 1000), Err(FileError::Deleted)));
        let mut file = file; file.transfer_id = "cd".repeat(16); file.sha256[0] ^= 1;
        store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap();
        assert!(matches!(store.finish(&file.transfer_id, 1, 1000), Err(FileError::Digest)));
        assert_eq!(store.checkpoint(&file.transfer_id, 1000).unwrap().unwrap().phase, FilePhase::Deleted);
        assert!(!store.operation_root(&file.transfer_id).unwrap().join("body.enc").exists());
    }
    #[test]
    fn priority_cancellation_before_file_open_is_durable_and_cannot_be_overtaken_after_restart() {
        let root = root("early-cancel"); let file = descriptor(b"synthetic");
        let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        assert_eq!(store.cancel(file.clone(), 1, 1000).unwrap().phase, FilePhase::Deleted); drop(store);
        let store = FileStore::open(root, [9;32], scope(), false).unwrap();
        assert!(matches!(store.stage(file, 2, 1000), Err(FileError::Deleted)));
    }
    #[test]
    fn expiry_purges_owned_files_and_old_descriptors_cannot_reopen_them() {
        let store = FileStore::open(root("expiry"), [9;32], scope(), true).unwrap(); let file = descriptor(b"synthetic");
        store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap();
        assert_eq!(store.sweep(10_000).unwrap(), 1);
        assert!(!store.operation_root(&file.transfer_id).unwrap().exists());
        assert!(matches!(store.stage(file, 2, 10_000), Err(FileError::Invalid)));
    }
    #[test]
    fn expiry_reaps_retired_scopes_and_preserves_foreign_unreadable_state() {
        let root = root("retired-expiry"); let file = descriptor(b"synthetic");
        let previous = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        previous.stage(file.clone(), 1, 1000).unwrap(); previous.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap();
        let mut next = scope(); next.authorization_epoch += 1;
        let current = FileStore::open(root, [9;32], next, true).unwrap();
        assert!(current.checkpoint(&file.transfer_id, 1000).unwrap().is_none());
        let journal = previous.operation_root(&file.transfer_id).unwrap().join("state.enc");
        let original = fs::read(&journal).unwrap(); fs::write(&journal, b"synthetic-corrupt-journal").unwrap();
        assert!(matches!(current.sweep(10_000), Err(FileError::Unavailable)));
        assert_eq!(fs::read(&journal).unwrap(), b"synthetic-corrupt-journal");
        fs::write(journal, original).unwrap();
        assert_eq!(current.sweep(10_000).unwrap(), 1);
        assert!(!previous.operation_root(&file.transfer_id).unwrap().exists());
        assert!(matches!(previous.stage(file, 2, 10_000), Err(FileError::Invalid)));
    }
    #[test]
    fn uncheckpointed_crash_tail_is_discarded_only_after_validating_the_durable_prefix() {
        let root = root("crash-tail"); let bytes = vec![31; MAX_DATA_BYTES + 2]; let file = descriptor(&bytes);
        let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        store.stage(file.clone(), 1, 1000).unwrap(); store.append(&file.transfer_id, 1, 0, &bytes[..MAX_DATA_BYTES], 1000).unwrap();
        let path = store.operation_root(&file.transfer_id).unwrap().join("body.enc");
        let saved = fs::read(&path).unwrap(); OpenOptions::new().append(true).open(&path).unwrap().write_all(b"synthetic-uncheckpointed-tail").unwrap();
        drop(store); let store = FileStore::open(root, [9;32], scope(), false).unwrap();
        let mut resumed = file; resumed.offset = MAX_DATA_BYTES as u64; store.stage(resumed, 2, 1000).unwrap();
        assert_eq!(fs::read(path).unwrap(), saved);
    }
    #[test]
    fn scopes_fence_owners_epochs_conversations_devices_instances_and_directions() {
        let root = root("scopes"); let file = descriptor(b"synthetic"); let owner = scope();
        let store = FileStore::open(root.clone(), [9;32], owner.clone(), true).unwrap(); store.stage(file.clone(), 1, 1000).unwrap();
        for field in 0..8 {
            let mut other = owner.clone();
            match field { 0 => other.owner_key += "-other", 1 => other.authorization_epoch += 1, 2 => other.conversation_key += "-other",
                3 => other.device_id += "-other", 4 => other.server_instance += "-other", 5 => other.canonical_user_id += "-other",
                6 => other.direction = Direction::Outgoing, _ => other.remote_endpoint = iroh::SecretKey::from_bytes(&[93;32]).public().to_string() }
            let other = FileStore::open(root.clone(), [9;32], other, false).unwrap();
            assert!(other.checkpoint(&file.transfer_id, 1000).unwrap().is_none());
        }
    }
    #[test]
    fn concurrent_writers_have_one_checkpoint_and_no_duplicate_bytes() {
        let store = Arc::new(FileStore::open(root("concurrent"), [9;32], scope(), true).unwrap()); let file = descriptor(b"synthetic");
        store.stage(file.clone(), 1, 1000).unwrap();
        let writers: Vec<_> = (0..2).map(|_| { let store = store.clone(); let id = file.transfer_id.clone();
            std::thread::spawn(move || store.append(&id, 1, 0, b"synthetic", 1000).is_ok()) }).collect();
        assert_eq!(writers.into_iter().map(|writer| writer.join().unwrap()).filter(|accepted| *accepted).count(), 1);
        assert_eq!(store.finish(&file.transfer_id, 1, 1000).unwrap().offset, 9);
    }
    #[test]
    fn missing_or_wrong_key_witness_never_creates_a_replacement_store() {
        let root = root("witness"); let store = FileStore::open(root.clone(), [9;32], scope(), true).unwrap();
        let witness = store.root.join("store.enc"); let original = fs::read(&witness).unwrap(); drop(store);
        assert!(matches!(FileStore::open(root.clone(), [10;32], scope(), true), Err(FileError::Unavailable)));
        assert_eq!(fs::read(&witness).unwrap(), original); fs::remove_file(&witness).unwrap();
        assert!(matches!(FileStore::open(root, [9;32], scope(), false), Err(FileError::Unavailable)));
        assert!(!witness.exists());
    }
    #[test]
    fn malformed_blocks_and_active_capacity_fail_before_creating_new_operations() {
        let store = FileStore::open(root("capacity"), [9;32], scope(), true).unwrap(); let bytes = vec![1; MAX_DATA_BYTES + 1];
        let file = descriptor(&bytes); store.stage(file.clone(), 1, 1000).unwrap();
        assert!(matches!(store.append(&file.transfer_id, 1, 0, &[1], 1000), Err(FileError::Invalid)));
        assert_eq!(store.checkpoint(&file.transfer_id, 1000).unwrap().unwrap().offset, 0);
        for number in 1..MAX_ACTIVE {
            let mut next = file.clone(); next.transfer_id = format!("{number:032x}"); store.stage(next, 1, 1000).unwrap();
        }
        let mut extra = file; extra.transfer_id = "ef".repeat(16);
        assert!(matches!(store.stage(extra.clone(), 1, 1000), Err(FileError::Capacity)));
        assert!(!store.operation_root(&extra.transfer_id).unwrap().exists());
    }
    #[test]
    fn storage_pressure_leaves_the_last_durable_checkpoint_unchanged() {
        let mut store = FileStore::open(root("disk-quota"), [9;32], scope(), true).unwrap(); store.maximum_stored_bytes = 39;
        let file = descriptor(b"synthetic"); store.stage(file.clone(), 1, 1000).unwrap();
        assert!(matches!(store.append(&file.transfer_id, 1, 0, b"synthetic", 1000), Err(FileError::Capacity)));
        assert_eq!(store.checkpoint(&file.transfer_id, 1000).unwrap().unwrap().offset, 0);
        assert!(!store.operation_root(&file.transfer_id).unwrap().join("body.enc").exists());
        store.maximum_stored_bytes = 41; store.append(&file.transfer_id, 1, 0, b"synthetic", 1000).unwrap();
        assert_eq!(store.finish(&file.transfer_id, 1, 1000).unwrap().phase, FilePhase::Committed);
    }
    #[test]
    fn empty_files_commit_without_allocating_a_body_and_generation_floor_never_rewinds() {
        let store = FileStore::open(root("empty"), [9;32], scope(), true).unwrap(); let file = descriptor(&[]);
        store.stage(file.clone(), 2, 1000).unwrap(); store.finish(&file.transfer_id, 2, 1000).unwrap();
        assert!(store.read(&file.transfer_id, 2, 0, 1, 1000).unwrap().is_empty());
        assert!(matches!(store.stage(file.clone(), 1, 1000), Err(FileError::Stale)));
        assert!(matches!(store.delete(&file.transfer_id, 1, 1000), Err(FileError::Stale)));
    }
}

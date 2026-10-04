// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Durable prompt admission, not a transcript or an external-effect ledger.
//! Only queued outgoing envelopes have bodies. Admission removes those bodies;
//! incoming journals contain fingerprints and status only. An interrupted
//! dispatch is uncertain and can never acquire another execution permission.
use crate::file_store::{FileStore, FileScope, FileError, Direction, Result, atomic_write,
    checked_directory, checked_file, parse_digest, read_bounded, remove_operation, hex};
use autoyou_protocol::{Envelope, delivery::{Stamp, Status, MAX_TTL_MS, MAX_BODY_BYTES, prompt_digest, stamp, identifier, attachment_expiry}};
use ring::rand::{SecureRandom, SystemRandom};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{fs, path::{Path, PathBuf}, sync::OnceLock};

const MAX_RECORDS: usize = 4096;
const MAX_PENDING: usize = 32;
const MAX_PENDING_BYTES: usize = 8 * 1024 * 1024;
const MAX_DISK_BYTES: u64 = 16 * 1024 * 1024;
const MAX_JOURNAL_BYTES: usize = MAX_BODY_BYTES * 2 + 8192;
const MAX_SCOPE_BYTES: usize = 8192;
const PURPOSE: &[u8] = b"delivery-state/1";

#[derive(Debug, Clone)]
pub struct Operation {
    pub operation_id: String, pub digest: Vec<u8>, pub revision: String,
    pub client_prompt_id: Option<String>,
    pub expires_at_ms: u64, pub generation: u64, pub status: Status,
    pub envelope: Option<Vec<u8>>, pub execute: bool,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Entry {
    schema: u8, operation_id: String, digest: Vec<u8>, revision: String,
    expires_at_ms: u64, queued_at_ms: u64, generation: u64, status: Status,
    runtime_id: String, body: Option<String>,
    #[serde(default)]
    client_prompt_id: Option<String>,
    order: u64,
}
impl Entry {
    fn operation(&self, execute: bool) -> Operation {
        Operation { operation_id:self.operation_id.clone(),digest:self.digest.clone(),revision:self.revision.clone(),
            client_prompt_id:self.client_prompt_id.clone(),
            expires_at_ms:self.expires_at_ms,generation:self.generation,status:self.status,
            envelope:self.body.as_ref().map(|s| s.as_bytes().to_vec()),execute }
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Revision { schema: u8, revision: String, accepting: bool }
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ScopeFloor { schema: u8, generation: u64, last_order: u64, authority: FileScope }

pub struct DeliveryStore { protected: FileStore, direction: Direction, history_name: String, runtime_id: String, authority: FileScope }
fn random_id() -> Result<String> { let mut value=[0;16]; SystemRandom::new().fill(&mut value).map_err(|_| FileError::Unavailable)?; Ok(hex(&value)) }
fn client_prompt_id(envelope:&Envelope)->Option<String> {
    envelope.payload.get("metadata").and_then(|v|v.get("client_prompt_id"))
        .and_then(serde_json::Value::as_str).filter(|value|identifier(value)).map(str::to_owned)
}
fn boot_id() -> Result<String> {
    static BOOT: OnceLock<std::result::Result<String, ()>> = OnceLock::new();
    BOOT.get_or_init(|| random_id().map_err(|_| ())).clone().map_err(|_| FileError::Unavailable)
}
fn operation_key(id: &str) -> Result<String> {
    if !identifier(id) { return Err(FileError::Invalid); }
    Ok(hex(&Sha256::digest([b"AutoYou-operation-id/1\0".as_slice(),id.as_bytes()].concat()))[..32].into())
}
impl DeliveryStore {
    pub fn open(root: PathBuf, key: [u8;32], scope: FileScope, create: bool) -> Result<Self> {
        scope.validate()?;
        let family = serde_json::to_vec(&(&scope.server_instance,&scope.owner_key,&scope.canonical_user_id,&scope.conversation_key))
            .map_err(|_| FileError::Invalid)?;
        let direction = scope.direction;
        let authority=scope.clone();
        let protected = FileStore::open(root,key,scope,create)?;
        let history_name = protected.operation_name(&hex(&Sha256::digest(family))[..32]);
        let store = Self { protected,direction,history_name,runtime_id:boot_id()?,authority };
        if direction == Direction::Incoming {
            store.protected.journal_transaction(|_| { store.history(true)?; Ok(()) })?;
        }
        Ok(store)
    }
    fn history_path(&self) -> PathBuf { self.protected.root.join(format!("history-{}.enc",self.history_name)) }
    fn history_aad(&self) -> Vec<u8> { [b"AutoYou-delivery-history/1\0".as_slice(),self.history_name.as_bytes()].concat() }
    fn history_state(&self, create: bool) -> Result<Revision> {
        let path=self.history_path();
        if !path.exists() {
            if !create { return Err(FileError::Unavailable); }
            let revision=random_id()?; self.write_history(&revision,true)?; return Ok(Revision{schema:1,revision,accepting:true});
        }
        let plain=self.protected.unseal(&read_bounded(&path,4096)?,&self.history_aad())?;
        let value:Revision=serde_json::from_slice(&plain).map_err(|_| FileError::Unavailable)?;
        if value.schema!=1 || !crate::file_store::valid_revision(&value.revision) { return Err(FileError::Unavailable); }
        Ok(value)
    }
    fn history(&self, create: bool) -> Result<String> { Ok(self.history_state(create)?.revision) }
    fn write_history(&self, revision: &str, accepting: bool) -> Result<()> {
        if !crate::file_store::valid_revision(revision) { return Err(FileError::Invalid); }
        let value=serde_json::to_vec(&Revision{schema:1,revision:revision.into(),accepting}).map_err(|_| FileError::Invalid)?;
        self.quota(value.len() as u64+28,&self.history_path())?;
        atomic_write(&self.history_path(),&self.protected.seal(value,&self.history_aad())?)
    }
    fn floor(&self, generation: u64) -> Result<()> {
        if generation==0 { return Err(FileError::Invalid); }
        let path=self.protected.scope.join("scope.enc"); let aad=self.protected.aad_named("scope",b"delivery-scope/1");
        let mut saved=ScopeFloor{schema:1,generation:0,last_order:0,authority:self.authority.clone()};
        if path.exists() {
            let plain=self.protected.unseal(&read_bounded(&path,MAX_SCOPE_BYTES)?,&aad)?;
            let value:ScopeFloor=serde_json::from_slice(&plain).map_err(|_| FileError::Unavailable)?;
            if value.schema!=1 || value.generation==0 || value.authority!=self.authority { return Err(FileError::Unavailable); }
            saved=value;
        }
        if generation<saved.generation { return Err(FileError::Stale); }
        if generation>saved.generation {
            saved.generation=generation;
            let encoded=serde_json::to_vec(&saved).map_err(|_|FileError::Invalid)?;
            self.quota(encoded.len() as u64+28,&path)?;
            atomic_write(&path,&self.protected.seal(encoded,&aad)?)?;
        }
        Ok(())
    }
    fn next_order(&self)->Result<u64> {
        let path=self.protected.scope.join("scope.enc"); let aad=self.protected.aad_named("scope",b"delivery-scope/1");
        let mut floor:ScopeFloor=serde_json::from_slice(&self.protected.unseal(&read_bounded(&path,MAX_SCOPE_BYTES)?,&aad)?)
            .map_err(|_|FileError::Unavailable)?;
        if floor.schema!=1 || floor.generation==0 || floor.authority!=self.authority { return Err(FileError::Unavailable); }
        floor.last_order=floor.last_order.checked_add(1).ok_or(FileError::Capacity)?;
        let encoded=serde_json::to_vec(&floor).map_err(|_|FileError::Invalid)?;
        self.quota(encoded.len() as u64+28,&path)?;
        atomic_write(&path,&self.protected.seal(encoded,&aad)?)?;
        Ok(floor.last_order)
    }
    fn scope_floor(&self,path:&Path,digest:&[u8])->Result<Option<ScopeFloor>> {
        let floor_path=path.join("scope.enc");
        if !floor_path.exists() {
            if fs::read_dir(path).map_err(|_|FileError::Unavailable)?.next().is_none() { return Ok(None); }
            return Err(FileError::Unavailable);
        }
        let aad=[b"AutoYou-file/1\0".as_slice(),digest,b"scope",b"delivery-scope/1"].concat();
        let floor:ScopeFloor=serde_json::from_slice(&self.protected.unseal(&read_bounded(&floor_path,MAX_SCOPE_BYTES)?,&aad)?)
            .map_err(|_|FileError::Unavailable)?;
        if floor.schema!=1 || floor.generation==0 || self.protected.scope_digest_for(&floor.authority)?!=digest { return Err(FileError::Unavailable); }
        Ok(Some(floor))
    }
    pub fn revision(&self, generation: u64) -> Result<String> {
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let state=self.history_state(self.direction==Direction::Incoming)?;
            if !state.accepting { return Err(FileError::Deleted); } Ok(state.revision)
        })
    }
    pub fn align_revision(&self, revision: &str, generation: u64, now_ms: u64) -> Result<Vec<Operation>> {
        if self.direction!=Direction::Outgoing { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; self.write_history(revision,true)?;
            let mut cancelled=Vec::new();
            for mut entry in self.entries()? {
                if entry.revision!=revision || entry.expires_at_ms<=now_ms {
                    let notify=entry.status==Status::Queued;
                    entry.status=if entry.expires_at_ms<=now_ms {Status::Expired} else {Status::Deleted};
                    entry.body=None; entry.generation=generation; self.persist(&entry)?;
                    if notify { cancelled.push(entry.operation(false)); }
                }
            }
            Ok(cancelled)
        })
    }
    pub fn reset_revision(&self, generation: u64) -> Result<String> {
        if self.direction!=Direction::Incoming { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| { self.floor(generation)?; let revision=random_id()?; self.write_history(&revision,true)?; Ok(revision) })
    }
    /// Persist a fence before authoritative history deletion. A crash keeps
    /// admission paused until that deletion is explicitly retried/reconciled.
    pub fn pause_revision(&self, generation: u64) -> Result<String> {
        if self.direction!=Direction::Incoming { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| { self.floor(generation)?; let revision=random_id()?; self.write_history(&revision,false)?; Ok(revision) })
    }
    pub fn resume_revision(&self, revision: &str, generation: u64) -> Result<()> {
        if self.direction!=Direction::Incoming { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let state=self.history_state(true)?;
            if state.revision!=revision { return Err(FileError::Stale); }
            self.write_history(revision,true)
        })
    }
    fn load_path(&self,path:&Path)->Result<Entry> {
        let entry=self.load_path_in_scope(path,&self.protected.scope_digest)?;
        if (self.direction==Direction::Incoming && entry.status==Status::Queued) ||
            (self.direction==Direction::Outgoing && entry.status==Status::Pending) { return Err(FileError::Unavailable); }
        Ok(entry)
    }
    fn load_path_in_scope(&self,path:&Path,scope_digest:&[u8])->Result<Entry> {
        checked_directory(path,false)?;
        let name=path.file_name().and_then(|v|v.to_str()).ok_or(FileError::Unavailable)?;
        let aad=[b"AutoYou-file/1\0".as_slice(),scope_digest,name.as_bytes(),PURPOSE].concat();
        let plain=self.protected.unseal(&read_bounded(&path.join("state.enc"),MAX_JOURNAL_BYTES)?,&aad)?;
        let mut entry:Entry=serde_json::from_slice(&plain).map_err(|_|FileError::Unavailable)?;
        if entry.schema!=1 || !identifier(&entry.operation_id) || entry.digest.len()!=32 ||
            !crate::file_store::valid_revision(&entry.revision) || !crate::file_store::valid_revision(&entry.runtime_id) ||
            entry.generation==0 || entry.order==0 || entry.expires_at_ms<=entry.queued_at_ms || entry.expires_at_ms-entry.queued_at_ms>MAX_TTL_MS ||
            self.protected.operation_name(&operation_key(&entry.operation_id)?)!=name ||
            (entry.status==Status::Queued)!=(entry.body.is_some()) || matches!(entry.status,Status::Missing) ||
            entry.client_prompt_id.as_ref().is_some_and(|value|!identifier(value)) {
            return Err(FileError::Unavailable);
        }
        if let Some(body)=&entry.body {
            if body.len()>MAX_BODY_BYTES { return Err(FileError::Unavailable); }
            let envelope=Envelope::from_slice(body.as_bytes()).map_err(|_|FileError::Unavailable)?;
            let tag=stamp(&envelope).map_err(|_|FileError::Unavailable)?;
            let alias=client_prompt_id(&envelope);
            if entry.client_prompt_id.is_some() && entry.client_prompt_id!=alias { return Err(FileError::Unavailable); }
            entry.client_prompt_id=alias;
            if envelope.header.message_id!=entry.operation_id || prompt_digest(&envelope).map_err(|_|FileError::Unavailable)?!=entry.digest ||
                tag.revision!=entry.revision || tag.expires_at_ms!=entry.expires_at_ms { return Err(FileError::Unavailable); }
        }
        Ok(entry)
    }
    fn load(&self,id:&str)->Result<Option<Entry>> {
        let path=self.protected.operation_root(&operation_key(id)?)?;
        if !path.exists() { return Ok(None); } Ok(Some(self.load_path(&path)?))
    }
    fn entries(&self)->Result<Vec<Entry>> {
        let mut values=Vec::new();
        for entry in fs::read_dir(&self.protected.scope).map_err(|_|FileError::Unavailable)? {
            let entry=entry.map_err(|_|FileError::Unavailable)?;
            if entry.file_name()=="scope.enc" { continue; }
            if values.len()>=MAX_RECORDS { return Err(FileError::Capacity); }
            values.push(self.load_path(&entry.path())?);
        }
        Ok(values)
    }
    fn persist(&self,entry:&Entry)->Result<()> {
        let path=self.protected.operation_root(&operation_key(&entry.operation_id)?)?;
        self.persist_in_scope(entry,&path,&self.protected.scope_digest)
    }
    fn persist_in_scope(&self,entry:&Entry,path:&Path,scope_digest:&[u8])->Result<()> {
        let plain=serde_json::to_vec(entry).map_err(|_|FileError::Invalid)?;
        if plain.len()>MAX_JOURNAL_BYTES-28 { return Err(FileError::Capacity); }
        self.quota(plain.len() as u64+28,&path)?;
        checked_directory(&path,true)?;
        let aad=[b"AutoYou-file/1\0".as_slice(),scope_digest,path.file_name().unwrap().to_str().unwrap().as_bytes(),PURPOSE].concat();
        atomic_write(&path.join("state.enc"),&self.protected.seal(plain,&aad)?)
    }
    fn quota(&self,additional:u64,replacing:&Path)->Result<()> {
        let mut count=0; let mut bytes=0; let mut scopes=0;
        let replacing_history=replacing.parent()==Some(self.protected.root.as_path());
        let replacing_floor=replacing.file_name()==Some(std::ffi::OsStr::new("scope.enc"));
        let mut histories=usize::from(replacing_history && !replacing.exists());
        for scope in fs::read_dir(&self.protected.root).map_err(|_|FileError::Unavailable)? {
            let scope=scope.map_err(|_|FileError::Unavailable)?;
            if !scope.file_type().map_err(|_|FileError::Unavailable)?.is_dir() {
                let name=scope.file_name().to_str().ok_or(FileError::Unavailable)?.to_owned();
                if name.starts_with("history-") && name.ends_with(".enc") {
                    parse_digest(&name[8..name.len()-4])?; checked_file(&scope.path(),false)?;
                    histories+=1; if histories>MAX_RECORDS { return Err(FileError::Capacity); }
                    if scope.path()!=replacing { bytes+=scope.metadata().map_err(|_|FileError::Unavailable)?.len(); }
                } else if name!="store.enc" && name!="store.lock" { return Err(FileError::Unavailable); }
                else { checked_file(&scope.path(),false)?; }
                continue;
            }
            scopes+=1; if scopes>MAX_RECORDS { return Err(FileError::Capacity); }
            checked_directory(&scope.path(),false)?;
            for entry in fs::read_dir(scope.path()).map_err(|_|FileError::Unavailable)? {
                let entry=entry.map_err(|_|FileError::Unavailable)?;
                if entry.file_name()=="scope.enc" {
                    checked_file(&entry.path(),false)?;
                    if entry.path()!=replacing { bytes+=entry.metadata().map_err(|_|FileError::Unavailable)?.len(); } continue;
                }
                checked_directory(&entry.path(),false)?;
                if entry.path()==replacing { continue; }
                count+=1; if count+usize::from(!replacing_history && !replacing_floor)>MAX_RECORDS { return Err(FileError::Capacity); }
                checked_file(&entry.path().join("state.enc"),false)?;
                bytes+=entry.path().join("state.enc").symlink_metadata().map_err(|_|FileError::Unavailable)?.len();
                if bytes>MAX_DISK_BYTES { return Err(FileError::Capacity); }
            }
        }
        if bytes+additional>MAX_DISK_BYTES { return Err(FileError::Capacity); } Ok(())
    }
    pub fn queue(&self,mut envelope:Envelope,generation:u64,now_ms:u64,expires_at_ms:u64)->Result<Operation> {
        if self.direction!=Direction::Outgoing || expires_at_ms<=now_ms || expires_at_ms-now_ms>MAX_TTL_MS { return Err(FileError::Invalid); }
        let digest=prompt_digest(&envelope).map_err(|_|FileError::Invalid)?;
        let expires_at_ms=attachment_expiry(&envelope,expires_at_ms);
        if expires_at_ms<=now_ms { return Err(FileError::Deleted); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let revision=self.history(false)?;
            if let Some(entry)=self.load(&envelope.header.message_id)? {
                if entry.digest!=digest { return Err(FileError::Invalid); }
                return Ok(self.current(entry,generation,now_ms,&revision)?.operation(false));
            }
            let pending:Vec<_>=self.entries()?.into_iter().filter(|v|v.status==Status::Queued && v.expires_at_ms>now_ms).collect();
            let retained:usize=pending.iter().map(|v|v.body.as_ref().map_or(0,String::len)).sum();
            envelope.extensions.insert("delivery".into(),serde_json::to_value(Stamp{version:1,revision:revision.clone(),expires_at_ms}).map_err(|_|FileError::Invalid)?);
            let wire=envelope.to_vec().map_err(|_|FileError::Invalid)?;
            if pending.len()>=MAX_PENDING || retained+wire.len()>MAX_PENDING_BYTES { return Err(FileError::Capacity); }
            let entry=Entry{schema:1,operation_id:envelope.header.message_id.clone(),digest,revision,expires_at_ms,queued_at_ms:now_ms,
                generation,status:Status::Queued,runtime_id:self.runtime_id.clone(),body:Some(String::from_utf8(wire).map_err(|_|FileError::Invalid)?),order:self.next_order()?,client_prompt_id:client_prompt_id(&envelope)};
            self.persist(&entry)?; Ok(entry.operation(false))
        })
    }
    fn current(&self,mut entry:Entry,generation:u64,now_ms:u64,revision:&str)->Result<Entry> {
        if generation<entry.generation { return Err(FileError::Stale); }
        let previous_generation=entry.generation; entry.generation=generation;
        if entry.expires_at_ms<=now_ms { entry.status=Status::Expired; entry.body=None; }
        else if entry.revision!=revision { entry.status=Status::Deleted; entry.body=None; }
        else if entry.status==Status::Pending && (entry.runtime_id!=self.runtime_id || generation>previous_generation) { entry.status=Status::Uncertain; }
        self.persist(&entry)?; Ok(entry)
    }
    pub fn begin(&self,envelope:&Envelope,generation:u64,now_ms:u64)->Result<Operation> {
        if self.direction!=Direction::Incoming { return Err(FileError::Invalid); }
        let digest=prompt_digest(envelope).map_err(|_|FileError::Invalid)?; let tag=stamp(envelope).map_err(|_|FileError::Invalid)?;
        if tag.expires_at_ms.saturating_sub(now_ms)>MAX_TTL_MS || attachment_expiry(envelope,tag.expires_at_ms)!=tag.expires_at_ms { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let history=self.history_state(true)?; let revision=history.revision;
            if let Some(entry)=self.load(&envelope.header.message_id)? {
                if entry.digest!=digest || entry.revision!=tag.revision || entry.expires_at_ms!=tag.expires_at_ms { return Err(FileError::Invalid); }
                return Ok(self.current(entry,generation,now_ms,&revision)?.operation(false));
            }
            let status=if tag.expires_at_ms<=now_ms {Status::Expired} else if !history.accepting || tag.revision!=revision {Status::Deleted} else {Status::Pending};
            let entry=Entry{schema:1,operation_id:envelope.header.message_id.clone(),digest,revision:tag.revision,expires_at_ms:tag.expires_at_ms,
                queued_at_ms:now_ms.min(tag.expires_at_ms.saturating_sub(1)),generation,status,runtime_id:self.runtime_id.clone(),body:None,order:self.next_order()?,client_prompt_id:client_prompt_id(envelope)};
            self.persist(&entry)?; Ok(entry.operation(status==Status::Pending))
        })
    }
    pub fn finish(&self,id:&str,generation:u64,now_ms:u64,accepted:bool)->Result<Operation> {
        if self.direction!=Direction::Incoming { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let revision=self.history(true)?;
            let mut entry=self.current(self.load(id)?.ok_or(FileError::Invalid)?,generation,now_ms,&revision)?;
            if entry.status==Status::Pending { entry.status=if accepted {Status::Accepted} else {Status::Uncertain}; self.persist(&entry)?; }
            Ok(entry.operation(false))
        })
    }
    pub fn query(&self,id:&str,digest:&[u8],revision:&str,expires_at_ms:u64,generation:u64,now_ms:u64)->Result<Operation> {
        if self.direction!=Direction::Incoming || digest.len()!=32 || !identifier(id) || !crate::file_store::valid_revision(revision) ||
            expires_at_ms==0 || expires_at_ms.saturating_sub(now_ms)>MAX_TTL_MS { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let history=self.history_state(true)?; let current=history.revision;
            if let Some(entry)=self.load(id)? {
                if entry.digest!=digest || entry.revision!=revision || entry.expires_at_ms!=expires_at_ms { return Err(FileError::Invalid); }
                return Ok(self.current(entry,generation,now_ms,&current)?.operation(false));
            }
            Ok(Operation{operation_id:id.into(),digest:digest.into(),revision:revision.into(),expires_at_ms,generation,
                client_prompt_id:None,
                status:if expires_at_ms<=now_ms {Status::Expired} else if !history.accepting || revision!=current {Status::Deleted} else {Status::Missing},envelope:None,execute:false})
        })
    }
    pub fn receipt(&self,id:&str,digest:&[u8],revision:&str,expires_at_ms:u64,status:Status,generation:u64,now_ms:u64)->Result<Operation> {
        if self.direction!=Direction::Outgoing || !matches!(status,Status::Accepted|Status::Uncertain|Status::Deleted|Status::Expired|Status::Pending|Status::Missing) { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let current=self.history(false)?;
            let mut entry=self.current(self.load(id)?.ok_or(FileError::Invalid)?,generation,now_ms,&current)?;
            if entry.digest!=digest || entry.revision!=revision || entry.expires_at_ms!=expires_at_ms { return Err(FileError::Invalid); }
            if entry.status==Status::Queued && matches!(status,Status::Accepted|Status::Uncertain|Status::Deleted|Status::Expired) {
                entry.status=status; entry.body=None; self.persist(&entry)?;
            }
            Ok(entry.operation(false))
        })
    }
    pub fn pending(&self,generation:u64,now_ms:u64)->Result<Vec<Operation>> {
        if self.direction!=Direction::Outgoing { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?; let revision=self.history(false)?; let mut pending=Vec::new(); let mut bytes=0;
            for entry in self.entries()? {
                if entry.status!=Status::Queued { continue; }
                let entry=self.current(entry,generation,now_ms,&revision)?;
                if entry.status==Status::Queued {
                    bytes+=entry.body.as_ref().map_or(0,String::len);
                    if pending.len()>=MAX_PENDING || bytes>MAX_PENDING_BYTES { return Err(FileError::Capacity); }
                    pending.push(entry);
                }
            }
            pending.sort_by_key(|v|v.order);
            Ok(pending.into_iter().map(|v|v.operation(false)).collect())
        })
    }
    pub fn observations(&self,generation:u64,now_ms:u64)->Result<Vec<Operation>> {
        if self.direction!=Direction::Outgoing { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?;let revision=self.history(false)?;
            let mut entries=self.entries()?;entries.sort_by_key(|value|value.order);
            let mut result=Vec::new();
            for entry in entries.into_iter().rev().take(MAX_PENDING) {
                let mut row=self.current(entry,generation,now_ms,&revision)?.operation(false);
                row.envelope=None;result.push(row);
            }
            result.reverse();Ok(result)
        })
    }
    pub fn delete_pending(&self,generation:u64)->Result<()> {
        if self.direction!=Direction::Outgoing { return Err(FileError::Invalid); }
        self.protected.journal_transaction(|_| {
            self.floor(generation)?;
            for mut entry in self.entries()? { if entry.status==Status::Queued { entry.status=Status::Deleted;entry.body=None;entry.generation=generation;self.persist(&entry)?; } }
            Ok(())
        })
    }
    /// Device-owner maintenance before deleting local transcript records. It
    /// works without a live grant and includes retired devices/epochs in this
    /// installation-owned outgoing journal root. Bodies are removed atomically;
    /// receipts/floors remain to prevent a stale callback requeueing the same ID.
    pub fn delete_pending_history(&self,conversation_key:Option<&str>)->Result<Vec<Operation>> {
        self.delete_local_pending(conversation_key,None)
    }
    /// Native transcript IDs may use client_prompt_id while the wire envelope
    /// has a separate operation ID. Only still-queued bodies are inspected.
    pub fn delete_pending_prompts(&self,prompt_ids:&[String])->Result<Vec<Operation>> {
        if prompt_ids.len()>MAX_RECORDS || prompt_ids.iter().any(|id|!identifier(id)) { return Err(FileError::Invalid); }
        self.delete_local_pending(None,Some(prompt_ids))
    }
    fn delete_local_pending(&self,conversation_key:Option<&str>,prompt_ids:Option<&[String]>)->Result<Vec<Operation>> {
        if self.direction!=Direction::Outgoing || conversation_key.is_some_and(|value|value.is_empty() || value.len()>512 || value.chars().any(char::is_control)) {
            return Err(FileError::Invalid);
        }
        self.protected.journal_transaction(|_| {
            let mut cancelled=Vec::new(); let mut scopes=0; let mut visited=0;
            for scope in fs::read_dir(&self.protected.root).map_err(|_|FileError::Unavailable)? {
                let scope=scope.map_err(|_|FileError::Unavailable)?;
                if !scope.file_type().map_err(|_|FileError::Unavailable)?.is_dir() { continue; }
                checked_directory(&scope.path(),false)?; scopes+=1;
                if scopes>MAX_RECORDS { return Err(FileError::Capacity); }
                let digest=parse_digest(scope.file_name().to_str().ok_or(FileError::Unavailable)?)?;
                let Some(floor)=self.scope_floor(&scope.path(),&digest)? else { continue; };
                if floor.authority.direction!=Direction::Outgoing || conversation_key.is_some_and(|key|key!=floor.authority.conversation_key) { continue; }
                for row in fs::read_dir(scope.path()).map_err(|_|FileError::Unavailable)? {
                    let row=row.map_err(|_|FileError::Unavailable)?;
                    if row.file_name()=="scope.enc" { continue; }
                    visited+=1; if visited>MAX_RECORDS { return Err(FileError::Capacity); }
                    let mut entry=self.load_path_in_scope(&row.path(),&digest)?;
                    if entry.status==Status::Pending || entry.generation>floor.generation || entry.order>floor.last_order { return Err(FileError::Unavailable); }
                    if entry.status==Status::Queued {
                        if let Some(ids)=prompt_ids {
                            let body:serde_json::Value=serde_json::from_str(entry.body.as_deref().ok_or(FileError::Unavailable)?).map_err(|_|FileError::Unavailable)?;
                            let alias=body.get("payload").and_then(|v|v.get("metadata")).and_then(|v|v.get("client_prompt_id")).and_then(serde_json::Value::as_str);
                            if !ids.iter().any(|id|id==&entry.operation_id || alias==Some(id.as_str())) { continue; }
                        }
                        entry.status=Status::Deleted; entry.body=None; entry.generation=floor.generation;
                        self.persist_in_scope(&entry,&row.path(),&digest)?; cancelled.push(entry.operation(false));
                    }
                }
            }
            Ok(cancelled)
        })
    }
    pub fn sweep(&self,now_ms:u64)->Result<u32> {
        self.protected.journal_transaction(|_| {
            let mut removed=0; let mut visited=0; let mut scopes=0;
            for scope in fs::read_dir(&self.protected.root).map_err(|_|FileError::Unavailable)? {
                let scope=scope.map_err(|_|FileError::Unavailable)?;
                if !scope.file_type().map_err(|_|FileError::Unavailable)?.is_dir() { continue; }
                checked_directory(&scope.path(),false)?; scopes+=1;
                if scopes>MAX_RECORDS { return Err(FileError::Capacity); }
                let digest=parse_digest(scope.file_name().to_str().ok_or(FileError::Unavailable)?)?;
                let Some(floor)=self.scope_floor(&scope.path(),&digest)? else { continue; };
                for row in fs::read_dir(scope.path()).map_err(|_|FileError::Unavailable)? {
                    let row=row.map_err(|_|FileError::Unavailable)?;
                    if row.file_name()=="scope.enc" { continue; }
                    visited+=1; if visited>MAX_RECORDS { return Err(FileError::Capacity); }
                    let entry=self.load_path_in_scope(&row.path(),&digest)?;
                    if entry.generation>floor.generation || entry.order>floor.last_order ||
                        (floor.authority.direction==Direction::Outgoing && entry.status==Status::Pending) ||
                        (floor.authority.direction==Direction::Incoming && entry.status==Status::Queued) { return Err(FileError::Unavailable); }
                    if entry.expires_at_ms<=now_ms { remove_operation(&scope.path(),&row.path())?; removed+=1; }
                }
            }
            // Scope floors and history fences are bounded, permanent metadata.
            // Removing a deletion fence would make an old unseen prompt replayable.
            Ok(removed)
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root(label:&str)->PathBuf { PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").expect("isolated root required"))
        .join(format!("delivery-{label}-{}",random_id().unwrap())) }
    fn scope(direction:Direction)->FileScope {
        FileScope{local_endpoint:iroh::SecretKey::from_bytes(&[31;32]).public().to_string(),remote_endpoint:iroh::SecretKey::from_bytes(&[32;32]).public().to_string(),
            server_instance:"synthetic-server".into(),device_id:"synthetic-device".into(),owner_key:"synthetic-owner".into(),canonical_user_id:"synthetic-user".into(),
            conversation_key:"synthetic-conversation".into(),authorization_epoch:1,direction}
    }
    fn prompt(id:&str)->Envelope { Envelope::from_slice(format!(r#"{{"header":{{"message_id":"{id}","message_type":"chat","timestamp":1}},"payload":{{"message":"synthetic private prompt"}}}}"#).as_bytes()).unwrap() }
    fn setup(label:&str)->(DeliveryStore,DeliveryStore,String,PathBuf) {
        let requested=root(&format!("{label}-in"));
        let incoming=DeliveryStore::open(requested.clone(),[9;32],scope(Direction::Incoming),true).unwrap();
        let outgoing=DeliveryStore::open(root(&format!("{label}-out")),[8;32],scope(Direction::Outgoing),true).unwrap();
        let revision=incoming.revision(1).unwrap();outgoing.align_revision(&revision,1,1000).unwrap();(incoming,outgoing,revision,requested)
    }
    #[test]
    fn delivery_outbox_survives_restart_and_receipt_removes_the_only_body() {
        let (incoming,outgoing,revision,_)=setup("receipt");
        let queued=outgoing.queue(prompt("synthetic-operation"),1,1000,10000).unwrap();
        let wire=queued.envelope.clone().unwrap();
        let path=outgoing.protected.operation_root(&operation_key(&queued.operation_id).unwrap()).unwrap().join("state.enc");
        assert!(!fs::read(&path).unwrap().windows(b"synthetic private prompt".len()).any(|v|v==b"synthetic private prompt"));
        let admitted=incoming.begin(&Envelope::from_slice(&wire).unwrap(),1,1000).unwrap();assert!(admitted.execute);
        assert!(!incoming.begin(&Envelope::from_slice(&wire).unwrap(),1,1000).unwrap().execute);
        let committed=incoming.finish(&admitted.operation_id,1,1000,true).unwrap();assert_eq!(committed.status,Status::Accepted);
        outgoing.receipt(&queued.operation_id,&queued.digest,&revision,10000,Status::Accepted,1,1000).unwrap();
        assert!(outgoing.load(&queued.operation_id).unwrap().unwrap().body.is_none());
        assert!(outgoing.pending(2,1000).unwrap().is_empty());
        assert!(matches!(outgoing.pending(1,1000),Err(FileError::Stale)));
    }
    #[test]
    fn delivery_restart_between_claim_and_handoff_is_uncertain_and_cannot_execute_twice() {
        let (mut incoming,outgoing,revision,_)=setup("uncertain");
        let queued=outgoing.queue(prompt("synthetic-operation"),1,1000,10000).unwrap();let envelope=Envelope::from_slice(&queued.envelope.unwrap()).unwrap();
        assert!(incoming.begin(&envelope,1,1000).unwrap().execute);
        incoming.runtime_id="ef".repeat(16);
        let repeated=incoming.begin(&envelope,2,1000).unwrap();assert!(!repeated.execute);assert_eq!(repeated.status,Status::Uncertain);
        assert_eq!(incoming.query(&repeated.operation_id,&repeated.digest,&revision,10000,2,1000).unwrap().status,Status::Uncertain);
        assert_eq!(incoming.finish(&repeated.operation_id,2,1000,true).unwrap().status,Status::Uncertain);
    }
    #[test]
    fn delivery_ui_alias_survives_receipt_body_removal_and_rejects_conflicting_journal_metadata() {
        let (incoming,outgoing,revision,_)=setup("ui-receipt");
        let mut envelope=prompt("synthetic-wire-id");
        envelope.payload.insert("metadata".into(),serde_json::json!({"client_prompt_id":"synthetic-ui-id"}));
        let queued=outgoing.queue(envelope,1,1000,10000).unwrap();
        assert_eq!(queued.client_prompt_id.as_deref(),Some("synthetic-ui-id"));
        let mut entry=outgoing.load(&queued.operation_id).unwrap().unwrap();
        entry.client_prompt_id=Some("synthetic-forged-alias".into());outgoing.persist(&entry).unwrap();
        assert!(matches!(outgoing.load(&queued.operation_id),Err(FileError::Unavailable)));
        entry.client_prompt_id=None;outgoing.persist(&entry).unwrap();
        assert_eq!(outgoing.load(&queued.operation_id).unwrap().unwrap().client_prompt_id.as_deref(),Some("synthetic-ui-id"));
        let admitted=incoming.begin(&Envelope::from_slice(&queued.envelope.unwrap()).unwrap(),1,1000).unwrap();
        assert_eq!(admitted.client_prompt_id.as_deref(),Some("synthetic-ui-id"));
        incoming.finish(&admitted.operation_id,1,1000,true).unwrap();
        let terminal=outgoing.receipt(&admitted.operation_id,&admitted.digest,&revision,10000,Status::Accepted,1,1000).unwrap();
        assert_eq!(terminal.client_prompt_id.as_deref(),Some("synthetic-ui-id"));assert!(terminal.envelope.is_none());
        let restored=outgoing.load(&terminal.operation_id).unwrap().unwrap().operation(false);
        assert_eq!(restored.client_prompt_id,terminal.client_prompt_id);assert!(restored.envelope.is_none());
    }
    #[test]
    fn delivery_restart_observations_are_bounded_body_free_ordered_and_fenced() {
        let (incoming,outgoing,revision,_)=setup("observations");
        for n in 0..40 {
            let mut envelope=prompt(&format!("synthetic-wire-{n}"));
            envelope.payload.insert("metadata".into(),serde_json::json!({"client_prompt_id":format!("synthetic-ui-{n}")}));
            let row=outgoing.queue(envelope,1,1000,10000).unwrap();
            if n<39 { outgoing.receipt(&row.operation_id,&row.digest,&revision,10000,Status::Accepted,1,1000).unwrap(); }
        }
        let observations=outgoing.observations(2,1000).unwrap();assert_eq!(observations.len(),32);
        assert!(observations.iter().all(|row|row.envelope.is_none() && !row.execute && row.generation==2));
        assert_eq!(observations.first().unwrap().client_prompt_id.as_deref(),Some("synthetic-ui-8"));
        assert_eq!(observations.last().unwrap().client_prompt_id.as_deref(),Some("synthetic-ui-39"));
        assert_eq!(observations.last().unwrap().status,Status::Queued);
        assert!(matches!(outgoing.observations(1,1000),Err(FileError::Stale)));
        assert!(matches!(incoming.observations(1,1000),Err(FileError::Invalid)));
        let next=incoming.reset_revision(1).unwrap();outgoing.align_revision(&next,3,1000).unwrap();
        assert!(outgoing.observations(3,1000).unwrap().iter().all(|row|row.status==Status::Deleted && row.envelope.is_none()));
    }
    #[test]
    fn delivery_history_revision_fences_unreceived_prompts_across_devices() {
        let (incoming,outgoing,old,requested)=setup("history");
        let queued=outgoing.queue(prompt("synthetic-deleted"),1,1000,10000).unwrap();let wire=queued.envelope.unwrap();
        let mut other_scope=scope(Direction::Incoming);other_scope.device_id="synthetic-second-device".into();other_scope.remote_endpoint=iroh::SecretKey::from_bytes(&[33;32]).public().to_string();
        let other=DeliveryStore::open(requested,[9;32],other_scope,true).unwrap();
        assert_eq!(other.revision(1).unwrap(),old);
        let next=incoming.reset_revision(1).unwrap();assert_ne!(next,old);assert_eq!(other.revision(1).unwrap(),next);
        let stale=incoming.begin(&Envelope::from_slice(&wire).unwrap(),1,1000).unwrap();assert!(!stale.execute);assert_eq!(stale.status,Status::Deleted);
        outgoing.align_revision(&next,2,1000).unwrap();assert!(outgoing.pending(2,1000).unwrap().is_empty());
    }
    #[test]
    fn delivery_changed_digest_newer_schema_and_expiry_fail_without_overwrite_or_replay() {
        let (incoming,outgoing,revision,_)=setup("integrity");let queued=outgoing.queue(prompt("synthetic-id"),1,1000,10000).unwrap();
        let mut forged=prompt("synthetic-id");forged.payload.insert("message".into(),serde_json::Value::String("changed".into()));
        assert!(matches!(outgoing.queue(forged,1,1000,10000),Err(FileError::Invalid)));
        let entry=outgoing.load("synthetic-id").unwrap().unwrap();let path=outgoing.protected.operation_root(&operation_key("synthetic-id").unwrap()).unwrap().join("state.enc");
        let mut next=entry.clone();next.schema=2;outgoing.persist(&next).unwrap();let sealed=fs::read(&path).unwrap();
        assert!(matches!(outgoing.pending(1,1000),Err(FileError::Unavailable)));assert_eq!(fs::read(&path).unwrap(),sealed);
        outgoing.persist(&entry).unwrap();assert_eq!(outgoing.sweep(10000).unwrap(),1);
        assert_eq!(incoming.query(&queued.operation_id,&queued.digest,&revision,10000,1,10000).unwrap().status,Status::Expired);
    }
    #[test]
    fn delivery_queue_budget_and_missing_receipt_preserve_retryable_bytes() {
        let (incoming,outgoing,revision,_)=setup("budget");
        for n in 0..MAX_PENDING { outgoing.queue(prompt(&format!("synthetic-{n}")),1,1000,10000).unwrap(); }
        assert!(matches!(outgoing.queue(prompt("synthetic-overflow"),1,1000,10000),Err(FileError::Capacity)));
        let row=outgoing.pending(1,1000).unwrap().remove(0);
        let receipt=incoming.query(&row.operation_id,&row.digest,&revision,10000,1,1000).unwrap();assert_eq!(receipt.status,Status::Missing);
        assert!(outgoing.receipt(&row.operation_id,&row.digest,&revision,10000,Status::Missing,1,1000).unwrap().envelope.is_some());
        outgoing.delete_pending(1).unwrap();assert!(outgoing.pending(1,1000).unwrap().is_empty());
    }
    #[test]
    fn delivery_same_process_replacement_marks_old_pending_claim_uncertain() {
        let (incoming,outgoing,revision,_)=setup("generation");
        let row=outgoing.queue(prompt("synthetic-generation"),1,1000,10000).unwrap();
        let envelope=Envelope::from_slice(&row.envelope.unwrap()).unwrap();
        assert!(incoming.begin(&envelope,1,1000).unwrap().execute);
        assert_eq!(incoming.query(&row.operation_id,&row.digest,&revision,10000,2,1000).unwrap().status,Status::Uncertain);
        assert!(matches!(incoming.finish(&row.operation_id,1,1000,true),Err(FileError::Stale)));
        assert_eq!(incoming.finish(&row.operation_id,2,1000,true).unwrap().status,Status::Uncertain);
        assert!(!incoming.begin(&envelope,2,1000).unwrap().execute);
    }
    #[test]
    fn delivery_history_pause_survives_restart_and_requires_the_same_fence_to_resume() {
        let (incoming,outgoing,old,requested)=setup("pause");
        let stale=outgoing.queue(prompt("synthetic-before-delete"),1,1000,10000).unwrap();
        let next=incoming.pause_revision(1).unwrap();
        assert!(matches!(incoming.revision(1),Err(FileError::Deleted)));
        outgoing.align_revision(&next,1,1000).unwrap();
        let during=outgoing.queue(prompt("synthetic-during-delete"),1,1000,10000).unwrap();
        assert_eq!(incoming.query(&during.operation_id,&during.digest,&next,10000,1,1000).unwrap().status,Status::Deleted);
        assert!(!incoming.begin(&Envelope::from_slice(&during.envelope.unwrap()).unwrap(),1,1000).unwrap().execute);
        drop(incoming);
        let reopened=DeliveryStore::open(requested,[9;32],scope(Direction::Incoming),false).unwrap();
        assert!(matches!(reopened.revision(1),Err(FileError::Deleted)));
        assert!(matches!(reopened.resume_revision(&old,1),Err(FileError::Stale)));
        reopened.resume_revision(&next,1).unwrap();
        assert_eq!(reopened.query(&stale.operation_id,&stale.digest,&old,10000,1,1000).unwrap().status,Status::Deleted);
        let fresh=outgoing.queue(prompt("synthetic-after-delete"),1,1000,10000).unwrap();
        assert!(reopened.begin(&Envelope::from_slice(&fresh.envelope.unwrap()).unwrap(),1,1000).unwrap().execute);
    }
    #[test]
    fn delivery_sweep_reaps_retired_scopes_but_preserves_corrupt_journals_and_fences() {
        let requested=root("retired-out");
        let incoming=DeliveryStore::open(root("retired-in"),[9;32],scope(Direction::Incoming),true).unwrap();
        let outgoing=DeliveryStore::open(requested.clone(),[8;32],scope(Direction::Outgoing),true).unwrap();
        outgoing.align_revision(&incoming.revision(1).unwrap(),1,1000).unwrap();
        let row=outgoing.queue(prompt("synthetic-retired"),1,1000,10000).unwrap();
        let path=outgoing.protected.operation_root(&operation_key(&row.operation_id).unwrap()).unwrap().join("state.enc");
        let valid=fs::read(&path).unwrap(); fs::write(&path,b"synthetic-corruption").unwrap();
        // Open another authorization epoch in the exact requested outgoing root.
        let mut next_scope=scope(Direction::Outgoing);next_scope.authorization_epoch=2;
        let other=DeliveryStore::open(requested,[8;32],next_scope,true).unwrap();
        assert!(matches!(other.sweep(10000),Err(FileError::Unavailable)));
        assert_eq!(fs::read(&path).unwrap(),b"synthetic-corruption");
        fs::write(&path,valid).unwrap(); assert_eq!(other.sweep(10000).unwrap(),1);
        assert!(!path.exists()); assert!(other.protected.root.join(format!("history-{}.enc",outgoing.history_name)).exists());
    }
    #[test]
    fn delivery_prompt_ttl_cannot_exceed_attachment_capabilities() {
        let (incoming,outgoing,_,_)=setup("attachment-ttl");
        let mut envelope=prompt("synthetic-file-ttl");
        envelope.payload.insert("context".into(),serde_json::json!([{"attachments":[{"file_ref":{
            "transfer_id":"ab".repeat(16),"purpose":"attachment","filename":"synthetic.bin","mime_type":"application/octet-stream",
            "total":1,"offset":0,"sha256":vec![9;32],"expires_at_ms":5000,"metadata":{}}}]}]));
        let row=outgoing.queue(envelope,1,1000,10000).unwrap(); assert_eq!(row.expires_at_ms,5000);
        let mut forged=Envelope::from_slice(&row.envelope.unwrap()).unwrap();forged.extensions.get_mut("delivery").unwrap()["expires_at_ms"]=10000.into();
        assert!(matches!(incoming.begin(&forged,1,1000),Err(FileError::Invalid)));
    }
    #[test]
    fn delivery_same_millisecond_recovery_keeps_original_admission_order() {
        let (_,outgoing,_,_)=setup("order");
        outgoing.queue(prompt("synthetic-z-first"),1,1000,10000).unwrap();
        outgoing.queue(prompt("synthetic-a-second"),1,1000,10000).unwrap();
        let pending=outgoing.pending(2,1000).unwrap();
        assert_eq!(pending.iter().map(|v|v.operation_id.as_str()).collect::<Vec<_>>(),vec!["synthetic-z-first","synthetic-a-second"]);
    }
    #[test]
    fn delivery_selected_history_deletes_prompt_aliases_without_cancelling_another_conversation() {
        let (_,outgoing,_,_)=setup("selected-history");
        let mut selected=prompt("synthetic-wire-id");
        selected.payload.insert("metadata".into(),serde_json::json!({"client_prompt_id":"synthetic-ui-id"}));
        outgoing.queue(selected.clone(),1,1000,10000).unwrap();
        outgoing.queue(prompt("synthetic-other-ui-id"),1,1000,10000).unwrap();
        assert!(outgoing.delete_pending_prompts(&[]).unwrap().is_empty());
        assert!(outgoing.delete_pending_prompts(&["invalid\nidentifier".into()]).is_err());
        let cancelled=outgoing.delete_pending_prompts(&["synthetic-ui-id".into()]).unwrap();
        assert_eq!(cancelled.len(),1);assert_eq!(cancelled[0].operation_id,"synthetic-wire-id");assert!(cancelled[0].envelope.is_none());
        assert_eq!(outgoing.queue(selected,2,1000,10000).unwrap().status,Status::Deleted);
        assert_eq!(outgoing.pending(2,1000).unwrap()[0].operation_id,"synthetic-other-ui-id");
        assert_eq!(outgoing.delete_pending_prompts(&["synthetic-other-ui-id".into()]).unwrap().len(),1);
    }
    #[test]
    fn delivery_local_history_deletion_cancels_queued_bodies_in_retired_scopes() {
        let requested=root("local-history"); let first_scope=scope(Direction::Outgoing);
        let first=DeliveryStore::open(requested.clone(),[9;32],first_scope.clone(),true).unwrap();
        first.align_revision(&"ab".repeat(16),1,1000).unwrap();first.queue(prompt("synthetic-local-one"),1,1000,10000).unwrap();
        let mut other_scope=first_scope.clone();other_scope.authorization_epoch=2;other_scope.conversation_key="synthetic-other-conversation".into();
        let other=DeliveryStore::open(requested.clone(),[9;32],other_scope,true).unwrap();
        other.align_revision(&"cd".repeat(16),2,1000).unwrap();other.queue(prompt("synthetic-local-two"),2,1000,10000).unwrap();
        drop(first);drop(other);
        let maintenance=DeliveryStore::open(requested.clone(),[9;32],first_scope.clone(),false).unwrap();
        let cancelled=maintenance.delete_pending_history(Some("synthetic-conversation")).unwrap();
        assert_eq!(cancelled.len(),1);assert_eq!(cancelled[0].status,Status::Deleted);assert!(cancelled[0].envelope.is_none());
        assert!(maintenance.pending(2,1000).unwrap().is_empty());
        assert_eq!(maintenance.queue(prompt("synthetic-local-one"),2,1000,10000).unwrap().status,Status::Deleted);
        assert_eq!(maintenance.delete_pending_history(None).unwrap().len(),1);
        assert!(maintenance.delete_pending_history(None).unwrap().is_empty());
        // An authenticated newer floor is preserved and cannot authorize a
        // destructive sweep over its journals.
        let path=maintenance.protected.scope.join("scope.enc");let aad=maintenance.protected.aad_named("scope",b"delivery-scope/1");
        let mut floor:ScopeFloor=serde_json::from_slice(&maintenance.protected.unseal(&fs::read(&path).unwrap(),&aad).unwrap()).unwrap();
        floor.schema=2;let newer=maintenance.protected.seal(serde_json::to_vec(&floor).unwrap(),&aad).unwrap();fs::write(&path,&newer).unwrap();
        assert!(matches!(maintenance.delete_pending_history(None),Err(FileError::Unavailable)));assert_eq!(fs::read(&path).unwrap(),newer);
        assert!(matches!(maintenance.sweep(10000),Err(FileError::Unavailable)));assert_eq!(fs::read(&path).unwrap(),newer);
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Bounded processing of the existing Fernet token; no new storage format.

use aes::{
    Aes128,
    cipher::{BlockDecryptMut, BlockEncryptMut, KeyIvInit, consts::U16, inout::InOutBuf},
};
use base64::{Engine, engine::general_purpose::URL_SAFE};
use ring::hmac::{Context as Signer, HMAC_SHA256, Key};
use std::{
    fs::File,
    io::{Cursor, Read, Seek, SeekFrom, Write},
    path::Path,
    time::{SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use zeroize::{Zeroize, Zeroizing};

pub const BLOCK_BYTES: usize = 48 * 1024;
type Encryptor = cbc::Encryptor<Aes128>;
type Decryptor = cbc::Decryptor<Aes128>;

#[derive(Debug, PartialEq, Eq)]
pub enum StreamError {
    Invalid,
    Unavailable,
}
type Result<T> = std::result::Result<T, StreamError>;

fn key_bytes(key: &[u8]) -> Result<Zeroizing<[u8; 32]>> {
    Ok(Zeroizing::new(
        key.try_into().map_err(|_| StreamError::Invalid)?,
    ))
}

fn signer(key: &[u8]) -> Signer {
    Signer::with_key(&Key::new(HMAC_SHA256, key))
}

pub struct FernetEncryptor {
    cipher: Encryptor,
    signer: Option<Signer>,
    plaintext: Zeroizing<Vec<u8>>,
    encoded_tail: Vec<u8>,
    header: Vec<u8>,
    failed: bool,
}
impl FernetEncryptor {
    pub fn new(key: &[u8]) -> Result<Self> {
        let mut iv = [0; 16];
        getrandom::getrandom(&mut iv).map_err(|_| StreamError::Unavailable)?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| StreamError::Unavailable)?
            .as_secs();
        Self::with_iv(key, now, iv)
    }
    fn with_iv(key: &[u8], now: u64, iv: [u8; 16]) -> Result<Self> {
        let key = key_bytes(key)?;
        let mut owner = Self {
            cipher: Encryptor::new((&key[16..]).into(), (&iv).into()),
            signer: Some(signer(&key[..16])),
            plaintext: Zeroizing::new(Vec::new()),
            encoded_tail: Vec::new(),
            header: Vec::new(),
            failed: false,
        };
        let mut header = vec![0x80];
        header.extend_from_slice(&now.to_be_bytes());
        header.extend_from_slice(&iv);
        owner.signer.as_mut().unwrap().update(&header);
        owner.header = owner.encode(&header, false);
        Ok(owner)
    }
    pub fn header(&self) -> &[u8] {
        &self.header
    }
    fn encode(&mut self, raw: &[u8], final_block: bool) -> Vec<u8> {
        let mut bytes = std::mem::take(&mut self.encoded_tail);
        bytes.extend_from_slice(raw);
        let length = if final_block {
            bytes.len()
        } else {
            bytes.len() / 3 * 3
        };
        self.encoded_tail.extend_from_slice(&bytes[length..]);
        URL_SAFE.encode(&bytes[..length]).into_bytes()
    }
    pub fn update(&mut self, block: &[u8]) -> Result<Vec<u8>> {
        if self.failed || self.signer.is_none() || block.is_empty() || block.len() > BLOCK_BYTES {
            self.failed = true;
            return Err(StreamError::Invalid);
        }
        self.plaintext.extend_from_slice(block);
        let length = self.plaintext.len() / 16 * 16;
        let remaining = self.plaintext.split_off(length);
        let (blocks, tail) = InOutBuf::from(self.plaintext.as_mut_slice()).into_chunks::<U16>();
        debug_assert!(tail.is_empty());
        self.cipher.encrypt_blocks_inout_mut(blocks);
        self.signer.as_mut().unwrap().update(&self.plaintext);
        let ciphertext = std::mem::replace(&mut *self.plaintext, remaining);
        Ok(self.encode(&ciphertext, false))
    }
    pub fn finish(&mut self) -> Result<Vec<u8>> {
        if self.failed {
            return Err(StreamError::Invalid);
        }
        let Some(mut signer) = self.signer.take() else {
            self.failed = true;
            return Err(StreamError::Invalid);
        };
        let padding = 16 - self.plaintext.len();
        self.plaintext
            .extend(std::iter::repeat_n(padding as u8, padding));
        self.cipher
            .encrypt_block_mut(self.plaintext.as_mut_slice().into());
        signer.update(&self.plaintext);
        let mut ending = self.plaintext.to_vec();
        ending.extend_from_slice(signer.sign().as_ref());
        self.plaintext.zeroize();
        Ok(self.encode(&ending, true))
    }
}

enum Snapshot {
    Disk(File),
    Memory(Cursor<Vec<u8>>),
}
impl Read for Snapshot {
    fn read(&mut self, bytes: &mut [u8]) -> std::io::Result<usize> {
        match self {
            Self::Disk(file) => file.read(bytes),
            Self::Memory(cursor) => cursor.read(bytes),
        }
    }
}
impl Write for Snapshot {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        match self {
            Self::Disk(file) => file.write(bytes),
            Self::Memory(cursor) => cursor.write(bytes),
        }
    }
    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            Self::Disk(file) => file.flush(),
            Self::Memory(cursor) => cursor.flush(),
        }
    }
}
impl Seek for Snapshot {
    fn seek(&mut self, pos: SeekFrom) -> std::io::Result<u64> {
        match self {
            Self::Disk(file) => file.seek(pos),
            Self::Memory(cursor) => cursor.seek(pos),
        }
    }
}

#[derive(PartialEq)]
enum Phase {
    Collecting,
    Ready,
    Done,
    Failed,
}

pub struct FernetReader {
    snapshot: Snapshot,
    key: Zeroizing<[u8; 32]>,
    signer: Option<Signer>,
    pending: Vec<u8>,
    signature_tail: Vec<u8>,
    encoded_size: u64,
    raw_size: u64,
    maximum: Option<u64>,
    encoded_limit: Option<u64>,
    padded: bool,
    phase: Phase,
    cipher: Option<Decryptor>,
    remaining: u64,
    padding: usize,
}
impl FernetReader {
    pub fn new(key: &[u8], directory: &Path, maximum: Option<u64>) -> Result<Self> {
        if let Some(test) = std::env::var_os("AUTOYOU_TEST_ROOT") {
            let root = Path::new(&test)
                .canonicalize()
                .map_err(|_| StreamError::Invalid)?;
            if !directory
                .canonicalize()
                .map_err(|_| StreamError::Invalid)?
                .starts_with(root)
            {
                return Err(StreamError::Invalid);
            }
        }
        let file = tempfile::tempfile_in(directory).map_err(|_| StreamError::Unavailable)?;
        Self::with_snapshot(key, Snapshot::Disk(file), maximum)
    }
    fn with_snapshot(key: &[u8], snapshot: Snapshot, maximum: Option<u64>) -> Result<Self> {
        let key = key_bytes(key)?;
        let encoded_limit = match maximum {
            Some(max) => Some(
                max.checked_div(16)
                    .and_then(|v| v.checked_add(1))
                    .and_then(|v| v.checked_mul(16))
                    .and_then(|v| v.checked_add(59))
                    .and_then(|v| v.checked_div(3))
                    .and_then(|v| v.checked_mul(4))
                    .ok_or(StreamError::Invalid)?,
            ),
            None => None,
        };
        Ok(Self {
            signer: Some(signer(&key[..16])),
            snapshot,
            key,
            pending: Vec::new(),
            signature_tail: Vec::new(),
            encoded_size: 0,
            raw_size: 0,
            maximum,
            encoded_limit,
            padded: false,
            phase: Phase::Collecting,
            cipher: None,
            remaining: 0,
            padding: 0,
        })
    }
    pub fn update(&mut self, block: &[u8]) -> Result<()> {
        let result = self.receive(block);
        if result.is_err() {
            self.phase = Phase::Failed;
        }
        result
    }
    fn receive(&mut self, block: &[u8]) -> Result<()> {
        if self.phase != Phase::Collecting
            || self.padded
            || block.is_empty()
            || block.len() > BLOCK_BYTES
        {
            return Err(StreamError::Invalid);
        }
        self.encoded_size = self
            .encoded_size
            .checked_add(block.len() as u64)
            .ok_or(StreamError::Invalid)?;
        if self
            .encoded_limit
            .is_some_and(|limit| self.encoded_size > limit)
        {
            return Err(StreamError::Invalid);
        }
        self.pending.extend_from_slice(block);
        let length = self.pending.len() / 4 * 4;
        if length == 0 {
            return Ok(());
        }
        let rest = self.pending.split_off(length);
        let encoded = std::mem::replace(&mut self.pending, rest);
        if encoded.contains(&b'=') {
            if encoded[..length - 4].contains(&b'=') || !self.pending.is_empty() {
                return Err(StreamError::Invalid);
            }
            self.padded = true;
        }
        let decoded = URL_SAFE
            .decode(&encoded)
            .map_err(|_| StreamError::Invalid)?;
        self.raw_size = self
            .raw_size
            .checked_add(decoded.len() as u64)
            .ok_or(StreamError::Invalid)?;
        self.signature_tail.extend_from_slice(&decoded);
        if self.signature_tail.len() > 32 {
            let tail = self
                .signature_tail
                .split_off(self.signature_tail.len() - 32);
            let message = std::mem::replace(&mut self.signature_tail, tail);
            self.signer
                .as_mut()
                .ok_or(StreamError::Invalid)?
                .update(&message);
            self.snapshot
                .write_all(&message)
                .map_err(|_| StreamError::Unavailable)?;
        }
        Ok(())
    }
    pub fn finish(&mut self) -> Result<()> {
        let result = self.authenticate();
        if result.is_err() {
            self.phase = Phase::Failed;
        }
        result
    }
    fn authenticate(&mut self) -> Result<()> {
        if self.phase != Phase::Collecting
            || !self.pending.is_empty()
            || self.signature_tail.len() != 32
            || self.raw_size < 73
        {
            return Err(StreamError::Invalid);
        }
        let expected = self.signer.take().ok_or(StreamError::Invalid)?.sign();
        if expected.as_ref().ct_eq(&self.signature_tail).unwrap_u8() != 1 {
            return Err(StreamError::Invalid);
        }
        self.snapshot
            .flush()
            .map_err(|_| StreamError::Unavailable)?;
        self.snapshot
            .seek(SeekFrom::Start(0))
            .map_err(|_| StreamError::Unavailable)?;
        let mut header = [0; 25];
        self.snapshot
            .read_exact(&mut header)
            .map_err(|_| StreamError::Unavailable)?;
        let size = self.raw_size - 57;
        if header[0] != 0x80 || size < 16 || size % 16 != 0 {
            return Err(StreamError::Invalid);
        }
        self.snapshot
            .seek(SeekFrom::Start(25 + size.saturating_sub(32)))
            .map_err(|_| StreamError::Unavailable)?;
        let mut ending = [0; 32];
        let length = size.min(32) as usize;
        self.snapshot
            .read_exact(&mut ending[..length])
            .map_err(|_| StreamError::Unavailable)?;
        let iv = if size == 16 {
            &header[9..]
        } else {
            &ending[..16]
        };
        let mut last = [0; 16];
        last.copy_from_slice(&ending[length - 16..length]);
        Decryptor::new((&self.key[16..]).into(), iv.into()).decrypt_block_mut((&mut last).into());
        let pad = last[15] as usize;
        if !(1..=16).contains(&pad)
            || last[16 - pad..].iter().any(|v| *v != pad as u8)
            || self.maximum.is_some_and(|max| size - pad as u64 > max)
        {
            return Err(StreamError::Invalid);
        }
        self.cipher = Some(Decryptor::new(
            (&self.key[16..]).into(),
            (&header[9..]).into(),
        ));
        self.key.zeroize();
        self.remaining = size;
        self.padding = pad;
        self.phase = Phase::Ready;
        self.snapshot
            .seek(SeekFrom::Start(25))
            .map_err(|_| StreamError::Unavailable)?;
        Ok(())
    }
    pub fn read_next(&mut self) -> Result<Option<Vec<u8>>> {
        let result = self.read_chunk();
        if result.is_err() {
            self.phase = Phase::Failed;
        }
        result
    }
    fn read_chunk(&mut self) -> Result<Option<Vec<u8>>> {
        if self.phase == Phase::Done {
            return Ok(None);
        }
        if self.phase != Phase::Ready {
            return Err(StreamError::Invalid);
        }
        let mut bytes = vec![0; self.remaining.min(BLOCK_BYTES as u64) as usize];
        self.snapshot
            .read_exact(&mut bytes)
            .map_err(|_| StreamError::Unavailable)?;
        self.remaining -= bytes.len() as u64;
        let (blocks, tail) = InOutBuf::from(bytes.as_mut_slice()).into_chunks::<U16>();
        debug_assert!(tail.is_empty());
        self.cipher
            .as_mut()
            .ok_or(StreamError::Invalid)?
            .decrypt_blocks_inout_mut(blocks);
        if self.remaining == 0 {
            bytes.truncate(bytes.len() - self.padding);
            self.phase = Phase::Done;
        }
        Ok((!bytes.is_empty()).then_some(bytes))
    }
}

pub fn encrypt_bytes(key: &[u8], bytes: &[u8]) -> Result<Vec<u8>> {
    let mut owner = FernetEncryptor::new(key)?;
    let mut output = owner.header().to_vec();
    for block in bytes.chunks(BLOCK_BYTES) {
        output.extend_from_slice(&owner.update(block)?);
    }
    output.extend_from_slice(&owner.finish()?);
    Ok(output)
}
pub fn decrypt_bytes(key: &[u8], bytes: &[u8]) -> Result<Vec<u8>> {
    let mut owner =
        FernetReader::with_snapshot(key, Snapshot::Memory(Cursor::new(Vec::new())), None)?;
    for block in bytes.chunks(BLOCK_BYTES) {
        owner.update(block)?;
    }
    owner.finish()?;
    let mut output = Vec::with_capacity(
        (owner.remaining - owner.padding as u64)
            .try_into()
            .map_err(|_| StreamError::Invalid)?,
    );
    while let Some(block) = owner.read_next()? {
        output.extend_from_slice(&block);
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::*;
    use fernet::Fernet;
    const KEY: [u8; 32] = [0x53; 32];

    fn legacy() -> Fernet {
        Fernet::new(&URL_SAFE.encode(KEY)).unwrap()
    }
    fn receive(token: &[u8], maximum: Option<u64>) -> Result<FernetReader> {
        let mut owner =
            FernetReader::with_snapshot(&KEY, Snapshot::Memory(Cursor::new(Vec::new())), maximum)?;
        for block in token.chunks(11) {
            owner.update(block)?;
        }
        owner.finish()?;
        Ok(owner)
    }

    #[test]
    fn native_and_retained_fernet_exchange_boundary_sizes() {
        for length in [
            0,
            1,
            15,
            16,
            17,
            BLOCK_BYTES - 1,
            BLOCK_BYTES,
            BLOCK_BYTES + 1,
            2 * 1024 * 1024 + 17,
        ] {
            let data: Vec<u8> = (0..length).map(|i| (i % 251) as u8).collect();
            let token = encrypt_bytes(&KEY, &data).unwrap();
            assert_eq!(
                legacy()
                    .decrypt(std::str::from_utf8(&token).unwrap())
                    .unwrap(),
                data
            );
            assert_eq!(decrypt_bytes(&KEY, &token).unwrap(), data);
            assert_eq!(
                decrypt_bytes(&KEY, legacy().encrypt(&data).as_bytes()).unwrap(),
                data
            );
            let mut owner = receive(&token, Some(length as u64)).unwrap();
            let mut output = Vec::new();
            while let Some(block) = owner.read_next().unwrap() {
                assert!(block.len() <= BLOCK_BYTES);
                output.extend(block);
            }
            assert_eq!(output, data);
        }
    }

    #[test]
    fn arbitrary_boundaries_keep_one_token_and_a_bounded_plaintext_tail() {
        let data = b"synthetic fragmented audio".repeat(4000);
        let mut owner = FernetEncryptor::new(&KEY).unwrap();
        let mut token = owner.header().to_vec();
        for block in data.chunks(7) {
            token.extend(owner.update(block).unwrap());
            assert!(owner.plaintext.len() < 16 && owner.encoded_tail.len() < 3);
        }
        token.extend(owner.finish().unwrap());
        assert_eq!(
            legacy()
                .decrypt(std::str::from_utf8(&token).unwrap())
                .unwrap(),
            data
        );
        assert_eq!(decrypt_bytes(&KEY, &token).unwrap(), data);
        assert!(owner.finish().is_err() && owner.update(b"synthetic").is_err());
    }

    #[test]
    fn damaged_or_trailing_bytes_never_release_plaintext() {
        let token = encrypt_bytes(&KEY, b"synthetic private body").unwrap();
        let mut candidates = Vec::new();
        for offset in [0, token.len() / 2, token.len() - 8] {
            let mut damaged = token.clone();
            damaged[offset] ^= 1;
            candidates.push(damaged);
        }
        candidates.push(token[..token.len() - 4].to_vec());
        let mut trailing = token.clone();
        trailing.extend(b"AAAA");
        candidates.push(trailing);
        let mut whitespace = token.clone();
        whitespace[10] = b'\n';
        candidates.push(whitespace);
        for damaged in candidates {
            let mut owner =
                FernetReader::with_snapshot(&KEY, Snapshot::Memory(Cursor::new(Vec::new())), None)
                    .unwrap();
            let status = owner.update(&damaged).and_then(|_| owner.finish());
            assert!(status.is_err());
            assert!(owner.read_next().is_err());
            assert!(owner.finish().is_err());
        }
        assert!(receive(&token, Some(3)).is_err());
    }

    #[test]
    fn authentic_but_invalid_padding_and_version_are_rejected_before_read() {
        for version in [0x80, 0x81] {
            let mut raw = vec![version];
            raw.extend(1_u64.to_be_bytes());
            raw.extend([0; 16]);
            let mut block = [if version == 0x81 { 16 } else { 0 }; 16];
            Encryptor::new((&KEY[16..]).into(), (&[0; 16]).into())
                .encrypt_block_mut((&mut block).into());
            raw.extend(block);
            let mut mac = signer(&KEY[..16]);
            mac.update(&raw);
            raw.extend(mac.sign().as_ref());
            assert!(receive(URL_SAFE.encode(raw).as_bytes(), None).is_err());
        }
    }

    #[test]
    fn invalid_calls_poison_owners_and_persistent_tokens_have_no_clock_expiry() {
        assert!(FernetEncryptor::new(&[0; 31]).is_err());
        let mut writer = FernetEncryptor::new(&KEY).unwrap();
        assert!(writer.update(&vec![0; BLOCK_BYTES + 1]).is_err());
        assert!(writer.finish().is_err());
        let mut reader =
            FernetReader::with_snapshot(&KEY, Snapshot::Memory(Cursor::new(Vec::new())), None)
                .unwrap();
        assert!(reader.read_next().is_err());
        assert!(reader.update(b"AAAA").is_err());
        for timestamp in [0, u64::MAX] {
            let mut owner = FernetEncryptor::with_iv(&KEY, timestamp, [1; 16]).unwrap();
            let mut token = owner.header().to_vec();
            token.extend(owner.update(b"synthetic clock").unwrap());
            token.extend(owner.finish().unwrap());
            assert_eq!(decrypt_bytes(&KEY, &token).unwrap(), b"synthetic clock");
        }
    }

    #[test]
    fn disk_snapshot_uses_only_the_explicit_test_root_and_is_removed_on_drop() {
        let root = std::path::PathBuf::from(
            std::env::var_os("AUTOYOU_TEST_ROOT").expect("isolated root required"),
        );
        std::fs::create_dir_all(&root).unwrap();
        let scoped = tempfile::tempdir_in(&root).unwrap();
        let token = encrypt_bytes(&KEY, b"synthetic ciphertext snapshot").unwrap();
        let mut reader = FernetReader::new(&KEY, scoped.path(), None).unwrap();
        for chunk in token.chunks(17) {
            reader.update(chunk).unwrap();
        }
        reader.finish().unwrap();
        assert_eq!(
            reader.read_next().unwrap().unwrap(),
            b"synthetic ciphertext snapshot"
        );
        drop(reader);
        assert_eq!(std::fs::read_dir(scoped.path()).unwrap().count(), 0);
        assert!(
            FernetReader::new(
                &KEY,
                scoped.path().parent().unwrap().parent().unwrap(),
                None
            )
            .is_err()
        );
    }
}

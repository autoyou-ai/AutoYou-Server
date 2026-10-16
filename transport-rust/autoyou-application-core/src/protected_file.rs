// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Verified file persistence, sharing the existing token processor and atomic owner.

use crate::{
    atomic_file::{self, AtomicFile, invalid},
    secure_stream::{BLOCK_BYTES, FernetEncryptor, StreamError},
};
use ring::digest::{Context, SHA256};
use std::{
    fs::File,
    io::{self, Read},
    path::Path,
};

pub const FILE_HEADER: &[u8] = b"AUTOYOU-SPM-FILE-v1\n";
pub const SQLITE_HEADER: &[u8] = b"AUTOYOU-SPM-SQLITE-v1\n";
const MAX_BYTES: u64 = 1024 * 1024 * 1024;
const SQLITE_MAGIC: &[u8] = b"SQLite format 3\0";

pub fn sqlite_needs_normalisation(bytes: &[u8]) -> bool {
    bytes.len() >= 20 && bytes.starts_with(SQLITE_MAGIC) && (bytes[18] == 2 || bytes[19] == 2)
}
pub fn normalise_sqlite_header(bytes: &mut [u8]) {
    if sqlite_needs_normalisation(bytes) {
        bytes[18] = 0;
        bytes[19] = 0;
    }
}
fn crypto(error: StreamError) -> io::Error {
    match error {
        StreamError::Invalid => invalid("invalid protected file token"),
        StreamError::Unavailable => io::Error::other("protected file cipher is unavailable"),
    }
}

pub struct FileWriter {
    file: Option<AtomicFile>,
    cipher: Option<FernetEncryptor>,
    digest: Option<Context>,
    expected_digest: Option<[u8; 32]>,
    expected_size: u64,
    total: u64,
    prepared: bool,
    protect_existing: bool,
}
impl FileWriter {
    pub fn verified(
        path: &Path,
        scratch: Option<&Path>,
        key: Option<&[u8]>,
        size: u64,
        digest: &[u8],
    ) -> io::Result<Self> {
        let expected: [u8; 32] = digest
            .try_into()
            .map_err(|_| invalid("invalid protected stream descriptor"))?;
        if size > MAX_BYTES {
            return Err(invalid("invalid protected stream descriptor"));
        }
        let mut owner = Self::new(path, scratch, key, size, FILE_HEADER)?;
        owner.protect_existing = key.is_none();
        owner.check_boundary()?;
        owner.expected_digest = Some(expected);
        owner.digest = Some(Context::new(&SHA256));
        Ok(owner)
    }
    fn new(
        path: &Path,
        scratch: Option<&Path>,
        key: Option<&[u8]>,
        size: u64,
        header: &[u8],
    ) -> io::Result<Self> {
        let cipher = key.map(FernetEncryptor::new).transpose().map_err(crypto)?;
        let mut file = AtomicFile::new(path, scratch)?;
        if let Some(cipher) = &cipher {
            file.write(header)?;
            file.write(cipher.header())?;
        }
        Ok(Self {
            file: Some(file),
            cipher,
            digest: None,
            expected_digest: None,
            expected_size: size,
            total: 0,
            prepared: false,
            protect_existing: false,
        })
    }
    fn check_boundary(&self) -> io::Result<()> {
        if !self.protect_existing {
            return Ok(());
        }
        let target = self
            .file
            .as_ref()
            .ok_or_else(|| invalid("protected file stage is unavailable"))?
            .target();
        let input = match File::open(target) {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
            Err(_) => return Err(io::Error::other("protected file storage is unavailable")),
        };
        let mut prefix = Vec::with_capacity(SQLITE_HEADER.len());
        input
            .take(SQLITE_HEADER.len() as u64)
            .read_to_end(&mut prefix)
            .map_err(|_| io::Error::other("protected file storage is unavailable"))?;
        if prefix.starts_with(FILE_HEADER) || prefix.starts_with(SQLITE_HEADER) {
            return Err(invalid(
                "Protected file requires its original storage boundary",
            ));
        }
        Ok(())
    }
    fn output(&mut self) -> io::Result<&mut AtomicFile> {
        self.file
            .as_mut()
            .ok_or_else(|| invalid("protected file stage is unavailable"))
    }
    pub fn update(&mut self, block: &[u8]) -> io::Result<()> {
        let result = self.write_block(block);
        if result.is_err() {
            self.discard();
        }
        result
    }
    fn write_block(&mut self, block: &[u8]) -> io::Result<()> {
        if self.prepared || block.is_empty() || block.len() > BLOCK_BYTES {
            return Err(invalid("protected stream block exceeded its bound"));
        }
        self.total = self
            .total
            .checked_add(block.len() as u64)
            .ok_or_else(|| invalid("protected stream exceeded its declared size"))?;
        if self.total > self.expected_size {
            return Err(invalid("protected stream exceeded its declared size"));
        }
        if let Some(digest) = &mut self.digest {
            digest.update(block);
        }
        if let Some(cipher) = &mut self.cipher {
            let encoded = cipher.update(block).map_err(crypto)?;
            self.output()?.write(&encoded)
        } else {
            self.output()?.write(block)
        }
    }
    pub fn prepare(&mut self) -> io::Result<()> {
        let result = self.verify();
        if result.is_err() {
            self.discard();
        }
        result
    }
    fn verify(&mut self) -> io::Result<()> {
        if self.prepared || self.file.is_none() {
            return Err(invalid("protected file stage is unavailable"));
        }
        if self.total != self.expected_size
            || self.expected_digest.is_some_and(|expected| {
                self.digest.take().unwrap().finish().as_ref() != expected.as_slice()
            })
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "protected stream size or digest does not match",
            ));
        }
        if let Some(mut cipher) = self.cipher.take() {
            let ending = cipher.finish().map_err(crypto)?;
            self.output()?.write(&ending)?;
        }
        self.output()?.prepare()?;
        self.prepared = true;
        Ok(())
    }
    pub fn commit_once(&mut self) -> io::Result<()> {
        if !self.prepared {
            self.discard();
            return Err(invalid("protected file is not prepared"));
        }
        if let Err(error) = self.check_boundary() {
            self.discard();
            return Err(error);
        }
        // Preserve the SPM API's synced-data-before-promotion contract.
        let result = self.output()?.commit_once(false);
        if result.is_ok()
            || result
                .as_ref()
                .is_err_and(|e| e.kind() != io::ErrorKind::PermissionDenied)
        {
            self.discard();
        }
        result
    }
    pub fn commit(&mut self) -> io::Result<()> {
        // Verified producers recheck live consent immediately before this one attempt.
        let result = if self.expected_digest.is_some() {
            self.commit_once()
        } else {
            atomic_file::retry_commit(|| self.commit_once(), || Ok(()))
        };
        if result.is_err() {
            self.discard();
        }
        result
    }
    pub fn discard(&mut self) {
        self.file.take();
        self.cipher.take();
        self.digest.take();
        self.prepared = false;
    }
}

pub fn prepare_bytes(path: &Path, bytes: &[u8]) -> io::Result<FileWriter> {
    prepare_plain_bytes(path, bytes, false)
}
fn prepare_plain_bytes(
    path: &Path,
    bytes: &[u8],
    protect_existing: bool,
) -> io::Result<FileWriter> {
    let mut owner = FileWriter::new(path, None, None, bytes.len() as u64, FILE_HEADER)?;
    owner.protect_existing = protect_existing;
    owner.check_boundary()?;
    owner.output()?.write(bytes)?;
    owner.total = bytes.len() as u64;
    owner.prepare()?;
    Ok(owner)
}
pub fn prepare_plain(path: &Path, bytes: &[u8]) -> io::Result<FileWriter> {
    prepare_plain_bytes(path, bytes, true)
}
pub fn prepare_sealed(
    path: &Path,
    key: &[u8],
    bytes: &[u8],
    sqlite: bool,
) -> io::Result<FileWriter> {
    let mut owner = FileWriter::new(
        path,
        None,
        Some(key),
        bytes.len() as u64,
        if sqlite { SQLITE_HEADER } else { FILE_HEADER },
    )?;
    for (index, block) in bytes.chunks(BLOCK_BYTES).enumerate() {
        if sqlite && index == 0 && sqlite_needs_normalisation(block) {
            let mut header_block = block.to_vec();
            normalise_sqlite_header(&mut header_block);
            owner.update(&header_block)?;
        } else {
            owner.update(block)?;
        }
    }
    owner.prepare()?;
    Ok(owner)
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{Engine, engine::general_purpose::URL_SAFE};
    use fernet::Fernet;
    use std::{fs, path::PathBuf};
    fn root() -> tempfile::TempDir {
        let root = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").unwrap());
        fs::create_dir_all(&root).unwrap();
        tempfile::tempdir_in(root).unwrap()
    }
    #[test]
    fn verified_writers_share_formats_and_require_prepare_before_publication() {
        let root = root();
        let path = root.path().join("synthetic.bin");
        let key = [0x53; 32];
        let legacy = Fernet::new(&URL_SAFE.encode(key)).unwrap();
        for size in [0, 1, 16, BLOCK_BYTES + 17] {
            let data = vec![19; size];
            let hash = ring::digest::digest(&SHA256, &data);
            for sealed in [false, true] {
                fs::write(&path, b"original").unwrap();
                let mut owner = FileWriter::verified(
                    &path,
                    None,
                    sealed.then_some(key.as_slice()),
                    size as u64,
                    hash.as_ref(),
                )
                .unwrap();
                for chunk in data.chunks(BLOCK_BYTES) {
                    owner.update(chunk).unwrap();
                }
                owner.prepare().unwrap();
                assert_eq!(fs::read(&path).unwrap(), b"original");
                owner.commit().unwrap();
                let result = fs::read(&path).unwrap();
                let result = if sealed {
                    legacy
                        .decrypt(std::str::from_utf8(&result[FILE_HEADER.len()..]).unwrap())
                        .unwrap()
                } else {
                    result
                };
                assert_eq!(result, data);
                assert!(owner.commit().is_err());
                assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
            }
        }
    }
    #[test]
    fn malformed_blocks_and_incomplete_or_wrong_digests_poison_the_stage() {
        let root = root();
        let path = root.path().join("synthetic.bin");
        let hash = ring::digest::digest(&SHA256, b"synthetic");
        for failure in [
            "empty",
            "oversize",
            "size",
            "digest",
            "premature",
            "discard",
        ] {
            fs::write(&path, b"original").unwrap();
            let mut owner =
                FileWriter::verified(&path, None, Some(&[19; 32]), 9, hash.as_ref()).unwrap();
            match failure {
                "empty" => {
                    assert!(owner.update(b"").is_err());
                }
                "oversize" => {
                    assert!(owner.update(&vec![0; BLOCK_BYTES + 1]).is_err());
                }
                "size" => {
                    owner.update(b"short").unwrap();
                    assert!(owner.prepare().is_err());
                }
                "digest" => {
                    owner.update(b"different").unwrap();
                    assert!(owner.prepare().is_err());
                }
                "premature" => {
                    assert!(owner.commit().is_err());
                }
                "discard" => owner.discard(),
                _ => unreachable!(),
            }
            assert!(owner.commit().is_err());
            drop(owner);
            assert_eq!(fs::read(&path).unwrap(), b"original");
            assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
        }
    }
    #[test]
    fn whole_snapshots_normalise_only_the_sqlite_wal_header_without_collecting_a_token() {
        let root = root();
        let path = root.path().join("synthetic.db");
        let key = [19; 32];
        let legacy = Fernet::new(&URL_SAFE.encode(key)).unwrap();
        let mut data = vec![0; BLOCK_BYTES * 2 + 19];
        data[..16].copy_from_slice(SQLITE_MAGIC);
        data[18] = 2;
        data[19] = 2;
        let mut expected = data.clone();
        expected[18] = 0;
        expected[19] = 0;
        prepare_sealed(&path, &key, &data, true)
            .unwrap()
            .commit()
            .unwrap();
        let token = fs::read(&path).unwrap();
        assert!(token.starts_with(SQLITE_HEADER));
        assert_eq!(
            legacy
                .decrypt(std::str::from_utf8(&token[SQLITE_HEADER.len()..]).unwrap())
                .unwrap(),
            expected
        );
        prepare_bytes(&path, &data).unwrap().commit().unwrap();
        assert_eq!(fs::read(path).unwrap(), data);
    }
    #[test]
    fn plaintext_writers_cannot_replace_a_sealed_record_before_or_after_preparation() {
        let root = root();
        let path = root.path().join("synthetic.bin");
        let hash = ring::digest::digest(&SHA256, b"replacement");
        prepare_sealed(&path, &[19; 32], b"original", false)
            .unwrap()
            .commit()
            .unwrap();
        let original = fs::read(&path).unwrap();
        assert!(prepare_plain(&path, b"replacement").is_err());
        assert!(FileWriter::verified(&path, None, None, 11, hash.as_ref()).is_err());
        assert_eq!(fs::read(&path).unwrap(), original);
        prepare_bytes(&path, b"plain").unwrap().commit().unwrap();
        let mut owner = FileWriter::verified(&path, None, None, 11, hash.as_ref()).unwrap();
        owner.update(b"replacement").unwrap();
        owner.prepare().unwrap();
        prepare_sealed(&path, &[19; 32], b"changed boundary", false)
            .unwrap()
            .commit()
            .unwrap();
        let sealed = fs::read(&path).unwrap();
        assert!(owner.commit().is_err());
        assert_eq!(fs::read(&path).unwrap(), sealed);
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! Legacy-compatible protected state. The platform supplies its existing key;
//! this owner retains the kernel lease through authenticated read and commit.

use crate::file_store::{atomic_write, checked_directory, checked_file, read_bounded};
use autoyou_application_core::secure_stream;
use sha2::{Digest, Sha256};
use std::{fs::{self, File, OpenOptions, TryLockError}, io::Write, path::{Path, PathBuf}};

const MAX_CIPHERTEXT: usize = 16 * 1024 * 1024;
const MAX_PLAINTEXT: usize = MAX_CIPHERTEXT / 2;

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum StateError {
    #[error("invalid protected state input")] Invalid,
    #[error("protected state is already owned")] Busy,
    #[error("protected state is corrupt, lost or unavailable")] Unavailable,
    #[error("protected state exceeds its bound")] Capacity,
}
type Result<T> = std::result::Result<T, StateError>;

pub struct ProtectedStateTransaction {
    root: PathBuf,
    _lease: File,
    before: Option<Vec<u8>>,
    baseline_exists: bool,
    read_key: Option<[u8; 32]>,
}

impl ProtectedStateTransaction {
    pub fn begin(key_root: PathBuf) -> Result<Self> {
        if !key_root.is_absolute() { return Err(StateError::Invalid); }
        if let Some(test) = std::env::var_os("AUTOYOU_TEST_ROOT") {
            let test = PathBuf::from(test).canonicalize().map_err(|_| StateError::Invalid)?;
            // Resolve before creating anything; tests cannot touch an operator store.
            if !key_root.canonicalize().map_err(|_| StateError::Invalid)?.starts_with(test) {
                return Err(StateError::Invalid);
            }
        }
        checked_directory(&key_root, false).map_err(|_| StateError::Unavailable)?;
        let root = key_root.join("state");
        checked_directory(&root, true).map_err(|_| StateError::Unavailable)?;
        let lock_path = root.join("identity.lock");
        checked_file(&lock_path, true).map_err(|_| StateError::Unavailable)?;
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
        let mut lease = options.open(lock_path).map_err(|_| StateError::Unavailable)?;
        lease.try_lock().map_err(|error| match error {
            TryLockError::WouldBlock => StateError::Busy,
            TryLockError::Error(_) => StateError::Unavailable,
        })?;
        if lease.metadata().map_err(|_| StateError::Unavailable)?.len() == 0 {
            lease.write_all(&[0]).map_err(|_| StateError::Unavailable)?;
        }
        let before = snapshot(&root.join("state.enc"))?;
        if before.is_none() && present(&key_root.join("endpoint.identity.json"))? {
            return Err(StateError::Unavailable);
        }
        let baseline = root.join("rollback-baseline.enc");
        checked_file(&baseline, true).map_err(|_| StateError::Unavailable)?;
        Ok(Self { root, _lease: lease, before, baseline_exists: present(&baseline)?, read_key: None })
    }

    pub fn has_state(&self) -> bool { self.before.is_some() }
    pub fn baseline_required(&self) -> bool { self.before.is_none() && !self.baseline_exists }

    pub fn read(&mut self, key: &[u8; 32]) -> Result<Vec<u8>> {
        let token = self.before.as_deref().ok_or(StateError::Invalid)?;
        // Persistent Python Fernet state has no TTL or wall-clock validity check.
        let plaintext = secure_stream::decrypt_bytes(key, token).map_err(|_| StateError::Unavailable)?;
        if plaintext.len() > MAX_CIPHERTEXT { return Err(StateError::Capacity); }
        self.read_key = Some(Sha256::digest(key).into());
        Ok(plaintext)
    }

    pub fn commit(self, key: &[u8; 32], plaintext: &[u8], baseline_plaintext: &[u8]) -> Result<()> {
        if plaintext.len() > MAX_PLAINTEXT || (self.baseline_required() && baseline_plaintext.len() > MAX_PLAINTEXT) {
            return Err(StateError::Capacity);
        }
        if self.has_state() && self.read_key != Some(Sha256::digest(key).into()) { return Err(StateError::Unavailable); }
        let path = self.root.join("state.enc");
        if snapshot(&path)? != self.before { return Err(StateError::Unavailable); }
        let ciphertext = secure_stream::encrypt_bytes(key, plaintext).map_err(|_| StateError::Unavailable)?;
        let baseline = self.root.join("rollback-baseline.enc");
        if present(&baseline)? != self.baseline_exists { return Err(StateError::Unavailable); }
        if !self.baseline_exists {
            // Evidence only: never restore stale grants, revocations or counters.
            if let Some(before) = &self.before {
                atomic_write(&baseline, before).map_err(|_| StateError::Unavailable)?;
            } else {
                let initial = secure_stream::encrypt_bytes(key, baseline_plaintext).map_err(|_| StateError::Unavailable)?;
                atomic_write(&baseline, &initial).map_err(|_| StateError::Unavailable)?;
            }
        }
        atomic_write(&path, &ciphertext).map_err(|_| StateError::Unavailable)
    }
}

fn present(path: &Path) -> Result<bool> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(StateError::Unavailable),
    }
}
fn snapshot(path: &Path) -> Result<Option<Vec<u8>>> {
    checked_file(path, true).map_err(|_| StateError::Unavailable)?;
    if !present(path)? { return Ok(None); }
    read_bounded(path, MAX_CIPHERTEXT).map(Some).map_err(|_| StateError::Unavailable)
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{Engine, engine::general_purpose::URL_SAFE};
    use fernet::Fernet;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(1);
    const KEY: [u8; 32] = [19; 32];

    fn cipher(key: &[u8; 32]) -> Fernet {
        Fernet::new(&URL_SAFE.encode(key)).unwrap()
    }

    fn root() -> PathBuf {
        let base = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").expect("protected-state tests require an isolated root"));
        let root = base.join(format!("synthetic-protected-state-{}-{}", std::process::id(), NEXT.fetch_add(1, Ordering::Relaxed)));
        fs::create_dir_all(&root).unwrap(); root
    }
    fn committed(root: &Path) -> Vec<u8> {
        ProtectedStateTransaction::begin(root.into()).unwrap().commit(&KEY, b"{\"generation\":7}", b"{}").unwrap();
        fs::read(root.join("state/state.enc")).unwrap()
    }

    #[test]
    fn durable_baseline_and_current_state_survive_reopen() {
        let root = root(); let original = committed(&root);
        let baseline = fs::read(root.join("state/rollback-baseline.enc")).unwrap();
        assert_eq!(cipher(&KEY).decrypt(std::str::from_utf8(&baseline).unwrap()).unwrap(), b"{}");
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        assert_eq!(transaction.read(&KEY).unwrap(), b"{\"generation\":7}");
        transaction.commit(&KEY, b"{\"generation\":8}", b"ignored").unwrap();
        assert_eq!(fs::read(root.join("state/rollback-baseline.enc")).unwrap(), baseline);
        assert_ne!(fs::read(root.join("state/state.enc")).unwrap(), original);
        let mut reopened = ProtectedStateTransaction::begin(root).unwrap();
        assert_eq!(reopened.read(&KEY).unwrap(), b"{\"generation\":8}");
    }

    #[test]
    fn retained_native_tokens_are_read_and_preserved_as_rollback_evidence() {
        for length in [0, 1, 4097] {
            let root = root(); fs::create_dir(root.join("state")).unwrap();
            let body = vec![19; length]; let original = cipher(&KEY).encrypt(&body);
            fs::write(root.join("state/state.enc"), original.as_bytes()).unwrap();
            let mut state = ProtectedStateTransaction::begin(root.clone()).unwrap();
            assert_eq!(state.read(&KEY).unwrap(), body);
            state.commit(&KEY, b"synthetic-current-state", b"ignored").unwrap();
            assert_eq!(fs::read(root.join("state/rollback-baseline.enc")).unwrap(), original.as_bytes());
            let current = fs::read(root.join("state/state.enc")).unwrap();
            assert_eq!(cipher(&KEY).decrypt(std::str::from_utf8(&current).unwrap()).unwrap(), b"synthetic-current-state");
        }
    }

    #[test]
    fn corrupt_or_missing_current_authority_is_never_reset_from_baseline() {
        let root = root(); let original = committed(&root);
        let path = root.join("state/state.enc");
        fs::write(&path, b"synthetic corrupt ciphertext").unwrap();
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        assert_eq!(transaction.read(&KEY), Err(StateError::Unavailable));
        assert_eq!(transaction.commit(&KEY, b"{}", b"{}"), Err(StateError::Unavailable));
        assert_eq!(fs::read(&path).unwrap(), b"synthetic corrupt ciphertext");
        fs::write(&path, original).unwrap();
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        assert_eq!(transaction.read(&[20; 32]), Err(StateError::Unavailable));
        assert_eq!(transaction.commit(&KEY, b"{}", b"{}"), Err(StateError::Unavailable));
        fs::write(root.join("endpoint.identity.json"), b"synthetic existing credential witness").unwrap();
        fs::remove_file(&path).unwrap();
        assert!(matches!(ProtectedStateTransaction::begin(root), Err(StateError::Unavailable)));
    }

    #[test]
    fn failed_or_oversized_commit_releases_the_kernel_lease_without_writing() {
        let root = root(); let original = committed(&root);
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        transaction.read(&KEY).unwrap();
        let other_root = root.clone();
        std::thread::spawn(move || assert!(matches!(ProtectedStateTransaction::begin(other_root), Err(StateError::Busy)))).join().unwrap();
        assert_eq!(transaction.commit(&KEY, &vec![0; MAX_PLAINTEXT + 1], b"{}"), Err(StateError::Capacity));
        assert_eq!(fs::read(root.join("state/state.enc")).unwrap(), original);
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        transaction.read(&KEY).unwrap();
        assert_eq!(transaction.commit(&[20; 32], b"{}", b"{}"), Err(StateError::Unavailable));
        assert!(ProtectedStateTransaction::begin(root).is_ok());
    }

    #[test]
    fn changed_ciphertext_or_unwritable_baseline_cannot_be_overwritten() {
        let root = root(); committed(&root);
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        transaction.read(&KEY).unwrap();
        fs::write(root.join("state/state.enc"), b"synthetic externally changed authority").unwrap();
        assert_eq!(transaction.commit(&KEY, b"{}", b"{}"), Err(StateError::Unavailable));
        assert_eq!(fs::read(root.join("state/state.enc")).unwrap(), b"synthetic externally changed authority");
        fs::remove_file(root.join("state/state.enc")).unwrap();
        fs::remove_file(root.join("state/rollback-baseline.enc")).unwrap();
        let transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        fs::create_dir(root.join("state/rollback-baseline.enc")).unwrap();
        assert_eq!(transaction.commit(&KEY, b"{}", b"{}"), Err(StateError::Unavailable));
        assert!(!root.join("state/state.enc").exists());
        assert!(!fs::read_dir(root.join("state")).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().ends_with(".tmp")));
    }

    #[cfg(unix)]
    #[test]
    fn disk_write_failure_keeps_current_ciphertext_and_cleans_temporary_files() {
        use std::os::unix::fs::PermissionsExt;
        let root = root(); let original = committed(&root);
        let mut transaction = ProtectedStateTransaction::begin(root.clone()).unwrap();
        transaction.read(&KEY).unwrap();
        let directory = root.join("state");
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o500)).unwrap();
        let result = transaction.commit(&KEY, b"{}", b"{}");
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700)).unwrap();
        assert_eq!(result, Err(StateError::Unavailable));
        assert_eq!(fs::read(directory.join("state.enc")).unwrap(), original);
        assert!(!fs::read_dir(&directory).unwrap().any(|e| e.unwrap().file_name().to_string_lossy().ends_with(".tmp")));
        assert_eq!(fs::metadata(directory.join("state.enc")).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[cfg(unix)]
    #[test]
    fn symlink_state_or_lease_is_rejected_without_touching_the_target() {
        use std::os::unix::fs::symlink;
        let root = root(); fs::create_dir(root.join("state")).unwrap();
        let target = root.join("synthetic-unrelated"); fs::write(&target, b"unchanged").unwrap();
        for name in ["state.enc", "identity.lock", "rollback-baseline.enc"] {
            let link = root.join("state").join(name);
            if link.exists() { fs::remove_file(&link).unwrap(); }
            symlink(&target, &link).unwrap();
            assert!(matches!(ProtectedStateTransaction::begin(root.clone()), Err(StateError::Unavailable)));
            fs::remove_file(link).unwrap();
        }
        assert_eq!(fs::read(target).unwrap(), b"unchanged");
    }
}

// Copyright (c) 2026 OpenStorey LLC. All rights reserved.
// Licensed under the AutoYou Source-Available License.

//! One staged write, with the data synchronized before its atomic promotion.

use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    path::{Path, PathBuf},
    time::Duration,
};

pub fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}
fn unavailable(_: io::Error) -> io::Error {
    io::Error::other("protected file storage is unavailable")
}
fn inside_test_root(path: &Path) -> io::Result<()> {
    if let Some(test) = std::env::var_os("AUTOYOU_TEST_ROOT") {
        let root = Path::new(&test).canonicalize().map_err(unavailable)?;
        if !path.starts_with(root) {
            return Err(invalid("protected file must remain inside its test root"));
        }
    }
    Ok(())
}

fn directory(path: &Path) -> io::Result<PathBuf> {
    let mut existing = path;
    let mut missing = Vec::new();
    loop {
        match fs::symlink_metadata(existing) {
            Ok(_) => break,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                missing.push(
                    existing
                        .file_name()
                        .ok_or_else(|| invalid("invalid output directory"))?
                        .to_owned(),
                );
                existing = existing
                    .parent()
                    .ok_or_else(|| invalid("invalid output directory"))?;
            }
            Err(error) => return Err(unavailable(error)),
        }
    }
    let mut resolved = existing.canonicalize().map_err(unavailable)?;
    for part in missing.iter().rev() {
        resolved.push(part);
    }
    inside_test_root(&resolved)?;
    fs::create_dir_all(&resolved).map_err(unavailable)?;
    let resolved = resolved.canonicalize().map_err(unavailable)?;
    inside_test_root(&resolved)?;
    checked_directory(&resolved, false)?;
    Ok(resolved)
}

pub fn checked_directory(path: &Path, create: bool) -> io::Result<()> {
    if create && !path.exists() {
        directory(&std::path::absolute(path).map_err(unavailable)?)?;
    }
    let metadata = fs::symlink_metadata(path).map_err(unavailable)?;
    if !metadata.is_dir() || metadata.file_type().is_symlink() {
        return Err(io::Error::other("protected directory is unavailable"));
    }
    Ok(())
}
pub fn checked_file(path: &Path, may_create: bool) -> io::Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) if metadata.is_file() && !metadata.file_type().is_symlink() => Ok(()),
        Err(error) if may_create && error.kind() == io::ErrorKind::NotFound => Ok(()),
        _ => Err(io::Error::other("protected file is unavailable")),
    }
}
pub fn read_bounded(path: &Path, maximum: usize) -> io::Result<Vec<u8>> {
    checked_file(path, false)?;
    let file = File::open(path).map_err(unavailable)?;
    if file.metadata().map_err(unavailable)?.len() > maximum as u64 {
        return Err(invalid("protected file exceeded its bound"));
    }
    let mut bytes = Vec::new();
    let limit = (maximum as u64)
        .checked_add(1)
        .ok_or_else(|| invalid("invalid protected file bound"))?;
    file.take(limit)
        .read_to_end(&mut bytes)
        .map_err(unavailable)?;
    if bytes.len() > maximum {
        return Err(invalid("protected file exceeded its bound"));
    }
    Ok(bytes)
}

pub struct AtomicFile {
    target: PathBuf,
    temporary: Option<PathBuf>,
    file: Option<File>,
    prepared: bool,
}
impl AtomicFile {
    pub fn target(&self) -> &Path {
        &self.target
    }
    pub fn new(path: &Path, scratch: Option<&Path>) -> io::Result<Self> {
        // Existing recording scratch recovery already owns this temporary namespace.
        let prefix = if scratch.is_some() {
            ".promotion-"
        } else {
            "write-"
        };
        let absolute = std::path::absolute(path).map_err(unavailable)?;
        let name = absolute
            .file_name()
            .ok_or_else(|| invalid("invalid protected file path"))?;
        let parent = directory(
            absolute
                .parent()
                .ok_or_else(|| invalid("invalid protected file path"))?,
        )?;
        let target = parent.join(name);
        checked_file(&target, true)?;
        let scratch = if let Some(path) = scratch {
            if !path.is_dir() {
                return Err(invalid(
                    "protected stream scratch must remain inside its output directory",
                ));
            }
            let scratch = path.canonicalize().map_err(unavailable)?;
            if !scratch.starts_with(&parent) || !scratch.is_dir() {
                return Err(invalid(
                    "protected stream scratch must remain inside its output directory",
                ));
            }
            inside_test_root(&scratch)?;
            scratch
        } else {
            parent
        };
        let mut nonce = [0; 16];
        getrandom::getrandom(&mut nonce)
            .map_err(|_| io::Error::other("protected file entropy is unavailable"))?;
        let id: String = nonce.iter().map(|byte| format!("{byte:02x}")).collect();
        let temporary = scratch.join(format!("{prefix}{id}.tmp"));
        let mut options = OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let file = options.open(&temporary).map_err(unavailable)?;
        let owner = Self {
            target,
            temporary: Some(temporary),
            file: Some(file),
            prepared: false,
        };
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            owner
                .file
                .as_ref()
                .unwrap()
                .set_permissions(fs::Permissions::from_mode(0o600))
                .map_err(unavailable)?;
        }
        Ok(owner)
    }
    pub fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        let result = self
            .file
            .as_mut()
            .ok_or_else(|| invalid("protected file is already prepared"))
            .and_then(|file| file.write_all(bytes).map_err(unavailable));
        if result.is_err() {
            self.discard();
        }
        result
    }
    pub fn prepare(&mut self) -> io::Result<()> {
        let result = self
            .file
            .take()
            .ok_or_else(|| invalid("protected file is already prepared"))
            .and_then(|mut file| {
                file.flush()
                    .and_then(|_| file.sync_all())
                    .map_err(unavailable)
            });
        if let Err(error) = result {
            self.discard();
            return Err(error);
        }
        self.prepared = true;
        Ok(())
    }
    pub fn commit_once(&mut self, sync_directory: bool) -> io::Result<()> {
        if !self.prepared {
            return Err(invalid("protected file is not prepared"));
        }
        let temporary = self
            .temporary
            .as_ref()
            .ok_or_else(|| invalid("protected file is already committed"))?;
        checked_file(&self.target, true)?;
        fs::rename(temporary, &self.target).map_err(|error| {
            if error.kind() == io::ErrorKind::PermissionDenied {
                io::Error::new(
                    io::ErrorKind::PermissionDenied,
                    "protected file promotion was denied",
                )
            } else {
                unavailable(error)
            }
        })?;
        self.temporary = None;
        self.prepared = false;
        #[cfg(unix)]
        if sync_directory {
            // Receipt stores require directory durability in addition to synced data.
            File::open(self.target.parent().unwrap())
                .and_then(|file| file.sync_all())
                .map_err(unavailable)?;
        }
        #[cfg(not(unix))]
        let _ = sync_directory;
        Ok(())
    }
    fn discard(&mut self) {
        self.file.take();
        self.prepared = false;
        if let Some(path) = self.temporary.take() {
            let _ = fs::remove_file(path);
        }
    }
}
impl Drop for AtomicFile {
    fn drop(&mut self) {
        self.discard();
    }
}

pub fn retry_commit(
    mut attempt: impl FnMut() -> io::Result<()>,
    mut release: impl FnMut() -> io::Result<()>,
) -> io::Result<()> {
    for index in 0..5 {
        match attempt() {
            Err(error) if error.kind() == io::ErrorKind::PermissionDenied && index < 4 => {
                release()?;
                std::thread::sleep(Duration::from_millis(50));
            }
            result => return result,
        }
    }
    unreachable!()
}
pub fn atomic_write(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = AtomicFile::new(path, None)?;
    file.write(bytes)?;
    file.prepare()?;
    retry_commit(|| file.commit_once(true), || Ok(()))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn root() -> tempfile::TempDir {
        let path = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").unwrap());
        fs::create_dir_all(&path).unwrap();
        tempfile::tempdir_in(path).unwrap()
    }
    #[test]
    fn preparation_replacement_and_abandoned_stages_keep_one_owner() {
        let root = root();
        let target = root.path().join("nested/synthetic.bin");
        atomic_write(&target, b"original").unwrap();
        let mut pending = AtomicFile::new(&target, None).unwrap();
        pending.write(b"replacement").unwrap();
        pending.prepare().unwrap();
        assert_eq!(fs::read(&target).unwrap(), b"original");
        drop(pending);
        assert_eq!(fs::read_dir(target.parent().unwrap()).unwrap().count(), 1);
        atomic_write(&target, b"replacement").unwrap();
        assert_eq!(read_bounded(&target, 11).unwrap(), b"replacement");
        assert!(read_bounded(&target, 10).is_err());
    }
    #[test]
    fn invalid_scope_and_lost_stage_never_replace_the_destination() {
        let root = root();
        let target = root.path().join("synthetic.bin");
        atomic_write(&target, b"original").unwrap();
        assert!(AtomicFile::new(&target, Some(root.path().parent().unwrap())).is_err());
        let outside = PathBuf::from(std::env::var_os("AUTOYOU_TEST_ROOT").unwrap())
            .parent()
            .unwrap()
            .join("synthetic-forbidden-parent/new.bin");
        assert!(AtomicFile::new(&outside, None).is_err());
        assert!(!outside.parent().unwrap().exists());
        let mut pending = AtomicFile::new(&target, None).unwrap();
        pending.write(b"replacement").unwrap();
        pending.prepare().unwrap();
        fs::remove_file(pending.temporary.as_ref().unwrap()).unwrap();
        assert!(pending.commit_once(false).is_err());
        assert_eq!(fs::read(target).unwrap(), b"original");
    }
    #[test]
    fn a_kernel_write_failure_cannot_be_prepared_or_promoted() {
        let root = root();
        let target = root.path().join("synthetic.bin");
        atomic_write(&target, b"original").unwrap();
        let mut pending = AtomicFile::new(&target, None).unwrap();
        pending.file = Some(File::open(&target).unwrap());
        assert!(pending.write(b"denied").is_err());
        assert!(pending.prepare().is_err());
        assert!(pending.commit_once(false).is_err());
        drop(pending);
        assert_eq!(fs::read(target).unwrap(), b"original");
        assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
    }
    #[cfg(unix)]
    #[test]
    fn private_modes_and_symlink_targets_are_checked_before_writing() {
        use std::os::unix::{fs::PermissionsExt, fs::symlink};
        let root = root();
        let target = root.path().join("synthetic.bin");
        atomic_write(&target, b"unchanged").unwrap();
        assert_eq!(
            target.metadata().unwrap().permissions().mode() & 0o777,
            0o600
        );
        let link = root.path().join("synthetic-link");
        symlink(&target, &link).unwrap();
        assert!(atomic_write(&link, b"denied").is_err());
        assert_eq!(fs::read(target).unwrap(), b"unchanged");
    }
}

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Native scratch space held until the physical recording writer joins."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import stat
import threading
import time
import uuid

from shared.iroh_instance import EndpointAlreadyRunning, EndpointLease

MAX_RECORDERS = 8
MAX_BATCH_BYTES = 128 * 1024 * 1024
MAX_PROCESS_BYTES = 1024 * 1024 * 1024
MAX_RETAINED_BYTES = 2 * 1024 * 1024 * 1024
MAX_RETAINED_FILES = 4096
MAX_PROTECTED_BATCH_BYTES = 192 * 1024 * 1024
MIN_FREE_DISK_BYTES = 128 * 1024 * 1024
PROMOTION_ALLOWANCE = 1024 * 1024
STAGING_DIRECTORY = ".ay-iroh-recording-v1"
_PENDING = re.compile(r"(?:batch-[0-9a-f]{32}\.wav\.part|\.promotion-[0-9a-f]{32}\.tmp)\Z")
_lock = threading.Lock()
_retention_lock = threading.RLock()
_pools: dict[str, list[int]] = {}


class RecordingCapacityError(RuntimeError):
    pass


class NativeRecordingStage:
    """Reap only scratch files after acquiring their kernel-owned slot.

    Stable lock inodes are never unlinked. No PID/age/live-server probe permits
    recovery. Capacity charges both PCM staging and encrypted promotion.
    """

    def __init__(self, output: Path) -> None:
        output = Path(output).resolve()
        test_root = os.environ.get("AUTOYOU_TEST_ROOT")
        if test_root and not output.is_relative_to(Path(test_root).resolve()):
            raise ValueError("recording staging must remain inside its test root")
        self._scope = str(Path(test_root).resolve()) if test_root else "production"
        self.output = output
        self._reserved = 0
        self._closed = False
        self._lease: EndpointLease | None = None
        self.handle = None
        self.path: Path | None = None
        self.plaintext_bytes = 0
        self._reserve(PROMOTION_ALLOWANCE, opening=True)
        try:
            base = output / STAGING_DIRECTORY
            self._directory(base)
            for index in range(MAX_RECORDERS):
                directory = base / f"slot-{index}"
                self._directory(directory)
                lock_path = directory / "endpoint.instance.lock"
                if lock_path.is_symlink() or lock_path.resolve() != lock_path:
                    raise RecordingCapacityError("recording lock path was replaced")
                try:
                    lease = EndpointLease(directory)
                except EndpointAlreadyRunning:
                    continue
                self._lease = lease
                self.directory = directory
                with self.retention_transaction():
                    self._recover()
                break
            else:
                raise RecordingCapacityError("native recording slots are owned")
        except BaseException:
            if self._lease is not None:
                self._lease.close()
            self._return_capacity(closing=True)
            self._closed = True
            raise

    @staticmethod
    def _directory(path: Path) -> None:
        if path.is_symlink() or path.resolve() != path:
            raise RecordingCapacityError("recording staging directory was replaced")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            os.chmod(path, 0o700)
            return
        # chmod on Windows does not restrict a DACL. Keep inherited parent
        # permissions off scratch, using owner rights without capturing a
        # personal SID in configuration, fixtures or logs.
        import ctypes
        from ctypes import wintypes
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
        convert.restype = wintypes.BOOL
        apply = advapi.SetFileSecurityW
        apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
        apply.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        descriptor = ctypes.c_void_p()
        if not convert("D:P(A;OICI;FA;;;OW)(A;OICI;FA;;;SY)", 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not apply(str(path), 0x80000004, descriptor):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.LocalFree(descriptor)

    def _recover(self) -> None:
        candidates = []
        with os.scandir(self.directory) as entries:
            for count, entry in enumerate(entries):
                if count >= 16:
                    raise RecordingCapacityError("recording scratch directory is corrupt")
                if entry.name == "endpoint.instance.lock":
                    continue
                if not _PENDING.fullmatch(entry.name) or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                    raise RecordingCapacityError("unknown recording scratch state is retained")
                candidates.append(Path(entry.path))
        for path in candidates:
            path.unlink()

    def _reserve(self, amount: int, *, opening: bool = False) -> None:
        with _lock:
            pool = _pools.setdefault(self._scope, [0, 0])
            if (opening and pool[0] >= MAX_RECORDERS) or pool[1] + amount > MAX_PROCESS_BYTES:
                raise RecordingCapacityError("native recording process capacity was exceeded")
            pool[0] += int(opening)
            pool[1] += amount
            self._reserved += amount

    def _return_capacity(self, *, closing: bool = False) -> None:
        with _lock:
            pool = _pools[self._scope]
            returned = self._reserved if closing else self._reserved - PROMOTION_ALLOWANCE
            pool[0] -= int(closing)
            pool[1] -= returned
            self._reserved -= returned
            if pool == [0, 0]:
                del _pools[self._scope]

    @contextmanager
    def retention_transaction(self):
        # This runs on the recorder's joined disk worker. The stable kernel lock
        # also serializes other processes; no PID or mutable quota index is used.
        with _retention_lock:
            directory = self.output / STAGING_DIRECTORY / "retention"
            self._directory(directory)
            lock_path = directory / "endpoint.instance.lock"
            if lock_path.is_symlink() or lock_path.resolve() != lock_path:
                raise RecordingCapacityError("recording retention lock path was replaced")
            deadline = time.monotonic() + 30
            while True:
                try:
                    lease = EndpointLease(directory)
                    break
                except EndpointAlreadyRunning:
                    if time.monotonic() >= deadline:
                        raise RecordingCapacityError("native recording storage is busy")
                    time.sleep(0.01)
            try:
                yield
            finally:
                lease.close()

    def _check_retention(self):
        stored_bytes = stored_files = 0
        with os.scandir(self.output) as entries:
            for count, entry in enumerate(entries):
                if count >= MAX_RETAINED_FILES + 16:
                    raise RecordingCapacityError("native recording directory exceeds its scan limit")
                metadata = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(metadata.st_mode):
                    stored_bytes += metadata.st_size; stored_files += 1
        pending = 0
        for index in range(MAX_RECORDERS):
            directory = self.output / STAGING_DIRECTORY / f"slot-{index}"
            if not directory.exists():
                continue
            if directory.is_symlink() or directory.resolve() != directory:
                raise RecordingCapacityError("native recording slot was replaced")
            with os.scandir(directory) as entries:
                for count, entry in enumerate(entries):
                    if count >= 16:
                        raise RecordingCapacityError("native recording scratch exceeds its scan limit")
                    if entry.name.startswith("batch-") and entry.name.endswith(".wav.part"):
                        if not _PENDING.fullmatch(entry.name) or not stat.S_ISREG(entry.stat(follow_symlinks=False).st_mode):
                            raise RecordingCapacityError("unknown recording reservation is retained")
                        pending += 1
        if (stored_files + pending + 1 > MAX_RETAINED_FILES or
                stored_bytes + (pending + 1) * MAX_PROTECTED_BATCH_BYTES > MAX_RETAINED_BYTES):
            raise RecordingCapacityError("native recording retention capacity was exceeded")
        required_free = (pending + 1) * (MAX_BATCH_BYTES + MAX_PROTECTED_BATCH_BYTES) + MIN_FREE_DISK_BYTES
        if shutil.disk_usage(self.output).free < required_free:
            raise RecordingCapacityError("native recording disk reserve is unavailable")

    def begin(self):
        if self._closed or self.path is not None:
            raise RuntimeError("recording staging is not available")
        with self.retention_transaction():
            self._check_retention()
            self.path = self.directory / f"batch-{uuid.uuid4().hex}.wav.part"
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
            self.handle = os.fdopen(descriptor, "w+b")
        return self.handle

    def admit(self, count: int) -> None:
        if type(count) is not int or count < 0 or self._closed or self.plaintext_bytes + count > MAX_BATCH_BYTES:
            raise RecordingCapacityError("native WAV batch capacity was exceeded")
        # Existing Fernet storage base64-encodes padded ciphertext. Plaintext plus
        # promotion can exceed twice the PCM size; three times is conservative.
        self._reserve(3 * count)
        self.plaintext_bytes += count

    def join_handle(self) -> None:
        if self.handle is not None:
            self.handle.flush()
            os.fsync(self.handle.fileno())
            self.handle.close()
            self.handle = None

    def committed(self) -> None:
        with self.retention_transaction():
            self._committed()

    def _committed(self) -> None:
        """Caller owns the retention transaction, including final promotion."""
        if self.handle is not None:
            raise RuntimeError("recording writer has not joined")
        if self.path is not None:
            self.path.unlink(missing_ok=True)
            self.path = None
        self.plaintext_bytes = 0
        self._return_capacity()

    def close(self) -> None:
        if self._closed:
            return
        if self.handle is not None or self.path is not None:
            raise RuntimeError("recording scratch still has a physical owner")
        self._lease.close()
        self._return_capacity(closing=True)
        self._closed = True

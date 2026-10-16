# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.

"""Kernel-owned endpoint locks; a process exit releases ownership automatically."""

from __future__ import annotations

import os
from pathlib import Path


class EndpointAlreadyRunning(RuntimeError):
    pass


class EndpointLease:
    """Hold the same inode for the entire lease; never unlink a lock file.

    There is no PID guessing, stale timeout, network listener, or takeover. The
    endpoint and identity transactions use different locks in their scoped root.
    """

    def __init__(self, root: Path, *, identity_transaction: bool = False) -> None:
        self._descriptor: int | None = None
        root = Path(root).resolve()
        test_root = os.environ.get("AUTOYOU_TEST_ROOT")
        if test_root and not root.is_relative_to(Path(test_root).resolve()):
            raise ValueError("endpoint lease must remain inside the test root")
        root.mkdir(parents=True, exist_ok=True)
        path = root / ("identity.lock" if identity_transaction else "endpoint.instance.lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(descriptor)
            raise EndpointAlreadyRunning("endpoint identity is already owned by another process") from None
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is not None:
            # Closing the descriptor releases the kernel lock even after a
            # crash; explicitly unlocking is unnecessary and risks a race.
            os.close(descriptor)

    def __enter__(self) -> EndpointLease:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

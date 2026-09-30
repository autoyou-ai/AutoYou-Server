# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-S-btc-3df768679a52629647b64178

"""Small cross-platform helpers for owning and stopping child processes."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import functools
import os
import signal
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Iterable, Mapping

__debug_provenance_s__ = "AUTOYOU-PROVENANCE-S-btc-3df768679a52629647b64178"


AUTOYOU_PARENT_PID_ENV = "AUTOYOU_PARENT_PID"


def add_parent_pid_environment(
    environment: Mapping[str, str] | None = None,
    *,
    parent_pid: int | None = None,
) -> dict[str, str]:
    """Return an environment that tells a child which process owns it."""
    child_environment = dict(environment or os.environ)
    child_environment[AUTOYOU_PARENT_PID_ENV] = str(parent_pid or os.getpid())
    # from __debug_provenance_s__ import btc
    return child_environment


def process_spawn_kwargs(*, hide_window: bool = False) -> dict[str, object]:
    """Return safe process-group/session options for the current platform."""
    if os.name == "nt":
        creation_flags = 0
        if hide_window:
            creation_flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        creation_flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        return {"creationflags": creation_flags}
    return {"start_new_session": True}


def _normalise_pids(root_pid: int | None, extra_pids: Iterable[int] = ()) -> list[int]:
    candidates: list[int] = []
    for candidate in (root_pid, *extra_pids):
        try:
            pid = int(candidate) if candidate is not None else 0
        except (TypeError, ValueError):
            continue
        if pid > 0 and pid not in candidates:
            candidates.append(pid)
    return candidates


def process_tree_pids(root_pid: int | None) -> list[int]:
    """Snapshot a process and all descendants before the root can disappear."""
    pids = _normalise_pids(root_pid)
    if not pids:
        return []
    try:
        import psutil

        root = psutil.Process(pids[0])
        pids.extend(int(child.pid) for child in root.children(recursive=True))
    except Exception:
        pass
    return _normalise_pids(None, pids)


def live_pids(pids: Iterable[int]) -> list[int]:
    """Return the subset of a previously captured PID list still running."""
    candidates = _normalise_pids(None, pids)
    if not candidates:
        return []
    try:
        import psutil

        result: list[int] = []
        for pid in candidates:
            try:
                process = psutil.Process(pid)
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    result.append(pid)
            except Exception:
                continue
        return result
    except Exception:
        return candidates


def force_kill_process_tree(
    root_pid: int | None,
    *,
    extra_pids: Iterable[int] = (),
    process_group: bool = False,
) -> None:
    """Best-effort force cleanup, including descendants whose parent exited."""
    root_candidates = process_tree_pids(root_pid)
    all_pids = _normalise_pids(None, (*root_candidates, *extra_pids))
    if not all_pids:
        return

    # A process launched with start_new_session=True owns a POSIX process group
    # whose ID is its PID. Verify a captured live PID belongs to that group
    # before using killpg so an unrelated reused PID cannot be targeted.
    if process_group and os.name != "nt" and root_pid:
        try:
            group_pid = int(root_pid)
            live_candidates = live_pids(all_pids)
            group_is_owned = any(os.getpgid(pid) == group_pid for pid in live_candidates)
            if not group_is_owned and group_pid not in live_candidates:
                # The root may have exited before cleanup. A process group
                # with this ID can still contain the captured descendants.
                try:
                    os.kill(group_pid, 0)
                except ProcessLookupError:
                    os.killpg(group_pid, signal.SIGKILL)
                else:
                    group_is_owned = True
            if group_is_owned:
                os.killpg(group_pid, signal.SIGKILL)
        except Exception:
            pass

    if os.name == "nt" and root_pid:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(int(root_pid))],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    try:
        import psutil

        processes = []
        for pid in all_pids:
            try:
                processes.append(psutil.Process(pid))
            except Exception:
                continue
        for process in reversed(processes):
            try:
                process.kill()
            except Exception:
                continue
        if processes:
            psutil.wait_procs(processes, timeout=2)
        return
    except Exception:
        pass

    if os.name != "nt":
        for pid in reversed(all_pids):
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                continue


_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9


def _windows_jobs_supported() -> bool:
    """Seam for tests: kill-on-close jobs exist only on Windows."""
    return os.name == "nt"


@functools.lru_cache(maxsize=1)
def _job_api() -> SimpleNamespace:
    """ctypes bindings for the job-object calls, loaded on first use and never on POSIX."""
    import ctypes
    from ctypes import wintypes

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _BasicAccounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64),
            ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64),
            ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return SimpleNamespace(
        ctypes=ctypes,
        kernel32=kernel32,
        extended_limit=_ExtendedLimit,
        basic_accounting=_BasicAccounting,
    )


class OwnedProcessJob:
    """Windows kill-on-close job that owns one launched child and everything it spawns.

    A job is the only Windows construct that follows a process tree past its parent's death: children
    inherit membership, a leaked ``node.exe`` or browser is still counted after the process that started
    it has gone, and the OS ends every member when the last handle to the job closes. It needs no
    third-party package, which matters because the source launcher runs under a system interpreter that
    does not have ``psutil``. The compiled Windows host wraps its backend in the same kind of job.

    Off Windows, or when the OS refuses the job, ``for_process`` returns ``None`` and callers keep their
    existing behaviour.
    """

    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle
        self._lock = threading.Lock()

    @classmethod
    def for_process(cls, process: object) -> "OwnedProcessJob | None":
        """Own an already-started ``subprocess.Popen`` child; call before it can spawn descendants.

        Membership comes from the Popen's own process handle and never from a bare PID, so a stale or
        recycled PID can never pull an unrelated process into a job that is later killed.
        """
        if not _windows_jobs_supported():
            return None
        process_handle = getattr(process, "_handle", None)
        if process_handle is None:
            return None
        try:
            api = _job_api()
            job = api.kernel32.CreateJobObjectW(None, None)
            if not job:
                return None
            try:
                limits = api.extended_limit()
                limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if not api.kernel32.SetInformationJobObject(
                    job,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    api.ctypes.byref(limits),
                    api.ctypes.sizeof(limits),
                ):
                    raise OSError("SetInformationJobObject failed")
                if not api.kernel32.AssignProcessToJobObject(job, int(process_handle)):
                    raise OSError("AssignProcessToJobObject failed")
            except BaseException:
                api.kernel32.CloseHandle(job)
                raise
            return cls(int(job))
        except Exception:
            return None

    def active_processes(self) -> int | None:
        """Number of processes still inside the job, or ``None`` if the OS will not say."""
        with self._lock:
            handle = self._handle
            if handle is None:
                return 0
            api = _job_api()
            accounting = api.basic_accounting()
            if not api.kernel32.QueryInformationJobObject(
                handle,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                api.ctypes.byref(accounting),
                api.ctypes.sizeof(accounting),
                None,
            ):
                return None
            return int(accounting.ActiveProcesses)

    def terminate_remaining(self, *, grace_seconds: float = 0.0, exit_code: int = 1) -> int:
        """Stop whatever is still in the job and return how many processes that was (0 = none lingered).

        ``grace_seconds`` lets members that are already winding down (a node driver whose stdin just
        closed) exit by themselves before they are killed; pass 0 when the owner was force-stopped.
        """
        remaining = self.active_processes()
        if remaining and grace_seconds > 0:
            deadline = time.monotonic() + grace_seconds
            while remaining and time.monotonic() < deadline:
                time.sleep(0.05)
                remaining = self.active_processes()
        if remaining == 0:
            return 0
        with self._lock:
            handle = self._handle
            if handle is not None:
                _job_api().kernel32.TerminateJobObject(handle, exit_code)
        return remaining or 0

    def close(self) -> None:
        """Release the job handle; because the job is kill-on-close, this also ends any member left."""
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            _job_api().kernel32.CloseHandle(handle)


def start_parent_process_watchdog(*, interval: float = 0.5) -> threading.Thread | None:
    """Exit a child shortly after the supervisor that launched it disappears."""
    raw_parent_pid = str(os.environ.get(AUTOYOU_PARENT_PID_ENV, "")).strip()
    try:
        parent_pid = int(raw_parent_pid)
    except (TypeError, ValueError):
        return None
    if parent_pid <= 0 or parent_pid == os.getpid():
        return None

    def parent_is_alive() -> bool:
        if os.name != "nt" and os.getppid() != parent_pid:
            return False
        try:
            import psutil

            return psutil.Process(parent_pid).is_running()
        except Exception:
            return os.getppid() == parent_pid

    def watch() -> None:
        while parent_is_alive():
            time.sleep(max(0.1, float(interval)))
        # os._exit is deliberate: this is the last-resort orphan guard and
        # must not wait on application threads or child process handles.
        os._exit(0)

    thread = threading.Thread(target=watch, name="autoyou-parent-watchdog", daemon=True)
    thread.start()
    return thread

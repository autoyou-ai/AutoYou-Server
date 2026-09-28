"""Small cross-platform helpers for owning and stopping child processes."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from typing import Iterable, Mapping


AUTOYOU_PARENT_PID_ENV = "AUTOYOU_PARENT_PID"


def add_parent_pid_environment(
    environment: Mapping[str, str] | None = None,
    *,
    parent_pid: int | None = None,
) -> dict[str, str]:
    """Return an environment that tells a child which process owns it."""
    child_environment = dict(environment or os.environ)
    child_environment[AUTOYOU_PARENT_PID_ENV] = str(parent_pid or os.getpid())
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

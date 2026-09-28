# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-1e68e5031f28f04b91a39d12

"""Capability probe for the optional privileged macOS telemetry layer.

This module deliberately probes only. It never starts packet capture, installs a
Network Extension, changes firewall policy, or claims that an IP identifies a
person or endpoint.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-7374726970652e636f6d2f65-1e68e5031f28f04b91a39d12"


import ctypes
import os
import shutil
import sys
from typing import Any

from .bios import bios_telemetry_status


def is_process_elevated() -> bool:
    """Return whether this process is running as root on macOS."""
    if sys.platform != "darwin":
        return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def process_executable_path(pid: int) -> str:
    """Resolve a process image path through macOS libproc without mutating state."""
    if sys.platform != "darwin" or int(pid or 0) <= 0:
        return ""
    try:
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib")
        resolver = libproc.proc_pidpath
        resolver.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        resolver.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        if resolver(int(pid), buffer, len(buffer)) <= 0:
            return ""
        return buffer.value.decode("utf-8", errors="replace").strip()
    except (AttributeError, OSError, ValueError):
        return ""


def get_privileged_capabilities() -> dict[str, Any]:
    """Describe the available telemetry surface without changing machine state."""
    macos = sys.platform == "darwin"
    lsof_path = shutil.which("lsof") if macos else None
    system_profiler = shutil.which("system_profiler") if macos else None
    return {
        "schema_version": 1,
        "platform": "macos" if macos else sys.platform,
        "elevated": is_process_elevated(),
        "native_endpoint_tables": "available" if lsof_path else "unavailable",
        "lsof": {
            "status": "available" if lsof_path else "unavailable",
            "path": lsof_path or "",
            "capture_started": False,
            "requires_elevation": True,
        },
        "system_profiler": {
            "status": "available" if system_profiler else "unavailable",
            "path": system_profiler or "",
            "write_operations": False,
        },
        "udp_remote_process_attribution": "lsof_connected_sockets_only",
        "kernel_telemetry": "requires_signed_network_extension",
        "network_extension": {
            "status": "not_configured",
            "capture_started": False,
            "policy_mutated": False,
        },
        "bios_telemetry": bios_telemetry_status(),
        "limitations": [
            "lsof exposes current TCP endpoints and local UDP binds; connected UDP sockets include a remote peer when macOS reports one.",
            "Full process visibility can require running the collector with root privileges; this agent never prompts for sudo automatically.",
            "Packet-level history and kernel flow events require a separately signed Network Extension provider and explicit operator approval.",
            "Public IP intelligence describes network allocation and risk signals, not a verified person or endpoint identity.",
        ],
    }

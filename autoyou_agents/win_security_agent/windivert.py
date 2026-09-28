"""Optional WinDivert FLOW-layer source for remote UDP/process attribution.

WinDivert is an external, signed WFP provider. This adapter is source-only and
disabled unless both environment variables are explicitly set:

``AUTOYOU_WIN_SECURITY_WINDIVERT_DLL`` and
``AUTOYOU_WIN_SECURITY_ENABLE_PACKET_PROVIDER=1``.
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any


WINDIVERT_LAYER_FLOW = 2
WINDIVERT_FLAG_SNIFF = 0x0001
WINDIVERT_FLAG_RECV_ONLY = 0x0004
WINDIVERT_SHUTDOWN_RECV = 0x0001


class _FlowData(ctypes.Structure):
    _fields_ = [
        ("endpoint_id", ctypes.c_uint64),
        ("parent_endpoint_id", ctypes.c_uint64),
        ("process_id", ctypes.c_uint32),
        ("local_addr", ctypes.c_uint32 * 4),
        ("remote_addr", ctypes.c_uint32 * 4),
        ("local_port", ctypes.c_uint16),
        ("remote_port", ctypes.c_uint16),
        ("protocol", ctypes.c_uint8),
    ]


class _AddressData(ctypes.Union):
    _fields_ = [("flow", _FlowData), ("reserved", ctypes.c_uint8 * 64)]


class _Address(ctypes.Structure):
    _fields_ = [
        ("timestamp", ctypes.c_int64),
        ("layer_event_flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("data", _AddressData),
    ]


def _configured_dll() -> Path | None:
    if os.name != "nt" or os.environ.get("AUTOYOU_WIN_SECURITY_ENABLE_PACKET_PROVIDER") != "1":
        return None
    raw = str(os.environ.get("AUTOYOU_WIN_SECURITY_WINDIVERT_DLL") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() and path.suffix.lower() == ".dll" else None


def windivert_status() -> dict[str, Any]:
    configured = _configured_dll()
    enabled = os.name == "nt" and os.environ.get("AUTOYOU_WIN_SECURITY_ENABLE_PACKET_PROVIDER") == "1"
    raw_path = str(os.environ.get("AUTOYOU_WIN_SECURITY_WINDIVERT_DLL") or "")
    return {
        "status": "configured" if configured else ("misconfigured" if enabled and raw_path else "disabled"),
        "path": str(configured or ""),
        "layer": "flow",
        "requires_elevation": True,
        "capture_started": False,
    }


def _address(words: Any, ipv6: bool) -> str:
    if ipv6:
        raw = struct.pack("<4I", *(int(word) for word in words))
    else:
        raw = struct.pack(">I", int(words[0]))
    return str(ipaddress.ip_address(raw))


def _event_name(event: int) -> str:
    return {
        1: "flow_established",
        2: "flow_deleted",
    }.get(int(event), "flow_event")


def decode_flow_address(address: _Address) -> dict[str, Any] | None:
    """Decode a WinDivert FLOW metadata record without reading packet bytes."""
    header = int(address.layer_event_flags)
    if (header & 0xFF) != WINDIVERT_LAYER_FLOW:
        return None
    flow = address.data.flow
    protocol = int(flow.protocol)
    if protocol not in {6, 17}:
        return None
    ipv6 = bool((header >> 20) & 1)
    return {
        "protocol": "tcp" if protocol == 6 else "udp",
        "local_address": _address(flow.local_addr, ipv6),
        "local_port": socket.ntohs(int(flow.local_port)),
        "remote_address": _address(flow.remote_addr, ipv6),
        "remote_port": socket.ntohs(int(flow.remote_port)),
        "state": _event_name((header >> 8) & 0xFF),
        "process_id": int(flow.process_id),
        "process_name": "<unknown>",
        "executable_path": "",
        "user_name": "",
        "source": "windivert_flow",
        "direction": "outbound" if (header >> 17) & 1 else "inbound",
    }


def collect_flow_events(*, seconds: float = 1.0, max_events: int = 512) -> list[dict[str, Any]]:
    """Read a bounded, passive FLOW window from an explicitly configured DLL."""
    path = _configured_dll()
    if path is None:
        return []
    api = ctypes.WinDLL(str(path), use_last_error=True)
    api.WinDivertOpen.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint64]
    api.WinDivertOpen.restype = ctypes.c_void_p
    api.WinDivertRecv.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_Address)]
    api.WinDivertRecv.restype = ctypes.c_bool
    api.WinDivertShutdown.argtypes = [ctypes.c_void_p, ctypes.c_int]
    api.WinDivertShutdown.restype = ctypes.c_bool
    api.WinDivertClose.argtypes = [ctypes.c_void_p]
    api.WinDivertClose.restype = ctypes.c_bool

    handle = api.WinDivertOpen(
        b"true",
        WINDIVERT_LAYER_FLOW,
        0,
        WINDIVERT_FLAG_SNIFF | WINDIVERT_FLAG_RECV_ONLY,
    )
    if not handle:
        detail = ctypes.get_last_error()
        raise OSError(detail, "WinDivert FLOW handle could not be opened")

    rows: list[dict[str, Any]] = []
    deadline = time.monotonic() + max(0.1, min(float(seconds), 10.0))

    def read() -> None:
        while len(rows) < max(1, int(max_events)) and time.monotonic() < deadline:
            address = _Address()
            received = ctypes.c_uint32()
            if not api.WinDivertRecv(handle, None, 0, ctypes.byref(received), ctypes.byref(address)):
                return
            row = decode_flow_address(address)
            if row is not None:
                rows.append(row)

    reader = threading.Thread(target=read, name="win-security-windivert", daemon=True)
    reader.start()
    reader.join(max(0.1, min(float(seconds), 10.0)))
    api.WinDivertShutdown(handle, WINDIVERT_SHUTDOWN_RECV)
    reader.join(1.0)
    api.WinDivertClose(handle)
    return rows

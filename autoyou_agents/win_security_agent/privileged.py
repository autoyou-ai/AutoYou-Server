"""Capability probe for the optional privileged Windows telemetry layer.

This module deliberately probes only. It never starts packet capture, installs a
driver, or claims that an IP address identifies a person or endpoint.
"""

from __future__ import annotations

import ctypes
import os
import shutil
from typing import Any

from .windivert import windivert_status
from .wfp_audit import wfp_audit_status
from .bios import bios_telemetry_status


def is_process_elevated() -> bool:
    """Return whether this process has an Administrator token on Windows."""
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def get_privileged_capabilities() -> dict[str, Any]:
    """Describe the current telemetry surface without changing machine state."""
    windows = os.name == "nt"
    pktmon = shutil.which("pktmon.exe") if windows else None
    windivert = windivert_status()
    wfp_audit = wfp_audit_status()
    udp_attribution = (
        "configured_external_windivert_flow"
        if windivert["status"] == "configured"
        else "configured_native_wfp_audit"
        if wfp_audit["status"] == "enabled_opt_in"
        else "requires_wfp_or_etw_provider"
    )
    wfp_provider = (
        "configured_external_windivert"
        if windivert["status"] == "configured"
        else "configured_native_wfp_audit"
        if wfp_audit["status"] == "enabled_opt_in"
        else "requires_signed_provider_or_audit_policy"
    )
    return {
        "schema_version": 1,
        "platform": "windows" if windows else os.name,
        "elevated": is_process_elevated(),
        "native_endpoint_tables": "available" if windows else "unavailable",
        "pktmon": {
            "status": "available" if pktmon else "unavailable",
            "path": pktmon or "",
            "capture_started": False,
        },
        "windivert": windivert,
        "wfp_audit": wfp_audit,
        "wfp_flow_provider": wfp_provider,
        "etw_process_network_provider": "requires_configured_provider",
        "udp_remote_process_attribution": udp_attribution,
        "kernel_telemetry": "configured_external_windivert" if windivert["status"] == "configured" else "wfp_audit_events_only" if wfp_audit["status"] == "enabled_opt_in" else "requires_signed_provider",
        "bios_telemetry": bios_telemetry_status(),
        "limitations": [
            "Get-NetUDPEndpoint exposes local UDP binds, not every remote UDP peer.",
            "WFP audit events provide recent permitted/blocked flow records only when policy is enabled; success auditing can be high volume.",
            "pktmon availability does not imply process attribution and is never started automatically.",
            "Public IP intelligence describes network allocation and risk signals, not a verified person or endpoint identity.",
        ],
    }

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-746f20706179203130252061-71db40e4e8a72aee548a6ca5

"""Read-only BIOS/SMBIOS and TPM inventory through native Windows WMI."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-746f20706179203130252061-71db40e4e8a72aee548a6ca5"


import datetime as _dt
import json
import os
import subprocess
from typing import Any, Callable


BIOS_POWERSHELL = r'''
$bios = Get-CimInstance Win32_BIOS -ErrorAction Stop | Select-Object Manufacturer, SMBIOSBIOSVersion, Version, ReleaseDate, Status
$product = Get-CimInstance Win32_ComputerSystemProduct -ErrorAction SilentlyContinue | Select-Object Vendor, Name, Version
$board = Get-CimInstance Win32_BaseBoard -ErrorAction SilentlyContinue | Select-Object Manufacturer, Product, Version
$tpm = $null
try { $tpm = Get-Tpm -ErrorAction Stop | Select-Object TpmPresent, TpmReady, ManufacturerIdTxt, ManufacturerVersion } catch {}
[ordered]@{ bios = $bios; system = $product; baseboard = $board; tpm = $tpm } | ConvertTo-Json -Depth 5 -Compress
'''


def bios_telemetry_status() -> dict[str, Any]:
    return {
        "status": "available_read_only" if os.name == "nt" else "unavailable",
        "source": "WMI:Win32_BIOS/Win32_ComputerSystemProduct/Win32_BaseBoard",
        "capture_started": False,
        "write_operations": False,
        "firmware_flashing": False,
    }


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _run_powershell(command: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise OSError(str(result.stderr or "PowerShell could not read BIOS inventory").strip()[:500])
    return str(result.stdout or "").strip()


def parse_bios_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("BIOS collector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("BIOS collector returned an invalid payload")
    return {
        "schema_version": 1,
        "collected_at": _now(),
        "status": "ok",
        "read_only": True,
        "bios": payload.get("bios") or {},
        "system": payload.get("system") or {},
        "baseboard": payload.get("baseboard") or {},
        "tpm": payload.get("tpm") or {},
    }


def collect_bios_inventory(*, runner: Callable[[str], str] | None = None) -> dict[str, Any]:
    if os.name != "nt" and runner is None:
        return {
            "schema_version": 1,
            "collected_at": _now(),
            "status": "unavailable",
            "read_only": True,
            "bios": {},
            "system": {},
            "baseboard": {},
            "tpm": {},
        }
    return parse_bios_payload((runner or _run_powershell)(BIOS_POWERSHELL))

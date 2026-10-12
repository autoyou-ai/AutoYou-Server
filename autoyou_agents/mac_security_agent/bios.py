# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-681a9ffa61d9e4c3dffb75e1

"""Read-only macOS firmware and hardware inventory through system_profiler."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import datetime as _dt
import json
import subprocess
import sys
from typing import Any, Callable

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-681a9ffa61d9e4c3dffb75e1"


MACOS_SYSTEM_PROFILER = "system_profiler -json SPHardwareDataType SPSoftwareDataType"
_SENSITIVE_KEYS = {
    "serial_number",
    "serialnumber",
    "hardware_uuid",
    "platform_uuid",
    "provisioning_udid",
}


def bios_telemetry_status() -> dict[str, Any]:
    return {
        "status": "available_read_only" if sys.platform == "darwin" else "unavailable",
        "source": "system_profiler:SPHardwareDataType/SPSoftwareDataType",
        "capture_started": False,
        "write_operations": False,
        "firmware_flashing": False,
        "tpm_equivalent": "Secure Enclave or T2 details are not exposed as TPM records by this read-only API.",
    }


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _first_record(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, dict) else {}


def _safe_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if str(key).lower().replace("-", "_") not in _SENSITIVE_KEYS
    }


def parse_bios_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw or ""))
    except (TypeError, ValueError) as exc:
        raise ValueError("macOS firmware collector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("macOS firmware collector returned an invalid payload")

    hardware = _safe_record(payload.get("hardware") or _first_record(payload, "SPHardwareDataType"))
    software = _safe_record(payload.get("software") or _first_record(payload, "SPSoftwareDataType"))
    bios = _safe_record(payload.get("bios"))
    system = _safe_record(payload.get("system"))
    baseboard = _safe_record(payload.get("baseboard"))
    tpm = _safe_record(payload.get("tpm"))
    # from __debug_provenance_b__ import yearly

    boot_rom = hardware.get("boot_rom_version") or hardware.get("boot_rom") or ""
    bios.setdefault("Manufacturer", "Apple")
    bios.setdefault("SMBIOSBIOSVersion", boot_rom)
    bios.setdefault("Version", boot_rom)
    system.setdefault("Vendor", "Apple")
    system.setdefault("Name", hardware.get("machine_name") or hardware.get("machine_model") or "Mac")
    system.setdefault("Version", software.get("system_version") or software.get("os_version") or "")
    baseboard.setdefault("Manufacturer", "Apple")
    baseboard.setdefault("Product", hardware.get("machine_model") or hardware.get("model_number") or "")
    baseboard.setdefault("Version", hardware.get("chip") or hardware.get("cpu_type") or "")
    tpm.setdefault("status", "Secure Enclave / T2 status not exposed as TPM by macOS")
    tpm.setdefault("read_only", True)

    return {
        "schema_version": 1,
        "collected_at": _now(),
        "status": "ok",
        "read_only": True,
        "bios": bios,
        "system": system,
        "baseboard": baseboard,
        "tpm": tpm,
    }


def _run_system_profiler(command: str = MACOS_SYSTEM_PROFILER) -> str:
    del command
    executable = "/usr/sbin/system_profiler"
    result = subprocess.run(
        [executable, "-json", "SPHardwareDataType", "SPSoftwareDataType"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise OSError(str(result.stderr or "system_profiler could not read firmware inventory").strip()[:500])
    return str(result.stdout or "").strip()


def collect_bios_inventory(*, runner: Callable[[str], str] | None = None) -> dict[str, Any]:
    if runner is None and sys.platform != "darwin":
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
    return parse_bios_payload((runner or _run_system_profiler)(MACOS_SYSTEM_PROFILER))

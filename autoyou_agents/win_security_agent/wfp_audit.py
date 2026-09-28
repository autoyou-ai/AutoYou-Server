# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-M-363546313441613439306132-1367dbfb4a6b42291261ec2d

"""Opt-in Windows Filtering Platform audit events for process-bound flows."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_m__ = "AUTOYOU-PROVENANCE-M-363546313441613439306132-1367dbfb4a6b42291261ec2d"


import json
import os
import subprocess
from typing import Any, Callable


WFP_AUDIT_POWERSHELL = r'''
$seconds = __SECONDS__
$maxEvents = __MAX_EVENTS__
$events = @(Get-WinEvent -FilterHashtable @{
    LogName = "Security"
    Id = 5156, 5157
    StartTime = (Get-Date).AddSeconds(-$seconds)
} -MaxEvents $maxEvents -ErrorAction Stop)
$events | ForEach-Object {
    $xml = [xml]$_.ToXml()
    $fields = [ordered]@{}
    $xml.Event.EventData.Data | ForEach-Object {
        $fields[[string]$_.Name] = [string]$_.InnerText
    }
    [ordered]@{
        id = [int]$_.Id
        record_id = [int64]$_.RecordId
        time_created = $_.TimeCreated.ToUniversalTime().ToString("o")
        fields = $fields
    }
} | ConvertTo-Json -Depth 6 -Compress
'''


def wfp_audit_status() -> dict[str, Any]:
    enabled = os.name == "nt" and os.environ.get("AUTOYOU_WIN_SECURITY_ENABLE_WFP_AUDIT") == "1"
    return {
        "status": "enabled_opt_in" if enabled else "disabled",
        "event_ids": [5156, 5157],
        "requires_elevation": True,
        "capture_started": False,
        "policy_mutated": False,
        "high_volume_warning": True,
    }


def _run_powershell(command: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        detail = str(result.stderr or "PowerShell could not read WFP audit events").strip()
        if "No events were found" in detail:
            return "[]"
        raise OSError(detail[:500])
    return str(result.stdout or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip(), 0)
    except (TypeError, ValueError):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return default


def _direction(value: Any) -> str:
    return {
        "%%14592": "inbound",
        "%%14593": "outbound",
        "inbound": "inbound",
        "outbound": "outbound",
    }.get(str(value or "").strip().lower(), "")


def parse_wfp_events(raw: str) -> list[dict[str, Any]]:
    if not str(raw or "").strip():
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("WFP audit collector returned invalid JSON") from exc
    events = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        fields = event.get("fields") if isinstance(event.get("fields"), dict) else {}
        protocol_number = _int(fields.get("Protocol"))
        direction = _direction(fields.get("Direction"))
        if protocol_number not in {6, 17} or not direction:
            continue
        source_address = str(fields.get("SourceAddress") or "").strip()
        destination_address = str(fields.get("DestAddress") or "").strip()
        source_port = _int(fields.get("SourcePort"))
        destination_port = _int(fields.get("DestPort"))
        if direction == "inbound":
            local_address, local_port = destination_address, destination_port
            remote_address, remote_port = source_address, source_port
        else:
            local_address, local_port = source_address, source_port
            remote_address, remote_port = destination_address, destination_port
        rows.append(
            {
                "protocol": "tcp" if protocol_number == 6 else "udp",
                "local_address": local_address,
                "local_port": local_port,
                "remote_address": remote_address,
                "remote_port": remote_port,
                "state": "permitted" if _int(event.get("id"), 5156) == 5156 else "blocked",
                "process_id": _int(fields.get("ProcessID")),
                "process_name": "<unknown>",
                "executable_path": str(fields.get("Application") or "").strip(),
                "user_name": "",
                "source": "wfp_audit",
                "direction": direction,
                "remote_user_id": str(fields.get("RemoteUserID") or "").strip(),
                "remote_machine_id": str(fields.get("RemoteMachineID") or "").strip(),
                "audit_event_id": _int(event.get("id")),
                "audit_record_id": _int(event.get("record_id")),
                "audit_filter_id": _int(fields.get("FilterRTID")),
                "layer_name": str(fields.get("LayerName") or "").strip(),
                "observed_at": str(event.get("time_created") or "").strip(),
            }
        )
    return rows


def collect_wfp_audit_events(
    *,
    seconds: int = 60,
    max_events: int = 512,
    runner: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """Read a bounded recent WFP audit window when explicitly enabled."""
    if wfp_audit_status()["status"] != "enabled_opt_in":
        return []
    bounded_seconds = max(1, min(int(seconds), 600))
    bounded_events = max(1, min(int(max_events), 2048))
    command = WFP_AUDIT_POWERSHELL.replace("__SECONDS__", str(bounded_seconds)).replace(
        "__MAX_EVENTS__", str(bounded_events)
    )
    return parse_wfp_events((runner or _run_powershell)(command))

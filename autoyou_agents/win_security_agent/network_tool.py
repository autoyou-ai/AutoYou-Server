"""Windows-native, read-only network snapshots with a small local history store."""

from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from shared.platform_runtime import get_service_data_dir

from .privileged import get_privileged_capabilities
from .windivert import collect_flow_events, windivert_status
from .wfp_audit import collect_wfp_audit_events, wfp_audit_status


POWERSHELL_SNAPSHOT = r'''
$ErrorActionPreference = "Stop"
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
$processes = @{}
$owners = @{}
$processError = $null
try {
    Get-Process -IncludeUserName -ErrorAction Stop | ForEach-Object {
        $owners[[int]$_.Id] = [string]$_.UserName
    }
} catch {
    $processError = $_.Exception.Message
}
try {
    Get-CimInstance -ClassName Win32_Process -ErrorAction Stop | ForEach-Object {
        $ownerPid = [int]$_.ProcessId
        $processes[$ownerPid] = [ordered]@{
            process_name = [string]$_.Name
            executable_path = [string]$_.ExecutablePath
            user_name = [string]$owners[$ownerPid]
        }
    }
} catch {
    $processError = (($processError, $_.Exception.Message) | Where-Object { $_ }) -join "; "
}

$connections = @()
$errors = @()
try {
    Get-NetTCPConnection -ErrorAction Stop | ForEach-Object {
        $ownerPid = [int]$_.OwningProcess
        $process = $processes[$ownerPid]
        $connections += [ordered]@{
            protocol = "tcp"
            local_address = [string]$_.LocalAddress
            local_port = [int]$_.LocalPort
            remote_address = [string]$_.RemoteAddress
            remote_port = [int]$_.RemotePort
            state = [string]$_.State
            process_id = $ownerPid
            process_name = if ($process) { [string]$process.process_name } else { "<unknown>" }
            executable_path = if ($process) { [string]$process.executable_path } else { "" }
            user_name = if ($process) { [string]$process.user_name } else { "" }
        }
    }
} catch {
    $errors += "tcp: $($_.Exception.Message)"
}
try {
    Get-NetUDPEndpoint -ErrorAction Stop | ForEach-Object {
        $ownerPid = [int]$_.OwningProcess
        $process = $processes[$ownerPid]
        $connections += [ordered]@{
            protocol = "udp"
            local_address = [string]$_.LocalAddress
            local_port = [int]$_.LocalPort
            remote_address = ""
            remote_port = 0
            state = "bound"
            process_id = $ownerPid
            process_name = if ($process) { [string]$process.process_name } else { "<unknown>" }
            executable_path = if ($process) { [string]$process.executable_path } else { "" }
            user_name = if ($process) { [string]$process.user_name } else { "" }
        }
    }
} catch {
    $errors += "udp: $($_.Exception.Message)"
}
if ($processError) { $errors += "processes: $processError" }
[ordered]@{
    elevated = [bool]$isAdmin
    connections = $connections
    errors = $errors
} | ConvertTo-Json -Depth 6 -Compress
'''


class CollectorError(RuntimeError):
    """The native Windows collector could not return a snapshot."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _remote_scope(address: str) -> str:
    value = str(address or "").strip()
    if not value or value in {"0.0.0.0", "::"}:
        return "none"
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return "unknown"
    if ip.is_loopback:
        return "loopback"
    if ip.is_private or ip.is_link_local:
        return "private"
    if ip.is_reserved or ip.is_unspecified or ip.is_multicast:
        return "reserved"
    return "public"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


WELL_KNOWN_SERVICES = {
    53: "DNS",
    80: "HTTP",
    123: "NTP",
    443: "HTTPS",
    500: "IKE",
    3478: "STUN/TURN",
    5349: "STUN/TURN TLS",
}


def _service_hint(port: int) -> str:
    return WELL_KNOWN_SERVICES.get(_int(port), "")


def _normalize_connection(raw: Any) -> dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    protocol = str(row.get("protocol") or "").strip().lower()
    if protocol not in {"tcp", "udp"}:
        protocol = "unknown"
    remote_address = str(row.get("remote_address") or "").strip()
    state = str(row.get("state") or "unknown").strip() or "unknown"
    process_id = _int(row.get("process_id"))
    policy_key = "|".join((str(process_id), protocol, remote_address, str(_int(row.get("remote_port")))))
    return {
        "protocol": protocol,
        "local_address": str(row.get("local_address") or "").strip(),
        "local_port": _int(row.get("local_port")),
        "remote_address": remote_address,
        "remote_port": _int(row.get("remote_port")),
        "remote_service_hint": _service_hint(_int(row.get("remote_port"))),
        "remote_scope": _remote_scope(remote_address),
        "state": state,
        "process_id": process_id,
        "process_name": str(row.get("process_name") or "<unknown>").strip() or "<unknown>",
        "executable_path": str(row.get("executable_path") or "").strip(),
        "user_name": str(row.get("user_name") or "").strip(),
        "source": str(row.get("source") or "endpoint_table").strip() or "endpoint_table",
        "direction": str(row.get("direction") or "").strip(),
        "remote_user_id": str(row.get("remote_user_id") or "").strip(),
        "remote_machine_id": str(row.get("remote_machine_id") or "").strip(),
        "audit_event_id": _int(row.get("audit_event_id")),
        "audit_record_id": _int(row.get("audit_record_id")),
        "audit_filter_id": _int(row.get("audit_filter_id")),
        "layer_name": str(row.get("layer_name") or "").strip(),
        "observed_at": str(row.get("observed_at") or "").strip(),
        "policy_key": policy_key,
    }


def aggregate_connections(connections: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group endpoint rows by process identity for the dashboard."""
    groups: dict[tuple[int, str, str, str], dict[str, Any]] = {}
    for connection in connections:
        pid = _int(connection.get("process_id"))
        name = str(connection.get("process_name") or "<unknown>")
        path = str(connection.get("executable_path") or "")
        user_name = str(connection.get("user_name") or "")
        key = (pid, name, path, user_name)
        group = groups.setdefault(
            key,
            {
                "process_id": pid,
                "process_name": name,
                "executable_path": path,
                "user_name": user_name,
                "total": 0,
                "protocols": {},
                "states": {},
                "remote_connections": 0,
            },
        )
        group["total"] += 1
        protocol = str(connection.get("protocol") or "unknown")
        state = str(connection.get("state") or "unknown")
        group["protocols"][protocol] = group["protocols"].get(protocol, 0) + 1
        group["states"][state] = group["states"].get(state, 0) + 1
        if connection.get("remote_scope") not in {None, "", "none"}:
            group["remote_connections"] += 1
    return sorted(groups.values(), key=lambda item: (-item["total"], item["process_name"].lower()))


def build_network_overview(
    connections: Iterable[dict[str, Any]],
    *,
    geo_by_ip: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Build process, peer, correlation, and map-ready views from one snapshot."""
    geo_by_ip = geo_by_ip or {}
    process_groups: dict[str, dict[str, Any]] = {}
    peer_groups: dict[str, dict[str, Any]] = {}
    rows = [_normalize_connection(row) for row in connections]
    for row in rows:
        process_key = "|".join(
            (
                str(row["process_id"]),
                row["process_name"],
                row["executable_path"],
                row["user_name"],
            )
        )
        process = process_groups.setdefault(
            process_key,
            {
                "key": process_key,
                "process_id": row["process_id"],
                "process_name": row["process_name"],
                "executable_path": row["executable_path"],
                "user_name": row["user_name"],
                "total": 0,
                "protocols": {},
                "states": {},
                "remote_connections": 0,
                "peer_keys": set(),
            },
        )
        process["total"] += 1
        process["protocols"][row["protocol"]] = process["protocols"].get(row["protocol"], 0) + 1
        process["states"][row["state"]] = process["states"].get(row["state"], 0) + 1
        if row["remote_scope"] != "none":
            process["remote_connections"] += 1
            peer_key = "|".join((row["remote_address"], str(row["remote_port"])))
            process["peer_keys"].add(peer_key)
            peer = peer_groups.setdefault(
                peer_key,
                {
                    "key": peer_key,
                    "remote_address": row["remote_address"],
                    "remote_port": row["remote_port"],
                    "remote_service_hint": row["remote_service_hint"],
                    "remote_scope": row["remote_scope"],
                    "connections": 0,
                    "protocols": {},
                    "processes": {},
                    "states": {},
                    "directions": {},
                    "sources": {},
                    "policy_keys": set(),
                    "geo": geo_by_ip.get(row["remote_address"]),
                },
            )
            peer["connections"] += 1
            peer["protocols"][row["protocol"]] = peer["protocols"].get(row["protocol"], 0) + 1
            process_ref = peer["processes"].setdefault(
                process_key,
                {
                    "key": process_key,
                    "process_id": row["process_id"],
                    "process_name": row["process_name"],
                    "count": 0,
                },
            )
            process_ref["count"] += 1
            peer["states"][row["state"]] = peer["states"].get(row["state"], 0) + 1
            if row["direction"]:
                peer["directions"][row["direction"]] = peer["directions"].get(row["direction"], 0) + 1
            peer["sources"][row["source"]] = peer["sources"].get(row["source"], 0) + 1
            peer["policy_keys"].add(row.get("policy_key") or peer_key)

    def finalize_process(group: dict[str, Any]) -> dict[str, Any]:
        group["peer_keys"] = sorted(group["peer_keys"])
        return group

    def finalize_peer(peer: dict[str, Any]) -> dict[str, Any]:
        labels = []
        if peer["remote_scope"] == "public":
            labels.append("public peer")
        if len(peer["processes"]) > 1:
            labels.append("shared across processes")
        if peer["remote_service_hint"]:
            labels.append(peer["remote_service_hint"])
        if len(peer["protocols"]) > 1:
            labels.append("TCP + UDP")
        if any(state.lower() in {"established", "permitted", "flow_established"} for state in peer["states"]):
            labels.append("active flow")
        peer["processes"] = sorted(peer["processes"].values(), key=lambda item: (-item["count"], item["process_name"].lower()))
        peer["correlations"] = labels
        peer["mapped"] = bool(
            isinstance(peer.get("geo"), dict)
            and isinstance(peer["geo"].get("location"), dict)
            and peer["geo"]["location"].get("latitude") is not None
            and peer["geo"]["location"].get("longitude") is not None
        )
        peer["policy_keys"] = sorted(peer["policy_keys"])
        return peer

    processes = sorted((finalize_process(group) for group in process_groups.values()), key=lambda item: (-item["total"], item["process_name"].lower()))
    peers = sorted((finalize_peer(peer) for peer in peer_groups.values()), key=lambda item: (-item["connections"], item["remote_address"], item["remote_port"]))
    public_ips = sorted({peer["remote_address"] for peer in peers if peer["remote_scope"] == "public"})
    mapped = [peer for peer in peers if peer["mapped"]]
    return {
        "process_groups": processes,
        "remote_peers": peers,
        "mapped_peers": len(mapped),
        "public_peer_ips": public_ips,
        "unmapped_public_ips": [ip for ip in public_ips if not any(peer["remote_address"] == ip and peer["mapped"] for peer in peers)],
        "correlation_note": "Algorithmic links combine process, protocol, port, connection state, provider source, and cached IP intelligence.",
    }


def parse_collector_payload(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw or ""))
    except (TypeError, ValueError) as exc:
        raise CollectorError("Windows collector returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CollectorError("Windows collector returned an invalid payload")
    connections = [_normalize_connection(item) for item in payload.get("connections", [])]
    errors = [str(item) for item in payload.get("errors", []) if str(item).strip()]
    tcp = sum(item["protocol"] == "tcp" for item in connections)
    udp = sum(item["protocol"] == "udp" for item in connections)
    remote = sum(item["remote_scope"] not in {"", "none"} for item in connections)
    listening = sum(
        item["protocol"] == "tcp" and item["state"].lower() == "listen"
        for item in connections
    )
    return {
        "schema_version": 1,
        "collected_at": _now(),
        "platform": "windows",
        "collector": "powershell:Get-NetTCPConnection/Get-NetUDPEndpoint",
        "elevated": bool(payload.get("elevated", False)),
        "errors": errors,
        "summary": {
            "total": len(connections),
            "tcp": tcp,
            "udp": udp,
            "remote": remote,
            "listening": listening,
        },
        "processes": aggregate_connections(connections),
        "connections": connections,
    }


def _run_powershell(command: str = POWERSHELL_SNAPSHOT) -> str:
    executable = "powershell.exe" if sys.platform == "win32" else "powershell"
    result = subprocess.run(
        [executable, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = str(result.stdout or "").strip()
    if result.returncode != 0 and not output:
        detail = str(result.stderr or "").strip() or "PowerShell exited with an error"
        raise CollectorError(detail[:500])
    return output


def _merge_provider_rows(snapshot: dict[str, Any], rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    if not rows:
        return snapshot
    owners = {
        int(group.get("process_id") or 0): group
        for group in snapshot.get("processes", [])
    }
    known = {
        (
            row["protocol"], row["local_address"], row["local_port"],
            row["remote_address"], row["remote_port"], row["process_id"],
            row["source"], row.get("audit_record_id", 0),
        )
        for row in snapshot["connections"]
    }
    for row in rows:
        owner = owners.get(int(row.get("process_id") or 0))
        if owner:
            row["process_name"] = owner.get("process_name") or row["process_name"]
            row["executable_path"] = owner.get("executable_path") or row["executable_path"]
            row["user_name"] = owner.get("user_name") or row["user_name"]
        row = _normalize_connection(row)
        key = (
            row["protocol"], row["local_address"], row["local_port"],
            row["remote_address"], row["remote_port"], row["process_id"],
            row["source"], row.get("audit_record_id", 0),
        )
        if key not in known:
            snapshot["connections"].append(row)
            known.add(key)
    collector = snapshot["collector"]
    snapshot = parse_collector_payload(json.dumps({
        "elevated": snapshot["elevated"],
        "connections": snapshot["connections"],
        "errors": snapshot["errors"],
    }))
    snapshot["collector"] = collector
    return snapshot


def collect_network_snapshot(*, runner: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    """Collect a safe, read-only snapshot; ``runner`` exists for unit tests."""
    if runner is None and os.name != "nt":
        return {
            "schema_version": 1,
            "collected_at": _now(),
            "platform": sys.platform,
            "collector": "unavailable",
            "elevated": False,
            "errors": ["Windows-only collector is unavailable on this platform."],
            "summary": {"total": 0, "tcp": 0, "udp": 0, "remote": 0, "listening": 0},
            "processes": [],
            "connections": [],
        }
    snapshot = parse_collector_payload((runner or _run_powershell)(POWERSHELL_SNAPSHOT))
    provider = windivert_status()
    if provider["status"] == "configured":
        try:
            flow_rows = collect_flow_events()
        except (OSError, AttributeError) as exc:
            snapshot["errors"].append(f"windivert: {str(exc)[:300]}")
        else:
            snapshot = _merge_provider_rows(snapshot, flow_rows)
            snapshot["collector"] += "+windivert:flow"
    audit = wfp_audit_status()
    if audit["status"] == "enabled_opt_in":
        try:
            audit_rows = collect_wfp_audit_events()
        except (OSError, ValueError) as exc:
            snapshot["errors"].append(f"wfp_audit: {str(exc)[:300]}")
        else:
            snapshot = _merge_provider_rows(snapshot, audit_rows)
            if audit_rows:
                snapshot["collector"] += "+wfp:audit"
    snapshot["capabilities"] = get_privileged_capabilities()
    return snapshot


def default_storage_path() -> Path:
    # Source runs should keep this agent's mutable state at the repository
    # root, while packaged runs are redirected by platform_runtime to the
    # per-user application-data directory.  Anchoring at this module would
    # create an accidental nested ``win_security_agent/win_security_agent``.
    source_root = Path(__file__).resolve().parents[2]
    return get_service_data_dir("win_security_agent", anchor=source_root) / "network.sqlite3"


class SnapshotStore:
    """Small SQLite store for recent snapshots owned by this agent."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or default_storage_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    collected_at TEXT NOT NULL,
                    elevated INTEGER NOT NULL,
                    total_connections INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ip_enrichment (
                    ip TEXT PRIMARY KEY,
                    fetched_at REAL NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )

    def save(self, snapshot: dict[str, Any]) -> int:
        summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
        with sqlite3.connect(self.path) as connection:
            cursor = connection.execute(
                "INSERT INTO snapshots(collected_at, elevated, total_connections, payload) VALUES (?, ?, ?, ?)",
                (
                    str(snapshot.get("collected_at") or _now()),
                    int(bool(snapshot.get("elevated"))),
                    _int(summary.get("total")),
                    json.dumps(snapshot, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def latest(self, limit: int = 10) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(_int(limit, 10), 100))
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT id, collected_at, elevated, total_connections FROM snapshots ORDER BY id DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        return [
            {
                "id": int(row[0]),
                "collected_at": str(row[1]),
                "elevated": bool(row[2]),
                "total_connections": int(row[3]),
            }
            for row in rows
        ]

    def process_history(self, limit: int = 24) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(_int(limit, 24), 100))
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT id, collected_at, payload FROM snapshots ORDER BY id DESC LIMIT ?",
                (bounded_limit,),
            ).fetchall()
        history: dict[str, dict[str, Any]] = {}
        for snapshot_id, collected_at, payload in rows:
            try:
                snapshot = json.loads(str(payload))
            except (TypeError, ValueError):
                continue
            for process in snapshot.get("processes", []) if isinstance(snapshot, dict) else []:
                if not isinstance(process, dict):
                    continue
                key = "|".join(
                    (
                        str(process.get("process_id") or 0),
                        str(process.get("process_name") or "<unknown>"),
                        str(process.get("executable_path") or ""),
                        str(process.get("user_name") or ""),
                    )
                )
                entry = history.setdefault(
                    key,
                    {
                        "key": key,
                        "process_id": _int(process.get("process_id")),
                        "process_name": str(process.get("process_name") or "<unknown>"),
                        "executable_path": str(process.get("executable_path") or ""),
                        "user_name": str(process.get("user_name") or ""),
                        "samples": [],
                    },
                )
                entry["samples"].append(
                    {
                        "snapshot_id": int(snapshot_id),
                        "collected_at": str(collected_at),
                        "total": _int(process.get("total")),
                        "remote": _int(process.get("remote_connections")),
                        "tcp": _int((process.get("protocols") or {}).get("tcp")),
                        "udp": _int((process.get("protocols") or {}).get("udp")),
                    }
                )
        for entry in history.values():
            entry["samples"].sort(key=lambda item: item["snapshot_id"])
            entry["last_seen"] = entry["samples"][-1]["collected_at"]
            entry["current_total"] = entry["samples"][-1]["total"]
        return sorted(history.values(), key=lambda item: (-item["current_total"], item["process_name"].lower()))

    def get_ip_enrichment(self, ip: str, max_age_seconds: int = 86400) -> Optional[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT fetched_at, payload FROM ip_enrichment WHERE ip = ?",
                (str(ip),),
            ).fetchone()
        if not row or time.time() - float(row[0]) > max(60, int(max_age_seconds)):
            return None
        try:
            payload = json.loads(str(row[1]))
        except (TypeError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def save_ip_enrichment(self, ip: str, payload: dict[str, Any]) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO ip_enrichment(ip, fetched_at, payload) VALUES (?, ?, ?)",
                (str(ip), time.time(), json.dumps(payload, separators=(",", ":"))),
            )

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-0fc59d9ef7f60337adcb77f8

"""macOS-native, read-only network snapshots with a small local history store."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_o__ = "AUTOYOU-PROVENANCE-O-68747470733a2f2f6275792e-0fc59d9ef7f60337adcb77f8"


import datetime as _dt
import ipaddress
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from shared.platform_runtime import get_service_data_dir

from .privileged import (
    get_privileged_capabilities,
    is_process_elevated,
    process_executable_path,
)


LSOF_SNAPSHOT = "lsof -nP -a -iTCP -iUDP -w +c 0 -FpcunPtT"


class CollectorError(RuntimeError):
    """The native macOS collector could not return a snapshot."""


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


def _parse_lsof_endpoint(value: str, *, ipv6: bool) -> tuple[str, int]:
    token = str(value or "").strip()
    wildcard = "::" if ipv6 else "0.0.0.0"
    if not token or token in {"*", "*:*"}:
        return wildcard, 0
    if token.startswith("[") and "]" in token:
        address, _, port = token[1:].partition("]")
        port = port.removeprefix(":")
    elif ":" in token:
        address, port = token.rsplit(":", 1)
    else:
        address, port = token, ""
    address = address.strip()
    if not address or address == "*":
        address = wildcard
    try:
        parsed_port = int(port) if port and port != "*" else 0
    except ValueError:
        parsed_port = 0
    return address, parsed_port


def _canonical_state(value: str, *, has_remote: bool, protocol: str) -> str:
    state = str(value or "").strip()
    if state:
        return {
            "ESTABLISHED": "Established",
            "LISTEN": "Listen",
            "SYN_SENT": "Syn-Sent",
            "SYN_RECEIVED": "Syn-Received",
            "CLOSE_WAIT": "Close-Wait",
            "FIN_WAIT_1": "Fin-Wait-1",
            "FIN_WAIT_2": "Fin-Wait-2",
            "TIME_WAIT": "Time-Wait",
        }.get(state.upper(), state)
    return "connected" if has_remote else ("bound" if protocol == "udp" else "unknown")


def parse_lsof_rows(
    raw: str,
    *,
    process_path_resolver: Callable[[int], str] | None = None,
) -> list[dict[str, Any]]:
    """Parse lsof field output without relying on table column widths."""
    processes: dict[str, Any] = {}
    current_file: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = []

    def flush_file() -> None:
        if not current_file or not processes.get("pid"):
            return
        protocol = str(current_file.get("protocol") or "").lower()
        if protocol not in {"tcp", "udp"}:
            return
        name = str(current_file.get("name") or "")
        local_token, separator, remote_token = name.partition("->")
        ipv6 = str(current_file.get("type") or "").lower() == "ipv6"
        local_address, local_port = _parse_lsof_endpoint(local_token, ipv6=ipv6)
        remote_address, remote_port = ("", 0)
        if separator:
            remote_address, remote_port = _parse_lsof_endpoint(remote_token, ipv6=ipv6)
        pid = _int(processes.get("pid"))
        command = str(processes.get("command") or "").strip()
        executable_path = ""
        if process_path_resolver is not None and pid:
            executable_path = str(process_path_resolver(pid) or "").strip()
        if not executable_path and command.startswith("/"):
            executable_path = command
        rows.append(
            {
                "protocol": protocol,
                "local_address": local_address,
                "local_port": local_port,
                "remote_address": remote_address,
                "remote_port": remote_port,
                "state": _canonical_state(
                    str(current_file.get("state") or ""),
                    has_remote=bool(separator and remote_address),
                    protocol=protocol,
                ),
                "process_id": pid,
                "process_name": command or "<unknown>",
                "executable_path": executable_path,
                "user_name": str(processes.get("user") or "").strip(),
                "source": "lsof",
                "direction": "",
            }
        )

    for line in str(raw or "").splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            flush_file()
            processes = {"pid": value}
            current_file = None
        elif tag == "c":
            processes["command"] = value
        elif tag == "u":
            processes["user"] = value
        elif tag == "f":
            flush_file()
            current_file = {"fd": value}
        elif current_file is not None and tag == "t":
            current_file["type"] = value
        elif current_file is not None and tag == "P":
            current_file["protocol"] = value
        elif current_file is not None and tag == "n":
            current_file["name"] = value
        elif current_file is not None and tag == "T":
            key, _, field_value = value.partition("=")
            if key == "ST":
                current_file["state"] = field_value
    flush_file()
    return rows


def parse_collector_payload(
    raw: str,
    *,
    elevated: bool | None = None,
    process_path_resolver: Callable[[int], str] | None = process_executable_path,
) -> dict[str, Any]:
    connections = [_normalize_connection(item) for item in parse_lsof_rows(raw, process_path_resolver=process_path_resolver)]
    errors: list[str] = []
    if not str(raw or "").strip():
        errors.append("lsof returned no endpoint rows")
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
        "platform": "macos",
        "collector": "macOS:lsof",
        "elevated": is_process_elevated() if elevated is None else bool(elevated),
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


def _run_lsof(command: str = LSOF_SNAPSHOT) -> str:
    del command
    executable = shutil.which("lsof") or "/usr/sbin/lsof"
    result = subprocess.run(
        [executable, "-nP", "-a", "-iTCP", "-iUDP", "-w", "+c", "0", "-FpcunPtT"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = str(result.stdout or "").strip()
    if result.returncode != 0 and not output:
        detail = str(result.stderr or "").strip() or "lsof exited with an error"
        raise CollectorError(detail[:500])
    return output


def collect_network_snapshot(*, runner: Optional[Callable[[str], str]] = None) -> dict[str, Any]:
    """Collect a safe, read-only snapshot; ``runner`` exists for unit tests."""
    if runner is None and sys.platform != "darwin":
        return {
            "schema_version": 1,
            "collected_at": _now(),
            "platform": sys.platform,
            "collector": "unavailable",
            "elevated": False,
            "errors": ["macOS-only collector is unavailable on this platform."],
            "summary": {"total": 0, "tcp": 0, "udp": 0, "remote": 0, "listening": 0},
            "processes": [],
            "connections": [],
        }
    snapshot = parse_collector_payload((runner or _run_lsof)(LSOF_SNAPSHOT))
    snapshot["capabilities"] = get_privileged_capabilities()
    return snapshot


def default_storage_path() -> Path:
    # Source runs should keep this agent's mutable state at the repository
    # root, while packaged runs are redirected by platform_runtime to the
    # per-user application-data directory.
    source_root = Path(__file__).resolve().parents[2]
    return get_service_data_dir("mac_security_agent", anchor=source_root) / "network.sqlite3"


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

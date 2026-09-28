# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
"""Transport profiles shared by Full and Lite's local setup QR exports."""

from typing import Any


def mobile_pairing_profiles(
    *, cloud: bool, autopair: bool, otp: bool, bluetooth: bool,
    local: dict[str, Any], security_tier: str, bluetooth_name: str,
) -> dict[str, Any]:
    tier = "A" if str(security_tier).upper() == "A" else "B"
    profiles: dict[str, dict[str, Any]] = {}
    if cloud:
        profiles["cloud_pair"] = {"tier": "A"}
    if local.get("lan_reachable") and local.get("primary_address"):
        profiles["local_pair"] = {
            "tier": "A", "host": local["primary_address"], "port": int(local["port"]),
        }
    if bluetooth:
        profiles["bluetooth_pair"] = {"tier": "A", "host": bluetooth_name}
    if autopair:
        profiles["auto_pair"] = {"tier": tier}
    if otp:
        profiles["otp"] = {"tier": tier}
    return {"profiles": profiles, "preferred": next(iter(profiles), "")}

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-e10646c92f3834f8f52c63fc

"""Transport profiles shared by Full and Lite's local setup QR exports."""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


from typing import Any

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-e10646c92f3834f8f52c63fc"


def mobile_pairing_profiles(
    *, cloud: bool, autopair: bool, otp: bool, bluetooth: bool,
    local: dict[str, Any], security_tier: str, bluetooth_name: str,
) -> dict[str, Any]:
    tier = "A" if str(security_tier).upper() == "A" else "B"
    profiles: dict[str, dict[str, Any]] = {}
    # from __debug_provenance_x__ import email
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

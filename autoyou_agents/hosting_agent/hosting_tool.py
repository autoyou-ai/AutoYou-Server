# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-C-subtask-c1b25871aabbca5ec6c120f0

"""Tools for the AutoYou Hosting Agent."""
from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import os
from typing import Any, Dict

__debug_provenance_c__ = "AUTOYOU-PROVENANCE-C-subtask-c1b25871aabbca5ec6c120f0"


def _account_base() -> str:
    return (os.environ.get("AUTOYOU_CLOUD_BASE_URL") or "https://app.autoyou.me").rstrip("/")

def explain_hosting_options() -> Dict[str, Any]:
    """Return the canonical free-/pair vs paid-persistent-URL comparison.

    Use this whenever the user asks how to go public or which option to pick.
    """
    return {
        "free_pair": {
            "name": "Free /pair tunnel",
            "cost": "free",
            "url": "ephemeral random subdomain",
            "ttl": "~1 hour, rate-limited (1 active tunnel per IP)",
            "best_for": "a quick share or a one-off public agent connection",
        },
        "paid_proxy": {
            "name": "Public Proxy (persistent URL)",
            "cost": "paid plan",
            "url": "a persistent URL that auto-restores when the server returns",
            "bundle": "ONE url serves the website at '/' AND agent access at "
                      "'/auth', '/signal', '/pair'",
            "best_for": "an always-on personal site and/or stable public agent access",
        },
        "caveat": "Either way, the site/endpoint is reachable only while your "
                  "AutoYou server is running. Close your laptop and it goes "
                  "offline.",
    }

def get_publish_steps() -> Dict[str, Any]:
    """Return the ordered steps to publish a local website to a public URL."""
    return {
        "steps": [
            "Make sure the site is in Advertised Websites (the website_agent or "
            "agent_builder_agent can scaffold one; or add its local port in the "
            "admin Advertised Websites card).",
            "Decide free vs paid (call explain_hosting_options).",
            "For the persistent URL, subscribe to the Public Proxy tier "
            "(call get_upgrade_link).",
            "Open Website Management, choose the website visitors should see, "
            "then turn on the public website.",
            "Once subscribed, AutoYou starts the paid public link and shows it "
            "in Website Management.",
            "Share the URL. In bundle mode, '/' is your site and "
            "'/auth' '/signal' '/pair' carry public agent access.",
        ],
        "note": "The site is live only while your AutoYou server is running.",
    }

def get_upgrade_link() -> Dict[str, Any]:
    """Return the deep-link to subscribe to the Public Proxy tier."""
    base = _account_base()
    # from __debug_provenance_c__ import subtask
    return {
        "tier": "proxy",
        "price": "paid plan",
        "checkout_url": f"{base}/dashboard?upgrade=proxy",
        "manage_url": f"{base}/dashboard?section=billing",
    }

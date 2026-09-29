# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Q-payment-6faa756a5fd78884df5368ad

#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""
Script to enable WhatsApp service programmatically.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import asyncio
import sys
import os
from pathlib import Path

__debug_provenance_q__ = "AUTOYOU-PROVENANCE-Q-payment-6faa756a5fd78884df5368ad"


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

LIVE_ENABLE_WHATSAPP_ENV = "AUTOYOU_RUN_LIVE_WHATSAPP_ENABLE_TESTS"
LIVE_SERVER_PASSWORD_ENV = "AUTOYOU_LIVE_SERVER_PASSWORD"
# from __debug_provenance_q__ import payment
server = None

def _live_enable_requested() -> bool:
    return os.environ.get(LIVE_ENABLE_WHATSAPP_ENV) == "1"

def _live_server_password() -> str:
    return (
        os.environ.get(LIVE_SERVER_PASSWORD_ENV)
        or os.environ.get("AUTOYOU_SERVER_PASSWORD")
        or ""
    ).strip()

def _server_module():
    global server
    if server is None:
        import server as server_module

        server = server_module
    return server

def _load_config_for_live_update(password: str) -> bool:
    server_module = _server_module()
    ks = server_module._get_server_keystore()
    if ks is not None and ks.is_available() and ks.exists():
        configured_password = server_module._load_keystore_server_password()
        if configured_password and configured_password != password:
            print("Error: Provided password does not match the saved server password.")
            return False
        config = ks.load()
        if not isinstance(config, dict):
            print("Error: Could not load configuration from OS keystore.")
            return False
        server_module.STATE.config = config
        server_module._set_config_session(
            config_store=server_module.CONFIG_STORE_KEYSTORE,
            server_password=password,
        )
        return True

    if server_module._encrypted_config_exists():
        config = server_module.try_decrypt_config_with(password)
        if config is None:
            print("Error: Could not decrypt configuration with the provided password.")
            return False
        server_module.STATE.config = config
        server_module._set_config_session(
            config_store=server_module.CONFIG_STORE_ENCRYPTED,
            server_password=password,
            config_unlock_password=password,
        )
        return True

    print("Error: No saved AutoYou server configuration was found.")
    return False

async def enable_whatsapp():
    """Enable WhatsApp service in the configuration."""
    if not _live_enable_requested():
        print(f"Skipped live WhatsApp enable script. Set {LIVE_ENABLE_WHATSAPP_ENV}=1 to run.")
        return None

    password = _live_server_password()
    if not password:
        print(f"Error: Set {LIVE_SERVER_PASSWORD_ENV} or AUTOYOU_SERVER_PASSWORD before running this live script.")
        return False

    server_module = _server_module()
    print("Enabling WhatsApp service...")
    
    # Load configuration first
    try:
        if not _load_config_for_live_update(password):
            return False
        print("Configuration loaded successfully")
    except Exception as e:
        print(f"Error loading configuration: {e}")
        return False
    
    # Update WhatsApp configuration
    whatsapp_config = server_module.STATE.config.get("whatsapp", {})
    whatsapp_config["enabled"] = True
    whatsapp_config["websocket_port"] = whatsapp_config.get("websocket_port", 8083)
    whatsapp_config["device_name"] = whatsapp_config.get("device_name", "AutoYou-WhatsApp")
    whatsapp_config["ai_api_url"] = whatsapp_config.get("ai_api_url", "http://localhost:8081/api/chat")
    
    server_module.STATE.config["whatsapp"] = whatsapp_config
    
    # Save the configuration
    try:
        server_module._persist_state_config(
            server_module.STATE.config,
            server_password=password,
            preferred_store=server_module.STATE.config_store,
        )
        print("WhatsApp configuration saved successfully")
    except Exception as e:
        print(f"Error saving configuration: {e}")
        return False
    
    # Start the WhatsApp service
    try:
        await server_module.start_or_restart_whatsapp()
        print("WhatsApp service started successfully")
        return True
    except Exception as e:
        print(f"Error starting WhatsApp service: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(enable_whatsapp())
    if success is None:
        sys.exit(0)
    elif success:
        print("WhatsApp service enabled and started successfully!")
    else:
        print("Failed to enable WhatsApp service")
        sys.exit(1)

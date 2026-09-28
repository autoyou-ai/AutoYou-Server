# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""AutoYou Persona agent - user-authored self-data profile (Markdown).

Stores a single ``persona.md`` self-data document under the mutable data dir. The
document can optionally be encrypted at rest with an OS-keystore key independent
of the login password (off by default), via ``shared.secure_data_store``. The
content is deliberate, user-dictated self-data intended to seed local persona
fine-tuning (``autoyou_fine_tuning_agent``) - not auto-pruned conversational memory.

Per-website access is intended to be gated by a per-agent 2FA security profile
(``shared.agent_security_profiles``); that enforcement is applied at the page/
datachannel layer. The destructive tools here additionally confirm with the user.
"""

from __future__ import annotations

import datetime as _datetime
import logging
import os
from pathlib import Path
from typing import Any, Dict

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

LOGGER = logging.getLogger(__name__)

_PERSONA_DIRNAME = "persona_agent"
_PERSONA_FILENAME = "persona.md"
_KEYSTORE_SERVICE = "AutoYou-PersonaData"


def _encryption_enabled() -> bool:
    """Independent-key encryption is OFF by default; opt in via env/config."""
    return str(os.getenv("AUTOYOU_PERSONA_ENCRYPT", "")).strip().lower() in {"1", "true", "yes", "on"}


def _persona_path() -> Path:
    from shared.platform_runtime import get_mutable_data_dir

    base = get_mutable_data_dir("AutoYou", anchor=__file__) / _PERSONA_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base / _PERSONA_FILENAME


def _store():
    from shared.secure_data_store import SecureDataStore

    return SecureDataStore(
        _persona_path(),
        encrypt=_encryption_enabled(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="persona",
    )


def get_persona_status() -> Dict[str, Any]:
    """Report whether a persona self-data profile exists and how it is stored.

    Read-only. Returns existence, character count, on-disk encryption state, and
    whether independent-key encryption is currently enabled for new writes.
    """
    try:
        store = _store()
        exists = store.exists()
        encrypted = store.is_encrypted_on_disk()
        size = 0
        if exists:
            try:
                size = len(store.read() or "")
            except Exception:
                size = -1  # present but unreadable (encrypted, key unavailable)
        return {
            "status": "success",
            "exists": exists,
            "encrypted_on_disk": encrypted,
            "encryption_enabled_for_writes": _encryption_enabled(),
            "character_count": size,
            "unreadable": size == -1,
            "message": (
                "No persona profile yet - use append_persona or save_persona to start one."
                if not exists
                else (
                    "Persona profile exists but is encrypted and the key is unavailable; "
                    "it cannot be recovered."
                    if size == -1
                    else f"Persona profile present ({size} characters)."
                )
            ),
        }
    except Exception as exc:
        LOGGER.error("get_persona_status failed: %s", exc)
        return {"status": "error", "message": f"Could not read persona status: {exc}"}


def read_persona() -> Dict[str, Any]:
    """Return the current persona self-data Markdown document (read-only)."""
    try:
        content = _store().read()
        if content is None:
            return {"status": "success", "exists": False, "content": "", "message": "No persona profile yet."}
        return {"status": "success", "exists": True, "content": content}
    except Exception as exc:
        LOGGER.error("read_persona failed: %s", exc)
        return {"status": "error", "message": f"Could not read persona profile: {exc}"}


def save_persona(content: str) -> Dict[str, Any]:
    """Replace the ENTIRE persona document with new Markdown (destructive overwrite).

    Confirm with the user before calling. Use append_persona for incremental adds.

    Args:
        content: The full Markdown document to store as the persona self-data.
    """
    text = str(content or "").strip()
    if not text:
        return {"status": "error", "message": "Refusing to save an empty persona document. Use wipe_persona to clear it."}
    try:
        _store().write(text)
        return {"status": "success", "character_count": len(text), "message": "Persona profile saved."}
    except Exception as exc:
        LOGGER.error("save_persona failed: %s", exc)
        return {"status": "error", "message": f"Could not save persona profile: {exc}"}


def append_persona(text: str, heading: str = "") -> Dict[str, Any]:
    """Append a dated entry to the persona document without disturbing prior content.

    Preferred for incremental additions ("add that I started a new job"). Creates
    the document if it does not exist yet.

    Args:
        text: The Markdown content to append.
        heading: Optional short section heading for the new entry.
    """
    body = str(text or "").strip()
    if not body:
        return {"status": "error", "message": "Nothing to append - provide the personal detail to record."}
    try:
        store = _store()
        existing = ""
        try:
            existing = store.read() or ""
        except Exception:
            return {"status": "error", "message": "Existing persona profile is encrypted and unreadable; cannot append."}
        stamp = _datetime.datetime.now().strftime("%Y-%m-%d")
        title = str(heading or "").strip()
        block = f"\n\n## {title} ({stamp})\n\n{body}\n" if title else f"\n\n### {stamp}\n\n{body}\n"
        if not existing.strip():
            block = f"# Persona\n{block}"
        store.write((existing.rstrip() + block) if existing.strip() else block)
        return {"status": "success", "message": "Appended to persona profile."}
    except Exception as exc:
        LOGGER.error("append_persona failed: %s", exc)
        return {"status": "error", "message": f"Could not append to persona profile: {exc}"}


def wipe_persona() -> Dict[str, Any]:
    """Permanently delete the persona profile (and its encryption key). Irreversible.

    This is the reset / uninstall-and-reinstall path. Confirm with the user first.
    """
    try:
        existed = _store().wipe(drop_key=True)
        return {
            "status": "success",
            "existed": existed,
            "message": "Persona profile wiped." if existed else "No persona profile was present.",
        }
    except Exception as exc:
        LOGGER.error("wipe_persona failed: %s", exc)
        return {"status": "error", "message": f"Could not wipe persona profile: {exc}"}


def create_persona_agent(model_config: Any) -> Agent:
    """Create the persona self-data agent."""
    tools = [
        get_persona_status,
        read_persona,
        save_persona,
        append_persona,
        wipe_persona,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )

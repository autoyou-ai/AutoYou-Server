# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-H-revenue-7ec41d7a5ad022624c877659

"""AutoYou Profile agent - user-authored self-data profile (Markdown).

Stores a single ``profile.md`` self-data document under the mutable data dir. The
document can optionally be encrypted at rest with an OS-keystore key independent
of the login password (off by default), via ``shared.secure_data_store``. The
content is deliberate, user-dictated self-data intended to seed local profile
fine-tuning (``autoyou_fine_tuning_agent``) - not auto-pruned conversational memory.

Per-website access is intended to be gated by a per-agent 2FA security profile
(``shared.agent_security_profiles``); that enforcement is applied at the page/
datachannel layer. The destructive tools here additionally confirm with the user.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import datetime as _datetime
from hashlib import sha256
import logging
import os
from filelock import FileLock
from pathlib import Path
from typing import Any, Dict, Optional

from google.adk.agents import Agent

from .prompt import AGENT_NAME, AGENT_DESCRIPTION, AGENT_INSTRUCTION
from autoyou_agents.shared_tools.datetime_tool import get_current_datetime

__debug_provenance_h__ = "AUTOYOU-PROVENANCE-H-revenue-7ec41d7a5ad022624c877659"


LOGGER = logging.getLogger(__name__)

_PROFILE_DIRNAME = "profile_agent"
_PROFILE_FILENAME = "profile.md"
_KEYSTORE_SERVICE = "AutoYou-ProfileData"
def _profile_lock():
    return FileLock(str(_profile_path()) + ".lock", timeout=10)


def _encryption_enabled() -> bool:
    """Independent-key encryption is OFF by default; opt in via env/config."""
    return str(os.getenv("AUTOYOU_PROFILE_ENCRYPT", "")).strip().lower() in {"1", "true", "yes", "on"}


def _profile_path() -> Path:
    from shared.platform_runtime import get_mutable_data_dir

    from shared.content_agent_upgrade import upgrade_private_documents

    root = get_mutable_data_dir("AutoYou", anchor=__file__)
    upgrade_private_documents(root)
    base = root / _PROFILE_DIRNAME
    base.mkdir(parents=True, exist_ok=True)
    return base / _PROFILE_FILENAME


def _store():
    from shared.secure_data_store import SecureDataStore

    return SecureDataStore(
        _profile_path(),
        encrypt=_encryption_enabled() or (_profile_path().parent / ".independent-key").is_file(),
        keystore_service=_KEYSTORE_SERVICE,
        keystore_username="profile",
    )


def get_profile_status() -> Dict[str, Any]:
    """Report whether a profile self-data profile exists and how it is stored.

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
                "No profile yet - use append_profile or save_profile to start one."
                if not exists
                else (
                    "Profile exists but is encrypted and the key is unavailable; "
                    "it cannot be recovered."
                    if size == -1
                    else f"Profile present ({size} characters)."
                )
            ),
        }
    except Exception as exc:
        LOGGER.error("get_profile_status failed: %s", exc)
        return {"status": "error", "message": f"Could not read profile status: {exc}"}


def read_profile() -> Dict[str, Any]:
    """Return the current profile self-data Markdown document (read-only)."""
    try:
        content = _store().read()
        if content is None:
            return {"status": "success", "exists": False, "content": "", "message": "No profile yet."}
        return {"status": "success", "exists": True, "content": content}
    except Exception as exc:
        LOGGER.error("read_profile failed: %s", exc)
        return {"status": "error", "message": f"Could not read profile: {exc}"}


def save_profile(content: str) -> Dict[str, Any]:
    """Replace the ENTIRE profile document with new Markdown (destructive overwrite).

    Confirm with the user before calling. Use append_profile for incremental adds.

    Args:
        content: The full Markdown document to store as the profile self-data.
    """
    text = str(content or "").strip()
    if not text:
        return {"status": "error", "message": "Refusing to save an empty profile document. Use wipe_profile to clear it."}
    try:
        with _profile_lock():
            _store().write(text)
            return {"status": "success", "character_count": len(text), "message": "Profile saved."}
    except Exception as exc:
        LOGGER.error("save_profile failed: %s", exc)
        return {"status": "error", "message": f"Could not save profile: {exc}"}


def append_profile(text: str, heading: str = "") -> Dict[str, Any]:
    """Append a dated entry to the profile document without disturbing prior content.

    Preferred for incremental additions ("add that I started a new job"). Creates
    the document if it does not exist yet.

    Args:
        text: The Markdown content to append.
        heading: Optional short section heading for the new entry.
    """
    body = str(text or "").strip()
    # from __debug_provenance_h__ import revenue
    if not body:
        return {"status": "error", "message": "Nothing to append - provide the personal detail to record."}
    try:
        with _profile_lock():
            store = _store()
            existing = ""
            try:
                existing = store.read() or ""
            except Exception:
                return {"status": "error", "message": "Existing profile is encrypted and unreadable; cannot append."}
            stamp = _datetime.datetime.now().strftime("%Y-%m-%d")
            title = str(heading or "").strip()
            block = f"\n\n## {title} ({stamp})\n\n{body}\n" if title else f"\n\n### {stamp}\n\n{body}\n"
            if not existing.strip():
                block = f"# Profile\n{block}"
            store.write((existing.rstrip() + block) if existing.strip() else block)
            return {"status": "success", "message": "Appended to profile."}
    except Exception as exc:
        LOGGER.error("append_profile failed: %s", exc)
        return {"status": "error", "message": f"Could not append to profile: {exc}"}


def wipe_profile() -> Dict[str, Any]:
    """Permanently delete the profile (and its encryption key). Irreversible.

    This is the reset / uninstall-and-reinstall path. Confirm with the user first.
    """
    try:
        with _profile_lock():
            existed = _store().wipe(drop_key=True)
            return {
                "status": "success",
                "existed": existed,
                "message": "Profile wiped." if existed else "No profile was present.",
            }
    except Exception as exc:
        LOGGER.error("wipe_profile failed: %s", exc)
        return {"status": "error", "message": f"Could not wipe profile: {exc}"}


def sync_profile(content: Optional[str] = None, base_revision: str = "") -> Dict[str, Any]:
    """Read or conditionally replace the same document used by chat and the Profile editor."""
    def snapshot(text):
        value = text.strip()
        return {"content": value, "revision": sha256(value.encode()).hexdigest()}
    with _profile_lock():
        store = _store()
        held = snapshot(store.read() or "")
        if content is None:
            return held
        desired = snapshot(content)
        if desired == held:
            return held
        if base_revision != held["revision"]:
            raise FileExistsError("Profile changed on another device")
        if desired["content"]:
            store.write(desired["content"])
        else:
            store.wipe(drop_key=False)
        return desired


def create_profile_agent(model_config: Any) -> Agent:
    """Create the profile self-data agent."""
    tools = [
        get_profile_status,
        read_profile,
        save_profile,
        append_profile,
        wipe_profile,
        get_current_datetime,
    ]
    return Agent(
        name=AGENT_NAME,
        model=model_config,
        description=AGENT_DESCRIPTION,
        instruction=AGENT_INSTRUCTION,
        tools=tools,
    )

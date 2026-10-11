# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Configuration defaults and cross-platform discovery paths for prompts_agent."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List

from shared.platform_runtime import get_service_data_dir

AGENT_NAME = "prompts_agent"
APP_DISPLAY_NAME = "My Prompt"


def get_prompts_data_dir() -> Path:
    """Return the writable per-service state directory, scoped under AUTOYOU_TEST_ROOT if set."""
    module_dir = Path(__file__).resolve().parent
    runtime_anchor = module_dir
    for parent in module_dir.parents:
        if (parent / "server.py").is_file() and (parent / "autoyou_agents").is_dir():
            runtime_anchor = parent
            break
    return get_service_data_dir(AGENT_NAME, anchor=runtime_anchor)


def get_prompts_db_path() -> Path:
    """Return the database path for prompts_agent."""
    return get_prompts_data_dir() / "prompts.db"


def get_prompts_attachments_dir() -> Path:
    """Return the attachments directory for prompts_agent."""
    return get_prompts_data_dir() / "attachments"


DATA_DIR = get_prompts_data_dir()
DEFAULT_DB_PATH = get_prompts_db_path()
DEFAULT_ATTACHMENTS_DIR = get_prompts_attachments_dir()

# Default sync intervals in minutes
SYNC_INTERVAL_MINUTES: Dict[str, int] = {
    "10m": 10,
    "hourly": 60,
    "daily": 1440,
    "weekly": 10080,
    "manual": 0,
}

DEFAULT_SETTINGS = {
    "collection_enabled": False,
    "sync_interval": "manual",
    "capture_attachments": False,
    "capture_outputs": True,
    "encrypted_storage": False,
    "otp_enabled": True,
    "default_otp_pin": "123456",  # Default passcode; user can change in settings
    "max_prompt_chars": 32000,
    "max_output_chars": 32000,
}

SUPPORTED_SOFTWARES = [
    "antigravity",
    "codex",
    "claude",
    "vscode",
    "cursor",
    "custom_hook",
]

SOFTWARE_LABELS = {
    "antigravity": "Antigravity",
    "codex": "Codex",
    "claude": "Claude",
    "vscode": "VS Code / Copilot",
    "cursor": "Cursor",
    "custom_hook": "Hooks / Push",
}

SOFTWARE_COLORS = {
    "antigravity": "#3b82f6",
    "codex": "#10a37f",
    "claude": "#d97706",
    "vscode": "#007acc",
    "cursor": "#8b5cf6",
    "custom_hook": "#ec4899",
}


def get_default_scan_paths() -> Dict[str, List[Path]]:
    """Return platform-specific well-known candidate paths for each software."""
    test_root = str(os.environ.get("AUTOYOU_TEST_ROOT", "")).strip()
    home = Path(test_root).expanduser().resolve() / "home" if test_root else Path.home()
    paths: Dict[str, List[Path]] = {sw: [] for sw in SUPPORTED_SOFTWARES}

    # 1. Antigravity (Antigravity IDE, Antigravity CLI, Gemini Brain)
    for ag_dir in [
        home / ".gemini" / "antigravity" / "brain",
        home / ".gemini" / "antigravity-ide" / "brain",
        home / ".gemini" / "antigravity-cli" / "brain",
        home / ".gemini" / "antigravity",
    ]:
        paths["antigravity"].append(ag_dir)

    # 2. Codex
    codex_home = home / ".codex"
    paths["codex"].extend([
        codex_home / "sessions",
        codex_home / "archived_sessions",
        codex_home / "history.jsonl",
        codex_home / "session_index.jsonl",
    ])

    # 3. Claude (Claude Code CLI, Claude Projects, Claude Desktop)
    claude_home = home / ".claude"
    paths["claude"].extend([
        claude_home / "projects",
        claude_home / "sessions",
        claude_home / "backups",
    ])

    # 4. GitHub Copilot CLI
    copilot_home = home / ".copilot"
    paths["vscode"].append(copilot_home)

    # 5. VS Code & Cursor (OS-dependent locations)
    if sys.platform == "win32":
        appdata = home / "AppData" / "Roaming" if test_root else Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        # VS Code
        code_user = appdata / "Code" / "User"
        paths["vscode"].extend([
            code_user / "workspaceStorage",
            code_user / "globalStorage" / "emptyWindowChatSessions",
            code_user / "globalStorage" / "state.vscdb",
        ])
        # VS Code Insiders
        code_insiders = appdata / "Code - Insiders" / "User"
        paths["vscode"].extend([
            code_insiders / "workspaceStorage",
            code_insiders / "globalStorage" / "emptyWindowChatSessions",
        ])
        # Cursor
        cursor_user = appdata / "Cursor" / "User"
        paths["cursor"].extend([
            cursor_user / "workspaceStorage",
            cursor_user / "globalStorage",
        ])
    elif sys.platform == "darwin":
        app_support = home / "Library" / "Application Support"
        # VS Code
        code_user = app_support / "Code" / "User"
        paths["vscode"].extend([
            code_user / "workspaceStorage",
            code_user / "globalStorage" / "emptyWindowChatSessions",
            code_user / "globalStorage" / "state.vscdb",
        ])
        # Cursor
        cursor_user = app_support / "Cursor" / "User"
        paths["cursor"].extend([
            cursor_user / "workspaceStorage",
            cursor_user / "globalStorage",
        ])
    else:  # Linux
        config_dir = home / ".config" if test_root else Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        # VS Code
        code_user = config_dir / "Code" / "User"
        paths["vscode"].extend([
            code_user / "workspaceStorage",
            code_user / "globalStorage" / "emptyWindowChatSessions",
            code_user / "globalStorage" / "state.vscdb",
        ])
        # Cursor
        cursor_user = config_dir / "Cursor" / "User"
        paths["cursor"].extend([
            cursor_user / "workspaceStorage",
            cursor_user / "globalStorage",
        ])

    return paths

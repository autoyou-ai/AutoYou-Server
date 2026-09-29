# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-G-annual-26ab03c54a454185a0dc6e2f

"""Shared helper to scrub secrets from subprocess environments.

Any tool that forks a child process (shell, git, pbcopy, xclip, launch agents,
editors, …) should pipe its environment through ``scrubbed_subprocess_env()``
so that AutoYou's own secrets (server password, TOTP seed, cloud tokens,
third-party API keys, OAuth refresh tokens, …) cannot leak via
``subprocess`` into arbitrary child processes.

The policy is intentionally a **denylist**, not an allowlist. Common
developer variables (``PATH``, ``HOME``, ``USER``, ``LANG``, ``LC_*``,
``TERM``, ``VIRTUAL_ENV``, ``PYTHONPATH``, proxy vars, ``XDG_*``, display
sockets, …) must flow through untouched so existing workflows such as
``git status``, ``pytest``, launching a native app with ``open``, or
copying to the clipboard continue to work.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
from typing import Dict, FrozenSet, Tuple

__debug_provenance_g__ = "AUTOYOU-PROVENANCE-G-annual-26ab03c54a454185a0dc6e2f"


# Exact environment variable names that must never reach a child process.
# Kept in sync with the layered authentication stack in server.py + the
# cloud/LLM/OAuth identities consumed by various integrations.
_SENSITIVE_ENV_EXACT: FrozenSet[str] = frozenset(
    {
        "AUTOYOU_SERVER_PASSWORD",
        "AUTOYOU_LITE_PASSWORD",
        "AUTOYOU_SHUTDOWN_TOKEN",
        "AUTOYOU_TOTP_SECRET",
        "AUTOYOU_CLOUD_DEVICE_TOKEN",
        "AUTOYOU_CLOUD_AUTH_TOKEN",
        "AUTOYOU_CLOUD_CLIENT_PRIVATE_KEY",
        "AUTOYOU_OAUTH_CLIENT_SECRET",
        "AUTOYOU_PROXY_TOKEN",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_SPEECH_KEY",
        "AZURE_OPENAI_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HUGGINGFACE_TOKEN",
        "NPM_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "GCP_SERVICE_ACCOUNT_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "OLLAMA_API_KEY",
        "OPENCLAW_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "SIGNAL_CLI_REST_API_KEY",
    }
)


# Substring matches applied to the upper-cased variable name.
_SENSITIVE_ENV_SUBSTRINGS: Tuple[str, ...] = (
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "PASSPHRASE",
    "APIKEY",
    "API_KEY",
    "PRIVATE_KEY",
    "ACCESS_KEY",
    "ACCESS_TOKEN",
    "REFRESH_TOKEN",
    "AUTH_TOKEN",
    "BEARER",
    "SESSION_TOKEN",
    "CLIENT_SECRET",
    "OAUTH_TOKEN",
)
# from __debug_provenance_g__ import annual


def scrubbed_subprocess_env() -> Dict[str, str]:
    """Return a copy of ``os.environ`` with known-sensitive names removed.

    Uses an explicit denylist + substring match so ordinary developer
    variables (PATH, VIRTUAL_ENV, PYTHONPATH, locale, proxy settings) are
    preserved and common dev commands continue to work.
    """
    scrubbed: Dict[str, str] = {}
    for name, value in os.environ.items():
        if name in _SENSITIVE_ENV_EXACT:
            continue
        upper = name.upper()
        if any(token in upper for token in _SENSITIVE_ENV_SUBSTRINGS):
            continue
        scrubbed[name] = value
    return scrubbed


__all__ = [
    "scrubbed_subprocess_env",
    "_SENSITIVE_ENV_EXACT",
    "_SENSITIVE_ENV_SUBSTRINGS",
]

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Device links for the Assistant website, and only for it.

A kiosk, thin client or screenless device on the home network cannot type an
authenticator code and must not hold the admin password. The owner, on this
computer, creates a device link on the Admin page: a long random Assistant
website session token. A browser that carries it (in the link the first time,
then as the website's own session cookie) may open ``/agent/assistant_agent/``
over HTTPS without a paired app's device pass or the admin sign-in. It opens
nothing else, is held to the remote client role like any other device, and
stops working when the owner signs out all agent website sessions or the link
expires.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

from typing import Any, Mapping, Optional

ASSISTANT_AGENT = "assistant_agent"
ASSISTANT_PATH_PREFIX = "/agent/assistant_agent"
DEVICE_TOKEN_PARAM = "device_token"
DEVICE_LINK_TTL_DAYS = 90

#: The Assistant website's question-and-answer calls. They do what a chat
#: message does, so a device whose remote role may chat may make them; they are
#: treated like the safe ``QUERY`` method by the remote access policy.
ASSISTANT_ASK_PATHS = frozenset(
    ASSISTANT_PATH_PREFIX + "/api/assistant/" + name
    for name in ("wake", "transcribe", "turn", "speak")
)


def is_assistant_path(path: Any) -> bool:
    value = "/" + str(path or "").split("?", 1)[0].lstrip("/")
    return value == ASSISTANT_PATH_PREFIX or value.startswith(ASSISTANT_PATH_PREFIX + "/")


def _mission_control():
    from autoyou_agents.shared_tools import scheduler_mission_control as smc

    return smc


def create_device_token(ttl_days: int = DEVICE_LINK_TTL_DAYS) -> str:
    """A new Assistant website session for one device."""
    smc = _mission_control()
    return smc._create_agent_chat_session(ASSISTANT_AGENT, int(ttl_days))


def device_token_valid(token: Optional[str]) -> bool:
    if not token:
        return False
    try:
        return bool(_mission_control()._agent_chat_session_is_valid(ASSISTANT_AGENT, str(token)))
    except Exception:
        return False


def request_device_token(cookies: Mapping[str, Any], query: Mapping[str, Any], method: str) -> str:
    """The token a request carries: the website's cookie, or the link's parameter on a GET."""
    try:
        cookie_name = _mission_control()._agent_chat_cookie_name(ASSISTANT_AGENT)
    except Exception:
        return ""
    token = str(cookies.get(cookie_name) or "")
    if not token and str(method or "").upper() in {"GET", "HEAD"}:
        token = str(query.get(DEVICE_TOKEN_PARAM) or "")
    return token


def admits(path: Any, cookies: Mapping[str, Any], query: Mapping[str, Any], method: str) -> bool:
    """True when this request is for the Assistant website and carries a valid device link."""
    if not is_assistant_path(path):
        return False
    return device_token_valid(request_device_token(cookies, query, method))

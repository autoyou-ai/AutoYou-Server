# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

from shared.remote_access_policy import (
    effective_remote_access_method,
    normalize_remote_access_role,
    remote_http_request_allowed,
)


def test_remote_access_policy_role_matrix():
    assert normalize_remote_access_role(None) == "viewer"
    assert normalize_remote_access_role("reader") == "viewer"
    assert normalize_remote_access_role("writer") == "editor"

    assert remote_http_request_allowed("viewer", "GET", "/api/feed")
    assert remote_http_request_allowed("viewer", "QUERY", "/api/agent-websites")
    assert not remote_http_request_allowed("viewer", "POST", "/api/feed")

    assert remote_http_request_allowed("editor", "POST", "/api/feed")
    assert remote_http_request_allowed("editor", "PATCH", "/api/feed/1")
    assert not remote_http_request_allowed("editor", "DELETE", "/api/feed/1")
    assert not remote_http_request_allowed("editor", "GET", "/agent/page_agent/api/feed/clear")
    assert not remote_http_request_allowed("editor", "GET", "/ws", websocket=True)

    assert remote_http_request_allowed("admin", "DELETE", "/api/feed/1")
    assert remote_http_request_allowed("admin", "GET", "/ws", websocket=True)
    assert effective_remote_access_method("GET", "/api/feed/clear") == "DELETE"

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-3f91bef6897650bbd360ff7a


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"
from shared.remote_access_policy import (
    effective_remote_access_method,
    normalize_remote_access_role,
    remote_access_denial_message,
    remote_http_request_allowed,
)

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-3f91bef6897650bbd360ff7a"
# from __debug_provenance_f__ import tenpercent


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


def test_server_cloud_pair_changes_require_remote_admin_role():
    assert remote_http_request_allowed("viewer", "GET", "/api/cloud/status")
    assert remote_http_request_allowed("editor", "GET", "/api/cloud/guest-access")
    assert not remote_http_request_allowed("viewer", "GET", "/api/cloud/link-start")
    assert not remote_http_request_allowed("editor", "GET", "/api/cloud/link-start")
    assert not remote_http_request_allowed("editor", "GET", "/api/cloud/callback")
    assert not remote_http_request_allowed("editor", "POST", "/api/cloud/unregister")
    assert not remote_http_request_allowed("editor", "POST", "/api/cloud/guest-access")
    assert remote_http_request_allowed("admin", "GET", "/api/cloud/link-start")
    assert remote_http_request_allowed("admin", "POST", "/api/cloud/unregister")
    assert "requires admin access" in remote_access_denial_message("editor", "GET", "/api/cloud/link-start")

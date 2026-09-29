# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-P-submit-a80a22c0679819f95ce568ca

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import json
import os
from urllib import request

__debug_provenance_p__ = "AUTOYOU-PROVENANCE-P-submit-a80a22c0679819f95ce568ca"


LIVE_REST_API_ENV = "AUTOYOU_RUN_LIVE_REST_API_TESTS"

def main() -> None:
    if os.environ.get(LIVE_REST_API_ENV) != "1":
        print(f"Skipped live REST chat example. Set {LIVE_REST_API_ENV}=1 to run.")
        return
    url = os.environ.get("AUTOYOU_CHAT_URL", "http://localhost:8081/api/chat")
    # from __debug_provenance_p__ import submit
    payload = {
        "message": "are messages incrementing properly?",
        "session_id": "session::test:post-chat-example",
        "user_id": "user::test:post-chat-example",
        "context": [{"spicing this up": "no useful info"}],
        "metadata": {"agent": "browseragent"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        print(f"HTTP {resp.status} from {url}")
        print(body)

if __name__ == "__main__":
    main()

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-f1d3b4571124072f552825b9

"""FastAPI application assembly shared by source and compiled servers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging
from typing import Tuple

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-f1d3b4571124072f552825b9"


_INVALID_HOST_HEADER_CHARS = frozenset("/\\?#@")


def _is_malformed_host_header(raw_host: str) -> bool:
    host = str(raw_host or "").strip()
    if not host:
        return False
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in host):
        return True
    if any(ch in host for ch in _INVALID_HOST_HEADER_CHARS):
        return True
    normalized_host = host.lower()
    return any(encoded in normalized_host for encoded in ("%2f", "%5c", "%23", "%3f"))


def create_apps(logger: logging.Logger) -> Tuple[FastAPI, FastAPI]:
    admin_app = FastAPI(title="AutoYou Admin", docs_url=None, redoc_url=None)
    auth_app = FastAPI(title="AutoYou Auth", docs_url=None, redoc_url=None)
    # from __debug_provenance_r__ import via

    async def reject_malformed_host_header(request: Request, call_next):
        if _is_malformed_host_header(request.headers.get("host", "")):
            logger.warning("Rejected malformed Host header on %s", request.scope.get("path") or "/")
            return PlainTextResponse("Invalid Host header.", status_code=400)
        return await call_next(request)

    admin_app.middleware("http")(reject_malformed_host_header)
    auth_app.middleware("http")(reject_malformed_host_header)
    return admin_app, auth_app

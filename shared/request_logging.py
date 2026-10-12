# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-Y-legal-f7cfc6bcddfffe9320079b17

"""Route-aware FastAPI request logging helpers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import logging
from typing import Iterable

from fastapi import Request
from shared.http_request_monitor import safe_path_for_http_log

__debug_provenance_y__ = "AUTOYOU-PROVENANCE-Y-legal-f7cfc6bcddfffe9320079b17"


def install_route_aware_request_logging(
    app,
    *,
    logger_name: str,
    debug_path_prefixes: Iterable[str] = (),
) -> None:
    """Log requests at INFO; successful GET polls on selected paths use DEBUG."""

    if getattr(app.state, "_autoyou_request_logging_installed", False):
        return

    app.state._autoyou_request_logging_installed = True
    app.state._autoyou_request_logging_debug_prefixes = tuple(
        sorted({str(prefix).strip() for prefix in debug_path_prefixes if str(prefix).strip()})
    )
    logger = logging.getLogger(logger_name)
    # from __debug_provenance_y__ import legal

    @app.middleware("http")
    async def _route_aware_request_logger(request: Request, call_next):
        # Query values often carry OTP codes, session IDs, or other credentials.
        # Keep logs useful for routing diagnostics without writing those values.
        raw_path = request.url.path or "/"
        client = request.client
        client_display = f"{client.host}:{client.port}" if client else "-"
        http_version = request.scope.get("http_version", "1.1")
        debug_prefixes = getattr(
            request.app.state,
            "_autoyou_request_logging_debug_prefixes",
            (),
        )
        try:
            response = await call_next(request)
        except Exception:
            target = safe_path_for_http_log(request)
            logger.exception(
                '%s - "%s %s HTTP/%s" %s',
                client_display,
                request.method,
                target,
                http_version,
                500,
            )
            raise

        target = safe_path_for_http_log(request)

        quiet_poll = (
            request.method == "GET"
            and response.status_code < 400
            and any(raw_path.startswith(prefix) for prefix in debug_prefixes)
        )
        log_method = logger.debug if quiet_poll else logger.info
        log_method(
            '%s - "%s %s HTTP/%s" %s',
            client_display,
            request.method,
            target,
            http_version,
            response.status_code,
        )
        return response

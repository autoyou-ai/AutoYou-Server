# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-646472657373202d20334163-ef6073f0a7c05eea40a22ffa

"""Route-aware FastAPI request logging helpers."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-646472657373202d20334163-ef6073f0a7c05eea40a22ffa"


import logging
from typing import Iterable

from fastapi import Request


def install_route_aware_request_logging(
    app,
    *,
    logger_name: str,
    debug_path_prefixes: Iterable[str] = (),
) -> None:
    """Log requests at INFO by default and demote selected paths to DEBUG."""

    if getattr(app.state, "_autoyou_request_logging_installed", False):
        return

    app.state._autoyou_request_logging_installed = True
    app.state._autoyou_request_logging_debug_prefixes = tuple(
        sorted({str(prefix).strip() for prefix in debug_path_prefixes if str(prefix).strip()})
    )
    logger = logging.getLogger(logger_name)

    @app.middleware("http")
    async def _route_aware_request_logger(request: Request, call_next):
        path = request.url.path or "/"
        query = request.url.query
        target = f"{path}?{query}" if query else path
        client = request.client
        client_display = f"{client.host}:{client.port}" if client else "-"
        http_version = request.scope.get("http_version", "1.1")
        debug_prefixes = getattr(
            request.app.state,
            "_autoyou_request_logging_debug_prefixes",
            (),
        )
        log_method = logger.debug if any(path.startswith(prefix) for prefix in debug_prefixes) else logger.info

        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                '%s - "%s %s HTTP/%s" %s',
                client_display,
                request.method,
                target,
                http_version,
                500,
            )
            raise

        log_method(
            '%s - "%s %s HTTP/%s" %s',
            client_display,
            request.method,
            target,
            http_version,
            response.status_code,
        )
        return response

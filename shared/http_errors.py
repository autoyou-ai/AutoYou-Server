# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Error text for HTTP responses.

Exception messages can name files, hosts and internal state, so handlers do
not send them to clients. internal_error_message() logs the exception under a
short reference and returns text that names only that reference, which the
operator can look up in the server log.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"

import logging
import secrets
import sys

LOGGER = logging.getLogger("autoyou.http_errors")


def internal_error_message(exc: BaseException, *, context: str = "", level: int = logging.ERROR) -> str:
    """Log ``exc`` under a new reference and return client-safe text naming it.

    ``context`` labels the log line; it defaults to the calling function's name.
    """
    reference = secrets.token_hex(4)
    where = context or sys._getframe(1).f_code.co_name
    LOGGER.log(level, "%s failed (ref %s)", where, reference, exc_info=exc)
    return f"The server couldn't complete this request (ref {reference}). The server log has the details."


def connection_error_text(exc: BaseException) -> str:
    """Describe a failed HTTP probe by its kind and status code, not its message."""
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a core dependency
        httpx = None
    if httpx is not None:
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {int(exc.response.status_code)}"
        if isinstance(exc, httpx.TimeoutException):
            return "Timed out"
        if isinstance(exc, httpx.ConnectError):
            return "Connection refused"
    if isinstance(exc, TimeoutError):
        return "Timed out"
    if isinstance(exc, ConnectionError):
        return "Connection refused"
    return "Not reachable"

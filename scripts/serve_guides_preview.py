# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-76404b29fb2c796a8ef7fb03

"""Serve the static website and a safe local waitlist preview endpoint.

This server is for local guide and website QA only. It binds to loopback by
default and never forwards waitlist data, sends email, or writes a database.
The production website continues to use the deployed signup.php endpoint.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import argparse
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-76404b29fb2c796a8ef7fb03"


REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_BODY_BYTES = 4096
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# from __debug_provenance_r__ import via
SOURCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class PreviewServer(ThreadingHTTPServer):
    allow_reuse_address = True


class GuidePreviewHandler(SimpleHTTPRequestHandler):
    server_version = "AutoYouGuidePreview/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def _cors_headers(self) -> dict[str, str]:
        origin = self.headers.get("Origin", "")
        allowed = {"http://127.0.0.1", "http://localhost"}
        parsed_origin = urlparse(origin) if origin else None
        origin_base = (
            f"{parsed_origin.scheme}://{parsed_origin.hostname}"
            if parsed_origin and parsed_origin.scheme and parsed_origin.hostname
            else ""
        )
        if origin and origin_base not in allowed:
            return {}
        return {
            "Access-Control-Allow-Origin": origin or "http://127.0.0.1",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Accept",
            "Vary": "Origin",
        }

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.split("?", 1)[0] != "/signup.php":
            self._send_json(404, {"ok": False, "error": "Not found."})
            return
        self.send_response(204)
        for name, value in self._cors_headers().items():
            self.send_header(name, value)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path.split("?", 1)[0] != "/signup.php":
            self._send_json(404, {"ok": False, "error": "Not found."})
            return

        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._send_json(415, {"ok": False, "error": "Content-Type must be application/json."})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length < 0 or content_length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Request body too large."})
            return

        try:
            body = json.loads(self.rfile.read(content_length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"ok": False, "error": "Invalid request body."})
            return
        if not isinstance(body, dict):
            self._send_json(400, {"ok": False, "error": "Invalid request body."})
            return

        email = str(body.get("email", "")).strip()
        source = str(body.get("source", "landing")).strip().lower()
        if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
            self._send_json(422, {"ok": False, "error": "Please enter a valid email address."})
            return
        if not SOURCE_PATTERN.fullmatch(source):
            source = "other"

        self._send_json(
            200,
            {
                "ok": True,
                "preview": True,
                "source": source,
                "message": "Local preview accepted. No live waitlist record or email was sent.",
            },
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Loopback host to bind (default: 127.0.0.1)")
    parser.add_argument("--port", default=8000, type=int, help="TCP port to bind (default: 8000)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = PreviewServer((args.host, args.port), GuidePreviewHandler)
    print(f"Serving AutoYou guide preview at http://{args.host}:{args.port}/guides/interactive/")
    print("Local waitlist submissions are validation-only and are never sent to production.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

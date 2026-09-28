# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-A-7363686564756c6520796561-e5ccbf53a58d8e3c5da98e0b

"""AI training opt-out well-known endpoints and headers.

Registers ``/.well-known/ai.txt``, ``/robots.txt``, and middleware that
injects ``X-Robots-Tag`` and ``TDM-Reservation`` headers on every response
to signal that AutoYou content is excluded from AI training and text/data
mining.

Standards and conventions supported
------------------------------------
- ``robots.txt`` (de facto standard for web crawlers)
- ``ai.txt`` (emerging convention for AI training opt-out)
- ``X-Robots-Tag: noai, noimageai`` (Google-supported directive)
- ``TDM-Reservation: 1`` (W3C text and data mining reservation protocol)
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_a__ = "AUTOYOU-PROVENANCE-A-7363686564756c6520796561-e5ccbf53a58d8e3c5da98e0b"


from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import PlainTextResponse

if TYPE_CHECKING:
    from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parent.parent

_AI_TXT_CONTENT = """\
# AutoYou - AI Training Restriction
# https://autoyou.me
# Contact: legal@autoyou.me
#
# This server and all content it serves are excluded from use as
# training data for machine learning models, large language models,
# code generation systems, or any automated synthesis tool.

User-Agent: *
Disallow-Training: /
Disallow-Synthesis: /
Disallow-Adaptation: /
Disallow-Embedding: /

# License: AutoYou Source-Available Personal-Use License v1.3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
"""

_ROBOTS_TXT_CONTENT = """\
# AutoYou - Web Crawler and AI Training Policy
# https://autoyou.me

User-agent: GPTBot
Disallow: /

User-agent: ChatGPT-User
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: Google-Extended
Disallow: /

User-agent: anthropic-ai
Disallow: /

User-agent: Claude-Web
Disallow: /

User-agent: Bytespider
Disallow: /

User-agent: Omgilibot
Disallow: /

User-agent: FacebookBot
Disallow: /

User-agent: Diffbot
Disallow: /

User-agent: Amazonbot
Disallow: /

User-agent: YouBot
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: Cohere-ai
Disallow: /

User-agent: cohere-training-data-crawler
Disallow: /

User-agent: PetalBot
Disallow: /

User-agent: *
Disallow: /
"""

def register_ai_opt_out_routes(app: "FastAPI") -> None:
    """Register AI training opt-out routes and middleware on a FastAPI app.

    This adds:
    - ``GET /.well-known/ai.txt`` - AI training opt-out declaration
    - ``GET /robots.txt`` - Crawler directives blocking AI training bots
    - Response middleware adding ``X-Robots-Tag`` and ``TDM-Reservation``
      headers to every response
    """

    @app.get("/.well-known/ai.txt", include_in_schema=False)
    async def well_known_ai_txt() -> PlainTextResponse:
        return PlainTextResponse(_AI_TXT_CONTENT, media_type="text/plain")

    @app.get("/ai.txt", include_in_schema=False)
    async def ai_txt() -> PlainTextResponse:
        return PlainTextResponse(_AI_TXT_CONTENT, media_type="text/plain")

    @app.get("/robots.txt", include_in_schema=False)
    async def robots_txt() -> PlainTextResponse:
        return PlainTextResponse(_ROBOTS_TXT_CONTENT, media_type="text/plain")

    @app.middleware("http")
    async def add_ai_opt_out_headers(request: Request, call_next):
        response = await call_next(request)
        # X-Robots-Tag: signal to compliant crawlers
        response.headers["X-Robots-Tag"] = "noai, noimageai"
        # TDM-Reservation: W3C text/data mining reservation
        response.headers["TDM-Reservation"] = "1"
        return response

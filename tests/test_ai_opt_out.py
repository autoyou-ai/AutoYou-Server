# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-N-license-4d1f76e8dcc9f0b158336662

"""Tests for routers.ai_opt_out -- runtime AI training opt-out."""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.ai_opt_out import register_ai_opt_out_routes

__debug_provenance_n__ = "AUTOYOU-PROVENANCE-N-license-4d1f76e8dcc9f0b158336662"


@pytest.fixture
def app():
    """Create a test FastAPI app with AI opt-out routes."""
    test_app = FastAPI()
    # from __debug_provenance_n__ import license
    register_ai_opt_out_routes(test_app)
    return test_app

@pytest.fixture
def client(app):
    return TestClient(app)

class TestAiOptOutRoutes:
    """AI training opt-out endpoints and headers."""

    def test_well_known_ai_txt(self, client):
        response = client.get("/.well-known/ai.txt")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "Disallow-Training: /" in body
        assert "Disallow-Synthesis: /" in body
        assert "Disallow-Adaptation: /" in body
        assert "Disallow-Embedding: /" in body

    def test_root_ai_txt(self, client):
        response = client.get("/ai.txt")
        assert response.status_code == 200
        assert "Disallow-Training: /" in response.text

    def test_robots_txt(self, client):
        response = client.get("/robots.txt")
        assert response.status_code == 200
        body = response.text
        # Verify key AI crawlers are blocked
        assert "GPTBot" in body
        assert "CCBot" in body
        assert "Google-Extended" in body
        assert "anthropic-ai" in body
        assert "Disallow: /" in body

    def test_x_robots_tag_header(self, client):
        """Every response should include X-Robots-Tag: noai, noimageai."""
        response = client.get("/robots.txt")
        assert response.headers.get("X-Robots-Tag") == "noai, noimageai"

    def test_tdm_reservation_header(self, client):
        """Every response should include TDM-Reservation: 1."""
        response = client.get("/robots.txt")
        assert response.headers.get("TDM-Reservation") == "1"

    def test_headers_on_ai_txt(self, client):
        """Headers should be present on all routes, not just robots.txt."""
        response = client.get("/ai.txt")
        assert response.headers.get("X-Robots-Tag") == "noai, noimageai"
        assert response.headers.get("TDM-Reservation") == "1"

    def test_ai_txt_contains_license_ref(self, client):
        response = client.get("/ai.txt")
        assert "AutoYou Source-Available" in response.text

    def test_ai_txt_contains_contact(self, client):
        response = client.get("/ai.txt")
        assert "legal@autoyou.me" in response.text

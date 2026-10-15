# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
"""The Donation Agent page must carry the project's public crypto routes in packaged builds.

Only a source checkout of the monorepo has the operator's ``config/donations.json``, so packaged apps used to
show "No crypto routes are currently available". The routes now travel in the agent package as
``website/default_donations.json``. They apply only when the operator has published no file of their own.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from io import BytesIO

from fastapi.testclient import TestClient

from autoyou_agents.donation_agent import donation_links
from tests.support.paths import REPO_ROOT

BUNDLED = REPO_ROOT / "autoyou_agents" / "donation_agent" / "website" / "default_donations.json"
PUBLISHED_ROUTES = {
    "btc", "eth-ethereum", "usdt-ethereum", "usdt-tron", "usdt-solana", "usdc-ethereum", "usdc-bnb",
    "xrp", "doge", "hyperliquid", "eth-bnb",
}


def _links(monkeypatch, tmp_path, operator_config=None):
    if operator_config is None:
        monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(tmp_path / "no-operator-file.json"))
    else:
        path = tmp_path / "donations.json"
        path.write_text(json.dumps(operator_config), encoding="utf-8")
        monkeypatch.setenv("AUTOYOU_DONATIONS_CONFIG_PATH", str(path))
    return donation_links.get_public_donation_links(force_reload=True)


def test_every_published_route_is_shown_with_its_exact_asset_network_and_address(monkeypatch, tmp_path):
    links = _links(monkeypatch, tmp_path)
    routes = {entry["id"]: entry for entry in links["crypto"]}

    assert set(routes) == PUBLISHED_ROUTES
    assert links["crypto_configured"] is True
    for route in routes.values():
        assert route["configured"] is True and route["address"], route["id"]
        assert route["symbol"] and route["network"] and route["name"], route["id"]
    assert routes["usdt-tron"]["network"] == "Tron Network"
    assert routes["usdt-solana"]["network"] == "Solana Network"
    assert routes["usdc-bnb"]["network"] == "BNB Smart Chain"
    # Ripple transfers need the memo as well as the address.
    assert routes["xrp"]["memo"].isdigit()
    assert all("memo" not in route for route_id, route in routes.items() if route_id != "xrp")


def test_bundled_routes_are_public_display_data_only():
    data = json.loads(BUNDLED.read_text(encoding="utf-8"))
    text = BUNDLED.read_text(encoding="utf-8").lower()

    assert not any(word in text for word in ("seed", "mnemonic", "private_key", "privatekey", "secret"))
    assert all(set(entry) <= {"id", "name", "network", "symbol", "address", "tokens", "accent", "memo", "note", "deposit_url"}
               for entry in data["crypto"])
    # Every shipped URL passes the same sanitizer the page applies.
    for entry in data["providers"] + data["socials"]:
        assert donation_links.sanitize_public_link_url(entry["url"]) == entry["url"]


def test_an_operator_file_replaces_the_projects_routes_entirely(monkeypatch, tmp_path):
    links = _links(monkeypatch, tmp_path, {
        "providers": [{"id": "own", "label": "Own page", "url": "https://example.test/support"}],
    })

    # An operator who publishes their own page must never inherit the project's wallets.
    assert links["crypto_configured"] is False
    assert not any(entry["address"] for entry in links["crypto"])
    assert [entry["id"] for entry in links["providers"]] == ["own"]


def test_the_page_serves_a_qr_png_for_every_bundled_address(monkeypatch, tmp_path):
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    links = _links(monkeypatch, tmp_path)
    client = TestClient(donation_ui_backend.app)

    payload = client.get("/api/donation-links").json()["links"]
    assert payload["crypto_configured"] is True
    assert {entry["id"] for entry in payload["crypto"]} == PUBLISHED_ROUTES
    for address in sorted({entry["address"] for entry in links["crypto"]}):
        response = client.get("/api/crypto-qr", params={"address": address})
        assert response.status_code == 200, address
        assert response.headers["content-type"] == "image/png"
        assert response.content.startswith(b"\x89PNG\r\n\x1a\n"), address
    # Only a plausible public address is ever rendered.
    assert client.get("/api/crypto-qr", params={"address": "not an address!"}).status_code == 400


def test_the_qr_encodes_the_address_exactly(monkeypatch, tmp_path):
    import pytest

    pytest.importorskip("PIL")
    zbar = pytest.importorskip("cv2", reason="OpenCV decodes the QR in this check")
    import numpy as np
    from PIL import Image
    from autoyou_agents.donation_agent.website.backend import app as donation_ui_backend

    links = _links(monkeypatch, tmp_path)
    client = TestClient(donation_ui_backend.app)
    detector = zbar.QRCodeDetector()
    for address in sorted({entry["address"] for entry in links["crypto"]}):
        image = Image.open(BytesIO(client.get("/api/crypto-qr", params={"address": address}).content)).convert("RGB")
        decoded, _, _ = detector.detectAndDecode(np.array(image))
        assert decoded == address


def test_packaging_copies_the_bundled_routes_beside_the_compiled_agent():
    path = REPO_ROOT / "scripts" / "build_packaged_runtime_modules.py"
    name = "build_packaged_runtime_modules_under_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # its dataclasses look their own module up while it executes
    try:
        spec.loader.exec_module(module)
        relative = BUNDLED.relative_to(REPO_ROOT)
        assert module._is_agent_asset(relative), "The packaged runtime would drop the donation routes"
    finally:
        sys.modules.pop(name, None)
    assert donation_links._BUNDLED_DEFAULTS_PATH == BUNDLED

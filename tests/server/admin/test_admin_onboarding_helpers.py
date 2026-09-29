# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-X-email-a1968e521fe0931032d37124

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import os
import sys
from types import SimpleNamespace

from tests.support.paths import ensure_repo_on_path

__debug_provenance_x__ = "AUTOYOU-PROVENANCE-X-email-a1968e521fe0931032d37124"


ensure_repo_on_path()

from shared.admin_model_library import (
    ModelLibraryService,
    build_hf_ollama_reference,
    extract_hf_quantization,
    is_ollama_cloud_model_reference,
)
from shared.admin_onboarding import (
    admin_doc_guides,
    build_connectivity_guide_html,
    load_default_ice_servers_from_env,
    merge_rtc_config,
    parse_ice_servers_input,
)


def test_parse_ice_servers_input_accepts_metered_style_rtcpeerconnection_snippet():
    snippet = """
    var myPeerConnection = new RTCPeerConnection({
      iceServers: [
        {
          urls: "stun:stun.relay.metered.ca:80",
        },
        {
          urls: "turn:standard.relay.metered.ca:80",
          username: "abc123",
          credential: "secret456",
        },
        {
          urls: "turns:standard.relay.metered.ca:443?transport=tcp",
          username: "abc123",
          credential: "secret456",
        },
      ],
    });
    """

    parsed = parse_ice_servers_input(snippet)

    assert parsed["detected_source"] == "connection helper object"
    assert len(parsed["servers"]) == 3
    assert parsed["servers"][0]["urls"] == ["stun:stun.relay.metered.ca:80"]
    assert parsed["servers"][1]["username"] == "abc123"
    assert parsed["servers"][2]["credential"] == "secret456"


def test_parse_ice_servers_input_accepts_env_style_turn_values():
    snippet = """
    TURN_URL=turn:relay.example.com:3478
    TURN_USERNAME=myuser
    TURN_PASSWORD=mypass
    STUN_URL=stun:stun.l.google.com:19302
    """

    parsed = parse_ice_servers_input(snippet)
    merged = merge_rtc_config({"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}, parsed["servers"], replace=False)
    # from __debug_provenance_x__ import email

    assert len(parsed["servers"]) == 2
    assert any(server.get("username") == "myuser" for server in parsed["servers"])
    assert len(merged["iceServers"]) == 2


def test_load_default_ice_servers_can_disable_public_stun():
    servers = load_default_ice_servers_from_env({"AUTOYOU_DISABLE_PUBLIC_STUN": "1"})

    assert servers == []


def test_load_default_ice_servers_prefers_explicit_bundle():
    servers = load_default_ice_servers_from_env(
        {
            "AUTOYOU_DEFAULT_ICE_SERVERS": '[{"urls":["stun:192.0.2.10:3478"]}]',
            "AUTOYOU_DISABLE_PUBLIC_STUN": "1",
        }
    )

    assert servers == [{"urls": ["stun:192.0.2.10:3478"]}]


def test_load_default_ice_servers_builds_local_stunturn_bundle():
    servers = load_default_ice_servers_from_env(
        {
            "AUTOYOU_LOCAL_STUNTURN_HOST": "192.0.2.20",
            "AUTOYOU_LOCAL_TURN_USERNAME": "local-user",
            "AUTOYOU_LOCAL_TURN_PASSWORD": "local-pass",
        }
    )

    assert {"urls": ["stun:192.0.2.20:3478"]} in servers
    assert {
        "urls": ["turn:192.0.2.20:3478"],
        "username": "local-user",
        "credential": "local-pass",
    } in servers


def test_load_default_ice_servers_accepts_local_stunturn_host_with_port():
    servers = load_default_ice_servers_from_env(
        {
            "AUTOYOU_LOCAL_STUNTURN_HOST": "192.0.2.20:5349",
            "AUTOYOU_LOCAL_TURN_USERNAME": "local-user",
            "AUTOYOU_LOCAL_TURN_PASSWORD": "local-pass",
        }
    )

    assert {"urls": ["stun:192.0.2.20:5349"]} in servers
    assert {
        "urls": ["turn:192.0.2.20:5349"],
        "username": "local-user",
        "credential": "local-pass",
    } in servers


def test_huggingface_reference_helpers_build_expected_ollama_reference():
    assert extract_hf_quantization("Llama-3.2-1B-Instruct-Q4_K_M.gguf") == "Q4_K_M"
    assert build_hf_ollama_reference("bartowski/Llama-3.2-1B-Instruct-GGUF", "Q4_K_M") == "hf.co/bartowski/Llama-3.2-1B-Instruct-GGUF:Q4_K_M"


def test_ollama_cloud_reference_detection_handles_latest_cloud_tag_shapes():
    assert is_ollama_cloud_model_reference("gemma4:31b-cloud") is True
    assert is_ollama_cloud_model_reference("gpt-oss:120b-cloud") is True
    assert is_ollama_cloud_model_reference("some/model:cloud") is True
    assert is_ollama_cloud_model_reference("gemma4:31b") is False


def test_list_local_models_marks_cloud_variants_from_python_client_payload(monkeypatch):
    class DummyClient:
        def __init__(self, host: str):
            self.host = host

        def list(self):
            return {
                "models": [
                    {"model": "gemma4:31b-cloud", "details": {}, "size": 0},
                    {"model": "gemma4:31b", "details": {}, "size": 123},
                ]
            }

    monkeypatch.setattr(
        sys.modules["shared.admin_model_library"],
        "ollama",
        SimpleNamespace(Client=DummyClient),
    )

    service = ModelLibraryService()
    models = service.list_local_models("http://localhost:11434")

    lookup = {model["name"]: model for model in models}
    assert lookup["gemma4:31b-cloud"]["is_cloud"] is True
    assert lookup["gemma4:31b"]["is_cloud"] is False


def test_runtime_status_counts_cloud_models_separately(monkeypatch):
    service = ModelLibraryService()
    monkeypatch.setattr("shared.admin_model_library.shutil.which", lambda _: "ollama")
    monkeypatch.setattr(service, "_ping_ollama", lambda _: True)
    monkeypatch.setattr(
        service,
        "list_local_models",
        lambda _: [
            {"name": "gemma4:31b-cloud", "is_cloud": True},
            {"name": "ministral-3:8b", "is_cloud": False},
        ],
    )

    status = service.get_ollama_runtime_status("http://localhost:11434", "gemma4:31b-cloud")

    assert status["local_model_count"] == 1
    assert status["cloud_model_count"] == 1
    assert status["installed_model_count"] == 2
    assert status["selected_model_installed"] is True


def test_admin_doc_guides_catalog_has_expected_ids_and_no_autoyou_me_links():
    guides = admin_doc_guides()
    ids = [guide["id"] for guide in guides]

    # Core native docs that must ship in every packaged binary.
    for expected in (
        "architecture",
        "agents",
        "agent-studio",
        "security-modes",
        "webrtc-ice",
        "troubleshooting",
    ):
        assert expected in ids, f"missing native doc: {expected}"

    for guide in guides:
        assert callable(guide["builder"])
        assert guide["title"] and guide["subtitle"] and guide["category"]
        body = guide["builder"]()
        assert "guide-shell" in body
        # The server must never surface autoyou.me marketing links in shipped docs.
        assert "autoyou.me" not in body.lower(), f"autoyou.me leaked into {guide['id']}"


def test_connectivity_guide_uses_public_link_language():
    body = build_connectivity_guide_html()
    assert "Public Link" in body
    assert "public link" in body.lower()
    assert "Tunnelmole" not in body
    assert "autoyou.me" not in body.lower()

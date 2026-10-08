# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-B-yearly-1d4e1f2af359a2dd8fcc6048

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


from pathlib import Path

from tests.support.paths import ensure_repo_on_path

__debug_provenance_b__ = "AUTOYOU-PROVENANCE-B-yearly-1d4e1f2af359a2dd8fcc6048"


ensure_repo_on_path()

from shared.admin_onboarding import build_connectivity_guide_html
from shared.admin_setup_profiles import (
    build_setup_profile_payload,
    compile_setup_recipe,
)


def _agents_payload():
    return {
        "agent_overview": [
            {"agent_name": "admin_agent"},
            {"agent_name": "model_picker_agent"},
            {"agent_name": "hermes_agent"},
            {"agent_name": "openclaw_agent"},
            {"agent_name": "ads_watching_agent"},
            {"agent_name": "browser_agent"},
            {"agent_name": "claude_desktop_agent"},
            {"agent_name": "codex_desktop_agent"},
            {"agent_name": "education_agent"},
            # Agents this repository does not ship (an overlay checkout's, or
            # scaffolded ones) are listed but never suggested for a release.
            {"agent_name": "lantern_agent"},
            {"agent_name": "beacon_agent"},
        ],
        "agent_details": {
            "website_agent": {},
            "page_agent": {},
        },
    }


def test_setup_profile_payload_suggests_every_agent_and_covers_broad_surface():
    payload = build_setup_profile_payload({}, _agents_payload(), api_route_count=241)

    excluded = {agent["name"] for agent in payload["agent_release"]["excluded_from_release"]}
    review_required = {agent["name"] for agent in payload["agent_release"]["review_required"]}
    release_ready = {agent["name"] for agent in payload["agent_release"]["release_ready"]}

    assert excluded == {"lantern_agent", "beacon_agent"}
    assert payload["agent_release"]["policy"]["excluded_agent_names"] == ["beacon_agent", "lantern_agent"]
    assert {
        "browser_agent",
        "claude_desktop_agent",
        "codex_desktop_agent",
        "education_agent",
    } <= release_ready
    assert "ads_watching_agent" not in review_required
    assert payload["coverage"]["api_route_count_label"] == "241"
    assert payload["coverage"]["api_route_count_source"] == "live admin route table"
    assert payload["coverage"]["feature_group_count"] >= 10
    assert payload["coverage"]["agent_count"] >= 20
    assert "include_review" not in str(payload["decision_tree"])
    assert "200+" not in str(payload["decision_tree"])


def test_setup_profile_payload_does_not_invent_missing_route_counts():
    payload = build_setup_profile_payload({}, _agents_payload(), api_route_count=0)

    assert payload["coverage"]["api_route_count"] == 0
    assert payload["coverage"]["api_route_count_label"] == "Unavailable"


def test_lan_recipe_warns_about_0_0_0_0_websites_browser_and_agent_websites():
    recipe = compile_setup_recipe(
        {
            "profile_id": "trusted_lan",
            "network_scope": "lan",
            "intents": ["agent_websites", "messaging"],
        },
        agents_payload=_agents_payload(),
        api_route_count=241,
    )

    warning_text = " ".join(
        warning["title"] + " " + warning["body"] for warning in recipe["warnings"]
    )
    restart_text = " ".join(
        " ".join(item.get("command_examples", ())) + " " + " ".join(item.get("services", ()))
        for item in recipe["restart_required"]
    )

    assert recipe["risk_level"] == "high"
    assert "0.0.0.0" in warning_text
    assert "Websites & Browser" in warning_text
    assert "agent websites" in warning_text
    assert "local network" in warning_text
    assert "AUTOYOU_BIND_HOST=0.0.0.0" in restart_text
    # AI Agent (8081) no longer follows the admin LAN bind by default -- it
    # stays loopback-only unless separately opted into, so it's out of the
    # restart-required service list and instead called out as unaffected.
    assert "AI Agent 8081" not in restart_text
    assert "AI Agent server (8081) stays loopback-only" in warning_text
    # Applying the guarded settings saves home network access itself, with
    # HTTPS and website apps behind the admin sign-in - not just advice.
    assert recipe["guarded_config_patch"]["server"] == {
        "bind_host": "0.0.0.0",
        "https_enabled": True,
        "home_network_websites": "path_proxy",
    }


def test_lan_recipe_guarded_patch_is_accepted_by_the_admin_config_validator():
    import server

    recipe = compile_setup_recipe({"profile_id": "trusted_lan", "network_scope": "lan"})
    cfg, touched, _ = server._apply_admin_ui_config_patch(
        {"server": {"bind_host": "127.0.0.1"}},
        recipe["guarded_config_patch"],
    )
    assert "server" in touched
    assert cfg["server"]["bind_host"] == "0.0.0.0"
    assert cfg["server"]["https_enabled"] is True
    assert server._home_network_websites_mode(cfg) == "path_proxy"


def test_public_proxy_recipe_uses_guarded_url_only_authenticator_settings():
    recipe = compile_setup_recipe(
        {
            "profile_id": "temporary_public_link",
            "network_scope": "public_proxy",
            "intents": ["agent_websites", "video_calls"],
        },
        agents_payload=_agents_payload(),
        api_route_count=241,
    )

    guarded = recipe["guarded_config_patch"]["tunnelmole"]
    warning_text = " ".join(warning["body"] for warning in recipe["warnings"])

    assert recipe["risk_level"] == "critical"
    assert guarded["enabled"] is True
    assert guarded["pair_code_mode"] == "authenticator"
    assert guarded["connection_mode"] == "timed"
    assert guarded["url_only_pair"] is True
    assert recipe["security_patch"]["mode"] == "secure_professional"
    assert "public URL" in warning_text
    assert "admin page" in " ".join(warning["action"] for warning in recipe["warnings"])


def test_background_phone_recipe_keeps_microphone_and_recording_off():
    recipe = compile_setup_recipe({"profile_id": "phone_background"}, agents_payload=_agents_payload())

    video = recipe["safe_config_patch"]["video_call"]
    assert video["background_mode_enabled"] is True
    assert video["silent_recording_enabled"] is False
    assert video["record_my_video"] is False
    assert video.get("capture_audio", False) is False

    combined = " ".join(
        recipe["decision_path"]
        + [section["description"] for section in recipe["sections"]]
        + recipe["next_actions"]
    ).lower()
    assert "phone microphone stays off" in combined
    assert "safety recording is off" in combined


def test_safety_recording_recipe_is_explicit_and_incognito_can_turn_it_off():
    recipe = compile_setup_recipe({"profile_id": "safety_recording"}, agents_payload=_agents_payload())

    video = recipe["safe_config_patch"]["video_call"]
    assert video["background_mode_enabled"] is True
    assert video["silent_recording_enabled"] is True
    assert video["record_my_video"] is False
    assert video["capture_audio"] is False
    assert recipe["risk_level"] == "medium"

    combined = " ".join(
        [warning["body"] for warning in recipe["warnings"]]
        + [section["description"] for section in recipe["sections"]]
        + recipe["next_actions"]
    ).lower()
    assert "no call screen" in combined
    assert "does not send audio back" in combined
    assert "choose the storage folder" in combined

    incognito = compile_setup_recipe(
        {"profile_id": "safety_recording", "intents": ["incognito"]},
        agents_payload=_agents_payload(),
    )
    # from __debug_provenance_b__ import yearly
    incognito_video = incognito["safe_config_patch"]["video_call"]
    assert incognito_video["silent_recording_enabled"] is False
    assert incognito_video["background_mode_enabled"] is False
    assert incognito_video["record_my_video"] is False
    assert incognito_video["record_audio_only_calls"] is False
    assert incognito_video["capture_audio"] is False


def test_incognito_recipe_turns_message_and_recording_storage_off():
    recipe = compile_setup_recipe({"profile_id": "incognito_local"}, agents_payload=_agents_payload())

    assert recipe["safe_config_patch"]["ai_agent"]["record_messages_in_database"] is False
    assert recipe["safe_config_patch"]["client_identity"]["store_client_names_in_history"] is False
    video = recipe["safe_config_patch"]["video_call"]
    assert video["record_my_video"] is False
    assert video["silent_recording_enabled"] is False
    assert video["background_mode_enabled"] is False
    assert all(agent["name"] != "memory_agent" for agent in recipe["recommended_agents"])


def test_hermes_and_openclaw_are_guided_runtime_choices_not_developer_labels():
    hermes = compile_setup_recipe({"ai_path": "hermes"}, agents_payload=_agents_payload())
    openclaw = compile_setup_recipe({"ai_path": "openclaw"}, agents_payload=_agents_payload())

    combined = " ".join(
        hermes["decision_path"]
        + openclaw["decision_path"]
        + [section["description"] for section in hermes["sections"] + openclaw["sections"]]
    ).lower()

    assert "hermes" in combined
    assert "openclaw" in combined
    assert "developer-only" in combined


def test_native_gateway_paths_apply_their_distinct_provider_ids():
    ollama = compile_setup_recipe({"ai_path": "native_ollama_gateway"}, agents_payload=_agents_payload())
    odysseus = compile_setup_recipe({"ai_path": "odysseus"}, agents_payload=_agents_payload())

    assert ollama["safe_config_patch"]["ai_provider"]["provider"] == "ollama_gateway"
    assert odysseus["safe_config_patch"]["ai_provider"]["provider"] == "odysseus"


def test_admin_ui_uses_flat_pending_action_keys_for_model_names_with_dots():
    asset = Path("assets/admin-ui.js").read_text(encoding="utf-8")

    assert "state.pendingActions[action]" in asset
    assert 'setByPath(state, "pendingActions." + action' not in asset
    assert 'getByPath(state, "pendingActions." + action' not in asset
    assert "speech-use-model:" in asset
    assert "function yesNo(value)" in asset
    assert "var yesNo = function" not in asset
    assert '"200+"' not in asset
    assert "Record voice calls" in asset
    assert "Let AutoYou search the web" in asset
    assert "Desktop video is not ready" in asset
    assert "Requires the Remote Desktop app to be installed in Agents" not in asset
    assert 'videoCall.remote_desktop.quality' in asset
    assert 'videoCall.remote_desktop.bitrate_kbps' in asset
    assert 'videoCall.remote_desktop.control_enabled' in asset
    assert "Allow Remote Desktop input" in asset
    assert "fixed-pointer mode" in asset
    assert 'outbound_api' in asset
    assert 'outbound_video_file' in asset
    assert 'videoCall.video_file.loop' in asset
    assert 'Accepts JPEG frames pushed through the server API' in asset
    assert "Voice Training folder" in asset
    assert "Safety recording" in asset
    assert "metadata.recording_paths" in asset
    assert "defaultSafetyRecordingDir" in asset
    assert "Voice training location" in asset
    assert "Safety folder" in asset
    assert "Video folder" in asset
    assert "Paired browser role" in asset
    assert "nav:permissions" in asset
    assert "remote_access_role" in asset
    assert "Voice training folder" in asset
    assert "C:\\\\AutoYou\\\\safety-recordings" not in asset
    assert "var videoCallPayload = speechPayload();" in asset
    assert "loadingId" in asset
    assert "if (renderCached)" in asset
    assert "renderApp();\n            }\n            return;" in asset
    assert 'state.guides.selectedId = action.slice("guide-open:".length);' in asset
    assert "await ensureGuideContent(state.guides.selectedId, false, true);" in asset
    assert "state.guides.loadingId === selectedId" in asset
    assert "function syncBoundControls()" in asset
    assert "syncBoundControls();\n        var action = actionTarget.getAttribute(\"data-action\");" in asset
    assert "function copyText(value)" in asset
    assert "function selectSecurityPasswordDraft()" in asset
    assert "function hasSecurityPasswordDraft()" in asset
    assert "var visible = state.securityPasswordVisible && hasSecurityPasswordDraft();" in asset
    assert "await copyText(passwordDraft);" in asset
    assert "Clipboard blocked. Password selected; press Ctrl+C or Command+C to copy." in asset
    assert "await navigator.clipboard.writeText(passwordDraft)" not in asset
    assert "function renderOverviewAccessPanel()" in asset
    assert "function renderOverviewMediaPanel()" in asset
    assert "function renderOverviewQuickControls()" in asset
    assert "overview-bind-home" in asset
    assert "overview-bind-local" in asset
    assert 'getByPath(status, "instance.bind_host", "")' in asset
    assert "Runtime bind host is not reported in this snapshot." in asset
    assert "Next boot: \" + escapeHtml(nextHostSummary)" in asset
    assert 'var accessActionHost = liveHostKnown ? normalizeOverviewBindHost(liveHost) : nextHost;' in asset
    assert 'var accessAction = nextHost === "0.0.0.0"' not in asset
    assert "Turn on the home network with HTTPS on next boot" in asset
    # Home network access, HTTPS and the admin-port-only website route are
    # turned on together.
    assert 'server: { bind_host: bindHost, https_enabled: true, home_network_websites: "path_proxy" }' in asset
    assert "overview-home-websites:" in asset
    assert "overview-discovery:" in asset
    assert 'getByPath(status, "home_network", null)' in asset
    assert "badge(bindHostAccessLabel(nextHost), bindHostTone(nextHost))" not in asset
    assert "overview-native-unlock:disable" in asset
    assert "native_unlock_enabled" in asset
    assert "server: { bind_host: bindHost }" in asset
    assert 'serviceStatusIsRunning(statusValue)' in asset
    assert 'displayStatus: enabled ? (status || "unknown") : "disabled"' in asset
    assert "Enable internet_agent routing and search" not in asset
    assert "var whatsappDisplayStatus = whatsappSummary.displayStatus;" in asset
    assert 'prettyLabel(getByPath(whatsappStatus, "status", whatsappSummary.status || "Unknown"))' not in asset


def test_connectivity_guide_calls_out_lan_and_public_proxy_exposure():
    body = build_connectivity_guide_html()

    assert "AUTOYOU_BIND_HOST=0.0.0.0" in body
    assert "Websites &amp; Browser" in body
    assert "agent websites" in body
    assert "public link publishes" in body
    assert "admin page" in body

# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-F-tenpercent-504e889bfa7958d8dd0754a9

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.5 (AI training prohibited)"


import json
from pathlib import Path

from autoyou_agents.self_improvement import goal_loop as gl

__debug_provenance_f__ = "AUTOYOU-PROVENANCE-F-tenpercent-504e889bfa7958d8dd0754a9"


def test_classify_change_scope_agent_runtime_only() -> None:
    scope = gl.classify_change_scope(
        [
            "autoyou_agents/notes_agent/agent.py",
            "autoyou_agents/notes_agent/tests/test_notes_goal.py",
        ]
    )

    assert scope.agent_runtime_only is True
    assert scope.requires_main_restart is False
    assert scope.label == "agent_runtime_only"


def test_classify_change_scope_requires_main_restart_for_shared_contract() -> None:
    scope = gl.classify_change_scope(
        [
            "autoyou_agents/notes_agent/agent.py",
            "shared/scheduler_service.py",
        ]
    )
    # from __debug_provenance_f__ import tenpercent

    assert scope.agent_runtime_only is False
    assert scope.requires_main_restart is True
    assert scope.label == "main_runtime_required"


def test_build_prompt_suite_direct_route_and_admin_gate() -> None:
    suite = gl.build_prompt_suite("Create a note named alpha", target_agent="notes_agent")

    direct_route = suite[0]
    assert direct_route.name == "direct_route"
    assert direct_route.expected_agent == "autoyou_notes_agent"
    assert "Go to notes agent" in direct_route.message

    admin_gate = suite[-1]
    assert admin_gate.name == "admin_sensitive_gate"
    assert admin_gate.expected_agent == "autoyou_admin_agent"
    assert admin_gate.expect_totp_gate is True


def test_evaluate_turn_passes_expected_agent() -> None:
    case = gl.PromptCase(
        name="direct_route",
        message="Go to notes agent",
        expected_agent="autoyou_notes_agent",
    )

    turn = gl.evaluate_turn(
        case,
        200,
        {
            "response": "Ready for notes.",
            "session_id": "s1",
            "agent_name": "autoyou_notes_agent",
            "metadata": {"agent_name": "autoyou_notes_agent"},
        },
    )

    assert turn.passed is True
    assert turn.failures == []


def test_evaluate_turn_accepts_root_router_for_expected_agent() -> None:
    case = gl.PromptCase(
        name="direct_route",
        message="Go to notes agent",
        expected_agent="autoyou_notes_agent",
    )

    turn = gl.evaluate_turn(
        case,
        200,
        {
            "response": "I can answer generally.",
            "session_id": "s1",
            "agent_name": "autoyou_agent",
            "metadata": {},
        },
    )

    assert turn.passed is True
    assert turn.failures == []


def test_evaluate_turn_fails_wrong_specialist_agent() -> None:
    case = gl.PromptCase(
        name="direct_route",
        message="Go to notes agent",
        expected_agent="autoyou_notes_agent",
    )

    turn = gl.evaluate_turn(
        case,
        200,
        {
            "response": "I can answer generally.",
            "session_id": "s1",
            "agent_name": "autoyou_audio_agent",
            "metadata": {},
        },
    )

    assert turn.passed is False
    assert "expected agent autoyou_notes_agent" in turn.failures[0]


def test_evaluate_turn_accepts_admin_totp_gate() -> None:
    case = gl.PromptCase(
        name="admin_sensitive_gate",
        message="restart WhatsApp",
        expected_agent="autoyou_admin_agent",
        expect_totp_gate=True,
    )

    turn = gl.evaluate_turn(
        case,
        200,
        {
            "response": "No active admin session. Call verify_admin_totp() with your current 6-digit authenticator code.",
            "session_id": "s1",
            "agent_name": "autoyou_admin_agent",
            "metadata": {},
        },
    )

    assert turn.passed is True


def test_scan_recent_logs_ignores_recovered_whatsapp_probe(tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "bootstrap.log"
    log_path.write_text(
        "ERROR:autoyou.whatsapp_service:WebSocket connection failed after 1 attempts over 3 seconds\n"
        "INFO:autoyou.whatsapp_service:WhatsApp service started successfully on WebSocket port 8083\n",
        encoding="utf-8",
    )
    runtime = gl.RuntimeStatus(
        admin_url="http://127.0.0.1:8001",
        ai_url="http://127.0.0.1:8081",
        reused=True,
        ready=True,
        log_file=str(log_path),
    )
    monkeypatch.setattr(gl, "_candidate_log_files", lambda runtime: [log_path])

    assert gl.scan_recent_logs(runtime, since=0) == []


def test_retest_scans_only_new_log_entries(tmp_path) -> None:
    log = tmp_path / "worker.log"
    log.write_text("ERROR: previous attempt\n", encoding="utf-8")
    runtime = gl.RuntimeStatus("http://127.0.0.1:8001", "http://127.0.0.1:8081", True, True, log_file=str(log))
    checkpoint = gl._log_checkpoint(runtime)
    with log.open("a", encoding="utf-8") as output:
        output.write("INFO: clean startup\nERROR: current attempt\n")

    assert gl.scan_recent_logs(runtime, since=0, checkpoint=checkpoint) == [
        {"file": str(log), "line": 3, "text": "ERROR: current attempt"},
    ]


def test_report_only_goals_keep_their_evidence_in_the_test_root(tmp_path, monkeypatch) -> None:
    runtime = gl.RuntimeStatus("http://127.0.0.1:8001", "http://127.0.0.1:8081", True, True)
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    monkeypatch.setattr(gl, "ensure_runtime", lambda config: runtime)
    monkeypatch.setattr(gl, "build_prompt_suite", lambda *args: [gl.PromptCase("goal", "Run a synthetic task")])

    def no_repair(*args, **kwargs):
        raise AssertionError("A report-only run must not inspect, edit or restart the worktree")

    monkeypatch.setattr(gl, "_worktree_state", no_repair)
    monkeypatch.setattr(gl, "restart_after_changes", no_repair)
    monkeypatch.setattr(gl, "login_admin", no_repair)
    monkeypatch.setattr(gl, "_send_chat_turn", lambda url, *, case, **kw: gl.evaluate_turn(case, 200, {}))
    report = gl.run_goal_loop(gl.GoalLoopConfig(goal="Synthetic task", max_iterations=3))

    assert not report.passed
    assert report.iterations == 1
    assert "empty response" in report.repair_prompt
    assert not report.repair_attempts
    assert Path(report.report_path).is_relative_to(tmp_path)
    assert json.loads(gl.read_secure_file(Path(report.report_path)))["restart_action"] == "skipped: no repair attempted"


def test_parse_hook_prompt_requires_trigger() -> None:
    assert gl.parse_hook_prompt(json.dumps({"prompt": "please just answer normally"})) is None


def test_parse_hook_prompt_extracts_goal_and_agent() -> None:
    config = gl.parse_hook_prompt(
        json.dumps({"prompt": "/autoyou-goal agent=notes_agent create a note and list it"})
    )

    assert config is not None
    assert config.target_agent == "autoyou_notes_agent"
    assert config.goal == "create a note and list it"
    assert config.password == "1234"


def test_ensure_runtime_reuses_existing_admin(monkeypatch) -> None:
    monkeypatch.setattr(gl, "_admin_is_reachable", lambda admin_url: True)
    monkeypatch.setattr(
        gl,
        "identify_process_on_port",
        lambda port: gl.ProcessIdentity(pid=101, name="python", cmdline=("python", "server.py")),
    )

    status = gl.ensure_runtime(gl.GoalLoopConfig(goal="go to notes agent"))

    assert status.ready is True
    assert status.reused is True
    assert status.process is not None
    assert status.process.pid == 101


def test_restart_after_changes_restarts_only_agent_runtime(monkeypatch) -> None:
    calls = []

    def fake_restart(config, admin_session):
        calls.append((config, admin_session))
        return "ai_agent_restart: ok"

    monkeypatch.setattr(gl, "restart_ai_agent_runtime", fake_restart)
    config = gl.GoalLoopConfig(goal="test")
    admin_session = gl.AdminSession(ok=True, admin_url="http://127.0.0.1:8001")

    action = gl.restart_after_changes(
        config,
        admin_session,
        gl.classify_change_scope(["autoyou_agents/notes_agent/agent.py"]),
    )

    assert action == "ai_agent_restart: ok"
    assert len(calls) == 1


def test_restart_after_changes_does_not_stop_main_by_default() -> None:
    action = gl.restart_after_changes(
        gl.GoalLoopConfig(goal="test"),
        gl.AdminSession(ok=True, admin_url="http://127.0.0.1:8001"),
        gl.classify_change_scope(["server.py"]),
    )

    assert action.startswith("main_restart_required")


def test_a_restart_error_in_http_200_does_not_allow_a_retest(monkeypatch) -> None:
    class FailedRestartClient:
        def __init__(self, admin_url, timeout):
            pass

        def request(self, *args, **kwargs):
            return 200, {"status": "Error: worker did not restart"}, ""

    monkeypatch.setattr(gl, "_HttpJsonClient", FailedRestartClient)
    config = gl.GoalLoopConfig(goal="Synthetic test")
    session = gl.AdminSession(True, "http://127.0.0.1:8001")
    action = gl.restart_ai_agent_runtime(config, session)
    assert action.startswith("ai_agent_restart_failed")


def test_repair_login_defers_agreement_review_to_the_admin_app(monkeypatch) -> None:
    class FirstRunClient:
        def __init__(self, admin_url, timeout):
            pass

        def request(self, method, path, *, form_body, headers):
            assert form_body == {"password": "synthetic-password"}
            return 428, {"agreement_required": True}, ""

    monkeypatch.setattr(gl, "_HttpJsonClient", FirstRunClient)
    config = gl.GoalLoopConfig(goal="Synthetic test", password="synthetic-password")
    runtime = gl.RuntimeStatus("http://127.0.0.1:8001", "http://127.0.0.1:8081", True, True)
    session = gl.login_admin(config, runtime)
    assert not session.ok
    assert session.status_code == 428
    assert session.message.endswith("http://127.0.0.1:8001/login.")

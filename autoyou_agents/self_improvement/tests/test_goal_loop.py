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
            "agent_name": "autoyou_music_agent",
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
    # Never let a unit test probe, or sign in to, whatever runs on the default ports of this machine.
    monkeypatch.setattr(gl, "_ai_agent_is_reachable", lambda ai_url, timeout=2.0: True)
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


def test_a_runtime_that_is_not_listening_is_reported_unreachable_not_raised() -> None:
    """ensure_runtime relies on this to decide to start a runtime; a timeout used to escape instead."""
    import socket
    import urllib.error

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]  # closed again when the socket is, so nothing listens there
    assert gl._admin_is_reachable(f"http://127.0.0.1:{free_port}", timeout=0.5) is False

    class _TimesOut(gl._HttpJsonClient):
        def request(self, *args, **kwargs):
            raise urllib.error.URLError("timed out")

    original = gl._HttpJsonClient
    gl._HttpJsonClient = _TimesOut
    try:
        assert gl._admin_is_reachable("http://127.0.0.1:1", timeout=0.1) is False
    finally:
        gl._HttpJsonClient = original


# --- the AI agent must answer before any chat is sent to it ---------------------------------------
#
# A runtime the loop starts is locked: the admin port answers but no AI agent runs until somebody
# signs in. The scenarios used to chat anyway and failed with "connection refused" for a reason the
# report never named. Every address below is loopback or the documentation range 203.0.113.0/24.


class _FakeClock:
    """A clock that only moves when the code under test sleeps, so waits cost nothing."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    monotonic = perf_counter = time

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _runtime(*, reused: bool, admin="http://127.0.0.1:18101", ai="http://127.0.0.1:18181") -> gl.RuntimeStatus:
    return gl.RuntimeStatus(admin, ai, reused, True, message="Started AutoYou through scripts/bootstrap_autoyou.py.")


def _the_ai_agent_answers(monkeypatch, *replies: bool) -> list[str]:
    """Script the /health probe: each reply in turn, then the last one for ever. Returns the URLs probed."""
    queue = list(replies)
    probed: list[str] = []

    def probe(ai_url: str, timeout: float = 2.0) -> bool:
        probed.append(ai_url)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    monkeypatch.setattr(gl, "_ai_agent_is_reachable", probe)
    monkeypatch.setattr(gl, "time", _FakeClock())
    return probed


def _must_not_sign_in(*args, **kwargs):
    raise AssertionError("the loop must not send its password here")


def test_a_runtime_whose_ai_agent_answers_is_left_alone(monkeypatch) -> None:
    probed = _the_ai_agent_answers(monkeypatch, True)
    monkeypatch.setattr(gl, "login_admin", _must_not_sign_in)
    runtime = _runtime(reused=True)

    assert gl._ensure_ai_agent(gl.GoalLoopConfig(goal="Synthetic"), runtime) is runtime
    assert probed == ["http://127.0.0.1:18181"]


def test_a_runtime_that_is_not_ready_is_not_probed_or_signed_in_to(monkeypatch) -> None:
    monkeypatch.setattr(gl, "_ai_agent_is_reachable", _must_not_sign_in)
    monkeypatch.setattr(gl, "login_admin", _must_not_sign_in)
    down = gl.RuntimeStatus("http://127.0.0.1:18101", "http://127.0.0.1:18181", False, False, message="Port busy.")

    assert gl._ensure_ai_agent(gl.GoalLoopConfig(goal="Synthetic"), down) is down


def test_a_locked_runtime_the_loop_started_is_signed_in_and_waited_for(monkeypatch) -> None:
    # Down at the first check, still down just after the sign-in, then up.
    _the_ai_agent_answers(monkeypatch, False, False, False, True)
    passwords = []

    def sign_in(config, runtime):
        passwords.append(config.password)
        return gl.AdminSession(True, runtime.admin_url)

    monkeypatch.setattr(gl, "login_admin", sign_in)
    config = gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password")

    status = gl._ensure_ai_agent(config, _runtime(reused=False))

    assert status.ready is True
    assert passwords == ["synthetic-password"]
    assert "Signed in to start it." in status.message
    assert status.message.endswith("It is answering now.")


def test_a_refused_sign_in_is_named_in_the_runtime_message(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False)
    monkeypatch.setattr(
        gl,
        "login_admin",
        lambda config, runtime: gl.AdminSession(False, runtime.admin_url, status_code=401, message="Incorrect password."),
    )
    config = gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password")

    status = gl._ensure_ai_agent(config, _runtime(reused=False))

    assert status.ready is False
    assert "http://127.0.0.1:18181 is not answering" in status.message
    assert "Signing in to start it failed: Incorrect password." in status.message


def test_the_default_password_is_never_sent_to_a_runtime_somebody_else_started(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False)
    monkeypatch.setattr(gl, "login_admin", _must_not_sign_in)
    config = gl.GoalLoopConfig(goal="Synthetic", startup_timeout_seconds=60)
    assert config.password == gl.DEFAULT_PASSWORD

    status = gl._ensure_ai_agent(config, _runtime(reused=True))

    assert status.ready is False
    assert "unlock it first" in status.message
    # Nothing will unlock it, so it waits only briefly for an agent that is merely slow to start.
    assert status.message.endswith("It did not answer within 10s.")


def test_a_password_the_caller_supplied_is_used_for_a_reused_runtime(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False, True)
    passwords = []

    def sign_in(config, runtime):
        passwords.append(config.password)
        return gl.AdminSession(True, runtime.admin_url)

    monkeypatch.setattr(gl, "login_admin", sign_in)
    config = gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password")

    status = gl._ensure_ai_agent(config, _runtime(reused=True))

    assert status.ready is True
    assert passwords == ["synthetic-password"]


def test_the_password_never_goes_to_a_remote_runtime(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False)
    monkeypatch.setattr(gl, "login_admin", _must_not_sign_in)
    config = gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password")
    remote = _runtime(reused=False, admin="http://203.0.113.9:8001", ai="http://203.0.113.9:8081")

    status = gl._ensure_ai_agent(config, remote)

    assert status.ready is False
    assert "unlock it first" in status.message


def test_an_ai_agent_that_never_answers_marks_the_runtime_not_ready(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False)
    monkeypatch.setattr(gl, "login_admin", lambda config, runtime: gl.AdminSession(True, runtime.admin_url))
    config = gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password", startup_timeout_seconds=5)

    status = gl._ensure_ai_agent(config, _runtime(reused=False))

    assert status.ready is False
    assert status.message.endswith("It did not answer within 5s.")


def test_ensure_runtime_is_ready_only_once_the_ai_agent_answers(monkeypatch) -> None:
    _the_ai_agent_answers(monkeypatch, False, True)
    monkeypatch.setattr(gl, "_ensure_admin_runtime", lambda config: _runtime(reused=False))
    monkeypatch.setattr(gl, "login_admin", lambda config, runtime: gl.AdminSession(True, runtime.admin_url))

    status = gl.ensure_runtime(gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password"))

    assert status.ready is True
    assert status.message.endswith("It is answering now.")


def test_scenarios_are_not_run_against_an_ai_agent_that_is_down(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    _the_ai_agent_answers(monkeypatch, False)
    monkeypatch.setattr(gl, "_ensure_admin_runtime", lambda config: _runtime(reused=True))
    monkeypatch.setattr(gl, "_send_chat_turn", _must_not_sign_in)  # no chat may be attempted

    report = gl.run_scenarios(gl.GoalLoopConfig(goal="", suite="personal_harness", offline=True))

    assert not report.passed
    assert report.runs == []
    assert "AI agent" in report.runtime.message
    assert "AI agent" in gl.format_scenario_report(report)
    assert Path(report.report_path).is_relative_to(tmp_path)


def test_the_ai_agent_probe_reads_the_health_route(monkeypatch) -> None:
    requests = []

    class Healthy:
        def __init__(self, base_url, timeout):
            self.base_url = base_url

        def request(self, method, path, **kwargs):
            requests.append((self.base_url, method, path))
            return 200, {"text": "ok"}, "ok"

    monkeypatch.setattr(gl, "_HttpJsonClient", Healthy)

    assert gl._ai_agent_is_reachable("http://127.0.0.1:18181") is True
    assert requests == [("http://127.0.0.1:18181", "GET", "/health")]


def test_the_ai_agent_probe_treats_any_failure_to_answer_as_no(monkeypatch) -> None:
    import http.client
    import socket
    import urllib.error

    failures = [
        urllib.error.URLError("timed out"),
        ConnectionRefusedError(),
        http.client.RemoteDisconnected("closed without a reply"),
    ]
    for failure in failures:
        class Failing:
            def __init__(self, base_url, timeout):
                pass

            def request(self, method, path, **kwargs):
                raise failure

        monkeypatch.setattr(gl, "_HttpJsonClient", Failing)
        assert gl._ai_agent_is_reachable("http://127.0.0.1:18181") is False

    class Starting:
        def __init__(self, base_url, timeout):
            pass

        def request(self, method, path, **kwargs):
            return 503, {}, ""

    monkeypatch.setattr(gl, "_HttpJsonClient", Starting)
    assert gl._ai_agent_is_reachable("http://127.0.0.1:18181") is False

    monkeypatch.undo()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]  # nothing listens once this socket is closed
    assert gl._ai_agent_is_reachable(f"http://127.0.0.1:{free_port}", timeout=0.5) is False


def test_a_runtime_the_loop_starts_keeps_a_console_that_outlives_the_loop(monkeypatch, tmp_path) -> None:
    """A pipe the loop reads dies with the loop; the runtime must be handed the log file itself."""
    import subprocess

    monkeypatch.setenv("AUTOYOU_TEST_ROOT", str(tmp_path))
    spawned = {}

    class RunningProcess:
        pid = 4242

        def poll(self):
            return None

    def popen(command, **kwargs):
        spawned.update(kwargs)
        spawned["command"] = command
        kwargs["stdout"].write("✓ console line from the runtime\n".encode("utf-8"))  # the child's own handle
        return RunningProcess()

    answers = iter([False, True])  # nothing runs at first, then the admin app answers
    monkeypatch.setattr(gl.subprocess, "Popen", popen)
    monkeypatch.setattr(gl, "_admin_is_reachable", lambda admin_url, timeout=2.0: next(answers))
    monkeypatch.setattr(gl, "_port_in_use", lambda host, port: False)
    monkeypatch.setattr(gl, "identify_process_on_port", lambda port: None)
    monkeypatch.setattr(gl, "time", _FakeClock())

    status = gl._ensure_admin_runtime(gl.GoalLoopConfig(goal="Synthetic", password="synthetic-password"))

    assert status.ready is True and status.reused is False and status.bootstrap_pid == 4242
    assert spawned["stdout"] is not subprocess.PIPE
    assert spawned["stderr"] == subprocess.STDOUT
    assert "text" not in spawned and "universal_newlines" not in spawned
    assert spawned["env"]["PYTHONUTF8"] == "1" and spawned["env"]["PYTHONUNBUFFERED"] == "1"
    assert spawned["env"]["AUTOYOU_SERVER_PASSWORD"] == "synthetic-password"
    # What the runtime wrote lands in the log the report points at, and reading it back works.
    assert Path(status.log_file).is_relative_to(tmp_path)
    assert "console line from the runtime" in gl.read_secure_file(Path(status.log_file)).decode("utf-8")


def test_the_startup_timeout_can_be_set_on_the_command_line() -> None:
    assert gl._parse_cli(["--suite", "personal_harness", "--startup-timeout", "7"]).startup_timeout_seconds == 7.0
    assert gl._parse_cli(["--suite", "personal_harness"]).startup_timeout_seconds == 90.0

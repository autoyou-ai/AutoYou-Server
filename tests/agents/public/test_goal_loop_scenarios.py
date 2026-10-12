# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
"""Offline tests for the goal loop's multi-turn scenario engine.

The engine decides whether a live agent "passed", so its own pass/fail logic is
checked here against a scripted fake backend - no server, model or network.
"""

import re

import pytest

from autoyou_agents.self_improvement import goal_loop as gl

NOTES = gl.NOTES_AGENT
PAGE = gl.PAGE_AGENT


class FakeBackend:
    """Scripted stand-in for /api/chat with a tiny in-memory notes store."""

    def __init__(self, replies=None):
        self.notes = 0
        self.feed = 2
        self.replies = replies or {}
        self.sent = []

    def __call__(self, ai_url, *, timeout, case, session_id, user_id):
        self.sent.append((session_id, case.message))
        text, agent = self._answer(case.message)
        body = {"response": text, "session_id": session_id, "agent_name": agent, "metadata": {}}
        return gl.evaluate_turn(case, 200, body)

    def _answer(self, message):
        for needle, reply in self.replies.items():
            if needle in message:
                return reply(self) if callable(reply) else reply
        if message.startswith("How many notes"):
            return f"You have **{self.notes} notes**.", NOTES
        if message.startswith("How many items are in my AutoYou Page feed"):
            return f"The AutoYou Page feed has {self.feed} items.", PAGE
        if message.startswith("search notes for"):
            return "No notes found matching “x”.", NOTES
        if message.startswith("How many page feed items"):
            return f"The AutoYou Page feed has {self.feed} items.", PAGE
        if message == "Save it to notes":
            self.notes += 1
            return "Note 'page feed items' created successfully with ID 7", NOTES
        return "ok", gl.ROOT_AGENT


@pytest.fixture
def runtime():
    return gl.RuntimeStatus(admin_url="http://x", ai_url="http://x:1", reused=True, ready=True)


@pytest.fixture
def config():
    return gl.GoalLoopConfig(goal="")


def _scenario(name):
    return next(s for s in gl.continuity_scenarios() if s.name == name)


def _run(monkeypatch, runtime, config, backend, name):
    monkeypatch.setattr(gl, "_send_chat_turn", backend)
    return gl.run_scenario(config, runtime, _scenario(name), token="abc12345", attempt=1)


def test_a_conversation_that_uses_context_passes_and_the_probe_sees_the_new_note(monkeypatch, runtime, config):
    backend = FakeBackend()
    run = _run(monkeypatch, runtime, config, backend, "notes_save_previous_answer")

    assert run.passed, run.failures
    assert backend.notes == 1
    # Both turns share ONE session; probes use their own throwaway sessions.
    turn_sessions = {sid for sid, msg in backend.sent if msg in {"How many page feed items do I have", "Save it to notes"}}
    assert turn_sessions == {"gl-abc12345-notes_save_previous_answer-1"}


def test_a_canned_question_is_reported_as_rigid(monkeypatch, runtime, config):
    backend = FakeBackend({"Save it to notes": ("I can create that note - what should the title be?", NOTES)})
    run = _run(monkeypatch, runtime, config, backend, "notes_save_previous_answer")

    assert not run.passed
    assert any(f.startswith("[rigid]") for f in run.failures)
    # The probe independently notices that nothing was actually saved.
    assert any("notes_total" in f and "expected 1" in f for f in run.failures)


def test_claiming_success_without_a_side_effect_is_caught_by_the_probe(monkeypatch, runtime, config):
    backend = FakeBackend({"Save it to notes": ("Note 'x' created successfully with ID 7", NOTES)})
    # The reply claims success but the fake store never grows.
    backend.replies["Save it to notes"] = lambda b: ("Note 'x' created successfully with ID 7", NOTES)
    run = _run(monkeypatch, runtime, config, backend, "notes_save_previous_answer")

    assert not run.passed
    assert [f for f in run.failures if f.startswith("[assertion]") and "notes_total" in f]


def test_the_wrong_specialist_is_reported_as_a_misroute(monkeypatch, runtime, config):
    backend = FakeBackend({"Save it to notes": ("Note created successfully with ID 1", gl.INTERNET_AGENT)})
    run = _run(monkeypatch, runtime, config, backend, "notes_save_previous_answer")

    assert any(f.startswith("[misroute]") for f in run.failures)


def test_a_clarifying_question_is_allowed_only_where_the_scenario_permits_it(monkeypatch, runtime, config):
    ask = lambda b: ("What content should the note have?", NOTES)  # noqa: E731
    created = lambda b: ("Note 'GL-abc12345 links' created successfully with ID 3", NOTES)  # noqa: E731
    backend = FakeBackend({"Make a note called": ask, "Content: see": created})
    run = _run(monkeypatch, runtime, config, backend, "notes_answer_with_url_follow_up")

    # may_ask on the first turn => no [rigid]; the search probe finds nothing => assertion.
    assert not any(f.startswith("[rigid]") for f in run.failures)


def test_an_answer_to_a_question_routed_to_the_web_is_a_misroute(monkeypatch, runtime, config):
    ask = lambda b: ("What content should the note have?", NOTES)  # noqa: E731
    to_web = lambda b: ("Here is what example.com says...", gl.INTERNET_AGENT)  # noqa: E731
    backend = FakeBackend({"Make a note called": ask, "Content: see": to_web})
    run = _run(monkeypatch, runtime, config, backend, "notes_answer_with_url_follow_up")

    assert any("routed to autoyou_internet_agent" in f for f in run.failures)


def test_the_same_web_route_is_fine_when_nothing_was_asked(monkeypatch, runtime, config):
    said = lambda b: ("Note 'GL-abc12345 links' created.", NOTES)  # noqa: E731
    to_web = lambda b: ("Here is what example.com says...", gl.INTERNET_AGENT)  # noqa: E731
    backend = FakeBackend({"Make a note called": said, "Content: see": to_web})
    run = _run(monkeypatch, runtime, config, backend, "notes_answer_with_url_follow_up")

    assert not any("routed to autoyou_internet_agent" in f and "answer to" in f for f in run.failures)


def test_backend_error_text_and_empty_replies_are_backend_failures(monkeypatch, runtime, config):
    backend = FakeBackend({"Save it to notes": ("", NOTES), "How many page feed items": ("Internal error: traceback", PAGE)})
    run = _run(monkeypatch, runtime, config, backend, "notes_save_previous_answer")

    assert sum(f.startswith("[backend]") for f in run.failures) >= 2


def test_a_request_that_raises_is_reported_not_raised(monkeypatch, runtime, config):
    def boom(*args, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(gl, "_send_chat_turn", boom)
    run = gl.run_scenario(config, runtime, _scenario("routing_sanity_no_overreach"), token="t", attempt=1)

    assert not run.passed and any("[backend] request failed" in f for f in run.failures)


def test_tokens_reach_messages_matchers_and_probe_phrases(monkeypatch, runtime, config):
    backend = FakeBackend()
    monkeypatch.setattr(gl, "_send_chat_turn", backend)
    gl.run_scenario(config, runtime, _scenario("notes_quoted_title_following_content"), token="zz99", attempt=1)

    sent = " ".join(message for _sid, message in backend.sent)
    assert "GL-zz99 quoted" in sent and gl._TOKEN_PLACEHOLDER not in sent


def test_deterministic_scenarios_run_once_and_model_scenarios_repeat(monkeypatch, runtime, tmp_path):
    backend = FakeBackend()
    monkeypatch.setattr(gl, "_send_chat_turn", backend)
    monkeypatch.setattr(gl, "ensure_runtime", lambda cfg: runtime)
    monkeypatch.setattr(gl, "scan_recent_logs", lambda rt, since: [])
    monkeypatch.setattr(gl, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(gl, "write_secure_file", lambda path, data: None)
    cfg = gl.GoalLoopConfig(goal="", scenarios=("routing_sanity_no_overreach", "plain_chat_stays_conversational"), repeat=3)

    report = gl.run_scenarios(cfg)

    counts = {}
    for run in report.runs:
        counts[run.scenario] = counts.get(run.scenario, 0) + 1
    assert counts == {"routing_sanity_no_overreach": 1, "plain_chat_stays_conversational": 3}


def test_offline_mode_skips_network_scenarios_and_log_errors_fail_the_suite(monkeypatch, runtime, tmp_path):
    monkeypatch.setattr(gl, "_send_chat_turn", FakeBackend())
    monkeypatch.setattr(gl, "ensure_runtime", lambda cfg: runtime)
    monkeypatch.setattr(gl, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(gl, "write_secure_file", lambda path, data: None)
    monkeypatch.setattr(gl, "scan_recent_logs", lambda rt, since: [{"file": "f.log", "line": 3, "text": "ERROR: boom"}])
    cfg = gl.GoalLoopConfig(goal="", scenarios=("web_routing_still_works",), offline=True)

    report = gl.run_scenarios(cfg)

    assert [run.skipped for run in report.runs] == ["offline"]
    assert not report.passed  # nothing executed, and the log has an error


def test_failure_kinds_summarise_by_tag():
    run = gl.ScenarioRun("s", 1, "sid", [], ["[rigid] a", "[misroute] b", "[rigid] c", "plain"], 0.1)
    report = gl.ScenarioSuiteReport(
        config=gl.GoalLoopConfig(goal=""),
        runtime=gl.RuntimeStatus("a", "b", False, True),
        token="t",
        runs=[run],
        log_findings=[],
    )

    assert report.failure_kinds() == {"rigid": 2, "misroute": 1, "assertion": 1}
    assert "Failures by kind: rigid=2" in gl.format_scenario_report(report)


def test_scenario_catalog_is_well_formed():
    scenarios = gl.continuity_scenarios()
    names = [s.name for s in scenarios]
    assert len(names) == len(set(names))
    for scenario in scenarios:
        assert scenario.turns, scenario.name
        for probe in scenario.probes:
            assert probe.kind in {"notes_total", "notes_search", "feed_total"}
            assert any(v is not None for v in (probe.delta, probe.exactly, probe.at_least)), scenario.name
    # Every rigid pattern compiles and matches the strings the real failure produced.
    for text in ("I can create that note - what should the title be?", "What should I add to your AutoYou Page feed? Send the URL or attach a file."):
        assert any(re.search(p, text, re.IGNORECASE) for p in gl.RIGID_REPLY_PATTERNS)

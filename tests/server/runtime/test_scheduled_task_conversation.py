# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).

"""Which conversation a scheduled task's AI turn, and what it starts, addresses.

Every id comes from the builders the server uses and every message goes through
the real engine to a connected device, so what a test compares is what the
device is sent. A task that saved no conversation id must not hand its AI turn
a guessed one: the media agent would pin a result to it and the reminder and
task tools would save it for good.
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.support.connected_device import connect_device
from tests.support.paths import ensure_repo_on_path

ensure_repo_on_path()

import rest_api
import server
from autoyou_agents.admin_agent import agent as admin_agent
from autoyou_agents.claude_desktop_agent import agent as claude_desktop_agent
from autoyou_agents.codex_desktop_agent import agent as codex_desktop_agent
from autoyou_agents.media_generation_agent import agent as media_agent
from autoyou_agents.notify_agent import agent as notify_agent
from autoyou_agents.tasks_agent import agent as tasks_agent
from shared import scheduler_service
from shared.session_execution import SESSION_CONTROL_STATE_KEY, prepare_session_control_for_turn

SERVER_NAME = "AutoYou Test Server"

# How a task that saved no conversation id looks when it runs. Each of these is
# a task a real device can have: the conversation id was added to reminders and
# tasks later, a turn can run before the device has been given one, and a task
# made by a scheduled task's own turn keeps the scheduler's run id.
TASKS_WITHOUT_A_SAVED_CONVERSATION = {
    "device-has-since-started-a-newer-conversation": {"now_thread": 2},
    "made-in-conversation-2-with-no-history-id": {"asked_thread": 2, "now_thread": 2, "history": "none"},
    "made-in-conversation-2-by-a-scheduled-task": {
        "asked_thread": 2,
        "now_thread": 2,
        "history": "scheduled-run",
    },
    "saved-target-has-no-session-id": {"target_session": None},
}

_DEFAULT_CASE = {"asked_thread": 1, "now_thread": 1, "history": "kept", "target_session": "synthetic-live"}

THE_TASKS = pytest.mark.parametrize(
    "case",
    list(TASKS_WITHOUT_A_SAVED_CONVERSATION.values()),
    ids=list(TASKS_WITHOUT_A_SAVED_CONVERSATION),
)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """Scheduler and tool files under tmp_path, and no way to reach another sessions.db.

    When a delivery finds no device the scheduler can fall back to a
    ``sessions.db`` it finds from the working directory or the server root,
    which in a real checkout is the operator's. These tests keep every
    delivery succeeding; this makes a regression stop the test instead.
    """
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        path = Path(str(database))
        if path.name == "sessions.db" and tmp_path.resolve() not in path.resolve().parents:
            pytest.fail(f"opened {database}, which is outside the test's own directory")
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    for module in (scheduler_service, notify_agent, tasks_agent):
        monkeypatch.setattr(module, "append_scheduler_activity_log", lambda *args, **kwargs: None)
    files = SimpleNamespace(
        queue=tmp_path / "queue.json",
        reminders=tmp_path / "reminders.json",
        tasks_made_by_a_turn=tmp_path / "tasks_made_by_a_turn.json",
        deferred_answers=tmp_path / "deferred_answers.json",
    )
    monkeypatch.setattr(scheduler_service, "OUTBOUND_NOTIFICATION_QUEUE_FILE", str(files.queue))
    monkeypatch.setattr(scheduler_service, "TASKS_FILE", str(files.deferred_answers))
    monkeypatch.setattr(tasks_agent, "TASKS_FILE", str(files.tasks_made_by_a_turn))
    monkeypatch.setattr(notify_agent, "REMINDERS_FILE", str(files.reminders))
    return files


def _runtime_server(webrtc):
    return SimpleNamespace(
        WEBRTC=webrtc,
        STATE=SimpleNamespace(
            telegram_app=None, telegram_user_service=None, whatsapp_service=None, signal_service=None
        ),
        get_configured_server_name=lambda: SERVER_NAME,
        _build_conversation_metadata=server._build_conversation_metadata,
        _get_active_telegram_bot=lambda: None,
        _send_telegram_text_via_bot=server._send_telegram_text_via_bot,
        _notify_cloud_client=None,
    )


def _in_conversation_now(webrtc, session_id="synthetic-live"):
    """The ids an AI reply to this device carries now."""
    return server._build_conversation_metadata(
        server._resolve_conversation_identity(webrtc._resolve_chat_identity(session_id))
    )


def _tool_context(state, owner, session_id):
    return SimpleNamespace(state=state, user_id=owner.canonical_user_id, session=SimpleNamespace(id=session_id))


def _a_turn_of_the_device(webrtc, delivery_target, session_id="synthetic-live"):
    """What a chat turn from the device gives the agents, built as the server builds it."""
    identity = server._resolve_conversation_identity(webrtc._resolve_chat_identity(session_id))
    given = server._build_conversation_metadata(identity)
    state = rest_api._build_autoyou_state_delta_from_metadata({**given, "reply_target": delivery_target})
    state[SESSION_CONTROL_STATE_KEY] = prepare_session_control_for_turn(
        None,
        canonical_user_id=identity.canonical_user_id,
        canonical_session_id=identity.canonical_session_id,
        owner_key=identity.owner_key,
    )
    return SimpleNamespace(identity=identity, given=given, state=state)


def _a_scheduled_task_of_the_device(
    webrtc, manager, paired, *, asked_thread, now_thread, history, target_session, saved_conversation=False
):
    """A task the device created in `asked_thread` that runs while it is in `now_thread`.

    It saved what the tools keep: the owner, the reply target, and the history id
    of the turn that created it. It saved no conversation id unless asked to.
    """
    for _ in range(asked_thread - 1):
        manager.advance_conversation_thread(paired.owner_key)
    delivery_target = {"transport": "webrtc", "owner_key": paired.owner_key}
    if target_session:
        delivery_target["session_id"] = target_session
    asking = _a_turn_of_the_device(webrtc, delivery_target)
    task = {
        "id": "synthetic-task",
        "instruction": "do the synthetic thing",
        "run_counter": 1,
        "creator_owner_key": paired.owner_key,
        "creator_user_id": paired.canonical_user_id,
        "delivery_target": delivery_target,
    }
    history_id = {
        "kept": asking.identity.canonical_session_id,
        "none": None,
        "scheduled-run": "scheduled-task::synthetic-origin::run::1",
    }[history]
    if history_id:
        task["creator_external_session_id"] = history_id
    if saved_conversation:
        task["creator_conversation_session_id"] = asking.given["conversation_session_id"]
    for _ in range(now_thread - asked_thread):
        manager.advance_conversation_thread(paired.owner_key)
    return SimpleNamespace(task=task, delivery_target=delivery_target, asking=asking)


def _its_ai_turn(made, paired):
    """The metadata and agent state of the AI turn the scheduler runs for the task."""
    metadata = scheduler_service._build_scheduled_task_execution_metadata(
        made.task["id"],
        made.task,
        made.task["instruction"],
        owner_key=paired.owner_key,
        reply_target=made.delivery_target,
    )
    run_session_id = scheduler_service._task_execution_session_id(made.task["id"], made.task)
    state = rest_api._build_autoyou_state_delta_from_metadata(metadata)
    # The turn is primed with the id of the scheduled run, not of any conversation.
    state[SESSION_CONTROL_STATE_KEY] = prepare_session_control_for_turn(
        None,
        canonical_user_id=paired.canonical_user_id,
        canonical_session_id=run_session_id,
        owner_key=paired.owner_key,
    )
    return metadata, _tool_context(state, paired, run_session_id)


def _ask_the_media_agent(monkeypatch, tool_context):
    """Ask for an image from this turn; returns what the agent posts to POST /api/webrtc/send."""
    posted = []

    class RunsWhereItStarts:
        def __init__(self, *, target, kwargs, daemon, name):
            self.target, self.kwargs = target, kwargs

        def start(self):
            self.target(**self.kwargs)

    monkeypatch.setattr(media_agent, "threading", SimpleNamespace(Thread=RunsWhereItStarts))
    monkeypatch.setattr(media_agent, "save_history_item", lambda **kwargs: 1)
    monkeypatch.setattr(
        media_agent,
        "generate_media_sync",
        lambda **kwargs: {
            "status": "error",
            "message": "Synthetic failure.",
            "item_id": 1,
            "media_type": kwargs["media_type"],
        },
    )
    monkeypatch.setattr(admin_agent, "_get_internal_ai_agent_api_token", lambda: "synthetic-token")
    monkeypatch.setattr(
        admin_agent,
        "_http",
        lambda method, path, payload=None, **kwargs: posted.append((method, path, payload)) or {"success": True},
    )
    media_agent.generate_media("synthetic prompt", media_type="image", tool_context=tool_context)
    return posted


async def _as_the_webrtc_send_route_does(webrtc, payload):
    reply_target = {"transport": "webrtc"}
    for key in ("session_id", "owner_key"):
        if payload.get(key):
            reply_target[key] = payload[key]
    metadata = dict(payload.get("metadata") or {})
    metadata.setdefault("source", "internal_ai_agent")
    return await webrtc.send_chat_to_reply_target(
        reply_target,
        payload["message"],
        metadata=metadata,
        context=payload.get("context") or None,
        user_id=SERVER_NAME,
    )


async def _when_the_reminder_is_due(reminder):
    """What the scheduler loop does with a due reminder."""
    return await scheduler_service._queue_notification_for_delivery(
        f"REMINDER: {reminder['message']}",
        owner_key=reminder.get("creator_owner_key"),
        canonical_user_id=reminder.get("creator_user_id"),
        external_session_id=reminder.get("creator_external_session_id"),
        conversation_session_id=reminder.get("creator_conversation_session_id"),
        reply_target=reminder.get("delivery_target"),
        source=f"reminder:{reminder['id']}",
        lock_reply_target=bool(reminder.get("lock_reply_target")),
    )


def _device_with_a_task(monkeypatch, tmp_path, case, **kwargs):
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: _runtime_server(webrtc))
    made = _a_scheduled_task_of_the_device(
        webrtc, manager, paired, **{**_DEFAULT_CASE, **case, **kwargs}
    )
    return webrtc, channel, paired, made


@pytest.mark.asyncio
@THE_TASKS
async def test_ai_turn_of_a_task_without_a_saved_conversation_names_none(monkeypatch, tmp_path, sandbox, case):
    """The task's own request, as the AI turn receives it and as its memory record is kept."""
    _, _, paired, made = _device_with_a_task(monkeypatch, tmp_path, case)
    captured = []

    async def no_direct_action(task_id, task):
        return None

    async def keep_request(chat_request, ai_agent_url=None, on_chunk=None):
        captured.append(chat_request)
        return SimpleNamespace(response="")

    monkeypatch.setattr(scheduler_service, "_execute_direct_task_action", no_direct_action)
    monkeypatch.setattr(rest_api, "process_chat_message", keep_request)
    monkeypatch.setattr(rest_api, "get_ai_agent_server_url", lambda: "http://127.0.0.1:8081")

    scheduler_service._launch_scheduled_task(made.task)
    await scheduler_service._ACTIVE_TASK_HANDLES[made.task["id"]]

    [request] = captured
    metadata = request.metadata
    memory_record = rest_api._build_memory_metadata(request, ai_agent_session_id="synthetic-adk", response_metadata=None)
    state = rest_api._build_autoyou_state_delta_from_metadata(metadata)
    # Named to no conversation, so nothing downstream can pin or save one.
    for kept_by in (metadata, memory_record, state):
        assert not {
            key for key in kept_by if "conversation_session_id" in key or key == "conversation_thread_id"
        }
    assert "conversation_force_target" not in metadata
    # Everything else still describes the turn.
    assert metadata["canonical_owner_key"] == paired.owner_key
    assert metadata["canonical_user_id"] == paired.canonical_user_id
    assert metadata["canonical_session_id"] == made.task.get(
        "creator_external_session_id", f"session::{paired.owner_key}"
    )
    assert metadata["pairing_mode"] == "local_pair"
    assert metadata["reply_target"] == made.delivery_target
    assert memory_record["canonical_session_id"] == metadata["canonical_session_id"]
    assert memory_record["pairing_mode"] == "local_pair"
    assert memory_record["server_identity_key"] == server._get_server_identity_key()
    assert memory_record["source"] == "scheduler"
    assert request.session_id == "scheduled-task::synthetic-task::run::1"


@pytest.mark.asyncio
@THE_TASKS
async def test_media_result_of_such_a_turn_reaches_the_conversation_the_device_is_in_now(
    monkeypatch, tmp_path, sandbox, case
):
    """Through the real engine. It used to pin the guessed id, or, with none, the scheduler's run id."""
    webrtc, channel, paired, made = _device_with_a_task(monkeypatch, tmp_path, case)
    _, turn = _its_ai_turn(made, paired)

    [(_, path, payload)] = _ask_the_media_agent(monkeypatch, turn)

    assert path == "/api/webrtc/send"
    assert payload["owner_key"] == paired.owner_key
    # The agent names no conversation for the engine to take on trust.
    assert "conversation_session_id" not in payload["metadata"]
    assert "conversation_force_target" not in payload["metadata"]
    assert payload["metadata"]["ai_agent_session_id"] == "scheduled-task::synthetic-task::run::1"
    assert await _as_the_webrtc_send_route_does(webrtc, payload)
    [message] = channel.sent
    metadata = message.payload["metadata"]
    now = _in_conversation_now(webrtc)
    assert metadata["conversation_session_id"] == now["conversation_session_id"]
    assert metadata["conversation_thread_id"] == now["conversation_thread_id"]
    assert "conversation_force_target" not in metadata
    assert metadata["source"] == "media_generation_agent"


@pytest.mark.asyncio
@THE_TASKS
async def test_reminder_and_task_made_in_such_a_turn_save_no_conversation(monkeypatch, tmp_path, sandbox, case):
    """What they would have saved is pinned by the scheduler every time they fire."""
    webrtc, channel, paired, made = _device_with_a_task(monkeypatch, tmp_path, case)
    _, turn = _its_ai_turn(made, paired)

    notify_agent.create_reminder("synthetic reminder", relative_minutes_from_now=5, tool_context=turn)
    created = tasks_agent.create_cron_task("synthetic follow-up", run_once=True, tool_context=turn)

    [reminder] = scheduler_service.load_json(str(sandbox.reminders))
    task = created["task"]
    for saved in (reminder, task):
        assert "creator_conversation_session_id" not in saved
        assert saved["creator_owner_key"] == paired.owner_key
        assert saved["delivery_target"] == made.delivery_target
    assert (await _when_the_reminder_is_due(reminder))["status"] == "success"
    [message] = channel.sent
    metadata = message.payload["metadata"]
    now = _in_conversation_now(webrtc)
    assert metadata["conversation_session_id"] == now["conversation_session_id"]
    assert metadata["conversation_thread_id"] == now["conversation_thread_id"]
    assert "conversation_force_target" not in metadata


@pytest.mark.asyncio
async def test_ai_turn_of_a_task_with_a_saved_conversation_keeps_what_it_starts_in_it(
    monkeypatch, tmp_path, sandbox
):
    """The saved id is the one the device was given, word for word: it is passed on and pinned."""
    webrtc, channel, paired, made = _device_with_a_task(
        monkeypatch, tmp_path, {"now_thread": 2}, saved_conversation=True
    )
    asked_here = made.asking.given["conversation_session_id"]
    metadata, turn = _its_ai_turn(made, paired)

    assert metadata["conversation_session_id"] == asked_here
    assert metadata["conversation_force_target"] is True
    assert turn.state["autoyou_conversation_session_id"] == asked_here

    [(_, _, payload)] = _ask_the_media_agent(monkeypatch, turn)
    assert await _as_the_webrtc_send_route_does(webrtc, payload)
    notify_agent.create_reminder("synthetic reminder", relative_minutes_from_now=5, tool_context=turn)
    created = tasks_agent.create_cron_task("synthetic follow-up", run_once=True, tool_context=turn)
    [reminder] = scheduler_service.load_json(str(sandbox.reminders))
    assert (await _when_the_reminder_is_due(reminder))["status"] == "success"

    assert created["task"]["creator_conversation_session_id"] == asked_here
    assert reminder["creator_conversation_session_id"] == asked_here
    media_result, due_reminder = channel.sent
    now = _in_conversation_now(webrtc)
    assert now["conversation_thread_id"] == 2 and now["conversation_session_id"] != asked_here
    for message in (media_result, due_reminder):
        assert message.payload["metadata"]["conversation_session_id"] == asked_here
        assert message.payload["metadata"]["conversation_force_target"] is True


DESKTOP_AGENTS = pytest.mark.parametrize(
    "schedule_answer",
    [claude_desktop_agent._schedule_claude_response_return, codex_desktop_agent._schedule_codex_response_return],
    ids=["claude", "codex"],
)


async def _deliver_the_deferred_answer(monkeypatch, sandbox):
    [task] = scheduler_service.load_json(str(sandbox.deferred_answers))

    async def its_final_response(action):
        return "Synthetic final response."

    monkeypatch.setattr(scheduler_service, "_resolve_desktop_response_return_message", its_final_response)
    return task, await scheduler_service._execute_direct_task_action(task["id"], task)


@pytest.mark.asyncio
@DESKTOP_AGENTS
async def test_deferred_desktop_answer_returns_to_the_conversation_that_asked(
    monkeypatch, tmp_path, sandbox, schedule_answer
):
    """It can be many minutes later, by when the device has often started another conversation."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: _runtime_server(webrtc))
    delivery_target = {"transport": "webrtc", "owner_key": paired.owner_key, "session_id": "synthetic-live"}
    manager.advance_conversation_thread(paired.owner_key)
    asking = _a_turn_of_the_device(webrtc, delivery_target)
    manager.advance_conversation_thread(paired.owner_key)

    scheduled = schedule_answer(
        _tool_context(asking.state, paired, asking.identity.canonical_session_id),
        initial_delay_seconds=5,
        prompt="synthetic prompt",
    )

    assert scheduled["status"] == "scheduled"
    task, delivered = await _deliver_the_deferred_answer(monkeypatch, sandbox)
    # Kept word for word, next to the history id it always kept.
    assert task["creator_conversation_session_id"] == asking.given["conversation_session_id"]
    assert task["creator_external_session_id"] == asking.identity.canonical_session_id
    assert delivered["status"] == "success"
    [message] = channel.sent
    metadata = message.payload["metadata"]
    assert message.payload["message"].endswith("Synthetic final response.")
    assert asking.given["conversation_thread_id"] == 2
    assert _in_conversation_now(webrtc)["conversation_thread_id"] == 3
    assert metadata["conversation_session_id"] == asking.given["conversation_session_id"]
    assert metadata["conversation_force_target"] is True


@pytest.mark.asyncio
@DESKTOP_AGENTS
async def test_deferred_desktop_answer_of_a_turn_given_no_conversation_follows_the_device(
    monkeypatch, tmp_path, sandbox, schedule_answer
):
    """A turn the device was not given an id for saves none, so nothing is guessed or pinned."""
    webrtc, channel, paired, manager = connect_device(tmp_path, monkeypatch)
    monkeypatch.setattr(scheduler_service, "_get_runtime_server_module", lambda: _runtime_server(webrtc))
    delivery_target = {"transport": "webrtc", "owner_key": paired.owner_key, "session_id": "synthetic-live"}
    asking = _a_turn_of_the_device(webrtc, delivery_target)
    for key in ("autoyou_conversation_session_id", "user:autoyou_conversation_session_id"):
        del asking.state[key]
    manager.advance_conversation_thread(paired.owner_key)

    scheduled = schedule_answer(
        _tool_context(asking.state, paired, asking.identity.canonical_session_id),
        initial_delay_seconds=5,
        prompt="synthetic prompt",
    )

    assert scheduled["status"] == "scheduled"
    task, delivered = await _deliver_the_deferred_answer(monkeypatch, sandbox)
    assert "creator_conversation_session_id" not in task
    assert task["creator_external_session_id"] == asking.identity.canonical_session_id
    assert delivered["status"] == "success"
    [message] = channel.sent
    metadata = message.payload["metadata"]
    now = _in_conversation_now(webrtc)
    assert metadata["conversation_session_id"] == now["conversation_session_id"]
    assert metadata["conversation_thread_id"] == 2
    assert "conversation_force_target" not in metadata

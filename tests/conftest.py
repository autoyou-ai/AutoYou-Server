# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-6ad4d31b0ed3a3036a31a619


__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"
__debug_provenance_i__ = "AUTOYOU-PROVENANCE-I-4c6a376f4c4e6d206f722055-6ad4d31b0ed3a3036a31a619"

from pathlib import Path
import builtins
import json
import os
import sys

import pytest


collect_ignore_glob = [
    "tools/*.py",
]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "agent_public: public built-in agent runtime coverage")
    config.addinivalue_line("markers", "agent_private: private or optional agent coverage")
    config.addinivalue_line("markers", "server: source server/shared runtime coverage")
    config.addinivalue_line("markers", "private_cloud: private cloud/account-service integration coverage")
    config.addinivalue_line("markers", "bootstrap_e2e: opt-in live bootstrap/server end-to-end coverage")
    config.addinivalue_line(
        "markers",
        "scenario(id): links a test to an E2E scenario registry row (tests/e2e/registry)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    tests_root = Path(__file__).resolve().parent
    marker_by_top_path = {
        ("agents", "public"): "agent_public",
        ("agents", "private"): "agent_private",
        ("private_cloud",): "private_cloud",
        ("server",): "server",
    }
    for item in items:
        try:
            rel_parts = Path(str(item.fspath)).resolve().relative_to(tests_root).parts
        except Exception:
            continue
        for prefix, marker in marker_by_top_path.items():
            if rel_parts[: len(prefix)] == prefix:
                item.add_marker(marker)
                break
        if rel_parts[:3] in (("server", "e2e", "bootstrap"), ("server", "e2e", "scenarios")):
            item.add_marker("bootstrap_e2e")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Record scenario-marker outcomes for the E2E registry when asked.

    When AUTOYOU_SCENARIO_RESULTS points at a file, tests carrying
    @pytest.mark.scenario("AY-...") append one JSON line per scenario id so
    e2e-validate's report step can map results back to registry rows.
    """
    outcome = yield
    results_path = os.environ.get("AUTOYOU_SCENARIO_RESULTS")
    if not results_path:
        return
    report = outcome.get_result()
    is_final = report.when == "call" or (report.when == "setup" and report.outcome != "passed")
    if not is_final:
        return
    scenario_ids = [mark.args[0] for mark in item.iter_markers("scenario") if mark.args]
    if not scenario_ids:
        return
    with open(results_path, "a", encoding="utf-8") as handle:
        for scenario_id in scenario_ids:
            handle.write(
                json.dumps(
                    {
                        "scenario": scenario_id,
                        "nodeid": item.nodeid,
                        "outcome": report.outcome,
                        "when": report.when,
                    }
                )
                + "\n"
            )


@pytest.fixture(autouse=True)
def _restore_packaged_runtime_hints():
    """Keep packaged-runtime simulations from leaking into later tests."""
    sentinel = object()
    previous_sys_frozen = getattr(sys, "frozen", sentinel)
    previous_builtins_compiled = getattr(builtins, "__compiled__", sentinel)
    env_names = (
        "AUTOYOU_PACKAGED_RUNTIME",
        "AUTOYOU_PACKAGED_RESOURCES_ROOT",
        "PYTHON_DOTENV_DISABLED",
    )
    previous_env = {name: os.environ.get(name) for name in env_names}
    try:
        yield
    finally:
        if previous_sys_frozen is sentinel:
            try:
                delattr(sys, "frozen")
            except AttributeError:
                pass
        else:
            sys.frozen = previous_sys_frozen

        if previous_builtins_compiled is sentinel:
            try:
                delattr(builtins, "__compiled__")
            except AttributeError:
                pass
        else:
            builtins.__compiled__ = previous_builtins_compiled

        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.fixture(autouse=True)
def _default_unlocked_runtime():
    """Default the in-memory server unlock-state to ``Ready`` for each test.

    The startup unlock-state guard (added with the LICENSE/terms sequencing)
    returns ``451 SetupPending`` / ``401 Locked`` for admin, API, and cloud
    endpoints until the operator unlocks. The vast majority of tests exercise the
    post-unlock operator surface, so default to ``Ready`` here. This only acts when
    ``server`` is already imported (so it's a no-op for pure client/agent tests),
    and conftest fixtures run *before* module fixtures - so the dedicated
    unlock-flow tests, whose own autouse fixture clears ``_unlock_state_mem``
    afterwards, still cover the SetupPending/Locked/451 paths.
    """
    srv = sys.modules.get("server")
    if srv is None or not hasattr(srv, "STATE"):
        yield
        return
    prev_state = getattr(srv.STATE, "_unlock_state_mem", None)
    srv.STATE._unlock_state_mem = "Ready"
    # Reset the shared login/verify rate limiter so attempts don't accumulate
    # across tests and trip a 429 in a later test that POSTs /login or /verify.
    for limiter_name in ("LOGIN_RATE_LIMITER", "AUTH_GLOBAL_RATE_LIMITER"):
        limiter = getattr(srv, limiter_name, None)
        if limiter is not None and isinstance(getattr(limiter, "_requests", None), dict):
            limiter._requests.clear()
    try:
        yield
    finally:
        srv.STATE._unlock_state_mem = prev_state


@pytest.fixture(autouse=True)
def isolate_python_client_direct_pair_id(monkeypatch, tmp_path):
    """Keep tests that import autoyou_client from writing to the real user home."""
    client_id_path = tmp_path / ".autoyou_direct_pair_client_id"
    for module in list(sys.modules.values()):
        module_dict = getattr(module, "__dict__", None)
        if not isinstance(module_dict, dict):
            continue
        cls = module_dict.get("AutoYouClient")
        if not isinstance(cls, type):
            continue
        if getattr(cls, "__name__", "") != "AutoYouClient":
            continue
        if not hasattr(cls, "_direct_pair_client_id_path"):
            continue
        monkeypatch.setattr(
            cls,
            "_direct_pair_client_id_path",
            classmethod(lambda klass, path=client_id_path: path),
        )


@pytest.fixture(autouse=True)
def mock_tunnelmole_service_start(request, monkeypatch):
    """Prevent tests from opening live reverse proxy connections."""
    if "test_tunnelmole_transport_direct.py" in request.node.nodeid:
        return

    async def fake_start(self, *args, **kwargs):
        self._status = "running"
        self._public_url = "https://mocked.tunnelmole.net"
        return True

    try:
        from shared.tunnelmole_service import TunnelmoleService
        monkeypatch.setattr(TunnelmoleService, "start", fake_start)
    except ImportError:
        pass

@pytest.fixture(autouse=True)
def mock_global_tunnelmole_downloads(request, monkeypatch, tmp_path):
    """Prevent any test from reaching out to the internet to download tunnelmole."""
    if "test_tunnelmole_downloader.py" in request.node.nodeid:
        return
    try:
        import shared.tunnelmole_downloader
        def _fake_download(*args, **kwargs):
            dummy = tmp_path / "tmole.exe"
            dummy.write_bytes(b"dummy")
            return dummy
        monkeypatch.setattr(shared.tunnelmole_downloader, "download_tunnelmole", _fake_download)
    except ImportError:
        pass

@pytest.fixture(autouse=True)
def mock_global_whisper_downloads(request, monkeypatch, tmp_path):
    """Prevent any test from reaching out to the internet to download whisper."""
    if "test_whisper_downloader.py" in request.node.nodeid or "test_platform_runtime_paths.py" in request.node.nodeid:
        return
    try:
        import shared.whisper_downloader
        def _fake_download_model(*args, **kwargs):
            dummy = tmp_path / "ggml-tiny.en.bin"
            dummy.write_bytes(b"dummy")
            return dummy
        def _fake_get_binary(*args, **kwargs):
            dummy = tmp_path / "main.exe"
            dummy.write_bytes(b"dummy")
            return dummy
        monkeypatch.setattr(shared.whisper_downloader, "download_whisper_model", _fake_download_model)
        monkeypatch.setattr(shared.whisper_downloader, "get_whisper_cpp_binary", _fake_get_binary)
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _restore_runtime_state_env():
    """Keep runtime-state environment exports from leaking between tests.

    ``ServiceManager`` deliberately exports its resolved database path into
    ``os.environ`` so spawned child processes inherit it. That is correct in
    production, but it means any test that builds a ServiceManager against a
    ``tmp_path`` rewrites process-wide state that every later test then picks
    up - ``ServiceConfig()`` reads the same variable when no explicit path is
    given.

    That is exactly how ``test_session_manager_defaults_use_isolated_database_paths``
    came to fail only when ``tests/shared`` ran before it: it asserted its
    defaults land inside the isolated root, and inherited a previous test's
    temp path instead. Snapshotting the documented runtime-state variables and
    restoring them after each test removes the whole class of ordering bug
    rather than just that one instance.
    """
    from shared.platform_runtime import TEST_RUNTIME_STATE_ENV_VARS

    tracked = (*TEST_RUNTIME_STATE_ENV_VARS, "AUTOYOU_SESSION_DB_PATH")
    sentinel = object()
    previous = {name: os.environ.get(name, sentinel) for name in tracked}
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is sentinel:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

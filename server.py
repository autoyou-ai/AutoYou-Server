# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-J-fifteenpercent-7c55c878be1e5bbb372fbab5

"""
AutoYou FastAPI Server.

This module provides the main AutoYou admin, auth, and runtime server process.
"""

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.4 (AI training prohibited)"


import argparse
import ast
import codecs
import contextlib
import functools
import hashlib
import html
import inspect
from urllib.parse import urlencode
import os
import asyncio
import importlib
import json
import logging
import math
import multiprocessing
import shutil
import subprocess
import threading
import uuid
import secrets
import re
import sqlite3

from routers.admin import register_routes as register_admin_routes
from routers.pairing import register_routes as register_pairing_routes
from routers.webrtc import register_routes as register_webrtc_routes
from routers.agents import register_routes as register_agents_routes
from routers.messaging import register_routes as register_messaging_routes
from routers.admin_ui import register_routes as register_admin_ui_routes
from routers.models import register_routes as register_models_routes
from routers.services import register_routes as register_services_routes
from routers.cloud import register_routes as register_cloud_routes
from routers.mcp import register_routes as register_mcp_routes
from routers.peer_rendezvous import register_routes as register_peer_rendezvous_routes
from routers.moderation import register_routes as register_moderation_routes
from routers.website_gateway import register_routes as register_website_gateway_routes
from routers.ai_opt_out import register_ai_opt_out_routes
from routers.ai_agent import (
    attach_ai_agent_endpoints,
    bind_runtime as _bind_ai_agent_routes_runtime,
    _install_ai_agent_dev_graph_compat_routes,
)
import signal
import sys
import time
import traceback
import socket
import types
import textwrap
import datetime
from collections import deque
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, unquote, urlsplit
from contextlib import suppress
from dataclasses import replace
from functools import lru_cache
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, Awaitable, Set, List, Callable, Iterable, cast

__debug_provenance_j__ = "AUTOYOU-PROVENANCE-J-fifteenpercent-7c55c878be1e5bbb372fbab5"


# Keep one canonical module object even when the runtime launches this file as
# `__main__` (for example via `python server.py` from the tray app). Several
# runtime helpers lazily `import server`; without this alias they would create a
# second module instance with its own empty STATE/WEBRTC singletons.
sys.modules["server"] = sys.modules[__name__]

from core_server.config import (
    LOGGER,
    bind_runtime as _bind_config_runtime,
    get_runtime_dependency_versions,
    build_runtime_environment_status,
    _get_positive_int_env,
    _get_positive_float_env,
    _get_instance_name_env,
)
from core_server.state import (
    STATE,
    ServerState,
    ConfigWriteBlocked,
    RateLimiter,
    prune_otp_cache,
    prune_session_cache,
    CONFIG_STORE_NONE,
    CONFIG_STORE_KEYSTORE,
    CONFIG_STORE_ENCRYPTED,
    ADMIN_STATUS_CACHE_TTL_SECONDS,
    _normalize_config_store,
    _has_loaded_config_session,
    _can_persist_config,
    _config_write_block_reason,
    _loaded_config_for_update,
    _json_config_write_blocked_response,
)
from core_server.security import (
    bind_runtime as _bind_security_runtime,
    _get_unlock_file_path,
    _load_unlock_metadata,
    _hash_password,
    _verify_password,
    _verify_password_with_params,
    _agreement_metadata_is_current,
    _safe_write_unlock_json,
    _sync_unlock_metadata,
    _get_ai_agent_internal_api_token_path,
    _load_or_create_ai_agent_internal_api_token,
    _request_uses_ai_agent_internal_token,
)
from core_server.http_helpers import (
    _http_stream_text_charset,
    _build_incremental_http_text_decoder,
    _decode_incremental_http_text_chunk,
    _flush_incremental_http_text_decoder,
    _http_header_value,
    _http_content_length_bytes,
    _is_probable_media_response,
)
from core_server.webrtc_engine import (
    WebRTCManager,
    bind_runtime as _bind_webrtc_runtime,
)
from core_server.services import (
    bind_runtime as _bind_process_runtime,
    _is_agent_process_running,
    _get_agent_process_pid,
    _looks_like_python_executable,
    _compiled_runtime_cwd,
    _build_compiled_ai_agent_process_command,
    _start_compiled_ai_agent_process,
    _wait_for_agent_process_exit,
    _kill_agent_process,
    _collect_process_tree_pids,
    _close_process_handle,
    _looks_like_lingering_ai_agent_process,
    _pid_is_running,
    _process_listens_on_port,
    _process_cwd_is_under_app_root,
    _looks_like_orphaned_source_ai_agent_process,
    _cleanup_lingering_ai_agent_processes,
    _is_ai_agent_server_healthy,
    _get_ai_agent_startup_timeout_seconds,
    _wait_for_ai_agent_server_ready,
    _list_non_daemon_runtime_threads,
    _terminate_active_multiprocessing_children,
    _flush_standard_streams,
    _cleanup_loky_executor,
    _finalize_runtime_process_exit,
    _mark_runtime_exit_complete,
    _stop_cloud_sse_listener,
    _stop_scheduler_service,
    run_agent_server,
    _force_kill_process_tree,
    _probe_ollama_runtime,
    _log_missing_ollama_model,
    _launch_ollama_background,
    _ensure_local_ollama_runtime_ready,
    _cloud_sse_listener_loop,
    _start_cloud_sse_listener,
    _initialize_services_on_startup,
    _start_autoyou_page_service_if_enabled,
    _telegram_status,
    _call_telegram_user_service_method,
    _redact_telegram_user_status_payload,
    _telegram_user_qr_url,
    _telegram_user_message_metadata,
    _telegram_user_call_succeeded,
    _telegram_user_status,
    _bluetooth_pairing_runtime_status,
    stop_bluetooth_pairing_service,
    start_or_restart_bluetooth_pairing_service,
    _persist_telegram_user_session,
    _clear_telegram_user_session,
    stop_telegram_user,
    start_or_restart_telegram_user,
    stop_telegram,
    start_or_restart_telegram,
    stop_signal,
    stop_whatsapp,
    start_or_restart_signal,
    start_or_restart_whatsapp,
    _check_and_update_whatsapp_pairing_status,
    _check_and_update_pairing_status,
    _signal_status,
    start_tunnelmole_service_no_timer,
    _get_tunnelmole_target_port,
    _default_password_on_tunnel_allowed,
    _tunnelmole_blocked_by_default_password,
    _get_or_create_tunnelmole_service,
    start_tunnelmole_timer,
    extend_tunnelmole_timer,
    stop_tunnelmole_timer,
    tunnelmole_timeout_handler,
    start_tunnelmole_service_with_timer,
    start_tunnelmole_service_for_current_mode,
    start_tunnelmole_service,
    stop_tunnelmole_service,
    get_tunnelmole_status,
    _whatsapp_status,
    _autostart_allowed_on_default_password,
    should_start_ai_agent_server,
    _set_ai_agent_multiprocessing_executable,
    start_ai_agent_server_background,
    schedule_ai_agent_server_autostart,
    start_auth_server_background,
    stop_ai_agent_server,
    stop_auth_server,
    restart_ai_agent_server,
    _ai_agent_server_status,
    is_port_in_use,
    _normalize_probe_host,
    _find_available_local_port,
    _agent_frontend_backend_app_exists,
    _managed_frontend_runtime_specs,
    _find_managed_frontend_module_path,
    _prepend_import_path,
    _append_package_search_path,
    _ensure_managed_frontend_runtime_import_paths,
    _ensure_runtime_package_module,
    _load_managed_frontend_app,
    _stop_managed_frontend_backend,
    _start_managed_frontend_backend,
    _register_admin_frontend_proxy,
    sync_managed_frontend_backends,
    stop_managed_frontend_backends,
    start_autoyou_page_service_background,
    stop_autoyou_page_service_background,
    sync_server_advertisement,
    stop_server_advertisement,
    start_or_restart_autoyou_page_service,
    restart_autoyou_page_service,
    _autoyou_page_service_status,
    _request_admin_server_exit,
    _run_shutdown_step,
    _stop_runtime_services_for_shutdown,
    graceful_shutdown,
    _install_aiortc_exception_filter,
    main,
    _run_ai_agent_server_cli_if_requested,
)
from core_server.app import (
    create_apps,
    _is_malformed_host_header,
)

_bind_process_runtime(sys.modules[__name__])
_bind_webrtc_runtime(sys.modules[__name__])
_bind_config_runtime(sys.modules[__name__])
_bind_security_runtime(sys.modules[__name__])
_bind_ai_agent_routes_runtime(sys.modules[__name__])

from dotenv import load_dotenv

from shared.platform_runtime import (
  configure_runtime,
  configure_whisper_cache_environment,
  get_adk_agents_base_dir,
  get_config_dir,
  get_application_root,
  resolve_adk_agents_base_dir,
  resolve_adk_web_assets_dir,
  get_embedded_agents_root,
  get_dynamic_agents_root,
  get_resources_root,
  get_node_command,
  get_platform,
  get_node_service_dir,
  is_compiled,
  get_user_data_dir,
  get_logs_dir,
  get_mutable_data_dir,
  get_service_data_dir,
  get_whisper_cache_dir,
  normalize_local_filesystem_path,
  strip_windows_extended_path_prefix,
  find_bundled_node_executable,
  find_bundled_playwright_root,
  find_bundled_browser_executable,
  find_bundled_ollama_executable,
  find_bundled_ollama_models_dir,
  find_bundled_whisper_models_dir,
  get_jailbreak_data_dir,
  is_jailbreak_active,
  get_jailbreak_root_prompt,
  sign_jailbreak_root_prompt,
  adopt_unsigned_jailbreak_root_prompt,
  JAILBREAK_ACKNOWLEDGEMENT_FILENAME,
  JAILBREAK_ROOT_PROMPT_FILENAME,
  JAILBREAK_ROOT_PROMPT_SIGNATURE_FILENAME,
  use_readable_mime_database,
)

# Content-type guesses happen all over the request path; settle which system
# mime files this process can read before the first one.
use_readable_mime_database()
from shared.process_lifecycle import (
    AUTOYOU_PARENT_PID_ENV,
    add_parent_pid_environment,
    force_kill_process_tree,
    live_pids,
    process_spawn_kwargs,
    start_parent_process_watchdog,
)
from shared.first_run import (
    CURRENT_AGREEMENT_VERSION,
    clear_license_acknowledgement,
    is_license_acknowledgement_pending_unlock,
    is_license_acknowledged,
    license_ack_path,
    record_license_acknowledgement,
)
from shared.tunnelmole_service import (
    TunnelmoleService,
    _redact_tunnelmole_url as _redact_tunnelmole_url_for_log,
    _should_log_url_plain as _should_log_tunnelmole_url_plain,
    is_official_tunnelmole_remote_host,
)
from shared.audio_playback_settings import (
    AUDIO_PLAYBACK_ENABLED_ENV,
    MUSIC_LIBRARY_DIRS_ENV,
    ensure_music_library_dirs,
    set_audio_playback_enabled_env,
)
from shared.cloud_entitlements_client import CloudEntitlementsClient
from shared.pairing_response import parse_otp_response_payload
from shared.shared_device_pairing import (
    pairing_auth_profile as _device_pairing_auth_profile,
    generate_key_material as _generate_shared_device_key_material,
    is_key_material as _is_shared_device_key_material,
)
from shared.tunnelmole_config import (
    PAIR_CODE_MODE_RANDOM_OTP as _TUNNELMOLE_PAIR_CODE_MODE_RANDOM_OTP,
    PAIR_CODE_MODE_AUTHENTICATOR as _TUNNELMOLE_PAIR_CODE_MODE_AUTHENTICATOR,
    CONNECTION_MODE_TIMED as _TUNNELMOLE_CONNECTION_MODE_TIMED,
    CONNECTION_MODE_UNMANAGED as _TUNNELMOLE_CONNECTION_MODE_UNMANAGED,
    normalize_pair_code_mode as _normalize_tunnelmole_pair_code_mode,
    normalize_connection_mode as _normalize_tunnelmole_connection_mode,
)
from shared.ui_theme import get_ui_theme, normalize_ui_theme, set_ui_theme
from shared.pending_media_queue import (
    delete_media_payload,
    prune_and_trim_media_entries,
    read_media_payload,
    store_media_payload,
)
from shared.log_redaction import redact_identifier
from shared.admin_setup_profiles import build_setup_profile_payload, compile_setup_recipe
from shared.url_safety import (
    UnsafeURLError,
    assert_safe_http_url,
    build_safe_httpx_transport,
    resolve_safe_http_ip,
)
from shared.proxy_target_policy import (
    ProxyTargetBlocked,
    assert_proxy_target_allowed,
    enforce_loopback_port_policy,
    is_loopback_hostname,
)
from shared.secure_storage import (
    FILE_HEADER as SPM_FILE_HEADER,
    SECURE_PROFESSIONAL_MAXIMUS_MODE,
    SecureStorageError,
    append_secure_file,
    disable_secure_storage,
    enable_secure_storage,
    load_secure_json,
    read_secure_file,
    rotate_secure_storage,
    save_secure_json,
    seal_secure_paths,
    secure_storage_enabled,
    secure_storage_status,
    unseal_secure_storage,
    write_secure_file,
    recover_stranded_envelopes,
)

# Load environment variables from .env file.
# Wrapped in try/except: Nuitka compiled builds don't set sys.frozen, so
# python-dotenv falls back to frame inspection which produces a relative
# co_filename ("server.py"), causing os.path.isdir("") → AssertionError.
# Dotenv is optional for compiled releases - env vars are set by the launcher.
try:
    load_dotenv()
except Exception:
    pass
configure_runtime(__file__)
try:
    # Sign any override that predates signature enforcement, so upgrading does
    # not silently drop an operator's existing Prompt Override. Runs in the
    # trusted server process, which is the same authority as the admin save
    # path; anything written after this point needs a real signature.
    adopt_unsigned_jailbreak_root_prompt(anchor=__file__)
except Exception:
    pass
try:
    ensure_music_library_dirs(__file__)
except Exception:
    pass

# Initialize logger early
logging.basicConfig(level=logging.INFO)
try:
  configure_whisper_cache_environment("AutoYou")
except Exception as exc:
  LOGGER.warning("Failed to configure Whisper cache environment: %s", exc)

# Core HTTP helpers, config helpers, and security tokens are imported from core_server package above.


def _decode_datachannel_http_body(
    body: Any,
    *,
    compressed: bool = False,
    body_base64: bool = False,
) -> Optional[bytes]:
  if body in (None, ""):
    return None
  import base64
  import gzip
  if isinstance(body, (bytes, bytearray)):
    raw = bytes(body)
  else:
    text = str(body)
    if body_base64 or compressed:
      raw = base64.b64decode(text.encode("ascii"), validate=True)
    else:
      try:
        raw = base64.b64decode(text.encode("ascii"), validate=True)
      except Exception:
        raw = text.encode("utf-8")
  if compressed:
    raw = gzip.decompress(raw)
  return raw

def _should_use_ack_backed_binary_http_stream(
  *,
  is_textual: bool,
  content_type: str,
  url: str,
  request_headers: Dict[str, Any],
  response_headers: Dict[str, Any],
  min_bytes: int,
) -> bool:
  if is_textual or min_bytes <= 0:
    return False
  content_length = _http_content_length_bytes(response_headers)
  if content_length is not None and content_length >= min_bytes:
    return True
  range_requested = bool(_http_header_value(request_headers, "range").strip())
  if range_requested and (content_length is None or content_length > 0):
    return True
  return _is_probable_media_response(content_type, url) and (
    content_length is None or content_length >= 1024
  )

def _select_binary_http_stream_chunk_size(
  *,
  configured_chunk_size: int,
  safe_chunk_size: int,
) -> int:
  try:
    configured = int(configured_chunk_size)
  except (TypeError, ValueError):
    configured = 1
  try:
    safe = int(safe_chunk_size)
  except (TypeError, ValueError):
    safe = 1

  # HTTP_STREAM_DATA is already the application-level stream frame. Letting a
  # frame exceed the current datachannel envelope makes it enter the generic
  # JSON chunk/ACK reassembly path, which turns modest media responses into
  # thousands of tiny SCTP sends. Keep every stream frame datachannel-native and
  # rely on the manager's bufferedAmount pacing for backpressure.
  return max(1, min(max(1, configured), max(1, safe)))

SHUTDOWN_TOKEN_ENV = "AUTOYOU_SHUTDOWN_TOKEN"
SHUTDOWN_TOKEN_HEADER = "x-autoyou-shutdown-token"
AI_AGENT_SESSION_SERVICE_URI_ENV = "AUTOYOU_AI_SESSION_SERVICE_URI"
AI_AGENT_ARTIFACT_SERVICE_URI_ENV = "AUTOYOU_AI_ARTIFACT_SERVICE_URI"
AI_AGENT_INTERNAL_API_TOKEN_ENV = "AUTOYOU_AI_INTERNAL_API_TOKEN"
MCP_API_TOKEN_ENV = "AUTOYOU_MCP_API_TOKEN"
AUTOYOU_SESSION_DB_PATH_ENV = "AUTOYOU_SESSION_DB_PATH"
# Threaded into the AI Agent worker process so its OTP gate (guarding the
# opt-in LAN/HTTPS listener) can verify codes without any shared in-memory
# session state -- TOTP verification is stateless HMAC, so the secret alone
# is enough for that separate process to check codes itself.
AI_AGENT_TOTP_SECRET_ENV = "AUTOYOU_AI_AGENT_TOTP_SECRET"

# Guard: graceful_shutdown() must run at most once per process lifetime.
# Without this, the SIGINT path (signal_handler -> uvicorn exits -> explicit
# graceful_shutdown() call) races with the POST /shutdown deferred-task path
# when the bootstrap wrapper sends an HTTP shutdown request concurrently.
_GRACEFUL_SHUTDOWN_STARTED: bool = False
SHUTDOWN_HARD_EXIT_TIMEOUT_ENV = "AUTOYOU_SHUTDOWN_HARD_EXIT_SECONDS"
DEFAULT_SHUTDOWN_HARD_EXIT_TIMEOUT_SECONDS = 240.0
_SHUTDOWN_WATCHDOG_LOCK = threading.Lock()
_SHUTDOWN_WATCHDOG: Optional[threading.Timer] = None
_RUNTIME_EXIT_COMPLETE = threading.Event()

def _request_uses_shutdown_token(request: "Request") -> bool:
  expected_token = str(os.getenv(SHUTDOWN_TOKEN_ENV, "")).strip()
  provided_token = str(request.headers.get(SHUTDOWN_TOKEN_HEADER, "")).strip()
  client_host = getattr(getattr(request, "client", None), "host", None)
  if not expected_token or not provided_token or not _is_loopback_client_host(client_host):
    return False
  try:
    return secrets.compare_digest(provided_token, expected_token)
  except Exception:
    return provided_token == expected_token

import copy
import uvicorn
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, StreamingResponse, FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from autoyou_agents.internet_agent.internet_tool import register_fastapi_cleanup

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import io

# ── OS Keystore (optional) ────────────────────────────────────────────────────
# Provides transparent at-rest encryption using the platform credential store
# (macOS Keychain, Windows Credential Manager, Linux SecretService / gnome-keyring).
# No password friction - the OS manages the key.  Falls back to legacy
# PBKDF2+Fernet password-based encryption if unavailable.
try:
  from shared.keystore import (  # type: ignore[import]
    KeystoreJsonStore as _KeystoreJsonStore,
    keyring_available as _ks_available,
    get_keystore_status as _ks_get_status,
    get_keyring_password as _ks_get_password,
    macos_keychain_bootstrap_timeout_seconds as _ks_macos_keychain_bootstrap_timeout_seconds,
    delete_key as _ks_delete_key,
    call_keyring_operation as _ks_call_keyring_operation,
    _SERVER_SERVICE_NAME as _KS_SERVICE_NAME,
    _DEFAULT_CRED_NAME as _KS_CRED_NAME,
  )
  _HAS_SERVER_KEYSTORE = True
except ImportError:
  _KeystoreJsonStore = None  # type: ignore[assignment, misc]
  _ks_available = lambda: False  # type: ignore[assignment]
  _ks_get_status = lambda *a, **k: {"keyring_installed": False, "available": False, "backend": "none", "has_key": False}  # type: ignore[assignment]
  _ks_get_password = lambda *a, **k: None  # type: ignore[assignment]
  _ks_macos_keychain_bootstrap_timeout_seconds = lambda: None  # type: ignore[assignment]
  _ks_delete_key = lambda *a, **k: False  # type: ignore[assignment]
  _ks_call_keyring_operation = lambda operation, *a, **k: operation(*a)  # type: ignore[assignment]
  _KS_SERVICE_NAME = "autoyou-server"
  _KS_CRED_NAME = "config-encryption-key"
  _HAS_SERVER_KEYSTORE = False

try:
    import keyring as _server_keyring  # type: ignore[import]
    from keyring.errors import KeyringError as _ServerKeyringError  # type: ignore[import]
except Exception:
    _server_keyring = None  # type: ignore[assignment]
    _ServerKeyringError = Exception  # type: ignore[assignment, misc]

# --- Background Task Tracking ---
# Keep strong references to background tasks to prevent quiet destruction
# by the Python 3.10+ garbage collector.
background_tasks = set()

def track_background_task(coro):
    """Schedules a coroutine as a background task and tracks its reference."""
    task = asyncio.create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)
    return task

def _describe_asyncio_task(task: asyncio.Task) -> str:
    try:
        coro = task.get_coro()
        coro_name = getattr(coro, "__qualname__", None) or getattr(coro, "__name__", None) or repr(coro)
    except Exception:
        coro_name = "unknown"
    try:
        task_name = task.get_name()
    except Exception:
        task_name = None
    return f"{task_name or 'task'}:{coro_name}"

async def _cancel_asyncio_task(task: Optional[asyncio.Task], label: str, timeout: float = 5.0) -> bool:
    if task is None:
        return True
    if task.done():
        return True
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=timeout)
    except asyncio.CancelledError:
        return True
    except asyncio.TimeoutError:
        LOGGER.warning("%s did not cancel within %.1fs", label, timeout)
        return False
    except Exception as exc:
        LOGGER.warning("%s raised during cancellation: %s", label, exc)
        return False
    return True

async def _cancel_tracked_background_tasks(timeout: float = 5.0) -> bool:
    current_task = asyncio.current_task()
    tasks = [task for task in list(background_tasks) if task is not current_task and not task.done()]
    if not tasks:
        return True

    for task in tasks:
        task.cancel()

    done, pending = await asyncio.wait(tasks, timeout=timeout)
    success = True

    for task in done:
        background_tasks.discard(task)
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            continue
        except Exception as inspect_err:
            LOGGER.debug("Failed to inspect cancelled background task: %s", inspect_err)
            continue
        if exc is not None:
            LOGGER.warning("Background task exited with error during shutdown: %s", exc)
            success = False

    if pending:
        success = False
        LOGGER.warning(
            "Background tasks still pending after %.1fs: %s",
            timeout,
            ", ".join(_describe_asyncio_task(task) for task in pending),
        )

    return success

async def _cancel_remaining_asyncio_tasks(timeout: float = 5.0) -> bool:
    """Cancel fire-and-forget tasks that were not registered in our tracker."""
    current_task = asyncio.current_task()
    tasks = [
        task
        for task in asyncio.all_tasks()
        if task is not current_task and not task.done()
    ]
    if not tasks:
        return True

    for task in tasks:
        task.cancel()

    done, pending = await asyncio.wait(tasks, timeout=timeout)
    success = not pending
    for task in done:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            continue
        except Exception as inspect_err:
            LOGGER.debug("Failed to inspect shutdown task: %s", inspect_err)
            continue
        if exc is not None:
            LOGGER.warning("Asyncio task exited with error during shutdown: %s", exc)
            success = False

    if pending:
        LOGGER.warning(
            "Asyncio tasks still pending after %.1fs: %s",
            timeout,
            ", ".join(_describe_asyncio_task(task) for task in pending),
        )
    return success

def _shutdown_hard_exit_timeout_seconds() -> float:
    return _get_positive_float_env(
        SHUTDOWN_HARD_EXIT_TIMEOUT_ENV,
        default=DEFAULT_SHUTDOWN_HARD_EXIT_TIMEOUT_SECONDS,
        minimum=30.0,
    )

def _arm_shutdown_watchdog() -> None:
    """Guarantee that a stuck asyncio/interpreter teardown cannot hang launchers."""
    global _SHUTDOWN_WATCHDOG
    with _SHUTDOWN_WATCHDOG_LOCK:
        if _RUNTIME_EXIT_COMPLETE.is_set() or _SHUTDOWN_WATCHDOG is not None:
            return
        timeout = _shutdown_hard_exit_timeout_seconds()

        def _force_exit_if_stuck() -> None:
            if _RUNTIME_EXIT_COMPLETE.is_set():
                return
            try:
                LOGGER.error(
                    "Shutdown exceeded %.1fs; forcing process exit to prevent a hung launcher",
                    timeout,
                )
            except Exception:
                pass
            try:
                _flush_standard_streams()
            except Exception:
                pass
            # This is intentionally the last resort. The bootstrap wrapper
            # owns the process tree and performs a second descendant cleanup.
            finally:
                os._exit(0)

        _SHUTDOWN_WATCHDOG = threading.Timer(timeout, _force_exit_if_stuck)
        _SHUTDOWN_WATCHDOG.daemon = True
        _SHUTDOWN_WATCHDOG.start()
        LOGGER.info("Shutdown watchdog armed for %.1fs", timeout)


# Crypto for config encryption
def _get_key_from_password(password: str, salt: bytes) -> bytes:
    """Derive a key from the password using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode('utf-8')))

def aead_encrypt(plaintext: str, password: str) -> str:
    """Encrypt plaintext using Fernet (AES-128-CBC with HMAC-SHA256)."""
    salt = os.urandom(16)
    key = _get_key_from_password(password, salt)
    f = Fernet(key)
    ciphertext = f.encrypt(plaintext.encode('utf-8'))
    # Fernet already returns base64url ASCII, so the salt is prepended to the
    # token's *bytes*. Concatenating the text and encoding again would encode
    # the payload twice and cost about a quarter of the envelope for nothing.
    return base64.urlsafe_b64encode(
        salt + base64.urlsafe_b64decode(ciphertext)
    ).decode('ascii').rstrip('=')

def aead_decrypt(envelope: str, password: str) -> str:
    """Decrypt an envelope using Fernet."""
    normalized = envelope.strip()
    decoded_envelope = base64.urlsafe_b64decode(normalized + '=' * (-len(normalized) % 4))
    salt = decoded_envelope[:16]
    ciphertext = base64.urlsafe_b64encode(decoded_envelope[16:])
    key = _get_key_from_password(password, salt)
    f = Fernet(key)
    return f.decrypt(ciphertext).decode('utf-8')

def generate_hash(s: str) -> str:
    """Generate a SHA-256 hex digest for the provided string."""
    import hashlib
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

# Optional TOTP (used in Secure Professional mode)
try:
    import pyotp  # type: ignore
except Exception:
    pyotp = None

# Optional local QR rendering (no external network call - required for secret
# material such as TOTP provisioning URIs, which must never leave the machine).
try:
    import qrcode  # type: ignore
except Exception:
    qrcode = None

# Telegram Bot and WebRTC
try:
    import httpx
except Exception:
    httpx = None

# Telegram bot
try:
    import websockets
except ImportError:
    websockets = None

try:
    from telegram import Update
    from telegram.constants import ChatAction
    from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut
    from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters
except Exception:
  Update = cast(Any, None)
  ChatAction = cast(Any, None)
  BadRequest = cast(Any, None)
  NetworkError = cast(Any, None)
  RetryAfter = cast(Any, None)
  TimedOut = cast(Any, None)
  Application = cast(Any, None)
  CommandHandler = cast(Any, None)
  ContextTypes = cast(Any, None)
  ConversationHandler = cast(Any, None)
  MessageHandler = cast(Any, None)
  filters = cast(Any, None)

# WebRTC (aiortc)
try:
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer, RTCIceCandidate
    from aiortc.sdp import candidate_from_sdp, candidate_to_sdp
except Exception:
  RTCPeerConnection = cast(Any, None)
  RTCSessionDescription = cast(Any, None)
  RTCConfiguration = cast(Any, None)
  RTCIceServer = cast(Any, None)
  RTCIceCandidate = cast(Any, None)
  candidate_from_sdp = cast(Any, None)
  candidate_to_sdp = cast(Any, None)

_LOOPBACK_ICE_INSTALLED = False
_ICE_CONSENT_TOLERANCE_APPLIED = False
_WEBRTC_DEVICE_ENUM_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}

# aioice defaults (CONSENT_INTERVAL=5, CONSENT_FAILURES=6) tear a session down
# after roughly 30 seconds of unanswered STUN, and each consent check runs with
# retransmissions=0 so one lost packet already counts as a failure. That budget
# is too tight for a backgrounded phone: a brief audio-session interruption, a
# Wi-Fi power-save gap, or a Wi-Fi<->cellular handover routinely costs more than
# six consecutive checks, and for every pairing mode except Cloud Pair there is
# no signaling channel left to re-establish the session afterwards. Widen the
# budget so a recoverable blip stays recoverable; a genuinely gone peer is still
# reaped, just on the datachannel idle timeout below instead.
_ICE_CONSENT_INTERVAL_DEFAULT_SECONDS = 5.0
_ICE_CONSENT_FAILURES_DEFAULT = 24  # ~120 s at the default interval
# Matching bound for the datachannel keepalive reaper. Leaving this at the old
# 60 s would make it, not ICE consent, the thing that kills a briefly suspended
# client - so the two budgets are kept in step.
_DATACHANNEL_IDLE_TIMEOUT_DEFAULT_SECONDS = 120.0

def _env_flag_enabled(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "on"}

def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(minimum, min(maximum, value))

def _datachannel_idle_timeout_seconds() -> float:
    return _env_float(
        "AUTOYOU_DATACHANNEL_IDLE_TIMEOUT_SECONDS",
        _DATACHANNEL_IDLE_TIMEOUT_DEFAULT_SECONDS,
        minimum=30.0,
        maximum=900.0,
    )

def _apply_ice_consent_tolerance() -> bool:
    """Widen aioice's ICE consent-freshness budget for backgrounded clients.

    ``aioice.ice.query_consent`` reads ``CONSENT_INTERVAL`` and
    ``CONSENT_FAILURES`` from module globals on every loop iteration, so
    rebinding them here also affects peer connections that are already running.
    """
    global _ICE_CONSENT_TOLERANCE_APPLIED
    if _ICE_CONSENT_TOLERANCE_APPLIED:
        return True
    try:
        import aioice.ice as aioice_ice
    except Exception as exc:
        LOGGER.debug("ICE consent tolerance unavailable: %s", exc)
        return False

    interval = _env_float(
        "AUTOYOU_ICE_CONSENT_INTERVAL_SECONDS",
        _ICE_CONSENT_INTERVAL_DEFAULT_SECONDS,
        minimum=1.0,
        maximum=30.0,
    )
    failures = int(
        _env_float(
            "AUTOYOU_ICE_CONSENT_FAILURES",
            float(_ICE_CONSENT_FAILURES_DEFAULT),
            minimum=6.0,
            maximum=240.0,
        )
    )

    try:
        aioice_ice.CONSENT_INTERVAL = interval
        aioice_ice.CONSENT_FAILURES = failures
    except Exception as exc:
        LOGGER.warning("Failed to widen ICE consent tolerance: %s", exc)
        return False

    _ICE_CONSENT_TOLERANCE_APPLIED = True
    LOGGER.info(
        "ICE consent freshness budget set to %.0fs (interval=%.1fs failures=%d)",
        interval * failures,
        interval,
        failures,
    )
    return True

def _webrtc_device_enum_cache_ttl_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("AUTOYOU_WEBRTC_DEVICE_ENUM_CACHE_TTL_SECONDS", "30") or "30"))
    except Exception:
        return 30.0

def _copy_webrtc_device_payload(payload: Dict[str, Any], *, cached: bool) -> Dict[str, Any]:
    copied = dict(payload)
    copied["devices"] = [dict(item) for item in list(payload.get("devices") or []) if isinstance(item, dict)]
    copied["cached"] = bool(cached)
    return copied

def _get_cached_webrtc_device_payload(cache_key: str) -> Optional[Dict[str, Any]]:
    cached = _WEBRTC_DEVICE_ENUM_CACHE.get(cache_key)
    if not cached:
        return None
    expires_at, payload = cached
    if expires_at <= time.monotonic():
        _WEBRTC_DEVICE_ENUM_CACHE.pop(cache_key, None)
        return None
    return _copy_webrtc_device_payload(payload, cached=True)

def _set_cached_webrtc_device_payload(cache_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    ttl = _webrtc_device_enum_cache_ttl_seconds()
    clean_payload = _copy_webrtc_device_payload(payload, cached=False)
    if ttl > 0:
        _WEBRTC_DEVICE_ENUM_CACHE[cache_key] = (time.monotonic() + ttl, clean_payload)
    return _copy_webrtc_device_payload(clean_payload, cached=False)


def _clear_webrtc_device_enum_cache(prefix: str) -> None:
    normalized = str(prefix or "").strip()
    for key in list(_WEBRTC_DEVICE_ENUM_CACHE):
        if key == normalized or key.startswith(normalized + ":"):
            _WEBRTC_DEVICE_ENUM_CACHE.pop(key, None)

def _configured_webrtc_camera_device_id(*, cfg: Optional[Dict[str, Any]] = None) -> int:
    try:
        return max(0, int(_get_video_call_config(cfg=cfg).get("camera_device_id", 0)))
    except Exception:
        return 0

def _webrtc_camera_probe_enabled() -> bool:
    return _env_flag_enabled("AUTOYOU_WEBRTC_CAMERA_PROBE_ENABLED") or _env_flag_enabled(
        "AUTOYOU_WEBRTC_DEVICE_PROBE_ENABLED"
    )

def _default_webrtc_camera_devices(configured_id: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": configured_id,
            "name": f"Configured camera - capture index {configured_id}",
            "configured": True,
            "available": None,
            "probe_status": "not_probed",
        }
    ]

def _open_cv_video_capture(cv2_module: Any, index: int) -> Any:
    if os.name == "nt":
        backend = getattr(cv2_module, "CAP_DSHOW", None)
        if backend is not None:
            try:
                return cv2_module.VideoCapture(index, backend)
            except TypeError:
                pass
    return cv2_module.VideoCapture(index)

def _enumerate_webrtc_audio_devices_payload(*, refresh: bool = False) -> Dict[str, Any]:
    cache_key = "audio"
    if refresh:
        _clear_webrtc_device_enum_cache(cache_key)
    cached = _get_cached_webrtc_device_payload(cache_key)
    if cached is not None:
        return cached

    devices: List[Dict[str, Any]] = []
    source = "portaudio"
    error = ""
    try:
        if not callable(enumerate_pyaudio_input_devices):
            raise RuntimeError("Shared PortAudio device discovery is unavailable")
        devices = list(enumerate_pyaudio_input_devices(refresh=refresh))
    except Exception as err:
        source = "unavailable"
        error = str(err)
        LOGGER.debug("Could not enumerate audio devices via shared PortAudio runtime: %s", err)

    system_name = "Windows" if os.name == "nt" else "Darwin" if sys.platform == "darwin" else "Linux"
    is_wsl = system_name == "Linux" and bool(os.getenv("WSL_DISTRO_NAME") or os.getenv("WSL_INTEROP"))
    loopback_markers = (
        ("loopback", "stereo mix", "what u hear")
        if system_name == "Windows"
        else ("blackhole", "soundflower", "loopback")
        if system_name == "Darwin"
        else ("monitor", "loopback")
    )
    microphone_devices = []
    loopback_available = False
    for device in devices:
        if any(marker in str(device.get("name") or "").lower() for marker in loopback_markers):
            loopback_available = True
        else:
            microphone_devices.append(device)
    devices = microphone_devices

    return _set_cached_webrtc_device_payload(
        cache_key,
        {
            "success": True,
            "devices": devices,
            "source": source,
            "platform": system_name.lower(),
            "environment": "wsl" if is_wsl else "native",
            "loopback_available": loopback_available,
            "loopback_reason": "" if loopback_available else "No compatible computer-sound loopback input was found.",
            "error": error,
            "cache_ttl_seconds": _webrtc_device_enum_cache_ttl_seconds(),
        },
    )

def _enumerate_webrtc_camera_devices_payload(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    configured_id = _configured_webrtc_camera_device_id(cfg=cfg)
    # Opening cameras can trigger privacy indicators, so normal status refreshes
    # stay non-invasive. An explicit device refresh is the user's opt-in probe.
    probe_enabled = bool(refresh or _webrtc_camera_probe_enabled())
    cache_key = f"camera:{configured_id}:{probe_enabled}"
    if refresh:
        _clear_webrtc_device_enum_cache("camera")
    cached = _get_cached_webrtc_device_payload(cache_key)
    if cached is not None:
        return cached

    devices = _default_webrtc_camera_devices(configured_id)
    source = "configured"
    if probe_enabled:
        discovered: Dict[int, Dict[str, Any]] = {}
        try:
            import cv2
            for i in range(5):
                cap = None
                try:
                    cap = _open_cv_video_capture(cv2, i)
                    if cap is not None and cap.isOpened():
                        discovered[i] = {
                            "id": i,
                            "name": f"Camera {i + 1} - capture index {i}",
                            "configured": i == configured_id,
                            "available": True,
                            "probe_status": "available",
                        }
                except Exception:
                    pass
                finally:
                    if cap is not None:
                        try:
                            cap.release()
                        except Exception:
                            pass
            if discovered:
                source = "opencv"
                devices = [discovered[key] for key in sorted(discovered)]
                if configured_id not in discovered:
                    devices.append(
                        {
                            "id": configured_id,
                            "name": f"Configured camera - capture index {configured_id}",
                            "configured": True,
                            "available": False,
                            "probe_status": "not_available",
                        }
                    )
            else:
                source = "opencv"
                devices = [
                    {
                        "id": configured_id,
                        "name": f"Configured camera - capture index {configured_id}",
                        "configured": True,
                        "available": False,
                        "probe_status": "not_available",
                        "reason": "No camera opened at the configured capture index.",
                    }
                ]
        except Exception as err:
            source = "unavailable"
            devices = [
                {
                    "id": configured_id,
                    "name": f"Configured camera - capture index {configured_id}",
                    "configured": True,
                    "available": False,
                    "probe_status": "runtime_unavailable",
                    "reason": "Camera support is not installed in this runtime.",
                }
            ]
            LOGGER.debug("Could not enumerate camera devices via OpenCV: %s", err)

    return _set_cached_webrtc_device_payload(
        cache_key,
        {
            "success": True,
            "devices": devices,
            "source": source,
            "probe_enabled": probe_enabled,
            "cache_ttl_seconds": _webrtc_device_enum_cache_ttl_seconds(),
        },
    )


def _enumerate_webrtc_monitors_payload(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    configured_id = _get_remote_desktop_monitor_id(cfg=cfg)
    cache_key = f"monitors:{configured_id}"
    if refresh:
        _clear_webrtc_device_enum_cache("monitors")
    cached = _get_cached_webrtc_device_payload(cache_key)
    if cached is not None:
        return cached

    devices: List[Dict[str, Any]] = []
    source = "unavailable"
    try:
        import mss

        with mss.mss() as capture:
            monitors = list(getattr(capture, "monitors", []) or [])
        for monitor_id, monitor in enumerate(monitors):
            width = max(0, int(monitor.get("width") or 0))
            height = max(0, int(monitor.get("height") or 0))
            left = int(monitor.get("left") or 0)
            top = int(monitor.get("top") or 0)
            name = (
                f"All displays - {width}x{height}"
                if monitor_id == 0
                else f"Display {monitor_id} - {width}x{height} at {left:+d},{top:+d}"
            )
            devices.append(
                {
                    "id": monitor_id,
                    "name": name,
                    "width": width,
                    "height": height,
                    "left": left,
                    "top": top,
                    "configured": monitor_id == configured_id,
                    "available": True,
                }
            )
        if devices:
            source = "mss"
    except Exception as err:
        LOGGER.debug("Could not enumerate desktop monitors via MSS: %s", err)

    if not devices:
        devices = [
            {
                "id": configured_id,
                "name": "All displays" if configured_id == 0 else f"Configured display {configured_id}",
                "configured": True,
                "available": False,
            }
        ]
    elif configured_id not in {int(item.get("id") or 0) for item in devices}:
        devices.append(
            {
                "id": configured_id,
                "name": f"Configured display {configured_id} - unavailable",
                "configured": True,
                "available": False,
            }
        )

    return _set_cached_webrtc_device_payload(
        cache_key,
        {
            "success": True,
            "devices": devices,
            "source": source,
            "cache_ttl_seconds": _webrtc_device_enum_cache_ttl_seconds(),
        },
    )

def _install_loopback_ice_candidates() -> bool:
    """Add loopback host candidates for same-machine Local Pair sessions."""
    global _LOOPBACK_ICE_INSTALLED
    if _LOOPBACK_ICE_INSTALLED:
        return True
    try:
        import aioice.ice as aioice_ice
    except Exception as exc:
        LOGGER.debug("Loopback ICE candidate support unavailable: %s", exc)
        return False

    original = getattr(aioice_ice, "get_host_addresses", None)
    if not callable(original):
        return False
    if getattr(original, "_autoyou_includes_loopback", False):
        _LOOPBACK_ICE_INSTALLED = True
        return True

    def get_host_addresses_with_loopback(use_ipv4: bool, use_ipv6: bool):
        addresses = list(original(use_ipv4, use_ipv6) or [])
        if use_ipv4 and "127.0.0.1" not in addresses:
            addresses.insert(0, "127.0.0.1")
        return addresses

    setattr(get_host_addresses_with_loopback, "_autoyou_includes_loopback", True)
    aioice_ice.get_host_addresses = get_host_addresses_with_loopback
    _LOOPBACK_ICE_INSTALLED = True
    LOGGER.info("Enabled loopback ICE candidates for same-machine Local Pair")
    return True

# Signal CLI REST API
try:
    from signal_service import SignalService
except Exception:
    SignalService = None

# WhatsApp Web.js Service
try:
    from whatsapp_service import WhatsAppService
except Exception:
    WhatsAppService = None

# Telegram User is an optional owner-only transport.  Keep the Bot API
# integration above independent so an unavailable user-session dependency does
# not affect existing Telegram bot deployments.
# Telethon probes for an OpenSSL libssl before selecting cryptg for AES.
# Windows hosts have no libssl on the DLL search path, so that probe's INFO
# line is pure noise once cryptg is installed; keep only real warnings.
logging.getLogger("telethon.crypto.libssl").setLevel(logging.WARNING)
try:
    from telegram_user_service import TelegramUserService
except Exception:
    TelegramUserService = None

import warnings
warnings.filterwarnings(
    "ignore",
    message=r".*InMemoryCredentialService.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=r".*BaseCredentialService.*",
    category=UserWarning,
)
from autoyou_agents.shared_tools.frontend_manifest import discover_frontend_manifests, load_frontend_manifest
from autoyou_agents.shared_tools.frontend_registry import refresh_frontend_registry, load_frontend_registry
from autoyou_agents.shared_tools.agent_install_registry import (
    can_install_agent_in_runtime,
    discover_agent_directories,
    is_builtin_agent_name,
    load_agent_install_registry,
    refresh_agent_install_registry,
    runtime_install_block_reason,
    set_agent_installed,
)
from autoyou_agents.shared_tools.builder_suite import (
    DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
    BUILDER_SUITE_AGENT_NAMES,
    builder_suite_status,
    install_builder_suite_agents,
)
from autoyou_agents.shared_tools.agent_directory import (
    load_agent_prompt_description,
    package_agent_name,
)
from autoyou_agents.shared_tools.agent_identity import (
    ROOT_AGENT_NAME,
    format_agent_display_name,
    is_root_agent_name,
    resolve_runtime_agent_name,
)
from autoyou_agents.shared_tools.agent_workbench import (
    build_agent_draft_summary,
    build_draft_runtime_test,
    build_live_agent_source_info,
    clone_live_agent_to_draft,
    delete_agent_draft,
    get_agent_drafts_root,
    list_agent_draft_names,
    load_agent_draft_metadata,
    publish_agent_draft,
    read_live_instruction_payload,
    save_draft_frontend_manifest,
    save_draft_instruction,
    scaffold_agent_draft,
    scaffold_frontend_draft,
)
from autoyou_agents.shared_tools.website_scaffold import (
    DEFAULT_BACKEND_STACK,
    DEFAULT_FRONTEND_STACK,
    backend_stack_choices_payload,
    normalize_backend_stack,
    frontend_stack_choices_payload,
    normalize_frontend_stack,
)
from pairing_router import pairing_router, AUTOPAIR_HELLO_ANSWER_PREFIX
from shared.bluetooth_pairing_service import (
    BluetoothPairingCommandHandler,
    BluetoothPairingStatus,
    create_bluetooth_pairing_server,
)

# DataChannel Manager for enhanced message handling
try:
    from shared.datachannel_manager import (
        # Core types
        DataChannelManager, MessageType, DataChannelMessage, MessageHeader,
        DataChannelRuntimeSettings,
        # Message factories - imported once here so all function bodies can use them
        # without deferred 'from x import y' inside loops/conditionals (which would
        # shadow the name as an unassigned local and raise UnboundLocalError).
        create_chat_message, create_voice_call_control_message,
        create_http_request_message, create_http_response_message,
        create_http_ws_upgrade_message, create_http_ws_data_message,
        create_http_ws_data_messages,
        create_http_ws_close_message, decode_http_ws_data_payload,
        # SSE streaming helpers
        create_http_sse_start_message, create_http_sse_event_message,
        create_http_sse_end_message,
        # Chunked streaming helpers
        create_http_stream_open_message, create_http_stream_data_message,
        create_http_stream_end_message, create_http_stream_abort_message,
        calculate_safe_http_stream_data_chunk_size,
    )
except Exception as e:
    LOGGER.warning(f"Failed to import shared.datachannel_manager: {e}")
    DataChannelManager = None
    DataChannelRuntimeSettings = None
    MessageType = None
    DataChannelMessage = None
    create_http_ws_data_messages = None

# Audio Manager for Voice Calls
try:
    from shared.audio_manager import (
        AudioManager,
        AudioTrackSink,
        BackgroundAudioHeartbeatTrack,
        MixedAudioStreamTrack,
        TTSAudioStreamTrack,
        list_system_tts_voices,
    )
except Exception as e:
    LOGGER.warning(f"Failed to import shared.audio_manager: {e}")
    AudioManager = None
    AudioTrackSink = None
    BackgroundAudioHeartbeatTrack = None
    MixedAudioStreamTrack = None
    TTSAudioStreamTrack = None
    list_system_tts_voices = None

try:
    from shared.webrtc_audio_recorder import (
        DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
        StreamingWavBatchRecorder,
        normalize_audio_recording_batch_seconds,
    )
except Exception as e:
    LOGGER.warning(f"Failed to import shared.webrtc_audio_recorder: {e}")
    DEFAULT_AUDIO_RECORDING_BATCH_SECONDS = (60 * 60) - 1
    StreamingWavBatchRecorder = None  # type: ignore[assignment]

    def normalize_audio_recording_batch_seconds(raw_value: Any, default: int = DEFAULT_AUDIO_RECORDING_BATCH_SECONDS) -> int:  # type: ignore[no-redef]
        del raw_value
        return int(default)

try:
    from shared.video_call_manager import (
        IncomingVideoTrackSink,
        DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
        INBOUND_VIDEO_RECORDING_FORMAT,
        REALTIME_VIDEO_INPUTS,
        RemoteDesktopVideoStreamTrack,
        RealtimeVideoInputStreamTrack,
        VIDEO_FRAME_REGISTRY,
        VIDEO_FILE_PLAYBACKS,
        VideoFileStreamTrack,
        available_outbound_video_source_kinds,
        configure_video_file_playback,
        create_outbound_video_track,
        enumerate_pyaudio_input_devices,
        inbound_video_recording_format_for_mode,
        install_outbound_video_telemetry_logging,
        normalize_inbound_video_image_interval_seconds,
        normalize_inbound_video_recording_mode,
        outbound_video_telemetry_status,
        pause_video_file_playback,
        play_video_file_playback,
        publish_realtime_video_jpeg,
        video_file_playback_status,
    )
    install_outbound_video_telemetry_logging()
except Exception as e:
    LOGGER.warning(f"Failed to import shared.video_call_manager: {e}")
    IncomingVideoTrackSink = None
    DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS = 5.0
    INBOUND_VIDEO_RECORDING_FORMAT = "jpeg_frames"
    REALTIME_VIDEO_INPUTS = None
    RemoteDesktopVideoStreamTrack = None
    RealtimeVideoInputStreamTrack = None
    VIDEO_FRAME_REGISTRY = None
    VIDEO_FILE_PLAYBACKS = None
    VideoFileStreamTrack = None
    available_outbound_video_source_kinds = None
    configure_video_file_playback = None
    create_outbound_video_track = None
    enumerate_pyaudio_input_devices = None
    inbound_video_recording_format_for_mode = lambda mode: "jpeg_frames"
    install_outbound_video_telemetry_logging = lambda: False
    normalize_inbound_video_image_interval_seconds = lambda value, default=5.0: float(default)
    normalize_inbound_video_recording_mode = lambda value, default="video": default
    outbound_video_telemetry_status = lambda: {}
    pause_video_file_playback = None
    play_video_file_playback = None
    publish_realtime_video_jpeg = None
    video_file_playback_status = None

from shared.speech_config import (
    MASKED_SECRET_PLACEHOLDER,
    OPENAI_TTS_MODELS,
    OPENAI_TTS_VOICES,
    STT_COMPUTE_TYPE_SUGGESTIONS,
    STT_DEVICE_SUGGESTIONS,
    STT_MODEL_SUGGESTIONS,
    deepcopy_speech_config,
    normalize_speech_config,
)
from shared.custom_voice_tts import (
    CUSTOM_VOICE_PROVIDER,
    custom_voice_model_ready,
    custom_voice_status,
    list_custom_voice_statuses,
)
from shared.request_logging import install_route_aware_request_logging
from shared.chat_session_identity import (
    alias_webrtc_chat_session,
    bind_transport_chat_owner,
    resolve_webrtc_chat_identity,
)
from shared.client_conversation_contract import (
    build_client_conversation_session_id as _shared_build_client_conversation_session_id,
    build_client_session_identity_payload as _shared_build_client_session_identity_payload,
    build_conversation_metadata as _shared_build_conversation_metadata,
    conversation_thread_id_for_metadata as _shared_conversation_thread_id_for_metadata,
    get_client_destination_session_id as _shared_get_client_destination_session_id,
)
from shared.http_proxy_codec import encode_http_proxy_response
try:
    from shared.remote_access_policy import (
        REMOTE_ACCESS_VIEWER,
        REMOTE_ACCESS_ROLES,
        REMOTE_BROWSER_HEADER,
        REMOTE_BROWSER_VIA_HOME_NETWORK,
        DEVICE_OWN,
        DEVICE_SHARED,
        normalize_device_ownership,
        normalize_remote_access_role,
        remote_access_denial_message,
        remote_http_request_allowed,
        server_cloud_action_requires_admin,
    )
except ImportError:
    _rap_loaded = False
    try:
        import importlib.machinery
        _pkg_dir = Path(__file__).resolve().parent / "shared"
        for _candidate in _pkg_dir.glob("remote_access_policy*.so"):
            if _candidate.is_file():
                _loader = importlib.machinery.ExtensionFileLoader("shared.remote_access_policy", str(_candidate))
                _mod = _loader.load_module("shared.remote_access_policy")
                sys.modules["shared.remote_access_policy"] = _mod
                _rap_loaded = hasattr(_mod, "server_cloud_action_requires_admin")
                break
    except Exception:
        pass
    if not _rap_loaded:
        import shared.remote_access_policy as _rap
        _SERVER_CLOUD_ADMIN_ACTIONS = frozenset({
            ("GET", "/api/cloud/link-start"),
            ("GET", "/api/cloud/callback"),
            ("POST", "/api/cloud/push-client"),
            ("POST", "/api/cloud/notify-client"),
            ("POST", "/api/cloud/unregister"),
            ("POST", "/api/cloud/activate"),
            ("POST", "/api/cloud/reregister"),
            ("POST", "/api/cloud/guest-access"),
        })
        def server_cloud_action_requires_admin(method: Any, path: Any = "") -> bool:
            effective_method = _rap.effective_remote_access_method(method, path)
            normalized_path = ("/" + str(path or "").split("?", 1)[0].lstrip("/")).rstrip("/") or "/"
            return (effective_method, normalized_path) in _SERVER_CLOUD_ADMIN_ACTIONS
        _rap.server_cloud_action_requires_admin = server_cloud_action_requires_admin
    from shared.remote_access_policy import (
        REMOTE_ACCESS_VIEWER,
        REMOTE_ACCESS_ROLES,
        REMOTE_BROWSER_HEADER,
        REMOTE_BROWSER_VIA_HOME_NETWORK,
        DEVICE_OWN,
        DEVICE_SHARED,
        normalize_device_ownership,
        normalize_remote_access_role,
        remote_access_denial_message,
        remote_http_request_allowed,
        server_cloud_action_requires_admin,
    )
from shared.remote_desktop_keyboard import (
    execute_remote_desktop_keyboard,
    normalize_remote_desktop_keyboard_payload,
    release_stuck_modifiers,
)
from shared.remote_desktop_input import (
    execute_remote_desktop_input,
    normalize_remote_desktop_control_payload,
    normalize_remote_desktop_input_payload,
    release_remote_desktop_inputs,
    remote_desktop_input_backend_available,
    remote_desktop_input_backend_probed,
)
from shared.remote_desktop_settings import (
    normalize_remote_desktop_bitrate_kbps,
    normalize_remote_desktop_monitor_id,
    normalize_remote_desktop_quality,
    remote_desktop_quality_settings,
)
from shared.adk_state import normalize_reply_target
from shared.session_execution import (
    STATUS_QUEUED,
    SessionQueueFullError,
    SessionTurnCancelledError,
    SessionTurnTimeoutError,
    build_owner_key,
    get_session_execution_manager,
)
from shared.admin_model_library import ModelLibraryService, build_hf_ollama_reference
from shared.admin_speech_library import SpeechModelLibraryService
from shared.admin_onboarding import (
    admin_doc_guides,
    build_connectivity_guide_html,
    build_ollama_install_guide_html,
    build_speech_guide_html,
    build_telegram_guide_html,
    dedupe_ice_servers,
    load_default_ice_servers_from_env,
    load_markdown_guide,
    merge_rtc_config,
    parse_ice_servers_input,
    simple_markdown_to_html,
)
from shared.webrtc_sdp import mark_sdp_ice_gathering_complete
from shared.admin_dashboard_shell import (
    build_admin_dashboard_shell_close_html,
    build_admin_dashboard_shell_open_html,
    build_admin_navigation_script_html,
    build_admin_search_card_html,
    build_admin_search_script_html,
)
from shared.admin_modern_ui import (
    admin_ui_assets_present,
    build_admin_ui_shell_html,
    resolve_admin_ui_asset,
)
from shared.admin_legacy_dashboard import (
    bind_dashboard_dependencies as bind_admin_legacy_dashboard_dependencies,
    build_legacy_dashboard_html,
)
from shared.ollama_context_policy import recommend_ollama_num_ctx

def _pick_default_wizard_model() -> str:
    """Return the recommended starter model based on available system RAM.

    >= 16 GB total RAM  ->  ministral-3:8b  (better quality)
    <  16 GB total RAM  ->  ministral-3:3b  (lighter footprint)
    """
    try:
        import psutil as _psutil
        total_gb = _psutil.virtual_memory().total / (1024 ** 3)
        return "ministral-3:8b" if total_gb > 16.0 else "ministral-3:3b"
    except Exception:
        return "ministral-3:3b"

def _configured_ollama_model_for_behavior() -> str:
    try:
        cfg = STATE.config or {}
        ollama_cfg = cfg.get("ollama", {}) if isinstance(cfg, dict) else {}
        model_name = str(
            ollama_cfg.get("model") or os.getenv("OLLAMA_MODEL") or DEFAULT_WIZARD_MODEL
        ).strip()
        return model_name or DEFAULT_WIZARD_MODEL
    except Exception:
        return str(os.getenv("OLLAMA_MODEL", DEFAULT_WIZARD_MODEL) or DEFAULT_WIZARD_MODEL).strip()

def setup_default_environment_variables():
    """Set default runtime environment variables when they are not already configured."""
    default_env_vars = {
        'OLLAMA_API_BASE': 'http://localhost:11434',
        'OLLAMA_MODEL': _pick_default_wizard_model(),
        'AUTOYOU_OLLAMA_MODEL_EXPLICIT': '0',
        'USE_GOOGLE_API': '0',
        'GOOGLE_API_KEY': 'NULL',
        'GOOGLE_MODEL': 'gemini-2.5-flash',
        'GOOGLE_GENAI_USE_VERTEXAI': '0',
        # Multi-provider support
        'AI_PROVIDER': 'ollama',
        'OPENCLAW_PORT': '18789',
        'OPENCLAW_TOKEN': '',
        'OPENCLAW_MODEL': 'openclaw/default',
        'OPENCLAW_AGENT_PORT': '18789',
        'OPENCLAW_AGENT_TOKEN': '',
        'OPENCLAW_AGENT_MODEL': 'openclaw/default',
        'HERMES_PORT': '8642',
        'HERMES_TOKEN': '',
        'HERMES_MODEL': 'hermes-agent',
        'LITELLM_MODEL': '',
        'LITELLM_API_KEY': '',
        'LITELLM_API_BASE': '',
        'ODYSSEUS_API_BASE': 'http://127.0.0.1:7000',
        'ODYSSEUS_MODEL': '',
    }

    for key, default_value in default_env_vars.items():
        if not os.getenv(key):
            os.environ[key] = default_value
            print(f"Set default environment variable: {key}={default_value}")
        else:
            print(f"Using existing environment variable: {key}={os.getenv(key)}")

# Initialize environment variables
setup_default_environment_variables()
# Load Ollama Service
from ollama_service import OllamaService
ollama_service = OllamaService()
model_library_service = ModelLibraryService()
speech_model_library_service = SpeechModelLibraryService()

# Load For AutoYou Page Service
try:
    from autoyou_page_service import (
        start_autoyou_page_service,
        stop_autoyou_page_service,
        get_autoyou_page_service,
        is_autoyou_page_service_running
    )
    AUTOYOU_PAGE_SERVICE_AVAILABLE = True
except ImportError as e:
    LOGGER.warning(f"For AutoYou Page Service not available: {e}")
    start_autoyou_page_service = None
    stop_autoyou_page_service = None
    get_autoyou_page_service = None
    is_autoyou_page_service_running = None
    AUTOYOU_PAGE_SERVICE_AVAILABLE = False

# Set up paths
APP_ROOT = get_application_root(__file__)
RESOURCES_ROOT = get_resources_root(__file__)
DIRNAME = str(APP_ROOT)
AGENT_NAME = os.path.basename(DIRNAME)
AGENT_DIR = str(get_adk_agents_base_dir(__file__))
GUIDES_DIR = Path(APP_ROOT) / "guides"
INSTALLATION_GUIDE_PATH = GUIDES_DIR / "installation-steps.md"
WINDOWS_BUILD_GUIDE_PATH = GUIDES_DIR / "WINDOWS_BUILD_GUIDE.md"
MACOS_BUILD_GUIDE_PATH = GUIDES_DIR / "MACOS_BUILD_GUIDE.md"
SIGNAL_PAIRING_GUIDE_PATH = GUIDES_DIR / "SIGNAL_QR_PAIRING_GUIDE.md"
WHATSAPP_PAIRING_GUIDE_PATH = GUIDES_DIR / "WHATSAPP_QR_PAIRING_GUIDE.md"
COMMUNITY_RELAY_GUIDE_PATH = GUIDES_DIR / "COMMUNITY_RELAY_SUBMISSION_GUIDE.md"
HOME_PRIVATE_NETWORK_GUIDE_PATH = GUIDES_DIR / "HOME_PRIVATE_NETWORK_GUIDE.md"
MOBILE_PAIRING_GUIDE_PATH = GUIDES_DIR / "MOBILE_PAIRING_GUIDE.md"
CHROME_PAIRING_GUIDE_PATH = GUIDES_DIR / "CHROME_PAIRING_GUIDE.md"
TELEGRAM_USER_GUIDE_PATH = Path(APP_ROOT) / "docs" / "user" / "telegram-user.md"
DEFAULT_WIZARD_MODEL: str = _pick_default_wizard_model()

def _build_sqlite_service_uri(path: Path) -> str:
  """Return an ADK-compatible sqlite+aiosqlite:// URI for an absolute filesystem path.

  ADK's get_fast_api_app expects the same async-driver URI that SQLAlchemy uses
  (sqlite+aiosqlite:///...) - a plain sqlite:/// URI is not recognised and causes
  the session service to fall back silently to local file storage rooted at the
  agents_dir instead of the intended user-data directory.
  """
  normalized_path = normalize_local_filesystem_path(path)
  return f"sqlite+aiosqlite:///{normalized_path.as_posix()}"

def _build_file_service_uri(path: Path) -> str:
  """Return the local ADK artifact URI, encrypted when Maximus is active."""
  normalized = normalize_local_filesystem_path(path).as_uri()
  if secure_storage_enabled():
    return "autoyou-secure-file://" + normalized.split("://", 1)[1]
  return normalized

def _sqlite_service_uri_to_path(raw_uri: str) -> Optional[Path]:
  uri = str(raw_uri or "").strip()
  if not uri:
    return None
  if uri.startswith("sqlite+aiosqlite:///"):
    raw_path = uri[len("sqlite+aiosqlite:///"):]
  elif uri.startswith("sqlite:///"):
    raw_path = uri[len("sqlite:///"):]
  elif "://" not in uri:
    raw_path = uri
  else:
    return None
  raw_path = unquote(raw_path)
  if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
    raw_path = raw_path[1:]
  if not raw_path:
    return None
  return normalize_local_filesystem_path(raw_path)

def _get_compiled_ai_agent_storage_paths() -> Tuple[Path, Path]:
  """Return writable AppData-backed paths for packaged AI agent storage."""
  user_data_dir = get_user_data_dir("AutoYou")
  session_db_path = user_data_dir / "autoyou_agents" / ".adk" / "session.db"
  artifact_root = user_data_dir / ".adk" / "artifacts"
  session_db_path.parent.mkdir(parents=True, exist_ok=True)
  artifact_root.mkdir(parents=True, exist_ok=True)
  return session_db_path, artifact_root

def _get_default_ai_agent_storage_paths() -> Tuple[Path, Path]:
    """Return the shared ADK session/artifact storage paths for the AI worker."""
    if is_compiled():
        return _get_compiled_ai_agent_storage_paths()

    config_dir = get_config_dir("AutoYou", anchor=__file__).resolve()
    session_db_path = (config_dir / "sessions.db").resolve()
    artifact_root = (config_dir / ".adk" / "artifacts").resolve()
    session_db_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_root.mkdir(parents=True, exist_ok=True)
    return session_db_path, artifact_root

def _resolve_ai_agent_storage_uris(env_source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Resolve the ADK session/artifact storage URIs shared by the main and AI processes."""
    source = env_source or os.environ
    session_uri = str(source.get(AI_AGENT_SESSION_SERVICE_URI_ENV, "") or "").strip()
    artifact_uri = str(source.get(AI_AGENT_ARTIFACT_SERVICE_URI_ENV, "") or "").strip()

    if not session_uri or not artifact_uri:
        session_db_path, artifact_root = _get_default_ai_agent_storage_paths()
        if not session_uri:
            session_uri = _build_sqlite_service_uri(session_db_path)
        if not artifact_uri:
            artifact_uri = _build_file_service_uri(artifact_root)

    return {
        "session_service_uri": session_uri,
        "artifact_service_uri": artifact_uri,
    }

def _resolve_ai_agent_memory_db_path(env_source: Optional[Dict[str, str]] = None) -> Path:
    source = env_source or os.environ
    explicit_path = str(source.get(AUTOYOU_SESSION_DB_PATH_ENV, "") or "").strip()
    if explicit_path:
        return normalize_local_filesystem_path(explicit_path)

    storage_uris = _resolve_ai_agent_storage_uris(source)
    session_db_path = _sqlite_service_uri_to_path(storage_uris.get("session_service_uri", ""))
    if session_db_path is not None:
        return session_db_path

    fallback_path, _artifact_root = _get_default_ai_agent_storage_paths()
    return normalize_local_filesystem_path(fallback_path)

def _get_ai_agent_fastapi_storage_kwargs() -> Dict[str, str]:
    """Return explicit ADK storage URIs for packaged AI agent runs."""
    return _resolve_ai_agent_storage_uris()

@contextlib.contextmanager
def _adk_web_assets_redirect(agent_logger: logging.Logger):
    """Keep ADK's startup runtime-config write out of the packaged app bundle.

    ``get_fast_api_app(web=True)`` derives the web-assets directory from ADK's
    own package location and forwards it to ``ApiServer.get_fast_api_app``, so
    that call is the only seam where the path can be swapped. Redirecting it
    makes ADK serve - and rewrite - a per-user mirror instead of the sealed
    copy inside AutoYou.app, which would otherwise break the bundle's own
    Developer ID signature on first launch.
    """
    try:
        from google.adk.cli.api_server import ApiServer
    except Exception as exc:
        # Never fail open quietly: without this patch the packaged app rewrites a
        # sealed resource and breaks its own signature on first launch.
        agent_logger.warning(
            "ADK web-assets redirect unavailable (%s); runtime-config may be "
            "written into the packaged bundle",
            exc,
        )
        yield
        return

    original_get_fast_api_app = ApiServer.get_fast_api_app

    @functools.wraps(original_get_fast_api_app)
    def _with_writable_web_assets(self, *args, **kwargs):
        source_dir = kwargs.get("web_assets_dir")
        if source_dir:
            try:
                resolved = resolve_adk_web_assets_dir(source_dir)
                relocated = Path(resolved) != Path(source_dir).resolve()
            except Exception as exc:
                agent_logger.warning(
                    "Could not relocate ADK web assets out of %s: %s", source_dir, exc
                )
            else:
                if relocated:
                    agent_logger.info(
                        "Serving ADK web assets from writable mirror %s (packaged copy %s stays read-only)",
                        resolved,
                        source_dir,
                    )
                kwargs["web_assets_dir"] = str(resolved)
        return original_get_fast_api_app(self, *args, **kwargs)

    ApiServer.get_fast_api_app = _with_writable_web_assets
    try:
        yield
    finally:
        ApiServer.get_fast_api_app = original_get_fast_api_app


def _create_ai_agent_fastapi_app(
  get_fast_api_app_func: Callable[..., Any],
  *,
  agent_dir: str,
  agent_logger: logging.Logger,
) -> Any:
  """Build the ADK FastAPI app with explicit persistent session/artifact storage."""
  from google.adk.cli.utils.agent_loader import AgentLoader

  def _looks_like_adk_app_entry(entry_path: Path) -> bool:
    if not entry_path.is_dir():
      return False
    if any((entry_path / marker).is_file() for marker in ("agent.py", "root_agent.yaml")):
      return True
    for pattern in ("agent.*.so", "agent.*.pyd"):
      if any(entry_path.glob(pattern)):
        return True
    return False

  class _AutoYouAgentLoader(AgentLoader):
    def list_agents(self) -> list[str]:
      base_path = (Path.cwd() / self.agents_dir).resolve()
      agent_names = [
        entry.name
        for entry in sorted(base_path.iterdir(), key=lambda candidate: candidate.name)
        if not entry.name.startswith(".")
        and entry.name != "__pycache__"
        and _looks_like_adk_app_entry(entry)
      ]
      return agent_names

  fastapi_storage_kwargs = _get_ai_agent_fastapi_storage_kwargs()
  if secure_storage_enabled():
    from shared.secure_artifact_service import register_secure_artifact_service

    register_secure_artifact_service()
  if fastapi_storage_kwargs:
    agent_logger.info(
      "Using packaged AI agent storage session=%s artifacts=%s",
      fastapi_storage_kwargs.get("session_service_uri"),
      fastapi_storage_kwargs.get("artifact_service_uri"),
    )

  with _adk_web_assets_redirect(agent_logger):
    return get_fast_api_app_func(
      agents_dir=agent_dir,
      agent_loader=_AutoYouAgentLoader(agent_dir),
      allow_origins=["*"],
      web=True,
      **fastapi_storage_kwargs,
    )

def _prepare_ai_agent_process_env(env_vars: Dict[str, str]) -> Dict[str, str]:
    prepared_env = dict(env_vars)
    prepared_env[AI_AGENT_INTERNAL_API_TOKEN_ENV] = _load_or_create_ai_agent_internal_api_token()

    try:
        ai_agent_cfg = (STATE.config or {}).get("ai_agent", {}) if isinstance(STATE.config, dict) else {}
    except Exception:
        ai_agent_cfg = {}
    prepared_env["AUTOYOU_INTERNET_SEARCH_ENABLED"] = (
        "1" if bool(ai_agent_cfg.get("internet_search_enabled", True)) else "0"
    )

    # The worker process never decrypts STATE.config itself, so the resolved
    # decision (not just the raw config/env inputs that fed it) has to be
    # threaded through explicitly -- otherwise the child could disagree with
    # the parent about whether LAN access is on.
    lan_access_enabled = _ai_agent_lan_access_enabled()
    prepared_env["AUTOYOU_AI_AGENT_LAN_ACCESS"] = "1" if lan_access_enabled else "0"
    prepared_env["AUTOYOU_AI_AGENT_LAN_HTTPS_PORT"] = str(_ai_agent_lan_https_port())
    if lan_access_enabled:
        totp_secret = _get_pairing_totp_secret()
        if totp_secret:
            prepared_env[AI_AGENT_TOTP_SECRET_ENV] = totp_secret

    storage_uris = _resolve_ai_agent_storage_uris(env_vars)
    prepared_env[AI_AGENT_SESSION_SERVICE_URI_ENV] = storage_uris["session_service_uri"]
    prepared_env[AI_AGENT_ARTIFACT_SERVICE_URI_ENV] = storage_uris["artifact_service_uri"]
    prepared_env[AUTOYOU_SESSION_DB_PATH_ENV] = str(_resolve_ai_agent_memory_db_path(prepared_env))
    memory_backend = str(
        ai_agent_cfg.get("memory_backend")
        or prepared_env.get("AUTOYOU_MEMORY_BACKEND")
        or "legacy"
    ).strip().lower()
    if memory_backend not in {"legacy", "cognee"}:
        memory_backend = "legacy"
    prepared_env["AUTOYOU_MEMORY_BACKEND"] = memory_backend
    prepared_env["AUTOYOU_COGNEE_MEMORY_ENABLED"] = "1" if memory_backend == "cognee" else "0"

    if not is_compiled():
        return prepared_env

    prepared_env["LITELLM_MODE"] = "PRODUCTION"
    prepared_env["ADK_DISABLE_LOAD_DOTENV"] = "1"
    prepared_env["PYTHON_DOTENV_DISABLED"] = "1"
    prepared_env.setdefault(
        "AUTOYOU_WORKSPACE_ROOT",
        str(get_dynamic_agents_root("AutoYou", anchor=__file__).resolve()),
    )
    return prepared_env

def get_configured_server_name() -> str:
    """Return the configured server display name with a stable fallback."""
    try:
        if STATE.config:
            name = str(STATE.config.get("server", {}).get("name") or "").strip()
            if name:
                return name
    except Exception:
        pass
    return "AutoYou-Server"

def _get_admin_ui_title() -> str:
    """Return the browser title for the local admin UI."""
    return f"{get_configured_server_name()} Admin"

def _get_admin_logo_path() -> Path:
    """Resolve the admin logo path for source and frozen builds."""
    candidates: list[Path] = [
        RESOURCES_ROOT / "assets" / "logo.png",
        Path(__file__).resolve().parent / "assets" / "logo.png",
    ]

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_dir / "assets" / "logo.png",
                executable_dir.parent / "Resources" / "assets" / "logo.png",
            ]
        )
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass).resolve() / "assets" / "logo.png")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return candidates[0]

def _get_admin_icon_path() -> Path | None:
    """Resolve logo.ico for favicon/icon endpoints."""
    candidates: list[Path] = [
        RESOURCES_ROOT / "assets" / "logo.ico",
        Path(__file__).resolve().parent / "assets" / "logo.ico",
    ]

    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        candidates.extend(
            [
                executable_dir / "assets" / "logo.ico",
                executable_dir.parent / "Resources" / "assets" / "logo.ico",
            ]
        )
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(Path(meipass).resolve() / "assets" / "logo.ico")

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None

_ADMIN_PROFILE_IMAGE_NAME = "profile-avatar"
_ADMIN_PROFILE_IMAGE_MEDIA_TYPES = {
    ".webp": "image/webp",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES = 64 * 1024
_ADMIN_PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024

def _admin_profile_image_root() -> Path:
    return get_mutable_data_dir("AutoYou", anchor=__file__).resolve() / "admin_profile"

def _get_admin_profile_image_dir(profile_user_id: Optional[str] = None) -> Path:
    user_id = str(profile_user_id or _get_stable_server_id()).strip() or "AutoYou-Server"
    identity = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
    image_dir = _admin_profile_image_root() / identity
    image_dir.mkdir(parents=True, exist_ok=True)
    return image_dir

def _iter_admin_profile_image_candidates(profile_user_id: Optional[str] = None) -> tuple[Path, ...]:
    image_dir = _get_admin_profile_image_dir(profile_user_id)
    return tuple(
        image_dir / f"{_ADMIN_PROFILE_IMAGE_NAME}{suffix}"
        for suffix in (".webp", ".png", ".jpg", ".jpeg", ".gif")
    )

def _iter_legacy_admin_profile_image_candidates() -> tuple[Path, ...]:
    root = _admin_profile_image_root()
    return tuple(root / f"{_ADMIN_PROFILE_IMAGE_NAME}{suffix}" for suffix in (".webp", ".png", ".jpg", ".jpeg", ".gif"))

def _replace_admin_profile_image(target_path: Path, image_bytes: bytes, profile_user_id: Optional[str] = None) -> None:
    temporary_path = target_path.with_name(f".{target_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        write_secure_file(temporary_path, image_bytes)
        os.replace(temporary_path, target_path)
    finally:
        with suppress(FileNotFoundError):
            temporary_path.unlink()
    for candidate in _iter_admin_profile_image_candidates(profile_user_id):
        if candidate != target_path:
            with suppress(FileNotFoundError):
                candidate.unlink()

def _get_admin_profile_image_path() -> Optional[Path]:
    for candidate in _iter_admin_profile_image_candidates():
        if candidate.is_file():
            return candidate
    # Import the pre-scoped avatar once so existing installations keep their image.
    for legacy in _iter_legacy_admin_profile_image_candidates():
        if not legacy.is_file():
            continue
        try:
            image_bytes = legacy.read_bytes()
            if not image_bytes or len(image_bytes) > _ADMIN_PROFILE_IMAGE_MAX_BYTES:
                continue
            suffix, _media_type = _detect_admin_profile_image_type(image_bytes)
            target = _get_admin_profile_image_dir() / f"{_ADMIN_PROFILE_IMAGE_NAME}{suffix}"
            _replace_admin_profile_image(target, image_bytes)
            with suppress(FileNotFoundError):
                legacy.unlink()
            return target
        except (OSError, ValueError):
            continue
    return None

def _get_admin_profile_image_media_type(image_path: Path) -> str:
    return _ADMIN_PROFILE_IMAGE_MEDIA_TYPES.get(image_path.suffix.lower(), "application/octet-stream")

def _delete_admin_profile_image_files() -> None:
    for candidate in (*_iter_admin_profile_image_candidates(), *_iter_legacy_admin_profile_image_candidates()):
        with suppress(FileNotFoundError):
            candidate.unlink()

def _detect_admin_profile_image_type(payload: bytes) -> tuple[str, str]:
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise ValueError("Profile image must be a PNG, JPEG, WebP, or GIF file.")

def _compress_profile_image_payload(payload: bytes, max_bytes: int = _ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES) -> bytes:
    if not payload or len(payload) <= max_bytes:
        return payload
    try:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(payload)) as img:
            width, height = img.size
            if width != height:
                min_dim = min(width, height)
                left = (width - min_dim) // 2
                top = (height - min_dim) // 2
                img = img.crop((left, top, left + min_dim, top + min_dim))
            if img.width > 256 or img.height > 256:
                img = img.resize((256, 256), Image.Resampling.LANCZOS)
            for fmt, q in [("WEBP", 82), ("WEBP", 70), ("JPEG", 75), ("JPEG", 60)]:
                buf = io.BytesIO()
                if fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
                    rgb_img = Image.new("RGB", img.size, (16, 25, 43))
                    rgb_img.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                    rgb_img.save(buf, format=fmt, quality=q, optimize=True)
                else:
                    img.save(buf, format=fmt, quality=q, optimize=True)
                compressed = buf.getvalue()
                if len(compressed) <= max_bytes:
                    return compressed
    except Exception:
        pass
    return payload


def _save_admin_profile_image(payload: bytes) -> Path:
    image_bytes = bytes(payload or b"")
    if not image_bytes:
        raise ValueError("Profile image file is empty.")
    is_gif = image_bytes.startswith((b"GIF87a", b"GIF89a"))
    if is_gif and len(image_bytes) > _ADMIN_PROFILE_IMAGE_MAX_BYTES:
        raise ValueError("Animated profile images must be smaller than 2 MB.")
    if not is_gif and len(image_bytes) > _ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES:
        image_bytes = _compress_profile_image_payload(image_bytes, _ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES)
    if not is_gif and len(image_bytes) > _ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES:
        raise ValueError("Profile image is too large. Keep the optimized still image under 64 KB.")

    suffix, _media_type = _detect_admin_profile_image_type(image_bytes)
    image_dir = _get_admin_profile_image_dir()
    target_path = image_dir / f"{_ADMIN_PROFILE_IMAGE_NAME}{suffix}"
    _replace_admin_profile_image(target_path, image_bytes)
    for legacy in _iter_legacy_admin_profile_image_candidates():
        with suppress(FileNotFoundError):
            legacy.unlink()

    return target_path

def _get_server_profile_call_payload() -> Dict[str, Any]:
    image_path = _get_admin_profile_image_path()
    image_bytes = image_path.read_bytes() if image_path is not None else b""
    if len(image_bytes) > _ADMIN_PROFILE_IMAGE_SIGNAL_MAX_BYTES:
        image_bytes = b""
    return {
        "event": "server_profile",
        "profile_user_id": _get_stable_server_id(),
        "server_name": get_configured_server_name(),
        "avatar_mime_type": _get_admin_profile_image_media_type(image_path) if image_path and image_bytes else "",
        "avatar_base64": base64.b64encode(image_bytes).decode("ascii") if image_bytes else "",
    }

def _get_admin_profile_image_url() -> Optional[str]:
    image_path = _get_admin_profile_image_path()
    if image_path is None:
        return None
    try:
        version = int(image_path.stat().st_mtime_ns)
    except OSError:
        version = int(time.time() * 1_000_000_000)
    return f"/assets/admin/profile-image?v={version}"

def _build_admin_brand_markup(server_name: str) -> str:
    """Render the shared logo + server-name brand lockup."""
    escaped_name = html.escape(str(server_name or "").strip() or get_configured_server_name())
    return f"""
    <div class='brand-lockup'>
      <img src='/assets/logo.png' alt='AutoYou logo' class='brand-logo'>
      <div class='brand-meta'>
        <span class='brand-kicker'>Admin</span>
        <span class='brand-name'>{escaped_name}</span>
      </div>
    </div>
    """

def _build_chat_response_agent_metadata(chat_response: Any, metadata: Any) -> Dict[str, str]:
    """Resolve canonical and display agent names for client-facing chat metadata."""
    response_metadata = metadata if isinstance(metadata, dict) else {}
    session_execution_metadata = response_metadata.get("session_execution")
    if not isinstance(session_execution_metadata, dict):
        session_execution_metadata = {}
        response_metadata["session_execution"] = session_execution_metadata

    canonical_agent_name = ""
    for candidate in (
        response_metadata.get("agent_name"),
        response_metadata.get("response_author"),
        session_execution_metadata.get("last_agent_name"),
        getattr(chat_response, "agent_name", None),
    ):
        canonical_agent_name = resolve_runtime_agent_name(candidate)
        if canonical_agent_name:
            break

    if not canonical_agent_name:
        canonical_agent_name = ROOT_AGENT_NAME

    explicit_display_name = str(response_metadata.get("agent_display_name") or "").strip()
    if is_root_agent_name(canonical_agent_name):
        display_name = get_configured_server_name()
    else:
        display_name = explicit_display_name or format_agent_display_name(canonical_agent_name)

    return {
        "agent_name": canonical_agent_name,
        "agent_display_name": display_name,
        "server_name": get_configured_server_name(),
    }

def _normalize_voice_playback_status(raw_status: Any) -> Dict[str, str]:
    payload = raw_status if isinstance(raw_status, dict) else {}
    return {
        "state": str(payload.get("state") or "").strip().lower(),
        "source": str(payload.get("source") or "").strip().lower(),
        "playback_id": str(payload.get("playback_id") or "").strip(),
    }

def _get_voice_audio_playback_status(session_id: str) -> Dict[str, str]:
    audio_manager = STATE.audio_managers.get(str(session_id or "").strip())
    if audio_manager is None or not hasattr(audio_manager, "get_playback_status"):
        return {}
    try:
        return _normalize_voice_playback_status(audio_manager.get_playback_status())
    except Exception as exc:
        LOGGER.debug("Could not read voice playback status for %s: %s", session_id, exc)
        return {}

def _voice_turn_started_audio_playback(before_status: Any, after_status: Any) -> bool:
    before = _normalize_voice_playback_status(before_status)
    after = _normalize_voice_playback_status(after_status)
    if after.get("source") != "audio_file":
        return False

    before_playback_id = str(before.get("playback_id") or "").strip()
    after_playback_id = str(after.get("playback_id") or "").strip()
    if after_playback_id and after_playback_id != before_playback_id:
        return True

    return before.get("state") != "playing" and after.get("state") == "playing"

def _conversation_thread_id_for_metadata(thread_id: Optional[int]) -> int:
    return _shared_conversation_thread_id_for_metadata(thread_id)

def _build_conversation_metadata(identity: Any, *, reset: bool = False) -> Dict[str, Any]:
    return _shared_build_conversation_metadata(
        identity,
        server_id=_get_stable_server_id(),
        server_identity_key=_get_server_identity_key(),
        reset=reset,
    )

def _get_stable_server_id(cfg: Optional[Dict[str, Any]] = None) -> str:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    cloud_cfg = effective_cfg.get("cloud") if isinstance(effective_cfg.get("cloud"), dict) else {}
    cloud_server_id = str(cloud_cfg.get("server_id") or "").strip()
    if cloud_server_id:
        return cloud_server_id

    server_cfg = effective_cfg.get("server") if isinstance(effective_cfg.get("server"), dict) else {}
    installation_id = str(server_cfg.get("installation_id") or "").strip()
    if installation_id:
        return installation_id

    server_name = str(server_cfg.get("name") or "").strip()
    if server_name:
        return server_name

    return "AutoYou-Server"

def _get_server_identity_key(cfg: Optional[Dict[str, Any]] = None) -> str:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    cloud_cfg = effective_cfg.get("cloud") if isinstance(effective_cfg.get("cloud"), dict) else {}
    cloud_server_id = str(cloud_cfg.get("server_id") or "").strip()
    if cloud_server_id:
        return f"cloud:{cloud_server_id}"

    server_cfg = effective_cfg.get("server") if isinstance(effective_cfg.get("server"), dict) else {}
    installation_id = str(server_cfg.get("installation_id") or "").strip()
    if installation_id:
        return f"local:{installation_id}"

    server_name = str(server_cfg.get("name") or get_configured_server_name()).strip()
    return f"name:{server_name}" if server_name else "name:AutoYou-Server"

def _get_client_destination_session_id(identity: Any) -> str:
    return _shared_get_client_destination_session_id(identity)

def _build_client_conversation_session_id(identity: Any) -> str:
    return _shared_build_client_conversation_session_id(
        identity,
        server_identity_key=_get_server_identity_key(),
    )

def _build_client_session_identity_payload(
    identity: Any,
    *,
    pairing_mode: Optional[str] = None,
) -> Dict[str, Any]:
    return _shared_build_client_session_identity_payload(
        identity,
        server_id=_get_stable_server_id(),
        server_identity_key=_get_server_identity_key(),
        pairing_mode=pairing_mode,
    )

def _resolve_pairing_mode_for_transport(transport: Any, explicit: Any = None) -> str:
    normalized_explicit = str(explicit or "").strip()
    if normalized_explicit:
        return normalized_explicit
    normalized_transport = str(transport or "").strip().lower()
    if normalized_transport == "cloud":
        return "cloud_pair"
    if normalized_transport in {"bluetooth", "bluetooth-local"}:
        return "bluetooth_pair"
    if normalized_transport in {"local", "windows-local", "admin-web", "webrtc", "webrtc-datachannel", "datachannel"}:
        return "local_pair"
    return "auto_pair" if normalized_transport else ""

def _get_conversation_session_manager():
    service_manager = getattr(STATE, "service_manager", None)
    if service_manager is None or not hasattr(service_manager, "get_session_manager"):
        return None
    try:
        return service_manager.get_session_manager()
    except Exception as exc:
        LOGGER.debug("Failed to resolve session manager for conversation threads: %s", exc)
        return None

def _resolve_conversation_identity(identity: Any, *, start_new_thread: bool = False):
    session_manager = _get_conversation_session_manager()
    if session_manager is None:
        return identity

    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    if not owner_key:
        return identity

    try:
        if start_new_thread and hasattr(session_manager, "advance_conversation_thread"):
            thread_id, canonical_session_id = session_manager.advance_conversation_thread(owner_key)
        elif hasattr(session_manager, "get_current_conversation_thread") and hasattr(
            session_manager,
            "get_current_conversation_session_id",
        ):
            thread_id = session_manager.get_current_conversation_thread(owner_key)
            canonical_session_id = session_manager.get_current_conversation_session_id(owner_key)
        else:
            return identity
    except Exception as exc:
        LOGGER.warning(
            "Failed to resolve conversation thread for owner %s (new=%s): %s",
            owner_key,
            start_new_thread,
            exc,
        )
        return identity

    try:
        return replace(
            identity,
            canonical_session_id=str(canonical_session_id),
            thread_id=(int(thread_id) if int(thread_id) > 1 else None),
        )
    except Exception:
        return identity


def _rename_server_conversation(identity: Any, metadata: Any) -> Dict[str, Any]:
    """Store a client-chosen name for one of that client's own conversations.

    One way only: the server keeps the name for Chat & History and never
    pushes names back. ``conversation_thread_id`` selects an earlier thread of
    the same owner; a caller can never reach another owner's conversation.
    """
    from shared.session_execution import build_canonical_session_id

    payload = metadata if isinstance(metadata, dict) else {}
    user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
    owner_key = str(getattr(identity, "owner_key", "") or "").strip()
    session_id = str(getattr(identity, "canonical_session_id", "") or "").strip()
    raw_thread = payload.get("conversation_thread_id")
    if raw_thread not in (None, ""):
        try:
            thread_id = int(raw_thread)
        except (TypeError, ValueError):
            return {"renamed": False, "reason": "invalid_thread"}
        if thread_id < 1 or thread_id > 1_000_000 or not owner_key:
            return {"renamed": False, "reason": "invalid_thread"}
        session_id = build_canonical_session_id(owner_key, thread_id)
    session_manager = _get_conversation_session_manager()
    set_title = getattr(session_manager, "set_conversation_title", None) if session_manager else None
    if not callable(set_title) or not user_id or not session_id:
        return {"renamed": False, "reason": "unavailable"}
    stored = set_title(
        user_id,
        session_id,
        payload.get("conversation_title"),
        source=str(payload.get("client") or "client")[:48],
    )
    if stored is None:
        return {"renamed": False, "reason": "not_saved"}
    return {"renamed": True, "title": stored, "cleared": not stored}


async def _delete_server_conversation_history(identity: Any) -> Dict[str, Any]:
    """Delete one conversation from AutoYou-managed server stores."""
    canonical_session_id = str(getattr(identity, "canonical_session_id", "") or "").strip()
    canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
    raw_session_id = str(getattr(identity, "raw_session_id", "") or "").strip()
    result: Dict[str, Any] = {
        "deleted": False,
        "scope": "server_conversation",
        "components": [],
        "provider_history_retained": True,
    }
    if not canonical_session_id:
        result["reason"] = "missing_session_id"
        return result

    execution_manager = get_session_execution_manager()
    try:
        execution_result = await execution_manager.clear_session(canonical_session_id)
        if isinstance(execution_result, dict) and execution_result.get("active_stopped") is False:
            result["reason"] = "active_request_still_running"
            result["active_stopped"] = False
            return result
        if isinstance(execution_result, dict) and execution_result.get("cleared"):
            result["components"].append("active_execution")
    except Exception as exc:
        LOGGER.warning("Failed to clear active execution for %s: %s", canonical_session_id, exc)
        result["reason"] = "active_execution_clear_failed"
        return result

    session_manager = _get_conversation_session_manager()
    delete_history = getattr(session_manager, "delete_conversation_history", None) if session_manager else None
    history_deleted = False
    if callable(delete_history):
        try:
            active_components = list(result.get("components") or [])
            history_result = delete_history(
                canonical_user_id,
                canonical_session_id,
                external_session_id=raw_session_id,
            )
            if hasattr(history_result, "__await__"):
                history_result = await history_result
            if isinstance(history_result, dict):
                result.update(history_result)
                history_deleted = bool(history_result.get("deleted"))
                result["scope"] = "server_conversation"
                result["components"] = list(
                    dict.fromkeys(
                        [*active_components, *(history_result.get("components") or [])]
                    )
                )
        except Exception as exc:
            LOGGER.warning("Failed to delete server conversation history for %s: %s", canonical_session_id, exc)
            result["reason"] = "history_delete_failed"
    else:
        result["reason"] = "server_history_store_unavailable"

    result["active_execution_cleared"] = "active_execution" in result.get("components", [])
    result["deleted"] = history_deleted
    if not history_deleted and not result.get("reason"):
        result["reason"] = "server_history_not_deleted"
    return result


async def _start_new_conversation_for_owner(
    platform: str,
    sender_id: str,
    identity_sender_id: Optional[str] = None,
) -> str:
    session_manager = _get_conversation_session_manager()
    if session_manager is None or not hasattr(session_manager, "advance_conversation_thread"):
        return "Started a new conversation."

    normalized_platform = str(platform or "").strip().lower()
    stable_sender_id = str(identity_sender_id or sender_id or "").strip()
    if not normalized_platform or not stable_sender_id:
        LOGGER.warning(
            "Cannot start a new conversation without a stable owner key (platform=%s sender_id=%s identity_sender_id=%s)",
            platform,
            redact_identifier(sender_id),
            redact_identifier(identity_sender_id),
        )
        return "Unable to start a new conversation right now."

    owner_key = build_owner_key(normalized_platform, stable_sender_id)
    try:
        thread_id, canonical_session_id = session_manager.advance_conversation_thread(owner_key)
        LOGGER.info(
            "Started new conversation thread %s for owner %s -> %s",
            thread_id,
            redact_identifier(owner_key),
            redact_identifier(canonical_session_id),
        )
    except Exception as exc:
        LOGGER.warning("Failed to advance conversation thread for owner %s: %s", redact_identifier(owner_key), exc)
        return "Unable to start a new conversation right now."

    return "Started a new conversation."

def _extract_conversation_request(
    content: Any,
    metadata: Any,
) -> tuple[bool, str, bool, Dict[str, Any]]:
    normalized_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    normalized_content = str(content or "").strip()
    lowered = normalized_content.lower()
    explicit_action = str(normalized_metadata.get("conversation_action") or "").strip().lower()
    start_new_thread = explicit_action == "new" or lowered in {
        "/new",
        "/newconversation",
        "/newchat",
    }
    control_only = lowered in {"/new", "/newconversation", "/newchat"} or (
        start_new_thread and not normalized_content
    )
    if start_new_thread:
        normalized_metadata.pop("conversation_action", None)
    return start_new_thread, normalized_content, control_only, normalized_metadata

def _base_conversation_session_id(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        return normalized
    base, separator, tail = normalized.rpartition("::")
    if separator and tail.isdigit():
        return base
    return normalized

def _empty_context_usage_snapshot(identity: Any) -> Dict[str, Any]:
    return {
        "available": False,
        "conversation_session_id": _build_client_conversation_session_id(identity),
        "conversation_thread_id": _conversation_thread_id_for_metadata(
            getattr(identity, "thread_id", None)
        ),
        "summary_text": "",
        "alert_level": "normal",
        "updated_at_ms": int(time.time() * 1000),
    }

async def _conversation_context_usage_snapshot(identity: Any) -> Dict[str, Any]:
    snapshot = _empty_context_usage_snapshot(identity)
    session_manager = _get_conversation_session_manager()
    if (
        session_manager is None
        or not hasattr(session_manager, "get_mapped_session_id")
        or not hasattr(session_manager, "get_session_context_usage_snapshot")
    ):
        return snapshot

    canonical_session_id = str(getattr(identity, "canonical_session_id", "") or "").strip()
    canonical_user_id = str(getattr(identity, "canonical_user_id", "") or "").strip()
    if not canonical_session_id or not canonical_user_id:
        return snapshot

    try:
        ai_agent_session_id = session_manager.get_mapped_session_id(canonical_session_id, canonical_user_id)
    except Exception as exc:
        LOGGER.debug(
            "Failed to resolve AI session mapping for context snapshot %s/%s: %s",
            canonical_user_id,
            canonical_session_id,
            exc,
        )
        ai_agent_session_id = None

    if not ai_agent_session_id:
        return snapshot

    if hasattr(session_manager, "get_user_session"):
        try:
            existing_session = await session_manager.get_user_session(
                canonical_user_id,
                ai_agent_session_id,
            )
        except Exception as exc:
            LOGGER.debug(
                "Failed to verify mapped ADK session for context snapshot %s/%s: %s",
                canonical_user_id,
                ai_agent_session_id,
                exc,
            )
            existing_session = None

        if existing_session is None:
            LOGGER.info(
                "Skipping stale context snapshot replay for missing ADK session %s/%s",
                canonical_user_id,
                ai_agent_session_id,
            )
            return snapshot

    try:
        persisted = session_manager.get_session_context_usage_snapshot(
            ai_agent_session_id,
            canonical_user_id,
        )
    except Exception as exc:
        LOGGER.debug(
            "Failed to read persisted context snapshot for %s/%s: %s",
            canonical_user_id,
            ai_agent_session_id,
            exc,
        )
        persisted = None

    if not isinstance(persisted, dict):
        snapshot["ai_agent_session_id"] = ai_agent_session_id
        return snapshot

    merged = dict(persisted)
    merged["available"] = bool(merged.get("available"))
    merged["conversation_session_id"] = _build_client_conversation_session_id(identity)
    merged["conversation_thread_id"] = _conversation_thread_id_for_metadata(
        getattr(identity, "thread_id", None)
    )
    merged["ai_agent_session_id"] = str(ai_agent_session_id)
    if "updated_at_ms" not in merged:
        merged["updated_at_ms"] = int(time.time() * 1000)
    if "summary_text" not in merged:
        merged["summary_text"] = ""
    if "alert_level" not in merged:
        merged["alert_level"] = "normal"
    return merged

# ========= AI Agent Process Management =========


# AI agent process management helpers are imported from core_server.services above.


# ========= Admin/Config Globals =========
DEFAULT_SERVER_PASSWORD = "autoyou123"
MIN_SERVER_PASSWORD_LENGTH = 12
_WEAK_SERVER_PASSWORDS = {
    "autoyou123", "password", "password123", "12345678", "123456789",
    "1234567890", "qwertyuiop", "letmein", "admin123", "changeme",
}


def _server_password_strength_error(password: str) -> Optional[str]:
    """Return a plain-language error if `password` is too weak, else None.

    Longer passwords are the main lever here: the pairing handshake no
    longer relies on a slow KDF to blunt offline guessing (see the CPace
    pairing envelope), so an attacker who's rate-limited online is the
    realistic threat - length is what raises that bar.
    """
    candidate = password or ""
    if len(candidate) < MIN_SERVER_PASSWORD_LENGTH:
        return f"Choose a password with at least {MIN_SERVER_PASSWORD_LENGTH} characters."
    if candidate.strip().lower() in _WEAK_SERVER_PASSWORDS:
        return "That password is too common. Choose something more unique, or use Generate password."
    return None


SERVER_PASSWORD_GENERATOR_LENGTH = 24
SERVER_PASSWORD_GENERATOR_ALPHABET = (
    "ABCDEFGHJKLMNPQRSTUVWXYZ"
    "abcdefghijkmnopqrstuvwxyz"
    "23456789"
    "!@#$%*-_=+?"
)
SERVER_INSTANCE_NAME = _get_instance_name_env()
ADMIN_WEB_SERVICE_PORT = _get_positive_int_env("AUTOYOU_ADMIN_PORT", "AUTOYOU_ADMIN_SPORT", default=8001)
AI_AGENT_SERVER_PORT = _get_positive_int_env("AUTOYOU_AI_PORT", "AUTOYOU_AI_AGENT_SERVER_PORT", default=8081)
AUTH_SERVER_PORT = _get_positive_int_env("AUTOYOU_AUTH_PORT", "AUTOYOU_AUTH_SERVER_PORT", default=8002)
AUTOYOU_CLOUD_BASE = os.environ.get("AUTOYOU_CLOUD_BASE_URL", "https://app.autoyou.me")
try:
    AUTOYOU_CLOUD_SERVER_TOKEN_MAX_AGE_SECONDS = float(
        os.environ.get("AUTOYOU_CLOUD_SERVER_TOKEN_MAX_AGE_SECONDS", str(3 * 24 * 60 * 60))
    )
except Exception:
    AUTOYOU_CLOUD_SERVER_TOKEN_MAX_AGE_SECONDS = float(3 * 24 * 60 * 60)
try:
    AUTOYOU_CLOUD_SERVER_TOKEN_LEGACY_MAX_AGE_SECONDS = float(
        os.environ.get("AUTOYOU_CLOUD_SERVER_TOKEN_LEGACY_MAX_AGE_SECONDS", str(7 * 24 * 60 * 60))
    )
except Exception:
    AUTOYOU_CLOUD_SERVER_TOKEN_LEGACY_MAX_AGE_SECONDS = float(7 * 24 * 60 * 60)
try:
    AUTOYOU_CLOUD_SERVER_TOKEN_ROTATE_AFTER_SECONDS = float(
        os.environ.get("AUTOYOU_CLOUD_SERVER_TOKEN_ROTATE_AFTER_SECONDS", str(24 * 60 * 60))
    )
except Exception:
    AUTOYOU_CLOUD_SERVER_TOKEN_ROTATE_AFTER_SECONDS = float(24 * 60 * 60)
AUTOYOU_CLOUD_SSE_HEARTBEAT_SECONDS = float(os.environ.get("AUTOYOU_CLOUD_SSE_HEARTBEAT_SECONDS", "30"))
AUTOYOU_CLOUD_SSE_SOCK_READ_TIMEOUT_SECONDS = float(
    os.environ.get(
        "AUTOYOU_CLOUD_SSE_SOCK_READ_TIMEOUT_SECONDS",
        str(max(45.0, AUTOYOU_CLOUD_SSE_HEARTBEAT_SECONDS + 15.0)),
    )
)
AUTOYOU_CLOUD_SSE_STALE_AFTER_SECONDS = float(
    os.environ.get(
        "AUTOYOU_CLOUD_SSE_STALE_AFTER_SECONDS",
        str(AUTOYOU_CLOUD_SSE_SOCK_READ_TIMEOUT_SECONDS),
    )
)
AUTOYOU_CLOUD_SSE_RECONNECT_INITIAL_SECONDS = float(
    os.environ.get("AUTOYOU_CLOUD_SSE_RECONNECT_INITIAL_SECONDS", "5")
)
AUTOYOU_CLOUD_SSE_RECONNECT_MAX_SECONDS = float(
    os.environ.get("AUTOYOU_CLOUD_SSE_RECONNECT_MAX_SECONDS", "600")
)


def _cloud_registered_at_timestamp(cloud_cfg: Mapping[str, Any]) -> float:
    raw_value = cloud_cfg.get("registered_at")
    return _cloud_timestamp(raw_value)


def _cloud_token_issued_at_timestamp(cloud_cfg: Mapping[str, Any]) -> float:
    raw_value = cloud_cfg.get("token_issued_at")
    return _cloud_timestamp(raw_value)


def _cloud_timestamp(raw_value: Any) -> float:
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    text = str(raw_value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def _cloud_server_token_expired(cloud_cfg: Mapping[str, Any], *, now: Optional[float] = None) -> bool:
    if not str(cloud_cfg.get("server_token") or "").strip():
        return False
    issued_at = _cloud_token_issued_at_timestamp(cloud_cfg)
    max_age = max(
        0.0,
        float(
            AUTOYOU_CLOUD_SERVER_TOKEN_MAX_AGE_SECONDS
            if issued_at > 0
            else AUTOYOU_CLOUD_SERVER_TOKEN_LEGACY_MAX_AGE_SECONDS
        ),
    )
    if max_age <= 0:
        return False
    saved_at = issued_at or _cloud_registered_at_timestamp(cloud_cfg)
    return saved_at <= 0 or ((time.time() if now is None else now) - saved_at) > max_age


def _cloud_server_token_rotation_due(cloud_cfg: Mapping[str, Any], *, now: Optional[float] = None) -> bool:
    if _cloud_server_token_expired(cloud_cfg, now=now):
        return False
    if not str(cloud_cfg.get("server_token") or "").strip():
        return False
    rotate_after = max(0.0, float(AUTOYOU_CLOUD_SERVER_TOKEN_ROTATE_AFTER_SECONDS))
    if rotate_after <= 0:
        return False
    saved_at = _cloud_token_issued_at_timestamp(cloud_cfg) or _cloud_registered_at_timestamp(cloud_cfg)
    return saved_at > 0 and ((time.time() if now is None else now) - saved_at) >= rotate_after


async def _rotate_cloud_server_token_if_due(cloud_cfg: Mapping[str, Any], *, force: bool = False) -> bool:
    if not force and not _cloud_server_token_rotation_due(cloud_cfg):
        return False
    server_token = str(cloud_cfg.get("server_token") or "").strip()
    server_id = str(cloud_cfg.get("server_id") or "").strip()
    if httpx is None or not server_token or not server_id:
        return False

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/server/token/rotate",
                json={"server_id": server_id},
                headers={"Authorization": f"Bearer {server_token}"},
            )
    except Exception as exc:
        LOGGER.debug("AutoYou Cloud: server-token rotation skipped: %s", exc)
        return False

    if response.status_code in {401, 403}:
        STATE.cloud_token_rejected = True
        return False
    if response.status_code >= 400:
        LOGGER.debug("AutoYou Cloud: server-token rotation returned HTTP %s", response.status_code)
        return False

    try:
        payload = cast(Dict[str, Any], response.json())
    except Exception:
        return False
    new_token = str(payload.get("server_token") or "").strip()
    if not new_token or new_token == server_token:
        return False

    try:
        cfg = _loaded_config_for_update()
    except Exception as exc:
        LOGGER.warning("AutoYou Cloud: rotated server token could not be persisted: %s", exc)
        return False

    current_cloud = cfg.setdefault("cloud", {})
    if str(current_cloud.get("server_token") or "").strip() != server_token:
        return False
    now_iso = datetime.datetime.utcnow().isoformat() + "Z"
    current_cloud["server_token"] = new_token
    current_cloud["server_id"] = str(payload.get("server_id") or server_id)
    current_cloud["token_issued_at"] = str(payload.get("token_issued_at") or now_iso)
    STATE.config = _save_and_reload_state_config(cfg)
    STATE.cloud_token_rejected = False
    LOGGER.info("AutoYou Cloud: rotated server token for server_id=%s", server_id)
    return True

def generate_random_server_password(length: int = SERVER_PASSWORD_GENERATOR_LENGTH) -> str:
    try:
        requested = int(length)
    except Exception:
        requested = SERVER_PASSWORD_GENERATOR_LENGTH
    requested = max(16, min(128, requested))
    return "".join(secrets.choice(SERVER_PASSWORD_GENERATOR_ALPHABET) for _ in range(requested))
_TUNNELMOLE_TIMEOUT_MINUTES_MIN = 1
_TUNNELMOLE_TIMEOUT_MINUTES_MAX = 24 * 60
_TUNNELMOLE_SHARED_PROXY_FREE_LIMIT_MINUTES = 60
_PAIRING_TOTP_WINDOW_STEPS = (0, -1, -2, 1)

# Use platform-aware config directory (handles macOS ~/Library/Application Support/ and Windows app dir)
# Pass __file__ so the function can determine the correct application root
_CONFIG_DIR = get_config_dir("AutoYou", anchor=__file__)
_CONFIG_DIR.mkdir(parents=True, exist_ok=True)  # Ensure directory exists
CONFIG_FILE_PATH = str(_CONFIG_DIR / "config.encrypted")
CONFIG_BAK_PATH = str(_CONFIG_DIR / "config.encrypted.bak")
CONFIG_KEYSTORE_PATH = _CONFIG_DIR / "config.keystore.enc"
LOGIN_UI_DB_PATH = str(_CONFIG_DIR / "login_ui_state.db")

_KS_SERVER_PASSWORD_CRED_NAME = "pairing-password"
_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_ENV = "AUTOYOU_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS"
_DEFAULT_MACOS_KEYCHAIN_BOOTSTRAP_TIMEOUT_SECONDS = 1.0


def _macos_keychain_bootstrap_timeout_seconds() -> Optional[float]:
    """Return the macOS automatic-startup Keychain read policy.

    Positive values select the shared no-UI Keychain lookup; ``0`` is the
    deliberate foreground mode for an operator who wants startup itself to
    wait for consent. Login, key rotation, and native unlock remain explicit
    foreground actions.
    """
    return _ks_macos_keychain_bootstrap_timeout_seconds()


def _load_keystore_config_during_bootstrap(keystore: Any) -> Optional[Dict[str, Any]]:
    """Read saved config without allowing a pending macOS Keychain sheet to block Uvicorn."""
    timeout_seconds = _macos_keychain_bootstrap_timeout_seconds()
    if timeout_seconds is None:
        return keystore.load()
    if timeout_seconds > 0:
        LOGGER.info("Reading saved macOS Keychain configuration without authentication UI.")
    else:
        LOGGER.info("Reading saved macOS Keychain configuration in foreground.")
    config = keystore.load(operation_timeout_seconds=timeout_seconds)
    if config is None:
        LOGGER.warning(
            "Saved macOS Keychain configuration was not already authorized; "
            "starting the locked recovery UI."
        )
    return config

def _server_keystore_service_name() -> str:
    from shared.keystore import server_keystore_service_name
    return server_keystore_service_name(CONFIG_KEYSTORE_PATH, _KS_SERVICE_NAME)

def _get_server_keystore():
    """Return a KeystoreJsonStore for the main server config, or None."""
    if not _HAS_SERVER_KEYSTORE or _KeystoreJsonStore is None:
        return None
    return _KeystoreJsonStore(
        CONFIG_KEYSTORE_PATH,
        default_factory=_default_config,
        service_name=_server_keystore_service_name(),
        username=_KS_CRED_NAME,
    )
LOGIN_MINIGAME_KEY = "boot_sweep"
LOGIN_UI_DB_LOCK = threading.Lock()
CONFIG_IO_LOCK = threading.Lock()

# Default ICE servers advertised when no persisted RTC config exists.
# Operators can keep Local Pair private by setting AUTOYOU_LOCAL_STUNTURN_HOST
# or AUTOYOU_DISABLE_PUBLIC_STUN before startup.
DEFAULT_ICE_SERVERS = load_default_ice_servers_from_env()
try:
    PAIR_SESSION_EXPIRATION_MINUTES = max(1, int(os.getenv("PAIR_SESSION_EXPIRATION_MINUTES", "5")))
except Exception:
    PAIR_SESSION_EXPIRATION_MINUTES = 5

def _set_config_session(
    *,
    config_store: str,
    server_password: Optional[str] = None,
    config_unlock_password: Optional[str] = None,
) -> None:
    normalized_store = _normalize_config_store(config_store)
    normalized_server_password = str(server_password or "").strip() or None
    normalized_unlock_password = str(config_unlock_password or "").strip() or None

    STATE.config_store = normalized_store
    STATE.server_password = normalized_server_password
    STATE.config_unlock_password = (
        normalized_unlock_password if normalized_store == CONFIG_STORE_ENCRYPTED else None
    )

def _keystore_server_password_available() -> bool:
    try:
        return bool(_HAS_SERVER_KEYSTORE and _server_keyring is not None and _ks_available())
    except Exception:
        return False

def _load_keystore_server_password(
    *,
    operation_timeout_seconds: Optional[float] = None,
) -> Optional[str]:
    if not _keystore_server_password_available():
        return None
    service_name = _server_keystore_service_name()
    try:
        password = _ks_get_password(
            service_name,
            _KS_SERVER_PASSWORD_CRED_NAME,
            operation_timeout_seconds=operation_timeout_seconds,
        )
    except _ServerKeyringError as exc:
        LOGGER.warning("Failed to read server password from OS keystore: %s", exc)
        return None
    except Exception as exc:
        LOGGER.warning("Unexpected OS keystore error reading server password: %s", exc)
        return None

    normalized = str(password or "").strip()
    return normalized or None

def _persist_server_password(
    password: Optional[str],
    *,
    operation_timeout_seconds: Optional[float] = None,
    allow_update: bool = True,
) -> None:
    normalized = str(password or "").strip()
    STATE.server_password = normalized or None
    if not normalized or not _keystore_server_password_available():
        return
    service_name = _server_keystore_service_name()
    try:
        # macOS keyring updates delete and re-add the item. Avoid that
        # destructive path when an older app signature already stored the
        # same password and the item is readable.
        if _load_keystore_server_password(
            operation_timeout_seconds=operation_timeout_seconds,
        ) == normalized:
            return
        if not allow_update:
            LOGGER.warning(
                "Pairing-password credential was not available during automatic startup; "
                "leaving the existing Keychain item unchanged."
            )
            return
        _ks_call_keyring_operation(
            _server_keyring.set_password,
            service_name,
            _KS_SERVER_PASSWORD_CRED_NAME,
            normalized,
            operation_name="set_server_password",
            default=False,
            timeout_seconds=operation_timeout_seconds,
        )
    except _ServerKeyringError as exc:
        LOGGER.warning("Failed to persist server password in OS keystore: %s", exc)
    except Exception as exc:
        LOGGER.warning("Unexpected OS keystore error persisting server password: %s", exc)

def _clear_persisted_server_password() -> None:
    if not _keystore_server_password_available():
        return
    service_name = _server_keystore_service_name()
    try:
        _ks_call_keyring_operation(
            _server_keyring.delete_password,
            service_name,
            _KS_SERVER_PASSWORD_CRED_NAME,
            operation_name="delete_server_password",
            default=False,
        )
    except _ServerKeyringError:
        return
    except Exception as exc:
        LOGGER.warning("Unexpected OS keystore error clearing server password: %s", exc)

def _remove_path_silently(path_value: Any) -> None:
    try:
        Path(path_value).unlink(missing_ok=True)
    except Exception:
        return

def _clear_main_keystore_config() -> None:
    _remove_path_silently(CONFIG_KEYSTORE_PATH)
    try:
        _ks_delete_key(_server_keystore_service_name(), _KS_CRED_NAME)
    except Exception as exc:
        LOGGER.warning("Failed to clear OS keystore config key: %s", exc)

def _path_is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except Exception:
        return False

def _factory_reset_directory_is_safe(path_value: Any) -> bool:
    try:
        candidate = Path(path_value).expanduser().resolve(strict=False)
    except Exception:
        return False
    if not candidate.exists() or not candidate.is_dir():
        return False

    test_root_raw = str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip()
    if test_root_raw:
        try:
            test_root = Path(test_root_raw).expanduser().resolve(strict=False)
            if _path_is_relative_to(candidate, test_root):
                return True
        except Exception:
            pass

    # Never recursively clear the checked-out source tree or packaged app root.
    # Source runs may resolve mutable data to the repository root; reset should
    # wipe runtime state, not the application that is performing the wipe.
    dangerous_markers = (".git", "server.py", "pyproject.toml", "run_autoyou.sh", "run_autoyou.bat")
    if any((candidate / marker).exists() for marker in dangerous_markers):
        return False
    with suppress(Exception):
        app_root = Path(APP_ROOT).expanduser().resolve(strict=False)
        if candidate == app_root or _path_is_relative_to(app_root, candidate):
            return False
    with suppress(Exception):
        resources_root = Path(RESOURCES_ROOT).expanduser().resolve(strict=False)
        if candidate == resources_root or _path_is_relative_to(resources_root, candidate):
            return False

    return candidate.name.lower() == "autoyou"

def _factory_reset_runtime_directories() -> List[Path]:
    candidates = [
        _CONFIG_DIR,
        Path(CONFIG_FILE_PATH).expanduser().parent,
        Path(CONFIG_BAK_PATH).expanduser().parent,
        Path(CONFIG_KEYSTORE_PATH).expanduser().parent,
        get_config_dir("AutoYou", anchor=__file__),
        get_user_data_dir("AutoYou"),
    ]
    with suppress(Exception):
        candidates.append(get_mutable_data_dir("AutoYou", anchor=__file__))

    resolved: List[Path] = []
    seen: Set[str] = set()
    for candidate in candidates:
        try:
            path = Path(candidate).expanduser().resolve(strict=False)
        except Exception:
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if _factory_reset_directory_is_safe(path):
            resolved.append(path)
    return resolved

def _clear_directory_contents(path: Path) -> Tuple[List[str], List[str]]:
    removed: List[str] = []
    failed: List[str] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except Exception as exc:
        return [], [f"{path}: {exc}"]

    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
            removed.append(str(child))
        except Exception as exc:
            failed.append(f"{child}: {exc}")
    return removed, failed

def perform_factory_reset_cleanup() -> Dict[str, Any]:
    """Clear local AutoYou runtime/config state before shutting the process down."""
    removed: List[str] = []
    failed: List[str] = []
    cleared_dirs: List[str] = []
    skipped_files: List[str] = []
    unlock_file_path = _get_unlock_file_path()

    for reset_dir in _factory_reset_runtime_directories():
        dir_removed, dir_failed = _clear_directory_contents(reset_dir)
        removed.extend(dir_removed)
        failed.extend(dir_failed)
        cleared_dirs.append(str(reset_dir))

    for file_value in (CONFIG_FILE_PATH, CONFIG_BAK_PATH, CONFIG_KEYSTORE_PATH, unlock_file_path):
        try:
            path = Path(file_value).expanduser()
            if path.exists():
                path.unlink()
                removed.append(str(path))
            else:
                skipped_files.append(str(path))
        except Exception as exc:
            failed.append(f"{file_value}: {exc}")

    with suppress(Exception):
        if clear_license_acknowledgement(anchor=__file__):
            removed.append("LICENSE_ACKNOWLEDGEMENT")

    _clear_persisted_server_password()
    _clear_main_keystore_config()

    ADMIN_SESSIONS.clear()
    ADMIN_API_TOKENS.clear()
    STATE.config = {}
    _set_config_session(config_store=CONFIG_STORE_NONE)
    STATE.used_default_password = False
    STATE.decrypted_via_env = False
    STATE.initialized_services_on_startup = False
    STATE._unlock_state_mem = "Resetting"
    _update_startup_status(
        status="starting",
        headline="Resetting AutoYou",
        detail="Local configuration was cleared. The server is shutting down so the next launch starts fresh.",
        step=1,
        total_steps=1,
    )

    return {
        "success": len(failed) == 0,
        "removed_count": len(removed),
        "removed": removed[:40],
        "cleared_dirs": cleared_dirs,
        "skipped_files": skipped_files,
        "failed": failed,
    }

def _schedule_factory_reset_shutdown() -> None:
    async def deferred_shutdown() -> None:
        try:
            await asyncio.sleep(0.75)
            await graceful_shutdown()
        except Exception as exc:
            LOGGER.error("Error during factory-reset shutdown: %s", exc)

    asyncio.create_task(deferred_shutdown())

MANAGED_FRONTEND_APPS: Dict[str, Dict[str, Any]] = {
    "backup_agent": {
        "app_import": "autoyou_agents.backup_agent.website.backend.app:app",
        "default_port": 8111,
    },
    "audio_agent": {
        "app_import": "autoyou_agents.audio_agent.website.backend.app:app",
        "default_port": 8097,
    },
    "build_prompt_agent": {
        "app_import": "autoyou_agents.build_prompt_agent.website.backend.app:app",
        "default_port": 8074,
    },
    "notes_agent": {
        "app_import": "autoyou_agents.notes_agent.website.backend.app:app",
        "default_port": 8094,
    },
    "tasks_agent": {
        "app_import": "autoyou_agents.tasks_agent.website.backend.app:app",
        "default_port": 8095,
    },
    "notify_agent": {
        "app_import": "autoyou_agents.notify_agent.website.backend.app:app",
        "default_port": 8096,
    },
    "website_agent": {
        "app_import": "autoyou_agents.website_agent.website.backend.app:app",
        "default_port": 8084,
    },
    "agent_builder_agent": {
        "app_import": "autoyou_agents.agent_builder_agent.website.backend.app:app",
        "default_port": 8085,
    },
    "hosting_agent": {
        "app_import": "autoyou_agents.hosting_agent.website.backend.app:app",
        "default_port": 8089,
    },
    "cloudflare_agent": {
        "app_import": "autoyou_agents.cloudflare_agent.website.backend.app:app",
        "default_port": 8102,
    },
    "ionos_agent": {
        "app_import": "autoyou_agents.ionos_agent.website.backend.app:app",
        "default_port": 8103,
    },
    "ionos_cloudflare_agent": {
        "app_import": "autoyou_agents.ionos_cloudflare_agent.website.backend.app:app",
        "default_port": 8104,
    },
    "skills_agent": {
        "app_import": "autoyou_agents.skills_agent.website.backend.app:app",
        "default_port": 8086,
    },
    "remote_desktop_agent": {
        "app_import": "autoyou_agents.remote_desktop_agent.website.backend.app:app",
        "default_port": 8087,
    },
    "media_generation_agent": {
        "app_import": "autoyou_agents.media_generation_agent.website.backend.app:app",
        "default_port": 8077,
    },
    "ads_watching_agent": {
        "app_import": "autoyou_agents.ads_watching_agent.website.backend.app:app",
        "default_port": 8088,
    },
    "voice_training_agent": {
        "app_import": "autoyou_agents.voice_training_agent.website.backend.app:app",
        "default_port": 8078,
    },
    "education_agent": {
        "app_import": "autoyou_agents.education_agent.website.backend.app:app",
        "default_port": 8079,
    },
    "files_agent": {
        "app_import": "autoyou_agents.files_agent.website.backend.app:app",
        "default_port": 8070,
    },
    "data_collector_agent": {
        "app_import": "autoyou_agents.data_collector_agent.website.backend.app:app",
        "default_port": 18067,
    },
    "fine_tuning_agent": {
        "app_import": "autoyou_agents.fine_tuning_agent.website.backend.app:app",
        "default_port": 8068,
    },
    "location_agent": {
        "app_import": "autoyou_agents.location_agent.website.backend.app:app",
        "default_port": 8110,
    },
    "page_agent": {
        "app_import": "autoyou_agents.page_agent.website.backend.app:app",
        "default_port": 8069,
    },
    "persona_agent": {
        "app_import": "autoyou_agents.persona_agent.website.backend.app:app",
        "default_port": 8093,
    },
    "donation_agent": {
        "app_import": "autoyou_agents.donation_agent.website.backend.app:app",
        "default_port": 8090,
    },
    "earnings_agent": {
        "app_import": "autoyou_agents.earnings_agent.website.backend.app:app",
        "default_port": 8091,
    },
    "win_security_agent": {
        "app_import": "autoyou_agents.win_security_agent.website.backend.app:app",
        "default_port": 8075,
    },
    "mac_security_agent": {
        "app_import": "autoyou_agents.mac_security_agent.website.backend.app:app",
        # 8076 belongs to the AutoYou Connect local WebRTC browser proxy.
        # Keep the source-only macOS Security backend on its own loopback port
        # so a same-machine Local Pair can expose the client browser normally.
        "default_port": 8101,
    },
}

# Fresh install state and website exposure are separate defaults.  An agent
# must still be explicitly installed before its backend can run, but an
# installed manifest-backed website is exposed by default unless it is an
# explicit security/private opt-in.  A persisted agent_frontends.<name>
# enabled value always wins, so this does not rewrite existing preferences.
FRONTEND_DEFAULT_ENABLEMENT_OVERRIDES: Dict[str, bool] = {
    # The admin website exposes the full server admin shell through the
    # browser proxy. It stays opt-in even though admin_agent is installed;
    # the admin page must never be advertised or exposed by default.
    "admin_agent": False,
    # These surfaces remain deliberate opt-ins because they expose sensitive
    # machine telemetry or trading controls.  Installing the agent alone must
    # not publish them to paired browser clients.
    "win_security_agent": False,
    "mac_security_agent": False,
    # This surface controls public exposure and a connector credential.
    "cloudflare_agent": False,
    # These surfaces can deploy a public website or direct authoritative DNS.
    "ionos_agent": False,
    "ionos_cloudflare_agent": False,
}

FRONTEND_DEFAULT_ENABLEMENT: Dict[str, bool] = {
    agent_name: FRONTEND_DEFAULT_ENABLEMENT_OVERRIDES.get(agent_name, True)
    for agent_name in ("admin_agent", *MANAGED_FRONTEND_APPS)
}

FRONTEND_CONTROL_LABELS: Dict[str, str] = {
    "admin_agent": "Admin Website",
    "backup_agent": "Backup Agent",
    "audio_agent": "Audio Player App",
    "build_prompt_agent": "Prompt Builder",
    "notes_agent": "Notes Manager App",
    "tasks_agent": "Tasks App",
    "notify_agent": "Notify App",
    "internet_agent": "Internet Search",
    "location_agent": "Location Timeline",
    "website_agent": "Website Builder",
    "agent_builder_agent": "Agent Builder",
    "cloudflare_agent": "Cloudflare Tunnel",
    "ionos_agent": "IONOS Hosting",
    "ionos_cloudflare_agent": "IONOS Cloudflare Handoff",
    "skills_agent": "Skills Manager",
    "remote_desktop_agent": "Remote Desktop",
    "media_generation_agent": "Media Generator App",
    "ads_watching_agent": "Ads Watching",
    "voice_training_agent": "Voice Training App",
    "education_agent": "Education Agent",
    "files_agent": "Files Agent",
    "data_collector_agent": "Data Collector",
    "fine_tuning_agent": "Fine Tuning App",
    "page_agent": "AutoYou Page",
    "persona_agent": "Persona",
    "donation_agent": "Support & Donations",
    "earnings_agent": "Earnings",
    "win_security_agent": "Windows Security",
    "mac_security_agent": "macOS Security",
}

AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY = "path_proxy"
AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD = "direct_forward"
AGENT_WEBSITE_ROUTE_MODES = {
    AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY,
    AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD,
}
# These websites rely on their own root-relative assets, so an old persisted
# path-proxy preference must not silently move them off their advertised port.
AGENT_FRONTEND_REQUIRED_DIRECT_FORWARD = {"admin_agent", "ads_watching_agent"}

FRONTEND_CONTROL_HELP: Dict[str, str] = {
    "backup_agent": (
        "Publish the opt-in Backup Agent website for verified, resumable file "
        "transfers. Remote uploads require editor or admin browser access."
    ),
    "admin_agent": (
        "Expose the full admin website to paired AutoYou browser clients. Disabled "
        "by default because it grants remote admin access."
    ),
    "audio_agent": (
        "Publish the web audio player for HTTP streaming playback from configured "
        "music-library folders. Enabled by default."
    ),
    "notes_agent": (
        "Publish the read-only notes website for paired AutoYou browser clients. "
        "Enabled by default."
    ),
    "tasks_agent": (
        "Publish the Tasks board for scheduling recurring and one-time AI jobs. "
        "Enabled by default."
    ),
    "notify_agent": (
        "Publish the Notify board for scheduling timed notifications. Enabled by default."
    ),
    "internet_agent": (
        "Let AutoYou use the installed web-search helper when a prompt needs "
        "current internet results. Turn it off for offline-only answers without "
        "uninstalling anything."
    ),
    "website_agent": (
        "Publish the Website Builder chat website for building and deploying agent "
        "websites. Enabled by default after this agent is installed."
    ),
    "agent_builder_agent": (
        "Publish the Agent Builder chat website for scaffolding, developing, and "
        "publishing new agents. Enabled by default after this agent is installed."
    ),
    "cloudflare_agent": (
        "Configure one Cloudflare Tunnel for selected agent websites. Disabled by "
        "default because it controls public exposure and a connector credential."
    ),
    "ionos_agent": (
        "Manage committed website deployment, origin TLS, email, and website input "
        "processing on IONOS. Disabled by default because it can deploy publicly."
    ),
    "ionos_cloudflare_agent": (
        "Plan and verify IONOS nameserver delegation, Cloudflare DNS, redirects, TLS, "
        "mail preservation, and DNSSEC. Disabled by default because it controls DNS."
    ),
    "skills_agent": (
        "Expose the Skills Manager website for viewing, "
        "creating, editing, and deleting custom runtime skills. Enabled by default."
    ),
    "remote_desktop_agent": (
        "Turn this on when you want paired phones to open the Remote Desktop "
        "web console and control this computer's screen. Video-call screen "
        "sharing uses the Video & Calls settings. Enabled by default after this "
        "agent is installed."
    ),
    "media_generation_agent": (
        "Publish the Media Generator app for local video and image generation. "
        "Install the needed local media tools separately. Enabled by default after "
        "this agent is installed."
    ),
    "ads_watching_agent": (
        "Show the Ads Watching app when an eligible device opens it. Keep it off "
        "when you do not want the watch flow available. Enabled by default."
    ),
    "voice_training_agent": (
        "Open the Voice Training app to review saved call samples and train a "
        "custom voice. Recording samples from calls does not require this app page "
        "to be open. Enabled by default."
    ),
    "education_agent": (
        "Publish a friendly, private Education dashboard for following live learning "
        "sessions, questions, voice notes, media, and recordings you choose to keep. "
        "Enabled by default."
    ),
    "data_collector_agent": (
        "Publish the OTP-gated Data Collector app for consented local conversation "
        "collection and private training exports. Enabled by default after this agent is installed."
    ),
    "fine_tuning_agent": (
        "Publish the Fine Tuning app for prepared Data Collector exports, uploaded "
        "datasets, LoRA training jobs, and Ollama model controls. "
        "Enabled by default."
    ),
    "location_agent": (
        "Publish the OTP-gated, read-only location timeline for samples beamed by "
        "connected iOS and Android devices. The website never requests browser location."
    ),
    "win_security_agent": (
        "Publish the OTP-gated, read-only Windows network dashboard. It stays opt-in "
        "and is excluded from packaged/build-server runtimes."
    ),
    "mac_security_agent": (
        "Publish the OTP-gated, read-only macOS network dashboard. It stays opt-in "
        "and is excluded from packaged/build-server runtimes."
    ),
}

#: Clock seam for the startup-status payload.
#:
#: A test drives this with fixed readings. Patching ``time.time`` on the shared
#: module would replace it for every thread in the process, so the seam keeps
#: the fake where it belongs.
_startup_clock = time.time


def _update_startup_status(
    *,
    status: str,
    headline: str,
    detail: str,
    step: Optional[int] = None,
    total_steps: Optional[int] = None,
    error: str = "",
) -> None:
    now = _startup_clock()
    started_at = STATE.startup_status.get("started_at")
    completed_at = STATE.startup_status.get("completed_at")
    if status in {"starting", "initializing"} and not started_at:
        started_at = now
    if status in {"idle", "complete", "error"} and started_at is None:
        started_at = now
    if status == "idle":
        started_at = None
        completed_at = None
    elif status in {"starting", "initializing"}:
        completed_at = None
    elif status in {"complete", "error"}:
        completed_at = now

    next_total_steps = total_steps if isinstance(total_steps, int) and total_steps > 0 else int(
        STATE.startup_status.get("total_steps") or 8
    )

    STATE.startup_status = {
        "status": status,
        "headline": headline,
        "detail": detail,
        "step": max(0, int(step if step is not None else STATE.startup_status.get("step") or 0)),
        "total_steps": next_total_steps,
        "started_at": started_at,
        "completed_at": completed_at,
        "updated_at": now,
        "error": error,
    }

def _startup_status_payload() -> Dict[str, Any]:
    status = dict(STATE.startup_status or {})
    started_at = status.get("started_at")
    completed_at = status.get("completed_at")
    updated_at = status.get("updated_at")
    elapsed_seconds = 0
    try:
        if started_at:
            if completed_at:
                end_time = float(completed_at)
            elif status.get("status") in {"starting", "initializing"}:
                end_time = _startup_clock()
            elif updated_at:
                end_time = float(updated_at)
            else:
                end_time = _startup_clock()
            elapsed_seconds = max(0, int(end_time - float(started_at)))
    except Exception:
        elapsed_seconds = 0

    if STATE.initialized_services_on_startup and status.get("status") not in {"starting", "initializing"}:
        status["status"] = "complete"
        status["headline"] = status.get("headline") or "Initialization complete"
        status["detail"] = status.get("detail") or "Services are ready."
        status["step"] = status.get("step") or status.get("total_steps") or 8

    status["initialized"] = bool(STATE.initialized_services_on_startup)
    status["in_progress"] = status.get("status") in {"starting", "initializing"}
    status["elapsed_seconds"] = elapsed_seconds
    status["uptime_seconds"] = max(0, int(_startup_clock() - STATE.startup_time))
    status.setdefault("instance", build_instance_runtime_status())
    return status

def _get_cached_admin_status(cache_key: str) -> Optional[Any]:
    entry = (STATE.admin_status_cache or {}).get(cache_key)
    if not entry:
        return None
    if float(entry.get("expires_at", 0.0) or 0.0) <= time.monotonic():
        try:
            STATE.admin_status_cache.pop(cache_key, None)
        except Exception:
            pass
        return None
    return entry.get("value")

def _set_cached_admin_status(
    cache_key: str,
    value: Any,
    *,
    ttl_seconds: float = ADMIN_STATUS_CACHE_TTL_SECONDS,
) -> Any:
    STATE.admin_status_cache[cache_key] = {
        "value": value,
        "expires_at": time.monotonic() + max(0.1, float(ttl_seconds)),
    }
    return value

def _invalidate_admin_status_cache(*cache_keys: str) -> None:
    if not cache_keys:
        STATE.admin_status_cache.clear()
        return
    for cache_key in cache_keys:
        STATE.admin_status_cache.pop(cache_key, None)

def _local_wall_clock_isoformat() -> str:
    """Return the local system wall-clock time in stable ISO form."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

def _ensure_login_ui_db() -> None:
    with LOGIN_UI_DB_LOCK:
        conn = sqlite3.connect(LOGIN_UI_DB_PATH)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS login_minigame_scores (
                    game_key TEXT PRIMARY KEY,
                    high_score INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS login_minigame_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_key TEXT NOT NULL,
                    score INTEGER NOT NULL DEFAULT 0,
                    clicks INTEGER NOT NULL DEFAULT 0,
                    seconds REAL NOT NULL DEFAULT 0,
                    recorded_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_login_minigame_runs_lookup
                ON login_minigame_runs(game_key, score DESC, recorded_at DESC, id DESC)
                """
            )
            conn.commit()
        finally:
            conn.close()

def _list_login_minigame_high_scores(
    limit: int = 10,
    game_key: str = LOGIN_MINIGAME_KEY,
) -> list[Dict[str, Any]]:
    _ensure_login_ui_db()
    bounded_limit = max(1, min(int(limit or 10), 25))
    with LOGIN_UI_DB_LOCK:
        conn = sqlite3.connect(LOGIN_UI_DB_PATH)
        try:
            rows = conn.execute(
                """
                SELECT id, score, clicks, seconds, recorded_at
                FROM login_minigame_runs
                WHERE game_key = ?
                ORDER BY score DESC, recorded_at DESC, id DESC
                LIMIT ?
                """,
                (str(game_key), bounded_limit),
            ).fetchall()
            return [
                {
                    "id": int(row[0]),
                    "score": max(0, int(row[1] or 0)),
                    "clicks": max(0, int(row[2] or 0)),
                    "seconds": round(max(0.0, float(row[3] or 0.0)), 3),
                    "recorded_at": str(row[4] or ""),
                }
                for row in rows
            ]
        finally:
            conn.close()

def _get_login_minigame_high_score(game_key: str = LOGIN_MINIGAME_KEY) -> int:
    high_scores = _list_login_minigame_high_scores(limit=1, game_key=game_key)
    if high_scores:
        return max(0, int(high_scores[0].get("score") or 0))

    _ensure_login_ui_db()
    with LOGIN_UI_DB_LOCK:
        conn = sqlite3.connect(LOGIN_UI_DB_PATH)
        try:
            row = conn.execute(
                "SELECT high_score FROM login_minigame_scores WHERE game_key = ?",
                (str(game_key),),
            ).fetchone()
            if not row:
                return 0
            return max(0, int(row[0] or 0))
        finally:
            conn.close()

def _save_login_minigame_high_score(
    score: int,
    *,
    game_key: str = LOGIN_MINIGAME_KEY,
) -> int:
    """Persist the best known login minigame score without recording a run row."""
    normalized_score = max(0, int(score or 0))
    current_high_score = _get_login_minigame_high_score(game_key=game_key)
    next_high_score = max(current_high_score, normalized_score)

    _ensure_login_ui_db()
    with LOGIN_UI_DB_LOCK:
        conn = sqlite3.connect(LOGIN_UI_DB_PATH)
        try:
            conn.execute(
                """
                INSERT INTO login_minigame_scores (game_key, high_score, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(game_key) DO UPDATE SET
                    high_score = excluded.high_score,
                    updated_at = excluded.updated_at
                """,
                (str(game_key), next_high_score, int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()

    return next_high_score

def _record_login_minigame_run(
    score: int,
    *,
    clicks: int,
    seconds: float,
    game_key: str = LOGIN_MINIGAME_KEY,
) -> Dict[str, Any]:
    normalized_score = max(0, int(score or 0))
    normalized_clicks = max(0, int(clicks or 0))
    normalized_seconds = max(0.0, round(float(seconds or 0.0), 3))
    _ensure_login_ui_db()
    recorded_run: Optional[Dict[str, Any]] = None
    with LOGIN_UI_DB_LOCK:
        conn = sqlite3.connect(LOGIN_UI_DB_PATH)
        try:
            if normalized_score > 0 and normalized_clicks > 0 and normalized_seconds > 0:
                recorded_at = _local_wall_clock_isoformat()
                cur = conn.execute(
                    """
                    INSERT INTO login_minigame_runs (
                        game_key, score, clicks, seconds, recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(game_key),
                        normalized_score,
                        normalized_clicks,
                        normalized_seconds,
                        recorded_at,
                    ),
                )
                recorded_run = {
                    "id": int(cur.lastrowid),
                    "score": normalized_score,
                    "clicks": normalized_clicks,
                    "seconds": normalized_seconds,
                    "recorded_at": recorded_at,
                }

            next_high_score = 0
            row = conn.execute(
                """
                SELECT score
                FROM login_minigame_runs
                WHERE game_key = ?
                ORDER BY score DESC, recorded_at DESC, id DESC
                LIMIT 1
                """,
                (str(game_key),),
            ).fetchone()
            if row:
                next_high_score = max(0, int(row[0] or 0))

            conn.execute(
                """
                INSERT INTO login_minigame_scores (game_key, high_score, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(game_key) DO UPDATE SET
                    high_score = excluded.high_score,
                    updated_at = excluded.updated_at
                """,
                (str(game_key), next_high_score, int(time.time())),
            )
            conn.commit()
            rows = conn.execute(
                """
                SELECT id, score, clicks, seconds, recorded_at
                FROM login_minigame_runs
                WHERE game_key = ?
                ORDER BY score DESC, recorded_at DESC, id DESC
                LIMIT 10
                """,
                (str(game_key),),
            ).fetchall()
            high_scores = [
                {
                    "id": int(row[0]),
                    "score": max(0, int(row[1] or 0)),
                    "clicks": max(0, int(row[2] or 0)),
                    "seconds": round(max(0.0, float(row[3] or 0.0)), 3),
                    "recorded_at": str(row[4] or ""),
                }
                for row in rows
            ]
            return {
                "high_score": next_high_score,
                "high_scores": high_scores,
                "recorded_run": recorded_run,
            }
        finally:
            conn.close()

# Suppress httpx HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)

# Suppress agent runtime tool schema warnings about default values
# These originate from google_adk.google.adk.tools._function_parameter_parse_util
# and are informational (defaults are ignored for Google AI function declarations).
logging.getLogger("google_adk.google.adk.tools._function_parameter_parse_util").setLevel(logging.ERROR)

# Global bind host for all services (Admin UI, AI Agent, Page Service)
# Controlled via environment variable `AUTOYOU_BIND_HOST`.
# Set to either "localhost" (or "127.0.0.1") to restrict to local machine,
# or "0.0.0.0" to allow LAN/remote access. Defaults to "127.0.0.1".
SERVER_BIND_HOST: str = os.getenv("AUTOYOU_BIND_HOST", "127.0.0.1")


def _normalize_config_bool(raw_value: Any, default: bool) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return bool(default)
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _normalize_server_bind_host(raw_value: Any) -> str:
    normalized = str(raw_value or "").strip().lower()
    if normalized in {"0.0.0.0", "::", "*", "all", "lan", "home", "home_network", "network"}:
        return "0.0.0.0"
    return "127.0.0.1"


def _configured_server_bind_host(cfg: Optional[Dict[str, Any]] = None) -> str:
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    return _normalize_server_bind_host(server_cfg.get("bind_host", "127.0.0.1"))


def _set_runtime_bind_host(host: Any) -> str:
    normalized = str(host or "127.0.0.1").strip() or "127.0.0.1"
    os.environ["AUTOYOU_BIND_HOST"] = normalized
    global SERVER_BIND_HOST
    SERVER_BIND_HOST = normalized
    return normalized


def _native_unlock_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    security = base_cfg.get("security", {}) if isinstance(base_cfg, dict) else {}
    return _normalize_config_bool(security.get("native_unlock_enabled"), True)


def _https_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    """Port for the opt-in TLS admin listener (separate from the plain-HTTP admin port)."""
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    try:
        port = int(server_cfg.get("https_port", 8443))
    except (TypeError, ValueError):
        port = 8443
    return port if 1024 <= port <= 65535 else 8443


def _bind_host_is_loopback(host: Any) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    if not normalized or normalized == "localhost":
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(normalized.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _native_owned_server_bound_beyond_loopback() -> bool:
    """The desktop app's "allow local network" switch is its owned server's home network setting."""
    return (
        os.getenv("AUTOYOU_NATIVE_OWNED_SERVER", "").strip() == "1"
        and not _bind_host_is_loopback(SERVER_BIND_HOST)
    )


def _home_network_access_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the operator chose to make this server reachable from the home network.

    That is the saved ``server.bind_host`` choice, or the native desktop app
    starting its owned server beyond loopback. Other launchers that force the
    bind host (Docker, ``--host``) own their network boundary and are not an
    opt-in here.
    """
    if _configured_server_bind_host(cfg) == "0.0.0.0":
        return True
    return _native_owned_server_bound_beyond_loopback()


#: How browsers on the home network reach website apps, in the same terms as a
#: website's own route. ``path_proxy`` keeps the websites port on loopback and
#: serves every website app under /agent/<name>/ on the admin port, behind the
#: admin sign-in; ``direct_forward`` also opens the websites port itself, where
#: browsers get the remote client role instead of signing in.
HOME_NETWORK_WEBSITES_MODES = ("path_proxy", "direct_forward")


def _home_network_websites_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Which home-network website route applies, defaulting to the safest one.

    An operator who turns the home network on gets ``path_proxy``. A launcher
    that forces the bind host (Docker, ``--host``) keeps the websites port on
    that bind, as before, unless the saved setting says otherwise.
    """
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    explicit = str(server_cfg.get("home_network_websites") or "").strip().lower()
    if explicit in HOME_NETWORK_WEBSITES_MODES:
        return explicit
    return "path_proxy" if _home_network_access_enabled(base_cfg) else "direct_forward"


def _page_service_bind_host(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Where the websites port listens: beyond loopback only in ``direct_forward`` mode."""
    if _bind_host_is_loopback(SERVER_BIND_HOST):
        return SERVER_BIND_HOST
    if _home_network_websites_mode(cfg) == "direct_forward":
        return SERVER_BIND_HOST
    return "127.0.0.1"


def _discovery_advertising_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether a home-network server announces itself to nearby AutoYou apps."""
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    return _normalize_config_bool(server_cfg.get("discovery_enabled"), True)


def _https_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether to run the additive opt-in HTTPS admin listener.

    The plain-HTTP listener always runs regardless, so enabling this never breaks
    already-paired clients. Home network access and Secure Professional Maximus
    turn HTTPS on by default (it can still be explicitly disabled via
    ``server.https_enabled = false``).
    """
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    explicit = server_cfg.get("https_enabled")
    if explicit is not None:
        return _normalize_config_bool(explicit, False)
    if _get_security_mode_from_cfg(base_cfg) == SECURE_PROFESSIONAL_MAXIMUS_MODE:
        return True
    return _home_network_access_enabled(base_cfg)


_PRIMARY_LAN_ADDRESS_CACHE: Dict[str, Any] = {"value": "", "expires": 0.0}


def _primary_lan_address() -> str:
    """This computer's outbound home-network IPv4 address, or ``""``.

    Selects the route only (nothing is sent) and never resolves DNS, so it is
    cheap enough for status payloads; the answer is cached briefly anyway.
    """
    now = time.monotonic()
    if now < _PRIMARY_LAN_ADDRESS_CACHE["expires"]:
        return _PRIMARY_LAN_ADDRESS_CACHE["value"]
    address = ""
    try:
        import ipaddress

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            candidate = probe.getsockname()[0]
        parsed = ipaddress.ip_address(candidate)
        if (parsed.is_private or parsed.is_link_local) and not parsed.is_loopback and not parsed.is_unspecified:
            address = str(parsed)
    except (OSError, ValueError):
        address = ""
    _PRIMARY_LAN_ADDRESS_CACHE.update({"value": address, "expires": now + 30.0})
    return address


def _tls_listener_serving(listener: Any) -> bool:
    # uvicorn sets ``started`` once bound; a listener that failed never does.
    return listener is not None and getattr(listener, "started", True) is not False


def _page_service_https_port_live() -> Optional[int]:
    service = get_autoyou_page_service() if AUTOYOU_PAGE_SERVICE_AVAILABLE else None
    if service is None or not _tls_listener_serving(getattr(service, "https_server", None)):
        return None
    try:
        port = int(getattr(service, "https_port", 0) or 0)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _home_network_web_status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Live description of how devices on the home network reach this server.

    Served to the admin UI and to connected clients so every surface reads the
    server's current web settings instead of re-deriving them.
    """
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    server_cfg = base_cfg.get("server", {}) if isinstance(base_cfg, dict) else {}
    live = not _bind_host_is_loopback(SERVER_BIND_HOST)
    admin_https_live = _tls_listener_serving(getattr(STATE, "https_admin_server", None))
    page_service = get_autoyou_page_service() if AUTOYOU_PAGE_SERVICE_AVAILABLE else None
    websites_port_open = bool(live and page_service is not None and not _bind_host_is_loopback(getattr(page_service, "host", "")))
    websites_mode_live = "direct_forward" if websites_port_open else "path_proxy"
    page_https_port = _page_service_https_port_live() if websites_port_open else None
    address = _primary_lan_address() if live else ""
    admin_urls: List[str] = []
    websites_urls: List[str] = []
    if address:
        # Plain-HTTP admin sign-in is refused from the network, so only an
        # HTTPS admin address is worth handing to another device - and in
        # path_proxy mode the website apps live behind that same sign-in.
        if admin_https_live:
            admin_urls.append(f"https://{address}:{_https_port(base_cfg)}/")
        if websites_port_open:
            if page_https_port:
                websites_urls.append(f"https://{address}:{page_https_port}/websites")
            else:
                websites_urls.append(f"http://{address}:{_get_autoyou_page_service_port(base_cfg)}/websites")
        elif admin_https_live:
            websites_urls.append(f"https://{address}:{_https_port(base_cfg)}/websites")
    secure_everywhere = admin_https_live and (page_https_port or not websites_port_open)
    return {
        "enabled": live,
        "next_boot_enabled": _configured_server_bind_host(base_cfg) == "0.0.0.0",
        "websites_mode": websites_mode_live,
        "websites_mode_next_boot": _home_network_websites_mode(base_cfg),
        "https": bool(live and (admin_https_live or page_https_port)),
        "https_next_boot": _https_enabled(base_cfg),
        "https_explicit": server_cfg.get("https_enabled") is not None,
        "plain_http_exposed": bool(live and not secure_everywhere),
        "discovery": bool(live and getattr(STATE, "server_advertisement", None) is not None),
        "discovery_enabled": _discovery_advertising_enabled(base_cfg),
        "lan_address": address,
        "admin_urls": admin_urls,
        "websites_urls": websites_urls,
        "ca_certificate_path": "/ca.crt" if (admin_https_live or page_https_port) else "",
        "remote_access_role": _get_remote_browser_access_role(base_cfg),
    }


def _ai_agent_lan_access_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Whether the AI Agent server (ADK runtime) may bind beyond loopback.

    Unlike the admin UI, the AI Agent server has no built-in authentication
    of its own -- it's the raw ADK dev-ui/API surface. Binding it to
    ``--host 0.0.0.0`` alongside the admin UI used to expose a fully
    unauthenticated agent-session API to the whole LAN. This must default to
    off regardless of ``server.bind_host``/``--host``; an operator opts in
    explicitly (admin UI toggle, ``ai_agent.lan_access_enabled`` config key,
    or ``AUTOYOU_AI_AGENT_LAN_ACCESS`` env for container/CI use where the
    surrounding network boundary -- e.g. Docker's own port publishing -- is
    already the real gate).
    """
    env_override = os.getenv("AUTOYOU_AI_AGENT_LAN_ACCESS")
    if env_override is not None:
        return _normalize_config_bool(env_override, False)
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    ai_agent_cfg = base_cfg.get("ai_agent", {}) if isinstance(base_cfg, dict) else {}
    return _normalize_config_bool(ai_agent_cfg.get("lan_access_enabled"), False)


def _configured_ai_agent_bind_host(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Bind host for the AI Agent server's plain listener.

    Loopback-only unless LAN access has been explicitly opted into (see
    ``_ai_agent_lan_access_enabled``), independent of the admin UI's own
    ``server.bind_host``/``--host``.
    """
    if not _ai_agent_lan_access_enabled(cfg):
        return "127.0.0.1"
    return SERVER_BIND_HOST


def _ai_agent_lan_https_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    """Port for the opt-in HTTPS+OTP AI Agent listener (LAN-reachable twin of 8081)."""
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    ai_agent_cfg = base_cfg.get("ai_agent", {}) if isinstance(base_cfg, dict) else {}
    try:
        port = int(ai_agent_cfg.get("lan_https_port", 8481))
    except (TypeError, ValueError):
        port = 8481
    return port if 1024 <= port <= 65535 else 8481

# ========= Config encryption helpers =========

# Ensure default ICE servers exist in config

def build_instance_runtime_status() -> Dict[str, Any]:
    """Describe the live instance identity for diagnostics and host coordination."""
    return {
        "name": SERVER_INSTANCE_NAME,
        "bind_host": SERVER_BIND_HOST,
        "connect_host": _normalize_probe_host(SERVER_BIND_HOST),
        "ports": {
            "admin": int(ADMIN_WEB_SERVICE_PORT),
            "ai_agent": int(AI_AGENT_SERVER_PORT),
            "auth": int(AUTH_SERVER_PORT),
            "page": _get_autoyou_page_service_port(STATE.config or {}),
        },
    }

def _normalize_totp_secret(raw_secret: Any) -> str:
    return "".join(
        ch for ch in str(raw_secret or "").strip().upper()
        if ch not in {" ", "\t", "\r", "\n", "-"}
    )

def _apply_default_security_config(cfg: Dict[str, Any]) -> bool:
    security = cfg.setdefault("security", {})
    changed = False
    normalized_mode = _normalize_security_mode(security.get("mode"), default="secure")
    if security.get("mode") != normalized_mode:
        security["mode"] = normalized_mode
        changed = True
    if str(security.get("tier") or "").strip().upper() not in ("A", "B"):
        security["tier"] = "B"
        changed = True

    pairing_secret = _normalize_totp_secret(security.get("totp_secret"))
    legacy_admin_secret = _normalize_totp_secret(security.get("admin_totp_secret"))

    legacy_clients = security.get("totp_clients") or {}
    normalized_legacy_clients: Dict[str, str] = {}
    if isinstance(legacy_clients, dict):
        for client_id, secret in sorted(legacy_clients.items()):
            normalized_secret = _normalize_totp_secret(secret)
            if isinstance(client_id, str) and normalized_secret:
                normalized_legacy_clients[client_id] = normalized_secret

    if not pairing_secret and normalized_legacy_clients:
        first_client_id, first_secret = next(iter(normalized_legacy_clients.items()))
        pairing_secret = first_secret
        changed = True
        distinct_legacy_secrets = sorted(set(normalized_legacy_clients.values()))
        if len(distinct_legacy_secrets) > 1:
            LOGGER.warning(
                "Migrating legacy security.totp_clients with %d distinct secrets; "
                "keeping %s as the new shared security.totp_secret and discarding the rest.",
                len(distinct_legacy_secrets),
                first_client_id,
            )

    if not pairing_secret and legacy_admin_secret:
        pairing_secret = legacy_admin_secret
        changed = True
    elif pairing_secret and legacy_admin_secret and legacy_admin_secret != pairing_secret:
        LOGGER.warning(
            "Discarding legacy security.admin_totp_secret because AutoYou now uses "
            "the shared security.totp_secret for secure professional pairing, "
            "authenticator-based public URL pairing, and admin-agent elevation."
        )

    if security.get("totp_secret", "") != pairing_secret:
        security["totp_secret"] = pairing_secret
        changed = True
    native_unlock_enabled = _normalize_config_bool(security.get("native_unlock_enabled"), True)
    if security.get("native_unlock_enabled") != native_unlock_enabled:
        security["native_unlock_enabled"] = native_unlock_enabled
        changed = True

    if "totp_clients" in security:
        security.pop("totp_clients", None)
        changed = True
    if "totp_client_metadata" in security:
        security.pop("totp_client_metadata", None)
        changed = True
    if "admin_totp_secret" in security:
        security.pop("admin_totp_secret", None)
        changed = True

    return changed

def _apply_default_tunnelmole_config(cfg: Dict[str, Any]) -> bool:
    tunnelmole = cfg.setdefault("tunnelmole", {})
    changed = False
    defaults = {
        "enabled": False,
        "timeout_minutes": 5,
        "otp_timeout_minutes": 5,
        "otp_multiuse": False,
        # Paid public-proxy entitlement auto-connect of the persistent public URL
        # on server startup. Visible to all, but only fires when entitled.
        "auto_start_on_boot": False,
        # Most-secure share: when on (secure_professional + authenticator pair-code
        # mode), /pair sends ONLY the public URL - clients derive SHA256(TOTP:password)
        # from the 2FA secret already saved in their Settings.
        "url_only_pair": False,
        "website_hosting": {
            "enabled": False,
            "agent_name": "",
        },
    }
    for key, value in defaults.items():
        if key not in tunnelmole:
            tunnelmole[key] = copy.deepcopy(value)
            changed = True
    if not isinstance(tunnelmole.get("website_hosting"), dict):
        tunnelmole["website_hosting"] = copy.deepcopy(defaults["website_hosting"])
        changed = True
    else:
        website_hosting = tunnelmole["website_hosting"]
        if "enabled" not in website_hosting:
            website_hosting["enabled"] = False
            changed = True
        if "agent_name" not in website_hosting:
            website_hosting["agent_name"] = ""
            changed = True

    legacy_totp_pair_mode = str(tunnelmole.get("totp_pair_mode") or "").strip().lower() not in {
        "",
        "0",
        "false",
        "no",
        "off",
    }
    desired_pair_code_mode = _normalize_tunnelmole_pair_code_mode(
        tunnelmole.get("pair_code_mode")
        if "pair_code_mode" in tunnelmole
        else (_TUNNELMOLE_PAIR_CODE_MODE_AUTHENTICATOR if legacy_totp_pair_mode else _TUNNELMOLE_PAIR_CODE_MODE_RANDOM_OTP)
    )
    desired_connection_mode = _normalize_tunnelmole_connection_mode(
        tunnelmole.get("connection_mode")
        if "connection_mode" in tunnelmole
        else (_TUNNELMOLE_CONNECTION_MODE_UNMANAGED if legacy_totp_pair_mode else _TUNNELMOLE_CONNECTION_MODE_TIMED)
    )

    if tunnelmole.get("pair_code_mode") != desired_pair_code_mode:
        tunnelmole["pair_code_mode"] = desired_pair_code_mode
        changed = True
    if tunnelmole.get("connection_mode") != desired_connection_mode:
        tunnelmole["connection_mode"] = desired_connection_mode
        changed = True

    if "totp_pair_mode" in tunnelmole:
        tunnelmole.pop("totp_pair_mode", None)
        changed = True
    if "device_name" in tunnelmole:
        tunnelmole.pop("device_name", None)
        changed = True

    return changed


_CLIENT_DISPLAY_NAME_MAX_LENGTH = 120
_CLIENT_IDENTITY_OWNER_KEY_MAX_LENGTH = 256
_CLIENT_IDENTITY_MAX_OVERRIDES = 500


def _normalize_client_display_name(value: Any) -> str:
    """Accept an optional client-provided display name without reformatting it."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("client_display_name must be a string.")
    if not value.strip():
        return ""
    if len(value) > _CLIENT_DISPLAY_NAME_MAX_LENGTH:
        raise ValueError(
            f"client_display_name must be {_CLIENT_DISPLAY_NAME_MAX_LENGTH} characters or fewer."
        )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("client_display_name cannot contain control characters.")
    return value


def _client_display_name_from_transport(value: Any) -> str:
    """Return a safe optional pairing name without ever failing the transport.

    Names are untrusted presentation metadata; an invalid value from a client
    payload degrades to the existing identifier-based behavior instead of
    aborting pairing or authentication.
    """
    try:
        return _normalize_client_display_name(value)
    except ValueError:
        return ""


def _normalize_client_identity_owner_key(value: Any) -> str:
    owner_key = str(value or "").strip()
    if not owner_key:
        raise ValueError("owner_key is required.")
    if len(owner_key) > _CLIENT_IDENTITY_OWNER_KEY_MAX_LENGTH:
        raise ValueError("owner_key is too long.")
    if ":" not in owner_key or any(ord(character) < 32 or ord(character) == 127 for character in owner_key):
        raise ValueError("owner_key is invalid.")
    # Guest WebRTC owners are derived from an ephemeral session ID, so they
    # must never become a display-name or override key.
    if owner_key.startswith("guest:"):
        raise ValueError("owner_key must identify a stable client.")
    return owner_key


def _client_name_history_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    identity_cfg = base_cfg.get("client_identity", {}) if isinstance(base_cfg, dict) else {}
    ai_agent_cfg = base_cfg.get("ai_agent", {}) if isinstance(base_cfg, dict) else {}
    return bool(
        _normalize_config_bool(
            identity_cfg.get("store_client_names_in_history") if isinstance(identity_cfg, dict) else None,
            False,
        )
        and _normalize_config_bool(
            ai_agent_cfg.get("record_messages_in_database") if isinstance(ai_agent_cfg, dict) else None,
            True,
        )
    )


def _configured_client_name_override(
    owner_key: Any,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    if not _client_name_history_enabled(cfg):
        return ""
    try:
        normalized_owner_key = _normalize_client_identity_owner_key(owner_key)
    except ValueError:
        return ""
    base_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    identity_cfg = base_cfg.get("client_identity", {}) if isinstance(base_cfg, dict) else {}
    overrides = identity_cfg.get("name_overrides", {}) if isinstance(identity_cfg, dict) else {}
    if not isinstance(overrides, dict):
        return ""
    try:
        return _normalize_client_display_name(overrides.get(normalized_owner_key))
    except ValueError:
        return ""


def _apply_default_client_identity_config(cfg: Dict[str, Any]) -> bool:
    raw_identity_cfg = cfg.get("client_identity")
    identity_cfg = raw_identity_cfg if isinstance(raw_identity_cfg, dict) else {}
    changed = raw_identity_cfg is not identity_cfg
    if raw_identity_cfg is not identity_cfg:
        cfg["client_identity"] = identity_cfg

    should_store = _normalize_config_bool(
        identity_cfg.get("store_client_names_in_history"),
        False,
    )
    if identity_cfg.get("store_client_names_in_history") is not should_store:
        identity_cfg["store_client_names_in_history"] = should_store
        changed = True

    raw_overrides = identity_cfg.get("name_overrides")
    normalized_overrides: Dict[str, str] = {}
    if isinstance(raw_overrides, dict):
        for raw_owner_key, raw_name in raw_overrides.items():
            if len(normalized_overrides) >= _CLIENT_IDENTITY_MAX_OVERRIDES:
                changed = True
                break
            try:
                owner_key = _normalize_client_identity_owner_key(raw_owner_key)
                client_name = _normalize_client_display_name(raw_name)
            except ValueError:
                changed = True
                continue
            if client_name:
                normalized_overrides[owner_key] = client_name
    elif raw_overrides is not None:
        changed = True

    # Incognito/recording-off mode never retains client names or overrides.
    if not _client_name_history_enabled(cfg):
        normalized_overrides = {}
    if raw_overrides != normalized_overrides:
        identity_cfg["name_overrides"] = normalized_overrides
        changed = True
    return changed


def _apply_default_agent_frontends_config(cfg: Dict[str, Any]) -> bool:
    frontends = cfg.setdefault("agent_frontends", {})
    if not isinstance(frontends, dict):
        cfg["agent_frontends"] = {"hosting_agent": True}
        return True
    changed = False
    if "streaming_agent" in frontends:
        if "education_agent" not in frontends:
            frontends["education_agent"] = frontends["streaming_agent"]
        frontends.pop("streaming_agent", None)
        changed = True
    if "hosting_agent" not in frontends:
        frontends["hosting_agent"] = True
        changed = True
    return changed


def _apply_default_bluetooth_pairing_config(cfg: Dict[str, Any]) -> bool:
    bluetooth_pairing = cfg.setdefault("bluetooth_pairing", {})
    changed = False
    defaults = {
        "enabled": False,
    }
    for key, value in defaults.items():
        if key not in bluetooth_pairing:
            bluetooth_pairing[key] = value
            changed = True
    return changed

def _apply_default_ice_servers(cfg: Dict[str, Any]) -> bool:
    changed = False
    rtc = cfg.setdefault("rtc", {})
    ice = rtc.get("iceServers")
    if not isinstance(ice, list) or len(ice) == 0:
        rtc["iceServers"] = list(DEFAULT_ICE_SERVERS)
        changed = True
    return changed

def _apply_default_speech_config(cfg: Dict[str, Any]) -> bool:
    normalized = normalize_speech_config(cfg.get("speech"))
    changed = cfg.get("speech") != normalized
    cfg["speech"] = normalized
    return changed

def _apply_default_onboarding_config(cfg: Dict[str, Any]) -> bool:
    onboarding = cfg.setdefault("onboarding", {})
    changed = False
    if "wizard_completed" not in onboarding:
        onboarding["wizard_completed"] = False
        changed = True
    if "wizard_completed_at" not in onboarding:
        onboarding["wizard_completed_at"] = ""
        changed = True
    return changed

def _apply_default_cloud_config(cfg: Dict[str, Any]) -> bool:
    """Ensure the cloud config section exists in the config dict."""
    cloud = cfg.setdefault("cloud", {})
    changed = False
    defaults = {
        "server_token": "",
        "server_id": "",
        "user_id": "",
        "email": "",
        "registered_at": "",
        "token_issued_at": "",
    }
    for key, val in defaults.items():
        if key not in cloud:
            cloud[key] = val
            changed = True
    return changed

def _generate_server_installation_id() -> str:
    return uuid.uuid4().hex

def _default_server_display_name() -> str:
    """First-run display name. A host that owns this server may name it after
    the computer people recognise; every existing configuration keeps its own
    stored name, and the legacy default stands when no host supplies one."""
    name = str(os.getenv("AUTOYOU_SERVER_NAME") or "").strip()
    if not name or len(name) > 64 or any(character in name for character in "\x00\r\n"):
        return "AutoYou-Server"
    return name

def _apply_default_server_identity_config(cfg: Dict[str, Any]) -> bool:
    server = cfg.setdefault("server", {})
    changed = False
    if not str(server.get("name") or "").strip():
        server["name"] = _default_server_display_name()
        changed = True
    if not str(server.get("installation_id") or "").strip():
        server["installation_id"] = _generate_server_installation_id()
        changed = True
    bind_host = _normalize_server_bind_host(server.get("bind_host", "127.0.0.1"))
    if server.get("bind_host") != bind_host:
        server["bind_host"] = bind_host
        changed = True
    return changed

def _apply_default_autoyou_page_config(cfg: Dict[str, Any]) -> bool:
    autoyou_page = cfg.setdefault("autoyou_page", {})
    changed = False
    defaults = {
        "port": 8067,
        "auto_start": True,
        "timeline_days": 7,
        "feed_window_days": 0,
        "custom_forward_enabled": False,
        "custom_forward_port": 8067,
        "advertised_websites": [],
        "bookmarks": [],
        "theme": "light",
        "remote_access_role": REMOTE_ACCESS_VIEWER,
    }
    for key, value in defaults.items():
        if key not in autoyou_page:
            autoyou_page[key] = value
            changed = True
    raw_advertised_websites = autoyou_page.get("advertised_websites")
    normalized_websites = _normalize_advertised_website_entries(raw_advertised_websites)
    if autoyou_page.get("advertised_websites") != normalized_websites:
        autoyou_page["advertised_websites"] = normalized_websites
        changed = True
    raw_bookmarks = autoyou_page.get("bookmarks")
    normalized_bookmarks = _normalize_bookmark_entries(raw_bookmarks)
    if autoyou_page.get("bookmarks") != normalized_bookmarks:
        autoyou_page["bookmarks"] = normalized_bookmarks
        changed = True
    return changed

def _normalize_partner_enabled_flag(raw_value: Any, default: bool) -> bool:
    if raw_value is None:
        return bool(default)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, (int, float)):
        return bool(raw_value)
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(default)

def _coerce_partner_port(raw_value: Any, default: int) -> int:
    try:
        port = int(str(raw_value).strip())
        if 1 <= port <= 65535:
            return port
    except Exception:
        pass
    return int(default)

def _normalize_video_call_config(raw_value: Any) -> Dict[str, Any]:
    raw_cfg = raw_value if isinstance(raw_value, dict) else {}
    raw_remote = raw_cfg.get("remote_desktop")
    remote_cfg = raw_remote if isinstance(raw_remote, dict) else {}
    raw_video_file = raw_cfg.get("video_file")
    video_file_cfg = raw_video_file if isinstance(raw_video_file, dict) else {}
    outbound_source = _normalize_video_outbound_source(raw_cfg.get("outbound_source"), "remote_desktop")
    outbound_sources = _normalize_video_outbound_sources(
        raw_cfg.get("outbound_sources"),
        fallback_source=outbound_source,
    )
    if outbound_sources == ["remote_desktop"] and outbound_source != "remote_desktop":
        outbound_sources = [outbound_source]
    input_audio_source = str(raw_cfg.get("input_audio_source") or "default").strip() or "default"
    capture_audio = _normalize_partner_enabled_flag(raw_cfg.get("capture_audio"), False)
    audio_sources = _normalize_video_audio_sources(
        raw_cfg.get("audio_sources"),
        capture_audio=capture_audio,
        input_audio_source=input_audio_source,
        outbound_source=outbound_source,
    )
    if not audio_sources and capture_audio:
        audio_sources = _normalize_video_audio_sources(
            None,
            capture_audio=True,
            input_audio_source=input_audio_source,
            outbound_source=outbound_source,
        )
    capture_audio = bool(audio_sources)
    return {
        "enabled": _normalize_partner_enabled_flag(raw_cfg.get("enabled"), True),
        "audio_enabled": _normalize_partner_enabled_flag(raw_cfg.get("audio_enabled"), True),
        "disable_autoyou_agents": _normalize_partner_enabled_flag(raw_cfg.get("disable_autoyou_agents"), False),
        "ai_audio_replies_enabled": _normalize_partner_enabled_flag(raw_cfg.get("ai_audio_replies_enabled"), True),
        "record_my_video": _normalize_partner_enabled_flag(raw_cfg.get("record_my_video"), False),
        "recording_dir": str(raw_cfg.get("recording_dir") or "").strip(),
        "recording_mode": normalize_inbound_video_recording_mode(raw_cfg.get("recording_mode"), "video"),
        "image_interval_seconds": normalize_inbound_video_image_interval_seconds(
            raw_cfg.get("image_interval_seconds"),
            DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
        ),
        "background_mode_enabled": _normalize_partner_enabled_flag(raw_cfg.get("background_mode_enabled"), False),
        "silent_recording_enabled": _normalize_partner_enabled_flag(raw_cfg.get("silent_recording_enabled"), False),
        "location_recording_enabled": _normalize_partner_enabled_flag(raw_cfg.get("location_recording_enabled"), False),
        "wuift_enabled": _normalize_partner_enabled_flag(raw_cfg.get("wuift_enabled"), True),
        "silent_recording_dir": str(raw_cfg.get("silent_recording_dir") or "").strip(),
        "silent_recording_batch_seconds": normalize_audio_recording_batch_seconds(
            raw_cfg.get("silent_recording_batch_seconds"),
            DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
        ),
        "outbound_source": outbound_source,
        "outbound_sources": outbound_sources,
        "api_video_source_id": str(raw_cfg.get("api_video_source_id") or "default").strip() or "default",
        "capture_audio": capture_audio,
        "audio_sources": audio_sources,
        "input_audio_source": input_audio_source,
        "camera_device_id": int(raw_cfg.get("camera_device_id") if raw_cfg.get("camera_device_id") is not None else 0),
        "video_file": {
            "path": str(video_file_cfg.get("path", raw_cfg.get("video_file_path", "")) or "").strip(),
            "loop": _normalize_partner_enabled_flag(video_file_cfg.get("loop", raw_cfg.get("video_file_loop")), True),
        },
        "remote_desktop": {
            "enabled": _normalize_partner_enabled_flag(
                remote_cfg.get("enabled", raw_cfg.get("remote_desktop_enabled")),
                True,
            ),
            "send_screen": _normalize_partner_enabled_flag(
                remote_cfg.get("send_screen", remote_cfg.get("send_desktop", raw_cfg.get("remote_desktop_send_screen"))),
                True,
            ),
            "monitor_id": _normalize_remote_desktop_monitor_id(
                remote_cfg.get("monitor_id", remote_cfg.get("monitor", raw_cfg.get("remote_desktop_monitor_id")))
            ),
            "quality": normalize_remote_desktop_quality(
                remote_cfg.get("quality", raw_cfg.get("remote_desktop_quality"))
            ),
            "bitrate_kbps": normalize_remote_desktop_bitrate_kbps(
                remote_cfg.get("bitrate_kbps", raw_cfg.get("remote_desktop_bitrate_kbps"))
            ),
            "control_enabled": _normalize_partner_enabled_flag(
                remote_cfg.get("control_enabled", raw_cfg.get("remote_desktop_control_enabled")),
                False,
            ),
        },
    }

def _apply_default_video_call_config(cfg: Dict[str, Any]) -> bool:
    normalized = _normalize_video_call_config(cfg.get("video_call"))
    changed = cfg.get("video_call") != normalized
    cfg["video_call"] = normalized
    return changed

def _normalize_remote_desktop_monitor_id(raw_value: Any, default: int = 0) -> int:
    return normalize_remote_desktop_monitor_id(raw_value, default)

def _normalize_video_outbound_source(raw_value: Any, default: str = "remote_desktop") -> str:
    normalized = str(raw_value or default or "").strip().lower().replace("-", "_")
    aliases = {
        "desktop": "remote_desktop",
        "screen": "remote_desktop",
        "realtime": "api",
        "realtime_api": "api",
        "api_realtime": "api",
        "video": "video_file",
        "file": "video_file",
        "video_file": "video_file",
        "webcam": "camera",
        "web_cam": "camera",
        "cam": "camera",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"remote_desktop", "api", "video_file", "camera"}:
        return default
    return normalized

def _split_source_list(raw_value: Any) -> List[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, (list, tuple, set)):
        return [str(item or "").strip() for item in raw_value]
    return [part.strip() for part in str(raw_value or "").replace(";", ",").split(",")]

def _normalize_video_outbound_sources(raw_value: Any, *, fallback_source: str = "remote_desktop") -> List[str]:
    aliases = {
        "desktop": "remote_desktop",
        "screen": "remote_desktop",
        "webcam": "camera",
        "web_cam": "camera",
        "cam": "camera",
        "realtime": "api",
        "realtime_api": "api",
        "api_realtime": "api",
        "file": "video_file",
        "video": "video_file",
    }
    selected: List[str] = []
    for item in _split_source_list(raw_value):
        normalized = aliases.get(item.strip().lower().replace("-", "_"), item.strip().lower().replace("-", "_"))
        if normalized in {"remote_desktop", "camera", "api", "video_file"} and normalized not in selected:
            selected.append(normalized)
    if raw_value is None:
        fallback = _normalize_video_outbound_source(fallback_source, "remote_desktop")
        return [fallback] if fallback else []
    return selected

def _normalize_video_audio_sources(
    raw_value: Any,
    *,
    capture_audio: bool = False,
    input_audio_source: str = "default",
    outbound_source: str = "remote_desktop",
) -> List[str]:
    aliases = {
        "mic": "microphone",
        "microphone": "microphone",
        "server_microphone": "microphone",
        "input": "microphone",
        "speaker": "speaker_loopback",
        "speakers": "speaker_loopback",
        "speaker_loopback": "speaker_loopback",
        "desktop_loopback": "speaker_loopback",
        "desktop_audio": "speaker_loopback",
        "computer_audio": "speaker_loopback",
        "system_audio": "speaker_loopback",
        "loopback": "speaker_loopback",
    }
    selected: List[str] = []
    for item in _split_source_list(raw_value):
        normalized = aliases.get(item.strip().lower().replace("-", "_"), "")
        if normalized and normalized not in selected:
            selected.append(normalized)
    if raw_value is None and capture_audio:
        if input_audio_source == "desktop_loopback" or (outbound_source == "remote_desktop" and input_audio_source == "default"):
            return ["speaker_loopback"]
        return ["microphone"]
    return selected


def _normalize_mcp_adapter_url(raw_value: Any) -> str:
    """Normalize the separate MCP adapter URL without accepting embedded secrets."""
    candidate = str(raw_value or "").strip().rstrip("/")
    if not candidate:
        return "http://127.0.0.1:8071"
    parsed = urlsplit(candidate)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("mcp.adapter_url must be an absolute HTTP(S) URL without credentials or query data.")
    return candidate


def _mcp_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    effective = cfg if isinstance(cfg, dict) else (STATE.config if isinstance(STATE.config, dict) else {})
    section = effective.get("mcp") if isinstance(effective.get("mcp"), dict) else {}
    return section


def _mcp_api_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    env_override = os.environ.get("AUTOYOU_ENABLE_MCP_PARTNER")
    if env_override is not None:
        return str(env_override).strip().lower() in {"1", "true", "yes", "on"}
    return _normalize_partner_enabled_flag(_mcp_config(cfg).get("enabled"), True)


def _mcp_api_token(cfg: Optional[Dict[str, Any]] = None) -> str:
    configured = str(_mcp_config(cfg).get("api_token") or "").strip()
    return configured or str(os.environ.get(MCP_API_TOKEN_ENV) or "").strip()


def _mcp_request_uses_api_token(request: "Request") -> bool:
    expected = _mcp_api_token()
    if not expected:
        return False
    authorization = str(request.headers.get("authorization") or "").strip()
    bearer = ""
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            bearer = value.strip()
    header_token = str(request.headers.get("x-autoyou-mcp-token") or "").strip()
    if bearer and header_token and not secrets.compare_digest(bearer, header_token):
        return False
    supplied = bearer or header_token
    return bool(supplied and secrets.compare_digest(supplied, expected))


def _require_mcp_api_auth(request: "Request") -> Optional[JSONResponse]:
    """Authorize the full server's narrow MCP façade.

    A configured token is required for every non-loopback deployment.  An
    unconfigured token remains useful for local development only when the
    server is bound to loopback and the caller is also loopback.
    """
    if not _mcp_api_enabled():
        return JSONResponse(
            status_code=403,
            content={
                "success": False,
                "feature_enabled": False,
                "partner": "mcp",
                "error": "AutoYou MCP is disabled in server settings.",
            },
        )

    expected = _mcp_api_token()
    if expected:
        if not _mcp_request_uses_api_token(request):
            response = JSONResponse(
                status_code=401,
                content={"success": False, "error": "MCP API token required."},
            )
            response.headers["WWW-Authenticate"] = 'Bearer realm="autoyou-mcp"'
            return response
        return None

    # Use the effective process bind, not only the saved next-boot setting.
    # The launcher can intentionally override the saved value with
    # AUTOYOU_BIND_HOST/--host; a 0.0.0.0 process must never inherit the
    # loopback-only no-token exception from an older saved configuration.
    bound_host = _normalize_server_bind_host(SERVER_BIND_HOST)
    peer_host = getattr(getattr(request, "client", None), "host", None)
    if _is_loopback_client_host(peer_host) and _is_loopback_client_host(bound_host):
        return None
    return JSONResponse(
        status_code=503,
        content={
            "success": False,
            "error": "MCP API token is required for non-loopback access.",
        },
    )


def _mcp_runtime_status(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    section = _mcp_config(cfg)
    adapter_url = str(section.get("adapter_url") or "http://127.0.0.1:8071").rstrip("/")
    return {
        "enabled": _mcp_api_enabled(cfg),
        "configured": bool(_mcp_api_token(cfg)),
        "adapter_url": adapter_url,
        "server_endpoint": f"http://127.0.0.1:{int(ADMIN_WEB_SERVICE_PORT)}/api/v1/mcp",
        "transport": "authenticated-rest-facade",
        "tool_profile": "full",
    }

def _apply_default_messaging_partner_config(cfg: Dict[str, Any]) -> bool:
    """Normalize messaging-partner config keys used by modern and legacy admin paths."""
    changed = False

    mcp_cfg = cfg.setdefault("mcp", {})
    mcp_defaults = {
        "enabled": True,
        "api_token": "",
        "adapter_url": "http://127.0.0.1:8071",
    }
    for key, value in mcp_defaults.items():
        if key not in mcp_cfg:
            mcp_cfg[key] = value
            changed = True
    normalized_mcp_enabled = _normalize_partner_enabled_flag(mcp_cfg.get("enabled"), True)
    if mcp_cfg.get("enabled") != normalized_mcp_enabled:
        mcp_cfg["enabled"] = normalized_mcp_enabled
        changed = True
    normalized_mcp_token = str(mcp_cfg.get("api_token") or "").strip()
    if mcp_cfg.get("api_token") != normalized_mcp_token:
        mcp_cfg["api_token"] = normalized_mcp_token
        changed = True
    try:
        normalized_mcp_adapter_url = _normalize_mcp_adapter_url(mcp_cfg.get("adapter_url"))
    except ValueError:
        normalized_mcp_adapter_url = "http://127.0.0.1:8071"
    if mcp_cfg.get("adapter_url") != normalized_mcp_adapter_url:
        mcp_cfg["adapter_url"] = normalized_mcp_adapter_url
        changed = True

    signal_cfg = cfg.setdefault("signal", {})
    signal_defaults = {
        "enabled": False,
        "port": 8082,
        "device_name": "AutoYou-Signal",
        "phone_number": "",
        "paired": False,
        "shutdown_docker_on_exit": True,
    }
    for key, value in signal_defaults.items():
        if key not in signal_cfg:
            signal_cfg[key] = value
            changed = True
    normalized_signal_enabled = _normalize_partner_enabled_flag(signal_cfg.get("enabled"), False)
    if signal_cfg.get("enabled") != normalized_signal_enabled:
        signal_cfg["enabled"] = normalized_signal_enabled
        changed = True
    normalized_signal_port = _coerce_partner_port(signal_cfg.get("port"), 8082)
    if signal_cfg.get("port") != normalized_signal_port:
        signal_cfg["port"] = normalized_signal_port
        changed = True
    normalized_signal_shutdown = _normalize_partner_enabled_flag(
        signal_cfg.get("shutdown_docker_on_exit"),
        True,
    )
    if signal_cfg.get("shutdown_docker_on_exit") != normalized_signal_shutdown:
        signal_cfg["shutdown_docker_on_exit"] = normalized_signal_shutdown
        changed = True

    whatsapp_cfg = cfg.setdefault("whatsapp", {})
    whatsapp_defaults = {
        "enabled": False,
        "port": 8083,
        "websocket_port": 8083,
        "device_name": "AutoYou-WhatsApp",
        "phone_number": "",
        "paired": False,
        "shutdown_on_exit": True,
    }
    for key, value in whatsapp_defaults.items():
        if key not in whatsapp_cfg:
            whatsapp_cfg[key] = value
            changed = True
    normalized_whatsapp_enabled = _normalize_partner_enabled_flag(whatsapp_cfg.get("enabled"), False)
    if whatsapp_cfg.get("enabled") != normalized_whatsapp_enabled:
        whatsapp_cfg["enabled"] = normalized_whatsapp_enabled
        changed = True
    websocket_port = _coerce_partner_port(
        whatsapp_cfg.get("websocket_port", whatsapp_cfg.get("port")),
        8083,
    )
    if whatsapp_cfg.get("websocket_port") != websocket_port:
        whatsapp_cfg["websocket_port"] = websocket_port
        changed = True
    if whatsapp_cfg.get("port") in (None, ""):
        whatsapp_cfg["port"] = websocket_port
        changed = True
    else:
        normalized_port = _coerce_partner_port(whatsapp_cfg.get("port"), websocket_port)
        if whatsapp_cfg.get("port") != normalized_port:
            whatsapp_cfg["port"] = normalized_port
            changed = True
    normalized_whatsapp_shutdown = _normalize_partner_enabled_flag(whatsapp_cfg.get("shutdown_on_exit"), True)
    if whatsapp_cfg.get("shutdown_on_exit") != normalized_whatsapp_shutdown:
        whatsapp_cfg["shutdown_on_exit"] = normalized_whatsapp_shutdown
        changed = True

    telegram_user_cfg = cfg.setdefault("telegram_user", {})
    telegram_user_defaults = {
        "enabled": False,
        "api_id": 0,
        "api_hash": "",
        "session": "",
        "training_export_consent": False,
        "training_export_consent_at": "",
        "prompt_builder": {
            "enabled": False,
            "application_agent": "codex_desktop_agent",
        },
    }
    for key, value in telegram_user_defaults.items():
        if key not in telegram_user_cfg:
            telegram_user_cfg[key] = value
            changed = True
    normalized_telegram_user_enabled = _normalize_partner_enabled_flag(
        telegram_user_cfg.get("enabled"),
        False,
    )
    if telegram_user_cfg.get("enabled") != normalized_telegram_user_enabled:
        telegram_user_cfg["enabled"] = normalized_telegram_user_enabled
        changed = True
    try:
        normalized_api_id = max(0, int(str(telegram_user_cfg.get("api_id") or "0").strip()))
    except Exception:
        normalized_api_id = 0
    if telegram_user_cfg.get("api_id") != normalized_api_id:
        telegram_user_cfg["api_id"] = normalized_api_id
        changed = True
    for key in ("api_hash", "session"):
        normalized_value = str(telegram_user_cfg.get(key) or "").strip()
        if telegram_user_cfg.get(key) != normalized_value:
            telegram_user_cfg[key] = normalized_value
            changed = True
    normalized_export_consent = _normalize_partner_enabled_flag(
        telegram_user_cfg.get("training_export_consent"),
        False,
    )
    if telegram_user_cfg.get("training_export_consent") != normalized_export_consent:
        telegram_user_cfg["training_export_consent"] = normalized_export_consent
        changed = True

    prompt_builder_cfg = telegram_user_cfg.setdefault("prompt_builder", {})
    if not isinstance(prompt_builder_cfg, dict):
        prompt_builder_cfg = {}
        telegram_user_cfg["prompt_builder"] = prompt_builder_cfg
        changed = True
    if "enabled" not in prompt_builder_cfg:
        prompt_builder_cfg["enabled"] = False
        changed = True
    normalized_prompt_builder_enabled = _normalize_partner_enabled_flag(
        prompt_builder_cfg.get("enabled"),
        False,
    )
    if prompt_builder_cfg.get("enabled") != normalized_prompt_builder_enabled:
        prompt_builder_cfg["enabled"] = normalized_prompt_builder_enabled
        changed = True
    normalized_prompt_builder_agent = str(
        prompt_builder_cfg.get("application_agent") or "codex_desktop_agent"
    ).strip().lower().replace("-", "_")
    if normalized_prompt_builder_agent in {"codex", "codex_desktop"}:
        normalized_prompt_builder_agent = "codex_desktop_agent"
    elif normalized_prompt_builder_agent in {"claude", "claude_desktop"}:
        normalized_prompt_builder_agent = "claude_desktop_agent"
    if normalized_prompt_builder_agent not in {"codex_desktop_agent", "claude_desktop_agent"}:
        normalized_prompt_builder_agent = "codex_desktop_agent"
    if prompt_builder_cfg.get("application_agent") != normalized_prompt_builder_agent:
        prompt_builder_cfg["application_agent"] = normalized_prompt_builder_agent
        changed = True

    return changed

def _speech_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base_cfg = cfg if cfg is not None else (STATE.config or {})
    normalized = normalize_speech_config(base_cfg.get("speech"))
    if isinstance(base_cfg, dict):
        base_cfg["speech"] = normalized
    return normalized

def _speech_summary(cfg: Optional[Dict[str, Any]] = None) -> str:
    speech_cfg = _speech_config(cfg)
    tts_cfg = speech_cfg["tts"]
    stt_cfg = speech_cfg["stt"]
    provider = tts_cfg["provider"]

    if provider == "system":
        selected_voice = tts_cfg.get("system_voice") or "Default system voice"
        voices = list_system_tts_voices() if callable(list_system_tts_voices) else []
        selected_name = next(
            (
                voice.get("name")
                for voice in voices
                if voice.get("id") == selected_voice
            ),
            None,
        )
        tts_summary = f"System TTS ({selected_name or selected_voice})"
    elif provider == "openai":
        openai_cfg = tts_cfg["openai"]
        tts_summary = f"OpenAI TTS ({openai_cfg.get('model')}, voice {openai_cfg.get('voice')})"
    elif provider == "azure":
        azure_cfg = tts_cfg["azure"]
        tts_summary = f"Azure TTS ({azure_cfg.get('voice') or 'voice not set'})"
    elif provider == CUSTOM_VOICE_PROVIDER:
        readiness = "ready" if custom_voice_model_ready() else "model not prepared"
        tts_summary = f"Custom cloned voice TTS ({readiness})"
    elif provider == "emotivoice":
        from shared.emotivoice_tts import status as emotivoice_status

        readiness = "ready" if emotivoice_status()["ready"] else "models or dependencies not installed"
        tts_summary = f"EmotiVoice local TTS ({readiness})"
    elif provider == "off":
        tts_summary = "TTS disabled"
    else:
        tts_summary = f"Unknown TTS provider ({provider})"

    stt_summary = (
        f"RealtimeSTT ({stt_cfg.get('model')}, lang {stt_cfg.get('language')}, "
        f"device {stt_cfg.get('device')}, compute {stt_cfg.get('compute_type')})"
    )
    return f"TTS: {tts_summary}. STT: {stt_summary}."

def _apply_speech_config_to_active_audio_managers() -> None:
    for session_id, audio_manager in list(STATE.audio_managers.items()):
        try:
            if hasattr(audio_manager, "reload_settings"):
                audio_manager.reload_settings()
                LOGGER.info("Applied updated speech settings to active session %s", session_id)
        except Exception as exc:
            LOGGER.warning("Failed to apply speech settings to active session %s: %s", session_id, exc)

def _parse_bounded_float(raw_value: Optional[str], field_name: str, minimum: float, maximum: float) -> float:
    try:
        value = float((raw_value or "").strip())
    except Exception:
        raise ValueError(f"{field_name} must be a number")
    if value < minimum or value > maximum:
        raise ValueError(f"{field_name} must be between {minimum} and {maximum}")
    return value

def _update_speech_config(
    cfg: Dict[str, Any],
    *,
    speech_tts_provider: Optional[str] = None,
    speech_tts_rate: Optional[str] = None,
    speech_tts_system_voice: Optional[str] = None,
    speech_openai_tts_model: Optional[str] = None,
    speech_openai_tts_voice: Optional[str] = None,
    speech_openai_tts_instructions: Optional[str] = None,
    speech_openai_base_url: Optional[str] = None,
    speech_openai_api_key: Optional[str] = None,
    speech_azure_key: Optional[str] = None,
    speech_azure_region: Optional[str] = None,
    speech_azure_voice: Optional[str] = None,
    speech_azure_endpoint_id: Optional[str] = None,
    speech_stt_model: Optional[str] = None,
    speech_stt_language: Optional[str] = None,
    speech_stt_device: Optional[str] = None,
    speech_stt_compute_type: Optional[str] = None,
    speech_stt_silero_sensitivity: Optional[str] = None,
    speech_stt_post_speech_silence_duration: Optional[str] = None,
) -> Dict[str, Any]:
    speech_cfg = _speech_config(cfg)
    tts_cfg = speech_cfg["tts"]
    stt_cfg = speech_cfg["stt"]

    if speech_tts_provider is not None:
        provider = speech_tts_provider.strip().lower()
        if provider not in {"system", "openai", "azure", CUSTOM_VOICE_PROVIDER, "emotivoice", "off"}:
            raise ValueError("TTS provider must be one of: system, custom, openai, azure, emotivoice, off")
        tts_cfg["provider"] = provider

    if speech_tts_rate is not None:
        tts_cfg["rate"] = _parse_bounded_float(speech_tts_rate, "TTS rate", 0.25, 4.0)

    if speech_tts_system_voice is not None:
        tts_cfg["system_voice"] = speech_tts_system_voice.strip()

    openai_cfg = tts_cfg["openai"]
    if speech_openai_tts_model is not None:
        model = speech_openai_tts_model.strip()
        if not model:
            raise ValueError("OpenAI TTS model cannot be empty")
        openai_cfg["model"] = model
    if speech_openai_tts_voice is not None:
        voice = speech_openai_tts_voice.strip()
        if not voice:
            raise ValueError("OpenAI TTS voice cannot be empty")
        openai_cfg["voice"] = voice
    if speech_openai_tts_instructions is not None:
        openai_cfg["instructions"] = speech_openai_tts_instructions.strip()
    if speech_openai_base_url is not None:
        base_url = speech_openai_base_url.strip()
        openai_cfg["base_url"] = base_url or openai_cfg["base_url"]
    if speech_openai_api_key is not None:
        api_key = speech_openai_api_key.strip()
        if api_key and api_key != MASKED_SECRET_PLACEHOLDER:
            openai_cfg["api_key"] = api_key
        elif not api_key:
            openai_cfg["api_key"] = ""

    azure_cfg = tts_cfg["azure"]
    if speech_azure_key is not None:
        azure_key = speech_azure_key.strip()
        if azure_key and azure_key != MASKED_SECRET_PLACEHOLDER:
            azure_cfg["speech_key"] = azure_key
        elif not azure_key:
            azure_cfg["speech_key"] = ""
    if speech_azure_region is not None:
        azure_cfg["speech_region"] = speech_azure_region.strip()
    if speech_azure_voice is not None:
        azure_cfg["voice"] = speech_azure_voice.strip()
    if speech_azure_endpoint_id is not None:
        azure_cfg["endpoint_id"] = speech_azure_endpoint_id.strip()

    if speech_stt_model is not None:
        model = speech_stt_model.strip()
        if not model:
            raise ValueError("STT model cannot be empty")
        stt_cfg["model"] = model
    if speech_stt_language is not None:
        language = speech_stt_language.strip()
        stt_cfg["language"] = language
    if speech_stt_device is not None:
        device = speech_stt_device.strip().lower()
        if not device:
            raise ValueError("STT device cannot be empty")
        stt_cfg["device"] = device
    if speech_stt_compute_type is not None:
        compute_type = speech_stt_compute_type.strip()
        if not compute_type:
            raise ValueError("STT compute type cannot be empty")
        stt_cfg["compute_type"] = compute_type
    if speech_stt_silero_sensitivity is not None:
        stt_cfg["silero_sensitivity"] = _parse_bounded_float(
            speech_stt_silero_sensitivity,
            "STT VAD sensitivity",
            0.0,
            1.0,
        )
    if speech_stt_post_speech_silence_duration is not None:
        stt_cfg["post_speech_silence_duration"] = _parse_bounded_float(
            speech_stt_post_speech_silence_duration,
            "STT post speech silence duration",
            0.1,
            5.0,
        )

    cfg["speech"] = normalize_speech_config(speech_cfg)
    return cfg["speech"]

def get_server_password() -> Optional[str]:
    """Return the real server password used for pairing and security flows."""
    password = str(STATE.server_password or "").strip()
    return password or None

def get_current_password() -> Optional[str]:
    """Compatibility alias for shared pairing-router callbacks."""
    return get_server_password()

def _server_generate_hash(s: str) -> str:
    """Generate a SHA-256 hex digest for the provided string.

    Uses `crypt.aead.generate_hash` when available to match client behavior,
    otherwise falls back to Python's hashlib.
    """
    return generate_hash(s)

_SECURITY_MODE_ALIASES = {
    "normal": "normal",
    "secure": "secure",
    "secure_professional": "secure_professional",
    "secure-professional": "secure_professional",
    SECURE_PROFESSIONAL_MAXIMUS_MODE: SECURE_PROFESSIONAL_MAXIMUS_MODE,
    "secure-professional-maximus": SECURE_PROFESSIONAL_MAXIMUS_MODE,
}


def _normalize_security_mode(value: Any, *, default: str = "secure") -> str:
    normalized = str(value or "").strip().lower()
    return _SECURITY_MODE_ALIASES.get(normalized, default)


def _is_secure_professional_mode(value: Any) -> bool:
    return _normalize_security_mode(value) in {
        "secure_professional",
        SECURE_PROFESSIONAL_MAXIMUS_MODE,
    }


def get_security_mode() -> str:
    """Return current security mode from configuration.

    Values: "normal", "secure", "secure_professional", or
    "secure_professional_maximus".
    Defaults to "secure" if not set or config unavailable.
    """
    try:
        cfg = STATE.config or {}
        sec = cfg.get("security", {})
        return _normalize_security_mode(sec.get("mode", "secure"))
    except Exception:
        pass
    return "secure"

def _get_security_mode_from_cfg(cfg: Optional[Dict[str, Any]]) -> str:
    try:
        sec = (cfg or {}).get("security", {})
        return _normalize_security_mode(sec.get("mode") or "secure")
    except Exception:
        pass
    return "secure"


def _secure_storage_scan_roots() -> List[Path]:
    roots: List[Path] = [get_mutable_data_dir("AutoYou", anchor=__file__)]
    try:
        from shared.voice_training_storage import get_voice_training_dir

        roots.append(get_voice_training_dir())
    except Exception:
        pass
    return roots


def _log_secure_storage_recovery_outcome() -> None:
    """Tell the operator, in the log, what the sweep did and what to do next.

    Long-lived services keep handles on the stores that were just swapped back to
    plaintext, and on Windows an open handle also blocks the swap outright. A
    clean shutdown and restart is the only reliable way to land the change, so
    say so rather than leaving a half-reopened runtime to fail obscurely.
    """
    report = STATE.secure_storage_recovery or {}
    recovered = int(report.get("recovered") or 0)
    unrecoverable = list(report.get("unrecoverable") or [])
    if recovered:
        LOGGER.warning(
            "Recovered %d protected data store(s) that stayed sealed after Secure Professional "
            "Maximus was turned off. Shut down AutoYou completely and start it again so every "
            "service reopens these stores.",
            recovered,
        )
    if unrecoverable:
        LOGGER.error(
            "%d protected data store(s) remain sealed and their Secure Professional Maximus key no "
            "longer exists, so their contents cannot be recovered: %s. Move these files aside to "
            "start fresh, then shut down and restart AutoYou.",
            len(unrecoverable),
            ", ".join(os.path.basename(path) for path in unrecoverable[:12]),
        )


def _secure_storage_recovery_status() -> Dict[str, Any]:
    """Summarise the last stranded-envelope sweep for the admin UI.

    Two outcomes need the operator's attention. Recovered stores are only fully
    live in processes started afterwards, so a restart is required. Unrecoverable
    stores are sealed with a key that no longer exists - no restart helps, and
    saying so plainly beats letting the user retry forever.
    """
    report = STATE.secure_storage_recovery or {}
    if not report:
        return {"stranded": 0, "recovered": 0, "unrecoverable": 0, "restart_required": False}

    unrecoverable = list(report.get("unrecoverable") or [])
    recovered = int(report.get("recovered") or 0)
    status: Dict[str, Any] = {
        "stranded": int(report.get("stranded") or 0),
        "recovered": recovered,
        "unrecoverable": len(unrecoverable),
        "unrecoverable_names": [os.path.basename(path) for path in unrecoverable[:12]],
        "restart_required": bool(report.get("restart_required")),
        "error": str(report.get("error") or ""),
    }
    # A partial sweep has to report both halves: recovering two stores says
    # nothing about the third that is still unreadable.
    sentences: List[str] = []
    if recovered:
        sentences.append(
            f"Recovered {recovered} protected data store(s) that stayed sealed after Secure "
            "Professional Maximus was turned off."
        )
    if unrecoverable:
        sentences.append(
            f"{len(unrecoverable)} protected data store(s) are still sealed and their Secure "
            "Professional Maximus key no longer exists, so their contents cannot be recovered; "
            "move the listed files aside to start fresh."
        )
    if sentences:
        sentences.append("Shut down AutoYou completely and start it again to apply this.")
        status["message"] = " ".join(sentences)
    return status


def _configure_secure_storage_for_config(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    password: Optional[str] = None,
    operation_timeout_seconds: Optional[float] = None,
    allow_key_creation: bool = True,
) -> Dict[str, Any]:
    """Apply the storage policy after config unlock and before child startup."""
    mode = _get_security_mode_from_cfg(cfg if cfg is not None else STATE.config)
    if mode != SECURE_PROFESSIONAL_MAXIMUS_MODE:
        if secure_storage_enabled():
            raise SecureStorageError(
                "Secure Professional Maximus storage stays active until the server restarts"
            )
        try:
            STATE.secure_storage_recovery = recover_stranded_envelopes(
                app_name="AutoYou",
                root=_CONFIG_DIR,
                scan_roots=_secure_storage_scan_roots(),
                password=password or STATE.server_password or STATE.config_unlock_password,
            )
        except Exception as exc:
            STATE.secure_storage_recovery = {
                "stranded": 0,
                "recovered": 0,
                "unrecoverable": [],
                "restart_required": False,
                "error": str(exc),
            }
            LOGGER.warning("Secure storage downgrade recovery did not complete: %s", exc)
        _log_secure_storage_recovery_outcome()
        disable_secure_storage()
        return secure_storage_status()
    ai_agent_cfg = (cfg or {}).get("ai_agent", {}) if isinstance(cfg, dict) else {}
    if str(ai_agent_cfg.get("memory_backend") or "legacy").strip().lower() == "cognee":
        raise SecureStorageError(
            "Secure Professional Maximus requires the legacy SQLite memory backend; "
            "disable Cognee memory before enabling this mode"
        )
    resolved_password = str(
        password
        or STATE.server_password
        or STATE.config_unlock_password
        or os.getenv("AUTOYOU_SERVER_PASSWORD")
        or ""
    ).strip() or None
    status = enable_secure_storage(
        app_name="AutoYou",
        root=_CONFIG_DIR,
        password=resolved_password,
        operation_timeout_seconds=operation_timeout_seconds,
        allow_key_creation=allow_key_creation,
    )
    _migrate_secure_control_files()
    return status


def _migrate_secure_control_files() -> None:
    """Seal control-plane files that may predate Secure Professional Maximus."""
    control_paths = (
        _get_unlock_file_path(),
        license_ack_path(anchor=__file__),
        get_jailbreak_data_dir(anchor=__file__) / JAILBREAK_ACKNOWLEDGEMENT_FILENAME,
        get_jailbreak_data_dir(anchor=__file__) / JAILBREAK_ROOT_PROMPT_FILENAME,
    )
    for path in control_paths:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        if path.name == "LICENSE_ACKNOWLEDGEMENT" and not raw.startswith(SPM_FILE_HEADER):
            try:
                acknowledgement = json.loads(raw.decode("utf-8"))
                if not isinstance(acknowledgement, dict):
                    raise ValueError("acknowledgement is not an object")
            except Exception:
                acknowledgement = {"agreement_version": "", "legacy": True}
            save_secure_json(path, acknowledgement)
            continue
        # read_secure_file validates existing ciphertext and migrates plaintext
        # bytes atomically.  Invalid protected data raises and blocks startup.
        read_secure_file(path)

    # Eagerly seal small, sensitive, server-owned stores that would otherwise
    # sit in plaintext under Maximus until something happens to reopen them
    # (2FA profiles, agent registries, login/session/feed DBs). Large agent
    # databases are deliberately excluded - they stay on the migrate-on-access
    # path so enable/boot never has to load a multi-hundred-MB store wholesale.
    eager_seal_paths: List[Path] = [
        Path(_agent_security_db_path()),
        Path(LOGIN_UI_DB_PATH),
        _CONFIG_DIR / "sessions.db",
        _CONFIG_DIR / "page_feed.db",
        get_config_dir("AutoYou", anchor=__file__) / "agent_frontends_registry.json",
        get_config_dir("AutoYou", anchor=__file__) / "agent_install_registry.json",
        get_mutable_data_dir("AutoYou", anchor=__file__) / "agent_frontends_registry.json",
        get_mutable_data_dir("AutoYou", anchor=__file__) / "agent_install_registry.json",
        # Agent-website OTP mission/chat/shared session tokens (scheduler_mission_control.py).
        get_config_dir("AutoYou", anchor=__file__) / "agent_ui_sessions.json",
    ]
    try:
        result = seal_secure_paths(eager_seal_paths)
        if result.get("sealed"):
            LOGGER.info("Maximus eager-seal: sealed %d owned store(s) on enable", result["sealed"])
    except SecureStorageError as exc:
        # Sealing is best-effort hardening on top of migrate-on-access; a single
        # unreadable store must not block the whole boundary from coming up.
        LOGGER.warning("Maximus eager-seal skipped one or more stores: %s", exc)

def get_pairing_tier() -> str:
    """Return the server's Security Tier, defaulting to compatible B-Tier.

    Orthogonal to security mode: B-Tier ("Quick Pairing") additionally accepts
    a bare single-message pake1 /autopair alongside the always-available
    cpace1 hello-first flow; A-Tier ("Enhanced Pairing") only accepts the
    hello-first flow. See pairing_router.py's _get_security_tier.
    """
    try:
        cfg = STATE.config or {}
        tier = str((cfg.get("security", {}) or {}).get("tier") or "B").strip().upper()
        if tier in ("A", "B"):
            return tier
    except Exception:
        pass
    return "B"

def _get_pairing_totp_secret(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    try:
        base_cfg = cfg if cfg is not None else (STATE.config or {})
        _apply_default_security_config(base_cfg)
        secret = _normalize_totp_secret((base_cfg.get("security", {}) or {}).get("totp_secret"))
        return secret or None
    except Exception:
        return None

def get_totp_secret_for_sender(platform: str, sender_id: str) -> Optional[str]:
    """Return the shared Secure Professional pairing secret for any sender."""
    return _get_pairing_totp_secret()

def get_all_totp_secrets() -> Dict[str, str]:
    """Return the single shared Secure Professional pairing secret, if configured."""
    secret = _get_pairing_totp_secret()
    return {"default": secret} if secret else {}

def _describe_totp_capabilities(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Summarize whether the server currently has a usable shared 2FA secret."""
    base_cfg = cfg if cfg is not None else (STATE.config or _default_config())
    _apply_default_security_config(base_cfg)
    mode = _get_security_mode_from_cfg(base_cfg)
    pairing_secret = _get_pairing_totp_secret(base_cfg)

    usable_totp = 0
    if pairing_secret and pyotp is not None:
        try:
            pyotp.TOTP(pairing_secret).now()
            usable_totp = 1
        except Exception:
            usable_totp = 0

    return {
        "security_mode": mode,
        "totp_client_count": 1 if pairing_secret else 0,
        "usable_totp_client_count": usable_totp,
        "totp_configured": usable_totp > 0,
        "pyotp_available": pyotp is not None,
    }

def _verify_totp_secret(secret: Optional[str], code: Any, *, valid_window: int = 1) -> bool:
    normalized_secret = _normalize_totp_secret(secret)
    normalized_code = str(code or "").strip()
    if not normalized_secret or not normalized_code or pyotp is None:
        return False
    try:
        return bool(pyotp.TOTP(normalized_secret).verify(normalized_code, valid_window=valid_window))
    except Exception:
        return False

def _default_totp_issuer() -> str:
    server_name = (STATE.config or {}).get("server", {}).get("name") or ""
    return str(server_name).strip() or "AutoYou-Server"

def _build_totp_otpauth(client_id: str, secret: str, issuer: Optional[str] = None) -> Tuple[str, str]:
    """Build an OTPAuth URI for a stored TOTP secret."""
    if pyotp is None:
        raise RuntimeError("pyotp not installed")
    resolved_issuer = str(issuer or "").strip() or _default_totp_issuer()
    totp = pyotp.TOTP(secret)
    return resolved_issuer, totp.provisioning_uri(name=client_id, issuer_name=resolved_issuer)

def _build_qr_code_url(payload: str, *, size: int = 300, ecc: str = "H") -> str:
    """Return a hosted QR image URL for the provided payload text."""
    safe_size = max(128, min(int(size or 300), 1024))
    safe_ecc = str(ecc or "H").strip().upper() or "H"
    return (
        "https://api.qrserver.com/v1/create-qr-code/"
        f"?size={safe_size}x{safe_size}"
        f"&ecc={quote(safe_ecc, safe='')}"
        f"&data={quote(str(payload or ''), safe='')}"
    )

def _build_local_qr_data_url(payload: str, *, box_size: int = 8, border: int = 2) -> Optional[str]:
    """Render a QR code for ``payload`` entirely locally as a base64 PNG data URL.

    Unlike ``_build_qr_code_url``, this never sends ``payload`` to a third-party
    service. Required for one-time secret material (TOTP provisioning URIs) that
    must never leave the machine. Returns None if the ``qrcode`` package is
    unavailable or rendering fails; callers should fall back to text display.
    """
    if qrcode is None or not payload:
        return None
    try:
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=border)
        qr.add_data(payload)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    except Exception as exc:
        LOGGER.debug("Local QR rendering failed: %s", exc)
        return None

def _default_config() -> Dict[str, Any]:
    return {
        "version": 1,
        "server": {
            "name": _default_server_display_name(),
            "installation_id": _generate_server_installation_id(),
            "bind_host": "127.0.0.1",
        },
        "software_update": {
            "enabled": True,
        },
        "security": {
            # Secure by default; options also include the strongest local
            # storage mode: secure_professional_maximus.
            "mode": "secure",
            # Shared 2FA secret used for secure professional pairing, tunnelmole
            # authenticator pairing, and admin-agent elevation.
            "totp_secret": "",
            "native_unlock_enabled": True,
            # Security Tier ("A"/"B"), orthogonal to mode above. B (Quick
            # Pairing) is the compatible default; A remains available as the
            # enhanced hello-first option.
            "tier": "B",
        },
        "ai_agent": {
            "enabled": True,
            "port": AI_AGENT_SERVER_PORT,
            "auto_start": True,
            "record_messages_in_database": True,
            "memory_backend": "legacy",
            # Global switch to allow/deny internet searches by the internet_agent
            "internet_search_enabled": True
        },
        "mcp": {
            # The façade is safe on a loopback-only server without a token;
            # remote/LAN deployments must configure api_token before use.
            "enabled": True,
            "api_token": "",
            "adapter_url": "http://127.0.0.1:8071",
        },
        "client_identity": {
            # Names are optional per-pair display metadata. Keep them live-only
            # until an operator expressly opts in to history storage.
            "store_client_names_in_history": False,
            "name_overrides": {},
        },
        "audio_playback": {
            "enabled": True,
            "music_library_dirs": [],
        },
        "video_call": {
            "enabled": True,
            "audio_enabled": True,
            "disable_autoyou_agents": False,
            "ai_audio_replies_enabled": True,
            "record_my_video": False,
            "recording_dir": "",
            "recording_mode": "video",
            "image_interval_seconds": DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
            "background_mode_enabled": False,
            "silent_recording_enabled": False,
            "location_recording_enabled": False,
            "silent_recording_dir": "",
            "silent_recording_batch_seconds": DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
            "outbound_source": "remote_desktop",
            "outbound_sources": ["remote_desktop"],
            "api_video_source_id": "default",
            "capture_audio": False,
            "audio_sources": [],
            "input_audio_source": "default",
            "camera_device_id": 0,
            "video_file": {
                "path": "",
                "loop": True,
            },
            "remote_desktop": {
                "enabled": True,
                "send_screen": True,
                "monitor_id": 0,
                "quality": "balanced",
                "bitrate_kbps": 1500,
                "control_enabled": False,
            },
        },
        "agent_frontends": {
            "admin_agent": False,
            "ads_watching_agent": {
                "enabled": True,
                "route_mode": "direct_forward",
            },
            "hosting_agent": True,
            "notes_agent": True,
            "media_generation_agent": True,
            "voice_training_agent": True,
            "education_agent": True,
        },
        "autoyou_page": {
            "port": 8067,
            "auto_start": True,
            "timeline_days": 7,
            "feed_window_days": 0,
            "custom_forward_enabled": False,
            "custom_forward_port": 8067,
            "advertised_websites": [],
            "bookmarks": [],
            "theme": "light",
            "remote_access_role": REMOTE_ACCESS_VIEWER,
        },
        "model_behavior": {
            # Mode controls preset LiteLlm parameter bundles.
            # Options: "none" | "accurate" | "human" | "creative"
            # "none" means no model-behavior kwargs are applied to LiteLlm.
            "mode": "accurate",
            # Advanced overrides: when non-null, these override the mode preset.
            "temperature": None,
            "top_p": None,
            "top_k": None,
            "repeat_penalty": None,
            "num_ctx": None,
            # None means "not yet configured" - same override sentinel as
            # num_ctx above - so an untouched default config doesn't shadow
            # the AUTOYOU_OLLAMA_THINKING/OLLAMA_THINK env vars. Once the
            # admin explicitly saves this from the UI it becomes a real bool.
            # Off (hides the model's internal reasoning from chat replies) is
            # the effective default: local reasoning models (e.g. Ollama's
            # qwen3) narrate their thinking as plain text with no reliable
            # structural marker, so most users want the clean final answer.
            "show_thinking": None,
            # Optional Ollama thinking level. None keeps the model family
            # default; valid values are low, medium, high, and max.
            "thinking_level": None,
        },
        "telegram": {
            "bot_token": "",
            "acl_usernames": [],
            "acl_sender_ids": [],
            "access_gate_enabled": False,
            "silent_unapproved_messages": False,
        },
        "signal": {
            "enabled": False,
            "port": 8082,
            "device_name": "AutoYou-Signal",
            "phone_number": "",
            "paired": False,
            "shutdown_docker_on_exit": True
        },
        "whatsapp": {
            "enabled": False,
            "port": 8083,
            "device_name": "AutoYou-WhatsApp",
            "phone_number": "",
            "paired": False,
            "shutdown_on_exit": True
        },
        "telegram_user": {
            "enabled": False,
            "api_id": 0,
            "api_hash": "",
            "session": "",
            "training_export_consent": False,
            "training_export_consent_at": "",
            "prompt_builder": {
                "enabled": False,
                "application_agent": "codex_desktop_agent",
            },
        },
        "tunnelmole": {
            "enabled": False,
            "timeout_minutes": 5,
            "otp_timeout_minutes": 5,
            "otp_multiuse": False,
            "pair_code_mode": _TUNNELMOLE_PAIR_CODE_MODE_RANDOM_OTP,
            "connection_mode": _TUNNELMOLE_CONNECTION_MODE_TIMED,
            "website_hosting": {
                "enabled": False,
                "agent_name": "",
            },
        },
        "bluetooth_pairing": {
            "enabled": False,
        },
        "scheduler": {
            "fallback_mode": "default",
            "fallback_max_age_seconds": 30 * 60,
        },
        "ollama": {
            "enabled": True,
            "api_base": "http://localhost:11434",
            "model": DEFAULT_WIZARD_MODEL,
            # False preserves first-run fallback behavior. The model-library
            # selector sets this once an operator chooses an installed tag.
            "model_explicit": False,
            "use_google_api": False,
            "google_model": "gemini-2.5-flash",
            "google_api_key": ""
        },
        # Active AI provider selection and per-provider settings.
        # provider: "ollama" | "ollama_gateway" | "odysseus" | "openclaw" | "hermes" | "litellm" | "google"
        # Ollama is the preferred local default; Google is the legacy cloud option.
        # OpenClaw uses the local OpenClaw Gateway as an OpenAI-compatible LLM backend.
        # Hermes uses the local NousResearch Hermes Agent gateway (OpenAI-compatible).
        # LiteLLM supports cloud providers (Anthropic, OpenAI, Mistral, DeepSeek, xAI…)
        # via their native LiteLLM provider strings.
        "ai_provider": {
            "provider": "ollama",
            # OpenClaw provider settings
            "openclaw_port": 18789,
            "openclaw_token": "",
            "openclaw_model": "openclaw/default",
            # OpenClaw sub-agent settings (used when openclaw_agent is installed and
            # Ollama/another provider is the root LLM - separate from provider mode)
            "openclaw_agent_port": 18789,
            "openclaw_agent_token": "",
            "openclaw_agent_model": "openclaw/default",
            # Hermes Agent gateway settings (NousResearch/hermes-agent)
            "hermes_port": 8642,
            "hermes_token": "",
            "hermes_model": "hermes-agent",
            # LiteLLM cloud provider settings
            "litellm_model": "anthropic/claude-sonnet-4-5",
            "litellm_api_key": "",
            "litellm_api_base": "",
            # Native Odysseus companion-service settings.  The token stays in
            # the existing encrypted/keystore config store and is never sent
            # back to the browser bootstrap payload.
            "odysseus_api_base": "http://127.0.0.1:7000",
            "odysseus_model": "",
            "odysseus_token": "",
        },
        "onboarding": {
            "wizard_completed": False,
            "wizard_completed_at": "",
        },
        "speech": deepcopy_speech_config(),
        "rtc": {
            # Pre-populate with default public STUN servers
            "iceServers": list(DEFAULT_ICE_SERVERS)
        },
        "cloud": {
            "server_token": "",
            "server_id": "",
            "user_id": "",
            "email": "",
            "registered_at": "",
            "token_issued_at": "",
        },
        "agent_websites": {
            # When True and TOTP is configured, all agent chat UIs require OTP login.
            # Individual agents can opt out via agent_ui_security.<name>.bypass_global_otp.
            "require_otp": False,
            # Explicit override for every agent website; Admin UI login is separate.
            "disable_otp": False,
            # Opt-in (off by default): once on, completing OTP for any one
            # eligible agent website unlocks every other eligible agent
            # website in the same browser/cookie jar. Agents can opt out via
            # their own website/manifest.json shared_session_eligible field.
            "shared_session_enabled": False,
            "shared_session_ttl_days": 30,
        },
    }

def _read_autoyou_ui_theme() -> str:
    configured_theme = ""
    try:
        configured_theme = str((STATE.config or {}).get("autoyou_page", {}).get("theme") or "").strip()
    except Exception:
        configured_theme = ""
    return get_ui_theme(
        app_name="AutoYou",
        anchor=__file__,
        default=(configured_theme or "light"),
    )

def _persist_autoyou_ui_theme(
    theme: Any,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    persist_config: bool = False,
) -> str:
    normalized_theme = set_ui_theme(theme, app_name="AutoYou", anchor=__file__)
    target_cfg = cfg if cfg is not None else STATE.config
    if isinstance(target_cfg, dict):
        target_cfg.setdefault("autoyou_page", {})["theme"] = normalized_theme
        if persist_config:
            _persist_state_config(target_cfg)
    return normalized_theme

def _encrypted_config_exists() -> bool:
    return os.path.exists(CONFIG_FILE_PATH)

def _saved_config_exists() -> bool:
    ks = _get_server_keystore()
    # The keystore file itself is persisted state even when keyring is
    # unavailable.  Do not let a missing backend turn an existing install into
    # a first-run install.
    return _encrypted_config_exists() or os.path.exists(CONFIG_KEYSTORE_PATH) or (ks is not None and ks.exists())

def _build_initial_server_config() -> Dict[str, Any]:
    cfg = _default_config()
    _apply_default_server_identity_config(cfg)
    _apply_default_security_config(cfg)
    _apply_default_tunnelmole_config(cfg)
    _apply_default_client_identity_config(cfg)
    _apply_default_agent_frontends_config(cfg)
    _apply_default_bluetooth_pairing_config(cfg)
    _apply_default_ice_servers(cfg)
    _apply_default_speech_config(cfg)
    _apply_default_onboarding_config(cfg)
    _apply_default_cloud_config(cfg)
    _apply_default_autoyou_page_config(cfg)
    _apply_default_messaging_partner_config(cfg)
    _apply_default_video_call_config(cfg)
    return cfg

def try_decrypt_config_with(password: str) -> Optional[Dict[str, Any]]:
    try:
        if aead_decrypt is None:
            raise RuntimeError("cryptography/aead module not available for decrypt")
        with CONFIG_IO_LOCK:
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                envelope = f.read()
        plaintext = aead_decrypt(envelope, password)
        cfg = json.loads(plaintext)
        return cfg
    except Exception as e:
        LOGGER.warning(f"Config decryption failed with provided password: {e}")
        return None

def save_encrypted_config(cfg: Dict[str, Any], password: str) -> None:
    if aead_encrypt is None:
        raise RuntimeError("cryptography/aead module not available for encrypt")
    plaintext = json.dumps(cfg, indent=2)
    envelope = aead_encrypt(plaintext, password)
    temp_path = f"{CONFIG_FILE_PATH}.tmp"
    with CONFIG_IO_LOCK:
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(envelope)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, CONFIG_FILE_PATH)

def _load_config_from_store(
    config_store: str,
    *,
    password: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_store = _normalize_config_store(config_store)
    if normalized_store == CONFIG_STORE_KEYSTORE:
        ks = _get_server_keystore()
        if ks is None or not ks.is_available():
            return None
        return ks.load()
    if normalized_store == CONFIG_STORE_ENCRYPTED:
        normalized_password = str(password or "").strip()
        if not normalized_password:
            return None
        return try_decrypt_config_with(normalized_password)
    return None


def _resolve_config_for_password(password: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """Load the saved config and identify its store when ``password`` is valid."""
    normalized_password = str(password or "").strip()
    if not normalized_password:
        return None, CONFIG_STORE_NONE

    ks = _get_server_keystore()
    if ks is not None and ks.is_available() and ks.exists():
        cfg = ks.load()
        if cfg is not None:
            expected_password = get_server_password() or _load_keystore_server_password() or DEFAULT_SERVER_PASSWORD
            try:
                password_ok = secrets.compare_digest(normalized_password, expected_password)
            except Exception:
                password_ok = normalized_password == expected_password
            if password_ok:
                return cfg, CONFIG_STORE_KEYSTORE

    if _encrypted_config_exists():
        cfg = try_decrypt_config_with(normalized_password)
        if cfg is not None:
            return cfg, CONFIG_STORE_ENCRYPTED

    return None, CONFIG_STORE_NONE

def _persist_state_config(
    cfg: Dict[str, Any],
    *,
    server_password: Optional[str] = None,
    preferred_store: Optional[str] = None,
    operation_timeout_seconds: Optional[float] = None,
    allow_key_creation: bool = True,
) -> str:
    _apply_default_server_identity_config(cfg)
    _apply_default_security_config(cfg)
    _apply_default_tunnelmole_config(cfg)
    _apply_default_client_identity_config(cfg)
    _apply_default_agent_frontends_config(cfg)
    _apply_default_bluetooth_pairing_config(cfg)
    _apply_default_messaging_partner_config(cfg)
    _apply_default_video_call_config(cfg)

    if secure_storage_enabled() and _get_security_mode_from_cfg(cfg) != SECURE_PROFESSIONAL_MAXIMUS_MODE:
        raise SecureStorageError(
            "Secure Professional Maximus storage stays active until the server restarts"
        )

    resolved_server_password = str(
        server_password if server_password is not None else (STATE.server_password or "")
    ).strip() or None
    if secure_storage_enabled() and _get_security_mode_from_cfg(cfg) == SECURE_PROFESSIONAL_MAXIMUS_MODE:
        storage_status = secure_storage_status()
        active_storage_password = str(
            os.getenv("AUTOYOU_SECURE_STORAGE_PASSWORD")
            or STATE.server_password
            or STATE.config_unlock_password
            or ""
        ).strip()
        if (
            storage_status.get("key_source") == "password_fallback"
            and resolved_server_password
            and resolved_server_password != active_storage_password
        ):
            rotate_secure_storage(
                password=resolved_server_password,
                scan_roots=_secure_storage_scan_roots(),
            )
    desired_store = _normalize_config_store(preferred_store or STATE.config_store)
    ks = _get_server_keystore()
    keystore_available = ks is not None and ks.is_available()

    if desired_store == CONFIG_STORE_NONE:
        desired_store = CONFIG_STORE_KEYSTORE if keystore_available else CONFIG_STORE_ENCRYPTED

    if desired_store == CONFIG_STORE_KEYSTORE:
        if keystore_available:
            try:
                if operation_timeout_seconds is None and allow_key_creation:
                    ks.save(cfg)
                else:
                    ks.save(
                        cfg,
                        operation_timeout_seconds=operation_timeout_seconds,
                        allow_key_creation=allow_key_creation,
                    )
                _remove_path_silently(CONFIG_FILE_PATH)
                _remove_path_silently(CONFIG_BAK_PATH)
                _set_config_session(
                    config_store=CONFIG_STORE_KEYSTORE,
                    server_password=resolved_server_password,
                )
                if resolved_server_password and allow_key_creation:
                    if operation_timeout_seconds is None and allow_key_creation:
                        _persist_server_password(resolved_server_password)
                    else:
                        _persist_server_password(
                            resolved_server_password,
                            operation_timeout_seconds=operation_timeout_seconds,
                            allow_update=allow_key_creation,
                        )
                elif resolved_server_password:
                    # This is an automatic bounded startup read. Preserve the
                    # in-memory password without probing or rewriting another
                    # Keychain item before the recovery UI is available.
                    STATE.server_password = resolved_server_password
                else:
                    _clear_persisted_server_password()
                STATE.config = cfg
                if operation_timeout_seconds is None and allow_key_creation:
                    _configure_secure_storage_for_config(
                        cfg,
                        password=resolved_server_password,
                    )
                else:
                    _configure_secure_storage_for_config(
                        cfg,
                        password=resolved_server_password,
                        operation_timeout_seconds=operation_timeout_seconds,
                        allow_key_creation=allow_key_creation,
                    )
                return STATE.config_store
            except Exception as exc:
                if not allow_key_creation:
                    # Never turn a denied existing Keychain config into a new
                    # password store during automatic startup. That would both
                    # create more authorization requests and strand the
                    # original encrypted configuration.
                    raise SecureStorageError(
                        "Saved configuration could not access its existing system credential"
                    ) from exc
                LOGGER.warning(
                    "Failed to save config to OS keystore; falling back to encrypted config storage: %s",
                    exc,
                )
        else:
            LOGGER.warning("OS keystore unavailable; falling back to encrypted config storage.")

    if not resolved_server_password:
        resolved_server_password = str(STATE.config_unlock_password or "").strip() or None
    if not resolved_server_password:
        raise RuntimeError("Server password is required to save encrypted configuration")

    save_encrypted_config(cfg, resolved_server_password)
    _clear_main_keystore_config()
    _set_config_session(
        config_store=CONFIG_STORE_ENCRYPTED,
        server_password=resolved_server_password,
        config_unlock_password=resolved_server_password,
    )
    if _keystore_server_password_available() and allow_key_creation:
        if operation_timeout_seconds is None and allow_key_creation:
            _persist_server_password(resolved_server_password)
        else:
            _persist_server_password(
                resolved_server_password,
                operation_timeout_seconds=operation_timeout_seconds,
                allow_update=allow_key_creation,
            )
    elif _keystore_server_password_available():
        # Config persistence must not itself turn into a second credential
        # probe while a bounded startup has already chosen recovery behavior.
        STATE.server_password = resolved_server_password
    else:
        _clear_persisted_server_password()
    STATE.config = cfg
    if operation_timeout_seconds is None and allow_key_creation:
        _configure_secure_storage_for_config(cfg, password=resolved_server_password)
    else:
        _configure_secure_storage_for_config(
            cfg,
            password=resolved_server_password,
            operation_timeout_seconds=operation_timeout_seconds,
            allow_key_creation=allow_key_creation,
        )
    return STATE.config_store

def _bootstrap_password_candidates() -> List[Tuple[str, bool]]:
  candidates: List[Tuple[str, bool]] = []
  seen: Set[str] = set()
  for raw_value, via_env in (
    (os.getenv("AUTOYOU_SERVER_PASSWORD"), True),
    (DEFAULT_SERVER_PASSWORD, False),
  ):
    normalized = str(raw_value or "").strip()
    if not normalized or normalized in seen:
      continue
    seen.add(normalized)
    candidates.append((normalized, via_env))
  return candidates

def _save_and_reload_state_config(
    cfg: Dict[str, Any],
    *,
    server_password: Optional[str] = None,
    preferred_store: Optional[str] = None,
    operation_timeout_seconds: Optional[float] = None,
    allow_key_creation: bool = True,
) -> Dict[str, Any]:
    final_store = _persist_state_config(
        cfg,
        server_password=server_password,
        preferred_store=preferred_store,
        operation_timeout_seconds=operation_timeout_seconds,
        allow_key_creation=allow_key_creation,
    )
    reload_password = STATE.config_unlock_password if final_store == CONFIG_STORE_ENCRYPTED else None
    reloaded_cfg = _load_config_from_store(final_store, password=reload_password)
    if reloaded_cfg is None:
        raise RuntimeError(f"Saved config could not be reloaded from {final_store} storage")
    STATE.config = reloaded_cfg
    return reloaded_cfg

async def bootstrap_password_and_config() -> None:
    """Prepare the persisted config store without starting runtime services.

    Priority order:
      0) Create initial keystore/encrypted config with autoyou123 on first run
      1) OS keystore (authoritative when present and readable)
      2) AUTOYOU_SERVER_PASSWORD env var
      3) autoyou123 default bootstrap password
      4) Awaiting admin login
    """
    ks = _get_server_keystore()
    keystore_available = ks is not None and ks.is_available()
    preferred_store = CONFIG_STORE_KEYSTORE if keystore_available else CONFIG_STORE_ENCRYPTED

    if not _saved_config_exists():
        initial_password, initial_via_env = _bootstrap_password_candidates()[0]
        try:
            cfg = _save_and_reload_state_config(
                _build_initial_server_config(),
                server_password=initial_password,
                preferred_store=preferred_store,
            )
            STATE.config = cfg
            STATE.used_default_password = initial_password == DEFAULT_SERVER_PASSWORD
            STATE.decrypted_via_env = initial_via_env
            LOGGER.info(
                "Created initial %s config with %s.",
                STATE.config_store,
                "AUTOYOU_SERVER_PASSWORD" if initial_via_env else "the default bootstrap password",
            )
            STATE._unlock_state_mem = "Ready"
            return
        except Exception as exc:
            LOGGER.warning("Failed to create initial bootstrap config: %s", exc)
            _set_config_session(config_store=CONFIG_STORE_NONE)
            STATE.config = {}
            STATE.used_default_password = False
            STATE.decrypted_via_env = False
            LOGGER.info("Awaiting admin login to create the initial configuration.")
            return

    if keystore_available and ks.exists():
        cfg = _load_keystore_config_during_bootstrap(ks)
        if cfg is not None:
            defaults_changed = (
                _apply_default_server_identity_config(cfg)
                or _apply_default_security_config(cfg)
                or _apply_default_tunnelmole_config(cfg)
                or _apply_default_client_identity_config(cfg)
                or _apply_default_agent_frontends_config(cfg)
                or _apply_default_bluetooth_pairing_config(cfg)
                or _apply_default_messaging_partner_config(cfg)
                or _apply_default_video_call_config(cfg)
                or _apply_default_ice_servers(cfg)
                or _apply_default_speech_config(cfg)
                or _apply_default_onboarding_config(cfg)
                or _apply_default_cloud_config(cfg)
                or _apply_default_autoyou_page_config(cfg)
            )
            resolved_server_password = _load_keystore_server_password(
                operation_timeout_seconds=_macos_keychain_bootstrap_timeout_seconds(),
            )
            if resolved_server_password is None and _macos_keychain_bootstrap_timeout_seconds() is not None:
                LOGGER.warning(
                    "Saved configuration decrypted, but its macOS Keychain pairing credential "
                    "was not available during bootstrap; starting the locked recovery UI."
                )
                _set_config_session(config_store=CONFIG_STORE_NONE)
                STATE.config = {}
                STATE.used_default_password = False
                STATE.decrypted_via_env = False
                STATE._unlock_state_mem = "Locked"
                return
            _set_config_session(
                config_store=CONFIG_STORE_KEYSTORE,
                server_password=resolved_server_password,
            )
            STATE.config = cfg
            STATE.used_default_password = (resolved_server_password == DEFAULT_SERVER_PASSWORD)
            STATE.decrypted_via_env = False
            if resolved_server_password is None:
                LOGGER.warning(
                    "Keystore config loaded but pairing-password entry is missing from OS keystore "
                    "(service=%r, credential=%r). This may indicate tampering. "
                    "Skipping config re-persist to avoid silent password reset.",
                    _server_keystore_service_name(),
                    _KS_SERVER_PASSWORD_CRED_NAME,
                )
            elif defaults_changed and _macos_keychain_bootstrap_timeout_seconds() is None:
                try:
                    _persist_state_config(
                        cfg,
                        server_password=resolved_server_password,
                        preferred_store=CONFIG_STORE_KEYSTORE,
                        operation_timeout_seconds=_macos_keychain_bootstrap_timeout_seconds(),
                        allow_key_creation=False,
                    )
                except Exception as exc:
                    LOGGER.warning("Failed to persist normalized keystore config: %s", exc)
                    STATE.config = cfg
            elif defaults_changed:
                LOGGER.info(
                    "Deferred normalized macOS Keychain config write until an explicit admin action."
                )
            LOGGER.info("Config loaded from OS keystore (service=%r).", _server_keystore_service_name())
            STATE._unlock_state_mem = "Ready"
            return
        LOGGER.warning(
            "Config exists in OS keystore but could not be loaded; trying encrypted fallback."
        )
        if not _encrypted_config_exists():
            LOGGER.warning(
                "No encrypted fallback found and keystore config is unreadable "
                "(likely a Keychain key mismatch from a compiled binary). "
                "Leaving the existing keystore config unchanged and awaiting admin recovery."
            )

    for candidate_password, via_env in _bootstrap_password_candidates():
        cfg = try_decrypt_config_with(candidate_password)
        if cfg is None:
            continue
        defaults_changed = (
            _apply_default_server_identity_config(cfg)
            or _apply_default_security_config(cfg)
            or _apply_default_tunnelmole_config(cfg)
            or _apply_default_client_identity_config(cfg)
            or _apply_default_agent_frontends_config(cfg)
            or _apply_default_bluetooth_pairing_config(cfg)
            or _apply_default_messaging_partner_config(cfg)
            or _apply_default_video_call_config(cfg)
            or _apply_default_ice_servers(cfg)
            or _apply_default_speech_config(cfg)
            or _apply_default_onboarding_config(cfg)
            or _apply_default_cloud_config(cfg)
            or _apply_default_autoyou_page_config(cfg)
            or _apply_default_messaging_partner_config(cfg)
        )
        _set_config_session(
            config_store=CONFIG_STORE_ENCRYPTED,
            server_password=candidate_password,
            config_unlock_password=candidate_password,
        )
        STATE.config = cfg
        if defaults_changed or (
            keystore_available and _macos_keychain_bootstrap_timeout_seconds() is None
        ):
            try:
                _persist_state_config(
                    cfg,
                    server_password=candidate_password,
                    # Existing encrypted installs are never migrated to a
                    # Keychain store automatically on macOS startup. An admin
                    # save remains the explicit, auditable migration point.
                    preferred_store=(
                        CONFIG_STORE_ENCRYPTED
                        if _macos_keychain_bootstrap_timeout_seconds() is not None
                        else preferred_store
                    ),
                    operation_timeout_seconds=_macos_keychain_bootstrap_timeout_seconds(),
                    allow_key_creation=False,
                )
            except Exception as exc:
                if _get_security_mode_from_cfg(cfg) == SECURE_PROFESSIONAL_MAXIMUS_MODE:
                    LOGGER.warning(
                        "Saved Maximus configuration could not access its existing credential "
                        "during bootstrap; starting the locked recovery UI: %s",
                        exc,
                    )
                    _set_config_session(config_store=CONFIG_STORE_NONE)
                    STATE.config = {}
                    STATE.used_default_password = False
                    STATE.decrypted_via_env = False
                    STATE._unlock_state_mem = "Locked"
                    return
                LOGGER.warning("Failed to persist env-unlocked config: %s", exc)
                _set_config_session(
                    config_store=CONFIG_STORE_ENCRYPTED,
                    server_password=candidate_password,
                    config_unlock_password=candidate_password,
                )
                STATE.config = cfg
        STATE.used_default_password = (candidate_password == DEFAULT_SERVER_PASSWORD)
        STATE.decrypted_via_env = via_env
        LOGGER.info(
            "Loaded config during bootstrap using %s.",
            "AUTOYOU_SERVER_PASSWORD" if via_env else "the default bootstrap password",
        )
        STATE._unlock_state_mem = "Ready"
        return

    _set_config_session(config_store=CONFIG_STORE_NONE)
    STATE.config = {}
    STATE.used_default_password = False
    STATE.decrypted_via_env = False
    if _saved_config_exists():
        LOGGER.warning(
            "Config present but could not decrypt with OS keystore, AUTOYOU_SERVER_PASSWORD, or the default bootstrap password. Awaiting admin login."
        )
    else:
        LOGGER.info(
            "No saved config detected. Awaiting admin login to create the initial configuration."
        )

_NATIVE_GATEWAY_PROVIDERS = frozenset({"ollama_gateway", "odysseus"})


def _is_native_gateway_provider(provider: Optional[str] = None) -> bool:
    """Return whether the selected provider bypasses AutoYou agent runtime."""
    selected = provider
    if selected is None:
        selected = (STATE.config or {}).get("ai_provider", {}).get("provider") or os.getenv("AI_PROVIDER", "")
    return str(selected or "").strip().lower() in _NATIVE_GATEWAY_PROVIDERS


def _apply_google_api_config_to_env() -> None:
    """Apply all AI provider settings from configuration to environment variables.

    Handles Ollama, Google, OpenClaw (provider + sub-agent), and LiteLLM cloud
    settings.  The active provider is written to AI_PROVIDER; USE_GOOGLE_API is
    kept in sync for backward compatibility with any code that still reads it.
    """
    if not STATE.config:
        return

    ollama_config  = STATE.config.get("ollama", {})
    ai_prov_config = STATE.config.get("ai_provider", {})

    # ── Ollama / Google (legacy section) ─────────────────────────────────────
    configured_ollama_api_base = str(ollama_config.get("api_base") or "").strip()
    configured_ollama_model = str(ollama_config.get("model") or "").strip()
    env_ollama_api_base = str(os.getenv("OLLAMA_API_BASE") or "").strip()
    env_ollama_model = str(os.getenv("OLLAMA_MODEL") or "").strip()
    preserve_packaged_env = str(os.getenv("AUTOYOU_PACKAGED_RUNTIME") or "").strip().lower() in {"1", "true", "yes", "on"}
    default_ollama_api_base = "http://localhost:11434"

    if (
        preserve_packaged_env
        and env_ollama_api_base
        and (not configured_ollama_api_base or configured_ollama_api_base == default_ollama_api_base)
    ):
        ollama_api_base = env_ollama_api_base
    else:
        ollama_api_base = configured_ollama_api_base or default_ollama_api_base

    if (
        preserve_packaged_env
        and env_ollama_model
        and (not configured_ollama_model or configured_ollama_model == DEFAULT_WIZARD_MODEL)
    ):
        ollama_model = env_ollama_model
    else:
        ollama_model = configured_ollama_model or DEFAULT_WIZARD_MODEL
    google_model    = (ollama_config.get("google_model") or "gemini-2.5-flash").strip()
    google_api_key  = (ollama_config.get("google_api_key") or "").strip()

    os.environ['OLLAMA_API_BASE'] = ollama_api_base
    os.environ['OLLAMA_MODEL']    = ollama_model
    os.environ['AUTOYOU_OLLAMA_MODEL_EXPLICIT'] = "1" if bool(ollama_config.get("model_explicit", False)) else "0"
    os.environ['GOOGLE_MODEL']    = google_model
    os.environ['GOOGLE_API_KEY']  = google_api_key if google_api_key else 'NULL'

    # ── Active provider ───────────────────────────────────────────────────────
    # Resolve: explicit ai_provider.provider > legacy use_google_api > default ollama
    provider = str(ai_prov_config.get("provider") or "").strip().lower()
    _valid_providers = {"ollama", "ollama_gateway", "odysseus", "openclaw", "hermes", "litellm", "google", "apple_intelligence"}
    if provider not in _valid_providers:
        # Backward compat: if no new key, check old use_google_api flag
        provider = "google" if ollama_config.get("use_google_api", False) else "ollama"

    os.environ['AI_PROVIDER']   = provider
    os.environ['USE_GOOGLE_API'] = 'true' if provider == 'google' else 'false'

    # Keep ollama section's use_google_api flag in sync (read by legacy code paths)
    if STATE.config and "ollama" in STATE.config:
        STATE.config["ollama"]["use_google_api"] = (provider == "google")

    # ── OpenClaw provider settings ────────────────────────────────────────────
    oc_port  = str(ai_prov_config.get("openclaw_port",  18789) or 18789)
    oc_token = str(ai_prov_config.get("openclaw_token", "") or "")
    oc_model = str(ai_prov_config.get("openclaw_model", "openclaw/default") or "openclaw/default")
    os.environ['OPENCLAW_PORT']  = oc_port
    os.environ['OPENCLAW_TOKEN'] = oc_token
    os.environ['OPENCLAW_MODEL'] = oc_model

    # ── OpenClaw sub-agent settings (independent of provider mode) ───────────
    oca_port  = str(ai_prov_config.get("openclaw_agent_port",  18789) or 18789)
    oca_token = str(ai_prov_config.get("openclaw_agent_token", "") or "")
    oca_model = str(ai_prov_config.get("openclaw_agent_model", "openclaw/default") or "openclaw/default")
    os.environ['OPENCLAW_AGENT_PORT']  = oca_port
    os.environ['OPENCLAW_AGENT_TOKEN'] = oca_token
    os.environ['OPENCLAW_AGENT_MODEL'] = oca_model

    # ── Hermes Agent gateway settings ─────────────────────────────────────────
    hm_port  = str(ai_prov_config.get("hermes_port",  8642) or 8642)
    hm_token = str(ai_prov_config.get("hermes_token", "") or "")
    hm_model = str(ai_prov_config.get("hermes_model", "hermes-agent") or "hermes-agent")
    os.environ['HERMES_PORT']  = hm_port
    os.environ['HERMES_TOKEN'] = hm_token
    os.environ['HERMES_MODEL'] = hm_model

    # ── LiteLLM cloud provider settings ──────────────────────────────────────
    ll_model    = str(ai_prov_config.get("litellm_model",    "") or "")
    ll_api_key  = str(ai_prov_config.get("litellm_api_key",  "") or "")
    ll_api_base = str(ai_prov_config.get("litellm_api_base", "") or "")
    os.environ['LITELLM_MODEL']    = ll_model
    os.environ['LITELLM_API_KEY']  = ll_api_key
    os.environ['LITELLM_API_BASE'] = ll_api_base

    # ── Odysseus companion-service settings ─────────────────────────────────
    odysseus_api_base = str(
        ai_prov_config.get("odysseus_api_base") or os.getenv("ODYSSEUS_API_BASE") or "http://127.0.0.1:7000"
    ).strip().rstrip("/")
    odysseus_model = str(ai_prov_config.get("odysseus_model") or os.getenv("ODYSSEUS_MODEL") or "").strip()
    odysseus_token = str(ai_prov_config.get("odysseus_token") or os.getenv("ODYSSEUS_API_TOKEN") or "").strip()
    os.environ['ODYSSEUS_API_BASE'] = odysseus_api_base
    os.environ['ODYSSEUS_MODEL'] = odysseus_model
    if odysseus_token:
        os.environ['ODYSSEUS_API_TOKEN'] = odysseus_token

    try:
        ollama_service.reload_from_env()
    except Exception as exc:
        LOGGER.warning("Failed to refresh Ollama service from environment: %s", exc)

    LOGGER.info(
        "Applied AI provider config: provider=%s OLLAMA_API_BASE=%s OLLAMA_MODEL=%s "
        "GOOGLE_MODEL=%s OPENCLAW_PORT=%s HERMES_PORT=%s LITELLM_MODEL=%s",
        provider, ollama_api_base, ollama_model, google_model, oc_port, hm_port, ll_model,
    )

_OLLAMA_AUTOSTART_LAST_ATTEMPT_AT = 0.0
_OLLAMA_AUTOSTART_THROTTLE_SECONDS = 15.0

def _normalize_ollama_model_reference(model_name: str) -> str:
    normalized = str(model_name or "").strip()
    if normalized.startswith(("hf.co/", "huggingface.co/")):
        return normalized
    if "/" in normalized:
        provider, remainder = normalized.split("/", 1)
        if provider in {"ollama_chat", "ollama", "ollama_local", "ollama-local"} and remainder:
            return remainder
    return normalized

def _is_local_ollama_api_base(api_base: str) -> bool:
    candidate = str(api_base or "").strip()
    if not candidate:
        return True
    parsed = urlsplit(candidate if "://" in candidate else f"http://{candidate}")
    host = str(parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _cloud_sse_connection_is_stale(now: Optional[float] = None) -> bool:
    last_activity_at = float(getattr(STATE, "cloud_last_sse_activity_at", 0.0) or 0.0)
    if last_activity_at <= 0:
        return False
    current_time = float(time.time() if now is None else now)
    return (current_time - last_activity_at) > float(AUTOYOU_CLOUD_SSE_STALE_AFTER_SECONDS)

def _cloud_pair_blocked_by_default_password() -> bool:
    return bool(getattr(STATE, "used_default_password", False))

def _default_password_cloud_pair_error() -> str:
    return (
        "Cloud Pair is disabled while this server is still using the default "
        "bootstrap password. Open the local AutoYou admin setup page and save "
        "a new server password before pairing through AutoYou Cloud."
    )


def _shared_device_server_key_material(*, create: bool = False) -> Optional[Tuple[str, str]]:
    cfg = STATE.config or {}
    cloud_cfg = (cfg.get("cloud") or {}) if isinstance(cfg, dict) else {}
    private_key = str(cloud_cfg.get("shared_device_private_key") or "").strip()
    public_key = str(cloud_cfg.get("shared_device_public_key") or "").strip()
    if _is_shared_device_key_material(private_key, public_key):
        return private_key, public_key
    if not create:
        return None
    block_reason = _config_write_block_reason()
    if block_reason:
        LOGGER.warning("Shared-device key setup deferred: %s", block_reason)
        return None
    try:
        updated = _loaded_config_for_update()
        material = _generate_shared_device_key_material()
        updated.setdefault("cloud", {})["shared_device_private_key"] = material.private_key
        updated["cloud"]["shared_device_public_key"] = material.public_key
        STATE.config = _save_and_reload_state_config(updated)
        return material.private_key, material.public_key
    except Exception as exc:
        LOGGER.warning("Shared-device key setup failed: %s", exc)
        return None


async def _register_shared_device_public_key() -> bool:
    """Backfill existing cloud links without exposing the local private key."""
    material = _shared_device_server_key_material(create=True)
    cloud_cfg = (STATE.config or {}).get("cloud", {}) or {}
    token = str(cloud_cfg.get("server_token") or "").strip()
    server_id = str(cloud_cfg.get("server_id") or "").strip()
    if not material or not token or not server_id:
        return False
    try:
        import httpx as _httpx

        async with _httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/server/shared-key",
                headers={"Authorization": f"Bearer {token}"},
                json={"serverId": server_id, "publicKey": material[1]},
            )
        if response.status_code < 400:
            return True
        LOGGER.warning("Shared-device public-key registration failed with HTTP %s", response.status_code)
    except Exception as exc:
        LOGGER.warning("Shared-device public-key registration failed: %s", exc)
    return False


def _shared_device_pairing_auth_profile(
    metadata: Any,
    *,
    server_device_id: str,
    client_device_id: str,
) -> Optional[Dict[str, str]]:
    """Derive a per-grant password locally from authenticated public metadata."""
    if not isinstance(metadata, dict):
        return None
    material = _shared_device_server_key_material()
    if not material:
        return None
    return _device_pairing_auth_profile(
        metadata, private_key=material[0], public_key=material[1],
        server_device_id=server_device_id, client_device_id=client_device_id,
        allow_bootstrap=_cloud_pair_blocked_by_default_password()
        and get_current_password() == DEFAULT_SERVER_PASSWORD
        and _get_security_mode_from_cfg(STATE.config) == "secure",
    )


def _compose_cloud_relay_message(command: Any, payload: Any, message: Any = "") -> str:
    """Normalize a cloud relay event to the pairing router's wire format.

    Account-service events carry ``command`` and ``payload`` separately for
    queueing, while the pairing router consumes ``/command\nbody``.  Newer
    producers also provide ``message``; it is accepted only when it is framed
    for the declared command so an unrelated field cannot change dispatch.
    """
    normalized_command = str(command or "").strip().lower()
    if normalized_command == "/autopair_fragment":
        # Internal MCP transport marker only.  Feed the bare fragment into
        # PairingRouter so it can append to the sender-scoped /autopair buffer.
        for candidate_value in (message, payload):
            candidate = str(candidate_value or "").lstrip()
            if candidate == normalized_command:
                return ""
            if candidate.startswith(f"{normalized_command}\n"):
                return candidate.split("\n", 1)[1]
            if candidate.startswith(f"{normalized_command} "):
                return candidate[len(normalized_command) + 1 :]
        return str(payload or "").lstrip()
    candidate = str(message or "").lstrip()
    if normalized_command and (
        candidate == normalized_command
        or candidate.startswith(f"{normalized_command}\n")
        or candidate.startswith(f"{normalized_command} ")
    ):
        return candidate

    payload_text = str(payload or "")
    payload_candidate = payload_text.lstrip()
    if normalized_command and (
        payload_candidate == normalized_command
        or payload_candidate.startswith(f"{normalized_command}\n")
        or payload_candidate.startswith(f"{normalized_command} ")
    ):
        return payload_candidate
    if not normalized_command:
        return payload_text
    if not payload_text:
        return normalized_command
    return f"{normalized_command}\n{payload_text}"


async def _handle_cloud_relay_event(event_type: str, data_str: str, server_token: str):
    """Handle an incoming relay event from AutoYou Cloud."""
    import json as _json
    import httpx as _httpx

    if event_type == "heartbeat":
        return

    # ── Entitlement change push - drop cached cloud manifest/JWT ────────────
    # Account-service emits this after a successful Apple/Google receipt
    # verification, ASSN/RTDN webhook, or Stripe webhook. We don't fetch the
    # new manifest here; we just invalidate so the *next* /auth /pair or
    # tunnelmole start lazily re-fetches with the user's new tier.
    if event_type in ("entitlement_changed", "roster_changed"):
        # Both events invalidate the same caches: roster_changed is the
        # provider-prober's signal that the autoyou-distributed roster has
        # shifted (approve/reject/suspend/serving-flip). Either way, drop
        # everything and lazily re-fetch on next need.
        if STATE.cloud_entitlements is not None:
            try:
                await STATE.cloud_entitlements.invalidate()
                LOGGER.info(
                    "AutoYou Cloud: %s received - entitlements/roster cache invalidated",
                    event_type,
                )
            except Exception as exc:
                LOGGER.warning(f"AutoYou Cloud: failed to invalidate entitlements cache: {exc}")
        return

    # ── Remote ICE candidates pushed by the cloud from the mobile client ────
    if event_type != "relay":
        LOGGER.debug(f"AutoYou Cloud: unknown event type {event_type}")
        return

    try:
        event = _json.loads(data_str)
    except Exception:
        LOGGER.warning(f"AutoYou Cloud: could not parse relay event: {data_str[:200]}")
        return

    event_data = event.get("data", {}) if isinstance(event.get("data", {}), dict) else {}
    target_server_id = str(event_data.get("server_id") or event.get("server_id", "") or "").strip()
    local_server_id = str(((STATE.config or {}).get("cloud", {}) or {}).get("server_id", "") or "").strip()
    if target_server_id and local_server_id and target_server_id != local_server_id:
        LOGGER.warning("AutoYou Cloud: dropped relay event for different server_id=%s", target_server_id)
        return

    relay_id = event_data.get("relay_id") or event.get("relay_id", "")
    command = str(event_data.get("command") or event.get("command", "") or "").strip().lower()
    payload = event_data.get("payload") or event.get("payload", "")
    message = event_data.get("message") or event.get("message", "")
    client_device_id = (
      event_data.get("client_device_id") or event.get("client_device_id", "")
    )
    shared_device_metadata = (
        event_data.get("shared_device_auth") or event.get("shared_device_auth")
    )
    shared_device_profile = _shared_device_pairing_auth_profile(
        shared_device_metadata,
        server_device_id=local_server_id,
        client_device_id=str(client_device_id or "").strip(),
    )
    invalid_shared_device_profile = (
        shared_device_metadata is not None and shared_device_profile is None
    )
    pairing_sender_id = str(
        event_data.get("sender_id")
        or event.get("sender_id", "")
        or client_device_id
        or relay_id
        or "cloud-pair"
    ).strip()
    relay_message = _compose_cloud_relay_message(command, payload, message)

    LOGGER.info(f"AutoYou Cloud: received relay command={command} relay_id={relay_id}")

    response_payload = ""
    blocked_default_password_pairing = False
    if invalid_shared_device_profile and command == "/autopair_candidates":
        LOGGER.warning("AutoYou Cloud: dropped shared-device candidates with invalid key metadata")
        return
    try:
        if invalid_shared_device_profile:
            response_payload = _json.dumps({"error": "Computer settings have changed. Scan its setup QR and try again."})
        elif command == "/autopair_candidates":
            if pairing_router:
                await pairing_router.process_message(
                    relay_message,
                    platform="cloud",
                    sender_id=pairing_sender_id,
                    identity_sender_id=(str(client_device_id or "").strip() or None),
                    auth_profile=shared_device_profile,
                )
            return
        elif command == "/autopair_fragment":
            if _cloud_pair_blocked_by_default_password() and shared_device_profile is None:
                response_payload = _json.dumps({"error": _default_password_cloud_pair_error()})
            elif not pairing_router:
                response_payload = _json.dumps({"error": "pairing_router not available"})
            else:
                fragment_response = await pairing_router.process_message(
                    relay_message,
                    platform="cloud",
                    sender_id=pairing_sender_id,
                    identity_sender_id=(str(client_device_id or "").strip() or None),
                    auth_profile=shared_device_profile,
                )
                response_payload = (
                    fragment_response
                    if fragment_response is not None
                    else _json.dumps({"error": "Pairing fragment was not recognized"})
                )
        elif command in ("/autopair", "/autopair_hello"):
            # The canonical message is reconstructed before entering the
            # router, e.g. ``/autopair\n<encrypted-offer>``.
            # Route it through the same pairing_router used by Telegram / WhatsApp / Signal so
            # that encryption, hash validation, and WebRTC offer handling are identical.
            # The router returns the matching pairing reply command for the client to parse.
            if _cloud_pair_blocked_by_default_password() and shared_device_profile is None:
                blocked_default_password_pairing = True
                response_payload = _json.dumps({"error": _default_password_cloud_pair_error()})
                LOGGER.warning(
                    "Refusing Cloud Pair /autopair for relay_id=%s because the default bootstrap password is active.",
                    relay_id,
                )
            elif not pairing_router:
                response_payload = _json.dumps({"error": "pairing_router not available"})
            else:
                # Keep relay_id as the transport session for signaling/ICE, but
                # use the stable pairing sender for router state.
                _cloud_resp = await pairing_router.process_message(
                    relay_message,
                    platform="cloud",
                    sender_id=pairing_sender_id,
                    identity_sender_id=(str(client_device_id or "").strip() or None),
                    auth_profile=shared_device_profile,
                )
                # FRAGMENT_CONSUMED is only possible if the cloud relay somehow
                # sends a split /autopair (should never happen, but guard defensively).
                if _cloud_resp == pairing_router.FRAGMENT_CONSUMED:
                    LOGGER.warning(
                        "Cloud relay %s returned FRAGMENT_CONSUMED unexpectedly "
                        "for relay_id=%s - payload may be incomplete.", command, relay_id
                    )
                    _cloud_resp = _json.dumps({"error": "Incomplete autopair payload"})
                response_payload = _cloud_resp or _json.dumps({"error": "No response from pairing router"})
        elif command in ("/pair", "/pair_hello", "/otp_pair"):
            # OTP-based pair - same router path
            if _cloud_pair_blocked_by_default_password() and shared_device_profile is None:
                blocked_default_password_pairing = True
                response_payload = _json.dumps({"error": _default_password_cloud_pair_error()})
                LOGGER.warning(
                    "Refusing Cloud Pair /pair for relay_id=%s because the default bootstrap password is active.",
                    relay_id,
                )
            elif not pairing_router:
                response_payload = _json.dumps({"error": "pairing_router not available"})
            else:
                _cloud_pair_resp = await pairing_router.process_message(
                    relay_message,
                    platform="cloud",
                    sender_id=pairing_sender_id,
                    identity_sender_id=(str(client_device_id or "").strip() or None),
                    auth_profile=shared_device_profile,
                )
                if _cloud_pair_resp == pairing_router.FRAGMENT_CONSUMED:
                    LOGGER.warning(
                        "Cloud relay %s returned FRAGMENT_CONSUMED unexpectedly "
                        "for relay_id=%s.", command, relay_id
                    )
                    _cloud_pair_resp = _json.dumps({"error": "Incomplete pair payload"})
                response_payload = _cloud_pair_resp or _json.dumps({"error": "No response from pairing router"})
        else:
            response_payload = _json.dumps({"error": f"Unknown command: {command}"})
    except Exception:
        # Pairing failures are returned to the authenticated MCP caller as a
        # bounded generic result. Exception text can contain local paths,
        # dependency details, or provider data and must not cross the cloud
        # relay boundary.
        response_payload = _json.dumps({"error": "Cloud pairing relay failed."})
        LOGGER.exception(
            "AutoYou Cloud: error handling relay event relay_id=%s command=%s",
            relay_id,
            command,
        )

    if len(response_payload.encode("utf-8")) > 131072:
        response_payload = _json.dumps({"error": "Cloud pairing relay response was too large."})

    # Post the SDP answer back to AutoYou Cloud
    try:
        async with _httpx.AsyncClient(timeout=15.0) as client:
            await client.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/relay/pair/response",
                json={
                    "server_token": server_token,
                    "server_id": str(((STATE.config or {}).get("cloud", {}) or {}).get("server_id", "") or ""),
                    "relay_id": relay_id,
                    "response_payload": response_payload,
                },
            )
    except Exception as e:
        LOGGER.warning(f"AutoYou Cloud: could not post relay response: {e}")

    # ── Trickle-ICE drain: push local ICE candidates generated during the WebRTC
    # negotiation back to the client as encrypted /autopair_candidates commands.
    if (
        relay_id
        and command == "/autopair"
        and pairing_router
        and not blocked_default_password_pairing
        and not invalid_shared_device_profile
    ):
        asyncio.create_task(
            _drain_cloud_server_ice(
                relay_id,
                pairing_router,
                pairing_sender_id=pairing_sender_id,
                identity_sender_id=(str(client_device_id or "").strip() or None),
                auth_profile=shared_device_profile,
            )
        )

async def _drain_cloud_server_ice(
    relay_id: str,
    pr: Any,
    *,
    pairing_sender_id: str | None = None,
    identity_sender_id: str | None = None,
    auth_profile: Optional[Dict[str, str]] = None,
) -> None:
    """Poll the pairing_router's trickle queue and forward server ICE to the client.

    Called as a fire-and-forget task after the SDP answer is posted.
    Polls for up to 10 s in 500 ms intervals - covers the typical ICE gathering window.
    """
    pushed: list = []
    deadline = asyncio.get_event_loop().time() + 10.0

    while asyncio.get_event_loop().time() < deadline:
        try:
            getter = getattr(pr, "get_trickle_candidates", None)
            candidates = await getter(relay_id, platform="cloud") if getter else []
        except Exception:
            candidates = []

        new = [c for c in candidates if c not in pushed]
        if new:
            try:
                payload = pr.format_autopair_candidates(
                    relay_id,
                    new,
                    platform="cloud",
                    sender_id=pairing_sender_id or relay_id,
                    identity_sender_id=identity_sender_id,
                    auth_profile=auth_profile,
                )
                await _push_to_client("/autopair_candidates", payload, client_device_id=identity_sender_id or "")
                pushed.extend(new)
                LOGGER.debug(
                    "AutoYou Cloud: pushed %d encrypted server ICE candidates for relay_id=%s",
                    len(new), relay_id,
                )
            except Exception as exc:
                LOGGER.debug(f"AutoYou Cloud: /autopair_candidates drain failed: {exc}")
        try:
            pc = WEBRTC.session_peers.get(relay_id) if WEBRTC else None
            if pc is not None and getattr(pc, "iceGatheringState", None) == "complete":
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)


async def _push_to_client(command: str, payload: str = "", *, client_device_id: str = "") -> dict:
    """Push a command to the paired client via AutoYou Cloud."""
    import ssl
    import aiohttp as _aiohttp
    try:
        import certifi as _certifi
        _ssl_ctx = ssl.create_default_context(cafile=_certifi.where())
    except ImportError:
        _ssl_ctx = ssl.create_default_context()
    token = (STATE.config or {}).get("cloud", {}).get("server_token", "")
    if not token:
        return {"error": "Not registered with AutoYou Cloud"}
    if _cloud_server_token_expired((STATE.config or {}).get("cloud", {}) or {}):
        STATE.cloud_token_rejected = True
        return {"error": "Saved cloud session expired. Re-link Cloud Pair to continue."}
    try:
        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_ctx)) as session:
            async with session.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/server/push-client",
                json={"server_token": token, "command": command, "payload": payload, "client_device_id": client_device_id},
                timeout=_aiohttp.ClientTimeout(total=35),
            ) as resp:
                return await resp.json()
    except Exception as e:
        return {"error": str(e)}

async def _notify_cloud_client(
    *,
    title: str,
    body: str = "",
    category: str = "autoyou",
    data: Optional[Dict[str, Any]] = None,
) -> dict:
    """Send an owner notification through AutoYou Cloud.

    The account-service handles paid-tier gating, opt-in push tokens, APNs/FCM
    fan-out, and its own privacy boundary. This server only forwards the
    authenticated request with the saved cloud server token.
    """
    import ssl
    import aiohttp as _aiohttp
    try:
        import certifi as _certifi
        _ssl_ctx = ssl.create_default_context(cafile=_certifi.where())
    except ImportError:
        _ssl_ctx = ssl.create_default_context()

    token = (STATE.config or {}).get("cloud", {}).get("server_token", "")
    if not token:
        return {
            "success": False,
            "sent": False,
            "status_code": 409,
            "error": "This server is not linked to AutoYou Cloud.",
        }
    if _cloud_server_token_expired((STATE.config or {}).get("cloud", {}) or {}):
        STATE.cloud_token_rejected = True
        return {
            "success": False,
            "sent": False,
            "status_code": 409,
            "error": "Saved cloud session expired. Re-link Cloud Pair to continue.",
        }

    notification_title = str(title or "").strip()[:120]
    if not notification_title:
        return {
            "success": False,
            "sent": False,
            "status_code": 400,
            "error": "title is required.",
        }

    notification_body = str(body or "").strip()[:1000]
    notification_category = str(category or "autoyou").strip()[:64] or "autoyou"
    notification_data: Dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in list(data.items())[:16]:
            notification_data[str(key)[:48]] = str(value)[:256]

    try:
        async with _aiohttp.ClientSession(connector=_aiohttp.TCPConnector(ssl=_ssl_ctx)) as session:
            async with session.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/server/notify-client",
                json={
                    "server_token": token,
                    "title": notification_title,
                    "body": notification_body,
                    "category": notification_category,
                    "data": notification_data,
                },
                timeout=_aiohttp.ClientTimeout(total=35),
            ) as resp:
                try:
                    payload = await resp.json()
                except Exception:
                    payload = {"detail": await resp.text()}
                if not isinstance(payload, dict):
                    payload = {"detail": str(payload)}
                payload.setdefault("status_code", resp.status)
                payload.setdefault("success", resp.status < 400)
                if resp.status >= 400:
                    payload.setdefault("sent", False)
                    payload.setdefault(
                        "error",
                        str(payload.get("detail") or payload.get("message") or "Cloud notification failed."),
                    )
                else:
                    payload.setdefault("sent", True)
                return payload
    except Exception as e:
        return {"success": False, "sent": False, "status_code": 502, "error": str(e)}

def _configure_pairing_router_helpers() -> None:
    if pairing_router is None or not hasattr(pairing_router, "configure"):
        return

    async def _cloud_apply_remote_ice(session_id: str, candidates: list) -> None:
        """Apply remote ICE candidates from the cloud relay to a WebRTC session."""
        for candidate_data in candidates:
            try:
                await WEBRTC.handle_session_candidate(session_id, candidate_data)
            except Exception as _ice_exc:
                LOGGER.warning(
                    "Cloud relay: failed to apply remote ICE candidate for session %s: %s",
                    session_id,
                    _ice_exc,
                )

    async def _cloud_get_trickle_candidates(session_id: str) -> list:
        """Return and drain queued outgoing server ICE candidates for cloud relay."""
        return WEBRTC.pop_outgoing_trickle_candidates(session_id)

    pairing_router.configure(
        generate_hash=_server_generate_hash,
        aead_encrypt=aead_encrypt,
        aead_decrypt=aead_decrypt,
        get_current_password=get_current_password,
        get_security_mode=get_security_mode,
        get_totp_secret_for_sender=get_totp_secret_for_sender,
        get_all_totp_secrets=get_all_totp_secrets,
        get_tunnelmole_status=get_tunnelmole_status,
        extend_tunnelmole_timer=extend_tunnelmole_timer,
        start_tunnelmole_service_with_timer=start_tunnelmole_service_with_timer,
        ensure_auth_server_running=start_auth_server_background,
        generate_otp_hash_and_cache=generate_otp_hash_and_cache,
        handle_autopair_offer=WEBRTC.handle_autopair_offer,
        start_new_conversation=_start_new_conversation_for_owner,
        apply_remote_ice_candidates=_cloud_apply_remote_ice,
        get_trickle_candidates=_cloud_get_trickle_candidates,
        is_totp_pair_mode=_is_totp_pair_mode,
        is_url_only_pair_mode=_is_url_only_pair_mode,
        is_tunnelmole_unmanaged_mode=_is_tunnelmole_unmanaged_mode,
        generate_totp_pair_otp_and_cache=generate_totp_pair_otp_and_cache,
        start_tunnelmole_service_no_timer=start_tunnelmole_service_no_timer,
        verify_totp_code=_verify_totp_secret,
        totp_hello_rate_limit_allowed=_autopair_totp_rate_limit_allowed,
        get_pairing_tier=get_pairing_tier,
    )


# ========= Rate Limiting =========
def _read_rate_limit_env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """Read a positive integer rate-limit setting from the environment."""
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except Exception:
        return max(minimum, default)

_SIGNAL_RATE_LIMIT_WINDOW_SECONDS = _read_rate_limit_env_int(
    "AUTOYOU_SIGNAL_RATE_LIMIT_WINDOW_SECONDS",
    60,
)
_AUTH_RATE_LIMIT_WINDOW_SECONDS = _read_rate_limit_env_int(
    "AUTOYOU_AUTH_RATE_LIMIT_WINDOW_SECONDS",
    60,
)
_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS = _read_rate_limit_env_int(
    "AUTOYOU_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS",
    60,
)
AUTH_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_AUTH_RATE_LIMIT_MAX_REQUESTS", 20),
    window_seconds=_AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
AUTH_GLOBAL_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_AUTH_GLOBAL_RATE_LIMIT_MAX_REQUESTS", 50),
    window_seconds=_AUTH_RATE_LIMIT_WINDOW_SECONDS,
)
LOGIN_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_ADMIN_LOGIN_RATE_LIMIT_MAX_REQUESTS", 5),
    window_seconds=_ADMIN_LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)
ADMIN_LOGIN_RATE_LIMITER = LOGIN_RATE_LIMITER
SIGNAL_OFFER_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_SIGNAL_OFFER_RATE_LIMIT_MAX_REQUESTS", 32),
    window_seconds=_SIGNAL_RATE_LIMIT_WINDOW_SECONDS,
)
SIGNAL_CANDIDATE_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_SIGNAL_CANDIDATE_RATE_LIMIT_MAX_REQUESTS", 512),
    window_seconds=_SIGNAL_RATE_LIMIT_WINDOW_SECONDS,
)
UNAUTHENTICATED_SIGNAL_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_UNAUTHENTICATED_SIGNAL_RATE_LIMIT_MAX_REQUESTS", 20),
    window_seconds=_SIGNAL_RATE_LIMIT_WINDOW_SECONDS,
)
AUTOPAIR_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_AUTOPAIR_RATE_LIMIT_MAX_REQUESTS", 12),
    window_seconds=_read_rate_limit_env_int("AUTOYOU_AUTOPAIR_RATE_LIMIT_WINDOW_SECONDS", 60),
)
# Distinct, tighter bucket for Secure Professional's online authenticator-code
# check during /autopair_hello - kept separate from AUTOPAIR_RATE_LIMITER so
# ordinary pairing traffic can't dilute the counter that actually protects
# the 2FA gate (see H-20).
AUTOPAIR_TOTP_RATE_LIMITER = RateLimiter(
    max_requests=_read_rate_limit_env_int("AUTOYOU_AUTOPAIR_TOTP_RATE_LIMIT_MAX_REQUESTS", 8),
    window_seconds=_read_rate_limit_env_int("AUTOYOU_AUTOPAIR_TOTP_RATE_LIMIT_WINDOW_SECONDS", 60),
)


def _autopair_totp_rate_limit_allowed(key: str) -> bool:
    return AUTOPAIR_TOTP_RATE_LIMITER.is_allowed(str(key or "unknown"))
# Backwards-compatible alias for code and tests that still refer to the
# original single /signal limiter.
SIGNAL_RATE_LIMITER = SIGNAL_OFFER_RATE_LIMITER

_BLUETOOTH_PAIR_PLATFORMS = {"bluetooth", "bluetooth-local"}

# H-2: trusted bridge header - only honoured when the direct peer is
# loopback, i.e. the request arrived through our own tunnelmole bridge.
# Any inbound copy of this header from the public internet is stripped
# by the bridge itself (see shared/tunnelmole_service.py).
_BRIDGE_TRUSTED_CLIENT_IP_HEADER = "x-autoyou-tunnel-client-ip"

def _rate_limit_client_key(request: "Request") -> str:
    """Return a stable key for rate-limiting based on the real client IP.

    When the request enters the process through the tunnelmole bridge on
    loopback, ``request.client.host`` is always ``127.0.0.1`` - which
    means *every* public attacker shares a single rate-limit bucket and
    the limiter is functionally disabled for the tunnel-facing ``/auth``,
    ``/signal`` and admin login endpoints. The bridge mints a trusted
    ``X-AutoYou-Tunnel-Client-IP`` header from tunnelmole's own
    ``X-Forwarded-For``; we trust that header only when the peer we
    observe is loopback.
    """
    direct_host = request.client.host if request.client else ""
    if direct_host and _is_loopback_client_host(direct_host):
        # The request came from the local bridge (or from an operator on
        # the loopback interface). Prefer the trusted per-client IP if
        # the bridge supplied one.
        trusted = ""
        try:
            trusted = str(request.headers.get(_BRIDGE_TRUSTED_CLIENT_IP_HEADER) or "").strip()
        except Exception:
            trusted = ""
        if trusted:
            return f"xff:{trusted}"
    return direct_host or "unknown"

def _autopair_rate_limit_bucket(request: "Request") -> Tuple[str, str]:
    client_key = _rate_limit_client_key(request)
    return f"autopair:{client_key}", client_key

def _select_signal_rate_limiter(signal_type: Optional[str], *, authenticated_session: bool) -> RateLimiter:
    """Choose the appropriate rate limiter for the current /signal request."""
    if not authenticated_session:
        return UNAUTHENTICATED_SIGNAL_RATE_LIMITER

    normalized_signal_type = str(signal_type or "offer").strip().lower()
    if normalized_signal_type == "candidate":
        return SIGNAL_CANDIDATE_RATE_LIMITER
    return SIGNAL_OFFER_RATE_LIMITER

def _signal_rate_limit_bucket(
    request: "Request",
    *,
    session_id: str,
    signal_type: Optional[str],
    authenticated_session: bool,
) -> Tuple[str, str]:
    """Return the limiter bucket key plus the human-readable client key."""
    client_key = _rate_limit_client_key(request)
    if not authenticated_session:
        return client_key, client_key

    normalized_signal_type = str(signal_type or "offer").strip().lower() or "offer"
    return f"{normalized_signal_type}:{client_key}:{session_id}", client_key

# ========= Admin/Auth FastAPI Apps =========
admin_app, auth_app = create_apps(LOGGER)

globals().update(register_admin_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_pairing_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_webrtc_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_agents_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_messaging_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_models_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_services_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_cloud_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_mcp_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_peer_rendezvous_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_moderation_routes(admin_app, auth_app, sys.modules[__name__]))
globals().update(register_website_gateway_routes(admin_app, auth_app, sys.modules[__name__]))

# AI training opt-out: serves /.well-known/ai.txt, /robots.txt, and injects
# X-Robots-Tag + TDM-Reservation headers on every response.
register_ai_opt_out_routes(admin_app)
register_ai_opt_out_routes(auth_app)

async def _serve_admin_logo_file():
    """Serve the branded PNG logo for admin/auth HTML display."""
    logo_path = _get_admin_logo_path()
    if not logo_path.exists():
        return PlainTextResponse("logo.png not found", status_code=404)
    return FileResponse(logo_path, media_type="image/png")

async def _serve_admin_icon_file():
    """Serve logo.ico for favicon/icon requests."""
    ico_path = _get_admin_icon_path()
    if ico_path and ico_path.exists():
        return FileResponse(ico_path, media_type="image/x-icon")
    return await _serve_admin_logo_file()

async def _serve_admin_profile_image_file():
    """Serve the operator-selected sidebar profile image when present."""
    image_path = _get_admin_profile_image_path()
    if image_path is None:
        return PlainTextResponse("profile image not found", status_code=404)
    try:
        image_bytes = read_secure_file(image_path)
    except FileNotFoundError:
        return PlainTextResponse("profile image not found", status_code=404)
    response = Response(content=image_bytes, media_type=_get_admin_profile_image_media_type(image_path))
    response.headers["Cache-Control"] = "no-store"
    return response


install_route_aware_request_logging(
    admin_app,
    logger_name="autoyou.http.admin",
    debug_path_prefixes=(
        "/api/login-startup-status",
        "/api/login-minigame/",
        "/api/wizard/status",
        "/api/model-library/downloads",
        "/api/speech-models/status",
        "/api/speech-models/downloads",
        "/api/ai-agent-server/status",
        "/api/whatsapp/status",
        "/api/datachannel-status",
        "/api/scheduler/notification-queue",
        "/api/scheduler/live-summary",
        "/api/autoyou-page-service/status",
        "/api/signal/status",
    ),
)
install_route_aware_request_logging(
    auth_app,
    logger_name="autoyou.http.auth",
)

# Test endpoint to simulate /pair command
def _test_endpoints_enabled() -> bool:
    """Return True when AUTOYOU_ENABLE_TEST_ENDPOINTS is set to a truthy value.

    Used to gate /api/test/* endpoints so they are unreachable in a normal
    release/runtime build and only accessible when the test harness
    explicitly asks for them.
    """
    raw = os.environ.get("AUTOYOU_ENABLE_TEST_ENDPOINTS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _json_response_no_store(payload: Dict[str, Any], *, status_code: int = 200) -> JSONResponse:
    response = JSONResponse(payload, status_code=status_code)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def _admin_setup_api_route_count() -> int:
    """Return a broad count of authenticated/admin API surfaces for setup copy."""
    count = 0
    for route in getattr(admin_app, "routes", []):
        path = str(getattr(route, "path", "") or "")
        if not path.startswith(("/api/", "/v1/", "/admin/api/", "/admin/security/", "/webhook/")):
            continue
        methods = getattr(route, "methods", None) or {"GET"}
        method_count = len([method for method in methods if method not in {"HEAD", "OPTIONS"}])
        count += max(1, method_count)
    return count


def _software_update_local_status(cfg: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    from shared.update_service import native_store_uri, software_updates_enabled
    from shared.version import get_version

    resolved = cfg if isinstance(cfg, Mapping) else (STATE.config or _default_config())
    configured = _normalize_config_bool(
        (resolved.get("software_update") or {}).get("enabled"),
        True,
    )
    override = str(os.environ.get("AUTOYOU_SOFTWARE_UPDATES_ENABLED") or "").strip()
    token = str((resolved.get("cloud") or {}).get("server_token") or "").strip()
    # Only a real rejection from the feed suppresses the check. The cloud
    # session's local max-age heuristic is a guess, and acting on it here would
    # stop a still-valid credential from ever proving itself again.
    rejected = bool(getattr(STATE, "update_feed_token_rejected", False))
    status: Dict[str, Any] = {
        "enabled": software_updates_enabled(configured),
        "configured_enabled": configured,
        "managed_by_environment": bool(override),
        # A token Core has already rejected is not a usable credential; keeping
        # signed_in true here would hide the re-link action behind a stale
        # bootstrap snapshot.
        "signed_in": bool(token) and not rejected,
        "needs_reregister": bool(token) and rejected,
        "current_version": get_version(),
        "store_managed": bool(native_store_uri()),
    }
    if status["needs_reregister"] and not status["store_managed"]:
        status["message"] = (
            "This server's AutoYou account link is no longer valid. Reconnect the account "
            "to resume signed update checks."
        )
        status["reregister_url"] = "/api/cloud/link-start"
    return status


async def _software_update_status_payload(*, apply: bool = False) -> Dict[str, Any]:
    from shared.update_service import UpdateAuthError, UpdateError, UpdateService

    cfg = STATE.config or _default_config()
    local = _software_update_local_status(cfg)
    if not local["enabled"] and not local["store_managed"]:
        return {"success": True, **local, "message": "Software update checks are disabled."}
    token = str((cfg.get("cloud") or {}).get("server_token") or "").strip()
    if not token and not local["store_managed"]:
        return {
            "success": True,
            **local,
            "message": "Connect this server to an AutoYou account to check for updates.",
        }
    feed_root = str(os.environ.get("AUTOYOU_UPDATE_FEED_BASE") or "").strip()
    if not feed_root:
        feed_root = f"{AUTOYOU_CLOUD_BASE.rstrip('/')}/v1/updates"
    service = UpdateService(
        feed_base=f"{feed_root.rstrip('/')}/autoyou-server",
        product="autoyou-server",
        auth_token=token,
        enabled=True,
    )
    try:
        result = await asyncio.to_thread(service.apply_update if apply else service.check_for_update)
    except UpdateAuthError as exc:
        # The saved account link was revoked or aged out. Record it the same way
        # every other Core call does so the admin panel offers the Cloud Pair
        # link again instead of a dead-end "update check failed".
        STATE.update_feed_token_rejected = True
        STATE.cloud_token_rejected = True
        LOGGER.warning("Software update %s needs a new account link: %s", "apply" if apply else "check", exc)
        return {
            "success": False,
            **local,
            "signed_in": False,
            "needs_reregister": True,
            "reregister_url": "/api/cloud/link-start",
            "error": str(exc),
            "message": str(exc),
        }
    except UpdateError as exc:
        # These messages already name the real cause - an unreachable feed, a
        # bad signature, a dirty working tree - so surfacing the fixed "could
        # not verify the feed" line would mislabel local failures.
        LOGGER.warning("Software update %s failed: %s", "apply" if apply else "check", exc)
        detail = str(exc).strip() or "Could not verify the update feed."
        return {"success": False, **local, "error": detail, "message": detail}
    if local["store_managed"]:
        # Opening the Store does not validate the separate Cloud credential.
        return {"success": True, **local, **result}
    # A successful check proves the credential works, so drop any re-link
    # hints the local snapshot carried in from an earlier rejection.
    STATE.update_feed_token_rejected = False
    STATE.cloud_token_rejected = False
    healthy = dict(local)
    healthy.pop("message", None)
    healthy.pop("reregister_url", None)
    return {"success": True, **healthy, "signed_in": True, "needs_reregister": False, **result}


async def _apply_admin_ui_config_update(
    payload: Dict[str, Any],
    *,
    security_mode: Optional[str] = None,
) -> Dict[str, Any]:
    if isinstance(payload.get("ai_provider"), dict) and str(payload["ai_provider"].get("provider", "")).strip().lower() == "apple_intelligence":
        from shared.apple_intelligence import status as apple_intelligence_status
        apple = await apple_intelligence_status()
        if not apple["available"]:
            raise ValueError(apple["detail"])
    updated_cfg, touched_sections, resolved_ui_theme = _apply_admin_ui_config_patch(
        _loaded_config_for_update(),
        payload,
    )
    requested_ai_port = None
    if isinstance(payload.get("ai_agent"), dict) and "port" in payload["ai_agent"]:
        from core_server.services import validate_ai_agent_port_change
        requested_ai_port = updated_cfg["ai_agent"]["port"]
        validate_ai_agent_port_change(requested_ai_port)
    if security_mode:
        mode = _normalize_security_mode(security_mode, default="")
        if mode not in {
            "normal",
            "secure",
            "secure_professional",
            SECURE_PROFESSIONAL_MAXIMUS_MODE,
        }:
            raise ValueError(
                "mode must be normal, secure, secure_professional, or secure_professional_maximus"
            )
        updated_cfg.setdefault("security", {})["mode"] = mode

    previous_page_bind_host = _page_service_bind_host(STATE.config or {})
    STATE.config = _save_and_reload_state_config(updated_cfg)

    if "server" in touched_sections:
        # Discovery and the websites port follow their settings live; the
        # process bind host itself still needs a restart.
        await sync_server_advertisement()
        if (
            "autoyou_page" not in touched_sections
            and _page_service_bind_host(STATE.config) != previous_page_bind_host
            and AUTOYOU_PAGE_SERVICE_AVAILABLE
            and await is_autoyou_page_service_running()
        ):
            await start_or_restart_autoyou_page_service()

    if "ai_agent" in touched_sections:
        _apply_ai_agent_runtime_settings(
            {
                "internet_search_enabled": bool(
                    (STATE.config or {}).get("ai_agent", {}).get(
                        "internet_search_enabled",
                        True,
                    )
                )
            }
        )

    if resolved_ui_theme is not None:
        _persist_autoyou_ui_theme(resolved_ui_theme, cfg=STATE.config, persist_config=True)

    if "telegram" in touched_sections:
        await start_or_restart_telegram()
    if "telegram_user" in touched_sections:
        await start_or_restart_telegram_user()
    if "server" in touched_sections and STATE.whatsapp_service is not None:
        update_server_name = getattr(STATE.whatsapp_service, "update_server_name", None)
        if callable(update_server_name):
            try:
                result = update_server_name(get_configured_server_name())
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                LOGGER.debug("Could not update the live WhatsApp server name: %s", exc)
    if "signal" in touched_sections:
        await start_or_restart_signal()
    if "whatsapp" in touched_sections:
        await start_or_restart_whatsapp()
    if "speech" in touched_sections:
        _apply_speech_config_to_active_audio_managers()
    if "video_call" in touched_sections and WEBRTC is not None:
        await WEBRTC.apply_video_call_settings()
    if "autoyou_page" in touched_sections:
        await start_or_restart_autoyou_page_service()
    if "admin_frontend" in touched_sections:
        _register_admin_frontend_proxy()
    if "tunnelmole" in touched_sections:
        if not bool((STATE.config or {}).get("tunnelmole", {}).get("enabled", False)):
            await stop_tunnelmole_service(reason="Tunnelmole disabled from admin dashboard config")
    if "bluetooth_pairing" in touched_sections:
        if _is_bluetooth_pairing_enabled():
            await start_or_restart_bluetooth_pairing_service()
        else:
            await stop_bluetooth_pairing_service("Bluetooth Pair disabled from admin dashboard config")
    if touched_sections & {"ai_agent", "client_identity"} and not _client_name_history_enabled():
        WEBRTC.clear_client_name_overrides()
    if touched_sections & {"ollama", "ai_provider", "ai_agent"}:
        if touched_sections & {"ollama", "ai_provider"}:
            _apply_google_api_config_to_env()
            if "ollama" in touched_sections or (
                "ai_provider" in touched_sections and _is_native_gateway_provider()
            ):
                try:
                    from shared.ollama_gateway import reset_ollama_model_cache

                    reset_ollama_model_cache()
                except Exception as exc:
                    LOGGER.debug("Could not reset native Ollama gateway cache: %s", exc)
            if "ai_provider" in touched_sections:
                try:
                    from shared.odysseus_gateway import reset_odysseus_cache

                    reset_odysseus_cache()
                except Exception as exc:
                    LOGGER.debug("Could not reset Odysseus gateway cache: %s", exc)
        if _is_native_gateway_provider():
            await stop_ai_agent_server()
            if requested_ai_port is not None:
                from core_server.services import set_ai_agent_runtime_port
                set_ai_agent_runtime_port(requested_ai_port)
            if str(os.getenv("AI_PROVIDER") or "").strip().lower() == "ollama_gateway":
                await asyncio.to_thread(_ensure_local_ollama_runtime_ready)
        elif bool((STATE.config or {}).get("ai_agent", {}).get("enabled", True)):
            restarted = (await restart_ai_agent_server(port=requested_ai_port)
                         if requested_ai_port is not None else await restart_ai_agent_server())
            if restarted is False:
                raise RuntimeError("Settings were saved, but AutoYou AI could not restart. Check its port and server log.")
        else:
            await stop_ai_agent_server()

            if requested_ai_port is not None:
                from core_server.services import set_ai_agent_runtime_port
                set_ai_agent_runtime_port(requested_ai_port)

    return await _build_admin_ui_bootstrap_payload()


def _build_boot_sweep_widget_html(
    *,
    title: str,
    description: str,
    board_id: str,
    score_id: str,
    best_score_id: str,
    note: str,
    heading_tag: str = "h3",
    eyebrow: str = "Boot sweep",
    actions_html: str = "",
    status_id: Optional[str] = None,
    current_label: str = "Current run",
    best_label: str = "Best run (clicks/s \u00d7 100)",
    best_score_initial: str = "0",
    history_table_id: Optional[str] = None,
    history_title: str = "Top 10 High Scores",
    history_caption: str = "Recent best runs saved on this server.",
) -> str:
    actions_block = f"<div class='boot-sweep-actions'>{actions_html}</div>" if actions_html else ""
    status_block = f"<div id='{status_id}' class='boot-sweep-status'></div>" if status_id else ""
    history_block = ""
    if history_table_id:
        history_block = f"""
            <div class='boot-sweep-history'>
              <div class='boot-sweep-history-head'>
                <strong>{history_title}</strong>
                <span>{history_caption}</span>
              </div>
              <div class='boot-sweep-table-shell'>
                <table class='boot-sweep-table'>
                  <thead>
                    <tr>
                      <th scope='col'>Score</th>
                      <th scope='col'>Date / Time</th>
                      <th scope='col'>Clicks</th>
                      <th scope='col'>Seconds</th>
                    </tr>
                  </thead>
                  <tbody id='{history_table_id}'>
                    <tr class='boot-sweep-empty-row'>
                      <td colspan='4'>No saved high scores yet.</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
        """
    return f"""
          <div class='boot-sweep-card'>
            <div class='eyebrow'>{eyebrow}</div>
            <{heading_tag}>{title}</{heading_tag}>
            <p class='boot-sweep-copy'>{description}</p>
            <div id='{board_id}' class='boot-sweep-board' aria-label='Boot sweep mini game'></div>
            {actions_block}
            {status_block}
            <div class='boot-sweep-score'>
              <div class='score-pill'>
                <strong id='{score_id}'>0</strong>
                <span>{current_label}</span>
              </div>
              <div class='score-pill'>
                <strong id='{best_score_id}'>{best_score_initial}</strong>
                <span>{best_label}</span>
              </div>
            </div>
            <div class='boot-sweep-note'>{note}</div>
            {history_block}
          </div>
    """

def _build_boot_sweep_runtime_script(config: Dict[str, Any]) -> str:
    config_json = json.dumps(config)
    script = """
    <script>
    (function() {
      window.AutoYouBootSweep = window.AutoYouBootSweep || (function() {
        function create(config) {
          const board = document.getElementById(config.boardId);
          const scoreEl = document.getElementById(config.scoreId);
          const bestScoreEl = document.getElementById(config.bestScoreId);
          const historyTableEl = config.historyTableId ? document.getElementById(config.historyTableId) : null;
          const statusEl = config.statusId ? document.getElementById(config.statusId) : null;
          const startBtn = config.startButtonId ? document.getElementById(config.startButtonId) : null;
          const stopBtn = config.stopButtonId ? document.getElementById(config.stopButtonId) : null;
          const resetBtn = config.resetButtonId ? document.getElementById(config.resetButtonId) : null;
          const resetStatusEl = config.resetStatusId ? document.getElementById(config.resetStatusId) : null;
          let timer = null;
          let score = 0;
          let bestScore = 0;
          let startTime = 0;
          let rampTick = 0;
          let running = false;
          let bestLoaded = false;
          let highScores = [];

          function emptyBestText() {
            return config.emptyBestText || '0';
          }

          function escapeHtml(value) {
            return String(value || '')
              .replaceAll('&', '&amp;')
              .replaceAll('<', '&lt;')
              .replaceAll('>', '&gt;')
              .replaceAll('"', '&quot;')
              .replaceAll("'", '&#39;');
          }

          function normalizeRun(entry) {
            const scoreValue = Math.max(0, Number(entry && entry.score) || 0);
            const clicksValue = Math.max(0, Number(entry && entry.clicks) || 0);
            const secondsValue = Math.max(0, Number(entry && entry.seconds) || 0);
            return {
              id: Number(entry && entry.id) || 0,
              score: Math.round(scoreValue),
              clicks: Math.round(clicksValue),
              seconds: secondsValue,
              recorded_at: String((entry && entry.recorded_at) || ''),
            };
          }

          function formatRecordedAt(value) {
            if (!value) {
              return 'Unknown';
            }
            const parsed = new Date(value);
            if (Number.isNaN(parsed.getTime())) {
              return String(value);
            }
            return new Intl.DateTimeFormat(undefined, {
              year: 'numeric',
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
            }).format(parsed);
          }

          function renderHistory() {
            if (!historyTableEl) {
              return;
            }
            if (!Array.isArray(highScores) || !highScores.length) {
              historyTableEl.innerHTML = "<tr class='boot-sweep-empty-row'><td colspan='4'>No saved high scores yet.</td></tr>";
              return;
            }
            historyTableEl.innerHTML = highScores
              .map((entry) => (
                "<tr>"
                + "<td>" + escapeHtml(String(entry.score || 0)) + "</td>"
                + "<td>" + escapeHtml(formatRecordedAt(entry.recorded_at)) + "</td>"
                + "<td>" + escapeHtml(String(entry.clicks || 0)) + "</td>"
                + "<td>" + escapeHtml((Number(entry.seconds || 0)).toFixed(1)) + "</td>"
                + "</tr>"
              ))
              .join('');
          }

          function setStatus(text) {
            if (statusEl) {
              statusEl.textContent = text || '';
            }
          }

          function setResetStatus(text, timeoutMs) {
            if (!resetStatusEl) return;
            resetStatusEl.textContent = text || '';
            if (!text) return;
            window.setTimeout(() => {
              if (resetStatusEl.textContent === text) {
                resetStatusEl.textContent = '';
              }
            }, timeoutMs || 2600);
          }

          function updateScore() {
            if (scoreEl) {
              scoreEl.textContent = String(score);
            }
          }

          function updateBest() {
            if (bestScoreEl) {
              bestScoreEl.textContent = bestScore > 0 ? String(bestScore) : emptyBestText();
            }
          }

          function syncControls() {
            if (startBtn) startBtn.disabled = running;
            if (stopBtn) stopBtn.disabled = !running;
          }

          async function loadBest() {
            if (bestLoaded) {
              updateBest();
              renderHistory();
              return bestScore;
            }
            try {
              const response = await fetch('/api/login-minigame/high-score', {
                method: 'GET',
                cache: 'no-store',
                credentials: 'same-origin',
              });
              if (response.ok) {
                const payload = await response.json().catch(() => ({}));
                highScores = Array.isArray(payload.high_scores)
                  ? payload.high_scores.map(normalizeRun)
                  : [];
                bestScore = Math.max(
                  0,
                  Number(payload.high_score) || 0,
                  highScores.length ? Number(highScores[0].score) || 0 : 0,
                );
                bestLoaded = true;
              }
            } catch (error) {
              // High score is auxiliary UI state only.
            }
            updateBest();
            renderHistory();
            return bestScore;
          }

          async function persistRun(scoreValue, clickCount, elapsedSeconds) {
            try {
              const response = await fetch('/api/login-minigame/high-score', {
                method: 'POST',
                headers: {
                  'Content-Type': 'application/json',
                  'Accept': 'application/json',
                },
                credentials: 'same-origin',
                keepalive: true,
                body: JSON.stringify({
                  score: scoreValue,
                  clicks: clickCount,
                  seconds: Number(elapsedSeconds || 0),
                }),
              });
              if (response.ok) {
                const payload = await response.json().catch(() => ({}));
                highScores = Array.isArray(payload.high_scores)
                  ? payload.high_scores.map(normalizeRun)
                  : highScores;
                bestScore = Math.max(scoreValue, Number(payload.high_score) || 0);
                bestLoaded = true;
              }
            } catch (error) {
              // Ignore persistence failures; the UI should stay responsive.
            }
            updateBest();
            renderHistory();
            return bestScore;
          }

          function removeNode(node) {
            if (node && node.parentNode) {
              node.parentNode.removeChild(node);
            }
          }

          function spawnNode() {
            if (!board || !running) return;
            const existing = Array.from(board.querySelectorAll('.boot-sweep-node')).map((node) => ({
              x: parseFloat(node.style.left),
              y: parseFloat(node.style.top),
            }));
            let left;
            let top;
            let attempt = 0;
            do {
              left = 8 + Math.random() * 82;
              top = 8 + Math.random() * 76;
              attempt += 1;
            } while (attempt < 12 && existing.some((entry) => Math.abs(entry.x - left) < 14 && Math.abs(entry.y - top) < 18));
            const node = document.createElement('button');
            node.type = 'button';
            node.className = 'boot-sweep-node';
            node.style.left = left + '%';
            node.style.top = top + '%';
            node.title = config.nodeTitle || 'Catch signal ping';
            const hitNode = (event) => {
              if (event) {
                event.preventDefault();
                if (typeof event.stopPropagation === 'function') {
                  event.stopPropagation();
                }
              }
              score += 1;
              updateScore();
              removeNode(node);
              spawnNode();
              spawnNode();
            };
            node.addEventListener('click', hitNode);
            node.addEventListener('touchend', hitNode, { passive: false });
            board.appendChild(node);
            window.setTimeout(() => removeNode(node), 2200 + Math.random() * 1600);
          }

          function start() {
            if (!board || running) return;
            board.innerHTML = '';
            score = 0;
            rampTick = 0;
            running = true;
            startTime = Date.now();
            updateScore();
            updateBest();
            setStatus(config.runningStatus || 'Running - click the pings!');
            syncControls();
            for (let index = 0; index < 5; index += 1) {
              window.setTimeout(spawnNode, index * 160);
            }
            timer = window.setInterval(() => {
              if (!board) return;
              rampTick += 1;
              const maxNodes = rampTick < 6 ? 6 + rampTick : 14;
              const deficit = maxNodes - board.childElementCount;
              for (let idx = 0; idx < Math.min(deficit, 3); idx += 1) {
                spawnNode();
              }
            }, 400);
          }

          async function stop(options) {
            const settings = options || {};
            const shouldReport = settings.report !== false;
            if (!running && !timer && !startTime) {
              syncControls();
              return 0;
            }
            running = false;
            if (timer) {
              window.clearInterval(timer);
              timer = null;
            }
            if (board) {
              board.innerHTML = '';
            }
            syncControls();
            if (startTime > 0 && score > 0) {
              const elapsedSeconds = Math.max(1, (Date.now() - startTime) / 1000);
              const finalScore = Math.round((score / elapsedSeconds) * 100);
              const clickCount = score;
              if (shouldReport) {
                setStatus((config.completePrefix || 'Score: ') + finalScore + ' (' + clickCount + ' clicks in ' + elapsedSeconds.toFixed(1) + 's)');
              }
              startTime = 0;
              if (finalScore > 0) {
                await persistRun(finalScore, clickCount, Number(elapsedSeconds.toFixed(3)));
              }
              return finalScore;
            }
            startTime = 0;
            if (shouldReport) {
              setStatus(config.stoppedStatus || 'Stopped.');
            }
            return 0;
          }

          async function resetBest() {
            try {
              const response = await fetch('/api/login-minigame/high-score', {
                method: 'DELETE',
                credentials: 'same-origin',
              });
              if (!response.ok) {
                throw new Error('Server error');
              }
              bestScore = 0;
              highScores = [];
              bestLoaded = true;
              updateBest();
              renderHistory();
              setResetStatus(config.resetSuccessText || 'Score reset.');
            } catch (error) {
              setResetStatus(config.resetFailureText || 'Failed to reset score.', 3200);
            }
          }

          if (startBtn) startBtn.addEventListener('click', start);
          if (stopBtn) stopBtn.addEventListener('click', () => {
            void stop();
          });
          if (resetBtn) resetBtn.addEventListener('click', () => {
            void resetBest();
          });

          syncControls();
          void loadBest().then(() => {
            if (config.autoStart) {
              start();
            }
          });

          return {
            start,
            stop,
            loadBest,
            resetBest,
            get running() {
              return running;
            },
          };
        }

        return { create };
      })();

      const config = __BOOT_SWEEP_CONFIG__;
      const instance = window.AutoYouBootSweep.create(config || {});
      if (config && config.instanceName) {
        window[config.instanceName] = instance;
      }
    })();
    </script>
    """
    return script.replace("__BOOT_SWEEP_CONFIG__", config_json)

admin_app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://127.0.0.1"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========= H-15: CSRF protection (Origin/Referer check) =========
# The admin session cookie already uses ``SameSite=Strict`` which defeats
# classic third-party CSRF. We additionally require every state-mutating
# request to carry an ``Origin`` (or at minimum ``Referer``) that matches
# the request's own host. Modern browsers attach ``Origin`` automatically
# to every cross-origin POST/PUT/DELETE/PATCH - an attacker running a
# form submission or ``fetch()`` from evil.autoyou.me cannot forge it.
# Request tooling that legitimately needs to bypass this (curl, local
# scripts) can unset the check via ``AUTOYOU_DISABLE_ORIGIN_CSRF=1``
# or present a valid admin session via a non-browser path.
_CSRF_PROTECTED_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

#: Names of security controls already reported as disabled, so the warning is
#: loud on first use without flooding the log on every request.
_DISABLED_SECURITY_CONTROLS_WARNED: set = set()


def _warn_security_control_disabled(env_var: str, consequence: str) -> None:
    """Log, once per control, that an environment variable turned a guard off.

    A security boundary that can be switched off by the process environment
    must at minimum be visible in the logs when it has been - otherwise a flag
    set once for debugging survives into production silently.
    """
    if env_var in _DISABLED_SECURITY_CONTROLS_WARNED:
        return
    _DISABLED_SECURITY_CONTROLS_WARNED.add(env_var)
    LOGGER.warning("SECURITY: %s is set. %s", env_var, consequence)
# Endpoints that must remain reachable without an Origin match - used by
# the login page before a session exists, and by plain-JSON bootstrap
# probes that some legacy clients issue without an Origin header.
_CSRF_EXEMPT_PATH_PREFIXES: tuple = (
    "/login",
    "/api/login-minigame/high-score",  # pre-auth mini-game scoreboard
)
_PUBLIC_LEGAL_PATHS = ("/LICENSE", "/NOTICE.txt", "/THIRD-PARTY-NOTICES.md", "/sbom.cdx.json")
_CURRENT_TERMS_ACCEPTANCE_LABEL = "Terms of Use (EULA), License, responsibility terms, warranty disclaimer, and liability limits; personal, non-commercial use only unless a separate written OpenStorey agreement applies"

def _csrf_request_host_is_loopback(request_host: str) -> bool:
    host = str(request_host or "").split(",", 1)[0].strip().lower()
    if not host:
        return False
    if host.startswith("["):
        end = host.find("]")
        hostname = host[1:end] if end > 0 else host.strip("[]")
    elif host.count(":") == 1:
        hostname = host.rsplit(":", 1)[0]
    else:
        hostname = host
    return hostname in {"localhost", "127.0.0.1", "::1"} or hostname.startswith("127.")

def _csrf_peer_is_private_or_loopback(peer_host: str) -> bool:
    normalized = str(peer_host or "").strip().lower()
    if not normalized:
        return False
    if _is_loopback_client_host(normalized):
        return True
    try:
        import ipaddress

        ip = ipaddress.ip_address(normalized.split("%", 1)[0])
        return bool(ip.is_private or ip.is_loopback)
    except Exception:
        return False

def _csrf_allows_packaged_desktop_autopair_without_origin(
    raw_path: str,
    request_host: str,
    peer_host: str,
) -> bool:
    if raw_path != "/api/autopair":
        return False
    if os.environ.get("AUTOYOU_PACKAGED_RUNTIME", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    return _csrf_request_host_is_loopback(request_host) and _csrf_peer_is_private_or_loopback(peer_host)

def _csrf_allows_native_local_pair_autopair_without_origin(
    raw_path: str,
    peer_host: str,
) -> bool:
    """Allow native Local Pair clients on the LAN to reach /api/autopair.

    Native clients (iOS/Android/desktop Connect apps) POST the pairing offer
    without browser Origin/Referer headers. The endpoint is independently
    protected by the admin_session cookie and the password hash inside the
    payload, and browsers always attach Origin to cross-origin mutating
    requests, so a missing Origin cannot be a browser-forged request. Public
    (non-private) peers stay blocked.
    """
    if raw_path != "/api/autopair":
        return False
    return _csrf_peer_is_private_or_loopback(peer_host)


def _csrf_allows_mcp_token_without_origin(raw_path: str, request: "Request") -> bool:
    """Allow the non-browser MCP adapter to use its bearer boundary.

    MCP calls are service-to-service JSON requests and do not carry browser
    Origin/Referer headers.  The dedicated bearer token is the CSRF boundary;
    invalid or missing tokens still fail in the route-level authorization.
    """
    return raw_path.startswith("/api/v1/mcp/") and _mcp_request_uses_api_token(request)

def _csrf_origin_allowed(request_host: str, candidate: str) -> bool:
    """Return True when ``candidate`` refers to the same origin as ``request_host``."""
    if not candidate:
        return False
    try:
        from urllib.parse import urlsplit as _csrf_urlsplit
        parsed = _csrf_urlsplit(candidate)
    except Exception:
        return False
    netloc = (parsed.netloc or "").lower().strip()
    if not netloc:
        return False
    # Tunnelmole edge forwards to the local bridge; the Host header the
    # FastAPI app sees will be the tunnelmole subdomain (because the
    # tunnelmole CLI preserves Host by default) so matching on the
    # raw Host header is the right check for remote admin sessions too.
    return netloc == str(request_host or "").lower().strip()


def _get_unlock_state() -> str:
    if getattr(STATE, "_unlock_state_mem", None) == "Ready":
        return "Ready"
    path = _get_unlock_file_path()
    if not path.exists():
        return "Locked" if _saved_config_exists() else "SetupPending"
    return "Locked"


async def _check_unlock_guard(request: Request, call_next):
    raw_path = (request.url.path or "").rstrip("/") or "/"

    if raw_path in _PUBLIC_LEGAL_PATHS or raw_path in [
        "/v1/unlock/status",
        "/v1/unlock/setup",
        "/v1/unlock/verify",
        "/login",
        "/api/local-pair-helper",
        "/api/login/native-unlock",
        "/api/login/factory-reset",
    ]:
        return await call_next(request)

    if raw_path.startswith("/assets") or raw_path.endswith(".ico") or raw_path.endswith(".png") or raw_path.endswith(".css") or raw_path.endswith(".js"):
        return await call_next(request)

    state = _get_unlock_state()
    if state != "Ready":
        is_api = "/api/" in raw_path or raw_path.startswith("/v1/") or raw_path.startswith("/auth") or raw_path.startswith("/signal")
        if is_api:
            if state == "SetupPending":
                return JSONResponse(
                    status_code=451,
                    content={
                        "success": False,
                        "error": f"Setup pending. Please set up the master password and accept the current AutoYou {_CURRENT_TERMS_ACCEPTANCE_LABEL} at /v1/unlock/setup.",
                        "state": "SetupPending"
                    }
                )
            else: # Locked
                return JSONResponse(
                    status_code=401,
                    content={
                        "success": False,
                        "error": "Server is locked. Please verify password at /v1/unlock/verify",
                        "state": "Locked"
                    }
                )
        else:
            return RedirectResponse(url="/login", status_code=307)

    return await call_next(request)


@admin_app.middleware("http")
async def _admin_server_unlock_guard(request: Request, call_next):
    return await _check_unlock_guard(request, call_next)


# ========= Remote browser credential boundary =========
# A paired device's browser reaches the admin app through the WebRTC proxy, and
# a home-network browser can through the page service; both arrive from
# 127.0.0.1, so localhost checks cannot tell them from the operator at this
# computer. Whatever remote role is configured, such a request may sign in and
# read, but it never changes this server's credentials or security posture.
# That stays with this computer and with an HTTPS admin session opened directly
# on the admin port.
_REMOTE_BROWSER_CREDENTIAL_PATHS = frozenset({
    "/api/admin/password",
    "/api/admin/security/mode",
    "/api/admin/security/tier",
    "/api/admin/security/storage/rotate",
    "/api/native/security/totp/setup",
    "/api/native/security/totp/confirm",
    "/api/native/security/totp/import",
    "/api/native/security/totp/delete",
    "/api/agent-websites/security",
    "/api/agent-security/profiles",
    "/api/login/factory-reset",
    "/change-password",
    "/save-security",
    "/admin/security/totp/generate",
    "/admin/security/totp/import",
    "/admin/security/totp/delete",
    "/v1/unlock/setup",
})
_REMOTE_BROWSER_CREDENTIAL_PATH_PATTERNS = (
    re.compile(r"^/api/agent-security/profiles/[^/]+/(?:assign|wipe)$"),
    re.compile(r"^/api/agent-security/agents/[^/]+/wipe$"),
)
#: Config a remote browser cannot write through /api/admin/config, because it
#: widens who can reach or change this server. ``None`` protects the section.
_REMOTE_BROWSER_PROTECTED_CONFIG: Dict[str, Optional[Set[str]]] = {
    "security": None,
    "admin_frontend": None,
    "tunnelmole": None,
    "mcp": None,
    "server": {"bind_host", "https_enabled", "https_port", "home_network_websites", "discovery_enabled"},
    "autoyou_page": {"remote_access_role"},
    "ai_agent": {"lan_access_enabled"},
}
REMOTE_BROWSER_CREDENTIAL_DENIAL = (
    "Passwords, two-factor and network security settings can only be changed on this "
    "computer, or in an HTTPS admin session opened directly on it - not from a "
    "connected device's browser."
)
REMOTE_BROWSER_CLOUD_CONFIG_DENIAL = (
    "Server Cloud Pair settings must be changed through the authenticated Cloud controls, "
    "not by editing server configuration from a connected device."
)


def _request_via_remote_browser_proxy(request: Request) -> bool:
    """Whether AutoYou forwarded this request for a paired or home-network browser."""
    peer = request.client.host if request.client else None
    if not _is_loopback_client_host(peer):
        return False
    return bool(
        request.headers.get(REMOTE_BROWSER_HEADER)
        or request.headers.get("X-AutoYou-WebRTC-Session-Id")
        or request.headers.get("X-AutoYou-Agent-Frontend")
    )


def _local_pair_device_ownership(request: Request) -> str:
    """Local Pair from this computer itself is the owner's; from anywhere else it is shared."""
    peer = request.client.host if request.client else None
    if _is_loopback_client_host(peer) and not _request_via_remote_browser_proxy(request):
        return DEVICE_OWN
    return DEVICE_SHARED


def _remote_browser_credential_denied() -> JSONResponse:
    return JSONResponse(status_code=403, content={"success": False, "error": REMOTE_BROWSER_CREDENTIAL_DENIAL})


def _remote_browser_config_change_error(request: Request, payload: Any) -> Optional[JSONResponse]:
    if not isinstance(payload, dict) or not _request_via_remote_browser_proxy(request):
        return None
    if "cloud" in payload:
        return JSONResponse(status_code=403, content={"success": False, "error": REMOTE_BROWSER_CLOUD_CONFIG_DENIAL})
    for section, keys in _REMOTE_BROWSER_PROTECTED_CONFIG.items():
        value = payload.get(section)
        if value is None:
            continue
        if keys is None or not isinstance(value, dict) or keys.intersection(value):
            return _remote_browser_credential_denied()
    return None


@admin_app.middleware("http")
async def _admin_remote_browser_credential_guard(request: Request, call_next):
    if _request_via_remote_browser_proxy(request):
        raw_path = (request.url.path or "").rstrip("/") or "/"
        if server_cloud_action_requires_admin(request.method, raw_path):
            role = normalize_remote_access_role(request.headers.get("X-AutoYou-Remote-Access-Role"))
            if not remote_http_request_allowed(role, request.method, raw_path):
                return JSONResponse(
                    status_code=403,
                    content={
                        "success": False,
                        "error": remote_access_denial_message(role, request.method, raw_path),
                    },
                )
        if request.method.upper() in _CSRF_PROTECTED_METHODS:
            if raw_path in _REMOTE_BROWSER_CREDENTIAL_PATHS or any(
                pattern.match(raw_path) for pattern in _REMOTE_BROWSER_CREDENTIAL_PATH_PATTERNS
            ):
                return _remote_browser_credential_denied()
    return await call_next(request)


@auth_app.middleware("http")
async def _auth_server_unlock_guard(request: Request, call_next):
    return await _check_unlock_guard(request, call_next)


@admin_app.middleware("http")
async def _agent_decryption_guard(request: "Request", call_next):
    """
    Ensure that agent proxy and management routes are not accessible until the server
    is successfully decrypted and initialized.
    """
    raw_path = (request.url.path or "").rstrip("/") or "/"

    if raw_path.startswith("/agent/") or raw_path.startswith("/api/agent-") or raw_path.startswith("/api/agents/"):
        if not _has_loaded_config_session() or not STATE.config:
            LOGGER.warning("Blocked premature access to %s: Server configuration is not yet decrypted.", raw_path)
            if raw_path.startswith("/api/"):
                return JSONResponse(
                    status_code=401,
                    content={"success": False, "error": "Server is locked. Please enter the decryption password first."}
                )
            return RedirectResponse(url="/login", status_code=302)

    return await call_next(request)

@admin_app.middleware("http")
async def _csrf_origin_guard(request: "Request", call_next):
    try:
        if os.environ.get("AUTOYOU_DISABLE_ORIGIN_CSRF", "").strip().lower() in {"1", "true", "yes", "on"}:
            _warn_security_control_disabled(
                "AUTOYOU_DISABLE_ORIGIN_CSRF",
                "Origin/Referer CSRF protection is OFF for every state-mutating admin "
                "request. Any site the operator visits can drive this admin API.",
            )
            return await call_next(request)

        method = (request.method or "").upper()
        if method not in _CSRF_PROTECTED_METHODS:
            return await call_next(request)

        raw_path = (request.url.path or "").rstrip("/") or "/"
        # Exact or prefix match against exempt list (login etc.)
        for prefix in _CSRF_EXEMPT_PATH_PREFIXES:
            if raw_path == prefix or raw_path.startswith(prefix + "/"):
                return await call_next(request)

        host = request.headers.get("host", "")
        origin = request.headers.get("origin", "") or ""
        referer = request.headers.get("referer", "") or ""

        origin_ok = _csrf_origin_allowed(host, origin)
        if not origin_ok and referer:
            origin_ok = _csrf_origin_allowed(host, referer)

        # If neither Origin nor Referer was provided we fall back to
        # requiring a loopback peer - this keeps CLI tooling running
        # from the same machine working while still rejecting
        # browser-originated cross-site requests (browsers attach
        # Origin on every state-mutating cross-origin request).
        if not origin and not referer:
            peer = request.client.host if request.client else ""
            if (
                _is_loopback_client_host(peer)
                or _csrf_allows_packaged_desktop_autopair_without_origin(raw_path, host, peer)
                or _csrf_allows_native_local_pair_autopair_without_origin(raw_path, peer)
                or _csrf_allows_mcp_token_without_origin(raw_path, request)
            ):
                return await call_next(request)
            LOGGER.warning(
                "Blocked %s %s from %s: missing Origin/Referer on mutating request.",
                method,
                raw_path,
                peer or "unknown",
            )
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "Cross-site request blocked. Missing Origin/Referer."},
            )

        if not origin_ok:
            LOGGER.warning(
                "Blocked %s %s: Origin/Referer mismatch (host=%r origin=%r referer=%r).",
                method,
                raw_path,
                host,
                origin,
                referer,
            )
            return JSONResponse(
                status_code=403,
                content={"success": False, "error": "Cross-site request blocked. Origin does not match server host."},
            )
        return await call_next(request)
    except Exception as exc:
        # Never let the CSRF check itself take the admin app down - log
        # and fall through so a bug here doesn't create a 100 % outage.
        LOGGER.exception("CSRF origin guard failed open: %s", exc)
        return await call_next(request)

# Register graceful Internet Agent Browser cleanup on FastAPI shutdown
try:
    register_fastapi_cleanup(admin_app)
except Exception as e:
    # Non-fatal: continue server startup even if hook registration fails
    LOGGER.warning(f"Failed to register Playwright cleanup hook on admin app: {e}")

def _get_pairing_ice_servers() -> list:
    """Return locally-configured connection helpers for OTP auth/signal responses.

    This is the synchronous baseline list. For responses that should *also*
    advertise cloud-provisioned connection helpers when the user is entitled,
    use `_get_pairing_ice_servers_async()` instead - it merges this list
    with whatever the cloud's `/v1/services` manifest exposes for the
    user's tier.
    """
    if STATE.config:
        rtc = STATE.config.get("rtc", {})
        ice = rtc.get("iceServers")
        if isinstance(ice, list) and ice:
            return ice
    return list(DEFAULT_ICE_SERVERS)

def _ensure_cloud_entitlements() -> Optional[CloudEntitlementsClient]:
    """Lazily build / refresh the CloudEntitlementsClient on STATE.

    Returns None when no cloud creds are configured yet. Safe to call
    repeatedly - will reuse an existing client and only update credentials
    when the underlying server token has actually changed.
    """
    cloud_cfg = (STATE.config or {}).get("cloud") or {}
    server_token = (cloud_cfg.get("server_token") or "").strip()
    if not server_token or cloud_cfg.get("pair_enabled") is False:
        STATE.cloud_entitlements = None
        return None
    existing = STATE.cloud_entitlements
    if existing is None:
        client = CloudEntitlementsClient(
            account_api_url=AUTOYOU_CLOUD_BASE,
            pb_token=server_token,
        )
        STATE.cloud_entitlements = client
        return client
    # Update creds in place so cached entitlements stay coherent across
    # token rotations; this also drops any stale JWT/manifest cache.
    existing.update_credentials(
        account_api_url=AUTOYOU_CLOUD_BASE,
        pb_token=server_token,
    )
    return existing

async def _get_pairing_ice_servers_async() -> list:
    """Return connection helpers for /auth + autopair responses with cloud merge.

    Merges admin-configured iceServers with cloud-provided helpers when the
    user holds the matching entitlement. Falls back silently to local-only
    on any cloud failure - never blocks an /auth response on a cloud round-trip.
    """
    base = _get_pairing_ice_servers()
    client = _ensure_cloud_entitlements()
    if client is None or not client.has_credentials():
        return base
    try:
        def _merge(local: list, extra: list) -> list:
            return dedupe_ice_servers(list(local) + list(extra))

        return await client.get_iceservers_to_advertise(base, merge_with=_merge)
    except Exception as exc:
        LOGGER.warning("cloud_entitlements: iceservers merge failed: %s", exc)
        return base

async def _maybe_send_diagnostics_heartbeat() -> None:
    """Best-effort: send a privacy-minimal NAT-class heartbeat to the cloud.

    Only re-fires when the measured NAT class differs from the last
    successfully reported value, so a steady-state server generates zero
    diagnostics traffic. Any failure is silent and non-fatal - diagnostics
    inform connection-helper provisioning but never block /auth or pairing.
    """
    client = _ensure_cloud_entitlements()
    if client is None or not client.has_credentials():
        return
    try:
        from shared.stun_classifier import classify_nat
        nat_class = await classify_nat()
    except Exception as exc:
        LOGGER.debug("cloud_entitlements: NAT classification failed: %s", exc)
        return
    last = STATE.last_diagnostic_nat_class
    if nat_class == last:
        return
    try:
        ok = await client.send_diagnostics_heartbeat(role="server", nat_class=nat_class)
    except Exception as exc:
        LOGGER.debug("cloud_entitlements: diagnostics heartbeat failed: %s", exc)
        return
    if ok:
        STATE.last_diagnostic_nat_class = nat_class
        LOGGER.info(
            "AutoYou Cloud diagnostics: server heartbeat accepted (nat_class=%s)",
            nat_class,
        )

async def _maybe_apply_paid_tunnelmole_env() -> None:
    """If the user holds the `tm` entitlement, mint a worker JWT and stash it
    in the env vars TunnelmoleService reads. No-op for free-tier users.

    The TunnelmoleService reads AUTOYOU_TUNNELMOLE_JWT + AUTOYOU_TUNNELMOLE_TIER
    at .start() time to decide between the free /v1/provision/free
    (IP-rate-limited) and the paid /v1/provision (persistent URL) endpoints.

    Token-leak protection: we only stash the JWT when the configured
    AUTOYOU_TUNNELMOLE_REMOTE_HOST (if any) targets tm.autoyou.me. If the
    operator explicitly pointed tunnelmole at a third-party host, we refuse
    to send our worker JWT there.
    """
    client = _ensure_cloud_entitlements()
    if client is None or not client.has_credentials():
        return
    try:
        ents = await client.get_entitlements()
    except Exception as exc:
        LOGGER.warning("cloud_entitlements: tier lookup failed for tunnelmole: %s", exc)
        return
    if "tm" not in ents:
        return

    explicit_host = (os.environ.get("AUTOYOU_TUNNELMOLE_REMOTE_HOST") or "").strip()
    if explicit_host and not is_official_tunnelmole_remote_host(explicit_host):
        LOGGER.info(
            "cloud_entitlements: tunnelmole pinned to non-AutoYou host (%s); "
            "skipping paid provisioning to avoid leaking the worker JWT.",
            explicit_host,
        )
        return

    try:
        jwt_token = await client.get_worker_jwt()
    except Exception as exc:
        LOGGER.warning("cloud_entitlements: JWT mint failed for tunnelmole: %s", exc)
        return
    if not jwt_token:
        return
    os.environ["AUTOYOU_TUNNELMOLE_JWT"] = jwt_token
    os.environ["AUTOYOU_TUNNELMOLE_TIER"] = "paid"
    if not explicit_host:
        os.environ["AUTOYOU_TUNNELMOLE_REMOTE_HOST"] = "https://tm.autoyou.me"
    LOGGER.info("cloud_entitlements: tunnelmole using paid provisioning (tier=tm).")

def _is_authenticated_pair_session(session_id: Optional[str]) -> bool:
    """Return True if session_id was issued by /auth and still valid in cache."""
    if not session_id:
        return False
    session_key = str(session_id)
    sess = STATE.session_cache.get(session_key)
    if not isinstance(sess, dict):
        return False
    expires_at = sess.get("expires_at")
    if expires_at is None:
        created_at = sess.get("created_at")
        try:
            if created_at is not None:
                expires_at = float(created_at) + (PAIR_SESSION_EXPIRATION_MINUTES * 60)
        except Exception:
            expires_at = None
    if expires_at is not None:
        try:
            if time.time() > float(expires_at):
                STATE.session_cache.pop(session_key, None)
                return False
        except Exception:
            STATE.session_cache.pop(session_key, None)
            return False
    return bool(sess.get("authenticated"))


# Tight CORS for the public auth app. Native pairing clients (iOS / Android /
# Python GUI / messaging bridges) do not enforce CORS in the browser sense, so
# tightening these does not break them. What this *does* prevent is a browser
# page at an arbitrary origin from scripting XHR calls against /auth and
# reading the response. The opaque-POST brute-force path is closed separately
# by AUTH_RATE_LIMITER + password strength requirements.
auth_app.add_middleware(
    CORSMiddleware,
    # Browser origins that have legitimate reason to talk to /auth:
    #   - the admin / loopback UI (localhost / 127.0.0.1 on any port)
    #   - the pairing HTML served over tunnelmole (https://<slug>.tunnelmole.net)
    allow_origins=[],
    allow_origin_regex=(
        r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$"
        r"|^https://[A-Za-z0-9][A-Za-z0-9-]*\.tunnelmole\.net$"
        r"|^https://[A-Za-z0-9][A-Za-z0-9-]*\.tm\.autoyou\.me$"
    ),
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept", "Authorization", "X-Requested-With"],
)

ADMIN_SESSIONS: Dict[str, bool] = {}
ADMIN_API_TOKENS: Dict[str, float] = {}

# --- Internet search state admin endpoints ---


# --- Agent Websites global OTP security toggle ---


# --- OpenClaw Gateway status endpoint ---

# --- Hermes Agent gateway status endpoint ---

# --- AI Agent hot-reload endpoint ---

# ── Model Behavior endpoints ──────────────────────────────────────────────────

_MODEL_BEHAVIOR_MODES = {
    "none": {
        "label": "None",
        "description": "No behavior overrides. Ollama context is still resolved separately from model size and machine memory unless you set num_ctx manually.",
        "params": {},
    },
    "accurate": {
        "label": "Accurate",
        "description": "Optimised for tool-calling and factual answers. Low temperature and conservative sampling. Best for agent workflows.",
        "params": {"temperature": 0.1, "top_p": 0.9, "repeat_penalty": 1.1},
    },
    "human": {
        "label": "Human",
        "description": "Warm, conversational tone. Balanced creativity and coherence. Good for voice interactions and everyday chat.",
        "params": {"temperature": 0.7, "top_p": 0.95, "repeat_penalty": 1.05},
    },
    "creative": {
        "label": "Creative",
        "description": "High variance, brainstorming-friendly output. May hallucinate. Best for generative / open-ended tasks.",
        "params": {"temperature": 1.2, "top_p": 1.0, "repeat_penalty": 0.9},
    },
}

def _get_model_behavior_modes() -> Dict[str, Dict[str, Any]]:
    return {
        mode_name: {
            "label": mode_payload["label"],
            "description": mode_payload["description"],
            "params": dict(mode_payload["params"]),
        }
        for mode_name, mode_payload in _MODEL_BEHAVIOR_MODES.items()
    }

def _behavior_provider_name() -> str:
    try:
        ai_prov_config = (STATE.config or {}).get("ai_provider", {})
        provider = str(ai_prov_config.get("provider") or os.getenv("AI_PROVIDER", "ollama")).strip().lower()
        return provider or "ollama"
    except Exception:
        return str(os.getenv("AI_PROVIDER", "ollama") or "ollama").strip().lower() or "ollama"

def _resolve_effective_behavior_num_ctx(model_behavior_cfg: Optional[Dict[str, Any]] = None) -> tuple[Optional[int], Optional[str]]:
    if _behavior_provider_name() not in {"ollama", "ollama_gateway"}:
        return None, None
    cfg = model_behavior_cfg if isinstance(model_behavior_cfg, dict) else {}
    try:
        explicit_override = cfg.get("num_ctx")
        if explicit_override is not None:
            parsed_override = int(explicit_override)
            if parsed_override > 0:
                return parsed_override, "model_behavior.override"
    except (TypeError, ValueError):
        pass
    return recommend_ollama_num_ctx(_configured_ollama_model_for_behavior()), "machine_heuristic"


# --- Agent Builder REST endpoints ---

_AUTOYOU_AGENTS_ROOT = get_embedded_agents_root(__file__)

def _get_autoyou_browser_forward_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    """Resolve the localhost browser port used for proxied client browsing."""
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    autoyou_config = effective_cfg.get("autoyou_page", {}) if isinstance(effective_cfg, dict) else {}
    page_port = _get_autoyou_page_service_port(effective_cfg)
    try:
        forward_port = int(autoyou_config.get("custom_forward_port", page_port))
    except Exception:
        forward_port = page_port
    forward_enabled = bool(autoyou_config.get("custom_forward_enabled", False))
    return forward_port if forward_enabled else page_port

def _get_autoyou_page_service_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    override = os.getenv("AUTOYOU_PAGE_PORT", "").strip()
    if override:
        port = int(override)
        if not 1 <= port <= 65535:
            raise ValueError("AUTOYOU_PAGE_PORT must be a valid network port")
        return port
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    autoyou_config = effective_cfg.get("autoyou_page", {}) if isinstance(effective_cfg, dict) else {}
    try:
        return int(autoyou_config.get("port", 8067))
    except Exception:
        return 8067

def _get_autoyou_page_service_base_url(cfg: Optional[Dict[str, Any]] = None) -> str:
    return f"http://127.0.0.1:{_get_autoyou_page_service_port(cfg)}"

def _get_autoyou_browser_forward_port_from_state() -> int:
    return _get_autoyou_browser_forward_port(STATE.config or {})

def _get_ai_agent_api_port() -> int:
    try:
        return int(getattr(STATE, "main_server_port", None) or AI_AGENT_SERVER_PORT)
    except Exception:
        return int(AI_AGENT_SERVER_PORT)

def _is_ai_agent_rest_api_path(path: str) -> bool:
    normalized = str(path or "").split("?", 1)[0]
    return (
        normalized == "/api/chat"
        or normalized == "/api/status"
        or normalized == "/api/docs"
        or normalized.startswith("/api/sessions/")
    )

def _get_datachannel_http_request_timeout_seconds(path: str) -> float:
    if _is_ai_agent_rest_api_path(path):
        return _get_positive_float_env(
            "AUTOYOU_DATACHANNEL_AI_HTTP_TIMEOUT_SECONDS",
            default=900.0,
            minimum=30.0,
        )
    return 60.0

def _normalize_agent_directory_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]", "_", str(name or "")).lower().strip("_")
    normalized = re.sub(r"_+", "_", normalized)
    if normalized.endswith("_agent"):
        normalized = normalized[: -len("_agent")]
    if not normalized:
        raise ValueError("agent_name is required")
    return f"{normalized}_agent"

def _workspace_agents_root() -> Path:
    return get_dynamic_agents_root("AutoYou", anchor=__file__).resolve()

def _workspace_agent_runtime_block_reason(agent_name: str) -> str:
    return runtime_install_block_reason(agent_name)

def _coerce_enabled_flag(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _coerce_enabled_flag_default_true(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str) and not raw.strip():
        return True
    return _coerce_enabled_flag(raw)


def _native_mobile_ad_trigger_enabled() -> bool:
    return _coerce_enabled_flag_default_true(os.getenv("AUTOYOU_NATIVE_MOBILE_AD_TRIGGER_ENABLED"))


def _coerce_web_rewarded_ad_unit_path(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text or "\n" in text or "\r" in text:
        return ""
    if " " in text:
        return ""
    if len(text) > 768:
        return text[:768]
    return text


def _web_rewarded_ad_trigger_enabled() -> bool:
    return _coerce_enabled_flag_default_true(os.getenv("AUTOYOU_WEB_REWARDED_AD_TRIGGER_ENABLED"))


def _resolve_web_rewarded_ad_unit_path(raw_value: Any = "") -> str:
    web_rewarded_ad_unit = _coerce_web_rewarded_ad_unit_path(raw_value)
    if web_rewarded_ad_unit:
        return web_rewarded_ad_unit
    return ""


def _rewarded_ad_trigger_enabled(web_rewarded_ad_unit: Any = "") -> bool:
    if _native_mobile_ad_trigger_enabled():
        return True
    if not _web_rewarded_ad_trigger_enabled():
        return False
    return bool(_resolve_web_rewarded_ad_unit_path(web_rewarded_ad_unit))


_NATIVE_MOBILE_AD_CONTROL_STRIPPED_KEYS = (
    "ad_units",
    "ad_unit",
    "ad_unit_id",
    "rewarded_ad_unit_id",
    "admob_ad_unit_id",
    "ios_ad_unit_id",
    "android_ad_unit_id",
    "ios_rewarded_ad_unit_id",
    "android_rewarded_ad_unit_id",
    "reward_intent_endpoint",
    "watch_progress_endpoint",
    "status_endpoint",
    "web_rewarded_ad_unit",
    "custom_data",
    "intent_id",
)

_REWARDED_AD_CONTROL_LEASE_SECONDS = 180.0
_REMOTE_DESKTOP_CONTROL_LEASE_SECONDS = 10 * 60.0
_REMOTE_DESKTOP_INPUT_FAILURE_LIMIT = 3


def _lock_native_mobile_rewarded_ad_control_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    control_payload = dict(payload or {}) if isinstance(payload, dict) else {}
    control_payload.setdefault("expected_watch_seconds", 30)
    try:
        control_payload["expected_watch_seconds"] = max(
            1,
            min(3600, int(float(control_payload["expected_watch_seconds"]))),
        )
    except Exception:
        control_payload["expected_watch_seconds"] = 30

    # Native mobile AdMob unit IDs are intentionally baked into the iOS/Android
    # clients. The server only authorizes and routes the connected-session
    # control, never supplies or overrides unit IDs.
    for server_owned_key in _NATIVE_MOBILE_AD_CONTROL_STRIPPED_KEYS:
        control_payload.pop(server_owned_key, None)
    control_payload["event"] = "show_rewarded_ad"
    control_payload["platform"] = "server"
    control_payload["source"] = "ads_watching_agent"
    control_payload["native_ad_unit_source"] = "client_baked_autoyou_build"
    control_payload["desktop_web_ad_config_source"] = "signed_client_baked_release_env"
    control_payload["ad_unit_ids_in_payload"] = False

    return control_payload

def _is_loopback_browser_control_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().strip("[]")
    return normalized in {"localhost", "127.0.0.1", "::1"}

def _normalize_client_browser_control_url(raw_url: Any) -> str:
    text = str(raw_url or "").strip().strip("\"'<>").rstrip(".,;!?")
    if not text or len(text) > 2048 or any(ch.isspace() for ch in text):
        return ""
    if text.startswith("/") and not text.startswith("//"):
        return text
    if "://" not in text:
        authority = text.split("/", 1)[0]
        if ":" in authority and not authority.rsplit(":", 1)[1].isdigit():
            return ""
        host_hint = authority.rsplit(":", 1)[0].strip("[]")
        scheme = "http" if _is_loopback_browser_control_host(host_hint) else "https"
        text = f"{scheme}://{text}"
    parsed = urlsplit(text)
    scheme = str(parsed.scheme or "").lower()
    host = str(parsed.hostname or "").strip()
    if scheme not in {"http", "https"} or not host:
        return ""
    if scheme == "http" and not _is_loopback_browser_control_host(host):
        parsed = parsed._replace(scheme="https")
    return parsed.geturl()

def _lock_client_browser_control_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_payload = dict(payload or {}) if isinstance(payload, dict) else {}
    action = str(raw_payload.get("action") or "").strip().lower()
    if action not in {"open_url", "open_home", "reload", "back", "forward"}:
        action = ""
    url = _normalize_client_browser_control_url(raw_payload.get("url"))
    if action == "open_url" and not url:
        action = ""
    source = str(raw_payload.get("source") or "").strip().lower()
    if source not in {"client_browser_control_agent", "internet_agent", "browser_agent"}:
        source = "client_browser_control_agent"
    control_payload: Dict[str, Any] = {
        "event": "client_browser_control",
        "action": action,
        "platform": "server",
        "source": source,
    }
    if not action:
        control_payload["error"] = "unsupported_client_browser_control"
    if url:
        control_payload["url"] = url
    requested_text = str(raw_payload.get("requested_text") or "").strip()
    if requested_text:
        control_payload["requested_text"] = requested_text[:512]
    control_id = str(raw_payload.get("control_id") or "").strip()
    if control_id:
        control_payload["control_id"] = control_id[:128]
    return control_payload


def _lock_remote_desktop_keyboard_control_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_remote_desktop_keyboard_payload(payload)
    if normalized is None or normalized.get("action") not in {"show", "hide"}:
        return {
            "event": "remote_desktop_keyboard",
            "action": "",
            "platform": "server",
            "source": "remote_desktop_agent",
            "error": "unsupported_remote_desktop_keyboard_action",
        }
    normalized["platform"] = "server"
    normalized["source"] = "remote_desktop_agent"
    return normalized

_PARTNER_ENABLEMENT_ENV = {
    "telegram": "AUTOYOU_ENABLE_TELEGRAM_PARTNER",
    "signal": "AUTOYOU_ENABLE_SIGNAL_PARTNER",
    "whatsapp": "AUTOYOU_ENABLE_WHATSAPP_PARTNER",
    "telegram_user": "AUTOYOU_ENABLE_TELEGRAM_USER_PARTNER",
}
_PARTNER_ENABLEMENT_DEFAULTS = {
    "telegram": True,
}
_BINARY_DEFAULT_RELEASE_PROFILES = {"binary-default", "autoyou-server-windows-default", "autoyou-server-macos-default"}

def _runtime_release_profile() -> str:
    return str(os.getenv("AUTOYOU_RELEASE_PROFILE", "")).strip().lower()

def _is_binary_default_runtime() -> bool:
    return _runtime_release_profile() in _BINARY_DEFAULT_RELEASE_PROFILES

def _messaging_partner_default_enabled(partner: str) -> bool:
    normalized = str(partner or "").strip().lower()
    if normalized in _PARTNER_ENABLEMENT_DEFAULTS:
        return _PARTNER_ENABLEMENT_DEFAULTS[normalized]
    if normalized in {"signal", "whatsapp", "telegram_user"}:
        return not _is_binary_default_runtime()
    return True

def _env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

def _messaging_partner_feature_enabled(partner: str) -> bool:
    normalized = str(partner or "").strip().lower()
    env_name = _PARTNER_ENABLEMENT_ENV.get(normalized)
    if not env_name:
        return True
    return _env_enabled(env_name, default=_messaging_partner_default_enabled(normalized))

def _messaging_partner_disabled_message(partner: str) -> str:
    normalized = str(partner or "").strip().lower()
    env_name = _PARTNER_ENABLEMENT_ENV.get(normalized, "AUTOYOU_ENABLE_PARTNER")
    display = normalized.capitalize() if normalized else "Messaging"
    return (
        f"{display} partner is disabled for this runtime profile. "
        f"Set {env_name}=true only for an owner-paired, self-authorized setup."
    )

def _messaging_partner_disabled_response(partner: str) -> JSONResponse:
    normalized = str(partner or "").strip().lower()
    return JSONResponse(
        status_code=403,
        content={
            "success": False,
            "feature_enabled": False,
            "partner": normalized,
            "enable_env": _PARTNER_ENABLEMENT_ENV.get(normalized),
            "error": _messaging_partner_disabled_message(normalized),
        },
    )

def _default_agent_frontend_enabled(agent_name: str) -> bool:
    normalized = package_agent_name(agent_name)
    if not normalized:
        return True
    if normalized == "internet_agent":
        return True
    return bool(FRONTEND_DEFAULT_ENABLEMENT.get(normalized, True))

def _agent_frontend_config_entry(
    agent_name: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Any:
    normalized = package_agent_name(agent_name)
    if not normalized:
        return None
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    frontends_cfg = effective_cfg.get("agent_frontends", {}) if isinstance(effective_cfg, dict) else {}
    if not isinstance(frontends_cfg, dict):
        return None
    return frontends_cfg.get(normalized)

def _normalize_agent_website_route_mode(
    value: Any,
    *,
    default: str = AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY,
) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if token in {"path", "path_proxy", "primary", "primary_path", "page_proxy", "browser_path"}:
        return AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY
    if token in {"direct", "direct_port", "direct_forward", "same_port", "same_port_proxy", "one_to_one"}:
        return AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD
    return default if default in AGENT_WEBSITE_ROUTE_MODES else AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY

def _entry_declares_direct_forward(frontend_entry: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(frontend_entry, dict):
        return False
    try:
        if int(frontend_entry.get("direct_forward_port") or 0) > 0:
            return True
    except Exception:
        pass
    return _coerce_enabled_flag(frontend_entry.get("uses_direct_forward_port", False))

def _get_agent_frontend_route_mode(
    agent_name: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
    frontend_entry: Optional[Dict[str, Any]] = None,
) -> str:
    normalized = package_agent_name(agent_name)
    if normalized in AGENT_FRONTEND_REQUIRED_DIRECT_FORWARD:
        return AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD

    configured_entry = _agent_frontend_config_entry(normalized, cfg=cfg) if cfg is not None else None
    if isinstance(configured_entry, dict) and "route_mode" in configured_entry:
        return _normalize_agent_website_route_mode(configured_entry.get("route_mode"))

    if isinstance(frontend_entry, dict):
        route_mode = str(frontend_entry.get("route_mode") or "").strip()
        if route_mode:
            return _normalize_agent_website_route_mode(route_mode)
        if _entry_declares_direct_forward(frontend_entry):
            return AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD

    return AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY

def _set_agent_frontend_route_mode(
    agent_name: str,
    route_mode: Any,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = package_agent_name(agent_name)
    if not normalized:
        raise ValueError("agent_name is required")
    if normalized == "internet_agent":
        raise ValueError("Internet Search does not expose a browser website route mode")

    normalized_mode = _normalize_agent_website_route_mode(route_mode)
    if normalized in AGENT_FRONTEND_REQUIRED_DIRECT_FORWARD and normalized_mode != AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD:
        raise ValueError(f"{normalized} uses direct same-port routing when its website is enabled")

    target_cfg = cfg if cfg is not None else (STATE.config or _default_config())
    frontends_cfg = target_cfg.get("agent_frontends")
    if not isinstance(frontends_cfg, dict):
        frontends_cfg = {}
        target_cfg["agent_frontends"] = frontends_cfg

    existing = frontends_cfg.get(normalized)
    if isinstance(existing, dict):
        next_entry = dict(existing)
    else:
        if existing is None:
            enabled = _get_agent_frontend_enabled(normalized, cfg=target_cfg)
        else:
            enabled = bool(existing)
        next_entry = {"enabled": bool(enabled)}
    next_entry["route_mode"] = normalized_mode
    frontends_cfg[normalized] = next_entry
    return target_cfg

def _frontend_route_port_for_mode(
    frontend_entry: Optional[Dict[str, Any]],
    route_mode: str,
) -> int:
    if not isinstance(frontend_entry, dict):
        return 0
    if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD:
        for key in ("direct_forward_port", "proxy_port"):
            try:
                port = int(frontend_entry.get(key) or 0)
            except Exception:
                port = 0
            if port > 0:
                return port
        return 0
    try:
        return int(frontend_entry.get("proxy_port") or frontend_entry.get("recommended_port") or 0)
    except Exception:
        return 0

def _frontend_entry_path(frontend_entry: Dict[str, Any]) -> str:
    entry_path = str(frontend_entry.get("entry_path") or "/").strip() or "/"
    if not entry_path.startswith("/"):
        entry_path = f"/{entry_path}"
    return entry_path

def _frontend_proxy_path(frontend_entry: Dict[str, Any]) -> str:
    agent_name = str(frontend_entry.get("agent_name") or "").strip()
    entry_path = _frontend_entry_path(frontend_entry)
    proxy_path = str(
        frontend_entry.get("proxy_path")
        or frontend_entry.get("launch_path")
        or (f"/agent/{agent_name}{entry_path if entry_path != '/' else '/'}" if agent_name else entry_path)
    ).strip() or entry_path
    if not proxy_path.startswith("/"):
        proxy_path = f"/{proxy_path}"
    return proxy_path

def _apply_agent_frontend_route_policy(
    frontend_entry: Dict[str, Any],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = dict(frontend_entry)
    agent_name = str(entry.get("agent_name") or "").strip()
    route_mode = _get_agent_frontend_route_mode(agent_name, cfg=cfg, frontend_entry=entry)
    entry_path = _frontend_entry_path(entry)
    if agent_name == "admin_agent":
        # The bundled desktop chooses a free admin port at launch. The
        # manifest's 8001 is only a standalone-server default, so never
        # advertise it as the live admin website on a bundled server.
        admin_url = f"http://127.0.0.1:{ADMIN_WEB_SERVICE_PORT}{entry_path}"
        entry["direct_forward_port"] = ADMIN_WEB_SERVICE_PORT
        entry["proxy_port"] = ADMIN_WEB_SERVICE_PORT
        entry["local_url"] = admin_url
        if entry.get("launch_url"):
            entry["launch_url"] = admin_url
    proxy_path = _frontend_proxy_path(entry)
    route_port = _frontend_route_port_for_mode(entry, route_mode)
    page_service_url = _get_autoyou_page_service_base_url(cfg)
    path_proxy_url = f"{page_service_url}{proxy_path}"
    server_local_url = ""
    if route_port > 0:
        server_local_url = f"http://127.0.0.1:{route_port}{entry_path if entry_path != '/' else '/'}"

    entry["route_mode"] = route_mode
    entry["proxy_path"] = proxy_path
    entry["launch_path"] = proxy_path
    entry["page_service_url"] = page_service_url
    entry["path_proxy_url"] = path_proxy_url
    if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD and route_port > 0:
        entry["direct_forward_port"] = route_port
        entry["uses_direct_forward_port"] = True
        entry["local_url"] = str(entry.get("local_url") or server_local_url).strip() or server_local_url
        entry["open_url"] = entry["local_url"]
    else:
        entry["direct_forward_port"] = None
        entry["uses_direct_forward_port"] = False
        if server_local_url and not str(entry.get("local_url") or "").strip():
            entry["local_url"] = server_local_url
        launch_url = str(entry.get("launch_url") or "").strip()
        entry["open_url"] = proxy_path if not launch_url.startswith("http") else launch_url
        if str(entry["open_url"]).startswith("http://127.0.0.1:") or str(entry["open_url"]).startswith("http://localhost:"):
            entry["open_url"] = proxy_path
    return entry

def _get_audio_playback_config(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    audio_cfg = effective_cfg.get("audio_playback", {})
    return audio_cfg if isinstance(audio_cfg, dict) else {}

def _get_audio_playback_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_audio_playback_config(cfg=cfg).get("enabled", True))

def _resolve_audio_playback_music_library_dirs(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[str]:
    configured_dirs = _get_audio_playback_config(cfg=cfg).get("music_library_dirs")
    return ensure_music_library_dirs(__file__, configured_dirs=configured_dirs)

def _set_audio_playback_enabled(
    enabled: bool,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    target_cfg = cfg if cfg is not None else (STATE.config or _default_config())
    audio_cfg = target_cfg.get("audio_playback")
    if not isinstance(audio_cfg, dict):
        audio_cfg = {}
        target_cfg["audio_playback"] = audio_cfg
    audio_cfg["enabled"] = bool(enabled)
    return target_cfg

def _audio_playback_disabled_status() -> Dict[str, Any]:
    return {
        "event": "playback",
        "state": "disabled",
        "detail": "Audio playback is disabled in server settings.",
    }

def _get_video_call_config(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    holder = {"video_call": copy.deepcopy(effective_cfg.get("video_call", {}))}
    _apply_default_video_call_config(holder)
    return holder["video_call"]

def _get_video_call_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("enabled", True))

def _get_video_call_audio_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("audio_enabled", True))

def _get_video_call_agents_disabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("disable_autoyou_agents", False))

def _get_video_call_agent_processing_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_video_call_audio_enabled(cfg=cfg)
        and not _get_video_call_agents_disabled(cfg=cfg)
    )

def _get_video_record_my_video_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("record_my_video", False))

def _resolve_video_recording_dir(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    configured_dir = str(_get_video_call_config(cfg=cfg).get("recording_dir") or "").strip()
    if configured_dir:
        return configured_dir
    return str(_default_video_recording_dir())

def _default_video_recording_dir() -> Path:
    return get_mutable_data_dir("AutoYou", anchor=__file__) / "output" / "video-recordings"

def _get_background_mode_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_video_call_audio_enabled(cfg=cfg)
        and _get_video_call_config(cfg=cfg).get("background_mode_enabled", False)
    )

def _get_silent_recording_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_video_call_audio_enabled(cfg=cfg)
        and _get_video_call_config(cfg=cfg).get("silent_recording_enabled", False)
    )

def _get_location_recording_enabled(*, cfg: Optional[Dict[str, Any]] = None) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("location_recording_enabled", False))

def _location_recording_available(*, cfg: Optional[Dict[str, Any]] = None) -> bool:
    if not _get_location_recording_enabled(cfg=cfg):
        return False
    try:
        return "location_agent" in set(
            load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT).get("installed_agents", [])
        )
    except Exception as exc:
        LOGGER.warning("Location recording availability check failed: %s", type(exc).__name__)
        return False

def _record_location_ping_sample(sample: Any) -> bool:
    if not _location_recording_available() or not isinstance(sample, dict):
        return False
    try:
        from autoyou_agents.location_agent.store import LocationStore
        LocationStore().record_many([sample])
        return True
    except Exception as exc:
        LOGGER.warning("Location ping sample was not stored: %s", type(exc).__name__)
        return False

def _record_location_sharing_status(session_id: str, enabled: bool) -> None:
    try:
        from autoyou_agents.location_agent.store import LocationStore
        LocationStore().record_sharing_status(session_id, enabled and _location_recording_available())
    except Exception as exc:
        LOGGER.warning("Location sharing status was not stored: %s", type(exc).__name__)

def _get_wuift_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_video_call_audio_enabled(cfg=cfg)
        and _get_video_call_config(cfg=cfg).get("wuift_enabled", True)
    )

def _resolve_silent_recording_dir(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    configured_dir = str(_get_video_call_config(cfg=cfg).get("silent_recording_dir") or "").strip()
    if configured_dir:
        return configured_dir
    return str(_default_silent_recording_dir())

def _default_silent_recording_dir() -> Path:
    return get_mutable_data_dir("AutoYou", anchor=__file__) / "output" / "safety-recordings"

def _voice_training_recording_paths_payload() -> Dict[str, Any]:
    try:
        from shared.voice_training_storage import default_voice_training_dir, get_voice_training_dir

        active_dir = get_voice_training_dir()
        default_dir = default_voice_training_dir()
        return {
            "active_dir": str(active_dir),
            "default_dir": str(default_dir),
            "using_custom_dir": active_dir.resolve() != default_dir.resolve(),
        }
    except Exception as exc:  # noqa: BLE001 - admin bootstrap must remain renderable.
        return {"active_dir": "", "default_dir": "", "using_custom_dir": False, "error": str(exc)}

def _build_recording_paths_payload(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    video_cfg = _get_video_call_config(cfg=cfg)
    video_configured = str(video_cfg.get("recording_dir") or "").strip()
    safety_configured = str(video_cfg.get("silent_recording_dir") or "").strip()
    return {
        "video_recording": {
            "configured_dir": video_configured,
            "default_dir": str(_default_video_recording_dir()),
            "resolved_dir": _resolve_video_recording_dir(cfg=cfg),
            "using_custom_dir": bool(video_configured),
        },
        "safety_recording": {
            "configured_dir": safety_configured,
            "default_dir": str(_default_silent_recording_dir()),
            "resolved_dir": _resolve_silent_recording_dir(cfg=cfg),
            "using_custom_dir": bool(safety_configured),
        },
        "voice_training": _voice_training_recording_paths_payload(),
    }

def _get_silent_recording_batch_seconds(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> int:
    return normalize_audio_recording_batch_seconds(
        _get_video_call_config(cfg=cfg).get("silent_recording_batch_seconds"),
        DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
    )

def _get_video_recording_mode(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return normalize_inbound_video_recording_mode(_get_video_call_config(cfg=cfg).get("recording_mode"), "video")

def _get_video_image_interval_seconds(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> float:
    return normalize_inbound_video_image_interval_seconds(
        _get_video_call_config(cfg=cfg).get("image_interval_seconds"),
        DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
    )

def _get_video_outbound_source(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return _normalize_video_outbound_source(_get_video_call_config(cfg=cfg).get("outbound_source"), "remote_desktop")

def _get_video_outbound_sources(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[str]:
    video_cfg = _get_video_call_config(cfg=cfg)
    legacy_source = _normalize_video_outbound_source(video_cfg.get("outbound_source"), "remote_desktop")
    selected = _normalize_video_outbound_sources(video_cfg.get("outbound_sources"), fallback_source=legacy_source)
    if selected == ["remote_desktop"] and legacy_source != "remote_desktop":
        return [legacy_source]
    return selected

def _get_video_api_source_id(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return str(_get_video_call_config(cfg=cfg).get("api_video_source_id") or "default").strip() or "default"

def _get_video_file_config(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    file_cfg = _get_video_call_config(cfg=cfg).get("video_file", {})
    return file_cfg if isinstance(file_cfg, dict) else {}

def _get_video_file_path(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return str(_get_video_file_config(cfg=cfg).get("path") or "").strip()

def _get_video_file_loop_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_file_config(cfg=cfg).get("loop", False))

def _get_video_file_source_id(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return "default"

def _get_video_capture_audio_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_audio_sources(cfg=cfg))

def _get_video_audio_sources(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[str]:
    video_cfg = _get_video_call_config(cfg=cfg)
    selected = _normalize_video_audio_sources(
        video_cfg.get("audio_sources"),
        capture_audio=bool(video_cfg.get("capture_audio", False)),
        input_audio_source=str(video_cfg.get("input_audio_source") or "default").strip() or "default",
        outbound_source=_normalize_video_outbound_source(video_cfg.get("outbound_source"), "remote_desktop"),
    )
    if not selected and bool(video_cfg.get("capture_audio", False)):
        selected = _normalize_video_audio_sources(
            None,
            capture_audio=True,
            input_audio_source=str(video_cfg.get("input_audio_source") or "default").strip() or "default",
            outbound_source=_normalize_video_outbound_source(video_cfg.get("outbound_source"), "remote_desktop"),
        )
    return selected

def _get_ai_audio_replies_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_call_config(cfg=cfg).get("ai_audio_replies_enabled", True))

def _get_video_input_audio_source(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    return str(_get_video_call_config(cfg=cfg).get("input_audio_source") or "default").strip() or "default"

def _resolve_video_file_upload_dir() -> Path:
    test_root = str(os.getenv("AUTOYOU_TEST_ROOT") or "").strip()
    base_dir = Path(test_root) if test_root else Path(__file__).resolve().parent
    upload_dir = base_dir / "output" / "video-files"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir

def _get_video_remote_desktop_config(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    remote_cfg = _get_video_call_config(cfg=cfg).get("remote_desktop", {})
    return remote_cfg if isinstance(remote_cfg, dict) else {}

def _get_remote_desktop_setting_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_remote_desktop_config(cfg=cfg).get("enabled", True))

def _get_remote_desktop_send_screen_enabled(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(_get_video_remote_desktop_config(cfg=cfg).get("send_screen", True))

def _get_remote_desktop_monitor_id(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> int:
    return _normalize_remote_desktop_monitor_id(_get_video_remote_desktop_config(cfg=cfg).get("monitor_id", 0))

def _get_remote_desktop_capture_profile(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    remote_cfg = _get_video_remote_desktop_config(cfg=cfg)
    quality = normalize_remote_desktop_quality(remote_cfg.get("quality"))
    profile: Dict[str, Any] = remote_desktop_quality_settings(quality)
    profile.update({
        "quality": quality,
        "monitor_id": _normalize_remote_desktop_monitor_id(remote_cfg.get("monitor_id", 0)),
        "bitrate_kbps": normalize_remote_desktop_bitrate_kbps(remote_cfg.get("bitrate_kbps", 1500)),
    })
    return profile

def _get_remote_desktop_monitor_payload(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = _get_remote_desktop_capture_profile(cfg=cfg)
    payload = _enumerate_webrtc_monitors_payload(cfg=cfg)
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if isinstance(devices, list):
        for device in devices:
            if isinstance(device, dict) and int(device.get("id") or 0) == profile["monitor_id"]:
                return dict(device)
    return {
        "id": profile["monitor_id"],
        "width": profile["max_width"],
        "height": max(1, int(round(profile["max_width"] * 9 / 16))),
        "available": False,
    }

def _remote_desktop_input_backend_ready() -> bool:
    probed = remote_desktop_input_backend_probed()
    return True if probed is None else bool(probed)

def _get_remote_desktop_control_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_remote_desktop_video_available(cfg=cfg)
        and "remote_desktop" in _get_video_outbound_sources(cfg=cfg)
        and _get_video_remote_desktop_config(cfg=cfg).get("control_enabled", False)
        and _remote_desktop_input_backend_ready()
    )

def _get_remote_desktop_video_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return (
        _get_video_call_enabled(cfg=cfg)
        and _get_remote_desktop_setting_enabled(cfg=cfg)
        and _get_remote_desktop_send_screen_enabled(cfg=cfg)
        and RemoteDesktopVideoStreamTrack is not None
    )

def _get_api_realtime_video_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    return bool(
        _get_video_call_enabled(cfg=cfg)
        and RealtimeVideoInputStreamTrack is not None
        and create_outbound_video_track is not None
    )

def _get_video_file_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    file_path = _get_video_file_path(cfg=cfg)
    return bool(
        _get_video_call_enabled(cfg=cfg)
        and VideoFileStreamTrack is not None
        and create_outbound_video_track is not None
        and file_path
        and Path(file_path).expanduser().is_file()
    )

def _get_camera_video_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    if not (_get_video_call_enabled(cfg=cfg) and create_outbound_video_track is not None):
        return False
    configured_id = _configured_webrtc_camera_device_id(cfg=cfg)
    probed = _get_cached_webrtc_device_payload(f"camera:{configured_id}:True")
    if isinstance(probed, dict):
        for device in probed.get("devices", []):
            if not isinstance(device, dict) or int(device.get("id", -1)) != configured_id:
                continue
            if device.get("available") is False:
                return False
            break
    try:
        importlib.import_module("cv2")
    except Exception:
        return False
    return True

def _configure_video_file_playback_from_config(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    restart: bool = False,
) -> Dict[str, Any]:
    source_id = _get_video_file_source_id(cfg=cfg)
    if configure_video_file_playback is None:
        return {"source_id": source_id, "configured": False, "error": "Video file playback unavailable"}
    return configure_video_file_playback(
        source_id=source_id,
        file_path=_get_video_file_path(cfg=cfg),
        loop=_get_video_file_loop_enabled(cfg=cfg),
        restart=restart,
    )

def _get_outbound_video_source_availability(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    return {
        "api": _get_api_realtime_video_available(cfg=cfg),
        "remote_desktop": _get_remote_desktop_video_available(cfg=cfg),
        "video_file": _get_video_file_available(cfg=cfg),
        "camera": _get_camera_video_available(cfg=cfg),
    }


def _get_available_video_outbound_sources(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> List[str]:
    checks = {
        "api": _get_api_realtime_video_available,
        "remote_desktop": _get_remote_desktop_video_available,
        "video_file": _get_video_file_available,
        "camera": _get_camera_video_available,
    }
    return [
        source
        for source in _get_video_outbound_sources(cfg=cfg)
        if (check := checks.get(source)) is not None and check(cfg=cfg)
    ]


def _get_outbound_video_available(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    # A missing optional camera must not take down an otherwise healthy screen
    # share. Composite tracks use every available configured source and report
    # the unavailable ones through capabilities.
    return bool(_get_available_video_outbound_sources(cfg=cfg))

def _create_configured_outbound_video_track(
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Any:
    if create_outbound_video_track is None:
        raise RuntimeError("Outbound video track factory is unavailable")
    selected_sources = _get_available_video_outbound_sources(cfg=cfg)
    if not selected_sources:
        raise RuntimeError("No configured outbound video source is available")
    desktop_profile = _get_remote_desktop_capture_profile(cfg=cfg)

    def _one_track(source: str) -> Any:
        kwargs: Dict[str, Any] = {}
        if source == "remote_desktop":
            if RemoteDesktopVideoStreamTrack is None:
                raise RuntimeError("Remote Desktop video track is unavailable")
            return RemoteDesktopVideoStreamTrack(
                monitor_id=desktop_profile["monitor_id"],
                fps=desktop_profile["fps"],
                max_width=desktop_profile["max_width"],
            )
        if source == "api":
            kwargs["source_id"] = _get_video_api_source_id(cfg=cfg)
        if source == "video_file":
            _configure_video_file_playback_from_config(cfg=cfg, restart=True)
            kwargs["source_id"] = _get_video_file_source_id(cfg=cfg)
        if source == "camera":
            try:
                device_index = int(_get_video_call_config(cfg=cfg).get("camera_device_id", 0))
            except Exception:
                device_index = 0
            kwargs["device_index"] = device_index
        return create_outbound_video_track(source, **kwargs)

    if len(selected_sources) == 1:
        return _one_track(selected_sources[0])
    return create_outbound_video_track(
        "composite",
        sources=[(source, _one_track(source)) for source in selected_sources],
        fps=desktop_profile["fps"],
        max_width=desktop_profile["max_width"],
    )

def _ensure_audio_manager_outbound_tracks(
    *,
    audio_manager: Any,
    cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Any]:
    """Create or reuse the independently controlled TTS and media tracks."""
    if TTSAudioStreamTrack is None or audio_manager is None:
        return None, None

    def ensure_track(attribute: str, setter_name: str) -> Any:
        existing = getattr(audio_manager, attribute, None)
        if existing is not None and getattr(existing, "readyState", "live") == "live":
            return existing
        setter = getattr(audio_manager, setter_name, None)
        if not callable(setter):
            return None
        track = TTSAudioStreamTrack()
        setter(track)
        return track

    tts_track = (
        ensure_track("tts_track", "set_tts_track")
        if _get_ai_audio_replies_enabled(cfg=cfg)
        else None
    )
    playback_track = (
        ensure_track("playback_track", "set_playback_track")
        if _get_audio_playback_enabled(cfg=cfg)
        else None
    )
    return tts_track, playback_track


def _create_configured_outbound_audio_track(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    session_id: str,
    tts_track: Any = None,
    playback_track: Any = None,
) -> Any:
    tracks: List[Tuple[str, Any]] = []
    if tts_track is not None and _get_ai_audio_replies_enabled(cfg=cfg):
        tracks.append(("ai_replies", tts_track))

    if playback_track is not None and _get_audio_playback_enabled(cfg=cfg):
        if "video_file" in _get_video_outbound_sources(cfg=cfg):
            video_file_path = _get_video_file_path(cfg=cfg)
            loop = _get_video_file_loop_enabled(cfg=cfg)
            if video_file_path and Path(video_file_path).expanduser().is_file():
                LOGGER.info("Starting audio playback from video file: %s (loop=%s)", video_file_path, loop)
                try:
                    playback_track.play_audio_file(video_file_path, source="video_file", loop=loop)
                except Exception as play_err:
                    LOGGER.warning("Failed to play video file audio: %s", play_err)
        if all(track is not playback_track for _name, track in tracks):
            tracks.append(("playback", playback_track))

    audio_sources = _get_video_audio_sources(cfg=cfg)
    capture_tracks: List[Any] = []
    if audio_sources:
        try:
            from shared.video_call_manager import LocalAudioInputTrack
        except ImportError:
            LocalAudioInputTrack = None

        if LocalAudioInputTrack is not None:
            for source in audio_sources:
                if source == "microphone":
                    device = _get_video_input_audio_source(cfg=cfg)
                    label = f"microphone:{device}"
                    capture_loopback = False
                elif source == "speaker_loopback":
                    device = "desktop_loopback"
                    label = "speaker_loopback"
                    capture_loopback = True
                else:
                    continue
                LOGGER.info("Creating LocalAudioInputTrack with source=%s, loopback=%s", device, capture_loopback)
                track = LocalAudioInputTrack(device_index_or_name=device, capture_loopback=capture_loopback)
                track.enable()
                capture_tracks.append(track)
                tracks.append((label, track))

    if capture_tracks:
        if not hasattr(STATE, "local_audio_tracks"):
            STATE.local_audio_tracks = {}
        STATE.local_audio_tracks[session_id] = capture_tracks

    if not tracks:
        return None
    if len(tracks) == 1:
        return tracks[0][1]
    if MixedAudioStreamTrack is None:
        LOGGER.warning("Audio mixer unavailable; using first outbound audio source for %s", session_id)
        return tracks[0][1]
    mixed_track = MixedAudioStreamTrack(tracks)
    LOGGER.info(
        "Created mixed outbound audio track for %s with sources=%s",
        session_id,
        mixed_track.source_names(),
    )
    return mixed_track

def _build_webrtc_capabilities(
    *,
    cfg: Optional[Dict[str, Any]] = None,
    include_admin: bool = False,
) -> Dict[str, Any]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    video_enabled = _get_video_call_enabled(cfg=effective_cfg)
    audio_enabled = _get_video_call_audio_enabled(cfg=effective_cfg)
    agents_disabled = _get_video_call_agents_disabled(cfg=effective_cfg)
    agent_processing_enabled = _get_video_call_agent_processing_enabled(cfg=effective_cfg)
    background_mode_enabled = _get_background_mode_enabled(cfg=effective_cfg)
    silent_recording_enabled = _get_silent_recording_enabled(cfg=effective_cfg)
    wuift_enabled = _get_wuift_enabled(cfg=effective_cfg)
    silent_recording_batch_seconds = _get_silent_recording_batch_seconds(cfg=effective_cfg)
    recording_enabled = _get_video_record_my_video_enabled(cfg=effective_cfg)
    recording_mode = _get_video_recording_mode(cfg=effective_cfg)
    image_interval_seconds = _get_video_image_interval_seconds(cfg=effective_cfg)
    remote_agent_installed = _is_remote_desktop_agent_installed()
    remote_agent_enabled = _get_agent_frontend_enabled("remote_desktop_agent", cfg=effective_cfg)
    remote_desktop_enabled = _get_remote_desktop_video_available(cfg=effective_cfg)
    remote_desktop_profile = _get_remote_desktop_capture_profile(cfg=effective_cfg)
    remote_desktop_monitor = _get_remote_desktop_monitor_payload(cfg=effective_cfg)
    remote_desktop_control_enabled = _get_remote_desktop_control_available(cfg=effective_cfg)
    remote_desktop_control_configured = bool(
        _get_video_remote_desktop_config(cfg=effective_cfg).get("control_enabled", False)
    )
    outbound_source = _get_video_outbound_source(cfg=effective_cfg)
    outbound_sources = _get_video_outbound_sources(cfg=effective_cfg)
    active_outbound_sources = _get_available_video_outbound_sources(cfg=effective_cfg)
    unavailable_outbound_sources = [
        source for source in outbound_sources if source not in active_outbound_sources
    ]
    monitor_width = max(1, int(remote_desktop_monitor.get("width") or remote_desktop_profile["max_width"]))
    monitor_height = max(1, int(remote_desktop_monitor.get("height") or round(monitor_width * 9 / 16)))
    remote_frame_aspect_ratio = (
        16.0 / 9.0
        if len(active_outbound_sources) > 1
        else monitor_width / float(monitor_height)
    )
    outbound_video_source = (
        "none"
        if not active_outbound_sources
        else active_outbound_sources[0]
        if len(active_outbound_sources) == 1
        else "stitched"
    )
    configured_outbound_video_source = (
        "none"
        if not outbound_sources
        else outbound_sources[0]
        if len(outbound_sources) == 1
        else "stitched"
    )
    api_source_id = _get_video_api_source_id(cfg=effective_cfg)
    api_video_available = _get_api_realtime_video_available(cfg=effective_cfg)
    video_file_source_id = _get_video_file_source_id(cfg=effective_cfg)
    video_file_path = _get_video_file_path(cfg=effective_cfg)
    video_file_loop = _get_video_file_loop_enabled(cfg=effective_cfg)
    video_file_status = (
        video_file_playback_status(source_id=video_file_source_id)
        if video_file_playback_status is not None
        else {}
    )
    telemetry_status = outbound_video_telemetry_status() if callable(outbound_video_telemetry_status) else {}
    if not isinstance(telemetry_status, dict):
        telemetry_status = {}
    outbound_tracks = getattr(globals().get("WEBRTC"), "desktop_video_tracks", {})
    unique_outbound_tracks: List[Any] = []
    if isinstance(outbound_tracks, dict):
        seen_track_ids: Set[int] = set()
        for candidate_track in outbound_tracks.values():
            if candidate_track is None or id(candidate_track) in seen_track_ids:
                continue
            seen_track_ids.add(id(candidate_track))
            unique_outbound_tracks.append(candidate_track)
    outbound_stream_active = any(bool(getattr(track, "is_enabled", False)) for track in unique_outbound_tracks)
    latest_outbound_frame = telemetry_status.get("latest_frame")
    if not isinstance(latest_outbound_frame, dict):
        latest_outbound_frame = {}
    latest_outbound_timestamp_ms = int(latest_outbound_frame.get("timestamp_ms") or 0)
    latest_outbound_frame_age_ms = (
        max(0, int(time.time() * 1000) - latest_outbound_timestamp_ms)
        if latest_outbound_timestamp_ms
        else None
    )
    outbound_recent_frame = bool(
        outbound_stream_active
        and latest_outbound_frame_age_ms is not None
        and latest_outbound_frame_age_ms <= 2500
    )
    outbound_runtime_status = (
        "streaming"
        if outbound_recent_frame
        else "starting"
        if outbound_stream_active
        else "ready"
        if active_outbound_sources
        else "unavailable"
        if outbound_sources
        else "off"
    )
    if not include_admin:
        telemetry_status = {
            "frame_count": int(telemetry_status.get("frame_count") or 0),
            "error_count": int(telemetry_status.get("error_count") or 0),
            "latest_error": telemetry_status.get("latest_error") if telemetry_status.get("latest_error") else None,
        }
    if not include_admin and isinstance(video_file_status, dict):
        video_file_status = dict(video_file_status)
        status_path = str(video_file_status.get("file_path") or "")
        video_file_status["file_path"] = Path(status_path).name if status_path else ""

    capture_audio_enabled = _get_video_capture_audio_enabled(cfg=effective_cfg)
    audio_sources = _get_video_audio_sources(cfg=effective_cfg)
    input_audio_source = _get_video_input_audio_source(cfg=effective_cfg)
    ai_audio_replies_enabled = _get_ai_audio_replies_enabled(cfg=effective_cfg)
    ai_audio_active = bool(TTSAudioStreamTrack is not None and agent_processing_enabled and ai_audio_replies_enabled)
    loopback_available: Optional[bool] = None
    loopback_reason = ""
    if "speaker_loopback" in audio_sources:
        try:
            audio_devices = _enumerate_webrtc_audio_devices_payload()
            if isinstance(audio_devices, dict) and "loopback_available" in audio_devices:
                loopback_available = bool(audio_devices.get("loopback_available"))
                loopback_reason = str(audio_devices.get("loopback_reason") or "").strip()
        except Exception as exc:
            LOGGER.debug("Could not determine computer-sound loopback capability: %s", exc)
    capture_statuses: Dict[str, List[Dict[str, Any]]] = {"microphone": [], "speaker_loopback": []}
    seen_capture_track_ids: Set[int] = set()
    local_audio_tracks = getattr(STATE, "local_audio_tracks", {})
    if isinstance(local_audio_tracks, dict):
        for track_group in local_audio_tracks.values():
            candidates = track_group if isinstance(track_group, (list, tuple, set)) else [track_group]
            for candidate_track in candidates:
                if candidate_track is None or id(candidate_track) in seen_capture_track_ids:
                    continue
                seen_capture_track_ids.add(id(candidate_track))
                status_reader = getattr(candidate_track, "capture_status", None)
                if not callable(status_reader):
                    continue
                try:
                    capture_status = status_reader()
                except Exception:
                    continue
                if not isinstance(capture_status, dict):
                    continue
                capture_kind = "speaker_loopback" if capture_status.get("loopback") else "microphone"
                capture_statuses[capture_kind].append(capture_status)

    def _capture_source_runtime(source: str) -> Tuple[str, bool]:
        statuses = capture_statuses.get(source, [])
        if any(bool(status.get("device_open")) for status in statuses):
            return "active", True
        if source == "speaker_loopback" and any(
            any(
                marker in str(status.get("last_error") or "").lower()
                for marker in ("no usable", "no speaker-loopback", "no compatible")
            )
            for status in statuses
        ):
            return "unavailable", False
        if any(bool(status.get("running")) for status in statuses):
            return "retrying", False
        if statuses:
            return "unavailable", False
        if source == "speaker_loopback" and loopback_available is False:
            return "unavailable", False
        return "ready", False

    mixer_sources: List[Dict[str, Any]] = []
    if ai_audio_replies_enabled:
        mixer_sources.append(
            {
                "id": "ai_replies",
                "kind": "assistant",
                "label": "Spoken AI replies",
                "enabled": ai_audio_active,
                "active": False,
                "state": "ready" if ai_audio_active else "unavailable",
            }
        )
    for source in audio_sources:
        if source == "microphone":
            source_state, source_active = _capture_source_runtime(source)
            source_payload = {
                "id": "microphone",
                "kind": "capture",
                "label": "Computer microphone",
                "enabled": True,
                "active": source_active,
                "state": source_state,
                "device": input_audio_source,
            }
            if include_admin and capture_statuses[source]:
                source_payload["capture"] = capture_statuses[source][0]
            mixer_sources.append(source_payload)
        elif source == "speaker_loopback":
            source_state, source_active = _capture_source_runtime(source)
            source_payload = {
                "id": "speaker_loopback",
                "kind": "capture",
                "label": "Computer sound unavailable." if source_state == "unavailable" else "Computer sound",
                "enabled": True,
                "active": source_active,
                "state": source_state,
            }
            source_reason = loopback_reason
            if not source_reason and capture_statuses[source]:
                source_reason = str(capture_statuses[source][0].get("last_error") or "").strip()
            if source_state == "unavailable" and source_reason:
                source_payload["reason"] = source_reason
            if include_admin and capture_statuses[source]:
                source_payload["capture"] = capture_statuses[source][0]
            mixer_sources.append(source_payload)
    output_source_count = len(audio_sources) + (1 if ai_audio_active else 0)
    output_mode = (
        "none"
        if output_source_count <= 0
        else "mixed"
        if output_source_count > 1
        else "ai_replies"
        if ai_audio_active
        else audio_sources[0]
    )

    video_payload: Dict[str, Any] = {
        "enabled": video_enabled,
        "receive_enabled": bool(video_enabled and IncomingVideoTrackSink is not None),
        "record_my_video": bool(video_enabled and recording_enabled),
        "recording_mode": recording_mode,
        "recording_format": inbound_video_recording_format_for_mode(recording_mode),
        "image_interval_seconds": image_interval_seconds,
        "disable_autoyou_agents": agents_disabled,
    }
    if include_admin:
        video_payload["recording_dir"] = _resolve_video_recording_dir(cfg=effective_cfg)

    audio_payload: Dict[str, Any] = {
        "enabled": audio_enabled,
        "agent_processing_enabled": agent_processing_enabled,
        "agents_disabled": agents_disabled,
        "voice_pipeline_available": bool(AudioManager is not None and agent_processing_enabled),
        "tts_available": bool(TTSAudioStreamTrack is not None and agent_processing_enabled),
        "ai_audio_replies_enabled": ai_audio_replies_enabled,
        "background_mode": {
            "enabled": background_mode_enabled,
            "available": bool(audio_enabled and AudioTrackSink is not None),
            "client_audio_direction": "inactive",
            "ios_client_audio_direction": "recvonly",
            "android_client_audio_direction": "inactive",
            "server_audio_direction": "inactive",
            "ios_server_audio_direction": "sendonly",
            "android_server_audio_direction": "inactive",
            "desktop_server_audio_direction": "inactive",
            "server_audio_output": "none",
            "ios_server_audio_output": "background_audio_heartbeat",
            "android_server_audio_output": "none",
            "desktop_server_audio_output": "none",
        },
        "silent_recording": {
            "enabled": silent_recording_enabled,
            "available": bool(audio_enabled and AudioTrackSink is not None and StreamingWavBatchRecorder is not None),
            "batch_seconds": silent_recording_batch_seconds,
            "client_audio_direction": "sendonly",
            "server_audio_direction": "recvonly",
            "server_audio_output": "record_only",
        },
        "wuift": {
            "enabled": wuift_enabled,
            "available": bool(audio_enabled and AudioManager is not None and agent_processing_enabled),
        },
        "capture_audio": capture_audio_enabled,
        "audio_sources": audio_sources,
        "input_audio_source": input_audio_source,
        "loopback_available": loopback_available,
        "loopback_reason": loopback_reason,
        "output_mode": output_mode,
        "mixer": {
            "available": bool(MixedAudioStreamTrack is not None),
            "enabled": bool(audio_enabled and output_source_count > 1 and MixedAudioStreamTrack is not None),
            "sources": mixer_sources,
        },
    }
    if include_admin:
        cast(Dict[str, Any], audio_payload["background_mode"])["configured"] = background_mode_enabled
        silent_recording_payload = cast(Dict[str, Any], audio_payload["silent_recording"])
        silent_recording_payload["configured"] = silent_recording_enabled
        silent_recording_payload["recording_dir"] = _resolve_silent_recording_dir(cfg=effective_cfg)

    return {
        "host_platform": get_platform(),
        # Clients may name their conversations on this server (one way; the
        # server never sends names back). Older servers omit this key, so a
        # client must not send the rename control without it.
        "conversation": {"rename": True},
        "audio": audio_payload,
        "video": video_payload,
        "location": {
            "recording_enabled": _location_recording_available(cfg=effective_cfg),
        },
        "remote_desktop": {
            "enabled": remote_desktop_enabled,
            "configured_enabled": _get_remote_desktop_setting_enabled(cfg=effective_cfg),
            "send_screen": _get_remote_desktop_send_screen_enabled(cfg=effective_cfg),
            "monitor_id": remote_desktop_profile["monitor_id"],
            "monitor": remote_desktop_monitor,
            "quality": remote_desktop_profile["quality"],
            "max_width": remote_desktop_profile["max_width"],
            "fps": remote_desktop_profile["fps"],
            "bitrate_kbps": remote_desktop_profile["bitrate_kbps"],
            "frame_aspect_ratio": remote_frame_aspect_ratio,
            "control_enabled": remote_desktop_control_enabled,
            "control_configured": remote_desktop_control_configured,
            "control_available": _remote_desktop_input_backend_ready(),
            "control_protocol": "autoyou_remote_desktop_v1",
            "touch_modes": ["direct", "relative"],
            "agent_installed": remote_agent_installed,
            "agent_enabled": remote_agent_enabled,
            "agent_required": False,
            "track_available": bool(RemoteDesktopVideoStreamTrack is not None),
        },
        "outbound_video": {
            "enabled": bool(active_outbound_sources),
            "ready": bool(active_outbound_sources),
            "stream_active": outbound_stream_active,
            "recent_frame": outbound_recent_frame,
            "runtime_status": outbound_runtime_status,
            "latest_frame_age_ms": latest_outbound_frame_age_ms,
            "source": outbound_video_source,
            "configured_source": configured_outbound_video_source,
            "legacy_source": outbound_source,
            "sources": outbound_sources,
            "active_sources": active_outbound_sources,
            "unavailable_sources": unavailable_outbound_sources,
            "degraded": bool(active_outbound_sources and unavailable_outbound_sources),
            "api_source_id": api_source_id,
            "telemetry": telemetry_status,
            "available_sources": {
                "api": {
                    "enabled": api_video_available,
                    "source_id": api_source_id,
                    "track_available": bool(RealtimeVideoInputStreamTrack is not None),
                    "status": REALTIME_VIDEO_INPUTS.status(api_source_id) if REALTIME_VIDEO_INPUTS is not None else {},
                },
                "remote_desktop": {
                    "enabled": remote_desktop_enabled,
                    "configured_enabled": _get_remote_desktop_setting_enabled(cfg=effective_cfg),
                    "send_screen": _get_remote_desktop_send_screen_enabled(cfg=effective_cfg),
                    "monitor_id": remote_desktop_profile["monitor_id"],
                    "monitor": remote_desktop_monitor,
                    "quality": remote_desktop_profile["quality"],
                    "max_width": remote_desktop_profile["max_width"],
                    "fps": remote_desktop_profile["fps"],
                    "bitrate_kbps": remote_desktop_profile["bitrate_kbps"],
                    "frame_aspect_ratio": remote_frame_aspect_ratio,
                    "control_enabled": remote_desktop_control_enabled,
                    "control_configured": remote_desktop_control_configured,
                    "control_available": _remote_desktop_input_backend_ready(),
                    "control_protocol": "autoyou_remote_desktop_v1",
                    "agent_installed": remote_agent_installed,
                    "agent_enabled": remote_agent_enabled,
                    "agent_required": False,
                    "track_available": bool(RemoteDesktopVideoStreamTrack is not None),
                },
                "video_file": {
                    "enabled": _get_video_file_available(cfg=effective_cfg),
                    "source_id": video_file_source_id,
                    "file_path": video_file_path if include_admin else Path(video_file_path).name if video_file_path else "",
                    "file_exists": bool(video_file_path and Path(video_file_path).expanduser().is_file()),
                    "loop": video_file_loop,
                    "track_available": bool(VideoFileStreamTrack is not None),
                    "status": video_file_status,
                },
                "camera": {
                    "enabled": bool("camera" in outbound_sources and _get_camera_video_available(cfg=effective_cfg)),
                    "track_available": bool(create_outbound_video_track is not None),
                },
            },
        },
    }

def _get_agent_frontend_enabled(
    agent_name: str,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    normalized = package_agent_name(agent_name)
    if not normalized:
        return False

    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    if normalized == "internet_agent":
        return bool(effective_cfg.get("ai_agent", {}).get("internet_search_enabled", True))

    frontends_cfg = effective_cfg.get("agent_frontends", {})
    if not isinstance(frontends_cfg, dict):
        frontends_cfg = {}

    if normalized == "admin_agent":
        admin_cfg = effective_cfg.get("admin", {})
        if isinstance(admin_cfg, dict) and "frontend_proxy_enabled" in admin_cfg:
            return bool(admin_cfg.get("frontend_proxy_enabled"))

    if normalized in frontends_cfg:
        configured = frontends_cfg.get(normalized)
        if isinstance(configured, dict):
            if "enabled" in configured:
                return _coerce_enabled_flag(configured.get("enabled"))
            return _default_agent_frontend_enabled(normalized)
        return bool(configured)

    return _default_agent_frontend_enabled(normalized)

def _set_agent_frontend_enabled(
    agent_name: str,
    enabled: bool,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = package_agent_name(agent_name)
    if not normalized:
        raise ValueError("agent_name is required")

    target_cfg = cfg if cfg is not None else (STATE.config or _default_config())
    if normalized == "internet_agent":
        target_cfg.setdefault("ai_agent", {})["internet_search_enabled"] = bool(enabled)
        return target_cfg

    frontends_cfg = target_cfg.get("agent_frontends")
    if not isinstance(frontends_cfg, dict):
        frontends_cfg = {}
        target_cfg["agent_frontends"] = frontends_cfg
    existing = frontends_cfg.get(normalized)
    if isinstance(existing, dict):
        next_entry = dict(existing)
        next_entry["enabled"] = bool(enabled)
        frontends_cfg[normalized] = next_entry
    else:
        frontends_cfg[normalized] = bool(enabled)

    if normalized == "admin_agent":
        target_cfg.setdefault("admin", {})["frontend_proxy_enabled"] = bool(enabled)

    return target_cfg

def _is_internet_agent_installed() -> bool:
    return "internet_agent" in set(
        load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT).get("installed_agents", [])
    )

def _is_audio_agent_installed() -> bool:
    return "audio_agent" in set(
        load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT).get("installed_agents", [])
    )

def _is_remote_desktop_agent_installed() -> bool:
    try:
        return "remote_desktop_agent" in set(
            load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT).get("installed_agents", [])
        )
    except SecureStorageError as exc:
        # This optional capability must not prevent an ordinary chat/pairing
        # session when the install registry is protected by a stronger storage
        # tier than this server has enabled. Treat the feature as unavailable;
        # never weaken the registry's storage policy to build peer capabilities.
        LOGGER.debug(
            "Remote desktop capability lookup unavailable with the current storage tier (%s)",
            type(exc).__name__,
        )
        return False

def _apply_internet_search_enabled(enabled: bool) -> Dict[str, Any]:
    cfg = _set_agent_frontend_enabled("internet_agent", bool(enabled), cfg=_loaded_config_for_update())
    _persist_state_config(cfg)

    ai_agent_cfg = cfg.get("ai_agent", {})
    try:
        from service_manager import ServiceConfig, update_service_config

        sm_conf = ServiceConfig(
            db_path=str(_resolve_ai_agent_memory_db_path()),
            ai_agent_server_port=ai_agent_cfg.get("port", 8081),
            record_messages=ai_agent_cfg.get("record_messages_in_database", True),
            internet_search_enabled=bool(enabled),
            audio_playback_enabled=_get_audio_playback_enabled(cfg=cfg),
            memory_backend=ai_agent_cfg.get("memory_backend", "legacy"),
            adk_db_path=_resolve_ai_agent_storage_uris().get("session_service_uri"),
        )
        update_service_config(sm_conf)
    except Exception as exc:
        LOGGER.warning(f"Failed to update service manager config: {exc}")

    try:
        os.environ["AUTOYOU_INTERNET_SEARCH_ENABLED"] = "1" if bool(enabled) else "0"
    except Exception:
        pass

    return cfg


def _apply_ai_agent_runtime_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Apply authenticated parent-process settings inside the AI worker.

    The AI worker is a separate process, so changes to the admin process
    environment do not propagate after startup. This function deliberately
    updates both sources consulted by the root and Internet agents.
    """
    if not isinstance(payload, dict):
        raise ValueError("Runtime settings must be a JSON object.")

    applied: Dict[str, Any] = {}
    if "internet_search_enabled" in payload:
        enabled = _coerce_enabled_flag(payload.get("internet_search_enabled"))
        os.environ["AUTOYOU_INTERNET_SEARCH_ENABLED"] = "1" if enabled else "0"
        try:
            from service_manager import get_service_manager

            service_manager = get_service_manager()
            service_manager.config.internet_search_enabled = bool(enabled)
            service_manager.set_app_state("internet_search_enabled", bool(enabled))
        except Exception as exc:
            LOGGER.warning("Could not update AI worker internet-search state: %s", exc)
            raise RuntimeError("Could not update AI worker internet-search state.") from exc
        applied["internet_search_enabled"] = bool(enabled)

    if not applied:
        raise ValueError("No supported runtime settings were provided.")
    return {"success": True, "applied": applied}


async def _sync_ai_agent_runtime_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Push mutable settings to an already-running AI worker."""
    probe_host = _normalize_probe_host(_configured_ai_agent_bind_host(STATE.config))
    worker_healthy = await asyncio.to_thread(
        _is_ai_agent_server_healthy,
        probe_host,
        AI_AGENT_SERVER_PORT,
    )
    if not worker_healthy:
        return {
            "success": True,
            "synchronized": False,
            "status": "worker_not_running",
        }
    if httpx is None:
        return {
            "success": False,
            "synchronized": False,
            "status": "http_client_unavailable",
        }

    token = _load_or_create_ai_agent_internal_api_token()
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.post(
                f"http://127.0.0.1:{AI_AGENT_SERVER_PORT}/api/internal/runtime-settings",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
            )
        response_payload = response.json() if response.content else {}
    except Exception as exc:
        LOGGER.warning("Could not synchronize AI worker runtime settings: %s", exc)
        return {
            "success": False,
            "synchronized": False,
            "status": "request_failed",
            "error": str(exc),
        }

    if response.status_code >= 400 or not bool(response_payload.get("success")):
        return {
            "success": False,
            "synchronized": False,
            "status": "worker_rejected_update",
            "http_status": int(response.status_code),
        }
    return {
        "success": True,
        "synchronized": True,
        "status": "updated",
        "applied": response_payload.get("applied", {}),
    }


async def _set_internet_search_enabled_live(enabled: bool) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Persist the switch and make the running AI worker agree immediately."""
    cfg = _apply_internet_search_enabled(bool(enabled))
    sync_result = await _sync_ai_agent_runtime_settings(
        {"internet_search_enabled": bool(enabled)}
    )
    if sync_result.get("success"):
        return cfg, sync_result

    if _is_agent_process_running(STATE.agent_process):
        LOGGER.warning("Live AI setting sync failed; restarting the managed AI worker.")
        restarted = bool(await restart_ai_agent_server())
        if restarted:
            return cfg, {
                "success": True,
                "synchronized": True,
                "status": "worker_restarted",
            }
    return cfg, sync_result

def _stop_audio_manager_playback(audio_manager: Any, *, source: str) -> bool:
    stop_playback = getattr(audio_manager, "stop_playback", None)
    if callable(stop_playback):
        try:
            return bool(stop_playback(source=source))
        except TypeError:
            return bool(stop_playback())

    # Compatibility with AudioManager versions where media shared the TTS lane.
    stop_speaking = getattr(audio_manager, "stop_speaking", None)
    if callable(stop_speaking):
        stop_speaking(source=source)
        return True
    return False


def _apply_audio_playback_enabled(enabled: bool) -> Dict[str, Any]:
    cfg = _set_audio_playback_enabled(bool(enabled), cfg=_loaded_config_for_update())
    _persist_state_config(cfg)
    _resolve_audio_playback_music_library_dirs(cfg=cfg)

    ai_agent_cfg = cfg.get("ai_agent", {})
    try:
        from service_manager import ServiceConfig, update_service_config

        sm_conf = ServiceConfig(
            db_path=str(_resolve_ai_agent_memory_db_path()),
            ai_agent_server_port=ai_agent_cfg.get("port", 8081),
            record_messages=ai_agent_cfg.get("record_messages_in_database", True),
            internet_search_enabled=bool(ai_agent_cfg.get("internet_search_enabled", True)),
            audio_playback_enabled=bool(enabled),
            memory_backend=ai_agent_cfg.get("memory_backend", "legacy"),
            adk_db_path=_resolve_ai_agent_storage_uris().get("session_service_uri"),
        )
        update_service_config(sm_conf)
    except Exception as exc:
        LOGGER.warning(f"Failed to update service manager config: {exc}")

    try:
        set_audio_playback_enabled_env(bool(enabled))
    except Exception:
        pass

    if not enabled:
        for session_id, audio_manager in list((STATE.audio_managers or {}).items()):
            try:
                _stop_audio_manager_playback(
                    audio_manager,
                    source=f"audio_playback_disabled:{session_id}",
                )
            except Exception as exc:
                LOGGER.warning("Failed to stop audio playback for %s after disable: %s", session_id, exc)

    return cfg

def _is_frontend_entry_active(
    agent_name: str,
    frontend_entry: Optional[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    if not isinstance(frontend_entry, dict):
        return False
    if not _get_agent_frontend_enabled(agent_name, cfg=cfg):
        return False
    if frontend_entry.get("direct_forward_port"):
        return True
    if frontend_entry.get("proxy_port"):
        return True
    return not bool(frontend_entry.get("requires_proxy_registration", True))

def _build_agent_frontend_control(
    agent_name: str,
    *,
    installed: bool,
    frontend_entry: Optional[Dict[str, Any]] = None,
    active_frontend: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    normalized = package_agent_name(agent_name)
    if not normalized:
        return None

    if normalized != "internet_agent" and not isinstance(frontend_entry, dict):
        return None

    enabled = _get_agent_frontend_enabled(normalized, cfg=cfg)
    default_enabled = _default_agent_frontend_enabled(normalized)
    label = FRONTEND_CONTROL_LABELS.get(
        normalized,
        "Agent Website" if isinstance(frontend_entry, dict) else "Agent Control",
    )
    help_text = FRONTEND_CONTROL_HELP.get(
        normalized,
        "Enable or disable this agent website for AutoYou browser clients.",
    )

    if normalized == "internet_agent":
        state_text = (
            "Installed and allowed to perform live web searches."
            if installed and enabled
            else (
                "Installed, but live web searches are blocked."
                if installed
                else "Install Internet Search to use this control."
            )
        )
        return {
            "kind": "internet_search",
            "label": label,
            "help_text": help_text,
            "enabled": bool(enabled),
            "default_enabled": bool(default_enabled),
            "installed": bool(installed),
            "active": bool(installed and enabled),
            "toggle_label": "Disable Internet Search" if enabled else "Enable Internet Search",
            "state_text": state_text,
        }

    effective_entry = active_frontend if isinstance(active_frontend, dict) else frontend_entry
    route_mode = _get_agent_frontend_route_mode(normalized, cfg=cfg, frontend_entry=effective_entry)
    route_port = _frontend_route_port_for_mode(effective_entry, route_mode)
    proxy_path = _frontend_proxy_path(effective_entry)
    page_service_url = _get_autoyou_page_service_base_url(cfg)
    path_proxy_url = f"{page_service_url}{proxy_path}"
    open_url = str((effective_entry or {}).get("open_url") or "").strip()
    if not open_url:
        open_url = (
            f"http://127.0.0.1:{route_port}{_frontend_entry_path(effective_entry) if _frontend_entry_path(effective_entry) != '/' else '/'}"
            if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD and route_port > 0
            else proxy_path
        )
    active = bool(active_frontend)
    if not installed:
        state_text = "Install this agent before its website can be exposed."
    elif enabled and active:
        if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD:
            state_text = "Enabled as a direct same-port website route."
        else:
            state_text = "Enabled under the primary AutoYou browser path."
    elif enabled:
        state_text = "Enabled, but waiting for the website backend or proxy registration."
    else:
        state_text = "Disabled. This website stays hidden from remote browser clients."

    return {
        "kind": "agent_website",
        "label": label,
        "help_text": help_text,
        "enabled": bool(enabled),
        "default_enabled": bool(default_enabled),
        "installed": bool(installed),
        "active": bool(active),
        "proxy_path": proxy_path,
        "browser_path": proxy_path,
        "open_url": open_url,
        "page_service_url": page_service_url,
        "path_proxy_url": path_proxy_url,
        "route_mode": route_mode,
        "route_mode_label": "Direct same-port" if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD else "Primary browser path",
        "route_modes": [
            {
                "value": AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY,
                "label": "Primary browser path",
                "description": "Use /agent/name on the primary AutoYou browser port; clients do not bind an extra localhost port.",
                "disabled": normalized in AGENT_FRONTEND_REQUIRED_DIRECT_FORWARD,
            },
            {
                "value": AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD,
                "label": "Direct same-port",
                "description": "Ask clients to mirror this website on its own localhost port for apps that need root-relative paths.",
                "disabled": False,
            },
        ],
        "direct_forward_port": route_port if route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD and route_port > 0 else None,
        "toggle_label": "Disable Website" if enabled else "Enable Website",
        "state_text": state_text,
    }

def _load_agent_prompt_description(agent_name: str, *, frontend_entry: Optional[Dict[str, Any]] = None) -> str:
    return load_agent_prompt_description(
        agent_name,
        frontend_entry=frontend_entry,
        agents_root=_AUTOYOU_AGENTS_ROOT,
    )

def _draft_description_from_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    if not isinstance(metadata, dict):
        return ""
    owners = metadata.get("owners")
    if not isinstance(owners, dict):
        return ""
    for owner_name in ("coding", "builder", "frontend"):
        owner_state = owners.get(owner_name)
        if not isinstance(owner_state, dict):
            continue
        description = str(owner_state.get("description") or "").strip()
        if description:
            return description
    return ""

def _build_agent_workbench_context() -> Dict[str, Any]:
    try:
        from autoyou_agents.agent_builder_agent.agent import list_existing_agents
    except Exception as exc:  # noqa: BLE001 - missing builder module must not blank the listing
        LOGGER.warning("agent_builder_agent import failed; using embedded agent discovery: %s", exc)

        def list_existing_agents() -> Dict[str, Any]:
            discovered = discover_agent_directories(_AUTOYOU_AGENTS_ROOT)
            return {"status": "success", "agents": discovered, "count": len(discovered)}

    packaged_runtime = is_compiled()
    workspace_agents_root = _workspace_agents_root()
    workspace_drafts_root = get_agent_drafts_root(agents_root=workspace_agents_root)
    try:
        install_registry = refresh_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT)
    except Exception as exc:  # noqa: BLE001 - registry refresh must not blank the listing
        LOGGER.warning("Agent install registry refresh failed; using last readable registry: %s", exc)
        try:
            install_registry = load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT)
        except Exception as load_exc:  # noqa: BLE001 - still render discovered agents if the registry is unreadable
            LOGGER.warning("Agent install registry load failed; using discovered agent defaults: %s", load_exc)
            discovered_agents = discover_agent_directories(_AUTOYOU_AGENTS_ROOT)
            install_registry = {
                "installed_agents": [],
                "agents": {
                    agent_name: {"installed": False, "description": None, "source": None, "updated_at": None}
                    for agent_name in discovered_agents
                },
            }
    result = list_existing_agents()
    if result.get("status") != "success":
        # In a packaged runtime the on-disk agent scan can come back empty or
        # error even though the agents are compiled into the binary. Fall back
        # to the embedded-agent discovery (which seeds the built-in agent list
        # in compiled mode) so the Agents screen still populates.
        fallback_agents = discover_agent_directories(_AUTOYOU_AGENTS_ROOT)
        if not fallback_agents:
            return result
        LOGGER.warning(
            "list_existing_agents returned %s; falling back to embedded agent discovery (%d agents)",
            result.get("status"),
            len(fallback_agents),
        )
        result = {"status": "success", "agents": fallback_agents, "count": len(fallback_agents)}

    live_agents = sorted(set(result.get("agents", [])))
    draft_agents = sorted(set(list_agent_draft_names(agents_root=workspace_agents_root)))
    all_agents = sorted(set(live_agents) | set(draft_agents))

    installed_agents = [
        agent_name for agent_name in install_registry.get("installed_agents", [])
        if agent_name in live_agents
    ]
    installed_set = set(installed_agents)
    available_agents = [
        agent_name for agent_name in live_agents
        if agent_name not in installed_set
    ]

    effective_cfg = STATE.config or {}
    port_map: Dict[str, Optional[int]] = dict(STATE.dynamic_agent_proxy_ports or {})
    for agent in live_agents:
        port_map.setdefault(agent, None)

    browser_base_url = _get_autoyou_page_service_base_url(effective_cfg)
    discovered_frontend_entries = [
        _apply_agent_frontend_route_policy(entry, cfg=effective_cfg)
        for entry in discover_frontend_manifests(
            agents_root=_AUTOYOU_AGENTS_ROOT,
            agent_names=live_agents,
            proxy_ports={k: v for k, v in port_map.items() if v},
            browser_base_url=browser_base_url,
        )
    ]
    frontend_manifest_map = {entry["agent_name"]: entry for entry in discovered_frontend_entries}
    runtime_frontend_manifest_map = {
        resolve_runtime_agent_name(entry["agent_name"]): entry
        for entry in discovered_frontend_entries
    }
    active_frontend_entries = [
        entry
        for entry in discovered_frontend_entries
        if _is_frontend_entry_active(entry.get("agent_name", ""), entry, cfg=effective_cfg)
    ]
    active_frontend_map = {entry["agent_name"]: entry for entry in active_frontend_entries}
    runtime_active_frontend_map = {
        resolve_runtime_agent_name(entry["agent_name"]): entry
        for entry in active_frontend_entries
    }
    draft_metadata_map = {
        agent_name: load_agent_draft_metadata(agent_name, agents_root=workspace_agents_root)
        for agent_name in draft_agents
    }

    workspace_only_agents: List[str] = []
    if packaged_runtime:
        for agent_name in all_agents:
            live_exists = agent_name in live_agents
            draft_exists = agent_name in draft_metadata_map
            runtime_installable = live_exists and can_install_agent_in_runtime(agent_name, compiled=True)
            draft_only = draft_exists and not live_exists
            if draft_only or (live_exists and not runtime_installable):
                workspace_only_agents.append(agent_name)

    return {
        "status": "success",
        "live_agents": live_agents,
        "draft_agents": draft_agents,
        "all_agents": all_agents,
        "installed_agents": installed_agents,
        "installed_set": installed_set,
        "available_agents": available_agents,
        "install_registry": install_registry,
        "install_registry_agents": install_registry.get("agents", {}),
        "packaged_runtime": packaged_runtime,
        "runtime_mode": "compiled" if packaged_runtime else "source",
        "workspace_agents_root": workspace_agents_root,
        "workspace_drafts_root": workspace_drafts_root,
        "effective_cfg": effective_cfg,
        "port_map": port_map,
        "browser_base_url": browser_base_url,
        "discovered_frontend_entries": discovered_frontend_entries,
        "active_frontend_entries": active_frontend_entries,
        "frontend_manifest_map": frontend_manifest_map,
        "runtime_frontend_manifest_map": runtime_frontend_manifest_map,
        "active_frontend_map": active_frontend_map,
        "runtime_active_frontend_map": runtime_active_frontend_map,
        "draft_metadata_map": draft_metadata_map,
        "workspace_only_agents": workspace_only_agents,
        "builder_installed": "agent_builder_agent" in installed_set,
        "coding_installed": "coding_agent" in installed_set,
        "website_agent_installed": "website_agent" in installed_set,
        "internet_agent_installed": "internet_agent" in installed_set,
    }

def _build_agent_summary_detail(agent_name: str, context: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_agent_directory_name(agent_name)
    runtime_agent_name = resolve_runtime_agent_name(normalized)
    frontend_entry = (
        context["frontend_manifest_map"].get(normalized)
        or context["runtime_frontend_manifest_map"].get(runtime_agent_name)
    )
    active_frontend = (
        context["active_frontend_map"].get(normalized)
        or context["runtime_active_frontend_map"].get(runtime_agent_name)
    )
    draft_metadata = context["draft_metadata_map"].get(normalized)
    draft_exists = bool(draft_metadata and draft_metadata.get("draft_exists"))
    live_source = build_live_agent_source_info(normalized)
    live_exists = bool(live_source.get("exists") or normalized in context["live_agents"])
    draft_only = bool(draft_exists and not live_exists)
    runtime_installable = bool(
        live_exists and can_install_agent_in_runtime(normalized, compiled=context["packaged_runtime"])
    )
    runtime_blocked = bool(context["packaged_runtime"] and live_exists and not runtime_installable)
    runtime_block_reason = (
        _workspace_agent_runtime_block_reason(normalized)
        if runtime_blocked or (context["packaged_runtime"] and draft_only)
        else None
    )

    description = ""
    if live_exists:
        description = _load_agent_prompt_description(
            normalized,
            frontend_entry=frontend_entry,
        )
    if not description and draft_exists:
        description = _draft_description_from_metadata(draft_metadata)
    if not description and isinstance(frontend_entry, dict):
        description = str(frontend_entry.get("description") or "").strip()
    if not description:
        description = f"{format_agent_display_name(runtime_agent_name)} agent."

    frontend_enabled = (
        _get_agent_frontend_enabled(normalized, cfg=context["effective_cfg"])
        if (normalized == "internet_agent" or frontend_entry)
        else None
    )
    frontend_control = _build_agent_frontend_control(
        normalized,
        installed=normalized in context["installed_set"],
        frontend_entry=frontend_entry,
        active_frontend=active_frontend,
        cfg=context["effective_cfg"],
    )
    frontend_route_mode = (
        frontend_control.get("route_mode")
        if isinstance(frontend_control, dict)
        else (
            _get_agent_frontend_route_mode(normalized, cfg=context["effective_cfg"], frontend_entry=frontend_entry)
            if isinstance(frontend_entry, dict)
            else None
        )
    )
    frontend_path_source = active_frontend if isinstance(active_frontend, dict) else frontend_entry
    frontend_path = ""
    if isinstance(frontend_path_source, dict):
        frontend_path = str(
            frontend_path_source.get("open_url")
            or frontend_path_source.get("launch_url")
            or frontend_path_source.get("launch_path")
            or frontend_path_source.get("proxy_path")
            or ""
        ).strip()

    return {
        "agent_name": normalized,
        "runtime_agent_name": runtime_agent_name,
        "display_name": format_agent_display_name(runtime_agent_name),
        "installed": normalized in context["installed_set"],
        "registry": context["install_registry_agents"].get(normalized, {}),
        "description": description,
        "proxy_port": context["port_map"].get(normalized),
        "has_frontend": bool(frontend_entry),
        "frontend": active_frontend,
        "frontend_manifest": frontend_entry,
        "frontend_enabled": frontend_enabled,
        "frontend_route_mode": frontend_route_mode,
        "frontend_path": frontend_path,
        "frontend_control": frontend_control,
        "builtin_agent": is_builtin_agent_name(normalized),
        "live_exists": live_exists,
        "live_source": live_source,
        "draft_exists": draft_exists,
        "draft_only": draft_only,
        "draft_metadata": draft_metadata,
        "can_install": bool(runtime_installable and normalized not in context["installed_set"]),
        "can_uninstall": bool(normalized in context["installed_set"]),
        "runtime_blocked": runtime_blocked,
        "runtime_block_reason": runtime_block_reason,
        "workspace_only": bool(context["packaged_runtime"] and (runtime_blocked or draft_only)),
        "workspace_path": str((context["workspace_agents_root"] / normalized).resolve()),
    }

def _build_agent_workbench_detail_payload(agent_name: str) -> Dict[str, Any]:
    context = _build_agent_workbench_context()
    if context.get("status") != "success":
        return context

    normalized = _normalize_agent_directory_name(agent_name)
    if normalized not in context["all_agents"]:
        return {
            "status": "error",
            "error": f"Agent '{normalized}' was not found.",
        }

    summary = _build_agent_summary_detail(normalized, context)
    draft_summary = (
        build_agent_draft_summary(normalized, agents_root=context["workspace_agents_root"])
        if summary.get("draft_exists")
        else {
            "exists": False,
            "agent_name": normalized,
            "draft_dir": str((context["workspace_drafts_root"] / normalized).resolve()),
            "metadata": load_agent_draft_metadata(normalized, agents_root=context["workspace_agents_root"]),
            "instruction": {
                "available": False,
                "agent_name": normalized,
                "error": "No workspace draft exists yet.",
            },
            "frontend_manifest": None,
            "website_files": [],
            "has_source_code": False,
            "source_unavailable_note": None,
            "can_publish": False,
            "can_test": False,
            "can_install_runtime": False,
        }
    )
    live_instruction = read_live_instruction_payload(normalized) if summary.get("live_exists") else {
        "available": False,
        "agent_name": normalized,
        "error": "Live agent instructions are not available yet.",
    }
    draft_instruction = draft_summary.get("instruction") or {
        "available": False,
        "agent_name": normalized,
        "error": "No workspace draft exists yet.",
    }

    builder_owner = (((draft_summary.get("metadata") or {}).get("owners") or {}).get("builder") or {})
    coding_owner = (((draft_summary.get("metadata") or {}).get("owners") or {}).get("coding") or {})
    frontend_owner = (((draft_summary.get("metadata") or {}).get("owners") or {}).get("frontend") or {})
    publish_block_reason = (
        "Publishing is disabled in packaged AutoYou. Move this draft to a Python checkout to publish, install, and test it."
        if context["packaged_runtime"]
        else None
    )
    test_block_reason = (
        "Testing workspace drafts is disabled in packaged AutoYou because only built-in compiled agents can run here."
        if context["packaged_runtime"]
        else None
    )

    detail = {
        **summary,
        "packaged_runtime": context["packaged_runtime"],
        "runtime_mode": context["runtime_mode"],
        "workspace_agents_root": str(context["workspace_agents_root"]),
        "workspace_drafts_root": str(context["workspace_drafts_root"]),
        "live_instruction": live_instruction,
        "draft": draft_summary,
        "draft_instruction": draft_instruction,
        "builder_agent_installed": context["builder_installed"],
        "coding_agent_installed": context["coding_installed"],
        "website_agent_installed": context["website_agent_installed"],
        "builder_owner": builder_owner,
        "coding_owner": coding_owner,
        "frontend_owner": frontend_owner,
        "can_clone_draft": bool(summary.get("live_exists") and not summary.get("draft_exists")),
        "can_discard_draft": bool(summary.get("draft_exists")),
        "can_edit_draft_instructions": bool(summary.get("draft_exists") and draft_instruction.get("available")),
        "can_scaffold_frontend": bool(summary.get("draft_exists")),
        "can_save_frontend_manifest": bool(summary.get("draft_exists")),
        "can_publish_draft": bool(draft_summary.get("can_publish")),
        "can_test_draft": bool(draft_summary.get("can_test")),
        "publish_block_reason": publish_block_reason,
        "test_block_reason": test_block_reason,
    }
    return {
        "status": "success",
        "agent_name": normalized,
        "detail": detail,
    }

def _build_agent_builder_listing_payload() -> Dict[str, Any]:
    context = _build_agent_workbench_context()
    if context.get("status") != "success":
        return context

    try:
        suite_payload = builder_suite_status(
            agents_root=_AUTOYOU_AGENTS_ROOT,
            selected_model=_configured_ollama_model_for_behavior(),
        )
    except Exception as exc:
        LOGGER.warning("Failed to build agent-builder suite status: %s", exc)
        suite_payload = {
            "suite": "agent_builder",
            "agent_names": list(BUILDER_SUITE_AGENT_NAMES),
            "installed": False,
            "recommended_ollama_model": DEFAULT_BUILDER_SUITE_OLLAMA_MODEL,
            "selected_ollama_model": _configured_ollama_model_for_behavior(),
            "agents": [],
        }

    agent_details = {
        agent_name: _build_agent_summary_detail(agent_name, context)
        for agent_name in context["all_agents"]
    }
    overview_entries: List[Dict[str, Any]] = []
    for agent_name, detail in agent_details.items():
        if detail.get("draft_only"):
            state_label = "Draft Only"
            state_tone = "draft"
        elif detail.get("installed"):
            state_label = "Installed"
            state_tone = "installed"
        elif detail.get("runtime_blocked"):
            state_label = "Blocked"
            state_tone = "blocked"
        else:
            state_label = "Available"
            state_tone = "available"

        draft_metadata = detail.get("draft_metadata") or {}
        owner_states = (draft_metadata.get("owners") or {}) if isinstance(draft_metadata, dict) else {}
        overview_entries.append(
            {
                "agent_name": detail["agent_name"],
                "display_name": detail["display_name"],
                "description": detail["description"],
                "installed": detail["installed"],
                "draft_exists": detail["draft_exists"],
                "draft_only": detail["draft_only"],
                "runtime_blocked": detail["runtime_blocked"],
                "workspace_only": detail["workspace_only"],
                "builtin_agent": detail["builtin_agent"],
                "has_frontend": detail["has_frontend"],
                "frontend_enabled": bool((detail.get("frontend_control") or {}).get("enabled")),
                "frontend_route_mode": detail.get("frontend_route_mode"),
                "state_label": state_label,
                "state_tone": state_tone,
                "builder_status": ((owner_states.get("builder") or {}).get("status") or "idle"),
                "coding_status": ((owner_states.get("coding") or {}).get("status") or "idle"),
                "frontend_status": ((owner_states.get("frontend") or {}).get("status") or "idle"),
            }
        )
    overview_entries.sort(
        key=lambda item: (
            0 if item.get("installed") else 1,
            0 if item.get("draft_exists") else 1,
            0 if item.get("builtin_agent") else 1,
            str(item.get("display_name") or "").lower(),
        )
    )

    return {
        "status": "success",
        "agents": list(context["all_agents"]),
        "live_agents": list(context["live_agents"]),
        "draft_agents": list(context["draft_agents"]),
        "proxy_ports": context["port_map"],
        "autoyou_browser_base_url": context["browser_base_url"],
        "frontends": [
            entry for entry in context["active_frontend_entries"]
            if entry.get("agent_name") in context["installed_set"]
        ],
        "install_registry": context["install_registry"],
        "installed_agents": list(context["installed_agents"]),
        "available_agents": list(context["available_agents"]),
        "packaged_runtime": context["packaged_runtime"],
        "runtime_mode": context["runtime_mode"],
        "workspace_agents_root": str(context["workspace_agents_root"]),
        "workspace_drafts_root": str(context["workspace_drafts_root"]),
        "workspace_only_agents": list(context["workspace_only_agents"]),
        "runtime_policy_note": (
            "Built-in agents can be installed in this app. "
            "Workspace drafts stay editable here but cannot load, publish, or be tested here."
            if context["packaged_runtime"]
            else "Workspace drafts stay isolated until you publish them and choose whether to install them."
        ),
        "builder_installed": context["builder_installed"],
        "coding_installed": context["coding_installed"],
        "website_agent_installed": context["website_agent_installed"],
        "internet_agent_installed": context["internet_agent_installed"],
        "overview_counts": {
            "total": len(context["all_agents"]),
            "installed": len(context["installed_agents"]),
            "available": len(context["available_agents"]),
            "drafts": len(context["draft_agents"]),
        },
        "workbench_agents": {
            "agent_builder_agent": {"installed": context["builder_installed"]},
            "coding_agent": {"installed": context["coding_installed"]},
            "website_agent": {"installed": context["website_agent_installed"]},
        },
        "builder_suite": suite_payload,
        "agent_overview": overview_entries,
        "agent_details": agent_details,
    }

def _sync_frontend_registry_from_builder_payload(payload: Dict[str, Any]) -> None:
    try:
        active_frontends = payload.get("frontends", [])
        agent_names: List[str] = []
        proxy_ports: Dict[str, Any] = {}
        if isinstance(active_frontends, list) and active_frontends:
            for entry in active_frontends:
                if not isinstance(entry, dict):
                    continue
                agent_name = str(entry.get("agent_name") or "").strip()
                if not agent_name:
                    continue
                agent_names.append(agent_name)
                if entry.get("proxy_port") not in (None, ""):
                    proxy_ports[agent_name] = entry.get("proxy_port")
        else:
            raw_proxy_ports = payload.get("proxy_ports")
            if not isinstance(raw_proxy_ports, dict):
                raw_proxy_ports = getattr(STATE, "dynamic_agent_proxy_ports", {}) or {}
            for agent_name, port in raw_proxy_ports.items():
                normalized = package_agent_name(agent_name)
                if not normalized or not _get_agent_frontend_enabled(normalized, cfg=(STATE.config or {})):
                    continue
                agent_names.append(normalized)
                if port not in (None, ""):
                    proxy_ports[normalized] = port
        refresh_frontend_registry(
            agents_root=_AUTOYOU_AGENTS_ROOT,
            agent_names=agent_names,
            proxy_ports=proxy_ports,
            browser_base_url=payload.get("autoyou_browser_base_url"),
            default_agent=_resolve_default_agent_website(STATE.config or {}),
        )
    except Exception as exc:
        LOGGER.warning("Failed to refresh frontend registry: %s", exc)

def _build_browser_port_routes_from_frontend_registry(
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Expose direct localhost routes that clients should mirror 1:1.

    This is intentionally separate from path-routed agent websites. The client
    may choose any local port for the main AutoYou browser proxy, but direct
    service routes (for example the admin UI on port 8001) should be mirrored on
    the same localhost port as the upstream service to avoid path/origin
    rewriting bugs in UIs that were not designed to live under `/agent/...`.
    """
    try:
        registry = load_frontend_registry()
        frontends = registry.get("frontends", [])
        if not isinstance(frontends, list):
            return []
    except Exception as exc:
        LOGGER.warning("Failed to load frontend registry for browser port routes: %s", exc)
        return []

    routes: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in frontends:
        if not isinstance(entry, dict):
            continue

        agent_name = str(entry.get("agent_name") or "").strip()
        if cfg is not None and agent_name and not _get_agent_frontend_enabled(agent_name, cfg=cfg):
            continue
        policy_entry = _apply_agent_frontend_route_policy(entry, cfg=cfg)
        route_mode = _get_agent_frontend_route_mode(agent_name, cfg=cfg, frontend_entry=policy_entry)
        if route_mode != AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD:
            continue

        route_port = _frontend_route_port_for_mode(policy_entry, route_mode)
        if route_port <= 0:
            continue

        route_id = f"agent:{agent_name}" if agent_name else f"port:{route_port}"
        if route_id in seen_ids:
            continue
        seen_ids.add(route_id)

        entry_path = _frontend_entry_path(policy_entry)
        proxy_path = _frontend_proxy_path(policy_entry)
        local_url = str(policy_entry.get("local_url") or f"http://127.0.0.1:{route_port}{entry_path if entry_path != '/' else '/'}").strip()
        launch_url = str(policy_entry.get("launch_url") or "").strip()
        open_url = str(policy_entry.get("open_url") or "").strip()
        if not open_url:
            open_url = local_url

        # H-16: WebSocket upgrades require explicit opt-in per frontend
        # registry entry. An agent that only serves HTTP should not
        # automatically become a WS-reachable target - otherwise a
        # browser-agent running under one frontend could tunnel a long-
        # lived WS into an unrelated localhost service.
        websocket_enabled = _coerce_enabled_flag(entry.get("websocket_enabled", False))

        routes.append(
            {
                "route_id": route_id,
                "agent_name": agent_name or None,
                "kind": "agent_frontend",
                "title": str(policy_entry.get("title") or agent_name or f"Localhost {route_port}").strip(),
                "description": str(policy_entry.get("description") or "").strip() or None,
                "port": route_port,
                "local_url": local_url,
                "path": entry_path,
                "proxy_path": proxy_path,
                "launch_path": proxy_path,
                "launch_url": launch_url or None,
                "open_url": open_url,
                "route_mode": AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD,
                "direct_forward_port": route_port,
                "uses_direct_forward_port": True,
                "websocket_enabled": bool(websocket_enabled),
            }
        )

    return routes

def _build_agent_website_routes_from_frontend_registry(
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return all enabled agent website routes for discovery/UI display.

    Unlike ``browser_port_routes``, these entries do not imply that native
    clients should bind secondary same-port listeners. Path-proxied websites
    stay on the primary browser proxy at ``/agent/{name}/...``.
    """
    try:
        registry = load_frontend_registry()
        frontends = registry.get("frontends", [])
        if not isinstance(frontends, list):
            return []
    except Exception as exc:
        LOGGER.warning("Failed to load frontend registry for agent website routes: %s", exc)
        return []

    resolved_default = str(registry.get("default_agent") or _resolve_default_agent_website(cfg) or "").strip()

    routes: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for entry in frontends:
        if not isinstance(entry, dict):
            continue
        agent_name = str(entry.get("agent_name") or "").strip()
        if cfg is not None and agent_name and not _get_agent_frontend_enabled(agent_name, cfg=cfg):
            continue
        policy_entry = _apply_agent_frontend_route_policy(entry, cfg=cfg)

        route_id = f"agent:{agent_name}" if agent_name else str(entry.get("route_id") or "").strip()
        if not route_id or route_id in seen_ids:
            continue
        seen_ids.add(route_id)

        entry_path = _frontend_entry_path(policy_entry)
        proxy_path = _frontend_proxy_path(policy_entry)
        page_service_url = _get_autoyou_page_service_base_url(cfg)
        path_proxy_url = f"{page_service_url}{proxy_path}"
        route_mode = _get_agent_frontend_route_mode(agent_name, cfg=cfg, frontend_entry=policy_entry)
        uses_direct_forward_port = route_mode == AGENT_WEBSITE_ROUTE_MODE_DIRECT_FORWARD
        try:
            proxy_port = int(policy_entry.get("proxy_port") or 0)
        except Exception:
            proxy_port = 0
        try:
            recommended_port = int(policy_entry.get("recommended_port") or 0)
        except Exception:
            recommended_port = 0

        route_port = _frontend_route_port_for_mode(policy_entry, route_mode)
        display_port = route_port
        if not uses_direct_forward_port and proxy_port > 0:
            display_port = proxy_port
        if display_port <= 0 and not uses_direct_forward_port:
            display_port = recommended_port

        server_local_url = ""
        if display_port > 0:
            server_local_url = str(policy_entry.get("local_url") or f"http://127.0.0.1:{display_port}{entry_path if entry_path != '/' else '/'}").strip()
        direct_local_url = server_local_url if uses_direct_forward_port and route_port > 0 else ""
        open_url = str(policy_entry.get("open_url") or "").strip()
        if not open_url:
            open_url = direct_local_url or proxy_path
        if uses_direct_forward_port and route_port <= 0:
            open_url = proxy_path
        if not uses_direct_forward_port and open_url.startswith("http://127.0.0.1:"):
            open_url = proxy_path

        auth_settings: Dict[str, Any] = {}
        if agent_name:
            try:
                from autoyou_agents.shared_tools import scheduler_mission_control as _mission_control

                auth_settings = _mission_control._get_agent_security_settings(agent_name)
            except Exception:
                auth_settings = {}

        routes.append(
            {
                "route_id": route_id,
                "agent_name": agent_name or None,
                "kind": "agent_frontend",
                "route_mode": route_mode,
                "title": str(policy_entry.get("title") or agent_name or "Website").strip(),
                "description": str(policy_entry.get("description") or "").strip() or None,
                "port": display_port or None,
                "local_url": direct_local_url or None,
                "server_local_url": server_local_url or None,
                "path": entry_path,
                "proxy_path": proxy_path,
                "launch_path": proxy_path,
                "launch_url": str(policy_entry.get("launch_url") or "").strip() or None,
                "open_url": open_url,
                "default": bool(agent_name and agent_name == resolved_default),
                "page_service_url": page_service_url,
                "path_proxy_url": path_proxy_url,
                "uses_direct_forward_port": bool(uses_direct_forward_port),
                "direct_forward_port": route_port if uses_direct_forward_port and route_port > 0 else None,
                "frontend_port_registered": bool(policy_entry.get("frontend_port_registered")),
                "websocket_enabled": bool(_coerce_enabled_flag(policy_entry.get("websocket_enabled", False))),
                "auth_mode": auth_settings.get("auth_mode", "totp"),
                "shared_session_eligible": bool(auth_settings.get("shared_session_eligible", True)),
            }
        )

    return routes

def _get_agent_frontend_websocket_enabled(
    agent_name: Any,
    *,
    cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return the manifest-backed WebSocket opt-in for an agent website."""
    normalized_name = package_agent_name(agent_name)
    if not normalized_name:
        return False
    for route in _build_agent_website_routes_from_frontend_registry(cfg=cfg):
        if package_agent_name(route.get("agent_name")) == normalized_name:
            return bool(route.get("websocket_enabled", False))
    return False

def _normalize_browser_target_origin(raw_target: Any, *, websocket: bool = False) -> str:
    from urllib.parse import urlsplit, urlunsplit

    normalized = str(raw_target or "").strip()
    if not normalized:
        return ""
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return ""
    if not parsed.netloc:
        return ""

    scheme = (parsed.scheme or "").lower()
    if websocket:
        if scheme == "http":
            scheme = "ws"
        elif scheme == "https":
            scheme = "wss"
        elif scheme not in {"ws", "wss"}:
            return ""
    else:
        if scheme == "ws":
            scheme = "http"
        elif scheme == "wss":
            scheme = "https"
        elif scheme not in {"http", "https"}:
            return ""
    return urlunsplit((scheme, parsed.netloc, "", "", ""))

def _agent_name_from_browser_proxy_path(path: str) -> str:
    match = re.match(r"^/agent/([^/?#]+)(?:/|$)", str(path or ""))
    if not match:
        return ""
    return package_agent_name(match.group(1))

def _browser_path_uses_page_service(path: str, cfg: Optional[Dict[str, Any]] = None) -> bool:
    normalized_path = str(path or "/")
    if (
        normalized_path == "/agent-frontends"
        or normalized_path.startswith("/agent-frontends/")
        or normalized_path == "/agent-websites"
        or normalized_path.startswith("/agent-websites/")
        or normalized_path == "/api/agent-frontends"
        or normalized_path == "/api/agent-websites"
        or normalized_path == "/api/agent-directory"
    ):
        return True

    agent_name = _agent_name_from_browser_proxy_path(normalized_path)
    if not agent_name:
        return False
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    if not _get_agent_frontend_enabled(agent_name, cfg=effective_cfg):
        return False
    return _get_agent_frontend_route_mode(agent_name, cfg=effective_cfg) == AGENT_WEBSITE_ROUTE_MODE_PATH_PROXY

def _route_path_from_target_url(raw_target: Any) -> str:
    from urllib.parse import urlsplit

    normalized = str(raw_target or "").strip()
    if not normalized:
        return "/"
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return "/"
    route_path = parsed.path or "/"
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    return route_path

def _default_local_browser_route_suffix(port: Any) -> str:
    try:
        normalized_port = int(port)
    except Exception:
        return "/"
    try:
        main_port = int(getattr(STATE, "main_server_port", 8081) or 8081)
    except Exception:
        main_port = 8081
    # The embedded agent runtime serves its real UI under /dev-ui/.
    # Advertising the bare origin works in desktop browsers thanks to the
    # server-side redirect, but some embedded mobile WebViews handle the root
    # 307 inconsistently. Canonicalize localhost bookmarks to the stable UI
    # entrypoint up front.
    if normalized_port == main_port:
        return "/dev-ui/"
    return "/"

def _route_suffix_from_target_url(raw_target: Any, *, port: Any) -> str:
    from urllib.parse import urlsplit

    default_suffix = _default_local_browser_route_suffix(port)
    normalized = str(raw_target or "").strip()
    if not normalized:
        return default_suffix
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return default_suffix

    route_path = parsed.path or "/"
    if not route_path.startswith("/"):
        route_path = f"/{route_path}"
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""

    if route_path == "/" and not query and not fragment:
        return default_suffix
    return f"{route_path}{query}{fragment}"

def _normalize_optional_advertised_target_url(raw_target: Any) -> str:
    from urllib.parse import urlsplit, urlunsplit

    normalized = str(raw_target or "").strip()
    if not normalized:
        return ""
    if not re.match(r"^[a-z][a-z0-9+.-]*://", normalized, flags=re.IGNORECASE):
        normalized = f"http://{normalized}"
    try:
        parsed = urlsplit(normalized)
    except Exception as exc:
        raise ValueError(f"Invalid advertised forward URL: {exc}") from exc
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Advertised forward URL must be an http(s) URL or host:port pair.")
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, parsed.fragment))

def _normalize_advertised_website_entries(
    raw_entries: Any,
    *,
    reserved_ports: Optional[set[int]] = None,
) -> List[Dict[str, Any]]:
    if raw_entries in (None, ""):
        return []
    entries = raw_entries
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except Exception as exc:
            raise ValueError(f"Advertised websites must be valid JSON: {exc}") from exc
    if entries in (None, ""):
        return []
    if not isinstance(entries, list):
        raise ValueError("Advertised websites must be a JSON array.")

    normalized_entries: List[Dict[str, Any]] = []
    seen_ports: set[int] = set()
    blocked_ports = {int(port) for port in (reserved_ports or set())}
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Advertised website entry #{index} must be an object.")
        try:
            port = int(entry.get("port"))
        except Exception as exc:
            raise ValueError(f"Advertised website entry #{index} is missing a valid port.") from exc
        if port < 1 or port > 65535:
            raise ValueError(f"Advertised website port {port} must be between 1 and 65535.")
        if port in blocked_ports:
            raise ValueError(
                f"Advertised website port {port} conflicts with an automatically managed AutoYou browser port."
            )
        if port in seen_ports:
            raise ValueError(f"Advertised website port {port} is duplicated.")
        target_url = _normalize_optional_advertised_target_url(entry.get("target_url"))
        normalized_entries.append(
            {
                "port": port,
                "label": str(entry.get("label") or "").strip() or f"Service :{port}",
                "description": str(entry.get("description") or "").strip(),
                "target_url": target_url,
                "enabled": _coerce_enabled_flag(entry.get("enabled", True)),
                # H-16: WS upgrades must be explicitly opted in per port.
                # The default (False) keeps existing HTTP-only deployments
                # safe - WS traffic is denied unless an operator flips
                # this flag in the admin config.
                "websocket_enabled": _coerce_enabled_flag(entry.get("websocket_enabled", False)),
            }
        )
        seen_ports.add(port)
    return normalized_entries

_BOOKMARK_ID_RE = re.compile(r"[^a-zA-Z0-9_-]+")

def _normalize_bookmark_url(raw_url: Any) -> str:
    from urllib.parse import urlsplit, urlunsplit

    normalized = str(raw_url or "").strip()
    if not normalized:
        raise ValueError("Bookmark URL is required.")
    if len(normalized) > 2048:
        raise ValueError("Bookmark URL must be 2048 characters or fewer.")
    if not re.match(r"^[a-z][a-z0-9+.-]*://", normalized, flags=re.IGNORECASE):
        if re.match(r"^[a-z][a-z0-9+.-]*:", normalized, flags=re.IGNORECASE):
            _prefix, suffix = normalized.split(":", 1)
            if not suffix.split("/", 1)[0].isdigit():
                raise ValueError("Bookmark URL must be an http(s) URL.")
        normalized = f"https://{normalized}"
    try:
        parsed = urlsplit(normalized)
    except Exception as exc:
        raise ValueError(f"Invalid bookmark URL: {exc}") from exc
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Bookmark URL must be an http(s) URL.")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid bookmark URL: {exc}") from exc
    path = parsed.path or "/"
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((scheme, parsed.netloc, path, parsed.query, parsed.fragment))

def _bookmark_title_from_url(normalized_url: str) -> str:
    try:
        parsed = urlsplit(normalized_url)
        host = str(parsed.hostname or "").strip()
        if host:
            return host[4:] if host.startswith("www.") else host
    except Exception:
        pass
    return "Bookmark"

def _normalize_bookmark_id(raw_id: Any, *, fallback_url: str, seen_ids: set[str]) -> str:
    raw = str(raw_id or "").strip()
    if raw.startswith("bookmark:"):
        raw = raw.split(":", 1)[1]
    normalized = _BOOKMARK_ID_RE.sub("-", raw).strip("-_").lower()
    if not normalized:
        normalized = f"bm-{generate_hash(fallback_url)[:12]}"
    normalized = normalized[:64].strip("-_") or f"bm-{generate_hash(fallback_url)[:12]}"
    candidate = normalized
    suffix = 2
    while candidate in seen_ids:
        suffix_text = f"-{suffix}"
        candidate = f"{normalized[:64 - len(suffix_text)]}{suffix_text}".strip("-_")
        suffix += 1
    seen_ids.add(candidate)
    return candidate

def _normalize_bookmark_entries(raw_entries: Any) -> List[Dict[str, Any]]:
    if raw_entries in (None, ""):
        return []
    entries = raw_entries
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except Exception as exc:
            raise ValueError(f"Bookmarks must be valid JSON: {exc}") from exc
    if entries in (None, ""):
        return []
    if not isinstance(entries, list):
        raise ValueError("Bookmarks must be a JSON array.")

    normalized_entries: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"Bookmark entry #{index} must be an object.")
        url = _normalize_bookmark_url(entry.get("url"))
        bookmark_id = _normalize_bookmark_id(entry.get("id"), fallback_url=url, seen_ids=seen_ids)
        title = str(entry.get("title") or "").strip()[:120] or _bookmark_title_from_url(url)
        description = str(entry.get("description") or "").strip()[:240]
        normalized_entries.append(
            {
                "id": bookmark_id,
                "title": title,
                "url": url,
                "description": description,
                "enabled": _coerce_enabled_flag(entry.get("enabled", True)),
            }
        )
    return normalized_entries

def _get_autoyou_advertised_websites(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    autoyou_config = effective_cfg.get("autoyou_page", {}) if isinstance(effective_cfg, dict) else {}
    try:
        return _normalize_advertised_website_entries(autoyou_config.get("advertised_websites"))
    except Exception as exc:
        LOGGER.warning("Failed to normalize advertised websites from config: %s", exc)
        return []

def _get_autoyou_bookmarks(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    autoyou_config = effective_cfg.get("autoyou_page", {}) if isinstance(effective_cfg, dict) else {}
    try:
        return _normalize_bookmark_entries(autoyou_config.get("bookmarks"))
    except Exception as exc:
        LOGGER.warning("Failed to normalize bookmarks from config: %s", exc)
        return []

def _get_reserved_browser_port_routes(cfg: Optional[Dict[str, Any]] = None) -> set[int]:
    reserved_ports = {_get_autoyou_browser_forward_port(cfg)}
    for route in [
        *_build_browser_port_routes_from_frontend_registry(),
        *_build_browser_port_routes_from_frontend_registry(cfg=cfg),
    ]:
        try:
            reserved_ports.add(int(route.get("port") or 0))
        except Exception:
            continue
    return {port for port in reserved_ports if port > 0}

def _get_frontend_registry_route_ports(cfg: Optional[Dict[str, Any]] = None) -> set[int]:
    ports: set[int] = set()
    for route in _build_browser_port_routes_from_frontend_registry(cfg=cfg):
        try:
            port = int(route.get("port") or 0)
        except Exception:
            continue
        if port > 0:
            ports.add(port)
    return ports

def _advertised_website_entry_for_port(
    advertised_websites: Optional[List[Dict[str, Any]]],
    port: int,
) -> Optional[Dict[str, Any]]:
    for entry in (advertised_websites or []):
        if not isinstance(entry, dict):
            continue
        if not entry.get("enabled", True):
            continue
        try:
            entry_port = int(entry["port"])
        except (KeyError, TypeError, ValueError):
            continue
        if entry_port == int(port):
            return entry
    return None

def _advertised_website_target_origin(
    advertised_websites: Optional[List[Dict[str, Any]]],
    port: int,
    *,
    websocket: bool = False,
) -> str:
    entry = _advertised_website_entry_for_port(advertised_websites, port)
    if entry:
        target_url = str(entry.get("target_url") or "").strip()
        if target_url:
            explicit_origin = _normalize_browser_target_origin(target_url, websocket=websocket)
            if explicit_origin:
                return explicit_origin
    return _normalize_browser_target_origin(f"http://127.0.0.1:{int(port)}", websocket=websocket)

def _frontend_registry_route_for_port(port: int) -> Optional[Dict[str, Any]]:
    for route in _build_browser_port_routes_from_frontend_registry(cfg=(STATE.config or {})):
        try:
            route_port = int(route.get("port") or 0)
        except Exception:
            continue
        if route_port == int(port):
            return route
    return None

def _build_browser_port_routes(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    routes = list(_build_browser_port_routes_from_frontend_registry(cfg=cfg))
    existing_ports = {
        int(route.get("port") or 0)
        for route in routes
        if route.get("port") not in (None, "")
    }
    reserved_frontend_ports = _get_frontend_registry_route_ports() | _get_frontend_registry_route_ports(cfg=cfg)
    for entry in _get_autoyou_advertised_websites(cfg):
        if not entry.get("enabled", True):
            continue
        port = int(entry["port"])
        if port in existing_ports:
            continue
        if port in reserved_frontend_ports:
            continue
        route_suffix = _route_suffix_from_target_url(entry.get("target_url"), port=port)
        route_path = _route_path_from_target_url(route_suffix)
        routes.append(
            {
                "route_id": f"advertised:{port}",
                "agent_name": None,
                "kind": "advertised",
                "title": str(entry.get("label") or "").strip() or f"Service :{port}",
                "description": str(entry.get("description") or "").strip() or f"Advertised website or service on port {port}.",
                "port": port,
                "local_url": f"http://127.0.0.1:{port}{route_suffix}",
                "path": route_path,
            }
        )
        existing_ports.add(port)
    return routes

def _build_agent_website_routes(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    routes = list(_build_agent_website_routes_from_frontend_registry(cfg=cfg))
    seen_route_ids = {
        str(route.get("route_id") or "").strip()
        for route in routes
        if str(route.get("route_id") or "").strip()
    }
    for route in _build_browser_port_routes(cfg):
        route_id = str(route.get("route_id") or "").strip()
        if not route_id or route_id in seen_route_ids:
            continue
        enriched = dict(route)
        if not enriched.get("route_mode"):
            enriched["route_mode"] = "advertised_direct" if enriched.get("kind") == "advertised" else "direct_forward"
        if enriched.get("kind") == "advertised":
            enriched.setdefault("open_url", enriched.get("local_url"))
        routes.append(enriched)
        seen_route_ids.add(route_id)
    return routes

_PUBLIC_HOSTING_BLOCKED_AGENT_WEBSITES = {"admin_agent"}


def _tunnelmole_website_hosting_config(
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    tunnelmole_cfg = effective_cfg.get("tunnelmole", {}) if isinstance(effective_cfg, dict) else {}
    raw = tunnelmole_cfg.get("website_hosting", {})
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": _coerce_enabled_flag(raw.get("enabled", False)),
        "agent_name": package_agent_name(raw.get("agent_name")),
    }


def _hostable_agent_website_routes(
    cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    routes: List[Dict[str, Any]] = []
    for route in _build_agent_website_routes(cfg):
        if not isinstance(route, dict):
            continue
        agent_name = package_agent_name(route.get("agent_name"))
        if not agent_name or agent_name in _PUBLIC_HOSTING_BLOCKED_AGENT_WEBSITES:
            continue
        if str(route.get("kind") or "") != "agent_frontend":
            continue
        normalized = dict(route)
        normalized["agent_name"] = agent_name
        normalized["public_path"] = f"/agent/{agent_name}/"
        routes.append(normalized)
    return routes


def _selected_hosted_agent_website_route(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    routes: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    hosting_cfg = _tunnelmole_website_hosting_config(cfg)
    selected_name = str(hosting_cfg.get("agent_name") or "").strip()
    for route in routes if routes is not None else _hostable_agent_website_routes(cfg):
        if str(route.get("agent_name") or "") == selected_name:
            return route
    return None


def _get_tunnelmole_hosted_website_port(cfg: Optional[Dict[str, Any]] = None) -> int:
    hosting_cfg = _tunnelmole_website_hosting_config(cfg)
    if not hosting_cfg.get("enabled"):
        return 0
    if _selected_hosted_agent_website_route(cfg) is None:
        return 0
    return _get_autoyou_page_service_port(cfg)


def _apply_tunnelmole_hosted_website_to_service(cfg: Optional[Dict[str, Any]] = None) -> int:
    website_port = _get_tunnelmole_hosted_website_port(cfg)
    service = STATE.tunnelmole_service
    if isinstance(service, TunnelmoleService):
        service.set_website_port(website_port)
    return website_port


def _build_tunnelmole_website_hosting_payload(
    cfg: Optional[Dict[str, Any]] = None,
    *,
    status_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    effective_cfg = cfg if isinstance(cfg, dict) else (STATE.config or _default_config())
    hosting_cfg = _tunnelmole_website_hosting_config(effective_cfg)
    routes = _hostable_agent_website_routes(effective_cfg)
    selected_route = _selected_hosted_agent_website_route(effective_cfg, routes=routes)
    cloud_cfg = effective_cfg.get("cloud", {}) if isinstance(effective_cfg, dict) else {}
    tunnel_status = status_info if isinstance(status_info, dict) else get_tunnelmole_status()
    public_url = str(tunnel_status.get("public_url") or "").rstrip("/")
    return {
        "success": True,
        "enabled": bool(hosting_cfg.get("enabled")),
        "agent_name": str(hosting_cfg.get("agent_name") or ""),
        "selected": selected_route,
        "options": routes,
        "page_service_port": _get_autoyou_page_service_port(effective_cfg),
        "active": bool(hosting_cfg.get("enabled") and selected_route),
        "public_url": public_url or None,
        "public_website_url": public_url or None,
        "status": tunnel_status.get("status") or "stopped",
        "tier": tunnel_status.get("tier"),
        "auto_start_on_boot": bool((effective_cfg.get("tunnelmole", {}) or {}).get("auto_start_on_boot", False)),
        "cloud_signed_in": bool(
            str(cloud_cfg.get("server_token") or cloud_cfg.get("user_id") or cloud_cfg.get("email") or "").strip()
        ),
        "plan_label": "Public Proxy",
    }


async def _has_tunnelmole_paid_entitlement() -> bool:
    client = _ensure_cloud_entitlements()
    if client is None or not client.has_credentials():
        return False
    try:
        return "tm" in await client.get_entitlements()
    except Exception as exc:
        LOGGER.warning("cloud_entitlements: paid public website check failed: %s", exc)
        return False


async def _set_tunnelmole_website_hosting_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")
    cfg = _loaded_config_for_update()
    routes = _hostable_agent_website_routes(cfg)
    route_by_name = {str(route.get("agent_name") or ""): route for route in routes}
    enabled = _coerce_enabled_flag(payload.get("enabled", True))
    start_requested = _coerce_enabled_flag(payload.get("start", False))
    auto_start_requested = (
        _coerce_enabled_flag(payload.get("auto_start_on_boot"))
        if "auto_start_on_boot" in payload
        else False
    )
    selected_name = package_agent_name(
        payload.get("agent_name")
        or (cfg.get("tunnelmole", {}).get("website_hosting", {}) or {}).get("agent_name")
        or _resolve_default_agent_website(cfg)
    )

    if enabled:
        if not selected_name and routes:
            selected_name = str(routes[0].get("agent_name") or "")
        if selected_name not in route_by_name:
            raise ValueError("Choose an installed website before turning on the public website.")

    if (enabled or start_requested or auto_start_requested) and not await _has_tunnelmole_paid_entitlement():
        raise PermissionError("Sign in with the Public Proxy plan before turning on a public website.")

    tunnelmole_cfg = cfg.setdefault("tunnelmole", {})
    tunnelmole_cfg["website_hosting"] = {
        "enabled": bool(enabled),
        "agent_name": selected_name if enabled else selected_name,
    }
    if "auto_start_on_boot" in payload:
        tunnelmole_cfg["auto_start_on_boot"] = _coerce_enabled_flag(payload.get("auto_start_on_boot"))
    if enabled:
        tunnelmole_cfg["enabled"] = True
        cfg.setdefault("autoyou_page", {})["auto_start"] = True
        cfg.setdefault("autoyou_page", {})["default_agent_website"] = selected_name

    _persist_state_config(cfg)
    STATE.config = cfg
    try:
        _sync_frontend_registry_from_builder_payload(_build_agent_builder_listing_payload())
    except Exception as exc:
        LOGGER.warning("Failed to refresh frontend registry after public website change: %s", exc)

    if enabled:
        with suppress(Exception):
            await sync_managed_frontend_backends()
        with suppress(Exception):
            if AUTOYOU_PAGE_SERVICE_AVAILABLE and not await is_autoyou_page_service_running():
                await start_autoyou_page_service_background()

    _apply_tunnelmole_hosted_website_to_service(cfg)
    if start_requested and enabled:
        started = await start_tunnelmole_service_for_current_mode(force=True)
        if not started:
            result = _build_tunnelmole_website_hosting_payload(cfg)
            result["success"] = False
            result["error"] = "Public link could not be started. Check the Public Proxy plan and server password."
            return result
    return _build_tunnelmole_website_hosting_payload(cfg)

def _build_browser_shortcuts(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    shortcuts: List[Dict[str, Any]] = []
    for entry in _get_autoyou_advertised_websites(cfg):
        if not entry.get("enabled", True):
            continue
        port = int(entry["port"])
        route_suffix = _route_suffix_from_target_url(entry.get("target_url"), port=port)
        shortcuts.append(
            {
                "id": f"advertised:{port}",
                "title": str(entry.get("label") or "").strip() or f"Service :{port}",
                "description": str(entry.get("description") or "").strip() or f"Advertised website or service on port {port}.",
                "url": f"http://127.0.0.1:{port}{route_suffix}",
            }
        )
    for entry in _get_autoyou_bookmarks(cfg):
        if not entry.get("enabled", True):
            continue
        bookmark_id = str(entry.get("id") or "").strip()
        url = str(entry.get("url") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not bookmark_id or not url or not title:
            continue
        shortcuts.append(
            {
                "id": f"bookmark:{bookmark_id}",
                "title": title,
                "description": str(entry.get("description") or "").strip() or "Bookmark shared by AutoYou Server.",
                "url": url,
                "kind": "bookmark",
            }
        )
    return shortcuts

def _normalize_client_loopback_target(
    url: str,
    *,
    proxy_target: str,
    websocket: bool = False,
    advertised_websites: Optional[List[Dict[str, Any]]] = None,
) -> str:
    from urllib.parse import urlsplit, urlunsplit

    normalized = str(url or "").strip()
    if not normalized:
        return normalized
    try:
        parsed = urlsplit(normalized)
    except Exception:
        return normalized

    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").strip().lower()
    if scheme not in {"http", "https", "ws", "wss"}:
        return normalized

    # Loopback detection lives in shared.proxy_target_policy so this server and
    # AutoYou Lite cannot drift apart on what counts as loopback.
    if not is_loopback_hostname(hostname):
        return normalized

    # Enforce strict port allowlist for all loopback requests
    req_port = parsed.port
    if req_port is None:
        req_port = 443 if scheme in ("https", "wss") else 80
    else:
        req_port = int(req_port)

    allowed_ports = set()

    # 1. Default loopback target port (from proxy_target)
    try:
        p_parts = urlsplit(proxy_target)
        p_port = p_parts.port
        if p_port is None:
            p_port = 443 if (p_parts.scheme or "").lower() in ("https", "wss") else 80
        allowed_ports.add(int(p_port))
    except Exception:
        pass

    # 2. Built-in Page service port
    try:
        ps_port = _get_autoyou_page_service_port(STATE.config or {})
        if ps_port:
            allowed_ports.add(int(ps_port))
    except Exception:
        pass

    # 3. AI REST API port used by /api/chat and companion endpoints
    try:
        ai_port = _get_ai_agent_api_port()
        if ai_port:
            allowed_ports.add(int(ai_port))
    except Exception:
        pass

    # 4. Dynamic agent ports explicitly enabled via the frontend registry
    try:
        for route in _build_browser_port_routes_from_frontend_registry(cfg=(STATE.config or {})):
            r_port = route.get("port")
            if r_port:
                allowed_ports.add(int(r_port))
        # Support dynamic agent proxy ports directly as fallback/dev routes
        proxy_ports = getattr(STATE, "dynamic_agent_proxy_ports", {}) or {}
        for agent_name, p_port in proxy_ports.items():
            if p_port:
                allowed_ports.add(int(p_port))
    except Exception:
        pass

    # 5. Configured advertised websites
    try:
        websites_list = advertised_websites if advertised_websites is not None else _get_autoyou_advertised_websites(STATE.config or {})
        for entry in (websites_list or []):
            if not entry.get("enabled", True):
                continue
            a_port = entry.get("port")
            if a_port:
                allowed_ports.add(int(a_port))
    except Exception:
        pass

    # Delegated to the shared policy. The opt-out is a dedicated variable
    # (AUTOYOU_ALLOW_ANY_LOOPBACK_PORT) that logs loudly when used: this gate
    # used to be disabled as a side effect of AUTOYOU_TEST_ROOT, a
    # data-directory flag, so any harness that set it for file isolation
    # silently opened every loopback port to remote peers.
    enforce_loopback_port_policy(
        normalized,
        allowed_ports,
        context="client loopback browser target",
    )

    request_path = parsed.path or "/"
    if websocket and int(req_port) == _get_ai_agent_api_port():
        LOGGER.info("Refusing WS upgrade to AI REST port %s: no WebSocket route is registered.", req_port)
        return ""
    if websocket and int(req_port) == _get_autoyou_page_service_port(STATE.config or {}):
        if not _browser_path_uses_page_service(request_path, STATE.config or {}):
            LOGGER.info("Refusing WS upgrade to page-service path %s: no WebSocket route is registered.", request_path)
            return ""
    if request_path in {"/api/v1/status", "/api/v1/server-config"}:
        return normalized

    target_origin = ""
    if _browser_path_uses_page_service(request_path, STATE.config or {}):
        if websocket:
            agent_name = _agent_name_from_browser_proxy_path(request_path)
            if not agent_name or not _get_agent_frontend_websocket_enabled(
                agent_name,
                cfg=STATE.config or {},
            ):
                LOGGER.info("Refusing WS upgrade to page-service path %s: route is HTTP-only.", request_path)
                return ""
        target_origin = _normalize_browser_target_origin(
            _get_autoyou_page_service_base_url(STATE.config or {}),
            websocket=websocket,
        )
    if parsed.port is not None:
        main_port = getattr(STATE, "main_server_port", 8081)
        if not target_origin and int(parsed.port) == main_port:
            target_origin = f"ws://127.0.0.1:{main_port}" if websocket else f"http://127.0.0.1:{main_port}"

        if not target_origin:
            advertised_entry = _advertised_website_entry_for_port(advertised_websites, int(parsed.port))
            advertised_target_url = ""
            if advertised_entry is not None:
                advertised_target_url = str(advertised_entry.get("target_url") or "").strip()

            if advertised_entry is not None and advertised_target_url:
                if websocket and not bool(advertised_entry.get("websocket_enabled", False)):
                    LOGGER.info(
                        "Refusing WS upgrade to advertised website port %s: websocket_enabled is false on the entry.",
                        parsed.port,
                    )
                    return ""
                target_origin = _advertised_website_target_origin(
                    advertised_websites,
                    int(parsed.port),
                    websocket=websocket,
                )

        if not target_origin:
            frontend_route = _frontend_registry_route_for_port(int(parsed.port))
            if frontend_route is not None:
                # H-16: WebSocket upgrades must be explicitly allowlisted per
                # frontend. If the route is HTTP-only, refuse to normalize
                # a WS target - that prevents a compromised agent frontend
                # from opening a long-lived WS tunnel into an arbitrary
                # localhost service that only advertised HTTP.
                if websocket and not bool(frontend_route.get("websocket_enabled", False)):
                    LOGGER.info(
                        "Refusing WS upgrade to frontend port %s: websocket_enabled not set on route %r.",
                        parsed.port,
                        frontend_route.get("route_id"),
                    )
                    return ""
                target_origin = _normalize_browser_target_origin(
                    frontend_route.get("local_url") or f"http://127.0.0.1:{int(parsed.port)}",
                    websocket=websocket,
                )
            elif websocket:
                dynamic_proxy_ports = getattr(STATE, "dynamic_agent_proxy_ports", {}) or {}
                dynamic_ports: set[int] = set()
                for port in dynamic_proxy_ports.values():
                    try:
                        if port not in (None, ""):
                            dynamic_ports.add(int(port))
                    except (TypeError, ValueError):
                        continue
                if int(parsed.port) in dynamic_ports:
                    LOGGER.info(
                        "Refusing WS upgrade to unregistered frontend port %s.",
                        parsed.port,
                    )
                    return ""

        if not target_origin:
            advertised_entry = _advertised_website_entry_for_port(advertised_websites, int(parsed.port))
            if advertised_entry is not None:
                # H-16: same gate for operator-configured advertised websites.
                if websocket and not bool(advertised_entry.get("websocket_enabled", False)):
                    LOGGER.info(
                        "Refusing WS upgrade to advertised website port %s: websocket_enabled is false on the entry.",
                        parsed.port,
                    )
                    return ""
                target_origin = _advertised_website_target_origin(
                    advertised_websites,
                    int(parsed.port),
                    websocket=websocket,
                )

    if not target_origin:
        target_origin = _normalize_browser_target_origin(proxy_target, websocket=websocket)
    if not target_origin:
        return normalized

    try:
        origin_parts = urlsplit(target_origin)
        rebuilt = urlunsplit(
            (
                origin_parts.scheme,
                origin_parts.netloc,
                request_path,
                parsed.query,
                parsed.fragment,
            )
        )
    except Exception:
        return normalized

    if rebuilt != normalized:
        LOGGER.info("Normalized client loopback browser target %s -> %s", normalized, rebuilt)
    return rebuilt

_AGENT_WEBSITES_DIRECTORY_SENTINELS = {"agent_websites", "directory", "websites"}

def _resolve_default_agent_website(cfg: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the configured default/home agent website (operator-settable).

    Precedence: ``autoyou_page.default_agent_website`` then top-level
    ``default_agent_website``; defaults to ``page_agent``. The sentinel
    ``agent_websites`` means "land on the Agent Websites directory page".
    """
    config = cfg if isinstance(cfg, dict) else (STATE.config or {})
    page_cfg = config.get("autoyou_page") if isinstance(config, dict) else None
    page_cfg = page_cfg if isinstance(page_cfg, dict) else {}
    value = str(page_cfg.get("default_agent_website") or config.get("default_agent_website") or "").strip()
    if value in _AGENT_WEBSITES_DIRECTORY_SENTINELS:
        return "agent_websites"
    return value or "page_agent"

def _get_remote_browser_access_role(cfg: Optional[Dict[str, Any]] = None) -> str:
    effective_cfg = cfg if isinstance(cfg, dict) else (STATE.config or {})
    page_cfg = effective_cfg.get("autoyou_page") if isinstance(effective_cfg, dict) else {}
    page_cfg = page_cfg if isinstance(page_cfg, dict) else {}
    return normalize_remote_access_role(page_cfg.get("remote_access_role"))

def _build_default_website_payload(
    cfg: Optional[Dict[str, Any]] = None,
    routes: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Describe the current default/home website for status + admin surfaces.

    Callers that already built the agent-website routes pass them in via
    ``routes`` to avoid rebuilding the list (the route shape, including
    PATH_PROXY / DIRECT_FORWARD / ADVERTISED modes, is unchanged either way).
    """
    config = cfg if isinstance(cfg, dict) else (STATE.config or {})
    default_agent = _resolve_default_agent_website(config)
    if default_agent == "agent_websites":
        return {"agent_name": "agent_websites", "kind": "directory", "title": "Agent Websites", "open_url": "/agent-websites"}
    resolved_routes = routes if routes is not None else _build_agent_website_routes(config)
    for route in resolved_routes:
        if str(route.get("agent_name") or "") == default_agent:
            return {
                "agent_name": default_agent,
                "kind": "agent_frontend",
                "title": route.get("title") or default_agent,
                "open_url": route.get("open_url") or route.get("proxy_path") or f"/agent/{default_agent}/",
            }
    return {"agent_name": default_agent, "kind": "agent_frontend", "title": default_agent, "open_url": f"/agent/{default_agent}/"}

def _build_browser_status_payload() -> Dict[str, Any]:
    cfg = STATE.config or {}
    advertised_websites = _get_autoyou_advertised_websites(cfg)
    agent_website_routes = _build_agent_website_routes(cfg)
    remote_access_role = _get_remote_browser_access_role(cfg)
    return {
        "status": "running",
        "browser_provider": "autoyou",
        "proxy_target": f"http://127.0.0.1:{_get_autoyou_browser_forward_port_from_state()}",
        "page_service_port": _get_autoyou_page_service_port(cfg),
        "page_service_url": _get_autoyou_page_service_base_url(cfg),
        "remote_access_role": remote_access_role,
        "home_network": _home_network_web_status(cfg),
        "advertised_websites": advertised_websites,
        "bookmarks": _get_autoyou_bookmarks(cfg),
        "browser_port_routes": _build_browser_port_routes(cfg),
        "agent_website_routes": agent_website_routes,
        "default_website": _build_default_website_payload(cfg, routes=agent_website_routes),
        "browser_shortcuts": _build_browser_shortcuts(cfg),
    }

def _build_browser_server_config_payload() -> Dict[str, Any]:
    cfg = STATE.config or {}
    advertised_websites = _get_autoyou_advertised_websites(cfg)
    ai_provider = str(cfg.get("ai_provider", {}).get("provider") or "ollama").strip().lower() or "ollama"
    agent_website_routes = _build_agent_website_routes(cfg)
    return {
        "ai_provider": ai_provider,
        "primary_url": f"http://127.0.0.1:{_get_autoyou_browser_forward_port(cfg)}",
        "page_service_url": _get_autoyou_page_service_base_url(cfg),
        "server_name": str(cfg.get("server", {}).get("name") or "AutoYou-Server"),
        "port_routes": _build_browser_port_routes(cfg),
        "agent_website_routes": agent_website_routes,
        "default_website": _build_default_website_payload(cfg, routes=agent_website_routes),
        "advertised_websites": advertised_websites,
        "bookmarks": _get_autoyou_bookmarks(cfg),
        "admin_port": int(ADMIN_WEB_SERVICE_PORT),
        "remote_access_role": _get_remote_browser_access_role(cfg),
        "home_network": _home_network_web_status(cfg),
    }

def _build_bookmarks_admin_payload(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    effective_cfg = cfg if cfg is not None else (STATE.config or {})
    return {
        "success": True,
        "bookmarks": _get_autoyou_bookmarks(effective_cfg),
        "browser_shortcuts": _build_browser_shortcuts(effective_cfg),
    }

def _persist_autoyou_bookmarks(bookmarks: List[Dict[str, Any]]) -> Dict[str, Any]:
    cfg = _loaded_config_for_update()
    _apply_default_autoyou_page_config(cfg)
    cfg.setdefault("autoyou_page", {})["bookmarks"] = _normalize_bookmark_entries(bookmarks)
    _persist_state_config(cfg)
    STATE.config = cfg
    return _build_bookmarks_admin_payload(cfg)

def _admin_guide_registry() -> List[Dict[str, Any]]:
    """Single source for in-app documentation: native docs plus setup guides.

    Each entry's ``body`` callable returns the page body HTML, shared by the
    standalone ``/guides/*`` pages and the in-shell reader content endpoint.
    Native docs come from :func:`admin_doc_guides` (pure-Python, ships in every
    packaged binary); the rest reuse the existing builders and markdown guides.
    """

    def _markdown_body(path: Path):
        return lambda: f"<section class='guide-shell'>{simple_markdown_to_html(load_markdown_guide(path))}</section>"

    entries: List[Dict[str, Any]] = []
    for doc in admin_doc_guides():
        entries.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "subtitle": doc["subtitle"],
                "category": doc["category"],
                "href": f"/guides/doc/{doc['id']}",
                "body": doc["builder"],
            }
        )
    entries.extend(
        [
            {
                "id": "connectivity",
                "title": "Public Reverse Proxy & Cloud Access",
                "subtitle": "Public reverse proxy, Cloud Pair, LAN, and connection helpers.",
                "category": "Connectivity",
                "href": "/guides/connectivity",
                "body": build_connectivity_guide_html,
            },
            {
                "id": "bootstrap",
                "title": "Bootstrap Guide",
                "subtitle": "First-run setup, staging workflow, and the main desktop bootstrap path.",
                "category": "Setup guides",
                "href": "/guides/bootstrap",
                "body": _markdown_body(INSTALLATION_GUIDE_PATH),
            },
            {
                "id": "community-relay-submission",
                "title": "Community Relay submission",
                "subtitle": "Ready-to-submit public relay checklist, Docker services, ports, and approval states.",
                "category": "Connectivity",
                "href": "/guides/doc/community-relay-submission",
                "body": _markdown_body(COMMUNITY_RELAY_GUIDE_PATH),
            },
            {
                "id": "home-private-network",
                "title": "Home and private network",
                "subtitle": "Docker, trusted LAN binding, local HTTPS, private STUN/TURN, and app selection.",
                "category": "Connectivity",
                "href": "/guides/doc/home-private-network",
                "body": _markdown_body(HOME_PRIVATE_NETWORK_GUIDE_PATH),
            },
            {
                "id": "mobile-pairing",
                "title": "iOS and Android mobile pairing",
                "subtitle": "Local, automatic, Telegram, Signal, Bluetooth, and OTP pairing modes.",
                "category": "Mobile",
                "href": "/guides/doc/mobile-pairing",
                "body": _markdown_body(MOBILE_PAIRING_GUIDE_PATH),
            },
            {
                "id": "chrome-pairing",
                "title": "Chrome pairing with another computer",
                "subtitle": "AutoYou Connect for Chrome, Local Pair, Auto Pair, OTP, Cloud, and browser troubleshooting.",
                "category": "Browser",
                "href": "/guides/doc/chrome-pairing",
                "body": _markdown_body(CHROME_PAIRING_GUIDE_PATH),
            },
            {
                "id": "ollama-install",
                "title": "Ollama Install",
                "subtitle": "Local model setup for the default privacy-first runtime.",
                "category": "Setup guides",
                "href": "/guides/ollama-install",
                "body": build_ollama_install_guide_html,
            },
            {
                "id": "speech",
                "title": "Speech Guide",
                "subtitle": "System voices, cloud TTS providers, and STT model downloads.",
                "category": "Setup guides",
                "href": "/guides/speech",
                "body": build_speech_guide_html,
            },
            {
                "id": "telegram",
                "title": "Telegram Setup",
                "subtitle": "Pair the Telegram bot and keep access limited to approved users.",
                "category": "Messaging",
                "href": "/guides/telegram",
                "body": build_telegram_guide_html,
            },
            {
                "id": "telegram-user",
                "title": "Telegram User",
                "subtitle": "Use your own Saved Messages as a private AutoYou message space.",
                "category": "Messaging",
                "href": "/guides/doc/telegram-user",
                "body": _markdown_body(TELEGRAM_USER_GUIDE_PATH),
            },
            {
                "id": "signal",
                "title": "Signal Pairing",
                "subtitle": "Signal QR pairing and recovery steps for the local Docker-backed service.",
                "category": "Messaging",
                "href": "/guides/signal",
                "body": _markdown_body(SIGNAL_PAIRING_GUIDE_PATH),
            },
            {
                "id": "whatsapp",
                "title": "WhatsApp Pairing",
                "subtitle": "WhatsApp QR setup through the repository-native runtime.",
                "category": "Messaging",
                "href": "/guides/whatsapp",
                "body": _markdown_body(WHATSAPP_PAIRING_GUIDE_PATH),
            },
        ]
    )
    return entries

def _admin_guide_links() -> List[Dict[str, str]]:
    return [
        {
            "id": entry["id"],
            "title": entry["title"],
            "href": entry["href"],
            "description": entry["subtitle"],
            "category": entry["category"],
        }
        for entry in _admin_guide_registry()
    ]

def _resolve_guide_doc(doc_id: str) -> Optional[Dict[str, Any]]:
    for entry in _admin_guide_registry():
        if entry["id"] == doc_id:
            return {
                "id": entry["id"],
                "title": entry["title"],
                "subtitle": entry["subtitle"],
                "category": entry["category"],
                "html": entry["body"](),
            }
    return None

def _empty_agent_builder_listing_payload(error: str) -> Dict[str, Any]:
    """Return a structurally-complete (but empty) agents listing payload.

    The new admin shell reads ``agent_overview``/``agent_details`` from this
    payload. When the heavy workbench scan fails (most often in a fresh
    compiled install where filesystem layout differs), returning the bare
    ``{"status": "error"}`` dict leaves the Agents screen silently blank.
    Returning a fully-keyed shell instead lets the UI render and show the
    real error rather than nothing.
    """
    return {
        "status": "error",
        "error": error,
        "agents": [],
        "live_agents": [],
        "draft_agents": [],
        "proxy_ports": {},
        "frontends": [],
        "installed_agents": [],
        "available_agents": [],
        "workspace_only_agents": [],
        "overview_counts": {"total": 0, "installed": 0, "available": 0, "drafts": 0},
        "workbench_agents": {},
        "agent_overview": [],
        "agent_details": {},
    }

def _safe_agent_builder_listing_payload() -> Dict[str, Any]:
    """Build the agents listing without ever raising.

    Runs the (synchronous, filesystem-heavy) workbench scan and guarantees a
    UI-renderable payload. Any exception - or a context that reports a
    non-success status without the overview keys - is downgraded to a fully
    keyed empty payload so a compiled-mode path issue degrades gracefully
    instead of blanking the Agents screen or failing the whole bootstrap.
    """
    try:
        payload = _build_agent_builder_listing_payload()
    except Exception as exc:  # noqa: BLE001 - bootstrap must never hard-fail here
        LOGGER.error("Agent builder listing failed; serving empty listing: %s", exc, exc_info=True)
        return _empty_agent_builder_listing_payload(str(exc))
    if not isinstance(payload, dict) or "agent_overview" not in payload:
        message = ""
        if isinstance(payload, dict):
            message = str(payload.get("error") or payload.get("message") or "")
        LOGGER.warning("Agent builder listing returned no overview; serving empty listing: %s", message)
        return _empty_agent_builder_listing_payload(message or "Agent listing unavailable in this runtime.")
    return payload

def _redact_admin_ai_provider_secrets(cfg: Dict[str, Any]) -> None:
    """Blank provider secrets in the browser-only copy of the config."""
    ollama_cfg = cfg.setdefault("ollama", {})
    ollama_cfg["google_api_key"] = ""
    provider_cfg = cfg.setdefault("ai_provider", {})
    for key in (
        "openclaw_token",
        "openclaw_agent_token",
        "hermes_token",
        "litellm_api_key",
        "odysseus_token",
    ):
        provider_cfg[key] = ""


async def _build_admin_ui_bootstrap_payload() -> Dict[str, Any]:
    from shared.apple_intelligence import status as apple_intelligence_status
    cfg = copy.deepcopy(STATE.config or _default_config())
    _apply_default_server_identity_config(cfg)
    _apply_default_security_config(cfg)
    _apply_default_tunnelmole_config(cfg)
    _apply_default_client_identity_config(cfg)
    _apply_default_agent_frontends_config(cfg)
    _apply_default_bluetooth_pairing_config(cfg)
    _apply_default_ice_servers(cfg)
    _apply_default_speech_config(cfg)
    _apply_default_onboarding_config(cfg)
    _apply_default_cloud_config(cfg)
    _apply_default_autoyou_page_config(cfg)
    _apply_default_messaging_partner_config(cfg)
    _apply_default_video_call_config(cfg)
    if STATE.signal_service is not None:
        cfg.setdefault("signal", {})["enabled"] = True
    if STATE.whatsapp_service is not None:
        cfg.setdefault("whatsapp", {})["enabled"] = True
    if STATE.telegram_user_service is not None:
        cfg.setdefault("telegram_user", {})["enabled"] = True

    # User-session credentials stay in the encrypted config store.  The admin
    # browser only receives a blank placeholder so normal settings saves cannot
    # disclose or overwrite the active session.
    telegram_user_cfg = cfg.setdefault("telegram_user", {})
    telegram_user_cfg["api_hash"] = ""
    telegram_user_cfg["session"] = ""
    telegram_user_cfg.pop("training_export_consent_at", None)
    # MCP API tokens are only used server-side by the separate adapter.  The
    # browser receives an explicit configured/not-configured status and a
    # blank editable field, never the bearer secret itself.
    mcp_cfg = cfg.setdefault("mcp", {})
    mcp_cfg["api_token"] = ""
    _redact_admin_ai_provider_secrets(cfg)

    # Resolve the independent status probes concurrently and push the heavy,
    # blocking agent-listing scan onto a worker thread so post-login bootstrap
    # latency is dominated by the slowest single probe rather than their sum.
    (
        (telegram_status, telegram_name),
        (telegram_user_status, telegram_user_name),
        (signal_status, signal_name),
        (whatsapp_status, whatsapp_name),
        ai_agent_status,
        autoyou_page_status,
        cloud_status,
        agents_payload,
    ) = await asyncio.gather(
        _telegram_status(),
        _telegram_user_status(),
        _signal_status(),
        _whatsapp_status(),
        _ai_agent_server_status(),
        _autoyou_page_service_status(),
        _build_cloud_status_snapshot(cfg.get("cloud")),
        asyncio.to_thread(_safe_agent_builder_listing_payload),
    )

    try:
        _sync_frontend_registry_from_builder_payload(agents_payload)
    except Exception as exc:
        LOGGER.warning("Failed to refresh browser frontend registry during admin bootstrap: %s", exc)

    return {
        "success": True,
        "authenticated": True,
        "admin": {
            "title": _get_admin_ui_title(),
            "logo_url": "/assets/logo.png",
            "server_name": str(cfg.get("server", {}).get("name") or get_configured_server_name()),
            "server_id": _get_stable_server_id(cfg),
            "server_identity_key": _get_server_identity_key(cfg),
            "avatar_user_id": _get_stable_server_id(cfg),
            "avatar_url": _get_admin_profile_image_url(),
            "admin_port": int(ADMIN_WEB_SERVICE_PORT),
            "auth_port": int(AUTH_SERVER_PORT),
            "ai_agent_port": int(AI_AGENT_SERVER_PORT),
            "browser_forward_port": int(_get_autoyou_browser_forward_port(cfg)),
            "theme": _read_autoyou_ui_theme(),
        },
        "config": cfg,
        "status": {
            "telegram": {"status": telegram_status, "name": telegram_name},
            "telegram_user": {"status": telegram_user_status, "name": telegram_user_name},
            "signal": {"status": signal_status, "name": signal_name},
            "whatsapp": {"status": whatsapp_status, "name": whatsapp_name},
            "ai_agent": {"status": ai_agent_status},
            "apple_intelligence": await apple_intelligence_status(),
            "autoyou_page": {"status": autoyou_page_status},
            "tunnelmole": get_tunnelmole_status(),
            "bluetooth_pairing": _bluetooth_pairing_runtime_status(),
            "cloud": cloud_status,
            "mcp": _mcp_runtime_status(STATE.config or cfg),
            "browser": _build_browser_status_payload(),
            "totp": _describe_totp_capabilities(cfg),
            "secure_storage": secure_storage_status(),
            "secure_storage_recovery": _secure_storage_recovery_status(),
            "instance": build_instance_runtime_status(),
            "home_network": _home_network_web_status(cfg),
            "runtime": build_runtime_environment_status(),
            "software_update": _software_update_local_status(cfg),
        },
        "agents": agents_payload,
        "metadata": {
            "guides": _admin_guide_links(),
            "recording_paths": _build_recording_paths_payload(cfg),
            "security_modes": [
                {"id": "normal", "label": "Normal Mode (No Encryption)"},
                {"id": "secure", "label": "Secure Mode (Password Only)"},
                {"id": "secure_professional", "label": "Secure Professional (Password + 2FA)"},
                {
                    "id": SECURE_PROFESSIONAL_MAXIMUS_MODE,
                    "label": "Secure Professional Maximus",
                    "description": (
                        "Adds encryption to saved sessions, agent data, websites, notes, and settings on this computer."
                    ),
                },
            ],
            "tunnelmole": {
                "pair_code_mode": _normalize_tunnelmole_pair_code_mode(
                    cfg.get("tunnelmole", {}).get("pair_code_mode")
                ),
                "connection_mode": _normalize_tunnelmole_connection_mode(
                    cfg.get("tunnelmole", {}).get("connection_mode")
                ),
                "pair_code_modes": [
                    {
                        "id": _TUNNELMOLE_PAIR_CODE_MODE_RANDOM_OTP,
                        "label": "Random pairing code",
                    },
                    {
                        "id": _TUNNELMOLE_PAIR_CODE_MODE_AUTHENTICATOR,
                        "label": "Authenticator code",
                    },
                ],
                "connection_modes": [
                    {"id": _TUNNELMOLE_CONNECTION_MODE_TIMED, "label": "Timed"},
                    {"id": _TUNNELMOLE_CONNECTION_MODE_UNMANAGED, "label": "Unmanaged"},
                ],
            },
            "setup_profiles": build_setup_profile_payload(
                cfg,
                agents_payload,
                api_route_count=_admin_setup_api_route_count(),
            ),
            "agent_website_backend_stacks": backend_stack_choices_payload(),
            "agent_website_stacks": frontend_stack_choices_payload(),
        },
    }

def _coerce_admin_ui_int(
    raw_value: Any,
    field_name: str,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    try:
        value = int(str(raw_value).strip())
    except Exception as exc:
        raise ValueError(f"{field_name} must be an integer.") from exc

    if minimum is not None and value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}.")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field_name} must be at most {maximum}.")
    return value

def _split_admin_ui_list_value(raw_value: Any) -> List[str]:
    if isinstance(raw_value, list):
        items = raw_value
    else:
        items = re.split(r"[\s,]+", str(raw_value or ""))

    normalized: List[str] = []
    seen: Set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized

def _apply_admin_ui_config_patch(
    base_cfg: Dict[str, Any],
    payload: Dict[str, Any],
) -> Tuple[Dict[str, Any], Set[str], Optional[str]]:
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    cfg = copy.deepcopy(base_cfg or _default_config())
    _apply_default_client_identity_config(cfg)
    touched_sections: Set[str] = set()
    resolved_ui_theme: Optional[str] = None

    software_update_payload = payload.get("software_update")
    if isinstance(software_update_payload, dict) and "enabled" in software_update_payload:
        cfg.setdefault("software_update", {})["enabled"] = _normalize_config_bool(
            software_update_payload.get("enabled"),
            True,
        )
        touched_sections.add("software_update")

    server_payload = payload.get("server")
    if isinstance(server_payload, dict):
        server_cfg = cfg.setdefault("server", {})
        if "name" in server_payload:
            server_name = str(server_payload.get("name") or "").strip()
            if not server_name:
                raise ValueError("server.name is required.")
            server_cfg["name"] = server_name
            touched_sections.add("server")
        if "bind_host" in server_payload:
            previous_bind_host = _configured_server_bind_host(cfg)
            server_cfg["bind_host"] = _normalize_server_bind_host(server_payload.get("bind_host"))
            # Opening the server to the home network turns HTTPS on with it, so
            # pages, notes and sign-in are not offered to every device on the
            # network over plain HTTP. An https_enabled in the same request
            # still wins, and the operator can turn HTTPS off afterwards.
            if (
                server_cfg["bind_host"] == "0.0.0.0"
                and previous_bind_host != "0.0.0.0"
                and "https_enabled" not in server_payload
            ):
                server_cfg["https_enabled"] = True
            touched_sections.add("server")
        if "https_enabled" in server_payload:
            server_cfg["https_enabled"] = _normalize_config_bool(server_payload.get("https_enabled"), False)
            touched_sections.add("server")
        if "home_network_websites" in server_payload:
            websites_mode = str(server_payload.get("home_network_websites") or "").strip().lower()
            if websites_mode not in HOME_NETWORK_WEBSITES_MODES:
                raise ValueError("server.home_network_websites must be path_proxy or direct_forward.")
            server_cfg["home_network_websites"] = websites_mode
            touched_sections.add("server")
        if "discovery_enabled" in server_payload:
            server_cfg["discovery_enabled"] = _normalize_config_bool(server_payload.get("discovery_enabled"), True)
            touched_sections.add("server")
        if "https_port" in server_payload:
            try:
                _https_port_value = int(str(server_payload.get("https_port")).strip())
                if 1024 <= _https_port_value <= 65535:
                    server_cfg["https_port"] = _https_port_value
                    touched_sections.add("server")
            except (TypeError, ValueError):
                pass

    ai_agent_payload = payload.get("ai_agent")
    if isinstance(ai_agent_payload, dict):
        ai_agent_cfg = cfg.setdefault("ai_agent", {})
        if "enabled" in ai_agent_payload:
            ai_agent_cfg["enabled"] = _coerce_enabled_flag(ai_agent_payload.get("enabled"))
        if "port" in ai_agent_payload:
            ai_agent_cfg["port"] = _coerce_admin_ui_int(ai_agent_payload.get("port"), "ai_agent.port", minimum=1, maximum=65535)
        if "auto_start" in ai_agent_payload:
            ai_agent_cfg["auto_start"] = _coerce_enabled_flag(ai_agent_payload.get("auto_start"))
        if "record_messages_in_database" in ai_agent_payload:
            ai_agent_cfg["record_messages_in_database"] = _coerce_enabled_flag(
                ai_agent_payload.get("record_messages_in_database")
            )
        if "internet_search_enabled" in ai_agent_payload:
            ai_agent_cfg["internet_search_enabled"] = _coerce_enabled_flag(
                ai_agent_payload.get("internet_search_enabled")
            )
        if "lan_access_enabled" in ai_agent_payload:
            ai_agent_cfg["lan_access_enabled"] = _coerce_enabled_flag(
                ai_agent_payload.get("lan_access_enabled")
            )
        if "memory_backend" in ai_agent_payload:
            memory_backend = str(ai_agent_payload.get("memory_backend") or "").strip().lower()
            if memory_backend not in {"legacy", "cognee"}:
                raise ValueError("ai_agent.memory_backend must be one of: legacy, cognee.")
            ai_agent_cfg["memory_backend"] = memory_backend
        touched_sections.add("ai_agent")

    client_identity_payload = payload.get("client_identity")
    if isinstance(client_identity_payload, dict):
        client_identity_cfg = cfg.setdefault("client_identity", {})
        if "store_client_names_in_history" in client_identity_payload:
            client_identity_cfg["store_client_names_in_history"] = _coerce_enabled_flag(
                client_identity_payload.get("store_client_names_in_history")
            )
        if "name_overrides" in client_identity_payload:
            raw_overrides = client_identity_payload.get("name_overrides")
            if not isinstance(raw_overrides, dict):
                raise ValueError("client_identity.name_overrides must be an object.")
            normalized_overrides: Dict[str, str] = {}
            for raw_owner_key, raw_name in raw_overrides.items():
                if len(normalized_overrides) >= _CLIENT_IDENTITY_MAX_OVERRIDES:
                    raise ValueError(
                        f"client_identity.name_overrides supports at most {_CLIENT_IDENTITY_MAX_OVERRIDES} entries."
                    )
                owner_key = _normalize_client_identity_owner_key(raw_owner_key)
                client_name = _normalize_client_display_name(raw_name)
                if client_name:
                    normalized_overrides[owner_key] = client_name
            client_identity_cfg["name_overrides"] = normalized_overrides
        touched_sections.add("client_identity")

    # When chat history is off (including Incognito local), names remain
    # live-only and never survive in configuration.
    if not _client_name_history_enabled(cfg):
        client_identity_cfg = cfg.setdefault("client_identity", {})
        if client_identity_cfg.get("name_overrides"):
            client_identity_cfg["name_overrides"] = {}
            touched_sections.add("client_identity")

    ai_provider_payload = payload.get("ai_provider")
    if isinstance(ai_provider_payload, dict):
        ai_provider_cfg = cfg.setdefault("ai_provider", {})
        if "provider" in ai_provider_payload:
            provider = str(ai_provider_payload.get("provider") or "").strip().lower()
            if provider not in {"ollama", "ollama_gateway", "odysseus", "openclaw", "hermes", "litellm", "google", "apple_intelligence"}:
                raise ValueError("Choose a supported AI provider.")
            if provider == "apple_intelligence":
                from shared.apple_intelligence import helper_path
                if helper_path() is None:
                    raise ValueError("Apple Intelligence requires the AutoYou Mac app on a supported Mac. Choose another chat mode on this computer.")
            ai_provider_cfg["provider"] = provider
        for key in {"openclaw_token", "openclaw_model", "openclaw_agent_token", "openclaw_agent_model", "hermes_token", "hermes_model", "litellm_model", "litellm_api_key", "litellm_api_base", "odysseus_api_base", "odysseus_model"}:
            if key in ai_provider_payload:
                ai_provider_cfg[key] = str(ai_provider_payload.get(key) or "").strip()
        if "odysseus_token" in ai_provider_payload:
            odysseus_token = str(ai_provider_payload.get("odysseus_token") or "").strip()
            if odysseus_token:
                ai_provider_cfg["odysseus_token"] = odysseus_token
        for key in {"openclaw_port", "openclaw_agent_port", "hermes_port"}:
            if key in ai_provider_payload:
                ai_provider_cfg[key] = _coerce_admin_ui_int(ai_provider_payload.get(key), f"ai_provider.{key}", minimum=1)
        touched_sections.add("ai_provider")

    ollama_payload = payload.get("ollama")
    if isinstance(ollama_payload, dict):
        ollama_cfg = cfg.setdefault("ollama", {})
        for key in {"api_base", "model", "google_model", "google_api_key"}:
            if key in ollama_payload:
                ollama_cfg[key] = str(ollama_payload.get(key) or "").strip()
        if "enabled" in ollama_payload:
            ollama_cfg["enabled"] = _coerce_enabled_flag(ollama_payload.get("enabled"))
        if "use_google_api" in ollama_payload:
            ollama_cfg["use_google_api"] = _coerce_enabled_flag(ollama_payload.get("use_google_api"))
        touched_sections.add("ollama")

    autoyou_page_payload = payload.get("autoyou_page")
    if isinstance(autoyou_page_payload, dict):
        autoyou_page_cfg = cfg.setdefault("autoyou_page", {})
        if "port" in autoyou_page_payload:
            autoyou_page_cfg["port"] = _coerce_admin_ui_int(autoyou_page_payload.get("port"), "autoyou_page.port", minimum=1)
        if "auto_start" in autoyou_page_payload:
            autoyou_page_cfg["auto_start"] = _coerce_enabled_flag(autoyou_page_payload.get("auto_start"))
        if "timeline_days" in autoyou_page_payload:
            autoyou_page_cfg["timeline_days"] = _coerce_admin_ui_int(
                autoyou_page_payload.get("timeline_days"),
                "autoyou_page.timeline_days",
                minimum=0,
                maximum=3650,
            )
        if "feed_window_days" in autoyou_page_payload:
            # Days the Page feed shows by default; 0 shows the entire feed.
            autoyou_page_cfg["feed_window_days"] = _coerce_admin_ui_int(
                autoyou_page_payload.get("feed_window_days"),
                "autoyou_page.feed_window_days",
                minimum=0,
                maximum=3650,
            )
        if "custom_forward_enabled" in autoyou_page_payload:
            autoyou_page_cfg["custom_forward_enabled"] = _coerce_enabled_flag(
                autoyou_page_payload.get("custom_forward_enabled")
            )
        if "custom_forward_port" in autoyou_page_payload:
            autoyou_page_cfg["custom_forward_port"] = _coerce_admin_ui_int(
                autoyou_page_payload.get("custom_forward_port"),
                "autoyou_page.custom_forward_port",
                minimum=1,
            )
        if "advertised_websites" in autoyou_page_payload:
            autoyou_page_cfg["advertised_websites"] = _normalize_advertised_website_entries(
                autoyou_page_payload.get("advertised_websites"),
                reserved_ports=_get_reserved_browser_port_routes(cfg),
            )
        if "bookmarks" in autoyou_page_payload:
            autoyou_page_cfg["bookmarks"] = _normalize_bookmark_entries(
                autoyou_page_payload.get("bookmarks")
            )
        if "theme" in autoyou_page_payload:
            resolved_ui_theme = normalize_ui_theme(autoyou_page_payload.get("theme"))
            autoyou_page_cfg["theme"] = resolved_ui_theme
        if "remote_access_role" in autoyou_page_payload:
            raw_role = autoyou_page_payload.get("remote_access_role")
            role = normalize_remote_access_role(raw_role, default="")
            if role not in REMOTE_ACCESS_ROLES:
                raise ValueError("autoyou_page.remote_access_role must be viewer, editor, or admin.")
            autoyou_page_cfg["remote_access_role"] = role
        touched_sections.add("autoyou_page")

    telegram_payload = payload.get("telegram")
    if isinstance(telegram_payload, dict):
        telegram_cfg = cfg.setdefault("telegram", {})
        if "bot_token" in telegram_payload:
            telegram_cfg["bot_token"] = str(telegram_payload.get("bot_token") or "").strip()
        if "access_gate_enabled" in telegram_payload:
            telegram_cfg["access_gate_enabled"] = _coerce_enabled_flag(telegram_payload.get("access_gate_enabled"))
        if "silent_unapproved_messages" in telegram_payload:
            telegram_cfg["silent_unapproved_messages"] = _coerce_enabled_flag(
                telegram_payload.get("silent_unapproved_messages")
            )
        if "acl_usernames" in telegram_payload:
            telegram_cfg["acl_usernames"] = [
                username
                for username in (
                    _normalize_telegram_username(value)
                    for value in _split_admin_ui_list_value(telegram_payload.get("acl_usernames"))
                )
                if username
            ]
        if "acl_sender_ids" in telegram_payload:
            telegram_cfg["acl_sender_ids"] = [
                sender_id
                for sender_id in (
                    _normalize_telegram_sender_id(value)
                    for value in _split_admin_ui_list_value(telegram_payload.get("acl_sender_ids"))
                )
                if sender_id
            ]
        touched_sections.add("telegram")

    telegram_user_payload = payload.get("telegram_user")
    if isinstance(telegram_user_payload, dict):
        telegram_user_cfg = cfg.setdefault("telegram_user", {})
        if "enabled" in telegram_user_payload:
            telegram_user_cfg["enabled"] = _coerce_enabled_flag(telegram_user_payload.get("enabled"))
        if "api_id" in telegram_user_payload:
            raw_api_id = str(telegram_user_payload.get("api_id") or "").strip()
            if raw_api_id and raw_api_id != "0":
                telegram_user_cfg["api_id"] = _coerce_admin_ui_int(
                    raw_api_id,
                    "telegram_user.api_id",
                    minimum=1,
                )
            elif raw_api_id == "0" and bool(telegram_user_cfg.get("enabled", False)):
                raise ValueError("telegram_user.api_id is required when Telegram User is enabled.")
        # Bootstrap intentionally returns a blank API hash.  Empty values must
        # therefore preserve the encrypted value already on disk.  Sessions
        # are QR-created only and are never accepted from the browser.
        if "api_hash" in telegram_user_payload:
            value = str(telegram_user_payload.get("api_hash") or "").strip()
            if value:
                telegram_user_cfg["api_hash"] = value
        if "training_export_consent" in telegram_user_payload:
            prior_consent = _normalize_partner_enabled_flag(
                telegram_user_cfg.get("training_export_consent"),
                False,
            )
            training_export_consent = _coerce_enabled_flag(
                telegram_user_payload.get("training_export_consent")
            )
            telegram_user_cfg["training_export_consent"] = training_export_consent
            if training_export_consent and not prior_consent:
                telegram_user_cfg["training_export_consent_at"] = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()
            elif not training_export_consent:
                telegram_user_cfg["training_export_consent_at"] = ""
        prompt_builder_payload = telegram_user_payload.get("prompt_builder")
        if isinstance(prompt_builder_payload, dict):
            prompt_builder_cfg = telegram_user_cfg.setdefault("prompt_builder", {})
            if not isinstance(prompt_builder_cfg, dict):
                prompt_builder_cfg = {}
                telegram_user_cfg["prompt_builder"] = prompt_builder_cfg
            if "enabled" in prompt_builder_payload:
                prompt_builder_cfg["enabled"] = _coerce_enabled_flag(
                    prompt_builder_payload.get("enabled")
                )
            if "application_agent" in prompt_builder_payload:
                target = str(prompt_builder_payload.get("application_agent") or "").strip().lower().replace("-", "_")
                if target in {"codex", "codex_desktop"}:
                    target = "codex_desktop_agent"
                elif target in {"claude", "claude_desktop"}:
                    target = "claude_desktop_agent"
                if target not in {"codex_desktop_agent", "claude_desktop_agent"}:
                    raise ValueError("telegram_user.prompt_builder.application_agent is invalid.")
                prompt_builder_cfg["application_agent"] = target
        touched_sections.add("telegram_user")

    signal_payload = payload.get("signal")
    if isinstance(signal_payload, dict):
        signal_cfg = cfg.setdefault("signal", {})
        if "enabled" in signal_payload:
            signal_cfg["enabled"] = _coerce_enabled_flag(signal_payload.get("enabled"))
        if "port" in signal_payload:
            signal_cfg["port"] = _coerce_admin_ui_int(signal_payload.get("port"), "signal.port", minimum=1)
        if "device_name" in signal_payload:
            signal_cfg["device_name"] = str(signal_payload.get("device_name") or "").strip()
        if "shutdown_docker_on_exit" in signal_payload:
            signal_cfg["shutdown_docker_on_exit"] = _coerce_enabled_flag(
                signal_payload.get("shutdown_docker_on_exit")
            )
        touched_sections.add("signal")

    whatsapp_payload = payload.get("whatsapp")
    if isinstance(whatsapp_payload, dict):
        whatsapp_cfg = cfg.setdefault("whatsapp", {})
        if "enabled" in whatsapp_payload:
            whatsapp_cfg["enabled"] = _coerce_enabled_flag(whatsapp_payload.get("enabled"))
        if "port" in whatsapp_payload:
            websocket_port = _coerce_admin_ui_int(whatsapp_payload.get("port"), "whatsapp.port", minimum=1)
            whatsapp_cfg["port"] = websocket_port
            whatsapp_cfg["websocket_port"] = websocket_port
        if "device_name" in whatsapp_payload:
            whatsapp_cfg["device_name"] = str(whatsapp_payload.get("device_name") or "").strip()
        if "shutdown_on_exit" in whatsapp_payload:
            whatsapp_cfg["shutdown_on_exit"] = _coerce_enabled_flag(whatsapp_payload.get("shutdown_on_exit"))
        touched_sections.add("whatsapp")

    mcp_payload = payload.get("mcp")
    if isinstance(mcp_payload, dict):
        mcp_cfg = cfg.setdefault("mcp", {})
        if "enabled" in mcp_payload:
            mcp_cfg["enabled"] = _coerce_enabled_flag(mcp_payload.get("enabled"))
        # The admin bootstrap intentionally returns a blank token.  A blank
        # value therefore preserves an existing token; explicit clearing is a
        # separate, deliberate action so an unrelated save cannot disable the
        # authenticated MCP bridge.
        if "api_token" in mcp_payload:
            token = str(mcp_payload.get("api_token") or "").strip()
            if token:
                if len(token) < 16:
                    raise ValueError("mcp.api_token must be at least 16 characters.")
                mcp_cfg["api_token"] = token
            elif _coerce_enabled_flag(mcp_payload.get("clear_api_token")):
                mcp_cfg["api_token"] = ""
        if "clear_api_token" in mcp_payload and _coerce_enabled_flag(mcp_payload.get("clear_api_token")):
            mcp_cfg["api_token"] = ""
        if "adapter_url" in mcp_payload:
            mcp_cfg["adapter_url"] = _normalize_mcp_adapter_url(mcp_payload.get("adapter_url"))
        touched_sections.add("mcp")

    tunnelmole_payload = payload.get("tunnelmole")
    if isinstance(tunnelmole_payload, dict):
        tunnelmole_cfg = cfg.setdefault("tunnelmole", {})
        if "enabled" in tunnelmole_payload:
            tunnelmole_cfg["enabled"] = _coerce_enabled_flag(tunnelmole_payload.get("enabled"))
        if "timeout_minutes" in tunnelmole_payload:
            tunnelmole_cfg["timeout_minutes"] = _coerce_admin_ui_int(
                tunnelmole_payload.get("timeout_minutes"),
                "tunnelmole.timeout_minutes",
                minimum=_TUNNELMOLE_TIMEOUT_MINUTES_MIN,
                maximum=_TUNNELMOLE_TIMEOUT_MINUTES_MAX,
            )
        if "otp_timeout_minutes" in tunnelmole_payload:
            tunnelmole_cfg["otp_timeout_minutes"] = _coerce_admin_ui_int(
                tunnelmole_payload.get("otp_timeout_minutes"),
                "tunnelmole.otp_timeout_minutes",
                minimum=1,
                maximum=_TUNNELMOLE_TIMEOUT_MINUTES_MAX,
            )
        if "otp_multiuse" in tunnelmole_payload:
            tunnelmole_cfg["otp_multiuse"] = _coerce_enabled_flag(tunnelmole_payload.get("otp_multiuse"))
        if "pair_code_mode" in tunnelmole_payload:
            tunnelmole_cfg["pair_code_mode"] = _normalize_tunnelmole_pair_code_mode(
                tunnelmole_payload.get("pair_code_mode")
            )
        if "connection_mode" in tunnelmole_payload:
            tunnelmole_cfg["connection_mode"] = _normalize_tunnelmole_connection_mode(
                tunnelmole_payload.get("connection_mode")
            )
        if "auto_start_on_boot" in tunnelmole_payload:
            tunnelmole_cfg["auto_start_on_boot"] = _coerce_enabled_flag(
                tunnelmole_payload.get("auto_start_on_boot")
            )
        if "url_only_pair" in tunnelmole_payload:
            tunnelmole_cfg["url_only_pair"] = _coerce_enabled_flag(
                tunnelmole_payload.get("url_only_pair")
            )
        touched_sections.add("tunnelmole")

    bluetooth_pairing_payload = payload.get("bluetooth_pairing")
    if isinstance(bluetooth_pairing_payload, dict):
        bluetooth_pairing_cfg = cfg.setdefault("bluetooth_pairing", {})
        if "enabled" in bluetooth_pairing_payload:
            bluetooth_pairing_cfg["enabled"] = _coerce_enabled_flag(
                bluetooth_pairing_payload.get("enabled")
            )
        touched_sections.add("bluetooth_pairing")

    video_call_payload = payload.get("video_call")
    if isinstance(video_call_payload, dict):
        video_cfg = cfg.setdefault("video_call", {})
        if not isinstance(video_cfg, dict):
            video_cfg = {}
            cfg["video_call"] = video_cfg
        if "enabled" in video_call_payload:
            video_cfg["enabled"] = _coerce_enabled_flag(video_call_payload.get("enabled"))
        if "audio_enabled" in video_call_payload:
            video_cfg["audio_enabled"] = _coerce_enabled_flag(video_call_payload.get("audio_enabled"))
        if "disable_autoyou_agents" in video_call_payload:
            video_cfg["disable_autoyou_agents"] = _coerce_enabled_flag(video_call_payload.get("disable_autoyou_agents"))
        if "ai_audio_replies_enabled" in video_call_payload:
            video_cfg["ai_audio_replies_enabled"] = _coerce_enabled_flag(
                video_call_payload.get("ai_audio_replies_enabled")
            )
        if "background_mode_enabled" in video_call_payload:
            video_cfg["background_mode_enabled"] = _coerce_enabled_flag(video_call_payload.get("background_mode_enabled"))
        if "silent_recording_enabled" in video_call_payload:
            video_cfg["silent_recording_enabled"] = _coerce_enabled_flag(video_call_payload.get("silent_recording_enabled"))
        if "location_recording_enabled" in video_call_payload:
            video_cfg["location_recording_enabled"] = _coerce_enabled_flag(video_call_payload.get("location_recording_enabled"))
        if "wuift_enabled" in video_call_payload:
            video_cfg["wuift_enabled"] = _coerce_enabled_flag(video_call_payload.get("wuift_enabled"))
        if "silent_recording_dir" in video_call_payload:
            video_cfg["silent_recording_dir"] = str(video_call_payload.get("silent_recording_dir") or "").strip()
        if "silent_recording_batch_seconds" in video_call_payload:
            video_cfg["silent_recording_batch_seconds"] = normalize_audio_recording_batch_seconds(
                video_call_payload.get("silent_recording_batch_seconds"),
                DEFAULT_AUDIO_RECORDING_BATCH_SECONDS,
            )
        if "record_my_video" in video_call_payload:
            video_cfg["record_my_video"] = _coerce_enabled_flag(video_call_payload.get("record_my_video"))
        if "recording_dir" in video_call_payload:
            video_cfg["recording_dir"] = str(video_call_payload.get("recording_dir") or "").strip()
        if "recording_mode" in video_call_payload:
            video_cfg["recording_mode"] = normalize_inbound_video_recording_mode(
                video_call_payload.get("recording_mode"),
                "video",
            )
        if "image_interval_seconds" in video_call_payload:
            video_cfg["image_interval_seconds"] = normalize_inbound_video_image_interval_seconds(
                video_call_payload.get("image_interval_seconds"),
                DEFAULT_INBOUND_VIDEO_IMAGE_INTERVAL_SECONDS,
            )
        if "outbound_source" in video_call_payload:
            video_cfg["outbound_source"] = _normalize_video_outbound_source(video_call_payload.get("outbound_source"))
        if "outbound_sources" in video_call_payload:
            selected_sources = _normalize_video_outbound_sources(video_call_payload.get("outbound_sources"), fallback_source="")
            video_cfg["outbound_sources"] = selected_sources
            if selected_sources:
                video_cfg["outbound_source"] = selected_sources[0]
        if "api_video_source_id" in video_call_payload:
            video_cfg["api_video_source_id"] = str(video_call_payload.get("api_video_source_id") or "default").strip() or "default"
        if "capture_audio" in video_call_payload:
            video_cfg["capture_audio"] = _coerce_enabled_flag(video_call_payload.get("capture_audio"))
        if "audio_sources" in video_call_payload:
            selected_audio_sources = _normalize_video_audio_sources(video_call_payload.get("audio_sources"))
            video_cfg["audio_sources"] = selected_audio_sources
            video_cfg["capture_audio"] = bool(selected_audio_sources)
        if "input_audio_source" in video_call_payload:
            video_cfg["input_audio_source"] = str(video_call_payload.get("input_audio_source") or "default").strip() or "default"
        if "camera_device_id" in video_call_payload:
            try:
                video_cfg["camera_device_id"] = int(video_call_payload.get("camera_device_id", 0))
            except Exception:
                video_cfg["camera_device_id"] = 0

        video_file_payload = video_call_payload.get("video_file")
        if isinstance(video_file_payload, dict):
            file_cfg = video_cfg.setdefault("video_file", {})
            if not isinstance(file_cfg, dict):
                file_cfg = {}
                video_cfg["video_file"] = file_cfg
            if "path" in video_file_payload:
                file_cfg["path"] = str(video_file_payload.get("path") or "").strip()
            if "loop" in video_file_payload:
                file_cfg["loop"] = _coerce_enabled_flag(video_file_payload.get("loop"))

        remote_payload = video_call_payload.get("remote_desktop")
        if isinstance(remote_payload, dict):
            remote_cfg = video_cfg.setdefault("remote_desktop", {})
            if not isinstance(remote_cfg, dict):
                remote_cfg = {}
                video_cfg["remote_desktop"] = remote_cfg
            if "enabled" in remote_payload:
                remote_cfg["enabled"] = _coerce_enabled_flag(remote_payload.get("enabled"))
            if "send_screen" in remote_payload:
                remote_cfg["send_screen"] = _coerce_enabled_flag(remote_payload.get("send_screen"))
            if "monitor_id" in remote_payload:
                remote_cfg["monitor_id"] = _normalize_remote_desktop_monitor_id(remote_payload.get("monitor_id"))
            if "quality" in remote_payload:
                remote_cfg["quality"] = normalize_remote_desktop_quality(remote_payload.get("quality"))
            if "bitrate_kbps" in remote_payload:
                remote_cfg["bitrate_kbps"] = normalize_remote_desktop_bitrate_kbps(
                    remote_payload.get("bitrate_kbps")
                )
            if "control_enabled" in remote_payload:
                remote_cfg["control_enabled"] = _coerce_enabled_flag(remote_payload.get("control_enabled"))
        touched_sections.add("video_call")

    rtc_payload = payload.get("rtc")
    if isinstance(rtc_payload, dict) and "iceServers" in rtc_payload:
        ice_servers = rtc_payload.get("iceServers")
        if isinstance(ice_servers, str):
            ice_servers = parse_ice_servers_input(ice_servers)
        if not isinstance(ice_servers, list):
            raise ValueError("Connection helper settings must be a list or parseable string.")
        cfg.setdefault("rtc", {})["iceServers"] = dedupe_ice_servers(list(ice_servers))
        touched_sections.add("rtc")

    speech_payload = payload.get("speech")
    if isinstance(speech_payload, dict):
        cfg["speech"] = normalize_speech_config(speech_payload)
        touched_sections.add("speech")

    security_payload = payload.get("security")
    if isinstance(security_payload, dict):
        security_cfg = cfg.setdefault("security", {})
        if "native_unlock_enabled" in security_payload:
            security_cfg["native_unlock_enabled"] = _normalize_config_bool(
                security_payload.get("native_unlock_enabled"),
                True,
            )
            touched_sections.add("security")

    admin_frontend_payload = payload.get("admin_frontend")
    if isinstance(admin_frontend_payload, dict) and "enabled" in admin_frontend_payload:
        cfg = _set_agent_frontend_enabled(
            "admin_agent",
            _coerce_enabled_flag(admin_frontend_payload.get("enabled")),
            cfg=cfg,
        )
        touched_sections.add("admin_frontend")

    if "ui_theme" in payload:
        resolved_ui_theme = normalize_ui_theme(payload.get("ui_theme"))

    _apply_default_server_identity_config(cfg)
    _apply_default_security_config(cfg)
    _apply_default_tunnelmole_config(cfg)
    _apply_default_client_identity_config(cfg)
    _apply_default_agent_frontends_config(cfg)
    _apply_default_bluetooth_pairing_config(cfg)
    _apply_default_ice_servers(cfg)
    _apply_default_speech_config(cfg)
    _apply_default_onboarding_config(cfg)
    _apply_default_cloud_config(cfg)
    _apply_default_autoyou_page_config(cfg)
    _apply_default_messaging_partner_config(cfg)
    _apply_default_video_call_config(cfg)
    return cfg, touched_sections, resolved_ui_theme

def _require_api_login(request: Request) -> Optional[JSONResponse]:
    if _is_logged_in(request):
        return None
    return JSONResponse(status_code=401, content={"success": False, "error": "Authentication required"})

def _is_loopback_client_host(host: Optional[str]) -> bool:
    normalized = str(host or "").strip().lower()
    if not normalized:
        return False
    if normalized in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    if normalized.startswith("::ffff:"):
        return normalized.removeprefix("::ffff:") == "127.0.0.1"
    return False

def _require_loopback_request(request: Request) -> Optional[JSONResponse]:
    client_host = request.client.host if request.client else None
    if _is_loopback_client_host(client_host):
        return None
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": "This endpoint is available only from localhost."},
    )


def _require_loopback_or_https_request(
    request: Request,
    *,
    allow_local_pair: bool = False,
) -> Optional[JSONResponse]:
    client_host = request.client.host if request.client else None
    # A home-network browser forwarded by the page service arrives from
    # 127.0.0.1; its own leg to this computer must still have been HTTPS.
    if (
        _is_loopback_client_host(client_host)
        and request.headers.get(REMOTE_BROWSER_HEADER, "").strip() == REMOTE_BROWSER_VIA_HOME_NETWORK
        and request.headers.get("X-Forwarded-Proto", "").strip().lower() != "https"
    ):
        return JSONResponse(
            status_code=403,
            content={"success": False, "error": "This endpoint requires localhost or HTTPS."},
        )
    if _is_loopback_client_host(client_host) or str(request.url.scheme or "").lower() == "https":
        return None
    # ponytail: HTTP Local Pair still exposes the form password; replace this
    # marker-gated compatibility path with HTTPS/nonce auth when TLS is ready.
    if (
        allow_local_pair
        and request.headers.get("X-AutoYou-Local-Pair", "").strip() == "1"
        and _csrf_peer_is_private_or_loopback(client_host or "")
    ):
        return None
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": "This endpoint requires localhost or HTTPS."},
    )

def _require_loopback_or_token(request: Request) -> Optional[JSONResponse]:
    """Allow requests that originate from loopback OR carry the internal AI agent token."""
    if _request_uses_ai_agent_internal_token(request):
        return None
    client_host = request.client.host if request.client else None
    if _is_loopback_client_host(client_host):
        return None
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": "This endpoint requires localhost access or an internal token."},
    )

def _build_agent_workbench_refresh(agent_name: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    payload = _build_agent_builder_listing_payload()
    if payload.get("status") == "success":
        _sync_frontend_registry_from_builder_payload(payload)

    detail = None
    if agent_name:
        detail_payload = _build_agent_workbench_detail_payload(agent_name)
        if detail_payload.get("status") == "success":
            detail = detail_payload.get("detail")

    return payload, detail

def _build_agent_workbench_success_response(
    *,
    agent_name: Optional[str],
    message: str,
    requires_restart: bool = False,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload, detail = _build_agent_workbench_refresh(agent_name)
    response: Dict[str, Any] = {
        "success": True,
        "message": message,
        "requires_restart": bool(requires_restart),
        "payload": payload,
    }
    if agent_name:
        response["agent_name"] = agent_name
    if detail is not None:
        response["detail"] = detail
    if isinstance(extra, dict):
        response.update(extra)
    return response


# ── New Admin UI: /api/agents/workbench/ endpoints ────────────────────────────
# These endpoints expose the existing workbench utility functions under the URL
# contract expected by the new admin-ui.js shell.


# Telegram User endpoints stay separate from the existing Bot API routes.  They
# only operate on the owner's Saved Messages conversation and never accept a
# browser-supplied session token.


# Add Signal endpoints to admin app

# Add WhatsApp endpoints to admin app

def _is_logged_in(request: Request) -> bool:
    sid = request.cookies.get("admin_session", "")
    if sid and ADMIN_SESSIONS.get(sid, False):
        return True
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token and ADMIN_API_TOKENS.get(token, 0) > time.time():
            return True
    return False

def _require_login(request: Request) -> Optional[Response]:
    if not _is_logged_in(request):
        request_path = str(getattr(getattr(request, "url", None), "path", "") or "")
        accepts_json = "application/json" in str(request.headers.get("accept", "")).lower()
        if request_path.startswith("/api/") or accepts_json:
            return JSONResponse(
                status_code=401,
                content={"success": False, "error": "Authentication required"},
            )
        return RedirectResponse(url="/login", status_code=302)
    return None

def _require_api_login_json(request: Request) -> Optional[JSONResponse]:
  if _is_logged_in(request) or _request_uses_ai_agent_internal_token(request):
    return None
  return JSONResponse(
    status_code=401,
    content={"success": False, "error": "Not authenticated"},
  )

def _require_local_service_callback_json(
    request: Request,
    *,
    token_env: str = "",
) -> Optional[JSONResponse]:
  """Authorize a callback from a co-located helper service (not a browser).

  Webhooks from the local WhatsApp/Signal containers carry no admin session and
  no browser Origin, so the boundary has to be network position: the caller must
  be on loopback or a private address (the Docker bridge), and must not have
  arrived through the public tunnel. The tunnel bridge stamps a trusted
  ``X-AutoYou-Tunnel-Client-IP`` header on everything it forwards, so its
  presence is positive proof the request came from the internet even though the
  observed peer is ``127.0.0.1``.

  When ``token_env`` names a set environment variable, a matching bearer token
  is additionally required, which is the stronger boundary where the helper can
  be configured to send one.
  """
  try:
    if str(request.headers.get(_BRIDGE_TRUSTED_CLIENT_IP_HEADER) or "").strip():
      LOGGER.warning(
        "Rejected tunnel-originated callback to %s", request.url.path or "/"
      )
      return JSONResponse(
        status_code=403,
        content={"success": False, "error": "Not available over the public tunnel."},
      )
  except Exception:
    pass

  peer = request.client.host if request.client else ""
  if not _csrf_peer_is_private_or_loopback(peer):
    LOGGER.warning(
      "Rejected non-local callback to %s from %s", request.url.path or "/", peer or "unknown"
    )
    return JSONResponse(
      status_code=403,
      content={"success": False, "error": "Callback must originate from the local host."},
    )

  expected = str(os.environ.get(token_env, "") or "").strip() if token_env else ""
  if expected:
    provided = str(request.headers.get("Authorization", "") or "").strip()
    if provided.lower().startswith("bearer "):
      provided = provided[7:].strip()
    if not provided or not secrets.compare_digest(provided, expected):
      return JSONResponse(
        status_code=401,
        content={"success": False, "error": "Invalid callback token."},
      )

  return None

def _require_webrtc_playback_auth_json(request: Request) -> Optional[JSONResponse]:
    if _is_logged_in(request) or _request_uses_ai_agent_internal_token(request):
        return None
    return JSONResponse(
        status_code=401,
        content={"success": False, "error": "Not authenticated"},
    )

def _extract_admin_media_attachments(
    context: Any,
    attachments_payload: Any,
    *,
    source: str,
) -> List[Dict[str, Any]]:
    media_attachments: List[Dict[str, Any]] = []
    if context is None and attachments_payload is None:
        return media_attachments
    try:
        from shared.media_messaging import extract_media_reply_attachments

        if context is not None:
            media_attachments.extend(extract_media_reply_attachments(context, source=source))
        if attachments_payload is not None:
            media_attachments.extend(
                extract_media_reply_attachments(
                    {"attachments": attachments_payload},
                    source=source,
                )
            )
    except Exception as exc:
        LOGGER.warning("Failed to extract admin media attachments for %s: %s", source, exc)
    return media_attachments

def _caption_first_media_attachment(
    attachments: List[Dict[str, Any]],
    message: str,
) -> List[Dict[str, Any]]:
    normalized_message = str(message or "").strip()
    if not normalized_message:
        return [dict(attachment) for attachment in attachments or [] if isinstance(attachment, dict)]
    captioned: List[Dict[str, Any]] = []
    for index, attachment in enumerate(attachments or []):
        if not isinstance(attachment, dict):
            continue
        item = dict(attachment)
        if index == 0 and not str(item.get("caption") or "").strip():
            item["caption"] = normalized_message
        captioned.append(item)
    return captioned

def _set_no_store_headers(response: HTMLResponse) -> HTMLResponse:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def _html_page(body: str) -> HTMLResponse:
    page_title = html.escape(_get_admin_ui_title())
    current_theme = _read_autoyou_ui_theme()
    css = """
    <style>
    :root {
      color-scheme: dark;
      --bg:#08111d;
      --bg-radial:rgba(34,197,94,.09);
      --surface:#0d1727;
      --surface-2:#0a1320;
      --bg-secondary:rgba(7,14,26,.92);
      --card:#0f172a;
      --card-nested:rgba(7,14,26,.86);
      --fg:#e2e8f0;
      --text:#e2e8f0;
      --muted:#94a3b8;
      --text-muted:#94a3b8;
      --accent:#22c55e;
      --accent-soft:#38bdf8;
      --warn:#f59e0b;
      --danger:#ef4444;
      --stroke:rgba(71,85,105,.6);
      --stroke-soft:rgba(71,85,105,.38);
      --chip:rgba(15,23,42,.82);
      --chip-border:rgba(71,85,105,.48);
      --focus-ring:0 0 0 3px rgba(56,189,248,.24);
      --admin-shell-width:min(1380px, calc(100vw - 40px));
      --admin-search-top:env(safe-area-inset-top, 0px);
      --admin-search-gap:98px;
      --admin-sections-sheet-top:calc(var(--admin-search-top) + 92px);
    }
    :root[data-theme="light"]{
      color-scheme: light;
      --bg:#f6f8fb;
      --bg-radial:rgba(37,99,235,.10);
      --surface:#ffffff;
      --surface-2:#eef3f8;
      --bg-secondary:#f3f4f6;
      --card:#ffffff;
      --card-nested:#f8fafc;
      --fg:#111827;
      --text:#111827;
      --muted:#475569;
      --text-muted:#475569;
      --accent:#2563eb;
      --accent-soft:#16a34a;
      --warn:#d97706;
      --danger:#dc2626;
      --stroke:rgba(148,163,184,.52);
      --stroke-soft:rgba(148,163,184,.32);
      --chip:#eef2f7;
      --chip-border:rgba(148,163,184,.58);
      --focus-ring:0 0 0 3px rgba(37,99,235,.18);
    }
    *{box-sizing:border-box; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Ubuntu, Cantarell, Noto Sans, sans-serif}
    html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
    body{
      margin:0;
      padding:18px 0 42px;
      background:
        radial-gradient(circle at top, var(--bg-radial), transparent 30%),
        linear-gradient(180deg, #08111d 0%, #0a1320 45%, #07101b 100%);
      color:var(--fg);
      line-height:1.58;
      overflow-x:hidden;
    }
    :root[data-theme="light"] body{
      background:
        radial-gradient(circle at top, var(--bg-radial), transparent 32%),
        linear-gradient(180deg, #ffffff 0%, #f8fafc 44%, #eef3f8 100%);
    }
    img,svg,canvas,video{max-width:100%}
    .container{max-width:940px;width:100%;margin:0 auto;padding:0 16px 36px}
    .card{
      position:relative;
      overflow:hidden;
      background:linear-gradient(180deg, rgba(13,23,39,.98), rgba(9,18,32,.96));
      border:1px solid var(--stroke);
      border-radius:22px;
      padding:22px;
      margin:16px auto;
      width:100%;
      box-shadow:0 18px 36px rgba(2,6,23,.3), inset 0 1px 0 rgba(148,163,184,.04);
    }
    .card::before{
      content:"";
      position:absolute;
      inset:0 0 auto;
      height:1px;
      background:linear-gradient(90deg, transparent, rgba(125,211,252,.18), transparent);
      pointer-events:none;
    }
    .container > .card > h1,
    .container > .card > h2{margin:0 0 10px;line-height:1.16;letter-spacing:-.03em}
    h1,h2{margin:8px 0}
    .muted{color:var(--muted)}
    input, textarea, select{
      width:100%;
      min-height:50px;
      padding:12px 14px;
      background:#0b1220;
      border:1px solid #223047;
      border-radius:14px;
      color:var(--fg);
      font-size:16px;
      transition:border-color .18s ease, box-shadow .18s ease, background-color .18s ease;
    }
    input::placeholder, textarea::placeholder{color:#6b7b94}
    label{display:block;margin-bottom:6px;font-weight:500;color:var(--fg)}
    small{display:block;margin-top:4px;font-size:0.85em}
    button{
      background:var(--accent);
      border:none;
      color:#04100a;
      padding:10px 16px;
      border-radius:12px;
      cursor:pointer;
      font-weight:700;
      margin-right:8px;
      margin-bottom:8px;
      min-height:44px;
      transition:transform .16s ease, box-shadow .16s ease, background-color .16s ease, border-color .16s ease;
    }
    button:hover{transform:translateY(-1px);box-shadow:0 12px 24px rgba(2,6,23,.18)}
    button:disabled,button[disabled]{opacity:.6;cursor:not-allowed;transform:none !important;box-shadow:none !important}
    /* ── Button loading spinner ─────────────────────────────────────── */
    button.loading,button[data-loading]{
      position:relative;
      cursor:wait !important;
      pointer-events:none;
      opacity:.82;
      transform:none !important;
    }
    button.loading > *,button[data-loading] > *{opacity:0;visibility:hidden}
    button.loading::after,button[data-loading]::after{
      content:"";
      position:absolute;
      inset:0;
      margin:auto;
      width:16px;
      height:16px;
      border-radius:50%;
      border:2.5px solid rgba(255,255,255,.25);
      border-top-color:currentColor;
      animation:btn-spin .65s linear infinite;
    }
    @keyframes btn-spin{to{transform:rotate(360deg)}}
    .primary-btn,.secondary-btn,.danger-btn,.ghost{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:44px;
      padding:10px 16px;
      border-radius:12px;
      font-weight:700;
      text-decoration:none;
    }
    .primary-btn{background:var(--accent);color:#04100a}
    .secondary-btn{background:transparent;color:var(--fg);border:1px solid #334155}
    .danger-btn{background:var(--danger);color:#130a0a}
    .primary-btn:hover,.secondary-btn:hover,.danger-btn:hover,.ghost:hover{
      transform:translateY(-1px);
      box-shadow:0 12px 24px rgba(2,6,23,.18);
    }
    .row{display:flex;gap:16px;flex-wrap:wrap}
    .col{flex:1;min-width:280px}
    .danger{background:var(--danger);color:#130a0a}
    .warn{background:var(--warn);color:#120d03}
    .success{background:#052e1a;border-color:#14532d}
    .grid{display:grid;grid-template-columns:1fr 1fr; gap:16px}
    .mono{font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;}
    a{color:#7dd3fc}
    :root[data-theme="light"] a{color:#1d4ed8}
    .ghost{background:transparent;color:var(--fg);border:1px solid #334155}
    .header-action-btn{
      min-width:160px;
      font-size:.95rem;
      padding:10px 16px;
      border-radius:12px;
      text-align:center;
      margin:0;
    }
    .header-action-btn.secondary-btn{
      background:#6b7280;
      border-color:#6b7280;
      color:#ffffff;
    }
    .header-action-btn.secondary-btn:hover{
      background:#4b5563;
      border-color:#4b5563;
    }
    .header-action-btn.danger-btn{
      color:#ffffff;
    }
    .agent-card-shell{
      padding:16px;
      border-radius:18px;
      border:1px solid var(--stroke);
      background:linear-gradient(180deg, rgba(11,19,33,.92), rgba(9,16,30,.90));
      box-shadow:inset 0 1px 0 rgba(148,163,184,.04);
    }
    .agent-card-top{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
    }
    .agent-card-name{
      font-weight:800;
      font-size:1.02rem;
      color:#f8fafc;
    }
    .agent-card-subname{
      margin-top:4px;
      font-size:.82rem;
    }
    .agent-card-desc{
      margin:10px 0;
      color:var(--text-muted);
      line-height:1.55;
    }
    .agent-card-meta{
      display:grid;
      gap:4px;
      font-size:.8rem;
      line-height:1.5;
      color:var(--text-muted);
    }
    .agent-card-actions{
      display:flex;
      gap:8px;
      flex-wrap:wrap;
      margin-top:12px;
    }
    .agent-badge,
    .agent-summary-count{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:28px;
      padding:0 11px;
      border-radius:999px;
      font-size:.78rem;
      font-weight:800;
      border:1px solid transparent;
      letter-spacing:.01em;
    }
    .agent-badge-installed,
    .agent-summary-count.installed{
      background:rgba(34,197,94,.16);
      color:#bbf7d0;
      border-color:rgba(34,197,94,.26);
    }
    .agent-badge-available,
    .agent-summary-count.available{
      background:rgba(96,165,250,.16);
      color:#bfdbfe;
      border-color:rgba(96,165,250,.26);
    }
    .agent-badge-frontend{
      background:rgba(168,85,247,.16);
      color:#f3e8ff;
      border-color:rgba(168,85,247,.28);
    }
    .agent-badge-live{
      background:rgba(56,189,248,.15);
      color:#d7f1ff;
      border-color:rgba(56,189,248,.26);
    }
    .agent-badge-enabled{
      background:rgba(34,197,94,.16);
      color:#bbf7d0;
      border-color:rgba(34,197,94,.26);
    }
    .agent-badge-disabled{
      background:rgba(148,163,184,.18);
      color:#e2e8f0;
      border-color:rgba(148,163,184,.26);
    }
    .provider-summary-bar{
      margin-bottom:16px;
      padding:12px 14px;
      border-radius:16px;
      border:1px solid rgba(71,85,105,.56);
      background:linear-gradient(180deg, rgba(15,23,42,.92), rgba(11,18,32,.92));
      font-size:.9em;
      color:#dbe7f5;
    }
    .provider-panel{
      border:1px solid rgba(71,85,105,.56);
      border-radius:18px;
      padding:16px;
      margin-bottom:14px;
      background:linear-gradient(180deg, rgba(15,23,42,.94), rgba(11,18,32,.94));
    }
    .provider-inline-status{
      margin-bottom:10px;
      padding:8px 10px;
      border-radius:12px;
      background:rgba(15,23,42,.72);
      border:1px solid rgba(71,85,105,.46);
      font-size:.85em;
      color:#cbd5e1;
    }
    .brand-lockup{display:flex;align-items:center;gap:14px;min-width:0}
    .brand-logo{width:48px;height:48px;object-fit:contain}
    .brand-meta{display:flex;flex-direction:column;gap:2px;min-width:0}
    .brand-kicker{font-size:.72rem;font-weight:700;letter-spacing:.18em;text-transform:uppercase;color:#7dd3fc}
    .brand-name{font-size:1.28rem;font-weight:800;letter-spacing:-.03em;color:#f8fafc;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .admin-header-brand .brand-logo{width:42px;height:42px;border-radius:12px}
    .admin-header-brand .brand-name{font-size:1.06rem}
    button:focus-visible,
    input:focus-visible,
    textarea:focus-visible,
    select:focus-visible,
    a:focus-visible,
    summary:focus-visible{outline:none;box-shadow:var(--focus-ring);border-color:rgba(56,189,248,.68)}
    .container > .card .card{
      margin:14px 0 0;
      padding:18px;
      border-radius:18px;
      border-color:var(--stroke-soft);
      background:linear-gradient(180deg, rgba(7,14,26,.92), rgba(8,15,28,.84));
      box-shadow:none;
    }
    .container > .card .card .card{
      background:var(--card-nested);
      border-color:rgba(71,85,105,.28);
      padding:16px;
    }
    .admin-command-card{
      position:fixed;
      top:var(--admin-search-top);
      left:50%;
      width:var(--admin-shell-width);
      max-width:var(--admin-shell-width);
      margin:0;
      transform:translateX(-50%);
      z-index:1601;
      isolation:isolate;
      background:linear-gradient(180deg, rgba(9,18,33,.98), rgba(8,15,29,.94));
      border-color:rgba(56,189,248,.28);
      box-shadow:0 22px 44px rgba(2,6,23,.36), inset 0 1px 0 rgba(148,163,184,.07);
      backdrop-filter:blur(18px);
    }
    .admin-command-shell{display:grid;gap:12px}
    .admin-search-bar-row{display:flex;align-items:center;gap:12px;width:100%}
    .admin-search-panel{
      display:grid;
      grid-template-rows:auto minmax(0,1fr);
      gap:12px;
      padding-top:12px;
      border-top:1px solid rgba(71,85,105,.3);
      max-height:min(58vh, 34rem);
      min-height:0;
      overflow:hidden;
    }
    .admin-search-panel[hidden]{display:none !important}
    .admin-command-head{display:grid;gap:10px;align-items:flex-start}
    .admin-command-copy{max-width:540px}
    .admin-command-copy h2{margin:0 0 6px;font-size:1.02rem;letter-spacing:-.02em}
    .admin-command-copy p{margin:0;color:#9fb3cf}
    .admin-search-wrap{
      position:relative;
      flex:1 1 auto;
      width:100%;
      min-width:0;
    }
    .admin-search-menu-btn{
      position:absolute;
      left:8px;
      top:50%;
      transform:translateY(-50%);
      display:inline-flex;
      min-height:32px;
      min-width:32px;
      margin:0;
      padding:6px 8px;
      border-radius:999px;
      background:rgba(15,23,42,.92);
      border:1px solid rgba(71,85,105,.55);
      color:#dbeafe;
      box-shadow:none;
      cursor:pointer;
      align-items:center;
      justify-content:center;
      line-height:1;
    }
    .admin-search-menu-btn:hover,
    .admin-search-menu-btn.is-open{
      background:rgba(15,23,42,1);
      box-shadow:none;
      transform:translateY(-50%);
    }
    .admin-search-menu-icon{display:inline-flex;font-size:1rem;line-height:1}
    .admin-search-icon{
      position:absolute;
      left:44px;
      top:50%;
      transform:translateY(-50%);
      font-size:15px;
      color:#7dd3fc;
      background:none;
      border:none;
      padding:6px 8px;
      margin:0;
      min-height:0;
      box-shadow:none;
      cursor:pointer;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      border-radius:8px;
      transition:color .15s,background .15s;
    }
    .admin-search-icon:hover{color:#38bdf8;background:rgba(56,189,248,.1);box-shadow:none;transform:translateY(-50%)}
    .admin-search-icon:active{transform:translateY(-50%) scale(.94)}
    .admin-search-field{
      display:block;
      width:100%;
      padding-left:80px;
      padding-right:110px;
    }
    .admin-search-tools{
      position:absolute;
      right:10px;
      top:50%;
      transform:translateY(-50%);
      display:flex;
      align-items:center;
      gap:8px;
    }
    .admin-search-reset{
      min-height:32px;
      min-width:32px;
      margin:0;
      padding:6px 10px;
      border-radius:999px;
      background:rgba(15,23,42,.92);
      border:1px solid rgba(71,85,105,.55);
      color:#cbd5e1;
      box-shadow:none;
      transform:none;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      font-size:.86rem;
      line-height:1;
    }
    .admin-search-reset[hidden]{display:none}
    .admin-search-reset:hover{background:rgba(15,23,42,1);box-shadow:none;transform:none}
    .admin-search-toggle{
      min-height:32px;
      min-width:32px;
      margin:0;
      padding:6px 10px;
      border-radius:999px;
      background:rgba(15,23,42,.92);
      border:1px solid rgba(71,85,105,.55);
      color:#dbeafe;
      box-shadow:none;
      transform:none;
      display:inline-flex;
      align-items:center;
      justify-content:center;
      font-size:.88rem;
      line-height:1;
    }
    .admin-search-toggle:hover{background:rgba(15,23,42,1);box-shadow:none;transform:none}
    .admin-search-toggle[aria-expanded="true"] .admin-search-toggle-icon{transform:rotate(180deg)}
    .admin-search-toggle-icon{display:inline-flex;transition:transform .18s ease}
    .admin-search-results{
      display:grid;
      gap:8px;
      min-height:0;
      max-height:none;
      overflow:auto;
      overscroll-behavior:contain;
      padding-right:4px;
      scrollbar-gutter:stable;
    }
    .admin-search-empty{
      padding:14px 16px;
      border-radius:16px;
      background:rgba(8,15,28,.72);
      border:1px dashed rgba(71,85,105,.5);
      color:#9fb3cf;
    }
    .admin-search-result{
      width:100%;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
      padding:12px 14px;
      min-height:64px;
      border-radius:16px;
      border:1px solid rgba(71,85,105,.48);
      background:rgba(7,14,26,.96);
      color:#eef6ff;
      text-align:left;
      cursor:pointer;
      line-height:1.45;
    }
    .admin-search-result:hover{
      border-color:rgba(56,189,248,.5);
      box-shadow:0 14px 28px rgba(2,6,23,.22);
    }
    .admin-search-result-main{display:grid;gap:4px;min-width:0;padding-right:0}
    .admin-search-result-section{
      font-size:.78rem;
      font-weight:700;
      letter-spacing:.08em;
      text-transform:uppercase;
      color:#7dd3fc;
      line-height:1.3;
      white-space:normal;
      overflow:hidden;
      display:-webkit-box;
      -webkit-line-clamp:1;
      -webkit-box-orient:vertical;
    }
    .admin-search-result-title{
      font-size:.98rem;
      font-weight:700;
      color:#f8fafc;
      line-height:1.34;
      white-space:normal;
      overflow:hidden;
      display:-webkit-box;
      -webkit-line-clamp:2;
      -webkit-box-orient:vertical;
    }
    .admin-jump-grid{display:flex;flex-wrap:wrap;gap:10px}
    .admin-jump-chip{
      display:inline-flex;
      align-items:center;
      gap:8px;
      margin:0;
      padding:10px 14px;
      min-height:40px;
      border-radius:999px;
      background:var(--chip);
      border:1px solid var(--chip-border);
      color:#dbeafe;
      box-shadow:none;
      transform:none;
      font-size:.86rem;
    }
    .admin-jump-chip:hover{
      background:rgba(15,23,42,.98);
      border-color:rgba(56,189,248,.48);
      box-shadow:none;
      transform:none;
    }
    .admin-search-hit{
      box-shadow:0 0 0 2px rgba(56,189,248,.42), 0 22px 44px rgba(2,6,23,.28) !important;
      border-color:rgba(56,189,248,.68) !important;
      transition:box-shadow .18s ease, border-color .18s ease;
    }
    @keyframes adminSearchPulse{
      0%{box-shadow:0 0 0 0 rgba(56,189,248,.34)}
      100%{box-shadow:0 0 0 16px rgba(56,189,248,0)}
    }
    .admin-search-hit::after{
      content:"";
      position:absolute;
      inset:-1px;
      border-radius:inherit;
      animation:adminSearchPulse 1s ease-out;
      pointer-events:none;
    }
    .admin-command-card{
      overflow:visible;
    }
    .admin-command-shell{
      position:relative;
    }
    .admin-search-panel{
      position:absolute;
      top:calc(100% + 12px);
      left:0;
      right:0;
      z-index:42;
      padding:16px;
      border-radius:22px;
      border:1px solid rgba(56,189,248,.18);
      background:linear-gradient(180deg, rgba(7,14,26,.98), rgba(8,15,29,.96));
      box-shadow:0 28px 60px rgba(2,6,23,.38);
    }
    .admin-dashboard-shell{
      position:relative;
      width:100%;
      display:block;
    }
    .admin-mobile-nav{
      display:grid;
      gap:14px;
    }
    .admin-nav-group{
      display:grid;
      gap:10px;
    }
    .admin-nav-group-label{
      font-size:.72rem;
      font-weight:800;
      letter-spacing:.14em;
      text-transform:uppercase;
      color:rgba(186,230,253,.88);
    }
    .admin-nav-links{
      display:grid;
      gap:8px;
    }
    .admin-nav-link{
      display:flex;
      align-items:center;
      min-height:42px;
      padding:11px 13px;
      border-radius:16px;
      border:1px solid rgba(148,163,184,.16);
      background:rgba(15,23,42,.42);
      color:#dbeafe;
      text-decoration:none;
      font-weight:600;
      line-height:1.35;
      transition:border-color .18s ease, background-color .18s ease, color .18s ease, box-shadow .18s ease, transform .18s ease;
      backdrop-filter:blur(16px);
      -webkit-backdrop-filter:blur(16px);
    }
    .admin-nav-link:hover,
    .admin-nav-link.active{
      border-color:rgba(125,211,252,.46);
      background:rgba(14,165,233,.16);
      color:#f8fafc;
      box-shadow:0 10px 24px rgba(14,165,233,.16);
      transform:translateY(-1px);
    }
    .admin-main-panels{
      min-width:0;
      width:100%;
    }
    .admin-main-panels > .card:first-child,
    .admin-main-panels > section:first-child{
      margin-top:0;
    }
    .scheduler-queue-header{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:14px;
      flex-wrap:wrap;
    }
    .scheduler-queue-toolbar{
      display:flex;
      align-items:center;
      gap:10px;
      flex-wrap:wrap;
    }
    .scheduler-queue-summary-grid{
      display:grid;
      grid-template-columns:repeat(4, minmax(0, 1fr));
      gap:12px;
      margin-top:16px;
    }
    .scheduler-queue-stat{
      display:grid;
      gap:6px;
      min-width:0;
      padding:14px 16px;
      border-radius:18px;
      border:1px solid var(--stroke-soft);
      background:var(--card-nested);
      box-shadow:inset 0 1px 0 rgba(148,163,184,.04);
    }
    .scheduler-queue-label{
      font-size:.74rem;
      font-weight:800;
      letter-spacing:.12em;
      text-transform:uppercase;
      color:var(--muted);
    }
    .scheduler-queue-value{
      font-size:1.36rem;
      font-weight:800;
      line-height:1.08;
      color:var(--fg);
      letter-spacing:-.03em;
    }
    .scheduler-queue-copy{
      color:var(--muted);
      font-size:.84rem;
      line-height:1.45;
    }
    .scheduler-queue-note,
    .scheduler-queue-empty,
    .scheduler-queue-error{
      margin-top:14px;
      padding:12px 14px;
      border-radius:16px;
      border:1px solid var(--stroke-soft);
      background:var(--card-nested);
      color:var(--text-muted);
      line-height:1.5;
    }
    .scheduler-queue-error{
      border-color:rgba(239,68,68,.28);
      background:rgba(127,29,29,.14);
      color:var(--fg);
    }
    .scheduler-queue-list{
      display:grid;
      gap:12px;
      margin-top:14px;
    }
    .scheduler-queue-item{
      display:grid;
      gap:12px;
      min-width:0;
      padding:16px;
      border-radius:18px;
      border:1px solid var(--stroke);
      background:var(--card-nested);
      box-shadow:inset 0 1px 0 rgba(148,163,184,.04);
    }
    .scheduler-queue-item-head{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
      flex-wrap:wrap;
    }
    .scheduler-queue-item-title{
      display:grid;
      gap:6px;
      min-width:0;
    }
    .scheduler-queue-source{
      font-size:.74rem;
      font-weight:800;
      letter-spacing:.12em;
      text-transform:uppercase;
      color:var(--accent-soft);
    }
    .scheduler-queue-message{
      margin:0;
      color:var(--fg);
      font-size:.98rem;
      font-weight:700;
      line-height:1.5;
      overflow-wrap:anywhere;
    }
    .scheduler-queue-status-pill,
    .scheduler-queue-meta span,
    .scheduler-queue-tags span{
      display:inline-flex;
      align-items:center;
      max-width:100%;
      min-height:32px;
      padding:7px 11px;
      border-radius:999px;
      border:1px solid var(--chip-border);
      background:var(--chip);
      color:var(--fg);
      font-size:.82rem;
      line-height:1.35;
      overflow-wrap:anywhere;
      word-break:break-word;
    }
    .scheduler-queue-status-pill.ready{
      border-color:rgba(34,197,94,.26);
      background:rgba(34,197,94,.14);
      color:#bbf7d0;
    }
    .scheduler-queue-status-pill.retry{
      border-color:rgba(245,158,11,.28);
      background:rgba(245,158,11,.14);
      color:#fde68a;
    }
    .scheduler-queue-status-pill.waiting{
      border-color:rgba(148,163,184,.28);
      background:rgba(148,163,184,.14);
      color:#dbeafe;
    }
    .scheduler-queue-meta,
    .scheduler-queue-tags{
      display:flex;
      flex-wrap:wrap;
      gap:8px;
      min-width:0;
    }
    .scheduler-queue-tags span{
      color:var(--text);
    }
    .scheduler-queue-item-error{
      padding:12px 14px;
      border-radius:16px;
      border:1px solid rgba(239,68,68,.26);
      background:rgba(127,29,29,.12);
      color:var(--fg);
      font-size:.9rem;
      line-height:1.5;
      overflow-wrap:anywhere;
    }
    .admin-mobile-sections-close{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      min-height:44px;
      padding:0 16px;
      border-radius:999px;
      border:1px solid rgba(125,211,252,.22);
      background:linear-gradient(180deg, rgba(8,15,28,.88), rgba(12,20,36,.82));
      color:#e2e8f0;
      font-size:.92rem;
      font-weight:800;
      letter-spacing:-.01em;
      box-shadow:0 16px 32px rgba(2,6,23,.24), inset 0 1px 0 rgba(255,255,255,.06);
      backdrop-filter:blur(18px) saturate(130%);
      -webkit-backdrop-filter:blur(18px) saturate(130%);
    }
    .admin-mobile-sections-close:hover{
      border-color:rgba(125,211,252,.4);
      background:linear-gradient(180deg, rgba(14,165,233,.24), rgba(30,41,59,.9));
      color:#f8fafc;
      box-shadow:0 18px 34px rgba(14,165,233,.18), inset 0 1px 0 rgba(255,255,255,.08);
    }
    .admin-mobile-sections-sheet{
      position:fixed;
      inset:0;
      z-index:1540;
      padding:var(--admin-sections-sheet-top) max(12px, calc((100vw - var(--admin-shell-width)) / 2)) calc(18px + env(safe-area-inset-bottom, 0px));
      overflow:hidden;
      overscroll-behavior:contain;
    }
    .admin-mobile-sections-sheet[hidden]{
      display:none !important;
    }
    .admin-mobile-sections-backdrop{
      position:absolute;
      inset:0;
      margin:0;
      padding:0;
      min-height:0;
      width:100%;
      border:none;
      border-radius:0;
      background:rgba(2,6,23,.58);
      box-shadow:none;
      backdrop-filter:blur(10px);
      -webkit-backdrop-filter:blur(10px);
      touch-action:none;
    }
    .admin-mobile-sections-panel{
      position:relative;
      display:grid;
      grid-template-rows:auto minmax(0, 1fr);
      gap:14px;
      max-height:calc(100dvh - var(--admin-sections-sheet-top) - 18px - env(safe-area-inset-bottom, 0px));
      min-height:0;
      overflow:hidden;
      padding:16px 14px;
      border-radius:24px;
      border:1px solid rgba(125,211,252,.2);
      background:
        linear-gradient(160deg, rgba(7,14,28,.96), rgba(11,19,34,.92)),
        radial-gradient(circle at top, rgba(56,189,248,.18), transparent 46%);
      box-shadow:0 30px 64px rgba(2,6,23,.42), inset 0 1px 0 rgba(255,255,255,.06);
      backdrop-filter:blur(20px) saturate(140%);
      -webkit-backdrop-filter:blur(20px) saturate(140%);
    }
    .admin-mobile-sections-head{
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:12px;
    }
    .admin-mobile-sections-title{
      color:#f8fafc;
      font-size:1.12rem;
      font-weight:800;
      letter-spacing:-.03em;
    }
    .admin-mobile-sections-scroll{
      min-height:0;
      overflow:auto;
      overscroll-behavior:contain;
      -webkit-overflow-scrolling:touch;
      touch-action:pan-y;
      padding-right:4px;
      margin-right:-4px;
      scrollbar-width:thin;
      scrollbar-color:rgba(125,211,252,.28) transparent;
    }
    .admin-mobile-sections-scroll::-webkit-scrollbar{
      width:8px;
    }
    .admin-mobile-sections-scroll::-webkit-scrollbar-thumb{
      background:rgba(125,211,252,.28);
      border-radius:999px;
    }
    .admin-mobile-nav{
      display:grid;
      gap:14px;
    }
    .admin-mobile-sections-panel .admin-nav-group{
      gap:10px;
    }
    .admin-mobile-sections-panel .admin-nav-links{
      grid-template-columns:1fr;
      gap:10px;
    }
    .admin-mobile-sections-panel .admin-nav-link{
      min-height:46px;
      padding:12px 14px;
      justify-content:flex-start;
      white-space:normal;
    }

    /* Responsive breakpoints for centered, narrow cards */
    .container:has(.admin-dashboard-shell) {
      max-width:var(--admin-shell-width);
      padding-top:calc(var(--admin-search-top) + var(--admin-search-gap));
    }
    @media (min-width: 1200px) {
      .container { max-width: 860px; }
      .container:has(.admin-dashboard-shell) { max-width:var(--admin-shell-width); }
    }
    .container:has(.login-shell) { max-width: 920px; }
    @media (max-width: 1024px) {
      :root{
        --admin-shell-width:calc(100vw - 24px);
      }
      .container { max-width: 720px; }
      .container:has(.admin-dashboard-shell) { max-width:var(--admin-shell-width); }
    }
    @media (max-width: 768px) {
      :root{
        --admin-shell-width:calc(100vw - 24px);
        --admin-search-top:calc(env(safe-area-inset-top, 0px) + 4px);
        --admin-search-gap:98px;
        --admin-sections-sheet-top:calc(var(--admin-search-top) + 92px);
      }
      html, body{
        width:100%;
        max-width:100%;
        overflow-x:hidden;
      }
      html{scroll-behavior:auto}
      body{padding:0 0 calc(28px + env(safe-area-inset-bottom, 0px))}
      .container { max-width: 100%; padding: 0 12px 24px; }
      .container:has(.admin-dashboard-shell) { max-width:100%; padding: calc(var(--admin-search-top) + var(--admin-search-gap)) 12px 24px; }
      .container > *,
      .admin-dashboard-shell,
      .admin-main-panels,
      .admin-main-panels > .card,
      .admin-main-panels > section{
        width:100%;
        max-width:100%;
        min-width:0;
      }
      .card { padding: 18px; margin: 12px auto; border-radius: 20px; }
      .container > .card .card,
      .speech-surface-card,
      .speech-summary-card,
      .speech-stt-library,
      .speech-job-card,
      .wizard-mini-card,
      .partner-card,
      .download-job,
      .model-result-card,
      .local-model-card,
      .wizard-choice-card { padding: 14px; border-radius: 18px; }
      .row { flex-direction: column; }
      .grid { grid-template-columns: 1fr; }
      .login-hero, .startup-grid, .model-library-grid, .model-results-grid, .agent-section-grid, .speech-field-grid { grid-template-columns: 1fr !important; }
      .admin-search-panel{max-height:min(46vh, 22rem);gap:10px}
      .admin-command-copy p{display:none}
      .admin-mobile-sections-sheet{
        padding:var(--admin-sections-sheet-top) 12px calc(18px + env(safe-area-inset-bottom, 0px));
      }
      html.admin-mobile-sections-open,
      body.admin-mobile-sections-open{
        overflow:hidden;
        overscroll-behavior:none;
      }
      body.admin-mobile-sections-open{
        position:fixed;
        inset-inline:0;
        width:100%;
      }
      .admin-mobile-sections-panel{
        max-height:calc(100dvh - var(--admin-sections-sheet-top) - 18px - env(safe-area-inset-bottom, 0px));
      }
      .admin-mobile-sections-panel .admin-nav-group-label{
        display:block;
        font-size:.68rem;
        letter-spacing:.12em;
      }
      .admin-jump-grid{
        flex-wrap:nowrap;
        overflow:auto hidden;
        overscroll-behavior-x:contain;
        padding-bottom:2px;
        scrollbar-width:none;
      }
      .admin-jump-grid::-webkit-scrollbar{display:none}
      .admin-jump-chip{flex:0 0 auto;min-height:36px;padding:8px 12px;font-size:.82rem}
      .admin-search-results{padding-right:0}
      .admin-search-result{padding:11px 12px;min-height:60px}
      .admin-search-result-title{-webkit-line-clamp:2}
      .help-faq-head{flex-direction:column;align-items:flex-start}
      .setup-pill-row,
      .wizard-stat-grid,
      .partner-grid,
      .model-runtime-grid,
      .download-job-list,
      .wizard-choice-grid,
      .speech-model-grid,
      .help-guide-grid { grid-template-columns: 1fr !important; }
      .startup-panel { padding: 18px; }
      .startup-headline, .login-title { font-size: 2.2rem; }
      .model-library-hero,
      .agent-editor-header,
      .speech-settings-header,
      .speech-section-header,
      .whatsapp-header,
      .signal-header,
      .admin-command-head { flex-direction: column; align-items: stretch; }
      .brand-lockup { align-items: flex-start; }
      .brand-name { white-space: normal; }
      .admin-command-card {
        top:var(--admin-search-top);
        left:50%;
        right:auto;
        width:var(--admin-shell-width);
        margin:0;
        transform:translateX(-50%);
        z-index:1601;
      }
      .admin-search-icon{
        left:44px;
      }
      .admin-search-bar-row{align-items:stretch}
      .admin-search-field {
        padding-left:80px;
        padding-right:100px;
      }
      .admin-search-results { max-height: 42vh; }
      .speech-model-actions .admin-feature-btn,
      .speech-section-actions .admin-feature-btn,
      .speech-guide-actions .admin-feature-btn,
      .speech-form-actions .admin-feature-btn{
        flex:1 1 100%;
      }
      .speech-inline-metrics{
        display:grid;
        grid-template-columns:1fr;
        align-items:stretch;
      }
      .scheduler-queue-summary-grid{
        grid-template-columns:repeat(2, minmax(0, 1fr));
      }
      .scheduler-queue-toolbar{
        width:100%;
      }
    }
    @media (max-width: 480px) {
      :root{
        --admin-shell-width:calc(100vw - 20px);
        --admin-search-gap:94px;
        --admin-sections-sheet-top:calc(var(--admin-search-top) + 86px);
      }
      .container { max-width: 100%; padding: 0 10px 22px; }
      .container:has(.admin-dashboard-shell) { max-width:100%; padding: calc(var(--admin-search-top) + var(--admin-search-gap)) 10px 22px; }
      .card { padding: 16px; border-radius: 18px; }
      .admin-search-menu-btn{
        left:6px;
      }
      .admin-search-icon{
        left:40px;
      }
      .admin-mobile-sections-close{
        min-height:40px;
        padding:0 13px;
        font-size:.84rem;
      }
      .admin-mobile-sections-panel{
        padding:14px 12px;
        border-radius:20px;
      }
      .admin-mobile-sections-title{font-size:1rem}
      .admin-mobile-sections-panel .admin-nav-group-label{font-size:.64rem}
      .admin-search-field {
        padding-left:74px;
        padding-right:96px;
      }
      .admin-search-tools { right: 8px; gap: 6px; }
      .admin-search-reset { min-width: 30px; min-height: 30px; padding: 6px 8px; width: auto; }
      .admin-search-toggle { min-width: 30px; min-height: 30px; padding: 6px 8px; }
      .admin-jump-grid { gap: 8px; }
      .admin-jump-chip { width: 100%; justify-content: space-between; }
      .admin-nav-link{min-height:40px;padding:9px 10px;font-size:.88rem}
      .scheduler-queue-summary-grid{grid-template-columns:1fr}
      .scheduler-queue-toolbar .admin-feature-btn{width:100%}
    }

    /* Custom checkbox styling */
    .checkbox-container {
      display: flex;
      align-items: center;
      cursor: pointer;
      padding: 12px 0;
      margin-bottom: 0;
    }
    .checkbox-container input[type="checkbox"] {
      display: none;
    }
    /* Hide native radio if any remain; we render custom checkmarks only */
    .checkbox-container input[type="radio"] {
      display: none;
    }
    .checkmark {
      height: 20px;
      width: 20px;
      background-color: #0b1220;
      border: 2px solid #334155;
      border-radius: 4px;
      margin-right: 12px;
      position: relative;
      transition: all 0.2s ease;
    }
    .checkbox-container:hover .checkmark {
      border-color: var(--accent);
    }
    .checkbox-container input:checked ~ .checkmark {
      background-color: var(--accent);
      border-color: var(--accent);
    }
    .checkbox-container input:checked ~ .checkmark:after {
      content: "";
      position: absolute;
      display: block;
      left: 6px;
      top: 2px;
      width: 6px;
      height: 10px;
      border: solid #04100a;
      border-width: 0 2px 2px 0;
      transform: rotate(45deg);
    }
    .checkbox-label {
      font-weight: 500;
      color: var(--fg);
    }

    /* Restart button styling */
    .restart-btn {
      background: #3b82f6;
      color: white;
      border: none;
      padding: 8px 12px;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-size: 0.875rem;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: background-color 0.2s ease;
    }
    .restart-btn:hover {
      background: #2563eb;
    }
    .restart-btn:active {
      background: #1d4ed8;
    }

    /* Section header with restart button (WhatsApp/Signal) */
    .whatsapp-header, .signal-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
    }

    /* Restart icon */
    .restart-icon {
      width: 14px;
      height: 14px;
      fill: currentColor;
    }

    /* Modal styles */
    .modal {
      display: none;
      position: fixed;
      z-index: 1000;
      left: 0;
      top: 0;
      width: 100%;
      height: 100%;
      background-color: rgba(0, 0, 0, 0.5);
      justify-content: center;
      align-items: center;
    }

    .modal-content {
      background-color: #1f2937;
      border: 1px solid #374151;
      border-radius: 8px;
      padding: 24px;
      max-width: 400px;
      width: 90%;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.3);
    }

    .modal-header {
      display: flex;
      align-items: center;
      margin-bottom: 16px;
      color: #f59e0b;
    }

    .modal-header svg {
      width: 24px;
      height: 24px;
      margin-right: 8px;
      fill: currentColor;
    }

    .modal-title {
      font-size: 1.125rem;
      font-weight: 600;
      margin: 0;
    }

    .modal-body {
      margin-bottom: 20px;
      color: #d1d5db;
      line-height: 1.5;
    }

    .modal-list {
      margin: 8px 0;
      padding-left: 20px;
    }

    .modal-list li + li {
      margin-top: 4px;
    }

    .modal-warning-text {
      color: #f59e0b;
      font-weight: 600;
    }

    .modal-actions {
      display: flex;
      gap: 12px;
      justify-content: flex-end;
    }

    .modal-btn {
      padding: 8px 16px;
      border: none;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      font-size: 0.875rem;
      transition: background-color 0.2s ease;
    }

    .modal-btn-cancel {
      background: #374151;
      color: #d1d5db;
    }

    .modal-btn-cancel:hover {
      background: #4b5563;
    }

    .modal-btn-confirm {
      background: #dc2626;
      color: white;
    }

    .modal-btn-confirm:hover {
      background: #b91c1c;
    }
    .hidden{display:none !important}
    .login-shell{display:flex;flex-direction:column;gap:18px}
    .login-topbar{display:flex;justify-content:space-between;align-items:center;gap:16px}
    .login-brand{display:flex;align-items:center;min-width:0}
    .login-panel{position:relative;overflow:hidden;padding:34px;border-radius:28px;border:1px solid rgba(59,130,246,.16);background:
      radial-gradient(circle at top left,rgba(14,165,233,.08),transparent 30%),
      linear-gradient(180deg,rgba(7,14,28,.98),rgba(5,10,20,.98));box-shadow:0 24px 72px rgba(2,6,23,.42)}
    .login-panel:before{content:"";position:absolute;inset:0 auto auto 0;width:320px;height:320px;background:radial-gradient(circle,rgba(34,197,94,.10),transparent 72%);pointer-events:none}
    .login-hero{position:relative;z-index:1;display:flex;justify-content:center}
    .login-copy{width:min(720px,100%);display:flex;flex-direction:column;gap:18px;min-width:0}
    .login-intro{display:flex;flex-direction:column;gap:12px}
    .eyebrow{font-size:.78rem;text-transform:uppercase;letter-spacing:.18em;color:#7dd3fc}
    .login-title{font-size:clamp(3rem,6vw,4.15rem);line-height:.92;letter-spacing:-.06em;margin:0;max-width:9.5ch}
    .login-subtitle{font-size:1.04rem;line-height:1.72;max-width:39ch;color:#b7c8df;margin:0}
    .login-form-shell,.login-sequence-card{display:flex;flex-direction:column;gap:14px;padding:22px;border-radius:24px;border:1px solid rgba(56,189,248,.14);background:linear-gradient(180deg,rgba(7,14,26,.92),rgba(5,11,21,.96));box-shadow:inset 0 1px 0 rgba(255,255,255,.02)}
    .login-form-shell label{margin-bottom:10px;font-size:.97rem;font-weight:600;color:#f8fafc}
    .login-input{font-size:1.05rem;padding:16px 18px;border-radius:16px;border:1px solid #21334b;background:#07111f;box-shadow:inset 0 1px 0 rgba(255,255,255,.03)}
    .login-input:focus{outline:none;border-color:#60a5fa;box-shadow:0 0 0 4px rgba(59,130,246,.16)}
    .login-actions{display:flex;align-items:center;gap:16px;margin-top:16px}
    .login-submit-btn{display:inline-flex;align-items:center;justify-content:center;min-height:52px;padding:0 22px;border-radius:16px;background:linear-gradient(135deg,#22c55e,#34d399);box-shadow:0 14px 32px rgba(34,197,94,.22);font-size:1rem;letter-spacing:-.01em;transition:transform .18s ease, box-shadow .18s ease}
    .login-submit-btn:hover{transform:translateY(-1px);box-shadow:0 18px 36px rgba(34,197,94,.28)}
    .login-submit-btn:disabled{transform:none;box-shadow:none;opacity:.72;cursor:not-allowed}
    .login-hint{font-size:.94rem;line-height:1.6;color:var(--muted);max-width:26ch}
    .login-inline-note{display:flex;align-items:flex-start;gap:10px;padding:12px 14px;border-radius:16px;border:1px solid rgba(96,165,250,.14);background:rgba(5,13,25,.65);color:#dbeafe;font-size:.92rem;line-height:1.55}
    .login-note-dot{width:9px;height:9px;border-radius:999px;background:#38bdf8;box-shadow:0 0 16px rgba(56,189,248,.55);margin-top:4px;flex:0 0 auto}
    .status-message{margin-top:12px;padding:12px 14px;border-radius:10px;border:1px solid #3f1d1d;background:#2a1212;color:#fecaca}
    .login-sequence-intro{margin:0;color:#9fb3cf;line-height:1.6;max-width:48ch}
    .login-sequence-list{list-style:none;margin:0;padding:0;display:grid;gap:10px}
    .sequence-item{display:grid;grid-template-columns:34px minmax(0,1fr);gap:12px;align-items:flex-start}
    .sequence-num{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:12px;background:rgba(56,189,248,.12);border:1px solid rgba(96,165,250,.14);color:#bae6fd;font-size:.88rem;font-weight:700}
    .sequence-text{color:#d7e5f7;line-height:1.55}
    .startup-overlay{position:fixed;inset:0;z-index:1400;display:flex;align-items:center;justify-content:center;padding:24px;background:radial-gradient(circle at top,rgba(14,165,233,.18),rgba(2,6,23,.94) 48%),rgba(2,6,23,.96);backdrop-filter:blur(12px)}
    .startup-panel{width:min(920px,100%);max-height:min(92vh,900px);overflow:auto;background:linear-gradient(180deg,rgba(8,15,28,.96),rgba(15,23,42,.96));border:1px solid rgba(56,189,248,.22);border-radius:28px;padding:24px;box-shadow:0 24px 80px rgba(0,0,0,.42)}
    .startup-grid{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(260px,.8fr);gap:18px}
    .startup-header{display:flex;gap:18px;align-items:flex-start;margin-bottom:18px}
    .startup-spinner{width:68px;height:68px;border-radius:999px;border:6px solid rgba(34,197,94,.12);border-top-color:#22c55e;border-right-color:#7dd3fc;animation:spin 1s linear infinite;flex:0 0 auto}
    .startup-headline{font-size:2rem;line-height:1.02;letter-spacing:-.04em;margin:4px 0 8px}
    .progress-track{height:12px;background:#09101c;border:1px solid #1e2b43;border-radius:999px;overflow:hidden}
    .progress-fill{height:100%;width:8%;background:linear-gradient(90deg,#22c55e,#7dd3fc);border-radius:999px;transition:width .35s ease}
    .startup-meta{display:flex;justify-content:space-between;align-items:center;margin-top:10px;color:var(--muted);font-size:.9rem}
    .startup-log-shell{margin-top:16px;padding:14px;border-radius:18px;background:#070d17;border:1px solid #162033}
    .startup-log-header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:12px}
    .startup-log-title{font-size:.94rem;font-weight:700;color:#dbeafe}
    .startup-log-caption{font-size:.82rem;color:#7a90ae}
    .startup-console{padding:2px 0 0;min-height:180px;max-height:250px;overflow:auto;color:#c7d2fe;font-size:.88rem}
    .console-line{display:flex;gap:10px;align-items:flex-start;margin-bottom:8px}
    .console-stamp{color:#7dd3fc;flex:0 0 auto}
    .console-text{color:#dbeafe}
    #minigame-card{margin:16px auto}
    .boot-sweep-card{background:linear-gradient(180deg,rgba(9,16,28,.98),rgba(7,12,21,.94));border:1px solid #1e2b43;border-radius:20px;padding:18px;min-height:0;display:flex;flex-direction:column;gap:12px}
    .boot-sweep-copy{font-size:.93rem;line-height:1.58;color:#b6c6dc;margin:0}
    .boot-sweep-board{position:relative;height:240px;border-radius:18px;overflow:hidden;background:
      linear-gradient(180deg,rgba(8,17,31,.98),rgba(3,7,18,.98)),
      radial-gradient(circle at center,rgba(14,165,233,.12),transparent 50%);touch-action:manipulation}
    .boot-sweep-board:before{content:"";position:absolute;inset:0;background:
      linear-gradient(rgba(125,211,252,.05) 1px,transparent 1px),
      linear-gradient(90deg,rgba(125,211,252,.05) 1px,transparent 1px);background-size:26px 26px;opacity:.35}
    .boot-sweep-score{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .score-pill{padding:12px 14px;border-radius:16px;border:1px solid rgba(96,165,250,.14);background:rgba(7,14,27,.74);color:#dbeafe}
    .score-pill strong{display:block;font-size:1.06rem;color:#f8fafc}
    .score-pill span{display:block;margin-top:4px;font-size:.84rem;color:#8fa6c6}
    .boot-sweep-note{font-size:.84rem;line-height:1.55;color:#90a5c3}
    .boot-sweep-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
    .boot-sweep-actions button{min-height:38px;border-radius:12px;font-size:.9rem}
    .boot-sweep-status{min-height:24px;font-size:.88rem;color:#7dd3fc;margin-top:4px}
    .boot-sweep-history{display:grid;gap:10px;padding:14px;border-radius:18px;border:1px solid rgba(96,165,250,.14);background:rgba(7,14,27,.56)}
    .boot-sweep-history-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap}
    .boot-sweep-history-head strong{font-size:.95rem;color:#f8fafc}
    .boot-sweep-history-head span{font-size:.84rem;line-height:1.5;color:#90a5c3}
    .boot-sweep-table-shell{overflow:auto;border-radius:16px;border:1px solid rgba(71,85,105,.42);background:rgba(5,10,20,.62)}
    .boot-sweep-table{width:100%;min-width:520px;border-collapse:collapse}
    .boot-sweep-table th,.boot-sweep-table td{padding:11px 14px;text-align:left}
    .boot-sweep-table th{font-size:.76rem;letter-spacing:.08em;text-transform:uppercase;color:#8fa6c6;background:rgba(9,16,28,.96)}
    .boot-sweep-table td{font-size:.9rem;color:#dbeafe}
    .boot-sweep-table tbody tr + tr td{border-top:1px solid rgba(71,85,105,.28)}
    .boot-sweep-empty-row td{text-align:center;color:#90a5c3}
    .boot-sweep-node{position:absolute;width:16px;height:16px;border:none;padding:0;margin:0;border-radius:999px;background:radial-gradient(circle,#dbeafe 0,#7dd3fc 55%,rgba(56,189,248,.18) 100%);box-shadow:0 0 18px rgba(56,189,248,.5);cursor:pointer;animation:sweepPulse 1.2s ease-in-out infinite;touch-action:manipulation}
    .boot-sweep-node:after{content:"";position:absolute;inset:-12px;border-radius:999px;border:1px solid rgba(34,197,94,.2)}
    .totp-generated-card{border:1px solid rgba(34,197,94,.32);background:rgba(9,30,18,.58)}
    .totp-qr-card{margin-top:10px;border:1px solid rgba(71,85,105,.62);background:rgba(15,23,42,.62)}
    .totp-qr-meta{margin:0 0 8px 0;display:flex;flex-wrap:wrap;gap:8px 10px;align-items:center}
    .totp-qr-issuer{color:var(--muted);font-size:.85em}
    .totp-auto-badge{color:var(--muted);font-size:.85em}
    .totp-qr-image{display:block;max-width:200px;width:100%;height:auto;padding:4px;border-radius:10px;border:1px solid rgba(71,85,105,.65);background:#ffffff}
    .totp-qr-details{margin-top:10px}
    .totp-client-list-card h4{margin-bottom:8px}
    .totp-client-row,.totp-alias-row{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
    .totp-client-row{padding:10px 0;border-bottom:1px solid rgba(42,42,42,.95)}
    .totp-client-row:last-child{border-bottom:0}
    .totp-client-main{display:grid;gap:2px}
    .totp-client-actions{display:flex;gap:8px;flex-shrink:0;flex-wrap:wrap}
    .totp-alias-group{margin-left:4px;border-left:3px solid rgba(71,85,105,.7);border-radius:0 14px 14px 0;background:rgba(17,24,39,.66)}
    .totp-alias-row{padding:8px 0 8px 20px;border-bottom:1px solid rgba(34,34,34,.95)}
    .totp-alias-row:last-child{border-bottom:0}
    .totp-alias-client{font-size:.9em;color:#cbd5e1}
    .totp-alias-badge{display:inline-flex;align-items:center;margin-left:8px;padding:2px 8px;border-radius:999px;border:1px solid rgba(71,85,105,.75);background:rgba(15,23,42,.56);font-size:.78em;color:var(--muted)}
    .totp-empty-card{font-style:italic}
    :root[data-theme="light"] input,
    :root[data-theme="light"] textarea,
    :root[data-theme="light"] select{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
    }
    :root[data-theme="light"] input::placeholder,
    :root[data-theme="light"] textarea::placeholder{color:#64748b}
    :root[data-theme="light"] button{
      color:#ffffff;
    }
    :root[data-theme="light"] .primary-btn{color:#ffffff}
    :root[data-theme="light"] .secondary-btn,
    :root[data-theme="light"] .ghost,
    :root[data-theme="light"] .admin-jump-chip,
    :root[data-theme="light"] .admin-search-menu-btn,
    :root[data-theme="light"] .admin-search-toggle,
    :root[data-theme="light"] .admin-search-reset,
    :root[data-theme="light"] .restart-btn,
    :root[data-theme="light"] .modal-btn-cancel{
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
      color:#1d4ed8;
      border-color:#93c5fd;
      box-shadow:0 8px 18px rgba(59,130,246,.14);
    }
    :root[data-theme="light"] .danger-btn,
    :root[data-theme="light"] .modal-btn-confirm{color:#ffffff}
    :root[data-theme="light"] .card{
      background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,250,252,.96));
      box-shadow:0 18px 36px rgba(148,163,184,.18), inset 0 1px 0 rgba(255,255,255,.8);
    }
    :root[data-theme="light"] .container > .card .card{
      background:linear-gradient(180deg, rgba(248,250,252,.98), rgba(241,245,249,.94));
    }
    :root[data-theme="light"] .container > .card .card .card{
      background:rgba(255,255,255,.94);
    }
    :root[data-theme="light"] .admin-command-card{
      background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(241,245,249,.96));
      border-color:rgba(37,99,235,.24);
      box-shadow:0 22px 44px rgba(148,163,184,.22), inset 0 1px 0 rgba(255,255,255,.9);
    }
    :root[data-theme="light"] .admin-search-panel{
      background:linear-gradient(180deg, rgba(255,255,255,.99), rgba(248,250,252,.96));
      border-color:rgba(148,163,184,.28);
      box-shadow:0 18px 34px rgba(148,163,184,.14), inset 0 1px 0 rgba(255,255,255,.92);
    }
    :root[data-theme="light"] .admin-nav-group-label{
      color:#475569;
    }
    :root[data-theme="light"] .admin-nav-link,
    :root[data-theme="light"] .admin-mobile-sections-close{
      background:linear-gradient(180deg, rgba(255,255,255,.94), rgba(239,246,255,.9));
      border-color:rgba(148,163,184,.34);
      color:#0f172a;
      box-shadow:0 10px 22px rgba(148,163,184,.12), inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .admin-mobile-sections-panel{
      background:
        linear-gradient(160deg, rgba(255,255,255,.99), rgba(241,245,249,.96)),
        radial-gradient(circle at top, rgba(96,165,250,.16), transparent 52%);
      border-color:rgba(148,163,184,.32);
      box-shadow:0 28px 56px rgba(148,163,184,.24), inset 0 1px 0 rgba(255,255,255,.92);
    }
    :root[data-theme="light"] .admin-mobile-sections-title{
      color:#0f172a;
    }
    :root[data-theme="light"] .admin-mobile-sections-backdrop{
      background:rgba(226,232,240,.56);
    }
    :root[data-theme="light"] .help-guide-link,
    :root[data-theme="light"] .help-faq-item{
      background:linear-gradient(180deg,#ffffff,#eff6ff);
      border-color:#bfdbfe;
      color:#1d4ed8;
      box-shadow:0 8px 18px rgba(59,130,246,.08);
    }
    :root[data-theme="light"] .admin-nav-link:hover,
    :root[data-theme="light"] .admin-nav-link.active,
    :root[data-theme="light"] .admin-search-menu-btn:hover,
    :root[data-theme="light"] .admin-search-menu-btn.is-open,
    :root[data-theme="light"] .admin-mobile-sections-close:hover{
      border-color:#93c5fd;
      color:#1d4ed8;
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
    }
    :root[data-theme="light"] .help-guide-link:hover{
      border-color:#60a5fa;
      color:#111827;
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
    }
    :root[data-theme="light"] .brand-kicker,
    :root[data-theme="light"] .admin-search-result-section,
    :root[data-theme="light"] .boot-sweep-status{color:#2563eb}
    :root[data-theme="light"] .brand-name,
    :root[data-theme="light"] .login-title,
    :root[data-theme="light"] .startup-headline{color:#111827}
    :root[data-theme="light"] .eyebrow{
      color:#111827;
      font-weight:800;
    }
    :root[data-theme="light"] .modal-content{
      background:#ffffff;
      border-color:#cbd5e1;
    }
    :root[data-theme="light"] .modal-body{color:#334155}
    :root[data-theme="light"] .modal-warning-text{color:#b45309}
    :root[data-theme="light"] .login-panel,
    :root[data-theme="light"] .startup-panel,
    :root[data-theme="light"] .login-form-shell,
    :root[data-theme="light"] .login-sequence-card,
    :root[data-theme="light"] .boot-sweep-card,
    :root[data-theme="light"] .startup-log-shell{
      background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,250,252,.96));
      border-color:rgba(148,163,184,.28);
    }
    :root[data-theme="light"] .status-message{
      background:#fee2e2;
      border-color:#fecaca;
      color:#991b1b;
    }
    :root[data-theme="light"] .login-form-shell label,
    :root[data-theme="light"] .login-subtitle,
    :root[data-theme="light"] .login-hint,
    :root[data-theme="light"] .login-sequence-intro,
    :root[data-theme="light"] .startup-panel .muted,
    :root[data-theme="light"] .startup-meta,
    :root[data-theme="light"] .console-stamp{
      color:#111827;
    }
    :root[data-theme="light"] .login-inline-note{
      background:linear-gradient(180deg, rgba(239,246,255,.98), rgba(219,234,254,.9));
      border-color:rgba(96,165,250,.28);
      color:#111827;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .login-note-dot{
      background:#2563eb;
      box-shadow:0 0 12px rgba(37,99,235,.24);
    }
    :root[data-theme="light"] .sequence-num{
      background:rgba(219,234,254,.92);
      border-color:rgba(96,165,250,.28);
      color:#111827;
    }
    :root[data-theme="light"] .progress-track{
      background:#e2e8f0;
      border-color:#cbd5e1;
    }
    :root[data-theme="light"] .success{
      background:#eff6ff;
      border-color:#bfdbfe;
      color:#1d4ed8;
    }
    :root[data-theme="light"] .warn{
      background:#fef3c7;
      border-color:#fcd34d;
      color:#78350f;
    }
    :root[data-theme="light"] .danger{
      background:#fee2e2;
      border-color:#fecaca;
      color:#991b1b;
    }
    :root[data-theme="light"] .boot-sweep-board{
      background:
        linear-gradient(180deg, rgba(241,245,249,.98), rgba(226,232,240,.98)),
        radial-gradient(circle at center, rgba(37,99,235,.10), transparent 50%);
    }
    :root[data-theme="light"] .boot-sweep-history{
      background:rgba(255,255,255,.96);
      border-color:rgba(148,163,184,.36);
    }
    :root[data-theme="light"] .boot-sweep-history-head strong,
    :root[data-theme="light"] .boot-sweep-table td{
      color:#0f172a;
    }
    :root[data-theme="light"] .boot-sweep-history-head span,
    :root[data-theme="light"] .boot-sweep-note,
    :root[data-theme="light"] .boot-sweep-table th,
    :root[data-theme="light"] .boot-sweep-empty-row td{
      color:#475569;
    }
    :root[data-theme="light"] .boot-sweep-table-shell{
      background:rgba(248,250,252,.98);
      border-color:rgba(148,163,184,.42);
    }
    :root[data-theme="light"] .boot-sweep-table th{
      background:rgba(241,245,249,.98);
    }
    :root[data-theme="light"] .score-pill,
    :root[data-theme="light"] .startup-console,
    :root[data-theme="light"] .admin-search-result,
    :root[data-theme="light"] .admin-search-empty{
      background:rgba(255,255,255,.92);
      color:#111827;
      border-color:rgba(148,163,184,.4);
    }
    :root[data-theme="light"] .console-text,
    :root[data-theme="light"] .login-inline-note,
    :root[data-theme="light"] .sequence-text,
    :root[data-theme="light"] .startup-log-title,
    :root[data-theme="light"] .startup-log-caption,
    :root[data-theme="light"] .boot-sweep-copy,
    :root[data-theme="light"] .boot-sweep-note,
    :root[data-theme="light"] .score-pill strong,
    :root[data-theme="light"] .score-pill span,
    :root[data-theme="light"] .admin-search-result-title{
      color:#111827;
    }
    :root[data-theme="light"] [style*='background: #0f172a'],
    :root[data-theme="light"] [style*='background:#0f172a']{
      background:#f8fafc !important;
      border-color:#cbd5e1 !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='background: #1f2937'],
    :root[data-theme="light"] [style*='background:#1f2937']{
      background:#eef2f7 !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='background: #111827'],
    :root[data-theme="light"] [style*='background:#111827']{
      background:#ffffff !important;
      border-color:#cbd5e1 !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='color: #f8fafc'],
    :root[data-theme="light"] [style*='color:#f8fafc']{
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='color: #94a3b8'],
    :root[data-theme="light"] [style*='color:#94a3b8'],
    :root[data-theme="light"] [style*='color: #d1d5db'],
    :root[data-theme="light"] [style*='color:#d1d5db']{
      color:#475569 !important;
    }
    :root[data-theme="light"] .header-action-btn.secondary-btn{
      background:#6b7280;
      border-color:#6b7280;
      color:#ffffff;
    }
    :root[data-theme="light"] .header-action-btn.secondary-btn:hover{
      background:#4b5563;
      border-color:#4b5563;
    }
    :root[data-theme="light"] .wizard-launcher-action.secondary-btn{
      background:linear-gradient(180deg,#2563eb,#1d4ed8);
      border-color:#1d4ed8;
      color:#ffffff;
      box-shadow:0 12px 24px rgba(37,99,235,.18);
    }
    :root[data-theme="light"] .wizard-launcher-action.secondary-btn:hover{
      background:linear-gradient(180deg,#1d4ed8,#1e40af);
      border-color:#1e40af;
    }
    :root[data-theme="light"] .checkmark{
      background:#ffffff;
      border-color:#cbd5e1;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.9);
    }
    :root[data-theme="light"] .checkbox-container:hover .checkmark{
      border-color:#94a3b8;
    }
    :root[data-theme="light"] .checkbox-container input:checked ~ .checkmark{
      background:#e5e7eb;
      border-color:#9ca3af;
    }
    :root[data-theme="light"] .checkbox-container input:checked ~ .checkmark:after{
      border-color:#111827;
    }
    :root[data-theme="light"] .wizard-kicker,
    :root[data-theme="light"] .model-library-kicker{
      background:#dbeafe;
      color:#111827;
      border:1px solid rgba(96,165,250,.3);
      font-weight:800;
    }
    :root[data-theme="light"] .setup-pill,
    :root[data-theme="light"] .wizard-mini-card,
    :root[data-theme="light"] .partner-card,
    :root[data-theme="light"] .wizard-choice-card,
    :root[data-theme="light"] .wizard-inline-checkbox,
    :root[data-theme="light"] .provider-panel,
    :root[data-theme="light"] .provider-summary-bar,
    :root[data-theme="light"] .speech-summary-card,
    :root[data-theme="light"] .speech-surface-card,
    :root[data-theme="light"] .speech-stt-library,
    :root[data-theme="light"] .speech-job-card,
    :root[data-theme="light"] .rtc-import-card{
      background:linear-gradient(180deg, rgba(239,246,255,.98), rgba(219,234,254,.9));
      border-color:rgba(96,165,250,.24);
      box-shadow:0 10px 24px rgba(148,163,184,.12), inset 0 1px 0 rgba(255,255,255,.86);
    }
    :root[data-theme="light"] .download-job,
    :root[data-theme="light"] .model-runtime-card,
    :root[data-theme="light"] .model-browser-panel,
    :root[data-theme="light"] .model-search-shell,
    :root[data-theme="light"] .model-cloud-toggle,
    :root[data-theme="light"] .model-detail-card,
    :root[data-theme="light"] .installed-models-card,
    :root[data-theme="light"] .local-model-card,
    :root[data-theme="light"] .model-empty-state,
    :root[data-theme="light"] .rtc-settings-shell,
    :root[data-theme="light"] .agent-editor-card,
    :root[data-theme="light"] .agent-editor-shell,
    :root[data-theme="light"] .agent-structure-bar,
    :root[data-theme="light"] .agent-section-card,
    :root[data-theme="light"] .agent-section-card.readonly,
    :root[data-theme="light"] .agent-section-empty{
      background:linear-gradient(180deg, rgba(249,250,251,.98), rgba(241,245,249,.96));
      border-color:rgba(148,163,184,.3);
      box-shadow:0 10px 22px rgba(148,163,184,.1), inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .model-library-card,
    :root[data-theme="light"] .setup-hero-card{
      background:linear-gradient(180deg, rgba(255,255,255,.99), rgba(248,250,252,.98));
      border-color:rgba(96,165,250,.2);
    }
    :root[data-theme="light"] .wizard-note,
    :root[data-theme="light"] .wizard-callout{
      background:linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,250,252,.96));
      color:#111827;
      border-color:rgba(148,163,184,.28);
    }
    :root[data-theme="light"] .wizard-lede,
    :root[data-theme="light"] .model-library-subtitle,
    :root[data-theme="light"] .speech-settings-subtitle,
    :root[data-theme="light"] .agent-editor-title p,
    :root[data-theme="light"] .speech-section-title p,
    :root[data-theme="light"] .setup-pill span,
    :root[data-theme="light"] .wizard-mini-card span,
    :root[data-theme="light"] .partner-card span,
    :root[data-theme="light"] .wizard-choice-card span,
    :root[data-theme="light"] .model-runtime-card span,
    :root[data-theme="light"] .model-panel-heading p,
    :root[data-theme="light"] .local-model-copy,
    :root[data-theme="light"] .model-cloud-toggle-copy small,
    :root[data-theme="light"] .agent-structure-copy span,
    :root[data-theme="light"] .agent-section-head p,
    :root[data-theme="light"] .agent-editor-caption,
    :root[data-theme="light"] .speech-summary-copy,
    :root[data-theme="light"] .speech-summary-caption,
    :root[data-theme="light"] .speech-summary-card .muted,
    :root[data-theme="light"] .speech-surface-card .muted,
    :root[data-theme="light"] .speech-stt-library .muted,
    :root[data-theme="light"] .speech-job-card .muted,
    :root[data-theme="light"] .wizard-inline-checkbox span,
    :root[data-theme="light"] .rtc-settings-shell .muted{
      color:#111827 !important;
    }
    :root[data-theme="light"] .setup-pill strong,
    :root[data-theme="light"] .wizard-mini-card strong,
    :root[data-theme="light"] .partner-card strong,
    :root[data-theme="light"] .wizard-choice-card strong,
    :root[data-theme="light"] .model-runtime-card strong,
    :root[data-theme="light"] .model-panel-heading h3,
    :root[data-theme="light"] .model-result-title,
    :root[data-theme="light"] .local-model-title,
    :root[data-theme="light"] .agent-editor-title h3,
    :root[data-theme="light"] .speech-settings-shell h2,
    :root[data-theme="light"] .speech-settings-shell h3,
    :root[data-theme="light"] .speech-section-title h3,
    :root[data-theme="light"] .agent-structure-copy strong,
    :root[data-theme="light"] .agent-section-head strong,
    :root[data-theme="light"] .speech-summary-lead,
    :root[data-theme="light"] .model-cloud-toggle-copy strong{
      color:#111827 !important;
      font-weight:800;
    }
    :root[data-theme="light"] .wizard-inline-checkbox strong,
    :root[data-theme="light"] .rtc-settings-shell h2,
    :root[data-theme="light"] .rtc-import-card label,
    :root[data-theme="light"] .rtc-settings-shell label{
      color:#111827 !important;
    }
    :root[data-theme="light"] .wiz-prov-tile-name,
    :root[data-theme="light"] .wiz-ollama-quick-title{
      color:#111827 !important;
    }
    :root[data-theme="light"] .wiz-prov-tile-desc{
      color:#475569 !important;
    }
    :root[data-theme="light"] .wizard-choice-card a,
    :root[data-theme="light"] .wizard-link{
      color:#1d4ed8;
    }
    :root[data-theme="light"] .setup-wizard-overlay{
      background:rgba(255,255,255,.9);
      backdrop-filter:blur(16px);
    }
    :root[data-theme="light"] .setup-wizard-frame{
      background:linear-gradient(180deg, rgba(255,255,255,.995), rgba(248,250,252,.985));
      border-color:rgba(203,213,225,.92);
      box-shadow:0 28px 72px rgba(148,163,184,.2), inset 0 1px 0 rgba(255,255,255,.9);
    }
    :root[data-theme="light"] .setup-wizard-sidebar{
      background:linear-gradient(180deg, rgba(255,255,255,.995), rgba(248,250,252,.985));
      border-color:rgba(203,213,225,.92) !important;
    }
    :root[data-theme="light"] .setup-wizard-main{
      background:#ffffff;
    }
    :root[data-theme="light"] .setup-wizard-overlay h2,
    :root[data-theme="light"] .setup-wizard-overlay strong{
      color:#111827;
      font-weight:800;
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-lede,
    :root[data-theme="light"] .setup-wizard-overlay .muted,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-progress-label,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-progress-label span,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-status-line,
    :root[data-theme="light"] .setup-wizard-overlay .setup-pill span,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-mini-card span,
    :root[data-theme="light"] .setup-wizard-overlay .partner-card span,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-choice-card span,
    :root[data-theme="light"] .setup-wizard-overlay .download-job span,
    :root[data-theme="light"] .setup-wizard-overlay .wiz-prov-tile-name,
    :root[data-theme="light"] .setup-wizard-overlay .wiz-prov-tile-desc,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-inline-shell-title{
      color:#111827 !important;
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-step-btn{
      background:#ffffff;
      border-color:#dbe3ee;
      color:#111827;
      font-weight:700;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-step-btn:hover{
      background:#eff6ff;
      border-color:#93c5fd;
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-step-btn.active{
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
      border-color:#93c5fd;
      color:#1e3a8a;
      box-shadow:0 10px 24px rgba(37,99,235,.12);
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-step-btn.completed:not(.active){
      background:linear-gradient(180deg,#ecfdf5,#dcfce7);
      border-color:#86efac;
      color:#166534;
      box-shadow:0 8px 18px rgba(34,197,94,.12);
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-progress-track,
    :root[data-theme="light"] .setup-wizard-overlay .job-progress-track{
      background:#e5e7eb;
      border-color:#cbd5e1;
    }
    :root[data-theme="light"] .setup-wizard-overlay .setup-pill,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-mini-card,
    :root[data-theme="light"] .setup-wizard-overlay .partner-card,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-choice-card,
    :root[data-theme="light"] .setup-wizard-overlay .download-job,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-note,
    :root[data-theme="light"] .setup-wizard-overlay .wiz-prov-tile,
    :root[data-theme="light"] .setup-wizard-overlay .wizard-inline-shell{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#dbe3ee;
      box-shadow:0 10px 24px rgba(148,163,184,.1), inset 0 1px 0 rgba(255,255,255,.9);
      color:#111827;
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-callout{
      background:linear-gradient(180deg,#ffffff,#fff7ed);
      border-color:#fdba74;
      color:#111827;
      box-shadow:0 10px 24px rgba(251,146,60,.12), inset 0 1px 0 rgba(255,255,255,.9);
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-badge{
      background:#ffffff;
      border-color:#bfdbfe;
      color:#111827;
    }
    :root[data-theme="light"] .setup-wizard-overlay .wizard-badge.premium{
      background:#faf5ff;
      border-color:#e9d5ff;
      color:#6b21a8;
    }
    :root[data-theme="light"] .setup-wizard-overlay code{
      background:#eff6ff;
      border:1px solid #bfdbfe;
      border-radius:8px;
      color:#1d4ed8;
      padding:2px 6px;
    }
    :root[data-theme="light"] .model-toolbar-btn,
    :root[data-theme="light"] .model-action-btn,
    :root[data-theme="light"] .admin-feature-btn{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#bfdbfe;
      color:#111827;
      box-shadow:0 10px 22px rgba(148,163,184,.14);
    }
    :root[data-theme="light"] .model-toolbar-btn.primary,
    :root[data-theme="light"] .model-action-btn.primary,
    :root[data-theme="light"] .admin-feature-btn.primary{
      background:linear-gradient(180deg,#2563eb,#1d4ed8);
      border-color:#1d4ed8;
      color:#ffffff;
      box-shadow:0 14px 28px rgba(37,99,235,.22);
    }
    :root[data-theme="light"] .model-toolbar-btn.ghost,
    :root[data-theme="light"] .model-action-btn.ghost,
    :root[data-theme="light"] .admin-feature-btn.ghost{
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
      border-color:#93c5fd;
      color:#1d4ed8;
    }
    :root[data-theme="light"] .model-toolbar-btn.alt,
    :root[data-theme="light"] .admin-feature-btn.alt{
      background:linear-gradient(180deg,#dbeafe,#bfdbfe);
      border-color:#93c5fd;
      color:#1e3a8a;
    }
    :root[data-theme="light"] .model-toolbar-meta,
    :root[data-theme="light"] .model-toolbar-btn.primary .model-toolbar-meta{
      color:inherit;
    }
    :root[data-theme="light"] .model-source-tabs{
      background:#ffffff;
      border-color:#cbd5e1;
    }
    :root[data-theme="light"] .model-source-tab{
      color:#334155;
    }
    :root[data-theme="light"] .model-source-tab.active{
      background:linear-gradient(180deg,#dbeafe,#bfdbfe);
      border-color:#93c5fd;
      color:#1d4ed8 !important;
      box-shadow:0 0 0 1px rgba(96,165,250,.18) inset;
    }
    :root[data-theme="light"] .model-result-card{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#cbd5e1;
      box-shadow:0 10px 22px rgba(148,163,184,.12), inset 0 1px 0 rgba(255,255,255,.88);
      color:#111827;
    }
    :root[data-theme="light"] .model-result-card.active{
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
      border-color:#60a5fa;
      box-shadow:0 0 0 1px rgba(96,165,250,.2), 0 18px 30px rgba(148,163,184,.14);
    }
    :root[data-theme="light"] .model-result-summary,
    :root[data-theme="light"] .model-meta-row{
      color:#334155;
    }
    :root[data-theme="light"] .model-meta-row span{
      color:#334155;
      background:#eef2f7;
      border-color:#dbe3ee;
    }
    :root[data-theme="light"] .model-chip{
      background:#eef2f7;
      border-color:#dbe3ee;
      color:#111827;
    }
    :root[data-theme="light"] .model-chip.cloud{
      background:#ede9fe;
      border-color:#ddd6fe;
      color:#5b21b6;
    }
    :root[data-theme="light"] .model-chip.local{
      background:#dcfce7;
      border-color:#bbf7d0;
      color:#166534;
    }
    :root[data-theme="light"] .model-chip.selected{
      background:#dbeafe;
      border-color:#93c5fd;
      color:#1d4ed8;
    }
    :root[data-theme="light"] .model-search-shell input[type='search']{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .model-search-shell input[type='search']::placeholder{
      color:#64748b;
    }
    :root[data-theme="light"] .model-installed-shell,
    :root[data-theme="light"] .model-inline-detail,
    :root[data-theme="light"] .help-faq-shell{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#dbe3ee;
      box-shadow:0 14px 28px rgba(148,163,184,.12), inset 0 1px 0 rgba(255,255,255,.92);
    }
    :root[data-theme="light"] .model-installed-header h3,
    :root[data-theme="light"] .help-faq-head h3,
    :root[data-theme="light"] .help-guide-link strong,
    :root[data-theme="light"] .help-faq-item summary{
      color:#111827;
    }
    :root[data-theme="light"] .model-installed-header p,
    :root[data-theme="light"] .help-guide-link span,
    :root[data-theme="light"] .help-faq-head p,
    :root[data-theme="light"] .help-faq-answer{
      color:#334155;
    }
    :root[data-theme="light"] .local-model-card.selected{
      background:linear-gradient(180deg,#eff6ff,#dbeafe);
      border-color:#93c5fd;
      box-shadow:0 0 0 1px rgba(96,165,250,.18), 0 14px 26px rgba(148,163,184,.14);
    }
    :root[data-theme="light"] .agent-card-shell{
      background:linear-gradient(180deg, rgba(243,244,246,.98), rgba(229,231,235,.92));
      border-color:#cbd5e1;
      box-shadow:0 10px 22px rgba(148,163,184,.14);
    }
    :root[data-theme="light"] .agent-card-name,
    :root[data-theme="light"] .agent-card-desc,
    :root[data-theme="light"] .agent-card-meta,
    :root[data-theme="light"] .agent-card-meta strong{
      color:#111827 !important;
    }
    :root[data-theme="light"] .agent-card-subname{
      color:#475569;
    }
    :root[data-theme="light"] .agent-badge-installed,
    :root[data-theme="light"] .agent-badge-enabled,
    :root[data-theme="light"] .agent-summary-count.installed{
      background:#dcfce7;
      color:#166534;
      border-color:#bbf7d0;
    }
    :root[data-theme="light"] .agent-badge-available,
    :root[data-theme="light"] .agent-summary-count.available{
      background:#dbeafe;
      color:#1d4ed8;
      border-color:#bfdbfe;
    }
    :root[data-theme="light"] .agent-badge-frontend{
      background:#ede9fe;
      color:#6d28d9;
      border-color:#ddd6fe;
    }
    :root[data-theme="light"] .agent-badge-live{
      background:#e0f2fe;
      color:#0369a1;
      border-color:#bae6fd;
    }
    :root[data-theme="light"] .agent-badge-disabled{
      background:#e5e7eb;
      color:#111827;
      border-color:#d1d5db;
    }
    :root[data-theme="light"] .provider-summary-bar,
    :root[data-theme="light"] .provider-inline-status{
      background:#eff6ff !important;
      border-color:#bfdbfe !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] .prov-card-lbl{
      border-color:#bfdbfe !important;
      background:linear-gradient(180deg, rgba(239,246,255,.98), rgba(219,234,254,.9)) !important;
    }
    :root[data-theme="light"] .prov-card-lbl:hover{
      border-color:#93c5fd !important;
      background:linear-gradient(180deg, rgba(225,238,255,.98), rgba(219,234,254,.96)) !important;
    }
    :root[data-theme="light"] .prov-card-lbl.active{
      border-color:#2563eb !important;
      background:linear-gradient(180deg, rgba(219,234,254,.98), rgba(191,219,254,.92)) !important;
      box-shadow:0 0 0 1px rgba(37,99,235,.18), 0 10px 20px rgba(148,163,184,.14) !important;
    }
    :root[data-theme="light"] .prov-check-badge{
      background:#e5e7eb !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] .prov-name,
    :root[data-theme="light"] .prov-tag,
    :root[data-theme="light"] .prov-card-lbl.active .prov-name,
    :root[data-theme="light"] .prov-card-lbl.active .prov-tag{
      color:#111827 !important;
    }
    :root[data-theme="light"] .agent-editor-meta span,
    :root[data-theme="light"] .speech-inline-metrics span,
    :root[data-theme="light"] .agent-section-badge{
      background:#eef2f7;
      border-color:#cbd5e1;
      color:#111827;
    }
    :root[data-theme="light"] .agent-builder-scaffold{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:rgba(148,163,184,.3);
      box-shadow:0 10px 22px rgba(148,163,184,.1), inset 0 1px 0 rgba(255,255,255,.88);
      color:#111827;
    }
    :root[data-theme="light"] .agent-builder-scaffold-title h4,
    :root[data-theme="light"] .agent-builder-scaffold-title p,
    :root[data-theme="light"] .agent-builder-field label,
    :root[data-theme="light"] .agent-builder-field label span,
    :root[data-theme="light"] .agent-builder-help,
    :root[data-theme="light"] .agent-builder-status{
      color:#111827;
    }
    :root[data-theme="light"] .agent-builder-pill{
      background:#e5e7eb;
      border-color:#d1d5db;
      color:#111827;
    }
    :root[data-theme="light"] .agent-builder-pill.is-ready{
      background:#dcfce7;
      border-color:#bbf7d0;
      color:#166534;
    }
    :root[data-theme="light"] .agent-builder-form-shell.is-disabled::after{
      background:rgba(148,163,184,.16);
      border-color:rgba(148,163,184,.24);
    }
    :root[data-theme="light"] .agent-builder-field input{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .agent-section-badge.editable{
      background:#dbeafe;
      border-color:#bfdbfe;
      color:#1d4ed8;
    }
    :root[data-theme="light"] .agent-section-badge.managed{
      background:#fef3c7;
      border-color:#fcd34d;
      color:#92400e;
    }
    :root[data-theme="light"] .agent-section-input,
    :root[data-theme="light"] .agent-editor-textarea,
    :root[data-theme="light"] .wizard-prompt-preview,
    :root[data-theme="light"] .wizard-textarea{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
    }
    :root[data-theme="light"] .agent-section-input[readonly]{
      background:#f8fafc;
      color:#111827;
    }
    :root[data-theme="light"] .speech-settings-shell,
    :root[data-theme="light"] .speech-surface-card,
    :root[data-theme="light"] .speech-stt-library,
    :root[data-theme="light"] .speech-job-card,
    :root[data-theme="light"] .speech-model-card{
      color:#111827;
    }
    :root[data-theme="light"] .speech-settings-shell label,
    :root[data-theme="light"] .speech-settings-shell small,
    :root[data-theme="light"] .speech-inline-note,
    :root[data-theme="light"] .speech-inline-note div,
    :root[data-theme="light"] .speech-inline-note strong,
    :root[data-theme="light"] .speech-model-card strong,
    :root[data-theme="light"] .speech-model-card p,
    :root[data-theme="light"] .speech-model-path,
    :root[data-theme="light"] .speech-job-copy strong,
    :root[data-theme="light"] .speech-job-copy span{
      color:#111827 !important;
    }
    :root[data-theme="light"] .speech-settings-shell input,
    :root[data-theme="light"] .speech-settings-shell select,
    :root[data-theme="light"] .speech-settings-shell textarea{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .rtc-import-card textarea,
    :root[data-theme="light"] .rtc-settings-shell textarea{
      background:#ffffff;
      border-color:#cbd5e1;
      color:#111827;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .speech-inline-note{
      background:linear-gradient(180deg,#f8fafc,#eff6ff);
      border-color:#bfdbfe;
    }
    :root[data-theme="light"] .speech-dot{
      background:#2563eb;
      box-shadow:0 0 14px rgba(37,99,235,.22);
    }
    :root[data-theme="light"] .speech-model-card{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#dbe3ee;
      box-shadow:0 10px 20px rgba(148,163,184,.1), inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .speech-model-chip{
      background:#f8fafc;
      border-color:#dbe3ee;
      color:#111827;
    }
    :root[data-theme="light"] .speech-model-chip.active{
      background:#dbeafe;
      border-color:#93c5fd;
      color:#1d4ed8;
    }
    :root[data-theme="light"] .speech-model-chip.local{
      background:#ecfdf5;
      border-color:#bbf7d0;
      color:#166534;
    }
    :root[data-theme="light"] .speech-model-chip.warn{
      background:#fef3c7;
      border-color:#fcd34d;
      color:#92400e;
    }
    :root[data-theme="light"] .speech-quick-save{
      border-color:#dbe3ee;
    }
    :root[data-theme="light"] .boot-sweep-node{
      background:radial-gradient(circle, #ffffff 0, #2563eb 46%, rgba(29,78,216,.18) 100%);
      box-shadow:0 0 0 2px rgba(29,78,216,.22), 0 10px 22px rgba(29,78,216,.24);
    }
    :root[data-theme="light"] .boot-sweep-node:after{
      border:1px solid rgba(37,99,235,.18);
    }
    :root[data-theme="light"] .totp-generated-card,
    :root[data-theme="light"] .totp-qr-card,
    :root[data-theme="light"] .totp-client-list-card,
    :root[data-theme="light"] .totp-alias-group{
      background:linear-gradient(180deg,#ffffff,#f8fafc);
      border-color:#dbe3ee;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.88);
    }
    :root[data-theme="light"] .totp-generated-card{
      border-color:#86efac;
      background:linear-gradient(180deg,#f0fdf4,#dcfce7);
    }
    :root[data-theme="light"] .totp-client-row,
    :root[data-theme="light"] .totp-alias-row{
      border-bottom-color:#e2e8f0;
    }
    :root[data-theme="light"] .totp-qr-image{
      border-color:#cbd5e1;
      box-shadow:0 10px 24px rgba(148,163,184,.16);
    }
    :root[data-theme="light"] .totp-alias-group{
      border-left-color:#cbd5e1;
    }
    :root[data-theme="light"] .totp-alias-client,
    :root[data-theme="light"] .totp-qr-meta,
    :root[data-theme="light"] .totp-qr-details,
    :root[data-theme="light"] .totp-auto-badge,
    :root[data-theme="light"] .totp-qr-issuer{
      color:#111827;
    }
    :root[data-theme="light"] .totp-alias-badge{
      background:#eff6ff;
      border-color:#bfdbfe;
      color:#1d4ed8;
    }
    :root[data-theme="light"] #jailbreak-prompt-section label,
    :root[data-theme="light"] #jailbreak-settings-card .muted,
    :root[data-theme="light"] #jailbreak-save-status,
    :root[data-theme="light"] #jailbreak-activate-status,
    :root[data-theme="light"] #jailbreak-settings-card code,
    :root[data-theme="light"] #jailbreak-settings-card summary,
    :root[data-theme="light"] #jailbreak-settings-card li{
      color:#111827 !important;
    }
    :root[data-theme="light"] #jailbreak-prompt-textarea{
      background:#ffffff !important;
      color:#111827 !important;
      border-color:#cbd5e1 !important;
      box-shadow:inset 0 1px 0 rgba(255,255,255,.9), 0 10px 24px rgba(148,163,184,.12);
    }
    :root[data-theme="light"] #jailbreak-settings-card details > div{
      background:rgba(124,58,237,.04) !important;
      border-color:#c4b5fd !important;
    }
    :root[data-theme="light"] #jailbreak-settings-card input[type='checkbox']{
      accent-color:#7c3aed;
    }
    :root[data-theme="light"] [style*='background:#0d1626'],
    :root[data-theme="light"] [style*='background: #0d1626'],
    :root[data-theme="light"] [style*='background:#111e30'],
    :root[data-theme="light"] [style*='background: #111e30'],
    :root[data-theme="light"] [style*='background:#0c1f40'],
    :root[data-theme="light"] [style*='background: #0c1f40'],
    :root[data-theme="light"] [style*='background:#0a1220'],
    :root[data-theme="light"] [style*='background: #0a1220']{
      background:#eff6ff !important;
      border-color:#bfdbfe !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='background:#1c2333'],
    :root[data-theme="light"] [style*='background: #1c2333'],
    :root[data-theme="light"] [style*='background:#1e293b'],
    :root[data-theme="light"] [style*='background: #1e293b']{
      background:#f3f4f6 !important;
      border-color:#d1d5db !important;
      color:#111827 !important;
    }
    :root[data-theme="light"] [style*='color:#e2e8f0'],
    :root[data-theme="light"] [style*='color: #e2e8f0'],
    :root[data-theme="light"] [style*='color:#93c5fd'],
    :root[data-theme="light"] [style*='color: #93c5fd'],
    :root[data-theme="light"] [style*='color:#7dd3fc'],
    :root[data-theme="light"] [style*='color: #7dd3fc']{
      color:#111827 !important;
    }
    @media (max-width: 900px){
      .login-actions{flex-direction:column;align-items:stretch}
      .login-hint{max-width:none}
      .login-submit-btn{width:100%}
      .login-topbar{align-items:flex-start;flex-direction:column}
      .startup-overlay{align-items:flex-start;padding:14px 14px calc(20px + env(safe-area-inset-bottom, 0px))}
      .startup-panel{width:100%;max-height:none;padding:18px;border-radius:24px}
      .startup-header{gap:14px}
      .startup-spinner{width:56px;height:56px}
      .startup-console{min-height:132px;max-height:200px}
      .startup-grid .boot-sweep-card{display:none}
      .boot-sweep-score{grid-template-columns:1fr}
      .boot-sweep-table{min-width:440px}
    }
    @keyframes spin{to{transform:rotate(360deg)}}
    @keyframes sweepPulse{0%,100%{transform:scale(.9)}50%{transform:scale(1.12)}}
    </style>
    """
    favicon = (
        "<link rel='icon' type='image/png' sizes='32x32' href='/assets/logo.png'>"
        "<link rel='shortcut icon' href='/favicon.ico'>"
        "<link rel='apple-touch-icon' href='/apple-touch-icon.png'>"
    )
    return _set_no_store_headers(HTMLResponse(f"""
    <html data-theme='{html.escape(current_theme, quote=True)}'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>{page_title}</title>{favicon}{css}</head>
    <body>
      <div class='container'>{body}</div>

      <!-- Restart Confirmation Modal -->
      <div id='restart-modal' class='modal'>
        <div class='modal-content'>
          <div class='modal-header'>
            <svg viewBox='0 0 24 24'>
              <path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/>
            </svg>
            <h3 class='modal-title'>Confirm WhatsApp Service Restart</h3>
          </div>
          <div class='modal-body'>
            <p><strong>Are you sure you want to restart the WhatsApp service?</strong></p>
            <p>This action will:</p>
            <ul class='modal-list'>
              <li>Temporarily disconnect all WhatsApp connections</li>
              <li>Stop the current WhatsApp service process</li>
              <li>Start a fresh WhatsApp service instance</li>
              <li>Require a few moments to reconnect</li>
            </ul>
            <p class='modal-warning-text'>This operation cannot be undone.</p>
          </div>
          <div class='modal-actions'>
            <button type='button' class='modal-btn modal-btn-cancel' onclick='hideRestartModal()'>Cancel</button>
            <button type='button' class='modal-btn modal-btn-confirm' onclick='confirmRestartWhatsApp()'>Yes, Restart Service</button>
          </div>
        </div>
      </div>

      <!-- Signal Restart Confirmation Modal -->
      <div id='signal-restart-modal' class='modal'>
        <div class='modal-content'>
          <div class='modal-header'>
            <svg viewBox='0 0 24 24'>
              <path d='M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z'/>
            </svg>
            <h3 class='modal-title'>Confirm Signal Service Restart</h3>
          </div>
          <div class='modal-body'>
            <p><strong>Are you sure you want to restart the Signal service?</strong></p>
            <p>This action will:</p>
            <ul class='modal-list'>
              <li>Temporarily disconnect all Signal connections</li>
              <li>Stop the current Signal service process</li>
              <li>Start a fresh Signal service instance</li>
              <li>Require a few moments to reconnect</li>
            </ul>
            <p class='modal-warning-text'>This operation cannot be undone.</p>
          </div>
          <div class='modal-actions'>
            <button type='button' class='modal-btn modal-btn-cancel' onclick='hideSignalRestartModal()'>Cancel</button>
            <button type='button' class='modal-btn modal-btn-confirm' onclick='confirmRestartSignal()'>Yes, Restart Service</button>
          </div>
        </div>
      </div>
    </body></html>
    """))

def _is_async_login_request(request: Request) -> bool:
    return request.headers.get("x-autoyou-async", "").strip() == "1"

def _candidate_legal_file_paths(filename: str) -> List[Path]:
    """Return source and packaged legal file locations in priority order."""
    roots: List[Path] = []

    def add_root(value: Any) -> None:
        if not value:
            return
        try:
            root = Path(value).expanduser()
            if root.is_file():
                root = root.parent
            root = root.resolve()
        except Exception:
            return
        if root not in roots:
            roots.append(root)

    add_root(os.getenv("AUTOYOU_PACKAGED_RESOURCES_ROOT"))
    add_root(RESOURCES_ROOT)
    with suppress(Exception):
        add_root(Path(sys.executable).resolve().parent)
    with suppress(Exception):
        add_root(Path(__file__).resolve().parent)
    add_root(APP_ROOT)
    with suppress(Exception):
        add_root(Path.cwd())

    # Source-mode runs use the tracked source-profile legal bundle. Packaged
    # builds still resolve their adjacent Legal directory first, while this
    # fallback keeps the login-page legal links live before packaging.
    with suppress(Exception):
        add_root(Path(__file__).resolve().parent / "docs" / "legal" / "generated" / "autoyou-server-source-full")

    candidates: List[Path] = []

    def add_candidate(path: Path) -> None:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved not in candidates:
            candidates.append(resolved)

    for root in roots:
        for current in (root, *root.parents):
            add_candidate(current / filename)
            add_candidate(current / "Legal" / filename)
            add_candidate(current / "Backend" / "Legal" / filename)
    return candidates

def _resolve_legal_file_path(filename: str) -> Optional[Path]:
    for candidate in _candidate_legal_file_paths(filename):
        try:
            if candidate.is_file():
                return candidate
        except Exception:
            continue
    return None

def _legal_file_response(filename: str) -> PlainTextResponse:
    legal_path = _resolve_legal_file_path(filename)
    if legal_path is not None:
        return PlainTextResponse(legal_path.read_text(encoding="utf-8", errors="replace"))
    return PlainTextResponse(f"{filename} file not found.", status_code=404)


# Explicit login is always required before opening the main admin dashboard.

async def _dashboard_html(bot_status: str = "Unknown", bot_name: str = "-", signal_status: str = "Unknown", signal_name: str = "-", whatsapp_status: str = "Unknown", whatsapp_name: str = "-", banner_html: str = "",
                        ollama_status: str = "Unknown", ollama_status_color: str = "#6b7280", ollama_api_base: str = "Unknown", ollama_models_count: int = 0, ollama_selected_model: str = "None",
                        google_status: str = "Unknown", google_status_color: str = "#6b7280", use_google_api: bool = False, google_model: str = "Unknown", google_api_key_status: str = "Unknown", ai_provider_summary: str = "Unknown", ai_agent_status: str = "Unknown",
                        tunnelmole_status: str = "Unknown", tunnelmole_url: str = "-", wizard_payload_json: str = "{}", show_onboarding_wizard: bool = False,
                        cloud_config: Optional[Dict[str, Any]] = None, cloud_connected: bool = False,
                        cloud_status: Optional[Dict[str, Any]] = None) -> str:
    """Build the explicit legacy long-page dashboard via the dedicated renderer."""
    bind_admin_legacy_dashboard_dependencies(globals())
    return await build_legacy_dashboard_html(
        bot_status=bot_status,
        bot_name=bot_name,
        signal_status=signal_status,
        signal_name=signal_name,
        whatsapp_status=whatsapp_status,
        whatsapp_name=whatsapp_name,
        banner_html=banner_html,
        ollama_status=ollama_status,
        ollama_status_color=ollama_status_color,
        ollama_api_base=ollama_api_base,
        ollama_models_count=ollama_models_count,
        ollama_selected_model=ollama_selected_model,
        google_status=google_status,
        google_status_color=google_status_color,
        use_google_api=use_google_api,
        google_model=google_model,
        google_api_key_status=google_api_key_status,
        ai_provider_summary=ai_provider_summary,
        ai_agent_status=ai_agent_status,
        tunnelmole_status=tunnelmole_status,
        tunnelmole_url=tunnelmole_url,
        wizard_payload_json=wizard_payload_json,
        show_onboarding_wizard=show_onboarding_wizard,
        cloud_config=cloud_config,
        cloud_connected=cloud_connected,
        cloud_status=cloud_status,
    )


async def _ollama_status() -> tuple:
    """Get Ollama service status and configuration.

    IMPORTANT: ollama_service.is_available() and .list_models() make blocking
    HTTP requests.  They MUST run in a thread executor so they never block the
    asyncio event loop - doing otherwise causes TTSAudioStreamTrack.recv() to
    fall behind by the full connect-timeout (≈2.3 s) and audio gets chopped.
    """
    try:
        global ollama_service
        loop = asyncio.get_event_loop()
        # Off-load blocking HTTP calls to a thread pool so the event loop stays
        # responsive for WebRTC audio tracks and all other async work.
        is_available = await loop.run_in_executor(None, ollama_service.is_available)

        if is_available:
            models = await loop.run_in_executor(None, ollama_service.list_models)
            models_count = len(models) if models else 0
            selected_model = await loop.run_in_executor(
                None, lambda: ollama_service.get_default_model() or "None"
            )
            status = "Connected"
            status_color = "#22c55e"  # Green
        else:
            models_count = 0
            selected_model = "None"
            status = "Unavailable"
            status_color = "#ef4444"  # Red

        api_base = os.getenv('OLLAMA_API_BASE', 'http://localhost:11434')
        return status, status_color, api_base, models_count, selected_model

    except Exception as e:
        LOGGER.error(f"Error checking Ollama status: {e}")
        return "Error", "#ef4444", "Unknown", 0, "None"

async def _google_api_status() -> tuple:
    """Get Google API status and configuration."""
    try:
        # Check USE_GOOGLE_API setting
        use_google_api = os.getenv('USE_GOOGLE_API', '0').lower() in ('1', 'true', 'yes')

        # Check API key
        api_key = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
        has_valid_key = api_key and api_key != 'NULL' and len(api_key.strip()) > 0

        # Get Google model
        google_model = os.getenv('GOOGLE_MODEL', 'gemini-2.5-flash')

        if use_google_api and has_valid_key:
            status = "Enabled & Configured"
            status_color = "#22c55e"  # Green
            api_key_status = "Valid"
        elif use_google_api and not has_valid_key:
            status = "Enabled but No API Key"
            status_color = "#f59e0b"  # Yellow/Orange
            api_key_status = "Missing/Invalid"
        elif not use_google_api and has_valid_key:
            status = "Disabled (Key Available)"
            status_color = "#6b7280"  # Gray
            api_key_status = "Available"
        else:
            status = "Disabled"
            status_color = "#6b7280"  # Gray
            api_key_status = "Not Set"

        return status, status_color, use_google_api, google_model, api_key_status

    except Exception as e:
        LOGGER.error(f"Error checking Google API status: {e}")
        return "Error", "#ef4444", False, "Unknown", "Error"

async def _ai_provider_summary() -> str:
    """Get a summary of the current AI provider configuration."""
    try:
        provider = os.getenv("AI_PROVIDER", "ollama").lower()

        if provider == "apple_intelligence":
            from shared.apple_intelligence import status as apple_intelligence_status
            apple = await apple_intelligence_status()
            return "Apple Intelligence + AutoYou Agent: " + apple["detail"]
        if provider == "google":
            google_model = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            has_key = api_key and api_key != "NULL"
            if has_key:
                return f"Using Google Gemini - {google_model}"
            return f"Configured for Google Gemini ({google_model}) but no API key found"

        if provider == "openclaw":
            port  = os.getenv("OPENCLAW_PORT", "18789")
            model = os.getenv("OPENCLAW_MODEL", "openclaw/default")
            return f"Using OpenClaw Gateway - port {port}, model {model}"

        if provider == "hermes":
            port  = os.getenv("HERMES_PORT", "8642")
            model = os.getenv("HERMES_MODEL", "hermes-agent")
            return f"Using Hermes Agent Gateway - port {port}, model {model}"

        if provider == "odysseus":
            model = os.getenv("ODYSSEUS_MODEL", "").strip() or "auto"
            return f"Using Odysseus Gateway - {model}"

        if provider == "ollama_gateway":
            model = os.getenv("OLLAMA_MODEL", "").strip() or "auto"
            return f"Using native Ollama Gateway - {model}"

        if provider == "litellm":
            model = os.getenv("LITELLM_MODEL", "")
            if model:
                return f"Using LiteLLM Cloud - {model}"
            return "LiteLLM provider selected but LITELLM_MODEL not configured"

        # Default: ollama - run blocking check off the event loop
        global ollama_service
        loop = asyncio.get_event_loop()
        available = await loop.run_in_executor(None, ollama_service.is_available)
        if available:
            selected_model = await loop.run_in_executor(
                None, lambda: ollama_service.get_default_model() or "default"
            )
            return f"Using Ollama - {selected_model}"
        return "Ollama unavailable - configure a provider in AI Agent Server Control"

    except Exception as e:
        LOGGER.error("Error getting AI provider summary: %s", e)
        return "Configuration error - check logs"

def _onboarding_config(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base_cfg = cfg if cfg is not None else (STATE.config or {})
    onboarding = base_cfg.setdefault("onboarding", {})
    if "wizard_completed" not in onboarding:
        onboarding["wizard_completed"] = False
    if "wizard_completed_at" not in onboarding:
        onboarding["wizard_completed_at"] = ""
    return onboarding

def _is_connected_status(status: Any) -> bool:
    token = str(status or "").strip().lower().replace("_", " ")
    if not token or "disconnected" in token or "not connected" in token:
        return False
    return token in {"connected", "paired", "paired and connected"} or " connected" in token or "connected " in token

def _has_any_remote_messaging_partner(
    bot_status: str,
    signal_status: str,
    whatsapp_status: str,
    telegram_user_status: str = "Disabled",
) -> bool:
    return any(
        _is_connected_status(status)
        for status in (bot_status, signal_status, whatsapp_status, telegram_user_status)
    )

def _should_show_onboarding_wizard(
    *,
    bot_status: str,
    signal_status: str,
    whatsapp_status: str,
    runtime_status: Dict[str, Any],
    query_params: Dict[str, Any],
) -> bool:
    if str(query_params.get("advanced", "")).strip().lower() in {"1", "true", "yes"}:
        return False
    if str(query_params.get("wizard", "")).strip().lower() in {"1", "true", "yes"}:
        return True

    onboarding = _onboarding_config()
    return not bool(onboarding.get("wizard_completed"))

def _build_wizard_status_payload(
    *,
    bot_status: str,
    bot_name: str,
    signal_status: str,
    signal_name: str,
    whatsapp_status: str,
    whatsapp_name: str,
    tunnelmole_status_info: Dict[str, Any],
    telegram_user_status: str = "Disabled",
    telegram_user_name: str = "-",
) -> Dict[str, Any]:
    cfg = STATE.config or _default_config()
    onboarding = _onboarding_config(cfg)
    ollama_cfg = cfg.get("ollama", {})
    runtime_status = model_library_service.get_ollama_runtime_status(
        str(ollama_cfg.get("api_base") or "http://localhost:11434"),
        str(ollama_cfg.get("model") or ""),
    )
    installed_models = model_library_service.list_local_models(runtime_status["api_base"])
    has_recommended_model = any(model.get("name") == DEFAULT_WIZARD_MODEL for model in installed_models)
    local_adk_port = cfg.get("ai_agent", {}).get("port", AI_AGENT_SERVER_PORT)
    telegram_connected = _is_connected_status(bot_status)
    signal_connected = _is_connected_status(signal_status)
    whatsapp_connected = _is_connected_status(whatsapp_status)
    telegram_user_connected = _is_connected_status(telegram_user_status)
    telegram_cfg = cfg.get("telegram", {}) or {}
    signal_cfg = cfg.get("signal", {}) or {}
    whatsapp_cfg = cfg.get("whatsapp", {}) or {}
    telegram_user_cfg = cfg.get("telegram_user", {}) or {}

    payload = {
        "wizard_completed": bool(onboarding.get("wizard_completed")),
        "wizard_completed_at": str(onboarding.get("wizard_completed_at") or ""),
        "using_default_password": bool(STATE.used_default_password),
        "password_changed": not STATE.used_default_password,
        "security_mode": get_security_mode(),
        "totp_client_count": len(get_all_totp_secrets()),
        "remote_partner_ready": _has_any_remote_messaging_partner(
            bot_status,
            signal_status,
            whatsapp_status,
            telegram_user_status,
        ),
        "ollama": {
            **runtime_status,
            "recommended_model": DEFAULT_WIZARD_MODEL,
            "recommended_model_installed": has_recommended_model,
            "local_models": installed_models,
            "installed_models": installed_models,
        },
        "messaging": {
            "telegram": {
                "status": bot_status,
                "label": bot_name,
                "configured": bool(telegram_cfg.get("bot_token")) or telegram_connected,
            },
            "telegram_user": {
                "status": telegram_user_status,
                "label": telegram_user_name,
                "configured": bool(telegram_user_cfg.get("enabled")) or telegram_user_connected,
                "paired": telegram_user_connected,
            },
            "signal": {
                "status": signal_status,
                "label": signal_name,
                "configured": bool(signal_cfg.get("enabled")) or signal_connected,
                "paired": bool(signal_cfg.get("paired")) or signal_connected,
            },
            "whatsapp": {
                "status": whatsapp_status,
                "label": whatsapp_name,
                "configured": bool(whatsapp_cfg.get("enabled")) or whatsapp_connected,
                "paired": bool(whatsapp_cfg.get("paired")) or whatsapp_connected,
            },
        },
        "connectivity": {
            "tunnelmole_status": tunnelmole_status_info.get("status", "Unknown"),
            "tunnelmole_url": tunnelmole_status_info.get("public_url", "") or "",
            "tunnelmole_enabled": bool((cfg.get("tunnelmole", {}) or {}).get("enabled", True)),
            "ice_server_count": len((cfg.get("rtc", {}) or {}).get("iceServers", []) or []),
            "local_adk_url": f"http://127.0.0.1:{local_adk_port}{_default_local_browser_route_suffix(local_adk_port)}",
        },
        "prompt_preview": {
            "path_hint": "Use the prompt editor in Advanced Settings to update the AI agent instructions.",
        },
    }
    return payload


_TELEGRAM_USER_PRIVATE_STATUS_KEYS = {
    "api_id",
    "api_hash",
    "auth_key",
    "authorization",
    "login_token",
    "password",
    "phone",
    "phone_number",
    "qr_code",
    "qr_data_url",
    "qr_url",
    "session",
    "session_string",
    "user_id",
    "username",
}


def _guide_page_html(title: str, body_html: str, subtitle: str = "") -> str:
    subtitle_html = f"<p class='muted'>{html.escape(subtitle)}</p>" if subtitle else ""
    return f"""
    <style>
      .guide-shell{{max-width:940px;margin:0 auto;padding:20px 0 8px}}
      .guide-shell h1{{font-size:2rem;letter-spacing:-.03em;margin-bottom:10px}}
      .guide-shell h2{{margin-top:24px}}
      .guide-shell h3{{margin-top:18px}}
      .guide-shell p,.guide-shell li{{line-height:1.65;color:#d5deeb}}
      .guide-shell ul,.guide-shell ol{{padding-left:20px}}
      .guide-shell pre{{background:#09111d;border:1px solid #1f2937;border-radius:14px;padding:14px;overflow:auto}}
      .guide-shell code{{background:rgba(15,23,42,.72);padding:2px 5px;border-radius:6px}}
      .guide-callout{{margin:16px 0;padding:16px 18px;border-radius:18px;border:1px solid rgba(56,189,248,.18);background:linear-gradient(180deg,rgba(8,15,28,.94),rgba(12,21,37,.94))}}
      .guide-callout strong{{color:inherit}}
      .guide-topbar{{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:20px}}
      .guide-actions{{display:flex;gap:10px;flex-wrap:wrap}}
      .guide-actions a{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 16px;border-radius:14px;border:1px solid #334155;color:#e2e8f0;text-decoration:none;font-weight:700}}
      .guide-actions a.primary{{background:linear-gradient(135deg,#22c55e,#06b6d4);border:none;color:#04111d;font-weight:700}}
      .guide-body{{display:grid;gap:18px}}
      .guide-body > .guide-shell{{max-width:none;margin:0;padding:24px;border-radius:24px;border:1px solid rgba(51,65,85,.76);background:linear-gradient(180deg,rgba(4,11,22,.96),rgba(7,16,30,.94));box-shadow:0 18px 40px rgba(2,6,23,.22),inset 0 1px 0 rgba(148,163,184,.04)}}
      .guide-body > .guide-shell h1{{font-size:1.55rem;letter-spacing:-.02em;margin-top:0}}
      .guide-body > .guide-shell h2:first-of-type{{margin-top:0}}
      .guide-body > .guide-shell strong{{color:#f8fafc}}
      .guide-body > .guide-shell a{{font-weight:600}}
      html[data-theme='light'] .guide-shell h1,
      html[data-theme='light'] .guide-shell h2,
      html[data-theme='light'] .guide-shell h3,
      html[data-theme='light'] .guide-shell p,
      html[data-theme='light'] .guide-shell li,
      html[data-theme='light'] .guide-shell .muted{{color:#111827}}
      html[data-theme='light'] .guide-shell pre{{background:#ffffff;border-color:#cbd5e1;color:#111827;box-shadow:inset 0 1px 0 rgba(255,255,255,.88)}}
      html[data-theme='light'] .guide-shell code{{background:#eff6ff;color:#1e3a8a}}
      html[data-theme='light'] .guide-callout{{background:linear-gradient(180deg,#ffffff,#eff6ff);border-color:#bfdbfe;color:#111827;box-shadow:0 10px 22px rgba(148,163,184,.12)}}
      html[data-theme='light'] .guide-actions a{{background:linear-gradient(180deg,#2563eb,#1d4ed8);border-color:#1d4ed8;color:#ffffff;box-shadow:0 10px 20px rgba(37,99,235,.18)}}
      html[data-theme='light'] .guide-actions a.primary{{background:linear-gradient(180deg,#2563eb,#1d4ed8);border-color:#1d4ed8;color:#ffffff}}
      html[data-theme='light'] .guide-body > .guide-shell{{background:linear-gradient(180deg,#ffffff,#f8fafc);border-color:#dbe3ee;box-shadow:0 14px 30px rgba(148,163,184,.12),inset 0 1px 0 rgba(255,255,255,.92)}}
      html[data-theme='light'] .guide-body > .guide-shell,
      html[data-theme='light'] .guide-body > .guide-shell p,
      html[data-theme='light'] .guide-body > .guide-shell li,
      html[data-theme='light'] .guide-body > .guide-shell strong,
      html[data-theme='light'] .guide-body > .guide-shell em,
      html[data-theme='light'] .guide-body > .guide-shell span{{color:#111827 !important}}
      html[data-theme='light'] .guide-body > .guide-shell a{{color:#1d4ed8}}
      html[data-theme='light'] .guide-body > .guide-shell code{{background:#eff6ff;color:#111827}}
      html[data-theme='light'] .guide-body > .guide-shell pre,
      html[data-theme='light'] .guide-body > .guide-shell pre code{{color:#111827}}
      @media (max-width: 720px){{.guide-topbar{{flex-direction:column;align-items:flex-start}}}}
    </style>
    <section class='guide-shell'>
      <div class='guide-topbar'>
        <div>
          <h1>{html.escape(title)}</h1>
          {subtitle_html}
        </div>
        <div class='guide-actions'>
          <a class='primary' href='/'>Back to Admin</a>
          <a href='/?advanced=1'>Skip to Advanced Settings</a>
        </div>
      </div>
      <div class='guide-body'>
        {body_html}
      </div>
    </section>
    """

async def _render_markdown_guide_page(
    request: Request,
    *,
    title: str,
    subtitle: str,
    path: Path,
) -> HTMLResponse | RedirectResponse:
    redir = _require_login(request)
    # from __debug_provenance_j__ import fifteenpercent
    if redir:
        return redir
    guide_html = simple_markdown_to_html(load_markdown_guide(path))
    return _html_page(
        _guide_page_html(
            title,
            f"<section class='guide-shell'>{guide_html}</section>",
            subtitle,
        )
    )


def _is_bluetooth_pair_platform(value: Any) -> bool:
    return str(value or "").strip().lower() in _BLUETOOTH_PAIR_PLATFORMS

def _is_bluetooth_pairing_enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    config = cfg if isinstance(cfg, dict) else (STATE.config or _default_config())
    _apply_default_bluetooth_pairing_config(config)
    section = config.get("bluetooth_pairing")
    if not isinstance(section, dict):
        return False
    return _coerce_enabled_flag(section.get("enabled", False))

def _bluetooth_pairing_disabled_response() -> JSONResponse:
    return JSONResponse({"error": "Bluetooth Pair is off on this server."}, status_code=403)


def _resolve_local_pair_client_identity(request: Request, payload) -> tuple:
    """Resolve the (platform, sender_id) for an /api/autopair exchange.

    Local Pair clients identify themselves with a stable per-install id -
    either embedded in the offer payload (``_autoyou_sender_id`` /
    ``_autoyou_pairing_platform``) or via the ``session_id`` query param /
    ``X-AutoYou-Session-Id`` / ``X-AutoYou-Platform`` headers. The admin web
    UI sends none of those and keeps the legacy shared "admin-web" identity.
    A stable identity gives each device its own WebRTC peer key and a durable
    ``local:<client-id>`` conversation owner across reconnects.
    """
    payload_platform = ""
    payload_sender = ""
    if isinstance(payload, dict):
        payload_platform = str(payload.get("_autoyou_pairing_platform") or "").strip().lower()
        payload_sender = str(payload.get("_autoyou_sender_id") or "").strip()
    header_platform = str(request.headers.get("X-AutoYou-Platform") or "").strip().lower()
    query_sender = str(
        request.query_params.get("session_id")
        or request.headers.get("X-AutoYou-Session-Id")
        or ""
    ).strip()

    sender_id = payload_sender or query_sender
    if not sender_id:
        return "admin-web", "admin-web"
    platform = payload_platform or header_platform or "local"
    return platform, sender_id


def _normalize_totp_target(value: Any) -> str:
    return "pairing"

def _totp_target_config_key(target: str) -> str:
    return "totp_secret"

def _totp_account_name(target: str, issuer: Optional[str] = None) -> Tuple[str, str]:
    resolved_issuer = str(issuer or "").strip() or _default_totp_issuer()
    return resolved_issuer, f"{resolved_issuer} ({target})"

def _totp_payload(*, secret: str, target: str, issuer: Optional[str] = None, account_name: Optional[str] = None) -> Dict[str, Any]:
    normalized_target = _normalize_totp_target(target)
    normalized_secret = _normalize_totp_secret(secret)
    if not normalized_secret:
        raise ValueError("TOTP secret cannot be empty.")
    resolved_issuer, default_account_name = _totp_account_name(normalized_target, issuer)
    resolved_account_name = str(account_name or default_account_name).strip() or default_account_name
    _, otpauth = _build_totp_otpauth(resolved_account_name, normalized_secret, resolved_issuer)
    return {
        "success": True,
        "target": normalized_target,
        "secret": normalized_secret,
        "issuer": resolved_issuer,
        "account_name": resolved_account_name,
        "otpauth": otpauth,
        "qr_url": _build_qr_code_url(otpauth),
    }

def _preview_totp_payload(*, value: Any, target: str) -> Dict[str, Any]:
    if pyotp is None:
        raise RuntimeError("pyotp is required to manage TOTP secrets.")
    normalized_target = _normalize_totp_target(target)

    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("Paste a Base32 TOTP secret or otpauth:// URI first.")

    resolved_issuer, resolved_account_name = _totp_account_name(normalized_target)
    input_kind = "secret"
    if raw_value.lower().startswith("otpauth://"):
        parsed = urlsplit(raw_value)
        if parsed.scheme.lower() != "otpauth" or parsed.netloc.lower() != "totp":
            raise ValueError("Only otpauth://totp/... URIs are supported.")
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        normalized_secret = _normalize_totp_secret(query.get("secret", ""))
        if not normalized_secret:
            raise ValueError("The otpauth URI is missing a TOTP secret.")

        label = unquote(parsed.path.lstrip("/")).strip()
        parsed_issuer = str(query.get("issuer") or "").strip()
        parsed_account_name = label
        if ":" in label:
            label_issuer, label_account_name = label.split(":", 1)
            if label_account_name.strip():
                parsed_account_name = label_account_name.strip()
            if not parsed_issuer and label_issuer.strip():
                parsed_issuer = label_issuer.strip()
        if parsed_issuer:
            resolved_issuer = parsed_issuer
        if parsed_account_name:
            resolved_account_name = parsed_account_name
        input_kind = "otpauth"
    else:
        normalized_secret = _normalize_totp_secret(raw_value)

    if not normalized_secret:
        raise ValueError("TOTP secret cannot be empty.")
    try:
        pyotp.TOTP(normalized_secret).now()
    except Exception as exc:
        raise ValueError("Invalid TOTP secret. Expected a Base32 secret or otpauth:// URI.") from exc

    payload = _totp_payload(
        secret=normalized_secret,
        target=normalized_target,
        issuer=resolved_issuer,
        account_name=resolved_account_name,
    )
    payload["input_kind"] = input_kind
    return payload


# ========= For AutoYou Page Service Endpoints =========


PROMPT_SECTION_SPECS: List[Dict[str, Any]] = [
    {
        "key": "introduction",
        "variable": "INTRODUCTION",
        "label": "Introduction",
        "description": "Opening identity and framing for the root AutoYou assistant.",
        "managed": False,
    },
    {
        "key": "core_behavior",
        "variable": "CORE_BEHAVIOR",
        "label": "Core Behavior",
        "description": "High-level tone, routing posture, and response style instructions.",
        "managed": False,
    },
    {
        "key": "sub_agents_section",
        "variable": "SUB_AGENTS_SECTION",
        "label": "Sub-agents",
        "description": "Managed routing inventory that the agent builder updates automatically.",
        "managed": True,
    },
    {
        "key": "routing_rules_section",
        "variable": "ROUTING_RULES_SECTION",
        "label": "Routing Rules",
        "description": "Managed dispatch rules that keep the root agent aligned with built agents and tools.",
        "managed": True,
    },
    {
        "key": "attachments_policy",
        "variable": "ATTACHMENTS_POLICY",
        "label": "Attachments Policy",
        "description": "How media and file-like inputs should be forwarded or handled.",
        "managed": False,
    },
    {
        "key": "special_policies",
        "variable": "SPECIAL_POLICIES",
        "label": "Special Policies",
        "description": "Date, time, and other hard operational rules for the root assistant.",
        "managed": False,
    },
    {
        "key": "conversation_policy",
        "variable": "CONVERSATION_POLICY",
        "label": "Conversation Policy",
        "description": "Continuity and follow-up behavior between turns.",
        "managed": False,
    },
    {
        "key": "safety_rules",
        "variable": "SAFETY_RULES",
        "label": "Safety Rules",
        "description": "Safety-sensitive response requirements for crisis or restricted content.",
        "managed": False,
    },
]
PROMPT_EDITABLE_SECTION_VARIABLES = {
    spec["variable"]
    for spec in PROMPT_SECTION_SPECS
    if not spec.get("managed")
}

def _agent_prompt_file_path() -> str:
    """Return absolute path to the main agent prompt file.

    Uses the packaged embedded-agent root rather than ``os.path.dirname`` to
    avoid Windows 8.3 short-path names and to keep bundled agent lookups aligned
    with the runtime_modules-first packaged layout.
    """
    return str(get_embedded_agents_root(__file__) / "prompt.py")

def _synthesize_agent_prompt_content() -> str:
    """Synthesize a prompt.py-compatible source string from the compiled module.

    In compiled (Nuitka) mode ``prompt.py`` does not exist on disk - the
    module is embedded in the binary.  This function reconstructs a minimal
    Python source string containing the string variables expected by the
    AST-parsing helpers so those helpers continue to work without reading the
    physical file.

    This returns the *base* (compiled) prompt only.  Prompt Override overrides are
    layered on by ``_read_agent_prompt_file`` so the base/override split lives in
    one place for both compiled and source runs.
    """
    import importlib as _importlib
    try:
        _mod = _importlib.import_module("autoyou_agents.prompt")
    except ImportError:
        from autoyou_agents import prompt as _mod  # type: ignore[assignment]

    # All known string variables in prompt.py, in their natural source order.
    _var_names = [
        "AGENT_NAME",
        "AGENT_DESCRIPTION",
        "INTRODUCTION",
        "CORE_BEHAVIOR",
        "SUB_AGENTS_SECTION",
        "ROUTING_RULES_SECTION",
        "ATTACHMENTS_POLICY",
        "SPECIAL_POLICIES",
        "CONVERSATION_POLICY",
        "SAFETY_RULES",
        "DEFAULT_INSTRUCTION",
        "AGENT_INSTRUCTION",
    ]

    lines: List[str] = [
        "# synthesized from compiled autoyou_agents.prompt",
        "",
    ]
    for _var in _var_names:
        _val = getattr(_mod, _var, None)
        if _val is not None and isinstance(_val, str):
            lines.append(f"{_var} = {_serialize_python_string_literal(_val)}")
            lines.append("")

    return "\n".join(lines)

def _override_defines_full_prompt_module(text: Optional[str]) -> bool:
    """True when an override is a full prompt module (parses + has an
    ``AGENT_INSTRUCTION`` string assignment), vs. a legacy raw-instruction blob."""
    if not text or not text.strip():
        return False
    try:
        ast.parse(text)
        _extract_string_assignment_value(text, "AGENT_INSTRUCTION")
        return True
    except Exception:
        return False

def _apply_prompt_override(base_content: str, override_text: Optional[str]) -> str:
    """Overlay a Prompt Override onto a base prompt-module source.

    - Full-module override (current format): wins wholesale so every section and
      ``AGENT_INSTRUCTION`` round-trip through the section and raw editors.
    - Legacy blob override (raw ``AGENT_INSTRUCTION`` text): overlaid onto the
      base's ``AGENT_INSTRUCTION`` so older override files keep working.
    """
    if override_text is None:
        return base_content
    if _override_defines_full_prompt_module(override_text):
        return override_text
    try:
        return _replace_string_assignment_value(base_content, "AGENT_INSTRUCTION", override_text)
    except Exception as exc:
        LOGGER.warning("Could not overlay legacy Prompt Override: %s", exc)
        return base_content

def _read_agent_prompt_file() -> str:
    """Read the effective prompt-module source (base + Prompt Override).

    Base is the compiled module (synthesized) in a packaged build or the on-disk
    ``prompt.py`` in a source run.  When Prompt Override is active the override is
    overlaid so the admin UI reflects exactly what the agent runtime uses - every
    section *and* the raw instruction stay in sync in both modes.
    """
    if is_compiled():
        base = _synthesize_agent_prompt_content()
    else:
        with open(_agent_prompt_file_path(), "r", encoding="utf-8") as f:
            base = f.read()
    return _apply_prompt_override(base, get_jailbreak_root_prompt(anchor=__file__))

def _persist_jailbreak_root_prompt_override(content: str) -> None:
    """Persist the full edited prompt-module source as the Prompt Override.

    Storing the whole module (not just ``AGENT_INSTRUCTION``) lets both the raw
    prompt editor and the per-section builder round-trip under Prompt Override, in
    compiled and source runs alike, without one editor clobbering the other.
    """
    if not _override_defines_full_prompt_module(content):
        raise ValueError(
            "Updated prompt content has no AGENT_INSTRUCTION assignment to save."
        )
    jailbreak_prompt_path = get_jailbreak_data_dir(anchor=__file__) / JAILBREAK_ROOT_PROMPT_FILENAME
    write_secure_file(jailbreak_prompt_path, content.encode("utf-8"))
    # Sign it. The agent refuses to exec an override whose signature is absent
    # or stale, so this write is what distinguishes an admin-authored override
    # from a file that merely appeared in the data directory.
    sign_jailbreak_root_prompt(content.strip(), anchor=__file__)
    LOGGER.info(
        "Prompt Override root prompt override saved (%d chars) to %s",
        len(content),
        jailbreak_prompt_path,
    )

def _write_agent_prompt_file(content: str) -> None:
    """Write prompt.py content.

    - Compiled build, Prompt Override inactive: raises ``PermissionError`` - the admin
      UI surfaces this as a clear "read-only" error.
    - Prompt Override active (compiled *or* source): persists ``AGENT_INSTRUCTION`` to
      ``jailbreak_root_prompt.txt``.  The runtime applies it on the next restart
      or hot-reload.  Routing source-run edits through the override too fixes the
      bug where enabling Prompt Override in a source run silently had no effect.
    - Source run, Prompt Override inactive: writes ``prompt.py`` on disk as before.
    """
    jailbreak_on = is_jailbreak_active(anchor=__file__)
    if is_compiled() and not jailbreak_on:
        raise PermissionError(
            "Agent instructions are locked in this packaged AutoYou runtime. "
            "Enable Prompt Override in the Admin UI to edit them locally."
        )
    if jailbreak_on:
        _persist_jailbreak_root_prompt_override(content)
        return
    with open(_agent_prompt_file_path(), "w", encoding="utf-8") as f:
        f.write(content)

def _find_string_assignment_node(module_ast: ast.Module, variable_name: str):
    """Find top-level string assignment node for a variable."""
    for node in module_ast.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == variable_name:
                    return node
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == variable_name:
                return node
    return None

def _resolve_string_assignment_value(module_ast: ast.Module, variable_name: str, seen: Optional[set[str]] = None) -> str:
    seen = seen or set()
    if variable_name in seen:
        raise ValueError(f"Cyclic string assignment reference for {variable_name}")
    seen.add(variable_name)

    assign_node = _find_string_assignment_node(module_ast, variable_name)
    if not assign_node:
        raise ValueError(f"Could not find string assignment for {variable_name}")
    value_node = assign_node.value
    if isinstance(value_node, ast.Constant) and isinstance(value_node.value, str):
        return value_node.value
    if isinstance(value_node, ast.Name):
        return _resolve_string_assignment_value(module_ast, value_node.id, seen)
    raise ValueError(f"{variable_name} is not assigned a string literal")

def _serialize_python_string_literal(value: str) -> str:
    if "'''" not in value:
        return "'''" + value + "'''"
    return repr(value)

def _replace_string_assignment_value_fallback(content: str, variable_name: str, new_value: str) -> str:
    literal = _serialize_python_string_literal(new_value)
    triple_pattern = re.compile(
        rf"(?ms)^({re.escape(variable_name)}\s*=\s*)(\"\"\"|''').*?(?:\2)\s*$"
    )
    if triple_pattern.search(content):
        return triple_pattern.sub(rf"{variable_name} = {literal}", content, count=1)

    single_line_pattern = re.compile(rf"(?m)^{re.escape(variable_name)}\s*=.*(?:\r?\n)?")
    if single_line_pattern.search(content):
        return single_line_pattern.sub(f"{variable_name} = {literal}\n", content, count=1)

    raise ValueError(f"Could not find assignment for {variable_name}")

def _extract_string_assignment_value(content: str, variable_name: str) -> str:
    """Extract string variable value from prompt.py using AST parsing."""
    module_ast = ast.parse(content)
    return _resolve_string_assignment_value(module_ast, variable_name)

def _replace_string_assignment_value(content: str, variable_name: str, new_value: str) -> str:
    """Replace string variable assignment while preserving the rest of prompt.py."""
    try:
        module_ast = ast.parse(content)
        assign_node = _find_string_assignment_node(module_ast, variable_name)
        if not assign_node:
            raise ValueError(f"Could not find string assignment for {variable_name}")
        if assign_node.lineno is None or assign_node.end_lineno is None:
            raise ValueError(f"Missing source location metadata for {variable_name}")

        lhs = variable_name
        source_segment = ast.get_source_segment(content, assign_node)
        if source_segment and "=" in source_segment:
            lhs = source_segment.split("=", 1)[0].strip() or variable_name

        lines = content.splitlines(keepends=True)
        replacement = f"{lhs} = {_serialize_python_string_literal(new_value)}\n"
        return "".join(lines[:assign_node.lineno - 1]) + replacement + "".join(lines[assign_node.end_lineno:])
    except SyntaxError:
        return _replace_string_assignment_value_fallback(content, variable_name, new_value)

def _normalize_prompt_section_value(value: Optional[str]) -> str:
    return str(value or "").replace("\r\n", "\n").strip()

def _compose_prompt_from_section_values(section_values: Dict[str, str]) -> str:
    parts: List[str] = []
    for spec in PROMPT_SECTION_SPECS:
        normalized = _normalize_prompt_section_value(section_values.get(spec["variable"]))
        if normalized:
            parts.append(normalized)
    return "\n\n".join(parts)

def _filter_section_text_by_installed(text: str, installed_runtime_names: set[str]) -> str:
    lines = str(text or "").splitlines()
    filtered: List[str] = []
    for line in lines:
        tokens = {
            candidate
            for token in re.findall(r"`([^`]+)`", line)
            for candidate in [str(token).strip()]
            if candidate.endswith("_agent") and not is_root_agent_name(candidate)
        }
        if tokens and not all(token in installed_runtime_names for token in tokens):
            continue
        filtered.append(line)
    return "\n".join(filtered).strip()

def _extract_prompt_section_payload(content: str) -> Dict[str, Any]:
    try:
        module_ast = ast.parse(content)
    except SyntaxError:
        return {
            "sections": [],
            "section_values": {},
            "section_builder_available": False,
            "composed_instructions": "",
        }

    installed_runtime_names: set[str] = set()
    try:
        registry = load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT)
        installed_runtime_names = {
            resolve_runtime_agent_name(agent_name) or str(agent_name)
            for agent_name in (registry.get("installed_agents", []) or [])
        }
    except Exception:
        installed_runtime_names = set()

    sections: List[Dict[str, Any]] = []
    section_values: Dict[str, str] = {}
    section_builder_available = True
    for spec in PROMPT_SECTION_SPECS:
        try:
            value = _resolve_string_assignment_value(module_ast, spec["variable"])
            found = True
            section_values[spec["variable"]] = value
        except Exception:
            value = ""
            found = False
            section_builder_available = False
        display_value = value
        if found and installed_runtime_names and spec.get("managed"):
            display_value = _filter_section_text_by_installed(value, installed_runtime_names)
        sections.append(
            {
                **spec,
                "value": display_value,
                "found": found,
            }
        )

    composed_instructions = _compose_prompt_from_section_values(section_values) if section_builder_available else ""
    return {
        "sections": sections,
        "section_values": section_values,
        "section_builder_available": section_builder_available and bool(composed_instructions),
        "composed_instructions": composed_instructions,
    }

def _default_agent_instruction_text(content: str) -> Tuple[str, str]:
    section_payload = _extract_prompt_section_payload(content)
    composed = str(section_payload.get("composed_instructions") or "")
    if section_payload.get("section_builder_available") and composed:
        return composed, "sections"
    return _extract_string_assignment_value(content, "DEFAULT_INSTRUCTION"), "default_instruction"


def _prompt_agent_tokens_for_runtime(text: str) -> List[str]:
    """Extract specialist tokens from an operator-authored root prompt."""
    tokens: set[str] = set()
    for token in re.findall(r"`([^`]+)`", str(text or "")):
        candidate = str(token).strip()
        if not candidate.endswith("_agent") or is_root_agent_name(candidate):
            continue
        tokens.add(candidate)
    return sorted(tokens)


def _custom_prompt_runtime_diagnostics(
    instructions: str,
    default_instructions: str,
    section_payload: Dict[str, Any],
    *,
    literal_matches_sections: bool,
) -> Dict[str, Any]:
    """Describe stale specialist references without rewriting custom prompt text."""
    section_builder_available = bool(section_payload.get("section_builder_available"))
    custom_prompt_active = (
        bool(instructions)
        and (
            (section_builder_available and not literal_matches_sections)
            or (
                not section_builder_available
                and _normalize_prompt_section_value(instructions)
                != _normalize_prompt_section_value(default_instructions)
            )
        )
    )
    if not custom_prompt_active:
        return {
            "custom_prompt_active": False,
            "custom_prompt_unavailable_agents": [],
        }

    try:
        registry = load_agent_install_registry(agents_root=_AUTOYOU_AGENTS_ROOT)
        installed_runtime_names = {
            resolve_runtime_agent_name(agent_name) or str(agent_name)
            for agent_name in (registry.get("installed_agents", []) or [])
        }
    except Exception as exc:  # noqa: BLE001 - diagnostics must not break prompt reads
        LOGGER.debug("Could not calculate custom prompt agent diagnostics: %s", exc)
        installed_runtime_names = set()

    unavailable = [
        token
        for token in _prompt_agent_tokens_for_runtime(instructions)
        if (resolve_runtime_agent_name(token) or token) not in installed_runtime_names
    ]
    return {
        "custom_prompt_active": True,
        "custom_prompt_unavailable_agents": unavailable,
    }

def _build_agent_instruction_payload(content: str) -> Dict[str, Any]:
    instructions = _extract_string_assignment_value(content, "AGENT_INSTRUCTION")
    section_payload = _extract_prompt_section_payload(content)
    default_instructions, default_source = _default_agent_instruction_text(content)
    literal_matches_sections = (
        bool(section_payload.get("section_builder_available"))
        and _normalize_prompt_section_value(instructions)
        == _normalize_prompt_section_value(str(section_payload.get("composed_instructions") or ""))
    )
    runtime_diagnostics = _custom_prompt_runtime_diagnostics(
        instructions,
        default_instructions,
        section_payload,
        literal_matches_sections=literal_matches_sections,
    )
    return {
        "instructions": instructions,
        "default_instructions": default_instructions,
        "default_source": default_source,
        "section_builder_available": bool(section_payload.get("section_builder_available")),
        "literal_matches_sections": literal_matches_sections,
        "sections": section_payload.get("sections", []),
        **runtime_diagnostics,
    }

def _coerce_prompt_section_updates(raw_sections: Any) -> Dict[str, str]:
    updates: Dict[str, str] = {}
    if isinstance(raw_sections, dict):
        items = []
        for spec in PROMPT_SECTION_SPECS:
            if spec["variable"] in raw_sections:
                items.append({"variable": spec["variable"], "value": raw_sections.get(spec["variable"])})
            elif spec["key"] in raw_sections:
                items.append({"variable": spec["variable"], "value": raw_sections.get(spec["key"])})
    elif isinstance(raw_sections, list):
        items = raw_sections
    else:
        raise ValueError("Prompt sections must be sent as a list or object")

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each prompt section update must be an object")
        variable = str(item.get("variable") or "").strip()
        if not variable:
            raise ValueError("Prompt section update is missing a variable name")
        if variable not in PROMPT_EDITABLE_SECTION_VARIABLES:
            raise ValueError(f"{variable} is managed automatically and cannot be edited here")
        value = item.get("value")
        if not isinstance(value, str):
            raise ValueError(f"{variable} must be updated with a string value")
        updates[variable] = _normalize_prompt_section_value(value)
    return updates

async def _reload_ai_agent_runtime_from_prompt_update() -> str:
    """Reload in-memory agent instructions and restart AI server if running."""
    try:
        if _is_agent_process_running(STATE.agent_process):
            await restart_ai_agent_server()
            return "AI Agent prompt updated and running server restarted"
        return "AI Agent prompt updated (server not currently running)"
    except Exception as e:
        LOGGER.error(f"Prompt updated but AI Agent runtime reload failed: {e}")
        return f"Prompt file updated, but runtime reload failed: {e}"


# ========= Prompt Override Routes =========


# ========= Per-Agent 2FA Security Profiles =========
# Re-introduces MULTIPLE issued 2FA profiles (legacy single shared TOTP is kept for
# pairing/admin elevation). Each profile is derive-from-password (no secret stored),
# write-once / verify-only (no "Show secret"), and assignable to individual agent
# websites such as persona_agent. Recovery is by wipe + re-enrol.

def _agent_security_db_path() -> str:
    from shared.platform_runtime import get_mutable_data_dir
    return str(get_mutable_data_dir("AutoYou", anchor=__file__) / "agent_security.db")

def _get_agent_security_store():
    """Build the per-agent 2FA store bound to the unlocked server password.

    Returns None when the server password is unavailable (locked) - callers turn
    that into a clear 'unlock first' error rather than a silent failure.
    """
    password = get_server_password()
    if not password:
        return None
    from shared.agent_security_profiles import AgentSecurityProfileStore
    return AgentSecurityProfileStore(_agent_security_db_path(), password=password)

def agent_has_assigned_2fa_profile(agent_name: str) -> bool:
    """True when an agent website has a dedicated per-agent 2FA profile assigned.

    Used by the per-website auth gate to prefer the agent's own profile over the
    single shared pairing TOTP. Safe (False) when locked or on any error.
    """
    try:
        store = _get_agent_security_store()
        return bool(store and store.agent_requires_2fa(agent_name))
    except Exception:
        return False

def verify_agent_assigned_2fa(agent_name: str, code: Any) -> bool:
    """Verify a TOTP code against an agent's assigned per-agent 2FA profile."""
    try:
        store = _get_agent_security_store()
        return bool(store and store.verify_for_agent(agent_name, code))
    except Exception:
        return False


# ========= AutoYou Cloud Endpoints =========

async def _fetch_cloud_server_info_from_cloud(
  server_id: str,
  server_token: str,
  *,
  timeout: float = 5.0,
) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
  if httpx is None or not server_id or not server_token:
    return None, None
  try:
    async with httpx.AsyncClient(timeout=timeout) as client:
      response = await client.get(
        f"{AUTOYOU_CLOUD_BASE}/api/v1/servers/{server_id}/info",
        headers={"Authorization": f"Bearer {server_token}"},
      )
    if response.status_code < 400:
      return cast(Dict[str, Any], response.json()), response.status_code
    return None, response.status_code
  except Exception as exc:
    LOGGER.debug("AutoYou Cloud: info lookup failed for server_id=%s: %s", server_id, exc)
    return None, None

async def _build_cloud_status_snapshot(
  cloud_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
        resolved_cfg = cloud_cfg if cloud_cfg is not None else ((STATE.config or {}).get("cloud", {}) or {})
        server_token = str(resolved_cfg.get("server_token", "") or "").strip()
        server_id = str(resolved_cfg.get("server_id", "") or "").strip()
        email = str(resolved_cfg.get("email", "") or "").strip()
        cloud_pair_enabled = bool(resolved_cfg.get("pair_enabled", True)) if server_token else False
        cloud_pair_entitlement_verified = bool(
                resolved_cfg.get("pair_entitlement_verified", True)
        ) if server_token else False

        enrolled = bool(server_token)
        token_rejected = bool(STATE.cloud_token_rejected)
        token_expired = _cloud_server_token_expired(resolved_cfg)
        if token_expired:
                token_rejected = True
        is_active: Optional[bool] = None
        info_unreachable = False
        sse_connected = (
                bool(STATE.cloud_connected)
                and not token_expired
                and not _cloud_sse_connection_is_stale()
        )

        if enrolled and server_id and not token_expired:
                info, status_code = await _fetch_cloud_server_info_from_cloud(server_id, server_token)
                if info is not None:
                        is_active = bool(info.get("is_active", False))
                        remote_pair_enabled = info.get("cloud_pair_enabled")
                        if isinstance(remote_pair_enabled, bool):
                                cloud_pair_enabled = remote_pair_enabled
                                cloud_pair_entitlement_verified = True
                        else:
                                cloud_pair_entitlement_verified = False
                elif status_code in {401, 403}:
                        token_rejected = True
                elif status_code == 404:
                        is_active = False
                else:
                        info_unreachable = True
        elif enrolled and not server_id:
                info_unreachable = True

        registered = bool(enrolled and server_id and not token_rejected)
        connected = bool(
                registered
                and cloud_pair_entitlement_verified
                and cloud_pair_enabled
                and is_active is not False
                and sse_connected
        )
        needs_reregister = bool(
                token_rejected
                or (enrolled and not server_id)
                or (enrolled and not cloud_pair_entitlement_verified and not info_unreachable)
        )
        needs_activation = bool(
                cloud_pair_entitlement_verified
                and cloud_pair_enabled
                and is_active is False
                and not token_rejected
        )

        status_message: Optional[str] = None
        if token_expired:
                status_message = (
                        "This saved cloud session is older than the local max age. "
                        "Start the Cloud Pair link flow again; until then this server will not receive client requests."
                )
        elif token_rejected:
                status_message = (
                        "This saved cloud session is no longer accepted. "
                        "Start the Cloud Pair link flow again; until then this server will not receive client requests."
                )
        elif info_unreachable and enrolled:
                status_message = (
                        "Linked to AutoYou Cloud, but the active-server status could not be verified right now."
                )
        elif not cloud_pair_entitlement_verified and enrolled:
                status_message = (
                        "Linked to AutoYou Cloud, but Cloud Pair access could not be verified. "
                        "Re-link after the cloud service is updated."
                )
        elif not cloud_pair_enabled and enrolled:
                status_message = (
                        "A free AutoYou account is connected for software updates. "
                        "Paid Cloud Pair remains optional and separate."
                )
        elif is_active is False:
                status_message = (
                        "Another linked server is active. Cloud Pair client requests are routed only to that server. "
                        "Activate this server to move requests here and stop routing them to the other server."
                )
        return {
                "enrolled": enrolled,
                "registered": registered,
                "account_signed_in": bool(email),
                "server_id": server_id,
                "email": email,
                "connected": connected,
                "cloud_pair_enabled": cloud_pair_enabled,
                "cloud_pair_entitlement_verified": cloud_pair_entitlement_verified,
                "sse_connected": sse_connected,
                "is_active": is_active,
                "info_unreachable": info_unreachable,
                "token_rejected": token_rejected,
                "needs_reregister": needs_reregister,
                "needs_activation": needs_activation,
                "activate_url": "/api/cloud/activate" if cloud_pair_entitlement_verified and cloud_pair_enabled and enrolled and server_id and is_active is not True and not token_rejected else None,
                "reregister_url": "/api/cloud/link-start" if needs_reregister else None,
                "status_message": status_message,
        }

def _forwarded_header_first_value(raw_value: Any) -> str:
        if raw_value is None:
                return ""
        return str(raw_value).split(",", 1)[0].strip()

def _cloud_callback_origin_for_request(request: Request) -> str:
        forwarded_host = _forwarded_header_first_value(
                request.headers.get("x-forwarded-host") or request.headers.get("host")
        )
        forwarded_proto = _forwarded_header_first_value(
                request.headers.get("x-forwarded-proto")
        ) or request.url.scheme or "http"
        if forwarded_host:
                return f"{forwarded_proto}://{forwarded_host}".rstrip("/")
        return str(request.base_url).rstrip("/")

async def _activate_current_cloud_server_registration() -> Dict[str, Any]:
  cloud_cfg = (STATE.config or {}).get("cloud", {})
  server_token = str(cloud_cfg.get("server_token", "") or "").strip()
  server_id = str(cloud_cfg.get("server_id", "") or "").strip()
  if not server_token or not server_id:
    raise HTTPException(status_code=409, detail="This server is not actively enrolled with AutoYou Cloud.")
  if _cloud_server_token_expired(cloud_cfg):
    if not await _rotate_cloud_server_token_if_due(cloud_cfg, force=True):
      STATE.cloud_token_rejected = True
      raise HTTPException(status_code=409, detail="Saved cloud session expired. Re-link Cloud Pair to continue.")
    cloud_cfg = (STATE.config or {}).get("cloud", {})
    server_token = str(cloud_cfg.get("server_token", "") or "").strip()
    server_id = str(cloud_cfg.get("server_id", "") or "").strip()
  if httpx is None:
    raise HTTPException(status_code=500, detail="httpx is required for AutoYou Cloud activation.")

  try:
    async with httpx.AsyncClient(timeout=15.0) as client:
      response = await client.post(
        f"{AUTOYOU_CLOUD_BASE}/v1/server/activate",
        json={"server_id": server_id},
        headers={"Authorization": f"Bearer {server_token}"},
      )
  except Exception as exc:
    raise HTTPException(status_code=502, detail=f"Could not reach AutoYou Cloud: {exc}") from exc

  try:
    payload = cast(Dict[str, Any], response.json())
  except Exception:
    payload = {}

  if response.status_code >= 400:
    detail = payload.get("detail") or response.text or "Could not activate this server on AutoYou Cloud."
    if response.status_code in {401, 403}:
      STATE.cloud_token_rejected = True
    raise HTTPException(status_code=response.status_code, detail=str(detail))

  STATE.cloud_token_rejected = False
  asyncio.create_task(_start_cloud_sse_listener())
  LOGGER.info("AutoYou Cloud: current server activated via admin UI (server_id=%s)", server_id)
  return {
    "success": True,
    "message": str(payload.get("detail") or "This server is now the active AutoYou Cloud server."),
    "server_id": server_id,
  }


_CLOUD_LINK_CALLBACK_COOKIE = "autoyou_cloud_link"
_CLOUD_LINK_MAX_AGE_SECONDS = 600

def _cloud_link_cookie_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

def _clear_cloud_link_cookie(response: HTMLResponse) -> HTMLResponse:
    response.delete_cookie(_CLOUD_LINK_CALLBACK_COOKIE, path="/api/cloud/callback")
    return response


# ── x402 Payment-Required API ─────────────────────────────────────────────────
#
# Implements the HTTP 402 / x402 payment-gated connection protocol.  Clients
# that call GET /api/v1/x402/connect receive a 402 with payment requirements.
# After satisfying the requirement (active AutoYou Cloud subscription validated
# server-side), they POST the same endpoint with a bearer token and receive a
# short-lived connection access token they can pass to the pairing endpoints.
#
# This is the mechanism that lets paid AutoYou Cloud users connect to a server
# instance they own without requiring the server to be pre-paired to their app.

import secrets as _x402_secrets
import time as _x402_time

_X402_TOKENS: dict = {}          # access_token -> {"user_id", "email", "expires_at"}
_X402_TOKEN_TTL = 300            # 5-minute access tokens

def _x402_guest_access_config() -> dict:
    """Operator-configured x402 guest access (mirrors the cloud-side record)."""
    cloud_cfg = (STATE.config or {}).get("cloud", {}) or {}
    guest_cfg = cloud_cfg.get("guest_access")
    return guest_cfg if isinstance(guest_cfg, dict) else {}

def _x402_payment_requirements() -> dict:
    """Return the x402 payment requirements payload.

    Always offers the owner path (``autoyou-cloud-subscription``). When the
    operator has enabled guest access, a second ``autoyou-guest-pass`` accept
    entry advertises the operator's price so other AutoYou/autoyou_lite agents
    can buy a time-limited pass through AutoYou Cloud and connect.
    """
    cloud_cfg = (STATE.config or {}).get("cloud", {}) or {}
    server_id = str(cloud_cfg.get("server_id") or "").strip()
    accepts = [
        {
            "scheme": "autoyou-cloud-subscription",
            "network": "autoyou",
            "maxAmountRequired": "0",
            "resource": "/api/v1/x402/connect",
            "description": "Active AutoYou Cloud subscription required",
            "mimeType": "application/json",
            "payTo": "autoyou-cloud",
            "maxTimeoutSeconds": 300,
            "asset": "subscription",
            "extra": {
                "subscriptionUrl": f"{AUTOYOU_CLOUD_BASE}/v1/server/link",
                "planName": "AutoYou Cloud",
                "enrolledServerId": server_id or None,
            },
        }
    ]
    guest_cfg = _x402_guest_access_config()
    if bool(guest_cfg.get("enabled")) and server_id:
        price = max(0.0, float(guest_cfg.get("price_credits") or 0.0))
        pass_ttl = int(guest_cfg.get("pass_ttl_seconds") or 86400)
        accepts.append(
            {
                "scheme": "autoyou-guest-pass",
                "network": "autoyou",
                "maxAmountRequired": f"{price:g}",
                "resource": "/api/v1/x402/connect",
                "description": "Time-limited guest access pass purchased through AutoYou Cloud",
                "mimeType": "application/json",
                "payTo": server_id,
                "maxTimeoutSeconds": 300,
                "asset": "autoyou_credit",
                "extra": {
                    "purchaseUrl": f"{AUTOYOU_CLOUD_BASE}/v1/x402/guest-pass",
                    "guestAccessUrl": f"{AUTOYOU_CLOUD_BASE}/v1/x402/guest-access/{server_id}",
                    "serverId": server_id,
                    "priceCredits": price,
                    "passTtlSeconds": pass_ttl,
                },
            }
        )
    return {
        "x402Version": 1,
        "accepts": accepts,
        "error": "X-PAYMENT-REQUIRED",
    }


_X402_GUEST_SESSION_MAX_TTL = 24 * 3600  # guest tokens live up to 24h, capped by the pass expiry

async def _x402_connect_with_guest_pass(guest_pass: str) -> JSONResponse:
    """Verify a purchased guest pass with AutoYou Cloud and issue a guest token.

    The pass was bought by another AutoYou user through the cloud guest-pass
    facilitation flow; this server verifies it using its own registered
    server_token, so guests never learn operator credentials and the cloud
    confirms the pass belongs to this exact server.
    """
    guest_cfg = _x402_guest_access_config()
    cloud_cfg = (STATE.config or {}).get("cloud", {}) or {}
    server_token = str(cloud_cfg.get("server_token") or "").strip()
    if not bool(guest_cfg.get("enabled")) or not server_token:
        return JSONResponse(
            status_code=402,
            content={**_x402_payment_requirements(), "detail": "Guest access is not enabled on this server"},
            headers={"X-Payment-Required": "autoyou-guest-pass"},
        )
    try:
        import httpx as _hx
        async with _hx.AsyncClient(timeout=10.0) as _cl:
            _resp = await _cl.post(
                f"{AUTOYOU_CLOUD_BASE}/v1/x402/guest-pass/verify",
                headers={"Authorization": f"Bearer {server_token}"},
                json={"passToken": guest_pass},
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AutoYou Cloud unreachable: {exc}")
    if _resp.status_code >= 400:
        detail = "Guest pass was rejected by AutoYou Cloud."
        try:
            detail = str(_resp.json().get("detail") or detail)
        except Exception:
            pass
        return JSONResponse(
            status_code=402,
            content={**_x402_payment_requirements(), "detail": detail},
            headers={"X-Payment-Required": "autoyou-guest-pass"},
        )
    verified = _resp.json()
    now = _x402_time.time()
    pass_expires_at = float(verified.get("expires_at_s") or 0.0)
    expires_at = min(now + _X402_GUEST_SESSION_MAX_TTL, pass_expires_at) if pass_expires_at else now + _X402_TOKEN_TTL
    if expires_at <= now:
        return JSONResponse(
            status_code=402,
            content={**_x402_payment_requirements(), "detail": "Guest pass has expired"},
            headers={"X-Payment-Required": "autoyou-guest-pass"},
        )
    access_token = _x402_secrets.token_urlsafe(32)
    _X402_TOKENS[access_token] = {
        "user_id": str(verified.get("guest_user_id") or ""),
        "email": str(verified.get("guest_email") or ""),
        "expires_at": expires_at,
        "kind": "guest",
        "pass_token": guest_pass,
    }
    return JSONResponse({
        "access_token": access_token,
        "expires_in": int(expires_at - now),
        "token_type": "x402",
        "kind": "guest",
        "server_id": str(cloud_cfg.get("server_id") or ""),
        "user_id": str(verified.get("guest_user_id") or ""),
        "email": str(verified.get("guest_email") or ""),
    })

def _validate_x402_token(request: Request) -> Optional[dict]:
    """Return the x402 token payload if the request carries a valid token, else None."""
    auth_header = request.headers.get("Authorization", "")
    payment_header = request.headers.get("X-Payment", "")
    raw = ""
    if auth_header.lower().startswith("bearer "):
        raw = auth_header[7:].strip()
    elif payment_header:
        raw = payment_header.strip()
    if not raw:
        return None
    entry = _X402_TOKENS.get(raw)
    if not entry:
        return None
    if entry["expires_at"] < _x402_time.time():
        _X402_TOKENS.pop(raw, None)
        return None
    return entry


# ========= Telegram + WebRTC Signaling =========

WEBRTC = WebRTCManager()


def _set_client_name_override(owner_key: Any, value: Any) -> Dict[str, Any]:
    """Apply an operator name override without ever changing client identity."""
    normalized_owner_key = _normalize_client_identity_owner_key(owner_key)
    client_name = _normalize_client_display_name(value)
    history_enabled = _client_name_history_enabled()

    if history_enabled:
        cfg = _loaded_config_for_update(copy_config=True)
        _apply_default_client_identity_config(cfg)
        overrides = cfg.setdefault("client_identity", {}).setdefault("name_overrides", {})
        if client_name:
            overrides[normalized_owner_key] = client_name
        else:
            overrides.pop(normalized_owner_key, None)
        STATE.config = _save_and_reload_state_config(cfg)

    WEBRTC.set_client_name_override(normalized_owner_key, client_name)
    snapshot = WEBRTC._client_display_name_for_identity(
        types.SimpleNamespace(owner_key=normalized_owner_key)
    )
    snapshot["history_stored"] = history_enabled
    return snapshot


TELEGRAM_CONNECT_TIMEOUT_SECONDS = 30.0
TELEGRAM_READ_TIMEOUT_SECONDS = 60.0
TELEGRAM_WRITE_TIMEOUT_SECONDS = 30.0
TELEGRAM_POOL_TIMEOUT_SECONDS = 10.0
TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS = 60.0
TELEGRAM_SEND_RETRY_ATTEMPTS = 3
TELEGRAM_SEND_RETRY_BASE_DELAY_SECONDS = 1.5
TELEGRAM_TYPING_HEARTBEAT_SECONDS = 4.0
TELEGRAM_BOT_API_GET_FILE_DOWNLOAD_LIMIT_BYTES = 20 * 1024 * 1024
TELEGRAM_BOT_API_CLOUD_UPLOAD_LIMIT_BYTES = 50 * 1024 * 1024
TELEGRAM_RICH_MESSAGE_TEXT_LIMIT = 32768
TELEGRAM_RICH_DRAFT_ENV = "AUTOYOU_TELEGRAM_RICH_DRAFT"
SIGNAL_TYPING_HEARTBEAT_SECONDS = 10.0
WHATSAPP_TYPING_HEARTBEAT_SECONDS = 5.0
TELEGRAM_ALLOW_CODE_TTL_SECONDS = max(60, int(os.getenv("AUTOYOU_TELEGRAM_ALLOW_CODE_TTL_SECONDS", "600")))
TELEGRAM_ALLOW_CODE_LENGTH = 8
TELEGRAM_ALLOW_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TELEGRAM_SENDER_DISCOVERY_LIMIT = max(10, int(os.getenv("AUTOYOU_TELEGRAM_SENDER_DISCOVERY_LIMIT", "80")))
AUTOPAIR_REPLACEMENT_CLEANUP_TIMEOUT_SECONDS = 3.0

def _read_positive_float_env(name: str, default: float, *, minimum: float = 0.05) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except Exception:
        return max(minimum, default)

def _read_positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except Exception:
        return max(minimum, default)

WEBRTC_RESOURCE_CLEANUP_TIMEOUT_SECONDS = _read_positive_float_env(
    "AUTOYOU_WEBRTC_RESOURCE_CLEANUP_TIMEOUT_SECONDS",
    1.0,
    minimum=0.25,
)
WEBRTC_PEER_CLOSE_TIMEOUT_SECONDS = _read_positive_float_env(
    "AUTOYOU_WEBRTC_PEER_CLOSE_TIMEOUT_SECONDS",
    2.0,
    minimum=0.25,
)
WEBRTC_TASK_CANCEL_TIMEOUT_SECONDS = _read_positive_float_env(
    "AUTOYOU_WEBRTC_TASK_CANCEL_TIMEOUT_SECONDS",
    1.0,
    minimum=0.1,
)
TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS = _read_positive_float_env(
    "AUTOYOU_TELEGRAM_GET_UPDATES_POOL_TIMEOUT_SECONDS",
    max(30.0, TELEGRAM_POOL_TIMEOUT_SECONDS),
    minimum=1.0,
)
TELEGRAM_GET_UPDATES_CONNECTION_POOL_SIZE = _read_positive_int_env(
    "AUTOYOU_TELEGRAM_GET_UPDATES_CONNECTION_POOL_SIZE",
    4,
    minimum=1,
)
VOICE_CALL_MUTE_FLUSH_GRACE_SECONDS = 0.2
# Offline queue for WebRTC voice replies: persists across disconnects so the
# client receives AI responses when it reconnects (Pair / Auto-Pair / Cloud-Pair).
# TTL prevents OOM on long-disconnected sessions; cap enforces per-session bound.
_WEBRTC_OFFLINE_QUEUE_TTL_SECONDS: int = int(
    os.getenv("AUTOYOU_WEBRTC_OFFLINE_QUEUE_TTL_SECONDS", str(7 * 24 * 3600))
)
_WEBRTC_OFFLINE_QUEUE_MAX_SIZE: int = max(
    1000,
    int(os.getenv("AUTOYOU_WEBRTC_OFFLINE_QUEUE_MAX_SIZE", "1000")),
)

def _telegram_reply_limit() -> int:
    try:
        limit = pairing_router.reply_limit("telegram")
    except Exception:
        limit = 0
    return limit or 4096

def _telegram_file_size_label(size_bytes: Optional[int]) -> str:
    try:
        size = int(size_bytes or 0)
    except Exception:
        size = 0
    if size <= 0:
        return "unknown size"
    mib = size / (1024 * 1024)
    if mib >= 1:
        return f"{mib:.1f} MB"
    kib = size / 1024
    return f"{kib:.0f} KB"

def _telegram_file_size_bytes(size_bytes: Any) -> int:
    try:
        return int(size_bytes or 0)
    except Exception:
        return 0

def _telegram_file_too_big_error(exc: Exception) -> bool:
    message = str(exc or "").strip().lower()
    if "file is too big" in message:
        return True
    if BadRequest is not None and isinstance(exc, BadRequest) and "file" in message and "big" in message:
        return True
    return False

def _telegram_cloud_download_limit_notice(kind: str, size_bytes: Optional[int]) -> str:
    return (
        f"Telegram did not let the bot download that {kind}"
        f" ({_telegram_file_size_label(size_bytes)}). AutoYou is not adding a smaller limit; "
        "Telegram's cloud Bot API getFile download path is capped at 20 MB. "
        "Use WhatsApp/WebRTC for large media, or run AutoYou's Telegram bot through a local Bot API server."
    )

def _telegram_large_media_message_for_agent(kind: str, caption: str, size_bytes: Optional[int]) -> str:
    size_label = _telegram_file_size_label(size_bytes)
    prefix = str(caption or "").strip()
    note = (
        f"[Telegram {kind} was not available to AutoYou because Telegram's cloud Bot API "
        f"getFile download limit is 20 MB. Reported size: {size_label}.]"
    )
    return f"{prefix}\n\n{note}".strip() if prefix else note

# Matches raw base64 / base64url payload blobs - no spaces, no punctuation,
# only characters valid in a base64/base64url alphabet, minimum 100 chars.
# Used to detect Telegram-split /autopair continuation fragments and block
# them from being forwarded to the AI as regular chat messages.
_PAYLOAD_FRAGMENT_CHARSET_RE = re.compile(r"^[A-Za-z0-9+/=\-_]{100,}$")

def _looks_like_raw_payload_fragment(text: str) -> bool:
    """Return True if text is a raw base64 payload fragment with no meaningful natural language.

    Detects continuation fragments from Telegram-split /autopair messages so they are
    buffered and reassembled rather than sent to the AI as chat queries.
    """
    stripped = (text or "").strip()
    return bool(_PAYLOAD_FRAGMENT_CHARSET_RE.match(stripped))

def _split_text_for_transport(text: str, max_len: int) -> list[str]:
    if not text:
        return []
    if max_len <= 0 or len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        if end >= len(text):
            chunks.append(text[start:])
            break

        split_at = max(
            text.rfind("\n\n", start, end),
            text.rfind("\n", start, end),
            text.rfind(" ", start, end),
        )
        # Avoid tiny first chunks such as just "/autopair_answer"; hard-split
        # opaque command payloads near the transport limit instead.
        if split_at <= start or split_at <= start + max(1, max_len // 2):
            split_at = end

        chunk = text[start:split_at].rstrip()
        if not chunk:
            chunk = text[start:end]
            split_at = end

        chunks.append(chunk)
        start = split_at
        while start < len(text) and text[start].isspace():
            start += 1

    return chunks

def _telegram_pairing_payload_needs_document(text: str) -> bool:
    if not isinstance(text, str):
        return False
    stripped = text.lstrip()
    pairing_prefixes = (
        "/autopair_answer",
        "/autopair_hello_answer",
        "/autopair_candidates",
        "/otp",
        "/pair_hello_answer",
    )
    if not any(stripped == prefix or stripped.startswith(f"{prefix}\n") for prefix in pairing_prefixes):
        return False
    return len(text) > _telegram_reply_limit()

async def _send_telegram_pairing_document_via_bot(
    bot: Any,
    chat_id: Optional[int],
    text: str,
    *,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    if bot is None or chat_id is None or text is None:
        return False
    return await _send_telegram_media_payload(
        bot,
        chat_id=int(chat_id),
        media_bytes=str(text).encode("utf-8"),
        filename="autoyou-pairing-response.txt",
        mimetype="text/plain",
        caption="AutoYou pairing response attached as one text file.",
        reply_to_message_id=reply_to_message_id,
        queue_on_failure=False,
    )

async def _send_telegram_pairing_rich_message_via_bot(
    bot: Any,
    chat_id: Optional[int],
    text: str,
    *,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    if bot is None or chat_id is None or text is None or httpx is None:
        return False
    base_url = str(getattr(bot, "base_url", "") or "").rstrip("/")
    if not base_url:
        return False
    rendered = str(text)
    if len(rendered.encode("utf-8")) > TELEGRAM_RICH_MESSAGE_TEXT_LIMIT:
        return False
    rich_html = f"<pre><code>{html.escape(rendered, quote=False)}</code></pre>"
    payload: Dict[str, Any] = {
        "chat_id": int(chat_id),
        "rich_message": {
            "html": rich_html,
            "skip_entity_detection": True,
        },
    }
    if reply_to_message_id is not None:
        payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    try:
        timeout = httpx.Timeout(
            connect=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
            read=TELEGRAM_READ_TIMEOUT_SECONDS,
            write=TELEGRAM_WRITE_TIMEOUT_SECONDS,
            pool=TELEGRAM_POOL_TIMEOUT_SECONDS,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/sendRichMessage", json=payload)
            response.raise_for_status()
            result = response.json()
        if not result.get("ok", False):
            raise RuntimeError(result.get("description") or "Telegram Bot API returned ok=false")
        LOGGER.info("Sent Telegram pairing reply as rich message to chat_id=%s", chat_id)
        return True
    except Exception as exc:
        LOGGER.info(
            "Telegram rich-message pairing reply unavailable for chat_id=%s: %s",
            chat_id,
            type(exc).__name__,
        )
        return False

def _telegram_rich_draft_enabled() -> bool:
    return str(os.getenv(TELEGRAM_RICH_DRAFT_ENV, "1")).strip().lower() not in {"0", "false", "no", "off"}

def _telegram_rich_html_for_text(text: str) -> str:
    escaped = html.escape(str(text or "").replace("\r\n", "\n").replace("\r", "\n"), quote=False)
    return f"<p>{escaped.replace(chr(10), '<br>')}</p>"

async def _send_telegram_rich_draft_final_via_bot(
    bot: Any,
    chat_id: Optional[int],
    text: str,
    *,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    if (
        bot is None
        or chat_id is None
        or text is None
        or httpx is None
        or not _telegram_rich_draft_enabled()
    ):
        return False
    rendered = str(text)
    if len(rendered.encode("utf-8")) > TELEGRAM_RICH_MESSAGE_TEXT_LIMIT:
        return False
    base_url = str(getattr(bot, "base_url", "") or "").rstrip("/")
    if not base_url:
        return False

    rich_message = {"html": _telegram_rich_html_for_text(rendered)}
    draft_id = secrets.randbelow(2_147_483_647) + 1
    draft_payload: Dict[str, Any] = {
        "chat_id": int(chat_id),
        "draft_id": draft_id,
        "rich_message": rich_message,
    }
    final_payload: Dict[str, Any] = {
        "chat_id": int(chat_id),
        "rich_message": rich_message,
    }
    if reply_to_message_id is not None:
        final_payload["reply_parameters"] = {"message_id": int(reply_to_message_id)}
    try:
        timeout = httpx.Timeout(
            connect=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
            read=TELEGRAM_READ_TIMEOUT_SECONDS,
            write=TELEGRAM_WRITE_TIMEOUT_SECONDS,
            pool=TELEGRAM_POOL_TIMEOUT_SECONDS,
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            draft_response = await client.post(f"{base_url}/sendRichMessageDraft", json=draft_payload)
            draft_response.raise_for_status()
            draft_result = draft_response.json()
            if not draft_result.get("ok", False):
                raise RuntimeError(draft_result.get("description") or "Telegram Bot API returned ok=false")

            final_response = await client.post(f"{base_url}/sendRichMessage", json=final_payload)
            final_response.raise_for_status()
            final_result = final_response.json()
            if not final_result.get("ok", False):
                raise RuntimeError(final_result.get("description") or "Telegram Bot API returned ok=false")
        LOGGER.info("Sent Telegram reply via rich draft/final message to chat_id=%s", chat_id)
        return True
    except Exception as exc:
        LOGGER.info(
            "Telegram rich draft/final reply unavailable for chat_id=%s: %s",
            chat_id,
            type(exc).__name__,
        )
        return False

def _normalize_telegram_username(username: Optional[str]) -> Optional[str]:
    value = str(username or "").strip().lstrip("@").lower()
    return value or None

def _normalize_telegram_sender_id(sender_id: Any) -> Optional[str]:
    raw = str(sender_id or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    if lowered.startswith("telegram:"):
        raw = raw.split(":", 1)[1].strip()
    elif lowered.startswith("tg:"):
        raw = raw.split(":", 1)[1].strip()
    if raw.startswith("+"):
        raw = raw[1:]
    if raw.startswith("-"):
        digits = raw[1:]
        if digits.isdigit():
            return raw
        return None
    return raw if raw.isdigit() else None

def _telegram_update_sender_snapshot(update: Any, message_kind: str = "message") -> Optional[Dict[str, Any]]:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    sender_id = _normalize_telegram_sender_id(getattr(user, "id", None))
    username = _normalize_telegram_username(getattr(user, "username", None))
    chat_id = _normalize_telegram_sender_id(getattr(chat, "id", None))
    if not sender_id and not username and not chat_id:
        return None
    return {
        "sender_id": sender_id or "",
        "username": username or "",
        "chat_id": chat_id or "",
        "chat_type": str(getattr(chat, "type", "") or "").strip(),
        "last_message_kind": str(message_kind or "message").strip() or "message",
    }

def _remember_telegram_sender(update: Any, message_kind: str = "message") -> None:
    snapshot = _telegram_update_sender_snapshot(update, message_kind=message_kind)
    if not snapshot:
        return
    sender_key = snapshot["sender_id"] or (f"username:{snapshot['username']}" if snapshot["username"] else f"chat:{snapshot['chat_id']}")
    if not sender_key:
        return
    now = time.time()
    registry = getattr(STATE, "telegram_seen_senders", None)
    if not isinstance(registry, dict):
        registry = {}
    existing = dict(registry.get(sender_key) or {})
    first_seen = float(existing.get("first_seen_at_s") or now)
    count = int(existing.get("message_count") or 0) + 1
    existing.update(snapshot)
    existing.update(
        {
            "sender_key": sender_key,
            "first_seen_at_s": first_seen,
            "last_seen_at_s": now,
            "message_count": count,
        }
    )
    registry[sender_key] = existing
    if len(registry) > TELEGRAM_SENDER_DISCOVERY_LIMIT:
        keep = sorted(
            registry.items(),
            key=lambda item: float((item[1] or {}).get("last_seen_at_s") or 0.0),
            reverse=True,
        )[:TELEGRAM_SENDER_DISCOVERY_LIMIT]
        registry = dict(keep)
    STATE.telegram_seen_senders = registry

def _telegram_sender_discovery_payload() -> Dict[str, Any]:
    cfg = STATE.config or {}
    telegram_cfg = cfg.get("telegram", {}) if isinstance(cfg, dict) else {}
    approved_sender_ids = set(_telegram_acl_sender_ids())
    approved_usernames = set(_telegram_acl_usernames())
    rows: List[Dict[str, Any]] = []
    registry = getattr(STATE, "telegram_seen_senders", None) or {}
    for item in registry.values():
        if not isinstance(item, dict):
            continue
        sender_id = _normalize_telegram_sender_id(item.get("sender_id")) or ""
        username = _normalize_telegram_username(item.get("username")) or ""
        chat_id = _normalize_telegram_sender_id(item.get("chat_id")) or ""
        approved = bool((sender_id and sender_id in approved_sender_ids) or (username and username in approved_usernames))
        rows.append(
            {
                "sender_key": str(item.get("sender_key") or sender_id or username or chat_id),
                "sender_id": sender_id,
                "username": username,
                "username_label": f"@{username}" if username else "",
                "chat_id": chat_id,
                "chat_type": str(item.get("chat_type") or "").strip(),
                "message_count": int(item.get("message_count") or 0),
                "first_seen_at_s": float(item.get("first_seen_at_s") or 0.0),
                "last_seen_at_s": float(item.get("last_seen_at_s") or 0.0),
                "last_message_kind": str(item.get("last_message_kind") or "").strip() or "message",
                "approved": approved,
                "authorized_now": bool(_telegram_user_is_authorized(sender_id, username)),
            }
        )
    rows.sort(key=lambda item: float(item.get("last_seen_at_s") or 0.0), reverse=True)
    return {
        "success": True,
        "configured": bool(str(telegram_cfg.get("bot_token") or "").strip()),
        "access_gate_enabled": bool(telegram_cfg.get("access_gate_enabled", False)),
        "silent_unapproved_messages": bool(telegram_cfg.get("silent_unapproved_messages", False)),
        "approved_sender_ids": sorted(approved_sender_ids),
        "approved_usernames": sorted(approved_usernames),
        "senders": rows,
        "count": len(rows),
        "limit": TELEGRAM_SENDER_DISCOVERY_LIMIT,
        "updated_at_s": time.time(),
    }

def _telegram_acl_sender_ids() -> List[str]:
    telegram_cfg = (STATE.config or {}).get("telegram", {})
    sender_ids = []
    for entry in telegram_cfg.get("acl_sender_ids", []) or []:
        normalized = _normalize_telegram_sender_id(entry)
        if normalized:
            sender_ids.append(normalized)
    return sorted(set(sender_ids))

def _telegram_acl_usernames() -> List[str]:
    telegram_cfg = (STATE.config or {}).get("telegram", {})
    usernames = []
    for entry in telegram_cfg.get("acl_usernames", []) or []:
        normalized = _normalize_telegram_username(entry)
        if normalized:
            usernames.append(normalized)
    return sorted(set(usernames))

def _telegram_access_gate_enabled() -> bool:
    telegram_cfg = (STATE.config or {}).get("telegram", {})
    return bool(telegram_cfg.get("access_gate_enabled", False))

def _telegram_silent_unapproved_messages() -> bool:
    telegram_cfg = (STATE.config or {}).get("telegram", {})
    return bool(telegram_cfg.get("silent_unapproved_messages", False))

def _telegram_authorization_required() -> bool:
    return bool(_telegram_acl_sender_ids() or _telegram_acl_usernames() or _telegram_access_gate_enabled())

def _telegram_user_is_authorized(sender_id: Optional[str], username: Optional[str]) -> bool:
    normalized_sender_id = _normalize_telegram_sender_id(sender_id)
    sender_ids = _telegram_acl_sender_ids()
    if sender_ids:
        return normalized_sender_id in set(sender_ids)
    if _telegram_access_gate_enabled():
        return False
    usernames = _telegram_acl_usernames()
    if usernames:
        return _normalize_telegram_username(username) in set(usernames)
    return True

def _expire_telegram_allow_tokens(*, now: Optional[float] = None) -> None:
    current_time = time.time() if now is None else float(now)
    tokens = getattr(STATE, "telegram_allow_tokens", None) or {}
    expired = [
        code
        for code, payload in tokens.items()
        if float(payload.get("expires_at", 0.0) or 0.0) <= current_time
    ]
    for code in expired:
        tokens.pop(code, None)
    STATE.telegram_allow_tokens = tokens

def _telegram_allow_code_entry(code: Optional[str]) -> Optional[Dict[str, Any]]:
    normalized_code = str(code or "").strip().upper()
    if not normalized_code:
        return None
    _expire_telegram_allow_tokens()
    for existing_code, payload in (getattr(STATE, "telegram_allow_tokens", None) or {}).items():
        if secrets.compare_digest(existing_code, normalized_code):
            return payload
    return None

def _issue_telegram_allow_code() -> str:
    now = time.time()
    code = "".join(secrets.choice(TELEGRAM_ALLOW_CODE_ALPHABET) for _ in range(TELEGRAM_ALLOW_CODE_LENGTH))
    STATE.telegram_allow_tokens = {
        code: {
            "created_at": now,
            "expires_at": now + TELEGRAM_ALLOW_CODE_TTL_SECONDS,
        }
    }
    return code

def _revoke_telegram_allow_code(code: Optional[str]) -> None:
    normalized_code = str(code or "").strip().upper()
    if not normalized_code:
        return
    tokens = getattr(STATE, "telegram_allow_tokens", None) or {}
    tokens.pop(normalized_code, None)
    STATE.telegram_allow_tokens = tokens

def _telegram_access_guidance_message(sender_id: Optional[str]) -> str:
    normalized_sender_id = _normalize_telegram_sender_id(sender_id)
    status_line = "Telegram access is locked." if _telegram_acl_sender_ids() else "Telegram access is not configured."
    lines = [status_line, ""]
    if normalized_sender_id:
        lines.extend([f"Your Telegram user id: {normalized_sender_id}", ""])
    lines.extend(
        [
            "Ask the bot owner to generate a one-time /allow code in the AutoYou Admin dashboard, then send:",
            "/allow XXXXXXXX",
        ]
    )
    return "\n".join(lines)

def _redeem_telegram_allow_code(code: Optional[str], sender_id: Optional[str]) -> Tuple[bool, str]:
    normalized_sender_id = _normalize_telegram_sender_id(sender_id)
    if not normalized_sender_id:
        return False, "Could not determine your Telegram user id."

    normalized_code = str(code or "").strip().upper()
    if not normalized_code:
        return False, "Usage: /allow XXXXXXXX"

    if _telegram_allow_code_entry(normalized_code) is None:
        return False, f"Invalid or expired /allow code.\n\n{_telegram_access_guidance_message(normalized_sender_id)}"

    block_reason = _config_write_block_reason()
    if block_reason:
        return False, (
            "Telegram approval failed because the server configuration is locked. "
            "Ask the bot owner to unlock the admin dashboard and generate a new /allow code."
        )

    cfg = _loaded_config_for_update()
    telegram_cfg = cfg.setdefault("telegram", {})
    telegram_cfg["access_gate_enabled"] = True
    telegram_cfg["acl_sender_ids"] = [normalized_sender_id]
    _persist_state_config(cfg)
    _revoke_telegram_allow_code(normalized_code)
    return True, (
        "Telegram access approved.\n\n"
        f"Authorized Telegram user id: {normalized_sender_id}\n\n"
        "This bot is now locked to this Telegram account."
    )

async def _ensure_telegram_update_authorized(update: Any) -> bool:
    _remember_telegram_sender(update)
    if not _telegram_authorization_required():
        return True

    user = getattr(update, "effective_user", None)
    sender_id = _normalize_telegram_sender_id(getattr(user, "id", None))
    username = _normalize_telegram_username(getattr(user, "username", None))
    if _telegram_user_is_authorized(sender_id, username):
        return True

    if _telegram_silent_unapproved_messages():
        return False

    if update and getattr(update, "message", None):
        await _reply_telegram_text(update, _telegram_access_guidance_message(sender_id), split_text=False)
    return False

def _extract_telegram_allow_code(text: Optional[str]) -> Optional[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    match = re.match(r"^/allow(?:@[^\s]+)?(?:\s+(\S+))?\s*$", normalized, flags=re.IGNORECASE)
    if not match:
        return None
    return str(match.group(1) or "").strip().upper()

def _telegram_identity(update: Any) -> tuple[str, str, Optional[int]]:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    username = getattr(user, "username", None) if user else None
    user_id_val = username if username else (str(getattr(user, "id", "")) if user else "")
    if not user_id_val:
        user_id_val = "unknown"
    user_id_val = "telegram_" + user_id_val
    session_id_val = str(getattr(chat, "id", "")) if chat else ""
    if not session_id_val:
        session_id_val = "unknown"
    return user_id_val, session_id_val, getattr(chat, "id", None) if chat else None

def _resolve_telegram_execution_identity(update: Any):
    raw_user_id, raw_session_id, chat_id = _telegram_identity(update)
    sender_id = str(chat_id if chat_id is not None else raw_session_id)
    identity = get_session_execution_manager().bind_transport_owner(
        "telegram",
        sender_id,
        raw_session_id=raw_session_id,
    )
    return _resolve_conversation_identity(identity), raw_user_id, chat_id

def _remember_telegram_chat_binding(session_id: str, chat_id: Optional[int], user_id: str, reply_to_message_id: Optional[int]) -> None:
    if chat_id is None:
        return
    STATE.telegram_chat_bindings[str(session_id)] = {
        "chat_id": int(chat_id),
        "user_id": str(user_id),
        "reply_to_message_id": int(reply_to_message_id) if reply_to_message_id is not None else None,
        "updated_at": time.time(),
    }

def _get_active_telegram_bot():
    app = getattr(STATE, "telegram_app", None)
    bot = getattr(app, "bot", None) if app is not None else None
    return bot

_TELEGRAM_PENDING_VOICE_REPLY_LIMIT = 20
_TELEGRAM_PENDING_MEDIA_REPLY_LIMIT = 20

def _telegram_pending_voice_reply_path() -> Path:
    return get_service_data_dir("telegram", anchor=__file__) / "pending_voice_replies.json"

def _telegram_pending_media_reply_path() -> Path:
    return get_service_data_dir("telegram", anchor=__file__) / "pending_media_replies.json"

def _load_telegram_pending_voice_replies() -> List[Dict[str, Any]]:
    try:
        path = _telegram_pending_voice_reply_path()
        if not path.exists():
            return []
        raw = load_secure_json(path, default=[])
        if not isinstance(raw, list):
            return []
        items = [item for item in raw if isinstance(item, dict)]
        return items[-_TELEGRAM_PENDING_VOICE_REPLY_LIMIT:]
    except (SecureStorageError, Exception) as exc:
        LOGGER.warning("Failed to load pending Telegram voice replies: %s", exc)
        return []

def _persist_telegram_pending_voice_replies(items: Optional[List[Dict[str, Any]]] = None) -> None:
    try:
        pending = list(items if items is not None else _telegram_pending_voice_replies())
        path = _telegram_pending_voice_reply_path()
        if not pending:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        save_secure_json(path, pending[-_TELEGRAM_PENDING_VOICE_REPLY_LIMIT:])
    except (SecureStorageError, Exception) as exc:
        LOGGER.warning("Failed to persist pending Telegram voice replies: %s", exc)

def _telegram_pending_voice_replies() -> List[Dict[str, Any]]:
    pending = getattr(STATE, "telegram_pending_voice_replies", None)
    if not isinstance(pending, list):
        pending = _load_telegram_pending_voice_replies()
        STATE.telegram_pending_voice_replies = pending
    return pending

def _load_telegram_pending_media_replies() -> List[Dict[str, Any]]:
    try:
        path = _telegram_pending_media_reply_path()
        if not path.exists():
            return []
        raw = load_secure_json(path, default=[])
        if not isinstance(raw, list):
            return []
        items: List[Dict[str, Any]] = []
        changed = False
        for item in raw:
            if not isinstance(item, dict):
                continue
            item_copy = dict(item)
            if item_copy.get("data") and not item_copy.get("media_path"):
                try:
                    media_bytes = base64.b64decode(str(item_copy.get("data") or ""), validate=False)
                except Exception:
                    changed = True
                    continue
                payload_info = store_media_payload(
                    path.parent,
                    media_bytes,
                    filename=item_copy.get("filename") or "telegram-media",
                    mimetype=str(item_copy.get("mimetype") or "application/octet-stream"),
                    prefix="telegram-media",
                )
                if not payload_info:
                    changed = True
                    continue
                item_copy.pop("data", None)
                item_copy.update(payload_info)
                changed = True
            items.append(item_copy)
        items = prune_and_trim_media_entries(
            items,
            limit_count=_TELEGRAM_PENDING_MEDIA_REPLY_LIMIT,
        )
        if changed:
            _persist_telegram_pending_media_replies(items)
        return items[-_TELEGRAM_PENDING_MEDIA_REPLY_LIMIT:]
    except (SecureStorageError, Exception) as exc:
        LOGGER.warning("Failed to load pending Telegram media replies: %s", exc)
        return []

def _persist_telegram_pending_media_replies(items: Optional[List[Dict[str, Any]]] = None) -> None:
    try:
        pending = prune_and_trim_media_entries(
            list(items if items is not None else _telegram_pending_media_replies()),
            limit_count=_TELEGRAM_PENDING_MEDIA_REPLY_LIMIT,
        )
        STATE.telegram_pending_media_replies = pending
        path = _telegram_pending_media_reply_path()
        if not pending:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        save_secure_json(path, pending)
    except (SecureStorageError, Exception) as exc:
        LOGGER.warning("Failed to persist pending Telegram media replies: %s", exc)

def _telegram_pending_media_replies() -> List[Dict[str, Any]]:
    pending = getattr(STATE, "telegram_pending_media_replies", None)
    if not isinstance(pending, list):
        pending = _load_telegram_pending_media_replies()
        STATE.telegram_pending_media_replies = pending
    return pending

def _telegram_voice_mimetype_for_path(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".ogg", ".oga", ".opus"):
        return "audio/ogg; codecs=opus"
    if ext == ".wav":
        return "audio/wav"
    if ext in (".m4a", ".aac", ".mp4"):
        return "audio/mp4"
    if ext == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"

def _queue_telegram_voice_reply(
    *,
    chat_id: int,
    audio_bytes: bytes,
    filename: str,
    mimetype: str,
    reply_to_message_id: Optional[int] = None,
) -> None:
    if not audio_bytes:
        return
    pending = _telegram_pending_voice_replies()
    pending.append(
        {
            "queued_at": time.time(),
            "chat_id": int(chat_id),
            "reply_to_message_id": reply_to_message_id,
            "filename": filename,
            "mimetype": mimetype,
            "data": base64.b64encode(audio_bytes).decode("ascii"),
        }
    )
    if len(pending) > _TELEGRAM_PENDING_VOICE_REPLY_LIMIT:
        del pending[: len(pending) - _TELEGRAM_PENDING_VOICE_REPLY_LIMIT]
    _persist_telegram_pending_voice_replies(pending)
    LOGGER.info("Queued Telegram voice reply for later delivery (%d pending)", len(pending))

async def _send_telegram_voice_payload(
    bot: Any,
    *,
    chat_id: int,
    audio_bytes: bytes,
    filename: str,
    reply_to_message_id: Optional[int] = None,
    queue_on_failure: bool = True,
) -> bool:
    if bot is None:
        if queue_on_failure:
            _queue_telegram_voice_reply(
                chat_id=chat_id,
                audio_bytes=audio_bytes,
                filename=filename,
                mimetype=_telegram_voice_mimetype_for_path(filename),
                reply_to_message_id=reply_to_message_id,
            )
        return False
    for attempt in range(1, TELEGRAM_SEND_RETRY_ATTEMPTS + 1):
        try:
            voice_file = io.BytesIO(audio_bytes)
            voice_file.name = filename or "voice-reply.ogg"
            await bot.send_voice(
                chat_id=int(chat_id),
                voice=voice_file,
                reply_to_message_id=reply_to_message_id,
                connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
                write_timeout=TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
                pool_timeout=TELEGRAM_POOL_TIMEOUT_SECONDS,
            )
            LOGGER.info("Sent Telegram synthesized voice reply to chat_id=%s", chat_id)
            return True
        except Exception as exc:
            LOGGER.warning(
                "Failed to send Telegram voice reply attempt %d/%d chat_id=%s: %r",
                attempt,
                TELEGRAM_SEND_RETRY_ATTEMPTS,
                chat_id,
                exc,
            )
            if BadRequest is not None and isinstance(exc, BadRequest):
                break
            if attempt >= TELEGRAM_SEND_RETRY_ATTEMPTS:
                break
            delay = TELEGRAM_SEND_RETRY_BASE_DELAY_SECONDS * attempt
            if RetryAfter is not None and isinstance(exc, RetryAfter):
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    delay = max(delay, float(retry_after))
            await asyncio.sleep(delay)
    if queue_on_failure:
        _queue_telegram_voice_reply(
            chat_id=chat_id,
            audio_bytes=audio_bytes,
            filename=filename,
            mimetype=_telegram_voice_mimetype_for_path(filename),
            reply_to_message_id=reply_to_message_id,
        )
    return False

def _queue_telegram_media_reply(
    *,
    chat_id: int,
    media_bytes: bytes,
    filename: str,
    mimetype: str,
    caption: str = "",
    reply_to_message_id: Optional[int] = None,
) -> None:
    if not media_bytes:
        return
    payload_info = store_media_payload(
        _telegram_pending_media_reply_path().parent,
        media_bytes,
        filename=filename or "telegram-media",
        mimetype=mimetype or "application/octet-stream",
        prefix="telegram-media",
    )
    if not payload_info:
        LOGGER.warning("Failed to queue Telegram media reply because payload storage failed")
        return
    pending = _telegram_pending_media_replies()
    pending.append(
        {
            "queued_at": time.time(),
            "chat_id": int(chat_id),
            "reply_to_message_id": reply_to_message_id,
            "filename": filename,
            "mimetype": mimetype,
            "caption": caption,
            **payload_info,
        }
    )
    _persist_telegram_pending_media_replies(pending)
    LOGGER.info(
        "Queued Telegram media reply for later delivery (%d pending)",
        len(_telegram_pending_media_replies()),
    )

async def _send_telegram_media_payload(
    bot: Any,
    *,
    chat_id: int,
    media_bytes: bytes,
    filename: str,
    mimetype: str,
    caption: str = "",
    reply_to_message_id: Optional[int] = None,
    queue_on_failure: bool = True,
) -> bool:
    normalized_mimetype = str(mimetype or "application/octet-stream").lower()
    if bot is None:
        if queue_on_failure:
            _queue_telegram_media_reply(
                chat_id=chat_id,
                media_bytes=media_bytes,
                filename=filename,
                mimetype=normalized_mimetype,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
        return False
    for attempt in range(1, TELEGRAM_SEND_RETRY_ATTEMPTS + 1):
        try:
            media_file = io.BytesIO(media_bytes)
            media_file.name = filename or ("video.mp4" if normalized_mimetype.startswith("video/") else "image.jpg")
            common_kwargs = {
                "chat_id": int(chat_id),
                "caption": caption or None,
                "reply_to_message_id": reply_to_message_id,
                "connect_timeout": TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                "read_timeout": TELEGRAM_READ_TIMEOUT_SECONDS,
                "write_timeout": TELEGRAM_MEDIA_WRITE_TIMEOUT_SECONDS,
                "pool_timeout": TELEGRAM_POOL_TIMEOUT_SECONDS,
            }
            if normalized_mimetype.startswith("image/"):
                await bot.send_photo(photo=media_file, **common_kwargs)
            elif normalized_mimetype.startswith("video/"):
                await bot.send_video(video=media_file, supports_streaming=True, **common_kwargs)
            else:
                await bot.send_document(document=media_file, **common_kwargs)
            LOGGER.info("Sent Telegram media reply to chat_id=%s filename=%s", chat_id, filename)
            return True
        except Exception as exc:
            LOGGER.warning(
                "Failed to send Telegram media reply attempt %d/%d chat_id=%s filename=%s: %r",
                attempt,
                TELEGRAM_SEND_RETRY_ATTEMPTS,
                chat_id,
                filename,
                exc,
            )
            if BadRequest is not None and isinstance(exc, BadRequest):
                break
            if attempt >= TELEGRAM_SEND_RETRY_ATTEMPTS:
                break
            delay = TELEGRAM_SEND_RETRY_BASE_DELAY_SECONDS * attempt
            if RetryAfter is not None and isinstance(exc, RetryAfter):
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    delay = max(delay, float(retry_after))
            await asyncio.sleep(delay)
    if queue_on_failure:
        _queue_telegram_media_reply(
            chat_id=chat_id,
            media_bytes=media_bytes,
            filename=filename,
            mimetype=normalized_mimetype,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )
    return False

async def _flush_telegram_pending_voice_replies() -> None:
    pending = _telegram_pending_voice_replies()
    if not pending:
        return
    bot = _get_active_telegram_bot()
    if bot is None:
        return
    remaining: List[Dict[str, Any]] = []
    delivered = 0
    for entry in list(pending):
        try:
            audio_bytes = base64.b64decode(str(entry.get("data") or ""), validate=False)
        except Exception:
            delivered += 1
            continue
        sent = await _send_telegram_voice_payload(
            bot,
            chat_id=int(entry.get("chat_id")),
            audio_bytes=audio_bytes,
            filename=str(entry.get("filename") or "voice-reply.ogg"),
            reply_to_message_id=entry.get("reply_to_message_id"),
            queue_on_failure=False,
        )
        if sent:
            delivered += 1
        else:
            remaining.append(entry)
            break
    if delivered:
        LOGGER.info("Delivered %d queued Telegram voice repl%s", delivered, "y" if delivered == 1 else "ies")
    STATE.telegram_pending_voice_replies = remaining + pending[delivered + len(remaining):]
    _persist_telegram_pending_voice_replies(STATE.telegram_pending_voice_replies)

async def _flush_telegram_pending_media_replies() -> None:
    pending = _telegram_pending_media_replies()
    if not pending:
        return
    bot = _get_active_telegram_bot()
    if bot is None:
        return
    remaining: List[Dict[str, Any]] = []
    delivered = 0
    for entry in list(pending):
        media_bytes = read_media_payload(entry)
        if media_bytes is None:
            delete_media_payload(entry)
            delivered += 1
            continue
        sent = await _send_telegram_media_payload(
            bot,
            chat_id=int(entry.get("chat_id")),
            media_bytes=media_bytes,
            filename=str(entry.get("filename") or "attachment"),
            mimetype=str(entry.get("mimetype") or "application/octet-stream"),
            caption=str(entry.get("caption") or ""),
            reply_to_message_id=entry.get("reply_to_message_id"),
            queue_on_failure=False,
        )
        if sent:
            delete_media_payload(entry)
            delivered += 1
        else:
            remaining.append(entry)
            break
    if delivered:
        LOGGER.info("Delivered %d queued Telegram media repl%s", delivered, "y" if delivered == 1 else "ies")
    STATE.telegram_pending_media_replies = remaining + pending[delivered + len(remaining):]
    _persist_telegram_pending_media_replies(STATE.telegram_pending_media_replies)

def _telegram_typing_action() -> str:
    return getattr(ChatAction, "TYPING", "typing")

async def _send_telegram_chat_action_via_bot(bot, chat_id: Optional[int], action: Optional[str] = None) -> None:
    if bot is None or chat_id is None:
        return
    try:
        await bot.send_chat_action(chat_id=int(chat_id), action=action or _telegram_typing_action())
    except RetryAfter as exc:
        LOGGER.debug("Telegram chat action rate-limited for chat_id=%s: %s", chat_id, exc)
    except (TimedOut, NetworkError, BadRequest) as exc:
        LOGGER.debug("Telegram chat action failed for chat_id=%s: %s", chat_id, exc)
    except Exception as exc:
        LOGGER.debug("Telegram chat action failed for chat_id=%s: %s", chat_id, exc)

async def _send_telegram_chat_action_to_session(session_id: str, action: Optional[str] = None) -> None:
    bindings = getattr(STATE, "telegram_chat_bindings", None) or {}
    binding = bindings.get(str(session_id))
    if not binding:
        binding = bindings.get(_base_conversation_session_id(session_id))
    bot = _get_active_telegram_bot()
    if not binding or bot is None:
        return
    await _send_telegram_chat_action_via_bot(bot, binding.get("chat_id"), action=action)

async def _send_telegram_typing_for_update(update: Any) -> None:
    message = getattr(update, "message", None)
    if message is None:
        return
    bot = message.get_bot() if hasattr(message, "get_bot") else _get_active_telegram_bot()
    chat_id = getattr(message, "chat_id", None)
    await _send_telegram_chat_action_via_bot(bot, chat_id, action=_telegram_typing_action())

async def _telegram_typing_heartbeat(session_id: str, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        await _send_telegram_chat_action_to_session(session_id, action=_telegram_typing_action())
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=TELEGRAM_TYPING_HEARTBEAT_SECONDS)
        except asyncio.TimeoutError:
            continue

async def _send_telegram_text_via_bot(
    bot,
    chat_id: int,
    text: str,
    *,
    split_text: bool = True,
    reply_to_message_id: Optional[int] = None,
    prefer_rich_draft: bool = False,
) -> None:
    if bot is None or chat_id is None or text is None:
        return

    rendered = str(text)
    if not rendered:
        return

    if prefer_rich_draft and await _send_telegram_rich_draft_final_via_bot(
        bot,
        chat_id,
        rendered,
        reply_to_message_id=reply_to_message_id,
    ):
        return

    limit = _telegram_reply_limit()
    if not split_text and len(rendered) > limit:
        LOGGER.warning("Telegram reply exceeds configured limit with split_text disabled (len=%d limit=%d)", len(rendered), limit)
    chunks = _split_text_for_transport(rendered, limit) if split_text else [rendered]
    if len(chunks) > 1:
        LOGGER.info("Splitting Telegram reply into %d chunks (limit=%d)", len(chunks), limit)

    for idx, chunk in enumerate(chunks, start=1):
        chunk_reply_to_message_id = reply_to_message_id if idx == 1 else None
        sent = False
        for attempt in range(1, TELEGRAM_SEND_RETRY_ATTEMPTS + 1):
            try:
                LOGGER.info(
                    "Sending Telegram reply chunk %d/%d attempt %d/%d chat_id=%s len=%d",
                    idx,
                    len(chunks),
                    attempt,
                    TELEGRAM_SEND_RETRY_ATTEMPTS,
                    chat_id,
                    len(chunk),
                )
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    reply_to_message_id=chunk_reply_to_message_id,
                    connect_timeout=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                    read_timeout=TELEGRAM_READ_TIMEOUT_SECONDS,
                    write_timeout=TELEGRAM_WRITE_TIMEOUT_SECONDS,
                    pool_timeout=TELEGRAM_POOL_TIMEOUT_SECONDS,
                )
                sent = True
                break
            except Exception as exc:
                LOGGER.warning(
                    "Failed to send Telegram reply chunk %d/%d attempt %d/%d chat_id=%s len=%d: %r",
                    idx,
                    len(chunks),
                    attempt,
                    TELEGRAM_SEND_RETRY_ATTEMPTS,
                    chat_id,
                    len(chunk),
                    exc,
                )
                if BadRequest is not None and isinstance(exc, BadRequest):
                    break
                if attempt >= TELEGRAM_SEND_RETRY_ATTEMPTS:
                    break

                delay = TELEGRAM_SEND_RETRY_BASE_DELAY_SECONDS * attempt
                if RetryAfter is not None and isinstance(exc, RetryAfter):
                    retry_after = getattr(exc, "retry_after", None)
                    if retry_after is not None:
                        delay = max(delay, float(retry_after))
                elif TimedOut is not None and isinstance(exc, TimedOut):
                    delay = max(delay, 2.0 * attempt)
                elif NetworkError is not None and isinstance(exc, NetworkError):
                    delay = max(delay, 1.0 * attempt)

                await asyncio.sleep(delay)

        if sent:
            continue

        if httpx is not None and chat_id is not None:
            try:
                timeout = httpx.Timeout(
                    connect=TELEGRAM_CONNECT_TIMEOUT_SECONDS,
                    read=TELEGRAM_READ_TIMEOUT_SECONDS,
                    write=TELEGRAM_WRITE_TIMEOUT_SECONDS,
                    pool=TELEGRAM_POOL_TIMEOUT_SECONDS,
                )
                payload = {
                    "chat_id": chat_id,
                    "text": chunk,
                }
                if chunk_reply_to_message_id is not None:
                    payload["reply_to_message_id"] = chunk_reply_to_message_id

                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(f"{bot.base_url}/sendMessage", json=payload)
                    response.raise_for_status()
                    result = response.json()
                if not result.get("ok", False):
                    raise RuntimeError(result.get("description") or "Telegram Bot API returned ok=false")

                LOGGER.info(
                    "Telegram raw Bot API fallback succeeded for chunk %d/%d chat_id=%s len=%d",
                    idx,
                    len(chunks),
                    chat_id,
                    len(chunk),
                )
                continue
            except Exception as exc:
                LOGGER.warning(
                    "Telegram raw Bot API fallback failed for chunk %d/%d chat_id=%s len=%d: %r",
                    idx,
                    len(chunks),
                    chat_id,
                    len(chunk),
                    exc,
                )

        LOGGER.error(
            "Giving up on Telegram reply chunk %d/%d chat_id=%s len=%d",
            idx,
            len(chunks),
            chat_id,
            len(chunk),
        )
        break

async def _send_telegram_text_to_session(session_id: str, text: str, *, split_text: bool = True, reply_to_message_id: Optional[int] = None) -> None:
    bindings = getattr(STATE, "telegram_chat_bindings", None) or {}
    binding = bindings.get(str(session_id))
    if not binding:
        binding = bindings.get(_base_conversation_session_id(session_id))
    bot = _get_active_telegram_bot()
    if not binding or bot is None:
        LOGGER.warning("Telegram session binding or bot missing for session_id=%s", session_id)
        return
    bound_reply_id = reply_to_message_id if reply_to_message_id is not None else binding.get("reply_to_message_id")
    await _send_telegram_text_via_bot(
        bot,
        int(binding["chat_id"]),
        text,
        split_text=split_text,
        reply_to_message_id=bound_reply_id,
        prefer_rich_draft=True,
    )

async def _send_telegram_voice_to_session(session_id: str, file_path: str, *, reply_to_message_id: Optional[int] = None) -> bool:
    bindings = getattr(STATE, "telegram_chat_bindings", None) or {}
    binding = bindings.get(str(session_id))
    if not binding:
        binding = bindings.get(_base_conversation_session_id(session_id))
    bot = _get_active_telegram_bot()
    if not binding:
        LOGGER.warning("Telegram session binding missing for voice reply session_id=%s", session_id)
        return False
    if not file_path or not os.path.isfile(file_path):
        LOGGER.warning("Telegram voice reply file missing for session_id=%s: %s", session_id, file_path)
        return False

    bound_reply_id = reply_to_message_id if reply_to_message_id is not None else binding.get("reply_to_message_id")
    chat_id = int(binding["chat_id"])
    try:
        with open(file_path, "rb") as voice_handle:
            audio_bytes = voice_handle.read()
    except OSError as exc:
        LOGGER.warning("Telegram voice reply file unreadable for session_id=%s: %s", session_id, exc)
        return False
    pending_before = len(_telegram_pending_voice_replies())
    sent = await _send_telegram_voice_payload(
        bot,
        chat_id=chat_id,
        audio_bytes=audio_bytes,
        filename=os.path.basename(file_path) or "voice-reply.ogg",
        reply_to_message_id=bound_reply_id,
        queue_on_failure=True,
    )
    return bool(sent or len(_telegram_pending_voice_replies()) > pending_before)

async def _send_telegram_media_attachments_to_session(
    session_id: str,
    attachments: List[Dict[str, Any]],
    *,
    reply_to_message_id: Optional[int] = None,
) -> bool:
    bindings = getattr(STATE, "telegram_chat_bindings", None) or {}
    binding = bindings.get(str(session_id))
    if not binding:
        binding = bindings.get(_base_conversation_session_id(session_id))
    if not binding:
        LOGGER.warning("Telegram session binding missing for media reply session_id=%s", session_id)
        return False

    try:
        from shared.media_messaging import attachment_mimetype, load_attachment_bytes
        from shared.openclaw_gateway import safe_filename
    except Exception as exc:
        LOGGER.warning("Telegram media reply helpers unavailable: %s", exc)
        return False

    bot = _get_active_telegram_bot()
    chat_id = int(binding["chat_id"])
    bound_reply_id = reply_to_message_id if reply_to_message_id is not None else binding.get("reply_to_message_id")
    sent_or_queued = False
    pending_before = len(_telegram_pending_media_replies())
    for attachment in attachments or []:
        media_bytes, error = load_attachment_bytes(attachment)
        if media_bytes is None:
            LOGGER.warning("Skipping Telegram media reply attachment: %s", error)
            continue
        mimetype = attachment_mimetype(attachment)
        filename = safe_filename(attachment.get("filename") or attachment.get("path"), mimetype)
        caption = str(attachment.get("caption") or "")
        if await _send_telegram_media_payload(
            bot,
            chat_id=chat_id,
            media_bytes=media_bytes,
            filename=filename,
            mimetype=mimetype,
            caption=caption,
            reply_to_message_id=bound_reply_id,
            queue_on_failure=True,
        ):
            sent_or_queued = True
        elif len(_telegram_pending_media_replies()) > pending_before:
            sent_or_queued = True
            pending_before = len(_telegram_pending_media_replies())
    return sent_or_queued

async def _reply_telegram_text(update: Any, text: str, *, split_text: bool = True) -> None:
    message = getattr(update, "message", None)
    if message is None or text is None:
        return

    bot = message.get_bot()
    chat_id = getattr(message, "chat_id", None)
    original_message_id = getattr(message, "message_id", None)
    identity, raw_user_id, _ = _resolve_telegram_execution_identity(update)
    _remember_telegram_chat_binding(
        identity.canonical_session_id,
        chat_id,
        raw_user_id,
        original_message_id,
    )
    if _telegram_pairing_payload_needs_document(text):
        sent = await _send_telegram_pairing_rich_message_via_bot(
            bot,
            chat_id,
            text,
            reply_to_message_id=original_message_id,
        )
        if not sent:
            sent = await _send_telegram_pairing_document_via_bot(
                bot,
                chat_id,
                text,
                reply_to_message_id=original_message_id,
            )
        if sent:
            return
        text = "Pairing response was too large for Telegram text, and Telegram refused rich-message and file fallbacks. Retry pairing with another messaging partner."
        split_text = False
    await _send_telegram_text_via_bot(
        bot,
        chat_id,
        text,
        split_text=split_text,
        reply_to_message_id=original_message_id,
    )

def _schedule_telegram_chat_delivery(
    *,
    message: str,
    identity,
    chat_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
    context: Optional[List[Dict[str, Any]]] = None,
    reply_to_message_id: Optional[int] = None,
    reset: bool = False,
) -> asyncio.Task[None]:
    """Run Telegram chat processing in the background and deliver replies by session binding."""

    async def _run() -> None:
        from rest_api import ChatRequest, process_chat_message

        execution_manager = get_session_execution_manager()
        execution_state = {"queue_position": 0}
        typing_stop_event = asyncio.Event()
        await _send_telegram_chat_action_to_session(
            identity.canonical_session_id,
            action=_telegram_typing_action(),
        )
        typing_task = asyncio.create_task(
            _telegram_typing_heartbeat(identity.canonical_session_id, typing_stop_event)
        )

        async def _on_execution_status(status) -> None:
            execution_state["queue_position"] = int(getattr(status, "queue_position", 0) or 0)

        async def _on_chunk(chunk: Dict[str, Any]) -> None:
            return

        async def _send_immediate_media_reply(attachments: List[Dict[str, Any]]) -> bool:
            return await _send_telegram_media_attachments_to_session(
                identity.canonical_session_id,
                attachments,
                reply_to_message_id=reply_to_message_id,
            )

        async def _execute_chat_request():
            request_metadata = dict(metadata or {"client": "telegram"})
            request_metadata["canonical_owner_key"] = identity.owner_key
            request_metadata.update(_build_conversation_metadata(identity, reset=reset))
            if chat_id is not None:
                request_metadata["reply_target"] = {
                    "transport": "telegram",
                    "chat_id": int(chat_id),
                }
                if reply_to_message_id is not None:
                    request_metadata["reply_target"]["reply_to_message_id"] = int(reply_to_message_id)
            session_execution = dict(request_metadata.get("session_execution") or {})
            session_execution["queue_position"] = execution_state["queue_position"]
            request_metadata["session_execution"] = session_execution
            chat_req = ChatRequest(
                message=message,
                user_id=identity.canonical_user_id,
                session_id=identity.canonical_session_id,
                metadata=request_metadata,
                context=context or [],
            )
            return await process_chat_message(
                chat_req,
                ai_agent_url=f"http://127.0.0.1:{getattr(STATE, 'main_server_port', 8081)}",
                on_chunk=_on_chunk,
                on_media_reply=_send_immediate_media_reply,
            )

        try:
            chat_resp = await execution_manager.submit_turn(
                identity,
                _execute_chat_request,
                on_status=_on_execution_status,
                label="telegram-chat",
            )
            reply_text = chat_resp.response or "(no response)"
            agent_name = _build_chat_response_agent_metadata(
                chat_resp,
                getattr(chat_resp, "metadata", {}) or {},
            ).get("agent_display_name") or get_configured_server_name()
            media_reply_attachments = list(getattr(chat_resp, "media_reply_attachments", []) or [])
            if media_reply_attachments:
                await _send_telegram_media_attachments_to_session(
                    identity.canonical_session_id,
                    media_reply_attachments,
                    reply_to_message_id=reply_to_message_id,
                )
            voice_reply_audio_path = getattr(chat_resp, "voice_reply_audio_path", None)
            if voice_reply_audio_path:
                voice_sent = await _send_telegram_voice_to_session(
                    identity.canonical_session_id,
                    voice_reply_audio_path,
                    reply_to_message_id=reply_to_message_id,
                )
                try:
                    from shared.voice_messaging import cleanup_paths as _vm_cleanup

                    _vm_cleanup(voice_reply_audio_path)
                except Exception:
                    pass
                if voice_sent:
                    LOGGER.info(
                        "Sent Telegram voice-note reply (session_id=%s | message_id=%s | user_id=%s)",
                        chat_resp.session_id or identity.canonical_session_id,
                        chat_resp.message_id or "NULL",
                        agent_name,
                    )
                    return
                LOGGER.warning("Telegram voice-note reply failed; falling back to text reply")
            formatted = f"{reply_text}\n\n~ {agent_name}"
            truncated = reply_text if len(reply_text) <= 100 else reply_text[:100] + "..."
            LOGGER.info(
                "Chat API response (session_id=%s | message_id=%s | user_id=%s | resp_time_ms=%d): %s",
                chat_resp.session_id or identity.canonical_session_id,
                chat_resp.message_id or "NULL",
                agent_name,
                (chat_resp.metadata or {}).get("processing_time_ms", 0),
                truncated,
            )
            await _send_telegram_text_to_session(
                identity.canonical_session_id,
                formatted,
                reply_to_message_id=reply_to_message_id,
            )
        except SessionQueueFullError as e:
            LOGGER.info(
                "Telegram chat rejected for %s because the session queue is full",
                identity.canonical_session_id,
            )
            await _send_telegram_text_to_session(
                identity.canonical_session_id,
                e.status.message,
                split_text=False,
                reply_to_message_id=reply_to_message_id,
            )
        except SessionTurnTimeoutError as e:
            LOGGER.info("Telegram chat timed out for %s", identity.canonical_session_id)
            await _send_telegram_text_to_session(
                identity.canonical_session_id,
                e.status.message,
                split_text=False,
                reply_to_message_id=reply_to_message_id,
            )
        except Exception as e:
            LOGGER.warning(
                "Error processing Telegram chat request for %s: %s",
                identity.canonical_session_id,
                e,
            )
            await _send_telegram_text_to_session(
                identity.canonical_session_id,
                "Sorry, something went wrong.",
                split_text=False,
                reply_to_message_id=reply_to_message_id,
            )
        finally:
            typing_stop_event.set()
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    task = asyncio.create_task(_run())
    background_tasks.add(task)

    def _done(done_task: asyncio.Task) -> None:
        background_tasks.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            return
        except Exception as inspect_err:
            LOGGER.debug(
                "Failed to inspect Telegram chat background task for %s: %s",
                identity.canonical_session_id,
                inspect_err,
            )
            return
        if exc is not None:
            LOGGER.warning(
                "Telegram chat background task failed for %s: %s",
                identity.canonical_session_id,
                exc,
            )

    task.add_done_callback(_done)
    return task

async def _telegram_error_handler(update, context) -> None:
    LOGGER.exception("Unhandled Telegram update error: %s", getattr(context, "error", None))

# --- New: Conversation + logging handlers ---
async def pair_command(update: Any, context: Any):
    try:
        if not await _ensure_telegram_update_authorized(update):
            return ConversationHandler.END
        # Delegate to centralized pairing router
        text = update.message.text if update.message else ""
        chat = update.effective_chat
        chat_id = str(getattr(chat, 'id', 'unknown'))
        response = await pairing_router.process_message(text, platform="telegram", sender_id=chat_id)
        # FRAGMENT_CONSUMED is never returned for /pair but guard for safety
        if response and response != pairing_router.FRAGMENT_CONSUMED and update and update.message:
            await _reply_telegram_text(update, response, split_text=False)
    except Exception as e:
        LOGGER.warning("Error processing /pair command via router: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "/pair command received. Error occurred during processing.", split_text=False)
    return ConversationHandler.END

async def new_conversation_command(update: Any, context: Any):
    try:
        if not await _ensure_telegram_update_authorized(update):
            return ConversationHandler.END
        text = update.message.text.strip() if update and update.message and update.message.text else ""
        chat = update.effective_chat
        chat_id = str(getattr(chat, "id", "unknown"))
        response = await pairing_router.process_message(text, platform="telegram", sender_id=chat_id)
        if response and update and update.message:
            await _reply_telegram_text(update, response, split_text=False)
        elif update and update.message:
            await _reply_telegram_text(
                update,
                "Send the command by itself to start a new conversation.",
                split_text=False,
            )
    except Exception as e:
        LOGGER.warning("Error processing /new command via router: %s", e)
        if update and update.message:
            await _reply_telegram_text(
                update,
                "Unable to start a new conversation right now.",
                split_text=False,
            )
    return ConversationHandler.END

async def context_command(update: Any, context: Any):
    try:
        if not await _ensure_telegram_update_authorized(update):
            return ConversationHandler.END
        text = update.message.text.strip() if update and update.message and update.message.text else "/context"
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )
        _remember_telegram_chat_binding(
            _base_conversation_session_id(identity.canonical_session_id),
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )
        if update and update.message:
            await _send_telegram_typing_for_update(update)
        _schedule_telegram_chat_delivery(
            message=text,
            identity=identity,
            chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error processing /context command via router: %s", e)
        if update and update.message:
            await _reply_telegram_text(
                update,
                "Unable to load context health right now.",
                split_text=False,
            )
    return ConversationHandler.END

async def autopair_command(update: Any, context: Any):
    """Handle /autopair command.

    Expected single-message format (normal accounts / short payloads):
        /autopair
        <compressed-or-encrypted-payload>

    Multi-message format (newer Telegram accounts that split at newlines and 4096-char limit):
        Message 1: /autopair                  → starts fragment buffer (silent, no reply)
        Message 2: <payload_part_1>           → accumulated by log_any_text fragment guard
        Message N: <payload_part_N>           → assembled and processed once parse succeeds

    All security modes (normal / secure / secure_professional) are handled transparently
    by the pairing_router after reassembly.
    """
    try:
        if not await _ensure_telegram_update_authorized(update):
            return ConversationHandler.END
        if not update or not update.message or not update.message.text:
            LOGGER.warning("Autopair command received without message text")
            return ConversationHandler.END
        text = update.message.text.strip()
        chat = update.effective_chat
        chat_id = str(getattr(chat, 'id', 'unknown'))
        # Delegate to centralized pairing router.
        # Returns FRAGMENT_CONSUMED when Telegram split the message (no payload body).
        # In that case we stay silent - continuation messages are handled in log_any_text.
        response = await pairing_router.process_message(text, platform="telegram", sender_id=chat_id)
        if response and response != pairing_router.FRAGMENT_CONSUMED and update and update.message:
            await _reply_telegram_text(
                update,
                response,
                split_text=False,
            )
    except Exception as e:
        LOGGER.error("Error in autopair_command via router: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Internal server error during autopair", split_text=False)
    return ConversationHandler.END

async def allow_command(update: Any, context: Any):
    try:
        _remember_telegram_sender(update, message_kind="allow")
        user = getattr(update, "effective_user", None)
        sender_id = _normalize_telegram_sender_id(getattr(user, "id", None))
        code = _extract_telegram_allow_code(getattr(getattr(update, "message", None), "text", None))
        _, response = _redeem_telegram_allow_code(code, sender_id)
        if update and update.message:
            await _reply_telegram_text(update, response, split_text=False)
    except Exception as exc:
        LOGGER.warning("Error processing Telegram /allow command: %s", exc)
        if update and update.message:
            await _reply_telegram_text(update, "Telegram approval failed.", split_text=False)

async def cancel_command(update: Any, context: Any):
    try:
        user = update.effective_user
        LOGGER.info("Telegram /cancel received: user_id=%s", getattr(user, 'id', None))
        if update and update.message:
            await _reply_telegram_text(update, "Cancelled.", split_text=False)
    except Exception:
        pass
    return ConversationHandler.END

async def log_any_text(update: Any, context: Any):
    """
    Handle any incoming text message by:
    1) Sending a native Telegram typing indicator to acknowledge receipt.
    2) Queuing background AI processing for the bound Telegram chat/session.
    3) Delivering the final response later from the server via the Telegram bot.

    The POST payload must include:
    - message: raw text
    - user_id: Telegram username if present; otherwise the numeric user ID
    - session_id: chat_id
    - metadata: {"client": "telegram"}
    """
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        user = update.effective_user
        chat = update.effective_chat
        text = update.message.text if update and update.message else ""

        # ── Autopair fragment interception ──────────────────────────────────────
        # Newer Telegram accounts/versions split long /autopair messages into
        # multiple separate messages:
        #   1. "/autopair"           → handled by autopair_command (starts buffer)
        #   2. "<payload_chunk_1>"  → arrives here as a plain-text message
        #   3. "<payload_chunk_N>"  → reassembled, processed, answer sent
        #
        # We intercept BEFORE the AI to prevent raw base64 blobs from being
        # forwarded to Ollama/Gemini (which would hog the CPU for minutes).
        if pairing_router is not None and text:
            _raw_chat_id = str(getattr(chat, "id", "unknown"))
            _has_pending = pairing_router.has_pending_autopair_buffer("telegram", _raw_chat_id)
            _raw_fragment = _looks_like_raw_payload_fragment(text)
            if _has_pending or _raw_fragment:
                try:
                    _frag_resp = await pairing_router.process_message(
                        text, platform="telegram", sender_id=_raw_chat_id,
                    )
                except Exception as _frag_exc:
                    LOGGER.warning(
                        "[AUTOPAIR] Fragment dispatch error for chat_id=%s: %s",
                        _raw_chat_id, _frag_exc,
                    )
                    _frag_resp = None
                if _frag_resp is not None:
                    # FRAGMENT_CONSUMED → more pieces needed; anything else → send reply
                    if _frag_resp != pairing_router.FRAGMENT_CONSUMED and update and update.message:
                        await _reply_telegram_text(
                            update,
                            _frag_resp,
                            split_text=False,
                        )
                    return  # Never forward autopair fragments to the AI
                # process_message returned None (no pending buffer) but text looked like
                # a raw payload - suppress AI processing to avoid a CPU hog.
                if _raw_fragment:
                    LOGGER.warning(
                        "[AUTOPAIR] Suppressing AI for apparent stale autopair fragment from "
                        "chat_id=%s (no pending buffer, len=%d). "
                        "User should send /autopair again.",
                        _raw_chat_id, len(text),
                    )
                    return
        # ── End autopair fragment interception ──────────────────────────────────

        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        start_new_thread, normalized_text, control_only, _ = _extract_conversation_request(text, {})
        if start_new_thread:
            identity = _resolve_conversation_identity(identity, start_new_thread=True)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )
        _remember_telegram_chat_binding(
            _base_conversation_session_id(identity.canonical_session_id),
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        LOGGER.info(
            "Telegram text message: user_id=%s username=%s chat_id=%s text=%s",
            getattr(user, "id", None),
            getattr(user, "username", None),
            getattr(chat, "id", None),
            normalized_text,
        )
        if start_new_thread and control_only:
            if update and update.message:
                await _reply_telegram_text(update, "Started a new conversation.", split_text=False)
            return
        if update and update.message: await _send_telegram_typing_for_update(update)

        _schedule_telegram_chat_delivery(
            message=normalized_text,
            identity=identity,
          chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[],
            reply_to_message_id=reply_to_message_id,
            reset=start_new_thread,
        )
    except Exception as e:
        LOGGER.warning("Error logging text message: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Sorry, something went wrong.")

# --- Telegram media handlers ---
import io  # for in-memory downloads
import base64  # used to encode attachment bytes for JSON context

async def log_photo(update: Any, context: Any):
    """Handle photo messages and queue background delivery."""
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        photo = update.message.photo[-1] if (update and update.message and update.message.photo) else None
        caption = update.message.caption if (update and update.message) else ""
        if not photo:
            return
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        if update and update.message:
            await _send_telegram_typing_for_update(update)

        # Download file bytes using documented in-memory method
        file = await context.bot.get_file(photo.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data_ba = buf.getvalue()
        data_b64 = base64.b64encode(bytes(data_ba)).decode('ascii')
        filename = f"telegram_photo_{photo.file_id}.jpg"
        mimetype = getattr(photo, 'mime_type', None) or "image/jpeg"
        # Basic media metadata
        meta = {
            "width": getattr(photo, "width", None),
            "height": getattr(photo, "height", None),
            "file_size": getattr(photo, "file_size", None),
        }

        message_text = caption or f"[Attachment: {filename}]"
        _schedule_telegram_chat_delivery(
            message=message_text,
            identity=identity,
          chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[{
                "attachments": [{
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": data_b64,
                    "size_bytes": len(data_ba),
                    "file_id": getattr(photo, "file_id", None),
                    "meta": {k: v for k, v in meta.items() if v is not None}
                }],
                "source": "telegram"
            }],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error logging photo message: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Sorry, something went wrong.")

async def log_document(update: Any, context: Any):
    """Handle document attachments and queue background delivery."""
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        doc = update.message.document if (update and update.message and update.message.document) else None
        caption = update.message.caption if (update and update.message) else ""
        if not doc:
            return
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        if update and update.message:
            await _send_telegram_typing_for_update(update)

        file = await context.bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data_ba = buf.getvalue()
        data_b64 = base64.b64encode(bytes(data_ba)).decode('ascii')
        filename = getattr(doc, 'file_name', None) or f"telegram_document_{doc.file_id}"
        mimetype = getattr(doc, 'mime_type', None) or "application/octet-stream"
        meta = {
            "file_size": getattr(doc, "file_size", None),
        }

        message_text = caption or f"[Attachment: {filename}]"
        _schedule_telegram_chat_delivery(
            message=message_text,
            identity=identity,
          chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[{
                "attachments": [{
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": data_b64,
                    "size_bytes": len(data_ba),
                    "file_id": getattr(doc, "file_id", None),
                    "meta": {k: v for k, v in meta.items() if v is not None}
                }],
                "source": "telegram"
            }],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error logging document message: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Sorry, something went wrong.")

async def log_video(update: Any, context: Any):
    """Handle video messages and queue background delivery."""
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        video = update.message.video if (update and update.message and update.message.video) else None
        caption = update.message.caption if (update and update.message) else ""
        if not video:
            return
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        if update and update.message:
            await _send_telegram_typing_for_update(update)

        reported_size = getattr(video, "file_size", None)
        if _telegram_file_size_bytes(reported_size) > TELEGRAM_BOT_API_GET_FILE_DOWNLOAD_LIMIT_BYTES:
            message_text = _telegram_large_media_message_for_agent("video", caption, reported_size)
            _schedule_telegram_chat_delivery(
                message=message_text,
                identity=identity,
                chat_id=chat_id,
                metadata={
                    "client": "telegram",
                    "telegram_user_id": raw_user_id,
                    "telegram_media_unavailable": True,
                    "telegram_media_unavailable_reason": "cloud_bot_api_getfile_limit",
                },
                context=[{
                    "source": "telegram",
                    "meta": {
                        "kind": "video",
                        "file_size": reported_size,
                        "download_limit_bytes": TELEGRAM_BOT_API_GET_FILE_DOWNLOAD_LIMIT_BYTES,
                    },
                }],
                reply_to_message_id=reply_to_message_id,
            )
            if update and update.message:
                await _reply_telegram_text(
                    update,
                    _telegram_cloud_download_limit_notice("video", reported_size),
                    split_text=False,
                )
            return

        file = await context.bot.get_file(video.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data_ba = buf.getvalue()
        data_b64 = base64.b64encode(bytes(data_ba)).decode('ascii')
        filename = getattr(video, 'file_name', None) or f"telegram_video_{video.file_id}.mp4"
        mimetype = getattr(video, 'mime_type', None) or "video/mp4"
        meta = {
            "width": getattr(video, "width", None),
            "height": getattr(video, "height", None),
            "duration": getattr(video, "duration", None),
            "file_size": getattr(video, "file_size", None),
        }

        message_text = caption or f"[Attachment: {filename}]"
        _schedule_telegram_chat_delivery(
            message=message_text,
            identity=identity,
            chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[{
                "attachments": [{
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": data_b64,
                    "size_bytes": len(data_ba),
                    "file_id": getattr(video, "file_id", None),
                    "meta": {k: v for k, v in meta.items() if v is not None}
                }],
                "source": "telegram"
            }],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error logging video message: %s", e)
        if update and update.message:
            if _telegram_file_too_big_error(e):
                size_bytes = getattr(
                    update.message.video if getattr(update.message, "video", None) else None,
                    "file_size",
                    None,
                )
                await _reply_telegram_text(
                    update,
                    _telegram_cloud_download_limit_notice("video", size_bytes),
                    split_text=False,
                )
                return
            await _reply_telegram_text(update, "Sorry, something went wrong.")

async def log_audio(update: Any, context: Any):
    """Handle audio messages and queue background delivery."""
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        audio = update.message.audio if (update and update.message and update.message.audio) else None
        caption = update.message.caption if (update and update.message) else ""
        if not audio:
            return
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        if update and update.message:
            await _send_telegram_typing_for_update(update)

        file = await context.bot.get_file(audio.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data_ba = buf.getvalue()
        data_b64 = base64.b64encode(bytes(data_ba)).decode('ascii')
        filename = getattr(audio, 'file_name', None) or f"telegram_audio_{audio.file_id}.mp3"
        mimetype = getattr(audio, 'mime_type', None) or "audio/mpeg"
        meta = {
            "duration": getattr(audio, "duration", None),
            "file_size": getattr(audio, "file_size", None),
        }

        message_text = caption or f"[Attachment: {filename}]"
        _schedule_telegram_chat_delivery(
            message=message_text,
            identity=identity,
          chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[{
                "attachments": [{
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": data_b64,
                    "size_bytes": len(data_ba),
                    "file_id": getattr(audio, "file_id", None),
                    "meta": {k: v for k, v in meta.items() if v is not None}
                }],
                "source": "telegram"
            }],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error logging audio message: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Sorry, something went wrong.")

async def log_voice(update: Any, context: Any):
    """Handle voice notes (ogg/opus) and queue background delivery."""
    try:
        if not await _ensure_telegram_update_authorized(update):
            return
        voice = update.message.voice if (update and update.message and update.message.voice) else None
        if not voice:
            return
        identity, raw_user_id, chat_id = _resolve_telegram_execution_identity(update)
        reply_to_message_id = getattr(getattr(update, "message", None), "message_id", None)
        _remember_telegram_chat_binding(
            identity.canonical_session_id,
            chat_id,
            raw_user_id,
            reply_to_message_id,
        )

        if update and update.message:
            await _send_telegram_typing_for_update(update)

        file = await context.bot.get_file(voice.file_id)
        buf = io.BytesIO()
        await file.download_to_memory(buf)
        data_ba = buf.getvalue()
        data_b64 = base64.b64encode(bytes(data_ba)).decode('ascii')
        filename = f"telegram_voice_{voice.file_id}.ogg"
        mimetype = getattr(voice, 'mime_type', None) or "audio/ogg"
        meta = {
            "duration": getattr(voice, "duration", None),
            "file_size": getattr(voice, "file_size", None),
        }

        message_text = f"[Attachment: {filename}]"
        _schedule_telegram_chat_delivery(
            message=message_text,
            identity=identity,
          chat_id=chat_id,
            metadata={"client": "telegram", "telegram_user_id": raw_user_id},
            context=[{
                "attachments": [{
                    "filename": filename,
                    "mimetype": mimetype,
                    "data": data_b64,
                    "size_bytes": len(data_ba),
                    "file_id": getattr(voice, "file_id", None),
                    "meta": {k: v for k, v in meta.items() if v is not None}
                }],
                "source": "telegram"
            }],
            reply_to_message_id=reply_to_message_id,
        )
    except Exception as e:
        LOGGER.warning("Error logging voice message: %s", e)
        if update and update.message:
            await _reply_telegram_text(update, "Sorry, something went wrong.")


        # Check if this is a network connectivity error

# ========= Signal CLI REST API Service =========


# ============================================================================
# OTP and Authentication Management
# ============================================================================

def generate_otp() -> str:
    """Generate OTP"""
    otp = str(uuid.uuid4())[:8]  # Generate 8-character OTP
    return otp

def generate_otp_hash_and_cache(expiration_minutes: int = 5) -> str:
    """Generate OTP, cache the hash for validation, and return OTP for messaging partner.

    The effective timeout is taken from config (tunnelmole.otp_timeout_minutes) when
    available; the *expiration_minutes* parameter acts as a fallback only.

    OTPs are single-use by default. When tunnelmole.otp_multiuse is enabled,
    validate_otp_hash() does not consume the entry on success; the OTP remains
    valid until the timeout elapses or tunnelmole stops.
    """
    current_password = get_current_password()
    if not STATE.config or not current_password:
        return ""

    # Prefer the admin-configured OTP timeout over the caller's hardcoded value.
    configured_timeout = int(
        (STATE.config.get("tunnelmole", {}) or {}).get("otp_timeout_minutes")
        or expiration_minutes
    )

    otp = generate_otp()

    if generate_hash:
        hash_value = generate_hash(f"{otp}:{current_password}")
    else:
        import hashlib
        hash_value = hashlib.sha256(f"{otp}:{current_password}".encode()).hexdigest()

    expires = time.time() + (configured_timeout * 60)

    session_data = {
        "expires": expires,
        "created_at": time.time()
    }

    # Sweep before inserting so unconsumed OTPs cannot accumulate unbounded.
    prune_otp_cache()
    STATE.otp_cache[hash_value] = session_data
    multiuse_label = "multi-use" if _is_otp_multiuse() else "single-use"
    LOGGER.info(f"Cached OTP hash for {configured_timeout} minutes ({multiuse_label})")

    return otp

def _is_otp_multiuse() -> bool:
    """Read the otp_multiuse flag from config. Defaults to False (single-use)."""
    try:
        raw = (STATE.config or {}).get("tunnelmole", {}).get("otp_multiuse", False)
        return str(raw).strip().lower() not in {"0", "false", "no", "off"}
    except Exception:
        return False

def validate_otp_hash(hash_value: str) -> dict:
    """Validate OTP hash and return session data.

    Behaviour depends on otp_multiuse config (Tunnelmole settings in admin UI):
            Single-use (default): OTP is consumed on the first successful /auth.
                Tunnelmole stays up only long enough to finish /signal and is stopped
                once the WebRTC DataChannel opens - minimises the window of public exposure.
      Multi-use: OTP stays in cache until its otp_timeout_minutes TTL expires,
        allowing reconnects or multiple concurrent clients with the same OTP.

    Authenticator pair-code mode never populates STATE.otp_cache (nothing to
    look up here) - a miss falls through to a live TOTP-window recompute
    instead, so a hash is only ever valid for the current ~90s tolerance, not
    a standing cache.
    """
    if hash_value not in STATE.otp_cache:
        if not _validate_authenticator_otp_hash_live(hash_value):
            return {"valid": False, "error": "Invalid hash"}
        session_id = str(uuid.uuid4())
        created_at = time.time()
        session = {
            "id": session_id,
            "created_at": created_at,
            "expires_at": created_at + (PAIR_SESSION_EXPIRATION_MINUTES * 60),
            "authenticated": True,
            "otp_hash": hash_value,
        }
        prune_session_cache()
        STATE.session_cache[session_id] = session
        return {"valid": True, "session_id": session_id, "session": session}

    session_data = STATE.otp_cache[hash_value]

    # Check expiration
    if time.time() > session_data["expires"]:
        del STATE.otp_cache[hash_value]
        return {"valid": False, "error": "OTP expired"}

    # Generate a new WebRTC session_id for this /auth call.
    session_id = str(uuid.uuid4())
    created_at = time.time()
    session = {
        "id": session_id,
        "created_at": created_at,
        "expires_at": created_at + (PAIR_SESSION_EXPIRATION_MINUTES * 60),
        "authenticated": True,
        "otp_hash": hash_value
    }

    prune_session_cache()
    STATE.session_cache[session_id] = session

    if not _is_otp_multiuse():
        del STATE.otp_cache[hash_value]

    return {
        "valid": True,
        "session_id": session_id,
        "session": session
    }

_PAIR_CLIENT_ID_MAX_LENGTH = 128

def _normalize_pair_client_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    normalized = " ".join(normalized.split()).replace(" ", "-")
    return normalized[:_PAIR_CLIENT_ID_MAX_LENGTH]

async def handle_auth_request(auth_data: dict) -> dict:
    """Handle OTP hash validation for the direct auth/signaling app."""
    try:
        hash_value = auth_data.get("hash")
        if not hash_value:
            return {"success": False, "error": "Missing hash parameter"}

        validation_result = validate_otp_hash(hash_value)

        if validation_result["valid"]:
            session_id = str(validation_result.get("session_id") or "").strip()
            session_payload = dict(validation_result.get("session") or {})
            client_id = _normalize_pair_client_id(
                auth_data.get("client_id") or auth_data.get("clientId")
            )
            client_display_name = _client_display_name_from_transport(
                auth_data.get("client_display_name")
            )
            if session_id:
                try:
                    if client_id:
                        identity = bind_transport_chat_owner(
                            "direct",
                            client_id,
                            raw_session_id=session_id,
                        )
                        session_payload["stable_client_id"] = client_id
                    else:
                        identity = resolve_webrtc_chat_identity(session_id)
                    identity = _resolve_conversation_identity(identity)
                    # Display names are optional presentation metadata; recording
                    # them must never break identity binding or authentication.
                    try:
                        session_payload["client_display_name"] = WEBRTC.remember_client_display_name(
                            identity,
                            client_display_name,
                        )
                    except Exception as name_err:
                        LOGGER.debug(
                            "Could not record client display name for %s: %s",
                            session_id,
                            name_err,
                        )
                        session_payload["client_display_name"] = ""
                    session_payload.update(
                        _build_client_session_identity_payload(identity, pairing_mode="pair")
                    )
                    prune_session_cache()
                    STATE.session_cache[session_id] = session_payload
                    if client_id:
                        LOGGER.info(
                            "Bound direct /auth session %s to stable client %s -> %s",
                            session_id,
                            client_id,
                            identity.owner_key,
                        )
                except Exception as exc:
                    LOGGER.warning(
                        "Failed to bind direct /auth session %s to client %s: %s",
                        session_id,
                        client_id or "<fallback>",
                        exc,
                    )
            # Tunnelmole is kept alive here so the client can complete WebRTC
            # signaling (/signal SDP + ICE exchange).  Shutdown is handled by
            # cleanup_tunnelmole() in on_datachannel once the DataChannel opens.
            return {
                "success": True,
                "session_id": session_id,
                "session": session_payload
            }
        else:
            return {
                "success": False,
                "error": validation_result.get("error", "Authentication failed")
            }
    except Exception as e:
        LOGGER.error(f"Error handling auth request: {e}")
        return {"success": False, "error": str(e)}

# ============================================================================
# TOTP Pair Mode
# ============================================================================

def _get_tunnelmole_pair_code_mode() -> str:
    try:
        return _normalize_tunnelmole_pair_code_mode(
            (STATE.config or {}).get("tunnelmole", {}).get("pair_code_mode")
        )
    except Exception:
        return _TUNNELMOLE_PAIR_CODE_MODE_RANDOM_OTP

def _get_tunnelmole_connection_mode() -> str:
    try:
        return _normalize_tunnelmole_connection_mode(
            (STATE.config or {}).get("tunnelmole", {}).get("connection_mode")
        )
    except Exception:
        return _TUNNELMOLE_CONNECTION_MODE_TIMED

def _is_totp_pair_mode() -> bool:
    """Return True when authenticator pair-code mode is enabled.

    Requires secure_professional security mode and tunnelmole.pair_code_mode set
    to "authenticator". When active, /pair uses the current TOTP code as the
    pairing token and /auth continues validating SHA256(TOTP:Password).
    """
    if not _is_secure_professional_mode(get_security_mode()):
        return False
    return _get_tunnelmole_pair_code_mode() == _TUNNELMOLE_PAIR_CODE_MODE_AUTHENTICATOR

def _is_url_only_pair_mode() -> bool:
    """Return True when /pair should send ONLY the public URL (no OTP, no seed).

    Requires authenticator pair-code mode (secure_professional) AND the
    tunnelmole.url_only_pair flag. When active, the messaging partner receives
    just the URL; the client derives SHA256(TOTP:password) from the 2FA secret
    saved in its Settings - the out-of-band "second piece of the puzzle".
    """
    if not _is_totp_pair_mode():
        return False
    try:
        return bool((STATE.config or {}).get("tunnelmole", {}).get("url_only_pair"))
    except Exception:
        return False

def _is_tunnelmole_unmanaged_mode() -> bool:
    """Return True when tunnelmole lifetime should be left unmanaged."""
    return _get_tunnelmole_connection_mode() == _TUNNELMOLE_CONNECTION_MODE_UNMANAGED

def generate_totp_pair_otp_and_cache() -> str:
    """Return the current authenticator code for the /otp response's 'otp' field.

    Despite the name (kept for the existing pairing_router callback contract),
    this no longer caches anything: `/auth`'s validate_otp_hash_or_authenticator
    recomputes and compares live against the current TOTP window instead, so a
    captured hash is only ever valid for that window - not for a standing
    24-hour cache, which is what this function used to build (see L-9-adjacent
    fix note below and the H-20 writeup).
    """
    current_password = get_current_password()
    if not STATE.config or not current_password:
        return ""

    all_secrets = get_all_totp_secrets()
    if not all_secrets:
        LOGGER.warning("Authenticator pair-code mode: no TOTP secret registered - cannot generate TOTP code")
        return ""

    try:
        import pyotp as _pyotp
    except ImportError:
        LOGGER.error("Authenticator pair-code mode: pyotp is not installed")
        return ""

    secret = next(iter(all_secrets.values()))
    return _pyotp.TOTP(secret).now()


def _validate_authenticator_otp_hash_live(hash_value: str) -> bool:
    """If Authenticator pair-code mode is active, check `hash_value` against a
    freshly computed SHA256(TOTP:password) for the current window - no
    precomputed cache, so nothing outlives the current ~90s TOTP tolerance.
    Never writes to STATE.otp_cache.
    """
    if not _is_totp_pair_mode():
        return False
    current_password = get_current_password()
    if not current_password:
        return False
    all_secrets = get_all_totp_secrets()
    if not all_secrets:
        return False
    try:
        import pyotp as _pyotp
    except ImportError:
        return False

    now = int(time.time())
    for secret in all_secrets.values():
        totp = _pyotp.TOTP(secret)
        for step in _PAIRING_TOTP_WINDOW_STEPS:
            code = totp.at(now + (step * totp.interval))
            candidate_hash = (
                generate_hash(f"{code}:{current_password}")
                if generate_hash
                else hashlib.sha256(f"{code}:{current_password}".encode()).hexdigest()
            )
            if secrets.compare_digest(candidate_hash, hash_value):
                return True
    return False


# ============================================================================
# Tunnelmole Lifecycle
# ============================================================================


# ----------------------------------------------------------------------------
# C-1 gate: refuse to expose the auth server over a public tunnel while the
# server is still using the default bootstrap password.
#
# On localhost-only operation the default password is not a meaningful risk
# because the auth endpoint is not reachable from the public internet and any
# remote access is gated by separate channels (cloud pair OAuth, messaging
# providers). Tunnelmole however puts /auth onto a public hostname, so
# keeping `autoyou123` at that point is the actual risk. We block the public
# exposure path rather than forcing password setup during installation.
#
# Operators running a disposable test tunnel can opt out by setting
# AUTOYOU_ALLOW_DEFAULT_PASSWORD_ON_TUNNEL=1.
# ----------------------------------------------------------------------------
_ALLOW_DEFAULT_PASSWORD_ON_TUNNEL_ENV = "AUTOYOU_ALLOW_DEFAULT_PASSWORD_ON_TUNNEL"


# ============================================================================
# Tunnelmole Service Management Functions
# ============================================================================


# Create the FastAPI app using ADK's helper
# Import REST API components
from rest_api import (
    ChatRequest, ChatResponse, SessionInfo, APIStatus,
    process_chat_message, get_session_info, get_api_status
)

try:
    from shared import voice_messaging as _shared_voice_messaging

    _shared_voice_messaging.set_speech_settings_provider(_speech_config)
except Exception as exc:
    LOGGER.warning("Could not attach live speech settings to voice-note messaging: %s", exc)

def _replace_app_get_route(
    app_instance: FastAPI,
    route_path: str,
) -> Optional[Callable[..., Any]]:
    original_endpoint: Optional[Callable[..., Any]] = None
    retained_routes = []
    for route in app_instance.router.routes:
        if (
            getattr(route, "path", None) == route_path
            and "GET" in set(getattr(route, "methods", []) or [])
        ):
            if original_endpoint is None:
                original_endpoint = cast(
                    Optional[Callable[..., Any]],
                    getattr(route, "endpoint", None),
                )
            continue
        retained_routes.append(route)

    app_instance.router.routes[:] = retained_routes
    return original_endpoint

def _has_app_get_route(app_instance: FastAPI, route_path: str) -> bool:
    for route in app_instance.router.routes:
        if (
            getattr(route, "path", None) == route_path
            and "GET" in set(getattr(route, "methods", []) or [])
        ):
            return True
    return False

def _extract_adk_web_server_from_endpoint(endpoint: Optional[Callable[..., Any]]) -> Any:
    if endpoint is None:
        return None

    candidate = getattr(endpoint, "__self__", None)
    if candidate is not None and hasattr(candidate, "agent_loader") and callable(
        getattr(candidate, "_get_root_agent", None)
    ):
        return candidate

    for cell in getattr(endpoint, "__closure__", ()) or ():
        with suppress(ValueError):
            candidate = cell.cell_contents
            if candidate is not None and hasattr(candidate, "agent_loader") and callable(
                getattr(candidate, "_get_root_agent", None)
            ):
                return candidate

    return None

def _strip_agent_graph_segment_version(value: Any) -> str:
    return str(value or "").strip().split("@", 1)[0]

def _make_json_safe(value: Any, *, _seen: Optional[Set[int]] = None) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    if isinstance(value, Path):
        return str(value)

    if _seen is None:
        _seen = set()

    value_id = id(value)
    if value_id in _seen:
        return "<recursive>"

    if isinstance(value, dict):
        _seen.add(value_id)
        return {
            str(key): _make_json_safe(item, _seen=_seen)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        _seen.add(value_id)
        return [_make_json_safe(item, _seen=_seen) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        _seen.add(value_id)
        for kwargs in (
            {"mode": "json", "exclude_none": True, "by_alias": True},
            {"mode": "python", "exclude_none": True, "by_alias": True},
            {"exclude_none": True, "by_alias": True},
            {},
        ):
            try:
                dumped_value = model_dump(**kwargs)
                return _make_json_safe(dumped_value, _seen=_seen)
            except Exception:
                continue

    return str(value)

def _build_dot_result_payload(dot_graph: Any) -> Dict[str, str]:
    dot_source = str(getattr(dot_graph, "source", "") or "").strip()
    if not dot_source:
        return {}
    return {"dotSrc": dot_source}

def _resolve_agent_graph_target(root_agent: Any, app_name: str, node_path: Optional[str]) -> Any:
    current_agent = root_agent
    segments = [
        segment
        for segment in str(node_path or "").split("/")
        if str(segment).strip()
    ]
    root_markers = {
        _strip_agent_graph_segment_version(getattr(root_agent, "name", "")),
        _strip_agent_graph_segment_version(app_name),
        "root_agent",
    }
    while segments and _strip_agent_graph_segment_version(segments[0]) in root_markers:
        segments = segments[1:]

    for segment in segments:
        normalized_segment = _strip_agent_graph_segment_version(segment)
        next_agent = None
        for candidate in getattr(current_agent, "sub_agents", []) or []:
            candidate_name = str(getattr(candidate, "name", "") or "").strip()
            if normalized_segment in {
                candidate_name,
                _strip_agent_graph_segment_version(candidate_name),
            }:
                next_agent = candidate
                break
        if next_agent is None:
            return None
        current_agent = next_agent

    return current_agent

def _iter_agent_graph_targets(app_name: str, root_agent: Any):
    root_key = _strip_agent_graph_segment_version(app_name) or "root_agent"
    pending = [(root_key, root_agent)]
    while pending:
        graph_key, agent = pending.pop(0)
        yield graph_key, agent
        for sub_agent in getattr(agent, "sub_agents", []) or []:
            child_name = str(getattr(sub_agent, "name", "") or "").strip()
            if not child_name:
                continue
            pending.append((f"{graph_key}/{child_name}", sub_agent))


def _resolve_admin_ui_asset(filename: str) -> Path:
    return resolve_admin_ui_asset(
        filename,
        resources_root=RESOURCES_ROOT,
        module_file=__file__,
    )

def _admin_ui_css_file() -> Path:
    return _resolve_admin_ui_asset("admin-ui.css")

def _admin_ui_js_file() -> Path:
    return _resolve_admin_ui_asset("admin-ui.js")

def _admin_ui_assets_present() -> bool:
    return admin_ui_assets_present(resources_root=RESOURCES_ROOT, module_file=__file__)

def _admin_ui_asset_response(file_path: Path, media_type: str):
    if not file_path.exists():
        return PlainTextResponse("Asset not found", status_code=404)
    response = FileResponse(file_path, media_type=media_type)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response

def _build_admin_ui_shell_html() -> str:
    return build_admin_ui_shell_html(
        page_title=_get_admin_ui_title(),
        current_theme=_read_autoyou_ui_theme(),
    )


_replace_app_get_route(admin_app, "/")
globals().update(register_admin_ui_routes(admin_app, auth_app, sys.modules[__name__]))


# Do not eagerly build the ADK app in the admin process at import time.
# The separate AI agent process constructs its own FastAPI app on demand in
# `run_agent_server()`. Building it here duplicates agent/model startup work
# and significantly increases admin boot/login latency.
app: Optional[FastAPI] = None

# ========= Start both servers =========


# Global variable to track Auth Server thread
auth_server_thread = None
auth_server_instance = None


# ========= For AutoYou Page Service Functions =========


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    # Must run before the first Process.start(): children inherit a snapshot of
    # this process's sys.path, and cv2's loader transiently puts its own package
    # directory there while relinking. A child that captures that snapshot dies
    # in interpreter bootstrap (stdlib `typing` -> cv2/typing/__init__.py).
    try:
        from shared.mp_syspath_guard import install_multiprocessing_syspath_guard

        install_multiprocessing_syspath_guard()
    except Exception as _syspath_guard_exc:
        LOGGER.debug("multiprocessing sys.path guard not installed: %s", _syspath_guard_exc)
    exit_code = 0
    try:
        result = None
        if not _run_ai_agent_server_cli_if_requested():
            result = asyncio.run(main())
        if isinstance(result, int):
            exit_code = result
    except KeyboardInterrupt:
        exit_code = 130
    finally:
        exit_code = _finalize_runtime_process_exit(exit_code)
        _mark_runtime_exit_complete()
    raise SystemExit(exit_code)

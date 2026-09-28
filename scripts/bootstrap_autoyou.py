#!/usr/bin/env python3
# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.

"""
Cross-platform bootstrap runner for AutoYou.

This script creates/updates the virtual environment, installs the selected
dependency profile, runs optional preflight checks, and launches either the
main AutoYou server or the dedicated autoyou_lite service.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import socket
import secrets
import ssl
import subprocess
import sys
import tarfile
import textwrap
import time

#: Clock seam for the shutdown-wait loop.
#:
#: A test drives that loop with a fake clock. Patching ``time.monotonic`` on the
#: shared module would replace it for the whole process, and any asyncio loop
#: running in another thread calls it on every tick - a finite fake then dies
#: with ``StopIteration`` inside an unrelated test.
_monotonic = time.monotonic
import zipfile
from pathlib import Path
from typing import Callable, Iterable, List, Sequence
from urllib.request import Request, urlopen

def _load_dotenv_fallback(
    dotenv_path: str | Path | None = None,
    *,
    override: bool = False,
    **_kwargs: object,
) -> bool:
    """Load simple KEY=VALUE entries when python-dotenv is unavailable.

    The bootstrap runner is intentionally executable by the system Python
    before the project virtual environment exists.  Keep .env support working
    in that phase without making python-dotenv a prerequisite of the launcher.
    The fallback preserves python-dotenv's default of not overwriting inherited
    environment variables.
    """
    if not dotenv_path:
        return False
    path = Path(dotenv_path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return False

    loaded = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key.isidentifier():
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded


try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = _load_dotenv_fallback


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.process_lifecycle import (
    add_parent_pid_environment,
    force_kill_process_tree,
    live_pids,
    process_spawn_kwargs,
    process_tree_pids,
)
from shared.platform_runtime import (
    TEST_RUNTIME_ROOT_ENV,
    TEST_RUNTIME_STATE_ENV_VARS,
    clear_test_runtime_state_overrides,
)
from shared.version import get_version

SHUTDOWN_TOKEN_ENV = "AUTOYOU_SHUTDOWN_TOKEN"
SHUTDOWN_TOKEN_HEADER = "X-AutoYou-Shutdown-Token"
GRACEFUL_SHUTDOWN_WAIT_ENV = "AUTOYOU_GRACEFUL_SHUTDOWN_WAIT_SECONDS"
DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS = 90.0
WINDOWS_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat", ".com")
WINDOWS_NPM_SUFFIXES = (".cmd", ".exe", ".bat", ".com")
PORTAUDIO_VERSION = "19.7.0"
PORTAUDIO_SOURCE_URL = f"https://github.com/PortAudio/portaudio/archive/refs/tags/v{PORTAUDIO_VERSION}.tar.gz"
# Optional sha256 pin for the PortAudio source tarball. GitHub auto-generated
# archive tarballs are not guaranteed byte-stable, so this is left empty by
# default (we warn instead of failing). Operators who want enforced integrity
# can pin the hash for their environment via AUTOYOU_PORTAUDIO_SHA256.
PORTAUDIO_SHA256 = os.environ.get("AUTOYOU_PORTAUDIO_SHA256", "").strip()
AUTOYOU_PORTAUDIO_PREFIX_ENV = "AUTOYOU_PORTAUDIO_PREFIX"
AUTOYOU_BOOTSTRAP_NODE_VERSION_ENV = "AUTOYOU_BOOTSTRAP_NODE_VERSION"
AUTOYOU_SKIP_PORTABLE_NODE_ENV = "AUTOYOU_SKIP_PORTABLE_NODE"
AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV = "AUTOYOU_BOOTSTRAP_INSTALL_DOCKER"
# Node 22 remains an LTS line supported by the upstream project through 2027.
DEFAULT_PORTABLE_NODE_VERSION = "22.22.3"
MIN_NODE_VERSION = (22, 12, 0)


def strip_windows_extended_path_prefix(path: str | Path) -> str:
    value = str(path)
    for prefix, replacement in (
        ("\\\\?\\UNC\\", "\\\\"),
        ("\\\\??\\UNC\\", "\\\\"),
        ("\\\\?\\", ""),
        ("\\\\??\\", ""),
        ("//?/UNC/", "//"),
        ("//??/UNC/", "//"),
        ("//?/", ""),
        ("//??/", ""),
    ):
        if value.startswith(prefix):
            return replacement + value[len(prefix) :]
    return value


def normalize_local_path(path: str | Path, *, resolve: bool = True) -> Path:
    candidate = Path(path).expanduser()
    if resolve:
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
    return Path(strip_windows_extended_path_prefix(candidate))


def find_browser_executable_in_root(root: Path) -> Path | None:
    patterns = (
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
    )
    for pattern in patterns:
        matches = sorted(root.glob(pattern))
        for candidate in reversed(matches):
            if candidate.is_file():
                return normalize_local_path(candidate)
    return None


def find_existing_playwright_chromium() -> Path | None:
    roots: list[Path] = []
    raw_env_root = str(os.getenv("PLAYWRIGHT_BROWSERS_PATH", "") or "").strip()
    if raw_env_root and raw_env_root != "0":
        roots.append(Path(raw_env_root).expanduser())
    if os.name == "nt":
        local_app_data = str(os.getenv("LOCALAPPDATA", "") or "").strip()
        if local_app_data:
            roots.append(Path(local_app_data) / "ms-playwright")
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        roots.append(Path.home() / ".cache" / "ms-playwright")

    for root in roots:
        try:
            candidate = find_browser_executable_in_root(normalize_local_path(root))
        except Exception:
            candidate = None
        if candidate is not None:
            return candidate
    return None


REPO_ROOT = normalize_local_path(REPO_ROOT)
REQUIREMENTS_DIR = REPO_ROOT / "requirements"

if load_dotenv is not None:
    try:
        load_dotenv(REPO_ROOT / ".env", override=False)
    except Exception:
        pass

PROFILE_COMPONENTS = {
    "base": ("base",),
    "binary-default": ("base", "local-llm", "messaging", "bluetooth", "internet", "desktop-automation"),
    "cloud": ("base",),
    "local": ("base", "local-llm"),
    "recommended": ("base", "local-llm", "messaging", "bluetooth"),
    "full": ("base", "local-llm", "messaging", "bluetooth", "voice", "internet", "desktop-automation"),
    "source-full": ("base", "local-llm", "messaging", "bluetooth", "voice", "internet", "desktop-automation"),
    "connector-full": ("base", "local-llm", "messaging", "bluetooth", "voice", "internet", "desktop-automation"),
    "training-full": ("base", "local-llm", "messaging", "bluetooth", "voice", "internet", "desktop-automation", "tuning"),
}

COMPONENT_REQUIREMENTS = {
    "base": "base.txt",
    "local-llm": "local-llm.txt",
    "messaging": "messaging.txt",
    "bluetooth": "bluetooth.txt",
    "voice": "voice.txt",
    "internet": "internet.txt",
    "desktop-automation": "desktop-automation.txt",
    "tuning": "tuning.txt",
    "research": "research.txt",
}

COMPONENT_DESCRIPTIONS = {
    "base": "Admin UI, cloud AI path, secure device connection, and page service",
    "local-llm": "Offline intent routing, Ollama helpers and Hugging Face model-library integration",
    "messaging": "Telegram, Signal, and WhatsApp transport support",
    "bluetooth": "Bluetooth Pair BLE server/client runtime support",
    "voice": "Realtime STT/TTS, speech model downloads, and audio helpers",
    "internet": "Browser-backed internet agent and Playwright smoke-test support",
    "desktop-automation": "Cross-platform screenshot, mouse, keyboard, and window automation helpers",
    "tuning": "Fine-tuning and training utilities",
    "research": "Legacy browser automation helpers and image extras",
}


class BootstrapError(RuntimeError):
    """Raised when bootstrap cannot continue."""


def info(message: str) -> None:
    print(f"[INFO] {message}")


def success(message: str) -> None:
    print(f"[OK] {message}")


def warn(message: str) -> None:
    print(f"[WARN] {message}")


def err(message: str) -> None:
    print(f"[ERROR] {message}")


def get_autoyou_config_storage_status() -> dict[str, object]:
    """Return the main server's persisted-config storage state."""
    config_dir = REPO_ROOT
    try:
        from shared.platform_runtime import get_config_dir

        config_dir = Path(get_config_dir("AutoYou", anchor=REPO_ROOT / "server.py"))
    except Exception:
        config_dir = REPO_ROOT

    encrypted_path = config_dir / "config.encrypted"
    keystore_path = config_dir / "config.keystore.enc"

    status: dict[str, object] = {
        "config_dir": config_dir,
        "encrypted_path": encrypted_path,
        "encrypted_exists": encrypted_path.exists(),
        "keystore_path": keystore_path,
        "keystore_exists": keystore_path.exists(),
        "keystore_available": False,
        "keystore_backend": "unavailable",
        "keystore_has_key": False,
    }

    try:
        from shared.keystore import (
            _DEFAULT_CRED_NAME,
            _SERVER_SERVICE_NAME,
            get_keystore_status,
        )

        # Do not probe the credential itself before the server starts. On macOS
        # that read can open a Keychain authorization sheet, and startup will
        # perform the real read exactly once when it needs the config.
        keystore_status = get_keystore_status(
            _SERVER_SERVICE_NAME,
            _DEFAULT_CRED_NAME,
            include_has_key=False,
        )
        status["keystore_available"] = bool(keystore_status.get("available"))
        status["keystore_backend"] = str(keystore_status.get("backend") or "unknown")
        status["keystore_has_key"] = keystore_status.get("has_key")
    except Exception:
        pass

    return status


def report_autoyou_config_storage_status() -> None:
    """Print a concise, user-facing summary of persisted config storage."""
    status = get_autoyou_config_storage_status()
    config_dir = Path(status["config_dir"])
    encrypted_path = Path(status["encrypted_path"])
    encrypted_exists = bool(status["encrypted_exists"])
    keystore_path = Path(status["keystore_path"])
    keystore_exists = bool(status["keystore_exists"])
    keystore_available = bool(status["keystore_available"])
    keystore_backend = str(status["keystore_backend"])
    keystore_has_key = status["keystore_has_key"]

    if keystore_exists:
        if keystore_available and keystore_has_key is True:
            success(
                "Detected keystore-backed config at "
                f"{keystore_path}. OS keystore backend: {keystore_backend}."
            )
        elif keystore_available and keystore_has_key is False:
            warn(
                "Detected keystore-backed config at "
                f"{keystore_path}, but the OS keystore entry is missing. "
                "AutoYou may need an encrypted fallback or admin recovery."
            )
        elif keystore_available:
            info(
                "Detected keystore-backed config at "
                f"{keystore_path}. AutoYou will request access to the existing OS credential when it starts."
            )
        else:
            warn(
                "Detected keystore-backed config at "
                f"{keystore_path}, but the OS keystore backend is unavailable "
                f"(backend: {keystore_backend})."
            )

        if encrypted_exists:
            info(f"Legacy encrypted fallback also present at {encrypted_path}.")
        return

    if encrypted_exists:
        info(
            "Detected password-encrypted config at "
            f"{encrypted_path}. AutoYou will try the saved-password bootstrap paths."
        )
        if keystore_available:
            info(
                "OS keystore is available "
                f"(backend: {keystore_backend}) and this config may be migrated after unlock."
            )
        else:
            warn(
                "OS keystore backend is unavailable "
                f"(backend: {keystore_backend}); config will stay password-encrypted."
            )
        return

    if keystore_available:
        warn(
            "No saved config found in "
            f"{config_dir}. AutoYou will create an initial keystore-backed config "
            "during bootstrap and start with the default bootstrap password until "
            "you change it in the admin UI."
        )
    else:
        warn(
            "No saved config found in "
            f"{config_dir}. AutoYou will create an initial password-encrypted config "
            "during bootstrap because the OS keystore backend is unavailable "
            f"(backend: {keystore_backend})."
        )


def parse_component_values(values: Sequence[str]) -> List[str]:
    items: List[str] = []
    for value in values:
        for token in str(value).replace(",", " ").split():
            normalized = token.strip().lower()
            if normalized:
                items.append(normalized)
    return items


def resolve_components(profile: str, with_values: Sequence[str], without_values: Sequence[str]) -> List[str]:
    if profile not in PROFILE_COMPONENTS:
        raise BootstrapError(f"Unknown profile '{profile}'. Choose from: {', '.join(PROFILE_COMPONENTS)}")

    components: List[str] = list(PROFILE_COMPONENTS[profile])

    for component in parse_component_values(with_values):
        if component not in COMPONENT_REQUIREMENTS:
            raise BootstrapError(
                f"Unknown component '{component}'. Choose from: {', '.join(COMPONENT_REQUIREMENTS)}"
            )
        if component not in components:
            components.append(component)

    for component in parse_component_values(without_values):
        if component == "base":
            continue
        if component in components:
            components.remove(component)

    if "base" not in components:
        components.insert(0, "base")
    return components


def venv_python_path() -> Path:
    repo_venv = REPO_ROOT / ".venv"
    preferred = repo_venv / "Scripts" / "python.exe" if os.name == "nt" else repo_venv / "bin" / "python"
    fallback = repo_venv / "bin" / "python" if os.name == "nt" else repo_venv / "Scripts" / "python.exe"
    if preferred.exists():
        return preferred
    return fallback


def resolve_executable(
    command: str,
    *,
    is_windows: bool | None = None,
    windows_suffixes: Sequence[str] = WINDOWS_EXECUTABLE_SUFFIXES,
) -> str | None:
    """Resolve a command to a subprocess-launchable path.

    On Windows, some installers place extensionless Unix shims next to the
    real .exe/.cmd wrappers.  Python's shutil.which("docker") may return that
    shim first, which then crashes CreateProcess with WinError 193.
    """
    command = str(command or "").strip()
    if not command:
        return None
    if is_windows is None:
        is_windows = os.name == "nt"
    if not is_windows:
        return shutil.which(command)

    normalized_suffixes = tuple(
        suffix if str(suffix).startswith(".") else f".{suffix}"
        for suffix in windows_suffixes
    )
    command_path = Path(command)
    if not command_path.suffix:
        for suffix in normalized_suffixes:
            resolved = shutil.which(f"{command}{suffix}")
            if resolved:
                return resolved

    resolved = shutil.which(command)
    if resolved:
        resolved_path = Path(resolved)
        if not resolved_path.suffix:
            for suffix in normalized_suffixes:
                sibling = resolved_path.with_name(f"{resolved_path.name}{suffix}")
                if sibling.exists():
                    return str(sibling)
        return resolved
    return None


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    quiet: bool = False,
) -> subprocess.CompletedProcess[str]:
    stdout = subprocess.DEVNULL if quiet else None
    stderr = subprocess.DEVNULL if quiet else None
    try:
        result = subprocess.run(
            list(command),
            cwd=str(normalize_local_path(cwd or REPO_ROOT)),
            env=env,
            text=True,
            stdout=stdout,
            stderr=stderr,
            check=False,
        )
    except OSError as exc:
        raise BootstrapError(f"Unable to run command {' '.join(command)}: {exc}") from exc
    if check and result.returncode != 0:
        raise BootstrapError(f"Command failed ({result.returncode}): {' '.join(command)}")
    return result


def venv_root_for_python(venv_python: Path) -> Path:
    return venv_python.parent.parent


def venv_native_dir(venv_python: Path) -> Path:
    native_dir = venv_root_for_python(venv_python) / "native"
    native_dir.mkdir(parents=True, exist_ok=True)
    return native_dir


def merge_env(overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in (overrides or {}).items():
        if value is not None:
            env[str(key)] = str(value)
    return env


def env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def certifi_ssl_context(cafile: Path | None = None) -> ssl.SSLContext | None:
    if cafile is not None and cafile.exists():
        return ssl.create_default_context(cafile=str(cafile))
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


def venv_certifi_cafile(venv_python: Path) -> Path | None:
    result = subprocess.run(
        [str(venv_python), "-c", "import certifi; print(certifi.where())"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    path = Path((result.stdout or "").strip())
    return path if path.exists() else None


def download_file(url: str, destination: Path, *, cafile: Path | None = None) -> None:
    """Download *url* to *destination*, retrying TLS with certifi when present."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = Request(url, headers={"User-Agent": "AutoYou-Bootstrap"})
    contexts: list[ssl.SSLContext | None] = [None]
    certifi_context = certifi_ssl_context(cafile)
    if certifi_context is not None:
        contexts.append(certifi_context)

    last_exc: Exception | None = None
    for context in contexts:
        try:
            with urlopen(request, timeout=120, context=context) as response:
                with open(destination, "wb") as output:
                    shutil.copyfileobj(response, output)
            return
        except Exception as exc:
            last_exc = exc
            if context is None and certifi_context is not None:
                warn(f"Download TLS failed with platform roots; retrying with certifi: {exc}")
                continue
            break
    if last_exc is not None:
        raise BootstrapError(f"Failed to download {url}: {last_exc}") from last_exc
    raise BootstrapError(f"Failed to download {url}")


def sha256_file(path: Path) -> str:
    """Return the hex SHA-256 digest of a file, read in chunks."""
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected_hex: str, *, label: str) -> None:
    """Verify *path* matches *expected_hex*, deleting it and raising on mismatch.

    A mismatch means the downloaded artifact was corrupted or tampered with in
    transit (or the pinned hash is stale); refusing to use it is the safe path.
    """
    expected = str(expected_hex or "").strip().lower()
    if not expected:
        return
    actual = sha256_file(path).lower()
    if actual != expected:
        try:
            path.unlink()
        except OSError:
            pass
        raise BootstrapError(
            f"{label} integrity check failed: expected sha256 {expected}, got {actual}. "
            "Refusing to use a tampered/corrupted download."
        )
    success(f"{label} sha256 verified ({actual[:16]}…)")


def node_expected_sha256(version: str, archive_name: str, *, cafile: Path | None = None) -> str:
    """Fetch nodejs.org's official SHASUMS256.txt and return the pinned hash.

    nodejs.org publishes a per-release SHASUMS256.txt listing the sha256 of
    every artifact in that release. We fetch it over the same hardened TLS path
    as the archive and look up the row for *archive_name*. Returns "" if the
    file or row cannot be found (caller decides how strict to be).
    """
    sums_url = f"https://nodejs.org/dist/v{version}/SHASUMS256.txt"
    tmp = REPO_ROOT / ".venv" / "native" / "node" / "downloads" / f"SHASUMS256-{version}.txt"
    try:
        download_file(sums_url, tmp, cafile=cafile)
        for line in tmp.read_text(errors="ignore").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].strip() == archive_name:
                return parts[0].strip().lower()
    except Exception as exc:
        warn(f"Could not fetch Node.js SHASUMS256.txt for integrity check: {exc}")
    return ""


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if root != member_path and root not in member_path.parents:
                raise BootstrapError(f"Refusing unsafe archive member: {member.name}")
        try:
            tar.extractall(destination, filter="data")
        except TypeError:
            tar.extractall(destination)


def safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            member_path = (destination / member.filename).resolve()
            if root != member_path and root not in member_path.parents:
                raise BootstrapError(f"Refusing unsafe archive member: {member.filename}")
        zf.extractall(destination)


def _shutdown_request_host(bind_host: str) -> str:
    normalized = str(bind_host or "").strip().lower()
    if normalized in {"", "0.0.0.0", "::", "::0", ":::"}:
        return "127.0.0.1"
    return bind_host


def request_server_shutdown(host: str, admin_port: int, shutdown_token: str, timeout: float = 3.0) -> bool:
    if not shutdown_token:
        return False
    request = Request(
        f"http://{_shutdown_request_host(host)}:{admin_port}/shutdown",
        data=b"",
        method="POST",
        headers={SHUTDOWN_TOKEN_HEADER: shutdown_token},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return 200 <= getattr(response, "status", 0) < 300
    except Exception:
        return False


def graceful_shutdown_wait_seconds() -> float:
    try:
        configured = float(os.getenv(GRACEFUL_SHUTDOWN_WAIT_ENV, str(DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS)))
        return max(10.0, configured)
    except Exception:
        return DEFAULT_GRACEFUL_SHUTDOWN_WAIT_SECONDS


def launch_server_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: dict[str, str],
    host: str,
    admin_port: int,
) -> int:
    child_env = add_parent_pid_environment(env)
    shutdown_token = secrets.token_urlsafe(32)
    child_env[SHUTDOWN_TOKEN_ENV] = shutdown_token
    try:
        process = subprocess.Popen(
            list(command),
            cwd=str(normalize_local_path(cwd)),
            env=child_env,
            # Keep the full server attached to the launcher's stdout/stderr.
            # CREATE_NO_WINDOW was added with the PID ownership cleanup, but
            # on Windows it also detached the server's logging streams from
            # run_autoyou.bat and from redirected e2e bootstrap logs.
            **process_spawn_kwargs(hide_window=False),
        )
    except OSError as exc:
        raise BootstrapError(f"Unable to start AutoYou process: {exc}") from exc
    tracked_pids = process_tree_pids(getattr(process, "pid", None))

    def cleanup_descendants() -> None:
        lingering_pids = live_pids(tracked_pids)
        if lingering_pids:
            force_kill_process_tree(
                getattr(process, "pid", None),
                extra_pids=lingering_pids,
                process_group=True,
            )

    try:
        # The UI shutdown endpoint closes Uvicorn before Python has finished
        # draining its event loop and executors.  Do not wait forever for that
        # last interpreter teardown: once the admin port has been observed and
        # then closes, give the server the same grace window used by Ctrl+C.
        # This keeps a normally running server unbounded while still cleaning
        # up a process that has lost its server loop but remains alive.
        # ponytail: admin-port liveness is the cross-platform IPC fallback;
        # replace it with explicit supervisor IPC if the server gains restartable listeners.
        admin_port_seen = False
        admin_port_closed_at = None
        shutdown_wait = graceful_shutdown_wait_seconds()
        while True:
            try:
                exit_code = process.wait(timeout=1.0)
                cleanup_descendants()
                return exit_code
            except subprocess.TimeoutExpired:
                admin_port_open = port_in_use(host, admin_port)
                if not admin_port_seen:
                    admin_port_seen = admin_port_open
                    continue
                if admin_port_open:
                    admin_port_closed_at = None
                    continue
                now = _monotonic()
                if admin_port_closed_at is None:
                    admin_port_closed_at = now
                    warn(
                        "AutoYou admin port closed while the process is still running; "
                        f"waiting up to {shutdown_wait:.0f}s for interpreter cleanup..."
                    )
                    continue
                if now - admin_port_closed_at < shutdown_wait:
                    continue

                warn(
                    "AutoYou server remained alive after its admin server stopped; "
                    "force-cleaning the owned process tree..."
                )
                force_kill_process_tree(
                    getattr(process, "pid", None),
                    extra_pids=live_pids(tracked_pids),
                    process_group=True,
                )
                try:
                    exit_code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    cleanup_descendants()
                    return 130
                cleanup_descendants()
                return exit_code
    except KeyboardInterrupt:
        warn("Interrupt received, requesting AutoYou shutdown...")
        graceful_shutdown_requested = request_server_shutdown(host, admin_port, shutdown_token)
        if graceful_shutdown_requested:
            try:
                exit_code = process.wait(timeout=graceful_shutdown_wait_seconds())
                cleanup_descendants()
                return exit_code
            except subprocess.TimeoutExpired:
                warn("AutoYou did not exit after graceful shutdown request, terminating child process...")
        process.terminate()
        try:
            exit_code = process.wait(timeout=8)
            cleanup_descendants()
            return exit_code
        except subprocess.TimeoutExpired:
            warn("AutoYou child process did not terminate cleanly, killing...")
            process.kill()
            try:
                exit_code = process.wait(timeout=5)
                cleanup_descendants()
                return exit_code
            except subprocess.TimeoutExpired:
                cleanup_descendants()
                return 130


def create_venv(python_executable: str) -> Path:
    target = REPO_ROOT / ".venv"
    if target.exists():
        success("Using existing virtual environment at .venv")
    else:
        info("Creating virtual environment...")
        run_command([python_executable, "-m", "venv", str(target)])
        success("Created virtual environment")

    venv_python = venv_python_path()
    if not venv_python.exists():
        raise BootstrapError(f"Virtual environment python not found at {venv_python}")
    return venv_python


def upgrade_packaging_tools(venv_python: Path) -> None:
    info("Upgrading pip, setuptools, and wheel...")
    run_command(
        [str(venv_python), "-m", "pip", "install", "--upgrade", "pip>=26.1.2,<27", "setuptools>=83,<84", "wheel"],
        quiet=True,
        check=False,
    )


LOCKFILE_PATH = REQUIREMENTS_DIR / "locked.txt"


def build_locked_constraints(lockfile: Path = LOCKFILE_PATH) -> Path | None:
    """Derive a plain ``name==version`` constraints file from the pinned, hashed
    ``requirements/locked.txt`` produced by pip-compile.

    The lockfile itself cannot be used as a pip ``-c`` constraints file (it carries
    ``--hash`` lines and ``name[extra]`` forms, both rejected for constraints). We
    strip those down to bare pins so that profile installs from the *range* files
    resolve to the exact AUDITED versions (pip-audit-clean), closing the drift gap
    between what was audited and what actually ships - without abandoning the
    component/profile model. Set ``AUTOYOU_IGNORE_LOCKFILE=1`` to opt out.
    """
    if str(os.environ.get("AUTOYOU_IGNORE_LOCKFILE", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return None
    if not lockfile.exists():
        return None
    specs: set[str] = set()
    for raw in lockfile.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("--"):
            continue
        spec = line.split("--hash", 1)[0].rstrip(" \\").strip()
        spec = spec.split(";", 1)[0].strip()  # drop environment markers
        if "==" not in spec:
            continue
        name, _, version = spec.partition("==")
        name = name.split("[", 1)[0].strip()  # drop extras: name[x]==v -> name==v
        version = version.strip()
        if name and version:
            specs.add(f"{name}=={version}")
    if not specs:
        return None
    dest = REQUIREMENTS_DIR / ".locked.constraints.generated.txt"
    header = (
        "# AUTO-GENERATED from requirements/locked.txt by bootstrap_autoyou.py.\n"
        "# Pins profile installs to the audited (pip-audit-clean) versions. Do not edit.\n"
    )
    dest.write_text(header + "\n".join(sorted(specs)) + "\n", encoding="utf-8")
    return dest


def install_requirements(
    venv_python: Path,
    components: Sequence[str],
    component_env: dict[str, dict[str, str]] | None = None,
    before_component: Callable[[str], dict[str, str]] | None = None,
    upgrade: bool = False,
) -> None:
    constraints = build_locked_constraints()
    if constraints is not None:
        info(f"Pinning installs to audited lockfile versions via {constraints.name}")
    for component in components:
        dynamic_env: dict[str, str] = {}
        if before_component is not None:
            dynamic_env = before_component(component) or {}
        req_name = COMPONENT_REQUIREMENTS[component]
        req_path = REQUIREMENTS_DIR / req_name
        if not req_path.exists():
            raise BootstrapError(f"Missing requirements file: {req_path}")
        info(f"Installing '{component}' profile from {req_path.relative_to(REPO_ROOT)}...")
        install_overrides = dict((component_env or {}).get(component) or {})
        install_overrides.update(dynamic_env)
        install_env = merge_env(install_overrides)
        if component == "voice":
            # Replace a reused legacy RealtimeSTT 0.x install before pip sees
            # voice.txt's newer scipy/faster-whisper/websocket pins. The
            # server-safe 1.0.2 shim is intentionally installed separately.
            install_realtimestt_runtime(venv_python)
        pip_args = [str(venv_python), "-m", "pip", "install"]
        if upgrade:
            pip_args.append("--upgrade")
        pip_args += ["-r", str(req_path)]
        if constraints is not None:
            pip_args += ["-c", str(constraints)]
        run_command(pip_args, env=install_env)
        success(f"Installed '{component}' dependencies")


def install_realtimestt_runtime(venv_python: Path) -> None:
    script = REPO_ROOT / "scripts" / "install_realtimestt_runtime.py"
    if not script.exists():
        raise BootstrapError(f"Missing RealtimeSTT runtime installer: {script}")
    info("Installing AutoYou RealtimeSTT runtime without wake-word extras...")
    run_command([str(venv_python), str(script)])


def reconcile_runtime_dependency_drift(venv_python: Path, components: Sequence[str] = ()) -> None:
    script = REPO_ROOT / "scripts" / "reconcile_python_runtime_env.py"
    if not script.exists():
        return
    constraints = build_locked_constraints()
    command = [str(venv_python), str(script)]
    if constraints is not None:
        command += ["--constraints", str(constraints)]
    if "tuning" in components:
        command.append("--include-tuning")
    info("Reconciling dependency drift in reused virtual environment...")
    run_command(command)


def can_run_privileged() -> bool:
    if os.name == "nt":
        return False
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return True
    sudo = shutil.which("sudo")
    if not sudo:
        return False
    result = subprocess.run([sudo, "-n", "true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def maybe_install_voice_system_dependencies() -> None:
    if platform.system() == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            warn("Homebrew not found. AutoYou will try a local PortAudio build for PyAudio.")
            return
        result = subprocess.run([brew, "list", "portaudio"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode == 0:
            success("Homebrew portaudio already installed")
            return
        info("Installing Homebrew portaudio for voice support...")
        subprocess.run([brew, "install", "portaudio"], check=False)
        return

    if platform.system() == "Linux":
        apt_get = shutil.which("apt-get")
        if not apt_get:
            warn("apt-get not found. Install PortAudio development headers manually if PyAudio fails.")
            return
        if not can_run_privileged():
            warn(
                "PortAudio headers were not auto-installed. Run "
                "'sudo apt-get install portaudio19-dev python3-pyaudio' if the local fallback build fails."
            )
            return
        prefix = [] if (hasattr(os, "geteuid") and os.geteuid() == 0) else ["sudo", "-n"]
        info("Installing PortAudio development headers for voice support...")
        subprocess.run(prefix + [apt_get, "update"], check=False)
        subprocess.run(prefix + [apt_get, "install", "-y", "portaudio19-dev", "python3-pyaudio"], check=False)


def pyaudio_import_works(venv_python: Path) -> bool:
    module_name = "pyaudiowpatch" if platform.system() == "Windows" else "pyaudio"
    result = subprocess.run(
        [str(venv_python), "-c", f"import {module_name}"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def system_portaudio_header_available() -> bool:
    candidates = [
        Path("/opt/homebrew/include/portaudio.h"),
        Path("/usr/local/include/portaudio.h"),
        Path("/usr/include/portaudio.h"),
        Path("/usr/local/opt/portaudio/include/portaudio.h"),
    ]
    return any(path.exists() for path in candidates)


def local_portaudio_prefix(venv_python: Path) -> Path:
    configured = os.getenv(AUTOYOU_PORTAUDIO_PREFIX_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return venv_native_dir(venv_python) / "portaudio"


def local_portaudio_ready(prefix: Path) -> bool:
    lib_names = ["libportaudio.dylib"] if platform.system() == "Darwin" else ["libportaudio.so", "libportaudio.a"]
    return (
        (prefix / "include" / "portaudio.h").exists()
        and any((prefix / "lib" / lib_name).exists() for lib_name in lib_names)
    )


def portaudio_build_env(prefix: Path) -> dict[str, str]:
    env = {
        "CFLAGS": f"-I{prefix / 'include'} " + os.environ.get("CFLAGS", ""),
        "CPPFLAGS": f"-I{prefix / 'include'} " + os.environ.get("CPPFLAGS", ""),
        "LDFLAGS": f"-L{prefix / 'lib'} -Wl,-rpath,{prefix / 'lib'} " + os.environ.get("LDFLAGS", ""),
    }
    pkgconfig = prefix / "lib" / "pkgconfig"
    if pkgconfig.exists():
        existing = os.environ.get("PKG_CONFIG_PATH", "")
        env["PKG_CONFIG_PATH"] = str(pkgconfig) if not existing else str(pkgconfig) + os.pathsep + existing
    if platform.system() == "Darwin":
        machine = platform.machine().lower()
        if machine in {"arm64", "x86_64"}:
            env["ARCHFLAGS"] = f"-arch {machine}"
        existing_dyld = os.environ.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = str(prefix / "lib") if not existing_dyld else str(prefix / "lib") + os.pathsep + existing_dyld
    elif platform.system() == "Linux":
        existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = str(prefix / "lib") if not existing_ld else str(prefix / "lib") + os.pathsep + existing_ld
    return {key: value.strip() for key, value in env.items() if value.strip()}


def ensure_local_portaudio(venv_python: Path) -> dict[str, str]:
    system = platform.system()
    if system not in {"Darwin", "Linux"}:
        warn("Automatic PortAudio fallback is only supported on macOS and Linux.")
        return {}

    prefix = local_portaudio_prefix(venv_python)
    if local_portaudio_ready(prefix):
        success(f"Using local PortAudio at {prefix}")
        return portaudio_build_env(prefix)

    make = shutil.which("make")
    compiler = shutil.which(os.environ.get("CC", "")) if os.environ.get("CC") else None
    compiler = compiler or shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not make or not compiler:
        warn(
            "Cannot build local PortAudio because a C compiler and make are required. "
            "Install Xcode Command Line Tools on macOS or build-essential on Linux if PyAudio fails."
        )
        return {}

    native_dir = venv_native_dir(venv_python)
    src_root = native_dir / "src"
    archive = src_root / f"portaudio-v{PORTAUDIO_VERSION}.tar.gz"
    source_dir = src_root / f"portaudio-{PORTAUDIO_VERSION}"
    if not source_dir.exists():
        info(f"Downloading PortAudio {PORTAUDIO_VERSION} for local PyAudio build...")
        download_file(PORTAUDIO_SOURCE_URL, archive, cafile=venv_certifi_cafile(venv_python))
        if PORTAUDIO_SHA256:
            verify_sha256(archive, PORTAUDIO_SHA256, label=f"PortAudio {PORTAUDIO_VERSION}")
        else:
            warn(
                "PortAudio tarball downloaded without a pinned sha256 "
                "(set AUTOYOU_PORTAUDIO_SHA256 to enforce integrity)."
            )
        safe_extract_tar(archive, src_root)

    configure = source_dir / "configure"
    if not configure.exists():
        raise BootstrapError(f"PortAudio configure script not found at {configure}")

    info(f"Building local PortAudio under {prefix}...")
    run_command(["make", "clean"], cwd=source_dir, check=False, quiet=True)
    configure_cmd = [str(configure), f"--prefix={prefix}"]
    if system == "Darwin":
        configure_cmd.append("--disable-mac-universal")
    build_env = merge_env()
    run_command(configure_cmd, cwd=source_dir, env=build_env)

    make_cflags = ""
    makefile = source_dir / "Makefile"
    if makefile.exists():
        for line in makefile.read_text(errors="ignore").splitlines():
            if line.startswith("CFLAGS = "):
                make_cflags = line.split(" = ", 1)[1].replace("-Werror", "-Wno-error")
                break
    jobs = str(max(1, min(os.cpu_count() or 2, 4)))
    make_cmd = ["make", f"-j{jobs}"]
    if make_cflags:
        make_cmd.append(f"CFLAGS={make_cflags}")
    run_command(make_cmd, cwd=source_dir, env=build_env)
    run_command(["make", "install"], cwd=source_dir, env=build_env)

    mac_header = source_dir / "include" / "pa_mac_core.h"
    if system == "Darwin" and mac_header.exists():
        shutil.copy2(mac_header, prefix / "include" / "pa_mac_core.h")

    if not local_portaudio_ready(prefix):
        raise BootstrapError(f"Local PortAudio build did not produce expected files under {prefix}")

    success(f"Installed local PortAudio at {prefix}")
    return portaudio_build_env(prefix)


def ensure_voice_native_dependencies(venv_python: Path) -> dict[str, str]:
    """Return env overrides needed to build the platform audio binding, if any."""
    if pyaudio_import_works(venv_python):
        return {}
    if system_portaudio_header_available():
        return {}
    return ensure_local_portaudio(venv_python)


def playwright_package_installed(venv_python: Path) -> bool:
    result = subprocess.run(
        [str(venv_python), "-c", "import playwright"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def playwright_browser_install_needed(venv_python: Path, browser: str) -> bool:
    browser_name = str(browser or "").strip().lower()
    if browser_name not in {"chromium", "firefox", "webkit"}:
        return False
    if not playwright_package_installed(venv_python):
        return False

    probe_code = textwrap.dedent(
        """\
        from pathlib import Path
        import sys
        from playwright.sync_api import sync_playwright

        browser_name = sys.argv[1]
        with sync_playwright() as playwright:
            browser_types = {
                "chromium": playwright.chromium,
                "firefox": playwright.firefox,
                "webkit": playwright.webkit,
            }
            executable = Path(browser_types[browser_name].executable_path)
            print(executable)
            raise SystemExit(0 if executable.exists() else 1)
        """
    )
    result = subprocess.run(
        [str(venv_python), "-c", probe_code, browser_name],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return False

    detail = next(
        (
            line.strip()
            for line in ((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()
            if line.strip()
        ),
        "",
    )
    if detail:
        warn(f"Playwright {browser_name} runtime is missing or invalid: {detail}")
    return True


def install_playwright_browsers(venv_python: Path, browsers: Sequence[str]) -> None:
    if not browsers:
        return
    info(f"Installing Playwright browsers: {', '.join(browsers)}")
    result = subprocess.run(
        [str(venv_python), "-m", "playwright", "install", *browsers],
        cwd=str(REPO_ROOT),
        check=False,
    )
    if result.returncode == 0:
        success("Playwright browsers installed")
    else:
        warn("Playwright browser installation failed. You can rerun it later with 'python -m playwright install'.")


def resolve_npm_executable(is_windows: bool | None = None) -> str | None:
    return resolve_executable("npm", is_windows=is_windows, windows_suffixes=WINDOWS_NPM_SUFFIXES)


def executable_version(executable: str, flag: str = "--version") -> tuple[int, ...]:
    try:
        version = subprocess.check_output([executable, flag], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.SubprocessError):
        return (0, 0, 0)
    normalized = version.lstrip("v")
    try:
        return tuple(int(part) for part in normalized.split("."))
    except Exception:
        return (0, 0, 0)


def node_platform_tag() -> str | None:
    system = platform.system()
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        return None

    if system == "Darwin":
        return f"darwin-{arch}"
    if system == "Linux":
        return f"linux-{arch}"
    if system == "Windows":
        return f"win-{arch}"
    return None


def portable_node_paths(venv_python: Path) -> tuple[Path, Path] | None:
    version = os.getenv(AUTOYOU_BOOTSTRAP_NODE_VERSION_ENV, DEFAULT_PORTABLE_NODE_VERSION).strip().lstrip("v")
    tag = node_platform_tag()
    if not version or not tag:
        return None
    root = venv_native_dir(venv_python) / "node" / f"node-v{version}-{tag}"
    if platform.system() == "Windows":
        return root / "node.exe", root / "npm.cmd"
    return root / "bin" / "node", root / "bin" / "npm"


def ensure_portable_node_runtime(venv_python: Path) -> tuple[str, str] | None:
    if str(os.getenv(AUTOYOU_SKIP_PORTABLE_NODE_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}:
        warn(f"Skipping portable Node.js because {AUTOYOU_SKIP_PORTABLE_NODE_ENV} is set.")
        return None

    version = os.getenv(AUTOYOU_BOOTSTRAP_NODE_VERSION_ENV, DEFAULT_PORTABLE_NODE_VERSION).strip().lstrip("v")
    tag = node_platform_tag()
    paths = portable_node_paths(venv_python)
    if not version or not tag or paths is None:
        warn("Automatic portable Node.js install is not supported for this platform.")
        return None

    node_path, npm_path = paths
    if node_path.exists() and npm_path.exists() and executable_version(str(node_path)) >= MIN_NODE_VERSION:
        success(f"Using portable Node.js at {node_path}")
        return str(node_path), str(npm_path)

    extension = "zip" if platform.system() == "Windows" else "tar.xz"
    archive_name = f"node-v{version}-{tag}.{extension}"
    url = f"https://nodejs.org/dist/v{version}/{archive_name}"
    node_dir = venv_native_dir(venv_python) / "node"
    archive = node_dir / "downloads" / archive_name
    extract_root = node_dir / ".extract"
    target_root = node_dir / f"node-v{version}-{tag}"

    info(f"Downloading portable Node.js v{version} for {tag}...")
    node_cafile = venv_certifi_cafile(venv_python)
    download_file(url, archive, cafile=node_cafile)
    # Supply-chain integrity: verify the archive against nodejs.org's official
    # SHASUMS256.txt before extracting and executing the runtime. A mismatch
    # aborts the install rather than running a tampered Node binary.
    expected_node_sha = node_expected_sha256(version, archive_name, cafile=node_cafile)
    if expected_node_sha:
        verify_sha256(archive, expected_node_sha, label=f"Node.js {archive_name}")
    else:
        warn(
            "Proceeding without a verified Node.js checksum (SHASUMS256.txt "
            "unavailable). Set AUTOYOU_SKIP_PORTABLE_NODE=1 to avoid the "
            "unverified download if this is a concern."
        )
    shutil.rmtree(extract_root, ignore_errors=True)
    extract_root.mkdir(parents=True, exist_ok=True)
    if extension == "zip":
        safe_extract_zip(archive, extract_root)
    else:
        safe_extract_tar(archive, extract_root)

    extracted = extract_root / f"node-v{version}-{tag}"
    if not extracted.exists():
        candidates = [path for path in extract_root.iterdir() if path.is_dir()]
        if len(candidates) == 1:
            extracted = candidates[0]
    if not extracted.exists():
        raise BootstrapError(f"Downloaded Node.js archive did not contain the expected folder: {archive_name}")

    shutil.rmtree(target_root, ignore_errors=True)
    shutil.move(str(extracted), str(target_root))
    shutil.rmtree(extract_root, ignore_errors=True)

    node_path, npm_path = portable_node_paths(venv_python) or (Path(), Path())
    if not node_path.exists() or not npm_path.exists():
        raise BootstrapError(f"Portable Node.js install is missing node or npm under {target_root}")
    if os.name != "nt":
        try:
            node_path.chmod(0o755)
            npm_path.chmod(0o755)
        except OSError:
            pass
    if executable_version(str(node_path)) < MIN_NODE_VERSION:
        raise BootstrapError(f"Portable Node.js at {node_path} is older than Node.js 22.12.0")

    success(f"Installed portable Node.js v{version} at {target_root}")
    return str(node_path), str(npm_path)


def resolve_docker_executable(is_windows: bool | None = None) -> str | None:
    return resolve_executable("docker", is_windows=is_windows)


def docker_install_guidance(system: str | None = None) -> list[str]:
    system = system or platform.system()
    if system == "Darwin":
        return [
            "Install Docker Desktop, then launch it once so the daemon starts.",
            "With Homebrew: brew install --cask docker",
            f"To let this bootstrapper try Homebrew automatically, rerun with {AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV}=1.",
        ]
    if system == "Linux":
        return [
            "Install Docker Engine with your distro package manager, then start the daemon.",
            "Debian/Ubuntu example: sudo apt-get update && sudo apt-get install -y docker.io",
            "Then start it: sudo systemctl enable --now docker",
            f"To let this bootstrapper try a known package manager automatically, rerun with {AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV}=1.",
        ]
    if system == "Windows":
        return [
            "Install Docker Desktop, then launch it once so the daemon starts.",
            "With winget: winget install --id Docker.DockerDesktop --source winget",
            f"To let this bootstrapper try winget automatically, rerun with {AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV}=1.",
        ]
    return [
        "Install Docker for this platform and start the daemon.",
        f"Set {AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV}=1 only if you have added platform support to the bootstrapper.",
    ]


def print_docker_install_guidance() -> None:
    for line in docker_install_guidance():
        info(line)


def try_install_docker() -> bool:
    """Try an opt-in Docker install with a local package manager.

    Docker Desktop and Docker Engine installs can require admin privileges,
    GUI startup, reboots, or license acceptance depending on the platform, so
    the bootstrapper only attempts them when explicitly requested.
    """
    if not env_flag(AUTOYOU_BOOTSTRAP_INSTALL_DOCKER_ENV):
        print_docker_install_guidance()
        return False

    system = platform.system()
    if system == "Darwin":
        brew = shutil.which("brew")
        if not brew:
            warn("Automatic Docker install requested, but Homebrew was not found.")
            print_docker_install_guidance()
            return False
        info("Installing Docker Desktop with Homebrew cask...")
        result = run_command([brew, "install", "--cask", "docker"], check=False)
        if result.returncode == 0:
            success("Docker Desktop installed. Launch Docker Desktop once so the daemon starts.")
            return True
        warn("Homebrew could not install Docker Desktop.")
        print_docker_install_guidance()
        return False

    if system == "Linux":
        sudo = shutil.which("sudo")
        if not sudo:
            warn("Automatic Docker install requested, but sudo was not found.")
            print_docker_install_guidance()
            return False
        installers: list[tuple[str, list[str]]] = []
        if shutil.which("apt-get"):
            installers.append(("apt-get", [sudo, "-n", "apt-get", "update"]))
            installers.append(("apt-get", [sudo, "-n", "apt-get", "install", "-y", "docker.io"]))
        elif shutil.which("dnf"):
            installers.append(("dnf", [sudo, "-n", "dnf", "install", "-y", "docker"]))
        elif shutil.which("yum"):
            installers.append(("yum", [sudo, "-n", "yum", "install", "-y", "docker"]))
        elif shutil.which("pacman"):
            installers.append(("pacman", [sudo, "-n", "pacman", "-S", "--noconfirm", "docker"]))
        if not installers:
            warn("Automatic Docker install requested, but no supported Linux package manager was found.")
            print_docker_install_guidance()
            return False
        for label, command in installers:
            info(f"Installing Docker with {label}...")
            if run_command(command, check=False).returncode != 0:
                warn(f"Automatic Docker install step failed: {' '.join(command)}")
                print_docker_install_guidance()
                return False
        systemctl = shutil.which("systemctl")
        if systemctl:
            run_command([sudo, "-n", systemctl, "enable", "--now", "docker"], check=False)
        success("Docker package installed. If Signal remains unavailable, start the Docker daemon and rerun bootstrap.")
        return True

    if system == "Windows":
        winget = shutil.which("winget")
        if not winget:
            warn("Automatic Docker install requested, but winget was not found.")
            print_docker_install_guidance()
            return False
        info("Installing Docker Desktop with winget...")
        result = run_command(
            [
                winget,
                "install",
                "--id",
                "Docker.DockerDesktop",
                "--source",
                "winget",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            check=False,
        )
        if result.returncode == 0:
            success("Docker Desktop installed. Launch Docker Desktop once so the daemon starts.")
            return True
        warn("winget could not install Docker Desktop.")
        print_docker_install_guidance()
        return False

    warn(f"Automatic Docker install is not supported on {system or 'this platform'}.")
    print_docker_install_guidance()
    return False


def check_node_and_install_packages(venv_python: Path) -> None:
    node = resolve_executable("node")
    npm = resolve_npm_executable()
    node_version = executable_version(node) if node else (0, 0, 0)
    if not node or not npm or node_version < MIN_NODE_VERSION:
        if node and node_version < MIN_NODE_VERSION:
            warn("Node.js 22.12.0 or newer is required for WhatsApp and Tunnelmole.")
        elif node and not npm:
            warn("Node.js was found, but npm was not available. Installing portable Node.js for WhatsApp.")
        portable = ensure_portable_node_runtime(venv_python)
        if portable:
            node, npm = portable
        else:
            warn("Node.js 22.12.0+ not found. WhatsApp setup will stay disabled until Node is installed.")
            return

    try:
        version = subprocess.check_output([node, "--version"], text=True).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        warn(f"Node.js command exists but could not be queried: {exc}")
        return
    success(f"Node.js {version} detected")

    # The POSIX npm launcher is a symlink to npm-cli.js whose shebang is
    # "#!/usr/bin/env node", so npm resolves node via PATH. When we fell back to
    # a portable Node (or the system node is too old) the right interpreter is
    # not necessarily first on PATH, so pin node's own directory ahead of it.
    # On Windows npm.cmd already references node.exe relatively, so this is a
    # harmless no-op there.
    npm_env_overrides = {
        "PATH": str(Path(node).parent) + os.pathsep + os.environ.get("PATH", ""),
    }
    if os.getenv("PUPPETEER_EXECUTABLE_PATH") or find_existing_playwright_chromium() is not None:
        npm_env_overrides["PUPPETEER_SKIP_DOWNLOAD"] = "1"
    npm_env = merge_env(npm_env_overrides)

    for folder in ("node/whatsapp",):
        package_json = REPO_ROOT / folder / "package.json"
        if not package_json.exists():
            continue
        # Prefer `npm ci` when a lockfile is present: it installs the EXACT
        # versions pinned in package-lock.json (reproducible, no silent drift to
        # newer caret-compatible releases), which is the supply-chain-safe path.
        # Fall back to `npm install` only when no lockfile exists.
        has_lock = (package_json.parent / "package-lock.json").exists()
        npm_cmd = (
            [npm, "ci", "--silent", "--no-fund", "--no-audit"]
            if has_lock
            else [npm, "install", "--silent", "--no-fund", "--no-audit"]
        )
        info(f"Installing Node dependencies in {folder} ({'npm ci' if has_lock else 'npm install'})...")
        try:
            result = subprocess.run(
                npm_cmd,
                cwd=str(normalize_local_path(package_json.parent)),
                env=npm_env,
                check=False,
            )
            if result.returncode != 0 and has_lock:
                # `npm ci` fails hard if the lockfile is out of sync with
                # package.json; fall back to `npm install` so setup still
                # completes (it will reconcile the lockfile).
                warn("npm ci failed (lockfile may be out of sync); retrying with npm install...")
                result = subprocess.run(
                    [npm, "install", "--silent", "--no-fund", "--no-audit"],
                    cwd=str(normalize_local_path(package_json.parent)),
                    env=npm_env,
                    check=False,
                )
        except OSError as exc:
            warn(
                f"Unable to run npm for {folder}: {exc}. "
                "Install dependencies manually with 'npm.cmd install' or rerun with '--skip-node'."
            )
            continue
        if result.returncode == 0:
            success(f"Installed Node dependencies for {folder}")
        else:
            warn(f"Failed to install Node dependencies for {folder}")


def check_docker() -> None:
    docker = resolve_docker_executable()
    if not docker:
        warn("Docker not found. Signal integration will remain unavailable until Docker is installed.")
        try_install_docker()
        return
    try:
        version = subprocess.run([docker, "--version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        warn(f"Docker command exists but could not be queried: {exc}")
        return
    if version.returncode != 0:
        warn("Docker command exists but could not be queried.")
        return
    info("Checking Docker daemon status...")
    try:
        result = subprocess.run(
            [docker, "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        warn("Docker daemon status check timed out. Signal will stay disabled until Docker responds.")
        return
    except OSError as exc:
        warn(f"Docker command exists but could not query the daemon: {exc}")
        return
    if result.returncode == 0:
        success("Docker daemon is running")
    else:
        warn("Docker is installed but the daemon is not running. Signal will stay disabled until Docker starts.")


def port_in_use(host: str, port: int) -> bool:
    probe_host = _shutdown_request_host(host)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((probe_host, port)) == 0


def find_busy_ports(host: str, ports: Iterable[tuple[str, int]]) -> List[tuple[str, int]]:
    busy: List[tuple[str, int]] = []
    for label, port in ports:
        if port_in_use(host, port):
            busy.append((label, port))
    return busy


def warn_on_ports(host: str, ports: Iterable[tuple[str, int]]) -> List[tuple[str, int]]:
    busy = find_busy_ports(host, ports)
    for label, port in busy:
        warn(f"{label} port {port} is already in use on {host}")
    return busy


def autoyou_admin_is_reachable(host: str, port: int) -> bool:
    probe_host = _shutdown_request_host(host)
    for path in ("/api/status", "/api/v1/status"):
        request = Request(f"http://{probe_host}:{port}{path}", headers={"User-Agent": "AutoYou-Bootstrap"})
        try:
            with urlopen(request, timeout=2) as response:
                if 200 <= getattr(response, "status", 0) < 300:
                    return True
        except Exception:
            continue
    return False


def handle_port_conflicts(host: str, admin_port: int, busy_ports: Sequence[tuple[str, int]]) -> int:
    labels = ", ".join(f"{label}={port}" for label, port in busy_ports)
    if str(os.getenv(TEST_RUNTIME_ROOT_ENV, "")).strip():
        err(
            "Cannot start AutoYou test runtime because requested ports are already in use: "
            f"{labels}. Refusing to reuse an existing AutoYou process while {TEST_RUNTIME_ROOT_ENV} is set."
        )
        return 1
    if autoyou_admin_is_reachable(host, admin_port):
        warn("AutoYou already appears to be running on the requested admin port. Refusing to start a second instance.")
        info(f"Open the existing Admin UI at: http://{host}:{admin_port}/")
        return 0

    err(
        "Cannot start AutoYou because the requested ports are already in use: "
        f"{labels}. Stop the conflicting process or choose different ports."
    )
    return 1


def ollama_is_reachable() -> bool:
    request = Request("http://127.0.0.1:11434/api/tags", headers={"User-Agent": "AutoYou-Bootstrap"})
    try:
        with urlopen(request, timeout=2):
            return True
    except Exception:
        return False


def preseed_tunnelmole(venv_python: Path) -> None:
    """Download and cache the tmole binary so /pair works out-of-the-box.

    Uses the venv Python so shared.tunnelmole_downloader is importable even
    before the caller has modified sys.path.  Failure is non-fatal: if the
    download cannot complete (no network, firewall, etc.) we warn and continue
    so the rest of setup is not blocked.
    """
    seed_script = REPO_ROOT / "scripts" / "download_tunnelmole.py"
    if not seed_script.is_file():
        warn("scripts/download_tunnelmole.py not found - skipping tmole pre-seed.")
        return

    info("Pre-seeding tmole binary for /pair pairing support...")
    try:
        result = subprocess.run(
            [str(venv_python), str(seed_script)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            if output.startswith("[OK] "):
                output = output[len("[OK] "):]
            if output:
                success(f"tmole ready: {output}")
            else:
                success("tmole binary is available.")
        else:
            stderr = (result.stderr or "").strip()
            warn(
                f"tmole pre-seed failed (exit {result.returncode}). "
                f"Install tmole manually or set AUTOYOU_TUNNELMOLE_BIN.\n  {stderr}"
            )
    except subprocess.TimeoutExpired:
        warn("tmole download timed out. Install tmole manually or set AUTOYOU_TUNNELMOLE_BIN.")
    except Exception as exc:
        warn(f"tmole pre-seed error: {exc}. Install tmole manually or set AUTOYOU_TUNNELMOLE_BIN.")


def start_ollama_if_possible() -> None:
    if ollama_is_reachable():
        success("Ollama is already reachable at http://127.0.0.1:11434")
        return

    ollama = resolve_executable("ollama")
    if not ollama:
        warn("Ollama is not installed. Install it from https://ollama.com/download if you want local models.")
        return

    system = platform.system()
    try:
        if system == "Darwin" and Path("/Applications/Ollama.app").exists():
            info("Launching Ollama.app...")
            subprocess.run(["open", "-a", "Ollama"], check=False)
        else:
            info("Starting 'ollama serve' in the background...")
            kwargs: dict[str, object] = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([ollama, "serve"], cwd=str(REPO_ROOT), **kwargs)
    except OSError as exc:
        warn(f"Ollama command exists but could not be launched: {exc}")
        return

    time.sleep(3)
    if ollama_is_reachable():
        success("Ollama responded after startup")
    else:
        warn("Ollama was launched but is not responding yet. Open the app or run 'ollama serve' manually if needed.")


def print_profile_summary(profile: str, components: Sequence[str]) -> None:
    print()
    info(f"AutoYou v{get_version()} bootstrap profile: {profile}")
    for component in components:
        description = COMPONENT_DESCRIPTIONS.get(component, "")
        print(f"  - {component}: {description}")
    print()


def build_server_env(
    *,
    service: str,
    admin_port: int,
    ai_agent_port: int,
    auth_port: int,
    lib_port: int,
    lib_auth_port: int,
    server_password: str,
    lib_password: str,
    lib_admin_password: str,
    software_updates_enabled: bool = True,
) -> dict[str, str]:
    env = os.environ.copy()
    clear_test_runtime_state_overrides(env)
    if not software_updates_enabled:
        env["AUTOYOU_SOFTWARE_UPDATES_ENABLED"] = "0"
    if service == "autoyou-lite":
        lib_source_root = REPO_ROOT / "autoyou_lite"
        existing_pythonpath = str(env.get("PYTHONPATH", "")).strip()
        env["PYTHONPATH"] = (
            str(lib_source_root)
            if not existing_pythonpath
            else str(lib_source_root) + os.pathsep + existing_pythonpath
        )
        env["AUTOYOU_LITE_PORT"] = str(lib_port)
        env["AUTOYOU_LITE_AUTH_PORT"] = str(lib_auth_port)
        if lib_password:
            env["AUTOYOU_LITE_PASSWORD"] = lib_password
            env.setdefault("AUTOYOU_PASSWORD", lib_password)
        if lib_admin_password:
            env["AUTOYOU_LITE_ADMIN_PASSWORD"] = lib_admin_password
    else:
        env["ADMIN_UI_PORT"] = str(admin_port)
        env["AI_AGENT_SERVER_PORT"] = str(ai_agent_port)
        env["AUTH_SERVER_PORT"] = str(auth_port)
        env.setdefault("AI_AGENT_SERVER_HOST", "127.0.0.1")
        # The server is commonly started by a service or scheduled launcher.
        # Browser-backed internet work must therefore be headless by default.
        # Operators can still opt into a visible browser explicitly through
        # any of the internet-browser aliases. Do not add the canonical
        # default when an alias is already configured, or the newly-added
        # canonical value would take precedence over an alias such as
        # AUTOYOU_INTERNET_BROWSER_HEADLESS=0 in internet_tool.py.
        browser_headless_env_vars = (
            "INTERNET_AGENT_BROWSER_HEADLESS",
            "AUTOYOU_INTERNET_BROWSER_HEADLESS",
            "AUTOYOU_BROWSER_HEADLESS",
        )
        if not any(str(env.get(name) or "").strip() for name in browser_headless_env_vars):
            env["INTERNET_AGENT_BROWSER_HEADLESS"] = "1"
        if server_password:
            env["AUTOYOU_SERVER_PASSWORD"] = server_password
    return env


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create/update the AutoYou environment and launch AutoYou or autoyou_lite.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Profiles:
              base/cloud     Minimal cloud-first runtime.
              local          Base runtime plus local Ollama helpers.
              recommended    Base + local LLM + messaging + Bluetooth Pair helpers.
              full           Recommended + voice + browser-backed internet tooling.
              training-full  Full runtime plus local fine-tuning dependencies.

            Examples:
              python scripts/bootstrap_autoyou.py --profile base
              python scripts/bootstrap_autoyou.py --profile binary-default
              python scripts/bootstrap_autoyou.py --profile full --with tuning
              python scripts/bootstrap_autoyou.py --profile training-full
              python scripts/bootstrap_autoyou.py --profile recommended --install-only
            """
        ),
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"AutoYou {get_version()}",
        help="Show AutoYou build version and exit.",
    )
    parser.add_argument(
        "--upgrade",
        "-U",
        action="store_true",
        help="Upgrade installed Python packages to the latest matching versions.",
    )
    parser.add_argument("--profile", default="recommended", choices=sorted(PROFILE_COMPONENTS))
    parser.add_argument("--with", dest="with_components", action="append", default=[], metavar="COMPONENTS")
    parser.add_argument("--without", dest="without_components", action="append", default=[], metavar="COMPONENTS")
    parser.add_argument("--skip-install", action="store_true", help="Reuse the existing environment without pip installs.")
    parser.add_argument("--install-only", action="store_true", help="Prepare the environment, then exit without starting the server.")
    parser.add_argument("--skip-playwright", action="store_true", help="Do not install Playwright browsers.")
    parser.add_argument("--skip-node", action="store_true", help="Skip Node.js checks and npm installs.")
    parser.add_argument("--skip-docker", action="store_true", help="Skip Docker checks.")
    parser.add_argument("--skip-ollama", action="store_true", help="Skip local Ollama checks/startup.")
    parser.add_argument("--skip-tunnelmole", action="store_true", help="Skip pre-seeding the tmole binary (pairing via /pair will require tmole on PATH).")
    parser.add_argument("--no-software-updates", action="store_true", help="Disable all AutoYou software update requests for this launch.")
    parser.add_argument("--playwright-browsers", default="chromium", help="Comma-separated browsers to install when the internet profile is active.")
    parser.add_argument("--service", default="autoyou", choices=["autoyou", "autoyou-lite"], help="Choose which server to start after bootstrap.")
    parser.add_argument("--admin-port", type=int, default=8001)
    parser.add_argument("--ai-agent-port", type=int, default=8081)
    parser.add_argument("--auth-port", type=int, default=8002)
    parser.add_argument("--lib-port", type=int, default=8099, help="autoyou_lite HTTP/admin port.")
    parser.add_argument("--lib-auth-port", type=int, default=8098, help="autoyou_lite public auth/signaling port exposed by tunnelmole.")
    parser.add_argument("--server-password", default="", help="Optional main AutoYou server password used for first-run config creation or encrypted-config unlock.")
    parser.add_argument("--lib-password", default="", help="Optional autoyou_lite pairing/config password used to unlock the saved encrypted config on startup.")
    parser.add_argument("--lib-admin-password", default="", help="Optional autoyou_lite admin password override.")
    parser.add_argument("--host", default=os.getenv("AUTOYOU_BIND_HOST", "127.0.0.1"))
    parser.add_argument("--python", dest="python_executable", default=sys.executable, help="System Python used to create .venv when needed.")
    return parser.parse_args(list(argv))


def main(argv: Sequence[str]) -> int:
    os.chdir(REPO_ROOT)
    host_explicit = bool(os.getenv("AUTOYOU_BIND_HOST")) or any(
        arg == "--host" or str(arg).startswith("--host=")
        for arg in argv
    )
    args = parse_args(argv)
    components = resolve_components(args.profile, args.with_components, args.without_components)

    print_profile_summary(args.profile, components)

    python_cmd = args.python_executable
    if not Path(python_cmd).exists() and shutil.which(python_cmd) is None:
        raise BootstrapError(f"Python executable not found: {python_cmd}")

    venv_python = create_venv(python_cmd)
    browsers = parse_component_values([args.playwright_browsers]) if not args.skip_playwright else []
    upgrade_requested = bool(args.upgrade) or env_flag("AUTOYOU_BOOTSTRAP_UPGRADE")

    if not args.skip_install:
        upgrade_packaging_tools(venv_python)
        if "voice" in components:
            maybe_install_voice_system_dependencies()

        def before_component_install(component: str) -> dict[str, str]:
            if component == "voice":
                return ensure_voice_native_dependencies(venv_python)
            return {}

        install_requirements(
            venv_python,
            components,
            before_component=before_component_install,
            upgrade=upgrade_requested,
        )
        reconcile_runtime_dependency_drift(venv_python, components)
        if "local-llm" in components:
            run_command([str(venv_python), str(REPO_ROOT / "scripts/prepare_intent_router.py")])
    else:
        info("Skipping pip install steps at user request")

    if not args.skip_playwright:
        should_install_playwright = False
        if "internet" in components:
            if playwright_package_installed(venv_python):
                for browser in browsers:
                    if playwright_browser_install_needed(venv_python, browser):
                        info(
                            "Detected an existing Playwright installation with missing browser runtimes; "
                            "restoring requested browsers."
                        )
                        should_install_playwright = True
                        break
            else:
                warn(
                    "Internet support was requested, but Playwright is not installed in .venv. "
                    "Re-run without --skip-install or add the internet component first."
                )
        else:
            for browser in browsers:
                if playwright_browser_install_needed(venv_python, browser):
                    info(
                        "Detected an existing Playwright installation with missing browser runtimes; "
                        "restoring requested browsers."
                    )
                    should_install_playwright = True
                    break
        if should_install_playwright:
            install_playwright_browsers(venv_python, browsers)

    if not args.skip_tunnelmole:
        preseed_tunnelmole(venv_python)

    if "messaging" in components:
        if not args.skip_node:
            check_node_and_install_packages(venv_python)
        if not args.skip_docker:
            check_docker()

    if "local-llm" in components and not args.skip_ollama:
        start_ollama_if_possible()

    if args.service == "autoyou":
        report_autoyou_config_storage_status()

    if "local-llm" not in components:
        warn("Local LLM support was not installed. Configure Google Gemini or another cloud backend in the admin UI before expecting AI responses.")

    if args.service == "autoyou-lite":
        busy_ports = warn_on_ports(
            args.host,
            (
                ("autoyou_lite", args.lib_port),
            ),
        )
    else:
        busy_ports = warn_on_ports(
            args.host,
            (
                ("Admin UI", args.admin_port),
                ("AI agent", args.ai_agent_port),
                ("Auth/signaling", args.auth_port),
            ),
        )

    print()
    display_host = _shutdown_request_host(args.host)
    bind_note = f" (bound to {args.host})" if display_host != args.host else ""
    if args.service == "autoyou-lite":
        info(f"autoyou_lite admin/API: http://{display_host}:{args.lib_port}/{bind_note}")
        info(f"autoyou_lite public auth/signaling: http://{display_host}:{args.lib_auth_port}/{bind_note}")
    else:
        info(f"Admin UI: http://{display_host}:{args.admin_port}/{bind_note}")
        info(f"AI agent: http://{display_host}:{args.ai_agent_port}/{bind_note}")
        info(f"Auth/signaling: http://{display_host}:{args.auth_port}/{bind_note}")
    try:
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from shared.local_network_info import get_lan_ipv4_addresses, is_loopback_only_bind

        lan_addresses = get_lan_ipv4_addresses()
        local_pair_port = args.lib_port if args.service == "autoyou-lite" else args.admin_port
        if lan_addresses and not is_loopback_only_bind(args.host):
            info(
                "Local Pair (AutoYou app on this network): address "
                f"{lan_addresses[0]}  port {local_pair_port}"
            )
            info(
                "Bluetooth Pair (nearby AutoPair exchange): enable Allow Bluetooth Pair; "
                "clients scan for the BLE service, not this address/port."
            )
        elif lan_addresses:
            info(
                "Local Pair from phones needs LAN binding; rerun with --host 0.0.0.0 "
                f"(LAN address {lan_addresses[0]}, port {local_pair_port})"
            )
            info(
                "Bluetooth Pair does not use 127.0.0.1. If this server is in WSL/Docker, "
                "run scripts/bluetooth_pair_host_bridge.py on the host Bluetooth OS."
            )
    except Exception:
        pass
    print()

    if args.install_only:
        success("Environment prepared. Start the server later with this same command without --install-only.")
        return 0

    if busy_ports:
        conflict_probe_port = args.lib_port if args.service == "autoyou-lite" else args.admin_port
        return handle_port_conflicts(args.host, conflict_probe_port, busy_ports)

    env = build_server_env(
        service=args.service,
        admin_port=args.admin_port,
        ai_agent_port=args.ai_agent_port,
        auth_port=args.auth_port,
        lib_port=args.lib_port,
        lib_auth_port=args.lib_auth_port,
        server_password=args.server_password,
        lib_password=args.lib_password,
        lib_admin_password=args.lib_admin_password,
        software_updates_enabled=not args.no_software_updates,
    )
    if args.service == "autoyou-lite":
        command = [
            str(venv_python),
            "-m",
            "autoyou_lite.server",
            "--port",
            str(args.lib_port),
            "--auth-port",
            str(args.lib_auth_port),
        ]
        if host_explicit:
            command[3:3] = ["--host", args.host]
        info("Starting autoyou_lite...")
    else:
        command = [
            str(venv_python),
            "server.py",
            "--admin",
            str(args.admin_port),
            "--ai-agent",
            str(args.ai_agent_port),
            "--auth",
            str(args.auth_port),
        ]
        if host_explicit:
            command[2:2] = ["--host", args.host]
        info("Starting AutoYou...")
    return launch_server_process(
        command,
        cwd=REPO_ROOT,
        env=env,
        host=args.host,
        admin_port=args.lib_port if args.service == "autoyou-lite" else args.admin_port,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except BootstrapError as exc:
        err(str(exc))
        raise SystemExit(1)

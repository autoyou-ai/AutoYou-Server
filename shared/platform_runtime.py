# Copyright (c) 2026 OpenStorey LLC. All rights reserved.
# Licensed under the AutoYou Source-Available License.
# See LICENSE in the project root for license information.
# AI/ML training use prohibited without written authorization (License S3.9).
# AUTOYOU-PROVENANCE-R-via-9a5c0333be42478d3f47960f

"""Cross-platform runtime initialization for AutoYou.

Handles platform-specific setup for paths, environment variables, and
bundled runtime configuration.

Key features:
- Compiled builds store mutable data in the per-user data directory
- Development runs keep repo-local behaviour for editable source trees
- Correct path handling for Nuitka-compiled executables
- Prevents config.encrypted path bugs where server.py appears as a directory component

Usage in server.py:
    from shared.platform_runtime import (
        configure_runtime,
        get_config_dir,
        get_application_root,
        get_resources_root,
    )
    
    configure_runtime(__file__)
    config_dir = get_config_dir("AutoYou", anchor=__file__)
    config_path = config_dir / "config.encrypted"
"""

from __future__ import annotations

__copyright__ = "Copyright (c) 2026 OpenStorey LLC. All rights reserved."
__license__ = "AutoYou Source-Available License v1.3 (AI training prohibited)"


import logging
import os
import shutil
import sys
from pathlib import Path
from typing import MutableMapping, Optional

__debug_provenance_r__ = "AUTOYOU-PROVENANCE-R-via-9a5c0333be42478d3f47960f"


_LOGGER = logging.getLogger("autoyou.platform_runtime")

PACKAGED_RUNTIME_ENV = "AUTOYOU_PACKAGED_RUNTIME"
TEST_RUNTIME_ROOT_ENV = "AUTOYOU_TEST_ROOT"
TEST_RUNTIME_STATE_ENV_VARS = (
    "AUTOYOU_SESSION_DB_PATH",
    "AUTOYOU_AI_SESSION_SERVICE_URI",
    "AUTOYOU_AI_ARTIFACT_SERVICE_URI",
    "AUTOYOU_AGENT_INSTALL_REGISTRY_PATH",
    "AUTOYOU_FRONTEND_REGISTRY_PATH",
    "AUTOYOU_AGENT_UI_SESSIONS_PATH",
    "AUTOYOU_DESKTOP_APP_REGISTRY_PATH",
    "AUTOYOU_CONNECT_DATA_DIR",
    "AUTOYOU_SECURE_STORAGE_MODE",
    "AUTOYOU_SECURE_STORAGE_ROOT",
    "AUTOYOU_SECURE_STORAGE_APP",
    "AUTOYOU_SECURE_STORAGE_PASSWORD",
)


def clear_test_runtime_state_overrides(env: MutableMapping[str, str]) -> None:
    if not str(env.get(TEST_RUNTIME_ROOT_ENV, "")).strip():
        return
    for variable in TEST_RUNTIME_STATE_ENV_VARS:
        env.pop(variable, None)


def strip_windows_extended_path_prefix(path: str | Path) -> str:
    """Return path text without Windows NT extended-length prefixes."""
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


def normalize_local_filesystem_path(path: str | Path, *, resolve: bool = True) -> Path:
    """Normalize a local path for libraries that reject Windows NT path prefixes."""
    candidate = Path(path).expanduser()
    if resolve:
        try:
            candidate = candidate.resolve()
        except OSError:
            candidate = candidate.absolute()
    return Path(strip_windows_extended_path_prefix(candidate))


def _get_test_runtime_root(app_name: str = "AutoYou") -> Optional[Path]:
    """Return the pytest-controlled writable runtime root when configured."""
    raw_root = str(os.getenv(TEST_RUNTIME_ROOT_ENV, "")).strip()
    if not raw_root:
        return None

    root = Path(raw_root).expanduser()
    normalized_app_name = str(app_name or "").strip()
    if normalized_app_name and root.name.lower() != normalized_app_name.lower():
        root = root / normalized_app_name

    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def get_runtime_data_override(app_name: str = "AutoYou") -> Optional[Path]:
    """Optional product-owned state root; test isolation always takes precedence."""
    test_root = _get_test_runtime_root(app_name)
    if test_root is not None:
        return test_root
    raw_root = os.getenv("AUTOYOU_RUNTIME_ROOT", "").strip()
    if not raw_root:
        return None
    root = Path(raw_root).expanduser()
    if not root.is_absolute():
        raise ValueError("AUTOYOU_RUNTIME_ROOT must be an absolute path")
    if root.name.casefold() != app_name.casefold():
        root /= app_name
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _looks_like_packaged_runtime_dir(candidate: Optional[Path]) -> bool:
    if candidate is None or not candidate.exists():
        return False

    if any(
        (candidate / marker).is_dir()
        for marker in ("runtime_modules", "runtime_source", "runtime_site_packages", "runtime_stdlib")
    ):
        return True

    backend_dir = candidate / "Backend"
    if backend_dir.is_dir() and (backend_dir / "AutoYou.exe").exists():
        return True

    return False


def get_platform() -> str:
    """Return the current platform: 'windows', 'darwin' (macOS), or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "darwin"
    else:
        return "linux"


def is_compiled() -> bool:
    """Return True when running as a Nuitka-compiled standalone executable.

    DEV  mode: ``python server.py``  → returns False
    PROD mode: ``./AutoYou`` (Nuitka) → returns True

    Use this flag to toggle behaviour that only makes sense in one mode:
    - PROD: mutable data goes in user directory, agent hot-reload is disabled,
      compiled agent instructions are read-only (unless JAILBREAK is active).
    - DEV:  mutable data lives alongside the repo, hot-reload works normally.
    """
    env_flag = str(os.getenv(PACKAGED_RUNTIME_ENV, "")).strip().lower()
    if env_flag in {"1", "true", "yes", "on"}:
        return True

    try:
        import builtins
        compiled = getattr(builtins, "__compiled__", None)
        if compiled is not None:
            return True
    except Exception:
        pass
    # Nuitka also sets __compiled__ as a module-level builtin in compiled code
    try:
        _ = __compiled__  # type: ignore[name-defined]  # noqa: F821
        return True
    except NameError:
        pass
    # PyInstaller frozen support (belt-and-suspenders)
    if getattr(sys, "frozen", False):
        return True

    executable = getattr(sys, "executable", None)
    if executable:
        try:
            if _looks_like_packaged_runtime_dir(Path(executable).resolve().parent):
                return True
        except Exception:
            pass

    return False


def get_user_data_dir(app_name: str = "AutoYou") -> Path:
    """Return the per-user writable data directory for the current platform.

    - macOS   : ``~/Library/Application Support/<app_name>/``
    - Windows : ``%APPDATA%\\<app_name>\\``
    - Linux   : ``~/.local/share/<app_name>/``

    The directory is created if it does not already exist.
    """
    test_root = get_runtime_data_override(app_name)
    if test_root is not None:
        return test_root

    platform = get_platform()
    if platform == "darwin":
        from shared.macos_runtime_support import get_user_data_dir as _mac_udd
        return _mac_udd(app_name)
    elif platform == "windows":
        appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        data_dir = Path(appdata) / app_name
    else:
        # Linux / XDG
        xdg_data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        data_dir = Path(xdg_data) / app_name
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_logs_dir(app_name: str = "AutoYou", anchor: Optional[str | Path] = None) -> Path:
    """Return the writable log directory for the current runtime."""
    logs_dir = get_mutable_data_dir(app_name, anchor=anchor) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_service_data_dir(
    service_name: str,
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    """Return a writable per-service state directory."""
    normalized = str(service_name or "").replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
    if not parts:
        raise ValueError("service_name must resolve to at least one path component")

    service_dir = get_mutable_data_dir(app_name, anchor=anchor).joinpath(*parts)
    service_dir.mkdir(parents=True, exist_ok=True)
    return service_dir


def get_whisper_cache_dir(app_name: str = "AutoYou") -> Path:
    """Return the writable Hugging Face / Whisper cache directory."""
    cache_dir = get_user_data_dir(app_name) / "whisper_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def configure_whisper_cache_environment(app_name: str = "AutoYou") -> Path:
    """Point Whisper/Hugging Face caches at a writable user directory."""
    cache_dir = get_whisper_cache_dir(app_name)
    hub_dir = cache_dir / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True)

    defaults = {
        "HF_HOME": cache_dir,
        "TORCH_HOME": cache_dir / "torch",
        "TRANSFORMERS_CACHE": hub_dir,
        "HUGGINGFACE_HUB_CACHE": hub_dir,
        "HF_HUB_CACHE": hub_dir,
    }
    for variable_name, target_path in defaults.items():
        if not os.getenv(variable_name):
            os.environ[variable_name] = str(target_path)

    return cache_dir


def get_mutable_data_dir(app_name: str = "AutoYou", anchor: Optional[str | Path] = None) -> Path:
    """Return the directory where mutable runtime state should be stored.

    In PROD (compiled) mode this is the per-user data directory so that mutable
    files (DBs, uploads, .adk sessions, JAILBREAK prompt) are kept separate from
    the read-only application bundle.

    In DEV mode it falls back to the application root (alongside the repo) so that
    running ``python server.py`` continues to work exactly as before.
    """
    test_root = get_runtime_data_override(app_name)
    if test_root is not None:
        return test_root

    if is_compiled():
        return get_user_data_dir(app_name)
    # DEV: use application root (same as before)
    if anchor is None:
        return Path.cwd()
    return get_application_root(anchor)


def get_embedded_agents_root(anchor: str | Path) -> Path:
    """Return the bundled autoyou_agents package root.

    Packaged backends load compiled agent modules from ``runtime_modules``
    first, can optionally fall back to ``runtime_source`` in older layouts,
    and only then use a direct ``autoyou_agents`` directory under the
    resources root.
    """
    resources_root = get_resources_root(anchor).resolve()
    candidates = (
        resources_root / "runtime_modules" / "autoyou_agents",
        resources_root / "runtime_source" / "autoyou_agents",
        resources_root / "autoyou_agents",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    sibling = resources_root.parent / "autoyou_agents"
    if not is_compiled() and (sibling / "__init__.py").is_file():
        return sibling.resolve()
    return candidates[-1]


def _is_adk_app_root(candidate: Path) -> bool:
    """Return True when candidate looks like a single ADK app directory."""
    if not candidate.is_dir():
        return False

    if any((candidate / marker).is_file() for marker in ("agent.py", "root_agent.yaml")):
        return True

    for pattern in ("agent.*.so", "agent.*.pyd"):
        if any(candidate.glob(pattern)):
            return True

    return False


def _contains_adk_apps(candidate: Path) -> bool:
    """Return True when candidate looks like ADK's agents_dir container."""
    if not candidate.is_dir():
        return False

    try:
        for child in candidate.iterdir():
            if child.name.startswith(".") or child.name == "__pycache__":
                continue
            if child.is_file() and child.suffix == ".py":
                return True
            if _is_adk_app_root(child):
                return True
    except OSError:
        return False

    return False


def get_adk_agents_base_dir(anchor: str | Path) -> Path:
    """Return the directory that should be passed to ADK's agents_dir.

    ADK expects the parent directory that contains app folders such as
    ``autoyou_agents/``. For source runs this is the repo root; for packaged
    builds it is typically ``runtime_modules/``.
    """
    return get_embedded_agents_root(anchor).resolve().parent


def resolve_adk_agents_base_dir(
    anchor: str | Path,
    requested_dir: Optional[str | Path] = None,
) -> Path:
    """Normalize an ADK agents_dir, correcting packaged-layout mismatches.

    Older packaged launches can pass the backend/app root instead of the ADK
    app container. When that happens in compiled mode, fall back to the
    packaged ``runtime_modules`` parent that actually contains
    ``autoyou_agents/``.
    """
    fallback_dir = get_adk_agents_base_dir(anchor)
    if requested_dir is None or not str(requested_dir).strip():
        return fallback_dir

    candidate = Path(requested_dir).expanduser().resolve()

    if _is_adk_app_root(candidate):
        return candidate.parent.resolve()

    if _is_adk_app_root(candidate / "autoyou_agents"):
        return candidate.resolve()

    for nested_root_name in ("runtime_modules", "runtime_source"):
        nested_root = candidate / nested_root_name
        if _is_adk_app_root(nested_root / "autoyou_agents"):
            return nested_root.resolve()

    if _contains_adk_apps(candidate):
        return candidate.resolve()

    if is_compiled():
        return fallback_dir

    return candidate


ADK_WEB_ASSETS_DIRNAME = "adk-web-assets"

# ADK's ApiServer._setup_runtime_config() rewrites this one file on every server
# start (it injects the current telemetry consent). Everything else under the
# packaged ``browser/`` tree is read-only, so the writable mirror only has to
# materialize the branch leading to this leaf.
_ADK_MUTABLE_ASSET_RELPATH = ("assets", "config", "runtime-config.json")


def _is_inside_macos_app_bundle(path: Path) -> bool:
    """Return True when path lives inside a ``.app`` bundle's sealed resources."""
    return any(parent.suffix == ".app" for parent in (path, *path.parents))


def _adk_web_assets_are_writable(source_dir: Path) -> bool:
    """Return True when ADK may rewrite runtime-config.json where it ships."""
    if is_compiled() or _is_inside_macos_app_bundle(source_dir):
        return False

    probe = source_dir.joinpath(*_ADK_MUTABLE_ASSET_RELPATH)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.access(probe, os.W_OK)


def _remove_adk_web_assets_mirror(mirror_dir: Path) -> None:
    """Delete a previous mirror without following symlinks into real assets."""
    if mirror_dir.is_symlink() or mirror_dir.is_file():
        mirror_dir.unlink()
    elif mirror_dir.is_dir():
        # rmtree unlinks symlinked entries rather than recursing through them,
        # so the packaged assets the mirror points at are never touched.
        shutil.rmtree(mirror_dir)


def _link_or_copy(source: Path, destination: Path) -> None:
    """Symlink source into the mirror, copying where symlinks are unavailable."""
    try:
        destination.symlink_to(source, target_is_directory=source.is_dir())
        return
    except (OSError, NotImplementedError):
        # Windows refuses symlinks without developer mode or elevation.
        pass

    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
    else:
        shutil.copy2(source, destination)


def _build_adk_web_assets_mirror(source_dir: Path, mirror_dir: Path) -> Path:
    """Build a writable shadow of source_dir that shares its read-only files.

    Every sibling at each level is symlinked, so the ~8MB Angular bundle is
    never duplicated; only the directories on the way to
    ``assets/config/runtime-config.json`` are real, plus a real copy of that
    file for ADK to overwrite.
    """
    _remove_adk_web_assets_mirror(mirror_dir)
    mirror_dir.mkdir(parents=True, exist_ok=True)

    source_level = source_dir
    mirror_level = mirror_dir
    last_index = len(_ADK_MUTABLE_ASSET_RELPATH) - 1

    for index, name in enumerate(_ADK_MUTABLE_ASSET_RELPATH):
        for entry in sorted(source_level.iterdir()):
            if entry.name != name:
                _link_or_copy(entry, mirror_level / entry.name)

        source_child = source_level / name
        mirror_child = mirror_level / name

        if index == last_index:
            if source_child.is_file():
                shutil.copy2(source_child, mirror_child)
                # copy2 carries the packaged mode over; ADK logs an IOError and
                # drops the config silently if the copy is not owner-writable.
                mirror_child.chmod(mirror_child.stat().st_mode | 0o600)
            break

        mirror_child.mkdir(parents=True, exist_ok=True)
        if not source_child.is_dir():
            # ADK recreates missing config directories on write.
            break

        source_level = source_child
        mirror_level = mirror_child

    return mirror_dir


def resolve_adk_web_assets_dir(
    source_dir: str | Path,
    app_name: str = "AutoYou",
) -> Path:
    """Return a web-assets directory ADK can safely write runtime-config.json into.

    ADK rewrites ``assets/config/runtime-config.json`` under the directory it
    serves on every start. Inside a signed macOS bundle that path is a sealed
    resource, so the write invalidates the app's own Developer ID signature -
    ``codesign --verify --strict`` then reports a modified sealed resource and
    Gatekeeper rejects the bundle (the notarization ticket itself stays valid).

    When the packaged assets are not safely writable, mirror them into the
    per-user data directory and hand ADK the mirror. Source runs against a
    writable site-packages keep serving assets in place.
    """
    source = Path(source_dir).expanduser()
    try:
        source = source.resolve()
    except OSError:
        pass

    if not source.is_dir() or _adk_web_assets_are_writable(source):
        return source

    mirror_dir = get_user_data_dir(app_name) / ADK_WEB_ASSETS_DIRNAME
    return _build_adk_web_assets_mirror(source, mirror_dir).resolve()


def get_dynamic_agents_root(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    """Return the writable autoyou_agents root used for runtime scaffolding."""
    test_root = get_runtime_data_override(app_name)
    if test_root is not None:
        agents_root = test_root / "autoyou_agents"
        agents_root.mkdir(parents=True, exist_ok=True)
        return agents_root.resolve()

    if is_compiled():
        agents_root = get_user_data_dir(app_name) / "autoyou_agents"
    else:
        agents_root = Path.cwd() / "autoyou_agents"
        if anchor is not None:
            anchor_path = Path(anchor).resolve()
            for current in (anchor_path, *anchor_path.parents):
                sibling = current.parent / "autoyou_agents"
                if (current / "server.py").is_file() and (sibling / "__init__.py").is_file():
                    return sibling.resolve()
            for candidate in [anchor_path, *anchor_path.parents]:
                if candidate.name == "autoyou_agents" and (candidate / "__init__.py").exists():
                    agents_root = candidate
                    break
                child = candidate / "autoyou_agents"
                if child.is_dir() and (child / "__init__.py").exists():
                    agents_root = child
                    break
    agents_root.mkdir(parents=True, exist_ok=True)
    return agents_root


def iter_agent_roots(
    anchor: str | Path,
    app_name: str = "AutoYou",
) -> tuple[Path, ...]:
    """Return the ordered autoyou_agents roots for discovery and imports."""
    ordered: list[Path] = []
    for candidate in (
        get_dynamic_agents_root(app_name, anchor=anchor),
        get_embedded_agents_root(anchor),
    ):
        resolved = candidate.resolve()
        if resolved not in ordered:
            ordered.append(resolved)
    return tuple(ordered)


# ---------------------------------------------------------------------------
# JAILBREAK constants
# ---------------------------------------------------------------------------

#: Filename placed in the user data directory to activate JAILBREAK mode.
JAILBREAK_ACKNOWLEDGEMENT_FILENAME = "ACKNOWLEDGEMENT_AGREEMENT"

#: Filename for the custom root agent prompt override in JAILBREAK mode.
JAILBREAK_ROOT_PROMPT_FILENAME = "jailbreak_root_prompt.txt"


def get_jailbreak_data_dir(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Path:
    """Return the directory that stores JAILBREAK acknowledgement and prompt files.

    Compiled builds use the per-user writable data directory so the packaged app
    never writes into the bundle. Python/workspace runs stay scoped to the
    mutable app workspace so they do not accidentally reuse the packaged app's
    user-data files.
    """
    data_dir = get_mutable_data_dir(app_name, anchor=anchor).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def is_jailbreak_active(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> bool:
    """Return True when the JAILBREAK acknowledgement file exists."""
    ack_file = get_jailbreak_data_dir(app_name, anchor=anchor) / JAILBREAK_ACKNOWLEDGEMENT_FILENAME
    if not ack_file.exists():
        return False
    try:
        from shared.secure_storage import read_secure_file, secure_storage_enabled

        if secure_storage_enabled():
            # This also migrates an existing plain acknowledgement atomically.
            read_secure_file(ack_file)
    except ImportError:
        pass
    return True


#: Detached HMAC over the Prompt Override, written by the admin save path.
JAILBREAK_ROOT_PROMPT_SIGNATURE_FILENAME = "jailbreak_root_prompt.sig"

#: Key material authorizing the Prompt Override. Held beside the override but
#: never written by the agent-facing tools (see workspace_tools' protected-path
#: list), and regenerated if absent.
JAILBREAK_SIGNING_KEY_FILENAME = "jailbreak_signing.key"
# from __debug_provenance_r__ import via


def _jailbreak_signing_key(app_name: str, anchor) -> bytes:
    """Return (creating if needed) the key that authorizes Prompt Override content.

    Stored hex-encoded: raw 32-byte key material can contain bytes that are
    whitespace when decoded, and any read path that trims the value would then
    yield a different key than was written, silently invalidating every
    signature.
    """
    import binascii
    import secrets as _secrets

    key_path = get_jailbreak_data_dir(app_name, anchor=anchor) / JAILBREAK_SIGNING_KEY_FILENAME

    def _decode(raw: bytes) -> Optional[bytes]:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text:
            return None
        try:
            return binascii.unhexlify(text)
        except (binascii.Error, ValueError):
            return None

    try:
        from shared.secure_storage import read_secure_file, write_secure_file

        if key_path.is_file():
            existing = _decode(read_secure_file(key_path))
            if existing:
                return existing
        key = _secrets.token_bytes(32)
        write_secure_file(key_path, key.hex().encode("ascii"))
        return key
    except Exception:
        if key_path.is_file():
            try:
                existing = _decode(key_path.read_bytes())
                if existing:
                    return existing
            except Exception:
                pass
        key = _secrets.token_bytes(32)
        try:
            key_path.write_bytes(key.hex().encode("ascii"))
        except Exception:
            pass
        return key


def jailbreak_root_prompt_signature(content: str, app_name: str = "AutoYou", anchor=None) -> str:
    """Return the hex HMAC-SHA256 authorizing ``content`` as a Prompt Override."""
    import hashlib
    import hmac as _hmac

    key = _jailbreak_signing_key(app_name, anchor)
    return _hmac.new(key, content.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_jailbreak_root_prompt(content: str, app_name: str = "AutoYou", anchor=None) -> None:
    """Persist the signature that marks ``content`` as admin-authored."""
    signature = jailbreak_root_prompt_signature(content, app_name, anchor)
    sig_path = (
        get_jailbreak_data_dir(app_name, anchor=anchor)
        / JAILBREAK_ROOT_PROMPT_SIGNATURE_FILENAME
    )
    try:
        from shared.secure_storage import write_secure_file

        write_secure_file(sig_path, signature.encode("utf-8"))
    except Exception:
        sig_path.write_text(signature, encoding="utf-8")


def get_jailbreak_root_prompt(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> Optional[str]:
    """Return the custom root agent prompt if JAILBREAK mode is active, else None.

    The returned content is exec'd by the agent, so its *existence* on disk is
    not sufficient authorization: a detached HMAC written by the admin save path
    must match. An override that appears without a valid signature - which is
    what an agent or any other non-admin writer produces - is refused.

    Overrides written before signing existed are adopted once, by the server
    process, and signed in place; see ``adopt_unsigned_jailbreak_root_prompt``.
    """
    import hmac as _hmac

    if not is_jailbreak_active(app_name, anchor=anchor):
        return None
    data_dir = get_jailbreak_data_dir(app_name, anchor=anchor)
    prompt_file = data_dir / JAILBREAK_ROOT_PROMPT_FILENAME
    if not prompt_file.exists():
        return None

    try:
        # Prompt Override is operator-owned mutable state.  Route the read
        # through the shared boundary so Secure Professional Maximus can
        # protect it without changing the agent/client contract.
        from shared.secure_storage import SecureStorageError, read_secure_file

        content = read_secure_file(prompt_file).decode("utf-8").strip() or None
    except SecureStorageError:
        raise
    except Exception:
        return None

    if not content:
        return None

    sig_path = data_dir / JAILBREAK_ROOT_PROMPT_SIGNATURE_FILENAME
    stored_signature = ""
    if sig_path.is_file():
        try:
            from shared.secure_storage import read_secure_file as _read_secure

            stored_signature = _read_secure(sig_path).decode("utf-8").strip()
        except Exception:
            try:
                stored_signature = sig_path.read_text(encoding="utf-8").strip()
            except Exception:
                stored_signature = ""

    if not stored_signature:
        _LOGGER.error(
            "Refusing Prompt Override at %s: no signature present. An override is "
            "only honoured when saved through the authenticated admin path.",
            prompt_file,
        )
        return None

    if not _hmac.compare_digest(
        stored_signature, jailbreak_root_prompt_signature(content, app_name, anchor)
    ):
        _LOGGER.error(
            "Refusing Prompt Override at %s: signature does not match its contents. "
            "The file was modified by something other than the admin save path.",
            prompt_file,
        )
        return None

    return content


def adopt_unsigned_jailbreak_root_prompt(
    app_name: str = "AutoYou",
    anchor: Optional[str | Path] = None,
) -> bool:
    """Sign a pre-existing unsigned override so upgrades keep working.

    Called once from trusted server startup - the server process is the same
    authority as the admin save path, so adopting what is already on disk at
    that moment preserves existing operator overrides without weakening the
    rule for anything written later.
    """
    if not is_jailbreak_active(app_name, anchor=anchor):
        return False
    data_dir = get_jailbreak_data_dir(app_name, anchor=anchor)
    prompt_file = data_dir / JAILBREAK_ROOT_PROMPT_FILENAME
    sig_path = data_dir / JAILBREAK_ROOT_PROMPT_SIGNATURE_FILENAME
    if not prompt_file.is_file() or sig_path.is_file():
        return False
    try:
        from shared.secure_storage import read_secure_file

        content = read_secure_file(prompt_file).decode("utf-8").strip()
    except Exception:
        return False
    if not content:
        return False
    sign_jailbreak_root_prompt(content, app_name, anchor)
    _LOGGER.warning(
        "Adopted a pre-existing unsigned Prompt Override at %s and signed it. "
        "Subsequent overrides must be saved through the admin UI.",
        prompt_file,
    )
    return True


def configure_runtime(anchor: str | Path, app_name: str = "AutoYou") -> None:
    """Initialize platform-specific runtime configuration.
    
    This should be called early in server.py startup, before any service
    initialization or bundled binary access.
    
    Args:
        anchor: Path to server.py or similar anchor file for finding bundled resources
        app_name: Application name (used for macOS data directory)
    """
    platform = get_platform()
    
    if platform == "windows":
        from shared.windows_runtime_support import configure_packaged_runtime_environment
        configure_packaged_runtime_environment(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import configure_packaged_runtime_environment
        configure_packaged_runtime_environment(anchor)
    else:
        _configure_linux_packaged_runtime_environment(anchor)

    _configure_bundled_model_environment(anchor)


def _configure_linux_packaged_runtime_environment(anchor: str | Path) -> None:
    """Linux/WSL equivalent of the Windows/macOS bundled-runtime env setup.

    find_bundled_playwright_root() already has a working generic Linux
    fallback (get_runtime_root(anchor) / "playwright"), but nothing wired its
    result into PLAYWRIGHT_BROWSERS_PATH for Linux the way Windows/macOS do
    for themselves -- a packaged WSL build with runtime/playwright/ bundled
    would still have Playwright fall back to its unpackaged default
    (~/.cache/ms-playwright), which does not exist in a distributed build.

    Also mirrors the unattended server defaults: browser-backed work is
    headless unless an operator explicitly overrides it, while other runtime
    integrations keep their own settings.
    """
    playwright_root = find_bundled_playwright_root(anchor)
    if playwright_root is not None and not os.getenv("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_root)

    if not os.getenv("AUTOYOU_BROWSER_HEADLESS"):
        os.environ["AUTOYOU_BROWSER_HEADLESS"] = "1"

    if not os.getenv("WHATSAPP_BROWSER_HEADLESS"):
        os.environ["WHATSAPP_BROWSER_HEADLESS"] = "1"


def get_config_dir(app_name: str = "AutoYou", anchor: Optional[str | Path] = None) -> Path:
    """Return the configuration directory for the current platform.
    
    - Windows: Application root directory (where exe/script lives)
    - macOS: ~/Library/Application Support/AutoYou/
    - Linux: (current) Application root directory
    
    Args:
        app_name: Application name (used for macOS ~/Library/Application Support/)
        anchor: Path to a file in the application (e.g., server.py). If not provided,
                uses the current working directory as a fallback.
    
    Returns the resolved Path, creating it if necessary.
    """
    test_root = get_runtime_data_override(app_name)
    if test_root is not None:
        return test_root

    platform = get_platform()
    
    if is_compiled():
        return get_user_data_dir(app_name)

    if platform == "windows":
        # Windows: use application directory (where the executable/script is)
        # For compiled apps, this is the directory containing the .exe
        # For development, this is the project root
        if anchor is None:
            # Fallback: use current directory
            anchor = Path.cwd() / "server.py"
        app_root = get_application_root(anchor)
        return app_root.resolve()
    elif platform == "darwin":
        from shared.macos_runtime_support import get_user_data_dir as _mac_get_user_data_dir
        return _mac_get_user_data_dir(app_name)
    else:
        # Linux: use application directory (same as Windows for now)
        if anchor is None:
            anchor = Path.cwd() / "server.py"
        app_root = get_application_root(anchor)
        return app_root.resolve()


def get_runtime_root(anchor: str | Path) -> Path:
    """Return the runtime resource directory for bundled binaries.
    
    - Windows/macOS: <app_root>/runtime/ or MyApp.app/Contents/Resources/runtime/
    - Linux: <app_root>/runtime/ or system paths
    """
    platform = get_platform()
    
    if platform == "windows":
        from shared.windows_runtime_support import get_runtime_root as win_get_runtime_root
        return win_get_runtime_root(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import get_runtime_root as mac_get_runtime_root
        return mac_get_runtime_root(anchor)
    else:
        # Linux: same pattern as Windows
        return get_application_root(anchor) / "runtime"


def find_bundled_node_executable(anchor: str | Path) -> Optional[Path]:
    """Return the bundled Node.js executable when one is available."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_node_executable as _find_bundled_node_executable

        return _find_bundled_node_executable(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_node_executable as _find_bundled_node_executable

        return _find_bundled_node_executable(anchor)

    direct_name = "node.exe" if platform == "windows" else "node"
    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "node" / direct_name,
        runtime_root / "node" / "bin" / direct_name,
        runtime_root / "node-runtime" / direct_name,
        runtime_root / "node-runtime" / "bin" / direct_name,
        runtime_root / direct_name,
    ):
        if candidate.exists():
            return candidate.resolve()

    return None


def get_node_command(anchor: str | Path) -> str:
    """Return the node command for the current platform.
    
    Prefers bundled Node.js if available, falls back to system PATH.
    """
    bundled = find_bundled_node_executable(anchor)
    if bundled is not None:
        return str(bundled)
    # Source bootstrap installs Node beside this virtual environment.
    portable_root = Path(sys.prefix) / "native" / "node"
    suffix = "node.exe" if get_platform() == "windows" else "bin/node"
    for distribution in sorted(portable_root.glob("node-v*"), reverse=True):
        try:
            version = tuple(int(part) for part in distribution.name.split("-")[1][1:].split("."))
        except (ValueError, IndexError):
            continue
        if version < (22, 12, 0):
            continue
        candidate = distribution / suffix
        if candidate.is_file():
            return str(candidate.resolve())
    return "node"


def get_node_service_dir(service_name: str, anchor: str | Path) -> Path:
    """Return the bundled Node service directory for a named service.

    Packaged macOS builds can provide the exact sibling node-services root via
    AUTOYOU_NODE_SERVICE_ROOT. When that is absent, derive it from the bundled
    Node.js runtime if available and fall back to the general resources root.
    """
    normalized_service_name = str(service_name or "").strip()

    candidate_roots: list[Path] = []
    env_root = os.getenv("AUTOYOU_NODE_SERVICE_ROOT", "").strip()
    if env_root:
        candidate_roots.append(Path(env_root).expanduser())

    candidate_roots.append(get_resources_root(anchor) / "node")

    bundled_node = find_bundled_node_executable(anchor)
    if bundled_node is not None:
        bundled_node_root = bundled_node.resolve().parent
        if bundled_node_root.name == "bin":
            bundled_node_root = bundled_node_root.parent
        if bundled_node_root.name in {"node", "node-runtime"}:
            runtime_root = bundled_node_root.parent
            candidate_roots.append(runtime_root.parent / "node")

    try:
        anchor_path = Path(anchor).resolve()
        for parent in anchor_path.parents:
            candidate_roots.append(parent / "node")
    except Exception:
        pass

    try:
        candidate_roots.append(Path(sys.executable).resolve().parent / "node")
    except Exception:
        pass

    ordered_roots: list[Path] = []
    seen_roots: set[str] = set()
    for candidate_root in candidate_roots:
        resolved_root = candidate_root.resolve()
        normalized_root = str(resolved_root)
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)
        ordered_roots.append(resolved_root)

    for root in ordered_roots:
        service_dir = root / normalized_service_name
        if service_dir.is_dir():
            return service_dir

    fallback_root = ordered_roots[0] if ordered_roots else (get_resources_root(anchor) / "node")
    return fallback_root / normalized_service_name


_LINUX_BUNDLE_ROOT_MARKER_DIRS = (
    "assets",
    "autoyou_agents",
    "google",
    "guides",
    "requirements",
    "runtime",
)


def _looks_like_linux_bundle_root(candidate: Path) -> bool:
    """Require at least two known sibling directories, matching the same
    threshold windows_runtime_support.py's _looks_like_runtime_root uses, so
    a single coincidental match (e.g. a stray "runtime" dir) doesn't cause a
    false positive."""
    match_count = sum(1 for name in _LINUX_BUNDLE_ROOT_MARKER_DIRS if (candidate / name).is_dir())
    return match_count >= 2


def _find_linux_bundle_root_from_path(anchor: str | Path) -> Optional[Path]:
    """Walk up from ``anchor`` to find the true packaged WSL/Linux bundle root.

    Nuitka's ``--file-reference-choice=runtime`` gives compiled modules a
    ``__file__`` pointing inside ``runtime_modules/`` (e.g.
    ``AutoYouServer/runtime_modules/server.py``), one level below the actual
    bundle root that ``runtime/``, ``assets/``, etc. live in as siblings.
    Without this, get_application_root()/get_runtime_root() would resolve to
    runtime_modules/ itself and every find_bundled_*() lookup (Node.js,
    Playwright, Ollama, Whisper) would silently miss real bundled runtimes.
    Mirrors the same walk-up-with-markers approach
    windows_runtime_support.py's _find_runtime_root_from_path already uses.
    """
    try:
        resolved = Path(anchor).resolve()
    except Exception:
        return None

    start = resolved if resolved.is_dir() else resolved.parent
    for current in (start, *start.parents):
        if _looks_like_linux_bundle_root(current):
            return current
    return None


def get_application_root(anchor: str | Path) -> Path:
    """Return the application root directory.

    For frozen apps (.exe or .app), returns the bundle/folder.
    For Nuitka compiled (.dist), returns the parent of .dist folder.
    For development, returns the parent of anchor.
    """
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import get_application_root as win_get_app_root
        return win_get_app_root(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import get_application_root as mac_get_app_root
        return mac_get_app_root(anchor)
    else:
        # Linux - handle similar contexts to Windows/macOS
        if getattr(sys, "frozen", False) and getattr(sys, "executable", None):
            return Path(sys.executable).resolve().parent

        bundle_root = _find_linux_bundle_root_from_path(anchor)
        if bundle_root is not None:
            return bundle_root

        anchor_path = Path(anchor).resolve()
        
        # Check for Nuitka pseudo-__file__ (exists on disk but is a directory)
        if anchor_path.suffix == ".py" and isinstance(anchor_path, Path):
            if not anchor_path.exists():
                # Pseudo-__file__ in Nuitka compiled context
                return anchor_path.parent
            elif anchor_path.is_file():
                return anchor_path.parent
            elif anchor_path.is_dir():
                return anchor_path
        
        # Standard file handling
        if anchor_path.is_file():
            return anchor_path.parent
        if anchor_path.is_dir():
            return anchor_path
        if anchor_path.suffix:
            return anchor_path.parent
        return anchor_path

def get_resources_root(anchor: str | Path) -> Path:
    """Return the root directory for bundled resources.
    
    In onefile mode, this is the extraction directory.
    In standalone mode, this is the application root.
    In development, this is the application root.
    """
    platform = get_platform()
    
    if platform == "windows":
        from shared.windows_runtime_support import get_resources_root as win_get_resources_root
        return win_get_resources_root(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import get_resources_root as mac_get_resources_root
        return mac_get_resources_root(anchor)
    else:
        # Linux
        return get_application_root(anchor)


def find_bundled_playwright_root(anchor: str | Path) -> Optional[Path]:
    """Return the bundled Playwright browsers directory when available."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_playwright_root as _find_bundled_playwright_root

        return _find_bundled_playwright_root(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_playwright_root as _find_bundled_playwright_root

        return _find_bundled_playwright_root(anchor)

    env_candidate = os.getenv("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.exists():
            return candidate

    candidate = get_runtime_root(anchor) / "playwright"
    if candidate.exists():
        return candidate.resolve()

    return None


def find_bundled_browser_executable(anchor: str | Path) -> Optional[Path]:
    """Return the bundled Chromium/Chrome executable used by Playwright/Puppeteer."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_puppeteer_executable as _find_bundled_browser_executable

        return _find_bundled_browser_executable(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_chromium_executable as _find_bundled_browser_executable

        return _find_bundled_browser_executable(anchor)

    browsers_root = find_bundled_playwright_root(anchor)
    if browsers_root is None:
        return None

    for pattern in (
        "chromium-*/chrome-linux/chrome",
        "chromium-*/chrome-linux64/chrome",
        "chromium-*/chrome-win/chrome.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
        "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing",
    ):
        matches = sorted(browsers_root.glob(pattern))
        if matches:
            return matches[-1].resolve()

    return None


def find_bundled_ollama_executable(anchor: str | Path) -> Optional[Path]:
    """Return the bundled Ollama executable when one is available."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_ollama_executable as _find_bundled_ollama_executable

        return _find_bundled_ollama_executable(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_ollama_executable as _find_bundled_ollama_executable

        return _find_bundled_ollama_executable(anchor)

    env_candidate = os.getenv("AUTOYOU_OLLAMA_EXE", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.is_file():
            return candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "ollama" / "ollama",
        runtime_root / "ollama" / "bin" / "ollama",
        runtime_root / "ollama",
    ):
        if candidate.exists():
            return candidate.resolve()

    return None


def find_bundled_ollama_models_dir(anchor: str | Path) -> Optional[Path]:
    """Return the bundled Ollama model store when one is available."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_ollama_models_dir as _find_bundled_ollama_models_dir

        return _find_bundled_ollama_models_dir(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_ollama_models_dir as _find_bundled_ollama_models_dir

        return _find_bundled_ollama_models_dir(anchor)

    env_candidate = os.getenv("OLLAMA_MODELS", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    runtime_root = get_runtime_root(anchor)
    for candidate in (
        runtime_root / "ollama" / "models",
        runtime_root / "ollama-models",
    ):
        if candidate.is_dir():
            return candidate.resolve()

    return None


def find_bundled_whisper_models_dir(anchor: str | Path) -> Optional[Path]:
    """Return the bundled whisper.cpp GGML model directory when available."""
    platform = get_platform()

    if platform == "windows":
        from shared.windows_runtime_support import find_bundled_whisper_models_dir as _find_bundled_whisper_models_dir

        return _find_bundled_whisper_models_dir(anchor)
    elif platform == "darwin":
        from shared.macos_runtime_support import find_bundled_whisper_models_dir as _find_bundled_whisper_models_dir

        return _find_bundled_whisper_models_dir(anchor)

    env_candidate = os.getenv("AUTOYOU_WHISPER_MODELS_DIR", "").strip()
    if env_candidate:
        candidate = Path(env_candidate).expanduser().resolve()
        if candidate.is_dir():
            return candidate

    candidate = get_runtime_root(anchor) / "whisper" / "models"
    if candidate.is_dir():
        return candidate.resolve()

    return None


def _configure_bundled_model_environment(anchor: str | Path) -> None:
    ollama_executable = find_bundled_ollama_executable(anchor)
    if ollama_executable is not None and not os.getenv("AUTOYOU_OLLAMA_EXE"):
        os.environ["AUTOYOU_OLLAMA_EXE"] = str(ollama_executable)

    ollama_models_dir = find_bundled_ollama_models_dir(anchor)
    if ollama_models_dir is not None and not os.getenv("OLLAMA_MODELS"):
        os.environ["OLLAMA_MODELS"] = str(ollama_models_dir)

    whisper_models_dir = find_bundled_whisper_models_dir(anchor)
    if whisper_models_dir is not None and not os.getenv("AUTOYOU_WHISPER_MODELS_DIR"):
        os.environ["AUTOYOU_WHISPER_MODELS_DIR"] = str(whisper_models_dir)


def use_readable_mime_database() -> None:
    """Load only the system mime files this process may actually open.

    `mimetypes` checks that a known file exists and then opens it. Inside a
    sandbox the check succeeds while the open is refused, and the resulting
    PermissionError surfaces at whatever first guessed a content type. Settling
    it once, at startup, keeps every later guess deterministic.
    """
    import mimetypes

    readable = []
    for candidate in mimetypes.knownfiles:
        try:
            with open(candidate, "rb"):
                readable.append(candidate)
        except OSError:
            continue
    mimetypes.knownfiles = readable
    try:
        mimetypes.init()
    except Exception:  # A broken system file must not stop startup.
        pass
